"""True blade air slash and hundred-bird burial objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import ActionMiss
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns


class TrueBladeAirSlashSkill(Skill):
    requires_direct_unit_target_line = False
    target_unit_is_anchor = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r17_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "true_blade_air_slash", "真刀。空气斩",
            "普通技能：费1.5魔，每回合1次；声明五格直线落点和落点范内双方目标，先完成位移再按实际受体守+1破魔；成功补目标当前魔。",
            mana_cost=1.5, max_uses_per_turn=1, target_mode="unit",
        )

    def landing_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        result = []
        for dx, dy in ALL_DIRECTIONS:
            destination = actor.position.offset(dx * 5, dy * 5)
            try:
                battle.find_path(actor, destination, max_distance=5, exact_distance=5, straight_only=True)
            except ActionError:
                continue
            result.append(destination)
        return result

    def target_in_range_from_landing(self, battle: Battle, actor: HeroUnit, target: HeroUnit, landing: Position) -> bool:
        return any(origin.distance_to(cell) <= actor.targeting_range()
                   and battle.cells_are_straight_aligned(origin, cell)
                   for origin in actor.footprint_cells_at(landing) for cell in battle.unit_cells(target))

    def choose_landing(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any]) -> Position:
        if payload.get("x") is None or payload.get("y") is None:
            raise ActionError("真刀需要显式选择五格直线落点。")
        selected = payload_position(payload)
        if selected not in self.landing_cells(battle, actor) or not self.target_in_range_from_landing(battle, actor, target, selected):
            raise ActionError("真刀落点或移动后的目标范围不合法。")
        return selected

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = payload_target_unit(battle, payload)
        landing = self.choose_landing(battle, actor, target, payload)
        cells = [cell for cell in battle.unit_cells(target)
                 if any(origin.distance_to(cell) <= actor.targeting_range()
                        and battle.cells_are_straight_aligned(origin, cell)
                        for origin in actor.footprint_cells_at(landing))]
        cell = min(cells, key=lambda cell: (cell.y, cell.x))
        return {"air_slash_landing": landing.to_dict(), "air_slash_target_cell": cell.to_dict()}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        meta = payload if "air_slash_target_cell" in payload else self.queued_payload_metadata(battle, actor, payload)
        return [Position(**meta["air_slash_target_cell"])]

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        meta = payload if payload.get("queued_resolution") and "air_slash_landing" in payload else self.queued_payload_metadata(battle, actor, payload)
        landing = Position(**meta["air_slash_landing"])
        target_cell = Position(**meta["air_slash_target_cell"])
        actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
        battle.move_unit(actor, landing, via_skill=True, straight_only=True, max_distance=5, exact_distance=5,
                         tags={"movement", "true_blade_air_slash"})
        if not actor.alive or actor.banished or actor.position is None:
            return
        targets = battle.effect_units_at_cells([target_cell])
        if not targets:
            raise ActionMiss("真刀已完成移动，但原声明格没有有效目标。")
        target = targets[0]
        if not self.target_in_range_from_landing(battle, actor, target, actor.position):
            raise ActionMiss("真刀已完成移动，但原声明目标无法命中。")
        target_mana = target.current_mana
        ctx = battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=target.stat("defense") + 1,
            is_skill=True, action_name=self.name, ignore_shield=True,
            tags={"skill", "true_blade_air_slash"},
        ))
        if damage_followup_effect_applies(ctx) and not target.magic_immunity and actor.alive and not actor.banished:
            gained = actor.gain_mana(target_mana)
            battle.log(f"{actor.name} 因【真刀。空气斩】获得 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        landings = self.landing_cells(battle, actor)
        destinations = {
            unit.unit_id: positions_to_dict([cell for cell in landings if self.target_in_range_from_landing(battle, actor, unit, cell)])
            for unit in battle.all_units() if battle.unit_can_be_selected(unit, actor=actor)[0]
        }
        destinations = {unit_id: cells for unit_id, cells in destinations.items() if cells}
        return {
            "cells": positions_to_dict(landings) + positions_to_dict([cell for unit_id in destinations for cell in battle.unit_cells(battle.get_unit(unit_id))]),
            "target_unit_ids": list(destinations), "secondary_cells": positions_to_dict(landings),
            "requires_target": True, "destinations_by_target": destinations,
        }

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


MOVEMENT_SKILL_CODES = {
    "backstep_shot",
    "card_transposition",
    "chain_pull",
    "crazy_sand",
    "descent_moment",
    "detach_mage_cloak",
    "dragon_slash",
    "earth_walker",
    "equip_mage_cloak",
    "evasion",
    "fate_kick",
    "fantasy_move",
    "fly_leap",
    "frey_quick_flash",
    "fuma_pursuit",
    "gale",
    "ghost_step",
    "iaido_charge",
    "iron_chain_path",
    "jirobo_follow_step",
    "lao_mage_hand",
    "mana_pull",
    "mounted_leap",
    "natsume_wind_word",
    "nuclear_rush",
    "plasma_thruster",
    "rainbow_mirror",
    "remi_chaos",
    "shadow_counter",
    "split",
    "thor_rage_impact",
    "true_blade_air_slash",
    "weapon_transfer",
    "zero_dash",
}


def skill_has_movement_effect(skill: Skill) -> bool:
    if skill.code in {"departure_signal", "silver_grappling_hook"}:
        return True
    if skill.code in MOVEMENT_SKILL_CODES:
        return True
    movement_keywords = ("飞跃", "回避", "撤步", "牵引", "链条", "锁链", "瞬移", "换位", "移动", "位移", "推进", "飞踢", "降临", "喷射", "追步", "飞钩", "启航")
    return any(keyword in skill.name for keyword in movement_keywords)


class MovementSkillLockStatus(StatusEffect):
    def __init__(self, name: str = "百鸟葬禁位移", *, duration: int = 2) -> None:
        super().__init__(name, "不能使用带有位移效果的技能。", duration=duration, tick_scope="owner_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill_has_movement_effect(skill):
            return True, f"{self.name}状态下不能使用带有位移效果的技能。"
        return False, ""


class HundredBirdBurialSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r13_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "hundred_bird_burial",
            "百鸟葬",
            "普通技能：2 轮一次；远程 3*6 或 6*3 区域；伤害值为此单位攻 +2；被击中单位受到破魔禁位移效果 2 轮。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 6) + remote_rectangle_patterns(battle, actor, 6, 3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack") + 2,
                    is_skill=True,
                    action_name="百鸟葬",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "attack", "hundred_bird_burial"},
                )
            )
            if unit.alive:
                apply_piercing_status_effect(
                    battle,
                    actor,
                    unit,
                    action_name="百鸟葬禁位移",
                    status=MovementSkillLockStatus(),
                    is_skill=True,
                    consume_shield=not ctx.shield_consumed,
                    tags={"skill", "hundred_bird_burial", "movement_lock"},
                )

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True  # Only the movement lock pierces; damage is still ordinary.

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


