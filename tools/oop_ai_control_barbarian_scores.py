"""Fixed R262/R263/R260 candidates, scores, and scoring side effects."""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from wujiang.tactical.engine.core import Position, _id_counter
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.mechanics.bird_king import GreatFeatherFanSealStatus
from wujiang.tactical.heroes.mechanics.ice_emperor import AbsoluteZeroStatus, FloatingCraneCityField
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.rooms.ai import (_score_skill_payload, difficulty_profile,
    score_skill_payload, skill_payloads_for_action)


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def observe(label: str, battle, actor, code: str, payload: dict) -> dict:
    skill = actor.get_skill(code)
    action = {"code": code, "target_mode": skill.target_mode, "preview": skill.preview(battle, actor)}
    payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": code, **payload}
    profile = difficulty_profile("standard")
    before = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    candidates = skill_payloads_for_action(battle, actor, action)
    internal = _score_skill_payload(battle, actor, action, payload, profile, instant_only=False)
    public = score_skill_payload(battle, actor, action, payload, profile, instant_only=False)
    after = (digest(battle.to_public_dict()), digest(random.getstate()), _id_counter.peek())
    return {"label": label, "payload": payload, "candidates": candidates,
            "internal": internal, "public": public, "state_before_after": [before, after]}


def area_payload(skill, battle, actor, *, include=(), exclude=(), choice=None):
    patterns = skill.patterns(battle, actor)
    matching = [pattern for pattern in patterns
                if all(cell in pattern for cell in include)
                and all(cell not in pattern for cell in exclude)]
    assert matching, (skill.code, include, exclude)
    payload = {"cells": [cell.to_dict() for cell in matching[0]]}
    if choice is not None:
        payload["choice_code"] = choice
    return payload


def setup(code: str):
    battle = create_battle([code, "elite_soldier"], ["bard", "elite_soldier"])
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == code)
    ally = next(unit for unit in battle.hero_units(1) if unit is not actor)
    enemy = next(unit for unit in battle.hero_units(2) if unit.hero_code == "bard")
    other = next(unit for unit in battle.hero_units(2) if unit is not enemy)
    actor.position, ally.position = Position(2, 2), Position(3, 3)
    enemy.position, other.position = Position(4, 2), Position(7, 7)
    battle.configure_turn_order([actor.unit_id, enemy.unit_id, ally.unit_id, other.unit_id])
    battle.start_current_turn()
    return battle, actor, ally, enemy, other


def bird_rows():
    random.seed(262)
    battle, actor, ally, enemy, _ = setup("excel_r262")
    fan = actor.get_skill("great_feather_fan")
    enemy.add_status(StatModifierStatus("敌减益", attack_delta=-1), source=enemy)
    ally.add_status(StatModifierStatus("友增益", defense_delta=1), source=ally)
    rows = [observe("bird:fan_enemy", battle, actor, fan.code,
                    area_payload(fan, battle, actor, include=(enemy.position,), exclude=(ally.position, actor.position))),
            observe("bird:fan_ally", battle, actor, fan.code,
                    area_payload(fan, battle, actor, include=(ally.position,), exclude=(enemy.position,))),
            observe("bird:fan_empty", battle, actor, fan.code, {"cells": []}),
            observe("bird:poison_mana", battle, actor, "poison_feather", {"target_unit_id": enemy.unit_id})]
    enemy.current_mana = 0
    rows.append(observe("bird:poison_dry", battle, actor, "poison_feather", {"target_unit_id": enemy.unit_id}))
    enemy.current_mana = 1
    enemy._base_magic_immunity = True
    rows.append(observe("bird:poison_immune", battle, actor, "poison_feather", {"target_unit_id": enemy.unit_id}))
    enemy._base_magic_immunity = False
    actor.base_stats.defense = 1
    rows.append(observe("bird:poison_low_defense", battle, actor, "poison_feather", {"target_unit_id": enemy.unit_id}))
    enemy.add_status(GreatFeatherFanSealStatus(), source=actor)
    rows.append(observe("bird:fan_refresh", battle, actor, fan.code,
                        area_payload(fan, battle, actor, include=(enemy.position,), exclude=(ally.position,))))
    return rows


def ice_rows():
    random.seed(263)
    battle, actor, ally, enemy, other = setup("excel_r263")
    zero = actor.get_skill("absolute_zero")
    rows = [observe("ice:zero_enemy", battle, actor, zero.code,
                    area_payload(zero, battle, actor, include=(enemy.position,), exclude=(ally.position,))),
            observe("ice:zero_ally", battle, actor, zero.code,
                    area_payload(zero, battle, actor, include=(ally.position,), exclude=(enemy.position,))),
            observe("ice:zero_empty", battle, actor, zero.code, {"cells": []})]
    enemy.base_stats.attack_range = other.base_stats.attack_range = 5
    ally.position = None
    rows.append(observe("ice:city_fresh", battle, actor, "floating_crane_city", {}))
    ally.position = Position(3, 3)
    enemy.add_status(AbsoluteZeroStatus(), source=actor)
    rows.append(observe("ice:zero_refresh", battle, actor, zero.code,
                        area_payload(zero, battle, actor, include=(enemy.position,), exclude=(ally.position,))))
    battle.add_field_effect(FloatingCraneCityField(), source=actor)
    rows.append(observe("ice:city_existing", battle, actor, "floating_crane_city", {}))
    return rows


def barbarian_rows():
    random.seed(260)
    battle, actor, ally, enemy, _ = setup("excel_r260")
    axe = actor.get_skill("barbarian_axe")
    rows = [observe("barbarian:gluttony_full", battle, actor, "barbarian_gluttony", {}),
            observe("barbarian:axe_full", battle, actor, axe.code,
                    area_payload(axe, battle, actor, include=(enemy.position,), choice="full")),
            observe("barbarian:axe_weak", battle, actor, axe.code,
                    area_payload(axe, battle, actor, include=(enemy.position,), choice="weak")),
            observe("barbarian:axe_empty", battle, actor, axe.code, {"cells": [], "choice_code": "weak"})]
    actor.current_hp = 0.4
    rows.append(observe("barbarian:gluttony_hurt", battle, actor, "barbarian_gluttony", {}))
    enemy.position = Position(3, 2)
    rows.extend((observe("barbarian:harass_paid", battle, actor, "barbarian_harass",
                         {"target_unit_id": enemy.unit_id}),
                 observe("barbarian:harass_free", battle, actor, "barbarian_harass_free",
                         {"target_unit_id": enemy.unit_id}),
                 observe("barbarian:harass_missing", battle, actor, "barbarian_harass", {})))
    enemy.name = "野人"
    rows.append(observe("barbarian:harass_marked", battle, actor, "barbarian_harass_free",
                        {"target_unit_id": enemy.unit_id}))
    return rows


def main() -> None:
    rows = bird_rows() + ice_rows() + barbarian_rows()
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} control/barbarian rows, {output}")


if __name__ == "__main__":
    main()
