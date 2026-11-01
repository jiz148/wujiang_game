"""AIOpportunityScoring: shared AI scoring and queries for opportunity."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from copy import deepcopy
from typing import Any, Callable, Iterable, Optional
from wujiang.tactical.rooms.ai_acceleration import cached_effect_baseline, cached_move_batch, cached_move_score, cached_position_value, cached_tactical_query, decision_memo_scope, prepare_position_values, tactical_queries_active
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
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

class AIOpportunityScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    DifficultyProfile: Any
    _SHARED_REACTION_SCORING: Any
    _TACTICAL_QUERY_BINDINGS: Any
    _ai_reachable_coordinates: Callable[..., Any]
    _r21_queued_loss_expectations: Callable[..., Any]
    _r21_queued_loss_vector: Callable[..., Any]
    ai_reachable_positions: Callable[..., Any]
    copied_skill_action: Any
    dedupe_damage_area_payloads: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    preview_positions: Callable[..., Any]
    primary_target_unit: Callable[..., Any]
    r21_attack_value: Callable[..., Any]
    legal_attack_payload: Callable[..., Any]
    r21_queued_loss: Callable[..., Any]
    r21_unit_action_opportunities: Callable[..., Any]
    r21_unit_attack_opportunity: Callable[..., Any]
    r21_winged_contact_value: Callable[..., Any]
    reviewed_r18_effect_score: Callable[..., Any]
    scored_death_probability: Callable[..., Any]
    skill_payloads_for_action: Any
    trim_skill_payloads_for_ai: Callable[..., Any]


class AIOpportunityScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AIOpportunityScoringServices) -> None:
        self.services = services

    def beetle_armor_reaction_score(self, battle: Battle, reactor: Unit, queued_action: QueuedAction, profile: DifficultyProfile) -> float:
        """Compatibility entry point for the beetle skill's reaction estimate."""
        return reactor.get_skill("beetle_armor_deploy").ai_reaction_score(
            AIReactionScoreContext(battle, reactor, queued_action,
                {"action_code": "beetle_armor_deploy"}, {}, profile,
                self.services._SHARED_REACTION_SCORING))


    def r21_attack_value(self, battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> float:
        target = battle.effect_recipient(target)
        with ai_probe_rollback(battle):
            hp, mana, shields, dodges = target.current_hp, target.current_mana, target.total_shields(), target.dodge_charges
            stats = {name: target.stat(name) for name in ("attack", "defense", "speed")}
            own_mana = actor.current_mana
            ctx = battle.resolve_attack_damage(actor, target, action_name="普攻", payload=payload)
            if ctx is None:
                return -4.0
            value = max(0.0, hp - target.current_hp) * 100.0 + (90.0 if hp > 0 and not target.alive else 0.0)
            value += max(0.0, mana - target.current_mana) * 14.0
            value += max(0, shields - target.total_shields()) * 20.0 + max(0, dodges - target.dodge_charges) * 12.0
            value += sum(max(0.0, stats[name] - target.stat(name)) * weight
                         for name, weight in (("attack", 24.0), ("defense", 30.0), ("speed", 14.0)))
            value = value if target.player_id != actor.player_id else -value * 1.3
            return value + max(0.0, actor.current_mana - own_mana) * 26.0 - 4.0


    def _ai_reachable_coordinates(self, battle: Battle, unit: Unit, **options):
        return tuple((cell.x, cell.y) for cell in battle.reachable_positions(unit, **options))


    def ai_reachable_positions(self, battle: Battle, unit: Unit, **options) -> list[Position]:
        if (not tactical_queries_active() or type(battle) is not Battle
                or any(name in battle.__dict__ for owner, name, _, _ in self.services._TACTICAL_QUERY_BINDINGS if owner is Battle)
                or any(getattr(owner, name) is not original or original.__code__ is not original_code
                       for owner, name, original, original_code in self.services._TACTICAL_QUERY_BINDINGS)):
            return battle.reachable_positions(unit, **options)
        return [Position(x, y) for x, y in self.services._ai_reachable_coordinates(battle, unit, **options)]


    def r21_unit_attack_opportunity(self, battle: Battle, unit: Unit) -> float:
        """The original ordered attack reduction, inside the caller's rollback."""
        origin = unit.position
        destinations = [origin]
        if not unit.cannot_move and not unit.cannot_normal_move and unit.remaining_normal_move_distance(battle) > 0:
            destinations += self.services.ai_reachable_positions(battle, unit, max_distance=unit.remaining_normal_move_distance(battle), use_movement_cost=True)
        best_attack = 0.0
        if not unit.cannot_attack and unit.attacks_used < unit.attack_actions_per_turn():
            for target in battle.effect_units(battle.enemy_units(unit.player_id)):
                if not battle.unit_can_be_selected(target, actor=unit)[0]:
                    continue
                for destination in sorted(set(destinations), key=lambda cell: cell.distance_to(target.position)):
                    unit.position = destination
                    attack_payload = self.services.legal_attack_payload(battle, unit, target)
                    if attack_payload is None:
                        continue
                    best_attack = max(best_attack, self.services.r21_attack_value(battle, unit, target, attack_payload))
                    break
        unit.position = origin
        return best_attack * max(0, unit.attack_actions_per_turn() - unit.attacks_used)


    def r21_unit_action_opportunities(self, battle: Battle, unit: Unit, profile: DifficultyProfile) -> float:
        """Price legal attacks and useful ready active/instant casts in the unit's action window."""
        if not unit.alive or unit.banished or unit.position is None:
            return 0.0
        with ai_probe_rollback(battle):
            future = not unit.can_take_turn_actions(battle)
            battle.resolving_action = None
            battle.active_player = unit.player_id
            battle._exclusive_turn_unit_id = unit.unit_id
            unit.turn_ready = True
            if future:
                unit.attacks_used = unit.normal_move_actions_used = unit.normal_move_steps_used = 0
                unit.move_used = False
                for skill in unit.skills:
                    skill.uses_this_turn = 0
            value = self.services.r21_unit_attack_opportunity(battle, unit)
            for skill in unit.skills:
                if skill.timing not in {"active", "instant"} or skill.code in {"eagle_eye", "vain_giant_shadow"}:
                    continue
                if not skill.can_use(battle, unit, {})[0]:
                    continue
                action = self.services.copied_skill_action(battle, unit, skill)
                payloads = self.services.skill_payloads_for_action(battle, unit, action)
                if any(payload.get("cells") for payload in payloads):
                    payloads = self.services.dedupe_damage_area_payloads(battle, payloads)
                best = 0.0
                for payload in self.services.trim_skill_payloads_for_ai(battle, unit, payloads, limit=16):
                    best = max(best, self.services.reviewed_r18_effect_score(battle, unit, skill, payload, profile))
                value += best
            return value


    def r21_control_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        with ai_probe_rollback(battle):
            targets = battle.effect_units(skill.get_target_units_for_payload(battle, actor, payload))
            if not targets:
                return -1000.0
            if skill.code == "vain_giant_shadow" and actor in targets and not actor.cannot_attack:
                preview = battle.basic_attack_preview_for_payload(actor)
                if any(self.services.r21_attack_value(battle, actor, battle.get_unit(unit_id), {"target_unit_id": unit_id}) > 0
                       for unit_id in preview.get("target_unit_ids", [])):
                    return -1000.0
            before = {unit.unit_id: self.services.r21_unit_action_opportunities(battle, unit, profile) for unit in targets}
            status_name = "鹰眼" if skill.code == "eagle_eye" else "虚荣巨影"
            refresh = {}
            for unit in targets:
                existing = unit.get_status(status_name)
                if existing is not None:
                    with ai_probe_rollback(battle):
                        remaining = existing.duration or 0
                        unit.remove_status(existing, battle)
                        plain = self.services.r21_unit_action_opportunities(battle, unit, profile)
                        refresh[unit.unit_id] = (before[unit.unit_id] - plain, remaining)
            score = self.services.reviewed_r18_effect_score(battle, actor, skill, payload, profile)
            try:
                queued = battle.build_queued_action(payload)
                battle.prepay_skill_resources(skill, actor, queued.payload)
                battle.resolving_action = queued
                skill.execute(battle, actor, queued.payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            for unit in targets:
                if not unit.has_status(status_name):
                    continue
                delta = self.services.r21_unit_action_opportunities(battle, unit, profile) - before[unit.unit_id]
                if unit.unit_id in refresh:
                    effect_delta, old_duration = refresh[unit.unit_id]
                    extra = max(0, (unit.get_status(status_name).duration or 0) - old_duration)
                    delta += effect_delta * min(0.5, extra * 0.25)
                if unit.player_id == actor.player_id:
                    score += delta * (1.3 if delta < 0 else 1.0)
                else:
                    score -= delta * (1.3 if delta > 0 else 1.0)
            return score


    def r21_winged_contact_value(self, battle: Battle, actor: Unit, *, allow_leap: bool = True, allow_move: bool = True) -> float:
        if actor.position is None or actor.cannot_attack or actor.attacks_used >= actor.attack_actions_per_turn():
            return 0.0
        with ai_probe_rollback(battle):
            origin = actor.position
            destinations = {origin}
            if allow_move and not actor.cannot_move and not actor.cannot_normal_move and actor.remaining_normal_move_distance(battle) > 0:
                destinations.update(self.services.ai_reachable_positions(battle, actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True))
            leap = actor.get_skill("fly_leap")
            if allow_leap and leap.can_use(battle, actor, {})[0]:
                landings = set()
                for start in list(destinations):
                    actor.position = start
                    landings.update(self.services.preview_positions(leap.preview(battle, actor).get("cells")))
                destinations.update(landings)
                if allow_move and not actor.cannot_normal_move:
                    for landing in landings:
                        actor.position = landing
                        destinations.update(self.services.ai_reachable_positions(battle, actor, max_distance=actor.normal_move_distance(), use_movement_cost=True))
            best = 0.0
            for target in battle.effect_units(battle.enemy_units(actor.player_id)):
                if not battle.unit_can_be_selected(target, actor=actor)[0]:
                    continue
                for cell in sorted(destinations, key=lambda position: position.distance_to(target.position)):
                    actor.position = cell
                    if battle.attack_target_allowed(actor, target)[0]:
                        best = max(best, self.services.r21_attack_value(battle, actor, target, {"target_unit_id": target.unit_id}))
                        break
            return best


    def r21_winged_leap_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        before = self.services.r21_winged_contact_value(battle, actor, allow_leap=False)
        with ai_probe_rollback(battle):
            try:
                skill = actor.get_skill("fly_leap")
                queued = battle.build_queued_action(payload)
                battle.prepay_skill_resources(skill, actor, queued.payload)
                skill.execute(battle, actor, queued.payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            after = self.services.r21_winged_contact_value(battle, actor, allow_leap=False)
            return after - before - 8.0


    def r21_barrier_position_value(self, battle: Battle, actor: Unit) -> float:
        from wujiang.tactical.heroes.mechanics.electronic_barrier import ElectronicBarrierAuraField
        fields = [effect for effect in battle.field_effects if isinstance(effect, ElectronicBarrierAuraField) and effect.source_unit_id == actor.unit_id]
        cells = {cell for effect in fields for cell in effect.affected_cells(battle)}
        value = 0.0
        for target in battle.effect_units_at_cells(list(cells)):
            best = 0.0
            for attacker in battle.effect_units(battle.enemy_units(target.player_id)):
                if attacker.position is None or not battle.unit_can_be_selected(target, actor=attacker)[0]:
                    continue
                with ai_probe_rollback(battle):
                    origins = [attacker.position]
                    if not attacker.cannot_move and not attacker.cannot_normal_move:
                        origins += self.services.ai_reachable_positions(battle, attacker, max_distance=attacker.normal_move_distance(), use_movement_cost=True)
                    for origin in sorted(set(origins), key=lambda cell: cell.distance_to(target.position)):
                        attacker.position = origin
                        if not battle.attack_target_allowed(attacker, target)[0]:
                            continue
                        declared = battle.declared_cell_for_target(attacker, target, {"type": "attack", "unit_id": attacker.unit_id})
                        if declared is None:
                            continue
                        payload = {"type": "attack", "unit_id": attacker.unit_id,
                                   "target_unit_id": target.unit_id, "x": declared.x, "y": declared.y}
                        power = battle.basic_attack_preview_power(attacker, payload)
                        protected = probe_attack_damage_impact(battle, attacker, target, payload, attack_power=power).damage
                        battle.field_effects = [effect for effect in battle.field_effects if effect not in fields]
                        unprotected = probe_attack_damage_impact(battle, attacker, target, payload, attack_power=power).damage
                        best = max(best, min(target.current_hp, unprotected) - min(target.current_hp, protected))
                        break
            value += best * (100.0 if target.player_id == actor.player_id else -130.0)
        return value


    def r21_queued_loss(self, battle: Battle, target: Unit, queued: QueuedAction, *, observed_targets=None) -> float:
        target = battle.effect_recipient(target)
        targets = (battle.effect_units(observed_targets)
                   if type(observed_targets) in (list, tuple) and len(observed_targets) > 1 and joint_utilities_enabled() else [])
        if (len(targets) > 1 and any(unit is target for unit in targets)
                and all(battle.units.get(unit.unit_id) is unit for unit in targets)):
            # Each original query replays the entire action, even though it only
            # observes ONE target. Observe all recipients from the same joint
            # outcomes once; keep death/control utility nonlinear within each leaf.
            try:
                values = expected_vector(battle, lambda: self.services._r21_queued_loss_expectations(battle, targets, queued),
                    cache_request=("queued_losses", id(self.services._r21_queued_loss_vector.__code__),
                                   id(self.services._r21_queued_loss_expectations.__code__),
                                   id(unit_impact_signature), id(getattr(unit_impact_signature, "__code__", None)),
                                   id(Battle.resolve_queued_action), id(getattr(Battle.resolve_queued_action, "__code__", None)),
                                   tuple((id(type(unit).stat), id(getattr(type(unit).stat, "__code__", None))) for unit in targets),
                                   queued, tuple(targets)))
            except UnstableObservation:
                disable_joint_utilities()
            else:
                note("queued_loss_targets_served")
                return values[next(index for index, unit in enumerate(targets) if unit is target)]
        if forecasting_active() and joint_utilities_enabled():
            # A scalar observer of an area action must not enumerate every other
            # recipient's dodge either. Non-area/uncertified actions keep the old
            # scalar path and its observation semantics.
            from wujiang.tactical.prediction_factors import area_query
            if area_query(queued) and battle.units.get(target.unit_id) is target:
                try:
                    return expected_vector(battle, lambda: self.services._r21_queued_loss_expectations(battle, [target], queued),
                        cache_request=("scalar_area_loss", id(self.services._r21_queued_loss_expectations.__code__), queued, target))[0]
                except UnstableObservation:
                    disable_joint_utilities()
        with ai_probe_rollback(battle):
            hp, mana = target.current_hp, target.current_mana
            stats = {name: target.stat(name) for name in ("attack", "defense", "speed")}
            before = unit_impact_signature(target)
            try:
                battle.resolve_queued_action(deepcopy(queued))
            except (ActionError, KeyError, TypeError, ValueError):
                return 0.0
            value = max(0.0, hp - target.current_hp) * 100.0 + max(0.0, mana - target.current_mana) * 24.0
            value += sum(max(0.0, stats[name] - target.stat(name)) * weight
                         for name, weight in (("attack", 24.0), ("defense", 30.0), ("speed", 14.0)))
            if hp > 0 and self.services.scored_death_probability(target) > 0:
                value += (90.0 * self.services.scored_death_probability(target) if direct_scoring() else 90.0)
            if value <= 0 and unit_impact_signature(target) != before and target.alive:
                # A real new control status counts, consuming only a shield does not.
                if any(status.name in {"鹰眼", "虚荣巨影", "雪崩", "毁灭电击", "电风"} for status in target.statuses):
                    value += 30.0
            return value


    def _r21_queued_loss_expectations(self, battle: Battle, targets: list[Unit], queued: QueuedAction) -> tuple[float, ...]:
        from wujiang.tactical.prediction_factors import expected_area_utilities
        utility_key = tuple((id(function), id(getattr(function, "__code__", None))) for function in
                            (self.services._r21_queued_loss_vector, unit_impact_signature, status_impact_signature,
                             Battle.resolve_queued_action))
        utility_key += (tuple(sorted({(id(type(target).stat), id(getattr(type(target).stat, "__code__", None)))
                                     for target in targets})),)
        values = expected_area_utilities(battle, targets, queued,
            lambda recipients: self.services._r21_queued_loss_vector(battle, recipients, queued),
            utility_key=utility_key)
        if values is not None:
            return values
        # Finish the whole fallback expectation HERE. Returning a raw stochastic
        # vector would make the outer enumerator re-enter certification at every
        # prefix/leaf of its joint tree, although its initial input is identical.
        return expected_vector(battle, lambda: self.services._r21_queued_loss_vector(battle, targets, queued),
            cache_request=("joint_loss_after_factor_fallback", utility_key, queued, tuple(targets)))


    def _r21_queued_loss_vector(self, battle: Battle, targets: list[Unit], queued: QueuedAction) -> tuple[float, ...]:
        with ai_probe_rollback(battle):
            with stable_observations(battle):
                before = [(target.current_hp, target.current_mana,
                           {name: target.stat(name) for name in ("attack", "defense", "speed")},
                           unit_impact_signature(target)) for target in targets]
            try:
                battle.resolve_queued_action(deepcopy(queued))
            except (ActionError, KeyError, TypeError, ValueError):
                return (0.0,) * len(targets)
            with stable_observations(battle):
                values = []
                for target, (hp, mana, stats, signature) in zip(targets, before):
                    value = max(0.0, hp - target.current_hp) * 100.0 + max(0.0, mana - target.current_mana) * 24.0
                    value += sum(max(0.0, stats[name] - target.stat(name)) * weight
                                 for name, weight in (("attack", 24.0), ("defense", 30.0), ("speed", 14.0)))
                    if hp > 0 and self.services.scored_death_probability(target) > 0:
                        value += (90.0 * self.services.scored_death_probability(target) if direct_scoring() else 90.0)
                    if value <= 0 and unit_impact_signature(target) != signature and target.alive:
                        if any(status.name in {"鹰眼", "虚荣巨影", "雪崩", "毁灭电击", "电风"} for status in target.statuses):
                            value += 30.0
                    values.append(value)
            return tuple(values)


    def r21_barrier_shield_score(self, battle: Battle, actor: Unit, queued: QueuedAction, payload: dict[str, Any], code: str) -> float:
        """Compatibility entry point; shield choice is owned by Jade's policy."""
        result = BarrierShieldAIPolicy().contextual_reaction_score(AIReactionScoreContext(
            battle, actor, queued, {"action_code": code}, payload, None,
            self.services._SHARED_REACTION_SCORING))
        return result if result is not None else -1000.0


    def barrier_prevention_score(self, battle: Battle, actor: Unit, queued: QueuedAction,
                                 payload: dict[str, Any], code: str) -> float:
        skill = actor.get_skill(code)
        if not skill.can_react_with_payload(battle, actor, queued, payload)[0]:
            return -1000.0
        targets = battle.effect_units(skill.chosen_targets(battle, actor, payload))
        before = {target.unit_id: self.services.r21_queued_loss(battle, target, queued, observed_targets=targets) for target in targets}
        with ai_probe_rollback(battle):
            skill.react(battle, actor, payload, queued)
            prevention = sum(max(0.0, before[target.unit_id] - self.services.r21_queued_loss(battle, target, queued, observed_targets=targets)) for target in targets)
        if prevention <= 0:
            return -1000.0
        return prevention


    def purify_mana_score(self,
        battle: Battle,
        actor: Unit,
        payload: dict[str, Any],
        targets: list[Unit],
        profile: DifficultyProfile,
    ) -> float:
        target = self.services.primary_target_unit(battle, payload, targets)
        if target is None or target.player_id == actor.player_id:
            return -100.0
        if target.total_shields() > 0:
            return 48.0 + self.services.hostile_unit_value(target) * 0.12
        drained = min(5.0, target.current_mana)
        if drained <= 0:
            return -100.0
        return drained * 34.0 + self.services.hostile_unit_value(target) * 0.18 + profile.aggressive_bonus


    def sacred_duel_score(self,
        battle: Battle,
        actor: Unit,
        payload: dict[str, Any],
        targets: list[Unit],
        profile: DifficultyProfile,
    ) -> float:
        target = self.services.primary_target_unit(battle, payload, targets)
        if target is None or target.player_id == actor.player_id or target.has_status("神圣决斗"):
            return -100.0
        active_skills = sum(1 for skill in target.skills if skill.timing == "active")
        mobility = max(0.0, target.stat("speed")) * 8.0
        return 72.0 + active_skills * 18.0 + mobility + self.services.hostile_unit_value(target) * 0.24 + profile.aggressive_bonus


_BOUND_SERVICE: AIOpportunityScoring | None = None

def bind_room(service: AIOpportunityScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AIOpportunityScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

@expected_numeric(independent=True)
def beetle_armor_reaction_score(battle: Battle, reactor: Unit, queued_action: QueuedAction, profile: DifficultyProfile) -> float:
    """Compatibility entry point for the beetle skill's reaction estimate."""
    return _require_service().beetle_armor_reaction_score(battle, reactor, queued_action, profile)


@expected_numeric(independent=True)
def r21_unit_action_opportunities(battle: Battle, unit: Unit, profile: DifficultyProfile) -> float:
    """Price legal attacks and useful ready active/instant casts in the unit's action window."""
    return _require_service().r21_unit_action_opportunities(battle, unit, profile)


@expected_numeric(independent=True)
def r21_control_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().r21_control_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def r21_winged_contact_value(battle: Battle, actor: Unit, *, allow_leap: bool = True, allow_move: bool = True) -> float:
    return _require_service().r21_winged_contact_value(battle, actor, allow_leap=allow_leap, allow_move=allow_move)


@expected_numeric(independent=True)
def r21_winged_leap_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().r21_winged_leap_score(battle, actor, payload)


@expected_numeric(independent=True)
def r21_barrier_position_value(battle: Battle, actor: Unit) -> float:
    return _require_service().r21_barrier_position_value(battle, actor)


@expected_numeric(independent=True)
def r21_queued_loss(battle: Battle, target: Unit, queued: QueuedAction, *, observed_targets=None) -> float:
    return _require_service().r21_queued_loss(battle, target, queued, observed_targets=observed_targets)


def _r21_queued_loss_expectations(battle: Battle, targets: list[Unit], queued: QueuedAction) -> tuple[float, ...]:
    return _require_service()._r21_queued_loss_expectations(battle, targets, queued)


def _r21_queued_loss_vector(battle: Battle, targets: list[Unit], queued: QueuedAction) -> tuple[float, ...]:
    return _require_service()._r21_queued_loss_vector(battle, targets, queued)


@expected_numeric(independent=True)
def r21_barrier_shield_score(battle: Battle, actor: Unit, queued: QueuedAction, payload: dict[str, Any], code: str) -> float:
    """Compatibility entry point; shield choice is owned by Jade's policy."""
    return _require_service().r21_barrier_shield_score(battle, actor, queued, payload, code)


def barrier_prevention_score(battle: Battle, actor: Unit, queued: QueuedAction,
                             payload: dict[str, Any], code: str) -> float:
    return _require_service().barrier_prevention_score(battle, actor, queued, payload, code)


@expected_numeric(independent=True)
def purify_mana_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    return _require_service().purify_mana_score(battle, actor, payload, targets, profile)


@expected_numeric(independent=True)
def sacred_duel_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    return _require_service().sacred_duel_score(battle, actor, payload, targets, profile)
