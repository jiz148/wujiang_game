"""Fixed ship-crew active-skill candidates, scores, and side effects."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle, create_hero
from wujiang.tactical.rooms.ai import (_score_skill_payload, difficulty_profile,
    score_skill_payload, skill_payloads_for_action)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(hero: str):
    random.seed(20261028)
    battle = create_battle(hero, "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(3, 3), Position(7, 3)
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
    battle, captain, _ = fixture("excel_r328")
    ship_skill = captain.get_skill("flying_dragon_ship")
    anchors = ship_skill.destinations(battle, captain)
    if anchors:
        for anchor in (anchors[0], anchors[-1]):
            rows.append(observe("captain:summon", battle, captain, ship_skill.code,
                                {"x": anchor.x, "y": anchor.y}))
    rows.append(observe("captain:summon_missing", battle, captain, ship_skill.code, {}))

    battle, captain, _ = fixture("excel_r328")
    ship_skill = captain.get_skill("flying_dragon_ship")
    for anchor in ship_skill.destinations(battle, captain):
        ship_skill.execute(battle, captain, {"x": anchor.x, "y": anchor.y})
        if captain.get_skill("departure_signal").routes(battle, captain):
            break
        battle.remove_unit(next(unit for unit in battle.all_units() if unit.hero_code == "flying_dragon_ship_atlanta"))
    departure = captain.get_skill("departure_signal")
    routes = departure.routes(battle, captain)
    if routes:
        for route in (routes[0], routes[-1]):
            end = route[-1]
            rows.append(observe("captain:departure", battle, captain, departure.code,
                                {"x": end.x, "y": end.y}))
    rows.append(observe("captain:departure_missing", battle, captain, departure.code, {}))

    battle, adviser, _ = fixture("excel_r329")
    ally = create_hero("excel_r328", 1)
    battle.add_unit(ally, Position(4, 3))
    ally.current_mana = 1
    rows.append(observe("adviser:add_mana_ally", battle, adviser, "add_mana",
                        {"target_unit_id": ally.unit_id, "skill_code": "add_mana"}))
    rows.append(observe("adviser:add_mana_self", battle, adviser, "add_mana",
                        {"target_unit_id": adviser.unit_id, "skill_code": "add_mana"}))
    rows.append(observe("adviser:defense_one", battle, adviser, "defense_one",
                        {"target_unit_id": ally.unit_id}))
    rows.append(observe("adviser:defense_one_missing", battle, adviser, "defense_one", {}))
    rows.append(observe("adviser:flare", battle, adviser, "silver_signal_flare", {}))
    clone = create_hero("excel_r328", 1)
    clone.is_clone = True
    clone.name = "骷髅飞盗团的团长分身"
    battle.add_unit(clone, Position(3, 4))
    rows.append(observe("adviser:flare_clone", battle, adviser, "silver_signal_flare", {}))

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} ship skill rows, {output}")


if __name__ == "__main__":
    main()
