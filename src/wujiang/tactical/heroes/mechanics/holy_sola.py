"""Holy wall, Sola aura, and illumination objects."""

from __future__ import annotations
from wujiang.tactical.heroes.ai_impact_rule import R18_IMPACT

from typing import Any
from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import LightWallSkill
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext, HeroAIPolicy
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.prediction import expected_numeric


@expected_numeric(independent=True)
def sola_harvest_position_value(battle: Battle, actor: HeroUnit, destination: Position | None) -> float:
    from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

    if destination is None:
        return 0.0
    with ai_probe_rollback(battle):
        actor.position = destination
        auras = [set(effect.affected_cells(battle)) for effect in battle.field_effects
                 if isinstance(effect, SolaHarvestAuraEffect) and effect.player_id == actor.player_id]
        value = 0.0
        for unit in battle.effect_units(battle.player_units(actor.player_id)):
            if unit.position is None or unit.banished or unit.direct_effects_blocked():
                continue
            if not any(any(cell in cells for cell in battle.unit_cells(unit)) for cells in auras):
                continue
            if not unit.cannot_heal:
                value += min(0.25, max(0.0, unit.max_health - unit.current_hp)) * 130
            value += min(1.0, max(0.0, unit.max_mana() - unit.current_mana)) * 25
        return value


class SolaHarvestAIPolicy(HeroAIPolicy):
    """Value reachable missing HP and mana inside the harvest aura."""

    def aura_move_score_bonus(self, battle: Battle, actor: HeroUnit,
                              destination: Position) -> float:
        return (sola_harvest_position_value(battle, actor, destination)
                - sola_harvest_position_value(battle, actor, actor.position))


class HolyWallSkill(LightWallSkill):
    ai_paid_reaction_shield = False

    def __init__(self) -> None:
        super().__init__()
        self.code = "holy_wall"
        self.name = "圣墙"
        self.description = "被动技能：规则同通用光墙，可为受影响的己方目标提供临时护盾。"


class SolaHarvestAuraEffect(BattleFieldEffect):
    def __init__(self, source_unit_id: str, player_id: int) -> None:
        self.source_unit_id = source_unit_id
        self.player_id = player_id
        super().__init__(
            "丰收光环",
            "丰收之神。索拉周围 11*11 内的己方单位在自己的回合开始时血 +1/4、魔 +1。",
            duration=None,
        )

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if isinstance(effect, SolaHarvestAuraEffect) and effect.source_unit_id == self.source_unit_id:
                return True
        return False

    def _source(self, battle: Battle) -> HeroUnit | None:
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive or source.position is None or source.banished:
            return None
        return source  # type: ignore[return-value]

    def affected_cells(self, battle: Battle) -> list[Position]:
        source = self._source(battle)
        if source is None:
            return []
        return square_around_cells(battle, battle.unit_cells(source), radius=5)

    def board_marker(self, battle: Battle) -> str:
        return "丰"

    def on_turn_start(self, battle: Battle, active_unit: HeroUnit | None) -> None:
        source = self._source(battle)
        if source is None:
            existing_source = battle.units.get(self.source_unit_id)
            if existing_source is None or not existing_source.alive:
                battle.remove_field_effect(self)
            return
        if active_unit is None or active_unit.player_id != self.player_id:
            return
        affected = {(cell.x, cell.y) for cell in self.affected_cells(battle)}
        receipt = getattr(battle, "_harvest_start_receipt", None)
        if receipt is None or receipt["turn"] != battle.turn_number:
            receipt = {"turn": battle.turn_number, "units": set()}
            battle._harvest_start_receipt = receipt
        for unit in battle.effect_units(battle.turn_bundle_units(active_unit)):
            if unit.player_id != self.player_id or unit.position is None or unit.banished:
                continue
            if not any((cell.x, cell.y) in affected for cell in battle.unit_cells(unit)):
                continue
            if unit.unit_id in receipt["units"]:
                continue
            receipt["units"].add(unit.unit_id)
            battle.heal(HealContext(source=source, target=unit, amount=0.25, action_name="丰收光环"))
            gained = unit.gain_mana(1)
            if gained:
                battle.log(f"{unit.name} 因【丰收光环】获得 {gained} 点魔。")


class SolaHarvestAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("丰收光环", "周围 11*11 内己方单位每个自己的回合开始时血 +1/4、魔 +1。")

    def on_enter_battle(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            battle.add_field_effect(SolaHarvestAuraEffect(owner.unit_id, owner.player_id))

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for effect in list(battle.field_effects):
            if isinstance(effect, SolaHarvestAuraEffect) and effect.source_unit_id == owner.unit_id:
                battle.remove_field_effect(effect)


class IlluminationLightSkill(Skill):
    def ai_ignores_shield_for_target(self, battle, actor, payload, target):
        return target.attribute == "暗"

    ai_effective_impact_rule = R18_IMPACT
    def ai_attack_power(self, battle, actor, payload, target, cells) -> float:
        return 4.0

    target_cells_are_geometry_only = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r18_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "illumination_light",
            "照明之光",
            "普通技能：2 轮一次；周围 11*11 内敌方武将受到伤害值 4 的技能伤害；暗属性目标的这次伤害破魔。",
            cooldown_turns=2,
            target_mode="self",
        )

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=5)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"illumination_cells": positions_to_dict(self.affected_cells(battle, actor))}

    def targets(self, battle: Battle, actor: HeroUnit, cells: list[Position] | None = None) -> list[HeroUnit]:
        from wujiang.tactical.engine.army import is_army_soldier
        affected = set(cells if cells is not None else self.affected_cells(battle, actor))
        result: list[HeroUnit] = []
        for unit in battle.enemy_units(actor.player_id):
            if unit.is_summon or unit.is_clone or is_army_soldier(unit) or unit.position is None or unit.banished:
                continue
            if any(cell in affected for cell in battle.unit_cells(unit)):
                result.append(unit)  # type: ignore[arg-type]
        return list(battle.effect_units(result))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.get_target_cells_for_payload(battle, actor, payload)
        for target in self.targets(battle, actor, cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=4,
                    is_skill=True,
                    action_name="照明之光",
                    ignore_shield=target.attribute == "暗",
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "illumination_light"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor)
        targets = [unit for unit in battle.enemy_units(actor.player_id)
                   if (not unit.is_summon or unit.is_clone) and unit.position is not None and not unit.banished
                   and any(cell in cells for cell in battle.unit_cells(unit))
                   and battle.unit_can_be_selected(unit, actor=actor)[0]]
        return {
            "cells": [cell.to_dict() for cell in cells],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": False,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and "illumination_cells" in payload:
            return [Position(**cell) for cell in payload["illumination_cells"]]
        return self.affected_cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return self.targets(battle, actor, self.get_target_cells_for_payload(battle, actor, payload))


class MeditateManaSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        missing_mana = max(0.0, context.actor.max_mana() - context.actor.current_mana)
        restored = min(1.5, missing_mana)
        return restored * 34.0 if restored >= 0.5 else -8.0

    def __init__(self) -> None:
        super().__init__(
            "oboro_meditate",
            "凝神",
            "普通技能：3 轮一次；自身魔 +1.5。",
            cooldown_turns=3,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        gained = actor.gain_mana(1.5)
        battle.log(f"{actor.name} 使用【凝神】，获得 {gained} 点魔。")


