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
    unit.clamp_mana()


def positions_to_dict(cells: list[Position]) -> list[dict[str, int]]:
    return [cell.to_dict() for cell in cells]


def payload_position(payload: dict[str, Any], x_key: str = "x", y_key: str = "y") -> Position:
    if x_key not in payload or y_key not in payload:
        raise ActionError("缺少目标坐标。")
    return Position(int(payload[x_key]), int(payload[y_key]))


def payload_cells(payload: dict[str, Any], key: str = "cells") -> list[Position]:
    raw_cells = payload.get(key)
    if not isinstance(raw_cells, list) or not raw_cells:
        raise ActionError("缺少目标格子。")
    cells: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for raw_cell in raw_cells:
        if not isinstance(raw_cell, dict) or raw_cell.get("x") is None or raw_cell.get("y") is None:
            raise ActionError("目标格子格式不正确。")
        cell = Position(int(raw_cell["x"]), int(raw_cell["y"]))
        key_pair = (cell.x, cell.y)
        if key_pair in seen:
            raise ActionError("不能重复选择同一个格子。")
        seen.add(key_pair)
        cells.append(cell)
    return cells


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


def dedupe_positions(cells: list[Position]) -> list[Position]:
    unique: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        key = (cell.x, cell.y)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cell)
    return unique


def pattern_signature(cells: list[Position]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted((cell.x, cell.y) for cell in dedupe_positions(cells)))


def line_patterns(
    battle: Battle,
    origin: Optional[Position],
    directions: list[tuple[int, int]],
    length: int,
    *,
    min_length: int = 1,
) -> list[list[Position]]:
    if origin is None:
        return []
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for direction in directions:
        line = battle.line_positions(origin, direction, length)
        if len(line) < min_length:
            continue
        key = pattern_signature(line)
        if key in seen:
            continue
        seen.add(key)
        patterns.append(line)
    return patterns


def localized_line_patterns(
    battle: Battle,
    origin: Optional[Position],
    directions: list[tuple[int, int]],
    length: int,
    *,
    max_distance: Optional[int] = None,
) -> list[list[Position]]:
    if origin is None or length <= 0:
        return []
    radius = length if max_distance is None else max_distance
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for direction in directions:
        for start_x in range(-length + 1, battle.width):
            for start_y in range(-length + 1, battle.height):
                cells = [
                    Position(start_x + direction[0] * step, start_y + direction[1] * step)
                    for step in range(length)
                ]
                in_bounds = [cell for cell in cells if battle.in_bounds(cell)]
                if not in_bounds:
                    continue
                if any(cell == origin for cell in in_bounds):
                    continue
                if any(origin.distance_to(cell) > radius for cell in in_bounds):
                    continue
                key = pattern_signature(in_bounds)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(dedupe_positions(in_bounds))
    patterns.sort(key=pattern_signature)
    return patterns


def match_payload_pattern(payload: dict[str, Any], patterns: list[list[Position]]) -> list[Position]:
    chosen = payload_cells(payload)
    chosen_key = pattern_signature(chosen)
    for pattern in patterns:
        if pattern_signature(pattern) == chosen_key:
            return dedupe_positions(pattern)
    raise ActionError("所选格子不符合该技能的形状要求。")


def pattern_selection_preview(patterns: list[list[Position]]) -> dict[str, Any]:
    unique_cells: list[Position] = []
    seen_cells: set[tuple[int, int]] = set()
    preview_patterns: list[list[dict[str, int]]] = []
    for pattern in patterns:
        deduped = dedupe_positions(pattern)
        if not deduped:
            continue
        preview_patterns.append(positions_to_dict(deduped))
        for cell in deduped:
            key = (cell.x, cell.y)
            if key in seen_cells:
                continue
            seen_cells.add(key)
            unique_cells.append(cell)
    return {
        "cells": positions_to_dict(unique_cells),
        "selection": {
            "mode": "pattern_cells",
            "patterns": preview_patterns,
        },
    }


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


class SourcedDefenseStatus(StatusEffect):
    def __init__(
        self,
        name: str,
        *,
        source_unit_id: str,
        defense_delta: float,
        description: str = "",
        duration: Optional[int] = None,
        tick_scope: str = "owner_turn_end",
    ) -> None:
        super().__init__(name, description, duration=duration, tick_scope=tick_scope)
        self.source_unit_id = source_unit_id
        self.defense_delta = defense_delta

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "defense":
            return value + self.defense_delta
        return value


