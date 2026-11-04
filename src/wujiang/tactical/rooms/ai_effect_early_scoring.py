"""AIEarlyEffectScoring: shared AI scoring and queries for effect_early."""

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
from wujiang.tactical.heroes.mechanics.undead_king_lina import lina_has_attack_followup
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

class AIEarlyEffectScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    CONTROL_SKILL_CODES: Any
    DAMAGING_SKILL_CODES: Any
    DifficultyProfile: Any
    HEAL_SKILL_CODES: Any
    HOSTILE_EFFECT_SKILL_CODES: Any
    SUMMON_SKILL_CODES: Any
    _score_skill_payload: Callable[..., Any]
    _skill_score_context: Callable[..., Any]
    attack_payloads_for_action: Callable[..., Any]
    copied_skill_action: Any
    distance_between_units: Callable[..., Any]
    estimate_damage: Callable[..., Any]
    field_skill_score: Callable[..., Any]
    friendly_fire_penalty: Callable[..., Any]
    gale_score: Callable[..., Any]
    hero_style: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    payload_is_legal: Callable[..., Any]
    primary_target_unit: Callable[..., Any]
    r12_costly_window: Callable[..., Any]
    r12_future_banish_value: Callable[..., Any]
    r12_preparation_score: Callable[..., Any]
    r12_realized_effect_value: Callable[..., Any]
    r13_recovery_value: Callable[..., Any]
    r19_attack_window_value: Callable[..., Any]
    r19_incoming_position_risk: Callable[..., Any]
    r23_sun_slash_score: Callable[..., Any]
    score_move_destination: Callable[..., Any]
    score_skill_payload: Callable[..., Any]
    scored_death_probability: Callable[..., Any]
    skill_attack_power: Callable[..., Any]
    skill_effect_cells: Callable[..., Any]
    skill_effect_units: Callable[..., Any]
    skill_payload_has_effective_enemy_impact: Callable[..., Any]
    skill_payloads_for_action: Any


class AIEarlyEffectScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AIEarlyEffectScoringServices) -> None:
        self.services = services

    def resource_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload, profile))


    def blood_support_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload, profile))


    def li_attack_plan_value(self, battle: Battle, actor: Unit) -> float:
        remaining = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
        essence = actor.get_status("精华")
        charges = min(remaining, getattr(essence, "charges", 0))
        candidates = []
        for enemy in living_hostile_combatants(battle, actor.player_id):
            if self.services.distance_between_units(battle, actor, enemy) > actor.targeting_range() + actor.remaining_normal_move_distance(battle) or enemy.physical_immunity:
                continue
            normal = self.services.estimate_damage(battle, enemy, actor.stat("attack"))
            piercing = self.services.estimate_damage(battle, enemy, actor.stat("attack"), ignore_shield=True)
            value = min(enemy.current_hp, normal * (remaining - charges) + piercing * charges) * 110.0
            candidates.append(value)
        return max(candidates, default=0.0)


    def li_area_skill_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload, profile))


    def chanter_card_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload, profile))


    def perfect_swordsman_skill_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload, profile))


    def skill_owned_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                                 payload: dict[str, Any], profile: DifficultyProfile) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload, profile))


    def reviewed_r06_skill_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                                 profile: DifficultyProfile) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload, profile))


    def magnetic_wave_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
        units = self.services.skill_effect_units(battle, actor, skill, payload)
        before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.turn_ready, unit.alive, unit.total_shields()) for unit in units}
        with ai_probe_rollback(battle):
            try:
                probe = dict(payload)
                probe.update(skill.queued_payload_metadata(battle, actor, probe))
                skill.execute(battle, actor, probe)
            except (ActionError, ValueError):
                return -1000.0
            score = -40.0  # Two mana points could instead become two mana guards.
            for unit in units:
                hp, mana, ready, alive, shields = before[unit.unit_id]
                hostile = unit.player_id != actor.player_id
                sign = 1.0 if hostile else -1.0
                score += sign * ((hp - unit.current_hp) * (110.0 if hostile else 160.0) + (mana - unit.current_mana) * 16.0 + (shields - unit.total_shields()) * 8.0)
                if alive and not unit.alive:
                    score += sign * 160.0
                if ready and not unit.turn_ready and unit.alive and battle.unit_belongs_to_current_turn(unit):
                    attacks = max(0, unit.attack_actions_per_turn() - unit.attacks_used)
                    score += sign * (25.0 + min(4, attacks) * 18.0 + (15.0 if unit.remaining_normal_move_distance(battle) > 0 else 0.0))
            return score


    def masamune_attack_plan_value(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        best = 0.0
        remaining = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
        for action in battle.action_snapshot_for(actor).get("actions", []):
            if action.get("kind") != "attack" or not action.get("available"):
                continue
            for payload in self.services.attack_payloads_for_action(battle, actor, action):
                if not self.services.payload_is_legal(battle, payload):
                    continue
                target = battle.get_unit(payload["target_unit_id"])
                resolved = battle.resolved_basic_attack_payload(actor, payload)
                damage = estimate_attack_damage(battle, actor, target, payload, attack_power=battle.basic_attack_preview_power(actor, payload))
                total = min(target.current_hp, damage * (remaining // max(1, int(resolved.get("attack_cost", 1)))))
                best = max(best, total * 100.0 + (95.0 if total >= target.current_hp - 1e-9 else 0.0))
        return best


    def allied_attack_heal_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None:
            return 0.0
        amount = battle.allied_basic_heal_amount(actor, target)
        if amount <= 0:
            return 0.0
        with ai_probe_rollback(battle):
            before = target.current_hp
            battle.heal(HealContext(source=actor, target=target, amount=amount, action_name="攻击己方加血"))
            gained = target.current_hp - before
        if gained <= 0:
            return -1000.0
        threat = max((self.services.estimate_damage(battle, target, enemy.stat("attack"))
                      for enemy in living_hostile_combatants(battle, actor.player_id)
                      if self.services.distance_between_units(battle, target, enemy) <= enemy.targeting_range() + enemy.normal_move_distance()), default=0.0)
        saves = target.current_hp <= threat < target.current_hp + gained
        return gained * 150.0 + (90.0 if saves else 0.0)


    def fried_aura_position_value(self, battle: Battle, actor: Unit, destination: Position) -> float:
        from wujiang.tactical.heroes.mechanics.fried_soldier import FriedAuraTrait
        auras = [trait for unit in battle.all_units() for trait in unit.traits if isinstance(trait, FriedAuraTrait)]
        if not auras:
            return 0.0
        old = actor.position
        actor.position = destination
        try:
            recipients = [unit for unit in battle.player_units(actor.player_id) if unit.is_clone or unit.is_summon] if any(aura.owner is actor for aura in auras) else [actor]
            return sum(12.0 + max((sum(max(0.0, aura.owner.stat(stat) - getattr(aura.owner.base_stats, stat))
                                      for stat in ("attack", "defense", "speed", "attack_range"))
                                  for aura in auras if aura.covers(battle, unit)), default=0.0) * 5.0
                       for unit in recipients if any(aura.covers(battle, unit) for aura in auras))
        finally:
            actor.position = old


    def fried_skill_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload, profile))


    def frey_skill_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        return skill.ai_effect_score(self.services._skill_score_context(
            battle, actor, {"code": skill.code, "_skill_object": skill}, payload, profile))


    def morning_holy_light_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
        """Value the declared rectangle after actual defenses and friendly effects."""
        try:
            cells = skill.get_target_cells_for_payload(battle, actor, payload)
        except ActionError:
            return -1000.0
        targets = [target for target in battle.effect_units_at_cells(cells)
                   if target.player_id == actor.player_id or not target.is_stealthed()]
        if not targets:
            return -1000.0
        with ai_probe_rollback(battle):
            before = {target.unit_id: (target.current_hp, target.alive, target.total_shields(),
                                       getattr(target.get_status("被动封锁"), "duration", 0) or 0)
                      for target in targets}
            try:
                skill.execute(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            score = 0.0
            for target in targets:
                hp, alive, shields, old_lock = before[target.unit_id]
                value = max(0.0, hp - target.current_hp) * 115.0
                value += max(0, shields - target.total_shields()) * 20.0
                if alive and self.services.scored_death_probability(target) > 0:
                    value += (90.0 * self.services.scored_death_probability(target) if direct_scoring() else 90.0)
                new_lock = getattr(target.get_status("被动封锁"), "duration", 0) or 0
                if new_lock > old_lock:
                    passive_count = sum(candidate.timing == "passive" or candidate.passive
                                        for candidate in target.skills)
                    if passive_count:
                        value += (22.0 + min(3, passive_count) * 12.0) * min(1.0, new_lock - old_lock)
                score += value if target.player_id != actor.player_id else -value * 1.5
            score -= skill.mana_cost_for_payload(battle, actor, payload) * 12.0
            return score if score > 0 else -1000.0


    def skill_damage_score(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        affected = self.services.skill_effect_units(battle, actor, skill, payload)
        score = 0.0
        cells = self.services.skill_effect_cells(battle, actor, skill, payload)
        lina_reward = next((trait for trait in actor.traits if trait.name == "击破重置"), None)
        lina_reward_kills: list[Unit] = []
        for unit in affected:
            if unit.player_id == actor.player_id:
                score -= self.services.friendly_fire_penalty(unit)
                continue
            special_score = skill.ai_damage_target_score(unit, self.services.hostile_unit_value(unit))
            if special_score is not None:
                score += special_score
                continue
            attack_power = self.services.skill_attack_power(battle, actor, skill, payload, unit, cells)
            ignore_shield = bool(skill.ignores_shield_for_payload(battle, actor, payload))
            half_ignore_shield = bool(skill.half_ignores_shield_for_payload(battle, actor, payload))
            damage = estimate_skill_damage(
                battle,
                actor,
                skill,
                payload,
                unit,
                attack_power,
                cells=cells,
                ignore_shield=ignore_shield,
                half_ignore_shield=half_ignore_shield,
            )
            score += damage * 100.0
            score += self.services.hostile_unit_value(unit) * 0.5
            if damage >= unit.current_hp - 1e-9:
                score += 90.0
                if lina_reward is not None and lina_reward._eligible_target(unit):
                    lina_reward_kills.append(unit)
        if lina_reward_kills:
            mana_room = max(0.0, actor.max_mana() - actor.current_mana)
            for unit in lina_reward_kills:
                gained = min(unit.current_mana, mana_room)
                score += gained * 18.0
                mana_room -= gained
            if actor.attacks_used > 0 and lina_has_attack_followup(
                battle, actor, {unit.unit_id for unit in lina_reward_kills}
            ):
                score += 55.0
        score += len([unit for unit in affected if unit.player_id != actor.player_id]) * skill.ai_area_hit_bonus()
        if self.services.hero_style(actor) != "support":
            score += profile.aggressive_bonus
        return score


    def skill_control_bonus(self,
        battle: Battle,
        actor: Unit,
        code: str,
        payload: dict[str, Any],
        targets: list[Unit],
        profile: DifficultyProfile,
        *,
        instant_only: bool,
        skill: Any = None,
        action: dict[str, Any] | None = None,
    ) -> float:
        if skill is None:
            try:
                skill = actor.get_skill(code)
            except (ActionError, KeyError, ValueError):
                return 0.0
        context = self.services._skill_score_context(
            battle, actor, action or {"code": code, "_skill_object": skill}, payload,
            profile, instant_only=instant_only)
        return skill.ai_control_bonus(context, list(targets))


    def drain_mana_score(self, battle: Battle, actor: Unit, targets: list[Unit], profile: DifficultyProfile) -> float:
        enemies = [unit for unit in targets if unit.player_id != actor.player_id]
        if not enemies:
            return -4.0
        return sum(min(unit.current_mana, 1.0) * 55.0 + self.services.hostile_unit_value(unit) * 0.2 for unit in enemies)


    def r12_future_banish_value(self, battle: Battle, target: Unit, profile: DifficultyProfile) -> float:
        """Value attacks and currently usable active skills in the target's next owner slot."""
        with ai_probe_rollback(battle):
            battle.turn_number += 1
            battle.active_player = target.player_id
            battle._exclusive_turn_unit_id = battle.unit_turn_slot_id(target) or target.unit_id
            battle.resolving_action = None
            target.turn_ready = True
            attack = self.services.r19_attack_window_value(battle, target, profile)
            active = 0.0
            if not target.cannot_use_skills:
                for skill in target.skills:
                    if skill.timing != "active" or skill.ai_future_opportunity_excluded:
                        continue
                    try:
                        if not skill.can_use(battle, target, {})[0]:
                            continue
                        action = self.services.copied_skill_action(battle, target, skill)
                        payloads = self.services.skill_payloads_for_action(battle, target, action)
                        ranked = sorted(payloads, key=lambda payload: sum(
                            unit.player_id != target.player_id for unit in self.services.skill_effect_units(battle, target, skill, payload)
                        ), reverse=True)
                        for payload in ranked[:6]:
                            heals = skill.code in self.services.HEAL_SKILL_CODES or skill.ai_future_heal
                            if heals:
                                if not any(unit.player_id == target.player_id and unit.current_hp < unit.max_health and not unit.cannot_heal
                                           for unit in self.services.skill_effect_units(battle, target, skill, payload)):
                                    continue
                            elif not self.services.skill_payload_has_effective_enemy_impact(battle, target, action, payload):
                                continue
                            active = max(active, self.services.score_skill_payload(battle, target, action, payload, profile, instant_only=False))
                    except (ActionError, KeyError, TypeError, ValueError):
                        continue
            return max(0.0, attack, active)


    def r12_realized_effect_value(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                                  profile: DifficultyProfile, *, gravity_values: list[int] | None = None) -> float:
        with ai_probe_rollback(battle):
            units = list(battle.all_units())
            before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.alive, unit.total_shields(),
                                     unit.has_status("圣殿放逐")) for unit in units}
            control_before = skill.ai_r12_control_before(battle, actor, units, self.services, profile)
            try:
                queued = battle.build_queued_action({"type": "skill", "unit_id": actor.unit_id, **payload, "skill_code": skill.code})
                if gravity_values is not None:
                    queued.payload["gravity_coin_values"] = gravity_values
                    queued.payload["gravity_side"] = gravity_values[0] * gravity_values[1] * gravity_values[2]
                battle.pending_followup_actions.clear()
                battle.prepay_skill_resources(skill, actor, queued.payload)
                queued.payload["resources_prepaid"] = True
                actor.notify_action_declared(battle, "skill", queued.payload)
                queued.payload["declared_source_attack"] = actor.stat("attack")
                battle.resolve_queued_action(queued)
                while battle.pending_followup_actions:
                    battle.resolve_queued_action(battle.pending_followup_actions.popleft())
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            score = 0.0
            for unit in units:
                hp, mana, alive, shields, locked = before[unit.unit_id]
                mana_weight = skill.ai_r12_mana_weight(actor, unit)
                value = (hp - unit.current_hp) * 115.0 + (mana - unit.current_mana) * mana_weight
                value += max(0, shields - unit.total_shields()) * 20.0
                if alive and self.services.scored_death_probability(unit) > 0:
                    value += (90.0 * self.services.scored_death_probability(unit) if direct_scoring() else 90.0)
                value += skill.ai_r12_unit_bonus(
                    battle, actor, unit, locked=locked, control_before=control_before,
                    services=self.services, profile=profile)
                score += value if unit.player_id != actor.player_id else -value * (1.3 if hp > unit.current_hp else 1.0)
            return score


    def r12_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                         profile: DifficultyProfile) -> float:
        score = skill.ai_r12_effect_override(battle, actor, payload, self.services, profile)
        if score is not None and skill.ai_r12_direct_return and direct_scoring():
            return score
        if score is None:
            score = self.services.r12_realized_effect_value(battle, actor, skill, payload, profile)
        if score <= 0:
            return -1000.0
        if skill.max_uses_per_battle == 1 and score < profile.once_per_battle_threshold:
            score -= 32.0
        return score


    def r12_costly_window(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        best = 0.0
        for skill in actor.skills:
            if skill.timing != "active" or skill.mana_cost <= 0 or skill.ai_costly_window_excluded:
                continue
            if skill.code not in self.services.DAMAGING_SKILL_CODES | self.services.HOSTILE_EFFECT_SKILL_CODES | self.services.HEAL_SKILL_CODES:
                continue
            try:
                if not skill.can_use(battle, actor, {})[0]:
                    continue
                action = self.services.copied_skill_action(battle, actor, skill)
                payloads = self.services.skill_payloads_for_action(battle, actor, action)
                ranked = sorted(payloads, key=lambda payload: sum(
                    unit.player_id != actor.player_id for unit in self.services.skill_effect_units(battle, actor, skill, payload)
                ), reverse=True)
                for payload in ranked[:8]:
                    if not self.services.payload_is_legal(battle, payload):
                        continue
                    if skill.code in self.services.HEAL_SKILL_CODES:
                        if not any(unit.player_id == actor.player_id and unit.current_hp < unit.max_health and not unit.cannot_heal
                                   for unit in self.services.skill_effect_units(battle, actor, skill, payload)):
                            continue
                    elif not self.services.skill_payload_has_effective_enemy_impact(battle, actor, action, payload):
                        continue
                    best = max(best, self.services.score_skill_payload(battle, actor, action, payload, profile, instant_only=False))
            except (ActionError, KeyError, TypeError, ValueError):
                continue
        return best


    def r12_preparation_score(self, battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
        try:
            skill = actor.get_skill(code)
        except (ActionError, KeyError, ValueError):
            return -8.0
        return skill.ai_preparation_score(battle, actor, self.services, profile)


    def r13_recovery_value(self, battle: Battle, target: Unit) -> float:
        if target.cannot_heal:
            return 0.0
        missing = max(0.0, target.max_health - target.current_hp)
        if missing <= 0:
            return 0.0
        natural = any(trait.name in {"自然回血", "自然回复"} for trait in target.traits)
        active = 0.0
        with ai_probe_rollback(battle):
            battle.turn_number += 1
            battle.active_player = target.player_id
            battle._exclusive_turn_unit_id = battle.unit_turn_slot_id(target) or target.unit_id
            target.turn_ready = True
            battle.resolving_action = None
            for candidate in target.skills:
                if (candidate.code not in self.services.HEAL_SKILL_CODES and not candidate.ai_future_heal) or candidate.timing != "active":
                    continue
                try:
                    if not candidate.can_use(battle, target, {})[0]:
                        continue
                    action = self.services.copied_skill_action(battle, target, candidate)
                    payloads = [probe for probe in self.services.skill_payloads_for_action(battle, target, action)
                                if target in self.services.skill_effect_units(battle, target, candidate, probe)]
                    for probe in payloads[:8]:
                        with ai_probe_rollback(battle):
                            hp = target.current_hp
                            queued = battle.build_queued_action(probe)
                            battle.prepay_skill_resources(candidate, target, queued.payload)
                            queued.payload["resources_prepaid"] = True
                            target.notify_action_declared(battle, "skill", queued.payload)
                            battle.resolve_queued_action(queued)
                            gain = max(0.0, target.current_hp - hp)
                            if gain > 0:
                                active = max(active, gain * 100.0 + 22.0)
                except (ActionError, KeyError, ValueError, TypeError):
                    continue
        return (min(missing, 0.25) * 100.0 if natural else 0.0) + active


    def r13_attack_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        with ai_probe_rollback(battle):
            units = list(battle.all_units())
            before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.alive, unit.total_shields()) for unit in units}
            try:
                queued = battle.build_queued_action({"type": "attack", "unit_id": actor.unit_id, **payload})
                actor.notify_action_declared(battle, "attack", queued.payload)
                queued.payload["declared_source_attack"] = actor.stat("attack")
                battle.resolve_queued_action(queued)
            except (ActionError, KeyError, ValueError, TypeError):
                return -1000.0
            score = 0.0
            for unit in units:
                hp, mana, alive, shields = before[unit.unit_id]
                value = (hp - unit.current_hp) * 100.0 + (mana - unit.current_mana) * 18.0
                value += max(0, shields - unit.total_shields()) * 20.0 + (80.0 if alive and not unit.alive else 0.0)
                score += value if unit.player_id != actor.player_id else -value * (1.3 if hp > unit.current_hp else 1.0)
            return score - 4.0 if score > 0 else -1000.0


    def r13_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        with ai_probe_rollback(battle):
            units = list(battle.all_units())
            before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.alive, unit.total_shields(),
                                     {status.name for status in unit.statuses}) for unit in units}
            control = {unit.unit_id: skill.ai_simulated_control_value(battle, actor, unit, self.services)
                       for unit in self.services.skill_effect_units(battle, actor, skill, payload)}
            saved = skill.ai_simulation_before(battle, actor, self.services)
            remi_mana_risk = getattr(getattr(actor, "ai_policy", None), "ai_mana_risk", False)
            mana_risk = self.services.r19_incoming_position_risk(battle, actor) if remi_mana_risk and skill.mana_cost > 0 else 0.0
            try:
                queued = battle.build_queued_action({"type": "skill", "unit_id": actor.unit_id, **payload, "skill_code": skill.code})
                battle.pending_followup_actions.clear()
                battle.prepay_skill_resources(skill, actor, queued.payload)
                queued.payload["resources_prepaid"] = True
                actor.notify_action_declared(battle, "skill", queued.payload)
                queued.payload["declared_source_attack"] = actor.stat("attack")
                battle.resolve_queued_action(queued)
                while battle.pending_followup_actions:
                    battle.resolve_queued_action(battle.pending_followup_actions.popleft())
            except (ActionError, KeyError, ValueError, TypeError):
                return -1000.0
            score = 0.0
            for unit in units:
                hp, mana, alive, shields, statuses = before[unit.unit_id]
                value = (hp - unit.current_hp) * 100.0 + (mana - unit.current_mana) * 18.0
                value += max(0, shields - unit.total_shields()) * 20.0 + (80.0 if alive and not unit.alive else 0.0)
                name = skill.ai_simulated_control_status
                if name is not None and unit.alive and name not in statuses and unit.has_status(name):
                    value += control.get(unit.unit_id, 0.0)
                score += value if unit.player_id != actor.player_id else -value * (1.3 if hp > unit.current_hp else 1.0)
            score += skill.ai_simulation_after(battle, actor, queued, self.services, profile, saved, before, units)
            if remi_mana_risk and actor.alive and actor.current_mana < 2 and skill.mana_cost > 0:
                score -= min(80.0, max(0.0, self.services.r19_incoming_position_risk(battle, actor) - mana_risk))
            if score <= 0:
                return -1000.0
            if skill.max_uses_per_battle == 1 and score < profile.once_per_battle_threshold:
                score -= 32.0
            return score


    def r13_bat_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
        with ai_probe_rollback(battle):
            original = set(battle.units)
            risk = self.services.r19_incoming_position_risk(battle, actor)
            try:
                skill.execute(battle, actor, dict(payload))
            except (ActionError, KeyError, ValueError):
                return -1000.0
            from wujiang.tactical.heroes.mechanics.remi_bat import RemiBatSummon
            summon = next((unit for unit in battle.all_units()
                           if unit.unit_id not in original and isinstance(unit, RemiBatSummon)), None)
            if summon is None or not summon.can_take_turn_actions(battle):
                return -1000.0
            output = self.services.r19_attack_window_value(battle, summon, profile)
            cover = max(0.0, risk - self.services.r19_incoming_position_risk(battle, actor))
            return output + min(25.0, output * 0.4) + cover - 8.0 if output > 0 or cover > 0 else -8.0


