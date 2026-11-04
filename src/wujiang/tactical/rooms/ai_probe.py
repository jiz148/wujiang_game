"""Shared rollback boundary for tactical AI scoring probes."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterable

from wujiang.platform.id_sequence import isolated_ids
from wujiang.tactical.engine.core import Battle, battle_state_rollback
from wujiang.tactical.prediction import (direct_scoring, forecasting_active, note,
                                         prediction_rollback)


@contextmanager
def ai_probe_rollback(battle: Battle, *, view: bool = False) -> Iterable[None]:
    if direct_scoring():
        from wujiang.tactical.direct_expectation import scoring_probe
        # The enemy view owns one complete restoration for nested reaction
        # previews/aliases. Individual original scoring probes stay native;
        # no numeric query or random outcome gains an extra checkpoint.
        if view:
            note("direct_view_checkpoints")
        from wujiang.tactical.direct_checkpoint import direct_checkpoint
        checkpoint = prediction_rollback(battle) if view else direct_checkpoint(battle)
        with isolated_ids(), checkpoint, scoring_probe():
            battle._ai_probe_active = True
            battle._forecasting_action = True
            yield
        return
    if forecasting_active():
        with isolated_ids(), prediction_rollback(battle):
            battle._ai_probe_active = True
            battle._forecasting_action = True
            yield
        return
    with battle_state_rollback(battle):
        battle._forecasting_action = True
        yield
