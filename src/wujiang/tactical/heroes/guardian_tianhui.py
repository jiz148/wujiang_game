"""R349: Shi Xiu's guardian controls movement, dragons and a lethal window."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, QueuedAction, Skill, Stats, StatusEffect,
    Trait, Unit, is_army_soldier,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import DashMoveSkill, ShensuSkill
from wujiang.tactical.heroes.mechanics.guardian_spirit import (
    GuardianSummonerManaTrait, GuardianTetherTrait, SummonGuardianSkill, present,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


def _next_owner_turn_has_ended(battle: Battle, applied_turn: int) -> bool:
    return battle.turn_number > applied_turn


class NextOwnerTurnControl(StatusEffect):
    """The recipient keeps a full own turn even if hit during that turn."""

    def __init__(self, name: str, description: str, applied_turn: int) -> None:
        super().__init__(name, description, duration=None)
        self.applied_turn = applied_turn

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is not None and _next_owner_turn_has_ended(battle, self.applied_turn):
            self.owner.remove_status(self, battle)


class ClawMovementSeal(NextOwnerTurnControl):
    def __init__(self, applied_turn: int) -> None:
        super().__init__("天刑爪", "直到下一本人回合结束不能使用位移技能。", applied_turn)

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and skill.is_displacement_skill:
            return True, "天刑爪封锁了位移技能。"
        return False, ""


class DragonPushControl(NextOwnerTurnControl):
    def __init__(self, applied_turn: int, seal_active: bool) -> None:
        super().__init__("破龙靠", "下一本人回合速-1；飞龙或古龙不能使用主动技能。", applied_turn)
        self.seal_active = seal_active

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1, value - 1) if stat_name == "speed" else value

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and self.seal_active and skill.timing == "active":
            return True, "破龙靠封锁了主动技能。"
        return False, ""


class StarThoughtProtection(StatusEffect):
    """A precommitted ward that lasts for the next enemy hero turn only."""

    def __init__(self, friendly_player_id: int) -> None:
        super().__init__("占星术。想", "下个敌方英雄回合内，致命伤后保留1/4最大血。")
        self.friendly_player_id = friendly_player_id
        self.active_enemy_turn = False

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        if (self.owner is not None and active_unit.player_id != self.friendly_player_id
                and not active_unit.is_summon and not active_unit.is_clone
                and not is_army_soldier(active_unit)):
            self.active_enemy_turn = True

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None and self.active_enemy_turn and ended_player_id != self.friendly_player_id:
            self.owner.remove_status(self, battle)

    def lethal_damage_remaining_hp(self, battle: Battle, ctx: DamageContext) -> float | None:
        if self.owner is ctx.target and self.active_enemy_turn:
            ctx.modifier_receipts.add("star_thought")
            return ctx.target.max_health / 4
        return None

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and "star_thought" in ctx.modifier_receipts:
            # Lethal mitigation never raises HP by itself.  The star's explicit
            # quarter-HP result also applies when the target was already below it.
            ctx.target.current_hp = ctx.target.max_health / 4
            battle.log_public_event(f"{ctx.target.name} 受占星术。想保护，致命伤后留在场上。",
                                    source=ctx.source, target=ctx.target)


def _legal_enemies(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.all_units() if present(unit)
            and unit.player_id != actor.player_id
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
            and battle.unit_can_be_selected(unit, actor=actor)[0]]


def _damage_value(battle: Battle, actor: Unit, target: Unit, *, half_pierce: bool = False) -> float:
    if target.magic_immunity:
        return 0.0
    attack = actor.stat("attack") - (1 if half_pierce and target.total_shields() else 0)
    return min(target.current_hp, battle.damage_rule.calculate_damage(attack, target.stat("defense")))


class TianxingClawSkill(Skill):
    ai_payload_value_owned = True
    ai_probe_hostile_effect = True

    def __init__(self) -> None:
        super().__init__("tianxing_claw", "天刑爪",
                         "每回合一次；半破魔伤，命中者至下一本人回合结束不能用位移技能。",
                         max_uses_per_turn=1, target_mode="enemy")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return _legal_enemies(battle, actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [],
                "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return [target] if target is not None else []

    def half_ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                        payload: dict[str, Any]) -> bool:
        return True

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if payload and payload.get("target_unit_id") and str(payload["target_unit_id"]) not in {
                unit.unit_id for unit in targets}:
            return False, "天刑爪目标不在合法范围内。"
        return (True, "") if targets else (False, "没有合法的天刑爪目标。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units.get(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            battle.log("天刑爪未命中仍在合法范围的单位。")
            return
        ctx = battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
            action_name=self.name, half_ignore_shield=True, tags={"skill", self.code},
        ))
        recipient = ctx.target
        if not ctx.cancelled and present(recipient) and not recipient.skill_non_damage_effects_blocked():
            for status in list(recipient.statuses):
                if isinstance(status, ClawMovementSeal):
                    recipient.remove_status(status, battle)
            recipient.add_status(ClawMovementSeal(battle.turn_number), source=actor)
            battle.log(f"{recipient.name} 被天刑爪封锁位移技能，直到下一本人回合结束。")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor) or target.magic_immunity:
            return -100.0
        damage = _damage_value(battle, actor, target, half_pierce=True)
        mobility = sum(1 for skill in target.skills if skill.is_displacement_skill)
        control = 22.0 * mobility if not target.has_status("天刑爪") else 0.0
        return damage * 100.0 + control + (10.0 if target.total_shields() else 0.0) - 15.0

    def ai_priority_score(self, context: Any) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -100.0


def _backward_step(battle: Battle, actor: Unit, target: Unit) -> None:
    if actor.position is None or target.position is None:
        return
    dx = (target.position.x > actor.position.x) - (target.position.x < actor.position.x)
    dy = (target.position.y > actor.position.y) - (target.position.y < actor.position.y)
    landing = target.position.offset(dx, dy)
    if (dx == 0 and dy == 0) or not battle.in_bounds(landing):
        return
    try:
        battle.move_unit(target, landing, via_skill=True, forced=True,
                         straight_only=True, max_distance=1, tags={"polong_kao"})
        battle.log(f"{target.name} 被破龙靠推离 {actor.name} 一格。")
    except ActionError:
        battle.log(f"{target.name} 的破龙靠位移被阻挡。")


def _kao_followup(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
    target = battle.units.get(str(queued.payload.get("victim_id") or ""))
    if not present(actor) or not present(target):
        return
    _backward_step(battle, actor, target)
    if not present(target) or target.skill_non_damage_effects_blocked():
        return
    dragon = "飞龙" in target.name or "古龙" in target.name
    for status in list(target.statuses):
        if isinstance(status, DragonPushControl):
            target.remove_status(status, battle)
    target.add_status(DragonPushControl(battle.turn_number, dragon), source=actor)
    battle.log(f"{target.name} 受破龙靠影响：下一本人回合速-1" +
               ("、不能使用主动技能。" if dragon else "。"))


class PolongKaoSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("polong_kao", "破龙靠",
                         "每回合一次；外邻一周破魔伤、背推一格、下轮速-1，飞龙古龙另封主动。",
                         max_uses_per_turn=1, target_mode="self")

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        body = battle.unit_cells(actor)
        return [cell for cell in square_around_cells(battle, body, radius=1) if cell not in body]

    def recipients(self, battle: Battle, actor: Unit) -> list[Unit]:
        return battle.effect_units_at_cells(self.cells(battle, actor))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [cell.to_dict() for cell in self.cells(battle, actor)],
                "target_unit_ids": [unit.unit_id for unit in self.recipients(battle, actor)],
                "secondary_cells": [], "requires_target": False}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.recipients(battle, actor)

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        return (ok, reason) if not ok or self.recipients(battle, actor) else (False, "破龙靠周围没有受体。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        hit: list[Unit] = []
        for unit in self.recipients(battle, actor):
            if not present(unit):
                continue
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"), is_skill=True,
                action_name=self.name, ignore_shield=True, tags={"skill", self.code},
            ))
            recipient = ctx.target
            if (not ctx.cancelled and present(recipient)
                    and not recipient.skill_non_damage_effects_blocked() and recipient not in hit):
                hit.append(recipient)
        for recipient in hit:
            if not present(recipient):
                continue
            battle.queue_skill_effect_action(
                actor=actor, display_name="破龙靠推位", effect_code="polong_kao_push",
                effect_resolver=_kao_followup,
                payload={"victim_id": recipient.unit_id, "ignore_shield": True},
                target_units=[recipient], target_cells=battle.unit_cells(recipient),
                include_cell_units=False,
            )

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        score = -25.0
        for target in self.recipients(battle, actor):
            if target.magic_immunity:
                continue
            damage = _damage_value(battle, actor, target)
            benefit = damage * 100.0 + 18.0
            if "飞龙" in target.name or "古龙" in target.name:
                benefit += 40.0
            if target.player_id == actor.player_id:
                benefit *= -1.6
            score += benefit
        return score

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool | None:
        return any(target.player_id != actor.player_id and not target.magic_immunity
                   for target in self.recipients(battle, actor))

    def ai_priority_score(self, context: Any) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -100.0


class StarThoughtSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("star_thought", "占星术。想",
                         "每场一次；保护范内一名己方单位，在下个敌方英雄轮致命伤后留1/4血。",
                         max_uses_per_battle=1, target_mode="ally")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if present(unit)
                and unit.player_id == actor.player_id
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
                and battle.unit_can_be_selected(unit, actor=actor)[0]]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [],
                "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return [target] if target is not None else []

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if payload and payload.get("target_unit_id") and str(payload["target_unit_id"]) not in {
                unit.unit_id for unit in targets}:
            return False, "占星术。想目标不在合法范围内。"
        return (True, "") if targets else (False, "没有合法的占星术。想目标。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units.get(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor) or target.skill_non_damage_effects_blocked():
            battle.log("占星术。想没有影响合法友军。")
            return
        for status in list(target.statuses):
            if isinstance(status, StarThoughtProtection):
                target.remove_status(status, battle)
        target.add_status(StarThoughtProtection(actor.player_id), source=actor)
        battle.log(f"{target.name} 获得占星术。想：下一敌方英雄轮致命伤后保留生命。")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor) or target.has_status("占星术。想"):
            return -100.0
        enemies = [unit for unit in battle.all_units() if present(unit)
                   and unit.player_id != actor.player_id and battle.unit_can_be_selected(unit, actor=actor)[0]]
        danger = max((battle.damage_rule.calculate_damage(unit.stat("attack"), target.stat("defense"))
                      for unit in enemies if battle.distance_between_units(unit, target) <= unit.targeting_range() + 2),
                     default=0)
        if danger <= 0 or target.current_hp > max(0.25, danger) * 1.25:
            return -100.0
        return 55.0 + min(100.0, max(0.0, danger - target.current_hp) * 60.0)

    def ai_priority_score(self, context: Any) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -100.0


class TianhuiGuardian(AbstractHero):
    hero_code = "guardian_tianhui"
    hero_name = "守护灵。天慧"
    role = "召唤物"
    attribute = "土"
    race = "守护灵"
    level = 1
    base_stats = Stats(attack=5, defense=999, speed=4, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [ShensuSkill(), DashMoveSkill(
            "fly_leap", "飞跃", "费1魔；每回合一次，直线飞行移动恰好3格。",
            max_distance=3, exact_distance=3, mana_cost=1, max_uses_per_turn=1,
            straight_only=True, ignore_units=True,
        ), TianxingClawSkill(), PolongKaoSkill(), StarThoughtSkill()]

    def build_traits(self) -> list[Trait]:
        return [GuardianTetherTrait(), GuardianSummonerManaTrait()]


class SummonTianhuiSkill(SummonGuardianSkill):
    def __init__(self) -> None:
        super().__init__("summon_guardian_tianhui", "守护灵。天慧", TianhuiGuardian)
        self.description = "每场一次；在本人周围召唤攻5、守无限、速4、范1的天慧守护灵。"
