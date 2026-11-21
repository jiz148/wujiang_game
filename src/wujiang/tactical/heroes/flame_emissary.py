"""R062: flame-giant deployment and area attacks that share their geometry."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, Skill, Stats, Trait, Unit,
)
from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import (
    PassiveProtectionSkill, match_payload_pattern, pattern_selection_preview,
    payload_position, positions_to_dict,
)
from wujiang.tactical.heroes.flame_manager import FireWallSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class GiantFreeProtectionSkill(PassiveProtectionSkill):
    def __init__(self) -> None:
        super().__init__()
        self.mana_cost = 0
        self.max_uses_per_turn = None
        self.max_uses_per_battle = 1
        self.description = "炎巨人可免费使用一次保护，不占其他单位的保护次数。"


class FlameGiantUnit(AbstractHero):
    hero_code = "flame_giant_r062"
    hero_name = "炎巨人"
    role = "召唤物"
    attribute = "火"
    race = "巨人"
    level = 1
    base_stats = Stats(attack=4, defense=4, speed=3, attack_range=2, mana=0)
    raw_skill_text = "攻击两格；一次免费保护。"
    raw_trait_text = ""

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [GiantFreeProtectionSkill()]

    def build_traits(self) -> list[Trait]:
        return []


def own_giants(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.player_units(actor.player_id)
            if unit.hero_code == FlameGiantUnit.hero_code and unit.alive
            and not unit.banished and unit.position is not None]


class SummonFlameGiantSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("summon_flame_giant", "炎巨人",
                         "费1魔外邻召炎巨人；每2本人轮可免费召一只。",
                         mana_cost=1, target_mode="cell")
        self.free_wait = 0

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        body = set(battle.unit_cells(actor))
        probe = FlameGiantUnit(actor.player_id)
        return sorted((cell for cell in set(square_around_cells(battle, body, 1)) - body
                       if battle.can_place_unit(probe, cell)), key=lambda cell: (cell.y, cell.x))

    def mana_cost_for_payload(self, battle: Battle, actor: Unit,
                              payload: dict[str, Any] | None = None) -> float:
        if self.free_wait == 0:
            return 0.0
        return super().mana_cost_for_payload(battle, actor, payload)

    def prepay_resources(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any] | None = None) -> None:
        free = self.free_wait == 0
        super().prepay_resources(battle, actor, payload)
        if free:
            self.free_wait = 2

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        super().on_any_turn_end(battle, ended_player_id)
        if self.free_wait > 0 and battle.unit_belongs_to_current_turn(self.owner):
            self.free_wait -= 1

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.destinations(battle, actor)
        if not cells:
            return False, "炎巨人没有合法落格。"
        if payload is not None and "x" in payload:
            try:
                if payload_position(payload) not in cells:
                    return False, "炎巨人落格不合法。"
            except (ActionError, TypeError, ValueError):
                return False, "炎巨人落格不合法。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.destinations(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        cell = payload_position(payload)
        if cell not in self.destinations(battle, actor):
            raise ActionError("炎巨人落格不合法。")
        return [cell]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.destinations(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cell = self.get_target_cells_for_payload(battle, actor, payload)[0]
        except (ActionError, TypeError, ValueError):
            return -1000.0
        nearest = min((battle.unit_distance_to_cell(unit, cell) for unit in battle.enemy_units(actor.player_id)
                       if unit.alive and unit.position is not None and not unit.banished), default=8)
        return 42.0 + max(0.0, 5.0 - nearest) * 9.0 - (0 if self.free_wait == 0 else 12.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        if cell not in self.destinations(battle, actor):
            battle.log_public_event(f"{actor.name} 的炎巨人落格已失效。", source=actor)
            return
        battle.summon_unit(FlameGiantUnit(actor.player_id), cell, summoner=actor)


class FlameAreaSkill(Skill):
    ai_payload_value_owned = True
    ai_candidate_plan = RANKED_CANDIDATES
    ai_enemy_impact_requirement = False
    target_cells_are_geometry_only = True
    width = 3
    height = 3

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, self.width, self.height)

    def selected(self, battle: Battle, actor: Unit,
                 payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def extra_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        return []

    def declared_cells(self, battle: Battle, actor: Unit,
                       payload: dict[str, Any]) -> tuple[list[Position], list[Position]]:
        if "declared_flame_area" in payload:
            return battle.payload_positions(payload, "declared_flame_area"), battle.payload_positions(payload, "declared_flame_extra")
        return self.selected(battle, actor, payload), self.extra_cells(battle, actor)

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        area = self.selected(battle, actor, payload)
        extra = self.extra_cells(battle, actor)
        return {"declared_flame_area": positions_to_dict(area),
                "declared_flame_extra": positions_to_dict(extra),
                "declared_flame_empty": not battle.units_at_cells(area)}

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        result = pattern_selection_preview(self.patterns(battle, actor))
        extra = self.extra_cells(battle, actor)
        cells = [Position(int(item["x"]), int(item["y"])) for item in result["cells"]]
        affected = battle.effect_units_at_cells(cells, ignore=actor)
        affected.extend(unit for unit in battle.effect_units_at_cells(extra, ignore=actor)
                        if unit.player_id != actor.player_id)
        result.update({"secondary_cells": positions_to_dict(extra),
                       "target_unit_ids": list(dict.fromkeys(unit.unit_id for unit in affected)),
                       "requires_target": True})
        return result

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        area, extra = self.declared_cells(battle, actor, payload)
        return list(dict.fromkeys([*area, *extra]))

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        area, extra = self.declared_cells(battle, actor, payload)
        affected = battle.effect_units_at_cells(area, ignore=actor)
        affected.extend(unit for unit in battle.effect_units_at_cells(extra, ignore=actor)
                        if unit.player_id != actor.player_id)
        return list({unit.unit_id: unit for unit in affected}.values())

    def ai_effect_units(self, battle: Battle, actor: Unit,
                        payload: dict[str, Any]) -> list[Unit]:
        return self.get_target_units_for_payload(battle, actor, payload)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(pattern)} for pattern in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float:
        try:
            area, extra = self.declared_cells(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        value = -8.0
        for unit in self.get_target_units_for_payload(battle, actor, payload):
            if unit.magic_immunity:
                continue
            sign = 1.0 if unit.player_id != actor.player_id else -1.3
            if unit.total_shields() > 0:
                value += sign * 20.0
            else:
                power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(unit, area) - 1)
                damage = 1.0 if power > unit.stat("defense") else 1.0 / 2 ** (unit.stat("defense") - power + 1)
                value += sign * min(damage, unit.current_hp) * 100.0
        return value

    def ai_prefilter_score(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any], shared: Any) -> float:
        return self.ai_payload_value(battle, actor, payload)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        area, extra = self.declared_cells(battle, actor, payload)
        for unit in self.get_target_units_for_payload(battle, actor, payload):
            battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"),
                area_cell_hits=battle.unit_hit_count_for_cells(unit, area),
                is_skill=True, action_name=self.name, tags={"skill", self.code}))


class ExplosionSkill(FlameAreaSkill):
    def __init__(self) -> None:
        super().__init__("explosion", "爆裂", "2魔，选3×3伤双方；整片无单位则尽可能逐格召炎巨人。",
                         mana_cost=2, target_mode="cell")

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float:
        try:
            area, _ = self.declared_cells(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        if not battle.units_at_cells(area):
            probe = FlameGiantUnit(actor.player_id)
            count = sum(battle.can_place_unit(probe, cell) for cell in area)
            return count * 22.0 - 16.0 if count else -1000.0
        return super().ai_payload_value(battle, actor, payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        area, _ = self.declared_cells(battle, actor, payload)
        empty = bool(payload.get("declared_flame_empty", not battle.units_at_cells(area)))
        if not empty:
            super().execute(battle, actor, payload)
            return
        count = 0
        for cell in area:
            giant = FlameGiantUnit(actor.player_id)
            if battle.can_place_unit(giant, cell):
                battle.summon_unit(giant, cell, summoner=actor)
                count += 1
        battle.log_public_event(f"{actor.name} 的爆裂在空区召出 {count} 只炎巨人。", source=actor)


class PassionImpactSkill(FlameAreaSkill):
    width = 2
    height = 2

    def __init__(self) -> None:
        super().__init__("passion_impact", "热情冲击",
                         "1魔、每本人轮一次；2×2伤双方，并伤全场己方炎巨人外邻敌人。",
                         mana_cost=1, max_uses_per_turn=1, target_mode="cell")

    def extra_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        cells: set[Position] = set()
        for giant in own_giants(battle, actor):
            body = set(battle.unit_cells(giant))
            cells.update(set(square_around_cells(battle, body, 1)) - body)
        return sorted(cells, key=lambda cell: (cell.y, cell.x))


class TeamHealSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("team_heal", "加血", "大招：全场己方现存单位各回复1/4血。",
                         max_uses_per_battle=1, target_mode="self")

    def allies(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.player_units(actor.player_id)
                if unit.alive and not unit.banished and unit.position is not None]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        allies = self.allies(battle, actor)
        return {"cells": positions_to_dict([cell for unit in allies for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in allies],
                "secondary_cells": [], "requires_target": False}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.allies(battle, actor)

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float:
        missing = sum(min(unit.max_health - unit.current_hp, 0.25) for unit in self.allies(battle, actor))
        return missing * 100.0 - 9.0 if missing > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for ally in self.allies(battle, actor):
            battle.heal(HealContext(source=actor, target=ally, amount=0.25, action_name=self.name))


class FlameWallSkill(FireWallSkill):
    def __init__(self) -> None:
        super().__init__()
        self.name = "炎墙"
        self.description = "沿通用火墙：连锁保护受影响己方单位。"


__all__ = ["FlameGiantUnit", "GiantFreeProtectionSkill", "SummonFlameGiantSkill",
           "ExplosionSkill", "PassionImpactSkill", "TeamHealSkill", "FlameWallSkill"]
