"""AIAttackScoring: shared AI scoring and queries for attack."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Protocol
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

class AIAttackScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    DifficultyProfile: Any
    alexander_expected_swap_value: Callable[..., Any]
    allied_attack_heal_score: Callable[..., Any]
    feiwang_attack_value: Callable[..., Any]
    friendly_fire_penalty: Callable[..., Any]
    fusion_attack_value: Callable[..., Any]
    has_cat_retaliation: Callable[..., Any]
    hero_style: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    messenger_crow_position_score: Callable[..., Any]
    natsume_support_attack_score: Callable[..., Any]
    preview_positions: Callable[..., Any]
    r13_attack_value: Callable[..., Any]
    r21_attack_value: Callable[..., Any]
    r22_attack_value: Callable[..., Any]
    r23_attack_value: Callable[..., Any]
    red_attack_value: Callable[..., Any]
    required_attack_target: Callable[..., Any]
    reviewed_attack_synergy: Callable[..., Any]
    scored_kill_probability: Callable[..., Any]
    attack_payloads_for_action: Callable[..., Any]


class AIAttackScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AIAttackScoringServices) -> None:
        self.services = services

    def legal_attack_payload(self, battle: Battle, actor: Unit, target: Unit) -> Optional[dict[str, Any]]:
        """Choose one legal declaration for a future attack estimate."""
        declared = battle.declared_cell_for_target(actor, target, {})
        if declared is None:
            return None
        payload = {"type": "attack", "unit_id": actor.unit_id,
                   "target_unit_id": target.unit_id, "x": declared.x, "y": declared.y}
        try:
            return payload if battle.attack_target_allowed(actor, target, payload=payload)[0] else None
        except ActionError:
            pass
        # Pattern attacks require selected cells; the ordinary declaration
        # builder knows each component's preview shape.
        try:
            preview = battle.basic_attack_preview_for_payload(actor)
            candidates = self.services.attack_payloads_for_action(
                battle, actor, {"kind": "attack", "preview": preview})
        except ActionError:
            return None
        target_cells = set(battle.unit_cells(target))
        for candidate in candidates:
            selected_cells = battle.payload_positions(candidate, "attack_cells") or battle.payload_positions(candidate, "cells")
            if not target_cells.intersection(selected_cells):
                continue
            # The component preview supplies legal patterns from this origin.
            # Rechecking every pattern inside nested opportunity probes can
            # recursively project summoned traits for no additional selection.
            return candidate
        return None

    def required_attack_target(self, battle: Battle, actor: Unit) -> Optional[Unit]:
        """Expose reusable taunt-style target contracts to movement and attack scoring."""
        for component in actor.iter_components():
            target_id = str(getattr(component, "required_attack_target_id", "") or "")
            if not target_id:
                continue
            forces = getattr(component, "forces_attack_target", None)
            if callable(forces) and not bool(forces(battle)):
                continue
            target = battle.units.get(target_id)
            if target is not None and target.alive and target.position is not None and not target.banished:
                return target
        return None


    def attack_effect_units(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
        if "attack_cells" in resolved_payload:
            return [unit for unit in battle.effect_units_at_cells(battle.payload_positions(resolved_payload, "attack_cells"))
                    if unit.player_id != actor.player_id or all(component.basic_attack_area_affects_allies(
                        battle, actor, resolved_payload) for component in actor.iter_components())]
        target_id = str(payload.get("target_unit_id") or "")
        if target_id:
            target = battle.units.get(target_id)
            return [target] if target is not None else []
        resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
        cells = battle.payload_positions(resolved_payload, "attack_cells")
        if not cells:
            cells = self.services.preview_positions(payload.get("cells"))
        return battle.effect_units_at_cells(cells)


    def has_cat_retaliation(self, unit: Unit) -> bool:
        return any(
            component.name == "猫叔反制" and component.__class__.__name__ == "CatRetaliationTrait"
            for component in unit.iter_components()
        )


    def cat_retaliation_action_penalty(self, battle: Battle, actor: Unit, targets: Iterable[Unit]) -> float:
        """Price the mana loss, forced pull, and post-action lock caused by targeting Cat Uncle."""
        cats = {target.unit_id: target for target in targets if target.unit_id != actor.unit_id and self.services.has_cat_retaliation(target)}
        if not cats:
            return 0.0
        remaining_active_options = sum(
            1
            for skill in actor.skills
            if str(getattr(skill, "timing", "active")) in {"active", "instant"}
            and int(getattr(skill, "cooldown_remaining", 0) or 0) <= 0
            and (
                getattr(skill, "max_uses_per_turn", None) is None
                or int(getattr(skill, "uses_this_turn", 0) or 0) < int(skill.max_uses_per_turn)
            )
        )
        can_still_move = actor.remaining_normal_move_distance(battle) > 0
        lock_cost = (28.0 if can_still_move else 8.0) + min(3, remaining_active_options) * 14.0
        mana_cost = min(1.0, actor.current_mana) * 30.0
        penalty = 0.0
        for cat in cats.values():
            hostile_pull_cost = 0.0
            if cat.player_id != actor.player_id:
                hostile_pull_cost = 26.0 + cat.stat("attack") * cat.attack_actions_per_turn() * 6.0
            penalty += mana_cost + lock_cost + hostile_pull_cost
        return penalty


    def messenger_crow_position_score(self, battle: Battle, actor: Unit, cell: Position, *, mana_cost: float,
                                      excluded_crow_id: str | None = None) -> float:
        from wujiang.tactical.heroes.mechanics.messenger_crow import MessengerCrowSkill
        return MessengerCrowSkill.position_score(battle, actor, cell, mana_cost=mana_cost,
                                                 excluded_crow_id=excluded_crow_id)


    def scored_death_probability(self, unit: Unit) -> float:
        if direct_scoring():
            from wujiang.tactical.direct_expectation import expected_death
            return expected_death(unit)
        return float(not unit.alive)


    def scored_kill_probability(self, damage: float, hp: float) -> float:
        if direct_scoring():
            return getattr(damage, "kill_probability", float(damage >= hp))
        return float(damage >= hp)


    def score_attack_payload(self,
        battle: Battle,
        actor: Unit,
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        if payload.get("attack_variant") == "crow_spawn":
            if payload.get("x") is None or payload.get("y") is None:
                return -1000.0
            return self.services.messenger_crow_position_score(battle, actor, Position(int(payload["x"]), int(payload["y"])),
                                                 mana_cost=0.0) - 9.0
        if actor.hero_code == "excel_r056":
            return self.services.r13_attack_value(battle, actor, payload)
        if actor.hero_code == "excel_r379" or payload.get("attack_variant") == "kiku_legacy":
            return self.services.r23_attack_value(battle, actor, payload)
        if payload.get("target_unit_id"):
            target = battle.units.get(str(payload["target_unit_id"]))
            if target is not None and (actor.hero_code in {"excel_r327", "excel_r337", "excel_r352"}
                                      or battle.effect_recipient(target).hero_code == "excel_r327"):
                return self.services.r22_attack_value(battle, actor, target, payload)
        if actor.hero_code in {"excel_r225", "excel_r264", "excel_r326"} and payload.get("target_unit_id"):
            return self.services.r21_attack_value(battle, actor, battle.get_unit(str(payload["target_unit_id"])), payload)
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if actor.hero_code in {"excel_r187", "excel_r188", "excel_r224"} and target is not None and target.player_id != actor.player_id:
            power = battle.basic_attack_preview_power(actor, payload)
            impact = probe_attack_damage_impact(battle, actor, target, payload, attack_power=power)
            value = min(target.current_hp, impact.damage) * 100.0
            if self.services.scored_kill_probability(impact.damage, target.current_hp - 1e-9) > 0:
                value += 90.0 * self.services.scored_kill_probability(impact.damage, target.current_hp - 1e-9)
            elif impact.changed_target and impact.damage <= 0:
                value += 20.0
            from wujiang.tactical.heroes.mechanics.racial_weather import weather_race_eligible
            if value > 0 and not actor.direct_effects_blocked() and weather_race_eligible(actor, "恶魔") and battle.unit_in_weather("万魔殿", actor):
                value += 0.5 * min(max(0.0, target.current_hp - impact.damage), impact.damage) * 100.0
            return value - 4.0
        if target is not None and target.player_id == actor.player_id and any(component.name == "风壁赠予" for component in actor.iter_components()):
            return self.services.natsume_support_attack_score(battle, actor, payload)
        if actor.hero_code == "excel_r030":
            return self.services.fusion_attack_value(battle, actor, payload)
        if actor.hero_code == "excel_r029" or actor.has_status("无限") or actor.has_status("武装复制"):
            return self.services.red_attack_value(battle, actor, payload)
        attack_policy = getattr(actor, "ai_policy", None)
        if not payload.get("target_unit_id"):
            resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
            cells = battle.payload_positions(resolved_payload, "attack_cells")
            if not cells:
                cells = self.services.preview_positions(payload.get("cells"))
            attack_power = battle.basic_attack_preview_power(actor, payload)
            score = 0.0
            forced_target = self.services.required_attack_target(battle, actor)
            drain_room = max(0.0, actor.max_mana() - actor.current_mana)
            area_state = (attack_policy.begin_area_attack_score(battle, actor, payload, drain_room)
                          if attack_policy is not None else None)
            for target in battle.effect_units_at_cells(cells):
                if target.player_id == actor.player_id:
                    if all(component.basic_attack_area_affects_allies(battle, actor, resolved_payload)
                           for component in actor.iter_components()):
                        score -= self.services.friendly_fire_penalty(target)
                    if forced_target is not None and target.unit_id == forced_target.unit_id:
                        score += 650.0
                    continue
                hit_count = max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)
                expected_damage = estimate_attack_damage(
                    battle,
                    actor,
                    target,
                    resolved_payload,
                    attack_power=attack_power,
                    area_cell_hits=hit_count,
                )
                score += min(target.current_hp, expected_damage) * 100.0
                if attack_policy is not None:
                    score += attack_policy.area_hit_bonus(battle, actor, target, expected_damage, area_state)
                if actor.hero_code == "dragon_mount":
                    with ai_probe_rollback(battle):
                        defense, speed = target.stat("defense"), target.stat("speed")
                        battle.resolve_attack_damage(actor, target, action_name="龙爪", payload=resolved_payload, tags={"area_attack"})
                        score += max(0.0, defense - target.stat("defense")) * 26.0 + max(0.0, speed - target.stat("speed")) * 14.0
                if self.services.scored_kill_probability(expected_damage, target.current_hp - 1e-9) > 0:
                    score += 95.0 * self.services.scored_kill_probability(expected_damage, target.current_hp - 1e-9)
                score += self.services.hostile_unit_value(target) * 0.65
                forced_target = self.services.required_attack_target(battle, actor)
                if forced_target is not None and target.unit_id == forced_target.unit_id:
                    score += 500.0
            if self.services.hero_style(actor) != "support":
                score += profile.aggressive_bonus
            return score
        target = battle.get_unit(str(payload["target_unit_id"]))
        if actor.has_status("里次元大剑") or getattr(actor, "hero_code", "") == "excel_r028":
            if target.player_id != actor.player_id:
                return self.services.feiwang_attack_value(battle, actor, target, payload)
        if target.player_id == actor.player_id and battle.allied_basic_heal_amount(actor, target) > 0:
            return self.services.allied_attack_heal_score(battle, actor, payload)
        attack_power = battle.basic_attack_preview_power(actor, payload)
        cells = battle.payload_positions(battle.resolved_basic_attack_payload(actor, payload), "attack_cells")
        hit_count = max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)
        expected_damage = estimate_attack_damage(
            battle,
            actor,
            target,
            payload,
            attack_power=attack_power,
            area_cell_hits=hit_count,
        )
        if target.player_id == actor.player_id:
            required = self.services.required_attack_target(battle, actor)
            return ((650.0 if required is not None and required.unit_id == target.unit_id else -1000.0)
                    - self.services.friendly_fire_penalty(target) - min(expected_damage, target.current_hp) * 120.0)
        score = min(expected_damage, target.current_hp) * 100.0
        if self.services.scored_kill_probability(expected_damage, target.current_hp - 1e-9) > 0:
            score += 95.0 * self.services.scored_kill_probability(expected_damage, target.current_hp - 1e-9)
        score += self.services.hostile_unit_value(target) * 0.8
        if self.services.hero_style(actor) != "support":
            score += profile.aggressive_bonus
        if str(payload.get("attack_variant") or "") == "triple":
            ordinary = {**payload, "attack_variant": "default"}
            normal_damage = estimate_attack_damage(battle, actor, target, ordinary, attack_power=battle.basic_attack_preview_power(actor, ordinary))
            score -= 35.0 if min(normal_damage, target.current_hp) >= min(expected_damage, target.current_hp) else 8.0 + max(0.0, min(target.current_hp, normal_damage * 3) - expected_damage) * 100.0
        if attack_policy is not None:
            score += attack_policy.single_target_attack_bonus(battle, actor, target, payload, expected_damage)
        forced_target = self.services.required_attack_target(battle, actor)
        if forced_target is not None and target.unit_id == forced_target.unit_id:
            score += 500.0
        score += self.services.reviewed_attack_synergy(battle, actor, target, expected_damage)
        if attack_policy is not None:
            score += attack_policy.attack_followup_bonus(battle, actor, target, payload, expected_damage)
        if actor.hero_code == "excel_r194":
            score += self.services.alexander_expected_swap_value(battle, actor)
        return score


_BOUND_SERVICE: AIAttackScoring | None = None

def bind_room(service: AIAttackScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AIAttackScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

def required_attack_target(battle: Battle, actor: Unit) -> Optional[Unit]:
    """Expose reusable taunt-style target contracts to movement and attack scoring."""
    return _require_service().required_attack_target(battle, actor)


def attack_effect_units(battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
    return _require_service().attack_effect_units(battle, actor, payload)


def has_cat_retaliation(unit: Unit) -> bool:
    return _require_service().has_cat_retaliation(unit)


@expected_numeric(independent=True)
def messenger_crow_position_score(battle: Battle, actor: Unit, cell: Position, *, mana_cost: float,
                                  excluded_crow_id: str | None = None) -> float:
    return _require_service().messenger_crow_position_score(battle, actor, cell, mana_cost=mana_cost, excluded_crow_id=excluded_crow_id)


def scored_death_probability(unit: Unit) -> float:
    return _require_service().scored_death_probability(unit)


def scored_kill_probability(damage: float, hp: float) -> float:
    return _require_service().scored_kill_probability(damage, hp)


@expected_numeric(independent=True)
def score_attack_payload(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service().score_attack_payload(battle, actor, payload, profile)
