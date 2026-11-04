"""Fixed stationary-recovery movement witnesses for the AI OOP migration."""

from __future__ import annotations

import json
from oop_ai_soul_sandstorm_scores import fixture, measure
from wujiang.tactical.engine.core import Position
from wujiang.tactical.rooms.ai import difficulty_profile, score_move_destination


def main():
    battle, actor, _ = fixture("bard", "doomlight_dragon")
    actor.current_hp = max(0.0, actor.current_hp - 0.5)
    actor.current_mana = 0
    profile = difficulty_profile("standard")
    rows = [measure("bard_stay", battle,
                    lambda: score_move_destination(battle, actor, Position(2, 2), "support", profile)),
            measure("bard_leave", battle,
                    lambda: score_move_destination(battle, actor, Position(3, 2), "support", profile))]
    print(json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
