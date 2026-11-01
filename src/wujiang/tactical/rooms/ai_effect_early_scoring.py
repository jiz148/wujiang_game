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
        code = str(skill.code)
        affected = self.services.skill_effect_units(battle, actor, skill, payload)
        score = 0.0
        cells = self.services.skill_effect_cells(battle, actor, skill, payload)
        lina_reward = (next((trait for trait in actor.traits if trait.name == "击破重置"), None)
                       if actor.hero_code == "undead_king_lina" else None)
        lina_reward_kills: list[Unit] = []
        for unit in affected:
            if unit.player_id == actor.player_id:
                score -= self.services.friendly_fire_penalty(unit)
                continue
            if code == "morning_holy_light":
                if unit.attribute == "暗":
                    score += min(5.0, unit.current_hp) * 100.0
                score += self.services.hostile_unit_value(unit) * 0.5
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
        if code in {"judgment_fire", "great_funeral", "laser", "missile", "machine_gun", "pierce", "hell_spear", "hell_spear_free", "hell_ranged_spear", "hell_ranged_spear_free", "large_pierce_plus", "remote_dragon_breath", "dragon_breath", "magnetic_wave", "whirlwind_attack"}:
            score += len([unit for unit in affected if unit.player_id != actor.player_id]) * 18.0
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
    ) -> float:
        enemies = [unit for unit in targets if unit.player_id != actor.player_id]
        if code == "magnetic_wave":
            # The complete damage/control result is priced by magnetic_wave_score.
            return 0.0
        if code == "paralyzing_glove":
            return sum(self.services.hostile_unit_value(unit) * 0.6 for unit in enemies) + 45.0
        if code == "curse":
            return sum(unit.current_hp * 80.0 for unit in enemies)
        if code == "doom_light":
            return len(enemies) * 32.0 + sum(unit.current_hp * 40.0 for unit in enemies)
        if code in {"complete_burn", "blizzard"}:
            status_name = "完全燃烧" if code == "complete_burn" else "暴风雪"
            return sum(24.0 if not unit.has_status(status_name) else 0.0 for unit in enemies)
        if code == "morning_holy_light":
            return len(enemies) * 30.0 + sum(40.0 for unit in enemies if unit.attribute == "暗")
        if code == "eagle_eye":
            return sum(
                42.0
                + (0 if unit.cannot_attack else 24.0)
                + sum(1 for skill in unit.skills if skill.timing == "active") * 12.0
                for unit in enemies
                if not unit.has_status("鹰眼")
            )
        if code == "hundred_bird_burial":
            from wujiang.tactical.heroes.mechanics.true_blade_bird import skill_has_movement_effect
            return sum(24.0 + 18.0 * sum(skill_has_movement_effect(skill) for skill in unit.skills)
                       for unit in enemies if not unit.has_status("百鸟葬禁位移"))
        if code == "snow_avalanche":
            return sum(
                52.0 + 9.0 * sum(skill.timing == "active" for skill in unit.skills)
                for unit in enemies if not unit.has_status("雪崩") and unit.alive
            )
        if code == "thor_destroy_lightning":
            return sum(54.0 for unit in enemies if unit.attribute != "雷" and not unit.has_status("毁灭电击"))
        if code in {"drain_mana", "large_drain_mana"}:
            return sum(min(unit.current_mana, 1.0) * 45.0 for unit in enemies)
        if code == "mana_pull":
            return 20.0 if enemies else 6.0
        if code == "stance":
            return self.services.field_skill_score(battle, actor, code, targets, profile)
        if code == "plant_growth":
            return self.services.field_skill_score(battle, actor, code, targets, profile)
        if code == "smoke_spray":
            return self.services.field_skill_score(battle, actor, code, targets, profile)
        if code == "gale":
            return self.services.gale_score(battle, actor, payload, profile)
        if code == "heaven_lock":
            target = self.services.primary_target_unit(battle, payload, targets)
            if target is None or target.player_id == actor.player_id or target.has_status("天锁") or target.cannot_normal_move or target.stat("speed") <= 0:
                return -1000.0
            with ai_probe_rollback(battle):
                skill = actor.get_skill(code)
                try:
                    skill.execute(battle, actor, payload)
                except ActionError:
                    return -1000.0
                if not target.has_status("天锁"):
                    return -1000.0
            return 24.0 + min(6.0, target.stat("speed")) * 7.0 - 1.5 * 12.0
        if code == "sun_slash":
            return self.services.r23_sun_slash_score(battle, actor, actor.get_skill(code), payload)
        if code == "heaven_punishment":
            selected_code = str(payload.get("disabled_skill_code") or "")
            target = self.services.primary_target_unit(battle, payload, targets)
            if target is None or not selected_code:
                return -120.0
            from wujiang.tactical.heroes.mechanics.heaven_punishment import HeavenPunishmentSkill

            selected = next((skill for skill in HeavenPunishmentSkill.public_active_skills(battle, target) if skill.code == selected_code), None)
            if selected is None or any(getattr(status, "skill_code", None) == selected_code for status in target.statuses):
                return -120.0
            if selected.max_uses_per_battle is not None and selected.uses_this_battle >= selected.max_uses_per_battle:
                return -120.0
            score = 32.0 if selected.cooldown_remaining > 0 else 55.0
            if selected_code in self.services.DAMAGING_SKILL_CODES or selected_code in self.services.CONTROL_SKILL_CODES:
                score += 35.0
            if selected_code in self.services.SUMMON_SKILL_CODES or selected_code in self.services.HEAL_SKILL_CODES:
                score += 24.0
            if getattr(selected, "max_uses_per_battle", None) == 1 and getattr(selected, "uses_this_battle", 0) == 0:
                score += 20.0
            if selected.mana_cost > target.max_mana():
                score -= 45.0
            return score
        if code in {"paralysis_card", "poison_card", "drain_card", "magic_claw"}:
            if code == "drain_card":
                return sum(min(unit.current_mana, 1.0) * 35.0 + self.services.hostile_unit_value(unit) * 0.12 for unit in enemies)
            if code == "poison_card":
                return len(enemies) * 28.0 + sum(unit.current_hp * 18.0 for unit in enemies)
            if code == "magic_claw":
                return len(enemies) * 30.0
            return len(enemies) * 32.0
        return 0.0


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
                    if skill.timing != "active" or skill.code in {"sanctuary_banish", "mountain_awakening"}:
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
                            heals = skill.code in self.services.HEAL_SKILL_CODES | {"punisher_heal", "nian_dragon_dance"}
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
            control_before = {}
            if skill.code == "sanctuary_banish":
                control_before = {unit.unit_id: self.services.r12_future_banish_value(battle, unit, profile)
                                  for unit in units if unit.player_id != actor.player_id and unit.alive}
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
                mana_weight = 42.0 if skill.code == "punisher_heal" and unit.player_id == actor.player_id and unit.unit_id != actor.unit_id else 22.0
                value = (hp - unit.current_hp) * 115.0 + (mana - unit.current_mana) * mana_weight
                value += max(0, shields - unit.total_shields()) * 20.0
                if alive and self.services.scored_death_probability(unit) > 0:
                    value += (90.0 * self.services.scored_death_probability(unit) if direct_scoring() else 90.0)
                if skill.code == "sanctuary_banish" and unit.alive and not locked and unit.has_status("圣殿放逐"):
                    value += max(0.0, control_before.get(unit.unit_id, 0.0)
                                 - self.services.r12_future_banish_value(battle, unit, profile))
                score += value if unit.player_id != actor.player_id else -value * (1.3 if hp > unit.current_hp else 1.0)
            return score


    def r12_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                         profile: DifficultyProfile) -> float:
        if skill.code == "gravity_field":
            if direct_scoring():
                return self.services._score_skill_payload(battle, actor, {"code": skill.code}, payload, profile, instant_only=False)
            branches = (([1, 1, 1], 1), ([2, 1, 1], 3), ([2, 2, 1], 3), ([2, 2, 2], 1))
            score = sum(weight * self.services.r12_realized_effect_value(battle, actor, skill, payload, profile,
                                                           gravity_values=values) for values, weight in branches) / 8.0
        else:
            score = self.services.r12_realized_effect_value(battle, actor, skill, payload, profile)
        if score <= 0:
            return -1000.0
        if skill.max_uses_per_battle == 1 and score < profile.once_per_battle_threshold:
            score -= 32.0
        return score


    def r12_costly_window(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        best = 0.0
        for skill in actor.skills:
            if skill.timing != "active" or skill.mana_cost <= 0 or skill.code in {"mountain_god_muro", "mountain_escape", "mountain_awakening"}:
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
        if code == "mountain_god_muro":
            if actor.has_status("山神术。室王"):
                return -8.0
            before = self.services.r12_costly_window(battle, actor, profile)
            with ai_probe_rollback(battle):
                actor.get_skill(code).execute(battle, actor, {})
                after = self.services.r12_costly_window(battle, actor, profile)
            gain = max(0.0, after - before)
            return max(profile.once_per_battle_threshold + 35.0, gain * 1.5 + 30.0) if gain > 0 else -8.0
        if code == "mountain_escape":
            if actor.has_status("遁术。神山"):
                return -8.0
            before_risk = self.services.r19_incoming_position_risk(battle, actor)
            critically_low = actor.current_hp <= actor.max_health * 0.25
            likely_lethal = before_risk >= actor.current_hp * 100.0 and before_risk > 0
            if not critically_low and not likely_lethal:
                return -1000.0
            before_output = self.services.r19_attack_window_value(battle, actor, profile)
            hp = actor.current_hp
            with ai_probe_rollback(battle):
                actor.get_skill(code).execute(battle, actor, {})
                healing = max(0.0, actor.current_hp - hp)
                after_risk = self.services.r19_incoming_position_risk(battle, actor)
                after_output = self.services.r19_attack_window_value(battle, actor, profile)
            gain = healing * 150.0 + max(0.0, before_risk - after_risk) * 1.3
            gain += min(1.0, max(0.0, actor.max_mana() - actor.current_mana)) * 18.0
            gain -= max(0.0, before_output - after_output) * 0.85
            if likely_lethal:
                return gain + 200.0 if gain > 14.0 else -8.0
            # At critical HP without immediate lethal pressure, use available skills first.
            return min(profile.once_per_battle_threshold + 5.0, gain - 14.0) if gain > 14.0 else -8.0
        used = [skill for skill in actor.skills if skill.max_uses_per_battle == 1 and skill.uses_this_battle > 0]
        if not used or sum(status.name == "山神计数点" for status in actor.statuses) < 8:
            return -8.0
        gain = 0.0
        with ai_probe_rollback(battle):
            for skill in used:
                skill.uses_this_battle = 0
                status = actor.get_status(skill.name)
                if status is not None:
                    actor.remove_status(status, battle)
                if skill.code in {"mountain_god_muro", "mountain_escape"}:
                    gain = max(gain, self.services.r12_preparation_score(battle, actor, skill.code, profile))
        return gain + 30.0 if gain > 0 else -8.0


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
                if candidate.code not in self.services.HEAL_SKILL_CODES | {"punisher_heal", "nian_dragon_dance"} or candidate.timing != "active":
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
        from wujiang.tactical.heroes.mechanics.true_blade_bird import skill_has_movement_effect
        with ai_probe_rollback(battle):
            units = list(battle.all_units())
            before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.alive, unit.total_shields(),
                                     {status.name for status in unit.statuses}) for unit in units}
            control = {}
            for unit in self.services.skill_effect_units(battle, actor, skill, payload):
                if skill.code == "hundred_bird_burial":
                    control[unit.unit_id] = 24.0 * sum(skill_has_movement_effect(candidate) and not unit.cannot_use_skills
                        and candidate.cooldown_remaining == 0 and candidate.mana_cost <= unit.current_mana
                        and not any(component.blocks_skill_use(battle, unit, candidate)[0] for component in unit.iter_components())
                        for candidate in unit.skills)
                elif skill.code == "nian_jade_flash":
                    control[unit.unit_id] = self.services.r13_recovery_value(battle, unit)
            protected = [unit for unit in battle.player_units(actor.player_id) if unit.alive and unit.unit_id != actor.unit_id]
            risks = {unit.unit_id: self.services.r19_incoming_position_risk(battle, unit) for unit in protected} if skill.code == "nian_roar" else {}
            mana_risk = self.services.r19_incoming_position_risk(battle, actor) if actor.hero_code == "excel_r056" and skill.mana_cost > 0 else 0.0
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
                name = "百鸟葬禁位移" if skill.code == "hundred_bird_burial" else "碧玉闪光"
                if unit.alive and name not in statuses and unit.has_status(name):
                    value += control.get(unit.unit_id, 0.0)
                score += value if unit.player_id != actor.player_id else -value * (1.3 if hp > unit.current_hp else 1.0)
            if skill.code == "nian_roar" and any(unit.alive and unit.has_status("怒吼") and "怒吼" not in before[unit.unit_id][4] for unit in units):
                score += min(90.0, sum(max(0.0, risk - self.services.r19_incoming_position_risk(battle, battle.get_unit(unit_id)))
                                       for unit_id, risk in risks.items() if battle.units.get(unit_id) is not None))
            if skill.code == "remi_chaos" and actor.alive:
                old_position = battle.declared_source_position(queued.payload)
                score += (self.services.score_move_destination(battle, actor, actor.position, self.services.hero_style(actor), profile)
                          - self.services.score_move_destination(battle, actor, old_position, self.services.hero_style(actor), profile)) * 0.7
            if actor.hero_code == "excel_r056" and actor.alive and actor.current_mana < 2 and skill.mana_cost > 0:
                score -= min(80.0, max(0.0, self.services.r19_incoming_position_risk(battle, actor) - mana_risk))
            if skill.code == "nian_dragon_dance":
                score -= 20.0  # Preserve the two-round recovery when only a little mana is missing.
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
            summon = next((unit for unit in battle.all_units() if unit.unit_id not in original and unit.hero_code == "remi_bat"), None)
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
) -> float:
    return _require_service().skill_control_bonus(battle, actor, code, payload, targets, profile, instant_only=instant_only)


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
