"""Fixed generic and specialized shield reaction witnesses."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.registry import create_battle, create_hero
from wujiang.tactical.rooms.ai import (_score_reaction_payload, difficulty_profile,
    reaction_payloads_for_option, score_reaction_payload)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(hero: str, *, ally: bool = False):
    random.seed(20261028)
    battle = create_battle(hero, "elite_soldier")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(3, 3), Position(4, 3)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id])
    battle.start_current_turn()
    enemy.base_stats.attack = 8
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": actor.unit_id})
    if ally:
        friend = create_hero("bard", 1)
        battle.add_unit(friend, Position(3, 4))
        queued.target_unit_ids.append(friend.unit_id)
        queued.target_cells.extend(battle.unit_cells(friend))
        return battle, actor, enemy, queued, friend
    return battle, actor, enemy, queued, None


def observe(label: str, battle, actor, queued, code: str, payload: dict,
            *, protected_id: str | None = None):
    skill = actor.get_skill(code)
    option = {"action_code": code, "preview": skill.reaction_preview(battle, actor, queued)}
    profile = difficulty_profile("standard")
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    gate = (mock.patch("wujiang.tactical.rooms.ai.queued_shield_protected_unit_ids",
                       return_value={protected_id}) if protected_id else nullcontext())
    with gate:
        candidates = reaction_payloads_for_option(battle, actor, queued, option)
        internal = _score_reaction_payload(battle, actor, queued, option, payload, profile)
        public = score_reaction_payload(battle, actor, queued, option, payload, profile)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "payload": payload, "candidates": candidates,
            "internal": internal, "public": public, "state_before_after": [before, after]}


def main() -> None:
    rows = []
    battle, actor, enemy, queued, _ = fixture("ellie")
    target = {"target_unit_ids": [actor.unit_id]}
    rows.append(observe("ellie:normal", battle, actor, queued, "magic_wall", target))
    rows.append(observe("ellie:empty", battle, actor, queued, "magic_wall", {}))
    rows.append(observe("ellie:protected", battle, actor, queued, "magic_wall", target,
                        protected_id=actor.unit_id))
    queued.payload["ignore_shield"] = True
    rows.append(observe("ellie:pierce", battle, actor, queued, "magic_wall", target))
    del queued.payload["ignore_shield"]
    queued.payload["half_ignore_shield"] = True
    rows.append(observe("ellie:half_pierce", battle, actor, queued, "magic_wall", target))
    del queued.payload["half_ignore_shield"]
    actor.current_mana = 1
    enemy.base_stats.attack = 1
    rows.append(observe("ellie:reserve", battle, actor, queued, "magic_wall", target))

    battle, actor, _, queued, friend = fixture("jade", ally=True)
    both = {"target_unit_ids": [actor.unit_id, friend.unit_id]}
    rows.append(observe("jade:ion_multi", battle, actor, queued, "ion_shield", both))
    rows.append(observe("jade:quantum_multi", battle, actor, queued, "quantum_shield", both))
    rows.append(observe("jade:quantum_empty", battle, actor, queued, "quantum_shield", {}))

    for hero, code in (("excel_r328", "protection"),
                       ("excel_r225", "ion_shield"),
                       ("excel_r225", "quantum_shield"),
                       ("excel_r304", "light_wall")):
        battle, actor, _, queued, _ = fixture(hero)
        rows.append(observe(hero + ":" + code, battle, actor, queued, code,
                            {"target_unit_ids": [actor.unit_id]}))

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} shield reaction rows, {output}")


if __name__ == "__main__":
    main()
