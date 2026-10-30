"""Fixed score and side-effect witness for reaction-policy relocation."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, QueuedAction, _id_counter
from wujiang.tactical.heroes.flying_dragon_ship import ShipBorrowedSkill
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_reaction_payload, difficulty_profile,
    reaction_payloads_for_option, score_reaction_payload)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(code: str):
    random.seed(373328)
    battle = create_battle(code, "elite_soldier")
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    enemy = next(unit for unit in battle.hero_units(2))
    actor.position, enemy.position = Position(2, 2), Position(4, 2)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id])
    battle.start_current_turn()
    queued = QueuedAction(action_type="skill", actor_id=enemy.unit_id,
                          display_name="fixed threat", speed=1, payload={"type": "skill"},
                          target_unit_ids=[actor.unit_id], target_cells=[actor.position],
                          source_player_id=2, hostile=True)
    return battle, actor, enemy, queued


def record(label: str, battle, actor, queued, option: dict, payload: dict):
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    profile = difficulty_profile("standard")
    direct = _score_reaction_payload(battle, actor, queued, option, payload, profile)
    public = score_reaction_payload(battle, actor, queued, option, payload, profile)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "option": option, "payload": payload,
            "direct": direct, "public": public, "state_before_after": [before, after]}


def main() -> None:
    rows = []
    battle, actor, enemy, queued = fixture("excel_r373")
    shield = {"action_code": "infinity_instant__ion_shield"}
    rows.append(record("instant_shield", battle, actor, queued, shield,
                       {"target_unit_ids": [actor.unit_id]}))
    for source in ("machine_gun", "missile"):
        code = "infinity_instant__" + source
        option = {"action_code": code, "preview": actor.get_skill(code).preview(battle, actor)}
        payloads = reaction_payloads_for_option(battle, actor, queued, option)
        for index in (0, len(payloads) - 1):
            if payloads:
                rows.append(record(f"instant_{source}_{index}", battle, actor, queued,
                                   option, payloads[index]))
    rows.append(record("instant_unsupported", battle, actor, queued,
                       {"action_code": "infinity_instant__shensu"}, {}))

    battle, actor, enemy, queued = fixture("excel_r329")
    borrowed = ShipBorrowedSkill(actor.unit_id, actor.get_skill("light_wall")).bind(actor)
    actor.skills.append(borrowed)
    option = {"action_code": borrowed.code}
    rows.append(record("borrowed_wall", battle, actor, queued, option,
                       {"target_unit_id": actor.unit_id}))
    rows.append(record("borrowed_enemy", battle, actor, queued, option,
                       {"target_unit_id": enemy.unit_id}))
    ignored = QueuedAction(action_type=queued.action_type, actor_id=queued.actor_id,
                           display_name=queued.display_name, speed=queued.speed,
                           payload={**queued.payload, "ignore_shield": True},
                           target_unit_ids=queued.target_unit_ids, target_cells=queued.target_cells,
                           source_player_id=queued.source_player_id, hostile=True)
    rows.append(record("borrowed_ignored", battle, actor, ignored, option,
                       {"target_unit_id": actor.unit_id}))
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} reactions, {output}")


if __name__ == "__main__":
    main()
