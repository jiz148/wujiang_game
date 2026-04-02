from __future__ import annotations

import random
from typing import Any, Optional

from wujiang.engine.core import (
    ActionMiss,
    ActionError,
    Battle,
    BattleFieldEffect,
    DamageContext,
    HealContext,
    HeroUnit,
    MoveContext,
    Position,
    QueuedAction,
    Skill,
    StatusEffect,
    TargetContext,
    TemporaryDefenseStatus,
    Trait,
)


def clamp_mana(unit: HeroUnit) -> None:
    unit.current_mana = round(max(unit.current_mana, 0.0), 2)


def positions_to_dict(cells: list[Position]) -> list[dict[str, int]]:
    return [cell.to_dict() for cell in cells]


def payload_position(payload: dict[str, Any], x_key: str = "x", y_key: str = "y") -> Position:
    if x_key not in payload or y_key not in payload:
        raise ActionError("缺少目标坐标。")
    return Position(int(payload[x_key]), int(payload[y_key]))


def payload_target_unit(battle: Battle, payload: dict[str, Any], key: str = "target_unit_id") -> HeroUnit:
    if "resolved_target_unit_id" in payload:
        resolved = payload.get("resolved_target_unit_id")
        if not resolved:
            raise ActionMiss("原定目标格上已经没有单位，动作落空。")
        return battle.get_unit(resolved)  # type: ignore[return-value]
    if key not in payload:
        raise ActionError("缺少目标单位。")
    return battle.get_unit(payload[key])  # type: ignore[return-value]


def ensure_distance(actor: HeroUnit, target: HeroUnit | Position, max_distance: int) -> None:
    origin = actor.position
    other = target.position if isinstance(target, HeroUnit) else target
    if origin is None or other is None:
        raise ActionError("目标不在战场上。")
    if origin.distance_to(other) > max_distance:
        raise ActionError("目标超出技能范围。")


def straight_direction(start: Position, end: Position) -> tuple[int, int]:
    dx = end.x - start.x
    dy = end.y - start.y
    if dx == 0 and dy == 0:
        raise ActionError("需要选择一个不同的位置。")
    step_x = 0 if dx == 0 else dx // abs(dx)
    step_y = 0 if dy == 0 else dy // abs(dy)
    if dx != 0 and dy != 0 and abs(dx) != abs(dy):
        raise ActionError("目标必须位于直线或对角线上。")
    return step_x, step_y


def ensure_enemy(actor: HeroUnit, target: HeroUnit) -> None:
    if actor.player_id == target.player_id:
        raise ActionError("需要选择敌方单位。")


def ensure_ally(actor: HeroUnit, target: HeroUnit) -> None:
    if actor.player_id != target.player_id:
        raise ActionError("需要选择己方单位。")


class FlagStatus(StatusEffect):
    def __init__(
        self,
        name: str,
        flag_name: str,
        *,
        description: str = "",
        duration: Optional[int] = None,
        tick_scope: str = "owner_turn_end",
        value: bool = True,
    ) -> None:
        super().__init__(name, description, duration=duration, tick_scope=tick_scope)
        self.flag_name = flag_name
        self.value = value

    def bind(self, owner: HeroUnit) -> "FlagStatus":
        super().bind(owner)
        setattr(owner, self.flag_name, self.value)
        return self

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            setattr(self.owner, self.flag_name, False)


class StatModifierStatus(StatusEffect):
    def __init__(
        self,
        name: str,
        *,
        attack_delta: float = 0.0,
        defense_delta: float = 0.0,
        speed_delta: float = 0.0,
        range_delta: float = 0.0,
        description: str = "",
        duration: Optional[int] = None,
        tick_scope: str = "owner_turn_end",
    ) -> None:
        super().__init__(name, description, duration=duration, tick_scope=tick_scope)
        self.attack_delta = attack_delta
        self.defense_delta = defense_delta
        self.speed_delta = speed_delta
        self.range_delta = range_delta

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "attack":
            return value + self.attack_delta
        if stat_name == "defense":
            return value + self.defense_delta
        if stat_name == "speed":
            return value + self.speed_delta
        if stat_name == "attack_range":
            return value + self.range_delta
        return value


