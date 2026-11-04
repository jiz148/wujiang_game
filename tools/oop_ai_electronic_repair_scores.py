"""Fixed effective repair AI witness for the electronic score relocation."""

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


def snapshot(battle):
    digest = lambda value: hashlib.sha256(repr(value).encode("utf-8")).hexdigest()
    return digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek()


def main():
    random.seed(901)
    battle = create_battle(["excel_r222", "excel_r221"], "bard")
    battle.width, battle.height = 14, 12
    factory = next(unit for unit in battle.player_units(1) if unit.hero_code == "excel_r222")
    dragon = next(unit for unit in battle.player_units(1) if unit.hero_code == "excel_r221")
    enemy = next(iter(battle.hero_units(2)))
    factory.position, dragon.position, enemy.position = Position(2, 4), Position(6, 4), Position(11, 8)
    battle.active_player = factory.player_id
    battle._exclusive_turn_unit_id = factory.unit_id
    factory.turn_ready = True
    dragon.alive = False
    dragon.current_hp = 0
    battle.cleanup_dead_units()
    enemy_mech = create_hero("excel_r221", 2)
    enemy_mech.alive = False
    enemy_mech.current_hp = 0
    battle.destroyed_units.append(enemy_mech)
    skill = factory.get_skill("electronic_repair")
    action = {"code": skill.code, "target_mode": skill.target_mode,
              "preview": skill.preview(battle, factory)}
    before_candidates = snapshot(battle)
    candidates = skill_payloads_for_action(battle, factory, action)
    after_candidates = snapshot(battle)
    rows = []
    for target in (dragon, enemy_mech):
        cell = skill.legal_destinations(battle, factory, target)[0]
        payload = {"type": "skill", "unit_id": factory.unit_id,
                   "skill_code": skill.code, "revive_unit_id": target.unit_id,
                   "x": cell.x, "y": cell.y}
        before = snapshot(battle)
        internal = _score_skill_payload(battle, factory, action, payload,
                                        difficulty_profile("standard"), instant_only=False)
        public = score_skill_payload(battle, factory, action, payload,
                                     difficulty_profile("standard"), instant_only=False)
        rows.append({"target": target.unit_id, "payload": payload,
                     "internal": internal, "public": public,
                     "state_before_after": [before, snapshot(battle)]})
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"candidates": candidates,
                                  "candidate_state_before_after": [before_candidates, after_candidates],
                                  "scores": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(candidates)} candidates, {len(rows)} scores")


if __name__ == "__main__":
    main()