_BOUND_SERVICE: AIEarlyEffectScoring | None = None

def bind_room(service: AIEarlyEffectScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AIEarlyEffectScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

@expected_numeric(independent=True)
def resource_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().resource_effect_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def blood_support_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().blood_support_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def li_attack_plan_value(battle: Battle, actor: Unit) -> float:
    return _require_service().li_attack_plan_value(battle, actor)


@expected_numeric(independent=True)
def li_area_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().li_area_skill_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def chanter_card_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().chanter_card_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def perfect_swordsman_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().perfect_swordsman_skill_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def skill_owned_effect_score(battle: Battle, actor: Unit, skill: Any,
                             payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().skill_owned_effect_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def reviewed_r06_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                             profile: DifficultyProfile) -> float:
    return _require_service().reviewed_r06_skill_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def magnetic_wave_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    return _require_service().magnetic_wave_score(battle, actor, skill, payload)


@expected_numeric(independent=True)
def masamune_attack_plan_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().masamune_attack_plan_value(battle, actor, profile)


@expected_numeric(independent=True)
def allied_attack_heal_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().allied_attack_heal_score(battle, actor, payload)


@expected_numeric(independent=True)
def fried_aura_position_value(battle: Battle, actor: Unit, destination: Position) -> float:
    return _require_service().fried_aura_position_value(battle, actor, destination)


@expected_numeric(independent=True)
def fried_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().fried_skill_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def frey_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().frey_skill_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def morning_holy_light_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    """Value the declared rectangle after actual defenses and friendly effects."""
    return _require_service().morning_holy_light_score(battle, actor, skill, payload)


@expected_numeric(independent=True)
def skill_damage_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service().skill_damage_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def skill_control_bonus(
    battle: Battle,
    actor: Unit,
    code: str,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
    *,
    instant_only: bool,
    skill: Any = None,
    action: dict[str, Any] | None = None,
) -> float:
    return _require_service().skill_control_bonus(
        battle, actor, code, payload, targets, profile,
        instant_only=instant_only, skill=skill, action=action)


@expected_numeric(independent=True)
def drain_mana_score(battle: Battle, actor: Unit, targets: list[Unit], profile: DifficultyProfile) -> float:
    return _require_service().drain_mana_score(battle, actor, targets, profile)


@expected_numeric(independent=True)
def r12_future_banish_value(battle: Battle, target: Unit, profile: DifficultyProfile) -> float:
    """Value attacks and currently usable active skills in the target's next owner slot."""
    return _require_service().r12_future_banish_value(battle, target, profile)


@expected_numeric(independent=True)
def r12_realized_effect_value(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                              profile: DifficultyProfile, *, gravity_values: list[int] | None = None) -> float:
    return _require_service().r12_realized_effect_value(battle, actor, skill, payload, profile, gravity_values=gravity_values)


@expected_numeric(independent=True)
def r12_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                     profile: DifficultyProfile) -> float:
    return _require_service().r12_effect_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def r12_costly_window(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().r12_costly_window(battle, actor, profile)


@expected_numeric(independent=True)
def r12_preparation_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    return _require_service().r12_preparation_score(battle, actor, code, profile)


@expected_numeric(independent=True)
def r13_recovery_value(battle: Battle, target: Unit) -> float:
    return _require_service().r13_recovery_value(battle, target)


@expected_numeric(independent=True)
def r13_attack_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().r13_attack_value(battle, actor, payload)


@expected_numeric(independent=True)
def r13_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().r13_effect_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def r13_bat_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    return _require_service().r13_bat_score(battle, actor, skill, payload, profile)
