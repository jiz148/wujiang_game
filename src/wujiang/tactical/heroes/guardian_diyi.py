"""R348: Zheng Tianshou's sword, die-limited support and stealth detector."""

from __future__ import annotations

import random
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, QueuedAction, Skill, Stats, StatusEffect, Trait, Unit,
    is_army_soldier,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import payload_direction
from wujiang.tactical.heroes.mechanics.guardian_spirit import (
    GuardianTetherTrait, SummonGuardianSkill, present,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


def _true_hero(unit: Unit) -> bool:
    return not unit.is_summon and not unit.is_clone and not is_army_soldier(unit)


def _hit_value(battle: Battle, actor: Unit, target: Unit) -> float:
    if target.magic_immunity:
        return 0.0
    damage = min(target.current_hp, battle.damage_rule.calculate_damage(
        actor.stat("attack"), target.stat("defense")))
    return damage * (120.0 if target.player_id != actor.player_id else -175.0)


def _sword_followup(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
    target = battle.units.get(str(queued.payload.get("victim_id") or ""))
    if not present(target) or not present(actor):
        return
    battle.resolve_damage(DamageContext(
        source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
        action_name="禽凤剑追击", ignore_shield=True,
        tags={"skill", "bird_phoenix_sword", "followup"},
    ))


def _sword_pull(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
    target = battle.units.get(str(queued.payload.get("victim_id") or ""))
    if not present(target) or not present(actor):
        return
    recipient = piercing_nondamage_recipient(
        battle, actor, target, action_name="禽凤剑牵引", code="bird_phoenix_sword")
    if recipient is not None:
        target = recipient
        _pull_toward_guardian(battle, actor, recipient)
    for index in (1, 2):
        if not present(target) or not present(actor):
            break
        battle.queue_skill_effect_action(
            actor=actor, display_name=f"禽凤剑追击{index}", effect_code="bird_phoenix_followup",
            effect_resolver=_sword_followup, payload={"victim_id": target.unit_id,
                                                 "ignore_shield": True},
            target_units=[target], target_cells=battle.unit_cells(target),
            include_cell_units=False, segment_index=index, segment_count=2,
        )


def _pull_toward_guardian(battle: Battle, actor: Unit, target: Unit) -> None:
    assert actor.position is not None and target.position is not None
    dx = (actor.position.x > target.position.x) - (actor.position.x < target.position.x)
    dy = (actor.position.y > target.position.y) - (actor.position.y < target.position.y)
    distance = target.position.distance_to(actor.position)
    if distance <= 1:
        return
    for steps in range(max(0, distance - 1), 0, -1):
        landing = target.position.offset(dx * steps, dy * steps)
        if not battle.in_bounds(landing) or not battle.can_place_unit(target, landing, mover=target):
            continue
        try:
            battle.move_unit(target, landing, via_skill=True, forced=True,
                             straight_only=True, max_distance=steps,
                             tags={"bird_phoenix_sword"})
        except ActionError:
            continue
        battle.log(f"{target.name} 被禽凤剑拉向 {actor.name} 周围。")
        return
    battle.log(f"{target.name} 的禽凤剑牵引被阻挡。")


class DiyiGuardian(AbstractHero):
    hero_code = "guardian_diyi"
    hero_name = "守护灵。地异"
    role = "召唤物"
    attribute = "土"
    race = "守护灵"
    level = 1
    base_stats = Stats(attack=5, defense=999, speed=4, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [BirdPhoenixSwordSkill(), LeatherBagSkill(), StarCircleSkill()]

    def build_traits(self) -> list[Trait]:
        return [GuardianTetherTrait()]


class SummonDiyiSkill(SummonGuardianSkill):
    def __init__(self) -> None:
        super().__init__("summon_guardian_diyi", "守护灵。地异", DiyiGuardian)
        self.description = "每场一次；在本人周围召唤攻5、守无限、速4、范1的地异守护灵。"


class BirdPhoenixSwordSkill(Skill):
    ai_payload_value_owned = True
    ai_complete_direction_payloads = True

    def __init__(self) -> None:
        super().__init__("bird_phoenix_sword", "禽凤剑",
                         "每回合一次；贴身三格破魔伤，最近受体牵引至周围后再受两次破魔伤。",
                         max_uses_per_turn=1, target_mode="cell", direction_mode="required")

    def line(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        direction = payload_direction(payload)
        origin = battle.declared_source_position(payload) or actor.position
        if direction not in ALL_DIRECTIONS or origin is None:
            raise ActionError("请选择禽凤剑的合法方向。")
        return battle.line_positions(origin, direction, 3)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = {cell for direction in ALL_DIRECTIONS for cell in battle.line_positions(actor.position, direction, 3)}
        return {"cells": [cell.to_dict() for cell in sorted(cells, key=lambda c: (c.y, c.x))],
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "direction_only", "directions": [
                    {"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"} for dx, dy in ALL_DIRECTIONS
                    if battle.line_positions(actor.position, (dx, dy), 3)],
                    "prompt": "选择禽凤剑三格直线的方向。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.line(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.line(battle, actor, payload))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and ("direction" in payload or "dx" in payload):
            try:
                if not self.line(battle, actor, payload):
                    return False, "该方向没有有效三格。"
            except (ActionError, KeyError, TypeError, ValueError):
                return False, "请选择合法的禽凤剑方向。"
        return True, ""

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.line(battle, actor, payload)
        hit: list[Unit] = []
        for unit in battle.effect_units_at_cells(cells):
            if not present(unit):
                continue
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"), is_skill=True,
                action_name=self.name, ignore_shield=True,
                tags={"skill", self.code},
            ))
            if not ctx.cancelled and present(ctx.target) and ctx.target not in hit:
                hit.append(ctx.target)
        if not hit or not present(actor):
            return
        victim = min(hit, key=lambda unit: (battle.distance_between_units(actor, unit),
                                            min((cell.y, cell.x) for cell in battle.unit_cells(unit)),
                                            unit.unit_id))
        battle.queue_skill_effect_action(
            actor=actor, display_name="禽凤剑牵引", effect_code="bird_phoenix_pull",
            effect_resolver=_sword_pull, payload={"victim_id": victim.unit_id,
                                                "ignore_shield": True},
            target_units=[victim], target_cells=battle.unit_cells(victim),
            include_cell_units=False,
        )

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]] | None:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "direction": {"dx": dx, "dy": dy}}
                for dx, dy in ALL_DIRECTIONS if battle.line_positions(actor.position, (dx, dy), 3)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action) or []

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            cells = self.line(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        targets = battle.effect_units_at_cells(cells)
        score = sum(_hit_value(battle, actor, target) for target in targets) - 25.0
        survivors = [target for target in targets if not target.magic_immunity
                     and target.dodge_charges <= 0 and not target.is_clone
                     and target.current_hp > battle.damage_rule.calculate_damage(
                         actor.stat("attack"), target.stat("defense"))]
        if survivors:
            nearest = min(survivors, key=lambda unit: (battle.distance_between_units(actor, unit),
                                                        unit.unit_id))
            score += 2 * _hit_value(battle, actor, nearest)
        return score

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool | None:
        try:
            impacted = self.get_target_units_for_payload(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return False
        return any(target.player_id != actor.player_id and not target.magic_immunity
                   and (target.total_shields() > 0 or _hit_value(battle, actor, target) > 0)
                   for target in impacted)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0


class LeatherBagStatus(StatusEffect):
    def __init__(self, applied_turn: int) -> None:
        super().__init__("牛皮锦囊", "守、速各+1，直到下一本人回合结束。")
        self.applied_turn = applied_turn

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name in {"defense", "speed"} else value

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number > self.applied_turn and self.owner is not None:
            self.owner.remove_status(self, battle)


class LeatherBagSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    ai_optional_choice_owned = True
    requires_choice_replay_when_used = True

    def __init__(self) -> None:
        super().__init__("leather_bag", "牛皮锦囊",
                         "每回合一次；掷一枚六面骰，之后选最多点数名己方武将，守速各+1至下本人回合结束。",
                         max_uses_per_turn=1, target_mode="self")

    def recipients(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if present(unit)
                and unit.player_id == actor.player_id and _true_hero(unit)]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        return (ok, reason) if not ok or self.recipients(battle, actor) else (False, "没有可选己方武将。")

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        units = self.recipients(battle, actor)
        return {"cells": [cell.to_dict() for unit in units for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in units], "secondary_cells": [],
                "requires_target": False, "dice": "1d6", "choice_after_roll": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        rolled = battle.roll_die(6, action_name="牛皮锦囊掷骰", beneficiary_player_id=actor.player_id)
        battle.log_public_event(f"{actor.name} 的牛皮锦囊掷出 {rolled}。", source=actor)
        selected: set[str] = set()
        for index in range(rolled):
            options = [unit.unit_id for unit in self.recipients(battle, actor)
                       if unit.unit_id not in selected]
            if not options:
                break
            choice = battle.optional_position_choice(
                actor, options, action_name=f"牛皮锦囊 {index+1}/{rolled}",
                kind="required_unit_selection" if not selected else "optional_unit_selection")
            if choice not in options:
                break
            selected.add(choice)
            target = battle.units.get(choice)
            if present(target) and not target.skill_non_damage_effects_blocked():
                for old in list(target.statuses):
                    if isinstance(old, LeatherBagStatus):
                        target.remove_status(old, battle)
                target.add_status(LeatherBagStatus(battle.turn_number), source=actor)
                battle.log(f"{target.name} 获得牛皮锦囊：守、速各+1。")

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if prompt.get("kind") not in {"optional_unit_selection", "required_unit_selection"} or not str(
                prompt.get("action_name") or "").startswith("牛皮锦囊"):
            return None
        target = battle.units.get(choice)
        if target not in self.recipients(battle, actor):
            return -1000.0
        if target.has_status("牛皮锦囊") or target.skill_non_damage_effects_blocked():
            return -5.0
        return 20.0 + max(0.0, 1.0 - target.current_hp) * 25.0 + target.stat("attack") * 3.0

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        units = [unit for unit in self.recipients(battle, actor)
                 if not unit.has_status("牛皮锦囊") and not unit.skill_non_damage_effects_blocked()]
        return min(len(units), 3.5) * 28.0 - 20.0 if units else -100.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100.0


class StarCircleStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("占星术。圆", "本回合周围5×5使己方看见隐身敌人。",
                         duration=1, tick_scope="any_turn_end")

    def reveals_stealth_to(self, battle: Battle, player_id: int, target: Unit) -> bool:
        owner = self.owner
        return (present(owner) and owner.player_id == player_id
                and target.player_id != player_id and present(target)
                and battle.distance_between_units(owner, target) <= 2)


class StarCircleSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("star_circle", "占星术。圆",
                         "每场一次；本回合地异周围5×5反隐，不移除原隐身状态。",
                         max_uses_per_battle=1, target_mode="self")

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = [Position(x, y) for x in range(actor.position.x - 2, actor.position.x + 3)
                 for y in range(actor.position.y - 2, actor.position.y + 3)
                 if battle.in_bounds(Position(x, y))]
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [],
                "secondary_cells": [], "requires_target": False}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(StarCircleStatus(), source=actor)
        battle.log(f"{actor.name} 开启占星圆，周围5×5本回合反隐。")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        # All three AI routes may call this outside a private-view wrapper.  A
        # hidden unit's actual position must not influence the cast decision.
        unseen = bool(getattr(battle, "_ai_has_unseen_enemies", False)) or any(
            present(unit) and unit.player_id != actor.player_id and unit.is_stealthed()
            and not battle.stealth_revealed_to(unit, actor.player_id)
            for unit in battle.all_units())
        visible_nearby = any(present(unit) and unit.player_id != actor.player_id
                             and not unit.is_stealthed()
                             and battle.distance_between_units(actor, unit) <= 2
                             for unit in battle.all_units())
        return 20.0 if unseen and not visible_nearby else -100.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100.0
