"""Fixed impact, candidate, state and RNG witness for skill-owned impact rules."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (skill_payload_has_effective_enemy_impact,
                                       skill_payload_requires_enemy_impact,
                                       skill_payloads_for_action)
from oop_ai_candidate_plan_witness import digest


def snapshot(battle) -> tuple[str, str, int]:
    return digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek()


def observe(hero: str, code: str, stressed: bool) -> dict:
    battle = create_battle([hero, "elite_soldier"], ["bard", "elite_soldier"])
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == hero)
    enemy = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
    actor.position, enemy.position = Position(2, 2), Position(4, 2)
    if stressed:
        actor.current_hp = 0.4
        enemy.shields = 1
        enemy.current_mana = min(enemy.current_mana, 1)
    skill = actor.get_skill(code)
    action = {"code": code, "target_mode": skill.target_mode,
              "preview": skill.preview(battle, actor)}
    candidates = skill_payloads_for_action(battle, actor, action)
    indexes = sorted({0, len(candidates) // 2, len(candidates) - 1}) if candidates else []
    rows = []
    for index in indexes:
        payload = candidates[index]
        before = snapshot(battle)
        requires = skill_payload_requires_enemy_impact(battle, actor, action, payload)
        impact = skill_payload_has_effective_enemy_impact(battle, actor, action, payload)
        after = snapshot(battle)
        rows.append({"index": index, "payload": payload, "requires": requires,
                     "impact": impact, "state_before_after": [before, after]})
    return {"hero": hero, "skill": code, "scenario": "stressed" if stressed else "normal",
            "raw_count": len(candidates), "observations": rows}


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = Path(sys.argv[2])
    random.seed(20261031)
    rows = [observe(item["code"], code, stressed)
            for item in manifest for code in item["skills"] for stressed in (False, True)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} skill surfaces, {sum(len(row['observations']) for row in rows)} impact probes, {output}")


if __name__ == "__main__":
    main()
