"""Fixed generic free-reaction, evasion and R256 counter witnesses."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle, create_hero
from wujiang.tactical.rooms.ai import (_score_reaction_payload, difficulty_profile,
    reaction_payloads_for_option, score_reaction_payload)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(hero: str):
    random.seed(20261028)
    battle = create_battle(hero, "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(3, 3), Position(4, 3)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id])
    battle.start_current_turn()
    enemy.base_stats.attack = 8
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": actor.unit_id})
    return battle, actor, enemy, queued


def observe(label: str, battle, actor, queued, code: str, payload: dict):
    skill = next((skill for skill in actor.skills if skill.code == code), None)
    option = {"action_code": code}
    if skill is not None:
        option["preview"] = skill.reaction_preview(battle, actor, queued)
    profile = difficulty_profile("standard")
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    candidates = reaction_payloads_for_option(battle, actor, queued, option)
    internal = _score_reaction_payload(battle, actor, queued, option, payload, profile)
    public = score_reaction_payload(battle, actor, queued, option, payload, profile)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "payload": payload, "candidates": candidates,
            "internal": internal, "public": public, "state_before_after": [before, after]}


def main() -> None:
    rows = []
    battle, actor, _, queued = fixture("excel_r256")
    rows.append(observe("r256:block", battle, actor, queued, "block", {}))
    rows.append(observe("r256:counter", battle, actor, queued, "counter", {}))
    ally = create_hero("bard", 1)
    battle.add_unit(ally, Position(3, 4))
    rows.append(observe("r256:counter_with_ally", battle, actor, queued, "counter", {}))

    battle, actor, _, queued = fixture("excel_r328")
    rows.append(observe("r328:block", battle, actor, queued, "block", {}))
    rows.append(observe("r328:counter", battle, actor, queued, "counter", {}))
    actor.current_hp = 0.25
    rows.append(observe("r328:urgent_block", battle, actor, queued, "block", {}))

    battle, actor, _, queued = fixture("excel_r198")
    code = "evasion"
    skill = actor.get_skill(code)
    option = {"action_code": code, "preview": skill.reaction_preview(battle, actor, queued)}
    candidates = reaction_payloads_for_option(battle, actor, queued, option)
    if candidates:
        selected = candidates[-1]
        rows.append(observe("r198:evasion", battle, actor, queued, code, selected))
        queued.target_cells.append(Position(selected["x"], selected["y"]))
        rows.append(observe("r198:still_targeted", battle, actor, queued, code, selected))
    rows.append(observe("r198:missing", battle, actor, queued, code, {}))

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} core reaction rows, {output}")


if __name__ == "__main__":
    main()
