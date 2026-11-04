"""Fixed movement-candidate and Doomlight attack witnesses for AI OOP refactoring."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wujiang.tactical.engine import core
from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes import base
from wujiang.tactical.heroes.mechanics.doomlight_dragon import DoomLightStatus
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (build_move_candidates, difficulty_profile,
                                      fast_move_shortlist, reviewed_attack_synergy,
                                      true_red_blast_score)


def fixture(code: str, opponents: str | list[str] = "bard", allies: list[str] | None = None):
    with mock.patch("wujiang.tactical.heroes.registry.opening_player_for_units", return_value=1), mock.patch(
        "wujiang.tactical.heroes.registry.sort_units_for_classic", side_effect=list
    ):
        battle = create_battle([code, *(allies or [])], opponents)
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    targets = battle.hero_units(2)
    actor.position = Position(2, 2)
    for index, target in enumerate(targets):
        target.position = Position(6, 2 + index)
    return battle, actor, targets


def signature(battle) -> tuple:
    rng = getattr(battle, "chess_rng", None)
    return (tuple((unit.unit_id, unit.position, unit.current_hp, unit.current_mana,
                   unit.alive, tuple((status.name, status.duration) for status in unit.statuses))
                  for unit in battle.all_units()),
            core._id_counter.peek(), base._unit_counter.peek(),
            repr(rng.getstate()) if rng is not None and hasattr(rng, "getstate") else None)


def measure(label: str, battle, value) -> dict:
    before = signature(battle)
    result = value()
    after = signature(battle)
    return {"case": label, "value": result,
            "state_rng_unchanged": after[0] == before[0] and after[3] == before[3],
            "id_delta": [after[1] - before[1], after[2] - before[2]]}


def movement_candidates(battle, actor, action) -> dict:
    entries = [(candidate.summary, candidate.score, candidate.payload)
               for candidate in build_move_candidates(battle, actor, action, difficulty_profile("standard"))]
    encoded = json.dumps(entries, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {"count": len(entries), "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "best": sorted(entries, key=lambda entry: entry[1], reverse=True)[:5]}


def main() -> None:
    rows = []
    battle, actor, (target,) = fixture("excel_r303")
    hunt = next(trait for trait in actor.traits if trait.name == "昼行追猎")
    hunt.previous_target_id = target.unit_id
    cells = [Position(x, y) for x in range(8) for y in range(8)]
    rows.append(measure("daylight_quarry_shortlist", battle,
                        lambda: [(cell.x, cell.y) for cell in fast_move_shortlist(battle, actor, cells, "fighter")]))

    battle, actor, (target,) = fixture("excel_r302")
    actor.position, target.position = Position(3, 3), Position(5, 3)
    rows.append(measure("true_red_enemy_blast", battle,
                        lambda: true_red_blast_score(battle, actor, Position(4, 3))))
    action = {item["code"]: item for item in battle.action_snapshot_for(actor)["actions"]}["move"]
    rows.append(measure("true_red_move_candidates", battle,
                        lambda: movement_candidates(battle, actor, action)))

    battle, actor, (target,) = fixture("doomlight_dragon")
    rows.append(measure("doomlight_new_infection", battle,
                        lambda: reviewed_attack_synergy(battle, actor, target, 0.25)))
    target.add_status(DoomLightStatus(actor.unit_id, from_skill=False))
    rows.append(measure("doomlight_existing_infection", battle,
                        lambda: reviewed_attack_synergy(battle, actor, target, 0.25)))

    battle, actor, (target,) = fixture("elite_soldier", "doomlight_dragon")
    rows.append(measure("doomlight_defender_retaliation", battle,
                        lambda: reviewed_attack_synergy(battle, actor, target, 0.25)))
    print(json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
