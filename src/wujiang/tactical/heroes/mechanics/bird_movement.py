"""R219 Bird movement and post-move boost."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import MoveContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.common import line_patterns
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.next_five import ALL_DIRECTIONS


class BirdSkillMoveBoostStatus(StatusEffect):
    def __init__(self, duration: int) -> None:
        super().__init__("鸟人技能位移翻倍", "因技能实际位移，当前与下一本人轮攻守翻倍。",
                         duration=duration, tick_scope="owner_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value * 2 if stat_name in {"attack", "defense"} else value


class BirdSkillMoveBoostTrait(Trait):
    def __init__(self) -> None:
        super().__init__("次时代机动", "因技能真实位移后，本轮和下本人轮攻守翻倍，同名刷新。")

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or ctx.start == ctx.end or not ctx.via_skill:
            return
        duration = 2 if battle.unit_belongs_to_current_turn(owner) else 1
        existing = owner.get_status("鸟人技能位移翻倍")
        if existing is None:
            owner.add_status(BirdSkillMoveBoostStatus(duration), source=owner)
        else:
            existing.duration = max(int(existing.duration or 0), duration)
        battle.log_public_event(f"{owner.name} 因技能位移，攻守翻倍。", source=owner, target=owner)


class BirdDashSkill(Skill):
    target_cells_are_geometry_only = True

    def __init__(self, *, free: bool) -> None:
        self.free = free
        super().__init__("bird_dash_free" if free else "bird_dash",
                         "疾走（免费）" if free else "疾走（付费）",
                         "八方向当前范直线伤害两次；免费档每2本人轮一次。" if free else "2魔；八方向当前范直线伤害两次。",
                         mana_cost=0 if free else 2, cooldown_turns=2 if free else 0,
                         target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        if actor.position is None:
            return []
        body = set(battle.unit_cells(actor))
        patterns: list[list[Position]] = []
        for origin in sorted(body, key=lambda cell: (cell.y, cell.x)):
            for dx, dy in ALL_DIRECTIONS:
                if origin.offset(dx, dy) in body:
                    continue
                patterns.extend(line_patterns(battle, origin, [(dx, dy)], actor.targeting_range()))
        return list({tuple(line): line for line in patterns}.values())

    def chosen_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and payload.get("bird_dash_cells"):
            return [Position(**cell) for cell in payload["bird_dash_cells"]]
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("cells"):
            try:
                self.chosen_cells(battle, actor, payload)
            except (ActionError, KeyError, ValueError, TypeError) as exc:
                return False, str(exc)
        return (True, "") if self.patterns(battle, actor) else (False, "没有可用的疾走方向。")

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"bird_dash_cells": positions_to_dict(self.chosen_cells(battle, actor, payload))}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return []

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for segment in (1, 2):
            battle.queue_area_damage_effect(actor=actor, display_name=f"{self.name}第{segment}击",
                                            cells=cells, attack_power=actor.stat("attack"),
                                            tags={"skill", self.code}, refresh_cell_targets=True,
                                            segment_index=segment,
                                            segment_count=2)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        result = pattern_selection_preview(patterns)
        result.update({"target_unit_ids": list(dict.fromkeys(
            unit.unit_id for line in patterns for unit in battle.effect_units_at_cells(line, ignore=actor)
            if battle.unit_can_be_selected(unit, actor=actor)[0])),
            "secondary_cells": [], "requires_target": True})
        return result


