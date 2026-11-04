"""R028 Fei Wang gale skill."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import positions_to_dict


class GaleSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.gale_score(context)

    def ai_kings_insight_score(self, battle, actor, payload, services, profile) -> float:
        return services.gale_score(battle, actor, payload, profile)

    def ai_control_bonus(self, context: AISkillScoreContext, targets: list[Unit]) -> float:
        return context.shared.gale_score(context)

    ai_always_hostile_effect = True
    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool | None:
        if targets is None:
            return None
        return any(unit.is_summon or unit.is_clone or unit.position is not None for unit in targets)

    selection_cells_are_effect_cells = False
    DIRECTIONS = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}

    def __init__(self) -> None:
        super().__init__("gale", "狂风", "普通技能：费1.5魔；前方7×7破魔效果，显形，破坏双方召唤物/分身，其余单位尽量聚到中心合法格。", mana_cost=1.5, target_mode="cell")

    def direction(self, payload: dict[str, Any]) -> tuple[int, int]:
        raw = str(payload.get("choice_code") or payload.get("direction") or "").lower()
        if raw in self.DIRECTIONS:
            return self.DIRECTIONS[raw]
        dx, dy = int(payload.get("dx") or 0), int(payload.get("dy") or 0)
        if (dx, dy) in self.DIRECTIONS.values():
            return dx, dy
        raise ActionError("狂风需要选择正方向。")

    def origin(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> Position | None:
        return battle.declared_source_position(payload) if payload.get("queued_resolution") else actor.position

    def center(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> Position | None:
        origin = self.origin(battle, actor, payload)
        dx, dy = self.direction(payload)
        return origin.offset(dx * 4, dy * 4) if origin is not None else None

    def area(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        center = self.center(battle, actor, payload)
        if center is None:
            return []
        return [Position(x, y) for y in range(center.y - 3, center.y + 4)
                for x in range(center.x - 3, center.x + 4) if battle.in_bounds(Position(x, y))]

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        declaration = {**payload, "queued_resolution": False}
        cells = self.area(battle, actor, declaration)
        if not cells:
            raise ActionError("这个方向没有场内范围。")
        return {"gale_cells": positions_to_dict(cells), "gale_center": self.center(battle, actor, declaration).to_dict()}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "gale_cells") if payload.get("queued_resolution") else self.area(battle, actor, payload)
        raw_center = payload.get("gale_center") if payload.get("queued_resolution") else None
        center = Position(int(raw_center["x"]), int(raw_center["y"])) if raw_center else self.center(battle, actor, payload)
        if center is None:
            return
        remaining = []
        for unit in battle.effect_units_at_cells(cells):
            if unit.direct_effects_blocked():
                continue
            ctx = battle.validate_target(actor, unit, action_name=self.name, is_skill=True, is_hostile=True,
                                         ignore_shield=True, ignore_targeting_restrictions=True, tags={"skill", "gale"})
            if ctx.cancelled:
                continue
            if unit.total_shields() > 0 and ctx.ignore_shield:
                unit.consume_one_shield()
                battle.record_shield_break_summary(actor, unit, self.name)
            for status in list(unit.statuses):
                if status.name == "隐身":
                    unit.remove_status(status, battle)
            if unit.is_summon or unit.is_clone:
                unit.alive = False
                battle.log_public_event(f"{unit.name} 被【狂风】破坏。", source=actor, target=unit)
            else:
                remaining.append(unit)
        battle.cleanup_dead_units()
        candidates = sorted(cells, key=lambda cell: (cell.distance_to(center), cell.y, cell.x))
        for unit in sorted(remaining, key=lambda unit: (unit.position.distance_to(center) if unit.position else 99, unit.unit_id)):
            if not unit.alive or unit.position is None or unit.banished:
                continue
            for dest in candidates:
                if dest == unit.position:
                    break
                if (battle.is_forced_movement_blocked(dest)
                        or not battle.can_place_unit(unit, dest, ignore=unit, mover=unit)):
                    continue
                try:
                    battle.move_unit(unit, dest, via_skill=True, forced=True, max_distance=99,
                                     ignore_units=True, tags={"gale"})
                except ActionError:
                    continue
                break

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        choices, destinations = [], []
        for direction, label in (("east", "向右"), ("west", "向左"), ("south", "向下"), ("north", "向上")):
            cells = self.area(battle, actor, {"direction": direction})
            if not cells:
                continue
            center = self.center(battle, actor, {"direction": direction})
            anchor = min(cells, key=lambda cell: (cell.distance_to(center), cell.y, cell.x))
            selection = positions_to_dict([anchor])
            choices.append({"code": direction, "label": label, "patterns": [selection]})
            destinations.append({"choice_code": direction, "pattern": selection, "destination_cells": positions_to_dict(cells)})
        cells = sorted({cell for direction in self.DIRECTIONS for cell in self.area(battle, actor, {"direction": direction})}, key=lambda cell: (cell.y, cell.x))
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                              "choice_prompt": "选择狂风方向，再点击中心标记格确认。",
                              "cell_prompt": "点击中心标记格确认；高亮显示本次7×7范围。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        # This hook describes a new declaration; resolution uses the server-frozen gale_cells.
        return self.area(battle, actor, {**payload, "queued_resolution": False})

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


