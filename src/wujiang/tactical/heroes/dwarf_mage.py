"""R046 矮人法师：大穿刺、冰熊坐骑与六格冰风。"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, Skill, Stats, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import (
    StatModifierStatus, match_payload_pattern, pattern_selection_preview,
    pattern_signature, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.foundation import LargePierceSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class LargeIceSpikeSkill(LargePierceSkill):
    """The source's 冰刺（大） is exactly 穿刺（大） in battle rules."""

    def __init__(self) -> None:
        super().__init__(name="冰刺（大）")


class IceBearRiderMana(Trait):
    def __init__(self) -> None:
        super().__init__("冰熊乘骑回魔", "矮人法师乘骑本冰熊时，每个自己的回合开始自然回魔1。")

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        bear = self.owner
        if (bear is None or not bear.alive or bear.banished
                or bear.mount_owner_id != active_unit.unit_id
                or battle.mounted_unit_for(active_unit) is not bear):
            return
        gained = active_unit.gain_mana(1)
        if gained:
            battle.log_public_event(f"{active_unit.name} 乘骑冰熊，自然回复 {gained} 魔。",
                                    source=bear, target=active_unit)


class IceBear(AbstractHero):
    hero_code = "dwarf_mage_ice_bear"
    hero_name = "冰熊"
    role = "召唤物"
    attribute = "冰"
    race = "兽"
    level = 1
    base_stats = Stats(attack=4, defense=5, speed=4, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "可被召唤者乘骑；骑乘者自然回魔"

    def __init__(self, player_id: int, rider_id: str) -> None:
        super().__init__(player_id, is_summon=True)
        self.is_mount = True
        self.mount_owner_id = rider_id

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [IceBearRiderMana()]


class SummonIceBearsSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    bear_count = 2

    def __init__(self) -> None:
        super().__init__("summon_ice_bears", "冰熊*2",
                         "本场一次；选择两个合法相邻空格，召唤攻4守5速4范1的冰熊。"
                         "矮人法师可移动到自己的冰熊上乘骑；乘骑期间自然回魔。",
                         max_uses_per_battle=1, target_mode="cell")

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        body = set(battle.unit_cells(actor))
        probe = IceBear(actor.player_id, actor.unit_id)
        return sorted((cell for cell in set(square_around_cells(battle, body, 1)) - body
                       if battle.can_place_unit(probe, cell)),
                      key=lambda cell: (cell.y, cell.x))

    def selected(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        raw = payload.get("cells")
        if not isinstance(raw, list) or len(raw) != self.bear_count:
            raise ActionError("冰熊需要选择两个合法落格。")
        try:
            cells = [Position(int(item["x"]), int(item["y"])) for item in raw]
        except (TypeError, ValueError, KeyError) as exc:
            raise ActionError("冰熊落格不合法。") from exc
        if len(set(cells)) != self.bear_count or not set(cells) <= set(self.destinations(battle, actor)):
            raise ActionError("冰熊落格不能重复或被占用。")
        return cells

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if len(self.destinations(battle, actor)) < self.bear_count:
            return False, "冰熊周围没有两个合法落格。"
        if payload is not None and "cells" in payload:
            try:
                self.selected(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.destinations(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "pattern_cells", "patterns": [], "ordered": False,
                              "required_cells": self.bear_count}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.selected(battle, actor, payload)

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_bear_cells": positions_to_dict(self.selected(battle, actor, payload))}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "declared_bear_cells") or self.selected(battle, actor, payload)
        probe = IceBear(actor.player_id, actor.unit_id)
        if any(not battle.can_place_unit(probe, cell) for cell in cells):
            battle.log_public_event(f"{actor.name} 的冰熊落格被占，召唤未生效。", source=actor)
            return
        for cell in cells:
            battle.summon_unit(IceBear(actor.player_id, actor.unit_id), cell, summoner=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(pair)}
                for pair in combinations(self.destinations(battle, actor), self.bear_count)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cells = self.selected(battle, actor, payload)
        except ActionError:
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and not unit.banished and unit.position is not None]
        if not enemies:
            return 20.0
        return 65.0 + sum(max(0.0, 5.0 - min(battle.unit_distance_to_cell(unit, cell)
                                                for unit in enemies)) * 7.0 for cell in cells)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class IceWindSlow(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("冰风减速", speed_delta=-2,
                         description="被冰风击中，速-2，最低1。")


class IceWindSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("ice_wind", "冰风",
                         "每回合一次；选择身前2×3区域，造成一次普通技能伤害；"
                         "被击中单位速-2，最低1。",
                         max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        if actor.position is None:
            return []
        found: dict[tuple[tuple[int, int], ...], list[Position]] = {}
        for origin in battle.unit_cells(actor):
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                lateral = (-dy, dx)
                for sides in ((-1, 0), (0, 1)):
                    cells = [cell for step in range(1, 4) for side in sides
                             if battle.in_bounds(cell := origin.offset(
                                 dx * step + lateral[0] * side,
                                 dy * step + lateral[1] * side))]
                    if cells:
                        found[pattern_signature(cells)] = cells
        return list(found.values())

    def chosen(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        declared = battle.payload_positions(payload, "declared_ice_wind_cells")
        return declared or match_payload_pattern(payload, self.patterns(battle, actor))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.chosen(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.chosen(battle, actor, payload), ignore=actor))

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_ice_wind_cells": positions_to_dict(self.chosen(battle, actor, payload))}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen(battle, actor, payload)
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"),
                is_skill=True, action_name="冰风",
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                tags={"skill", "attack", "ice_wind"},
            ))
            if damage_followup_effect_applies(ctx) and unit.alive:
                unit.add_status(IceWindSlow(), source=actor)
                battle.log_public_event(f"{unit.name} 被冰风击中，速-2。", source=actor, target=unit)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cells = self.chosen(battle, actor, payload)
        except ActionError:
            return -1000.0
        score = 0.0
        enemy_hit = False
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            if unit.magic_immunity:
                continue
            damage = (20.0 if unit.total_shields() else min(
                unit.current_hp, battle.damage_rule.calculate_damage(
                    actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(unit, cells) - 1),
                    unit.stat("defense"))) * 100.0)
            slow = 30.0 if unit.stat("speed") > 1 else 0.0
            impact = damage + slow
            if unit.player_id == actor.player_id:
                score -= impact * 1.5
            else:
                score += impact
                enemy_hit |= impact > 0
        return score if enemy_hit and score > 0 else -1000.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
