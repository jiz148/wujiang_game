"""AIMobilityEffectScoring: shared AI scoring and queries for mobility_effect."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from typing import Any, Callable, Iterable, Optional
from wujiang.tactical.rooms.ai_acceleration import cached_effect_baseline, cached_move_batch, cached_move_score, cached_position_value, cached_tactical_query, decision_memo_scope, prepare_position_values, tactical_queries_active
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

class AIMobilityEffectScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    DAMAGING_SKILL_CODES: Any
    DifficultyProfile: Any
    best_available_attack_score: Callable[..., Any]
    copied_skill_action: Any
    distance_between_units: Callable[..., Any]
    friendly_fire_penalty: Callable[..., Any]
    hero_style: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    natsume_unit_danger: Callable[..., Any]
    offensive_reach_score_at: Callable[..., Any]
    payload_destination: Callable[..., Any]
    r19_incoming_position_risk: Callable[..., Any]
    reviewed_effect_baseline: Callable[..., Any]
    reviewed_r16_damage_result: Callable[..., Any]
    reviewed_r17_position_value: Callable[..., Any]
    score_move_destination: Callable[..., Any]
    scored_death_probability: Callable[..., Any]
    skill_effect_units: Callable[..., Any]
    skill_payloads_for_action: Any
    trim_skill_payloads_for_ai: Callable[..., Any]
    zero_path_effect_score: Callable[..., Any]


class AIMobilityEffectScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AIMobilityEffectScoringServices) -> None:
        self.services = services

    def payload_step_direction(self, payload: dict[str, Any]) -> tuple[int, int] | None:
        raw = payload.get("direction")
        if isinstance(raw, dict) and raw.get("dx") is not None and raw.get("dy") is not None:
            return int(raw["dx"]), int(raw["dy"])
        if payload.get("dx") is not None and payload.get("dy") is not None:
            return int(payload["dx"]), int(payload["dy"])
        return None


    def score_damage_cells(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        cells: list[Position],
        profile: DifficultyProfile,
        *,
        ignore_shield: bool,
    ) -> float:
        score = 0.0
        for target in battle.effect_units_at_cells(cells):
            if target.unit_id == actor.unit_id:
                continue
            if target.player_id == actor.player_id:
                score -= self.services.friendly_fire_penalty(target)
                continue
            impact = probe_skill_damage_impact(
                battle,
                actor,
                skill,
                payload,
                target,
                actor.stat("attack"),
                cells=cells,
                ignore_shield=ignore_shield,
                half_ignore_shield=False,
            )
            score += impact.damage * 100.0 + self.services.hostile_unit_value(target) * 0.45
            if impact.changed_target and impact.damage <= 0:
                score += 35.0
            if impact.damage >= target.current_hp - 1e-9:
                score += 90.0
        return score + profile.aggressive_bonus if score > 0 else score


    def zero_dash_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
        destination = self.services.payload_destination(payload)
        if destination is None:
            return -20.0
        try:
            path = battle.find_path(actor, destination, max_distance=8, exact_distance=8, straight_only=True, ignore_units=True)
        except Exception:
            return -20.0
        return self.services.zero_path_effect_score(battle, actor, path, via_skill=True)




    def zero_path_effect_score(self, battle: Battle, actor: Unit, path: list[Position], *, via_skill: bool = False) -> float:
        operation = lambda: battle.move_unit(actor, path[-1], path=path[1:], via_skill=via_skill,
                                             ignore_units=True, max_distance=len(path) - 1)
        baseline = self.services.reviewed_r16_damage_result(battle, actor, operation)
        if not any(unit.player_id != actor.player_id and unit.skill_map().get("evasion")
                   for unit in battle.path_crossing_units(actor, path)):
            return baseline
        evasion_case = self.services.reviewed_r16_damage_result(battle, actor, operation, try_passage_evasion=True)
        return min(baseline, (baseline + evasion_case) / 2)


    def blind_paid_move_score(self, battle: Battle, actor: Unit, destination: Position, profile: DifficultyProfile) -> float:
        if actor.position is None:
            return -1000.0
        before_attack = self.services.best_available_attack_score(battle, actor, profile)
        before_position_score = self.services.score_move_destination(battle, actor, actor.position, self.services.hero_style(actor), profile)
        with ai_probe_rollback(battle):
            before_mana = actor.current_mana
            before_hp = actor.current_hp
            try:
                battle.move_unit(actor, destination)
            except ActionError:
                return -1000.0
            if not actor.alive:
                return -1000.0
            cost = before_mana - actor.current_mana
            attack_gain = self.services.best_available_attack_score(battle, actor, profile) - before_attack
            position_gain = self.services.score_move_destination(battle, actor, destination, self.services.hero_style(actor), profile) - before_position_score
            score = position_gain + max(0.0, attack_gain) * 0.8 - cost * 24.0
            score -= max(0.0, before_hp - actor.current_hp) * 130.0
            if actor.current_mana < 0.5 and any(self.services.distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range()
                                               for enemy in living_hostile_combatants(battle, actor.player_id)):
                score -= 35.0
            return score if attack_gain > 0 or position_gain > cost * 24.0 else -20.0


    def fuma_pursuit_score(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        return self.services.reviewed_r16_damage_result(battle, actor, lambda: skill.execute(battle, actor, payload))


    def fuma_trap_score(self, battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        center = self.services.payload_destination(payload)
        if center is None:
            return -20.0
        if any(
            getattr(effect, "name", "") == "陷阱"
            and getattr(effect, "source_unit_id", None) == actor.unit_id
            and getattr(effect, "center", None) == center
            for effect in battle.field_effects
        ):
            return -30.0
        from wujiang.tactical.heroes.mechanics.fuma import FumaTrapEffect
        effect = FumaTrapEffect(actor.unit_id, actor.player_id, center)
        score = self.services.reviewed_r16_damage_result(battle, actor, lambda: effect.on_any_turn_end(battle, 3 - actor.player_id))
        return score - 12.0


    def reviewed_r17_position_value(self, battle: Battle, actor: Unit, unit: Unit) -> float:
        positions = getattr(unit, "_ai_expected_positions", None) if direct_scoring() else None
        if positions:
            current = unit.position
            del unit._ai_expected_positions
            try:
                value = 0.0
                for position, probability in positions:
                    unit.position = position
                    value += probability * self.services.reviewed_r17_position_value(battle, actor, unit)
                return value
            finally:
                unit.position = current
                unit._ai_expected_positions = positions
        if unit.position is None or not unit.alive or unit.banished:
            return 0.0
        if unit.player_id == actor.player_id:
            return self.services.offensive_reach_score_at(battle, unit, unit.position) * 30.0 - self.services.natsume_unit_danger(battle, unit) * 1.4
        estimate = expected_attack_damage if forecasting_active() else estimate_attack_damage
        return sum(estimate(battle, unit, ally, {}, attack_power=unit.stat("attack")) * 80.0
                   for ally in battle.player_units(actor.player_id)
                   if ally.position is not None and self.services.distance_between_units(battle, unit, ally) <= unit.normal_move_distance() + unit.targeting_range())


    def reviewed_effect_baseline(self, battle: Battle, actor: Unit, units: list[Unit], *, include_pending: bool = False) -> dict:
        # Preserve the original field reads and position queries, in their exact
        # per-unit order. The decorator skips only a previously pure whole batch.
        if include_pending:
            return {unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields(), unit.dodge_charges,
                                  unit.alive, unit.position, self.services.reviewed_r17_position_value(battle, actor, unit),
                                  bool(getattr(unit.get_status("毁灭电击"), "pending", False))) for unit in units}
        return {unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields(), unit.dodge_charges,
                              unit.alive, unit.position, self.services.reviewed_r17_position_value(battle, actor, unit)) for unit in units}


    def reviewed_r17_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        with ai_probe_rollback(battle):
            units = list(battle.all_units())
            prepare_position_values(battle, actor, units, for_baseline=True)
            before = self.services.reviewed_effect_baseline(battle, actor, units)
            try:
                queued = battle.build_queued_action(payload)
                battle.prepay_skill_resources(skill, actor, queued.payload)
                queued.payload["declared_source_attack"] = actor.stat("attack")
                battle.resolving_action = queued
                skill.execute(battle, actor, queued.payload)
            except (ActionError, KeyError, ValueError, TypeError):
                return -1000.0
            score = 0.0
            for unit in units:
                hp, mana, shields, dodges, alive, position, position_value = before[unit.unit_id]
                hp_loss = hp - unit.current_hp
                value = hp_loss * 100.0 + (mana - unit.current_mana) * 24.0
                value += max(0, shields - unit.total_shields()) * 20.0 + max(0, dodges - unit.dodge_charges) * 12.0
                if alive and self.services.scored_death_probability(unit) > 0:
                    value += (90.0 * self.services.scored_death_probability(unit) if direct_scoring() else 90.0)
                if unit.player_id == actor.player_id:
                    score -= value * (1.3 if hp_loss > 0 else 1.1)
                else:
                    score += value
                if unit.position != position:
                    delta = self.services.reviewed_r17_position_value(battle, actor, unit) - position_value
                    score += delta if unit.player_id == actor.player_id else -delta
            before_hp = before[actor.unit_id][0]
            if (actor.alive and before_hp < actor.max_health / 2 <= actor.current_hp
                    and any(component.name == "不死保留" for component in actor.iter_components())):
                score += 65.0
            return score - 8.0


    def friendly_mirror_score(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        from wujiang.tactical.heroes.mechanics.rainbow_mirror import FriendlyMirrorStatus
        if actor.has_status("友好镜"):
            return -1000.0
        risk = self.services.r19_incoming_position_risk(battle, actor)
        with ai_probe_rollback(battle):
            actor.add_status(FriendlyMirrorStatus())
            score = max(0.0, risk - self.services.r19_incoming_position_risk(battle, actor)) * 1.1
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
                            if battle.effect_recipient(actor) not in self.services.skill_effect_units(battle, enemy, skill, payload):
                                continue
                            losses = []
                            for protected in (False, True):
                                with ai_probe_rollback(battle):
                                    if protected:
                                        actor.add_status(FriendlyMirrorStatus())
                                    target = battle.effect_recipient(actor)
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
                                    losses.append(max(0.0, hp - target.current_hp))
                            best = max(best, (losses[0] - losses[1]) * 110.0)
                    except (ActionError, KeyError, ValueError, TypeError):
                        continue
                score += best
        return score if score > 0 else -25.0


    def reviewed_r18_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        from wujiang.tactical.heroes.mechanics.nameless_messenger import MessengerDamageSkillManaTrait
        with ai_probe_rollback(battle):
            units = list(battle.all_units())
            prepare_position_values(battle, actor, units, for_baseline=True)
            before = self.services.reviewed_effect_baseline(battle, actor, units, include_pending=True)
            points = actor.mana_points
            pressure = 0.0
            try:
                queued = battle.build_queued_action(payload)
                pressure_scorer = getattr(skill, "ai_reaction_mana_pressure", None)
                if pressure_scorer is not None:
                    pressure = pressure_scorer(battle, queued)
                battle.prepay_skill_resources(skill, actor, queued.payload)
                for component in actor.iter_components():
                    if isinstance(component, MessengerDamageSkillManaTrait):
                        component.on_owner_action_declared(battle, "skill", queued.payload)
                queued.payload["declared_source_attack"] = actor.stat("attack")
                battle.resolving_action = queued
                skill.execute(battle, actor, queued.payload)
            except (ActionError, KeyError, ValueError, TypeError):
                return -1000.0
            score, changed_enemy = 0.0, False
            for unit in units:
                hp, mana, shields, dodges, alive, position, position_value, pending = before[unit.unit_id]
                hp_loss = hp - unit.current_hp
                value = hp_loss * 100 + (mana - unit.current_mana) * 24
                value += max(0, shields - unit.total_shields()) * 20 + max(0, dodges - unit.dodge_charges) * 12
                if alive and self.services.scored_death_probability(unit) > 0:
                    value += (90 * self.services.scored_death_probability(unit) if direct_scoring() else 90)
                hostile = unit.player_id != actor.player_id
                score += value if hostile else -value * (1.3 if hp_loss > 0 else 1.1)
                changed_enemy |= hostile and (hp_loss > 0 or shields > unit.total_shields())
                if unit.position != position:
                    delta = self.services.reviewed_r17_position_value(battle, actor, unit) - position_value
                    score += -delta if hostile else delta
                if hostile and unit.alive and not pending and getattr(unit.get_status("毁灭电击"), "pending", False):
                    score += unit.normal_move_distance() * 4 + (0 if unit.is_clone else unit.attack_actions_per_turn() * 20)
                    score += min(30, sum(other.timing == "active" and other.mana_cost <= unit.current_mana for other in unit.skills) * 10)
            if getattr(getattr(actor, "ai_policy", None), "ai_messenger_mana", False) and actor.mana_points > points:
                score += 14.0 if points < 2 <= actor.mana_points else (3.0 if points < 4 else 0.0)
            if changed_enemy:
                score += pressure
            return score - 8.0


_BOUND_SERVICE: AIMobilityEffectScoring | None = None

def bind_room(service: AIMobilityEffectScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AIMobilityEffectScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

def payload_step_direction(payload: dict[str, Any]) -> tuple[int, int] | None:
    return _require_service().payload_step_direction(payload)


@expected_numeric(independent=True)
def score_damage_cells(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    cells: list[Position],
    profile: DifficultyProfile,
    *,
    ignore_shield: bool,
) -> float:
    return _require_service().score_damage_cells(battle, actor, skill, payload, cells, profile, ignore_shield=ignore_shield)


@expected_numeric(independent=True)
def zero_dash_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    return _require_service().zero_dash_score(battle, actor, skill, payload)




@expected_numeric(independent=True)
def zero_path_effect_score(battle: Battle, actor: Unit, path: list[Position], *, via_skill: bool = False) -> float:
    return _require_service().zero_path_effect_score(battle, actor, path, via_skill=via_skill)


@expected_numeric(independent=True)
def blind_paid_move_score(battle: Battle, actor: Unit, destination: Position, profile: DifficultyProfile) -> float:
    return _require_service().blind_paid_move_score(battle, actor, destination, profile)


@expected_numeric(independent=True)
def fuma_pursuit_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service().fuma_pursuit_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def fuma_trap_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().fuma_trap_score(battle, actor, payload, profile)


@expected_numeric(independent=True, eager_cache=True)
def reviewed_r17_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().reviewed_r17_effect_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def friendly_mirror_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().friendly_mirror_score(battle, actor, profile)


@expected_numeric(independent=True, eager_cache=True)
def reviewed_r18_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().reviewed_r18_effect_score(battle, actor, skill, payload, profile)
