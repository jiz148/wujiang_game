"""Fixed R306/R320/R321 skill-score witnesses for the AI OOP migration."""

from __future__ import annotations

import json
from oop_ai_priority_skill_scores import fixture, measure
from wujiang.tactical.engine.core import Position
from wujiang.tactical.heroes.registry import create_hero


def main():
    rows = []
    battle, actor, enemy = fixture("excel_r320")
    target = {"x": enemy.position.x, "y": enemy.position.y}
    rows.append(measure("fisher_gale_fresh", battle, actor, "gale_halberd", target))
    actor.attacks_used = 2
    rows.append(measure("fisher_gale_spent", battle, actor, "gale_halberd", target))
    enemy.position = Position(14, 10)
    rows.append(measure("fisher_gale_empty", battle, actor, "gale_halberd", target))

    battle, actor, enemy = fixture("excel_r321")
    enemy.position = Position(3, 3)
    rows.append(measure("garden_shadow_enemy", battle, actor, "black_night_shadow"))
    ally = create_hero("elite_soldier", 1)
    battle.add_unit(ally, Position(2, 4))
    rows.append(measure("garden_shadow_friendly", battle, actor, "black_night_shadow"))

    battle, actor, enemy = fixture("excel_r306")
    rows.append(measure("storm_dash_long_crossing", battle, actor, "electronic_storm", {"x": 7, "y": 3}))
    enemy.position = Position(14, 10)
    rows.append(measure("storm_dash_long_empty", battle, actor, "electronic_storm", {"x": 7, "y": 3}))
    enemy.position = Position(4, 3)
    rows.append(measure("storm_dash_short", battle, actor, "electronic_storm", {"x": 3, "y": 3}))
    rows.append(measure("storm_afterimage_enemy", battle, actor, "afterimage", {"x": 3, "y": 3}))
    ally = create_hero("elite_soldier", 1)
    battle.add_unit(ally, Position(3, 3))
    rows.append(measure("storm_afterimage_friendly", battle, actor, "afterimage", {"x": 3, "y": 3}))
    print(json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
