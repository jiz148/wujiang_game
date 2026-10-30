"""Fixed, side-effect-checked attack-synergy witnesses for the OOP migration."""

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
from wujiang.tactical.rooms.ai import reviewed_attack_synergy


def fixture(code: str, opponents: str | list[str] = "bard"):
    with mock.patch("wujiang.tactical.heroes.registry.opening_player_for_units", return_value=1), mock.patch(
        "wujiang.tactical.heroes.registry.sort_units_for_classic", side_effect=list
    ):
        battle = create_battle(code, opponents)
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    targets = battle.hero_units(2)
    actor.position = Position(2, 3)
    for index, target in enumerate(targets):
        target.position = Position(4, 3 + index)
    return battle, actor, targets


def signature(battle) -> tuple:
    rng = getattr(battle, "chess_rng", None)
    return (
        tuple((unit.unit_id, unit.current_hp, unit.current_mana, unit.alive,
               unit.position, tuple((status.name, status.duration) for status in unit.statuses))
              for unit in battle.all_units()),
        core._id_counter.peek(), base._unit_counter.peek(),
        repr(rng.getstate()) if rng is not None and hasattr(rng, "getstate") else None,
    )


def score(label: str, battle, actor, target, damage: float) -> dict:
    before = signature(battle)
    value = reviewed_attack_synergy(battle, actor, target, damage)
    return {"case": label, "score": float(value), "state_rng_ids_unchanged": signature(battle) == before}


def main() -> None:
    rows = []
    battle, actor, (target,) = fixture("excel_r023")
    actor.current_hp = max(0.25, actor.max_health - 0.5)
    rows.append(score("frey_lifesteal_hit", battle, actor, target, 0.25))
    rows.append(score("frey_no_damage", battle, actor, target, 0.0))

    battle, actor, (target,) = fixture("excel_r027")
    target.current_hp = 2.0
    rows.append(score("wuchang_fresh_seal", battle, actor, target, 0.25))
    rows.append(score("wuchang_lethal", battle, actor, target, 2.0))

    battle, actor, (target,) = fixture("excel_r303")
    hunt = next(trait for trait in actor.traits if trait.name == "昼行追猎")
    hunt.previous_target_id = target.unit_id
    hunt.previous_bonus = 2
    actor.current_hp = max(0.25, actor.max_health - 0.5)
    rows.append(score("daylight_repeat_quarry", battle, actor, target, 0.25))
    hunt.current_target_id = target.unit_id
    rows.append(score("daylight_locked_quarry", battle, actor, target, 0.25))

    battle, actor, (target, other) = fixture("undead_king_lina", ["bard", "ellie"])
    actor.current_mana = 0.0
    target.current_hp = 0.25
    target.current_mana = 1.0
    rows.append(score("lina_eligible_kill", battle, actor, target, 1.0))
    rows.append(score("lina_nonlethal_lock", battle, actor, other, 0.1))
    print(json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
