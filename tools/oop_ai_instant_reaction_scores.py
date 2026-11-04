"""Fixed candidates, scores and side effects for borrowed and instant reactions."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, QueuedAction, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_reaction_payload, difficulty_profile,
    reaction_payloads_for_option, score_reaction_payload)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(hero: str):
    random.seed(20261028)
    battle = create_battle(hero, "elite_soldier")
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
    skill = actor.get_skill(code)
    option = {"action_code": code, "preview": skill.reaction_preview(battle, actor, queued)}
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    candidates = reaction_payloads_for_option(battle, actor, queued, option)
    internal = _score_reaction_payload(battle, actor, queued, option, payload, profile)
    public = score_reaction_payload(battle, actor, queued, option, payload, profile)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "payload": payload, "candidates": candidates,
            "internal": internal, "public": public, "state_before_after": [before, after]}


def main() -> None:
    rows = []
    battle, actor, enemy, queued = fixture("excel_r021")
    rows.append(observe("swordsman:time_stop", battle, actor, queued, "time_stop", {}))
    ghost = actor.get_skill("ghost_step")
    cells = ghost.preview(battle, actor).get("cells", [])
    if cells:
        rows.append(observe("swordsman:ghost_step", battle, actor, queued, "ghost_step", dict(cells[-1])))
    rows.append(observe("swordsman:ghost_invalid", battle, actor, queued, "ghost_step",
                        actor.position.to_dict()))

    battle, actor, enemy, queued = fixture("li")
    rows.append(observe("li:foresight", battle, actor, queued, "foresight", {}))

    battle = create_battle(["excel_r025", "excel_r021"], "bard")
    battle.width = battle.height = 12
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r025")
    carrier = next(unit for unit in battle.hero_units(1) if unit is not actor)
    enemy = next(iter(battle.hero_units(2)))
    actor.position, carrier.position, enemy.position = Position(1, 1), Position(3, 3), Position(4, 3)
    battle.configure_turn_order([actor.unit_id, carrier.unit_id, enemy.unit_id])
    battle.start_current_turn()
    actor.position = Position(3, 2)
    battle.perform_action({"type": "skill", "unit_id": actor.unit_id,
                           "skill_code": "agency_contract", "target_unit_id": carrier.unit_id,
                           "stat_name": "attack", "copied_skill_code": "time_stop"})
    for _ in range(12):
        if battle.pending_chain is None:
            break
        battle.perform_action({"type": "chain_skip"})
    battle.perform_action({"type": "end_turn"})
    battle.perform_action({"type": "end_turn"})
    queued = QueuedAction(action_type="attack", actor_id=enemy.unit_id,
                          display_name="普攻", speed=1, payload={},
                          target_unit_ids=[carrier.unit_id], target_cells=battle.unit_cells(carrier),
                          source_player_id=2, hostile=True)
    code = "agency_borrowed_skill"
    option = {"action_code": code, "preview": actor.get_skill(code).reaction_preview(battle, actor, queued)}
    payloads = reaction_payloads_for_option(battle, actor, queued, option)
    if payloads:
        rows.append(observe("agency:borrowed_time_stop", battle, actor, queued, code, payloads[0]))
    attached = actor.get_status("代行契约附着")
    if attached is not None:
        actor.remove_status(attached, battle)
    rows.append(observe("agency:unattached", battle, actor, queued, code, {}))

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} instant reaction rows, {output}")


if __name__ == "__main__":
    main()
