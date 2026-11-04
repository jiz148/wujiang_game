"""Fixed score and side-effect witness for the six paid-reaction heroes."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_reaction_payload, difficulty_profile,
    r22_paid_reaction_score, score_reaction_payload)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(code: str):
    random.seed(20261029)
    battle = create_battle(code, "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(2, 2), Position(3, 2)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id])
    battle.start_current_turn()
    enemy.base_stats.attack = 8
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": actor.unit_id})
    return battle, actor, queued


def observe(label: str, battle, actor, queued, code: str, payload: dict):
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    profile = difficulty_profile("standard")
    option = {"action_code": code}
    direct = r22_paid_reaction_score(battle, actor, queued, payload, code)
    internal = _score_reaction_payload(battle, actor, queued, option, payload, profile)
    public = score_reaction_payload(battle, actor, queued, option, payload, profile)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "code": code, "payload": payload,
            "direct": direct, "internal": internal, "public": public,
            "state_before_after": [before, after]}


def main() -> None:
    rows = []
    for hero, code in (("excel_r304", "light_wall"), ("excel_r337", "light_wall"),
                       ("excel_r322", "fire_wall"), ("excel_r239", "hell_wall"),
                       ("excel_r247", "hell_wall")):
        battle, actor, queued = fixture(hero)
        payload = {"target_unit_ids": [actor.unit_id]}
        rows.append(observe(hero + ":wall", battle, actor, queued, code, payload))
        rows.append(observe(hero + ":empty", battle, actor, queued, code,
                            {"target_unit_ids": []}))
        if hero in {"excel_r239", "excel_r247"}:
            actor.current_mana = 0
            rows.append(observe(hero + ":free", battle, actor, queued,
                                "hell_wall_free", payload))

    battle, actor, queued = fixture("excel_r352")
    cells = actor.get_skill("evasion").evade_cells(battle, actor)
    for index in (0, len(cells) - 1):
        if cells:
            rows.append(observe(f"excel_r352:evasion_{index}", battle, actor, queued,
                                "evasion", {"x": cells[index].x, "y": cells[index].y}))
    rows.append(observe("excel_r352:invalid", battle, actor, queued,
                        "evasion", {"x": actor.position.x, "y": actor.position.y}))
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} paid reaction scores, {output}")


if __name__ == "__main__":
    main()
