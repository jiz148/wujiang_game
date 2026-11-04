"""AILateEffectScoring: shared AI scoring and queries for effect_late."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from typing import Any, Callable, Iterable, Optional
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants
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

class AILateEffectScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    DAMAGING_SKILL_CODES: Any
    DifficultyProfile: Any
    ai_reachable_positions: Callable[..., Any]
    copied_skill_action: Any
    distance_to_position: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    legal_attack_payload: Callable[..., Any]
    r14_cat_attack_and_retreat_value: Callable[..., Any]
    r14_incoming_skill_loss: Callable[..., Any]
    r15_control_value: Callable[..., Any]
    r19_attack_window_value: Callable[..., Any]
    r19_incoming_position_risk: Callable[..., Any]
    reviewed_r18_effect_score: Callable[..., Any]
    reviewed_r19_effect_score: Callable[..., Any]
    scored_death_probability: Callable[..., Any]
    skill_effect_units: Callable[..., Any]
    skill_payloads_for_action: Any
    trim_skill_payloads_for_ai: Callable[..., Any]


class AILateEffectScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AILateEffectScoringServices) -> None:
        self.services = services

    def r14_snow_avalanche_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
        targets = self.services.skill_effect_units(battle, actor, skill, payload)
        with ai_probe_rollback(battle):
            before = {unit.unit_id: (unit.current_hp, unit.alive, unit.total_shields(), unit.has_status("雪崩"),
                                     not unit.cannot_attack, not unit.cannot_move,
                                     sum(candidate.timing == "active" and candidate.cooldown_remaining == 0
                                         and candidate.mana_cost <= unit.current_mana
                                         for candidate in unit.skills) if not unit.cannot_use_skills else 0)
                      for unit in targets}
            try:
                skill.execute(battle, actor, dict(payload))
            except (ActionError, KeyError, ValueError):
                return -1000.0
            score = -skill.mana_cost_for_payload(battle, actor, payload) * 18.0
            for unit in targets:
                hp, alive, shields, locked, attack, movement, active = before[unit.unit_id]
                value = max(0.0, hp - unit.current_hp) * 100.0 + max(0, shields - unit.total_shields()) * 20.0
                if alive and self.services.scored_death_probability(unit) > 0:
                    value += (80.0 * self.services.scored_death_probability(unit) if direct_scoring() else 80.0)
                if unit.alive and not locked and unit.has_status("雪崩"):
                    value += (24.0 if attack else 0.0) + (28.0 if movement else 0.0) + 9.0 * active
                score += value if unit.player_id != actor.player_id else -value * 1.3
            return score if score > 0 else -1000.0


    def r14_incoming_skill_loss(self, battle: Battle, actor: Unit) -> float:
        total = 0.0
        for enemy in living_hostile_combatants(battle, actor.player_id):
            with ai_probe_rollback(battle):
                battle.turn_number += 1
                battle.active_player, battle._exclusive_turn_unit_id, enemy.turn_ready = enemy.player_id, enemy.unit_id, True
                battle.resolving_action = None
                best = 0.0
                for skill in enemy.skills:
                    if skill.timing != "active" or skill.code not in self.services.DAMAGING_SKILL_CODES:
                        continue
                    try:
                        if not skill.can_use(battle, enemy, {})[0]:
                            continue
                        action = self.services.copied_skill_action(battle, enemy, skill)
                        payloads = self.services.trim_skill_payloads_for_ai(battle, enemy, self.services.skill_payloads_for_action(battle, enemy, action), limit=16)
                        for payload in payloads:
                            target = battle.effect_recipient(actor)
                            if target not in self.services.skill_effect_units(battle, enemy, skill, payload):
                                continue
                            with ai_probe_rollback(battle):
                                hp = target.current_hp
                                battle.pending_followup_actions.clear()
                                queued = battle.build_queued_action(payload)
                                battle.prepay_skill_resources(skill, enemy, queued.payload)
                                queued.payload["resources_prepaid"] = True
                                enemy.notify_action_declared(battle, "skill", queued.payload)
                                queued.payload["declared_source_attack"] = enemy.stat("attack")
                                battle.resolve_queued_action(queued)
                                while battle.pending_followup_actions:
                                    battle.resolve_queued_action(battle.pending_followup_actions.popleft())
                                best = max(best, max(0.0, hp - target.current_hp) * 110.0)
                    except (ActionError, KeyError, ValueError, TypeError):
                        continue
                total += best
        return total


    def r14_cat_form_score(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        from wujiang.tactical.heroes.mechanics.black_cat import BlackCatFormStatus
        before = self.services.r14_incoming_skill_loss(battle, actor) + self.services.r19_incoming_position_risk(battle, actor)
        output = self.services.r14_cat_attack_and_retreat_value(battle, actor)
        with ai_probe_rollback(battle):
            existing = actor.get_status("化猫")
            if existing is not None:
                actor.remove_status(existing, battle)
            else:
                actor.add_status(BlackCatFormStatus())
            after = self.services.r14_incoming_skill_loss(battle, actor) + self.services.r19_incoming_position_risk(battle, actor)
            new_output = self.services.r14_cat_attack_and_retreat_value(battle, actor)
            value = before - after + new_output - output
            return value if value > 18.0 else -8.0


    def r14_cat_attack_and_retreat_value(self, battle: Battle, actor: Unit) -> float:
        enemies = living_hostile_combatants(battle, actor.player_id)
        if actor.position is None or actor.cannot_attack or not enemies:
            return 0.0
        origins = [actor.position]
        distance = actor.remaining_normal_move_distance(battle)
        if distance > 0 and not actor.cannot_move and not actor.cannot_normal_move:
            origins += self.services.ai_reachable_positions(battle, actor, max_distance=distance, use_movement_cost=True)
        best = 0.0
        for target in battle.effect_units(enemies):
            with ai_probe_rollback(battle):
                legal = []
                for origin in origins:
                    actor.position = origin
                    if battle.attack_target_allowed(actor, target)[0]:
                        legal.append(origin)
            for origin in sorted(legal, key=lambda cell: cell.distance_to(actor.position))[:8]:
                with ai_probe_rollback(battle):
                    try:
                        if origin != actor.position:
                            battle.move_unit(actor, origin)
                        hp, alive = target.current_hp, target.alive
                        queued = battle.build_queued_action({"type": "attack", "unit_id": actor.unit_id,
                                                            "target_unit_id": target.unit_id})
                        actor.notify_action_declared(battle, "attack", queued.payload)
                        queued.payload["declared_source_attack"] = actor.stat("attack")
                        battle.resolve_queued_action(queued)
                        if not actor.alive or actor.banished:
                            continue
                        gain = max(0.0, hp - target.current_hp) * 100.0 + (80.0 if alive and not target.alive else 0.0)
                        escapes = [actor.position]
                        remaining = actor.remaining_normal_move_distance(battle)
                        if remaining > 0 and not actor.cannot_move and not actor.cannot_normal_move:
                            escapes += self.services.ai_reachable_positions(battle, actor, max_distance=remaining, use_movement_cost=True)
                        escape_risk = float("inf")
                        for cell in sorted(set(escapes), key=lambda cell: min((cell.distance_to(enemy.position) for enemy in enemies
                                                                            if enemy.position is not None), default=0), reverse=True)[:8]:
                            with ai_probe_rollback(battle):
                                if cell != actor.position:
                                    battle.move_unit(actor, cell)
                                escape_risk = min(escape_risk, self.services.r19_incoming_position_risk(battle, actor))
                        best = max(best, gain - escape_risk)
                    except (ActionError, KeyError, ValueError, TypeError):
                        continue
        return best


    def kaiser_fist_score(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        targets = [unit for unit in self.services.skill_effect_units(battle, actor, skill, payload) if unit.player_id != actor.player_id]
        if not targets:
            return -20.0
        target = targets[0]
        with ai_probe_rollback(battle):
            hp, mana, shields, alive = target.current_hp, actor.current_mana, target.total_shields(), target.alive
            value = self.services.hostile_unit_value(target)
            try:
                skill.execute(battle, actor, dict(payload))
            except (ActionError, KeyError, ValueError):
                return -1000.0
            damage = max(0.0, hp - target.current_hp)
            # Clone destruction is not HP damage for the compensation clause.
            if target.is_clone:
                damage = 0.0
            score = damage * 100.0 + max(0.0, actor.current_mana - mana) * 28.0
            score += max(0, shields - target.total_shields()) * 42.0
            if alive and not target.alive:
                score += 60.0 + value * 0.25
            if damage > 0:
                score += value * 0.45 + profile.aggressive_bonus
            return score if score > 0 else -100.0


    def r15_control_value(self, battle: Battle, target: Unit, code: str) -> float:
        from wujiang.tactical.heroes.mechanics.noise_interference import NoiseWaveSkill
        from wujiang.tactical.heroes.mechanics.purify_duel import SacredDuelSkill

        # Historical public helper; the active score path already has the skill object.
        skill = NoiseWaveSkill() if code == "noise_wave" else SacredDuelSkill()
        return skill.ai_r15_control_value(battle, target)


    def reviewed_r15_effect_score(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        targets = self.services.skill_effect_units(battle, actor, skill, payload)
        control = {unit.unit_id: skill.ai_r15_control_value(battle, unit) for unit in targets}
        with ai_probe_rollback(battle):
            before = {unit.unit_id: (unit.current_hp, unit.alive, unit.player_id, unit.current_mana,
                                    unit.total_shields(), self.services.hostile_unit_value(unit),
                                    {status.name for status in unit.statuses}) for unit in battle.all_units()}
            probe = dict(payload)
            probe.update(skill.queued_payload_metadata(battle, actor, probe))
            try:
                skill.execute(battle, actor, probe)
            except (ActionError, KeyError, ValueError):
                return -1000.0
            score = -skill.mana_cost_for_payload(battle, actor, probe) * 18.0
            for unit_id, (hp, alive, player, mana, shields, value, statuses) in before.items():
                unit = battle.units.get(unit_id)
                lost_hp = max(0.0, hp - (unit.current_hp if unit is not None else 0.0))
                destroyed = alive and (unit is None or not unit.alive)
                impact = lost_hp * 100.0 + (60.0 + value * 0.25 if destroyed else 0.0)
                if unit is not None:
                    impact += max(0, shields - unit.total_shields()) * 14.0
                    impact += skill.ai_r15_post_effect_bonus(
                        actor, unit, alive=alive, player=player, mana=mana,
                        value=value, statuses=statuses, control=control.get(unit_id, 0.0))
                score += impact if player != actor.player_id else -impact * 1.3
            return score + profile.aggressive_bonus if score > 0 else score - 100.0


    def reviewed_r19_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        score = self.services.reviewed_r18_effect_score(battle, actor, skill, payload, profile)
        control_scorer = getattr(skill, "ai_control_effect_score", None)
        if control_scorer is not None:
            control_score = control_scorer(battle, actor, payload)
            if control_score is None:
                return -1000.0
            score += control_score
        if skill.max_uses_per_battle == 1 and score < profile.once_per_battle_threshold:
            score -= 32.0
        return score


    def r19_attack_window_value(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        if actor.position is None or actor.banished or not actor.alive:
            return 0.0
        origins = [actor.position]
        if not actor.cannot_move and not actor.cannot_normal_move:
            origins += self.services.ai_reachable_positions(battle, actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
        value = 0.0
        for target in battle.effect_units(living_hostile_combatants(battle, actor.player_id)):
            if not battle.unit_can_be_selected(target, actor=actor)[0]:
                continue
            if not actor.cannot_attack and actor.attacks_used < actor.attack_actions_per_turn() and any(self.services.distance_to_position(battle, target, origin) <= actor.targeting_range() for origin in origins):
                with ai_probe_rollback(battle):
                    actor.position = next(origin for origin in origins if self.services.distance_to_position(battle, target, origin) <= actor.targeting_range())
                    attack_payload = self.services.legal_attack_payload(battle, actor, target)
                    if attack_payload is not None:
                        hp, alive, shields = target.current_hp, target.alive, target.total_shields()
                        battle.resolve_attack_damage(actor, target, action_name="AI普攻窗口", tags=set(), payload=attack_payload)
                        damage = max(0.0, hp - target.current_hp)
                        value = max(value, damage * 100 + max(0, shields - target.total_shields()) * 20
                                    + (70 if alive and not target.alive else 0))
        if not actor.cannot_use_skills:
            for skill in actor.skills:
                if skill.code not in {"hell_slash", "beetle_spear"} or skill.cooldown_remaining > 0:
                    continue
                if skill.max_uses_per_battle is not None and skill.uses_this_battle >= skill.max_uses_per_battle:
                    continue
                if any(component.blocks_skill_use(battle, actor, skill)[0] for component in actor.iter_components()):
                    continue
                if not actor.direct_effects_blocked() and any(effect.blocks_skill_use(battle, actor, skill)[0] for effect in battle.field_effects):
                    continue
                for cells in skill.patterns(battle, actor):
                    payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": skill.code, "cells": [{"x": cell.x, "y": cell.y} for cell in cells]}
                    value = max(value, self.services.reviewed_r19_effect_score(battle, actor, skill, payload, profile))
        return value


    def r19_incoming_position_risk(self, battle: Battle, actor: Unit, *, until_next_enemy_end: bool = False) -> float:
        target = battle.effect_recipient(actor)
        value = 0.0
        enemies = living_hostile_combatants(battle, actor.player_id)
        if until_next_enemy_end:
            order = battle.turn_order_unit_ids
            first = next((battle.units.get(order[(battle.turn_slot_index + step) % len(order)])
                          for step in range(1, len(order) + 1)
                          if battle.units.get(order[(battle.turn_slot_index + step) % len(order)]) is not None
                          and battle.units[order[(battle.turn_slot_index + step) % len(order)]].player_id != actor.player_id
                          and battle.units[order[(battle.turn_slot_index + step) % len(order)]].alive), None)
            ids = {unit.unit_id for unit in battle.turn_bundle_units(first)} if first is not None else set()
            enemies = [enemy for enemy in enemies if enemy.unit_id in ids]
        for enemy in enemies:
            if enemy.cannot_attack:
                continue
            with ai_probe_rollback(battle):
                battle.resolving_action = None
                battle.active_player, battle._exclusive_turn_unit_id, enemy.turn_ready = enemy.player_id, enemy.unit_id, True
                enemy.attacks_used = enemy.normal_move_actions_used = enemy.normal_move_steps_used = 0
                enemy.move_used = False
                origins = [enemy.position]
                if not enemy.cannot_move and not enemy.cannot_normal_move:
                    origins += self.services.ai_reachable_positions(battle, enemy, max_distance=enemy.remaining_normal_move_distance(battle), use_movement_cost=True)
                best = 0.0
                for origin in origins:
                    enemy.position = origin
                    if not battle.attack_target_allowed(enemy, target)[0]:
                        continue
                    with ai_probe_rollback(battle):
                        hp, alive = target.current_hp, target.alive
                        battle.resolve_attack_damage(enemy, target, action_name="AI近敌风险")
                        best = max(best, max(0.0, hp - target.current_hp) * 100 + (90 if alive and not target.alive else 0))
                value += best
        return value


    def r19_preparation_score(self, battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
        before = self.services.r19_attack_window_value(battle, actor, profile)
        short_window = code == "martial_god_seal"
        danger = self.services.r19_incoming_position_risk(battle, actor, until_next_enemy_end=short_window)
        target = battle.effect_recipient(actor)
        hp, mana = target.current_hp, target.current_mana
        with ai_probe_rollback(battle):
            actor.get_skill(code).execute(battle, actor, {})
            after = self.services.r19_attack_window_value(battle, actor, profile)
            risk = self.services.r19_incoming_position_risk(battle, actor, until_next_enemy_end=short_window)
            recovery = max(0.0, target.current_hp - hp) * 130
            # Temporary mana is valuable only while output or a near-term defense can use it.
            if (after > 0 or danger > 0) and any(skill.mana_cost > 0 for skill in target.skills):
                recovery += max(0.0, target.current_mana - mana) * 12
            gain = after - before + danger - risk + recovery
        if gain <= 8.0:
            return -20.0
        return max(after, before) + gain + 18.0


_BOUND_SERVICE: AILateEffectScoring | None = None

def bind_room(service: AILateEffectScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AILateEffectScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

@expected_numeric(independent=True)
def r14_snow_avalanche_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    return _require_service().r14_snow_avalanche_score(battle, actor, skill, payload)


@expected_numeric(independent=True)
def r14_incoming_skill_loss(battle: Battle, actor: Unit) -> float:
    return _require_service().r14_incoming_skill_loss(battle, actor)


@expected_numeric(independent=True)
def r14_cat_form_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().r14_cat_form_score(battle, actor, profile)


@expected_numeric(independent=True)
def r14_cat_attack_and_retreat_value(battle: Battle, actor: Unit) -> float:
    return _require_service().r14_cat_attack_and_retreat_value(battle, actor)


@expected_numeric(independent=True)
def kaiser_fist_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service().kaiser_fist_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def r15_control_value(battle: Battle, target: Unit, code: str) -> float:
    return _require_service().r15_control_value(battle, target, code)


@expected_numeric(independent=True)
def reviewed_r15_effect_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service().reviewed_r15_effect_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True, eager_cache=True)
def reviewed_r19_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().reviewed_r19_effect_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def r19_attack_window_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().r19_attack_window_value(battle, actor, profile)


@expected_numeric(independent=True)
def r19_incoming_position_risk(battle: Battle, actor: Unit, *, until_next_enemy_end: bool = False) -> float:
    return _require_service().r19_incoming_position_risk(battle, actor, until_next_enemy_end=until_next_enemy_end)


@expected_numeric(independent=True)
def r19_preparation_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    return _require_service().r19_preparation_score(battle, actor, code, profile)
