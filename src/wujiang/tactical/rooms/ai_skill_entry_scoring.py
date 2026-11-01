"""AISkillEntryScoring: shared AI scoring and queries for skill_entry."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from typing import Any, Callable, Iterable, Optional
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
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

class AISkillEntryScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    DAMAGING_SKILL_CODES: Any
    DifficultyProfile: Any
    _score_skill_payload: Callable[..., Any]
    alexander_magic_coverage_value: Callable[..., Any]
    estimate_damage: Callable[..., Any]
    nova_paid_action_adjustment: Callable[..., Any]
    r23_action_delta: Callable[..., Any]
    r23_action_state: Callable[..., Any]
    reviewed_r17_position_value: Callable[..., Any]


class AISkillEntryScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AISkillEntryScoringServices) -> None:
        self.services = services

    def alexander_expected_swap_value(self, battle: Battle, actor: Unit) -> float:
        """Price the mandatory post-attack swap before spending an attack."""
        from wujiang.tactical.heroes.mechanics.gladiator_core import gladiator_swap, gladiator_swap_destinations

        allies = [unit for unit in battle.player_units(actor.player_id)
                  if unit.unit_id != actor.unit_id and unit.alive and not unit.banished
                  and gladiator_swap_destinations(battle, actor, unit, via_skill=False) is not None]
        if not allies:
            return 0.0
        units = list(battle.player_units(actor.player_id))
        before_positions = {unit.unit_id: self.services.reviewed_r17_position_value(battle, actor, unit) for unit in units}
        before_magic = {unit.unit_id: self.services.alexander_magic_coverage_value(battle, actor, unit) for unit in units}
        best = -1000.0
        for ally in allies:
            with ai_probe_rollback(battle):
                before_state = self.services.r23_action_state(battle)
                if not gladiator_swap(battle, actor, ally, skill_code="alexander_attack_swap", via_skill=False):
                    continue
                value = self.services.r23_action_delta(actor, before_state)
                value += sum((self.services.reviewed_r17_position_value(battle, actor, unit) - before_positions[unit.unit_id]) * 0.65
                             + self.services.alexander_magic_coverage_value(battle, actor, unit) - before_magic[unit.unit_id]
                             for unit in units)
                best = max(best, value)
        return max(-100.0, min(100.0, best)) if best > -1000.0 else 0.0


    def alexander_magic_coverage_value(self, battle: Battle, actor: Unit, ally: Unit) -> float:
        if ally.player_id != actor.player_id or not ally.magic_immunity or not ally.alive or ally.position is None:
            return 0.0
        threats = [enemy for enemy in battle.enemy_units(actor.player_id)
                   if enemy.alive and enemy.position is not None and not enemy.banished and not enemy.cannot_use_skills
                   and any(skill.timing == "active" and skill.code in self.services.DAMAGING_SKILL_CODES for skill in enemy.skills)
                   and battle.distance_between_units(enemy, ally) <= enemy.normal_move_distance() + enemy.targeting_range()]
        if not threats:
            return 0.0
        return min(48.0, max(min(ally.current_hp, self.services.estimate_damage(battle, ally, enemy.stat("attack"))) * 80.0
                               for enemy in threats))




    def nova_paid_action_adjustment(self, battle: Battle, actor: Unit, code: str, payload: dict[str, Any]) -> float:
        """Account for the payment event before the skill's ordinary effect score."""
        if not battle.has_weather("电磁场") and not any(
            unit.hero_code == "excel_r305" and unit.alive for unit in battle.all_units()
        ):
            return 0.0
        try:
            cost = actor.get_skill(code).mana_cost_for_payload(battle, actor, payload)
        except (ActionError, ValueError, KeyError, TypeError):
            return 0.0
        if cost <= 0 or actor.current_mana + 1e-9 < cost:
            return 0.0
        adjustment = 0.0
        if battle.unit_in_weather("电磁场", actor):
            loss = min(actor.current_hp, actor.max_health / 8.0)
            if actor.current_hp <= actor.max_health / 8.0 + 1e-9:
                return -1000.0
            adjustment -= loss * 170.0
        for nova in battle.all_units():
            if (nova.hero_code != "excel_r305" or not nova.alive or nova.banished
                    or nova.position is None or battle.distance_between_units(nova, actor) > 3):
                continue
            adjustment += 16.0 if nova.player_id == actor.player_id else -24.0
        return adjustment


    def hell_shuffle_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.unit_id == actor.unit_id or target.player_id != actor.player_id:
            return -1000.0
        recipient = battle.effect_recipient(target)
        if (not recipient.alive or recipient.total_shields() > 0 or recipient.dodge_charges > 0
                or recipient.skill_non_damage_effects_blocked()):
            return -1000.0
        if recipient.hero_code not in {"excel_r238", "excel_r239", "excel_r240", "excel_r242", "excel_r243", "excel_r244", "excel_r245", "excel_r246", "excel_r247"}:
            return -1000.0
        if not battle.hell_rebirth_options_for(recipient):
            return -1000.0
        rebirth_value = {"excel_r238": 92.0, "excel_r239": 112.0, "excel_r240": 93.0,
                         "excel_r242": 70.0, "excel_r243": 115.0, "excel_r244": 102.0,
                         "excel_r245": 112.0, "excel_r246": 105.0, "excel_r247": 85.0}[recipient.hero_code]
        lost_position = 16.0 if recipient.turn_ready else 0.0
        current_value = recipient.current_hp * 120.0 + recipient.current_mana * 13.0 + lost_position
        if recipient.hero_code == "excel_r247":
            dogs = [unit for unit in battle.all_units() if unit.alive and not unit.banished and unit.position is not None
                    and unit.hero_code == "hell_dog_summon" and unit.summoner_id == recipient.unit_id]
            for dog in dogs:
                ring = set(battle.neighbors(dog.position))
                for unit in battle.effect_units_at_cells(list(ring)):
                    if unit.unit_id == recipient.unit_id or unit.unit_id == dog.unit_id:
                        continue
                    impact = min(unit.current_hp, battle.damage_rule.calculate_damage(3, unit.stat("defense"))) * 100.0
                    rebirth_value += impact if unit.player_id != actor.player_id else -impact
            rebirth_value -= len(dogs) * 28.0
        return rebirth_value - current_value - 22.0


_BOUND_SERVICE: AISkillEntryScoring | None = None

def bind_room(service: AISkillEntryScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AISkillEntryScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

@expected_numeric(independent=True)
def alexander_expected_swap_value(battle: Battle, actor: Unit) -> float:
    """Price the mandatory post-attack swap before spending an attack."""
    return _require_service().alexander_expected_swap_value(battle, actor)


@expected_numeric(independent=True)
def alexander_magic_coverage_value(battle: Battle, actor: Unit, ally: Unit) -> float:
    return _require_service().alexander_magic_coverage_value(battle, actor, ally)


@expected_numeric(independent=True)
def hell_shuffle_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().hell_shuffle_score(battle, actor, payload)
