"""AISupportScoring: shared AI scoring and queries for support."""

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

class AISupportScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    ALLY_BUFF_SKILL_CODES: Any
    CONTROL_SKILL_CODES: Any
    DAMAGING_SKILL_CODES: Any
    DifficultyProfile: Any
    HEAL_SKILL_CODES: Any
    SELF_BUFF_SKILL_CODES: Any
    SUMMON_SKILL_CODES: Any
    _SHARED_REACTION_SCORING: Any
    _red_expected_defense_value: Callable[..., Any]
    _red_exposure_distribution: Callable[..., Any]
    _skill_score_context: Callable[..., Any]
    agency_borrowed_payload_context: Any
    ai_reachable_positions: Callable[..., Any]
    ally_unit_value: Callable[..., Any]
    attack_payloads_for_action: Callable[..., Any]
    copied_skill_action: Any
    deadly_bow_score: Callable[..., Any]
    difficulty_profile: Callable[..., Any]
    distance_between_units: Callable[..., Any]
    estimate_damage: Callable[..., Any]
    fusion_attack_plan_value: Callable[..., Any]
    fusion_attack_value: Callable[..., Any]
    fusion_position_risk: Callable[..., Any]
    guardian_finale_opportunities: Callable[..., Any]
    has_mana_point_skill: Callable[..., Any]
    messenger_refill_opportunity: Callable[..., Any]
    mimic_payload_context: Any
    natsume_position_plan_value: Callable[..., Any]
    natsume_skill_score: Callable[..., Any]
    natsume_support_attack_score: Callable[..., Any]
    natsume_unit_danger: Callable[..., Any]
    payload_is_legal: Callable[..., Any]
    positions_to_payload: Callable[..., Any]
    preview_positions: Callable[..., Any]
    r19_incoming_position_risk: Callable[..., Any]
    red_attack_plan_value: Callable[..., Any]
    red_attack_value: Callable[..., Any]
    red_outcome_snapshot: Callable[..., Any]
    red_outcome_value: Callable[..., Any]
    reviewed_r18_effect_score: Callable[..., Any]
    score_attack_payload: Callable[..., Any]
    score_skill_payload: Callable[..., Any]
    skill_effect_units: Callable[..., Any]
    skill_from_ai_action: Any
    skill_payload_has_effective_enemy_impact: Callable[..., Any]
    skill_payloads_for_action: Any


class AISupportScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AISupportScoringServices) -> None:
        self.services = services

    def messenger_refill_opportunity(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        if actor.direct_effects_blocked():
            return 0.0
        with ai_probe_rollback(battle):
            actor.current_mana = actor.max_mana()
            value = 0.0
            for code in ("pierce", "dragon_breath"):
                skill = actor.get_skill(code)
                if not skill.can_use(battle, actor, {})[0]:
                    continue
                action = self.services.copied_skill_action(battle, actor, skill)
                for payload in self.services.skill_payloads_for_action(battle, actor, action):
                    value = max(value, self.services.reviewed_r18_effect_score(battle, actor, skill, payload, profile))
            protection = actor.get_skill("protection")
            if not actor.cannot_use_skills and protection.cooldown_remaining <= 0:
                target = battle.effect_recipient(actor)
                risk = self.services.r19_incoming_position_risk(battle, actor)
                if risk > 0 and not target.direct_effects_blocked():
                    with ai_probe_rollback(battle):
                        target.shields += protection.shield_amount
                        prevented = risk - self.services.r19_incoming_position_risk(battle, actor)
                    if prevented > 0:
                        value = max(value, min(24.0, prevented))
            return value


    def messenger_preparation_score(self, battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
        opportunity = self.services.messenger_refill_opportunity(battle, actor, profile)
        if opportunity <= 0:
            return -10.0
        if code == "messenger_creation":
            return (35.0 if actor.current_mana <= 1e-9 and actor.mana_points >= 1 else 16.0) if actor.mana_points < 2 else -10.0
        if actor.current_mana > 1e-9 or actor.mana_points < 2:
            return -10.0
        return min(180.0, opportunity) + min(actor.max_mana(), 5.0) * 24.0


    def ally_buff_score(self, battle: Battle, actor: Unit, code: str, target: Unit, profile: DifficultyProfile) -> float:
        target_value = self.services.ally_unit_value(target)
        missing_hp = max(0.0, target.max_health - target.current_hp)
        if code == "experiment":
            if target.has_status("实验倒计时") or target.position is None:
                return -1000.0
            enemies = living_hostile_combatants(battle, actor.player_id)
            reachable = [unit for unit in enemies if self.services.distance_between_units(battle, target, unit) <= target.normal_move_distance() + target.targeting_range() + 4]
            decisive = False
            if len(enemies) == 1 and battle.unit_belongs_to_current_turn(target) and not target.cannot_attack and target.attacks_used < target.attack_actions_per_turn():
                enemy = enemies[0]
                if self.services.distance_between_units(battle, target, enemy) <= target.targeting_range() + 2:
                    attack = {"type": "attack", "unit_id": target.unit_id, "target_unit_id": enemy.unit_id}
                    ordinary = estimate_attack_damage(battle, target, enemy, attack, attack_power=target.stat("attack"))
                    boosted = estimate_attack_damage(battle, target, enemy, attack, attack_power=target.stat("attack") + 2)
                    decisive = ordinary < enemy.current_hp <= boosted
            if not reachable or (not target.is_summon and target.current_hp > 0.5 and not decisive):
                return -1000.0
            # A healthy core hero's forced death is not a free positive buff.
            return 45.0 + missing_hp * 55.0 + min(3, len(reachable)) * 16.0 + (180.0 if decisive else 0.0) - (0.0 if target.is_summon else target_value * 0.25)
        if code == "defend_twice":
            if any(status.name == "守*2" and getattr(status, "source_unit_id", None) == actor.unit_id for status in target.statuses):
                return -1000.0
            return target_value * 0.4 + 18.0
        if code == "baptism":
            if target.magic_immunity or target.race != "人类":
                return -1000.0
            return target_value * 0.35 + 10.0
        if code == "chant":
            reserve = max(4.0, max((float(getattr(skill, "mana_point_cost", 0) or 0) for skill in target.skills), default=0.0))
            if target.player_id == actor.player_id and self.services.has_mana_point_skill(target) and target.mana_points < reserve:
                return 55.0 + target_value * 0.25
            return -1000.0
        return 12.0


    def guardian_finale_opportunities(self, battle: Battle, actor: Unit) -> tuple[float, float, float, bool]:
        """Price reachable attacks and useful remaining breath casts without declaring actions."""
        enemies = living_hostile_combatants(battle, actor.player_id)
        if actor.position is None or not enemies:
            return 0.0, 0.0, 0.0, False
        attack_value = recovery = breath_value = 0.0
        can_finish = False
        origin = actor.position
        destinations = [origin]
        if not actor.cannot_move and not actor.cannot_normal_move and actor.remaining_normal_move_distance(battle) > 0:
            destinations.extend(self.services.ai_reachable_positions(battle, actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True))
        leap = next((skill for skill in actor.skills if skill.code == "fly_leap"), None)
        if leap is not None and leap.can_use(battle, actor, {})[0]:
            destinations.extend(self.services.preview_positions(leap.preview(battle, actor).get("cells")))
        try:
            if actor.attacks_used < actor.attack_actions_per_turn() and not actor.cannot_attack:
                # Stat/defense values can depend on position, so evaluate each legal endpoint.
                for destination in dict.fromkeys(destinations):
                    actor.position = destination
                    for enemy in enemies:
                        if not battle.attack_target_allowed(actor, enemy)[0]:
                            continue
                        payload = {"type": "attack", "unit_id": actor.unit_id, "target_unit_id": enemy.unit_id}
                        damage = estimate_attack_damage(battle, actor, enemy, payload, attack_power=battle.basic_attack_preview_power(actor, payload))
                        if damage <= 0:
                            continue
                        value = min(enemy.current_hp, damage) * 100.0 + (90.0 if damage >= enemy.current_hp else 0.0)
                        attack_value = max(attack_value, value)
                        if actor.has_status("终结") and not actor.cannot_heal and not enemy.is_clone:
                            recovery = max(recovery, min(0.25, max(0.0, actor.max_health - actor.current_hp)))
                        can_finish |= len(enemies) == 1 and damage >= enemy.current_hp
            actor.position = origin
            breath = next((skill for skill in actor.skills if skill.code == "dragon_breath"), None)
            if breath is not None and breath.can_use(battle, actor, {})[0]:
                # Area recipients, friendly fire and effective damage all use battle rules.
                for cells in breath.patterns(battle, actor):
                    payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": breath.code, "cells": [cell.to_dict() for cell in cells]}
                    value = 0.0
                    killed = set()
                    for target in battle.effect_units_at_cells(cells, ignore=actor):
                        damage = estimate_skill_damage(battle, actor, breath, payload, target, actor.stat("attack"), cells=cells,
                                                       ignore_shield=breath.ignores_shield_for_payload(battle, actor, payload),
                                                       half_ignore_shield=breath.half_ignores_shield_for_payload(battle, actor, payload))
                        loss = min(target.current_hp, damage)
                        if target.player_id == actor.player_id:
                            value -= loss * 150.0 + (110.0 if loss >= target.current_hp else 0.0)
                        else:
                            value += loss * 100.0 + (90.0 if loss >= target.current_hp else 0.0)
                            if loss >= target.current_hp:
                                killed.add(target.unit_id)
                    breath_value = max(breath_value, value)
                    can_finish |= bool(enemies) and all(enemy.unit_id in killed for enemy in enemies)
        finally:
            actor.position = origin
        return attack_value, recovery, breath_value, can_finish


    def guardian_finale_score(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        if actor.has_status("终结") or actor.direct_effects_blocked():
            return -1000.0
        with ai_probe_rollback(battle):
            before_attack, _, before_breath, _ = self.services.guardian_finale_opportunities(battle, actor)
            actor.get_skill("guardian_finale").execute(battle, actor, {})
            if not actor.has_status("终结"):
                return -1000.0
            after_attack, recovery, after_breath, can_finish = self.services.guardian_finale_opportunities(battle, actor)
            if max(after_attack, after_breath) <= 0:
                return -1000.0
            if actor.current_hp + recovery <= 0.25 and not can_finish:
                return -1000.0
            # The ongoing attack window sustains the irreversible upkeep; skill damage cannot heal.
            score = max(0.0, after_attack - before_attack) + max(0.0, after_breath - before_breath) * 0.6
            score += after_attack * 0.55 + recovery * 120.0 - 35.0
            if after_attack <= 0:
                score -= 35.0
            if actor.current_hp <= 0.5 and recovery <= 0 and not can_finish:
                score -= 45.0
            if can_finish:
                score += 50.0
            return score if score >= profile.once_per_battle_threshold else -10.0


    def agency_contract_score(self, battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        skill = actor.get_skill("agency_contract")
        enemies = living_hostile_combatants(battle, actor.player_id)
        if actor.has_status("代行契约附着"):
            before_hp = {unit.unit_id: unit.current_hp for unit in enemies}
            before_mana = actor.current_mana
            with ai_probe_rollback(battle):
                declared = dict(payload, **skill.queued_payload_metadata(battle, actor, payload))
                skill.execute(battle, actor, declared)
                loss = sum(max(0, before_hp[unit.unit_id] - unit.current_hp) for unit in enemies)
                kills = sum(before_hp[unit.unit_id] > 0 and not unit.alive for unit in enemies)
                gain = max(0.0, actor.current_mana - before_mana)
                risk = sum(self.services.estimate_damage(battle, actor, enemy.stat("attack")) for enemy in enemies
                           if enemy.alive and actor.position is not None and self.services.distance_between_units(battle, actor, enemy) <= enemy.targeting_range() + enemy.normal_move_distance())
                return loss * 100.0 + kills * 90.0 + gain * 22.0 - risk * 70.0 - 30.0
        try:
            target, stat, copied = skill.validate_binding(battle, actor, payload)
        except (ActionError, KeyError, ValueError, TypeError):
            return -1000.0
        before_stat = actor.stat(stat)
        current_risk = sum(self.services.estimate_damage(battle, actor, enemy.stat("attack")) for enemy in enemies
                           if self.services.distance_between_units(battle, actor, enemy) <= enemy.targeting_range() + enemy.normal_move_distance())
        with ai_probe_rollback(battle):
            skill.execute(battle, actor, dict(payload, agency_cancel=False))
            wrapper = actor.get_skill("agency_borrowed_skill")
            best_copy = 0.0
            if not wrapper.movement_blocked(copied) and copied.code not in self.services.SELF_BUFF_SKILL_CODES:
                with wrapper.copying(actor, copied):
                    action = self.services.copied_skill_action(battle, actor, copied)
                    for inner in self.services.skill_payloads_for_action(battle, actor, action):
                        if copied.can_use(battle, actor, inner)[0] and self.services.payload_is_legal(battle, inner):
                            best_copy = max(best_copy, self.services.score_skill_payload(battle, actor, action, inner, profile, instant_only=False))
            attacks = [self.services.score_attack_payload(battle, actor, {"type": "attack", "unit_id": actor.unit_id, "target_unit_id": enemy.unit_id}, profile)
                       for enemy in enemies if battle.attack_target_allowed(actor, enemy)[0]]
            offense = max([best_copy, *attacks, 0.0])
            stat_gain = actor.stat(stat) - before_stat
            if offense <= 0 and current_risk <= 0:
                return -1000.0
            return offense * 0.65 + current_risk * 75.0 + stat_gain * 5.0 - 18.0


    def natsume_unit_danger(self, battle: Battle, unit: Unit) -> float:
        if not unit.alive or unit.position is None:
            return 0.0
        damage = 0.0
        distribution = ((0.0, 1.0),)
        for enemy in living_hostile_combatants(battle, unit.player_id):
            if not battle.unit_can_be_selected(enemy, actor=unit)[0] or enemy.cannot_attack:
                continue
            movement = 0 if enemy.cannot_move or enemy.cannot_normal_move else enemy.normal_move_distance()
            if self.services.distance_between_units(battle, unit, enemy) <= movement + enemy.targeting_range():
                if forecasting_active():
                    distribution = sum_distributions(distribution, attack_damage_distribution(
                        battle, enemy, unit, {}, attack_power=enemy.stat("attack")))
                else:
                    damage += estimate_attack_damage(battle, enemy, unit, {}, attack_power=enemy.stat("attack"))
        if forecasting_active():
            return sum(probability * (min(unit.current_hp, amount) * 45.0
                       + (70.0 if amount >= unit.current_hp and amount > 0 else 0.0))
                       for amount, probability in distribution)
        return min(unit.current_hp, damage) * 45.0 + (70.0 if damage >= unit.current_hp and damage > 0 else 0.0)


    def natsume_support_attack_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if (target is None or target.player_id != actor.player_id
                or not any(component.name == "风壁赠予" for component in actor.iter_components())):
            return -1000.0
        target = battle.effect_recipient(target)
        if target.direct_effects_blocked() or not battle.attack_target_allowed(actor, target, payload=payload)[0]:
            return -1000.0
        before_mana, had_mark = target.current_mana, target.has_status("风壁计数点")
        with ai_probe_rollback(battle):
            battle.resolve_attack_damage(actor, target, action_name="友方普攻支援", payload=payload)
            gain = target.current_mana - before_mana
            mark_added = target.has_status("风壁计数点") and not had_mark
            unlock = any(skill.timing in {"active", "instant"} and before_mana < skill.mana_cost <= target.current_mana
                         and skill.cooldown_remaining <= 0 for skill in target.skills)
        value = gain * 42.0 + (18.0 if gain > 0 and unlock else 0.0)
        protectors = [unit for unit in battle.player_units(actor.player_id) if "natsume_wind_wall" in unit.skill_map()
                      and unit.alive and not unit.banished and unit.current_mana >= 1 and not unit.cannot_use_skills]
        if mark_added and protectors:
            pressure = self.services.natsume_unit_danger(battle, target)
            if pressure > 0:
                value += min(60.0, 25.0 + pressure * 0.5)
                if all(not battle.unit_target_in_range_and_line(unit, target, unit.targeting_range()) for unit in protectors):
                    value += 18.0
        return value if value > 0 else -20.0


    def natsume_skill_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload,
            self.services.difficulty_profile("standard")))


    def natsume_wall_score(self, battle: Battle, actor: Unit, queued_action: QueuedAction, payload: dict[str, Any]) -> float:
        """Compatibility entry point for Natsume's skill-owned reaction score."""
        return actor.get_skill("natsume_wind_wall").ai_reaction_score(AIReactionScoreContext(
            battle, actor, queued_action, {"action_code": "natsume_wind_wall"},
            payload, None, self.services._SHARED_REACTION_SCORING))


    def natsume_position_plan_value(self, battle: Battle, actor: Unit) -> float:
        if not actor.can_take_turn_actions(battle):
            return 0.0
        best_attack = 0.0
        if not actor.cannot_attack and actor.attacks_used < actor.attack_actions_per_turn():
            preview = battle.basic_attack_preview_for_payload(actor, {})
            for payload in self.services.attack_payloads_for_action(battle, actor, {"preview": preview}):
                target = battle.units.get(str(payload.get("target_unit_id")))
                if target is not None and battle.attack_target_allowed(actor, target, payload=payload)[0]:
                    best_attack = max(best_attack, self.services.natsume_support_attack_score(battle, actor, payload) if target.player_id == actor.player_id else self.services.red_attack_value(battle, actor, payload))
        word = actor.skill_map().get("natsume_wind_word")
        best_word = 0.0
        if word is not None and word.can_use(battle, actor)[0]:
            for target in word.targets(battle, actor):
                if target.player_id == actor.player_id and (target.current_hp < target.max_health or self.services.natsume_unit_danger(battle, target) > 0):
                    best_word = max(best_word, self.services.natsume_skill_score(battle, actor, word, {"target_unit_id": target.unit_id}))
        return best_attack + best_word


    def natsume_shensu_score(self, battle: Battle, actor: Unit) -> float:
        if actor.position is None or actor.move_used or actor.cannot_move or actor.cannot_normal_move or not actor.get_skill("shensu").can_use(battle, actor)[0]:
            return -1000.0
        if actor.current_mana < 2 and any(self.services.natsume_unit_danger(battle, ally) > 0 for ally in battle.player_units(actor.player_id)):
            return -20.0
        def reachable_value():
            origin, best = actor.position, 0.0
            try:
                positions = [origin, *self.services.ai_reachable_positions(battle, actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)]
                for position in positions:
                    actor.position = position
                    best = max(best, self.services.natsume_position_plan_value(battle, actor) - self.services.natsume_unit_danger(battle, actor) - origin.distance_to(position) * 2.0)
            finally:
                actor.position = origin
            return best
        before = reachable_value()
        with ai_probe_rollback(battle):
            actor.get_skill("shensu").execute(battle, actor, {})
            after = reachable_value()
        return after + 20.0 if after - before > 18.0 else -20.0


    def fusion_position_risk(self, battle: Battle, actor: Unit) -> float:
        if not actor.alive or actor.position is None:
            return 0.0
        incoming = 0.0
        for enemy in living_hostile_combatants(battle, actor.player_id):
            if not battle.unit_can_be_selected(enemy, actor=actor)[0]:
                continue
            distance = self.services.distance_between_units(battle, actor, enemy)
            movement = 0 if enemy.cannot_move or enemy.cannot_normal_move else enemy.normal_move_distance()
            if not enemy.cannot_attack and distance <= movement + enemy.targeting_range():
                incoming += estimate_attack_damage(battle, enemy, actor, {}, attack_power=enemy.stat("attack")) * enemy.attack_actions_per_turn()
            if not actor.magic_immunity and not enemy.cannot_use_skills and distance <= movement + max(2, enemy.targeting_range()):
                if any(skill.code in self.services.DAMAGING_SKILL_CODES and skill.timing in {"active", "instant"}
                       and enemy.current_mana >= skill.mana_cost and skill.cooldown_remaining <= 0 for skill in enemy.skills):
                    incoming += self.services.estimate_damage(battle, actor, enemy.stat("attack"))
        risk = min(actor.current_hp, incoming) * 70.0
        if incoming >= actor.current_hp and incoming > 0:
            risk += 90.0
            # Price collateral from a real death event, including chained explosions.
            # Enemy collateral can offset friendly collateral, but never makes suicide a reward.
            with ai_probe_rollback(battle):
                before = self.services.red_outcome_snapshot(battle)
                before.pop(actor.unit_id, None)
                actor.current_hp, actor.alive = 0.0, False
                battle.cleanup_dead_units()
                risk += max(0.0, -self.services.red_outcome_value(actor, before))
        return risk


    def fusion_attack_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        with ai_probe_rollback(battle):
            before = self.services.red_outcome_snapshot(battle)
            resolved = battle.resolved_basic_attack_payload(actor, payload)
            cells = battle.basic_attack_area_cells_for_payload(actor, resolved)
            if cells is None:
                return -1000.0
            battle.basic_area_attack_with_payload(actor, resolved, cells)
            return self.services.red_outcome_value(actor, before) - self.services.fusion_position_risk(battle, actor)


    def fusion_attack_plan_value(self, battle: Battle, actor: Unit, *, allow_move: bool = True) -> float:
        if (actor.position is None or not actor.can_take_turn_actions(battle) or actor.cannot_attack
                or actor.attacks_used >= actor.attack_actions_per_turn()):
            return 0.0
        origin, best = actor.position, 0.0
        destinations = [origin]
        if (allow_move and not actor.cannot_move and not actor.cannot_normal_move
                and actor.remaining_normal_move_distance(battle) > 0 and not actor.move_used):
            destinations += self.services.ai_reachable_positions(battle, actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
        try:
            for destination in sorted(set(destinations), key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                actor.position = destination
                preview = battle.basic_attack_preview_for_payload(actor, {})
                seen = set()
                for payload in self.services.attack_payloads_for_action(battle, actor, {"preview": preview}):
                    resolved = battle.resolved_basic_attack_payload(actor, payload)
                    cells = battle.payload_positions(resolved, "attack_cells")
                    key = tuple((cell.x, cell.y) for cell in cells)
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        value = self.services.fusion_attack_value(battle, actor, payload)
                    except ActionError:
                        continue
                    best = max(best, value - origin.distance_to(destination) * 2.0)
        finally:
            actor.position = origin
        return best


    def fusion_preparation_score(self, battle: Battle, actor: Unit, code: str) -> float:
        skill = actor.get_skill(code)
        if not skill.can_use(battle, actor)[0] or actor.direct_effects_blocked():
            return -1000.0
        if code == "shensu" and (actor.move_used or actor.cannot_move or actor.cannot_normal_move):
            return -1000.0
        before = self.services.fusion_attack_plan_value(battle, actor)
        cost = skill.mana_cost_for_payload(battle, actor, {})
        reserve_penalty = 60.0 if actor.current_mana - cost < 0.5 and self.services.fusion_position_risk(battle, actor) > 0 else 0.0
        with ai_probe_rollback(battle):
            skill.execute(battle, actor, {})
            after = self.services.fusion_attack_plan_value(battle, actor)
        gain = after - before - cost * 18.0 - reserve_penalty
        return after + 25.0 if gain > 4.0 else -20.0


    def fusion_pierce_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        skill = actor.get_skill("pierce")
        cost = skill.mana_cost_for_payload(battle, actor, payload)
        with ai_probe_rollback(battle):
            before = self.services.red_outcome_snapshot(battle)
            skill.execute(battle, actor, payload)
            value = self.services.red_outcome_value(actor, before)
            # A killing strike removes its threat before deciding whether to save evasion mana.
            reserve_penalty = 80.0 if actor.current_mana - cost < 0.5 and self.services.fusion_position_risk(battle, actor) > 0 else 0.0
        return value - cost * 18.0 - reserve_penalty


    def red_outcome_snapshot(self, battle: Battle) -> dict[str, tuple]:
        return {unit.unit_id: (unit, unit.current_hp, unit.current_mana, unit.total_shields(),
                               {status.name for status in unit.statuses}, unit.alive)
                for unit in battle.all_units() if unit.alive and unit.position is not None and not unit.banished}


    def red_outcome_value(self, actor: Unit, before: dict[str, tuple]) -> float:
        value = 0.0
        for unit, hp, mana, shields, statuses, alive in before.values():
            enemy = unit.player_id != actor.player_id
            loss = max(0.0, hp - unit.current_hp)
            impact = loss * 100.0 + (90.0 if alive and not unit.alive else 0.0)
            impact += max(0, shields - unit.total_shields()) * 14.0
            impact += max(0.0, mana - unit.current_mana) * 18.0
            if enemy:
                impact += len({status.name for status in unit.statuses} - statuses) * 22.0
            value += impact if enemy else -impact * 1.4
            if not enemy:
                value += max(0.0, unit.current_hp - hp) * 110.0 + max(0.0, unit.current_mana - mana) * 24.0
                if "风壁计数点" not in statuses and unit.has_status("风壁计数点"):
                    value += 24.0
        return value


    def red_attack_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        with ai_probe_rollback(battle):
            before = self.services.red_outcome_snapshot(battle)
            resolved = battle.resolved_basic_attack_payload(actor, payload)
            cells = battle.basic_attack_area_cells_for_payload(actor, resolved)
            if cells is not None:
                resolved.update(attack_cells=self.services.positions_to_payload(cells), area_attack=True)
                affects_allies = all(component.basic_attack_area_affects_allies(battle, actor, resolved)
                                      for component in actor.iter_components())
                targets = [target for target in battle.effect_units_at_cells(cells)
                           if target.player_id != actor.player_id or affects_allies]
            else:
                target = battle.units.get(str(payload.get("target_unit_id") or ""))
                if target is None or not battle.attack_target_allowed(actor, target, payload=payload)[0]:
                    return -1000.0
                targets = [battle.effect_recipient(target)]
            contexts = []
            for target in targets:
                ctx = battle.resolve_attack_damage(actor, target, action_name="普攻", tags={"area_attack"} if cells is not None else None, payload=resolved)
                if ctx is not None:
                    contexts.append(ctx)
            actor.notify_basic_attack_finished(battle, resolved, contexts, missed=not contexts)
            return self.services.red_outcome_value(actor, before)


    def red_attack_plan_value(self, battle: Battle, actor: Unit) -> float:
        remaining = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
        if not remaining or actor.position is None or actor.cannot_attack:
            return 0.0
        origin, best = actor.position, 0.0
        destinations = [origin]
        if not actor.has_status("无限") and not actor.cannot_move and not actor.cannot_normal_move and actor.remaining_normal_move_distance(battle) > 0:
            destinations += self.services.ai_reachable_positions(battle, actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
        # One nearest firing endpoint per visible enemy keeps preparation evaluation bounded.
        pending = {unit.unit_id for unit in living_hostile_combatants(battle, actor.player_id)
                   if battle.unit_can_be_selected(unit, actor=actor)[0]}
        seen = set()
        try:
            for destination in sorted(set(destinations), key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                actor.position = destination
                for spec in battle.basic_attack_action_specs(actor):
                    action = {**spec, "preview": battle.basic_attack_preview_for_payload(actor, spec.get("attack_payload"))}
                    for payload in self.services.attack_payloads_for_action(battle, actor, action):
                        target_id = str(payload.get("target_unit_id") or "")
                        if target_id and target_id not in pending:
                            continue
                        cells = battle.basic_attack_area_cells_for_payload(actor, payload)
                        key = tuple((cell.x, cell.y) for cell in cells) if cells is not None else (target_id,)
                        key += (str(payload.get("attack_variant") or "default"),)
                        if key in seen:
                            continue
                        seen.add(key)
                        pending.discard(target_id)
                        best = max(best, self.services.red_attack_value(battle, actor, payload) * remaining - origin.distance_to(destination) * 2)
                if not pending:
                    break
        finally:
            actor.position = origin
        return best


    def weapon_copy_score(self, battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        skill = actor.get_skill("weapon_copy")
        if actor.has_status("武装复制") or battle.units.get(str(payload.get("target_unit_id") or "")) not in skill.targets(battle, actor):
            return -1000.0
        before = self.services.red_attack_plan_value(battle, actor)
        with ai_probe_rollback(battle):
            skill.execute(battle, actor, payload)
            after = self.services.red_attack_plan_value(battle, actor)
            transfer = actor.skill_map().get("weapon_transfer")
            if transfer is not None and transfer.can_use(battle, actor)[0]:
                transfer.execute(battle, actor, {})
                after = max(after, self.services.red_attack_plan_value(battle, actor))
            infinite = actor.skill_map().get("infinite")
            if infinite is not None and infinite.can_use(battle, actor)[0]:
                infinite.execute(battle, actor, {})
                after = max(after, self.services.red_attack_plan_value(battle, actor) - profile.once_per_battle_threshold)
        # Compare with the preparation available without copying as well.
        with ai_probe_rollback(battle):
            transfer = actor.skill_map().get("weapon_transfer")
            if transfer is not None and transfer.can_use(battle, actor)[0]:
                transfer.execute(battle, actor, {})
                before = max(before, self.services.red_attack_plan_value(battle, actor))
            infinite = actor.skill_map().get("infinite")
            if infinite is not None and infinite.can_use(battle, actor)[0]:
                infinite.execute(battle, actor, {})
                before = max(before, self.services.red_attack_plan_value(battle, actor) - profile.once_per_battle_threshold)
        return after + 40.0 if after - before > 4 else -20.0


    def deadly_bow_score(self,
        battle: Battle, actor: Unit, action: dict[str, Any], payload: dict[str, Any], profile: DifficultyProfile,
    ) -> float:
        skill = self.services.skill_from_ai_action(actor, action, "deadly_bow")
        try:
            declared = {**payload, **skill.queued_payload_metadata(battle, actor, payload)}
        except (ActionError, ValueError, TypeError):
            return -1000.0
        with ai_probe_rollback(battle):
            before = self.services.red_outcome_snapshot(battle)
            skill.execute(battle, actor, declared)
            value = self.services.red_outcome_value(actor, before)
        return value - skill.mana_cost_for_payload(battle, actor, payload) * 18.0 - actor.mana_points * 7.0


    def red_preparation_score(self, battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
        skill = actor.get_skill(code)
        if code == "red_charge":
            if actor.mana_points >= getattr(actor, "max_mana_points", 5) or actor.direct_effects_blocked():
                return -20.0
            bow = actor.skill_map().get("deadly_bow")
            best_before = best_after = -1000.0
            if bow is not None and bow.can_use(battle, actor)[0]:
                for direction in bow.DIRECTIONS:
                    payload = {"direction": direction}
                    best_before = max(best_before, self.services.deadly_bow_score(battle, actor, {"code": bow.code}, payload, profile))
                    with ai_probe_rollback(battle):
                        actor.gain_mana_points(1)
                        best_after = max(best_after, self.services.deadly_bow_score(battle, actor, {"code": bow.code}, payload, profile))
            return max(28.0, best_after + 30.0) if best_after > best_before + 3 else 24.0
        if code in {"infinite_armor", "infinite_robe", "flying_dragon_orb"}:
            if (code in {"infinite_armor", "flying_dragon_orb"} and (actor.physical_immunity or actor.has_status("无限铠甲") or actor.has_status("飞龙宝珠"))
                    or code == "infinite_robe" and actor.magic_immunity):
                return -20.0
            if forecasting_active() and not direct_scoring():
                try:
                    with ai_probe_rollback(battle):
                        expected = self.services._red_expected_defense_value(battle, actor, skill, code)
                    if expected is not None:
                        return expected
                except UnstableObservation:
                    note("defense_convolution_unsafe_inspection")
            def exposure():
                result = 0.0
                for enemy in living_hostile_combatants(battle, actor.player_id):
                    distance = self.services.distance_between_units(battle, actor, enemy)
                    if code in {"infinite_armor", "flying_dragon_orb"}:
                        if not enemy.cannot_attack and distance <= enemy.targeting_range() + enemy.normal_move_distance():
                            result += estimate_attack_damage(battle, enemy, actor, {}, attack_power=enemy.stat("attack")) * enemy.attack_actions_per_turn()
                    elif distance <= max(5, enemy.targeting_range()) + enemy.normal_move_distance() and not enemy.cannot_use_skills:
                        for incoming in enemy.skills:
                            if (incoming.code in self.services.DAMAGING_SKILL_CODES and incoming.cooldown_remaining == 0
                                    and (incoming.max_uses_per_battle is None or incoming.uses_this_battle < incoming.max_uses_per_battle)
                                    and enemy.current_mana >= incoming.mana_cost):
                                result += estimate_skill_damage(battle, enemy, incoming, {}, actor, enemy.stat("attack"), cells=[],
                                                                ignore_shield=incoming.ignores_shield_for_payload(battle, enemy, {}),
                                                                half_ignore_shield=False)
                return result
            before = exposure()
            with ai_probe_rollback(battle):
                skill.execute(battle, actor, {})
                saved = max(0.0, before - exposure())
            return saved * 130.0 + (90.0 if saved >= actor.current_hp and saved > 0 else 0.0) - 35.0
        before = self.services.red_attack_plan_value(battle, actor)
        with ai_probe_rollback(battle):
            skill.execute(battle, actor, {})
            after = self.services.red_attack_plan_value(battle, actor)
            if code == "weapon_transfer":
                infinite = actor.skill_map().get("infinite")
                if infinite is not None and infinite.can_use(battle, actor)[0]:
                    infinite.execute(battle, actor, {})
                    after = max(after, self.services.red_attack_plan_value(battle, actor) - profile.once_per_battle_threshold)
        gain = after - before
        if gain <= 4:
            return -20.0
        return after + (25.0 if code == "weapon_transfer" else 10.0) - (25.0 if code == "infinite" else 0.0)


    def _red_exposure_distribution(self, battle: Battle, actor: Unit, code: str):
        from wujiang.tactical.prediction import outcomes
        # Only the individual damage probes are stochastic. They already restore
        # the battle independently; inspect all intervening reads before separating
        # their finite distributions. Keep attack-count multiplication INSIDE each
        # term, just as in the original exposure loop.
        terms = []
        with stable_observations(battle):
            for enemy in living_hostile_combatants(battle, actor.player_id):
                distance = self.services.distance_between_units(battle, actor, enemy)
                if code in {"infinite_armor", "flying_dragon_orb"}:
                    if not enemy.cannot_attack and distance <= enemy.targeting_range() + enemy.normal_move_distance():
                        terms.append((enemy, None, enemy.stat("attack"), enemy.attack_actions_per_turn(), False))
                elif distance <= max(5, enemy.targeting_range()) + enemy.normal_move_distance() and not enemy.cannot_use_skills:
                    for incoming in enemy.skills:
                        if (incoming.code in self.services.DAMAGING_SKILL_CODES and incoming.cooldown_remaining == 0
                                and (incoming.max_uses_per_battle is None or incoming.uses_this_battle < incoming.max_uses_per_battle)
                                and enemy.current_mana >= incoming.mana_cost):
                            terms.append((enemy, incoming, enemy.stat("attack"), 1,
                                          incoming.ignores_shield_for_payload(battle, enemy, {})))
        total = ((0.0, 1.0),)
        for enemy, incoming, power, count, ignore_shield in terms:
            def term():
                if incoming is None:
                    return estimate_attack_damage(battle, enemy, actor, {}, attack_power=power) * count
                return estimate_skill_damage(battle, enemy, incoming, {}, actor, power, cells=[],
                                             ignore_shield=ignore_shield, half_ignore_shield=False)
            distribution = outcomes(battle, term, cache_request=("defense_exposure_term", code, actor,
                enemy, incoming, power, count, ignore_shield, id(estimate_attack_damage.__code__),
                id(estimate_skill_damage.__code__), id(probe_attack_damage_impact.__code__),
                id(probe_skill_damage_impact.__code__)), eager_cache=True)
            total = sum_distributions(total, distribution)
        return total


    def _red_expected_defense_value(self, battle: Battle, actor: Unit, skill: Any, code: str) -> Optional[float]:
        import math
        import os
        if os.environ.get("WUJIANG_AI_EXPECTATION_FACTORS", "1") == "0":
            return None
        before = self.services._red_exposure_distribution(battle, actor, code)
        with ai_probe_rollback(battle):
            skill.execute(battle, actor, {})
            after = self.services._red_exposure_distribution(battle, actor, code)
        with stable_observations(battle):
            hp = actor.current_hp
        # Preserve max(0, before-after) AND its lethal threshold for every sum.
        # Before/after use independent draws in the original two probe loops.
        values = []
        for first, first_probability in before:
            for second, second_probability in after:
                saved = max(0.0, first - second)
                values.append((saved * 130.0 + (90.0 if saved >= hp and saved > 0 else 0.0) - 35.0)
                              * first_probability * second_probability)
        note("convolved_defense_queries")
        return math.fsum(values)


    def wuchang_action_success_probability(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.heroes.mechanics.wuchang import WuchangMistField
        if direct_scoring():
            from wujiang.tactical.heroes.mechanics.ice_emperor import FloatingCraneCityField
            is_attack = payload.get("type") == "attack"
            active_skill = False
            if payload.get("type") == "skill":
                skill = actor.get_skill(str(payload.get("skill_code") or ""))
                skill = skill.effective_skill_for_payload(battle, actor, payload)
                active_skill = skill.timing == "active"
            probability = .5 if ((is_attack or active_skill) and battle.has_weather("无常之雾")
                                 and WuchangMistField.affects_actor(actor)) else 1.0
            if active_skill and any(isinstance(effect, FloatingCraneCityField) and not effect.exempt(actor)
                                    and battle.weather_effect_applies(effect, actor) for effect in battle.field_effects):
                probability *= .5
            return probability
        if not battle.has_weather("无常之雾") or not WuchangMistField.affects_actor(actor):
            return 1.0
        if payload.get("type") == "attack":
            return 0.5
        if payload.get("type") != "skill":
            return 1.0
        skill = actor.get_skill(str(payload.get("skill_code") or ""))
        if skill.code == "mimic_skill":
            context = self.services.mimic_payload_context(battle, actor, payload)
            skill = context[1] if context is not None else skill
        elif skill.code == "agency_borrowed_skill":
            context = self.services.agency_borrowed_payload_context(battle, actor, payload)
            skill = context[1] if context is not None else skill
        return 0.5 if skill.timing == "active" else 1.0


    def wuchang_exposed_action_value(self, battle: Battle, unit: Unit) -> float:
        """Upcoming attack/active opportunities; passive and instant defenses are unaffected."""
        if not unit.alive or unit.banished or unit.position is None or unit.is_clone:
            return 0.0
        attack = 0.0
        opponents = battle.effect_units(living_hostile_combatants(battle, unit.player_id))
        if not unit.cannot_attack:
            with ai_probe_rollback(battle):
                battle.resolving_action = None
                battle.active_player, battle._exclusive_turn_unit_id, unit.turn_ready = unit.player_id, unit.unit_id, True
                unit.attacks_used = unit.normal_move_actions_used = unit.normal_move_steps_used = 0
                unit.move_used = False
                origin = unit.position
                destinations = [origin]
                if not unit.cannot_move and not unit.cannot_normal_move:
                    destinations.extend(self.services.ai_reachable_positions(battle,
                        unit, max_distance=unit.remaining_normal_move_distance(battle), use_movement_cost=True))
                for destination in dict.fromkeys(destinations):
                    unit.position = destination
                    for target in opponents:
                        if not battle.attack_target_allowed(unit, target)[0]:
                            continue
                        damage = estimate_attack_damage(battle, unit, target, {}, attack_power=unit.stat("attack"))
                        attack = max(attack, min(target.current_hp, damage) * 100.0)
        active = []
        if not unit.cannot_use_skills:
            with ai_probe_rollback(battle):
                battle.resolving_action = None
                battle.active_player, battle._exclusive_turn_unit_id, unit.turn_ready = unit.player_id, unit.unit_id, True
                for skill in unit.skills:
                    if skill.timing != "active":
                        continue
                    try:
                        if not skill.can_use(battle, unit, {})[0]:
                            continue
                        if skill.code in self.services.DAMAGING_SKILL_CODES or skill.code in self.services.CONTROL_SKILL_CODES:
                            action = self.services.copied_skill_action(battle, unit, skill)
                            payloads = self.services.skill_payloads_for_action(battle, unit, action)
                            payloads = sorted(payloads, key=lambda payload: sum(
                                target.player_id != unit.player_id for target in self.services.skill_effect_units(battle, unit, skill, payload)
                            ), reverse=True)
                            if any(self.services.skill_payload_has_effective_enemy_impact(battle, unit, action, payload)
                                   for payload in payloads):
                                active.append(36.0)
                        elif skill.code in self.services.HEAL_SKILL_CODES:
                            missing = max((ally.max_health - ally.current_hp for ally in battle.player_units(unit.player_id)
                                           if ally.alive and not ally.cannot_heal and not ally.direct_effects_blocked()), default=0.0)
                            if missing > 0:
                                active.append(min(0.25, missing) * 100.0)
                        elif skill.code in self.services.ALLY_BUFF_SKILL_CODES or skill.code in self.services.SELF_BUFF_SKILL_CODES or skill.code in self.services.SUMMON_SKILL_CODES:
                            active.append(24.0 if opponents else 0.0)
                    except (ActionError, KeyError, TypeError, ValueError):
                        continue
        return attack * unit.attack_actions_per_turn() + sum(sorted(active, reverse=True)[:3])


    def wuchang_mist_score(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        skill = actor.get_skill("wuchang_mist")
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, {}, profile))


    def migratory_bird_mark_score(self,
        battle: Battle, actor: Unit, action: dict[str, Any], payload: dict[str, Any], profile: DifficultyProfile,
    ) -> float:
        skill = self.services.skill_from_ai_action(actor, action, "migratory_bird_mark")
        return skill.ai_effect_score(self.services._skill_score_context(battle, actor, action, payload, profile))


    def wuchang_split_score(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        return actor.ai_policy.contextual_skill_score(self.services._skill_score_context(
            battle, actor, {"code": "split"}, {}, profile))


_BOUND_SERVICE: AISupportScoring | None = None

def bind_room(service: AISupportScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AISupportScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

@expected_numeric(independent=True)
def messenger_refill_opportunity(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().messenger_refill_opportunity(battle, actor, profile)


@expected_numeric(independent=True)
def messenger_preparation_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    return _require_service().messenger_preparation_score(battle, actor, code, profile)


@expected_numeric(independent=True)
def ally_buff_score(battle: Battle, actor: Unit, code: str, target: Unit, profile: DifficultyProfile) -> float:
    return _require_service().ally_buff_score(battle, actor, code, target, profile)


def guardian_finale_opportunities(battle: Battle, actor: Unit) -> tuple[float, float, float, bool]:
    """Price reachable attacks and useful remaining breath casts without declaring actions."""
    return _require_service().guardian_finale_opportunities(battle, actor)


@expected_numeric(independent=True)
def guardian_finale_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().guardian_finale_score(battle, actor, profile)


@expected_numeric(independent=True)
def agency_contract_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().agency_contract_score(battle, actor, payload, profile)


@expected_numeric(independent=True, eager_cache=True)
def natsume_unit_danger(battle: Battle, unit: Unit) -> float:
    return _require_service().natsume_unit_danger(battle, unit)


@expected_numeric(independent=True)
def natsume_support_attack_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().natsume_support_attack_score(battle, actor, payload)


@expected_numeric(independent=True)
def natsume_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    return _require_service().natsume_skill_score(battle, actor, skill, payload)


@expected_numeric(independent=True)
def natsume_wall_score(battle: Battle, actor: Unit, queued_action: QueuedAction, payload: dict[str, Any]) -> float:
    """Compatibility entry point for Natsume's skill-owned reaction score."""
    return _require_service().natsume_wall_score(battle, actor, queued_action, payload)


@expected_numeric(independent=True)
def natsume_position_plan_value(battle: Battle, actor: Unit) -> float:
    return _require_service().natsume_position_plan_value(battle, actor)


@expected_numeric(independent=True)
def natsume_shensu_score(battle: Battle, actor: Unit) -> float:
    return _require_service().natsume_shensu_score(battle, actor)


@expected_numeric(independent=True)
def fusion_position_risk(battle: Battle, actor: Unit) -> float:
    return _require_service().fusion_position_risk(battle, actor)


@expected_numeric(independent=True)
def fusion_attack_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().fusion_attack_value(battle, actor, payload)


@expected_numeric(independent=True)
def fusion_attack_plan_value(battle: Battle, actor: Unit, *, allow_move: bool = True) -> float:
    return _require_service().fusion_attack_plan_value(battle, actor, allow_move=allow_move)


@expected_numeric(independent=True)
def fusion_preparation_score(battle: Battle, actor: Unit, code: str) -> float:
    return _require_service().fusion_preparation_score(battle, actor, code)


@expected_numeric(independent=True)
def fusion_pierce_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().fusion_pierce_score(battle, actor, payload)


def red_outcome_snapshot(battle: Battle) -> dict[str, tuple]:
    return _require_service().red_outcome_snapshot(battle)


def red_outcome_value(actor: Unit, before: dict[str, tuple]) -> float:
    return _require_service().red_outcome_value(actor, before)


@expected_numeric(independent=True)
def red_attack_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().red_attack_value(battle, actor, payload)


@expected_numeric(independent=True)
def red_attack_plan_value(battle: Battle, actor: Unit) -> float:
    return _require_service().red_attack_plan_value(battle, actor)


@expected_numeric(independent=True)
def weapon_copy_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().weapon_copy_score(battle, actor, payload, profile)


@expected_numeric(independent=True)
def deadly_bow_score(
    battle: Battle, actor: Unit, action: dict[str, Any], payload: dict[str, Any], profile: DifficultyProfile,
) -> float:
    return _require_service().deadly_bow_score(battle, actor, action, payload, profile)


@expected_numeric(independent=True)
def red_preparation_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    return _require_service().red_preparation_score(battle, actor, code, profile)


def _red_exposure_distribution(battle: Battle, actor: Unit, code: str):
    return _require_service()._red_exposure_distribution(battle, actor, code)


def _red_expected_defense_value(battle: Battle, actor: Unit, skill: Any, code: str) -> Optional[float]:
    return _require_service()._red_expected_defense_value(battle, actor, skill, code)


@expected_numeric(independent=True)
def wuchang_exposed_action_value(battle: Battle, unit: Unit) -> float:
    """Upcoming attack/active opportunities; passive and instant defenses are unaffected."""
    return _require_service().wuchang_exposed_action_value(battle, unit)


@expected_numeric(independent=True)
def wuchang_mist_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().wuchang_mist_score(battle, actor, profile)


@expected_numeric(independent=True)
def migratory_bird_mark_score(
    battle: Battle, actor: Unit, action: dict[str, Any], payload: dict[str, Any], profile: DifficultyProfile,
) -> float:
    return _require_service().migratory_bird_mark_score(battle, actor, action, payload, profile)


@expected_numeric(independent=True)
def wuchang_split_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().wuchang_split_score(battle, actor, profile)
