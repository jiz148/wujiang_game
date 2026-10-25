"""R198 cycle riders and attack-triggered movement economy."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HeroUnit, Position, Skill, Stats, Trait,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import payload_position, positions_to_dict
from wujiang.tactical.heroes.next_five import square_around_cells

class ExtraMoveAfterAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击后移动次数+1", "本回合每完成一次普攻声明，普通移动次数上限 +1。")

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        owner = self.owner
        return value + (owner.attacks_used if owner is not None else 0)


class ResetMoveAfterAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击后重置移动", "每次普攻完成后，重置本回合普通移动次数和距离。")

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id:
            return
        owner.move_used = False
        owner.normal_move_actions_used = 0
        owner.normal_move_steps_used = 0
        battle.log(f"{owner.name} 攻击后重置了本回合普通移动。")


class UnicycleSummon(AbstractHero):
    hero_code = "unicycle_rider"
    hero_name = "一轮车人"
    role = "召唤物"
    attribute = "雷"
    race = "机甲"
    level = 1
    base_stats = Stats(attack=2, defense=3, speed=5, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [ResetMoveAfterAttackTrait()]


class SummonUnicycleSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "summon_unicycle",
            "一轮车人",
            "普通技能：每回合一次；在周围召唤攻2守3速5范1的一轮车人。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        probe = UnicycleSummon(actor.player_id)
        return [
            cell
            for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
            if battle.can_place_unit(probe, cell)
        ]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            raise ActionError("请选择二轮车周围的合法格召唤一轮车人。")
        battle.summon_unit(UnicycleSummon(actor.player_id), destination, summoner=actor)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": positions_to_dict(self.available_cells(battle, actor)),
            "target_unit_ids": [],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": True,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


class BicycleSummon(AbstractHero):
    hero_code = "bicycle_rider"
    hero_name = "二轮车"
    role = "召唤物"
    attribute = "雷"
    race = "机甲"
    level = 1
    base_stats = Stats(attack=3, defense=3, speed=5, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [SummonUnicycleSkill()]

    def build_traits(self) -> list[Trait]:
        return [ResetMoveAfterAttackTrait()]


class SummonBicycleSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "summon_bicycle",
            "二轮车",
            "普通技能：2 轮一次；在周围召唤攻3守3速5范1、攻击后重置移动的二轮车。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        probe = BicycleSummon(actor.player_id)
        return [
            cell
            for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
            if battle.can_place_unit(probe, cell)
        ]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            raise ActionError("请选择三轮车人周围的合法格召唤二轮车。")
        battle.summon_unit(BicycleSummon(actor.player_id), destination, summoner=actor)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": positions_to_dict(self.available_cells(battle, actor)),
            "target_unit_ids": [],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": True,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


