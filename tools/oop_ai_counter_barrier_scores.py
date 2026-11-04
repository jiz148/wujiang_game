"""Fixed reaction scores and side effects for counter and barrier AI migration."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import _score_reaction_payload, difficulty_profile, score_reaction_payload


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(code: str):
    random.seed(20261028)
    battle = create_battle(code, "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(2, 2), Position(3, 2)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id])
    battle.start_current_turn()
    enemy.base_stats.attack = 8
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": actor.unit_id})
    return battle, actor, enemy, queued


def observe(label: str, battle, actor, queued, code: str, payload: dict):
    profile = difficulty_profile("standard")
    option = {"action_code": code}
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    internal = _score_reaction_payload(battle, actor, queued, option, payload, profile)
    public = score_reaction_payload(battle, actor, queued, option, payload, profile)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "code": code, "payload": payload, "internal": internal,
            "public": public, "state_before_after": [before, after]}


def main() -> None:
    rows = []
    for hero in ("excel_r326", "excel_r327", "excel_r337", "excel_r352"):
        battle, actor, enemy, queued = fixture(hero)
        rows.append(observe(hero + ":counter", battle, actor, queued, "counter", {}))
        enemy.base_stats.defense = 30
        rows.append(observe(hero + ":blocked", battle, actor, queued, "counter", {}))
    battle, actor, enemy, queued = fixture("excel_r225")
    for code in ("ion_shield", "quantum_shield"):
        rows.append(observe("excel_r225:" + code, battle, actor, queued, code,
                            {"target_unit_ids": [actor.unit_id]}))
        rows.append(observe("excel_r225:" + code + "_empty", battle, actor, queued, code,
                            {"target_unit_ids": []}))
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} fixed reaction scores, {output}")


if __name__ == "__main__":
    main()
