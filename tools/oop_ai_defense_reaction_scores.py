"""Fixed defense-reaction candidates, scores and side effects before/after OOP migration."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.mechanics.giant_shield import GiantShieldBashChargeStatus
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_reaction_payload, difficulty_profile,
    reaction_payloads_for_option, score_reaction_payload)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def fixture(hero: str):
    random.seed(20261028)
    battle = create_battle(hero, "elite_soldier" if hero != "elite_soldier" else "bard")
    actor, enemy = next(iter(battle.hero_units(1))), next(iter(battle.hero_units(2)))
    actor.position, enemy.position = Position(3, 3), Position(4, 3)
    battle.configure_turn_order([enemy.unit_id, actor.unit_id])
    battle.start_current_turn()
    enemy.base_stats.attack = 8
    queued = battle.build_queued_action({"type": "attack", "unit_id": enemy.unit_id,
                                         "target_unit_id": actor.unit_id})
    return battle, actor, enemy, queued


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
    for hero in ("excel_r256", "excel_r259"):
        battle, actor, _, queued = fixture(hero)
        rows.append(observe(hero + ":bash", battle, actor, queued, "shield_bash", {}))
        actor.add_status(GiantShieldBashChargeStatus())
        rows.append(observe(hero + ":already", battle, actor, queued, "shield_bash", {}))

    battle, actor, _, queued = fixture("excel_r172")
    actor.current_hp = max(0.25, actor.max_health - 0.5)
    rows.append(observe("beetle:deploy", battle, actor, queued, "beetle_armor_deploy", {}))
    battle, actor, _, queued = fixture("elite_soldier")
    code = "backstep_shot"
    option = {"action_code": code, "preview": actor.get_skill(code).reaction_preview(battle, actor, queued)}
    candidates = reaction_payloads_for_option(battle, actor, queued, option)
    if candidates:
        rows.append(observe("soldier:retreat", battle, actor, queued, code, candidates[-1]))
    rows.append(observe("soldier:stale", battle, actor, queued, code, actor.position.to_dict()))

    battle, actor, _, queued = fixture("erasure_apostle")
    code = "shadow_counter"
    option = {"action_code": code, "preview": actor.get_skill(code).reaction_preview(battle, actor, queued)}
    candidates = reaction_payloads_for_option(battle, actor, queued, option)
    if candidates:
        rows.append(observe("erasure:retreat", battle, actor, queued, code, candidates[-1]))
    rows.append(observe("erasure:missing", battle, actor, queued, code, {}))

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} defense reaction rows, {output}")


if __name__ == "__main__":
    main()