class SlowStatus(StatusEffect):
    def __init__(self, amount: int, *, duration: int = 1) -> None:
        super().__init__("迟缓", f"速度 -{amount}", duration=duration, tick_scope="owner_turn_end")
        self.amount = amount

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "speed":
            return max(1.0, value - self.amount)
        return value


class CrystalBallStatus(StatusEffect):
    def __init__(self, *, duration: int = 4) -> None:
        super().__init__("水晶球", "目标范围改为全图。", duration=duration, tick_scope="any_turn_end")

    def modify_targeting_range(self, value: int) -> int:
        return 99


class FirstHostileEffectNegationStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("爆头准备", "本回合第一次受到的敌方效果无效。", duration=1, tick_scope="owner_turn_end")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is None:
            return
        if ctx.target.unit_id != self.owner.unit_id or not ctx.is_hostile:
            return
        ctx.cancelled = True
        ctx.reason = f"{self.owner.name} 的爆头准备抵消了这次效果。"
        self.owner.remove_status(self, battle)


class NextAttackBuffStatus(StatusEffect):
    def __init__(
        self,
        name: str,
        *,
        bonus_attack: float = 0.0,
        ignore_shield: bool = False,
        description: str = "",
    ) -> None:
        super().__init__(name, description, duration=None)
        self.bonus_attack = bonus_attack
        self.ignore_shield = ignore_shield

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is None or ctx.source is None:
            return
        if ctx.source.unit_id != self.owner.unit_id or "attack" not in ctx.tags:
            return
        ctx.attack_power += self.bonus_attack
        if self.ignore_shield:
            ctx.ignore_shield = True
        self.owner.remove_status(self, battle)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is None:
            return
        if ctx.actor.unit_id != self.owner.unit_id or "attack" not in ctx.tags:
            return
        if self.ignore_shield:
            ctx.ignore_shield = True


class ExperimentCountdownStatus(StatusEffect):
    def __init__(self, *, duration: int = 3) -> None:
        super().__init__("实验倒计时", "倒计时结束后直接死亡。", duration=duration, tick_scope="any_turn_end")

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None and self.owner.alive:
            self.owner.take_damage_fraction(self.owner.current_hp)
            battle.log(f"{self.owner.name} 的实验强化失控，直接阵亡。")


class CurseStatus(StatusEffect):
    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        damage = round(self.owner.current_hp / 2, 4)
        if damage <= 0:
            return
        battle.log(f"{self.owner.name} \u7684\u8bc5\u5492\u53d1\u4f5c\u3002")
        battle.resolve_damage(
            DamageContext(
                source=None,
                target=self.owner,
                attack_power=0,
                is_skill=True,
                action_name="\u8bc5\u5492",
                raw_damage=damage,
                ignore_shield=True,
                ignore_magic_immunity=False,
                cannot_evade=True,
                tags={"curse"},
            )
        )

    def __init__(self) -> None:
        super().__init__("诅咒", "每轮结束时生命减半。", duration=None, tick_scope="any_turn_end")

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        return None
        if self.owner is None:
            return
        if ended_player_id != 2:
            return
        damage = round(self.owner.current_hp / 2, 4)
        if damage <= 0:
            return
        battle.log(f"{self.owner.name} 的诅咒发作。")
        battle.resolve_damage(
            DamageContext(
                source=None,
                target=self.owner,
                attack_power=0,
                is_skill=True,
                action_name="诅咒",
                raw_damage=damage,
                ignore_shield=True,
                ignore_magic_immunity=False,
                cannot_evade=True,
                tags={"curse"},
            )
        )


