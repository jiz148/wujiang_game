"""AILateTacticalScoring: shared AI scoring and queries for late."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from copy import deepcopy
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

class AILateTacticalScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    _r23_expected_settled_value: Callable[..., Any]
    _skill_score_context: Callable[..., Any]
    ai_reachable_positions: Callable[..., Any]
    attack_payloads_for_action: Callable[..., Any]
    difficulty_profile: Callable[..., Any]
    distance_between_units: Callable[..., Any]
    distance_to_position: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    natsume_unit_danger: Callable[..., Any]
    paid_reaction_score: Callable[..., Any]
    r21_attack_value: Callable[..., Any]
    r21_queued_loss: Callable[..., Any]
    r22_attack_value: Callable[..., Any]
    r22_contact_value: Callable[..., Any]
    r23_action_delta: Callable[..., Any]
    r23_action_state: Callable[..., Any]
    r23_attack_value: Callable[..., Any]
    r23_passive_defense_value: Callable[..., Any]
    reaction_payloads_for_option: Callable[..., Any]
    reviewed_r17_position_value: Callable[..., Any]
    scored_death_probability: Callable[..., Any]
    simulated_reaction_score: Callable[..., Any]


class AILateTacticalScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AILateTacticalScoringServices) -> None:
        self.services = services

    def r23_action_state(self, battle: Battle) -> dict[str, tuple[Unit, float, float, int, int, bool, dict[str, float]]]:
        return {unit.unit_id: (unit, unit.current_hp, unit.current_mana, unit.total_shields(), unit.dodge_charges,
                              unit.alive, {name: unit.stat(name) for name in ("attack", "defense", "speed")})
                for unit in battle.all_units()}


    def r23_action_delta(self, actor: Unit, before: dict[str, Any]) -> float:
        """Price actual outcomes for all recipients, including friendly losses."""
        score = 0.0
        for unit, hp, mana, shields, dodges, alive, stats in before.values():
            loss = (hp - unit.current_hp) * 100.0 + (mana - unit.current_mana) * 18.0
            loss += (shields - unit.total_shields()) * 20.0 + (dodges - unit.dodge_charges) * 12.0
            loss += sum((stats[name] - unit.stat(name)) * weight
                        for name, weight in (("attack", 24.0), ("defense", 30.0), ("speed", 14.0)))
            if alive and not unit.alive:
                loss += 90.0
            elif alive and direct_scoring():
                from wujiang.tactical.direct_expectation import expected_death
                loss += expected_death(unit) * 90.0
            if direct_scoring():
                loss += getattr(unit, "_ai_expected_banish_probability", 0.0) * self.services.hostile_unit_value(unit)
            score += loss if unit.player_id != actor.player_id else -loss * 1.3
        return score


    def r23_attack_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        with ai_probe_rollback(battle):
            battle._forecasting_action = True
            battle.pending_followup_actions.clear()
            before = self.services.r23_action_state(battle)
            try:
                queued = battle.build_queued_action({"type": "attack", "unit_id": actor.unit_id, **payload})
                battle.resolve_queued_action(queued)
                while battle.pending_followup_actions:
                    battle.resolve_queued_action(battle.pending_followup_actions.popleft())
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            return self.services.r23_action_delta(actor, before) - 4.0


    def electronic_repair_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        skill = actor.get_skill("electronic_repair")
        return skill.ai_priority_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code}, payload, self.services.difficulty_profile("standard")))


    def r23_paid_skill_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        """Use the same outcome scale as Kiku's attacks, including actual prepaid mana."""
        with ai_probe_rollback(battle):
            battle._forecasting_action = True
            battle.pending_followup_actions.clear()
            before = self.services.r23_action_state(battle)
            try:
                queued = battle.build_queued_action({"type": "skill", "unit_id": actor.unit_id, **payload})
                skill = actor.get_skill(queued.payload["skill_code"])
                battle.prepay_skill_resources(skill, actor, queued.payload)
                queued.payload["resources_prepaid"] = True
                actor.notify_action_declared(battle, "skill", queued.payload)
                queued.payload["declared_source_attack"] = actor.stat("attack")
                battle.resolve_queued_action(queued)
                if (forecasting_active() and not direct_scoring() and len(battle.pending_followup_actions) == 1
                        and not battle.field_effects and not getattr(battle, "special_mode_codes", ())):
                    from wujiang.tactical.prediction_factors import area_query
                    if area_query(battle.pending_followup_actions[0]):
                        return self.services._r23_expected_settled_value(battle, actor, before)
                while battle.pending_followup_actions:
                    battle.resolve_queued_action(battle.pending_followup_actions.popleft())
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            return self.services.r23_action_delta(actor, before) - 8.0


    def _r23_expected_settled_value(self, battle: Battle, actor: Unit, before: dict[str, Any]) -> float:
        from math import fsum
        from wujiang.tactical.prediction import outcomes, UnstableObservation
        from wujiang.tactical.prediction_factors import expected_settled_area_utilities
        queued = battle.pending_followup_actions[0]
        targets = [item[0] for item in before.values()]
        key = (id(self.services.r23_action_delta.__code__), id(self.services._r23_expected_settled_value.__code__), actor, before)
        try:
            # Impure certification must unwind before the complete-tree fallback.
            with ai_probe_rollback(battle):
                values = expected_settled_area_utilities(battle, targets, queued,
                    lambda group: tuple(self.services.r23_action_delta(actor, {unit.unit_id: before[unit.unit_id]}) for unit in group),
                    utility_key=key)
            if values is not None:
                return sum(values) - 8.0
        except UnstableObservation:
            note("settled_factorization_unsafe_inspection")
        def complete_value():
            try:
                while battle.pending_followup_actions:
                    battle.resolve_queued_action(battle.pending_followup_actions.popleft())
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            return self.services.r23_action_delta(actor, before) - 8.0
        # Finish the fallback expectation HERE, so a rejected certificate is not
        # retried at every leaf of the enclosing scalar query.
        return fsum(value * probability for value, probability in outcomes(battle, complete_value,
            cache_request=("r23_complete_settled_value", key, queued), eager_cache=True))


    def r215219_skill_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        """Replay both sides, then price the actual landing and short attack window."""
        with ai_probe_rollback(battle):
            battle._forecasting_action = True
            battle.pending_followup_actions.clear()
            before = self.services.r23_action_state(battle)
            positions = {unit.unit_id: unit.position for unit in battle.all_units()}
            position_value = {unit.unit_id: self.services.reviewed_r17_position_value(battle, actor, unit)
                              for unit in battle.all_units()}
            try:
                queued = battle.build_queued_action({"type": "skill", "unit_id": actor.unit_id, **payload})
                skill = actor.get_skill(queued.payload["skill_code"])
                battle.prepay_skill_resources(skill, actor, queued.payload)
                queued.payload["resources_prepaid"] = True
                actor.notify_action_declared(battle, "skill", queued.payload)
                battle.resolve_queued_action(queued)
                while battle.pending_followup_actions:
                    battle.resolve_queued_action(battle.pending_followup_actions.popleft())
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            code = str(payload.get("skill_code") or "")
            score = self.services.r23_action_delta(actor, before) - 8.0
            if code.startswith("bird_soul"):
                old_stats = before[actor.unit_id][6]
                attack_gain = max(0.0, actor.stat("attack") - old_stats["attack"])
                defense_gain = max(0.0, actor.stat("defense") - old_stats["defense"])
                enemies = [unit for unit in living_hostile_combatants(battle, actor.player_id)
                           if unit.position is not None and battle.unit_can_be_selected(unit, actor=actor)[0]]
                nearest = min((self.services.distance_between_units(battle, actor, unit) for unit in enemies), default=10**6)
                skill_reach = max(actor.targeting_range(), 3)
                if nearest > skill_reach + actor.normal_move_distance():
                    score -= attack_gain * 24.0 * 1.3
                elif nearest > skill_reach:
                    score -= attack_gain * 24.0 * 1.3 * 0.5
                threatened = any(not enemy.cannot_attack and
                                 self.services.distance_between_units(battle, enemy, actor)
                                 <= (0 if enemy.cannot_move or enemy.cannot_normal_move
                                     else enemy.normal_move_distance()) + enemy.targeting_range()
                                 for enemy in enemies)
                if not threatened:
                    score -= defense_gain * 30.0 * 1.3
            for unit in battle.all_units():
                if unit.unit_id not in positions or unit.position == positions[unit.unit_id]:
                    continue
                gain = self.services.reviewed_r17_position_value(battle, actor, unit) - position_value[unit.unit_id]
                score += gain * (0.8 if unit.player_id == actor.player_id else -0.8)
            if code.startswith("bird_dash") and score <= 0:
                return -1000.0
            return score


    def r23_passive_defense_value(self, battle: Battle, defender: Unit) -> float:
        """At most one next legal prevention opportunity; names alone add no value."""
        if not defender.alive or defender.position is None or defender.banished:
            return 0.0
        with ai_probe_rollback(battle):
            battle._forecasting_action = True
            defender.statuses = [status for status in defender.statuses if status.name != "被动封锁"]
            battle.resolving_action = None
            best = 0.0
            for attacker in living_hostile_combatants(battle, defender.player_id):
                with ai_probe_rollback(battle):
                    battle.turn_number += 1
                    battle.active_player = attacker.player_id
                    battle._exclusive_turn_unit_id = attacker.unit_id
                    attacker.turn_ready = True
                    attacker.attacks_used = attacker.normal_move_steps_used = attacker.normal_move_actions_used = 0
                    attacker.move_used = False
                    if attacker.cannot_attack:
                        continue
                    origin = attacker.position
                    positions = [origin]
                    if not attacker.cannot_move and not attacker.cannot_normal_move:
                        positions += self.services.ai_reachable_positions(battle, attacker, max_distance=attacker.remaining_normal_move_distance(battle), use_movement_cost=True)
                    for position in sorted(positions, key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                        attacker.position = position
                        try:
                            queued = battle.build_queued_action({"type": "attack", "unit_id": attacker.unit_id,
                                                               "target_unit_id": defender.unit_id})
                        except (ActionError, KeyError, TypeError, ValueError):
                            continue
                        loss = self.services.r21_queued_loss(battle, defender, queued)
                        if loss <= 0:
                            break
                        for option in battle.available_reaction_options(defender, queued):
                            if option.action_type != "skill" or option.timing not in {"passive", "reaction"}:
                                continue
                            skill = defender.get_skill(option.action_code)
                            reactions = self.services.reaction_payloads_for_option(battle, defender, queued, option.to_public_dict())
                            if not reactions and not option.preview.get("requires_target", True):
                                reactions = [{"type": "chain_react", "unit_id": defender.unit_id, "action_code": option.action_code}]
                            for reaction in reactions:
                                if not skill.can_react_with_payload(battle, defender, queued, reaction)[0]:
                                    continue
                                with ai_probe_rollback(battle):
                                    cost = skill.mana_cost_for_payload(battle, defender, reaction)
                                    hp, alive = defender.current_hp, defender.alive
                                    defender.spend_mana(cost)
                                    skill.react(battle, defender, reaction, deepcopy(queued))
                                    saved = loss - self.services.r21_queued_loss(battle, defender, queued)
                                    saved -= max(0.0, hp - defender.current_hp) * 100.0
                                    saved -= 90.0 if alive and not defender.alive else 0.0
                                    best = max(best, saved - cost * 18.0)
                        break
            return max(0.0, best)


    def r23_sun_slash_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
        selected = battle.units.get(str(payload.get("target_unit_id") or ""))
        if selected is None:
            return -1000.0
        target = battle.effect_recipient(selected)
        with ai_probe_rollback(battle):
            battle._forecasting_action = True
            before = self.services.r23_action_state(battle)
            old_lock = target.get_status("被动封锁")
            old_ends = int(old_lock.duration or 0) if old_lock is not None else 0
            try:
                actor.spend_mana(skill.mana_cost_for_payload(battle, actor, payload))
                skill.execute(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            value = self.services.r23_action_delta(actor, before)
            lock = target.get_status("被动封锁")
            if lock is not None and target.alive:
                added = max(0, int(lock.duration or 0) - old_ends)
                prevention = self.services.r23_passive_defense_value(battle, target) * min(1.0, added / 3.0)
                value += prevention if target.player_id != actor.player_id else -prevention * 1.3
            # Preserve the finite ultimate when ordinary double attacks have equal value.
            return value - 18.0


    def r23_attack_plan_value(self, battle: Battle, actor: Unit) -> float:
        if not actor.can_take_turn_actions(battle) or actor.cannot_attack:
            return 0.0
        best = 0.0
        for spec in battle.basic_attack_action_specs(actor):
            resolved = battle.resolved_basic_attack_payload(actor, spec.get("attack_payload"))
            if not battle.basic_attack_resource_allowed(actor, resolved)[0]:
                continue
            action = {**spec, "preview": battle.basic_attack_preview_for_payload(actor, resolved)}
            for payload in self.services.attack_payloads_for_action(battle, actor, action):
                best = max(best, self.services.r23_attack_value(battle, actor, payload))
        return best


    def r22_attack_value(self, battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> float:
        value = self.services.r21_attack_value(battle, actor, target, payload)
        if actor.hero_code != "excel_r352" or value <= 0 or target.player_id == actor.player_id:
            return value
        with ai_probe_rollback(battle):
            previous = {unit.unit_id for unit in battle.all_units()}
            ctx = battle.resolve_attack_damage(actor, target, action_name="普攻", payload=payload)
            if ctx is None:
                return value
            original_position = actor.position
            risk_before = self.services.natsume_unit_danger(battle, actor)
            actor.notify_basic_attack_finished(battle, payload, [ctx])
            new_clones = [unit for unit in battle.all_units() if unit.unit_id not in previous
                          and unit.is_clone and unit.summoner_id == actor.unit_id]
            if new_clones and any(self.services.distance_between_units(battle, new_clones[0], enemy)
                                  <= enemy.normal_move_distance() + enemy.targeting_range()
                                  for enemy in living_hostile_combatants(battle, actor.player_id)):
                value += 8.0
            if actor.position is not None and original_position is not None:
                swap_positions = {actor.position}
                swap_positions.update(unit.position for unit in battle.all_units()
                                      if unit.is_clone and unit.summoner_id == actor.unit_id
                                      and unit.alive and not unit.banished and unit.position is not None
                                      and unit.position != original_position)
                risks = []
                for position in swap_positions:
                    actor.position = position
                    risks.append(self.services.natsume_unit_danger(battle, actor))
                value += 0.7 * (risk_before - sum(risks) / len(risks))
            return value


    def r22_contact_value(self, battle: Battle, actor: Unit, *, allow_move: bool = True) -> float:
        if not actor.alive or actor.position is None or actor.cannot_attack or actor.attacks_used >= actor.attack_actions_per_turn():
            return 0.0
        with ai_probe_rollback(battle):
            origin = actor.position
            positions = [origin]
            if allow_move and not actor.cannot_move and not actor.cannot_normal_move:
                positions += self.services.ai_reachable_positions(battle, actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
            best = 0.0
            for target in living_hostile_combatants(battle, actor.player_id):
                if not battle.unit_can_be_selected(target, actor=actor)[0]:
                    continue
                for position in sorted(positions, key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                    actor.position = position
                    if battle.attack_target_allowed(actor, target)[0]:
                        best = max(best, self.services.r22_attack_value(battle, actor, target, {"target_unit_id": target.unit_id}))
                        break
            return max(0.0, best)


    def r22_gazer_window_risk(self, battle: Battle, actor: Unit) -> float:
        if not actor.alive or actor.position is None:
            return 0.0
        with ai_probe_rollback(battle):
            hp = actor.current_hp
            battle.resolving_action = None
            for enemy in list(living_hostile_combatants(battle, actor.player_id)):
                if not actor.alive or enemy.cannot_attack:
                    continue
                battle.turn_number += 1
                battle.active_player = enemy.player_id
                battle._exclusive_turn_unit_id = enemy.unit_id
                enemy.turn_ready = True
                enemy.attacks_used = enemy.normal_move_actions_used = enemy.normal_move_steps_used = 0
                enemy.move_used = False
                positions = [enemy.position]
                if not enemy.cannot_move and not enemy.cannot_normal_move:
                    positions += self.services.ai_reachable_positions(battle, enemy, max_distance=enemy.remaining_normal_move_distance(battle), use_movement_cost=True)
                for position in sorted(positions, key=lambda cell: (self.services.distance_to_position(battle, actor, cell), cell.y, cell.x)):
                    enemy.position = position
                    if not battle.attack_target_allowed(enemy, actor)[0]:
                        continue
                    # Repeated attacks share this hero window; each later enemy hero has a fresh window.
                    for _ in range(enemy.attack_actions_per_turn()):
                        if not actor.alive:
                            break
                        battle.resolve_attack_damage(enemy, actor, action_name="普攻")
                    break
            loss = max(0.0, hp - actor.current_hp)
            if actor.alive:
                before = actor.current_hp
                battle.heal(HealContext(source=actor, target=actor, amount=0.25, action_name="自然回血"))
                loss = max(0.0, loss - (actor.current_hp - before))
            return loss * 100.0 + (90.0 if not actor.alive else 0.0)


    def r22_ninja_shensu_score(self, battle: Battle, actor: Unit) -> float:
        skill = actor.get_skill("shensu")
        if not skill.can_use(battle, actor, {})[0] or actor.move_used or actor.cannot_normal_move or actor.cannot_move:
            return -1000.0
        before = self.services.r22_contact_value(battle, actor)
        with ai_probe_rollback(battle):
            cost = skill.mana_cost_for_payload(battle, actor, {})
            actor.spend_mana(cost)
            skill.execute(battle, actor, {})
            skill.finalize_use(battle, actor)
            after = self.services.r22_contact_value(battle, actor)
            reserve = 0.0
            if actor.current_mana < 0.5 and any(self.services.distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range()
                                               for enemy in living_hostile_combatants(battle, actor.player_id)):
                reserve = 20.0
            return after - before - cost * 18.0 - reserve


    def r22_heal_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
        selected = battle.units.get(str(payload.get("target_unit_id") or ""))
        if selected is None:
            return -1000.0
        target = battle.effect_recipient(selected)
        with ai_probe_rollback(battle):
            hp, mana, alive = target.current_hp, actor.current_mana, target.alive
            actor.spend_mana(skill.mana_cost_for_payload(battle, actor, payload))
            skill.execute(battle, actor, payload)
            delta = target.current_hp - hp
            value = delta * 200.0 + (50.0 if delta > 0 and hp <= 0.5 else 0.0)
            value -= (mana - actor.current_mana) * 18.0
            if alive and self.services.scored_death_probability(target) > 0:
                value -= (90.0 * self.services.scored_death_probability(target) if direct_scoring() else 90.0)
            return value


    def paid_reaction_score(self, battle: Battle, actor: Unit, queued: QueuedAction,
                            payload: dict[str, Any], skill: Any,
                            multi_target: bool) -> float:
        """Simulate a paid defense against its actual affected recipients."""
        if not skill.can_react_with_payload(battle, actor, queued, payload)[0]:
            return -1000.0
        targets = (battle.effect_units(skill.chosen_targets(battle, actor, payload))
                   if multi_target else [battle.effect_recipient(actor)])
        before = {target.unit_id: self.services.r21_queued_loss(battle, target, queued, observed_targets=targets) for target in targets}
        with ai_probe_rollback(battle):
            hp_before = {target.unit_id: target.current_hp for target in targets}
            alive_before = {target.unit_id: target.alive for target in targets}
            cost = skill.mana_cost_for_payload(battle, actor, payload)
            actor.spend_mana(cost)
            skill.react(battle, actor, payload, deepcopy(queued))
            prevention = sum(before[target.unit_id] - self.services.r21_queued_loss(battle, target, queued, observed_targets=targets)
                             - max(0.0, hp_before[target.unit_id] - target.current_hp) * 100.0
                             - (90.0 if alive_before[target.unit_id] and not target.alive else 0.0)
                             for target in targets)
            return prevention - cost * 18.0 if prevention > 0 else -1000.0


    def r22_paid_reaction_score(self, battle: Battle, actor: Unit, queued: QueuedAction,
                                payload: dict[str, Any], code: str) -> float:
        """Compatibility entry for callers using the previous scorer name."""
        return self.services.paid_reaction_score(battle, actor, queued, payload, actor.get_skill(code),
                                   code in {"light_wall", "fire_wall", "hell_wall", "hell_wall_free"})


    def simulated_reaction_score(self, battle: Battle, actor: Unit, queued: QueuedAction,
                                 payload: dict[str, Any], skill: Any,
                                 reservation_cost: float) -> float:
        """Compare one legal reaction with the same queued action left untouched."""
        if not skill.can_react_with_payload(battle, actor, queued, payload)[0]:
            return -1000.0
        units = list(battle.all_units())
        initial = {
            unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields(), unit.alive,
                           unit.position, self.services.reviewed_r17_position_value(battle, actor, unit))
            for unit in units
        }

        @expected_local(battle)
        def outcome(with_reaction: bool) -> float:
            with ai_probe_rollback(battle):
                try:
                    source_action = deepcopy(queued)
                    if with_reaction:
                        skill.react(battle, actor, payload, source_action)
                    battle.resolve_queued_action(source_action)
                except (ActionError, KeyError, ValueError, TypeError):
                    return -1000.0
                score = 0.0
                for unit in units:
                    hp, mana, shields, alive, position, position_value = initial[unit.unit_id]
                    loss = (hp - unit.current_hp) * 100.0
                    loss += (mana - unit.current_mana) * 18.0
                    loss += max(0, shields - unit.total_shields()) * 20.0
                    if alive and self.services.scored_death_probability(unit) > 0:
                        loss += (90.0 * self.services.scored_death_probability(unit) if direct_scoring() else 90.0)
                    score += -loss if unit.player_id == actor.player_id else loss
                    if unit.position != position:
                        delta = self.services.reviewed_r17_position_value(battle, actor, unit) - position_value
                        score += delta * (0.8 if unit.player_id == actor.player_id else -0.8)
                return score

        before = outcome(False)
        after = outcome(True)
        if before <= -1000.0 or after <= -1000.0:
            return -1000.0
        return after - before - reservation_cost


    def andrew_reaction_score(self, battle: Battle, actor: Unit, queued: QueuedAction,
                              payload: dict[str, Any], code: str) -> float:
        """Compatibility entry for callers using the previous scorer name."""
        return self.services.simulated_reaction_score(battle, actor, queued, payload, actor.get_skill(code),
                                        8.0 if code == "gladiator_roar" else 4.0)


_BOUND_SERVICE: AILateTacticalScoring | None = None

def bind_room(service: AILateTacticalScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AILateTacticalScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

def r23_action_state(battle: Battle) -> dict[str, tuple[Unit, float, float, int, int, bool, dict[str, float]]]:
    return _require_service().r23_action_state(battle)


def r23_action_delta(actor: Unit, before: dict[str, Any]) -> float:
    """Price actual outcomes for all recipients, including friendly losses."""
    return _require_service().r23_action_delta(actor, before)


@expected_numeric(independent=True)
def r23_attack_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().r23_attack_value(battle, actor, payload)


@expected_numeric(independent=True)
def electronic_repair_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().electronic_repair_score(battle, actor, payload)


@expected_numeric(independent=True)
def r23_paid_skill_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    """Use the same outcome scale as Kiku's attacks, including actual prepaid mana."""
    return _require_service().r23_paid_skill_value(battle, actor, payload)


def _r23_expected_settled_value(battle: Battle, actor: Unit, before: dict[str, Any]) -> float:
    return _require_service()._r23_expected_settled_value(battle, actor, before)


@expected_numeric(independent=True)
def r215219_skill_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    """Replay both sides, then price the actual landing and short attack window."""
    return _require_service().r215219_skill_score(battle, actor, payload)


@expected_numeric(independent=True)
def r23_passive_defense_value(battle: Battle, defender: Unit) -> float:
    """At most one next legal prevention opportunity; names alone add no value."""
    return _require_service().r23_passive_defense_value(battle, defender)


@expected_numeric(independent=True)
def r23_sun_slash_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    return _require_service().r23_sun_slash_score(battle, actor, skill, payload)


@expected_numeric(independent=True)
def r23_attack_plan_value(battle: Battle, actor: Unit) -> float:
    return _require_service().r23_attack_plan_value(battle, actor)


@expected_numeric(independent=True)
def r22_attack_value(battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> float:
    return _require_service().r22_attack_value(battle, actor, target, payload)


@expected_numeric(independent=True)
def r22_contact_value(battle: Battle, actor: Unit, *, allow_move: bool = True) -> float:
    return _require_service().r22_contact_value(battle, actor, allow_move=allow_move)


@expected_numeric(independent=True)
def r22_gazer_window_risk(battle: Battle, actor: Unit) -> float:
    return _require_service().r22_gazer_window_risk(battle, actor)


@expected_numeric(independent=True)
def r22_ninja_shensu_score(battle: Battle, actor: Unit) -> float:
    return _require_service().r22_ninja_shensu_score(battle, actor)


@expected_numeric(independent=True)
def r22_heal_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    return _require_service().r22_heal_score(battle, actor, skill, payload)


@expected_numeric(independent=True)
def paid_reaction_score(battle: Battle, actor: Unit, queued: QueuedAction,
                        payload: dict[str, Any], skill: Any,
                        multi_target: bool) -> float:
    """Simulate a paid defense against its actual affected recipients."""
    return _require_service().paid_reaction_score(battle, actor, queued, payload, skill, multi_target)


@expected_numeric(independent=True)
def r22_paid_reaction_score(battle: Battle, actor: Unit, queued: QueuedAction,
                            payload: dict[str, Any], code: str) -> float:
    """Compatibility entry for callers using the previous scorer name."""
    return _require_service().r22_paid_reaction_score(battle, actor, queued, payload, code)


@expected_numeric(independent=True)
def simulated_reaction_score(battle: Battle, actor: Unit, queued: QueuedAction,
                             payload: dict[str, Any], skill: Any,
                             reservation_cost: float) -> float:
    """Compare one legal reaction with the same queued action left untouched."""
    return _require_service().simulated_reaction_score(battle, actor, queued, payload, skill, reservation_cost)


@expected_numeric(independent=True)
def andrew_reaction_score(battle: Battle, actor: Unit, queued: QueuedAction,
                          payload: dict[str, Any], code: str) -> float:
    """Compatibility entry for callers using the previous scorer name."""
    return _require_service().andrew_reaction_score(battle, actor, queued, payload, code)
