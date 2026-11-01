from __future__ import annotations

import random
import sys
from copy import deepcopy
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from itertools import combinations
from typing import Any, Callable, Iterable, Optional

from wujiang.tactical.rooms.ai_acceleration import cached_effect_baseline, cached_move_batch, cached_move_score, cached_position_value, cached_tactical_query, decision_memo_scope, prepare_position_values, tactical_queries_active
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants
from wujiang.tactical.rooms.ai_skill_payloads import SkillPayloadBuilder
from wujiang.tactical.rooms.ai_decisions import AIDecisionCoordinator
from wujiang.tactical.rooms.ai_move_candidates import MoveCandidateBuilder
from wujiang.tactical.rooms.ai_attack_candidates import AttackCandidateBuilder
from wujiang.tactical.rooms.ai_skill_candidates import SkillCandidateBuilder, _AreaRecipientIndex
from wujiang.tactical.rooms.ai_reaction_candidates import ReactionCandidateBuilder
from wujiang.tactical.rooms.ai_formation_scoring import AIFormationScoring
from wujiang.tactical.rooms.ai_attack_scoring import AIAttackScoring
from wujiang.tactical.rooms.ai_skill_entry_scoring import AISkillEntryScoring
from wujiang.tactical.rooms.ai_reaction_scoring import AIReactionScoring
from wujiang.tactical.rooms.ai_effect_early_scoring import AIEarlyEffectScoring
from wujiang.tactical.rooms.ai_effect_late_scoring import AILateEffectScoring
from wujiang.tactical.rooms.ai_opportunity_scoring import AIOpportunityScoring
from wujiang.tactical.rooms.ai_mobility_effect_scoring import AIMobilityEffectScoring
from wujiang.tactical.rooms.ai_support_scoring import AISupportScoring
from wujiang.tactical.rooms.ai_self_generic_scoring import AISelfAndGenericScoring
from wujiang.tactical.rooms.ai_impact_scoring import AIImpactQueries
from wujiang.tactical.rooms.ai_skill_dispatch_scoring import AISkillScoreCoordinator
from wujiang.tactical.rooms.ai_weather_scoring import AIWeatherAndThreatScoring
from wujiang.tactical.rooms.ai_late_scoring import AILateTacticalScoring
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
from wujiang.platform.id_sequence import isolated_ids
from wujiang.tactical.heroes.mechanics.undead_king_lina import lina_has_attack_followup
from wujiang.tactical.heroes.mechanics.true_red import true_red_blast_score
from wujiang.tactical.heroes.mechanics.electric_wind import electric_auto_position_value, electric_control_opportunities
from wujiang.tactical.heroes.mechanics.holy_sola import sola_harvest_position_value
from wujiang.tactical.heroes.ai_policy import (AIReactionCandidateContext,
    AIReactionScoreContext, AISkillCandidateContext, AISkillScoreContext,
    BarrierShieldAIPolicy, FreeReactionScoring, ShieldReactionScoring)
from wujiang.tactical.heroes.ai_candidate_plan import DEFAULT_CANDIDATES
from wujiang.tactical.heroes.storm_spirit import storm_spirit_chain_score
from wujiang.tactical.heroes.storm_spirit import (
    storm_spirit_dash_context_score, storm_spirit_afterimage_context_score,
)
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


@contextmanager
def ai_enemy_view(battle: Battle, player_id: int) -> Iterable[None]:
    if rules_enabled():
        if (getattr(battle, "_ai_view_player_id", None) == player_id and forecasting_active()
                and getattr(battle, "_ai_scoring_rules", None) == RULES_VERSION):
            yield
            return
        with prediction_session(player_id), isolated_ids(), ai_probe_rollback(battle, view=True):
            battle._ai_view_player_id = player_id
            battle._ai_scoring_rules = RULES_VERSION
            hidden = {unit.unit_id for unit in battle.all_units()
                      if unit.player_id != player_id and unit.is_stealthed()}
            battle._ai_has_unseen_enemies = bool(hidden)
            sources = dict(battle.units)
            if "chess_rng" in battle.__dict__:
                battle.chess_rng = ForecastRandom()
            battle.units = {unit_id: unit for unit_id, unit in battle.units.items() if unit_id not in hidden}
            battle.destroyed_units = [unit for unit in battle.destroyed_units
                                      if unit.player_id == player_id or not unit.is_stealthed()]
            note("hidden_enemies_omitted", len(hidden))
            for clone in list(battle.all_units()):
                if not clone.is_clone or clone.player_id == player_id:
                    continue
                source = sources.get(battle.controlling_hero_id(clone))
                perceived = deepcopy(source if source is not None else clone)
                for attribute in ("unit_id", "player_id", "alive", "banished", "position", "footprint_offsets",
                                  "mount_owner_id", "mounted_on_unit_id", "ridden_by_unit_id"):
                    setattr(perceived, attribute, deepcopy(getattr(clone, attribute)))
                perceived.is_clone = False
                if source is None:
                    perceived.is_summon = False
                    perceived.summoner_id = None
                    perceived.controller_hero_id = None
                # Visibility is that of this visible body, even if the source
                # used to construct its public disguise is currently hidden.
                perceived.statuses = [status for status in perceived.statuses if status.name != "隐身"]
                perceived._battle_ref = getattr(clone, "_battle_ref", None)
                battle.units[clone.unit_id] = perceived
                note("enemy_clones_disguised")
            yield
        return
    with _legacy_ai_enemy_view(battle, player_id):
        yield


