"""Adapters that connect hero policy objects to shared room services."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from wujiang.tactical.rooms.ai_types import DifficultyProfile
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
from wujiang.tactical.heroes.ai_policy import (AIReactionCandidateContext,
    AIReactionScoreContext, AISkillCandidateContext, AISkillScoreContext,
    BarrierShieldAIPolicy, FreeReactionScoring, ShieldReactionScoring)
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
from wujiang.tactical.rooms.ai_skill_catalog import (
    AI_DIFFICULTIES, SUPPORT_HERO_CODES, SUMMON_SKILL_CODES, HEAL_SKILL_CODES,
    ALLY_BUFF_SKILL_CODES, SELF_BUFF_SKILL_CODES, MOVE_SKILL_CODES,
    AREA_ESCAPE_REACTION_CODES, DAMAGING_SKILL_CODES, CONTROL_SKILL_CODES,
    REACTION_SHIELD_CODES, HOSTILE_EFFECT_SKILL_CODES,
)
from wujiang.tactical.rooms.ai_types import AICandidate, DifficultyProfile, difficulty_profile

class _SharedSkillScoringServices(Protocol):
    """Named dependencies used by this policy adapter."""
    ai_reachable_positions: Callable[..., Any]
    best_available_attack_score: Callable[..., Any]
    best_move_destination_score: Callable[..., Any]
    copied_skill_action: Any
    distance_to_position: Callable[..., Any]
    estimate_damage: Callable[..., Any]
    friendly_fire_penalty: Callable[..., Any]
    hell_shuffle_score: Callable[..., Any]
    hero_style: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    morning_holy_light_score: Callable[..., Any]
    natsume_unit_danger: Callable[..., Any]
    nova_electromagnetic_weather_score: Callable[..., Any]
    payload_destination: Callable[..., Any]
    payload_is_legal: Callable[..., Any]
    preview_positions: Callable[..., Any]
    r12_effect_score: Callable[..., Any]
    r13_bat_score: Callable[..., Any]
    r13_effect_score: Callable[..., Any]
    reviewed_r15_effect_score: Callable[..., Any]
    guardian_finale_score: Callable[..., Any]
    magnetic_wave_score: Callable[..., Any]
    judgment_stone_score: Callable[..., Any]
    world_seed_score: Callable[..., Any]
    kaiser_fist_score: Callable[..., Any]
    fuma_trap_score: Callable[..., Any]
    friendly_mirror_score: Callable[..., Any]
    great_fire_funeral_score: Callable[..., Any]
    sandstorm_value: Callable[..., Any]
    reviewed_r16_damage_result: Callable[..., Any]
    agency_contract_score: Callable[..., Any]
    weapon_copy_score: Callable[..., Any]
    red_preparation_score: Callable[..., Any]
    deadly_bow_score: Callable[..., Any]
    iron_chain_path_score: Callable[..., Any]
    sphinx_cannon_score: Callable[..., Any]
    solar_judgment_score: Callable[..., Any]
    taunt_control_score: Callable[..., Any]
    field_skill_score: Callable[..., Any]
    drain_mana_score: Callable[..., Any]
    r14_snow_avalanche_score: Callable[..., Any]
    r215219_skill_score: Callable[..., Any]
    r21_control_score: Callable[..., Any]
    r21_winged_leap_score: Callable[..., Any]
    r23_action_delta: Callable[..., Any]
    r23_action_state: Callable[..., Any]
    r23_paid_skill_value: Callable[..., Any]
    r23_sun_slash_score: Callable[..., Any]
    red_outcome_snapshot: Callable[..., Any]
    red_outcome_value: Callable[..., Any]
    reviewed_r17_effect_score: Callable[..., Any]
    reviewed_r17_position_value: Callable[..., Any]
    reviewed_r18_effect_score: Callable[..., Any]
    reviewed_r19_effect_score: Callable[..., Any]
    score_move_destination: Callable[..., Any]
    score_skill_payload: Callable[..., Any]
    scored_death_probability: Callable[..., Any]
    skill_attack_power: Callable[..., Any]
    skill_damage_score: Callable[..., Any]
    skill_effect_cells: Callable[..., Any]
    skill_effect_units: Callable[..., Any]
    skill_from_ai_action: Any
    skill_owned_effect_score: Callable[..., Any]
    skill_payload_has_effective_enemy_impact: Callable[..., Any]
    skill_payloads_for_action: Any
    stone_summon_score: Callable[..., Any]
    summon_position_score: Callable[..., Any]
    wuchang_exposed_action_value: Callable[..., Any]


class _SharedSkillScoring:
    """Adapter from the existing generic AI calculations to hero-owned policies."""

    def __init__(self, services: _SharedSkillScoringServices) -> None:
        self.services = services


    def skill_from_action(self, actor: Unit, action: dict, code: str) -> Any:
        return self.services.skill_from_ai_action(actor, action, code)

    def effect_units(self, battle: Battle, actor: Unit, skill: Any, payload: dict) -> Iterable[Unit]:
        return self.services.skill_effect_units(battle, actor, skill, payload)

    def damage_score(self, battle: Battle, actor: Unit, skill: Any,
                     payload: dict, profile: DifficultyProfile) -> float:
        return self.services.skill_damage_score(battle, actor, skill, payload, profile)

    def r17_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                         payload: dict, profile: DifficultyProfile) -> float:
        return self.services.reviewed_r17_effect_score(battle, actor, skill, payload, profile)

    def r18_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                         payload: dict, profile: DifficultyProfile) -> float:
        return self.services.reviewed_r18_effect_score(battle, actor, skill, payload, profile)

    def r19_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                         payload: dict, profile: DifficultyProfile) -> float:
        return self.services.reviewed_r19_effect_score(battle, actor, skill, payload, profile)

    def owned_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                           payload: dict, profile: DifficultyProfile) -> float:
        return self.services.skill_owned_effect_score(battle, actor, skill, payload, profile)

    def attack_damage(self, battle: Battle, source: Unit, target: Unit) -> float:
        return estimate_attack_damage(battle, source, target, {}, attack_power=source.stat("attack"))

    def reachable_positions(self, battle: Battle, unit: Unit) -> list[Position]:
        return self.services.ai_reachable_positions(battle, unit, max_distance=unit.remaining_normal_move_distance(battle), use_movement_cost=True)

    def reachable_positions_for(self, battle: Battle, unit: Unit, distance: int) -> list[Position]:
        return self.services.ai_reachable_positions(battle, unit, max_distance=distance)

    def best_move_score(self, battle: Battle, unit: Unit, cells: list[Position],
                        profile: DifficultyProfile) -> float:
        return self.services.best_move_destination_score(battle, unit, cells, profile)

    def effect_cells(self, battle: Battle, actor: Unit, skill: Any, payload: dict) -> list[Position]:
        return self.services.skill_effect_cells(battle, actor, skill, payload)

    def area_damage_value(self, battle: Battle, actor: Unit, skill: Any,
                          payload: dict, cells: list[Position]) -> float:
        score = 0.0
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            damage = estimate_skill_damage(
                battle, actor, skill, payload, target,
                self.services.skill_attack_power(battle, actor, skill, payload, target, cells),
                cells=cells, ignore_shield=skill.ignores_shield_for_payload(battle, actor, payload),
                half_ignore_shield=False,
            )
            loss = min(target.current_hp, damage)
            if loss <= 0:
                continue
            value = loss * 100.0
            if damage >= target.current_hp - 1e-9:
                value += 90.0
            score += value if target.player_id != actor.player_id else -value * 1.35
        return score

    def move_score(self, battle: Battle, actor: Unit, destination: Position,
                   role: str, profile: DifficultyProfile) -> float:
        return self.services.score_move_destination(battle, actor, destination, role, profile)

    def hero_style(self, actor: Unit) -> str:
        return self.services.hero_style(actor)

    def estimate_damage(self, battle: Battle, target: Unit, power: float,
                        *, ignore_shield: bool = False) -> float:
        return self.services.estimate_damage(battle, target, power, ignore_shield=ignore_shield)

    def distance_to_position(self, battle: Battle, unit: Unit, destination: Position) -> int:
        return self.services.distance_to_position(battle, unit, destination)

    def hostile_value(self, target: Unit) -> float:
        return self.services.hostile_unit_value(target)

    def friendly_penalty(self, target: Unit) -> float:
        return self.services.friendly_fire_penalty(target)

    def destination(self, payload: dict) -> Position | None:
        return self.services.payload_destination(payload)

    def summon_position_score(self, battle: Battle, actor: Unit,
                              destination: Position) -> float:
        return self.services.summon_position_score(battle, actor, destination)

    def hostile_units(self, battle: Battle, actor: Unit) -> Iterable[Unit]:
        return living_hostile_combatants(battle, actor.player_id)

    def is_move_skill(self, code: str) -> bool:
        return code in MOVE_SKILL_CODES

    def preview_positions(self, raw_cells: Any) -> list[Position]:
        return self.services.preview_positions(raw_cells)

    def is_damaging_skill(self, code: str) -> bool:
        return code in DAMAGING_SKILL_CODES

    def is_heal_skill(self, code: str) -> bool:
        return code in HEAL_SKILL_CODES

    def hell_shuffle_score(self, battle: Battle, actor: Unit, payload: dict) -> float:
        return self.services.hell_shuffle_score(battle, actor, payload)

    def electromagnetic_weather_score(self, battle: Battle, actor: Unit,
                                      profile: DifficultyProfile) -> float:
        return self.services.nova_electromagnetic_weather_score(battle, actor, profile)

    def paid_skill_value(self, battle: Battle, actor: Unit, payload: dict) -> float:
        return self.services.r23_paid_skill_value(battle, actor, payload)

    def bird_skill_score(self, battle: Battle, actor: Unit, payload: dict) -> float:
        return self.services.r215219_skill_score(battle, actor, payload)

    def stone_summon_score(self, battle: Battle, actor: Unit, code: str,
                           payload: dict, profile: DifficultyProfile) -> float:
        return self.services.stone_summon_score(battle, actor, code, payload, profile)

    def effective_enemy_impact(self, battle: Battle, actor: Unit,
                               action: dict, payload: dict) -> bool:
        return self.services.skill_payload_has_effective_enemy_impact(battle, actor, action, payload)

    def r12_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                         payload: dict, profile: DifficultyProfile) -> float:
        return self.services.r12_effect_score(battle, actor, skill, payload, profile)

    def r13_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                         payload: dict, profile: DifficultyProfile) -> float:
        return self.services.r13_effect_score(battle, actor, skill, payload, profile)

    def r15_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                         payload: dict, profile: DifficultyProfile) -> float:
        return self.services.reviewed_r15_effect_score(battle, actor, skill, payload, profile)

    def guardian_finale_score(self, context: AISkillScoreContext) -> float:
        return self.services.guardian_finale_score(context.battle, context.actor, context.profile)

    def magnetic_wave_score(self, context: AISkillScoreContext) -> float:
        return self.services.magnetic_wave_score(context.battle, context.actor, context.skill(context.action["code"]), context.payload)

    def judgment_stone_score(self, context: AISkillScoreContext) -> float:
        return self.services.judgment_stone_score(context.battle, context.actor, context.payload, context.profile)

    def world_seed_score(self, context: AISkillScoreContext) -> float:
        return self.services.world_seed_score(context.battle, context.actor, context.payload, context.profile)

    def kaiser_fist_score(self, context: AISkillScoreContext) -> float:
        return self.services.kaiser_fist_score(context.battle, context.actor, context.skill(context.action["code"]), context.payload, context.profile)

    def fuma_trap_score(self, context: AISkillScoreContext) -> float:
        return self.services.fuma_trap_score(context.battle, context.actor, context.payload, context.profile)

    def fuma_shuriken_score(self, context: AISkillScoreContext) -> float:
        skill = context.skill(context.action["code"])
        return self.services.reviewed_r16_damage_result(
            context.battle, context.actor,
            lambda: skill.execute(context.battle, context.actor, context.payload),
        ) - 8.0

    def friendly_mirror_score(self, context: AISkillScoreContext) -> float:
        return self.services.friendly_mirror_score(context.battle, context.actor, context.profile)

    def great_funeral_score(self, context: AISkillScoreContext) -> float:
        return self.services.great_fire_funeral_score(context.battle, context.actor, context.skill(context.action["code"]), context.payload, context.profile)

    def wind_sand_score(self, context: AISkillScoreContext) -> float:
        skill = context.skill(context.action["code"])
        if not list(context.effect_units(skill)):
            return -1000.0
        return (self.services.skill_damage_score(context.battle, context.actor, skill, context.payload, context.profile)
                + self.services.sandstorm_value(context.battle, context.actor))

    def agency_contract_score(self, context: AISkillScoreContext) -> float:
        return self.services.agency_contract_score(context.battle, context.actor, context.payload, context.profile)

    def weapon_copy_score(self, context: AISkillScoreContext) -> float:
        return self.services.weapon_copy_score(context.battle, context.actor, context.payload, context.profile)

    def red_preparation_score(self, context: AISkillScoreContext) -> float:
        return self.services.red_preparation_score(context.battle, context.actor, context.action["code"], context.profile)

    def deadly_bow_score(self, context: AISkillScoreContext) -> float:
        return self.services.deadly_bow_score(context.battle, context.actor, context.action, context.payload, context.profile)

    def iron_chain_path_score(self, context: AISkillScoreContext) -> float:
        return self.services.iron_chain_path_score(context.battle, context.actor, context.skill(context.action["code"]), context.payload, context.profile)

    def sphinx_cannon_score(self, context: AISkillScoreContext) -> float:
        return self.services.sphinx_cannon_score(context.battle, context.actor, context.payload)

    def solar_judgment_score(self, context: AISkillScoreContext) -> float:
        return self.services.solar_judgment_score(context.battle, context.actor, context.skill(context.action["code"]), context.payload, context.profile)

    def taunt_control_score(self, context: AISkillScoreContext) -> float:
        skill = context.skill(context.action["code"])
        targets = context.effect_units(skill)
        return self.services.taunt_control_score(
            context.battle, context.actor, skill, context.payload, context.action["code"], targets, context.profile)

    def field_skill_score(self, context: AISkillScoreContext) -> float:
        skill = context.skill(context.action["code"])
        targets = context.effect_units(skill)
        return self.services.field_skill_score(context.battle, context.actor, context.action["code"], targets, context.profile)

    def drain_mana_score(self, context: AISkillScoreContext) -> float:
        skill = context.skill(context.action["code"])
        targets = context.effect_units(skill)
        return self.services.drain_mana_score(context.battle, context.actor, targets, context.profile)

    def r13_bat_score(self, battle: Battle, actor: Unit, skill: Any,
                      payload: dict, profile: DifficultyProfile) -> float:
        return self.services.r13_bat_score(battle, actor, skill, payload, profile)

    def sun_slash_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict) -> float:
        return self.services.r23_sun_slash_score(battle, actor, skill, payload)

    def control_score(self, battle: Battle, actor: Unit, skill: Any,
                      payload: dict, profile: DifficultyProfile) -> float:
        return self.services.r21_control_score(battle, actor, skill, payload, profile)

    def winged_leap_score(self, battle: Battle, actor: Unit, payload: dict) -> float:
        return self.services.r21_winged_leap_score(battle, actor, payload)

    def snow_avalanche_score(self, battle: Battle, actor: Unit,
                             skill: Any, payload: dict) -> float:
        return self.services.r14_snow_avalanche_score(battle, actor, skill, payload)

    def morning_holy_light_score(self, battle: Battle, actor: Unit,
                                  skill: Any, payload: dict) -> float:
        return self.services.morning_holy_light_score(battle, actor, skill, payload)

    def probe_rollback(self, battle: Battle) -> Any:
        return ai_probe_rollback(battle)

    def action_state(self, battle: Battle) -> dict:
        return self.services.r23_action_state(battle)

    def action_delta(self, actor: Unit, before: dict) -> float:
        return self.services.r23_action_delta(actor, before)

    def best_attack_score(self, battle: Battle, actor: Unit,
                          profile: DifficultyProfile) -> float:
        return self.services.best_available_attack_score(battle, actor, profile)

    def position_value(self, battle: Battle, actor: Unit, target: Unit) -> float:
        return self.services.reviewed_r17_position_value(battle, actor, target)

    def unit_danger(self, battle: Battle, unit: Unit) -> float:
        return self.services.natsume_unit_danger(battle, unit)

    def outcome_snapshot(self, battle: Battle) -> dict[str, tuple]:
        return self.services.red_outcome_snapshot(battle)

    def outcome_value(self, actor: Unit, before: dict[str, tuple]) -> float:
        return self.services.red_outcome_value(actor, before)

    def action_exposure(self, battle: Battle, unit: Unit) -> float:
        return self.services.wuchang_exposed_action_value(battle, unit)

    def death_probability(self, unit: Unit) -> float:
        return self.services.scored_death_probability(unit)

    def direct_scoring(self) -> bool:
        return direct_scoring()

    def candidate_payloads_for_skill(self, battle: Battle, actor: Unit, skill: Any) -> list[dict]:
        return self.services.skill_payloads_for_action(battle, actor, self.services.copied_skill_action(battle, actor, skill))

    def candidate_is_legal(self, battle: Battle, payload: dict) -> bool:
        return self.services.payload_is_legal(battle, payload)

    def score_candidate(self, battle: Battle, actor: Unit, skill: Any,
                        payload: dict, profile: DifficultyProfile) -> float:
        return self.services.score_skill_payload(battle, actor, self.services.copied_skill_action(battle, actor, skill),
                                   payload, profile, instant_only=False)


class _SharedReactionScoringServices(Protocol):
    """Named dependencies used by this policy adapter."""
    _score_reaction_payload: Callable[..., Any]
    _skill_score_context: Callable[..., Any]
    active_damage_mana_reserve: Callable[..., Any]
    barrier_prevention_score: Callable[..., Any]
    cat_retaliation_action_penalty: Callable[..., Any]
    copied_skill_action: Any
    destination_still_in_queued_target_area: Callable[..., Any]
    estimate_damage: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    incoming_threat_score: Callable[..., Any]
    natsume_unit_danger: Callable[..., Any]
    paid_reaction_score: Callable[..., Any]
    payload_destination: Callable[..., Any]
    preview_positions: Callable[..., Any]
    primary_target_unit: Callable[..., Any]
    queued_shield_protected_unit_ids: Callable[..., Any]
    r19_attack_window_value: Callable[..., Any]
    r21_attack_value: Callable[..., Any]
    r22_attack_value: Callable[..., Any]
    red_outcome_snapshot: Callable[..., Any]
    red_outcome_value: Callable[..., Any]
    score_reaction_payload: Callable[..., Any]
    simulated_reaction_score: Callable[..., Any]
    stone_attack_spawn_penalty: Callable[..., Any]
    temporary_defense_prevention_score: Callable[..., Any]


class _SharedReactionScoring:
    def base_score(self, battle: Battle, actor: Unit, queued: QueuedAction,
                   option: dict, payload: dict, profile: DifficultyProfile) -> float:
        return self.services._score_reaction_payload(battle, actor, queued, option, payload, profile)

    def __init__(self, services: _SharedReactionScoringServices) -> None:
        self.services = services


    def positions(self, raw_cells: Any) -> list[Position]:
        return self.services.preview_positions(raw_cells)

    def damage(self, battle: Battle, target: Unit, power: float) -> float:
        return self.services.estimate_damage(battle, target, power)

    def primary_target(self, battle: Battle, payload: dict) -> Unit | None:
        return self.services.primary_target_unit(battle, payload, [])

    def threat(self, battle: Battle, target: Unit, queued: QueuedAction) -> float:
        return self.services.incoming_threat_score(battle, target, queued)

    def skill_context(self, battle: Battle, actor: Unit, payload: dict,
                      profile: DifficultyProfile, code: str) -> AISkillScoreContext:
        return self.services._skill_score_context(battle, actor, {"code": code}, payload, profile)

    def destination(self, payload: dict) -> Position | None:
        return self.services.payload_destination(payload)

    def destination_still_targeted(self, battle: Battle, actor: Unit,
                                   destination: Position, queued: QueuedAction) -> bool:
        return self.services.destination_still_in_queued_target_area(battle, actor, destination, queued)

    def simulated_reaction(self, battle: Battle, actor: Unit, queued: QueuedAction,
                           payload: dict, skill: Any, reservation_cost: float) -> float:
        return self.services.simulated_reaction_score(battle, actor, queued, payload, skill, reservation_cost)

    def paid_reaction(self, battle: Battle, actor: Unit, queued: QueuedAction,
                      payload: dict, code: str, multi_target: bool) -> float:
        return self.services.paid_reaction_score(battle, actor, queued, payload,
                                   actor.get_skill(code), multi_target)

    def counter_reaction(self, battle: Battle, actor: Unit, queued: QueuedAction,
                         with_followup: bool) -> float:
        source = battle.get_unit(queued.actor_id)
        attack_value = self.services.r22_attack_value if with_followup else self.services.r21_attack_value
        return attack_value(battle, actor, source, {"reaction_attack": True}) - self.services.stone_attack_spawn_penalty(
            battle, actor, {"type": "attack", "unit_id": actor.unit_id,
                            "target_unit_id": source.unit_id})

    def barrier_prevention(self, battle: Battle, actor: Unit, queued: QueuedAction,
                           payload: dict, code: str) -> float:
        return self.services.barrier_prevention_score(battle, actor, queued, payload, code)

    def queued_skill_damaging(self, queued: QueuedAction) -> bool:
        return str(queued.payload.get("skill_code") or "") in DAMAGING_SKILL_CODES

    def shield_protected_ids(self, battle: Battle) -> set[str]:
        return self.services.queued_shield_protected_unit_ids(battle)

    def unit_danger(self, battle: Battle, unit: Unit) -> float:
        return self.services.natsume_unit_danger(battle, unit)

    def outcome_snapshot(self, battle: Battle) -> dict[str, tuple]:
        return self.services.red_outcome_snapshot(battle)

    def outcome_value(self, actor: Unit, before: dict[str, tuple]) -> float:
        return self.services.red_outcome_value(actor, before)

    def public_score(self, battle: Battle, actor: Unit, queued: QueuedAction,
                     option: dict, payload: dict, profile: DifficultyProfile) -> float:
        return self.services.score_reaction_payload(battle, actor, queued, option, payload, profile)

    def proxy_target(self, battle: Battle, actor: Unit, queued: QueuedAction) -> Unit:
        return battle.reaction_proxy_target(actor, queued) or actor

    def hostile_units(self, battle: Battle, actor: Unit) -> Iterable[Unit]:
        return living_hostile_combatants(battle, actor.player_id)

    def copied_skill_action(self, battle: Battle, actor: Unit, skill: Any) -> dict:
        return self.services.copied_skill_action(battle, actor, skill)

    def attack_window_value(self, battle: Battle, actor: Unit,
                            profile: DifficultyProfile) -> float:
        return self.services.r19_attack_window_value(battle, actor, profile)

    def temporary_defense_prevention(self, battle: Battle, actor: Unit,
                                     queued: QueuedAction) -> float:
        return self.services.temporary_defense_prevention_score(battle, actor, queued)

    def retaliation_penalty(self, battle: Battle, actor: Unit,
                            targets: Iterable[Unit]) -> float:
        return self.services.cat_retaliation_action_penalty(battle, actor, targets)

    def counter_spawn_penalty(self, battle: Battle, actor: Unit,
                              target: Unit) -> float:
        return self.services.stone_attack_spawn_penalty(
            battle, actor, {"type": "attack", "unit_id": actor.unit_id,
                            "target_unit_id": target.unit_id})

    def hostile_value(self, target: Unit) -> float:
        return self.services.hostile_unit_value(target)

    def damage_mana_reserve(self, actor: Unit) -> float:
        return self.services.active_damage_mana_reserve(actor)


class CandidateRoomServices(Protocol):
    """Queries supplied by the room to candidate policy adapters."""

    MOVE_SKILL_CODES: set[str]
    _SHARED_CANDIDATE_GEOMETRY: Any
    dedupe_damage_area_payloads: Callable[..., Any]
    dedupe_area_payloads_by_affected_units: Callable[..., Any]
    preview_positions: Callable[..., Any]
    estimate_damage: Callable[..., Any]
    score_skill_payload: Callable[..., Any]
    skill_payload_requires_enemy_impact: Callable[..., Any]
    skill_payload_has_effective_enemy_impact: Callable[..., Any]
    trim_skill_payloads_for_ai: Callable[..., Any]
    dedupe_payloads: Callable[..., Any]
    distance_to_position: Callable[..., Any]
    payload_is_legal: Callable[..., Any]
    positions_to_payload: Callable[..., Any]


class _CandidatePlanServices:
    """Generic room queries consumed by immutable skill candidate plans."""

    def __init__(self, services: CandidateRoomServices) -> None:
        self.services = services

    def dedupe_damage(self, battle: Battle, payloads: list[dict]) -> list[dict]:
        return self.services.dedupe_damage_area_payloads(battle, payloads)

    def dedupe_units(self, battle: Battle, payloads: list[dict]) -> list[dict]:
        return self.services.dedupe_area_payloads_by_affected_units(
            battle, payloads, clones_and_summons_only=False)

    def positions(self, raw_cells: Any) -> list[Position]:
        return self.services.preview_positions(raw_cells)

    def estimate_damage(self, battle: Battle, target: Unit, power: float,
                        *, half_ignore_shield: bool = False) -> float:
        return self.services.estimate_damage(
            battle, target, power, half_ignore_shield=half_ignore_shield)

    def score(self, battle: Battle, actor: Unit, action: dict, payload: dict,
              profile: DifficultyProfile, *, instant_only: bool) -> float:
        return self.services.score_skill_payload(
            battle, actor, action, payload, profile, instant_only=instant_only)

    def requires_enemy_impact(self, battle: Battle, actor: Unit, action: dict,
                              payload: dict) -> bool:
        return self.services.skill_payload_requires_enemy_impact(
            battle, actor, action, payload)

    def effective_enemy_impact(self, battle: Battle, actor: Unit, action: dict,
                               payload: dict) -> bool:
        return self.services.skill_payload_has_effective_enemy_impact(
            battle, actor, action, payload)

    def trim(self, battle: Battle, actor: Unit, payloads: list[dict],
             *, limit: int) -> list[dict]:
        return self.services.trim_skill_payloads_for_ai(
            battle, actor, payloads, limit=limit)


class _SharedCandidateGeometry:
    """Preview geometry consumed by hero-owned candidate policies."""

    def __init__(self, services: CandidateRoomServices) -> None:
        self.services = services

    def preview_positions(self, raw_cells: Any) -> list[Position]:
        return self.services.preview_positions(raw_cells)

    def positions_to_payload(self, cells: Iterable[Position]) -> list[dict[str, int]]:
        return self.services.positions_to_payload(cells)


class _SkillPayloadServices:
    """Room operations consumed by the shared skill payload builder."""

    def __init__(self, services: CandidateRoomServices) -> None:
        self.services = services
        self.MOVE_SKILL_CODES = services.MOVE_SKILL_CODES
        self._SHARED_CANDIDATE_GEOMETRY = services._SHARED_CANDIDATE_GEOMETRY

    def dedupe_payloads(self, payloads: list[dict]) -> list[dict]:
        return self.services.dedupe_payloads(payloads)

    def distance_to_position(self, battle: Battle, unit: Unit,
                             destination: Position) -> int:
        return self.services.distance_to_position(battle, unit, destination)

    def payload_is_legal(self, battle: Battle, payload: dict) -> bool:
        return self.services.payload_is_legal(battle, payload)

    def positions_to_payload(self, cells: Iterable[Position]) -> list[dict[str, int]]:
        return self.services.positions_to_payload(cells)

    def preview_positions(self, raw_cells: Any) -> list[Position]:
        return self.services.preview_positions(raw_cells)
