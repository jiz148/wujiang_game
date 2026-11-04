"""Fixed attached-wing AI score and rollback witness for the OOP migration."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_skill_payload, difficulty_profile,
    score_skill_payload, skill_payloads_for_action)


def snapshot(battle):
    digest = lambda value: hashlib.sha256(repr(value).encode("utf-8")).hexdigest()
    return digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek()


def fixture(low_hp: bool):
    battle = create_battle(["excel_r223", "excel_r221"], "elite_soldier")
    battle.width, battle.height = 12, 10
    wing = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r223")
    host = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r221")
    enemy = next(iter(battle.hero_units(2)))
    wing.position, host.position, enemy.position = Position(1, 3), Position(3, 3), Position(6, 3)
    battle.active_player = 1
    battle._exclusive_turn_unit_id = wing.unit_id
    wing.turn_ready = True
    for unit in (wing, host, enemy):
        unit.max_health = unit.current_hp = 10
    wing.get_skill("electronic_fusion").execute(battle, wing, {"target_unit_id": host.unit_id})
    host.turn_ready = True
    battle._exclusive_turn_unit_id = host.unit_id
    if low_hp:
        host.current_hp = 0.4
    return battle, host


def observe(low_hp: bool):
    battle, actor = fixture(low_hp)
    rows = []
    for prefix in ("fusion_piercing_shot__", "electronic_unfuse__"):
        skill = next(item for item in actor.action_skills() if item.code.startswith(prefix))
        action = {"code": skill.code, "target_mode": skill.target_mode,
                  "preview": skill.preview(battle, actor)}
        before = snapshot(battle)
        candidates = skill_payloads_for_action(battle, actor, action)
        after = snapshot(battle)
        indexes = sorted({0, len(candidates) // 2, len(candidates) - 1}) if candidates else []
        scores = []
        for index in indexes:
            payload = {"type": "skill", "unit_id": actor.unit_id,
                       "skill_code": skill.code, **candidates[index]}
            before_score = snapshot(battle)
            internal = _score_skill_payload(battle, actor, action, payload,
                                            difficulty_profile("standard"), instant_only=False)
            public = score_skill_payload(battle, actor, action, payload,
                                         difficulty_profile("standard"), instant_only=False)
            scores.append({"payload": payload, "internal": internal, "public": public,
                           "state_before_after": [before_score, snapshot(battle)]})
        rows.append({"scenario": "low_hp" if low_hp else "normal", "code": skill.code,
                     "candidates": candidates, "candidate_state_before_after": [before, after],
                     "scores": scores})
    return rows


def main():
    random.seed(901)
    rows = observe(False) + observe(True)
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} dynamic surfaces, {sum(len(row['scores']) for row in rows)} scores")


if __name__ == "__main__":
    main()
