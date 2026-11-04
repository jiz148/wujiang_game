"""MoveCandidateBuilder for the shared AI candidate pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Optional, Protocol
from wujiang.tactical.engine.core import ActionError, Battle, Position, QueuedAction, Unit
from wujiang.tactical.prediction import forecasting_active, note
from wujiang.tactical.heroes.ai_policy import AIReactionCandidateContext
from wujiang.tactical.heroes.ai_candidate_plan import DEFAULT_CANDIDATES
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants

if TYPE_CHECKING:
    from wujiang.tactical.rooms.ai import AICandidate, DifficultyProfile

class MoveCandidateBuilderServices(Protocol):
    AICandidate: Any
    DifficultyProfile: Any
    FAST_AI_MOVE_LIMIT: Any
    blind_paid_move_score: Any
    distance_to_position: Any
    hero_style: Any
    judgment_stone_collision_move_score: Any
    payload_is_legal: Any
    preview_positions: Any
    score_move_destination: Any
    wyvern_move_score: Any
    zero_path_effect_score: Any

class MoveCandidateBuilder:
    """Own move candidates construction."""

    def __init__(self, services: MoveCandidateBuilderServices) -> None:
        self.services = services

    def fast_move_shortlist(self, battle: Battle, actor: Unit, cells: list[Position], role: str) -> list[Position]:
        """Keep a bounded set of destinations instead of scoring the whole board.

        Melee steps toward the nearest enemy, ranged heroes look for their
        preferred distance, and support hangs back. The rest of the reachable
        area is ignored.
        """
        if len(cells) <= self.services.FAST_AI_MOVE_LIMIT or actor.position is None:
            return cells
        enemies = living_hostile_combatants(battle, actor.player_id)
        if not enemies:
            return cells[:self.services.FAST_AI_MOVE_LIMIT]
        preferred = max(1, int(actor.targeting_range()))
        origin = actor.position

        def rank(cell: Position) -> tuple[float, int]:
            enemy_distance = min(self.services.distance_to_position(battle, enemy, cell) for enemy in enemies)
            step = cell.distance_to(origin)
            if role == "support":
                return (abs(enemy_distance - (preferred + 2)), step)
            if preferred <= 1:
                return (float(enemy_distance), step)
            return (abs(enemy_distance - preferred), step)

        shortlist = sorted(cells, key=rank)
        move_policy = getattr(actor, "ai_policy", None)
        focused = move_policy.focused_move_shortlist(battle, actor, cells) if move_policy is not None else None
        if focused is not None:
            return list(dict.fromkeys([*focused, *shortlist]))[:self.services.FAST_AI_MOVE_LIMIT]
        return shortlist[:self.services.FAST_AI_MOVE_LIMIT]


    def build_move_candidates(self,
        battle: Battle,
        actor: Unit,
        action: dict[str, Any],
        profile: DifficultyProfile,
    ) -> list[AICandidate]:
        if battle.mounted_unit_for(actor) is not None:
            return []
        role = self.services.hero_style(actor)
        current_score = self.services.score_move_destination(battle, actor, actor.position, role, profile) if actor.position is not None else 0.0
        candidates: list[self.services.AICandidate] = []
        move_policy = getattr(actor, "ai_policy", None)
        if move_policy is not None:
            candidates.extend(move_policy.extra_normal_move_candidates(
                battle, actor, role, profile, self))
        cells = self.services.preview_positions(action.get("preview", {}).get("cells"))
        if getattr(battle, "fast_ai_simulation", False):
            cells = self.fast_move_shortlist(battle, actor, cells, role)
        for cell in cells:
            payload = {"type": "move", "unit_id": actor.unit_id, "x": cell.x, "y": cell.y}
            if not self.services.payload_is_legal(battle, payload):
                continue
            score = self.services.score_move_destination(battle, actor, cell, role, profile) - current_score - 1.0
            if move_policy is not None:
                score += move_policy.move_candidate_score_bonus(battle, actor, cell)
            try:
                if move_policy is not None:
                    score = move_policy.normal_move_candidate_score(
                        battle, actor, cell, profile, score, self.services)
                unit_scorer = getattr(actor, "ai_normal_move_candidate_score", None)
                if callable(unit_scorer) and score is not None:
                    score = unit_scorer(battle, cell, profile, score, self.services)
            except ActionError:
                continue
            if score is None:
                continue
            candidates.append(self.services.AICandidate(payload=payload, score=score, summary=f"move:{cell.x},{cell.y}"))
        return candidates


    def direct_adjacent_path(self, start: Position, destination: Position) -> list[Position]:
        path: list[Position] = []
        current = start
        while current != destination:
            dx = 0 if current.x == destination.x else (1 if destination.x > current.x else -1)
            dy = 0 if current.y == destination.y else (1 if destination.y > current.y else -1)
            current = current.offset(dx, dy)
            path.append(current)
        return path


    def build_zero_crossing_move_candidates(self,
        battle: Battle,
        actor: Unit,
        role: str,
        profile: DifficultyProfile,
    ) -> list[AICandidate]:
        if actor.position is None:
            return []
        remaining = int(actor.remaining_normal_move_distance(battle))
        if remaining < 2:
            return []
        current_score = self.services.score_move_destination(battle, actor, actor.position, role, profile)
        candidates: list[self.services.AICandidate] = []
        for target in battle.enemy_units(actor.player_id):
            target_cells = set(battle.unit_cells(target))
            for ingress in target_cells:
                for exit_cell in battle.neighbors(ingress):
                    if exit_cell in target_cells:
                        continue
                    if not battle.can_place_unit(actor, exit_cell, ignore=actor, mover=actor):
                        continue
                    path = self.direct_adjacent_path(actor.position, ingress)
                    if len(path) + 1 > remaining:
                        continue
                    path.append(exit_cell)
                    while len(path) + 2 <= remaining:
                        path.extend([ingress, exit_cell])
                    payload = {
                        "type": "move",
                        "unit_id": actor.unit_id,
                        "x": exit_cell.x,
                        "y": exit_cell.y,
                        "path": [cell.to_dict() for cell in path],
                    }
                    if not self.services.payload_is_legal(battle, payload):
                        continue
                    crossing_events = battle.path_crossing_units(actor, [actor.position, *path])
                    score = self.services.score_move_destination(battle, actor, exit_cell, role, profile) - current_score - 1.0
                    score += self.services.zero_path_effect_score(battle, actor, [actor.position, *path])
                    candidates.append(
                        self.services.AICandidate(
                            payload=payload,
                            score=score,
                            summary=f"move:zero-crossings={len(crossing_events)}:{exit_cell.x},{exit_cell.y}",
                        )
                    )
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return candidates[:24]
