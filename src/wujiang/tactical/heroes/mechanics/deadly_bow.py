"""R029 deadly bow skill."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.heroes.common import positions_to_dict


class DeadlyBowSkill(Skill):
    selection_cells_are_effect_cells = False
    DIRECTIONS = {
        "north": (0, -1), "northeast": (1, -1), "east": (1, 0), "southeast": (1, 1),
        "south": (0, 1), "southwest": (-1, 1), "west": (-1, 0), "northwest": (-1, -1),
    }

    def __init__(self) -> None:
        super().__init__("deadly_bow", "致命之弓", "普通技能：1魔；八方向前5格，破魔；攻值等于声明时魔力点，声明即清空魔力点。", mana_cost=1, target_mode="cell")

    def cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            return []
        code = str(payload.get("choice_code") or payload.get("direction") or "")
        if code:
            direction = self.DIRECTIONS.get(code)
        else:
            direction = (payload.get("dx"), payload.get("dy"))
        if direction not in self.DIRECTIONS.values():
            raise ActionError("致命之弓需要选择八方向之一。")
        dx, dy = direction
        return [actor.position.offset(dx * step, dy * step) for step in range(1, 6)
                if battle.in_bounds(actor.position.offset(dx * step, dy * step))]

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        cells = self.cells(battle, actor, payload)
        if not cells:
            raise ActionError("该方向在棋盘内没有可攻击的格子。")
        if payload.get("cells") and battle.payload_positions(payload, "cells") != cells[:1]:
            raise ActionError("请选择该方向的第一格确认致命之弓。")
        return {"deadly_bow_cells": positions_to_dict(cells), "deadly_bow_points": actor.mana_points}

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> None:
        if payload is not None:
            payload["deadly_bow_points"] = actor.mana_points
        super().prepay_resources(battle, actor, payload)
        actor.spend_mana_points(actor.mana_points)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "deadly_bow_cells") if payload.get("queued_resolution") else self.cells(battle, actor, payload)
        power = float(payload.get("deadly_bow_points", actor.mana_points))
        for target in battle.effect_units_at_cells(cells):
            battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=power,
                                                is_skill=True, ignore_shield=True, action_name=self.name,
                                                tags={"skill", "deadly_bow", "area_attack"}))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor, payload)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        choices, destinations, all_cells = [], [], set()
        for code in self.DIRECTIONS:
            cells = self.cells(battle, actor, {"direction": code})
            if not cells:
                continue
            anchor = positions_to_dict(cells[:1])
            choices.append({"code": code, "label": {"north": "北", "northeast": "东北", "east": "东", "southeast": "东南", "south": "南", "southwest": "西南", "west": "西", "northwest": "西北"}[code], "patterns": [anchor]})
            destinations.append({"choice_code": code, "pattern": anchor, "destination_cells": positions_to_dict(cells)})
            all_cells.update(cells)
        return {"cells": positions_to_dict(sorted(all_cells, key=lambda cell: (cell.y, cell.x))),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                              "choice_prompt": "选择重箭方向，再点击第一格确认。",
                              "cell_prompt": "点击第一格确认；高亮为全部5格。"}}


# Only definitions whose text changes basic attacks belong to weapon copying.
# Mixed attack/skill rewards are narrowed to the attack branch below.
BASIC_ATTACK_COPY_TRAIT_TYPES = {
    "AttackCountTrait", "PrecisionTrainingTrait", "HalfPierceAttackTrait", "AttackLockTrait",
    "LinaDestroyRewardTrait", "TripleStrikeAttackTrait", "ArcAttackTrait", "MasamuneArcAttackTrait",
    "NAttackManaPointTrait", "NAttackCountTrait", "AttackLifeStealTrait", "AttackManaDrainTrait",
    "SoulWraithFailedAttackGrowthTrait", "BasicAttackPierceTrait", "NatsumeAllyAttackManaTrait",
    "FriedAllyAttackHealTrait", "WaterNinjaCloneAfterAttackTrait", "JiroboAfterAttackTrait",
    "WuchangAttackSealTrait", "FeiWangSpeedOnHeroKillTrait", "FusionCircleAttackTrait",
    "FlorenzaAttackFollowupTrait", "RedLotusUnevadableAttackTrait", "BlindEvasionAttackResetTrait",
    "BlindCircleAttackTrait", "ExtraMoveAfterAttackTrait", "ResetMoveAfterAttackTrait", "RedRandomPierceTrait",
    "AgencyPassivePunishTrait", "MountainGodCounterTrait", "ErasureApostleDestroyRewardTrait",
    "UnmountedCombatTrait", "ElectronicLaserDamageBonusTrait", "PerfectSwordsmanDefenseTrait",
    "DoomLightRetaliationTrait",
}


