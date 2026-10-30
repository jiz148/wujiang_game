"""Record exact AI candidate/score/state witnesses for a multi-hero skill batch.

The manifest selects the skill surfaces. Two deterministic board states expose
normal and stressed choices; every candidate is retained, with representative
scores sampled from its original order. The same manifest is replayed after a
source-preserving OOP migration.
"""

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


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def snapshot(battle) -> tuple[str, str, int]:
    return digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek()


def picked_payloads(candidates: list[dict], actor, ally, enemy) -> list[dict]:
    if not candidates:
        return [{}]
    indexes = {0, len(candidates) // 2, len(candidates) - 1}
    for unit in (enemy, ally):
        indexes.update(i for i, payload in enumerate(candidates)
                       if payload.get("target_unit_id") == unit.unit_id)
    return [candidates[i] for i in sorted(indexes)[:8]]


def fixture(item: dict, stressed: bool):
    code = item["code"]
    ally_code = item.get("ally", "elite_soldier")
    enemy_code = item.get("enemy", "bard")
    battle = create_battle([code, ally_code], [enemy_code, "elite_soldier"])
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    ally = next(unit for unit in battle.hero_units(1) if unit is not actor)
    enemy = next(unit for unit in battle.hero_units(2) if unit.hero_code == enemy_code)
    other = next(unit for unit in battle.hero_units(2) if unit is not enemy)
    actor.position, ally.position = Position(2, 2), Position(3, 2)
    enemy.position, other.position = Position(4, 2), Position(6, 4)
    battle.configure_turn_order([actor.unit_id, enemy.unit_id, ally.unit_id, other.unit_id])
    battle.start_current_turn()
    if code == "chanter":
        from wujiang.tactical.heroes.mechanics.chanter import ChanterCardFieldEffect
        battle.add_field_effect(ChanterCardFieldEffect(actor.unit_id, actor.player_id,
                                                       "paralysis", Position(4, 3)))
    if stressed:
        actor.current_hp = ally.current_hp = 0.4
        enemy.shields = 1
        enemy.current_mana = min(enemy.current_mana, 1)
    return battle, actor, ally, enemy


def observe(item: dict, stressed: bool) -> list[dict]:
    battle, actor, ally, enemy = fixture(item, stressed)
    profile = difficulty_profile("standard")
    rows = []
    for code in item["skills"]:
        skill = actor.get_skill(code)
        action = {"code": code, "target_mode": skill.target_mode,
                  "preview": skill.preview(battle, actor)}
        before_candidates = snapshot(battle)
        candidates = skill_payloads_for_action(battle, actor, action)
        after_candidates = snapshot(battle)
        scores = []
        for payload in picked_payloads(candidates, actor, ally, enemy):
            payload = {"type": "skill", "unit_id": actor.unit_id,
                       "skill_code": code, **payload}
            before = snapshot(battle)
            internal = _score_skill_payload(battle, actor, action, payload, profile,
                                            instant_only=False)
            public = score_skill_payload(battle, actor, action, payload, profile,
                                         instant_only=False)
            after = snapshot(battle)
            scores.append({"payload": payload, "internal": internal, "public": public,
                           "state_before_after": [before, after]})
        rows.append({"hero": item["code"], "skill": code,
                     "scenario": "stressed" if stressed else "normal",
                     "candidates": candidates,
                     "candidate_state_before_after": [before_candidates, after_candidates],
                     "scores": scores})
    return rows


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = Path(sys.argv[2])
    random.seed(901)
    rows = [row for item in manifest for stressed in (False, True)
            for row in observe(item, stressed)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} skill surfaces, {sum(len(row['scores']) for row in rows)} scores, {output}")


if __name__ == "__main__":
    main()
