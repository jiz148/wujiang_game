"""Fixed skill-priority scores for the four-hero policy migration."""

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
from wujiang.tactical.rooms.ai import difficulty_profile, score_skill_payload


def fixture(code: str):
    with mock.patch("wujiang.tactical.heroes.registry.opening_player_for_units", return_value=1), mock.patch(
        "wujiang.tactical.heroes.registry.sort_units_for_classic", side_effect=list
    ):
        battle = create_battle(code, "bard")
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    enemy = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
    battle.width, battle.height = 16, 12
    actor.position, enemy.position = Position(2, 3), Position(4, 3)
    return battle, actor, enemy


def signature(battle):
    rng = getattr(battle, "chess_rng", None)
    return (tuple((unit.unit_id, unit.position, unit.current_hp, unit.current_mana,
                   unit.alive, tuple((status.name, status.duration) for status in unit.statuses))
                  for unit in battle.all_units()),
            core._id_counter.peek(), base._unit_counter.peek(),
            repr(rng.getstate()) if rng is not None and hasattr(rng, "getstate") else None)


def measure(label, battle, actor, code, payload=None):
    before = signature(battle)
    value = score_skill_payload(battle, actor, {"code": code},
                                {"skill_code": code, **(payload or {})},
                                difficulty_profile("standard"), instant_only=False)
    after = signature(battle)
    return {"case": label, "score": float(value),
            "state_rng_unchanged": before[0] == after[0] and before[3] == after[3],
            "id_delta": [after[1] - before[1], after[2] - before[2]]}


def main():
    rows = []
    battle, actor, enemy = fixture("excel_r320")
    rows.append(measure("fisher_stealth_threatened", battle, actor, "fisher_stealth"))
    enemy.position = Position(14, 10)
    rows.append(measure("fisher_stealth_safe", battle, actor, "fisher_stealth"))

    battle, actor, enemy = fixture("excel_r321")
    actor.current_mana = 3
    actor.mana_points = 0
    rows.append(measure("garden_transform_near", battle, actor, "black_transformation"))
    rows.append(measure("garden_darkness_near", battle, actor, "enter_darkness"))
    enemy.position = Position(14, 10)
    rows.append(measure("garden_transform_far", battle, actor, "black_transformation"))
    rows.append(measure("garden_darkness_far", battle, actor, "enter_darkness"))

    battle, actor, enemy = fixture("excel_r324")
    with mock.patch.object(type(actor.get_skill("triangle_crow_array")),
                           "ai_priority_score", return_value=42.0):
        rows.append(measure("crow_array_available", battle, actor, "triangle_crow_array"))
        actor.actions_taken_this_turn.extend(["skill:triangle_crow_array"] * 3)
        rows.append(measure("crow_array_limited", battle, actor, "triangle_crow_array"))

    battle, actor, enemy = fixture("excel_r306")
    rows.append(measure("storm_chain_eligible", battle, actor, "lightning_chain",
                        {"target_unit_id": enemy.unit_id}))
    enemy._base_magic_immunity = True
    rows.append(measure("storm_chain_immune", battle, actor, "lightning_chain",
                        {"target_unit_id": enemy.unit_id}))
    print(json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
