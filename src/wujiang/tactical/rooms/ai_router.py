"""Live battle AI selection.

Rooms call these wrappers. The process setting decides whether the next
decision uses the bounded picker or the detailed scorer. Both modules stay
in place, and a change applies without restarting the match.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional
from contextlib import contextmanager
from contextvars import ContextVar

from wujiang.platform.app_settings import ai_decision_mode
from wujiang.tactical.engine.core import Battle, Position, Unit
from wujiang.tactical.rooms import ai as detailed
from wujiang.tactical.rooms import model_ai
from wujiang.tactical.rooms import simple_ai
from wujiang.tactical.rooms import tendency

_detailed_picker = ContextVar("ai_isolated_detailed_picker", default=False)


@contextmanager
def detailed_picker_scope():
    token = _detailed_picker.set(True)
    try:
        yield
    finally:
        _detailed_picker.reset(token)


def _picker():
    if _detailed_picker.get():
        return detailed
    mode = ai_decision_mode()
    if mode == "model":
        return model_ai
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
    picker = _picker()
    if picker is detailed:
        from wujiang.tactical.rooms.ai_parallel import take_turn_result
        units = list(units)
        prepared = take_turn_result(battle, units, difficulty)
        if prepared is not None:
            return prepared
    return picker.choose_turn_bundle_action(battle, units, difficulty)


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
