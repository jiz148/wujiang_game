"""Print fixed attack-score witnesses for the first hero-owned AI policies."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (difficulty_profile, score_attack_payload,
                                      score_skill_payload, skill_payloads_for_action)


def battle_for(hero: str, opponents: str | list[str]):
    with mock.patch("wujiang.tactical.heroes.registry.opening_player_for_units", return_value=1), mock.patch(
        "wujiang.tactical.heroes.registry.sort_units_for_classic", side_effect=list
    ):
        battle = create_battle(hero, opponents)
    actor = next(unit for unit in battle.player_units(1) if not unit.is_summon)
    targets = [unit for unit in battle.player_units(2) if not unit.is_summon]
    actor.position = Position(3, 3)
    for index, target in enumerate(targets):
        target.position = Position(4, 3 + index)
    return battle, actor, targets


def state_signature(battle) -> tuple:
    return (battle.turn_number,
            tuple((unit.unit_id, unit.current_hp, unit.current_mana, unit.alive,
                   unit.position, tuple((status.name, status.duration) for status in unit.statuses))
                  for unit in battle.all_units()))


def single_case(hero: str, label: str, *, mana: float | None = None,
                hp: float | None = None, target_hp: float | None = None,
                erasure_used: bool = False) -> dict:
    battle, actor, targets = battle_for(hero, "bard")
    target = targets[0]
    if mana is not None:
        actor.current_mana = mana
    if hp is not None:
        actor.current_hp = hp
    if target_hp is not None:
        target.current_hp = target_hp
    if erasure_used:
        actor.get_skill("erasure").uses_this_battle = 1
    payload = {"type": "attack", "unit_id": actor.unit_id,
               "target_unit_id": target.unit_id, "x": 4, "y": 3}
    before = state_signature(battle)
    score = score_attack_payload(battle, actor, payload, difficulty_profile("standard"))
    return {"case": label, "score": float(score), "state_unchanged": state_signature(battle) == before}


def soul_area_case() -> dict:
    battle, actor, targets = battle_for("soul_wraith", ["bard", "ellie"])
    actor.current_mana = 0
    for target in targets:
        target.current_mana = 2
    payload = {"type": "attack", "unit_id": actor.unit_id,
               "choice_code": "right", "x": 4, "y": 3}
    before = state_signature(battle)
    score = score_attack_payload(battle, actor, payload, difficulty_profile("standard"))
    return {"case": "soul_area_two_targets", "score": float(score),
            "state_unchanged": state_signature(battle) == before}


def fuma_skill_case() -> dict:
    battle, actor, _ = battle_for("excel_r123", "bard")
    action = next(item for item in battle.action_snapshot_for(actor)["actions"]
                  if item["code"] == "fuma_shuriken")
    payloads = skill_payloads_for_action(battle, actor, action)
    before = state_signature(battle)
    scores = [score_skill_payload(battle, actor, action, payload,
                                  difficulty_profile("standard"), instant_only=False)
              for payload in payloads]
    return {"case": "fuma_shuriken_best", "score": float(max(scores)),
            "payload_count": len(payloads), "state_unchanged": state_signature(battle) == before}


def main() -> None:
    rows = [
        single_case("li", "li_full_hp"),
        single_case("li", "li_wounded", hp=0.5),
        single_case("erasure_apostle", "erasure_kill_used", mana=0, target_hp=0.25, erasure_used=True),
        single_case("erasure_apostle", "erasure_kill_unused", mana=0, target_hp=0.25),
        single_case("n", "n_empty_mana", mana=0),
        single_case("n", "n_with_mana", mana=2),
        soul_area_case(),
        single_case("excel_r320", "fisher_hit"),
        single_case("excel_r320", "fisher_kill", target_hp=0.25),
        single_case("excel_r324", "super_crow_hit"),
        fuma_skill_case(),
    ]
    print(json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