class DelayedDarknessStatus(FlagStatus):
    def __init__(self, *, duration: int = 2) -> None:
        super().__init__(
            "遁入黑暗",
            "cannot_be_targeted",
            description="无法被选中，并且无法回复。",
            duration=duration,
            tick_scope="any_turn_end",
        )

    def bind(self, owner: HeroUnit) -> "DelayedDarknessStatus":
        super().bind(owner)
        owner.cannot_heal = True
        return self

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.cannot_be_targeted = False
            self.owner.cannot_heal = False
            self.owner.add_status(
                NextAttackBuffStatus(
                    "黑暗突袭",
                    bonus_attack=1,
                    ignore_shield=True,
                    description="下一次攻击伤害 +1 且破魔。",
                )
            )


class InvincibleUntilActionStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("隐身", "不受敌方普攻或技能的伤害与效果，直到自己下次普攻或使用技能前。", duration=None)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is None:
            return
        if ctx.target.unit_id != self.owner.unit_id or not ctx.is_hostile:
            return
        ctx.cancelled = True
        ctx.reason = f"{self.owner.name} 处于隐身无敌状态，这次效果无效。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is None or ctx.target.unit_id != self.owner.unit_id:
            return
        if ctx.source is None or ctx.source.player_id == self.owner.player_id:
            return
        ctx.cancelled = True
        ctx.reason = f"{self.owner.name} 处于隐身无敌状态，这次伤害无效。"

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if self.owner is None:
            return
        if action_type not in {"attack", "skill"}:
            return
        self.owner.remove_status(self, battle)


class AttackCountTrait(Trait):
    def __init__(self, attacks_per_turn: int) -> None:
        super().__init__(f"攻击 {attacks_per_turn} 次", f"每回合可攻击 {attacks_per_turn} 次。")
        self.attacks_per_turn = attacks_per_turn

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return max(value, self.attacks_per_turn)


class FlyingTrait(Trait):
    def __init__(self) -> None:
        super().__init__("飞行", "移动时无视其他单位。")

    def bind(self, owner: HeroUnit) -> "FlyingTrait":
        super().bind(owner)
        owner.ignore_units_while_moving = True
        owner.has_flying = True
        return self


class BlockCounterTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可格挡反击", "拥有格挡与反击两个连锁速度 2 的行动。")

    def bind(self, owner: HeroUnit) -> "BlockCounterTrait":
        super().bind(owner)
        owner.has_block_counter = True
        return self


class StationaryRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("原地回复", "若本回合未移动，则回魔并回血。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is None or self.owner.moved_this_turn:
            return
        self.owner.current_mana = round(self.owner.current_mana + 1, 2)
        battle.heal(HealContext(source=self.owner, target=self.owner, amount=0.25, action_name="原地回复"))


class PrecisionTrainingTrait(Trait):
    def __init__(self) -> None:
        super().__init__("压制射击", "普攻命中后使目标速度降低。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is None or ctx.source is None:
            return
        if ctx.source.unit_id != self.owner.unit_id or "attack" not in ctx.tags or ctx.target.unit_id == self.owner.unit_id:
            return
        if random.random() <= 1 / 3:
            ctx.target.add_status(SlowStatus(2, duration=1))
            battle.log(f"{ctx.target.name} 被精兵压制，下一回合速度下降。")


class MagicImmuneWhenAttackOneTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻一魔免", "攻击为 1 时不受技能影响。")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is None:
            return
        if ctx.target.unit_id != self.owner.unit_id or not ctx.is_skill or not ctx.is_hostile:
            return
        if self.owner.stat("attack") <= 1 and not ctx.ignore_magic_immunity:
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 当前攻击为 1，进入魔免状态。"


class EllieWardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("技能后免伤", "本回合已使用过主动技能的单位无法再伤害此单位。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is None or ctx.source is None:
            return
        if ctx.target.unit_id != self.owner.unit_id:
            return
        if ctx.source.performed_active_skill:
            ctx.cancelled = True
            ctx.reason = f"{ctx.source.name} 已使用过主动技能，本回合无法伤害 {self.owner.name}。"


class NoMoveActiveSkillImmunityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("静止克制", "没有移动过的敌方单位，其主动技能对自己无效。")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is None:
            return
        if ctx.target.unit_id != self.owner.unit_id:
            return
        if ctx.is_skill and ctx.is_hostile and not ctx.actor.moved_this_turn:
            ctx.cancelled = True
            ctx.reason = f"{ctx.actor.name} 本回合尚未移动，主动技能对 {self.owner.name} 无效。"


class SelfBuffSkill(Skill):
    def __init__(self, code: str, name: str, description: str, **kwargs: Any) -> None:
        super().__init__(code, name, description, target_mode="self", **kwargs)

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        raise NotImplementedError

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.apply_to_self(battle, actor)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class DashMoveSkill(Skill):
    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        max_distance: int,
        mana_cost: float,
        max_uses_per_turn: int,
        straight_only: bool = False,
        ignore_units: bool = False,
        allow_anywhere: bool = False,
    ) -> None:
        super().__init__(
            code,
            name,
            description,
            mana_cost=mana_cost,
            max_uses_per_turn=max_uses_per_turn,
            target_mode="cell",
        )
        self.max_distance = max_distance
        self.straight_only = straight_only
        self.ignore_units = ignore_units
        self.allow_anywhere = allow_anywhere

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            straight_only=self.straight_only,
            ignore_units=self.ignore_units,
            allow_anywhere=self.allow_anywhere,
            max_distance=self.max_distance,
            tags={self.code},
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = battle.reachable_positions(
            actor,
            max_distance=self.max_distance,
            straight_only=self.straight_only,
            ignore_units=self.ignore_units,
            allow_anywhere=self.allow_anywhere,
        )
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class MagicWallSkill(Skill):
    def __init__(self) -> None:
        super().__init__("magic_wall", "魔墙", "为己方单位增加 1 点护盾。", mana_cost=0, target_mode="unit")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        target.shields += 1
        battle.log(f"{target.name} 获得了 1 层护盾。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.player_units(actor.player_id) if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class MagicWallSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "magic_wall",
            "\u9b54\u5899",
            "\u88ab\u52a8\uff1a\u8fde\u9501\u901f\u5ea6 2\uff0c\u5bf9\u4e00\u4e2a\u5df1\u65b9\u76ee\u6807\u52a0 1 \u5c42\u62a4\u76fe\u3002",
            mana_cost=1,
            target_mode="ally",
            timing="passive",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("\u9b54\u5899\u53ea\u80fd\u901a\u8fc7\u8fde\u9501\u4f7f\u7528\u3002")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "\u53ea\u80fd\u5bf9\u654c\u65b9\u52a8\u4f5c\u8fde\u9501\u3002"
        threatened = self.threatened_allies(battle, actor, queued_action)
        if not threatened:
            return False, "\u5f53\u524d\u52a8\u4f5c\u6ca1\u6709\u5f71\u54cd\u5230\u53ef\u4fdd\u62a4\u7684\u5df1\u65b9\u5355\u4f4d\u3002"
        threatened_ids = {unit.unit_id for unit in threatened}
        if not [unit for unit in self.ally_targets(battle, actor) if unit.unit_id in threatened_ids]:
            return False, "\u6ca1\u6709\u53ef\u4ee5\u65bd\u653e\u9b54\u5899\u7684\u5df1\u65b9\u76ee\u6807\u3002"
        return True, ""

    def ally_targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [
            unit
            for unit in battle.player_units(actor.player_id)
            if unit.position is not None
            and actor.position is not None
            and actor.position.distance_to(unit.position) <= actor.targeting_range()
        ]

    def threatened_allies(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> list[HeroUnit]:
        threatened: list[HeroUnit] = []
        seen: set[str] = set()
        for unit_id in queued_action.target_unit_ids:
            if unit_id in seen:
                continue
            seen.add(unit_id)
            unit = battle.units.get(unit_id)
            if unit is None or unit.player_id != actor.player_id or unit.position is None or unit.banished or not unit.alive:
                continue
            ok, _ = battle.unit_can_be_selected(unit, ignore_stealth=battle.action_ignores_stealth(queued_action))
            if not ok or battle.shield_auto_blocks_chain(unit, queued_action):
                continue
            threatened.append(unit)  # type: ignore[arg-type]
        return threatened

    def apply_shield(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        target.shields += 1
        battle.log(f"{target.name} \u83b7\u5f97\u4e86 1 \u5c42\u62a4\u76fe\u3002")

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        reaction_payload = dict(payload)
        if not reaction_payload.get("target_unit_id"):
            reaction_payload["target_unit_id"] = actor.unit_id
            reaction_payload["resolved_target_unit_id"] = actor.unit_id
        self.apply_shield(battle, actor, reaction_payload)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.ally_targets(battle, actor)
        return {
            "cells": positions_to_dict([unit.position for unit in targets if unit.position]),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        threatened_ids = {unit.unit_id for unit in self.threatened_allies(battle, actor, queued_action)}
        targets = [unit for unit in self.ally_targets(battle, actor) if unit.unit_id in threatened_ids]
        return {
            "cells": positions_to_dict([unit.position for unit in targets if unit.position]),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class DrainManaSkill(Skill):
    def __init__(self) -> None:
        super().__init__("drain_mana", "吸魔", "命中后令目标魔力 -1，自身魔力 +1。", target_mode="enemy", max_uses_per_turn=1)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        target_ctx = battle.validate_target(actor, target, action_name="吸魔", is_skill=True, is_hostile=True)
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        lost = min(target.current_mana, 1.0)
        target.current_mana = round(max(target.current_mana - lost, 0.0), 2)
        actor.current_mana = round(actor.current_mana + lost, 2)
        battle.log(f"{actor.name} 吸取了 {target.name} 的 {lost} 点魔力。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.enemy_units(actor.player_id) if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class HardenSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__(
            "harden",
            "变硬",
            "守 +1，持续 2轮。",
            mana_cost=1,
            max_uses_per_turn=1,
        )

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        if actor.has_status("变硬"):
            raise ActionError("变硬效果尚未结束。")
        actor.add_status(
            StatModifierStatus(
                "变硬",
                defense_delta=1,
                duration=4,
                tick_scope="any_turn_end",
                description="守 +1。",
            )
        )
        battle.log(f"{actor.name} 进入变硬状态。")


class PierceSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "pierce",
            "穿刺",
            "直线 2 格破盾攻击。",
            mana_cost=1.5,
            max_uses_per_turn=2,
            target_mode="enemy",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ensure_distance(actor, target, 2)
        if actor.position is None or target.position is None:
            raise ActionError("目标不在战场上。")
        straight_direction(actor.position, target.position)
        target_ctx = battle.validate_target(
            actor,
            target,
            action_name="穿刺",
            is_skill=True,
            is_hostile=True,
            ignore_shield=True,
        )
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name="穿刺",
                ignore_shield=True,
                tags={"skill", "attack", "pierce"},
            )
        )

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = []
        if actor.position is not None:
            for direction in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
                cells.extend(battle.line_positions(actor.position, direction, 2))
        units = [unit.unit_id for unit in battle.enemy_units(actor.player_id) if unit.position and actor.position and unit.position.distance_to(actor.position) <= 2]
        return {"cells": positions_to_dict(cells), "target_unit_ids": units, "secondary_cells": [], "requires_target": True}


class KnockbackSkill(Skill):
    def __init__(self) -> None:
        super().__init__("knockback", "震开", "攻击相邻单位并尽量将其击退 1 格。", target_mode="enemy")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ensure_distance(actor, target, 1)
        target_ctx = battle.validate_target(actor, target, action_name="震开", is_skill=True, is_hostile=True)
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name="震开",
                tags={"skill", "attack"},
            )
        )
        if actor.position is None or target.position is None:
            return
        dx = target.position.x - actor.position.x
        dy = target.position.y - actor.position.y
        destination = target.position.offset(0 if dx == 0 else dx // abs(dx), 0 if dy == 0 else dy // abs(dy))
        if battle.in_bounds(destination) and not battle.is_occupied(destination):
            battle.move_unit(target, destination, via_skill=True, triggered_by_reaction=True, max_distance=1, tags={"knockback"})

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = battle.neighbors(actor.position) if actor.position else []
        targets = [unit.unit_id for unit in battle.enemy_units(actor.player_id) if unit.position and actor.position and unit.position.distance_to(actor.position) <= 1]
        return {"cells": positions_to_dict(cells), "target_unit_ids": targets, "secondary_cells": [], "requires_target": True}


class MachineGunSkill(Skill):
    def __init__(self) -> None:
        super().__init__("machine_gun", "机枪", "直线 3 格攻击。", max_uses_per_turn=1, target_mode="enemy")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ensure_distance(actor, target, 3)
        if actor.position is None or target.position is None:
            raise ActionError("目标不在战场上。")
        straight_direction(actor.position, target.position)
        target_ctx = battle.validate_target(actor, target, action_name="机枪", is_skill=True, is_hostile=True)
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name="机枪",
                tags={"skill", "attack"},
            )
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = []
        if actor.position is not None:
            for direction in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
                cells.extend(battle.line_positions(actor.position, direction, 3))
        targets = [unit.unit_id for unit in battle.enemy_units(actor.player_id) if unit.position and actor.position and unit.position.distance_to(actor.position) <= 3]
        return {"cells": positions_to_dict(cells), "target_unit_ids": targets, "secondary_cells": [], "requires_target": True}


class HeadshotSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__("headshot", "爆头", "本回合第一道敌方效果无效，下一次普攻 +2 且破魔。", max_uses_per_turn=1)

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        actor.add_status(FirstHostileEffectNegationStatus())
        actor.add_status(
            NextAttackBuffStatus(
                "爆头强化",
                bonus_attack=2,
                ignore_shield=True,
                description="下一次普攻伤害 +2 且破魔。",
            )
        )
        battle.log(f"{actor.name} 进入爆头准备状态。")


class DefendTwiceSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__("defend_twice", "守*2", "守 +1，持续 1轮。", mana_cost=0)

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        if actor.has_status("守*2"):
            raise ActionError("守*2 已在持续中。")
        actor.add_status(
            StatModifierStatus(
                "守*2",
                defense_delta=1,
                duration=2,
                tick_scope="any_turn_end",
                description="守 +1。",
            )
        )
        battle.log(f"{actor.name} 的守备提升了。")


class HealSkill(Skill):
    def __init__(self) -> None:
        super().__init__("heal", "回血", "恢复目标 1/4 生命；暗属性或灵体/恶魔则改为受伤。", mana_cost=1, max_uses_per_turn=1, target_mode="unit")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_distance(actor, target, actor.targeting_range())
        is_harmful = target.attribute == "暗" or target.race in {"灵体", "恶魔"}
        if is_harmful:
            target_ctx = battle.validate_target(actor, target, action_name="回血", is_skill=True, is_hostile=True)
            if target_ctx.cancelled:
                battle.log(target_ctx.reason)
                return
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=0,
                    is_skill=True,
                    action_name="回血",
                    raw_damage=0.25,
                    tags={"skill"},
                )
            )
            return
        battle.heal(HealContext(source=actor, target=target, amount=0.25, action_name="回血"))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units() if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class BaptismSkill(Skill):
    def __init__(self) -> None:
        super().__init__("baptism", "洗礼", "仅对人类使用，使其获得 1轮魔免。", mana_cost=2, target_mode="ally")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        if target.race != "人类":
            raise ActionError("洗礼只能对人类使用。")
        target.add_status(
            FlagStatus(
                "洗礼",
                "magic_immunity",
                description="获得魔免。",
                duration=2,
                tick_scope="any_turn_end",
            )
        )
        battle.log(f"{target.name} 获得了洗礼。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.player_units(actor.player_id) if unit.race == "人类" and unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class ChantSkill(Skill):
    def __init__(self) -> None:
        super().__init__("chant", "吟唱", "令目标魔力 +2。", max_uses_per_turn=1, target_mode="unit")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_distance(actor, target, actor.targeting_range())
        target.current_mana = round(target.current_mana + 2, 2)
        battle.log(f"{target.name} 获得了 2 点魔力。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units() if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class GreatHolyLightField(BattleFieldEffect):
    def __init__(self, owner_unit_id: str, *, duration: int = 5) -> None:
        super().__init__("大圣光", "移动惩罚与友军守备加成。", duration=duration)
        self.owner_unit_id = owner_unit_id

    def get_owner_unit(self, battle: Battle) -> Optional[HeroUnit]:
        unit = battle.units.get(self.owner_unit_id)
        if unit is None:
            return None
        return unit  # type: ignore[return-value]

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.get_owner_unit(battle)
        if owner is None or owner.position is None:
            return
        if ctx.unit.player_id == owner.player_id:
            return
        if ctx.end.distance_to(owner.position) > 5:
            return
        battle.log(f"{ctx.unit.name} 触发了大圣光。")
        battle.resolve_damage(
            DamageContext(
                source=owner,
                target=ctx.unit,
                attack_power=0,
                is_skill=True,
                action_name="大圣光",
                raw_damage=4,
                cannot_evade=True,
                tags={"holy_light"},
            )
        )

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.get_owner_unit(battle)
        if owner is not None and owner.position is not None and ended_player_id == owner.player_id:
            for unit in battle.player_units(owner.player_id):
                if unit.position is not None and unit.position.distance_to(owner.position) <= 5:
                    unit.add_status(
                        StatModifierStatus(
                            "大圣光守备",
                            defense_delta=1,
                            duration=1,
                            tick_scope="any_turn_end",
                            description="直到下次己方回合开始前守 +1。",
                        )
                    )
                    battle.log(f"{unit.name} 获得了大圣光的守备加成。")
        super().on_any_turn_end(battle, ended_player_id)


class GreatHolyLightSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__("great_holy_light", "大圣光", "持续 2.5轮的范围圣光。", max_uses_per_battle=1)

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        battle.add_field_effect(GreatHolyLightField(actor.unit_id, duration=5))


class PassiveProtectionSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "protection",
            "保护",
            "被动：连锁速度 2，在敌方动作前为自身增加 2 层临时护盾。",
            mana_cost=1,
            max_uses_per_turn=1,
            timing="passive",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("保护只能通过连锁使用。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if actor.unit_id not in queued_action.target_unit_ids:
            return False, "当前动作没有影响到自己。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        actor.add_temporary_shields(2)
        battle.log(f"{actor.name} 通过保护获得了 2 层临时护盾。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        return {"cells": [actor.position.to_dict()] if actor.position else [], "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}


class PassiveEvasionSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "evasion",
            "回避",
            "被动：连锁速度 2，每回合最多 2 次，选择移动至多 2 格。",
            mana_cost=0.5,
            max_uses_per_turn=2,
            timing="passive",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("回避只能通过连锁使用。")

    def evade_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        return sorted(
            battle.reachable_positions(actor, max_distance=2),
            key=lambda cell: (actor.position.distance_to(cell), cell.y, cell.x),
        )

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id or actor.unit_id not in queued_action.target_unit_ids:
            return False, "当前不能回避。"
        if not self.evade_cells(battle, actor):
            return False, "没有可用于回避的落点。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        if payload.get("x") is None or payload.get("y") is None:
            raise ActionError("回避需要选择落点。")
        destination = Position(int(payload["x"]), int(payload["y"]))
        if destination not in self.evade_cells(battle, actor):
            raise ActionError("该位置不能用于回避。")
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            triggered_by_reaction=True,
            max_distance=2,
            tags={"evasion"},
        )
        battle.log(f"{actor.name} 使用回避离开了原定目标格。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        cells = self.evade_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [actor.unit_id], "secondary_cells": [actor.position.to_dict()] if actor.position else [], "requires_target": True}


class BackstepShotSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "backstep_shot",
            "撤步射击",
            "被动：连锁速度 2，被普攻时后撤 2 格并反击。",
            mana_cost=0.5,
            max_uses_per_turn=2,
            timing="passive",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("撤步射击只能通过连锁使用。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.action_type != "attack":
            return False, "撤步射击只能响应普攻。"
        if actor.unit_id not in queued_action.target_unit_ids:
            return False, "当前动作没有攻击到自己。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        source = battle.units.get(queued_action.actor_id)
        if actor.position is None or source is None or source.position is None:
            return
        step_x = actor.position.x - source.position.x
        step_y = actor.position.y - source.position.y
        step_x = 0 if step_x == 0 else step_x // abs(step_x)
        step_y = 0 if step_y == 0 else step_y // abs(step_y)
        destination = actor.position
        for _ in range(2):
            candidate = destination.offset(step_x, step_y)
            if not battle.in_bounds(candidate) or battle.is_occupied(candidate):
                candidate = destination
                break
            destination = candidate
        if destination == actor.position:
            actor.dodge_charges += 1
            battle.log(f"{actor.name} 触发撤步射击失败，改为获得 1 次闪避。")
            return
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            straight_only=True,
            triggered_by_reaction=True,
            max_distance=2,
            tags={"backstep"},
        )
        battle.log(f"{actor.name} 触发撤步射击。")
        if actor.position.distance_to(source.position) <= actor.targeting_range():
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=source,
                    attack_power=actor.stat("attack"),
                    is_skill=False,
                    action_name="撤步反击",
                    tags={"attack", "counter"},
                )
            )

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        source = battle.units.get(queued_action.actor_id)
        cells: list[dict[str, int]] = []
        if source is not None and source.position is not None and actor.position is not None:
            step_x = actor.position.x - source.position.x
            step_y = actor.position.y - source.position.y
            step_x = 0 if step_x == 0 else step_x // abs(step_x)
            step_y = 0 if step_y == 0 else step_y // abs(step_y)
            destination = actor.position
            for _ in range(2):
                candidate = destination.offset(step_x, step_y)
                if not battle.in_bounds(candidate) or battle.is_occupied(candidate):
                    break
                cells.append(candidate.to_dict())
                destination = candidate
        return {"cells": cells, "target_unit_ids": [queued_action.actor_id], "secondary_cells": [], "requires_target": False}


class StealthSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__("stealth", "隐身", "费 1.5 魔，进入无敌状态，直到自己下次普攻或使用技能前。", mana_cost=1.5)

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        existing = actor.get_status("隐身")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(InvincibleUntilActionStatus())
        glove = actor.skill_map().get("paralyzing_glove")
        if glove is not None:
            glove.cooldown_remaining = 0
            glove.uses_this_battle = max(glove.uses_this_battle - 1, 0)
        battle.log(f"{actor.name} 进入隐身无敌状态。")


class BlockSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__("block", "格挡", "下一次伤害计算时守 +1。")

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        actor.add_status(
            StatModifierStatus(
                "格挡",
                defense_delta=1,
                duration=1,
                tick_scope="owner_turn_end",
                description="下一次结算前守 +1。",
            )
        )
        battle.log(f"{actor.name} 进入格挡姿态。")
