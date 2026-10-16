"""Live battle AI selection.

Rooms call these wrappers. The process setting decides whether the next
decision uses the bounded picker or the detailed scorer. Both modules stay
in place, and a change applies without restarting the match.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from wujiang.platform.app_settings import ai_decision_mode
from wujiang.tactical.engine.core import Battle, Position, Unit
from wujiang.tactical.rooms import ai as detailed
from wujiang.tactical.rooms import simple_ai
from wujiang.tactical.rooms import tendency


def _picker():
    mode = ai_decision_mode()
    if mode == "tendency":
        return tendency
    if mode == "simple":
        return simple_ai
    return detailed


def choose_turn_bundle_action(
    battle: Battle,
    units: Iterable[Unit],
    difficulty: str,
) -> tuple[dict[str, Any], Optional[Unit]]:
    return _picker().choose_turn_bundle_action(battle, units, difficulty)


def choose_instant_action(
    battle: Battle,
    units: Iterable[Unit],
    difficulty: str,
) -> Optional[dict[str, Any]]:
    return _picker().choose_instant_action(battle, units, difficulty)


def choose_chain_reaction(
    battle: Battle,
    reactor: Unit,
    options: list[dict[str, Any]],
    difficulty: str,
) -> Optional[dict[str, Any]]:
    return _picker().choose_chain_reaction(battle, reactor, options, difficulty)


def choose_respawn_action(
    battle: Battle,
    unit: Unit,
    options: list[Position],
    difficulty: str,
) -> Optional[dict[str, Any]]:
    return _picker().choose_respawn_action(battle, unit, options, difficulty)


def choose_damage_choice_action(battle: Battle) -> dict[str, Any]:
    return _picker().choose_damage_choice_action(battle)