@contextmanager
def _legacy_ai_enemy_view(battle: Battle, player_id: int) -> Iterable[None]:
    """Use the same clone disguise as the opponent UI while evaluating choices."""
    if getattr(battle, "_ai_view_player_id", None) == player_id:
        yield
        return
    clones = [unit for unit in battle.all_units() if unit.is_clone and unit.player_id != player_id]
    if not clones:
        yield
        return
    with ai_probe_rollback(battle):
        battle._ai_view_player_id = player_id
        for clone in clones:
            source = battle.units.get(battle.controlling_hero_id(clone))
            perceived = deepcopy(source if source is not None else clone)
            for attribute in ("unit_id", "player_id", "alive", "banished", "position", "footprint_offsets",
                              "mount_owner_id", "mounted_on_unit_id", "ridden_by_unit_id"):
                setattr(perceived, attribute, deepcopy(getattr(clone, attribute)))
            perceived.is_clone = False
            perceived._battle_ref = getattr(clone, "_battle_ref", None)
            battle.units[clone.unit_id] = perceived
        yield


def with_enemy_view(participant_name: str) -> Callable:
    def decorate(function: Callable) -> Callable:
        @wraps(function)
        def wrapped(battle: Battle, *args: Any, **kwargs: Any) -> Any:
            with decision_memo_scope():
                return in_view(battle, *args, **kwargs)

        def in_view(battle: Battle, *args: Any, **kwargs: Any) -> Any:
            participant = args[0] if args else kwargs[participant_name]
            if isinstance(participant, Unit):
                units = [participant]
            else:
                units = list(participant)
                if args:
                    args = (units, *args[1:])
                else:
                    kwargs[participant_name] = units
            players = {unit.player_id for unit in units}
            if len(players) != 1:
                if rules_enabled() and len(players) > 1:
                    raise ValueError("A full-AI choice requires one player's information view")
                return function(battle, *args, **kwargs)
            with ai_enemy_view(battle, next(iter(players))):
                return function(battle, *args, **kwargs)
        return wrapped
    return decorate


@with_enemy_view("actor")
def choose_turn_action(battle: Battle, actor: Unit, difficulty: str) -> dict[str, Any]:
    return _AI_DECISION_COORDINATOR.choose_turn_action(battle, actor, difficulty)


@with_enemy_view("units")
def choose_turn_bundle_action(battle: Battle, units: Iterable[Unit], difficulty: str) -> tuple[dict[str, Any], Optional[Unit]]:
    return _AI_DECISION_COORDINATOR.choose_turn_bundle_action(battle, units, difficulty)


def turn_action_candidates(
    battle: Battle,
    actor: Unit,
    profile: DifficultyProfile,
) -> tuple[list[AICandidate], list[AICandidate]]:
    return _AI_DECISION_COORDINATOR.turn_action_candidates(battle, actor, profile)


@with_enemy_view("units")
def choose_instant_action(battle: Battle, units: Iterable[Unit], difficulty: str) -> Optional[dict[str, Any]]:
    return _AI_DECISION_COORDINATOR.choose_instant_action(battle, units, difficulty)


@with_enemy_view("reactor")
def choose_chain_reaction(
    battle: Battle,
    reactor: Unit,
    options: list[dict[str, Any]],
    difficulty: str,
) -> Optional[dict[str, Any]]:
    return _AI_DECISION_COORDINATOR.choose_chain_reaction(battle, reactor, options, difficulty)


@with_enemy_view("unit")
def choose_respawn_action(battle: Battle, unit: Unit, options: list[Position], difficulty: str) -> Optional[dict[str, Any]]:
    return _AI_DECISION_COORDINATOR.choose_respawn_action(battle, unit, options, difficulty)


