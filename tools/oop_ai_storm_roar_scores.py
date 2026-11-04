"""Fixed reaction-score/state witness for R306 and R192."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_reaction_payload, difficulty_profile,
                                       score_reaction_payload)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def observe(label, battle, actor, queued, code, payload):
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    option = {"action_code": code}
    profile = difficulty_profile("standard")
    direct = _score_reaction_payload(battle, actor, queued, option, payload, profile)
    public = score_reaction_payload(battle, actor, queued, option, payload, profile)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "payload": payload, "direct": direct,
            "public": public, "state_before_after": [before, after]}


def main() -> None:
    rows = []
    random.seed(306192)
    battle = create_battle("excel_r306", "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(2, 2), Position(3, 2)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id])
    battle.start_current_turn()
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": actor.unit_id})
    preview = actor.get_skill("electronic_storm").preview(battle, actor)
    cells = preview.get("cells") or []
    for index in (0, len(cells) // 2, len(cells) - 1):
        if cells:
            cell = cells[index]
            rows.append(observe(f"storm_{index}", battle, actor, queued,
                                "electronic_storm", {"x": cell["x"], "y": cell["y"]}))

    random.seed(192306)
    battle = create_battle(["excel_r192", "bard"], "elite_soldier")
    battle.width, battle.height = 14, 12
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r192")
    ally = next(unit for unit in battle.hero_units(1) if unit.hero_code == "bard")
    enemy = next(iter(battle.hero_units(2)))
    actor.position, ally.position, enemy.position = Position(2, 4), Position(3, 5), Position(3, 4)
    for unit in (actor, ally, enemy):
        unit.max_health = unit.current_hp = 10
    enemy.base_stats.attack = 8
    battle.active_player = enemy.player_id
    battle._exclusive_turn_unit_id = enemy.unit_id
    enemy.turn_ready = True
    for label, target in (("roar_ally", ally), ("roar_self", actor)):
        queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                             "target_unit_id": target.unit_id})
        rows.append(observe(label, battle, actor, queued, "gladiator_roar", {}))
    actor.position, ally.position, enemy.position = Position(1, 4), Position(3, 4), Position(4, 4)
    enemy.base_stats.attack_range = 1
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": ally.unit_id})
    rows.append(observe("roar_range_fizzle", battle, actor, queued, "gladiator_roar", {}))

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} reaction scores, {output}")


if __name__ == "__main__":
    main()
