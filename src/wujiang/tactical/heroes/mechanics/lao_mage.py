"""R033 Lao mage wave, hand, and cloak mechanics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext
    from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import FlyingTrait
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import ensure_enemy
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS, square_around_cells
from wujiang.tactical.heroes.mechanics.soul_wraith import PassThroughMovementTrait
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns


class LaoWaveBulletSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        score = context.damage_score(self)
        cost = self.mana_cost_for_payload(context.battle, context.actor, context.payload)
        return score - cost * (30.0 if context.actor.current_mana <= 1.0 else 15.0)

    def __init__(self) -> None:
        super().__init__(
            "lao_wave_bullet",
            "波导弹",
            "普通技能：费 1 魔，每回合最多 1 次；远程 4*4 区域造成当前攻击伤害。可选择不花魔，此时伤害 -1。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def _free(self, payload: dict[str, Any] | None) -> bool:
        return bool(payload and (payload.get("free_cast") is True or payload.get("choice_code") == "free"))

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        if self._free(payload):
            return 0.0
        return super().mana_cost_for_payload(battle, actor, payload)

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        if payload and "free_cast" in payload and type(payload["free_cast"]) is not bool:
            return False, "波导弹的免费模式必须明确选择。"
        if payload and payload.get("choice_code") not in {None, "paid", "free"}:
            return False, "请选择波导弹的付费或免费模式。"
        return super().can_use(battle, actor, payload)

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 4, 4)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        cells = [payload_position(item) for item in payload.get("cells", [])]
        signature = pattern_signature(cells)
        legal = {pattern_signature(pattern): pattern for pattern in self.patterns(battle, actor)}
        if signature not in legal:
            raise ActionError("请选择合法的波导弹区域。")
        return legal[signature]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        attack_power = actor.stat("attack") - (1 if self._free(payload) else 0)
        for target in battle.effect_units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=attack_power,
                    is_skill=True,
                    action_name="波导弹",
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "attack", "lao_wave_bullet"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        selectable = preview["selection"]["patterns"]
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                        "selection": {"mode": "choice_pattern", "ordered": False,
                                      "choices": [{"code": "paid", "label": "费1魔：完整攻值", "patterns": selectable},
                                                  {"code": "free", "label": "免费：攻值-1", "patterns": selectable}],
                                      "choice_prompt": "选择波导弹费用模式。",
                                      "cell_prompt": "逐格选择4×4结算区域。"}})
        return preview


class LaoMageHandSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "lao_mage_hand",
            "法师之手",
            "普通技能：每回合最多 1 次；对一个近战目标造成普攻伤害，破魔；命中后按选择方向尽量推动 3 格。",
            max_uses_per_turn=1,
            target_mode="enemy",
            direction_mode="required",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return 1

    def _direction(self, payload: dict[str, Any]) -> tuple[int, int]:
        direction = payload.get("direction")
        if isinstance(direction, dict):
            dx = int(direction.get("dx", 0) or 0)
            dy = int(direction.get("dy", 0) or 0)
        else:
            dx = int(payload.get("dx", 0) or 0)
            dy = int(payload.get("dy", 0) or 0)
        if (dx, dy) not in ALL_DIRECTIONS:
            raise ActionError("请选择法师之手推动方向。")
        return dx, dy

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        if not battle.unit_target_in_range_and_line(actor, target, 1):
            raise ActionError("法师之手只能选择近战范围内的目标。")
        dx, dy = self._direction(payload)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=False,
                ignore_shield=True,
                action_name="法师之手",
                tags={"skill", "attack", "basic_attack_damage", "lao_mage_hand"},
            )
        )
        if not damage_followup_effect_applies(ctx):
            return
        effect_ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                            is_hostile=True, ignore_shield=True,
                                            tags={"skill", "lao_mage_hand"})
        if effect_ctx.cancelled:
            return
        destination = target.position
        if destination is None:
            return
        push_steps: list[Position] = []
        for _ in range(3):
            next_cell = Position(destination.x + dx, destination.y + dy)
            if not battle.in_bounds(next_cell) or not battle.can_place_unit(target, next_cell, ignore=target, mover=target):
                break
            destination = next_cell
            push_steps.append(next_cell)
        if push_steps:
            battle.move_unit(target, destination, via_skill=True, forced=True, max_distance=3,
                             straight_only=True, path=push_steps, tags={"lao_mage_hand"})

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.position is not None and battle.unit_target_in_range_and_line(actor, unit, 1)
        ]
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
            "selection": {
                "mode": "unit_direction",
                "directions": [{"dx": dx, "dy": dy,
                                "label": {(-1, -1): "↖", (-1, 0): "←", (-1, 1): "↙",
                                          (0, -1): "↑", (0, 1): "↓", (1, -1): "↗",
                                          (1, 0): "→", (1, 1): "↘"}[(dx, dy)]}
                               for dx, dy in ALL_DIRECTIONS],
            },
        }

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class MageCloakEquippedStatus(StatModifierStatus):
    def __init__(self, summoner_id: str, cloak_unit_id: str) -> None:
        super().__init__("法师斗篷", speed_delta=3, defense_delta=1, description="速 +3，守 +1，移动次数 +1，飞行，近战普攻伤害 +1。")
        self.summoner_id = summoner_id
        self.cloak_unit_id = cloak_unit_id
        self.detaching = False

    def bind(self, owner: HeroUnit) -> "MageCloakEquippedStatus":
        super().bind(owner)
        owner.ignore_units_while_moving = True
        owner.has_flying = True
        owner.skills.append(MageCloakDetachSkill().bind(owner))
        return self

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + 1

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags:
            return
        if ctx.target.position is not None and battle.unit_target_in_range_and_line(owner, ctx.target, 1):
            ctx.attack_power += 1

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.skills = [skill for skill in owner.skills if skill.code != "detach_mage_cloak"]
        owner.has_flying = any(isinstance(component, FlyingTrait) for component in owner.traits) or any(
            isinstance(status, MageCloakEquippedStatus) for status in owner.statuses
        )
        owner.ignore_units_while_moving = any(isinstance(component, PassThroughMovementTrait) for component in owner.traits) or owner.has_flying
        if not self.detaching:
            cloak = battle.units.get(self.cloak_unit_id)
            if cloak is not None and cloak.alive and getattr(cloak, "equipped_to_id", None) == owner.unit_id:
                cloak.alive = False
                cloak.position = owner.position or getattr(owner, "last_position", None)
                battle.log(f"{cloak.name} 随装备状态结束而破坏。")
                battle.capture_destruction_position(cloak)
                if cloak.unit_id not in {unit.unit_id for unit in battle.destroyed_units}:
                    battle.destroyed_units.append(cloak)
                battle.remove_unit(cloak)

    def on_owner_removed(self, battle: Battle) -> None:
        self.on_removed(battle)

    def sync_linked_state(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or self not in owner.statuses:
            return
        cloak = battle.units.get(self.cloak_unit_id)
        summoner = battle.units.get(self.summoner_id)
        if (not owner.alive or cloak is None or not cloak.alive or summoner is None or not summoner.alive):
            owner.remove_status(self, battle)


class MageCloakSummonedStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("法师斗篷已召唤", "已经使用过法师斗篷大招。", duration=None)


class MageCloakEquipSkill(Skill):
    def __init__(self) -> None:
        super().__init__("equip_mage_cloak", "装备斗篷", "装备到范内一个法师单位上，斗篷保留原身份但暂不占格。", target_mode="unit")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.summoner_id is None:
            return False, "法师斗篷没有召唤者。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target.role != "法师":
            raise ActionError("法师斗篷只能装备到法师单位。")
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range(), action_name=self.name)
        if target.get_status("法师斗篷") is not None:
            raise ActionError("该单位已经装备法师斗篷。")
        status = MageCloakEquippedStatus(str(actor.summoner_id), actor.unit_id)
        target.add_status(status, source=actor)
        if status not in target.statuses:
            battle.log(f"{target.name} 未受到【法师斗篷】装备效果。")
            return
        actor.equipped_to_id = target.unit_id
        actor.position = None
        battle.log(f"{actor.name} 装备到 {target.name} 身上。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.position is not None
            and unit.role == "法师"
            and unit.get_status("法师斗篷") is None
            and battle.unit_can_be_selected(unit, actor=actor)[0]
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class MageCloakDetachSkill(Skill):
    def __init__(self) -> None:
        super().__init__("detach_mage_cloak", "解除法师斗篷", "由装备者选择周围完整合法空格，原斗篷满血重新占格。", target_mode="cell")

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        status = actor.get_status("法师斗篷")
        if not isinstance(status, MageCloakEquippedStatus):
            return []
        cloak = battle.units.get(status.cloak_unit_id)
        if cloak is None or not cloak.alive or getattr(cloak, "equipped_to_id", None) != actor.unit_id:
            return []
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if battle.can_place_unit(cloak, cell, ignore=cloak, mover=cloak)]

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        return (True, "") if self.available_cells(battle, actor) else (False, "装备者周围没有斗篷可落的合法空格。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            raise ActionError("请选择装备者周围的合法空格解除斗篷。")
        status = actor.get_status("法师斗篷")
        assert isinstance(status, MageCloakEquippedStatus)
        cloak = battle.units[status.cloak_unit_id]
        status.detaching = True
        actor.remove_status(status, battle)
        cloak.equipped_to_id = None
        cloak.position = destination
        cloak.current_hp = cloak.max_health
        battle.log(f"{actor.name} 解除【法师斗篷】，原斗篷在周围满血重现。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {"cells": [cell.to_dict() for cell in self.available_cells(battle, actor)],
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class MageCloakSummon(AbstractHero):
    hero_code = "mage_cloak"
    hero_name = "法师斗篷"
    role = "装备"
    attribute = "土"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=1, defense=2, speed=5, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)
        self.can_act_on_entry_turn = True
        self.turn_ready = True

    def build_skills(self) -> list[Skill]:
        return [MageCloakEquipSkill()]

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait()]


class MageCloakSkill(Skill):
    def __init__(self) -> None:
        super().__init__("summon_mage_cloak", "法师斗篷", "大招：本场一次，在周围合法格召唤可行动的法师斗篷。", max_uses_per_battle=1, target_mode="cell")

    def own_cloak_alive(self, battle: Battle, actor: HeroUnit) -> HeroUnit | None:
        for unit in battle.player_units(actor.player_id):
            if getattr(unit, "hero_code", "") == "mage_cloak" and unit.summoner_id == actor.unit_id and unit.alive:
                return unit  # type: ignore[return-value]
        return None

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if self.own_cloak_alive(battle, actor) is not None:
            return False, "法师斗篷已经在场。"
        if actor.get_status("法师斗篷已召唤") is not None:
            return False, "法师斗篷大招本场已经使用过。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        cells = self.available_cells(battle, actor)
        if destination not in cells:
            raise ActionError("请选择周围合法格召唤法师斗篷。")
        cloak = MageCloakSummon(actor.player_id)
        battle.summon_unit(cloak, destination, summoner=actor)
        cloak.turn_ready = True
        cloak.can_act_on_entry_turn = True
        actor.add_status(MageCloakSummonedStatus())
        battle.log(f"{actor.name} 召唤【法师斗篷】。")

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if battle.can_place_unit(MageCloakSummon(actor.player_id), cell)]

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class LaoDamageStatReadyStatus(StatusEffect):
    def __init__(self, stat_name: str, declaration_id: str) -> None:
        super().__init__("能力抵消准备", f"若该动作造成实际伤害，永久降低{stat_name}1点并抵消该次伤害。", duration=1)
        self.stat_name = stat_name
        self.expires_after_declaration_id = declaration_id


class LaoDamageStatCancelSkill(Skill):
    STAT_NAMES = {"attack": "攻", "defense": "守", "speed": "速", "attack_range": "范"}
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__("lao_damage_stat_cancel", "能力抵消", "连锁：选择攻/守/速/范之一永久-1，最低1；只在当前动作实际造成伤害时支付并抵消该次伤害。", timing="passive", target_mode="self")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if actor not in battle.reaction_affected_units(queued_action):
            return False, "该动作不会影响拉奥。"
        if not any(actor.stat(name) > 1 for name in self.STAT_NAMES):
            return False, "没有可支付的能力值。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        stat_name = str((payload or {}).get("stat_name") or "")
        if stat_name not in self.STAT_NAMES or actor.stat(stat_name) <= 1:
            return False, "请选择当前高于1的攻、守、速或范。"
        return True, ""

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "stat_cells", "required_cells": 0,
                              "prompt": "选择要永久降低1点的能力；若该动作未造成实际伤害，则不会支付。",
                              "stats": [{"code": name, "label": label} for name, label in self.STAT_NAMES.items() if actor.stat(name) > 1]}}

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        actor.add_status(LaoDamageStatReadyStatus(str(payload["stat_name"]), str(queued_action.payload.get("declaration_id") or "")))
        battle.log(f"{actor.name} 准备以【{self.STAT_NAMES[str(payload['stat_name'])]}】抵消该动作的实际伤害。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("能力抵消只能在连锁时选择。")

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        if context.queued_action.action_type == "skill" and not context.queued_skill_damaging():
            return -1000.0
        threat = context.threat(context.actor)
        stat = str(context.payload.get("stat_name") or "")
        cost = {"attack": 48.0, "defense": 52.0, "speed": 40.0,
                "attack_range": 44.0}.get(stat)
        if cost is None or threat <= 0:
            return -1000.0
        if context.actor.stat(stat) <= 2:
            cost += 30.0
        if context.actor.current_hp <= threat / 100.0:
            threat += 100.0
        return threat - cost


class LaoDamageStatCancelTrait(Trait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("能力抵消", "受伤前可将攻/守/速/范之一 -1 到最低 1，取消该次伤害。")

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.cancelled:
            return
        ready = next((status for status in owner.statuses if isinstance(status, LaoDamageStatReadyStatus)), None)
        action = battle.resolving_action
        amount = ctx.raw_damage if ctx.raw_damage is not None else battle.damage_rule.calculate_damage(ctx.attack_power, owner.stat("defense"))
        if owner.damage_fraction_after_limits(amount) <= 0:
            return
        if (ready is not None and action is not None
                and ready.expires_after_declaration_id == action.payload.get("declaration_id")
                and owner.stat(ready.stat_name) > 1):
            stat = ready.stat_name
        else:
            available = [name for name in LaoDamageStatCancelSkill.STAT_NAMES if owner.stat(name) > 1]
            stat = battle.damage_stat_choice(ctx, available) if available else None
        if not stat:
            return
        kwargs = {
            "attack": {"attack_delta": -1},
            "defense": {"defense_delta": -1},
            "speed": {"speed_delta": -1},
            "attack_range": {"range_delta": -1},
        }[stat]
        owner.add_status(StatModifierStatus("能力抵消", description=f"{stat} -1。", duration=None, **kwargs))
        if ready is not None and stat == ready.stat_name:
            owner.remove_status(ready, battle)
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 将 {stat} -1，抵消了【{ctx.action_name}】的伤害。"


