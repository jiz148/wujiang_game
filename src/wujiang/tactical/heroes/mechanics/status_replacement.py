"""Shared source-owned status replacement."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import StatusEffect


def replace_status_by_name(battle: Battle, target: HeroUnit, status: StatusEffect) -> None:
    existing = target.get_status(status.name)
    if existing is not None:
        target.remove_status(existing, battle)
    target.add_status(status)


