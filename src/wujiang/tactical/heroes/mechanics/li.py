"""Li battlefield objects and skills."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import QueuedAction
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext, AISkillScoreContext, HeroAIPolicy
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import AttackCountTrait
from wujiang.tactical.heroes.common import DashMoveSkill
from wujiang.tactical.heroes.common import HardenSkill
from wujiang.tactical.heroes.common import MagicImmunityStatus
from wujiang.tactical.heroes.common import PassiveProtectionSkill
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.heroes.mechanics.status_replacement import replace_status_by_name


class LiAttackPolicy(HeroAIPolicy):
    def single_target_attack_bonus(self, battle: Battle, actor: Unit, target: Unit,
                                   payload: dict[str, Any], expected_damage: float) -> float:
        if expected_damage > 0 and not actor.cannot_heal:
            return min(0.25, max(0.0, actor.max_health - actor.current_hp)) * 140.0
        return 0.0


class LiAreaScoring:
    """One rollback probe for Li's two area effects."""
    ai_early_effect_score = True

    def ai_position_bonus(self, context: AISkillScoreContext, unit: Unit,
                          sign: float, position: Position | None) -> float:
        return 0.0

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        targets = list(context.effect_units(self))
        units = list(dict.fromkeys([actor, *targets]))
        before = {unit.unit_id: (unit.current_hp, unit.alive, unit.position) for unit in units}
        with context.probe_rollback():
            try:
                probe = dict(context.payload)
                probe.update(self.queued_payload_metadata(battle, actor, probe))
                self.execute(battle, actor, probe)
            except ActionError:
                return -1000.0
            score = -self.mana_cost * 20.0
            for unit in units:
                hp, alive, position = before[unit.unit_id]
                sign = 1.0 if unit.player_id != actor.player_id else -1.0
                score += sign * (hp - unit.current_hp) * (120.0 if sign > 0 else 180.0)
                if alive and not unit.alive:
                    score += sign * 110.0
                score += self.ai_position_bonus(context, unit, sign, position)
            if self.max_uses_per_battle == 1 and score < context.profile.once_per_battle_threshold:
                score -= 35.0
            return score


