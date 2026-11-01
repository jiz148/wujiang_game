"""R037 nuclear mutation and gravity field mechanics."""

from __future__ import annotations
from wujiang.tactical.heroes.ai_impact_rule import R12_IMPACT

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import is_mana_drain_immune
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.prediction import direct_scoring
from wujiang.tactical.prediction import random


def _area_hit_count(battle: Battle, target: HeroUnit, cells: list[Position]) -> int:
    keys = {(cell.x, cell.y) for cell in cells}
    return max(1, sum(1 for cell in battle.unit_cells(target) if (cell.x, cell.y) in keys))


class NuclearMutationSkill(Skill):
    ai_effective_impact_rule = R12_IMPACT
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r12_effect_score(self)

    def __init__(self) -> None:
        super().__init__("nuclear_mutation", "核变", "普通技能：费 2 魔，选择远程 6*6 区域，造成当前攻击伤害。", mana_cost=2, target_mode="cell")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 6, 6)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="核变",
                    area_cell_hits=_area_hit_count(battle, target, cells),
                    tags={"skill", "area", "nuclear_mutation"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        cell_keys = {(cell.x, cell.y) for pattern in patterns for cell in pattern}
        targets = [unit.unit_id for unit in battle.all_units() if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class GravityFieldSkill(Skill):
    ai_effective_impact_rule = R12_IMPACT
    def ai_priority_score(self, context: AISkillScoreContext) -> float | None:
        if direct_scoring():
            return None
        return context.r12_effect_score(self)

    def __init__(self) -> None:
        super().__init__("gravity_field", "重力场", "普通技能：3 轮一次；扔 3 次硬币决定范围，半破魔伤害，附带破魔吸魔。", cooldown_turns=3, target_mode="cell")

    def candidate_centers(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None:
            return []
        cells: list[Position] = []
        for x in range(battle.width):
            for y in range(battle.height):
                cell = Position(x, y)
                if battle.unit_distance_to_cell(actor, cell) <= actor.targeting_range():
                    cells.append(cell)
        return cells

    def chosen_center(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> Position:
        cell = Position(int(payload.get("x")), int(payload.get("y")))
        if cell not in self.candidate_centers(battle, actor):
            raise ActionError("请选择合法的重力场中心。")
        return cell

    def cells_for_side(self, battle: Battle, center: Position, side: int) -> list[Position]:
        radius_low = (side - 1) // 2
        radius_high = side // 2
        cells: list[Position] = []
        for x in range(center.x - radius_low, center.x + radius_high + 1):
            for y in range(center.y - radius_low, center.y + radius_high + 1):
                cell = Position(x, y)
                if battle.in_bounds(cell):
                    cells.append(cell)
        return cells

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        self.chosen_center(battle, actor, payload)
        if getattr(battle, "_forecasting_action", False) or getattr(battle, "_ai_probe_active", False):
            return {"gravity_results_logged": False}
        values = [2 if random.random() < 0.5 else 1 for _ in range(3)]
        return {"gravity_coin_values": values, "gravity_side": values[0] * values[1] * values[2], "gravity_results_logged": False}

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if (action_type == "skill" and payload.get("skill_code") == self.code
                and payload.get("unit_id") == self.owner.unit_id and payload.get("gravity_coin_values")
                and not payload.get("gravity_results_logged")):
            battle.log(f"{self.owner.name} 的【重力场】硬币结果为 {payload['gravity_coin_values']}，范围边长 {payload['gravity_side']}。")
            payload["gravity_results_logged"] = True

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        center = self.chosen_center(battle, actor, payload)
        coin_values = payload.get("gravity_coin_values")
        if direct_scoring() and not coin_values:
            # Four scalar area sizes with binomial weights; no battle replay.
            recipients = {}
            for side, probability in ((1, 1 / 8), (2, 3 / 8), (4, 3 / 8), (8, 1 / 8)):
                cells = self.cells_for_side(battle, center, side)
                for target in battle.effect_units_at_cells(cells):
                    entry = recipients.setdefault(target.unit_id, (target, []))
                    entry[1].append((max(0, _area_hit_count(battle, target, cells) - 1), probability))
            for target, rows in recipients.values():
                probability = sum(weight for _, weight in rows)
                ctx = battle.resolve_damage(DamageContext(source=actor, target=target,
                    attack_power=actor.stat("attack"), is_skill=True, action_name=self.name,
                    half_ignore_shield=True, forecast_hit_probability=probability,
                    forecast_attack_changes=tuple((value, weight / probability) for value, weight in rows),
                    tags={"skill", "area", "gravity_field"}))
                if not target.alive or is_mana_drain_immune(target):
                    continue
                drain = battle.validate_target(actor, target, action_name="重力场吸魔", is_skill=True,
                    is_hostile=target.player_id != actor.player_id, ignore_shield=True,
                    ignore_targeting_restrictions=True, resolve_defenses=False,
                    tags={"skill", "area", "gravity_field", "mana_drain"})
                if not drain.cancelled:
                    amount = min(1.0, target.current_mana) * max(0.0, probability - ctx.forecast_kill_probability)
                    actor.gain_mana(target.drain_mana(amount))
            return
        if not isinstance(coin_values, list) or len(coin_values) != 3 or any(value not in (1, 2) for value in coin_values):
            if getattr(battle, "_forecasting_action", False) or getattr(battle, "_ai_probe_active", False):
                raise ActionError("重力场预演需要明确的概率分支。")
            coin_values = [2 if random.random() < 0.5 else 1 for _ in range(3)]
        side = coin_values[0] * coin_values[1] * coin_values[2]
        cells = self.cells_for_side(battle, center, side)
        if not payload.get("gravity_results_logged"):
            battle.log(f"{actor.name} 的【重力场】硬币结果为 {coin_values}，范围边长 {side}。")
        for target in battle.effect_units_at_cells(cells):
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="重力场",
                    half_ignore_shield=True,
                    area_cell_hits=_area_hit_count(battle, target, cells),
                    tags={"skill", "area", "gravity_field"},
                )
            )
            if not target.alive:
                continue
            drain_ctx = battle.validate_target(
                actor, target, action_name="重力场吸魔", is_skill=True,
                is_hostile=target.player_id != actor.player_id,
                ignore_shield=True,
                ignore_targeting_restrictions=True, resolve_defenses=False,
                tags={"skill", "area", "gravity_field", "mana_drain"},
            )
            if drain_ctx.cancelled:
                continue
            if is_mana_drain_immune(target):
                battle.log(f"{target.name} 免疫吸魔。")
                continue
            if target.player_id != actor.player_id and not ctx.shield_consumed and target.total_shields() > 0:
                target.consume_one_shield()
            drained = target.drain_mana(1)
            gained = actor.gain_mana(drained)
            if drained or gained:
                battle.log(f"{actor.name} 的【重力场】从 {target.name} 吸取 {drained} 点魔，获得 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.candidate_centers(battle, actor)
        return {
            "mode": "cell",
            "cells": positions_to_dict(cells),
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        center = self.chosen_center(battle, actor, payload)
        side = int(payload.get("gravity_side") or 8)
        return self.cells_for_side(battle, center, side)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload))  # type: ignore[return-value]

    def half_ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


