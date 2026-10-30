"""Fixed aura and automatic-wind position witnesses for the AI OOP migration."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wujiang.tactical.engine import core
from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes import base
from wujiang.tactical.heroes.registry import create_battle, create_hero
from wujiang.tactical.rooms.ai import (difficulty_profile, electric_auto_position_value,
                                      score_move_destination, sola_harvest_position_value)


def fixture(code: str):
    with mock.patch("wujiang.tactical.heroes.registry.opening_player_for_units", return_value=1), mock.patch(
        "wujiang.tactical.heroes.registry.sort_units_for_classic", side_effect=list
    ):
        battle = create_battle(code, "bard")
    battle.width, battle.height = 16, 12
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    target = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
    actor.position, target.position = Position(1, 2), Position(8, 2)
    actor.max_health = actor.current_hp = 10
    target.max_health = target.current_hp = 10
    ally = create_hero("elite_soldier", 1)
    ally.max_health = ally.current_hp = 10
    battle.add_unit(ally, Position(10, 2) if code == "excel_r139" else Position(4, 2))
    return battle, actor, target, ally


def signature(battle):
    rng = getattr(battle, "chess_rng", None)
    return (tuple((unit.unit_id, unit.position, unit.current_hp, unit.current_mana,
                   unit.alive, tuple((status.name, status.duration) for status in unit.statuses))
                  for unit in battle.all_units()),
            core._id_counter.peek(), base._unit_counter.peek(),
            repr(rng.getstate()) if rng is not None and hasattr(rng, "getstate") else None)


def measure(label, battle, value):
    before = signature(battle)
    result = value()
    after = signature(battle)
    return {"case": label, "score": float(result),
            "state_rng_unchanged": before[0] == after[0] and before[3] == after[3],
            "id_delta": [after[1] - before[1], after[2] - before[2]]}


def main():
    rows = []
    battle, actor, _, ally = fixture("excel_r139")
    ally.current_hp, ally.current_mana = 9.5, 0
    rows.append(measure("sola_aura_now", battle,
                        lambda: sola_harvest_position_value(battle, actor, actor.position)))
    rows.append(measure("sola_aura_moved", battle,
                        lambda: sola_harvest_position_value(battle, actor, Position(5, 2))))
    rows.append(measure("sola_move_score", battle,
                        lambda: score_move_destination(battle, actor, Position(5, 2), "support",
                                                       difficulty_profile("standard"))))

    battle, actor, _, _ = fixture("excel_r166")
    rows.append(measure("electric_auto_near_ally", battle,
                        lambda: electric_auto_position_value(battle, actor, Position(3, 2))))
    rows.append(measure("electric_auto_near_enemy", battle,
                        lambda: electric_auto_position_value(battle, actor, Position(7, 2))))
    rows.append(measure("electric_move_score", battle,
                        lambda: score_move_destination(battle, actor, Position(7, 2), "fighter",
                                                       difficulty_profile("standard"))))
    print(json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