def choose_damage_choice_action(battle: Battle) -> dict[str, Any]:
    return _AI_DECISION_COORDINATOR.choose_damage_choice_action(battle)


def choose_hell_sacrifice_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    return _AI_DECISION_COORDINATOR.choose_hell_sacrifice_action(battle, prompt, unit)


def choose_hell_dragon_sacrifice_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    return _AI_DECISION_COORDINATOR.choose_hell_dragon_sacrifice_action(battle, prompt, unit)


@with_enemy_view("unit")
def choose_hell_dragon_return_action(battle: Battle, unit: Unit) -> Optional[dict[str, Any]]:
    return _AI_DECISION_COORDINATOR.choose_hell_dragon_return_action(battle, unit)


def choose_hell_rescue_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    return _AI_DECISION_COORDINATOR.choose_hell_rescue_action(battle, prompt, unit)


def choose_hell_death_strike_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    return _AI_DECISION_COORDINATOR.choose_hell_death_strike_action(battle, prompt, unit)


def choose_rotation_damage_choice_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    return _AI_DECISION_COORDINATOR.choose_rotation_damage_choice_action(battle, prompt, unit)


def choose_uesugi_end_dash_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    return _AI_DECISION_COORDINATOR.choose_uesugi_end_dash_action(battle, prompt, unit)


def choose_electronic_teleport_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    return _AI_DECISION_COORDINATOR.choose_electronic_teleport_action(battle, prompt, unit)


FAST_AI_MOVE_LIMIT = 12


def fast_move_shortlist(battle: Battle, actor: Unit, cells: list[Position], role: str) -> list[Position]:
    return _AI_MOVECANDIDATEBUILDER.fast_move_shortlist(battle, actor, cells, role)


def build_move_candidates(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    profile: DifficultyProfile,
) -> list[AICandidate]:
    return _AI_MOVECANDIDATEBUILDER.build_move_candidates(battle, actor, action, profile)


def direct_adjacent_path(start: Position, destination: Position) -> list[Position]:
    return _AI_MOVECANDIDATEBUILDER.direct_adjacent_path(start, destination)


def build_zero_crossing_move_candidates(
    battle: Battle,
    actor: Unit,
    role: str,
    profile: DifficultyProfile,
) -> list[AICandidate]:
    return _AI_MOVECANDIDATEBUILDER.build_zero_crossing_move_candidates(battle, actor, role, profile)


@with_enemy_view("actor")
def build_attack_candidates(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    profile: DifficultyProfile,
) -> list[AICandidate]:
    return _AI_ATTACKCANDIDATEBUILDER.build_attack_candidates(battle, actor, action, profile)


@expected_numeric(independent=True)
def ancient_turret_prefilter_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _AI_ATTACKCANDIDATEBUILDER.ancient_turret_prefilter_score(battle, actor, payload)


from wujiang.tactical.rooms.ai_room_services import (
    _CandidatePlanServices, _SharedCandidateGeometry, _SkillPayloadServices,
)


_CANDIDATE_PLAN_SERVICES = _CandidatePlanServices(sys.modules[__name__])


@with_enemy_view("actor")
def build_skill_candidates(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    profile: DifficultyProfile,
    *,
    instant_only: bool,
) -> list[AICandidate]:
    return _AI_SKILLCANDIDATEBUILDER.build_skill_candidates(battle, actor, action, profile, instant_only=instant_only)


def dedupe_area_payloads_by_affected_units(
    battle: Battle,
    payloads: list[dict[str, Any]],
    *,
    clones_and_summons_only: bool,
) -> list[dict[str, Any]]:
    return _AI_SKILLCANDIDATEBUILDER.dedupe_area_payloads_by_affected_units(battle, payloads, clones_and_summons_only=clones_and_summons_only)


