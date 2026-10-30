"""Fixed Lao, Natsume and Sakura reaction candidates, scores and side effects."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, QueuedAction, _id_counter
from wujiang.tactical.heroes.mechanics.natsume import WindWallCounterStatus
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_reaction_payload, difficulty_profile,
    natsume_wall_score, reaction_payloads_for_option, score_reaction_payload)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def observe(label: str, battle, actor, queued, code: str, payload: dict):
    profile = difficulty_profile("standard")
    skill = actor.get_skill(code)
    option = {"action_code": code, "preview": skill.reaction_preview(battle, actor, queued)}
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    candidates = reaction_payloads_for_option(battle, actor, queued, option)
    internal = _score_reaction_payload(battle, actor, queued, option, payload, profile)
    public = score_reaction_payload(battle, actor, queued, option, payload, profile)
    direct = natsume_wall_score(battle, actor, queued, payload) if code == "natsume_wind_wall" else None
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "payload": payload, "candidates": candidates,
            "internal": internal, "public": public, "direct": direct,
            "state_before_after": [before, after]}


def lao_rows():
    random.seed(20261028)
    battle = create_battle("excel_r033", "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(3, 3), Position(4, 3)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id])
    battle.start_current_turn()
    enemy.base_stats.attack = 8
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": actor.unit_id})
    return [observe("lao:" + stat, battle, actor, queued, "lao_damage_stat_cancel", {"stat_name": stat})
            for stat in ("attack", "defense", "speed", "attack_range", "invalid")]


def natsume_rows():
    random.seed(20261029)
    battle = create_battle(["excel_r031", "bard"], "elite_soldier")
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == "excel_r031")
    ally = next(unit for unit in battle.hero_units(1) if unit is not actor)
    enemy = next(iter(battle.hero_units(2)))
    actor.position, ally.position, enemy.position = Position(4, 4), Position(7, 4), Position(8, 4)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id, ally.unit_id])
    battle.start_current_turn()
    enemy.base_stats.attack = 8
    ally.add_status(WindWallCounterStatus())
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": ally.unit_id})
    return [observe("natsume:marked", battle, actor, queued, "natsume_wind_wall",
                    {"target_unit_id": ally.unit_id}),
            observe("natsume:wrong", battle, actor, queued, "natsume_wind_wall",
                    {"target_unit_id": enemy.unit_id})]


def sakura_rows():
    random.seed(20261030)
    battle = create_battle("excel_r034", "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(3, 3), Position(5, 3)
    battle.configure_turn_order([actor.unit_id, enemy.unit_id])
    battle.start_current_turn()
    battle.perform_action({"type": "skill", "unit_id": actor.unit_id,
                           "skill_code": "floating_cannons", "x": 2, "y": 2})
    cannons = [unit for unit in battle.player_units(1) if unit.hero_code == "floating_cannon"]
    enemy.base_stats.attack = 8
    queued = QueuedAction(action_type="attack", actor_id=enemy.unit_id,
                          display_name="普攻", speed=1, payload={"declaration_id": "oop-sakura"},
                          target_unit_ids=[actor.unit_id], target_cells=battle.unit_cells(actor),
                          source_player_id=2, hostile=True)
    return [observe("sakura:cover", battle, actor, queued, "floating_cannon_cover",
                    {"target_unit_id": actor.unit_id, "cannon_unit_id": cannons[0].unit_id}),
            observe("sakura:missing", battle, actor, queued, "floating_cannon_cover",
                    {"target_unit_id": actor.unit_id})]


def main() -> None:
    rows = lao_rows() + natsume_rows() + sakura_rows()
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} reaction rows, {output}")


if __name__ == "__main__":
    main()
