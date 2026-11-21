"""R055: a ranged instant flash and independently rolled followers."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, Skill, Stats, Trait, Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class FollowerUnit(AbstractHero):
    hero_code = "kanzaki_follower"
    hero_name = "追随者"
    role = "召唤物"
    attribute = "木"
    race = "人类"
    level = 1
    base_stats = Stats(attack=1, defense=1, speed=1, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "攻守速范各由一枚公开六面骰决定。"

    def __init__(self, player_id: int, values: tuple[int, int, int, int]) -> None:
        self.base_stats = Stats(attack=values[0], defense=values[1], speed=values[2],
                                attack_range=values[3], mana=0)
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return []


class SummonFollowerSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("summon_follower", "追随者",
                         "每本人轮一次；外邻召一只追随者，攻守速范各独立投公开六面骰。",
                         max_uses_per_turn=1, target_mode="cell")

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        body = set(battle.unit_cells(actor))
        probe = FollowerUnit(actor.player_id, (1, 1, 1, 1))
        return sorted((cell for cell in set(square_around_cells(battle, body, 1)) - body
                       if battle.can_place_unit(probe, cell)), key=lambda cell: (cell.y, cell.x))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.destinations(battle, actor)
        if not cells:
            return False, "追随者没有合法落格。"
        if payload is not None and "x" in payload:
            try:
                if payload_position(payload) not in cells:
                    return False, "追随者落格不合法。"
            except (ActionError, TypeError, ValueError):
                return False, "追随者落格不合法。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.destinations(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        cell = payload_position(payload)
        if cell not in self.destinations(battle, actor):
            raise ActionError("追随者落格不合法。")
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
        return 38.0 + max(0.0, 5.0 - nearest) * 7.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        if cell not in self.destinations(battle, actor):
            battle.log_public_event(f"{actor.name} 的追随者落格已失效。", source=actor)
            return
        values = tuple(battle.roll_die(6, action_name="追随者能力") for _ in range(4))
        child = FollowerUnit(actor.player_id, values)
        battle.summon_unit(child, cell, summoner=actor)
        battle.log_public_event(f"{child.name} 的攻/守/速/范为 {values[0]}/{values[1]}/{values[2]}/{values[3]}。",
                                source=actor, target=child)


class KanzakiFlashSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("kanzaki_flash", "一闪", "四格内单格当前攻+1技能伤害；随时发动，每4本人轮一次。",
                         cooldown_turns=4, target_mode="cell", timing="instant")

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        return [Position(x, y) for y in range(battle.height) for x in range(battle.width)
                if any(0 < origin.distance_to(Position(x, y)) <= 4 and
                       battle.cells_are_straight_aligned(origin, Position(x, y))
                       for origin in battle.unit_cells(actor))]

    def selected(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Position:
        cell = payload_position(payload)
        if cell not in self.cells(battle, actor):
            raise ActionError("一闪目标格须在直线四格内。")
        return cell

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload is not None and "x" in payload:
            try:
                self.selected(battle, actor, payload)
            except (ActionError, TypeError, ValueError) as exc:
                return False, str(exc)
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.cells(battle, actor)
        return {"cells": positions_to_dict(cells), "target_unit_ids": [unit.unit_id
                for unit in battle.effect_units_at_cells(cells, ignore=actor)],
                "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [self.selected(battle, actor, payload)]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload), ignore=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.cells(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cell = self.selected(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        value = -8.0
        for unit in battle.effect_units_at_cells([cell], ignore=actor):
            if unit.magic_immunity:
                continue
            sign = 1 if unit.player_id != actor.player_id else -1
            if unit.total_shields() > 0:
                value += sign * 20.0
            else:
                power = actor.stat("attack") + 1
                damage = 1.0 if power > unit.stat("defense") else 1.0 / 2 ** (unit.stat("defense") - power + 1)
                value += sign * (damage * 95.0)
        return value

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "declared_area_cells") if "declared_area_cells" in payload else [self.selected(battle, actor, payload)]
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            battle.resolve_damage(DamageContext(source=actor, target=unit,
                                                attack_power=actor.stat("attack") + 1,
                                                is_skill=True, action_name=self.name,
                                                tags={"skill", self.code}))

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict([self.selected(battle, actor, payload)])}


__all__ = ["FollowerUnit", "SummonFollowerSkill", "KanzakiFlashSkill"]
