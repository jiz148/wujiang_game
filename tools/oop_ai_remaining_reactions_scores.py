"""Fixed reaction candidates, scores, and side effects before/after object migration."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.mechanics.chanter import ChanterCardFieldEffect
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_reaction_payload, difficulty_profile,
    reaction_payloads_for_option, score_reaction_payload)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(hero: str, *, mana: float | None = None):
    random.seed(20261028)
    battle = create_battle(hero, "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(3, 3), Position(5 if hero == "excel_r252" else 4, 3)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id])
    battle.start_current_turn()
    enemy.base_stats.attack = 8
    if mana is not None:
        actor.current_mana = mana
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": actor.unit_id})
    return battle, actor, queued


def observe(label: str, battle, actor, queued, code: str, payload: dict):
    skill = actor.get_skill(code)
    option = {"action_code": code, "preview": skill.reaction_preview(battle, actor, queued)}
    profile = difficulty_profile("standard")
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    candidates = reaction_payloads_for_option(battle, actor, queued, option)
    internal = _score_reaction_payload(battle, actor, queued, option, payload, profile)
    public = score_reaction_payload(battle, actor, queued, option, payload, profile)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "payload": payload, "candidates": candidates,
            "internal": internal, "public": public, "state_before_after": [before, after]}


def main() -> None:
    rows = []
    battle, actor, queued = fixture("chanter")
    battle.field_effects.extend([
        ChanterCardFieldEffect(actor.unit_id, actor.player_id, "poison", Position(1, 3)),
        ChanterCardFieldEffect(actor.unit_id, actor.player_id, "drain", Position(2, 3)),
    ])
    queued.target_cells.append(Position(2, 3))
    rows.append(observe("chanter:safe", battle, actor, queued, "card_transposition", {"x": 1, "y": 3}))
    rows.append(observe("chanter:still_targeted", battle, actor, queued, "card_transposition", {"x": 2, "y": 3}))
    rows.append(observe("chanter:missing", battle, actor, queued, "card_transposition", {}))

    for hero in ("excel_r252", "excel_r248"):
        battle, actor, queued = fixture(hero)
        rows.append(observe(hero + ":knockback", battle, actor, queued, "knockback", {}))
        actor.current_hp = 0.25
        rows.append(observe(hero + ":urgent", battle, actor, queued, "knockback", {}))

    for hero in ("excel_r238", "excel_r240", "excel_r242", "excel_r243", "excel_r244", "excel_r246"):
        battle, actor, queued = fixture(hero)
        rows.append(observe(hero + ":paid", battle, actor, queued, "hell_pressure", {}))
        actor.current_mana = 0
        rows.append(observe(hero + ":free", battle, actor, queued, "hell_pressure_free", {}))

    battle, actor, queued = fixture("excel_r238", mana=1)
    next(iter(battle.hero_units(2))).base_stats.attack = 1
    rows.append(observe("excel_r238:reserve", battle, actor, queued, "hell_pressure", {}))

    battle, actor, queued = fixture("excel_r206")
    rows.append(observe("boxer:normal", battle, actor, queued, "boxer_block_counter", {}))
    actor.current_hp = max(0.25, actor.max_health - 0.5)
    rows.append(observe("boxer:injured", battle, actor, queued, "boxer_block_counter", {}))

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} reaction rows, {output}")


if __name__ == "__main__":
    main()
