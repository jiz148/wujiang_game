"""Fixed R323/R290 skill candidates, scores, and scoring side effects."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.mechanics.messenger_crow import MessengerCrowSummon
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.heroes.wyvern_raider import NightSkyPartyStatus
from wujiang.tactical.rooms.ai import (_score_skill_payload, difficulty_profile,
    messenger_crow_position_score, score_skill_payload, skill_payloads_for_action)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def observe(label: str, battle, actor, code: str, payload: dict):
    skill = actor.get_skill(code)
    payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": code, **payload}
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


def wyvern_rows() -> list[dict]:
    random.seed(323)
    battle = create_battle("excel_r323", "elite_soldier")
    rider, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    mount = battle.mounted_unit_for(rider)
    assert mount is not None
    rider.position = mount.position = Position(2, 2)
    enemy.position = Position(4, 2)
    battle.configure_turn_order([rider.unit_id, enemy.unit_id])
    battle.start_current_turn()
    code = "night_sky_party"
    rows = [observe("wyvern:near", battle, rider, code, {})]
    combat = next(trait for trait in rider.traits if trait.name == "翼龙骑手追击")
    combat.attacked_ids.add(enemy.unit_id)
    rows.append(observe("wyvern:locked", battle, rider, code, {}))
    enemy.position = Position(10, 10)
    rows.append(observe("wyvern:far", battle, rider, code, {}))
    enemy.position = Position(4, 2)
    rider.add_status(NightSkyPartyStatus(), source=rider)
    rows.append(observe("wyvern:already", battle, rider, code, {}))
    return rows


def crow_rows() -> list[dict]:
    random.seed(290)
    battle = create_battle("excel_r290", "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    battle.width = battle.height = 12
    actor.position, enemy.position = Position(2, 3), Position(5, 3)
    battle.configure_turn_order([actor.unit_id, enemy.unit_id])
    battle.start_current_turn()
    rows = [observe("crow:summon_near", battle, actor, "messenger_crow", {"x": 3, "y": 3}),
            observe("crow:summon_far", battle, actor, "messenger_crow", {"x": 1, "y": 2}),
            observe("crow:summon_missing", battle, actor, "messenger_crow", {})]
    enemy.position = Position(4, 3)
    rows.append(observe("crow:summon_adjacent", battle, actor, "messenger_crow", {"x": 3, "y": 3}))
    enemy.position = Position(5, 3)
    crow_skill = actor.get_skill("messenger_crow")
    crow_skill.execute(battle, actor, {"x": 3, "y": 3})
    crow = next(unit for unit in battle.player_units(1) if unit.hero_code == "messenger_crow")
    rows.extend((observe("crow:swap", battle, actor, "crow_swap", {"target_unit_id": crow.unit_id}),
                 observe("crow:swap_missing", battle, actor, "crow_swap", {})))
    flock = actor.get_skill("great_crow_flock")
    pattern = next(cells for cells in flock.patterns(battle, actor) if enemy.position in cells)
    rows.append(observe("crow:flock_hit", battle, actor, flock.code,
                        {"cells": [cell.to_dict() for cell in pattern]}))
    enemy.position = Position(10, 10)
    rows.append(observe("crow:flock_empty", battle, actor, flock.code,
                        {"cells": [cell.to_dict() for cell in pattern]}))
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    value = messenger_crow_position_score(battle, actor, Position(3, 3), mana_cost=0.0)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    rows.append({"label": "crow:empty_attack_position", "value": value,
                 "state_before_after": [before, after]})
    return rows


def main() -> None:
    rows = wyvern_rows() + crow_rows()
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} wyvern/crow rows, {output}")


if __name__ == "__main__":
    main()
