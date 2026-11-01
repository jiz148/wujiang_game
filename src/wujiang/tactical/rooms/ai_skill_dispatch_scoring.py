"""AISkillScoreCoordinator: shared AI scoring and queries for skill_dispatch."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from typing import Any, Callable, Iterable, Optional
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants
from wujiang.tactical.prediction import (expected_numeric, expected_local, forecasting_active, direct_scoring, rules_enabled,
    prediction_session, record_candidates, note, expected_attack_damage,
    attack_damage_distribution, sum_distributions, prediction_rollback, ForecastRandom, RULES_VERSION,
    expected_vector, joint_utilities_enabled, disable_joint_utilities, stable_observations, UnstableObservation)
from wujiang.tactical.heroes.mechanics.true_red import true_red_blast_score
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
from wujiang.tactical.rooms.ai_mobility_effect_scoring import (
    payload_step_direction, score_damage_cells, zero_dash_score,
    zero_path_effect_score, blind_paid_move_score, fuma_pursuit_score, fuma_trap_score,
    reviewed_r17_effect_score, friendly_mirror_score, reviewed_r18_effect_score,
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
from wujiang.tactical.rooms.ai_self_generic_scoring import (
    self_buff_score, best_available_attack_score, field_skill_score, square_cells_around_position,
    judgment_stone_collision_move_score, judgment_stone_score, world_seed_score, feiwang_attack_value,
    feiwang_attack_plan_value, feiwang_stance_score, feiwang_shensu_score, kings_insight_score,
    gale_score, great_fire_funeral_score, great_fire_funeral_alignment_score_at, generic_skill_score,
    summon_position_score, flame_chimera_position_score, flame_password_score, wyvern_move_score,
    wyvern_resummon_score, nova_quantum_separation_score, nova_electromagnetic_weather_score, hell_dog_summon_score,
    stone_summon_score, stone_attack_spawn_penalty,
)
from wujiang.tactical.rooms.ai_impact_scoring import (
    score_respawn_destination, attack_payload_has_effective_enemy_impact, attack_payload_satisfies_required_target, attack_payload_enemy_targets,
    attack_target_has_effective_impact, hostile_skill_effect_target_has_impact, hostile_skill_extra_effect_applies,
    payload_is_legal, reaction_payload_is_legal, skill_effect_units, skill_effect_cells,
    skill_attack_power,
)
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

class AISkillScoreCoordinatorServices(Protocol):
    """The exact room services used by this scoring family."""
    _score_skill_payload: Callable[..., Any]
    _skill_score_context: Callable[..., Any]
    agency_borrowed_payload_context: Any
    build_zero_crossing_move_candidates: Callable[..., Any]
    copied_skill_action: Any
    mimic_payload_context: Any
    reviewed_r16_damage_result: Callable[..., Any]
    score_skill_payload: Callable[..., Any]
    skill_from_ai_action: Any
    skill_payloads_for_action: Any


class AISkillScoreCoordinator:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AISkillScoreCoordinatorServices) -> None:
        self.services = services

    def _score_skill_payload(self,
        battle: Battle,
        actor: Unit,
        action: dict[str, Any],
        payload: dict[str, Any],
        profile: DifficultyProfile,
        *,
        instant_only: bool,
    ) -> float:
        code = str(action.get("code") or payload.get("skill_code") or "")
        if actor.hero_code == "excel_r373" and code.startswith("infinity_instant__"):
            from wujiang.tactical.heroes.electronic_infinity import InfinityInstantSkill
            converted = actor.get_skill(code)
            if not isinstance(converted, InfinityInstantSkill):
                return -1000.0
            if converted.source.code not in {"machine_gun", "missile"}:
                return -1000.0  # No value from an off-turn movement buff or an empty temporary shield.
            source_payload = {**payload, "skill_code": converted.source.code}
            previous_mana = actor.current_mana
            try:
                # The paid conversion changes the attack before the same skill resolves.
                object.__setattr__(actor, "current_mana", max(0.0, previous_mana - converted.mana_cost_for_payload(battle, actor, payload)))
                score = self.services._score_skill_payload(
                    battle, actor,
                    {**action, "code": converted.source.code, "_skill_object": converted.source},
                    source_payload, profile, instant_only=False,
                )
            finally:
                object.__setattr__(actor, "current_mana", previous_mana)
            return score + (14.0 if previous_mana > 0 and actor.stat("defense") <= 4 else 4.0) - 26.0
        if code.startswith("ship_borrow__"):
            from wujiang.tactical.heroes.flying_dragon_ship import ShipBorrowedSkill
            borrowed = actor.get_skill(code)
            if not isinstance(borrowed, ShipBorrowedSkill):
                return -1000.0
            return self.services._score_skill_payload(battle, actor,
                                        {**action, "code": borrowed.inner.code, "_skill_object": borrowed.inner},
                                        payload, profile, instant_only=instant_only)
        priority_policy = getattr(actor, "ai_policy", None)
        if priority_policy is not None:
            priority_score = priority_policy.priority_skill_score(battle, actor, code, payload)
            if priority_score is not None:
                return priority_score
        try:
            priority_skill = self.services.skill_from_ai_action(actor, action, code)
        except (ActionError, KeyError, ValueError):
            priority_skill = None
        priority_scorer = getattr(priority_skill, "ai_priority_score", None)
        if priority_scorer is not None:
            priority_value = priority_scorer(self.services._skill_score_context(battle, actor, action, payload, profile))
            if priority_value is not None:
                return priority_value
        if priority_policy is not None:
            if code in priority_policy.contextual_skill_codes:
                contextual_score = priority_policy.contextual_skill_score(
                    self.services._skill_score_context(battle, actor, action, payload, profile))
                if contextual_score is not None:
                    return contextual_score
        if actor.hero_code == "excel_r056" and code == "pierce":
            return r13_effect_score(battle, actor, self.services.skill_from_ai_action(actor, action, code), payload, profile)
        if actor.hero_code == "excel_r379" and code == "pierce":
            return r23_paid_skill_value(battle, actor, payload)
        if code == "heal" and actor.hero_code == "excel_r337":
            return r22_heal_score(battle, actor, self.services.skill_from_ai_action(actor, action, code), payload)
        if actor.hero_code in {"excel_r264", "excel_r326"} and code in {"pierce", "remote_pierce"}:
            return reviewed_r18_effect_score(battle, actor, self.services.skill_from_ai_action(actor, action, code), payload, profile)
        if code == "black_cat_paw":
            skill = self.services.skill_from_ai_action(actor, action, code)
            targets = skill_effect_units(battle, actor, skill, payload)
            if not any(unit.player_id != actor.player_id for unit in targets):
                return -1000.0
            with ai_probe_rollback(battle):
                before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields()) for unit in targets}
                before_mana = actor.current_mana
                skill.execute(battle, actor, payload)
                score = (actor.current_mana - before_mana) * 18.0
                for unit in targets:
                    hp, mana, shields = before[unit.unit_id]
                    value = max(0.0, hp - unit.current_hp) * 160.0 + max(0.0, mana - unit.current_mana) * 24.0
                    value += max(0, shields - unit.total_shields()) * 20.0
                    if scored_death_probability(unit) > 0:
                        value += (80.0 * scored_death_probability(unit) if direct_scoring() else 80.0)
                    score += value if unit.player_id != actor.player_id else -value * 1.3
            return score if score > 0 else -1000.0
        if code == "heaven_punishment":
            skill = self.services.skill_from_ai_action(actor, action, code)
            targets = skill_effect_units(battle, actor, skill, payload)
            selected_code = str(payload.get("disabled_skill_code") or "")
            target = battle.units.get(str(payload.get("target_unit_id") or ""))
            if target is None or not selected_code:
                return -1000.0
            control_value = skill_control_bonus(battle, actor, code, payload, targets, profile, instant_only=instant_only)
            with ai_probe_rollback(battle):
                before = {unit.unit_id: (unit.current_hp, unit.total_shields()) for unit in targets}
                sealed_before = any(getattr(status, "skill_code", None) == selected_code for status in target.statuses)
                try:
                    skill.execute(battle, actor, payload)
                except ActionError:
                    return -1000.0
                score = 0.0
                for unit in targets:
                    hp, shields = before[unit.unit_id]
                    value = max(0.0, hp - unit.current_hp) * 155.0 + max(0, shields - unit.total_shields()) * 20.0
                    if scored_death_probability(unit) > 0:
                        value += (80.0 * scored_death_probability(unit) if direct_scoring() else 80.0)
                    score += value if unit.player_id != actor.player_id else -value * 1.35
                sealed_after = any(getattr(status, "skill_code", None) == selected_code for status in target.statuses)
                if sealed_after and not sealed_before:
                    score += max(0.0, control_value)
            return score if score > 0 else -1000.0
        if code == "equip_mage_cloak":
            target = battle.units.get(str(payload.get("target_unit_id") or ""))
            if target is None or target.player_id != actor.player_id or target.position is None:
                return -1000.0
            nearby_enemy = any(battle.distance_between_units(target, enemy) <= target.normal_move_distance() + target.targeting_range()
                               for enemy in living_hostile_combatants(battle, actor.player_id))
            return 26.0 + (24.0 if nearby_enemy else 0.0) + (15.0 if target.stat("speed") <= 3 else 0.0)
        if code == "detach_mage_cloak":
            status = actor.get_status("法师斗篷")
            cloak = battle.units.get(getattr(status, "cloak_unit_id", "")) if status is not None else None
            destination = payload_destination(payload)
            if cloak is None or destination is None:
                return -1000.0
            recover = max(0.0, cloak.max_health - cloak.current_hp) * 70.0
            reach = sum(18.0 for enemy in living_hostile_combatants(battle, actor.player_id)
                        if max(abs(destination.x - enemy.position.x), abs(destination.y - enemy.position.y)) <= cloak.targeting_range())
            return recover + reach + summon_position_score(battle, actor, destination) * 0.25 - 48.0
        if actor.hero_code == "excel_r030" and code == "pierce":
            return fusion_pierce_score(battle, actor, payload)
        if code == "mimic_skill":
            context = self.services.mimic_payload_context(battle, actor, payload)
            if context is None:
                return -1000.0
            _, copied, copied_payload, copied_action = context
            mimic = actor.get_skill("mimic_skill")
            with mimic.copying(actor, copied, reserve_point=True):
                value = self.services.score_skill_payload(battle, actor, copied_action, copied_payload, profile, instant_only=instant_only)
                mana = copied.mana_cost_for_payload(battle, actor, copied_payload)
            return value - (18.0 if actor.mana_points > 1 else 30.0) - mana * 8.0
        if code == "agency_borrowed_skill":
            context = self.services.agency_borrowed_payload_context(battle, actor, payload)
            if context is None:
                return -10.0
            _, copied, copied_payload, copied_action = context
            if copied.code in SELF_BUFF_SKILL_CODES:
                return -1000.0
            with actor.get_skill("agency_borrowed_skill").copying(actor, copied):
                if not copied.can_use(battle, actor, copied_payload)[0]:
                    return -1000.0
                return self.services.score_skill_payload(battle, actor, copied_action, copied_payload, profile, instant_only=instant_only)
        if actor.hero_code == "excel_r138" and code == "pierce":
            return reviewed_r18_effect_score(battle, actor, self.services.skill_from_ai_action(actor, action, code), payload, profile)
        skill = self.services.skill_from_ai_action(actor, action, code)
        if getattr(skill, "ai_early_effect_score", False):
            return skill_owned_effect_score(battle, actor, skill, payload, profile)
        targets = skill_effect_units(battle, actor, skill, payload)
        role = hero_style(actor)
        if code == "curse":
            eligible = [unit for unit in targets if unit.player_id != actor.player_id and not unit.has_status("诅咒")]
            if actor.current_hp <= 0.5 or not eligible:
                return -1000.0
            # A curse is delayed attrition, not an immediate attack using Ellie's attack stat.
            return sum(unit.current_hp * 80.0 for unit in eligible) - 60.0 - (35.0 if actor.current_hp <= 0.75 else 0.0)
        if code == "mana_pull":
            target = primary_target_unit(battle, payload, targets)
            if target is None or target.position is None or payload.get("dest_x") is None or payload.get("dest_y") is None:
                return -1000.0
            destination = Position(int(payload["dest_x"]), int(payload["dest_y"]))
            style = hero_style(target)
            change = score_move_destination(battle, target, destination, style, profile) - score_move_destination(battle, target, target.position, style, profile)
            return change - 12.0 if target.player_id == actor.player_id else -change + (20.0 if not target.cannot_normal_move else 0.0) - 12.0
        if code == "jirobo_follow_step":
            destination = payload_destination(payload)
            if destination is None or actor.position is None:
                return -1000.0
            gain = score_move_destination(battle, actor, destination, role, profile) - score_move_destination(
                battle, actor, actor.position, role, profile,
            )
            return gain + 8.0 if gain > 3.0 else -8.0
        if code in MOVE_SKILL_CODES:
            destination = payload_destination(payload)
            if destination is None:
                return -5.0
            origin_score = score_move_destination(battle, actor, actor.position, role, profile) if actor.position is not None else 0.0
            score = score_move_destination(battle, actor, destination, role, profile) - origin_score - 1.0
            if actor.hero_code == "excel_r302":
                score += true_red_blast_score(battle, actor, destination)
                if code.startswith("true_red_free_"):
                    score += 3.0
            if code == "hell_teleport":
                enemies = living_hostile_combatants(battle, actor.player_id)
                @expected_local(battle)
                def danger_at(cell: Position) -> float:
                    return sum(max(0.0, enemy.stat("attack") - actor.stat("defense") + 1.0) * 15.0
                               for enemy in enemies if enemy.position is not None
                               and distance_to_position(battle, enemy, cell)
                               <= enemy.normal_move_distance() + enemy.targeting_range())
                if actor.position is None:
                    return -1000.0
                avoided = danger_at(actor.position) - danger_at(destination)
                return score + avoided * (1.7 if actor.current_hp <= 0.5 else 0.9) - 42.0
            if code == "fate_kick" and actor.position is not None:
                dx, dy = destination.x - actor.position.x, destination.y - actor.position.y
                if dx or dy:
                    impact = destination.offset(0 if dx == 0 else (1 if dx > 0 else -1), 0 if dy == 0 else (1 if dy > 0 else -1))
                    target = battle.unit_at(impact) if battle.in_bounds(impact) else None
                    if target is not None and target.player_id != actor.player_id:
                        score += 0.5 * (hostile_unit_value(target) - ally_unit_value(actor))
                        score -= max(0, actor.attack_actions_per_turn() - actor.attacks_used) * 22.0
            if code == "zero_dash":
                score += zero_dash_score(battle, actor, skill, payload)
            if code == "fuma_pursuit":
                score += fuma_pursuit_score(battle, actor, skill, payload, profile)
            if code == "crazy_sand":
                score += skill_damage_score(battle, actor, skill, payload, profile) - 15.0
            if code == "mounted_leap" and actor.position is not None:
                before = masamune_attack_plan_value(battle, actor, profile)
                with ai_probe_rollback(battle):
                    battle.clear_mounted_state(actor)
                    actor.position = destination
                    followup = next((candidate for candidate in actor.skills if candidate.code == "six_blade_style"), None)
                    if followup is not None and followup.can_use(battle, actor, {})[0]:
                        followup.execute(battle, actor, {})
                    after = masamune_attack_plan_value(battle, actor, profile)
                score += min(100.0, after - before) - 40.0
            return score
        if code in SUMMON_SKILL_CODES:
            destination = payload_destination(payload)
            if code == "wyvern":
                return wyvern_resummon_score(battle, actor, profile)
            if code == "quantum_separation":
                return nova_quantum_separation_score(battle, actor, destination, profile)
            if code == "hell_dog":
                if destination is None:
                    return -1000.0
                return hell_dog_summon_score(battle, actor, destination)
            if code == "summon_remi_bat":
                if destination is None:
                    return -1000.0
                reachable = [enemy for enemy in living_hostile_combatants(battle, actor.player_id)
                             if enemy.position is not None and max(abs(enemy.position.x - destination.x),
                                                                   abs(enemy.position.y - destination.y)) <= 4]
                cover = any(enemy.position is not None and battle.distance_between_units(actor, enemy) <= 3
                            for enemy in living_hostile_combatants(battle, actor.player_id))
                return 30.0 + summon_position_score(battle, actor, destination) + len(reachable) * 25.0 + (12.0 if cover else 0.0) if reachable or cover else -8.0
            if code == "split" and actor.hero_code == "n":
                if best_available_attack_score(battle, actor, profile) > 0:
                    return -1000.0
                nearby = any(distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range() for enemy in living_hostile_combatants(battle, actor.player_id))
                return 22.0 if nearby and actor.current_mana >= 2.5 else -20.0
            if code == "split" and actor.hero_code == "excel_r118":
                if best_available_attack_score(battle, actor, profile) > profile.action_threshold:
                    return -1000.0
                dash = actor.get_skill("zero_dash")
                if dash.can_use(battle, actor, {})[0]:
                    dash_action = self.services.copied_skill_action(battle, actor, dash)
                    for dash_payload in self.services.skill_payloads_for_action(battle, actor, dash_action):
                        if zero_dash_score(battle, actor, dash, dash_payload) > profile.action_threshold:
                            return -1000.0
                if actor.position is not None and actor.remaining_normal_move_distance(battle) > 0:
                    if any(candidate.score > profile.action_threshold
                           for candidate in self.services.build_zero_crossing_move_candidates(battle, actor, hero_style(actor), profile)):
                        return -1000.0
            if code == "split" and actor.hero_code == "excel_r136":
                if best_available_attack_score(battle, actor, profile) > profile.action_threshold:
                    return -1000.0
                for other in actor.skills:
                    if other.code not in {"true_blade_air_slash", "pierce", "oboro_meditate"} or not other.can_use(battle, actor, {})[0]:
                        continue
                    other_action = self.services.copied_skill_action(battle, actor, other)
                    for other_payload in self.services.skill_payloads_for_action(battle, actor, other_action):
                        if self.services.score_skill_payload(battle, actor, other_action, other_payload, profile, instant_only=False) > profile.action_threshold:
                            return -1000.0
            if code == "split" and actor.hero_code == "excel_r113":
                if best_available_attack_score(battle, actor, profile) > 0:
                    return -1000.0
                for other in actor.skills:
                    if other.code not in {"purify_mana", "sacred_duel", "dragon_breath", "drain_mana"}:
                        continue
                    if not other.can_use(battle, actor, {})[0]:
                        continue
                    other_action = self.services.copied_skill_action(battle, actor, other)
                    for other_payload in self.services.skill_payloads_for_action(battle, actor, other_action):
                        if self.services.score_skill_payload(battle, actor, other_action, other_payload, profile, instant_only=False) > profile.action_threshold:
                            return -1000.0
            if code == "earth_walker":
                if actor.has_status("土行者") or not living_hostile_combatants(battle, actor.player_id):
                    return -1000.0
                if best_available_attack_score(battle, actor, profile) > 0:
                    return -1000.0
                nearby = any(distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range() for enemy in living_hostile_combatants(battle, actor.player_id))
                return 24.0 + max(0.0, actor.max_health - actor.current_hp) * 45.0 if nearby else -8.0
            if code in {"summon_bicycle", "summon_unicycle"}:
                return cycle_summon_score(battle, actor, code, destination, profile)
            score = 42.0
            if destination is not None:
                score += summon_position_score(battle, actor, destination)
            if code in {"earth_walker", "split"}:
                score += 10.0
            score += follow_catch_up_skill_penalty(battle, actor)
            return score
        if code in HEAL_SKILL_CODES:
            healed = primary_target_unit(battle, payload, targets)
            if code == "heal_mount":
                healed = battle.mounted_unit_for(actor)
            if healed is None:
                healed = actor
            if code in {"heal", "add_health", "heal_mount", "mech_enhancement"}:
                with ai_probe_rollback(battle):
                    before_hp = healed.current_hp
                    before_defense = healed.stat("defense")
                    skill.execute(battle, actor, payload)
                    gain = healed.current_hp - before_hp
                    defense_gain = healed.stat("defense") - before_defense
                return gain * 200.0 + max(0.0, defense_gain) * 24.0 + (50.0 if gain > 0 and healed.current_hp <= 0.5 else 0.0) if gain > 0 or defense_gain > 0 else -1000.0
            missing = max(0.0, healed.max_health - healed.current_hp)
            score = missing * 120.0
            if code == "mech_enhancement":
                score += 24.0
            return score
        if code == "recover_mana":
            target = primary_target_unit(battle, payload, targets)
            if target is None or target.player_id != actor.player_id:
                return -1000.0
            cost = skill.mana_cost_for_payload(battle, actor, payload)
            available = target.current_mana - (cost if target.unit_id == actor.unit_id else 0.0)
            gained = min(1.0, max(0.0, target.max_mana() - available))
            if gained <= 0 or (target.unit_id == actor.unit_id and gained <= cost):
                return -1000.0
            return gained * 48.0 - cost * 18.0
        if code in ALLY_BUFF_SKILL_CODES:
            target = primary_target_unit(battle, payload, targets)
            if target is None:
                return -2.0
            score = ally_buff_score(battle, actor, code, target, profile)
            return score
        if code in SELF_BUFF_SKILL_CODES:
            return self_buff_score(battle, actor, code, profile) + follow_catch_up_skill_penalty(battle, actor)
        if code == "demon_link":
            target = battle.units.get(str(payload.get("target_unit_id") or ""))
            if target is None or target.player_id == actor.player_id or target.magic_immunity or target.dodge_charges > 0:
                return -1000.0
            if target.skill_non_damage_effects_blocked():
                return -1000.0
            if any(status.name == "恶魔链接·来源" and getattr(status, "target_unit_id", None) == target.unit_id
                   and getattr(status, "ending_turn_number", None) is None for status in actor.statuses):
                return -1000.0
            distance = battle.distance_between_units(actor, target)
            duration = max(0, 4 - distance)
            return (22.0 + min(3.0, target.stat("attack")) * 8.0
                    + min(3.0, target.stat("speed")) * 8.0
                    + duration * 4.0 + (12.0 if not target.is_summon and not target.is_clone else 0.0)
                    - (18.0 if target.stat("attack") <= 1 or target.stat("speed") <= 1 else 0.0))
        if code in DAMAGING_SKILL_CODES or code in CONTROL_SKILL_CODES:
            score = skill_damage_score(battle, actor, skill, payload, profile)
            score += skill_control_bonus(battle, actor, code, payload, targets, profile, instant_only=instant_only)
            if skill.max_uses_per_battle is not None and skill.max_uses_per_battle <= 1 and score < profile.once_per_battle_threshold:
                score -= 32.0
            return score
        return generic_skill_score(battle, actor, code, targets, profile)
