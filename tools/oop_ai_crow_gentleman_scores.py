"""Fixed R324/R325 active-skill candidates, scores, and side effects."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.heroes.super_crow import SuperMessengerCrow
from wujiang.tactical.rooms.ai import (_score_skill_payload, difficulty_profile,
    score_skill_payload, skill_payloads_for_action)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(hero: str, *, enemy_position: Position):
    random.seed(20261028)
    battle = create_battle(hero, "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(2, 2), enemy_position
    battle.configure_turn_order([actor.unit_id, enemy.unit_id])
    battle.start_current_turn()
    return battle, actor, enemy


def observe(label: str, battle, actor, code: str, payload: dict):
    skill = actor.get_skill(code)
    action = {"code": code, "target_mode": skill.target_mode,
              "preview": skill.preview(battle, actor)}
    profile = difficulty_profile("standard")
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    candidates = skill_payloads_for_action(battle, actor, action)
    internal = _score_skill_payload(battle, actor, action, payload, profile, instant_only=False)
    public = score_skill_payload(battle, actor, action, payload, profile, instant_only=False)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "payload": payload, "candidates": candidates,
            "internal": internal, "public": public, "state_before_after": [before, after]}


def main() -> None:
    rows = []
    battle, crow, enemy = fixture("excel_r324", enemy_position=Position(4, 3))
    summon = crow.get_skill("super_messenger_crow")
    for cell in (Position(3, 2), Position(1, 1)):
        rows.append(observe("crow:summon_free", battle, crow, summon.code,
                            {"x": cell.x, "y": cell.y, "skill_code": summon.code}))
    summon.sync_turn_scope(battle)
    summon.uses_this_turn = 3
    rows.append(observe("crow:summon_paid", battle, crow, summon.code,
                        {"x": 3, "y": 2, "skill_code": summon.code}))

    formation_codes = ("triangle_crow_array", "square_crow_array",
                       "hexagram_crow_array", "eight_dragon_crow_array")
    ring = (Position(5, 5), Position(6, 5), Position(7, 5), Position(5, 6),
            Position(7, 6), Position(5, 7), Position(6, 7), Position(7, 7))
    for code in formation_codes:
        battle, crow, enemy = fixture("excel_r324", enemy_position=Position(6, 6))
        for cell in ring:
            battle.summon_unit(SuperMessengerCrow(1), cell, summoner=crow)
        skill = crow.get_skill(code)
        ravens = [unit.unit_id for unit in battle.player_units(1)
                  if unit.hero_code == "super_messenger_crow"]
        payload = {"skill_code": code, "target_unit_id": enemy.unit_id,
                   "x": 6, "y": 6, "raven_ids": ravens[:skill.crow_count]}
        rows.append(observe(f"crow:{code}", battle, crow, code, payload))
        if code == "triangle_crow_array":
            crow.actions_taken_this_turn.extend(["skill:triangle_crow_array"] * 3)
            rows.append(observe("crow:formation_limited", battle, crow, code, payload))
        if code == "square_crow_array":
            enemy.shields = 2
            rows.append(observe("crow:formation_shielded", battle, crow, code, payload))
    battle, gentleman, enemy = fixture("excel_r325", enemy_position=Position(3, 2))
    step = gentleman.get_skill("surprise_step")
    rows.append(observe("gentleman:surprise_cross", battle, gentleman, step.code,
                        {"x": 3, "y": 0, "skill_code": step.code}))
    rows.append(observe("gentleman:surprise_approach", battle, gentleman, step.code,
                        {"x": 4, "y": 1, "skill_code": step.code}))
    rows.append(observe("gentleman:surprise_invalid", battle, gentleman, step.code,
                        {"x": 4, "y": 2, "skill_code": step.code}))
    duel = gentleman.get_skill("gentleman_duel")
    landing = duel.landing_cells(battle, gentleman, enemy)[0]
    payload = {"skill_code": duel.code, "target_unit_id": enemy.unit_id,
               "dest_x": landing.x, "dest_y": landing.y}
    rows.append(observe("gentleman:duel", battle, gentleman, duel.code, payload))
    enemy.shields = 1
    rows.append(observe("gentleman:duel_shield", battle, gentleman, duel.code, payload))
    rows.append(observe("gentleman:duel_missing", battle, gentleman, duel.code, {}))

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} crow/gentleman rows, {output}")


if __name__ == "__main__":
    main()