def dedupe_damage_area_payloads(battle: Battle, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _AI_SKILLCANDIDATEBUILDER.dedupe_damage_area_payloads(battle, payloads)


@expected_numeric(independent=True, boolean=True)
def should_throttle_unlimited_nonhostile_skill(battle: Battle, actor: Unit, action: dict[str, Any], payload: dict[str, Any]) -> bool:
    return _AI_SKILLCANDIDATEBUILDER.should_throttle_unlimited_nonhostile_skill(battle, actor, action, payload)


def trim_skill_payloads_for_ai(
    battle: Battle,
    actor: Unit,
    payloads: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    return _AI_SKILLCANDIDATEBUILDER.trim_skill_payloads_for_ai(battle, actor, payloads, limit=limit)


@with_enemy_view("reactor")
def build_reaction_candidates(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    option: dict[str, Any],
    profile: DifficultyProfile,
) -> list[AICandidate]:
    return _AI_REACTIONCANDIDATEBUILDER.build_reaction_candidates(battle, reactor, queued_action, option, profile)


def attack_payloads_for_action(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    return _AI_REACTIONCANDIDATEBUILDER.attack_payloads_for_action(battle, actor, action)


def attack_payloads_for_cells(
    battle: Battle,
    actor: Unit,
    base_payload: dict[str, Any],
    cells: list[Position],
    *,
    choice_code: Optional[str] = None,
) -> list[dict[str, Any]]:
    return _AI_REACTIONCANDIDATEBUILDER.attack_payloads_for_cells(battle, actor, base_payload, cells, choice_code=choice_code)


def reaction_payloads_for_option(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    option: dict[str, Any],
) -> list[dict[str, Any]]:
    return _AI_REACTIONCANDIDATEBUILDER.reaction_payloads_for_option(battle, reactor, queued_action, option)


def backstep_payloads(base_payload: dict[str, Any], preview: dict[str, Any]) -> list[dict[str, Any]]:
    return _AI_REACTIONCANDIDATEBUILDER.backstep_payloads(base_payload, preview)


def shield_targets_for_reaction(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    preview: dict[str, Any],
    *,
    action_code: str,
) -> list[str]:
    return _AI_REACTIONCANDIDATEBUILDER.shield_targets_for_reaction(battle, reactor, queued_action, preview, action_code=action_code)


def queued_shield_protected_unit_ids(battle: Battle) -> set[str]:
    return _AI_REACTIONCANDIDATEBUILDER.queued_shield_protected_unit_ids(battle)


def active_damage_mana_reserve(actor: Unit) -> float:
    return _AI_REACTIONCANDIDATEBUILDER.active_damage_mana_reserve(actor)


def hero_ai_style_for(battle: Battle, actor: Unit) -> str:
    styles = getattr(battle, "hero_ai_styles", None) or {}
    raw = styles.get(int(actor.player_id)) or styles.get(str(actor.player_id)) or "rush"
    return "follow" if str(raw) == "follow" else "rush"


FOLLOW_COMFORT_RADIUS = 5
FOLLOW_FRONT_PACK_SLACK = 4
FOLLOW_LEAD_BY_ORDER = {
    "hold": 1,
    "advance": 3,
    "seek": 3,
    "retreat": 2,
}
FOLLOW_ANCHOR_EXCLUDED_KINDS = frozenset({"cannon", "arrow_tower"})


from wujiang.tactical.rooms.ai_formation_scoring import (
    _chebyshev_cells, _axis_heading, _clamp_follow_cell, _follow_heading_for_army,
    _is_follow_anchor_unit, _follow_front_pack, clear_follow_anchor_cache, follow_anchor_state,
    army_anchor_cell, follow_catch_up_skill_penalty, follow_leash_adjustment, follow_formation_adjustment,
    score_move_destination,
)


from wujiang.tactical.rooms.ai_attack_scoring import (
    required_attack_target, attack_effect_units, has_cat_retaliation, messenger_crow_position_score,
    scored_death_probability, scored_kill_probability, score_attack_payload,
)


def legal_attack_payload(battle: Battle, actor: Unit, target: Unit) -> Optional[dict[str, Any]]:
    return _AI_ATTACK_SCORING.legal_attack_payload(battle, actor, target)


@expected_numeric(independent=True)
def cat_retaliation_action_penalty(battle: Battle, actor: Unit, targets: Iterable[Unit]) -> float:
    """Price the mana loss, forced pull, and post-action lock caused by targeting Cat Uncle."""
    return _AI_ATTACK_SCORING.cat_retaliation_action_penalty(battle, actor, targets)


from wujiang.tactical.rooms.ai_skill_entry_scoring import (
    alexander_expected_swap_value, alexander_magic_coverage_value, hell_shuffle_score,
)


@expected_numeric(independent=True)
def score_skill_payload(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
    *,
    instant_only: bool,
) -> float:
    score = _score_skill_payload(battle, actor, action, payload, profile, instant_only=instant_only)
    if score > -1000.0:
        adjustment = nova_paid_action_adjustment(
            battle, actor, str(action.get("code") or payload.get("skill_code") or ""), payload)
        if adjustment <= -1000.0:
            return -1000.0
        score += adjustment
    attack_policy = getattr(actor, "ai_policy", None)
    if attack_policy is not None and score > 0:
        score += attack_policy.skill_score_bonus(battle, actor, action, payload, score)
    return score


@expected_numeric(independent=True)
def nova_paid_action_adjustment(battle: Battle, actor: Unit, code: str, payload: dict[str, Any]) -> float:
    """Account for the payment event before the skill's ordinary effect score."""
    return _AI_SKILL_ENTRY_SCORING.nova_paid_action_adjustment(battle, actor, code, payload)


def storm_spirit_dash_score(battle: Battle, actor: Unit, payload: dict[str, Any],
                            profile: DifficultyProfile) -> float:
    return storm_spirit_dash_context_score(_skill_score_context(
        battle, actor, {"code": "electronic_storm"}, payload, profile))


def storm_spirit_afterimage_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return storm_spirit_afterimage_context_score(_skill_score_context(
        battle, actor, {"code": "afterimage"}, payload, difficulty_profile("standard")))


from wujiang.tactical.rooms.ai_room_services import _SharedSkillScoring


_SHARED_SKILL_SCORING = _SharedSkillScoring(sys.modules[__name__])


_SHARED_CANDIDATE_GEOMETRY = _SharedCandidateGeometry(sys.modules[__name__])


_SKILL_PAYLOAD_BUILDER = SkillPayloadBuilder(_SkillPayloadServices(sys.modules[__name__]))
skill_payloads_for_action = _SKILL_PAYLOAD_BUILDER.skill_payloads_for_action
heaven_punishment_payloads = _SKILL_PAYLOAD_BUILDER.heaven_punishment_payloads
skill_from_ai_action = _SKILL_PAYLOAD_BUILDER.skill_from_ai_action
copied_skill_action = _SKILL_PAYLOAD_BUILDER.copied_skill_action
mimic_skill_payloads = _SKILL_PAYLOAD_BUILDER.mimic_skill_payloads
mimic_payload_context = _SKILL_PAYLOAD_BUILDER.mimic_payload_context
royal_soldier_payloads = _SKILL_PAYLOAD_BUILDER.royal_soldier_payloads
agency_contract_payloads = _SKILL_PAYLOAD_BUILDER.agency_contract_payloads
agency_borrowed_skill_payloads = _SKILL_PAYLOAD_BUILDER.agency_borrowed_skill_payloads
agency_borrowed_payload_context = _SKILL_PAYLOAD_BUILDER.agency_borrowed_payload_context
direction_payload_for_cell_skill = _SKILL_PAYLOAD_BUILDER.direction_payload_for_cell_skill
split_payloads = _SKILL_PAYLOAD_BUILDER.split_payloads
rock_absorb_payloads = _SKILL_PAYLOAD_BUILDER.rock_absorb_payloads
rock_cannon_payloads = _SKILL_PAYLOAD_BUILDER.rock_cannon_payloads


from wujiang.tactical.rooms.ai_room_services import _SharedReactionScoring


_SHARED_REACTION_SCORING = _SharedReactionScoring(sys.modules[__name__])
_FREE_REACTION_SCORING = FreeReactionScoring()
_SHIELD_REACTION_SCORING = ShieldReactionScoring()


def _skill_score_context(battle: Battle, actor: Unit, action: dict[str, Any],
                         payload: dict[str, Any], profile: DifficultyProfile) -> AISkillScoreContext:
    return AISkillScoreContext(battle, actor, action, payload, profile, _SHARED_SKILL_SCORING)


@expected_numeric(independent=True)
def _score_skill_payload(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
    *,
    instant_only: bool,
) -> float:
    return _AI_SKILL_DISPATCH_SCORING._score_skill_payload(battle, actor, action, payload, profile, instant_only=instant_only)


from wujiang.tactical.rooms.ai_reaction_scoring import (
    score_reaction_payload, _score_reaction_payload, temporary_defense_prevention_score, destination_still_in_queued_target_area,
    taunt_control_score, cycle_summon_score, sphinx_cannon_score, best_future_artillery_area_value,
    ai_terrain_unit, area_escape_reaction_available, lethal_counter_reaction_available, solar_judgment_score,
    iron_chain_path_score, reviewed_attack_synergy, sandstorm_value,
)


from wujiang.tactical.rooms.ai_effect_early_scoring import (
    resource_effect_score, blood_support_score, li_attack_plan_value, li_area_skill_score,
    chanter_card_score, perfect_swordsman_skill_score, skill_owned_effect_score, reviewed_r06_skill_score,
    magnetic_wave_score, masamune_attack_plan_value, allied_attack_heal_score, fried_aura_position_value,
    fried_skill_score, frey_skill_score, morning_holy_light_score, skill_damage_score,
    skill_control_bonus, drain_mana_score, r12_future_banish_value, r12_realized_effect_value,
    r12_effect_score, r12_costly_window, r12_preparation_score, r13_recovery_value,
    r13_attack_value, r13_effect_score, r13_bat_score,
)


from wujiang.tactical.rooms.ai_effect_late_scoring import (
    r14_snow_avalanche_score, r14_incoming_skill_loss, r14_cat_form_score, r14_cat_attack_and_retreat_value,
    kaiser_fist_score, r15_control_value, reviewed_r15_effect_score, reviewed_r19_effect_score,
    r19_attack_window_value, r19_incoming_position_risk, r19_preparation_score,
)


from wujiang.tactical.rooms.ai_opportunity_scoring import (
    beetle_armor_reaction_score, r21_unit_action_opportunities, r21_control_score, r21_winged_contact_value,
    r21_winged_leap_score, r21_barrier_position_value, r21_queued_loss, _r21_queued_loss_expectations,
    _r21_queued_loss_vector, r21_barrier_shield_score, barrier_prevention_score, purify_mana_score,
    sacred_duel_score,
)


@expected_numeric(independent=True)
def r21_attack_value(battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> float:
    return _AI_OPPORTUNITY_SCORING.r21_attack_value(battle, actor, target, payload)


@cached_tactical_query
def _ai_reachable_coordinates(battle: Battle, unit: Unit, **options):
    return _AI_OPPORTUNITY_SCORING._ai_reachable_coordinates(battle, unit, **options)


def ai_reachable_positions(battle: Battle, unit: Unit, **options) -> list[Position]:
    return _AI_OPPORTUNITY_SCORING.ai_reachable_positions(battle, unit, **options)


@cached_tactical_query
@expected_numeric(independent=True)
def r21_unit_attack_opportunity(battle: Battle, unit: Unit) -> float:
    """The original ordered attack reduction, inside the caller's rollback."""
    return _AI_OPPORTUNITY_SCORING.r21_unit_attack_opportunity(battle, unit)


@expected_numeric(independent=True)
def reviewed_r16_damage_result(battle: Battle, actor: Unit, operation: Any, *, try_passage_evasion: bool = False) -> float:
    """Evaluate the complete sequence, including defenses consumed and deaths."""
    with ai_probe_rollback(battle):
        before = [(unit, unit.current_hp, unit.total_shields(), unit.dodge_charges, unit.alive)
                  for unit in battle.all_units()]
        mana_before = actor.current_mana
        try:
            operation()
            evasion_used = False
            while battle.pending_chain is not None:
                window = battle.pending_chain
                if try_passage_evasion and not evasion_used and window.queued_action.payload.get("pass_through_damage"):
                    future_cells = {cell for queued in battle.pending_followup_actions for cell in queued.target_cells}
                    hazard_cells = future_cells | set(window.queued_action.target_cells)
                    for unit_id in window.pending_reactor_ids:
                        if not any(option.action_code == "evasion" for option in window.options_by_unit.get(unit_id, [])):
                            continue
                        target = battle.get_unit(unit_id)
                        skill = target.skill_map().get("evasion")
                        if skill is None:
                            continue
                        exits = [cell for cell in skill.evade_cells(battle, target)
                                 if not hazard_cells.intersection(target.footprint_cells_at(cell))]
                        if not exits:
                            continue
                        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != unit_id:
                            battle.perform_action({"type": "chain_skip"})
                        if battle.pending_chain is not None:
                            exit_cell = exits[0]
                            battle.perform_action({"type": "chain_react", "unit_id": unit_id,
                                                   "action_code": "evasion", "x": exit_cell.x, "y": exit_cell.y})
                            evasion_used = True
                            break
                    if evasion_used:
                        continue
                battle.finalize_reaction_window()
        except ActionError:
            return -1000.0
        score = (actor.current_mana - mana_before) * 24.0
        for unit, hp, shields, dodges, alive in before:
            value = max(0.0, hp - unit.current_hp) * 100.0
            value += max(0, shields - unit.total_shields()) * 20.0 + max(0, dodges - unit.dodge_charges) * 12.0
            if alive and scored_death_probability(unit) > 0:
                value += (90.0 * scored_death_probability(unit) if direct_scoring() else 90.0)
            score += value if unit.player_id != actor.player_id else -value * 1.3
        return score


from wujiang.tactical.rooms.ai_mobility_effect_scoring import (
    payload_step_direction, score_damage_cells, zero_dash_score,
    zero_path_effect_score, blind_paid_move_score, fuma_pursuit_score, fuma_trap_score,
    reviewed_r17_effect_score, friendly_mirror_score, reviewed_r18_effect_score,
)


@cached_position_value
@expected_numeric(independent=True)
def reviewed_r17_position_value(battle: Battle, actor: Unit, unit: Unit) -> float:
    return _AI_MOBILITY_EFFECT_SCORING.reviewed_r17_position_value(battle, actor, unit)


@cached_effect_baseline
def reviewed_effect_baseline(battle: Battle, actor: Unit, units: list[Unit], *, include_pending: bool = False) -> dict:
    # Preserve the original field reads and position queries, in their exact
    # per-unit order. The decorator skips only a previously pure whole batch.
    return _AI_MOBILITY_EFFECT_SCORING.reviewed_effect_baseline(battle, actor, units, include_pending=include_pending)


# Freeze the functions actually loaded by this module. An isolated process
# cannot reproduce runtime replacements merely by matching source-file hashes.
_PARALLEL_BASELINE_BINDINGS = tuple(
    (query, query.__code__, query.__wrapped__, query.__wrapped__.__code__)
    for query in (reviewed_effect_baseline, reviewed_r17_position_value)
)


from wujiang.tactical.rooms.ai_support_scoring import (
    messenger_refill_opportunity, messenger_preparation_score, ally_buff_score, guardian_finale_opportunities,
    guardian_finale_score, agency_contract_score, natsume_unit_danger, natsume_support_attack_score,
    natsume_skill_score, natsume_wall_score, natsume_position_plan_value, natsume_shensu_score,
    fusion_position_risk, fusion_attack_value, fusion_attack_plan_value, fusion_preparation_score,
    fusion_pierce_score, red_outcome_snapshot, red_outcome_value, red_attack_value,
    red_attack_plan_value, weapon_copy_score, deadly_bow_score, red_preparation_score,
    _red_exposure_distribution, _red_expected_defense_value, wuchang_exposed_action_value, wuchang_mist_score,
    migratory_bird_mark_score, wuchang_split_score,
)


@expected_numeric
def wuchang_action_success_probability(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _AI_SUPPORT_SCORING.wuchang_action_success_probability(battle, actor, payload)


from wujiang.tactical.rooms.ai_self_generic_scoring import (
    self_buff_score, best_available_attack_score, field_skill_score, square_cells_around_position,
    judgment_stone_collision_move_score, judgment_stone_score, world_seed_score, feiwang_attack_value,
    feiwang_attack_plan_value, feiwang_stance_score, feiwang_shensu_score, kings_insight_score,
    gale_score, great_fire_funeral_score, great_fire_funeral_alignment_score_at, generic_skill_score,
    summon_position_score, flame_chimera_position_score, flame_password_score, wyvern_move_score,
    wyvern_resummon_score, nova_quantum_separation_score, nova_electromagnetic_weather_score, hell_dog_summon_score,
    stone_summon_score, stone_attack_spawn_penalty,
)


@expected_numeric(independent=True, boolean=True)
def skill_payload_has_effective_enemy_impact(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    return _AI_IMPACT_SCORING.skill_payload_has_effective_enemy_impact(battle, actor, action, payload)


from wujiang.tactical.rooms.ai_impact_scoring import (
    score_respawn_destination, attack_payload_has_effective_enemy_impact, attack_payload_satisfies_required_target, attack_payload_enemy_targets,
    attack_target_has_effective_impact, hostile_skill_effect_target_has_impact, hostile_skill_extra_effect_applies,
    payload_is_legal, reaction_payload_is_legal, skill_effect_units, skill_effect_cells,
    skill_attack_power,
)


@expected_numeric(independent=True, boolean=True)
def skill_payload_requires_enemy_impact(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    return _AI_IMPACT_SCORING.skill_payload_requires_enemy_impact(battle, actor, action, payload)


from wujiang.tactical.rooms.ai_weather_scoring import (
    estimate_damage, r20_line_window_value, r20_pattern_attack_window_value, r20_weather_combat_value,
    r20_piercing_skill_window_value, r20_weather_cast_score, incoming_threat_score, best_move_destination_score,
    offensive_reach_score_at, primary_target_unit, hero_style, has_mana_point_skill,
    hostile_unit_value, ally_unit_value, friendly_fire_penalty, distance_between_units,
    distance_to_position, preview_positions, positions_to_payload, choose_declared_target_cell,
    payload_destination, best_candidate, best_unit_candidate, dedupe_payloads,
    sorted_payload,
)


from wujiang.tactical.rooms.ai_late_scoring import (
    r23_action_state, r23_action_delta, r23_attack_value, electronic_repair_score,
    r23_paid_skill_value, _r23_expected_settled_value, r215219_skill_score, r23_passive_defense_value,
    r23_sun_slash_score, r23_attack_plan_value, r22_attack_value, r22_contact_value,
    r22_gazer_window_risk, r22_ninja_shensu_score, r22_heal_score, paid_reaction_score,
    r22_paid_reaction_score, simulated_reaction_score, andrew_reaction_score,
)


# Only shipped, unchanged query implementations can produce reusable values.
_TACTICAL_QUERY_ROOTS = (_ai_reachable_coordinates, r21_unit_attack_opportunity)
_TACTICAL_GLOBAL_BINDINGS = tuple(
    (name, globals()[name], globals()[name].__code__, getattr(globals()[name], "__wrapped__", None),
     getattr(getattr(globals()[name], "__wrapped__", None), "__code__", None))
    for name in ("r21_attack_value", "ai_reachable_positions", "_ai_reachable_coordinates", "r21_unit_attack_opportunity")
)
_TACTICAL_QUERY_BINDINGS = tuple(
    (owner, name, getattr(owner, name), getattr(owner, name).__code__)
    for owner, names in (
        (Battle, ("reachable_positions", "neighbors", "line_positions", "terrain_step_allowed",
                  "can_traverse_position", "can_place_unit", "normal_movement_step_cost",
                  "attack_target_allowed", "resolve_attack_damage", "effect_units", "enemy_units", "all_units")),
        (Unit, ("__getstate__", "__setattr__", "iter_components", "stat")),
    ) for name in names
)


# Initialize after all scoring and candidate services have been defined.
_AI_DECISION_COORDINATOR = AIDecisionCoordinator(sys.modules[__name__])
_AI_MOVECANDIDATEBUILDER = MoveCandidateBuilder(sys.modules[__name__])
_AI_ATTACKCANDIDATEBUILDER = AttackCandidateBuilder(sys.modules[__name__])
_AI_SKILLCANDIDATEBUILDER = SkillCandidateBuilder(sys.modules[__name__])
_AI_REACTIONCANDIDATEBUILDER = ReactionCandidateBuilder(sys.modules[__name__])
_AI_FORMATION_SCORING = AIFormationScoring(sys.modules[__name__])
_AI_ATTACK_SCORING = AIAttackScoring(sys.modules[__name__])
_AI_SKILL_ENTRY_SCORING = AISkillEntryScoring(sys.modules[__name__])
_AI_REACTION_SCORING = AIReactionScoring(sys.modules[__name__])
_AI_EFFECT_EARLY_SCORING = AIEarlyEffectScoring(sys.modules[__name__])
_AI_EFFECT_LATE_SCORING = AILateEffectScoring(sys.modules[__name__])
_AI_OPPORTUNITY_SCORING = AIOpportunityScoring(sys.modules[__name__])
_AI_MOBILITY_EFFECT_SCORING = AIMobilityEffectScoring(sys.modules[__name__])
_AI_SUPPORT_SCORING = AISupportScoring(sys.modules[__name__])
_AI_SELF_GENERIC_SCORING = AISelfAndGenericScoring(sys.modules[__name__])
_AI_IMPACT_SCORING = AIImpactQueries(sys.modules[__name__])
_AI_SKILL_DISPATCH_SCORING = AISkillScoreCoordinator(sys.modules[__name__])
_AI_WEATHER_SCORING = AIWeatherAndThreatScoring(sys.modules[__name__])
_AI_LATE_SCORING = AILateTacticalScoring(sys.modules[__name__])
sys.modules[AIFormationScoring.__module__].bind_room(_AI_FORMATION_SCORING)
sys.modules[AIAttackScoring.__module__].bind_room(_AI_ATTACK_SCORING)
sys.modules[AISkillEntryScoring.__module__].bind_room(_AI_SKILL_ENTRY_SCORING)
sys.modules[AIReactionScoring.__module__].bind_room(_AI_REACTION_SCORING)
sys.modules[AIEarlyEffectScoring.__module__].bind_room(_AI_EFFECT_EARLY_SCORING)
sys.modules[AILateEffectScoring.__module__].bind_room(_AI_EFFECT_LATE_SCORING)
sys.modules[AIOpportunityScoring.__module__].bind_room(_AI_OPPORTUNITY_SCORING)
sys.modules[AIMobilityEffectScoring.__module__].bind_room(_AI_MOBILITY_EFFECT_SCORING)
sys.modules[AISupportScoring.__module__].bind_room(_AI_SUPPORT_SCORING)
sys.modules[AISelfAndGenericScoring.__module__].bind_room(_AI_SELF_GENERIC_SCORING)
sys.modules[AIImpactQueries.__module__].bind_room(_AI_IMPACT_SCORING)
sys.modules[AIWeatherAndThreatScoring.__module__].bind_room(_AI_WEATHER_SCORING)
sys.modules[AILateTacticalScoring.__module__].bind_room(_AI_LATE_SCORING)
