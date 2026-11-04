"""AISelfAndGenericScoring: shared AI scoring and queries for self_generic."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from typing import Any, Callable, Iterable, Optional
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants
from wujiang.tactical.rooms.ai_damage_probes import (
    DamageImpact, estimate_attack_damage, estimate_skill_damage,
    probe_attack_damage_impact, probe_skill_damage_impact,
    probe_skill_raw_damage_impact, probe_target_effect_impact,
    status_impact_signature, unit_impact_signature,
)
from wujiang.tactical.prediction import (expected_numeric, expected_local, forecasting_active, direct_scoring, rules_enabled,
    prediction_session, record_candidates, note, expected_attack_damage,
    attack_damage_distribution, sum_distributions, prediction_rollback, ForecastRandom, RULES_VERSION,
    expected_vector, joint_utilities_enabled, disable_joint_utilities, stable_observations, UnstableObservation)
from wujiang.tactical.engine.core import (
    ActionError,
    Battle,
    DamageContext,
    HealContext,
    HeroUnit,
    Position,
    QueuedAction,
    TemporaryDefenseStatus,
    Unit,
    battle_state_rollback,
)

class AISelfAndGenericScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    DifficultyProfile: Any
    _skill_score_context: Callable[..., Any]
    ai_reachable_positions: Callable[..., Any]
    attack_payload_enemy_targets: Callable[..., Any]
    best_available_attack_score: Callable[..., Any]
    build_attack_candidates: Callable[..., Any]
    copied_skill_action: Any
    difficulty_profile: Callable[..., Any]
    distance_between_units: Callable[..., Any]
    distance_to_position: Callable[..., Any]
    estimate_damage: Callable[..., Any]
    feiwang_attack_plan_value: Callable[..., Any]
    feiwang_attack_value: Callable[..., Any]
    feiwang_shensu_score: Callable[..., Any]
    feiwang_stance_score: Callable[..., Any]
    friendly_fire_penalty: Callable[..., Any]
    fusion_preparation_score: Callable[..., Any]
    gale_score: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    judgment_stone_collision_move_score: Callable[..., Any]
    kings_insight_score: Callable[..., Any]
    li_attack_plan_value: Callable[..., Any]
    masamune_attack_plan_value: Callable[..., Any]
    messenger_preparation_score: Callable[..., Any]
    natsume_shensu_score: Callable[..., Any]
    payload_destination: Callable[..., Any]
    r12_preparation_score: Callable[..., Any]
    r14_cat_form_score: Callable[..., Any]
    r19_incoming_position_risk: Callable[..., Any]
    r19_preparation_score: Callable[..., Any]
    r20_weather_cast_score: Callable[..., Any]
    r22_ninja_shensu_score: Callable[..., Any]
    skill_damage_score: Callable[..., Any]
    skill_effect_cells: Callable[..., Any]
    skill_payloads_for_action: Any
    square_cells_around_position: Callable[..., Any]
    wuchang_mist_score: Callable[..., Any]


class AISelfAndGenericScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AISelfAndGenericScoringServices) -> None:
        self.services = services

    def self_buff_score(self, battle: Battle, actor: Unit, code: str, profile: DifficultyProfile,
                        *, skill: Any = None, action: dict[str, Any] | None = None,
                        payload: dict[str, Any] | None = None) -> float:
        if skill is None:
            try:
                skill = actor.get_skill(code)
            except (ActionError, KeyError, ValueError):
                skill = None
        scorer = getattr(skill, "ai_self_buff_score", None)
        if scorer is not None:
            score_action = action or {"code": code, "_skill_object": skill}
            score_payload = payload or {"skill_code": code}
            return scorer(self.services._skill_score_context(
                battle, actor, score_action, score_payload, profile))
        return 10.0


    def best_available_attack_score(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        return max((candidate.score for action in battle.action_snapshot_for(actor).get("actions", []) if action.get("kind") == "attack" and action.get("available") for candidate in self.services.build_attack_candidates(battle, actor, action, profile)), default=0.0)


    def field_skill_score(self,
        battle: Battle,
        actor: Unit,
        code: str,
        targets: list[Unit],
        profile: DifficultyProfile,
    ) -> float:
        try:
            skill = actor.get_skill(code)
        except (ActionError, KeyError, ValueError):
            skill = None
        scorer = getattr(skill, "ai_field_score", None)
        if scorer is not None:
            return scorer(battle, actor, targets, self.services.distance_between_units)
        return 6.0


    def square_cells_around_position(self, battle: Battle, center: Position, *, radius: int) -> list[Position]:
        return [
            Position(x, y)
            for y in range(center.y - radius, center.y + radius + 1)
            for x in range(center.x - radius, center.x + radius + 1)
            if battle.in_bounds(Position(x, y))
        ]


    def judgment_stone_collision_move_score(self,
        battle: Battle, actor: Unit, destination: Position, profile: DifficultyProfile,
    ) -> float:
        if not any(unit.player_id != actor.player_id for unit in battle.units_at(destination)):
            return 0.0
        cells = self.services.square_cells_around_position(battle, destination, radius=2)
        summoner = battle.units.get(getattr(actor, "summoner_id", ""))
        if summoner is not None and summoner.alive and any(cell in cells for cell in battle.unit_cells(summoner)):
            return -1000.0
        units = [unit for unit in battle.all_units() if unit is not actor]
        before = {unit.unit_id: (unit.current_hp, unit.alive) for unit in units}
        with ai_probe_rollback(battle):
            actor.position = destination
            from wujiang.tactical.heroes.mechanics.world_seed import apply_judgment_stone_explosion
            apply_judgment_stone_explosion(battle, actor, cells)
            score = -12.0
            for unit in units:
                hp, alive = before[unit.unit_id]
                loss = max(0.0, hp - unit.current_hp)
                death = alive and not unit.alive
                value = loss * 100.0 + (self.services.hostile_unit_value(unit) * 0.6 + 100.0 if death else 0.0)
                score += value if unit.player_id != actor.player_id else -value * 1.5
            return score


    def judgment_stone_score(self,
        battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile,
    ) -> float:
        destination = self.services.payload_destination(payload)
        if destination is None:
            return -1000.0
        from wujiang.tactical.heroes.mechanics.world_seed import JudgmentStoneSummon, alive_world_root_numbers
        stone = JudgmentStoneSummon(actor.player_id, actor.unit_id)
        with ai_probe_rollback(battle):
            if destination not in actor.get_skill("judgment_stone").summon_cells(battle, actor):
                return -1000.0
            battle.add_unit(stone, destination)
            reachable = set(self.services.ai_reachable_positions(battle, stone, max_distance=6, ignore_units=True))
            targets = {cell for enemy in living_hostile_combatants(battle, actor.player_id) for cell in battle.unit_cells(enemy)}
            best = max((self.services.judgment_stone_collision_move_score(battle, stone, cell, profile) for cell in targets & reachable), default=-1000.0)
            ready = 2 in alive_world_root_numbers(battle, actor)
            cost = actor.get_skill("judgment_stone").mana_cost_for_payload(battle, actor, payload)
            if best > 0:
                return best * (0.85 if ready else 0.4) - cost * 22.0
            distance = min((destination.distance_to(cell) for cell in targets), default=999)
            waiting = sum(1 for unit in battle.all_units()
                          if unit is not stone and isinstance(unit, JudgmentStoneSummon)
                          and unit.summoner_id == actor.unit_id)
            return (18.0 - distance - waiting * 15.0 - cost * 30.0) if distance <= 10 else -1000.0


    def world_seed_score(self,
        battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile,
    ) -> float:
        skill = actor.get_skill("world_seed")
        try:
            anchor = skill.anchor(payload)
            edges = skill._edges(payload)
            numbers = skill._numbers(payload, edges)
            if not skill.legal_layout(battle, anchor, edges, numbers):
                return -1000.0
        except (ActionError, KeyError, ValueError):
            return -1000.0
        enemies = living_hostile_combatants(battle, actor.player_id)
        if not enemies:
            return -1000.0
        body = [anchor.offset(dx, dy) for dy in range(5) for dx in range(5)]
        distance = lambda unit, cells: min(a.distance_to(b) for a in battle.unit_cells(unit) for b in cells)
        nearest = min(distance(enemy, body) for enemy in enemies)
        score = 25.0 + max(0.0, 7.0 - nearest) * 6.0
        score += sum(16.0 for enemy in enemies if distance(enemy, body) <= 2)
        for number, _, _, cells in skill.root_placements(anchor, edges, numbers):
            exposure = sum(max(0.0, enemy.targeting_range() + min(4, enemy.stat("speed")) + 1 - distance(enemy, cells)) for enemy in enemies)
            score -= exposure * (2.5 if number == 2 else 2.0 if number == 3 else 1.0)
        score -= sum(2.0 for ally in battle.player_units(actor.player_id) if ally is not actor and not getattr(ally, "has_flying", False) and distance(ally, body) <= 1)
        return score


    def feiwang_attack_value(self, battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.heroes.common import is_mana_drain_immune
        recipients = [battle.effect_recipient(target)]
        if actor.has_status("里次元大剑"):
            cells = battle.basic_attack_area_cells_for_payload(actor, payload) or battle.unit_cells(target)
            recipients = battle.effect_units_at_cells(cells)
        score, room = 0.0, max(0.0, actor.max_mana() - actor.current_mana)
        for recipient in recipients:
            if recipient.player_id == actor.player_id:
                continue
            damage = estimate_attack_damage(battle, actor, recipient, {**payload, "attack_cells": [], "area_attack": True},
                                            attack_power=battle.basic_attack_preview_power(actor, payload), area_cell_hits=1)
            score += min(recipient.current_hp, damage) * 100.0
            if damage >= recipient.current_hp and damage > 0:
                score += 95.0 + (22.0 if not recipient.is_clone and not recipient.is_summon else 0.0)
            if (damage > 0 and not recipient.is_clone and not is_mana_drain_immune(recipient)
                    and any(trait.name == "攻击吸魔" for trait in actor.traits)):
                drain = min(1.0, recipient.current_mana)
                gain = min(room, drain)
                room -= gain
                score += drain * 14.0 + gain * 26.0
            elif recipient.total_shields() > 0 and not recipient.physical_immunity:
                score += 12.0
        return score


    def feiwang_attack_plan_value(self, battle: Battle, actor: Unit) -> float:
        if actor.position is None or actor.cannot_attack or actor.attacks_used >= actor.attack_actions_per_turn():
            return 0.0
        enemies = living_hostile_combatants(battle, actor.player_id)
        origin, best = actor.position, 0.0
        destinations = [origin]
        if not actor.cannot_move and not actor.cannot_normal_move and not actor.move_used:
            destinations += self.services.ai_reachable_positions(battle, actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
        # For each primary, only its nearest legal firing endpoint is needed; no hypothetical actions/randomness.
        try:
            for target in enemies:
                for destination in sorted(destinations, key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                    actor.position = destination
                    if not battle.attack_target_allowed(actor, target)[0]:
                        continue
                    payload = {"type": "attack", "unit_id": actor.unit_id, "target_unit_id": target.unit_id}
                    best = max(best, self.services.feiwang_attack_value(battle, actor, target, payload) - origin.distance_to(destination) * 2.0)
                    break
        finally:
            actor.position = origin
        return best


    def feiwang_stance_score(self, battle: Battle, actor: Unit) -> float:
        before = self.services.feiwang_attack_plan_value(battle, actor)
        with ai_probe_rollback(battle):
            actor.get_skill("inner_dimension_sword").execute(battle, actor, {})
            after = self.services.feiwang_attack_plan_value(battle, actor)
        gain = after - before
        return max(after + 25.0, gain * 2.0 + 25.0) if gain > 4.0 else -20.0


    def feiwang_shensu_score(self, battle: Battle, actor: Unit) -> float:
        if actor.move_used or actor.cannot_move or actor.cannot_normal_move:
            return -1000.0
        before = self.services.feiwang_attack_plan_value(battle, actor)
        with ai_probe_rollback(battle):
            actor.get_skill("big_shensu").execute(battle, actor, {})
            gain = self.services.feiwang_attack_plan_value(battle, actor) - before
        return gain + 25.0 if gain > 15.0 else -20.0


    def kings_insight_score(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        if battle.has_weather("王者的看破") or actor.position is None:
            return -1000.0
        from dataclasses import replace
        from wujiang.tactical.heroes.mechanics.inner_dimension import KingsInsightField
        targets = living_hostile_combatants(battle, actor.player_id)
        origin = actor.position
        destinations = [origin]
        if not actor.move_used and not actor.cannot_move and not actor.cannot_normal_move:
            destinations += self.services.ai_reachable_positions(battle, actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
        reactors: dict[str, str] = {}

        def collect_reactors(queued: QueuedAction) -> None:
            for enemy in targets:
                for option in battle.available_reaction_options(enemy, queued):
                    if option.action_type == "skill" and enemy.get_skill(option.action_code).timing in {"passive", "reaction"}:
                        reactors[enemy.unit_id] = option.action_code
                        break

        if not actor.cannot_attack and actor.attacks_used < actor.attack_actions_per_turn():
            try:
                for target in targets:
                    for destination in sorted(destinations, key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                        actor.position = destination
                        if not battle.attack_target_allowed(actor, target)[0]:
                            continue
                        queued = battle.build_queued_action({"type": "attack", "unit_id": actor.unit_id, "target_unit_id": target.unit_id})
                        for segment in queued.payload.get("separate_attack_segments", [{"target_unit_id": target.unit_id, "cells": [p.to_dict() for p in battle.unit_cells(target)]}]):
                            part = replace(queued, target_unit_ids=[segment["target_unit_id"]], target_cells=battle.payload_positions(segment, "cells"))
                            collect_reactors(part)
                        break
            finally:
                actor.position = origin
        for code in ("large_pierce_plus", "gale"):
            skill = actor.skill_map().get(code)
            if skill is None or not skill.can_use(battle, actor, {})[0]:
                continue
            for candidate in self.services.skill_payloads_for_action(battle, actor, self.services.copied_skill_action(battle, actor, skill)):
                try:
                    value = skill.ai_kings_insight_score(battle, actor, candidate, self.services, profile)
                    if value < profile.action_threshold:
                        continue
                    collect_reactors(battle.build_queued_action(candidate))
                except ActionError:
                    continue
        if not reactors:
            return -20.0
        score = 0.0
        for unit_id in reactors:
            target = battle.get_unit(unit_id)
            before_hp = target.current_hp
            with ai_probe_rollback(battle):
                before_mana = sum(unit.current_mana for unit in battle.hero_units(actor.player_id))
                # Value the actual field effect, including defenses and capped restoration.
                skill = target.get_skill(reactors[unit_id])
                KingsInsightField(actor.player_id).on_owner_action_declared(
                    battle, "skill", {"unit_id": unit_id, "skill_code": skill.code, "queued_resolution": True})
                score += (before_hp - target.current_hp) * 100.0
                score += max(0.0, sum(unit.current_mana for unit in battle.hero_units(actor.player_id)) - before_mana) * 16.0
        # Non-Fei-Wang allies may need defenses against hostile counter effects in the same turn.
        for ally in battle.player_units(actor.player_id):
            if getattr(getattr(ally, "ai_policy", None), "is_feiwang", False) or ally.direct_effects_blocked():
                continue
            if any(skill.timing in {"passive", "reaction"} and skill.can_use(battle, ally, {})[0] for skill in ally.skills):
                if any(self.services.distance_between_units(battle, ally, enemy) <= enemy.targeting_range() for enemy in targets):
                    score -= min(0.75, ally.current_hp) * 35.0
        return score + 28.0 if score > 10.0 else -20.0


    def gale_score(self, battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        skill = actor.get_skill("gale")
        units = battle.effect_units_at_cells(skill.get_target_cells_for_payload(battle, actor, payload))
        before = {unit.unit_id: (unit.alive, unit.position, unit.is_stealthed(), unit.total_shields()) for unit in units}
        plan = self.services.feiwang_attack_plan_value(battle, actor)
        with ai_probe_rollback(battle):
            skill.execute(battle, actor, payload)
            score = -skill.mana_cost * 20.0
            enemy_effect = False
            for unit in units:
                alive, position, hidden, shields = before[unit.unit_id]
                sign = 1.0 if unit.player_id != actor.player_id else -1.5
                if alive and not unit.alive:
                    score += sign * (110.0 + self.services.hostile_unit_value(unit) * 0.3)
                    enemy_effect |= sign > 0
                if hidden and not unit.is_stealthed():
                    score += sign * 24.0
                    enemy_effect |= sign > 0
                score += sign * max(0, shields - unit.total_shields()) * 12.0
                enemy_effect |= sign > 0 and shields > unit.total_shields()
                if sign < 0 and position != unit.position and unit.alive:
                    score -= 12.0
                if sign > 0 and position != unit.position and unit.alive:
                    enemy_effect = True
            plan_gain = self.services.feiwang_attack_plan_value(battle, actor) - plan
            score += plan_gain * 1.3
            return score if enemy_effect and score > 0 else -1000.0


    def great_fire_funeral_score(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        cells = self.services.skill_effect_cells(battle, actor, skill, payload)
        if not cells:
            return -20.0
        cell_keys = {(cell.x, cell.y) for cell in cells}
        enemies = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive and unit.position is not None and not unit.banished
        ]
        allies = [
            unit
            for unit in battle.player_units(actor.player_id)
            if unit.unit_id != actor.unit_id and unit.alive and unit.position is not None and not unit.banished
        ]
        score = self.services.skill_damage_score(battle, actor, skill, payload, profile)
        direct_enemy_hits = 0
        existing_fire = set().union(*(set(getattr(effect, "cells", set())) for effect in battle.field_effects if getattr(effect, "name", "") == "大火葬余烬"))
        new_fire = cell_keys - existing_fire
        for enemy in enemies:
            occupied = {(cell.x, cell.y) for cell in battle.unit_cells(enemy)}
            if occupied & cell_keys:
                direct_enemy_hits += 1
                score += self.services.hostile_unit_value(enemy) * 0.25 + 34.0
            elif new_fire:
                nearest_to_fire = min((min(abs(cell.x - x) + abs(cell.y - y) for x, y in new_fire) for cell in battle.unit_cells(enemy)), default=99)
                if nearest_to_fire <= max(1, int(enemy.stat("speed"))):
                    score += max(0.0, 4.0 - nearest_to_fire) * 10.0
        for ally in allies:
            occupied = {(cell.x, cell.y) for cell in battle.unit_cells(ally)}
            if occupied & cell_keys:
                score -= self.services.friendly_fire_penalty(ally) * 0.8
        if direct_enemy_hits == 0 and score < profile.action_threshold:
            return -12.0
        if direct_enemy_hits == 0 and not new_fire:
            return -1000.0
        if actor.stat("attack") > 1:
            score -= 20.0 * max(1, actor.attack_actions_per_turn())
            if actor.stat("attack") == 2 and any(any(candidate.timing == "active" for candidate in enemy.skills) for enemy in enemies):
                score += 24.0
        return score + profile.aggressive_bonus


    def great_fire_funeral_alignment_score_at(self, battle: Battle, actor: Unit, destination: Position) -> float:
        if not any(getattr(skill, "code", "") == "great_funeral" and skill.cooldown_remaining <= 0 for skill in actor.skills):
            return 0.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
        if not enemies:
            return 0.0
        aligned = 0
        near_line = 0
        for enemy in enemies:
            enemy_cells = battle.unit_cells(enemy)
            if any(cell.x == destination.x or cell.y == destination.y for cell in enemy_cells):
                aligned += 1
                continue
            distance_to_cross = min(min(abs(cell.x - destination.x), abs(cell.y - destination.y)) for cell in enemy_cells)
            if distance_to_cross <= 1:
                near_line += 1
        return aligned * 42.0 + near_line * 10.0


    def generic_skill_score(self,
        battle: Battle,
        actor: Unit,
        code: str,
        targets: list[Unit],
        profile: DifficultyProfile,
    ) -> float:
        enemies = [unit for unit in targets if unit.player_id != actor.player_id]
        allies = [unit for unit in targets if unit.player_id == actor.player_id]
        if enemies:
            return len(enemies) * 18.0 + profile.aggressive_bonus
        if allies:
            return len(allies) * 12.0 + profile.support_bonus
        return 6.0


    def summon_position_score(self, battle: Battle, actor: Unit, destination: Position) -> float:
        enemies = living_hostile_combatants(battle, actor.player_id)
        if not enemies:
            return 0.0
        nearest = min(self.services.distance_to_position(battle, unit, destination) for unit in enemies)
        return max(0.0, 4.0 - nearest) * 8.0


    def flame_chimera_position_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        skill = actor.get_skill("chimera")
        return skill.ai_priority_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload,
            self.services.difficulty_profile("standard")))


    def flame_password_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        skill = actor.get_skill("flame_password")
        return skill.ai_priority_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload,
            self.services.difficulty_profile("standard")))


    def wyvern_move_score(self, battle: Battle, mount: Unit, path: list[Position]) -> float:
        rider = battle.rider_for(mount)
        if rider is None or len(path) < 2:
            return 0.0
        combat = next((trait for trait in rider.traits if trait.name == "翼龙骑手追击"), None)
        locked = set(getattr(combat, "attacked_ids", ()))
        party = rider.has_status("夜空派对")
        seen: set[str] = set()
        rider_start = rider.position
        score = 0.0
        growth = 0
        for step in path[1:]:
            rider_step = rider_start.offset(step.x - path[0].x, step.y - path[0].y)
            for enemy in battle.enemy_units(rider.player_id):
                target = battle.effect_recipient(enemy)
                if (not target.alive or target.position is None or target.banished or target.unit_id in seen
                        or (target.unit_id in locked and not party)):
                    continue
                if min((body.distance_to(rider_step) for body in battle.unit_cells(target)), default=99) > 1:
                    continue
                seen.add(target.unit_id)
                damage = 0.0 if target.total_shields() > 0 else self.services.estimate_damage(
                    battle, target, rider.stat("attack") + growth)
                score += min(target.current_hp, damage) * 116.0
                score += 75.0 if damage >= target.current_hp > 0 else 0.0
                score += 10.0 if damage > 0 and target.current_mana > 0 and rider.current_mana < rider.max_mana() else 0.0
                score += 8.0 if target.total_shields() > 0 else 0.0
                growth += 1
        crossed_ids: set[str] = set()
        for target, _ in battle.path_crossing_events(mount, path):
            if target.unit_id in crossed_ids:
                continue
            crossed_ids.add(target.unit_id)
            speed_value = 12.0 if target.stat("speed") > 1 else 0.0
            score += speed_value if target.player_id != mount.player_id else -speed_value * 1.4
        return score


    def wyvern_resummon_score(self, battle: Battle, rider: Unit, profile: DifficultyProfile) -> float:
        if rider.position is None or battle.mounted_unit_for(rider) is not None:
            return -1000.0
        nearby = [enemy for enemy in living_hostile_combatants(battle, rider.player_id)
                  if enemy.position is not None and battle.distance_between_units(rider, enemy) <= 5]
        guard = 45.0 if rider.stat("defense") <= 2 and nearby else 20.0
        return guard + min(3, len(nearby)) * 20.0 + profile.aggressive_bonus


    def nova_quantum_separation_score(self,
        battle: Battle, actor: Unit, destination: Position | None, profile: DifficultyProfile,
    ) -> float:
        """Value only attacks the guard can cash in before its owner-turn expiry."""
        if destination is None:
            return -1000.0
        opportunities: list[float] = []
        for enemy in living_hostile_combatants(battle, actor.player_id):
            if enemy.position is None:
                continue
            distance = self.services.distance_to_position(battle, enemy, destination)
            if distance > 4:
                continue
            physical = min(enemy.current_hp, battle.damage_rule.calculate_damage(3, enemy.stat("defense")))
            skill_damage = 0.0 if enemy.magic_immunity or getattr(enemy, "skill_damage_immunity", False) else physical
            if enemy.total_shields() > 0:
                skill_damage = 0.0
            basic_line = any(
                destination.x == cell.x or destination.y == cell.y
                or abs(destination.x - cell.x) == abs(destination.y - cell.y)
                for cell in battle.unit_cells(enemy)
            )
            basic_damage = physical if distance <= 3 and basic_line and enemy.total_shields() == 0 else 0.0
            immediate = max(skill_damage * (2.0 if distance <= 3 else 1.0), basic_damage)
            score = immediate * 150.0 + max(0, 4 - distance) * 8.0
            if immediate >= enemy.current_hp - 1e-9:
                score += 65.0
            if enemy.total_shields() > 0:
                score += 14.0
            nearby_allies = sum(
                ally.alive and ally.position is not None and ally.unit_id != actor.unit_id
                and self.services.distance_to_position(battle, ally, enemy.position) <= 1
                for ally in battle.player_units(actor.player_id)
            )
            score -= nearby_allies * 12.0
            opportunities.append(score)
        if not opportunities:
            return -12.0
        opportunities.sort(reverse=True)
        return opportunities[0] + (opportunities[1] * 0.2 if len(opportunities) > 1 else 0.0)


    def nova_electromagnetic_weather_score(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        if battle.has_weather("电磁场"):
            return -1000.0
        score = 0.0
        for unit in battle.all_units():
            if not unit.alive or unit.banished or unit.position is None:
                continue
            paid_options = [skill for skill in unit.skills
                            if skill.mana_cost > 0 and skill.mana_cost <= unit.current_mana + 1e-9]
            if not paid_options:
                continue
            uses = min(2.0, len(paid_options) * 0.7)
            hp_loss = min(unit.current_hp, unit.max_health / 8.0)
            pressure = uses * hp_loss * 170.0
            if unit.current_hp <= unit.max_health / 8.0:
                pressure += 28.0
            if unit.player_id != actor.player_id and battle.distance_between_units(actor, unit) <= 3:
                pressure += uses * 12.0
            if unit.is_summon or unit.is_clone:
                pressure *= 0.5
            score += pressure if unit.player_id != actor.player_id else -pressure * 1.15
        if any(getattr(effect, "global_weather", False)
               and actor.player_id in getattr(effect, "weather_owner_player_ids", set())
               for effect in battle.field_effects):
            score -= 35.0
        score = score * 2.0 - 8.0
        return score if score >= profile.once_per_battle_threshold else -12.0


    def hell_dog_summon_score(self, battle: Battle, actor: Unit, destination: Position) -> float:
        enemies = [unit for unit in living_hostile_combatants(battle, actor.player_id)
                   if unit.position is not None]
        if not enemies:
            return -1000.0
        nearest = min(self.services.distance_to_position(battle, enemy, destination) for enemy in enemies)
        if nearest > 6:
            return -12.0
        from wujiang.tactical.heroes.mechanics.hell_dog_hunter import HellDogSummon
        dogs = [unit for unit in battle.all_units() if unit.alive and not unit.banished and isinstance(unit, HellDogSummon)
                and unit.summoner_id == actor.unit_id]
        ring = set(battle.neighbors(destination))
        blast_value = sum((1.0 if unit.player_id != actor.player_id else -1.4)
                          * min(unit.current_hp, battle.damage_rule.calculate_damage(3, unit.stat("defense"))) * 90.0
                          for unit in battle.effect_units_at_cells(list(ring))
                          if unit.unit_id != actor.unit_id)
        threatened = any(actor.position is not None and self.services.distance_between_units(battle, actor, enemy)
                         <= enemy.normal_move_distance() + enemy.targeting_range() for enemy in enemies)
        mana_reserve = 28.0 if threatened and actor.current_mana <= 1 else 0.0
        return (24.0 + max(0.0, 5.0 - nearest) * 12.0 + blast_value * 0.25
                - len(dogs) * 9.0 - mana_reserve)


    def stone_summon_score(self, battle: Battle, actor: Unit, code: str,
                           payload: dict[str, Any], profile: DifficultyProfile) -> float:
        if payload.get("x") is None or payload.get("y") is None:
            return -1000.0
        destination = Position(int(payload["x"]), int(payload["y"]))
        skill = actor.get_skill(code)
        if destination not in skill.available_cells(battle, actor):
            return -1000.0
        enemies = living_hostile_combatants(battle, actor.player_id)
        if not enemies:
            return -1000.0
        child = skill.child(actor)
        occupied = battle.unit_cells_at(child, destination)
        nearest = min(min(self.services.distance_to_position(battle, enemy, cell) for cell in occupied) for enemy in enemies)
        if nearest > 6:
            return -8.0
        value = (10.0 if getattr(skill, "small", False) else 20.0) + max(0, 6 - nearest) * 7.0
        allies = [unit for unit in battle.player_units(actor.player_id)
                  if unit is not actor and unit.position is not None and unit.alive and not unit.banished]
        for ally in allies:
            if any(battle.distance_between_units(ally, actor) <= 2 and
                   self.services.distance_to_position(battle, ally, cell) <= 1 for cell in occupied):
                value -= 14.0
        existing = sum(unit.hero_code == child.hero_code and unit.player_id == actor.player_id
                       for unit in battle.all_units())
        value -= max(0, existing - 2) * 12.0
        return value


    def stone_attack_spawn_penalty(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        penalty = 0.0
        for target in self.services.attack_payload_enemy_targets(battle, actor, payload):
            penalty += sum(score(battle, actor) for component in target.traits
                           if callable(score := getattr(component, "ai_spawn_penalty", None)))
        return penalty


_BOUND_SERVICE: AISelfAndGenericScoring | None = None

def bind_room(service: AISelfAndGenericScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AISelfAndGenericScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

@expected_numeric(independent=True)
def self_buff_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile,
                    *, skill: Any = None, action: dict[str, Any] | None = None,
                    payload: dict[str, Any] | None = None) -> float:
    return _require_service().self_buff_score(battle, actor, code, profile,
                                              skill=skill, action=action, payload=payload)


@expected_numeric(independent=True)
def best_available_attack_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().best_available_attack_score(battle, actor, profile)


@expected_numeric(independent=True)
def field_skill_score(
    battle: Battle,
    actor: Unit,
    code: str,
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    return _require_service().field_skill_score(battle, actor, code, targets, profile)


def square_cells_around_position(battle: Battle, center: Position, *, radius: int) -> list[Position]:
    return _require_service().square_cells_around_position(battle, center, radius=radius)


@expected_numeric(independent=True)
def judgment_stone_collision_move_score(
    battle: Battle, actor: Unit, destination: Position, profile: DifficultyProfile,
) -> float:
    return _require_service().judgment_stone_collision_move_score(battle, actor, destination, profile)


@expected_numeric(independent=True)
def judgment_stone_score(
    battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile,
) -> float:
    return _require_service().judgment_stone_score(battle, actor, payload, profile)


@expected_numeric(independent=True)
def world_seed_score(
    battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile,
) -> float:
    return _require_service().world_seed_score(battle, actor, payload, profile)


@expected_numeric(independent=True)
def feiwang_attack_value(battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> float:
    return _require_service().feiwang_attack_value(battle, actor, target, payload)


@expected_numeric(independent=True)
def feiwang_attack_plan_value(battle: Battle, actor: Unit) -> float:
    return _require_service().feiwang_attack_plan_value(battle, actor)


@expected_numeric(independent=True)
def feiwang_stance_score(battle: Battle, actor: Unit) -> float:
    return _require_service().feiwang_stance_score(battle, actor)


@expected_numeric(independent=True)
def feiwang_shensu_score(battle: Battle, actor: Unit) -> float:
    return _require_service().feiwang_shensu_score(battle, actor)


@expected_numeric(independent=True)
def kings_insight_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().kings_insight_score(battle, actor, profile)


@expected_numeric(independent=True)
def gale_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().gale_score(battle, actor, payload, profile)


@expected_numeric(independent=True)
def great_fire_funeral_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service().great_fire_funeral_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def great_fire_funeral_alignment_score_at(battle: Battle, actor: Unit, destination: Position) -> float:
    return _require_service().great_fire_funeral_alignment_score_at(battle, actor, destination)


@expected_numeric(independent=True)
def generic_skill_score(
    battle: Battle,
    actor: Unit,
    code: str,
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    return _require_service().generic_skill_score(battle, actor, code, targets, profile)


@expected_numeric(independent=True)
def summon_position_score(battle: Battle, actor: Unit, destination: Position) -> float:
    return _require_service().summon_position_score(battle, actor, destination)


@expected_numeric(independent=True)
def flame_chimera_position_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().flame_chimera_position_score(battle, actor, payload)


@expected_numeric(independent=True)
def flame_password_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().flame_password_score(battle, actor, payload)


@expected_numeric(independent=True)
def wyvern_move_score(battle: Battle, mount: Unit, path: list[Position]) -> float:
    return _require_service().wyvern_move_score(battle, mount, path)


@expected_numeric(independent=True)
def wyvern_resummon_score(battle: Battle, rider: Unit, profile: DifficultyProfile) -> float:
    return _require_service().wyvern_resummon_score(battle, rider, profile)


@expected_numeric(independent=True)
def nova_quantum_separation_score(
    battle: Battle, actor: Unit, destination: Position | None, profile: DifficultyProfile,
) -> float:
    """Value only attacks the guard can cash in before its owner-turn expiry."""
    return _require_service().nova_quantum_separation_score(battle, actor, destination, profile)


@expected_numeric(independent=True)
def nova_electromagnetic_weather_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().nova_electromagnetic_weather_score(battle, actor, profile)


@expected_numeric(independent=True)
def hell_dog_summon_score(battle: Battle, actor: Unit, destination: Position) -> float:
    return _require_service().hell_dog_summon_score(battle, actor, destination)


@expected_numeric(independent=True)
def stone_summon_score(battle: Battle, actor: Unit, code: str,
                       payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().stone_summon_score(battle, actor, code, payload, profile)


@expected_numeric(independent=True)
def stone_attack_spawn_penalty(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().stone_attack_spawn_penalty(battle, actor, payload)
