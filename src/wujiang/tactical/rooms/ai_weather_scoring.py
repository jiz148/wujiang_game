"""AIWeatherAndThreatScoring: shared AI scoring and queries for weather."""

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

class AIWeatherAndThreatScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    AICandidate: Any
    DAMAGING_SKILL_CODES: Any
    DifficultyProfile: Any
    SUPPORT_HERO_CODES: Any
    ai_reachable_positions: Callable[..., Any]
    ally_unit_value: Callable[..., Any]
    dedupe_damage_area_payloads: Callable[..., Any]
    distance_to_position: Callable[..., Any]
    estimate_damage: Callable[..., Any]
    hero_style: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    positions_to_payload: Callable[..., Any]
    preview_positions: Callable[..., Any]
    r20_line_window_value: Callable[..., Any]
    r20_pattern_attack_window_value: Callable[..., Any]
    r20_piercing_skill_window_value: Callable[..., Any]
    r20_weather_combat_value: Callable[..., Any]
    reviewed_r18_effect_score: Callable[..., Any]
    score_move_destination: Callable[..., Any]
    skill_effect_units: Callable[..., Any]
    skill_payloads_for_action: Any
    sorted_payload: Callable[..., Any]
    trim_skill_payloads_for_ai: Callable[..., Any]


class AIWeatherAndThreatScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AIWeatherAndThreatScoringServices) -> None:
        self.services = services

    def estimate_damage(self,
        battle: Battle,
        target: Unit,
        attack_power: float,
        *,
        ignore_shield: bool = False,
        half_ignore_shield: bool = False,
    ) -> float:
        attack_value = float(attack_power)
        if target.total_shields() > 0 and not ignore_shield:
            if half_ignore_shield:
                attack_value = max(0.0, attack_value - 1.0)
            else:
                return 0.0
        return target.damage_fraction_after_limits(battle.damage_rule.calculate_damage(attack_value, target.stat("defense")))


    def r20_line_window_value(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        value = 0.0
        for skill in actor.skills:
            if skill.code not in {"vitality_blast", "electronic_laser", "machine_gun"} or not skill.can_use(battle, actor, {})[0]:
                continue
            seen: set[tuple[tuple[str, int], ...]] = set()
            for cells in skill.patterns(battle, actor):
                targets = battle.effect_units_at_cells(cells)
                signature = tuple(sorted((unit.unit_id, battle.unit_hit_count_for_cells(unit, cells)) for unit in targets))
                if signature in seen or not any(unit.player_id != actor.player_id for unit in targets):
                    continue
                seen.add(signature)
                payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": skill.code,
                           "cells": [cell.to_dict() for cell in cells]}
                value = max(value, self.services.reviewed_r18_effect_score(battle, actor, skill, payload, profile))
        return value


    def r20_pattern_attack_window_value(self, battle: Battle, unit: Unit, origins: list[Position]) -> tuple[float, float]:
        """Value an area basic attack with its declared pattern, never as a bare unit target."""
        hostile = living_hostile_combatants(battle, unit.player_id)
        current = unit.position
        if current is None or not hostile:
            return 0.0, 0.0
        nearby = sorted((origin for origin in origins if origin != current),
                        key=lambda origin: min(self.services.distance_to_position(battle, target, origin) for target in hostile))[:2]
        best, pool = 0.0, 0.0
        try:
            for origin in [current, *nearby]:
                unit.position = origin
                preview = battle.basic_attack_preview_for_payload(unit, {})
                selection = preview.get("selection") or {}
                if selection.get("mode") != "pattern_cells":
                    continue
                patterns: dict[tuple[tuple[str, int], ...], tuple[list[Position], list[Unit]]] = {}
                for raw in selection.get("patterns") or []:
                    cells = self.services.preview_positions(raw)
                    targets = [target for target in battle.effect_units_at_cells(cells)
                               if target.player_id != unit.player_id and target.alive]
                    if targets:
                        signature = tuple(sorted((target.unit_id, battle.unit_hit_count_for_cells(target, cells))
                                                 for target in targets))
                        patterns.setdefault(signature, (cells, targets))
                ranked = sorted(patterns.values(), key=lambda entry: sum(target.current_hp for target in entry[1]),
                                reverse=True)[:8]
                for cells, targets in ranked:
                    payload = {"type": "attack", "unit_id": unit.unit_id, "cells": self.services.positions_to_payload(cells)}
                    attack_power = battle.basic_attack_preview_power(unit, payload)
                    damage, total_hp = 0.0, 0.0
                    for target in targets:
                        if not battle.attack_target_allowed(unit, target, payload=payload)[0]:
                            continue
                        total_hp += target.current_hp * 100.0
                        damage += min(target.current_hp, estimate_attack_damage(
                            battle, unit, target, payload, attack_power=attack_power,
                            area_cell_hits=battle.unit_hit_count_for_cells(target, cells))) * 100.0
                    best = max(best, damage)
                    pool = max(pool, total_hp)
        finally:
            unit.position = current
        return best, pool


    def r20_weather_combat_value(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        from wujiang.tactical.heroes.mechanics.racial_weather import weather_race_eligible
        value = 0.0
        for unit in list(battle.all_units()):
            if not unit.alive or unit.position is None or unit.banished:
                continue
            sign = 1.0 if unit.player_id == actor.player_id else -1.15
            with ai_probe_rollback(battle):
                # Future turn opportunities are measured without firing opening effects or RNG.
                if not unit.can_take_turn_actions(battle):
                    battle.turn_number += 1
                battle.resolving_action = None
                battle.active_player = unit.player_id
                battle._exclusive_turn_unit_id = unit.unit_id
                unit.turn_ready = True
                unit.attacks_used = 0
                unit.normal_move_actions_used = unit.normal_move_steps_used = 0
                unit.move_used = False
                origins = [unit.position]
                if not unit.cannot_move and not unit.cannot_normal_move:
                    origins += self.services.ai_reachable_positions(battle, unit, max_distance=unit.remaining_normal_move_distance(battle), use_movement_cost=True)
                best, pool = 0.0, 0.0
                needs_pattern = False
                for target in battle.effect_units(living_hostile_combatants(battle, unit.player_id)):
                    if unit.cannot_attack or not battle.unit_can_be_selected(target, actor=unit)[0]:
                        continue
                    damage = 0.0
                    current = unit.position
                    for origin in origins:
                        if self.services.distance_to_position(battle, target, origin) > unit.targeting_range():
                            continue
                        unit.position = origin
                        try:
                            if battle.attack_target_allowed(unit, target)[0]:
                                payload = {"target_unit_id": target.unit_id}
                                damage = max(damage, estimate_attack_damage(battle, unit, target, payload,
                                                                          attack_power=battle.basic_attack_preview_power(unit, payload)))
                                break
                        except ActionError as exc:
                            if str(exc) != "缺少目标格子。":
                                raise
                            needs_pattern = True
                            break
                    unit.position = current
                    if needs_pattern:
                        break
                    if damage > 0:
                        best = max(best, min(target.current_hp, damage) * 100.0)
                        pool += target.current_hp * 100.0
                if needs_pattern:
                    best, pool = self.services.r20_pattern_attack_window_value(battle, unit, origins)
                for status in list(unit.statuses):
                    if status.name == "万魔殿追加攻击":
                        unit.remove_status(status, battle)
                attempts = float(unit.attack_actions_per_turn())
                if not unit.direct_effects_blocked() and weather_race_eligible(unit, "恶魔") and battle.unit_in_weather("万魔殿", unit):
                    attempts *= 2.0  # Each completed attempt independently returns one with probability 1/2.
                value += sign * min(pool, best * attempts)
                if weather_race_eligible(unit, "天使") and battle.unit_in_weather("天空圣域", unit) and not unit.direct_effects_blocked():
                    hp = 0.0 if unit.cannot_heal else min(0.25, max(0.0, unit.max_health - unit.current_hp))
                    mana = min(1.0, max(0.0, unit.max_mana() - unit.current_mana)) if any(skill.mana_cost > 0 for skill in unit.skills) else 0.0
                    value += sign * (hp * 130.0 + mana * 25.0)
                for skill in unit.skills:
                    skill._uses_turn_number = battle.turn_number
                    skill.uses_this_turn = 0
                value += sign * (self.services.r20_line_window_value(battle, unit, profile) + self.services.r20_piercing_skill_window_value(battle, unit, profile))
        return value


    def r20_piercing_skill_window_value(self, battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
        value = 0.0
        for skill in actor.skills:
            if skill.code not in self.services.DAMAGING_SKILL_CODES:
                continue
            try:
                if not skill.can_use(battle, actor, {})[0]:
                    continue
                action = {"code": skill.code, "target_mode": skill.target_mode, "preview": skill.preview(battle, actor)}
                generated = self.services.skill_payloads_for_action(battle, actor, action)
            except (ActionError, KeyError, ValueError, TypeError):
                continue
            payloads = self.services.dedupe_damage_area_payloads(battle, [payload for payload in generated if payload.get("cells")])
            payloads += [payload for payload in generated if not payload.get("cells")]
            for payload in self.services.trim_skill_payloads_for_ai(battle, actor, payloads, limit=16):
                try:
                    if not (skill.ignores_shield_for_payload(battle, actor, payload)
                            or skill.half_ignores_shield_for_payload(battle, actor, payload)):
                        continue
                    if not any(unit.player_id != actor.player_id for unit in self.services.skill_effect_units(battle, actor, skill, payload)):
                        continue
                except (ActionError, KeyError, ValueError, TypeError):
                    continue
                value = max(value, self.services.reviewed_r18_effect_score(battle, actor, skill, payload, profile))
        return value


    def r20_weather_cast_score(self, battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
        name = {"pandemonium": "万魔殿", "sky_sanctuary": "天空圣域", "wetland_grassland": "湿地草原"}[code]
        if any(getattr(effect, "global_weather", False)
               and battle.canonical_weather_name(getattr(effect, "weather_name", "")) == name for effect in battle.field_effects):
            return -1000.0
        before = self.services.r20_weather_combat_value(battle, actor, profile)
        with ai_probe_rollback(battle):
            actor.get_skill(code).execute(battle, actor, {})
            after = self.services.r20_weather_combat_value(battle, actor, profile)
        return after - before - 10.0


    def incoming_threat_score(self, battle: Battle, target: Unit, queued_action: QueuedAction) -> float:
        if forecasting_active() and queued_action.actor_id not in battle.units:
            if target.unit_id not in queued_action.target_unit_ids:
                return 0.0
            payload = queued_action.payload or {}
            power = payload.get("declared_source_attack", payload.get("attack_power"))
            if power is not None:
                return self.services.estimate_damage(battle, target, float(power),
                    ignore_shield=bool(payload.get("ignore_shield")),
                    half_ignore_shield=bool(payload.get("half_ignore_shield"))) * 100.0
            return 65.0 if queued_action.hostile else 0.0
        if not battle.target_can_chain_against(target, queued_action):
            return 0.0
        payload = dict(queued_action.payload or {})
        source = battle.get_unit(queued_action.actor_id)
        recipient = battle.effect_recipient(target)
        if queued_action.action_type == "attack":
            if recipient.has_status("古代伤害适应·技能"):
                return 0.0
            attack_power = battle.basic_attack_preview_power(source, payload)
            return self.services.estimate_damage(
                battle,
                target,
                attack_power,
                ignore_shield=bool(payload.get("ignore_shield")),
                half_ignore_shield=bool(payload.get("half_ignore_shield")),
            ) * 100.0
        if queued_action.action_type == "skill_effect" and payload.get("effect_code") == "area_damage":
            if recipient.has_status("古代伤害适应·普攻"):
                return 0.0
            attack_power = float(payload.get("attack_power", 0.0) or 0.0)
            return self.services.estimate_damage(
                battle,
                target,
                attack_power,
                ignore_shield=bool(payload.get("ignore_shield")),
                half_ignore_shield=bool(payload.get("half_ignore_shield")),
            ) * 100.0
        if queued_action.action_type in {"skill", "skill_effect"}:
            return 65.0
        return 0.0


    def best_move_destination_score(self, battle, actor, cells, profile, *, subtract=None):
        if subtract is None:
            return max((self.services.score_move_destination(battle, actor, cell, self.services.hero_style(actor), profile) for cell in cells), default=0.0)
        return max((self.services.score_move_destination(battle, actor, cell, self.services.hero_style(actor), profile) - subtract for cell in cells), default=0.0)


    def offensive_reach_score_at(self, battle: Battle, actor: Unit, destination: Position) -> int:
        current = actor.position
        actor.position = destination
        try:
            preview_target_ids = battle.basic_attack_target_ids_for_payload(actor, {})
            target_ids = {
                str(unit_id)
                for unit_id in preview_target_ids
                if not bool(getattr(battle.units.get(str(unit_id)), "is_siege_structure", False))
            }
            return len(target_ids)
        except Exception:
            return 0
        finally:
            actor.position = current


    def primary_target_unit(self, battle: Battle, payload: dict[str, Any], targets: list[Unit]) -> Optional[Unit]:
        target_id = payload.get("target_unit_id")
        if target_id:
            return battle.get_unit(str(target_id))
        if targets:
            return targets[0]
        return None


    def hero_style(self, unit: Unit) -> str:
        code = str(getattr(unit, "hero_code", "") or "")
        return "support" if code in self.services.SUPPORT_HERO_CODES else "aggressive"


    def has_mana_point_skill(self, unit: Unit) -> bool:
        return any(float(getattr(skill, "mana_point_cost", 0) or 0) > 0 or getattr(skill, "code", "") in {"magnetic_wave", "n_skill"} for skill in unit.skills)


    def hostile_unit_value(self, unit: Unit) -> float:
        if bool(getattr(unit, "is_siege_structure", False)):
            return 0.0
        return (
            unit.level * 8.0
            + unit.stat("attack") * 5.0
            + unit.stat("defense") * 3.0
            + unit.stat("speed") * 3.0
            + unit.stat("attack_range") * 2.0
            + unit.current_mana
            + unit.current_hp * 24.0
        )


    def ally_unit_value(self, unit: Unit) -> float:
        return self.services.hostile_unit_value(unit)


    def friendly_fire_penalty(self, unit: Unit) -> float:
        return 70.0 + self.services.ally_unit_value(unit) * 0.6


    def distance_between_units(self, battle: Battle, source: Unit, target: Unit) -> int:
        return battle.distance_between_units(source, target)


    def distance_to_position(self, battle: Battle, unit: Unit, destination: Position) -> int:
        return battle.unit_distance_to_cell(unit, destination)


    def preview_positions(self, raw_cells: Any) -> list[Position]:
        cells: list[Position] = []
        if not isinstance(raw_cells, list):
            return cells
        for cell in raw_cells:
            if not isinstance(cell, dict) or cell.get("x") is None or cell.get("y") is None:
                continue
            cells.append(Position(int(cell["x"]), int(cell["y"])))
        return cells


    def positions_to_payload(self, cells: Iterable[Position]) -> list[dict[str, int]]:
        return [{"x": cell.x, "y": cell.y} for cell in cells]


    def choose_declared_target_cell(self, battle: Battle, target: Unit, candidate_cells: list[Position]) -> Optional[Position]:
        occupied = set((cell.x, cell.y) for cell in candidate_cells)
        for cell in sorted(battle.unit_cells(target), key=lambda item: (item.y, item.x)):
            if (cell.x, cell.y) in occupied:
                return cell
        return None


    def payload_destination(self, payload: dict[str, Any]) -> Optional[Position]:
        if payload.get("x") is None or payload.get("y") is None:
            return None
        return Position(int(payload["x"]), int(payload["y"]))


    def best_candidate(self, candidates: Iterable[AICandidate]) -> Optional[AICandidate]:
        best: Optional[AICandidate] = None
        for candidate in candidates:
            if best is None or candidate.score > best.score:
                best = candidate
        return best


    def best_unit_candidate(self, candidates: Iterable[tuple[Unit, AICandidate]]) -> Optional[tuple[Unit, AICandidate]]:
        best: Optional[tuple[Unit, AICandidate]] = None
        for unit, candidate in candidates:
            if best is None or candidate.score > best[1].score:
                best = (unit, candidate)
        return best


    def dedupe_payloads(self, payloads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for payload in payloads:
            key = repr(self.services.sorted_payload(payload))
            if key in seen:
                continue
            seen.add(key)
            ordered.append(payload)
        return ordered


    def sorted_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            return [(key, self.services.sorted_payload(value)) for key, value in sorted(payload.items(), key=lambda item: str(item[0]))]
        if isinstance(payload, list):
            return [self.services.sorted_payload(value) for value in payload]
        return payload


_BOUND_SERVICE: AIWeatherAndThreatScoring | None = None

def bind_room(service: AIWeatherAndThreatScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AIWeatherAndThreatScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

@expected_numeric
def estimate_damage(
    battle: Battle,
    target: Unit,
    attack_power: float,
    *,
    ignore_shield: bool = False,
    half_ignore_shield: bool = False,
) -> float:
    return _require_service().estimate_damage(battle, target, attack_power, ignore_shield=ignore_shield, half_ignore_shield=half_ignore_shield)


@expected_numeric(independent=True)
def r20_line_window_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().r20_line_window_value(battle, actor, profile)


def r20_pattern_attack_window_value(battle: Battle, unit: Unit, origins: list[Position]) -> tuple[float, float]:
    """Value an area basic attack with its declared pattern, never as a bare unit target."""
    return _require_service().r20_pattern_attack_window_value(battle, unit, origins)


@expected_numeric(independent=True)
def r20_weather_combat_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().r20_weather_combat_value(battle, actor, profile)


@expected_numeric(independent=True)
def r20_piercing_skill_window_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return _require_service().r20_piercing_skill_window_value(battle, actor, profile)


@expected_numeric(independent=True)
def r20_weather_cast_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    return _require_service().r20_weather_cast_score(battle, actor, code, profile)


@expected_numeric(independent=True)
def incoming_threat_score(battle: Battle, target: Unit, queued_action: QueuedAction) -> float:
    return _require_service().incoming_threat_score(battle, target, queued_action)


@cached_move_batch
def best_move_destination_score(battle, actor, cells, profile, *, subtract=None):
    return _require_service().best_move_destination_score(battle, actor, cells, profile, subtract=subtract)


def offensive_reach_score_at(battle: Battle, actor: Unit, destination: Position) -> int:
    return _require_service().offensive_reach_score_at(battle, actor, destination)


def primary_target_unit(battle: Battle, payload: dict[str, Any], targets: list[Unit]) -> Optional[Unit]:
    return _require_service().primary_target_unit(battle, payload, targets)


def hero_style(unit: Unit) -> str:
    return _require_service().hero_style(unit)


def has_mana_point_skill(unit: Unit) -> bool:
    return _require_service().has_mana_point_skill(unit)


def hostile_unit_value(unit: Unit) -> float:
    return _require_service().hostile_unit_value(unit)


def ally_unit_value(unit: Unit) -> float:
    return _require_service().ally_unit_value(unit)


def friendly_fire_penalty(unit: Unit) -> float:
    return _require_service().friendly_fire_penalty(unit)


def distance_between_units(battle: Battle, source: Unit, target: Unit) -> int:
    return _require_service().distance_between_units(battle, source, target)


def distance_to_position(battle: Battle, unit: Unit, destination: Position) -> int:
    return _require_service().distance_to_position(battle, unit, destination)


def preview_positions(raw_cells: Any) -> list[Position]:
    return _require_service().preview_positions(raw_cells)


def positions_to_payload(cells: Iterable[Position]) -> list[dict[str, int]]:
    return _require_service().positions_to_payload(cells)


def choose_declared_target_cell(battle: Battle, target: Unit, candidate_cells: list[Position]) -> Optional[Position]:
    return _require_service().choose_declared_target_cell(battle, target, candidate_cells)


def payload_destination(payload: dict[str, Any]) -> Optional[Position]:
    return _require_service().payload_destination(payload)


def best_candidate(candidates: Iterable[AICandidate]) -> Optional[AICandidate]:
    return _require_service().best_candidate(candidates)


def best_unit_candidate(candidates: Iterable[tuple[Unit, AICandidate]]) -> Optional[tuple[Unit, AICandidate]]:
    return _require_service().best_unit_candidate(candidates)


def dedupe_payloads(payloads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return _require_service().dedupe_payloads(payloads)


def sorted_payload(payload: Any) -> Any:
    return _require_service().sorted_payload(payload)
