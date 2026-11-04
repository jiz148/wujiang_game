"""Fixed active-skill candidates, scores and side effects for R304/R277/R322."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.flame_manager import ChimeraSummon
from wujiang.tactical.heroes.mechanics.tianyuan import TianyuanCounterStatus
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_skill_payload, difficulty_profile,
    score_skill_payload, skill_payloads_for_action)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def observe(label: str, battle, actor, code: str, payload: dict):
    skill = actor.get_skill(code)
    payload = {"type": "skill", "unit_id": actor.unit_id, **payload}
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


def willow_rows() -> list[dict]:
    random.seed(304)
    battle = create_battle(["excel_r304", "elite_soldier"], ["bard", "dark_human"])
    willow = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r304")
    ally = next(unit for unit in battle.hero_units(1) if unit is not willow)
    enemy = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
    other = next(unit for unit in battle.hero_units(2) if unit is not enemy)
    willow.position, ally.position = Position(2, 2), Position(4, 2)
    enemy.position, other.position = Position(5, 2), Position(7, 7)
    battle.configure_turn_order([willow.unit_id, enemy.unit_id, ally.unit_id, other.unit_id])
    battle.start_current_turn()
    code = "exchange"
    rows = [observe("willow:enemy", battle, willow, code,
                    {"skill_code": code, "target_unit_id": enemy.unit_id}),
            observe("willow:ally", battle, willow, code,
                    {"skill_code": code, "target_unit_id": ally.unit_id}),
            observe("willow:missing", battle, willow, code, {})]
    enemy._base_magic_immunity = True
    rows.append(observe("willow:immune", battle, willow, code,
                        {"skill_code": code, "target_unit_id": enemy.unit_id}))
    return rows


def tianyuan_rows() -> list[dict]:
    random.seed(277)
    battle = create_battle("excel_r277", "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(2, 3), Position(5, 3)
    battle.configure_turn_order([actor.unit_id, enemy.unit_id])
    battle.start_current_turn()
    rows = [observe("tianyuan:empty", battle, actor, "tianyuan", {"skill_code": "tianyuan"}),
            observe("tianyuan:breakthrough_empty", battle, actor, "tianyuan_breakthrough", {})]
    actor.add_status(TianyuanCounterStatus(3), source=actor)
    rows.extend((observe("tianyuan:stored", battle, actor, "tianyuan", {"skill_code": "tianyuan"}),
                 observe("tianyuan:breakthrough_enemy", battle, actor, "tianyuan_breakthrough",
                         {"skill_code": "tianyuan_breakthrough", "x": 5, "y": 3}),
                 observe("tianyuan:breakthrough_clear", battle, actor, "tianyuan_breakthrough",
                         {"skill_code": "tianyuan_breakthrough", "x": 2, "y": 6}),
                 observe("tianyuan:breakthrough_missing", battle, actor, "tianyuan_breakthrough", {})))
    return rows


def flame_rows() -> list[dict]:
    random.seed(322)
    battle = create_battle(["excel_r322", "excel_r302"], ["excel_r320", "bard"])
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r322")
    fire = next(unit for unit in battle.hero_units(1) if unit is not actor)
    water = next(unit for unit in battle.hero_units(2) if unit.hero_code == "excel_r320")
    bard = next(unit for unit in battle.hero_units(2) if unit is not water)
    actor.position, fire.position = Position(2, 2), Position(3, 3)
    water.position, bard.position = Position(4, 2), Position(7, 7)
    battle.configure_turn_order([actor.unit_id, water.unit_id, fire.unit_id, bard.unit_id])
    battle.start_current_turn()
    code = "chimera"
    rows = [observe("flame:chimera_free", battle, actor, code,
                    {"skill_code": code, "x": 3, "y": 2}),
            observe("flame:chimera_far", battle, actor, code,
                    {"skill_code": code, "x": 1, "y": 1}),
            observe("flame:chimera_missing", battle, actor, code, {})]
    skill = actor.get_skill(code)
    skill.sync_turn_scope(battle)
    skill.uses_this_turn = 1
    rows.append(observe("flame:chimera_paid", battle, actor, code,
                        {"skill_code": code, "x": 3, "y": 2}))
    battle.summon_unit(ChimeraSummon(1), Position(3, 2), summoner=actor)
    chimera = next(unit for unit in battle.player_units(1) if unit.hero_code == "flame_chimera")
    water.shields = 1
    rows.append(observe("flame:password", battle, actor, "flame_password",
                        {"skill_code": "flame_password", "target_unit_id": chimera.unit_id}))
    water.shields = 0
    rows.append(observe("flame:password_weak", battle, actor, "flame_password",
                        {"skill_code": "flame_password", "target_unit_id": chimera.unit_id}))
    rows.append(observe("flame:password_missing", battle, actor, "flame_password", {}))
    return rows


def main() -> None:
    rows = willow_rows() + tianyuan_rows() + flame_rows()
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} willow/tianyuan/flame rows, {output}")


if __name__ == "__main__":
    main()
