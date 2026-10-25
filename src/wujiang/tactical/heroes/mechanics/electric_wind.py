"""Electric wind and rectangular targeting objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.mechanics.racial_weather import WeatherUltimateSkill
from wujiang.tactical.heroes.mechanics.thor import ThorDestroyedLightningStatus
from wujiang.tactical.heroes.next_five import damage_followup_effect_applies
from wujiang.tactical.heroes.next_five import square_around_cells


class ElectricWindStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__(
            "电风",
            speed_delta=-1,
            description="不能使用技能，速 -1，到 1。",
            duration=2,
            tick_scope="owner_turn_end",
        )

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id:
            return True, "电风状态下不能使用技能。"
        return False, ""


def front_rectangle_patterns(battle: Battle, actor: HeroUnit, depth: int, width: int) -> list[list[Position]]:
    if actor.position is None:
        return []
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    half_width = max(0, width // 2)
    for origin in battle.unit_cells(actor) or [actor.position]:
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            lateral = (-dy, dx)
            cells: list[Position] = []
            for forward in range(1, depth + 1):
                center = origin.offset(dx * forward, dy * forward)
                for side in range(-half_width, half_width + 1):
                    cell = center.offset(lateral[0] * side, lateral[1] * side)
                    if battle.in_bounds(cell):
                        cells.append(cell)
            if not cells:
                continue
            key = pattern_signature(cells)
            if key in seen:
                continue
            seen.add(key)
            patterns.append(cells)
    return patterns


class ElectricWindSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "electric_wind",
            "电风",
            "普通技能：2 轮一次；身前 2*3 双方按当前攻受技能伤害，有效命中者 2 轮不能使用技能，速 -1 到 1。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return front_rectangle_patterns(battle, actor, depth=2, width=3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def apply_to_units(self, battle: Battle, actor: HeroUnit, units: list[HeroUnit], *, action_name: str,
                       cells: list[Position] | None = None) -> None:
        for unit in battle.effect_units(units):
            if not unit.alive or unit.position is None or unit.banished:
                continue
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"), is_skill=True,
                action_name=action_name, tags={"skill", "electric_wind"},
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells) if cells else 1,
            ))
            if not damage_followup_effect_applies(ctx) or not unit.alive or unit.banished or unit.position is None:
                continue
            effect = battle.validate_target(actor, unit, action_name=action_name, is_skill=True,
                                            is_hostile=unit.player_id != actor.player_id,
                                            resolve_defenses=ctx.cancelled and not ctx.shield_consumed,
                                            ignore_targeting_restrictions=True)
            if effect.cancelled:
                continue
            existing = unit.get_status("电风")
            if existing is not None:
                unit.remove_status(existing, battle)
            unit.add_status(ElectricWindStatus())
            battle.log_public_event(f"{unit.name} 被【电风】影响，不能使用技能且速 -1。", source=actor, target=unit)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        self.apply_to_units(battle, actor, battle.effect_units_at_cells(cells), action_name="电风", cells=cells)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class AutoElectricWindTrait(Trait):
    def __init__(self) -> None:
        super().__init__("自动电风", "每个自己的回合开始时，对周围 5*5 内单位自动使用电风；没有合法目标则跳过。")
        self.last_start_turn: int | None = None

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.banished or owner.position is None:
            return
        if self.last_start_turn == battle.turn_number:
            return
        self.last_start_turn = battle.turn_number
        wind = ElectricWindSkill()
        # The automatic cadence is independent, but the named spell still obeys skill seals.
        if owner.cannot_use_skills or any(component.blocks_skill_use(battle, owner, wind)[0]
               or not component.allows_block_counter(battle, owner)
               or (isinstance(component, ThorDestroyedLightningStatus) and component.pending)
               for component in owner.iter_components()):
            return
        if not owner.direct_effects_blocked() and any(effect.blocks_skill_use(battle, owner, wind)[0] for effect in battle.field_effects):
            return
        cells = square_around_cells(battle, battle.unit_cells(owner), radius=2)
        targets = battle.effect_units_at_cells(cells, ignore=battle.effect_recipient(owner))
        if not targets:
            battle.log(f"{owner.name} 的【自动电风】没有合法目标，跳过。")
            return
        wind.apply_to_units(battle, owner, targets, action_name="自动电风", cells=cells)


class SkySanctuarySkill(WeatherUltimateSkill):
    def __init__(self) -> None:
        super().__init__("sky_sanctuary", "天使的气息", weather_name="天空的圣域", marker="圣")


