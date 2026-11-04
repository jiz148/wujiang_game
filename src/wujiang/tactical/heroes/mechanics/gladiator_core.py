"""Gladiator swap and Willow battlefield mechanics."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import (
    DAMAGE_DEDUPED_CANDIDATES,
    RANKED_CANDIDATES,
)

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import ActionMiss
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import MoveContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import LightWallSkill
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import ensure_ally
from wujiang.tactical.heroes.common import line_patterns
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.h1_line_damage import H1LineDamageSkill
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.dragon_breath import RemoteDragonBreathSkill
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


def gladiator_swap_destinations(battle: Battle, actor: Unit, ally: Unit, *, via_skill: bool = True) -> tuple[Position, Position] | None:
    """Find legal placements for an anchor swap without mutating the live board."""
    if actor.position is None or ally.position is None or actor.unit_id == ally.unit_id:
        return None
    if (ally.direct_effects_blocked() or (via_skill and ally.skill_non_damage_effects_blocked())
            or getattr(ally, "standable_terrain", False)):
        return None
    actor_start, ally_start = actor.position, ally.position
    ally_rider = battle.rider_for(ally)
    hidden = [actor, ally] + ([ally_rider] if ally_rider is not None else [])
    starts = {unit.unit_id: unit.position for unit in hidden}
    try:
        for unit in hidden:
            unit.position = None

        def legal(unit: Unit, preferred: Position) -> list[Position]:
            positions = [Position(x, y) for y in range(battle.height) for x in range(battle.width)]
            positions.sort(key=lambda cell: (cell.distance_to(preferred), cell.y, cell.x))
            from wujiang.tactical.placement_queries import legal_placement_batch
            batch = legal_placement_batch(battle, unit, positions)
            if batch is not None:
                return batch
            return [cell for cell in positions if battle.can_place_unit(unit, cell, ignore=unit, mover=unit)]

        actor_options = legal(actor, ally_start)
        ally_options = legal(ally, actor_start)
        for actor_end in actor_options:
            actor_cells = set(battle.unit_cells_at(actor, actor_end))
            for ally_end in ally_options:
                if actor_end == actor_start and ally_end == ally_start:
                    continue
                ally_cells = set(battle.unit_cells_at(ally, ally_end))
                if actor_cells & ally_cells and not battle.units_can_overlap(actor, ally):
                    continue
                if ally_rider is not None:
                    rider_start = starts[ally_rider.unit_id]
                    if rider_start is None:
                        continue
                    rider_end = rider_start.offset(ally_end.x - ally_start.x, ally_end.y - ally_start.y)
                    if not battle.can_place_unit(ally_rider, rider_end, ignore=ally_rider, mover=ally_rider):
                        continue
                    rider_cells = set(battle.unit_cells_at(ally_rider, rider_end))
                    if actor_cells & rider_cells and not battle.units_can_overlap(actor, ally_rider):
                        continue
                return actor_end, ally_end
    finally:
        for unit in hidden:
            unit.position = starts[unit.unit_id]
    return None


class WillowExchangeSkill(Skill):
    resolves_from_current_position = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float | None:
        actor, payload = context.actor, context.payload
        if actor.hero_code != "excel_r304":
            return None
        target = context.battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target is actor or target.skill_non_damage_effects_blocked():
            return -1000.0
        value = context.r17_effect_score(self)
        if value <= -1000.0:
            return value
        # The once-per-battle exchange needs an immediate rescue or position gain.
        return value - 55.0

    def __init__(self) -> None:
        super().__init__("exchange", "交换", "一场一次；没有伤害；破魔并与范内直线目标交换位置，多格身体取双方最近合法落点。",
                         max_uses_per_battle=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.position is None:
            return []
        return [unit for unit in battle.all_units()
                if unit is not actor and unit.alive and not unit.banished and unit.position is not None
                and not getattr(unit, "attached_to_unit_id", None)
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
                and gladiator_swap_destinations(battle, actor, unit) is not None]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if payload and payload.get("target_unit_id"):
            return ((True, "") if str(payload["target_unit_id"]) in {unit.unit_id for unit in targets}
                    else (False, "请选择范内直线且可完整合法换位的单位。"))
        return (True, "") if targets else (False, "当前没有可合法换位的单位。")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return [payload_target_unit(battle, payload)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            target = payload_target_unit(battle, payload)
        except ActionMiss:
            battle.log_public_event(f"{actor.name} 的交换打在原格，目标已离开。", source=actor)
            return
        if target is actor or target.position is None or actor.position is None:
            return
        hostile = target.player_id != actor.player_id
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=hostile, ignore_shield=True, damage_target=False,
                                        resolve_defenses=False, record_effect_on_validation=False)
        if result.cancelled or target.skill_non_damage_effects_blocked():
            battle.log_public_event(result.reason or f"{target.name} 未受交换影响。", source=actor, target=target)
            return
        if hostile and target.total_shields() > 0:
            target.consume_one_shield()
            battle.log_public_event(f"{target.name} 的一层护盾被交换贯穿。", source=actor, target=target)
        if hostile and target.dodge_charges > 0:
            target.dodge_charges -= 1
            battle.log_public_event(f"{target.name} 回避了交换。", source=actor, target=target)
            return
        if battle.destroy_clone_for_skill_effect(target, source=actor, action_name=self.name):
            return
        if gladiator_swap(battle, actor, target, skill_code=self.code, via_skill=True):
            battle.record_skill_effect(actor)
            battle.record_skill_effect(target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        landing_cells: list[Position] = []
        landing_by_target: dict[str, dict[str, dict[str, int]]] = {}
        for target in targets:
            pair = gladiator_swap_destinations(battle, actor, target)
            if pair is None:
                continue
            landing_cells.extend(battle.unit_cells_at(actor, pair[0]))
            landing_cells.extend(battle.unit_cells_at(target, pair[1]))
            landing_by_target[target.unit_id] = {"self": pair[0].to_dict(), "target": pair[1].to_dict()}
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": positions_to_dict(landing_cells),
                "landing_by_target": landing_by_target, "requires_target": True}


class WillowGhostLightWallSkill(LightWallSkill):
    def can_react_after_destroyed(self, battle: Battle, actor: Unit, queued_action: Any) -> bool:
        turn_owner = battle.current_turn_unit()
        return (actor.hero_code == "excel_r304" and not actor.is_clone and not actor.is_summon
                and not actor.alive and actor in battle.destroyed_units and actor.last_position is not None
                and queued_action.source_player_id != actor.player_id
                and turn_owner is not None and turn_owner.player_id != actor.player_id)

    def ally_targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.alive:
            return super().ally_targets(battle, actor)
        origin = actor.last_position
        if origin is None:
            return []
        return [unit for unit in battle.effect_units(battle.player_units(actor.player_id))
                if unit.position is not None and unit.alive and not unit.banished
                and not unit.direct_effects_blocked() and not unit.skill_non_damage_effects_blocked()
                and any(origin.distance_to(cell) <= actor.targeting_range()
                        and battle.cells_are_straight_aligned(origin, cell)
                        for cell in battle.unit_cells(unit))]

    def chosen_targets(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        if actor.alive:
            return super().chosen_targets(battle, actor, payload)
        valid = {unit.unit_id: unit for unit in self.ally_targets(battle, actor)}
        result: list[Unit] = []
        for target_id in battle.payload_target_unit_ids(payload):
            target = battle.units.get(target_id)
            if target is None:
                continue
            target = battle.effect_recipient(target)
            ensure_ally(actor, target)
            if target.unit_id not in valid:
                raise ActionError(f"{target.name} 不在柳影光墙的直线范围内。")
            if target not in result:
                result.append(target)
        return result

    def can_react_to(self, battle: Battle, actor: Unit, queued_action: Any) -> tuple[bool, str]:
        self.sync_turn_scope(battle)
        if not actor.alive and self.uses_this_turn >= 2:
            return False, "本敌方武将回合的两次死态光墙已用完。"
        return super().can_react_to(battle, actor, queued_action)


class WillowAfterDeathWallTrait(Trait):
    def __init__(self) -> None:
        super().__init__("柳影光墙", "真死后以最后位置施放光墙；每个敌方武将回合最多两次，逐次费魔。")

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        owner = self.owner
        if owner is not None and not owner.alive:
            skill = owner.skill_map().get("light_wall")
            if skill is not None:
                skill.sync_turn_scope(battle)
                data.update({"ghost_casting_anchor": owner.last_position.to_dict() if owner.last_position else None,
                             "ghost_wall_uses_this_enemy_turn": skill.uses_this_turn,
                             "ghost_wall_remaining_this_enemy_turn": max(0, 2 - skill.uses_this_turn),
                             "ghost_wall_mana_remaining": owner.current_mana})
        return data


def gladiator_swap(battle: Battle, actor: Unit, ally: Unit, *, skill_code: str, via_skill: bool = True) -> bool:
    pair = gladiator_swap_destinations(battle, actor, ally, via_skill=via_skill)
    if pair is None or actor.position is None or ally.position is None:
        battle.log_public_event(f"{actor.name} 的交换没有合法落点。", source=actor)
        return False
    actor_start, ally_start = actor.position, ally.position
    actor_end, ally_end = pair
    if actor_start == actor_end and ally_start == ally_end:
        battle.log_public_event(f"{actor.name} 的交换没有改变位置。", source=actor)
        return False
    rider = battle.rider_for(ally)
    rider_start = rider.position if rider is not None else None
    actor.position, ally.position = actor_end, ally_end
    if hasattr(actor, "_resolution_actual_position"):
        actor._resolution_actual_position = actor_end
    if hasattr(ally, "_resolution_actual_position"):
        ally._resolution_actual_position = ally_end
    if rider is not None and rider_start is not None:
        rider.position = rider_start.offset(ally_end.x - ally_start.x, ally_end.y - ally_start.y)
        rider.moved_this_turn = True
    if battle.mounted_unit_for(actor) is not None and actor.position not in battle.unit_cells(battle.mounted_unit_for(actor)):
        battle.clear_mounted_state(actor)
    actor.moved_this_turn = ally.moved_this_turn = True
    battle.log_public_event(f"{actor.name} 与 {ally.name} 交换了位置。", source=actor, target=ally)
    for unit, start, end in ((actor, actor_start, actor_end), (ally, ally_start, ally_end)):
        if start == end:
            continue
        ctx = MoveContext(unit=unit, start=start, end=end, path=[start, end], via_skill=via_skill,
                          tags={skill_code, "gladiator_swap", f"source:{actor.unit_id}"})
        for effect in list(battle.field_effects):
            effect.on_unit_moved(battle, ctx)
        for other in battle.all_units():
            for component in list(other.iter_components()):
                component.on_unit_moved(battle, ctx)
    battle.enforce_sandstorm_stealth_rules()
    battle.cleanup_dead_units()
    return True


class GladiatorSwapSkill(H1LineDamageSkill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r17_effect_score(self)

    target_cells_are_geometry_only = True

    def __init__(self, *, code: str, name: str, length: int, requires_damage: bool, pierces: bool) -> None:
        super().__init__(code, name, f"每回合一次；近身直线{length}格技能伤害后与指定己方单位交换位置。",
                         length=length, max_uses_per_turn=1)
        self.requires_damage = requires_damage
        self.pierces = pierces

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        body = set(battle.unit_cells(actor))
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for origin in sorted(body, key=lambda cell: (cell.y, cell.x)):
            for dx, dy in ALL_DIRECTIONS:
                if origin.offset(dx, dy) in body:
                    continue
                for pattern in line_patterns(battle, origin, [(dx, dy)], self.length):
                    signature = tuple((cell.x, cell.y) for cell in pattern)
                    if signature not in seen:
                        patterns.append(pattern)
                        seen.add(signature)
        return patterns

    def allies(self, battle: Battle, actor: Unit) -> list[Unit]:
        unique: dict[str, Unit] = {}
        for unit in battle.player_units(actor.player_id):
            if unit.unit_id == actor.unit_id or unit.position is None or unit.banished:
                continue
            recipient = battle.effect_recipient(unit)
            if recipient.unit_id == battle.effect_recipient(actor).unit_id:
                continue
            if getattr(recipient, "attached_to_unit_id", None):
                continue
            unique[recipient.unit_id] = recipient
        return [unit for unit in unique.values() if gladiator_swap_destinations(battle, actor, unit) is not None]

    def selected(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[list[Position], Unit]:
        cells = match_payload_pattern(payload, self.patterns(battle, actor))
        code = str(payload.get("choice_code") or "")
        ally = battle.units.get(code.removeprefix("swap:")) if code.startswith("swap:") else None
        if ally is None or ally.unit_id not in {unit.unit_id for unit in self.allies(battle, actor)}:
            raise ActionError("请选择可合法交换位置的己方单位。")
        return cells, ally

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and ("choice_code" in payload or "cells" in payload):
            try:
                self.selected(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError) as exc:
                return False, str(exc)
        has_ally = bool(self.allies(battle, actor))
        return has_ally, "" if has_ally else "没有可交换的己方单位。"

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        cells, ally = self.selected(battle, actor, payload)
        return {"gladiator_cells": positions_to_dict(cells), "gladiator_ally_id": ally.unit_id}

    def prepay_resources(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        self.selected(battle, actor, payload)
        super().prepay_resources(battle, actor, payload)

    def chosen_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and "gladiator_cells" in payload:
            return [Position(**cell) for cell in payload["gladiator_cells"]]
        return self.selected(battle, actor, payload)[0]

    def affected_units(self, battle: Battle, actor: Unit, cells: list[Position]) -> list[Unit]:
        return battle.effect_units_at_cells(cells, ignore=actor)

    def resolve_hit(self, battle: Battle, actor: Unit, target: Unit, cells: list[Position]) -> DamageContext:
        return battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
            action_name=self.name, ignore_shield=self.pierces,
            area_cell_hits=battle.unit_hit_count_for_cells(target, cells), tags={"skill", self.code},
        ))

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return self.pierces or super().ignores_shield_for_payload(battle, actor, payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        ally_id = str(payload.get("gladiator_ally_id") or payload.get("choice_code", "").removeprefix("swap:"))
        ally = battle.units.get(ally_id)
        dealt_damage = False
        for target in self.affected_units(battle, actor, cells):
            if not actor.alive or actor.banished or actor.position is None:
                break
            dealt_damage = self.resolve_hit(battle, actor, target, cells).actual_damage > 0 or dealt_damage
        if not actor.alive or actor.banished or actor.position is None:
            return
        if self.requires_damage and not dealt_damage:
            battle.log_public_event(f"{actor.name} 的{self.name}未造成实际伤害，没有交换位置。", source=actor)
            return
        if ally is None or not ally.alive or ally.banished or ally.position is None or ally.player_id != actor.player_id:
            battle.log_public_event(f"{actor.name} 的交换对象已离场。", source=actor)
            return
        gladiator_swap(battle, actor, ally, skill_code=self.code)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        base = pattern_selection_preview(patterns)
        choices = [{"code": f"swap:{ally.unit_id}", "label": f"与{ally.name}交换位置",
                    "patterns": [positions_to_dict(pattern) for pattern in patterns]}
                   for ally in self.allies(battle, actor)]
        visible_targets = [unit.unit_id for unit in battle.effect_units_at_cells(
            [cell for pattern in patterns for cell in pattern], ignore=actor)
            if battle.unit_can_be_selected(unit, actor=actor)[0]]
        base.update({"target_unit_ids": visible_targets, "secondary_cells": [], "requires_target": True,
                     "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                                   "choice_prompt": "先选择要交换的己方单位，再选择攻击直线。",
                                   "cell_prompt": "选择完整直线；结算伤害后按技能条件交换位置。"}})
        return base


class GladiatorClawSkill(GladiatorSwapSkill):
    ai_candidate_plan = RANKED_CANDIDATES
    def __init__(self) -> None:
        super().__init__(code="gladiator_claw", name="剑斗力爪", length=2, requires_damage=True, pierces=True)


class GladiatorGaleSkill(GladiatorSwapSkill):
    ai_enemy_impact_requirement = False
    ai_candidate_plan = RANKED_CANDIDATES
    def __init__(self) -> None:
        super().__init__(code="gladiator_gale", name="暴风", length=5, requires_damage=False, pierces=False)


class WaterWaveCannonSkill(RemoteDragonBreathSkill):
    ai_candidate_plan = DAMAGE_DEDUPED_CANDIDATES
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r18_effect_score(self)

    def __init__(self) -> None:
        super().__init__()
        self.code = "water_wave_cannon"
        self.name = "水波炮"
        self.description = "费2魔、每本人回合最多2次；范内远程2×2普通技能伤害，双方单位可能受击。"


class GladiatorSoulStatus(StatModifierStatus):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("剑斗之魂", attack_delta=1, mana_delta=1,
                         duration=3, tick_scope="owner_turn_end")
        self.source_unit_id = source_unit_id
        self.is_skill_effect = True

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.clamp_mana()


class GladiatorSoulSkill(Skill):
    ai_enemy_impact_requirement = False
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.paid_skill_value()

    excludes_caster_from_effect = True

    def __init__(self) -> None:
        super().__init__("gladiator_soul", "剑斗之魂",
                         "大招；周围双方其他单位攻和魔能力值各+1，持续受体3本人轮；受友方效果位移后重置。",
                         max_uses_per_battle=1, target_mode="self")

    def affected_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and "soul_cells" in payload:
            return [Position(**cell) for cell in payload["soul_cells"]]
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if cell not in set(battle.unit_cells(actor))]

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"soul_cells": positions_to_dict(self.affected_cells(battle, actor, payload))}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.affected_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.affected_cells(battle, actor, payload), ignore=actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in self.get_target_units_for_payload(battle, actor, payload):
            if not actor.alive or actor.banished or actor.position is None:
                break
            hostile = target.player_id != actor.player_id
            ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                         is_hostile=hostile, ignore_targeting_restrictions=True)
            if ctx.cancelled or target.skill_non_damage_effects_blocked():
                continue
            existing = next((status for status in target.statuses
                             if isinstance(status, GladiatorSoulStatus)
                             and status.source_unit_id == actor.unit_id), None)
            if existing is not None:
                existing.duration = 3
            else:
                target.add_status(GladiatorSoulStatus(actor.unit_id), source=actor)
                if not any(isinstance(status, GladiatorSoulStatus)
                           and status.source_unit_id == actor.unit_id for status in target.statuses):
                    continue
                target.current_mana = round(min(target.max_mana(), target.current_mana + 1), 2)
            battle.log_public_event(f"{target.name} 获得【剑斗之魂】攻与魔能力值各+1。",
                                    source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor, {})
        targets = self.get_target_units_for_payload(battle, actor, {})
        return {"cells": positions_to_dict(cells), "secondary_cells": [],
                "target_unit_ids": [unit.unit_id for unit in targets
                                    if battle.unit_can_be_selected(unit, actor=actor)[0]],
                "requires_target": False}