class ChainPullSkill(LiAreaScoring, Skill):
    ai_probe_hostile_effect = True
    def ai_position_bonus(self, context: AISkillScoreContext, unit: Unit,
                          sign: float, position: Position | None) -> float:
        if unit.position == position or unit.position is None:
            return 0.0
        score = sign * 45.0
        if sign > 0:
            actor = context.actor
            score += min(unit.current_hp, context.estimate_damage(unit, actor.stat("attack"))
                         * max(0, actor.attack_actions_per_turn() - actor.attacks_used)) * 100.0
        return score

    directions = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    def __init__(self) -> None:
        super().__init__(
            "chain_pull",
            "链条",
            "普通技能：费 0.5 魔，每回合最多 1 次；选择身前直线 5 格，击中的第一个单位被直线拉到自己周围。",
            mana_cost=0.5,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for origin in battle.unit_cells(actor) or ([actor.position] if actor.position else []):
            if origin is None:
                continue
            for direction in self.directions:
                pattern = battle.line_positions(origin, direction, 5)
                if not pattern:
                    continue
                key = pattern_signature(pattern)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(pattern)
        return patterns

    def chosen_line(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_chain_cells") or match_payload_pattern(payload, self.patterns(battle, actor))

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_chain_cells": positions_to_dict(self.chosen_line(battle, actor, payload))}

    def first_hit(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> HeroUnit | None:
        for cell in cells:
            for unit in battle.units_at(cell):
                if unit.unit_id != actor.unit_id:
                    return unit  # type: ignore[return-value]
        return None

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_line(battle, actor, payload)
        target = self.first_hit(battle, actor, cells)
        if target is None:
            battle.log(f"{actor.name} 的链条没有击中单位。")
            return
        target_ctx = battle.validate_target(actor, target, action_name="链条", is_skill=True, is_hostile=target.player_id != actor.player_id, ignore_targeting_restrictions=True)
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        target_cells = battle.unit_cells(target)
        try_cells = [cell for cell in cells if any(cell.distance_to(actor_cell) <= 1 for actor_cell in battle.unit_cells(actor))]
        for destination in try_cells:
            if any(destination == occupied for occupied in target_cells):
                battle.log(f"{target.name} 已经在 {actor.name} 周围。")
                return
            if battle.can_place_unit(target, destination, ignore=target):
                battle.move_unit(
                    target,
                    destination,
                    via_skill=True,
                    forced=True,
                    max_distance=battle.width + battle.height,
                    tags={"chain_pull"},
                )
                battle.log(f"{actor.name} 用链条将 {target.name} 拉到身边。")
                return
        battle.log(f"{actor.name} 的链条击中了 {target.name}，但没有合法落点。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        targets = []
        for pattern in self.patterns(battle, actor):
            target = self.first_hit(battle, actor, pattern)
            if target is not None and target.unit_id not in targets:
                targets.append(target.unit_id)
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        cells = self.chosen_line(battle, actor, payload)
        target = self.first_hit(battle, actor, cells)
        return [cell for cell in cells if target is not None and cell in battle.unit_cells(target)]

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        target = self.first_hit(battle, actor, self.chosen_line(battle, actor, payload))
        return [target] if target is not None else []  # type: ignore[list-item]


class WhirlwindAttackSkill(LiAreaScoring, Skill):
    ai_damage_probe_resolves_as_attack = True

    ai_area_bonus_codes = frozenset({"whirlwind_attack"})

    def __init__(self) -> None:
        super().__init__(
            "whirlwind_attack",
            "回天",
            "大招：对周围一圈所有单位各结算一次普攻。",
            max_uses_per_battle=1,
            target_mode="self",
        )

    def area_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if "declared_area_cells" in payload:
            return battle.payload_positions(payload, "declared_area_cells")
        body = battle.unit_cells(actor)
        return [cell for cell in square_around_cells(battle, body, radius=1) if cell not in body]

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.area_cells(battle, actor, payload))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for unit in self.get_target_units_for_payload(battle, actor, payload):
            if actor.alive and unit.alive and not unit.banished:
                battle.resolve_attack_damage(actor, unit, action_name="回天", tags={"whirlwind"}, payload={"area_attack": True})

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.area_cells(battle, actor, {})
        return {"cells": positions_to_dict(cells), "target_unit_ids": [unit.unit_id for unit in self.get_target_units_for_payload(battle, actor, {})], "secondary_cells": positions_to_dict(battle.unit_cells(actor)), "requires_target": False}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.area_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [unit for unit in battle.effect_units_at_cells(self.area_cells(battle, actor, payload)) if unit.unit_id != actor.unit_id]


class RedHeatStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("红热", attack_delta=2, speed_delta=3, description="攻 +2，速 +3。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is None or not self.owner.alive:
            return
        damage = round(self.owner.current_hp / 2, 4)
        if damage <= 0:
            return
        battle.resolve_damage(DamageContext(source=self.owner, target=self.owner, attack_power=0, raw_damage=damage, is_skill=True, action_name="红热", tags={"red_heat", "special_health_loss"}))


class RedHeatSkill(Skill):
    def ai_self_buff_score(self, context: AISkillScoreContext) -> float:
        actor, battle = context.actor, context.battle
        active = actor.has_status("红热")
        before = context.shared.li_attack_plan_value(context)
        with context.probe_rollback():
            self.execute(battle, actor, {})
            after = context.shared.li_attack_plan_value(context)
        half_hp_cost = actor.current_hp * 60.0 if not actor.magic_immunity and actor.total_shields() == 0 else 0.0
        gain = after - before + (half_hp_cost if active else -half_hp_cost)
        return 300.0 + gain if gain > 8.0 else -1000.0

    def __init__(self) -> None:
        super().__init__(
            "red_heat",
            "红热",
            "开关技能：只能在回合开始阶段使用；开启期间攻 +2、速 +3，自己回合结束时血减半。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used > 0 or actor.performed_active_skill:
            return False, "红热只能在回合开始阶段使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("红热")
        if existing is not None:
            actor.remove_status(existing, battle)
            battle.log(f"{actor.name} 关闭了红热。")
            return
        actor.add_status(RedHeatStatus())
        battle.log(f"{actor.name} 开启了红热。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class EssenceAttackStatus(StatusEffect):
    def __init__(self, charges: int = 2) -> None:
        super().__init__("精华", f"剩余 {charges} 次破魔攻击。", duration=1, tick_scope="owner_turn_end")
        self.charges = charges

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + 2

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        if self.owner is not actor or self.charges <= 0 or payload.get("area_attack") or payload.get("reaction_attack"):
            return {}
        return {"ignore_shield": True, "essence_attack": True}

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if action_type == "attack" and self.charges > 0:
            self.charges -= 1
            payload["ignore_shield"] = True
            payload["essence_attack"] = True
            payload["essence_declared"] = True
            self.description = f"剩余 {self.charges} 次破魔攻击；本回合攻击上限仍额外 +2。"

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        # Direct engine callers have no queued declaration; consume once at finish.
        if payload.get("essence_attack") and not payload.get("essence_declared") and self.charges > 0:
            self.charges -= 1

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is not None and self.charges > 0:
            gained = self.owner.gain_mana(self.charges)
            battle.log(f"{self.owner.name} 将剩余精华转化为 {gained} 点魔。")
        super().on_owner_turn_end(battle)


class EssenceSkill(Skill):
    def ai_self_buff_score(self, context: AISkillScoreContext) -> float:
        actor, battle = context.actor, context.battle
        if actor.has_status("精华"):
            return -1000.0
        before = context.shared.li_attack_plan_value(context)
        with context.probe_rollback():
            self.execute(battle, actor, {})
            after = context.shared.li_attack_plan_value(context)
        if after > before + 1.0:
            return max(context.best_attack_score(), after) + 30.0
        return min(2.0, max(0.0, actor.max_mana() - actor.current_mana)) * 24.0 - 12.0

    def __init__(self) -> None:
        super().__init__(
            "essence",
            "精华",
            "普通技能：冷却 2 轮，本回合获得 2 次额外破魔攻击；回合结束时未使用的次数转化为魔。",
            cooldown_turns=2,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        replace_status_by_name(battle, actor, EssenceAttackStatus(2))
        battle.log(f"{actor.name} 凝聚精华，获得 2 次额外破魔攻击。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class ForesightRewardStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("见切奖励", "下个自己的回合攻击次数 +1、速 +1。", duration=1, tick_scope="owner_turn_end")
        self.active = False

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.active = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.active:
            super().on_owner_turn_end(battle)

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + int(self.active)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if self.active and stat_name == "speed" else value


class ForesightBlockStatus(StatusEffect):
    def __init__(self, queued_action: QueuedAction) -> None:
        super().__init__("见切", "挡住本次声明的普攻。")
        self.queued_action = queued_action
        self.expire_with_chain = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.cancelled:
            return
        current = battle.resolving_action
        declaration = self.queued_action.payload.get("declaration_id")
        same_action = current is self.queued_action or (
            current is not None and declaration is not None
            and current.payload.get("declaration_id") == declaration
        )
        if ctx.is_skill or "attack" not in ctx.tags or not same_action:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 用见切挡住了普攻。"
        battle.emit_defense_visual_event(source=ctx.source, target=owner, action_name=ctx.action_name, defense_reason="block")


class ForesightSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__(
            "foresight",
            "见切",
            "被动技能：每个对方武将回合最多 1 次，挡住一次敌方普攻；李下个回合攻击次数 +1、速 +1。",
            max_uses_per_turn=1,
            timing="passive",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("见切只能通过连锁使用。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方普攻连锁。"
        if queued_action.action_type != "attack":
            return False, "见切只能挡普攻。"
        if battle.reaction_proxy_target(actor, queued_action) is None:
            return False, "当前普攻没有影响到自己。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        actor.add_status(ForesightBlockStatus(queued_action), source=actor)
        actor.add_status(ForesightRewardStatus(), source=actor)
        battle.log(f"{actor.name} 准备用见切挡住这次普攻。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        return self.preview(battle, actor)

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        threat = context.threat(context.proxy_target())
        return threat + 100.0 if threat > 0 else 45.0


class StillnessStatus(StatusEffect):
    provided_flags = frozenset({"cannot_move", "cannot_attack", "cannot_use_skills", "magic_immunity"})

    def __init__(self) -> None:
        super().__init__("定", "无法行动，守 +2，魔免，自然回血。", duration=4, tick_scope="owner_turn_start")

    def bind(self, owner: HeroUnit) -> "StillnessStatus":
        super().bind(owner)
        owner.cannot_move = True
        owner.cannot_attack = True
        owner.cannot_use_skills = True
        owner.magic_immunity = True
        return self

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "defense":
            return value + 2
        return value

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is not None:
            battle.heal(HealContext(source=self.owner, target=self.owner, amount=0.25, action_name="定"))
        super().on_owner_turn_start(battle)

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for flag in ("cannot_move", "cannot_attack", "cannot_use_skills"):
            remaining = any(
                flag in getattr(component, "provided_flags", ())
                or (getattr(component, "flag_name", "") in {flag, "cannot_act"}
                    and getattr(component, "value", True) and getattr(component, "flag_active", True))
                for component in owner.iter_components()
            )
            setattr(owner, flag, (owner.is_clone and flag != "cannot_move") or remaining)
        owner.magic_immunity = any(
            isinstance(component, MagicImmunityStatus)
            or "magic_immunity" in getattr(component, "provided_flags", ())
            or (getattr(component, "flag_name", "") == "magic_immunity"
                and getattr(component, "value", True) and getattr(component, "flag_active", True))
            for component in owner.iter_components()
        )


class StillnessSkill(Skill):
    def ai_self_buff_score(self, context: AISkillScoreContext) -> float:
        actor, battle = context.actor, context.battle
        enemies = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and unit.position is not None and not unit.banished]
        missing = max(0.0, actor.max_health - actor.current_hp)
        threatened = any(context.shared.unit_distance(battle, actor, enemy)
                         <= enemy.targeting_range() + enemy.normal_move_distance() for enemy in enemies)
        if missing < 0.5 or actor.current_hp > 0.5 or not threatened:
            return -1000.0
        attack = context.best_attack_score()
        return min(0.5, missing) * 200.0 + 75.0 - min(100.0, attack * 0.5)

    def __init__(self) -> None:
        super().__init__(
            "stillness",
            "定",
            "大招：血 +1/2，结束红热；持续 4 轮，无法行动，守 +2，魔免，自然回血。",
            max_uses_per_battle=1,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        red_heat = actor.get_status("红热")
        if red_heat is not None:
            actor.remove_status(red_heat, battle)
        battle.heal(HealContext(source=actor, target=actor, amount=0.5, action_name="定"))
        replace_status_by_name(battle, actor, StillnessStatus())
        battle.log(f"{actor.name} 进入定状态。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class SplitMovementTrait(Trait):
    def __init__(self) -> None:
        super().__init__("没有移动次数限制", "可以多次普通移动，但累计普通移动距离不能超过当前速。")

    def allows_split_normal_movement(self, battle: Battle, actor: HeroUnit) -> bool:
        return self.owner is not None and actor.unit_id == self.owner.unit_id



class AntiSpeedReductionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("不受敌方减速", "不会受到来自对方单位的速度下降或无法位移。")

    def enemy_sourced(self, status: StatusEffect) -> bool:
        player_id = getattr(status, "source_player_id", None)
        return self.owner is not None and player_id is not None and player_id != self.owner.player_id

    def accepts_status(self, status: StatusEffect) -> bool:
        return not (self.enemy_sourced(status) and getattr(status, "flag_name", "") in {"cannot_move", "cannot_normal_move"} and getattr(status, "value", True))

    def modify_received_stat_change(self, status: StatusEffect, stat_name: str, before: float, after: float) -> float:
        return before if stat_name == "speed" and after < before and self.enemy_sourced(status) else after


class AttackLifeStealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击吸血", "普攻造成伤害后，血 +1/4。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.cancelled or ctx.actual_damage <= 0:
            return
        battle.heal(HealContext(source=owner, target=owner, amount=0.25, action_name="攻击吸血"))


class Li(AbstractHero):
    hero_code = "li"
    ai_policy = LiAttackPolicy()
    hero_name = "李"
    role = "勇者"
    attribute = "土"
    race = "人类"
    level = 9
    base_stats = Stats(attack=3, defense=5, speed=3, attack_range=1, mana=5)
    raw_skill_text = "飞跃 链条 变硬 保护 ￥回天（普攻一周） 红热（开关技能，仅可在回合开始时使用，一回合一次；每个己方回合结束时血*1/2；攻+2，速+3） 精华（2轮一次，额外破魔攻击两次，如果在回合结束时有破魔攻击次数没有用过，则魔+剩余次数） 见切（被动技能；一回合一次；挡住一次攻击；下回合攻击次数+1，速+1） ￥定（使用后血+1/2，结束红热状态；效果持续4轮；此单位无法行动，守+2，魔免，自然回血）"
    raw_trait_text = "攻击3次；攻击吸血；没有移动次数限制；不会受到来自对方单位的无法位移效果影响，速度不会被对方单位下降"

    def build_skills(self) -> list[Skill]:
        return [
            DashMoveSkill("leap", "飞跃", "普通技能：费 1 魔，直线移动最多 3 格，可穿过单位。", max_distance=3, mana_cost=1, max_uses_per_turn=1, straight_only=True, ignore_units=True),
            ChainPullSkill(),
            HardenSkill(),
            PassiveProtectionSkill(),
            WhirlwindAttackSkill(),
            RedHeatSkill(),
            EssenceSkill(),
            ForesightSkill(),
            StillnessSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            AttackCountTrait(3),
            AttackLifeStealTrait(),
            SplitMovementTrait(),
            AntiSpeedReductionTrait(),
        ]


