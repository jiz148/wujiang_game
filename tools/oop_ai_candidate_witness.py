"""Fixed, read-only candidate witness for the AI payload-policy migration."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (build_skill_candidates, difficulty_profile,
    reaction_payloads_for_option, skill_payloads_for_action)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(code: str):
    random.seed(20261028)
    battle = create_battle(code, "elite_soldier")
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    enemy = next(unit for unit in battle.hero_units(2))
    actor.position, enemy.position = Position(2, 2), Position(4, 2)
    battle.configure_turn_order([actor.unit_id, enemy.unit_id])
    battle.start_current_turn()
    return battle, actor, enemy


def observe(label: str, battle, build):
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    payloads, scores = build()
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "payloads": payloads, "scores": scores,
            "state_before_after": (before, after)}


def main() -> None:
    rows = []
    battle, actor, _ = fixture("excel_r373")
    for code in ("infinity_instant__machine_gun", "infinity_instant__missile"):
        skill = actor.get_skill(code)
        action = {"code": code, "target_mode": "cell", "timing": "instant",
                  "preview": skill.preview(battle, actor)}
        rows.append(observe(code, battle, lambda action=action: (
            skill_payloads_for_action(battle, actor, action),
            [(entry.payload, entry.score) for entry in build_skill_candidates(
                battle, actor, action, difficulty_profile("standard"), instant_only=False)])))
    for code in ("infinity_instant__ion_shield", "infinity_instant__machine_gun"):
        skill = actor.get_skill(code)
        option = {"action_code": code, "preview": skill.preview(battle, actor)}
        rows.append(observe("reaction:" + code, battle, lambda option=option: (
            reaction_payloads_for_option(battle, actor, None, option), [])))

    battle, actor, _ = fixture("excel_r257")
    for code in ("shield_sweep", "shield_volley"):
        skill = actor.get_skill(code)
        action = {"code": code, "target_mode": "cell", "preview": skill.preview(battle, actor)}
        rows.append(observe(code, battle, lambda action=action: (
            skill_payloads_for_action(battle, actor, action),
            [(entry.payload, entry.score) for entry in build_skill_candidates(
                battle, actor, action, difficulty_profile("standard"), instant_only=False)])))
    from wujiang.tactical.heroes.super_crow import SuperMessengerCrow

    battle, actor, enemy = fixture("excel_r324")
    enemy.position = Position(6, 6)
    for cell in (Position(5, 5), Position(6, 5), Position(7, 5), Position(5, 6),
                 Position(7, 6), Position(5, 7), Position(6, 7), Position(7, 7)):
        battle.summon_unit(SuperMessengerCrow(actor.player_id), cell, summoner=actor)
    for code in ("triangle_crow_array", "eight_dragon_crow_array"):
        skill = actor.get_skill(code)
        action = {"code": code, "target_mode": "enemy", "preview": skill.preview(battle, actor)}
        rows.append(observe(code, battle, lambda action=action: (
            skill_payloads_for_action(battle, actor, action), [])))
    path = Path("reports/oop-ai-candidates-20261028")
    path.mkdir(parents=True, exist_ok=True)
    output = path / "witness.json"
    import sys
    if len(sys.argv) > 1:
        output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} witnesses, {sum(len(row['payloads']) for row in rows)} payloads, {output}")


if __name__ == "__main__":
    main()
