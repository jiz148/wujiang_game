"""Fixed candidate-pipeline witness for skill-owned reduction policies."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (build_skill_candidates, difficulty_profile,
                                       skill_payloads_for_action)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def snapshot(battle) -> tuple[str, str, int]:
    return digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek()


def observe(item: dict, stressed: bool) -> list[dict]:
    code = item["code"]
    battle = create_battle([code, "elite_soldier"], ["bard", "elite_soldier"])
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    ally = next(unit for unit in battle.hero_units(1) if unit is not actor)
    enemy = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
    other = next(unit for unit in battle.hero_units(2) if unit is not enemy)
    actor.position, ally.position = Position(2, 2), Position(3, 2)
    enemy.position, other.position = Position(4, 2), Position(6, 4)
    battle.configure_turn_order([actor.unit_id, enemy.unit_id, ally.unit_id, other.unit_id])
    battle.start_current_turn()
    if stressed:
        actor.current_hp = ally.current_hp = 0.4
        enemy.shields = 1
        enemy.current_mana = min(enemy.current_mana, 1)
    profile = difficulty_profile("standard")
    rows = []
    for skill_code in item["skills"]:
        skill = actor.get_skill(skill_code)
        action = {"code": skill_code, "target_mode": skill.target_mode,
                  "preview": skill.preview(battle, actor)}
        before = snapshot(battle)
        payloads = skill_payloads_for_action(battle, actor, action)
        candidates = build_skill_candidates(battle, actor, action, profile, instant_only=False)
        after = snapshot(battle)
        rows.append({"hero": code, "skill": skill_code,
                     "scenario": "stressed" if stressed else "normal",
                     "payloads": payloads,
                     "candidates": [(entry.payload, entry.score) for entry in candidates],
                     "state_before_after": [before, after]})
    return rows


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = Path(sys.argv[2])
    random.seed(20261029)
    rows = [row for item in manifest for stressed in (False, True)
            for row in observe(item, stressed)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} surfaces, {sum(len(row['payloads']) for row in rows)} payloads, "
          f"{sum(len(row['candidates']) for row in rows)} candidates, {output}")


if __name__ == "__main__":
    main()
