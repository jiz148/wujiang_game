"""R305 electronic guard, quantum separation, and nova objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import MachineGunSkill
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.racial_weather import SimpleGlobalWeatherEffect
from wujiang.tactical.heroes.mechanics.racial_weather import WeatherUltimateSkill
from wujiang.tactical.heroes.next_five import CommonMissileSkill
from wujiang.tactical.heroes.next_five import square_around_cells


class ElectronicGuardDragonExpiry(Trait):
    def __init__(self) -> None:
        super().__init__("量子分离时限", "召唤所在的主人回合结束时破坏。")
        self.summoned_turn_number: int | None = None

    def on_enter_battle(self, battle: Battle) -> None:
        self.summoned_turn_number = battle.turn_number

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        result = super().to_public_dict(battle)
        result.update({"summoned_turn_number": self.summoned_turn_number,
                       "expires_on_owner_turn_end": True})
        return result

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.alive:
            return
        owner.alive = False
        battle.log_public_event(f"{owner.name} 在召唤回合结束时破坏。", target=owner)
        battle.cleanup_dead_units()


class ElectronicGuardDragon(AbstractHero):
    hero_code = "electronic_guard_dragon"
    hero_name = "电子守卫龙"
    role = "召唤物"
    attribute = "雷"
    race = "机甲"
    level = 1
    base_stats = Stats(attack=3, defense=4, speed=3, attack_range=3, mana=0)
    raw_skill_text = "机枪；导弹"
    raw_trait_text = "在召唤的主人回合结束时破坏。"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [MachineGunSkill(), CommonMissileSkill()]

    def build_traits(self) -> list[Trait]:
        return [ElectronicGuardDragonExpiry()]


class QuantumSeparationSkill(Skill):
    def __init__(self) -> None:
        super().__init__("quantum_separation", "量子分离",
                         "每本人回合一次；范内召唤电子守卫龙，本回合可行动，主人轮末破坏。",
                         max_uses_per_turn=1, target_mode="cell")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        probe = ElectronicGuardDragon(actor.player_id)
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor),
                                                      radius=actor.targeting_range())
                if battle.can_place_unit(probe, cell)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        options = self.available_cells(battle, actor)
        if not options:
            return False, "范内没有合法空格召唤电子守卫龙。"
        if payload and any(key in payload for key in ("x", "y", "cells")):
            try:
                if payload_position(payload) not in options:
                    return False, "请选择范内合法空格召唤电子守卫龙。"
            except (ActionError, ValueError, TypeError, KeyError):
                return False, "请选择范内合法空格召唤电子守卫龙。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> None:
        if payload_position(payload or {}) not in self.available_cells(battle, actor):
            raise ActionError("电子守卫龙落点已失效。")
        super().prepay_resources(battle, actor, payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            battle.log_public_event(f"{actor.name} 的量子分离落点已被占据，召唤落空。", source=actor)
            return
        guard = ElectronicGuardDragon(actor.player_id)
        battle.summon_unit(guard, destination, summoner=actor)
        guard.turn_ready = True
        guard.can_act_on_entry_turn = True
        battle.log_public_event(f"{guard.name} 本回合可行动，回合末将被破坏。", source=actor, target=guard)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.available_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


class ElectromagneticFieldEffect(SimpleGlobalWeatherEffect):
    weather_name = "电磁场"

    def __init__(self) -> None:
        super().__init__(self.weather_name, marker="电")
        self.description = "全场天气：双方每次真正支付正数魔力时失去最大生命的1/8；同名不叠。"

    def on_skill_mana_paid(self, battle: Battle, actor: Unit, skill: Skill, amount: float) -> None:
        if amount <= 0 or not battle.weather_effect_applies(self, actor):
            return
        before = actor.current_hp
        actor.take_damage_fraction(actor.max_health / 8)
        loss = round(before - actor.current_hp, 4)
        battle.log_public_event(f"{actor.name} 支付 {amount:g} 魔使用【{skill.name}】，电磁场使其生命 -{loss:g}。",
                                target=actor)


class ElectromagneticFieldSkill(WeatherUltimateSkill):
    def __init__(self) -> None:
        super().__init__("electromagnetic_field", "电磁场", weather_name="电磁场", marker="电")
        self.description += "双方每次真正支付正数魔力时各自失去最大生命的1/8。"

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(ElectromagneticFieldEffect(), source=actor)


class NovaChargeStatus(StatModifierStatus):
    def __init__(self, created_turn: int) -> None:
        super().__init__("新星电荷", attack_delta=1, defense_delta=1,
                         description="敌方在7×7内付魔：攻、守各+1，到新星下一个本人回合结束。")
        self.created_turn = created_turn

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number > self.created_turn and self.owner is not None:
            self.owner.remove_status(self, battle)


class NovaManaReactionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("新星电荷", "7×7内敌方每次真正使用魔，攻守各+1至下一个本人回合结束。")

    def on_skill_mana_paid(self, battle: Battle, actor: Unit, skill: Skill, amount: float) -> None:
        owner = self.owner
        if (owner is None or not owner.alive or owner.banished or owner.position is None
                or actor.player_id == owner.player_id or amount <= 0
                or battle.distance_between_units(owner, actor) > 3):
            return
        charge = NovaChargeStatus(battle.turn_number)
        owner.add_status(charge, source=owner)
        if charge in owner.statuses:
            layers = sum(isinstance(status, NovaChargeStatus) for status in owner.statuses)
            battle.log_public_event(f"{actor.name} 在7×7内付魔，{owner.name} 的新星电荷叠至 {layers} 层。",
                                    source=actor, target=owner)