class MagicImmunityStatus(StatusEffect):
    def __init__(self, *, source_name: str = "洗礼", duration: int = 2) -> None:
        super().__init__(
            f"魔免（来自{source_name}）" if source_name else "魔免",
            "敌方主动技能造成的伤害与效果无效，但仍会受到场地效果影响。",
            duration=duration,
            tick_scope="any_turn_end",
        )

    def bind(self, owner: HeroUnit) -> "MagicImmunityStatus":
        super().bind(owner)
        owner.magic_immunity = True
        return self

    def on_removed(self, battle: Battle) -> None:
        if self.owner is None:
            return
        still_active = any(
            isinstance(status, MagicImmunityStatus) and status is not self
            for status in self.owner.statuses
        )
        self.owner.magic_immunity = still_active


class NextNormalMoveBoostStatus(StatusEffect):
    def __init__(self, amount: int, *, duration: int = 1) -> None:
        super().__init__(
            "神速",
            f"本回合内下一次普通移动格数 +{amount}。",
            duration=duration,
            tick_scope="owner_turn_end",
        )
        self.amount = amount

    def modify_normal_move_distance(self, value: int) -> int:
        return value + self.amount

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        if self.owner is None:
            return
        if ctx.unit.unit_id != self.owner.unit_id or ctx.via_skill:
            return
        self.owner.remove_status(self, battle)


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
        duration: Optional[int] = None,
        tick_scope: str = "owner_turn_end",
        consume_on_attack_attempt: bool = False,
    ) -> None:
        super().__init__(name, description, duration=duration, tick_scope=tick_scope)
        self.bonus_attack = bonus_attack
        self.ignore_shield = ignore_shield
        self.consume_on_attack_attempt = consume_on_attack_attempt

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


class HeadshotStanceStatus(StatusEffect):
    def __init__(self, *, duration: int = 1) -> None:
        super().__init__(
            "爆头姿态",
            "本回合内攻击只能选择直线或对角线上的目标。",
            duration=duration,
            tick_scope="owner_turn_end",
        )

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        if self.owner is None or actor.unit_id != self.owner.unit_id:
            return True, ""
        if actor.position is None or target.position is None:
            return True, ""
        try:
            straight_direction(actor.position, target.position)
        except ActionError:
            return False, "爆头状态下只能攻击直线上的目标。"
        return True, ""


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


class DelayedDarknessStatus(StatusEffect):
    def __init__(self, *, duration: int = 2) -> None:
        super().__init__(
            "遁入黑暗",
            description="持续期间无法回复；若以普攻现身，则那次普攻伤害 +1 且破魔。",
            duration=duration,
            tick_scope="any_turn_end",
        )

    def bind(self, owner: HeroUnit) -> "DelayedDarknessStatus":
        super().bind(owner)
        owner.cannot_heal = True
        return self

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.cannot_heal = False


class InvincibleUntilActionStatus(StatusEffect):
    def __init__(
        self,
        *,
        duration: Optional[int] = None,
        tick_scope: str = "owner_turn_end",
        bonus_attack_on_attack_break: float = 0.0,
        ignore_shield_on_attack_break: bool = False,
        attack_break_buff_name: str = "现身突袭",
        attack_break_buff_description: str = "",
    ) -> None:
        super().__init__(
            "隐身",
            "仅己方可见；敌方不能直接选中，但点地施放的技能仍可能命中，直到自己下次普攻或使用技能前。",
            duration=duration,
            tick_scope=tick_scope,
        )
        self.bonus_attack_on_attack_break = bonus_attack_on_attack_break
        self.ignore_shield_on_attack_break = ignore_shield_on_attack_break
        self.attack_break_buff_name = attack_break_buff_name
        self.attack_break_buff_description = attack_break_buff_description

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if self.owner is None:
            return
        if action_type not in {"attack", "skill"}:
            return
        if action_type == "attack" and (
            self.bonus_attack_on_attack_break != 0 or self.ignore_shield_on_attack_break
        ):
            self.owner.add_status(
                NextAttackBuffStatus(
                    self.attack_break_buff_name,
                    bonus_attack=self.bonus_attack_on_attack_break,
                    ignore_shield=self.ignore_shield_on_attack_break,
                    description=self.attack_break_buff_description,
                )
            )
            battle.log(f"{self.owner.name} 因现身发动突袭，这次普攻伤害 +1 且破魔。")
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
        self.owner.gain_mana(1)
        battle.heal(HealContext(source=self.owner, target=self.owner, amount=0.25, action_name="原地回复"))


