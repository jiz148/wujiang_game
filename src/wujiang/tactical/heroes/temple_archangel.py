"""R364: the archangel's immunity, movement control, and restorative hymn."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, QueuedAction,
    Skill, StatusEffect, Unit,
)
from wujiang.tactical.heroes.common import (
    DashMoveSkill, HardenSkill, MagicImmunityStatus, payload_position, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect


_DIRECTIONS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
               (1, -1), (1, 0), (1, 1))


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


def _line_direction(actor: Unit, target: Unit) -> tuple[int, int] | None:
    if actor.position is None or target.position is None:
        return None
    dx, dy = target.position.x - actor.position.x, target.position.y - actor.position.y
    if dx == 0 and dy == 0:
        return None
    if dx and dy and abs(dx) != abs(dy):
        return None
    return (0 if dx == 0 else dx // abs(dx), 0 if dy == 0 else dy // abs(dy))


class LargeHardenSkill(HardenSkill):
    """All ordinary Harden rules remain; only the defense magnitude changes."""

    def __init__(self) -> None:
        super().__init__()
        self.code = "harden_large"
        self.name = "变硬（大）"
        self.description = "与变硬相同，守改为+2，持续2轮。"

    def apply_to_self(self, battle: Battle, actor: Unit) -> None:
        from wujiang.tactical.heroes.common import StatModifierStatus

        actor.add_status(StatModifierStatus("变硬", defense_delta=2, duration=2,
                                            tick_scope="owner_turn_start", description="守+2。"))
        battle.log_public_event(f"{actor.name} 变硬（大），守+2。", source=actor, target=actor)


class HolyBodyStatus(StatusEffect):
    provided_flags = frozenset({"magic_immunity", "physical_immunity"})

    def __init__(self) -> None:
        super().__init__("圣体", "物免与魔免，直到本人下回合结束。",
                         duration=2, tick_scope="owner_turn_end")
        self.previous_magic = False
        self.previous_physical = False

    def bind(self, owner: Unit) -> HolyBodyStatus:
        super().bind(owner)
        # Preserve only innate flags. Temporary immunity already has its own
        # status and may expire while Holy Body is still active.
        self.previous_magic = owner.magic_immunity and not any(
            isinstance(status, MagicImmunityStatus)
            or "magic_immunity" in getattr(status, "provided_flags", ())
            for status in owner.statuses if status is not self)
        self.previous_physical = getattr(owner, "_base_physical_immunity", False) and not any(
            "physical_immunity" in getattr(status, "provided_flags", ())
            for status in owner.statuses if status is not self)
        owner.magic_immunity = True
        owner.physical_immunity = True
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        others = [status for status in owner.statuses if status is not self]
        owner.magic_immunity = self.previous_magic or any(
            isinstance(status, MagicImmunityStatus)
            or "magic_immunity" in getattr(status, "provided_flags", ()) for status in others)
        owner.physical_immunity = self.previous_physical or any(
            "physical_immunity" in getattr(status, "provided_flags", ()) for status in others)


class HolyBodySkill(Skill):
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("holy_body", "圣体", "3轮一次，直到下回合结束物免且魔免。",
                         cooldown_turns=3, max_uses_per_turn=1, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("圣体")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(HolyBodyStatus())
        battle.log_public_event(f"{actor.name} 进入圣体状态。", source=actor, target=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [actor.position.to_dict()] if actor.position else [],
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}


class LandingSlowStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("圣殿坠落迟缓", "速-3，最低1，直到下回合结束。",
                         duration=1, tick_scope="owner_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 3.0) if stat_name == "speed" else value


class SuperheroLandingSkill(DashMoveSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("superhero_landing", "Superhero。landing",
                         "费1；每本人轮一次，飞跃恰4格，周围单位受破魔速-3至下回合结束。",
                         max_distance=4, exact_distance=4, mana_cost=1,
                         max_uses_per_turn=1, straight_only=True, ignore_units=True)

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        return [cell for cell in battle.reachable_positions(
            actor, max_distance=4, exact_distance=4, straight_only=True, ignore_units=True)
            if (abs(cell.x - actor.position.x), abs(cell.y - actor.position.y)) in {(4, 0), (0, 4)}]

    def affected_at(self, battle: Battle, actor: Unit, landing: Position) -> list[Unit]:
        cells = [landing.offset(dx, dy) for dx, dy in _DIRECTIONS]
        return [unit for unit in battle.effect_units_at_cells(cells, ignore=actor)
                if unit.position is not None]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return []  # Targets enter a separate reaction window after the movement.

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        landing = payload_position(payload)
        if landing not in self.available_cells(battle, actor):
            raise ActionError("请选择合法的四格直线飞跃落点。")
        super().execute(battle, actor, payload)
        if not _present(actor):
            return
        targets = self.affected_at(battle, actor, landing)
        battle.queue_skill_effect_action(
            actor=actor, display_name="Superhero。landing减速", effect_code="superhero_landing_slow",
            target_units=targets, include_cell_units=False,
            payload={"ignore_shield": True, "cannot_evade": True},
            effect_resolver=self.resolve_slow,
        )

    def resolve_slow(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if _present(target):
                apply_piercing_status_effect(battle, actor, target, action_name=self.name,
                                             status=LandingSlowStatus(), is_skill=True,
                                             tags={"skill", self.code})

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        secondary = [unit.position for cell in cells for unit in self.affected_at(battle, actor, cell)
                     if unit.position is not None]
        return {"cells": positions_to_dict(cells), "target_unit_ids": [],
                "secondary_cells": positions_to_dict(list(dict.fromkeys(secondary))),
                "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.available_cells(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cell = payload_position(payload)
        except (ActionError, ValueError, TypeError):
            return -1000.0
        if cell not in self.available_cells(battle, actor):
            return -1000.0
        return sum((25.0 if unit.player_id != actor.player_id else -35.0)
                   for unit in self.affected_at(battle, actor, cell)) - 10.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class TempleGrappleSkill(Skill):
    is_displacement_skill = True
    moves_owner_on_use = True
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("temple_grapple", "圣殿抱摔",
                         "费2；每本人轮一次，和受体尽量同向直线移动3格，再使其受破魔伤。",
                         mana_cost=2, max_uses_per_turn=1, target_mode="enemy")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"ignore_shield": True}

    def _target(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Unit:
        target = battle.get_unit(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id")))
        if not _present(target) or target.player_id == actor.player_id:
            raise ActionError("请选择一个合法敌方目标。")
        if battle.distance_between_units(actor, target) > actor.targeting_range():
            raise ActionError("目标已离开抱摔射程。")
        return target

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = self._target(battle, actor, payload)
        direction = _line_direction(actor, target)
        if direction is None:
            raise ActionError("抱摔目标不在直线上。")
        ctx = battle.validate_target(actor, target, action_name=self.name,
                                     is_skill=True, is_hostile=True, ignore_shield=True,
                                     resolve_defenses=False, tags={"skill", self.code})
        if ctx.cancelled:
            return
        if target.total_shields() > 0:
            target.consume_one_shield()
        for _ in range(3):
            if not _present(actor) or not _present(target) or actor.cannot_move or target.cannot_move:
                break
            assert actor.position is not None and target.position is not None
            actor_next = actor.position.offset(*direction)
            target_next = target.position.offset(*direction)
            if (not battle.in_bounds(actor_next) or not battle.in_bounds(target_next)
                    or not battle.can_place_unit(target, target_next, ignore=target, mover=target)
                    or not battle.can_place_unit(actor, actor_next, ignore=target, mover=actor)):
                break
            battle.move_unit(target, target_next, via_skill=True, forced=True,
                             max_distance=1, tags={self.code})
            battle.move_unit(actor, actor_next, via_skill=True, max_distance=1,
                             tags={self.code})
        if _present(actor) and _present(target):
            battle.queue_skill_effect_action(
                actor=actor, display_name="圣殿抱摔破魔伤", effect_code="temple_grapple_damage",
                target_units=[target], include_cell_units=False,
                payload={"ignore_shield": True, "target_unit_id": target.unit_id},
                effect_resolver=self.resolve_damage,
            )

    def resolve_damage(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        target = battle.units.get(str(queued.payload.get("target_unit_id")))
        if _present(target):
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name="圣殿抱摔", ignore_shield=True,
                tags={"skill", self.code}))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.enemy_units(actor.player_id)
                   if _present(unit) and battle.distance_between_units(actor, unit) <= actor.targeting_range()
                   and _line_direction(actor, unit) is not None]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit_id} for unit_id in self.preview(battle, actor)["target_unit_ids"]]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target = self._target(battle, actor, payload)
        except (ActionError, KeyError):
            return -1000.0
        return 50.0 + min(1.0, target.current_hp) * 60.0

    def ai_effect_units(self, battle: Battle, actor: Unit,
                        payload: dict[str, Any]) -> list[Unit]:
        try:
            return [self._target(battle, actor, payload)]
        except (ActionError, KeyError):
            return []

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class HolySongSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    requires_direct_unit_target_line = False
    is_ultimate = True

    def __init__(self) -> None:
        super().__init__("holy_song", "圣歌",
                         "大招；范5己方单体血魔全满，非大招攻击与技能本轮使用次数清零。",
                         max_uses_per_battle=1, target_mode="unit")

    def eligible(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.player_units(actor.player_id)
                if _present(unit) and battle.distance_between_units(actor, unit) <= 5]

    def _target(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Unit:
        unit = battle.get_unit(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id")))
        if unit not in self.eligible(battle, actor):
            raise ActionError("圣歌只能选范5内己方单位。")
        return unit

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            try:
                self._target(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = self._target(battle, actor, payload)
        battle.heal(HealContext(source=actor, target=target,
                                amount=max(0.0, target.max_health - target.current_hp),
                                action_name=self.name, direct_target=True))
        target.gain_mana(max(0.0, target.max_mana() - target.current_mana))
        target.attacks_used = 0
        for skill in target.skills:
            if not getattr(skill, "is_ultimate", False):
                skill.uses_this_turn = 0
        battle.log_public_event(f"{target.name} 受圣歌加护，血魔全满且本轮非大招次数重置。",
                                source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible(battle, actor)
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [self._target(battle, actor, payload)]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit.unit_id} for unit in self.eligible(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target = self._target(battle, actor, payload)
        except (ActionError, KeyError):
            return -1000.0
        restored = (target.max_health - target.current_hp) * 110.0
        restored += max(0.0, target.max_mana() - target.current_mana) * 16.0
        restored += min(target.attacks_used, target.attack_actions_per_turn()) * 30.0
        restored += sum(16.0 for skill in target.skills if not getattr(skill, "is_ultimate", False)
                        and skill.uses_this_turn > 0 and skill.cooldown_remaining <= 0)
        return restored - 35.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class LaserEyeSkill(Skill):
    ai_payload_value_owned = True
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("laser_eye", "镭射眼",
                         "每本人轮一次，范5射线首个阻挡单位受破魔逆向推至边界。",
                         max_uses_per_turn=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.position is None:
            return []
        result = []
        for dx, dy in _DIRECTIONS:
            for cell in battle.line_positions(actor.position, (dx, dy), 5):
                blockers = battle.effect_units_at_cells([cell], ignore=actor)
                if blockers:
                    result.extend(blockers)
                    break
        return list(dict.fromkeys(result))

    def _target(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Unit:
        target = battle.get_unit(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id")))
        if target not in self.targets(battle, actor):
            raise ActionError("只能选择范5射线中的第一位阻挡单位。")
        return target

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            try:
                self._target(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"ignore_shield": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = self._target(battle, actor, payload)
        direction = _line_direction(actor, target)
        if direction is None:
            return
        ctx = battle.validate_target(actor, target, action_name=self.name,
                                     is_skill=True, is_hostile=target.player_id != actor.player_id,
                                     ignore_shield=True, resolve_defenses=False,
                                     tags={"skill", self.code})
        if ctx.cancelled:
            return
        if target.player_id != actor.player_id and target.total_shields() > 0:
            target.consume_one_shield()
        for _ in range(max(battle.width, battle.height)):
            if not _present(target) or target.cannot_move:
                break
            assert target.position is not None
            destination = target.position.offset(*direction)
            if not battle.in_bounds(destination) or not battle.can_place_unit(
                    target, destination, ignore=target, mover=target):
                break
            battle.move_unit(target, destination, via_skill=True, forced=True,
                             max_distance=1, tags={self.code})
        battle.log_public_event(f"{target.name} 被镭射眼推向边界。", source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [self._target(battle, actor, payload)]

    def ai_effect_units(self, battle: Battle, actor: Unit,
                        payload: dict[str, Any]) -> list[Unit]:
        try:
            return [self._target(battle, actor, payload)]
        except (ActionError, KeyError):
            return []

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit.unit_id} for unit in self.targets(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target = self._target(battle, actor, payload)
        except (ActionError, KeyError):
            return -1000.0
        direction = _line_direction(actor, target)
        if direction is None or target.position is None or target.cannot_move:
            return -1000.0
        distance = 0
        cell = target.position
        while distance < max(battle.width, battle.height):
            next_cell = cell.offset(*direction)
            if not battle.in_bounds(next_cell) or not battle.can_place_unit(
                    target, next_cell, ignore=target, mover=target):
                break
            cell = next_cell
            distance += 1
        return (distance * 16.0 - 10.0) * (1.0 if target.player_id != actor.player_id else -1.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
