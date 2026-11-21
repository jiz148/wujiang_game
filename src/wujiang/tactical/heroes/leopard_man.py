"""R061 豹头人: six-cell rush and ally-owned companion travel."""

from __future__ import annotations


from typing import Any
from wujiang.tactical.component_providers import readonly_component_fields

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, Position, Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.ai_candidate_plan import TargetDestinationCandidates
from wujiang.tactical.heroes.common import DashMoveSkill, payload_position, positions_to_dict


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def _leopards(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.all_units()
            if unit is not actor and unit.player_id == actor.player_id
            and unit.hero_code == "excel_r061" and _present(unit)
            and battle.unit_can_be_selected(unit, actor=actor)[0]
            and battle.unit_target_in_range_and_line(actor, unit, 6)]


class CompanionTravelSkill(TargetDestinationCandidates, Skill):
    ai_destination_fields = ("dest_x", "dest_y")
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    is_displacement_skill = True

    def __init__(self) -> None:
        super().__init__("companion_travel", "结伴而行",
                         "每本人轮一次；范6选己方豹头人并瞬移到其周围合法格。",
                         max_uses_per_turn=1, target_mode="unit")

    def direct_unit_target_range(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any] | None = None) -> int:
        return 6

    def landings(self, battle: Battle, actor: Unit, target: Unit) -> list[Position]:
        body = set(battle.unit_cells(target))
        return sorted({cell for origin in body for cell in battle.neighbors(origin)
                       if cell not in body and battle.can_place_unit(actor, cell, ignore=actor, mover=actor)},
                      key=lambda cell: (cell.y, cell.x))

    def choices(self, battle: Battle, actor: Unit) -> dict[str, list[Position]]:
        if actor.position is None or actor.cannot_move:
            return {}
        return {target.unit_id: cells for target in _leopards(battle, actor)
                if (cells := self.landings(battle, actor, target))}

    def selected(self, battle: Battle, actor: Unit,
                 payload: dict[str, Any]) -> tuple[Unit, Position]:
        target_id = str(payload.get("target_unit_id") or "")
        choices = self.choices(battle, actor)
        if target_id not in choices:
            raise ActionError("结伴而行须选择范6内己方豹头人。")
        cell = payload_position(payload, "dest_x", "dest_y")
        if cell not in choices[target_id]:
            raise ActionError("结伴而行落格已不合法。")
        return battle.units[target_id], cell

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.choices(battle, actor):
            return False, "范6内没有可同行的豹头人及落格。"
        if payload is not None and payload.get("target_unit_id"):
            try:
                self.selected(battle, actor, payload)
            except (ActionError, TypeError, ValueError) as exc:
                return False, str(exc)
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        choices = self.choices(battle, actor)
        return {"cells": positions_to_dict([cell for unit_id in choices
                                              for cell in battle.unit_cells(battle.units[unit_id])]),
                "target_unit_ids": list(choices), "secondary_cells": [], "requires_target": True,
                "destinations_by_target": {unit_id: positions_to_dict(cells)
                                           for unit_id, cells in choices.items()},
                "selection": {"mode": "unit_then_destination",
                              "cell_prompt": "先选豹头人，再选其周围的合法落格。"}}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [self.selected(battle, actor, payload)[0]]

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(self.selected(battle, actor, payload)[0])

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit_id, "dest_x": cell.x, "dest_y": cell.y}
                for unit_id, cells in self.choices(battle, actor).items() for cell in cells]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            _, landing = self.selected(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        if not enemies or actor.position is None:
            return -1000.0
        old = min(actor.position.distance_to(cell) for unit in enemies for cell in battle.unit_cells(unit))
        new = min(landing.distance_to(cell) for unit in enemies for cell in battle.unit_cells(unit))
        attack_range = actor.targeting_range()
        value = (old - new) * 8.0 + (24.0 if old > attack_range >= new else 0.0)
        if actor.current_hp < actor.max_health / 2:
            value += (new - old) * 5.0
        return value - 9.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            _, landing = self.selected(battle, actor, payload)
            battle.move_unit(actor, landing, via_skill=True, allow_anywhere=True,
                             tags={"companion_travel"})
        except ActionError:
            battle.log_public_event(f"{actor.name} 的结伴而行目标或落格已失效。", source=actor)


class CompanionTravelGrant(StatusEffect):
    def __init__(self) -> None:
        super().__init__("豹头人同行", "可使用结伴而行，向范6内己方豹头人瞬移。")
        self.skill = CompanionTravelSkill()

    def bind(self, owner: Unit) -> CompanionTravelGrant:
        super().bind(owner)
        self.skill.bind(owner)
        return self

    @readonly_component_fields('skill')
    def provided_components(self) -> tuple[Skill, ...]:
        return (self.skill,)


class CompanionTravelField(BattleFieldEffect):
    def __init__(self, player_id: int) -> None:
        super().__init__("豹头人同行许可")
        self.player_id = player_id

    def on_unit_entered(self, battle: Battle, unit: Unit) -> None:
        if unit.player_id == self.player_id and unit.hero_code != "excel_r061" \
                and not unit.has_status("豹头人同行"):
            unit.add_status(CompanionTravelGrant())

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if any(unit.hero_code == "excel_r061" and _present(unit)
               for unit in battle.all_units() if unit.player_id == self.player_id):
            return
        for unit in battle.all_units():
            if unit.player_id == self.player_id:
                status = unit.get_status("豹头人同行")
                if isinstance(status, CompanionTravelGrant):
                    unit.remove_status(status, battle)
        battle.remove_field_effect(self)


class CompanionTravelTrait(Trait):
    def __init__(self) -> None:
        super().__init__("结伴而行许可", "己方其他单位可各自主动使用结伴而行。")

    def on_enter_battle(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        field = next((effect for effect in battle.field_effects
                      if isinstance(effect, CompanionTravelField)
                      and effect.player_id == owner.player_id), None)
        if field is None:
            field = CompanionTravelField(owner.player_id)
            battle.add_field_effect(field, source=owner)
        for unit in battle.all_units():
            field.on_unit_entered(battle, unit)


class BeastRushField(BattleFieldEffect):
    def __init__(self, source_id: str, player_id: int) -> None:
        super().__init__("兽族奔袭增伤")
        self.source_unit_id = source_id
        self.player_id = player_id

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        source = battle.units.get(self.source_unit_id)
        if (_present(source) and _present(ctx.target)
                and ctx.target.player_id != self.player_id and ctx.raw_damage is not None
                and ctx.raw_damage > 0 and battle.distance_between_units(source, ctx.target) <= 1):
            ctx.raw_damage += 1.0

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if ended_player_id == self.player_id:
            battle.remove_field_effect(self)


class BeastRushSkill(DashMoveSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("beast_rush", "兽族奔袭",
                         "本场一次；移动至多6格，本队本回合外邻敌人受所有生命伤+1。",
                         max_distance=6, mana_cost=0, max_uses_per_turn=1)
        self.max_uses_per_battle = 1
        self.is_ultimate = True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        super().execute(battle, actor, payload)
        if _present(actor):
            battle.add_field_effect(BeastRushField(actor.unit_id, actor.player_id), source=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y}
                for cell in battle.reachable_positions(actor, max_distance=6)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.position is None:
            return -1000.0
        cell = payload_position(payload)
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        if not enemies:
            return -1000.0
        old = min(actor.position.distance_to(p) for unit in enemies for p in battle.unit_cells(unit))
        new = min(cell.distance_to(p) for unit in enemies for p in battle.unit_cells(unit))
        adjacent = sum(cell.distance_to(p) <= 1 for unit in enemies for p in battle.unit_cells(unit))
        return (old - new) * 7.0 + adjacent * 20.0 - 9.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