class PrecisionTrainingTrait(Trait):
    PROC_TAG = "precision_training_slow_proc"
    CHECKED_TAG = "precision_training_slow_checked"

    def __init__(self) -> None:
        super().__init__("压制射击", "普攻有 1/3 概率附带破魔减速，使目标下回合速度 -2，最低到 1。")

    def _is_owners_attack(self, ctx: DamageContext) -> bool:
        return (
            self.owner is not None
            and ctx.source is not None
            and ctx.source.unit_id == self.owner.unit_id
            and "attack" in ctx.tags
            and ctx.target.unit_id != self.owner.unit_id
        )

    def _roll_proc(self, tags: set[str]) -> None:
        if self.CHECKED_TAG in tags:
            return
        tags.add(self.CHECKED_TAG)
        if random.random() < 1 / 3:
            tags.add(self.PROC_TAG)

    def _apply_slow(self, battle: Battle, target: HeroUnit) -> None:
        target.add_status(SlowStatus(2, duration=1))
        battle.log(f"{target.name} 被精兵压制，下一回合速度下降。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if not self._is_owners_attack(ctx):
            return
        self._roll_proc(ctx.tags)

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if not self._is_owners_attack(ctx):
            return
        if self.PROC_TAG not in ctx.tags:
            return
        self._apply_slow(battle, ctx.target)  # type: ignore[arg-type]

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        if not self._is_owners_attack(ctx):
            return
        if self.PROC_TAG not in ctx.tags or not ctx.shield_consumed:
            return
        self._apply_slow(battle, ctx.target)  # type: ignore[arg-type]


class MagicImmuneWhenAttackOneTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻一魔免", "攻击为 1 时不受技能影响。")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is None:
            return
        if ctx.target.unit_id != self.owner.unit_id or not ctx.is_skill or not ctx.is_hostile or ctx.from_field_effect:
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
        if ctx.is_skill and ctx.is_hostile and not ctx.from_field_effect and not ctx.actor.moved_this_turn:
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


class ShensuSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__(
            "shensu",
            "神速",
            "普通技能：费 1 魔，本回合内下一次普通移动的格数 +3。",
            mana_cost=1,
            max_uses_per_turn=1,
        )

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        existing = actor.get_status("神速")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(NextNormalMoveBoostStatus(3))
        battle.log(f"{actor.name} 获得了神速，本回合下一次普通移动格数 +3。")


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
        exact_distance: int | None = None,
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
        self.exact_distance = exact_distance

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
            exact_distance=self.exact_distance,
            tags={self.code},
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = battle.reachable_positions(
            actor,
            max_distance=self.max_distance,
            exact_distance=self.exact_distance,
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
        source = battle.units.get(queued_action.actor_id)
        for unit_id in queued_action.target_unit_ids:
            if unit_id in seen:
                continue
            seen.add(unit_id)
            unit = battle.units.get(unit_id)
            if unit is None or unit.player_id != actor.player_id or unit.position is None or unit.banished or not unit.alive:
                continue
            ok, _ = battle.unit_can_be_selected(
                unit,
                actor=source,
                ignore_stealth=battle.action_ignores_stealth(queued_action),
            )
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
        target.spend_mana(lost)
        actor.gain_mana(lost)
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
            "主动：1.5 魔，每回合最多 2 次，逐格选择一片直线区域；通常需要点满 2 格，贴边时按实际存在的格子结算。",
            mana_cost=1.5,
            max_uses_per_turn=2,
            target_mode="cell",
        )

    def directions(self) -> list[tuple[int, int]]:
        return [
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        ]

    def selectable_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return dedupe_positions([cell for pattern in self.patterns(battle, actor) for cell in pattern])

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return localized_line_patterns(battle, actor.position, self.directions(), 2, max_distance=2)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for unit in battle.units_at_cells(self.chosen_cells(battle, actor, payload)):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="穿刺",
                    tags={"skill", "attack", "pierce"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [unit for unit in battle.units_at_cells(self.chosen_cells(battle, actor, payload))]  # type: ignore[list-item]


class KnockbackSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "knockback",
            "震开",
            "被动：连锁速度 2，被敌方攻击或主动技能影响时，先获得 1 层护盾，再将周围单位尽量向外推开 1 格。",
            timing="passive",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("震开只能通过连锁使用。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if actor.unit_id not in queued_action.target_unit_ids:
            return False, "当前动作没有影响到自己。"
        return True, ""

    def outward_destination(self, battle: Battle, actor: HeroUnit, unit: HeroUnit) -> Position | None:
        if actor.position is None or unit.position is None:
            return None
        dx = unit.position.x - actor.position.x
        dy = unit.position.y - actor.position.y
        step_x = 0 if dx == 0 else dx // abs(dx)
        step_y = 0 if dy == 0 else dy // abs(dy)
        destination = unit.position.offset(step_x, step_y)
        if not battle.in_bounds(destination):
            return None
        if battle.is_occupied(destination, ignore=unit):
            return None
        if battle.is_forced_movement_blocked(destination):
            return None
        return destination

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        actor.shields += 1
        battle.log(f"{actor.name} 通过震开获得了 1 层护盾。")
        if actor.position is None:
            return
        neighbors = [
            unit
            for unit in battle.units_at_cells(battle.neighbors(actor.position))
            if unit.unit_id != actor.unit_id
        ]
        for unit in neighbors:
            destination = self.outward_destination(battle, actor, unit)  # type: ignore[arg-type]
            if destination is None:
                continue
            battle.move_unit(
                unit,
                destination,
                via_skill=True,
                triggered_by_reaction=True,
                max_distance=1,
                tags={"knockback"},
                forced=True,
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": positions_to_dict(battle.neighbors(actor.position)) if actor.position else [],
            "requires_target": False,
        }

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        return self.preview(battle, actor)


class MachineGunSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "machine_gun",
            "机枪",
            "普通技能：每回合最多 1 次，逐格选择一片直线区域；通常需要点满 3 格，贴边时按实际存在的格子结算，对其中敌方单位分别结算伤害。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def selectable_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return dedupe_positions([cell for pattern in self.patterns(battle, actor) for cell in pattern])

    def directions(self) -> list[tuple[int, int]]:
        return [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return localized_line_patterns(battle, actor.position, self.directions(), 3, max_distance=3)

    def chosen_line(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for unit in battle.units_at_cells(self.chosen_line(battle, actor, payload)):
            if unit.player_id == actor.player_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="机枪",
                    tags={"skill", "attack"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.selectable_cells(battle, actor)
        cell_keys = {(cell.x, cell.y) for cell in cells}
        targets = [
            unit.unit_id
            for unit in battle.enemy_units(actor.player_id)
            if unit.position is not None and (unit.position.x, unit.position.y) in cell_keys
        ]
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_line(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [
            unit
            for unit in battle.units_at_cells(self.chosen_line(battle, actor, payload))
            if unit.player_id != actor.player_id
        ]  # type: ignore[list-item]


class HeadshotSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__(
            "headshot",
            "爆头",
            "本回合内失去周围方形普攻特性；本回合内下一次攻击伤害 +2 且破魔。",
            max_uses_per_turn=1,
        )

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        actor.add_status(HeadshotStanceStatus(duration=1))
        actor.add_status(
            NextAttackBuffStatus(
                "爆头强化",
                bonus_attack=2,
                ignore_shield=True,
                description="本回合内下一次攻击伤害 +2 且破魔。",
                duration=1,
                consume_on_attack_attempt=True,
            )
        )
        battle.log(f"{actor.name} 进入爆头准备状态。")


class DefendTwiceSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "defend_twice",
            "守*2",
            "普通技能：费 1 魔，每回合最多 1 次，可对己方单位或自己使用；目标守 +1，持续 1 轮，来自同一武将的不叠加。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="ally",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        existing = next(
            (
                status
                for status in target.statuses
                if isinstance(status, SourcedDefenseStatus)
                and status.name == "守*2"
                and status.source_unit_id == actor.unit_id
            ),
            None,
        )
        if existing is not None:
            raise ActionError("来自同一武将的守*2效果不能叠加。")
        target.add_status(
            SourcedDefenseStatus(
                "守*2",
                source_unit_id=actor.unit_id,
                defense_delta=1,
                duration=2,
                tick_scope="any_turn_end",
                description="守 +1。",
            )
        )
        battle.log(f"{target.name} 获得了来自 {actor.name} 的守*2加成。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.player_units(actor.player_id)
            if unit.position is not None
            and actor.position is not None
            and actor.position.distance_to(unit.position) <= actor.targeting_range()
        ]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class HealSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "heal",
            "回血",
            "普通技能：费 1 魔，每回合最多 1 次，可对包括自己在内的己方单位使用；目标回复 1/4 生命。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="ally",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        battle.heal(HealContext(source=actor, target=target, amount=0.25, action_name="回血"))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.player_units(actor.player_id) if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class BaptismSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "baptism",
            "洗礼",
            "普通技能：费 2 魔，仅对人类使用，使其获得 1 轮魔免。魔免只抵消敌方主动技能的伤害与效果，不会抵消场地效果。",
            mana_cost=2,
            target_mode="ally",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        if target.race != "人类":
            raise ActionError("洗礼只能对人类使用。")
        target.add_status(MagicImmunityStatus(source_name="洗礼", duration=2))
        battle.log(f"{target.name} 获得了魔免（来自洗礼）。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.player_units(actor.player_id) if unit.race == "人类" and unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class ChantSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "chant",
            "吟唱",
            "普通技能：不费魔，每回合最多 1 次，选择一个范内目标，令其魔力点 +2。",
            max_uses_per_turn=1,
            target_mode="unit",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_distance(actor, target, actor.targeting_range())
        gained = target.gain_mana_points(2)
        battle.log(f"{target.name} 获得了 {gained} 点魔力点。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units() if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class GreatHolyLightField(BattleFieldEffect):
    def __init__(self, owner_unit_id: str, *, duration: int = 5) -> None:
        super().__init__("大圣光", "以吟游诗人为中心的持续圣光场。", duration=duration)
        self.owner_unit_id = owner_unit_id

    def get_owner_unit(self, battle: Battle) -> Optional[HeroUnit]:
        unit = battle.units.get(self.owner_unit_id)
        if unit is None:
            return None
        return unit  # type: ignore[return-value]

    def affected_cells(self, battle: Battle) -> list[Position]:
        owner = self.get_owner_unit(battle)
        if owner is None or owner.position is None:
            return []
        return [
            Position(x, y)
            for x in range(battle.width)
            for y in range(battle.height)
            if owner.position.distance_to(Position(x, y)) <= 5
        ]

    def board_marker(self, battle: Battle) -> str:
        return "圣"

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.get_owner_unit(battle)
        if owner is None or owner.position is None:
            return
        if ctx.unit.player_id == owner.player_id:
            return
        if ctx.via_skill:
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
                from_field_effect=True,
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
        super().__init__("great_holy_light", "大圣光", "大招：持续 2.5 轮，以自己为中心生成范围会变化的圣光场。", max_uses_per_battle=1)

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
            "被动：连锁速度 2，每回合最多 2 次，直线移动恰好 1 格。",
            mana_cost=0.5,
            max_uses_per_turn=2,
            timing="passive",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("回避只能通过连锁使用。")

    def evade_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        cells: list[Position] = []
        for dx, dy in (
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        ):
            destination = actor.position.offset(dx, dy)
            if not battle.in_bounds(destination):
                continue
            if battle.is_occupied(destination, ignore=actor):
                continue
            cells.append(destination)
        return sorted(cells, key=lambda cell: (cell.y, cell.x))

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
            straight_only=True,
            ignore_units=True,
            triggered_by_reaction=True,
            max_distance=1,
            tags={"evasion"},
        )
        battle.log(f"{actor.name} 使用回避离开了原定目标格。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        cells = self.evade_cells(battle, actor)
        return {
            "cells": [cell.to_dict() for cell in cells],
            "target_unit_ids": [],
            "secondary_cells": [actor.position.to_dict()] if actor.position else [],
            "requires_target": True,
        }


class BackstepShotSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "backstep_shot",
            "撤步射击",
            "被动：连锁速度 2，被普攻时先由玩家选择一个直线穿人的 2 格撤步落点；随后再选择一个仍在普攻范围内的敌方目标反击。",
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
        if not self.retreat_cells(battle, actor):
            return False, "没有可用于撤步射击的落点。"
        return True, ""

    def retreat_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        cells: list[Position] = []
        for dx, dy in (
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        ):
            destination = actor.position.offset(dx * 2, dy * 2)
            if not battle.in_bounds(destination):
                continue
            if battle.is_occupied(destination, ignore=actor):
                continue
            cells.append(destination)
        return sorted(cells, key=lambda cell: (cell.y, cell.x))

    def attack_targets_after_retreat(
        self,
        battle: Battle,
        actor: HeroUnit,
        destination: Position,
    ) -> list[HeroUnit]:
        if actor.position is None:
            return []
        actual_position = actor.position
        actor.position = destination
        try:
            targets = [
                unit
                for unit in battle.enemy_units(actor.player_id)
                if battle.attack_target_allowed(
                    actor,
                    unit,
                    ignore_stealth=battle.attack_ignores_stealth(actor, unit),
                )[0]
            ]
        finally:
            actor.position = actual_position
        return sorted(
            targets,
            key=lambda unit: (
                unit.position.y if unit.position is not None else 99,
                unit.position.x if unit.position is not None else 99,
                unit.unit_id,
            ),
        )

    def preview_targets_by_retreat_cell(
        self,
        battle: Battle,
        actor: HeroUnit,
    ) -> dict[str, list[HeroUnit]]:
        targets_by_cell: dict[str, list[HeroUnit]] = {}
        for destination in self.retreat_cells(battle, actor):
            targets_by_cell[f"{destination.x},{destination.y}"] = self.attack_targets_after_retreat(
                battle,
                actor,
                destination,
            )
        return targets_by_cell

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        if payload.get("x") is None or payload.get("y") is None:
            raise ActionError("撤步射击需要选择落点。")
        destination = Position(int(payload["x"]), int(payload["y"]))
        if destination not in self.retreat_cells(battle, actor):
            raise ActionError("该位置不能用于撤步射击。")
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            straight_only=True,
            exact_distance=2,
            ignore_units=True,
            triggered_by_reaction=True,
            max_distance=2,
            tags={"backstep"},
        )
        battle.log(f"{actor.name} 触发撤步射击。")
        valid_targets = self.attack_targets_after_retreat(battle, actor, destination)
        if not valid_targets:
            battle.log(f"{actor.name} 撤步后没有可反击的敌方目标。")
            return
        target = payload_target_unit(battle, payload)
        valid_target_ids = {unit.unit_id for unit in valid_targets}
        if target.unit_id not in valid_target_ids:
            raise ActionError("该目标不在撤步射击后的反击范围内。")
        battle.resolve_attack_damage(actor, target, action_name="撤步反击", tags={"counter"})

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        retreat_cells = self.retreat_cells(battle, actor)
        targets_by_cell = self.preview_targets_by_retreat_cell(battle, actor)
        target_unit_ids: list[str] = []
        seen_target_ids: set[str] = set()
        for targets in targets_by_cell.values():
            for unit in targets:
                if unit.unit_id in seen_target_ids:
                    continue
                seen_target_ids.add(unit.unit_id)
                target_unit_ids.append(unit.unit_id)
        return {
            "cells": positions_to_dict(retreat_cells),
            "target_unit_ids": target_unit_ids,
            "secondary_cells": [actor.position.to_dict()] if actor.position else [],
            "requires_target": True,
            "follow_up_target_ids_by_cell": {
                key: [unit.unit_id for unit in targets]
                for key, targets in targets_by_cell.items()
            },
            "follow_up_target_cells_by_cell": {
                key: positions_to_dict([unit.position for unit in targets if unit.position is not None])
                for key, targets in targets_by_cell.items()
            },
        }


class StealthSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__("stealth", "隐身", "普通技能：费 1.5 魔，仅己方可见，直到自己第一次普攻或使用技能后解除。", mana_cost=1.5)

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        existing = actor.get_status("隐身")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(InvincibleUntilActionStatus())
        glove = actor.skill_map().get("paralyzing_glove")
        if glove is not None:
            glove.cooldown_remaining = 0
            glove.uses_this_battle = max(glove.uses_this_battle - 1, 0)
        battle.log(f"{actor.name} 进入隐身状态。")
        battle.clear_all_stealth_if_all_heroes_stealthed()


class BlockSkill(SelfBuffSkill):
    def __init__(self) -> None:
        super().__init__("block", "格挡", "下一次伤害结算时守 +1。")

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        actor.add_status(
            TemporaryDefenseStatus(
                "格挡",
                defense_delta=1,
                description="下一次结算前守 +1。",
            )
        )
        battle.log(f"{actor.name} 进入格挡姿态。")
