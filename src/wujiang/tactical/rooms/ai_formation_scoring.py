"""AIFormationScoring: shared AI scoring and queries for formation."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from typing import Any, Callable, Iterable, Optional
from wujiang.tactical.rooms.ai_acceleration import cached_effect_baseline, cached_move_batch, cached_move_score, cached_position_value, cached_tactical_query, decision_memo_scope, prepare_position_values, tactical_queries_active
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

class AIFormationScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    DAMAGING_SKILL_CODES: Any
    DifficultyProfile: Any
    FOLLOW_ANCHOR_EXCLUDED_KINDS: Any
    FOLLOW_COMFORT_RADIUS: Any
    FOLLOW_FRONT_PACK_SLACK: Any
    FOLLOW_LEAD_BY_ORDER: Any
    _axis_heading: Callable[..., Any]
    _chebyshev_cells: Callable[..., Any]
    _clamp_follow_cell: Callable[..., Any]
    _follow_front_pack: Callable[..., Any]
    _follow_heading_for_army: Callable[..., Any]
    _is_follow_anchor_unit: Callable[..., Any]
    distance_between_units: Callable[..., Any]
    distance_to_position: Callable[..., Any]
    follow_anchor_state: Callable[..., Any]
    follow_formation_adjustment: Callable[..., Any]
    follow_leash_adjustment: Callable[..., Any]
    fried_aura_position_value: Callable[..., Any]
    fusion_attack_plan_value: Callable[..., Any]
    fusion_position_risk: Callable[..., Any]
    great_fire_funeral_alignment_score_at: Callable[..., Any]
    hero_ai_style_for: Callable[..., Any]
    natsume_position_plan_value: Callable[..., Any]
    natsume_unit_danger: Callable[..., Any]
    offensive_reach_score_at: Callable[..., Any]
    r20_line_window_value: Callable[..., Any]
    r21_barrier_position_value: Callable[..., Any]
    r21_winged_contact_value: Callable[..., Any]
    r22_contact_value: Callable[..., Any]
    r22_gazer_window_risk: Callable[..., Any]
    r23_attack_plan_value: Callable[..., Any]
    required_attack_target: Callable[..., Any]


class AIFormationScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AIFormationScoringServices) -> None:
        self.services = services

    def _chebyshev_cells(self, origin: Position, destination: Position) -> int:
        return max(abs(int(destination.x) - int(origin.x)), abs(int(destination.y) - int(origin.y)))


    def _axis_heading(self, origin: Position, target: Position) -> tuple[int, int]:
        dx = int(target.x) - int(origin.x)
        dy = int(target.y) - int(origin.y)
        sx = 0 if dx == 0 else (1 if dx > 0 else -1)
        sy = 0 if dy == 0 else (1 if dy > 0 else -1)
        return (sx, sy)


    def _clamp_follow_cell(self, battle: Battle, x: int, y: int) -> Position:
        max_x = max(0, int(getattr(battle, "width", 1)) - 1)
        max_y = max(0, int(getattr(battle, "height", 1)) - 1)
        return Position(min(max(0, x), max_x), min(max(0, y), max_y))


    def _follow_heading_for_army(self,
        battle: Battle,
        player_id: int,
        centroid: Position,
        command: dict[str, str],
    ) -> tuple[int, int]:
        from wujiang.tactical.engine.army import march_direction

        if str(command.get("order") or "") == "seek":
            enemies = living_hostile_combatants(battle, player_id)
            if enemies:
                nearest = min(enemies, key=lambda enemy: self.services._chebyshev_cells(centroid, enemy.position))
                return self.services._axis_heading(centroid, nearest.position)
        return march_direction(command)


    def _is_follow_anchor_unit(self, unit: Unit) -> bool:
        if not unit.alive or unit.banished or unit.position is None:
            return False
        if bool(getattr(unit, "is_summon", False) or getattr(unit, "is_clone", False)):
            return False
        from wujiang.tactical.engine.army import army_kind_for_unit, is_army_soldier

        if not is_army_soldier(unit):
            return False
        if bool(getattr(unit, "is_siege_structure", False)):
            return False
        kind = str(army_kind_for_unit(unit) or "")
        return kind not in self.services.FOLLOW_ANCHOR_EXCLUDED_KINDS


    def _follow_front_pack(self, units: list[Unit], heading: tuple[int, int]) -> list[Unit]:
        if not units:
            return []
        if heading == (0, 0):
            return units
        alongs = [
            int(unit.position.x) * heading[0] + int(unit.position.y) * heading[1]
            for unit in units
        ]
        front_along = max(alongs)
        pack = [
            unit
            for unit, along in zip(units, alongs)
            if along >= front_along - self.services.FOLLOW_FRONT_PACK_SLACK
        ]
        return pack or units


    def clear_follow_anchor_cache(self, battle: Battle) -> None:
        battle._army_anchor_cache = {}


    def follow_anchor_state(self, battle: Battle, player_id: int) -> tuple[Optional[Position], tuple[int, int]]:
        cache = getattr(battle, "_army_anchor_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            battle._army_anchor_cache = cache
        key = int(player_id)
        if key in cache:
            return cache[key]
        from wujiang.tactical.engine.army import command_for_kind, default_army_orders

        soldiers = [unit for unit in battle.player_units(player_id) if self.services._is_follow_anchor_unit(unit)]
        if not soldiers:
            cache[key] = (None, (0, 0))
            return cache[key]
        probe = Position(
            int(round(sum(unit.position.x for unit in soldiers) / len(soldiers))),
            int(round(sum(unit.position.y for unit in soldiers) / len(soldiers))),
        )
        orders = getattr(battle, "army_orders", None) or default_army_orders()
        command = command_for_kind(orders, player_id, "infantry")
        heading = self.services._follow_heading_for_army(battle, player_id, probe, command)
        pack = self.services._follow_front_pack(soldiers, heading)
        centroid = Position(
            int(round(sum(unit.position.x for unit in pack) / len(pack))),
            int(round(sum(unit.position.y for unit in pack) / len(pack))),
        )
        lead = self.services.FOLLOW_LEAD_BY_ORDER.get(str(command.get("order") or ""), 3)
        if heading == (0, 0):
            lead = 0
        anchor = self.services._clamp_follow_cell(battle, centroid.x + heading[0] * lead, centroid.y + heading[1] * lead)
        cache[key] = (anchor, heading)
        return cache[key]


    def army_anchor_cell(self, battle: Battle, player_id: int) -> Optional[Position]:
        return self.services.follow_anchor_state(battle, player_id)[0]


    def follow_catch_up_skill_penalty(self, battle: Battle, actor: Unit) -> float:
        if self.services.hero_ai_style_for(battle, actor) != "follow" or actor.position is None:
            return 0.0
        if bool(getattr(actor, "is_summon", False) or getattr(actor, "is_clone", False)):
            return 0.0
        anchor, heading = self.services.follow_anchor_state(battle, actor.player_id)
        if anchor is None or heading == (0, 0):
            return 0.0
        current_along = int(actor.position.x) * heading[0] + int(actor.position.y) * heading[1]
        front_along = int(anchor.x) * heading[0] + int(anchor.y) * heading[1]
        behind = front_along - current_along
        if behind < 3:
            return 0.0
        return -30.0


    def follow_leash_adjustment(self, army_dist: int, nearest_enemy: float, offensive_gain: float) -> float:
        over = army_dist - self.services.FOLLOW_COMFORT_RADIUS
        if over <= 0:
            return (self.services.FOLLOW_COMFORT_RADIUS - army_dist) * 1.5
        penalty = over * 10.0 + over * over * 3.0
        if nearest_enemy <= 2 and army_dist <= self.services.FOLLOW_COMFORT_RADIUS + 2:
            penalty *= 0.2
        elif offensive_gain > 0:
            penalty *= 0.35
        return -penalty


    def follow_formation_adjustment(self,
        destination: Position,
        current: Optional[Position],
        anchor: Position,
        heading: tuple[int, int],
        nearest_enemy: float,
        offensive_gain: float,
        *,
        is_summon: bool = False,
    ) -> float:
        dest_dist = self.services._chebyshev_cells(anchor, destination)
        current_dist = self.services._chebyshev_cells(anchor, current) if current is not None else dest_dist
        if heading == (0, 0):
            score = self.services.follow_leash_adjustment(dest_dist, nearest_enemy, offensive_gain)
            if dest_dist < current_dist:
                score += float(current_dist - dest_dist) * 3.0
            return score
        dest_along = int(destination.x) * heading[0] + int(destination.y) * heading[1]
        front_along = int(anchor.x) * heading[0] + int(anchor.y) * heading[1]
        ahead = dest_along - front_along
        score = 0.0
        if current is not None:
            current_along = int(current.x) * heading[0] + int(current.y) * heading[1]
            if current_along < front_along and dest_along > current_along and dest_along <= front_along + 1:
                score += float(min(dest_along, front_along) - current_along) * 8.0
        slack = 2 if is_summon else 1
        if ahead > slack:
            over = ahead - slack
            penalty = over * 10.0 + over * over * 3.0
            if nearest_enemy <= 2 and dest_dist <= self.services.FOLLOW_COMFORT_RADIUS + 2:
                penalty *= 0.2
            elif offensive_gain > 0:
                penalty *= 0.35
            score -= penalty
        elif dest_dist <= self.services.FOLLOW_COMFORT_RADIUS:
            score += float(self.services.FOLLOW_COMFORT_RADIUS - dest_dist) * 1.5
        return score


    def score_move_destination(self,
        battle: Battle,
        actor: Unit,
        destination: Position,
        role: str,
        profile: DifficultyProfile,
    ) -> float:
        enemies = living_hostile_combatants(battle, actor.player_id)
        allies = [unit for unit in battle.player_units(actor.player_id) if unit.unit_id != actor.unit_id and unit.alive and unit.position is not None and not unit.banished]
        if not enemies:
            return -10.0
        forced_target = self.services.required_attack_target(battle, actor)
        movement_targets = [forced_target] if forced_target is not None else enemies
        nearest_enemy = min(self.services.distance_to_position(battle, enemy, destination) for enemy in movement_targets)
        current_distance = min(self.services.distance_between_units(battle, actor, enemy) for enemy in movement_targets)
        score = float(current_distance - nearest_enemy) * 6.0
        move_policy = getattr(actor, "ai_policy", None)
        if move_policy is not None:
            score += move_policy.early_move_score_bonus(battle, actor, destination)
        if forced_target is not None:
            score += float(current_distance - nearest_enemy) * 30.0
            if nearest_enemy <= actor.targeting_range():
                score += 140.0
        if move_policy is not None:
            score += move_policy.post_forced_move_score_bonus(battle, actor, destination)
        offensive_gain = self.services.offensive_reach_score_at(battle, actor, destination)
        score += offensive_gain * 18.0
        score += self.services.great_fire_funeral_alignment_score_at(battle, actor, destination)
        follow_behind = False
        if self.services.hero_ai_style_for(battle, actor) == "follow":
            anchor, heading = self.services.follow_anchor_state(battle, actor.player_id)
            if anchor is not None and heading != (0, 0) and actor.position is not None:
                current_along = int(actor.position.x) * heading[0] + int(actor.position.y) * heading[1]
                front_along = int(anchor.x) * heading[0] + int(anchor.y) * heading[1]
                follow_behind = front_along - current_along >= 3
            if anchor is not None:
                score += self.services.follow_formation_adjustment(
                    destination,
                    actor.position,
                    anchor,
                    heading,
                    nearest_enemy,
                    offensive_gain,
                    is_summon=bool(getattr(actor, "is_summon", False) or getattr(actor, "is_clone", False)),
                )
        if role == "support" and not follow_behind:
            nearest_ally = min((self.services.distance_to_position(battle, ally, destination) for ally in allies), default=2)
            score += max(0.0, 3.0 - nearest_ally) * 6.0
            score += max(0.0, nearest_enemy - 1.0) * profile.support_bonus
        elif role != "support":
            score += max(0.0, 4.0 - nearest_enemy) * (4.0 + profile.aggressive_bonus / 6.0)
        if getattr(battle, "fast_ai_simulation", False):
            return score
        if move_policy is not None:
            score = move_policy.post_fast_move_score(battle, actor, destination, enemies, self.services.DAMAGING_SKILL_CODES, score)
        if destination != actor.position and not actor.moved_this_turn:
            for trait in actor.traits:
                departure_score = getattr(trait, "ai_move_departure_score", None)
                if callable(departure_score):
                    score = departure_score(score)
        score += self.services.fried_aura_position_value(battle, actor, destination)
        if move_policy is not None:
            score += move_policy.aura_move_score_bonus(battle, actor, destination)
        if actor.hero_code in {"excel_r188", "excel_r224"}:
            before = self.services.r20_line_window_value(battle, actor, profile)
            with ai_probe_rollback(battle):
                actor.position = destination
                score += 0.75 * (self.services.r20_line_window_value(battle, actor, profile) - before)
        if actor.hero_code == "excel_r225":
            with ai_probe_rollback(battle):
                actor.position = destination
                score += self.services.r21_barrier_position_value(battle, actor)
        if actor.hero_code == "excel_r264":
            immediate = self.services.r21_winged_contact_value(battle, actor, allow_leap=False, allow_move=False)
            with ai_probe_rollback(battle):
                moved = destination != actor.position
                actor.position = destination
                if moved:
                    actor.normal_move_actions_used = actor.normal_move_actions_per_turn()
                    actor.normal_move_steps_used = actor.normal_move_distance()
                score += 0.7 * max(0.0, self.services.r21_winged_contact_value(battle, actor) - immediate)
        if actor.hero_code in {"excel_r327", "excel_r352"}:
            with ai_probe_rollback(battle):
                actor.position = destination
                score += 0.8 * self.services.r22_contact_value(battle, actor, allow_move=False)
                if actor.hero_code == "excel_r327":
                    score -= self.services.r22_gazer_window_risk(battle, actor)
        if actor.hero_code == "excel_r379" or actor.has_status("菊之遗击"):
            with ai_probe_rollback(battle):
                actor.position = destination
                score += 0.7 * self.services.r23_attack_plan_value(battle, actor) - 2.0 * self.services.natsume_unit_danger(battle, actor)
        if actor.hero_code == "excel_r031":
            with ai_probe_rollback(battle):
                actor.position = destination
                score += self.services.natsume_position_plan_value(battle, actor) - self.services.natsume_unit_danger(battle, actor)
        if actor.hero_code == "excel_r030":
            with ai_probe_rollback(battle):
                actor.position = destination
                score += self.services.fusion_attack_plan_value(battle, actor, allow_move=False) - self.services.fusion_position_risk(battle, actor)
        if move_policy is not None:
            score += move_policy.late_move_score_bonus(battle, actor, destination, allies)
        return score


_BOUND_SERVICE: AIFormationScoring | None = None

def bind_room(service: AIFormationScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AIFormationScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

def _chebyshev_cells(origin: Position, destination: Position) -> int:
    return _require_service()._chebyshev_cells(origin, destination)


def _axis_heading(origin: Position, target: Position) -> tuple[int, int]:
    return _require_service()._axis_heading(origin, target)


def _clamp_follow_cell(battle: Battle, x: int, y: int) -> Position:
    return _require_service()._clamp_follow_cell(battle, x, y)


def _follow_heading_for_army(
    battle: Battle,
    player_id: int,
    centroid: Position,
    command: dict[str, str],
) -> tuple[int, int]:
    return _require_service()._follow_heading_for_army(battle, player_id, centroid, command)


def _is_follow_anchor_unit(unit: Unit) -> bool:
    return _require_service()._is_follow_anchor_unit(unit)


def _follow_front_pack(units: list[Unit], heading: tuple[int, int]) -> list[Unit]:
    return _require_service()._follow_front_pack(units, heading)


def clear_follow_anchor_cache(battle: Battle) -> None:
    return _require_service().clear_follow_anchor_cache(battle)


def follow_anchor_state(battle: Battle, player_id: int) -> tuple[Optional[Position], tuple[int, int]]:
    return _require_service().follow_anchor_state(battle, player_id)


def army_anchor_cell(battle: Battle, player_id: int) -> Optional[Position]:
    return _require_service().army_anchor_cell(battle, player_id)


@expected_numeric(independent=True)
def follow_catch_up_skill_penalty(battle: Battle, actor: Unit) -> float:
    return _require_service().follow_catch_up_skill_penalty(battle, actor)


def follow_leash_adjustment(army_dist: int, nearest_enemy: float, offensive_gain: float) -> float:
    return _require_service().follow_leash_adjustment(army_dist, nearest_enemy, offensive_gain)


def follow_formation_adjustment(
    destination: Position,
    current: Optional[Position],
    anchor: Position,
    heading: tuple[int, int],
    nearest_enemy: float,
    offensive_gain: float,
    *,
    is_summon: bool = False,
) -> float:
    return _require_service().follow_formation_adjustment(destination, current, anchor, heading, nearest_enemy, offensive_gain, is_summon=is_summon)


@cached_move_score
@expected_numeric(independent=True)
def score_move_destination(
    battle: Battle,
    actor: Unit,
    destination: Position,
    role: str,
    profile: DifficultyProfile,
) -> float:
    return _require_service().score_move_destination(battle, actor, destination, role, profile)
