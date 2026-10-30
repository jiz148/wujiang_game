"""R021 iaido and related defensive sword mechanics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable
if TYPE_CHECKING:
    from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext
    from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import FlagStatus
from wujiang.tactical.heroes.common import NextNormalMoveBoostStatus
from wujiang.tactical.heroes.common import PierceSkill
from wujiang.tactical.heroes.common import ShensuSkill
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import localized_line_patterns
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.common import positions_to_dict


class IaidoAttackRules:
    def destination(self, payload: dict[str, Any] | None) -> Position | None:
        payload = payload or {}
        try:
            choice = str(payload.get("choice_code") or "")
            if choice.startswith("iaido:"):
                x, y = choice[6:].split(",")
                return Position(int(x), int(y))
            if payload.get("move_x") is not None and payload.get("move_y") is not None:
                return Position(int(payload["move_x"]), int(payload["move_y"]))
        except (TypeError, ValueError):
            return None
        return None

    def destinations(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.cannot_move or actor.position is None or battle.mounted_unit_for(actor) is not None:
            return []
        return battle.reachable_positions(actor, max_distance=1, exact_distance=1)

    def basic_attack_origins(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None) -> list[Position] | None:
        if not actor.has_status("聚气。拔刀斩") or (payload or {}).get("_attack_pre_move_done"):
            return None
        dest = self.destination(payload)
        destinations = self.destinations(battle, actor)
        return battle.unit_cells_at(actor, dest) if dest in destinations else [cell for pos in destinations for cell in battle.unit_cells_at(actor, pos)] if payload is None else []

    def valid_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit, dest: Position, payload: dict[str, Any]) -> bool:
        cells = battle.unit_cells_at(actor, dest)
        targets = battle.unit_cells(target)
        clicked = Position(int(payload["x"]), int(payload["y"])) if payload.get("x") is not None and payload.get("y") is not None else None
        if clicked is not None:
            targets = [clicked] if clicked in targets else []
        return any(origin.distance_to(cell) <= actor.targeting_range() for origin in cells for cell in targets)

    def can_attack_target_with_payload(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any] | None) -> tuple[bool, str]:
        if not actor.has_status("聚气。拔刀斩") or (payload or {}).get("_attack_pre_move_done"):
            return True, ""
        destinations = self.destinations(battle, actor)
        dest = self.destination(payload)
        if payload is None:
            return (True, "") if any(self.valid_target(battle, actor, target, pos, {}) for pos in destinations) else (False, "没有可命中目标的拔刀落点。")
        if dest not in destinations or not self.valid_target(battle, actor, target, dest, payload):
            return False, "拔刀斩需要合法的一格前移落点和移动后射程内的目标格。"
        return True, ""

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not actor.has_status("聚气。拔刀斩"):
            return {}
        dest = self.destination(payload)
        data = {"attack_power_override": 5, "ignore_shield": True, "cannot_evade": True}
        if dest is not None:
            data.update({"move_x": dest.x, "move_y": dest.y})
        return data

    def basic_attack_preview(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not actor.has_status("聚气。拔刀斩"):
            return None
        choices, cells, ids, destinations = [], [], set(), []
        for dest in self.destinations(battle, actor):
            patterns = []
            choice = f"iaido:{dest.x},{dest.y}"
            for target in battle.enemy_units(actor.player_id):
                if not battle.unit_can_be_selected(target, actor=actor)[0]:
                    continue
                for cell in battle.unit_cells(target):
                    candidate = {"choice_code": choice, "x": cell.x, "y": cell.y}
                    if not battle.attack_target_allowed(actor, target, payload=candidate)[0]:
                        continue
                    patterns.append([cell.to_dict()])
                    cells.append(cell)
                    ids.add(target.unit_id)
                    destinations.append({"choice_code": choice, "pattern": [cell.to_dict()], "destination_cells": positions_to_dict(battle.unit_cells_at(actor, dest))})
            if patterns:
                choices.append({"code": choice, "label": f"前移至 ({dest.x}, {dest.y})", "patterns": patterns})
        return {"cells": positions_to_dict(cells), "target_unit_ids": sorted(ids), "secondary_cells": [],
                "requires_target": True, "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False}}

    def before_basic_attack_resolution(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        if not actor.has_status("聚气。拔刀斩"):
            return True
        dest = self.destination(payload)
        origin = battle.declared_source_position(payload) if payload.get("queued_resolution") else actor.position
        if actor.position != origin or dest not in self.destinations(battle, actor):
            return False
        try:
            battle.move_unit(actor, dest, via_skill=True, max_distance=1, exact_distance=1, tags={"iaido_charge"})
        except ActionError:
            return False
        payload["_attack_pre_move_done"] = True
        return actor.alive and not actor.banished


class IaidoChargeStatus(IaidoAttackRules, StatusEffect):
    def __init__(self) -> None:
        super().__init__("聚气。拔刀斩", "下次普攻前速度视为1；选择前移一格后攻击，伤5、破魔、无法回避。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return 1.0 if stat_name == "speed" else value

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is not None:
            gained = self.owner.gain_mana_points(1)
            battle.log(f"{self.owner.name} 因【聚气。拔刀斩】获得 {gained} 点魔力点。")

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if self.owner is actor:
            actor.remove_status(self, battle)


class IaidoChargeSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        if actor.has_status("聚气。拔刀斩"):
            return -1000.0
        best_attack = context.best_attack_score()
        remaining = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
        if best_attack > 0:
            threats = [enemy for enemy in context.hostile_units()
                       if battle.distance_between_units(actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range()
                       and not battle.attack_ignores_shield(enemy, actor)]
            preventable = sorted((context.attack_damage(enemy, actor) for enemy in threats), reverse=True)[:remaining]
            can_kill_now = any(battle.attack_target_allowed(actor, enemy)[0]
                               and context.attack_damage(actor, enemy) >= enemy.current_hp
                               for enemy in threats)
            if remaining and sum(preventable) >= actor.current_hp > 0 and not can_kill_now:
                return best_attack + 35.0
            return -1000.0
        nearby = any(battle.distance_between_units(actor, enemy) <= 4 for enemy in context.hostile_units())
        return 30.0 + remaining * (12.0 if nearby else 2.0) + (18.0 if actor.mana_points < 3 else 0.0)

    def __init__(self) -> None:
        super().__init__("iaido_charge", "聚气。拔刀斩", "普通技能：每回合1次；本回合不能攻击，下次普攻先移动1格，伤5、破魔、无法回避。", max_uses_per_turn=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not actor.has_status("聚气。拔刀斩"):
            actor.add_status(IaidoChargeStatus())
        if not actor.has_status("聚气当回合禁攻"):
            actor.add_status(FlagStatus("聚气当回合禁攻", "cannot_attack", duration=1))
        battle.log(f"{actor.name} 进入【聚气。拔刀斩】状态。")


class PerfectDeflectStatus(StatusEffect):
    def __init__(self, charges: int) -> None:
        self.charges = charges
        self.blocked_token = None
        super().__init__("剩余攻击挡开", f"可挡开{charges}次非破魔攻击或技能。", duration=1, tick_scope="owner_turn_start")

    def block(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        if owner is not ctx.target or source is None or source.player_id == owner.player_id or ctx.from_field_effect:
            return
        if ctx.ignore_shield or ctx.half_ignore_shield or not (ctx.is_skill or "attack" in ctx.tags):
            return
        token = battle._current_action_resolution_token
        if token is None:
            token = id(ctx)
        if self.blocked_token != token:
            if self.charges <= 0 or ctx.cancelled:
                return
            self.charges -= 1
            self.blocked_token = token
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【剩余攻击挡开】挡住了【{ctx.action_name}】。"

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if ctx.is_skill and not ctx.damage_target and "attack" not in ctx.tags:
            self.block(battle, ctx)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self.block(battle, ctx)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["charges"] = self.charges
        return data


class PerfectSwordsmanDefenseTrait(Trait):
    def __init__(self) -> None:
        super().__init__("剩余攻击转挡开", "回合结束时将剩余攻击次数转为防守回合挡开次数，破魔无效。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        remaining = max(0, owner.attack_actions_per_turn() - owner.attacks_used)
        old = owner.get_status("剩余攻击挡开")
        if old is not None:
            owner.remove_status(old, battle)
        if remaining > 0:
            owner.add_status(PerfectDeflectStatus(remaining))
            battle.log(f"{owner.name} 将 {remaining} 次剩余攻击转为挡开次数。")


class TimeStopSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        return self.ai_turn_interrupt_value(
            context.battle, context.actor,
            lambda: context.hostile_units(),
            lambda target, power: context.estimate_damage(target, power))

    def __init__(self) -> None:
        super().__init__("time_stop", "时停", "随时大招：仅在敌方回合且周围5×5有敌人时，结束当前回合并插入只有自身可行动的回合；之后恢复原顺序。", max_uses_per_battle=1, target_mode="self", timing="instant")

    def condition(self, battle: Battle, actor: HeroUnit) -> tuple[bool, str]:
        if actor.player_id == battle.active_player:
            return False, "时停只能在对方回合使用。"
        if not any(unit.alive and not unit.banished and unit.position is not None and battle.distance_between_units(actor, unit) <= 2
                   for unit in battle.enemy_units(actor.player_id)):
            return False, "时停需要5×5内有对方单位。"
        return True, ""

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        return self.condition(battle, actor) if ok else (ok, reason)

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        return self.condition(battle, actor) if ok else (ok, reason)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.unit_id not in battle.turn_order_unit_ids:
            return
        battle._pending_exclusive_turn_unit_id = actor.unit_id
        battle.pending_followup_actions.clear()
        battle._separate_attack_sequences.clear()
        battle.pending_chain = None
        battle.log(f"{actor.name} 发动【时停】，结束当前回合并插入自己的独占回合。")
        battle.resolve_turn_end()

    def ai_turn_interrupt_value(self, battle: Battle, actor: HeroUnit,
                                hostile_units: Callable[[], Iterable[HeroUnit]],
                                damage: Callable[[HeroUnit, float], float]) -> float:
        if not self.condition(battle, actor)[0]:
            return -1000.0
        active = battle.current_turn_unit()
        if active is None:
            return -1000.0
        attacks = max(0, active.attack_actions_per_turn() - active.attacks_used)
        skills_left = sum(1 for item in active.skills if item.timing == "active" and item.can_use(battle, active, {})[0])
        retaliation = sum(damage(enemy, 5 if actor.has_status("聚气。拔刀斩") else actor.stat("attack")) * 45.0
                          for enemy in hostile_units() if battle.distance_between_units(actor, enemy) <= 3)
        return min(3, attacks) * 28.0 + min(3, skills_left) * 12.0 + retaliation - 45.0

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        threat = context.threat(context.proxy_target())
        return self.ai_turn_interrupt_value(context.battle, context.actor,
                                            context.hostile_units, context.damage) + threat


class FocusSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        actor = context.actor
        stop = next((item for item in actor.skills if item.code == "time_stop"), None)
        return 65.0 if stop is not None and stop.uses_this_battle > 0 and actor.mana_points >= 3 else -1000.0

    def __init__(self) -> None:
        super().__init__("focus_reset", "定神", "普通技能：每回合 1 次；魔力点 -3，重置【时停】。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.mana_points < 3:
            return False, "魔力点不足。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.sync_turn_scope(battle)
        actor.spend_mana_points(3)
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        skill = actor.get_skill("time_stop")
        skill.uses_this_battle = 0
        skill.cooldown_remaining = 0
        battle.log(f"{actor.name} 使用【定神】重置【时停】。")


class DPantherManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("D。魔力点", "每回合开始按场上己方显示名带 D。的单位数获得魔力点，最多 3。")

    def bind(self, owner: HeroUnit) -> "DPantherManaTrait":
        super().bind(owner)
        owner.max_mana_points = 3.0
        owner.mana_points = min(3.0, owner.mana_points)
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        count = sum(1 for unit in battle.player_units(owner.player_id) if unit.position is not None and not unit.banished and "D。" in unit.name)
        before = owner.mana_points
        owner.mana_points = round(min(3.0, owner.mana_points + count), 2)
        gained = round(owner.mana_points - before, 2)
        if gained:
            battle.log(f"{owner.name} 因 D。名字获得 {gained} 点魔力点。")


class LargePiercePlusSkill(PierceSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "large_pierce_plus"
        self.name = "穿刺（大）"
        self.description = "普通技能：通用穿刺扩大 1 格，选择连续 3 格直线。"

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        actor_cells = {(cell.x, cell.y) for cell in battle.unit_cells(actor)}
        origins = battle.unit_cells(actor) or ([actor.position] if actor.position else [])
        for origin in origins:
            for pattern in localized_line_patterns(
                battle,
                origin,
                self.directions(),
                3,
                max_distance=3,
                touch_distance=1,
            ):
                if any((cell.x, cell.y) in actor_cells for cell in pattern):
                    continue
                key = pattern_signature(pattern)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(pattern)
        return patterns


    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name, area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", "attack", "pierce"}))


class ManaDrainImmunityTrait(Trait):
    prevents_mana_drain = True

    def __init__(self) -> None:
        super().__init__("无法被吸魔", "免疫双方吸魔效果。")


class BigShensuSkill(ShensuSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "big_shensu"
        self.name = "神速（大）"
        self.description = "普通技能：本回合下一次普通移动距离 +4。"

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        existing = actor.get_status("神速")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(NextNormalMoveBoostStatus(4))
        battle.log(f"{actor.name} 获得神速（大），本回合下一次普通移动距离 +4。")


class PierceImmunityTrait(Trait):
    ignores_incoming_piercing = True

    def __init__(self) -> None:
        super().__init__("不受破魔", "破魔对该单位失效，护盾和魔免仍可正常阻挡。")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is not None and ctx.target is self.owner:
            ctx.ignore_shield = ctx.half_ignore_shield = ctx.ignore_magic_immunity = False

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        ctx.ignore_shield = False
        ctx.half_ignore_shield = False
        ctx.ignore_magic_immunity = False


class FeiWangSpeedOnHeroKillTrait(Trait):
    def __init__(self) -> None:
        super().__init__("破将加速", "每破坏一个武将速度 +1。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if (ctx.cancelled or ctx.actual_damage <= 0 or ctx.target.alive or ctx.target.is_summon
                or ctx.target.is_clone or is_army_soldier(ctx.target) or not isinstance(ctx.target, HeroUnit) or not owner.alive):
            return
        if any(ctx is previous for previous in getattr(self, "counted_destructions", ())):
            return
        # Context identity marks a destruction event, so revival can yield a new event.
        self.counted_destructions = [ctx]
        owner.add_status(StatModifierStatus("破将加速", speed_delta=1, duration=None, description="每破坏一个武将速度 +1。"))


