"""Fixed move and weather-score witnesses for the hero AI OOP migration."""

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
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import difficulty_profile, sandstorm_value, score_move_destination


def fixture(code: str, opponent: str):
    with mock.patch("wujiang.tactical.heroes.registry.opening_player_for_units", return_value=1), mock.patch(
        "wujiang.tactical.heroes.registry.sort_units_for_classic", side_effect=list
    ):
        battle = create_battle(code, opponent)
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    enemy = next(unit for unit in battle.hero_units(2) if unit.hero_code == opponent)
    actor.position, enemy.position = Position(2, 2), Position(5, 2)
    return battle, actor, enemy


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
    battle, actor, enemy = fixture("soul_wraith", "doomlight_dragon")
    profile = difficulty_profile("standard")
    rows.append(measure("soul_immunity_kept", battle,
                        lambda: score_move_destination(battle, actor, Position(2, 2), "fighter", profile)))
    rows.append(measure("soul_immunity_lost", battle,
                        lambda: score_move_destination(battle, actor, Position(4, 2), "fighter", profile)))
    enemy.skills = []
    rows.append(measure("soul_no_skill_pressure", battle,
                        lambda: score_move_destination(battle, actor, Position(4, 2), "fighter", profile)))
    battle, actor, _ = fixture("undead_king_lina", "bard")
    actor.current_hp = max(0.0, actor.current_hp - 0.5)
    actor.current_mana = 0
    rows.append(measure("lina_sandstorm_recovery", battle, lambda: sandstorm_value(battle, actor)))
    actor.current_hp = actor.max_health
    actor.current_mana = actor.max_mana()
    rows.append(measure("lina_sandstorm_full", battle, lambda: sandstorm_value(battle, actor)))
    print(json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
