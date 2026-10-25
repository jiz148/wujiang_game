"""Shared Hell skills, mount, and rebirth status objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import KnockbackSkill
from wujiang.tactical.heroes.common import LightWallSkill
from wujiang.tactical.heroes.common import PierceSkill
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.foundation import RemotePierceSkill
from wujiang.tactical.heroes.next_five import RemoteDragonBreathSkill


class HellSpearSkill(PierceSkill):
    def __init__(self, *, free: bool = False) -> None:
        super().__init__()
        self.free = free
        self.code = "hell_spear_free" if free else "hell_spear"
        self.name = "魔枪（0魔免费）" if free else "魔枪"
        self.description = ("当前魔恰为0时，每2本人轮免费使用一次通用穿刺。" if free
                            else "沿用通用穿刺：1.5魔，每本人轮最多2次。")
        if free:
            self.mana_cost = 0
            self.max_uses_per_turn = 1
            self.cooldown_turns = 2

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and self.free and actor.current_mana != 0:
            return False, "只有当前魔为0时才可免费使用魔枪。"
        return ok, reason


class HellRangedSpearSkill(RemotePierceSkill):
    def __init__(self, *, free: bool = False) -> None:
        super().__init__()
        self.free = free
        self.code = "hell_ranged_spear_free" if free else "hell_ranged_spear"
        self.name = "魔枪（远程0魔免费）" if free else "魔枪（远程）"
        self.description = ("当前魔恰为0时，每2本人轮免费使用一次远程穿刺。" if free
                            else "沿用远程穿刺：1.5魔，每本人轮最多2次。")
        if free:
            self.mana_cost = 0
            self.max_uses_per_turn = 1
            self.cooldown_turns = 2

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and self.free and actor.current_mana != 0:
            return False, "只有当前魔为0时才可免费使用远程魔枪。"
        return ok, reason


class HellPressureSkill(KnockbackSkill):
    def __init__(self, *, free: bool = False) -> None:
        super().__init__()
        self.free = free
        self.code = "hell_pressure_free" if free else "hell_pressure"
        self.name = "魔压（0魔免费）" if free else "魔压"
        self.description = ("当前魔恰为0时，每个敌方武将回合可免费连锁一次通用震开。" if free
                            else "沿用通用震开：1魔，被敌方动作影响时连锁。")
        if free:
            self.mana_cost = 0
            self.max_uses_per_turn = 1

    def can_react_to(self, battle: Battle, actor: Unit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if ok and self.free and actor.current_mana != 0:
            return False, "只有当前魔为0时才可免费使用魔压。"
        return ok, reason


class HellOrbSkill(RemoteDragonBreathSkill):
    def __init__(self, *, free: bool = False) -> None:
        super().__init__()
        self.free = free
        self.code = "hell_orb_free" if free else "hell_orb"
        self.name = "魔动球（0魔免费）" if free else "魔动球"
        self.description = ("当前魔恰为0时，每本人轮免费一次远程龙息。" if free
                            else "沿用远程龙息：2魔，每本人轮最多2次。")
        if free:
            self.mana_cost = 0
            self.max_uses_per_turn = 1

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and self.free and actor.current_mana != 0:
            return False, "只有当前魔为0时才可免费使用魔动球。"
        return ok, reason


class HellWallSkill(LightWallSkill):
    def __init__(self, *, free: bool = False) -> None:
        super().__init__()
        self.free = free
        self.code = "hell_wall_free" if free else "hell_wall"
        self.name = "魔墙（0魔免费）" if free else "魔墙"
        self.description = ("当前魔恰为0时，每敌方英雄回合免费连锁2次光墙。" if free
                            else "沿用光墙：每保护一个受影响己方实际受体费1魔。")
        if free:
            self.mana_cost = 0
            self.max_uses_per_turn = 2

    def can_react_to(self, battle: Battle, actor: Unit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if ok and self.free and actor.current_mana != 0:
            return False, "只有当前魔为0时才可免费使用魔墙。"
        return ok, reason


class HellShuffleSkill(Skill):
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("hell_shuffle", "地狱洗牌",
                         "大招：每场一次，选择场上己方名字含地狱的单位技能破坏；魔免不能挡，其他普通防御照常。",
                         max_uses_per_battle=1, target_mode="ally")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit.alive and not unit.banished and unit.position is not None
                and unit.player_id == actor.player_id and "地狱" in unit.name]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id") and str(payload["target_unit_id"]) not in {u.unit_id for u in self.targets(battle, actor)}:
            return False, "只能选择己方名字含地狱的在场单位。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target.unit_id not in {unit.unit_id for unit in self.targets(battle, actor)}:
            raise ActionError("只能选择己方名字含地狱的在场单位。")
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=True, ignore_magic_immunity=True,
                                        ignore_targeting_restrictions=True,
                                        record_effect_on_validation=False)
        recipient = result.target
        if result.cancelled or recipient.skill_non_damage_effects_blocked():
            battle.log_public_event(result.reason or f"{recipient.name} 未受到地狱洗牌影响。",
                                    source=actor, target=recipient)
            return
        recipient.current_hp = 0
        recipient.alive = False
        battle.log_public_event(f"{actor.name} 用地狱洗牌技能破坏了{recipient.name}。",
                                source=actor, target=recipient)
        for component in list(recipient.iter_components()):
            callback = getattr(component, "on_hell_forced_skill_death", None)
            if callable(callback):
                callback(battle)
        battle.cleanup_dead_units()

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}


class HellHorseRideableTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可乘骑", "乘骑时由地狱马承受伤害和技能效果，骑士仍可替马连锁。")


class HellHorseSummon(AbstractHero):
    hero_code = "hell_horse"
    hero_name = "地狱马"
    role = "坐骑"
    attribute = "暗"
    race = "兽"
    level = 1
    base_stats = Stats(attack=1, defense=4, speed=4, attack_range=1, mana=0)
    footprint_width = 1
    footprint_height = 2
    raw_skill_text = ""
    raw_trait_text = "可乘骑"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [HellHorseRideableTrait()]


def resolve_hell_death_strike(battle: Battle, actor: Unit, queued_action: Any) -> None:
    cells = battle.payload_positions(queued_action.payload, "cells")
    for recipient in battle.effect_units_at_cells(cells):
        battle.resolve_damage(DamageContext(
            source=actor, target=recipient, attack_power=float(queued_action.payload["attack_power"]),
            is_skill=False, from_field_effect=True, action_name=queued_action.display_name,
            tags={"hell_death_strike", "death_trait"},
        ))


class HellRebirthPierceStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("地狱再临·普攻破魔", "再临后3本人轮普攻破魔。", duration=3)

    def _matches(self, ctx: TargetContext | DamageContext) -> bool:
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        return (self.owner is not None and source is not None and source.unit_id == self.owner.unit_id
                and not ctx.is_skill and "attack" in ctx.tags)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self._matches(ctx):
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._matches(ctx):
            ctx.ignore_shield = True


class HellRebirthGuardStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("地狱再临·守卫", "再临后3本人轮自然回血、每轮普攻2次。", duration=3)

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is not None and self.owner.alive:
            battle.heal(HealContext(source=self.owner, target=self.owner, amount=0.25,
                                    action_name="地狱再临·自然回血"))

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + 1


class HellSnowManaStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("地狱再临·自然回魔", "再临后接下来三个本人轮开始各自然回魔1。",
                         duration=3, tick_scope="owner_turn_start")

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is not None and self.owner.alive:
            gained = self.owner.gain_mana(1)
            if gained:
                battle.log_public_event(f"{self.owner.name} 地狱再临自然回魔 +{gained}。",
                                        source=self.owner, target=self.owner)
        self.decrement(battle)


