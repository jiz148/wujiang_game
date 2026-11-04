"""Fixed direct attack-power and state witness for skill-owned AI formulas."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import skill_attack_power


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def snapshot(battle) -> tuple[str, str, int]:
    return digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek()


def observe(hero: str, code: str, stressed: bool) -> dict:
    battle = create_battle(hero, "bard")
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == hero)
    target = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
    actor.position, target.position = Position(2, 2), Position(4, 2)
    if stressed:
        actor.current_hp = 0.4
        target.shields = 1
    skill = actor.get_skill(code)
    cells = [Position(4, 2), Position(5, 2), Position(6, 2)]
    if stressed:
        cells.reverse()
    payload = {"cells": [cell.to_dict() for cell in cells]}
    if code == "deadly_bow":
        payload["deadly_bow_points"] = 3 if stressed else actor.mana_points
    if code == "barbarian_axe":
        payload["choice_code"] = "weak" if stressed else "strong"
    if code == "lao_wave_bullet":
        payload["free_cast"] = stressed
    if code == "apocalypse":
        payload["choice_code"] = "2" if stressed else "0"
    before = snapshot(battle)
    value = skill_attack_power(battle, actor, skill, payload, target, cells)
    after = snapshot(battle)
    return {"hero": hero, "skill": code, "stressed": stressed,
            "value": value, "state_before_after": [before, after]}


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = Path(sys.argv[2])
    random.seed(20261031)
    rows = [observe(item["code"], code, stressed)
            for item in manifest for code in item["skills"] for stressed in (False, True)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} direct attack-power surfaces, {output}")


if __name__ == "__main__":
    main()
