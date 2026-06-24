from __future__ import annotations

import re
import random
from typing import Any, Callable

from wujiang.engine.core import (
    ActionError,
    ActionMiss,
    Battle,
    BattleFieldEffect,
    DamageContext,
    HealContext,
    HeroUnit,
    Position,
    Skill,
    Stats,
    StatusEffect,
    TargetContext,
    Trait,
)
from wujiang.heroes.base import AbstractHero
from wujiang.heroes.common import (
    AttackCountTrait,
    BackstepShotSkill,
    BaptismSkill,
    BlockCounterTrait,
    ChantSkill,
    DashMoveSkill,
    DefendTwiceSkill,
    DrainManaSkill,
    FlyingTrait,
    HardenSkill,
    HealSkill,
    KnockbackSkill,
    LightWallSkill,
    MachineGunSkill,
    MagicWallSkill,
    MagicImmunityStatus,
    PassiveEvasionSkill,
    PassiveProtectionSkill,
    PierceSkill,
    NextNormalMoveBoostStatus,
    ShensuSkill,
    StationaryRecoveryTrait,
    StatModifierStatus,
    StealthSkill,
    StoneWallSkill,
    ensure_ally,
    ensure_distance,
    ensure_enemy,
    is_mana_drain_immune,
    line_patterns,
    localized_line_patterns,
    match_payload_pattern,
    pattern_signature,
    pattern_selection_preview,
    payload_position,
    payload_target_unit,
    payload_target_units,
    positions_to_dict,
)
from wujiang.heroes.excel_roster_data import EXCEL_HERO_SPECS
from wujiang.heroes.next_five import (
    ArcAttackTrait,
    AttackLifeStealTrait,
    AttackManaDrainTrait,
    BasicAttackImmunityTrait,
    ChainPullSkill,
    DragonBreathSkill,
    HalfPierceAttackTrait,
    ALL_DIRECTIONS,
    IonShieldSkill,
    LaserSkill,
    MagicShieldSkill,
    MissileSkill,
    NaturalManaRecoveryTrait,
    PassThroughMovementTrait,
    RecoverManaSkill,
    RemoteDragonBreathSkill,
    SplitSkill,
    StandardCloneSummon,
    apply_piercing_status_effect,
    damage_followup_effect_applies,
    nearby_rectangle_patterns,
    position_key,
    remote_rectangle_patterns,
    square_around_cells,
)


HeroFactory = Callable[[int], object]


class NaturalHealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("自然回血", "每个自己的己方回合开始时回复 1/4 生命。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        battle.heal(HealContext(source=self.owner, target=self.owner, amount=0.25, action_name="自然回血"))


class NaturalRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("自然回复", "每个自己的己方回合开始时自然回血并自然回魔。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        gained = self.owner.gain_mana(1)
        if gained:
            battle.log(f"{self.owner.name} 自然回魔，获得 {gained} 点魔。")
        battle.heal(HealContext(source=self.owner, target=self.owner, amount=0.25, action_name="自然回复"))


class StationaryManaRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("原地回魔", "若本回合未移动，则回合结束时魔 +1。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is None or self.owner.moved_this_turn:
            return
        gained = self.owner.gain_mana(1)
        if gained:
            battle.log(f"{self.owner.name} 原地回魔，获得 {gained} 点魔。")


class StationaryHealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("原地回血", "若本回合未移动，则回合结束时回复 1/4 生命。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is None or self.owner.moved_this_turn:
            return
        battle.heal(HealContext(source=self.owner, target=self.owner, amount=0.25, action_name="原地回血"))


class PermanentMagicImmunityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("魔免", "免疫敌方技能伤害和技能附带效果。")

    def bind(self, owner: HeroUnit) -> "PermanentMagicImmunityTrait":
        super().bind(owner)
        owner.magic_immunity = True
        return self


class BasicAttackPierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("普攻破魔", "普攻伤害和普攻附带效果破魔。")

    def _is_owner_basic_attack(self, ctx: TargetContext | DamageContext) -> bool:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        return owner is not None and source is not None and source.unit_id == owner.unit_id and not ctx.is_skill and "attack" in ctx.tags

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self._is_owner_basic_attack(ctx):
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._is_owner_basic_attack(ctx):
            ctx.ignore_shield = True


class RemotePierceSkill(PierceSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "remote_pierce"
        self.name = "远程穿刺"
        self.description = "普通技能：费 1.5 魔，每回合最多 2 次，按范远程选择连续直线 2 格并结算范围伤害。"

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Any]]:
        if actor.position is None:
            return []
        patterns: list[list[Any]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        directions = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
        for x in range(battle.width):
            for y in range(battle.height):
                start = type(actor.position)(x, y)
                for dx, dy in directions:
                    cells = [start, start.offset(dx, dy)]
                    if any(not battle.in_bounds(cell) for cell in cells):
                        continue
                    if not any(battle.unit_distance_to_cell(actor, cell) <= actor.targeting_range() for cell in cells):
                        continue
                    key = tuple(sorted((cell.x, cell.y) for cell in cells))
                    if key in seen:
                        continue
                    seen.add(key)
                    patterns.append(cells)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Any]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview


class GuardianFinaleStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__(
            "终结",
            "每个己方回合结束时血 -1/4；攻 +3，速 +3；不受伤害以外的效果；普攻破魔并吸血；主动技能不费魔。",
            duration=None,
        )

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "speed"}:
            return value + 3
        return value

    def modify_skill_mana_cost(
        self,
        battle: Battle,
        actor: HeroUnit,
        skill: Skill,
        payload: dict[str, Any] | None,
        cost: float,
    ) -> float:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill.timing == "active":
            return 0.0
        return cost

    def _is_owner_basic_attack(self, ctx: TargetContext | DamageContext) -> bool:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        return owner is not None and source is not None and source.unit_id == owner.unit_id and not ctx.is_skill and "attack" in ctx.tags

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        owner = self.owner
        if self._is_owner_basic_attack(ctx):
            ctx.ignore_shield = True
            return
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if ctx.is_skill and "damage" not in ctx.tags and "attack" not in ctx.tags:
            ctx.cancelled = True
            ctx.reason = f"{owner.name} 的【终结】免疫伤害以外的效果。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._is_owner_basic_attack(ctx):
            ctx.ignore_shield = True

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.cancelled or (ctx.raw_damage or 0) <= 0:
            return
        battle.heal(HealContext(source=owner, target=owner, amount=0.25, action_name="终结吸血"))

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if ctx.action_name == "终结吸血":
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【终结】免疫治疗。"

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.alive:
            return
        owner.take_damage_fraction(0.25)
        battle.log_public_event(f"{owner.name} 因【终结】失去 0.25 点生命。", source=owner, target=owner)
        battle.cleanup_dead_units()


class GuardianFinaleSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "guardian_finale",
            "终结",
            "大招：一场战斗一次。使用后永久进入终结状态：每个己方回合结束时血 -1/4，攻 +3，速 +3，不受伤害以外的效果，普攻破魔并吸血，主动技能不费魔。",
            max_uses_per_battle=1,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("终结")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(GuardianFinaleStatus())
        battle.log(f"{actor.name} 发动【终结】，进入终结状态。")


class LargePierceSkill(PierceSkill):
    def __init__(self, *, line_length: int = 3, code: str = "large_pierce", name: str = "穿刺（大）") -> None:
        super().__init__()
        self.code = code
        self.name = name
        self.line_length = line_length
        self.description = (
            f"主动技能：费 1.5 魔，每回合最多 2 次，逐格选择一段连续直线 {line_length} 格；"
            "只要整段里至少有一格紧贴自己就算合法，贴边时按实际存在的格子结算。"
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Any]]:
        patterns: list[list[Any]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        actor_cells = {(cell.x, cell.y) for cell in battle.unit_cells(actor)}
        origins = battle.unit_cells(actor) or ([actor.position] if actor.position else [])
        for origin in origins:
            for pattern in localized_line_patterns(
                battle,
                origin,
                self.directions(),
                self.line_length,
                max_distance=self.line_length,
                touch_distance=1,
            ):
                if any((cell.x, cell.y) in actor_cells for cell in pattern):
                    continue
                key = pattern_signature(pattern)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(pattern)
        return patterns

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "attack", "pierce"},
                )
            )


class WindWallCounterStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("风壁计数点", "可让夏目的风壁远程保护此单位一次。", duration=None)


class WindWallBlockStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("风壁", "挡住下一次敌方攻击或技能造成的伤害和效果；即使该动作破魔也会被挡。", duration=None)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or not ctx.is_hostile:
            return
        if "attack" not in ctx.tags and not ctx.is_skill:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【风壁】挡住了【{ctx.action_name}】。"
        owner.remove_status(self, battle)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if ctx.source is not None and ctx.source.player_id == owner.player_id:
            return
        if "attack" not in ctx.tags and not ctx.is_skill:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【风壁】挡住了【{ctx.action_name}】。"
        owner.remove_status(self, battle)


class NatsumeWindWordSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "natsume_wind_word",
            "风之语",
            "普通技能：每回合最多 1 次；破魔；按当前攻击造成技能伤害，命中单位血 +1/4，然后尽量直线瞬移到夏目周围。",
            max_uses_per_turn=1,
            target_mode="unit",
        )

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def _pull_destination(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> Position | None:
        if actor.position is None or target.position is None:
            return None
        own_keys = {position_key(cell) for cell in battle.unit_cells(actor)}
        candidates = [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1) if position_key(cell) not in own_keys]
        if not candidates:
            return None
        dx = target.position.x - actor.position.x
        dy = target.position.y - actor.position.y
        step_x = 0 if dx == 0 else dx // abs(dx)
        step_y = 0 if dy == 0 else dy // abs(dy)
        preferred = actor.position.offset(step_x, step_y)
        ordered = sorted(
            candidates,
            key=lambda cell: (
                0 if cell == preferred else 1,
                target.position.distance_to(cell),
                cell.y,
                cell.x,
            ),
        )
        for cell in ordered:
            if battle.can_place_unit(target, cell, ignore=target, mover=target):
                return cell
        return None

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range(), action_name=self.name)
        is_hostile = target.player_id != actor.player_id
        target_ctx = battle.validate_target(
            actor,
            target,
            action_name=self.name,
            is_skill=True,
            is_hostile=is_hostile,
            ignore_shield=True,
            tags={"skill", "attack", "natsume_wind_word"},
        )
        if target_ctx.cancelled:
            if target_ctx.reason:
                battle.log_public_event(target_ctx.reason, source=actor, target=target)
            return
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name=self.name,
                ignore_shield=True,
                tags={"skill", "attack", "natsume_wind_word"},
            )
        )
        if not damage_followup_effect_applies(ctx):
            return
        if target.alive and target.position is not None:
            battle.heal(HealContext(source=actor, target=target, amount=0.25, action_name=self.name))
        destination = self._pull_destination(battle, actor, target)
        if destination is not None and target.alive and target.position is not None and destination != target.position:
            battle.move_unit(target, destination, via_skill=True, forced=True, max_distance=99, ignore_units=True, tags={"natsume_wind_word"})

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.position is not None
            and actor.position is not None
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class NatsumeWindWallSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "natsume_wind_wall",
            "风壁",
            "被动技能：连锁速度 2，费 1 魔；保护一个当前受影响的己方单位，使下一次伤害和效果无效；可额外保护带有风壁计数点的单位并摘除该计数点。",
            mana_cost=1,
            target_mode="ally",
            timing="passive",
        )

    def _threatened_allies(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> list[HeroUnit]:
        result: list[HeroUnit] = []
        seen: set[str] = set()
        for unit_id in queued_action.target_unit_ids:
            unit = battle.units.get(unit_id)
            if unit is None or unit.player_id != actor.player_id or unit.position is None or unit.banished or not unit.alive:
                continue
            if unit.unit_id in seen:
                continue
            seen.add(unit.unit_id)
            result.append(unit)  # type: ignore[arg-type]
        return result

    def _selectable_targets(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> list[HeroUnit]:
        threatened = self._threatened_allies(battle, actor, queued_action)
        return [
            unit
            for unit in threatened
            if unit.unit_id == actor.unit_id
            or battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
            or unit.has_status("风壁计数点")
        ]

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if not self._selectable_targets(battle, actor, queued_action):
            return False, "当前动作没有风壁可保护的己方目标。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        selectable = {unit.unit_id for unit in self._selectable_targets(battle, actor, queued_action)}
        if not selectable:
            return False, "当前动作没有风壁可保护的己方目标。"
        reaction_payload = dict(payload or {})
        if not reaction_payload.get("target_unit_id") and len(selectable) == 1:
            reaction_payload["target_unit_id"] = next(iter(selectable))
        try:
            targets = payload_target_units(battle, reaction_payload)
        except ActionError as exc:
            return False, str(exc)
        if len(targets) != 1:
            return False, "风壁一次只能保护一个目标。"
        if targets[0].unit_id not in selectable:
            return False, "这个目标当前不能被风壁保护。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        selectable = {unit.unit_id for unit in self._selectable_targets(battle, actor, queued_action)}
        reaction_payload = dict(payload)
        if not reaction_payload.get("target_unit_id") and len(selectable) == 1:
            reaction_payload["target_unit_id"] = next(iter(selectable))
        targets = payload_target_units(battle, reaction_payload)
        if len(targets) != 1 or targets[0].unit_id not in selectable:
            raise ActionError("这个目标当前不能被风壁保护。")
        target = targets[0]
        counter = target.get_status("风壁计数点")
        if counter is not None:
            target.remove_status(counter, battle)
        target.add_status(WindWallBlockStatus())
        battle.log(f"{actor.name} 为 {target.name} 张开【风壁】。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("风壁只能通过连锁使用。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        targets = self._selectable_targets(battle, actor, queued_action)
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
            "selection": {"mode": "multi_unit", "min_targets": 1, "max_targets": 1},
        }


class NatsumeDispelSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "natsume_dispel",
            "驱散",
            "普通技能：每回合最多 1 次；周围 11*11 内召唤物、分身破坏，隐身无效；每影响一个单位，夏目魔 +1。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=5)

    def affected_units(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.affected_cells(battle, actor), ignore=actor)  # type: ignore[return-value]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        affected_count = 0
        for unit in list(self.affected_units(battle, actor)):
            unit_affected = False
            if unit.is_summon or unit.is_clone:
                unit.alive = False
                unit_affected = True
            for status in list(unit.statuses):
                if status.name == "隐身" or getattr(status, "grants_stealth", False):
                    unit.remove_status(status, battle)
                    unit_affected = True
            if unit_affected:
                affected_count += 1
        if affected_count:
            battle.cleanup_dead_units()
            gained = actor.gain_mana(affected_count)
            battle.log(f"{actor.name} 的【驱散】影响了 {affected_count} 个单位，魔 +{gained}。")
        else:
            battle.log(f"{actor.name} 的【驱散】没有影响到单位。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor)
        targets = self.affected_units(battle, actor)
        return {
            "cells": [cell.to_dict() for cell in cells],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": False,
        }


class NatsumeAllyAttackManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("风壁赠予", "普攻己方单位时不造成伤害；目标魔 +1，并获得一个风壁计数点。")

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id:
            return True, ""
        if target.player_id == actor.player_id:
            return True, ""
        return True, ""

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id:
            return {}
        return {"allow_allied_attack_target": True}

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.target.player_id != owner.player_id:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的普攻转为给 {ctx.target.name} 加魔并施加风壁计数点。"

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.target.player_id != owner.player_id:
            return
        if ctx.reason and "风壁计数点" not in ctx.reason:
            return
        gained = ctx.target.gain_mana(1)
        existing = ctx.target.get_status("风壁计数点")
        if existing is not None:
            ctx.target.remove_status(existing, battle)
        ctx.target.add_status(WindWallCounterStatus())
        battle.log(f"{ctx.target.name} 因 {owner.name} 的普攻获得 {gained} 点魔和一个风壁计数点。")


class GreatUnicornSummon(AbstractHero):
    hero_code = "great_unicorn"
    hero_name = "大独角兽"
    role = "坐骑"
    attribute = "光"
    race = "兽"
    level = 1
    base_stats = Stats(attack=4, defense=6, speed=5, attack_range=1, mana=0)
    footprint_width = 1
    footprint_height = 2
    raw_skill_text = ""
    raw_trait_text = "可乘骑；普攻破魔"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [GreatUnicornRideableTrait(), BasicAttackPierceTrait(), AaronSummonDestroyedMarkerTrait()]


def alive_owned_great_unicorn(battle: Battle, rider: HeroUnit) -> GreatUnicornSummon | None:
    for unit in battle.all_units():
        if (
            isinstance(unit, GreatUnicornSummon)
            and unit.mount_owner_id == rider.unit_id
            and unit.alive
            and not unit.banished
            and unit.position is not None
        ):
            return unit
    return None


class GreatUnicornCooldownStatus(StatusEffect):
    def __init__(self, duration: int) -> None:
        super().__init__(
            "大独角兽召回冷却",
            "坐骑被破坏后，需要再等待 1 个自己的回合才能重新召唤。",
            duration=duration,
            tick_scope="owner_turn_end",
        )


class GreatUnicornRideableTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可乘骑", "只有被乘骑单位会承受伤害和技能效果；乘骑者仍可替其连锁。")

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.mount_owner_id:
            return
        rider = battle.units.get(owner.mount_owner_id)
        if not isinstance(rider, HeroUnit) or not rider.alive:
            return
        duration = 2 if battle.active_player == rider.player_id else 1
        existing = rider.get_status("大独角兽召回冷却")
        if existing is not None:
            rider.remove_status(existing, battle)
        rider.add_status(GreatUnicornCooldownStatus(duration))


class AaronMountedStartTrait(Trait):
    def __init__(self) -> None:
        super().__init__("骑士开场坐骑", "出场时已经召唤出自己的大独角兽，并且已经处于乘骑状态。")

    def on_enter_battle(self, battle: Battle) -> None:
        owner = self.owner
        if not isinstance(owner, HeroUnit) or owner.position is None:
            return
        if alive_owned_great_unicorn(battle, owner) is not None:
            return
        mount = GreatUnicornSummon(owner.player_id)
        mount.summoner_id = owner.unit_id
        mount.mount_owner_id = owner.unit_id
        mount.is_mount = True
        mount.can_act_on_entry_turn = True
        mount.turn_ready = True
        battle.add_unit(mount, owner.position)
        battle.set_mounted_state(owner, mount)


class GreatUnicornSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "summon_great_unicorn",
            "大独角兽",
            "普通技能：召唤并乘骑自己的大独角兽（攻4守6速5范1；1*2；普攻破魔）。",
            target_mode="self",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.position is None:
            return False, "当前不在战场上。"
        if alive_owned_great_unicorn(battle, actor) is not None:
            return False, "场上已经有自己的大独角兽。"
        if actor.has_status("大独角兽召回冷却"):
            return False, "大独角兽仍在召回冷却中。"
        probe = GreatUnicornSummon(actor.player_id)
        probe.mount_owner_id = actor.unit_id
        probe.is_mount = True
        if not battle.can_place_unit(probe, actor.position, ignore=probe, mover=probe):
            return False, "当前位置无法召唤大独角兽。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.position is None:
            raise ActionError("当前不在战场上。")
        mount = GreatUnicornSummon(actor.player_id)
        mount.summoner_id = actor.unit_id
        mount.mount_owner_id = actor.unit_id
        mount.is_mount = True
        battle.summon_unit(mount, actor.position, summoner=actor)
        battle.set_mounted_state(actor, mount)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class AaronDestroyedSummonBoostStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__(
            "独角兽遗辉",
            attack_delta=2,
            defense_delta=2,
            speed_delta=2,
            description="召唤物被破坏后的下个回合：攻守速 +2，血魔已补满，主动技能不费魔。",
            duration=1,
            tick_scope="owner_turn_end",
        )

    def modify_skill_mana_cost(self, battle: Battle, actor: HeroUnit, skill: Skill, payload: dict[str, Any] | None, cost: float) -> float:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill.timing == "active":
            return 0.0
        return cost


class AaronSummonDestroyedMarkerTrait(Trait):
    def __init__(self) -> None:
        super().__init__("亚伦召唤物", "此召唤物被破坏时，使召唤者下个回合获得独角兽遗辉。")

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.summoner_id:
            return
        summoner = battle.units.get(owner.summoner_id)
        if summoner is None or getattr(summoner, "hero_code", "") != "excel_r032" or not summoner.alive:
            return
        existing = summoner.get_status("独角兽遗辉")
        if existing is not None:
            summoner.remove_status(existing, battle)
        summoner.current_hp = summoner.max_health
        summoner.current_mana = summoner.max_mana()
        summoner.add_status(AaronDestroyedSummonBoostStatus())
        battle.log(f"{summoner.name} 因召唤物被破坏，获得下回合的【独角兽遗辉】。")


class MorningHolyLightSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "morning_holy_light",
            "晨曦圣光",
            "普通技能：费 1.5 魔，每回合最多 1 次；远程 5*10 或 10*5；破魔；无伤害；命中单位 2 轮不能使用被动技能，暗属性单位额外受到 5 点伤害。",
            mana_cost=1.5,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns = remote_rectangle_patterns(battle, actor, 5, 10) + remote_rectangle_patterns(battle, actor, 10, 5)
        seen: set[tuple[tuple[int, int], ...]] = set()
        result: list[list[Position]] = []
        for pattern in patterns:
            key = pattern_signature(pattern)
            if key in seen:
                continue
            seen.add(key)
            result.append(pattern)
        return result

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            is_hostile = target.player_id != actor.player_id
            target_ctx = battle.validate_target(
                actor,
                target,
                action_name=self.name,
                is_skill=True,
                is_hostile=is_hostile,
                ignore_shield=True,
                cannot_evade=True,
                tags={"skill", "morning_holy_light"},
            )
            if target_ctx.cancelled:
                if target_ctx.reason:
                    battle.log_public_event(target_ctx.reason, source=actor, target=target)
                continue
            existing = target.get_status("被动封锁")
            if existing is None:
                target.add_status(PassiveSkillLockStatus(duration=2))
                battle.log(f"{target.name} 被【晨曦圣光】封锁被动技能。")
            if target.attribute == "暗":
                battle.resolve_damage(
                    DamageContext(
                        source=actor,
                        target=target,
                        attack_power=0,
                        raw_damage=5,
                        is_skill=True,
                        action_name=self.name,
                        ignore_shield=True,
                        cannot_evade=True,
                        tags={"skill", "morning_holy_light"},
                    )
                )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class AaronLightAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("晨曦光环", "自身周围 7*7 单位在亚伦己方回合开始时血 +1/4；亚伦及其召唤物对暗属性单位伤害 +1。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None:
            return
        affected = {position_key(cell) for cell in square_around_cells(battle, battle.unit_cells(owner), radius=3)}
        for unit in battle.all_units():
            if unit.position is None or unit.banished:
                continue
            if any(position_key(cell) in affected for cell in battle.unit_cells(unit)):
                battle.heal(HealContext(source=owner, target=unit, amount=0.25, action_name="晨曦光环"))

    def _source_is_aaron_or_summon(self, battle: Battle, source: HeroUnit) -> bool:
        owner = self.owner
        if owner is None:
            return False
        return source.unit_id == owner.unit_id or source.summoner_id == owner.unit_id

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is None or ctx.target.attribute != "暗":
            return
        if not self._source_is_aaron_or_summon(battle, ctx.source):
            return
        if ctx.raw_damage is not None:
            ctx.raw_damage = round(ctx.raw_damage + 1, 4)
        else:
            ctx.attack_power += 1


class LaoWaveBulletSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "lao_wave_bullet",
            "波导弹",
            "普通技能：费 1 魔，每回合最多 1 次；远程 4*4 区域造成当前攻击伤害。可选择不花魔，此时伤害 -1。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        if payload and payload.get("free_cast"):
            return 0.0
        return super().mana_cost_for_payload(battle, actor, payload)

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 4, 4)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        cells = [payload_position(item) for item in payload.get("cells", [])]
        signature = pattern_signature(cells)
        legal = {pattern_signature(pattern): pattern for pattern in self.patterns(battle, actor)}
        if signature not in legal:
            raise ActionError("请选择合法的波导弹区域。")
        return legal[signature]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        attack_power = actor.stat("attack") - (1 if payload.get("free_cast") else 0)
        for target in battle.units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=attack_power,
                    is_skill=True,
                    action_name="波导弹",
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "attack", "lao_wave_bullet"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        cells = [cell for pattern in patterns for cell in pattern]
        cell_keys = {(cell.x, cell.y) for cell in cells}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview


class LaoMageHandSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "lao_mage_hand",
            "法师之手",
            "普通技能：每回合最多 1 次；对一个近战目标造成普攻伤害，破魔；命中后按选择方向尽量推动 3 格。",
            max_uses_per_turn=1,
            target_mode="enemy",
            direction_mode="required",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return 1

    def _direction(self, payload: dict[str, Any]) -> tuple[int, int]:
        direction = payload.get("direction")
        if isinstance(direction, dict):
            dx = int(direction.get("dx", 0) or 0)
            dy = int(direction.get("dy", 0) or 0)
        else:
            dx = int(payload.get("dx", 0) or 0)
            dy = int(payload.get("dy", 0) or 0)
        if dx == 0 and dy == 0:
            raise ActionError("请选择法师之手推动方向。")
        return (0 if dx == 0 else (1 if dx > 0 else -1), 0 if dy == 0 else (1 if dy > 0 else -1))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        if not battle.unit_target_in_range_and_line(actor, target, 1):
            raise ActionError("法师之手只能选择近战范围内的目标。")
        dx, dy = self._direction(payload)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                ignore_shield=True,
                action_name="法师之手",
                tags={"skill", "attack", "basic_attack_damage", "lao_mage_hand"},
            )
        )
        if not damage_followup_effect_applies(ctx):
            return
        destination = target.position
        if destination is None:
            return
        for _ in range(3):
            next_cell = Position(destination.x + dx, destination.y + dy)
            if not battle.in_bounds(next_cell) or not battle.can_place_unit(target, next_cell, ignore=target, mover=target):
                break
            destination = next_cell
        if destination != target.position:
            battle.move_unit(target, destination, via_skill=True, forced=True, max_distance=99, tags={"lao_mage_hand"})

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.position is not None and battle.unit_target_in_range_and_line(actor, unit, 1)
        ]
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
            "selection": {
                "mode": "unit_direction",
                "directions": [{"dx": dx, "dy": dy} for dx, dy in ALL_DIRECTIONS],
            },
        }

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class MageCloakEquippedStatus(StatModifierStatus):
    def __init__(self, summoner_id: str) -> None:
        super().__init__("法师斗篷", speed_delta=3, defense_delta=1, description="速 +3，守 +1，移动次数 +1，飞行，近战普攻伤害 +1。")
        self.summoner_id = summoner_id

    def bind(self, owner: HeroUnit) -> "MageCloakEquippedStatus":
        super().bind(owner)
        owner.ignore_units_while_moving = True
        owner.has_flying = True
        return self

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + 1

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags:
            return
        if ctx.target.position is not None and battle.unit_target_in_range_and_line(owner, ctx.target, 1):
            ctx.attack_power += 1

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.has_flying = any(isinstance(component, FlyingTrait) for component in owner.traits) or any(
            isinstance(status, MageCloakEquippedStatus) for status in owner.statuses
        )
        owner.ignore_units_while_moving = any(isinstance(component, PassThroughMovementTrait) for component in owner.traits) or owner.has_flying


class MageCloakSummonedStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("法师斗篷已召唤", "已经使用过法师斗篷大招。", duration=None)


class MageCloakEquipSkill(Skill):
    def __init__(self) -> None:
        super().__init__("equip_mage_cloak", "装备斗篷", "装备到一个法师单位上。", target_mode="ally")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.summoner_id is None:
            return False, "法师斗篷没有召唤者。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        if target.role != "法师":
            raise ActionError("法师斗篷只能装备到法师单位。")
        if target.get_status("法师斗篷") is not None:
            raise ActionError("该单位已经装备法师斗篷。")
        target.add_status(MageCloakEquippedStatus(str(actor.summoner_id)))
        actor.alive = False
        actor.position = None
        battle.log(f"{actor.name} 装备到 {target.name} 身上。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.player_units(actor.player_id)
            if unit.position is not None
            and unit.role == "法师"
            and unit.get_status("法师斗篷") is None
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class MageCloakSummon(AbstractHero):
    hero_code = "mage_cloak"
    hero_name = "法师斗篷"
    role = "装备"
    attribute = "土"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=1, defense=2, speed=5, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)
        self.can_act_on_entry_turn = True
        self.turn_ready = True

    def build_skills(self) -> list[Skill]:
        return [MageCloakEquipSkill()]

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait()]


class MageCloakSkill(Skill):
    def __init__(self) -> None:
        super().__init__("summon_mage_cloak", "法师斗篷", "大招：召唤法师斗篷；若已装备，可在装备者周围解除并满血重新召唤。", target_mode="cell")

    def attached_status(self, battle: Battle, actor: HeroUnit) -> tuple[HeroUnit, MageCloakEquippedStatus] | None:
        for unit in battle.player_units(actor.player_id):
            for status in unit.statuses:
                if isinstance(status, MageCloakEquippedStatus) and status.summoner_id == actor.unit_id:
                    return unit, status
        return None

    def own_cloak_alive(self, battle: Battle, actor: HeroUnit) -> HeroUnit | None:
        for unit in battle.player_units(actor.player_id):
            if getattr(unit, "hero_code", "") == "mage_cloak" and unit.summoner_id == actor.unit_id and unit.alive:
                return unit  # type: ignore[return-value]
        return None

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if self.attached_status(battle, actor) is not None:
            return True, ""
        if self.own_cloak_alive(battle, actor) is not None:
            return False, "法师斗篷已经在场。"
        if actor.get_status("法师斗篷已召唤") is not None:
            return False, "法师斗篷大招本场已经使用过。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        attached = self.attached_status(battle, actor)
        destination = payload_position(payload)
        if attached is not None:
            target, status = attached
            if not any(destination == cell for cell in square_around_cells(battle, battle.unit_cells(target), radius=1)):
                raise ActionError("解除装备只能召唤到装备者周围。")
            probe = MageCloakSummon(actor.player_id)
            if not battle.can_place_unit(probe, destination):
                raise ActionError("该位置不能召唤法师斗篷。")
            target.remove_status(status, battle)
            cloak = MageCloakSummon(actor.player_id)
            battle.summon_unit(cloak, destination, summoner=actor)
            cloak.current_hp = cloak.max_health
            cloak.turn_ready = True
            cloak.can_act_on_entry_turn = True
            battle.log(f"{actor.name} 解除【法师斗篷】，斗篷重新出现在战场。")
            return
        cells = self.available_cells(battle, actor)
        if destination not in cells:
            raise ActionError("请选择周围合法格召唤法师斗篷。")
        cloak = MageCloakSummon(actor.player_id)
        battle.summon_unit(cloak, destination, summoner=actor)
        cloak.turn_ready = True
        cloak.can_act_on_entry_turn = True
        actor.add_status(MageCloakSummonedStatus())
        battle.log(f"{actor.name} 召唤【法师斗篷】。")

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        attached = self.attached_status(battle, actor)
        center = battle.unit_cells(attached[0]) if attached is not None else battle.unit_cells(actor)
        return [cell for cell in square_around_cells(battle, center, radius=1) if battle.can_place_unit(MageCloakSummon(actor.player_id), cell)]

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class LaoDamageStatCancelTrait(Trait):
    def __init__(self) -> None:
        super().__init__("能力抵消", "受伤前可将攻/守/速/范之一 -1 到最低 1，取消该次伤害。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.cancelled:
            return
        if ctx.source is not None and ctx.source.player_id == owner.player_id:
            return
        if ctx.is_skill and owner.magic_immunity and not ctx.ignore_magic_immunity:
            return
        if owner.total_shields() > 0 and not ctx.ignore_shield and not ctx.half_ignore_shield:
            return
        if ctx.raw_damage is None:
            attack_power = ctx.attack_power + max(0, int(ctx.area_cell_hits) - 1)
            if battle.damage_rule.calculate_damage(attack_power, owner.stat("defense")) <= 0:
                return
        elif ctx.raw_damage <= 0:
            return
        candidates = [stat for stat in ("attack", "defense", "speed", "attack_range") if owner.stat(stat) > 1]
        if not candidates:
            return
        stat = max(candidates, key=lambda item: owner.stat(item))
        kwargs = {
            "attack": {"attack_delta": -1},
            "defense": {"defense_delta": -1},
            "speed": {"speed_delta": -1},
            "attack_range": {"range_delta": -1},
        }[stat]
        owner.add_status(StatModifierStatus("能力抵消", description=f"{stat} -1。", duration=None, **kwargs))
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 将 {stat} -1，抵消了【{ctx.action_name}】的伤害。"


class FloatingCannonBerserkStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("浮游炮狂暴化", "浮游炮攻 +2，速 +2，范 -2，攻 2 次，且只能攻击最近敌方单位。", duration=None)


class FloatingCannonsActiveStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("浮游炮展开", "樱火已展开浮游炮；被破坏的浮游炮会在樱火下回合开始时补回。", duration=None)


class FloatingCannonBuffStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("浮游炮狂暴", attack_delta=2, speed_delta=2, range_delta=-2, description="攻 +2，速 +2，范 -2，攻击次数变 2。")

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return max(value, 2)


class FloatingCannonStatTrait(Trait):
    def __init__(self) -> None:
        super().__init__("浮游炮狂暴属性", "狂暴化时攻 +2，速 +2，范 -2，攻击次数变 2。")

    def bind(self, owner: HeroUnit) -> "FloatingCannonStatTrait":
        super().bind(owner)
        owner.magic_immunity = True
        return self

    def _berserk(self, battle: Battle) -> bool:
        owner = self.owner
        if owner is None or owner.summoner_id is None:
            return False
        summoner = battle.units.get(owner.summoner_id)
        return bool(summoner is not None and summoner.get_status("浮游炮狂暴化") is not None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value

    def can_attack_target_with_payload(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any]) -> tuple[bool, str]:
        setattr(actor, "_current_battle_for_trait", battle)
        if not self._berserk(battle):
            return True, ""
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.position is not None and not unit.banished and unit.alive]
        if not enemies:
            return True, ""
        nearest = min(battle.distance_between_units(actor, unit) for unit in enemies)
        if battle.distance_between_units(actor, target) != nearest:
            return False, "浮游炮狂暴化时只能攻击最近的敌方单位。"
        return True, ""


class FloatingCannonSummon(AbstractHero):
    hero_code = "floating_cannon"
    hero_name = "浮游炮"
    role = "召唤物"
    attribute = "光"
    race = "机械"
    level = 1
    base_stats = Stats(attack=3, defense=2, speed=4, attack_range=4, mana=0)
    raw_skill_text = ""
    raw_trait_text = "魔免"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FloatingCannonStatTrait()]


def floating_cannons_for(battle: Battle, owner: HeroUnit) -> list[HeroUnit]:
    return [
        unit
        for unit in battle.player_units(owner.player_id)
        if getattr(unit, "hero_code", "") == "floating_cannon"
        and unit.summoner_id == owner.unit_id
        and unit.alive
        and unit.position is not None
    ]  # type: ignore[return-value]


class FloatingCannonsSkill(Skill):
    def __init__(self) -> None:
        super().__init__("floating_cannons", "浮游炮*4", "大招：召唤 4 个浮游炮到周围合法格。", max_uses_per_battle=1, target_mode="cell")

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1) if battle.can_place_unit(FloatingCannonSummon(actor.player_id), cell)]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.available_cells(battle, actor)
        if len(cells) < 4:
            raise ActionError("周围没有足够空间召唤 4 个浮游炮。")
        first = payload_position(payload)
        ordered = sorted(cells, key=lambda cell: (cell != first, cell.y, cell.x))
        if first not in cells:
            raise ActionError("请选择周围合法格作为浮游炮召唤起点。")
        for cell in ordered[:4]:
            cannon = FloatingCannonSummon(actor.player_id)
            battle.summon_unit(cannon, cell, summoner=actor)
            if actor.get_status("浮游炮狂暴化") is not None:
                cannon.add_status(FloatingCannonBuffStatus())
        if actor.get_status("浮游炮展开") is None:
            actor.add_status(FloatingCannonsActiveStatus())
        battle.log(f"{actor.name} 展开 4 个【浮游炮】。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class FloatingCannonBerserkSkill(Skill):
    def __init__(self) -> None:
        super().__init__("floating_cannon_berserk", "浮游炮狂暴化", "开关技能：仅可在回合开始时使用；切换浮游炮狂暴化。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.moved_this_turn or actor.attacks_used:
            return False, "浮游炮狂暴化只能在回合开始时使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        status = actor.get_status("浮游炮狂暴化")
        if status is None:
            actor.add_status(FloatingCannonBerserkStatus())
            for cannon in floating_cannons_for(battle, actor):
                if cannon.get_status("浮游炮狂暴") is None:
                    cannon.add_status(FloatingCannonBuffStatus())
            battle.log(f"{actor.name} 开启【浮游炮狂暴化】。")
        else:
            actor.remove_status(status, battle)
            for cannon in floating_cannons_for(battle, actor):
                buff = cannon.get_status("浮游炮狂暴")
                if buff is not None:
                    cannon.remove_status(buff, battle)
            battle.log(f"{actor.name} 关闭【浮游炮狂暴化】。")


class FloatingCannonCoverSkill(Skill):
    def __init__(self) -> None:
        super().__init__("floating_cannon_cover", "浮游炮掩护", "被动技能：狂暴化关闭时，破坏目标周围 7*7 内一个己方浮游炮，保护该目标一次。", timing="passive", target_mode="ally")

    def _cannons_near(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> list[HeroUnit]:
        cells = {position_key(cell) for cell in square_around_cells(battle, battle.unit_cells(target), radius=3)}
        return [
            unit
            for unit in battle.player_units(actor.player_id)
            if getattr(unit, "hero_code", "") == "floating_cannon"
            and unit.summoner_id == actor.unit_id
            and unit.alive
            and unit.position is not None
            and any(position_key(cell) in cells for cell in battle.unit_cells(unit))
        ]  # type: ignore[return-value]

    def _threatened_allies(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> list[HeroUnit]:
        result: list[HeroUnit] = []
        for unit_id in queued_action.target_unit_ids:
            unit = battle.units.get(unit_id)
            if unit is not None and unit.player_id == actor.player_id and unit.alive and unit.position is not None and not unit.banished:
                result.append(unit)  # type: ignore[arg-type]
        return result

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if actor.get_status("浮游炮狂暴化") is not None:
            return False, "浮游炮狂暴化开启时不能使用掩护。"
        if not any(self._cannons_near(battle, actor, target) for target in self._threatened_allies(battle, actor, queued_action)):
            return False, "没有可用于掩护的浮游炮。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        reaction_payload = dict(payload or {})
        target = battle.units.get(str(reaction_payload.get("target_unit_id") or ""))
        if target is None:
            threatened = self._threatened_allies(battle, actor, queued_action)
            target = threatened[0] if threatened else None
        if target is None or target.player_id != actor.player_id:
            return False, "请选择要掩护的己方目标。"
        if not self._cannons_near(battle, actor, target):
            return False, "目标周围 7*7 内没有可用浮游炮。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None:
            threatened = self._threatened_allies(battle, actor, queued_action)
            target = threatened[0] if threatened else None
        if target is None:
            raise ActionError("请选择要掩护的目标。")
        cannons = self._cannons_near(battle, actor, target)
        if not cannons:
            raise ActionError("目标周围 7*7 内没有可用浮游炮。")
        cannon_id = str(payload.get("cannon_unit_id") or "")
        cannon = next((unit for unit in cannons if unit.unit_id == cannon_id), cannons[0])
        cannon.alive = False
        cannon.position = None
        target.add_status(WindWallBlockStatus())
        battle.log(f"{actor.name} 破坏 {cannon.name}，为 {target.name} 发动【浮游炮掩护】。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("浮游炮掩护只能通过连锁使用。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        targets = [target for target in self._threatened_allies(battle, actor, queued_action) if self._cannons_near(battle, actor, target)]
        cannon_cells = []
        for target in targets:
            for cannon in self._cannons_near(battle, actor, target):
                cannon_cells.extend(cell.to_dict() for cell in battle.unit_cells(cannon))
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": cannon_cells,
            "requires_target": True,
        }


class SakuraFloatingCannonTrait(Trait):
    def __init__(self) -> None:
        super().__init__("浮游炮回补", "已展开浮游炮后，每个樱火回合开始时补回被破坏的浮游炮到周围合法格。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None or owner.get_status("浮游炮展开") is None:
            return
        alive = [
            unit
            for unit in battle.player_units(owner.player_id)
            if getattr(unit, "hero_code", "") == "floating_cannon" and unit.summoner_id == owner.unit_id and unit.alive
        ]
        missing = max(0, 4 - len(alive))
        if missing <= 0:
            return
        cells = [cell for cell in square_around_cells(battle, battle.unit_cells(owner), radius=1) if battle.can_place_unit(FloatingCannonSummon(owner.player_id), cell)]
        for cell in sorted(cells, key=lambda item: (item.y, item.x))[:missing]:
            cannon = FloatingCannonSummon(owner.player_id)
            battle.summon_unit(cannon, cell, summoner=owner)
            if owner.get_status("浮游炮狂暴化") is not None:
                cannon.add_status(FloatingCannonBuffStatus())
            battle.log(f"{owner.name} 在回合开始时补回 1 个【浮游炮】。")


class MountainGodCounterStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("山神计数点", "牛鬼使用山神术。觉醒需要 8 个。", duration=None)


def mountain_counter_count(unit: HeroUnit) -> int:
    return sum(1 for status in unit.statuses if isinstance(status, MountainGodCounterStatus))


class DemonBladeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("demon_blade", "妖刀。魔鬼", "普通技能：费 1 魔，每回合最多 1 次；声明 3 格直线，三格伤害分别为 5、4、3。", mana_cost=1, max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for origin in battle.unit_cells(actor):
            for dx, dy in ALL_DIRECTIONS:
                cells: list[Position] = []
                for step in range(1, 4):
                    cell = Position(origin.x + dx * step, origin.y + dy * step)
                    if not battle.in_bounds(cell):
                        break
                    cells.append(cell)
                if not cells:
                    continue
                key = pattern_signature(cells)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(cells)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        cells = [payload_position(item) for item in payload.get("cells", [])]
        signature = pattern_signature(cells)
        legal = {pattern_signature(pattern): pattern for pattern in self.patterns(battle, actor)}
        if signature not in legal:
            raise ActionError("请选择合法的妖刀直线。")
        return legal[signature]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        damage_by_key = {(cell.x, cell.y): damage for cell, damage in zip(cells, [5, 4, 3])}
        for target in battle.units_at_cells(cells):
            if target.player_id == actor.player_id:
                continue
            hit_damages = [damage_by_key[(cell.x, cell.y)] for cell in battle.unit_cells(target) if (cell.x, cell.y) in damage_by_key]
            if not hit_damages:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=max(hit_damages),
                    is_skill=True,
                    action_name="妖刀。魔鬼",
                    tags={"skill", "attack", "demon_blade"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns, ordered=True)
        cell_keys = {(cell.x, cell.y) for pattern in patterns for cell in pattern}
        targets = [unit.unit_id for unit in battle.enemy_units(actor.player_id) if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview


class LargeDrainManaSkill(DrainManaSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "large_drain_mana"
        self.name = "吸魔（大）"
        self.description = "普通技能：吸魔的扩大版，范 +1。"

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range() + 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ensure_distance(actor, target, actor.targeting_range() + 1)
        target_ctx = battle.validate_target(actor, target, action_name="吸魔（大）", is_skill=True, is_hostile=True)
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        if is_mana_drain_immune(target):
            battle.log(f"{target.name} 无法被吸魔。")
            return
        lost = min(target.current_mana, 1.0)
        target.spend_mana(lost)
        actor.gain_mana(lost)
        battle.log(f"{actor.name} 吸取了 {target.name} 的 {lost} 点魔力。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range() + 1
        ]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class UnlimitedManaTemporaryStatus(StatusEffect):
    def __init__(self, duration: int = 4) -> None:
        super().__init__("山神术。室王", "魔无限。", duration=duration, tick_scope="owner_turn_end")

    def bind(self, owner: HeroUnit) -> "UnlimitedManaTemporaryStatus":
        super().bind(owner)
        owner.allow_unbounded_mana = True
        owner.current_mana = max(owner.current_mana, owner.max_mana())
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.allow_unbounded_mana = any(status is not self and isinstance(status, UnlimitedManaTemporaryStatus) for status in owner.statuses)
        owner.clamp_mana()


class MountainGodMuroSkill(Skill):
    def __init__(self) -> None:
        super().__init__("mountain_god_muro", "山神术。室王", "大招：一场战斗一次；魔无限，持续 4 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("山神术。室王")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(UnlimitedManaTemporaryStatus())
        battle.log(f"{actor.name} 发动【山神术。室王】，4 轮内魔无限。")


class MountainEscapeStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("遁术。神山", defense_delta=2, description="守 +2，不能移动，每回合魔 +1。", duration=6, tick_scope="owner_turn_end")

    def bind(self, owner: HeroUnit) -> "MountainEscapeStatus":
        super().bind(owner)
        owner.cannot_move = True
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        gained = owner.gain_mana(1)
        if gained:
            battle.log(f"{owner.name} 的【遁术。神山】使魔 +{gained}。")

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_move = any(getattr(status, "flag_name", "") in {"cannot_move", "cannot_act"} for status in owner.statuses)


class MountainEscapeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("mountain_escape", "遁术。神山", "大招：一场战斗一次；守 +2，血满，不能移动，每回合魔 +1，持续 6 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.current_hp = actor.max_health
        existing = actor.get_status("遁术。神山")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(MountainEscapeStatus())
        battle.log(f"{actor.name} 发动【遁术。神山】，生命回满并进入神山状态。")


class MountainAwakeningSkill(Skill):
    def __init__(self) -> None:
        super().__init__("mountain_awakening", "山神术。觉醒", "清空 8 个山神计数点，重置所有大招。", target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if mountain_counter_count(actor) < 8:
            return False, "需要 8 个山神计数点。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for status in list(actor.statuses):
            if isinstance(status, MountainGodCounterStatus):
                actor.remove_status(status, battle)
        for skill in actor.skills:
            if skill.max_uses_per_battle is not None:
                skill.uses_this_battle = 0
        battle.log(f"{actor.name} 发动【山神术。觉醒】，重置所有大招。")


class MountainGodCounterTrait(Trait):
    def __init__(self) -> None:
        super().__init__("山神计数", "普攻被对方连锁抵消或自己破坏单位时获得山神计数点。")

    def add_counter(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.add_status(MountainGodCounterStatus())
        battle.log(f"{owner.name} 获得 1 个【山神计数点】。")

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
        if not bool(payload.get("enemy_reacted")):
            return
        dealt_damage = any(
            ctx.target.player_id != owner.player_id
            and not ctx.cancelled
            and (ctx.raw_damage or 0) > 0
            for ctx in damage_contexts
        )
        if dealt_damage:
            return
        if missed or any(ctx.target.player_id != owner.player_id and ctx.cancelled for ctx in damage_contexts):
            self.add_counter(battle)

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.target.alive:
            return
        self.add_counter(battle)


def _area_hit_count(battle: Battle, target: HeroUnit, cells: list[Position]) -> int:
    keys = {(cell.x, cell.y) for cell in cells}
    return max(1, sum(1 for cell in battle.unit_cells(target) if (cell.x, cell.y) in keys))


class NuclearMutationSkill(Skill):
    def __init__(self) -> None:
        super().__init__("nuclear_mutation", "核变", "普通技能：费 2 魔，选择远程 6*6 区域，造成当前攻击伤害。", mana_cost=2, target_mode="cell")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 6, 6)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="核变",
                    area_cell_hits=_area_hit_count(battle, target, cells),
                    tags={"skill", "area", "nuclear_mutation"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        cell_keys = {(cell.x, cell.y) for pattern in patterns for cell in pattern}
        targets = [unit.unit_id for unit in battle.all_units() if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class GravityFieldSkill(Skill):
    def __init__(self) -> None:
        super().__init__("gravity_field", "重力场", "普通技能：3 轮一次；扔 3 次硬币决定范围，半破魔伤害，附带破魔吸魔。", cooldown_turns=3, target_mode="cell")

    def candidate_centers(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None:
            return []
        cells: list[Position] = []
        for x in range(battle.width):
            for y in range(battle.height):
                cell = Position(x, y)
                if battle.unit_distance_to_cell(actor, cell) <= actor.targeting_range():
                    cells.append(cell)
        return cells

    def chosen_center(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> Position:
        cell = Position(int(payload.get("x")), int(payload.get("y")))
        if cell not in self.candidate_centers(battle, actor):
            raise ActionError("请选择合法的重力场中心。")
        return cell

    def cells_for_side(self, battle: Battle, center: Position, side: int) -> list[Position]:
        radius_low = (side - 1) // 2
        radius_high = side // 2
        cells: list[Position] = []
        for x in range(center.x - radius_low, center.x + radius_high + 1):
            for y in range(center.y - radius_low, center.y + radius_high + 1):
                cell = Position(x, y)
                if battle.in_bounds(cell):
                    cells.append(cell)
        return cells

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        center = self.chosen_center(battle, actor, payload)
        coin_values = [2 if random.random() < 0.5 else 1 for _ in range(3)]
        b_value = sum(coin_values)
        side = b_value ** 3
        cells = self.cells_for_side(battle, center, side)
        battle.log(f"{actor.name} 的【重力场】硬币结果为 {coin_values}，范围边长 {side}。")
        for target in battle.units_at_cells(cells):
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="重力场",
                    half_ignore_shield=True,
                    area_cell_hits=_area_hit_count(battle, target, cells),
                    tags={"skill", "area", "gravity_field"},
                )
            )
            if ctx.cancelled and not ctx.shield_consumed:
                continue
            if is_mana_drain_immune(target):
                battle.log(f"{target.name} 免疫吸魔。")
                continue
            drained = target.spend_mana(1)
            gained = actor.gain_mana(drained)
            if drained or gained:
                battle.log(f"{actor.name} 的【重力场】从 {target.name} 吸取 {drained} 点魔，获得 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.candidate_centers(battle, actor)
        return {
            "mode": "cell",
            "cells": positions_to_dict(cells),
            "target_unit_ids": [unit.unit_id for unit in battle.all_units()],
            "secondary_cells": [],
            "requires_target": True,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        center = self.chosen_center(battle, actor, payload)
        return self.cells_for_side(battle, center, max(battle.width, battle.height))

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.all_units()  # type: ignore[return-value]

    def half_ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class MultiCellAreaDamageGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("多格范围伤害保护", "同一范围伤害命中本体多个占格时，只按 1 格命中结算。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if ctx.area_cell_hits <= 1:
            return
        ctx.area_cell_hits = 1
        battle.log(f"{owner.name} 不同时受到范围一格以上的伤害，只按 1 格命中结算。")


class KaiserFistSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "kaiser_fist",
            "凯撒神拳",
            "普通技能：2 轮一次，范 6，对一个敌方单位造成攻 +1 的伤害；若最终没有造成伤害，此单位魔 +2。",
            cooldown_turns=2,
            target_mode="enemy",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return 6

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack") + 1,
                is_skill=True,
                action_name="凯撒神拳",
                tags={"skill", "attack", "kaiser_fist"},
            )
        )
        if ctx.cancelled or (ctx.raw_damage or 0) <= 0:
            gained = actor.gain_mana(2)
            battle.log(f"{actor.name} 的【凯撒神拳】未造成伤害，魔 +{gained}。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.player_id != actor.player_id
            and unit.position is not None
            and actor.position is not None
            and battle.unit_target_in_range_and_line(actor, unit, 6)
        ]
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class WaterNinjaCloneAfterAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("水忍分身", "每次普攻结束后，在自身周围第一个合法格自动召唤 1 个分身。")

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id or owner.position is None:
            return
        own_keys = {(cell.x, cell.y) for cell in battle.unit_cells(owner)}
        cells = [
            cell
            for cell in square_around_cells(battle, battle.unit_cells(owner), radius=1)
            if (cell.x, cell.y) not in own_keys
        ]
        for cell in sorted(cells, key=lambda item: (item.y, item.x)):
            clone = StandardCloneSummon(owner.player_id, owner)
            if not battle.can_place_unit(clone, cell):
                continue
            battle.summon_unit(clone, cell, summoner=owner)
            battle.log(f"{owner.name} 攻击后在周围召唤了一个分身。")
            return
        battle.log(f"{owner.name} 周围没有合法位置，无法召唤分身。")


class CannotActNextTurnStatus(StatusEffect):
    def __init__(self, source_name: str) -> None:
        super().__init__(source_name, "下个自己的回合不能行动。", duration=1, tick_scope="owner_turn_end")
        self.flag_name = "cannot_act"

    def bind(self, owner: HeroUnit) -> "CannotActNextTurnStatus":
        super().bind(owner)
        owner.cannot_move = True
        owner.cannot_attack = True
        owner.cannot_use_skills = True
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.turn_ready = False
        super().on_owner_turn_start(battle)

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_move = any(
            getattr(status, "flag_name", "") in {"cannot_move", "cannot_act"}
            for status in owner.statuses
        )
        owner.cannot_attack = owner.is_clone or any(
            getattr(status, "flag_name", "") in {"cannot_attack", "cannot_act"}
            for status in owner.statuses
        )
        owner.cannot_use_skills = owner.is_clone or any(
            getattr(status, "flag_name", "") in {"cannot_use_skills", "cannot_act"}
            for status in owner.statuses
        )


class BigAvalancheWeatherEffect(BattleFieldEffect):
    weather_name = "大雪崩"
    global_weather = True

    def __init__(self) -> None:
        super().__init__("大雪崩", "全场天气：大雪崩。", duration=5)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if getattr(effect, "weather_name", None) != self.weather_name:
                continue
            effect.duration = max(int(effect.duration or 0), int(self.duration or 0))
            battle.log("天气【大雪崩】刷新。")
            return True
        return False

    def board_marker(self, battle: Battle) -> str:
        return "雪"


class SnowAvalancheSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "snow_avalanche",
            "雪崩",
            "普通技能：2 轮一次，远程选择 2*6 或 6*2 区域，按当前攻造成伤害；被击中单位下个自己的回合不能行动。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Any]]:
        return remote_rectangle_patterns(battle, actor, 2, 6) + remote_rectangle_patterns(battle, actor, 6, 2)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Any]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="雪崩",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "snow_avalanche"},
                )
            )
            if unit.alive and damage_followup_effect_applies(ctx):
                existing = unit.get_status("雪崩")
                if existing is not None:
                    unit.remove_status(existing, battle)
                unit.add_status(CannotActNextTurnStatus("雪崩"))
                battle.log(f"{unit.name} 被【雪崩】压制，下个回合不能行动。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Any]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class BigAvalancheSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "big_avalanche",
            "大雪崩",
            "大招：一场战斗一次，将天气变为“大雪崩”，持续 5 个全局天气倒计时。",
            max_uses_per_battle=1,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(BigAvalancheWeatherEffect())


class MartialGodSealStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("魔界武神之印", "全能力 +2，直到下一个敌方英雄回合结束。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "defense", "speed", "attack_range", "mana"}:
            return value + 2
        return value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if owner is not None and ended_player_id != owner.player_id:
            owner.remove_status(self, battle)

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.clamp_mana()


class MartialGodSealSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "martial_god_seal",
            "魔界武神之印",
            "普通技能：2 轮一次；全能力 +2，血 +1/2，持续到下一个敌方英雄回合结束。",
            cooldown_turns=2,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("魔界武神之印")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(MartialGodSealStatus())
        actor.gain_mana(2)
        battle.heal(HealContext(source=actor, target=actor, amount=0.5, action_name="魔界武神之印"))
        battle.log(f"{actor.name} 获得【魔界武神之印】，全能力 +2。")


class HellSlashSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "hell_slash",
            "地狱之斩",
            "大招：一场战斗一次，选择一条直线最多 10 格，按当前攻造成技能伤害。",
            max_uses_per_battle=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Any]]:
        patterns: list[list[Any]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for origin in battle.unit_cells(actor) or ([actor.position] if actor.position else []):
            for pattern in line_patterns(battle, origin, ALL_DIRECTIONS, 10):
                key = pattern_signature(pattern)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(pattern)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Any]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="地狱之斩",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "hell_slash"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Any]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class SimpleGlobalWeatherEffect(BattleFieldEffect):
    global_weather = True

    def __init__(self, weather_name: str, *, duration: int | None = None, marker: str | None = None) -> None:
        self.weather_name = weather_name
        self._marker = marker or weather_name[:1]
        super().__init__(weather_name, f"全场天气：{weather_name}。", duration=duration)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if getattr(effect, "weather_name", None) != self.weather_name:
                continue
            if self.duration is not None:
                effect.duration = max(int(effect.duration or 0), self.duration)
            battle.log(f"天气【{self.weather_name}】刷新。")
            return True
        return False

    def board_marker(self, battle: Battle) -> str:
        return self._marker


class WeatherUltimateSkill(Skill):
    def __init__(
        self,
        code: str,
        name: str,
        *,
        weather_name: str | None = None,
        duration: int | None = None,
        marker: str | None = None,
    ) -> None:
        self.weather_name = weather_name or name
        duration_text = "永久" if duration is None else f"持续 {duration} 个全局天气倒计时"
        super().__init__(
            code,
            name,
            f"大招：一场战斗一次，将全场天气变为“{self.weather_name}”，{duration_text}。",
            max_uses_per_battle=1,
            target_mode="self",
        )
        self.weather_duration = duration
        self.weather_marker = marker

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(SimpleGlobalWeatherEffect(self.weather_name, duration=self.weather_duration, marker=self.weather_marker))


class PandemoniumSpeedTrait(Trait):
    def __init__(self) -> None:
        super().__init__("万魔殿加速", "在“万魔殿”天气中速 +3。")

    def sync(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        existing = owner.get_status("万魔殿加速")
        if battle.has_weather("万魔殿"):
            if existing is None:
                owner.add_status(StatModifierStatus("万魔殿加速", speed_delta=3, description="在“万魔殿”天气中速 +3。"))
            return
        if existing is not None:
            owner.remove_status(existing, battle)

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.sync(battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        self.sync(battle)


class PandemoniumSkill(WeatherUltimateSkill):
    def __init__(self) -> None:
        super().__init__("pandemonium", "万魔殿", marker="魔")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        super().execute(battle, actor, payload)
        for unit in battle.all_units():
            for component in unit.iter_components():
                if isinstance(component, PandemoniumSpeedTrait):
                    component.sync(battle)


class PurifyManaSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "purify_mana",
            "净化",
            "普通技能：5 轮一次，选择一个敌方单位；若未被防御挡住，目标魔 -5。",
            cooldown_turns=5,
            target_mode="enemy",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ctx = battle.validate_target(
            actor,
            target,
            action_name="净化",
            is_skill=True,
            is_hostile=True,
            tags={"skill", "purify_mana"},
        )
        if ctx.cancelled:
            if ctx.reason:
                battle.log_public_event(ctx.reason, source=actor, target=target)
            return
        lost = target.spend_mana(5)
        battle.log(f"{target.name} 被【净化】减少了 {lost} 点魔。")


class SacredDuelStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("神圣决斗", "无法移动，不能使用主动技能。", duration=5, tick_scope="owner_turn_end")
        self.flag_name = "cannot_move"

    def bind(self, owner: HeroUnit) -> "SacredDuelStatus":
        super().bind(owner)
        owner.cannot_move = True
        return self

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill.timing == "active":
            return True, "神圣决斗状态下不能使用主动技能。"
        return False, ""

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_move = any(
            getattr(status, "flag_name", "") in {"cannot_move", "cannot_act"}
            for status in owner.statuses
        )


class SacredDuelSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "sacred_duel",
            "神圣决斗",
            "普通技能：5 轮一次，破魔；选择一个敌方单位，使其 5 轮无法移动且不能使用主动技能。",
            cooldown_turns=5,
            target_mode="enemy",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        apply_piercing_status_effect(
            battle,
            actor,
            target,
            action_name="神圣决斗",
            status=SacredDuelStatus(),
            is_skill=True,
            tags={"skill", "sacred_duel"},
        )


class HolyWallSkill(LightWallSkill):
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
            battle.remove_field_effect(self)
            return
        if active_unit is None or active_unit.player_id != self.player_id:
            return
        affected = {(cell.x, cell.y) for cell in self.affected_cells(battle)}
        for unit in battle.turn_bundle_units(active_unit):
            if unit.player_id != self.player_id or unit.position is None or unit.banished:
                continue
            if not any((cell.x, cell.y) in affected for cell in battle.unit_cells(unit)):
                continue
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

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        affected = {(cell.x, cell.y) for cell in self.affected_cells(battle, actor)}
        result: list[HeroUnit] = []
        for unit in battle.enemy_units(actor.player_id):
            if unit.is_summon or unit.is_clone or unit.position is None or unit.banished:
                continue
            if any((cell.x, cell.y) in affected for cell in battle.unit_cells(unit)):
                result.append(unit)  # type: ignore[arg-type]
        return result

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for target in self.targets(battle, actor):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=4,
                    is_skill=True,
                    action_name="照明之光",
                    ignore_shield=target.attribute == "暗",
                    tags={"skill", "illumination_light"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor)
        targets = self.targets(battle, actor)
        return {
            "cells": [cell.to_dict() for cell in cells],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": False,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.affected_cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return self.targets(battle, actor)


class MeditateManaSkill(Skill):
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


class TrueBladeAirSlashSkill(Skill):
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__(
            "true_blade_air_slash",
            "真刀。空气斩",
            "普通技能：费 1.5 魔，每回合一次；先向一个方向直线移动恰好 5 格，再对一个敌方目标造成其守 +1 的破魔伤害，成功后自身魔 +目标当前魔。",
            mana_cost=1.5,
            max_uses_per_turn=1,
            target_mode="enemy",
        )

    def landing_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None:
            return []
        result: list[Position] = []
        for dx, dy in ALL_DIRECTIONS:
            destination = actor.position.offset(dx * 5, dy * 5)
            if not battle.in_bounds(destination):
                continue
            try:
                battle.find_path(actor, destination, max_distance=5, exact_distance=5, straight_only=True)
            except ActionError:
                continue
            result.append(destination)
        return result

    def target_in_range_from_landing(self, battle: Battle, actor: HeroUnit, target: HeroUnit, landing: Position) -> bool:
        target_cells = battle.unit_cells(target)
        if not target_cells:
            return False
        for origin in actor.footprint_cells_at(landing):
            for target_cell in target_cells:
                if origin.distance_to(target_cell) <= actor.targeting_range() and battle.cells_are_straight_aligned(origin, target_cell):
                    return True
        return False

    def choose_landing(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any]) -> Position:
        landings = [
            landing
            for landing in self.landing_cells(battle, actor)
            if self.target_in_range_from_landing(battle, actor, target, landing)
        ]
        if not landings:
            raise ActionError("没有可在移动 5 格后命中目标的落点。")
        if payload.get("x") is not None and payload.get("y") is not None:
            selected = payload_position(payload)
            if selected not in landings:
                raise ActionError("该落点不能用于【真刀。空气斩】命中目标。")
            return selected
        target_cells = battle.unit_cells(target)
        return min(
            landings,
            key=lambda landing: (
                min(origin.distance_to(target_cell) for origin in actor.footprint_cells_at(landing) for target_cell in target_cells),
                landing.y,
                landing.x,
            ),
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        landing = self.choose_landing(battle, actor, target, payload)
        battle.move_unit(
            actor,
            landing,
            via_skill=True,
            straight_only=True,
            max_distance=5,
            exact_distance=5,
            tags={"movement", "true_blade_air_slash"},
        )
        if not self.target_in_range_from_landing(battle, actor, target, actor.position):  # type: ignore[arg-type]
            raise ActionError("移动后目标不在可命中范围内。")
        target_mana = target.current_mana
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=target.stat("defense") + 1,
                is_skill=True,
                action_name="真刀。空气斩",
                ignore_shield=True,
                tags={"skill", "attack", "true_blade_air_slash"},
            )
        )
        if not ctx.cancelled:
            gained = actor.gain_mana(target_mana)
            battle.log(f"{actor.name} 因【真刀。空气斩】获得 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.landing_cells(battle, actor)
        destinations_by_target = {
            unit.unit_id: [
                landing.to_dict()
                for landing in cells
                if self.target_in_range_from_landing(battle, actor, unit, landing)
            ]
            for unit in battle.enemy_units(actor.player_id)
        }
        destinations_by_target = {
            unit_id: destinations
            for unit_id, destinations in destinations_by_target.items()
            if destinations
        }
        target_ids = list(destinations_by_target)
        target_cells = [
            cell.to_dict()
            for unit_id in target_ids
            for cell in battle.unit_cells(battle.get_unit(unit_id))
        ]
        return {
            "cells": [cell.to_dict() for cell in cells] + target_cells,
            "target_unit_ids": target_ids,
            "secondary_cells": [cell.to_dict() for cell in cells],
            "requires_target": True,
            "destinations_by_target": destinations_by_target,
        }

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


MOVEMENT_SKILL_CODES = {
    "backstep_shot",
    "card_transposition",
    "chain_pull",
    "crazy_sand",
    "descent_moment",
    "dragon_slash",
    "evasion",
    "fate_kick",
    "fly_leap",
    "jirobo_follow_step",
    "mana_pull",
    "plasma_thruster",
    "shadow_counter",
    "true_blade_air_slash",
}


def skill_has_movement_effect(skill: Skill) -> bool:
    if skill.code in MOVEMENT_SKILL_CODES:
        return True
    movement_keywords = ("飞跃", "回避", "撤步", "牵引", "链条", "锁链", "瞬移", "换位", "移动", "位移", "推进", "飞踢", "降临", "喷射", "追步")
    return any(keyword in skill.name for keyword in movement_keywords)


class MovementSkillLockStatus(StatusEffect):
    def __init__(self, name: str = "百鸟葬禁位移", *, duration: int = 2) -> None:
        super().__init__(name, "不能使用带有位移效果的技能。", duration=duration, tick_scope="owner_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill_has_movement_effect(skill):
            return True, f"{self.name}状态下不能使用带有位移效果的技能。"
        return False, ""


class HundredBirdBurialSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "hundred_bird_burial",
            "百鸟葬",
            "普通技能：2 轮一次；远程 3*6 或 6*3 区域；伤害值为此单位攻 +2；被击中单位受到破魔禁位移效果 2 轮。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 6) + remote_rectangle_patterns(battle, actor, 6, 3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack") + 2,
                    is_skill=True,
                    action_name="百鸟葬",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "attack", "hundred_bird_burial"},
                )
            )
            if unit.alive:
                apply_piercing_status_effect(
                    battle,
                    actor,
                    unit,
                    action_name="百鸟葬禁位移",
                    status=MovementSkillLockStatus(),
                    is_skill=True,
                    tags={"skill", "hundred_bird_burial", "movement_lock"},
                )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class JiroboAfterAttackStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__(
            "次郎坊攻击后守备",
            defense_delta=1,
            description="攻击后直到下回合结束前守 +1；当前回合可使用一次百鸟葬追步移动至多 2 格。",
            duration=2,
            tick_scope="owner_turn_end",
        )
        self.follow_step_available = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        self.follow_step_available = False
        super().on_owner_turn_end(battle)


class JiroboAfterAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击后追步守备", "每次普攻后，直到下回合结束前守 +1，并可在当前回合移动至多 2 格。")

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
        existing = owner.get_status("次郎坊攻击后守备")
        if existing is not None:
            owner.remove_status(existing, battle)
        owner.add_status(JiroboAfterAttackStatus())
        battle.log(f"{owner.name} 攻击后进入追步守备状态。")


class JiroboFollowStepSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "jirobo_follow_step",
            "百鸟葬追步",
            "特性触发后的可选移动：攻击后当回合可移动至多 2 格。",
            target_mode="cell",
        )

    def _status(self, actor: HeroUnit) -> JiroboAfterAttackStatus | None:
        status = actor.get_status("次郎坊攻击后守备")
        if isinstance(status, JiroboAfterAttackStatus):
            return status
        return None

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        status = self._status(actor)
        if status is None or not status.follow_step_available:
            return False, "需要先完成一次普攻后才能追步。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        status = self._status(actor)
        if status is None or not status.follow_step_available:
            raise ActionError("需要先完成一次普攻后才能追步。")
        destination = payload_position(payload)
        battle.move_unit(actor, destination, via_skill=True, max_distance=2, tags={"movement", "jirobo_follow_step"})
        status.follow_step_available = False

    def finalize_use(self, battle: Battle, actor: HeroUnit) -> None:
        return None

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        status = self._status(actor)
        if status is None or not status.follow_step_available:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        cells = battle.reachable_positions(actor, max_distance=2)
        return {
            "cells": [cell.to_dict() for cell in cells],
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
        }


class DevourSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "undead_boy_devour",
            "吞噬",
            "普通技能：2 轮一次，破魔；目标失去当前生命的一半，此单位回复等于自身当前生命的生命值。",
            cooldown_turns=2,
            target_mode="enemy",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=0,
                raw_damage=round(target.current_hp / 2, 4),
                is_skill=True,
                action_name="吞噬",
                ignore_shield=True,
                tags={"skill", "devour"},
            )
        )
        if not ctx.cancelled:
            battle.heal(HealContext(source=actor, target=actor, amount=actor.current_hp, action_name="吞噬"))

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class UndyingQuarterTrait(Trait):
    def __init__(self) -> None:
        super().__init__("不死保留", "半血以上受到致命伤害时，每个伤害实例可保留 1/4 血留场。")
        self._pending_context_ids: set[int] = set()

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.cancelled:
            return
        if owner.current_hp >= 0.5:
            self._pending_context_ids.add(id(ctx))

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if id(ctx) not in self._pending_context_ids:
            return
        self._pending_context_ids.discard(id(ctx))
        if owner.alive:
            return
        owner.alive = True
        owner.current_hp = 0.25
        battle.log_public_event(f"{owner.name} 触发【不死保留】，以 0.25 点生命留在场上。", source=ctx.source, target=owner)


class ElectricWindStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__(
            "电风",
            speed_delta=-1,
            description="不能使用技能，速 -1，到 1。",
            duration=2,
            tick_scope="owner_turn_end",
        )

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id:
            return True, "电风状态下不能使用技能。"
        return False, ""


def front_rectangle_patterns(battle: Battle, actor: HeroUnit, depth: int, width: int) -> list[list[Position]]:
    if actor.position is None:
        return []
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    half_width = max(0, width // 2)
    for origin in battle.unit_cells(actor) or [actor.position]:
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            lateral = (-dy, dx)
            cells: list[Position] = []
            for forward in range(1, depth + 1):
                center = origin.offset(dx * forward, dy * forward)
                for side in range(-half_width, half_width + 1):
                    cell = center.offset(lateral[0] * side, lateral[1] * side)
                    if battle.in_bounds(cell):
                        cells.append(cell)
            if not cells:
                continue
            key = pattern_signature(cells)
            if key in seen:
                continue
            seen.add(key)
            patterns.append(cells)
    return patterns


class ElectricWindSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "electric_wind",
            "电风",
            "普通技能：2 轮一次；选择身前 2*3 区域，被击中单位 2 轮不能使用技能，速 -1 到 1。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return front_rectangle_patterns(battle, actor, depth=2, width=3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def apply_to_units(self, battle: Battle, actor: HeroUnit, units: list[HeroUnit], *, action_name: str) -> None:
        for unit in units:
            if not unit.alive or unit.position is None or unit.banished:
                continue
            ctx = battle.validate_target(
                actor,
                unit,
                action_name=action_name,
                is_skill=True,
                is_hostile=unit.player_id != actor.player_id,
                tags={"skill", "electric_wind"},
            )
            if ctx.cancelled:
                if ctx.reason:
                    battle.log_public_event(ctx.reason, source=actor, target=unit)
                continue
            existing = unit.get_status("电风")
            if existing is not None:
                unit.remove_status(existing, battle)
            unit.add_status(ElectricWindStatus())
            battle.log(f"{unit.name} 被【电风】影响，不能使用技能且速 -1。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        self.apply_to_units(battle, actor, battle.units_at_cells(cells), action_name="电风")  # type: ignore[arg-type]

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class AutoElectricWindTrait(Trait):
    def __init__(self) -> None:
        super().__init__("自动电风", "每个自己的回合开始时，对周围 5*5 内单位自动使用电风；没有合法目标则跳过。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None or owner.cannot_use_skills:
            return
        cells = square_around_cells(battle, battle.unit_cells(owner), radius=2)
        owner_keys = {(cell.x, cell.y) for cell in battle.unit_cells(owner)}
        targets = [
            unit
            for unit in battle.units_at_cells(cells)
            if unit.unit_id != owner.unit_id and any((cell.x, cell.y) not in owner_keys for cell in battle.unit_cells(unit))
        ]
        if not targets:
            battle.log(f"{owner.name} 的【自动电风】没有合法目标，跳过。")
            return
        ElectricWindSkill().apply_to_units(battle, owner, targets, action_name="自动电风")  # type: ignore[arg-type]


class SkySanctuarySkill(WeatherUltimateSkill):
    def __init__(self) -> None:
        super().__init__("sky_sanctuary", "天使的气息", weather_name="天空的圣域", marker="圣")


class VitalityBlastSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "vitality_blast",
            "元气爆破",
            "普通技能：仅在“天空的圣域”中可用，2 轮一次；选择一条最多 5 格直线，按当前攻造成技能伤害。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not battle.has_weather("天空的圣域"):
            return False, "需要处于“天空的圣域”天气中。"
        return True, ""

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for origin in battle.unit_cells(actor) or ([actor.position] if actor.position else []):
            if origin is None:
                continue
            for pattern in line_patterns(battle, origin, ALL_DIRECTIONS, 5):
                key = pattern_signature(pattern)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(pattern)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="元气爆破",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "vitality_blast"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class SkySanctuaryAuraEffect(BattleFieldEffect):
    weather_name = "天空圣域"

    def __init__(self, source_unit_id: str) -> None:
        self.source_unit_id = source_unit_id
        super().__init__("天空圣域", "制裁者周围 11*11 被视为天空圣域。", duration=None)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if isinstance(effect, SkySanctuaryAuraEffect) and effect.source_unit_id == self.source_unit_id:
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
        return "圣"

    def on_turn_start(self, battle: Battle, active_unit: HeroUnit | None) -> None:
        if self._source(battle) is None:
            battle.remove_field_effect(self)


class SkySanctuaryAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("天空圣域光环", "周围 11*11 天气变为“天空圣域”。")

    def _ensure_aura(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            battle.add_field_effect(SkySanctuaryAuraEffect(owner.unit_id))

    def on_enter_battle(self, battle: Battle) -> None:
        self._ensure_aura(battle)

    def on_owner_turn_start(self, battle: Battle) -> None:
        self._ensure_aura(battle)

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for effect in list(battle.field_effects):
            if isinstance(effect, SkySanctuaryAuraEffect) and effect.source_unit_id == owner.unit_id:
                battle.remove_field_effect(effect)


class PunisherHealSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "punisher_heal",
            "治疗",
            "普通技能：费 1 魔，每回合最多 1 次，可对包括自己在内的己方单位使用；目标血 +1/4，魔 +1。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="ally",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        battle.heal(HealContext(source=actor, target=target, amount=0.25, action_name="治疗"))
        gained = target.gain_mana(1)
        if gained:
            battle.log(f"{target.name} 因【治疗】获得 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.player_units(actor.player_id)
            if unit.position is not None
            and actor.position is not None
            and battle.distance_between_units(actor, unit) <= actor.targeting_range()
        ]
        cells = [unit.position.to_dict() for unit in targets if unit.position is not None]
        return {"cells": cells, "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class SanctuaryBanishStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("圣殿放逐", "无法攻击，不能使用主动技能。", duration=1, tick_scope="owner_turn_end")
        self.flag_name = "cannot_attack"

    def bind(self, owner: HeroUnit) -> "SanctuaryBanishStatus":
        super().bind(owner)
        owner.cannot_attack = True
        return self

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill.timing == "active":
            return True, "圣殿放逐状态下不能使用主动技能。"
        return False, ""

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_attack = owner.is_clone or any(
            getattr(status, "flag_name", "") in {"cannot_attack", "cannot_act"}
            for status in owner.statuses
        )


class SanctuaryBanishSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "sanctuary_banish",
            "圣殿放逐",
            "普通技能：3 轮一次；对所有处于“天空圣域”中的敌方单位施加破魔效果，直到下回合结束前无法攻击且不能使用主动技能。",
            cooldown_turns=3,
            target_mode="self",
        )

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [
            unit  # type: ignore[list-item]
            for unit in battle.enemy_units(actor.player_id)
            if battle.unit_in_weather("天空圣域", unit)
        ]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for target in self.targets(battle, actor):
            apply_piercing_status_effect(
                battle,
                actor,
                target,
                action_name="圣殿放逐",
                status=SanctuaryBanishStatus(),
                is_skill=True,
                tags={"skill", "sanctuary_banish"},
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        cells = [cell.to_dict() for target in targets for cell in battle.unit_cells(target)]
        return {"cells": cells, "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": False}

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class SanctuaryJudgmentSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "sanctuary_judgment",
            "制裁",
            "大招：一场战斗一次；所有处于“天空圣域”中的敌方单位受到 5 次技能伤害，每次伤害值等于该单位当前攻击。",
            max_uses_per_battle=1,
            target_mode="self",
        )

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [
            unit  # type: ignore[list-item]
            for unit in battle.enemy_units(actor.player_id)
            if battle.unit_in_weather("天空圣域", unit)
        ]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for target in self.targets(battle, actor):
            for _ in range(5):
                battle.resolve_damage(
                    DamageContext(
                        source=actor,
                        target=target,
                        attack_power=target.stat("attack"),
                        is_skill=True,
                        action_name="制裁",
                        tags={"skill", "sanctuary_judgment"},
                    )
                )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        cells = [cell.to_dict() for target in targets for cell in battle.unit_cells(target)]
        return {"cells": cells, "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": False}


class RemiBatSummon(AbstractHero):
    hero_code = "remi_bat"
    hero_name = "蝙蝠"
    role = "召唤物"
    attribute = ""
    race = ""
    level = 1
    base_stats = Stats(attack=3, defense=1, speed=3, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "飞行"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait()]


class RemiChaosSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "remi_chaos",
            "混沌",
            "大招：一场战斗一次；移动恰好 3 格后，对周围 8 格按当前攻击造成技能伤害。",
            max_uses_per_battle=1,
            target_mode="cell",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        path = battle.payload_positions(payload, "path")
        battle.move_unit(actor, destination, via_skill=True, max_distance=3, exact_distance=3, path=path or None, tags={"remi_chaos"})
        cells = square_around_cells(battle, battle.unit_cells(actor), radius=1)
        for target in battle.units_at_cells(cells):
            if target.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="混沌",
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "remi_chaos"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = battle.reachable_positions(actor, max_distance=3, exact_distance=3)
        secondary: list[dict[str, int]] = []
        seen: set[tuple[int, int]] = set()
        for destination in cells:
            for cell in square_around_cells(battle, actor.footprint_cells_at(destination), radius=1):
                key = (cell.x, cell.y)
                if key in seen:
                    continue
                seen.add(key)
                secondary.append(cell.to_dict())
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": secondary, "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        destination = payload_position(payload)
        return square_around_cells(battle, actor.footprint_cells_at(destination), radius=1)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.get_target_cells_for_payload(battle, actor, payload)
        return [unit for unit in battle.units_at_cells(cells) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]


class RemiBatSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "summon_remi_bat",
            "蝙蝠",
            "普通技能：每回合一次；在周围合法格召唤一只蝙蝠（攻3守1速3范1，飞行），召唤回合可以行动。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def legal_destinations(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        probe = RemiBatSummon(actor.player_id)
        result: list[Position] = []
        for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1):
            if any(cell == occupied for occupied in battle.unit_cells(actor)):
                continue
            if battle.can_place_unit(probe, cell):
                result.append(cell)
        return result

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.legal_destinations(battle, actor):
            raise ActionError("蝙蝠只能召唤在自身周围的合法空格。")
        summon = RemiBatSummon(actor.player_id)
        battle.summon_unit(summon, destination, summoner=actor)
        summon.turn_ready = True
        summon.can_act_on_entry_turn = True

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.legal_destinations(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class RemiUndyingTrait(Trait):
    def __init__(self) -> None:
        super().__init__("蕾米不灭", "血量归 0 时不破坏，而是血变为 1/4、魔 -1；若因此魔变为 0 则破坏。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or owner.alive or owner.current_hp > 0:
            return
        if owner.current_mana <= 0:
            return
        spent = owner.spend_mana(1)
        if owner.current_mana <= 0:
            battle.log(f"{owner.name} 的【蕾米不灭】扣除了 {spent} 点魔，魔为 0，仍被破坏。")
            return
        owner.current_hp = 0.25
        owner.alive = True
        battle.log(f"{owner.name} 的【蕾米不灭】使其保留 1/4 生命，并扣除了 {spent} 点魔。")


class PassiveSkillLockStatus(StatusEffect):
    def __init__(self, *, duration: int = 3) -> None:
        super().__init__("被动封锁", "不能使用被动技能。", duration=duration, tick_scope="owner_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and (skill.passive or skill.timing == "passive"):
            return True, "被动封锁状态下不能使用被动技能。"
        return False, ""


class SunSlashSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "sun_slash",
            "斩技。阳",
            "大招：一场战斗一次；破魔，按当前攻击造成技能伤害；被击中单位 3 轮不能使用被动技能。",
            max_uses_per_battle=1,
            target_mode="enemy",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name="斩技。阳",
                ignore_shield=True,
                tags={"skill", "sun_slash"},
            )
        )
        if damage_followup_effect_applies(ctx):
            target.add_status(PassiveSkillLockStatus())
            battle.log(f"{target.name} 被【斩技。阳】封锁被动技能。")

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class KikuLegacyStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("菊之遗击", "每个进攻回合额外获得一次伤害值为 4 的普攻。", duration=None)
        self._turn_number: int | None = None
        self._normal_attacks_used = 0
        self._legacy_attack_used = False

    def _sync(self, battle: Battle) -> None:
        turn_number = int(getattr(battle, "turn_number", 1) or 1)
        if self._turn_number == turn_number:
            return
        self._turn_number = turn_number
        self._normal_attacks_used = 0
        self._legacy_attack_used = False

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + 1

    def basic_attack_action_entries(self, battle: Battle, actor: HeroUnit) -> list[dict[str, Any]]:
        self._sync(battle)
        return [
            {
                "code": "kiku_legacy_attack",
                "name": "菊之遗击",
                "description": "额外普攻：伤害值为 4。",
                "attack_payload": {"attack_variant": "kiku_legacy", "attack_name": "菊之遗击"},
            }
        ]

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if (payload or {}).get("attack_variant") != "kiku_legacy":
            return {}
        return {"attack_power_override": 4, "attack_note": "伤害值固定为 4。"}

    def can_attack_target_with_payload(
        self,
        battle: Battle,
        actor: HeroUnit,
        target: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        self._sync(battle)
        variant = (payload or {}).get("attack_variant")
        base_limit = max(1, actor.attack_actions_per_turn() - 1)
        if variant == "kiku_legacy":
            if self._legacy_attack_used:
                return False, "本回合已经使用过【菊之遗击】。"
            return True, ""
        if self._normal_attacks_used >= base_limit:
            return False, "普通攻击次数已用完，只剩【菊之遗击】。"
        return True, ""

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        self._sync(battle)
        if payload.get("attack_variant") == "kiku_legacy":
            self._legacy_attack_used = True
        else:
            self._normal_attacks_used += 1


class KikuAfterDeathTrait(Trait):
    def __init__(self) -> None:
        super().__init__("妖仙遗志", "被破坏后，场上其他己方单位每个进攻回合额外增加一次伤 4 的普攻。")

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for ally in battle.player_units(owner.player_id):
            if ally.unit_id == owner.unit_id or ally.has_status("菊之遗击"):
                continue
            ally.add_status(KikuLegacyStatus())
            battle.log(f"{ally.name} 获得【菊之遗击】。")


def _payload_direction(payload: dict[str, Any]) -> tuple[int, int]:
    direction = payload.get("direction")
    if isinstance(direction, dict):
        dx = int(direction.get("dx", 0))
        dy = int(direction.get("dy", 0))
    else:
        dx = int(payload.get("dx", 0))
        dy = int(payload.get("dy", 0))
    if (dx, dy) not in ALL_DIRECTIONS:
        raise ActionError("需要选择一个合法方向。")
    return dx, dy


class FreySkillPierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("所有技能破魔", "芙蕾的所有技能伤害和技能附带效果破魔。")

    def _is_owner_skill(self, ctx: TargetContext | DamageContext) -> bool:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        return owner is not None and source is not None and source.unit_id == owner.unit_id and ctx.is_skill

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self._is_owner_skill(ctx):
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._is_owner_skill(ctx):
            ctx.ignore_shield = True


class FreyDamageCapTrait(Trait):
    def __init__(self) -> None:
        super().__init__("伤害封顶", "每个伤害实例最多失去 1/4 生命。")
        self._before_hp: dict[int, float] = {}

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        self._before_hp[id(ctx)] = owner.current_hp
        if ctx.raw_damage is not None and ctx.raw_damage > 0.25:
            ctx.raw_damage = 0.25

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        before = self._before_hp.pop(id(ctx), None)
        if owner is None or before is None or (ctx.raw_damage or 0) <= 0.25:
            return
        capped_hp = round(max(0.0, before - 0.25), 4)
        owner.current_hp = capped_hp
        owner.alive = capped_hp > 0
        ctx.raw_damage = 0.25
        battle.log(f"{owner.name} 的【伤害封顶】使本次伤害最多为 1/4。")


class FreyQuickFlashSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "frey_quick_flash",
            "快闪",
            "普通技能：每回合最多 2 次，范 5；瞬移到一个单位周围的合法格，然后对周围造成技能伤害。",
            max_uses_per_turn=2,
            target_mode="unit",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return 5

    def legal_destinations(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> list[Position]:
        target_cells = set(battle.unit_cells(target))
        result: list[Position] = []
        for cell in square_around_cells(battle, battle.unit_cells(target), radius=1):
            if cell in target_cells:
                continue
            if battle.can_place_unit(actor, cell, ignore=actor, mover=actor):
                result.append(cell)
        return result

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_distance(actor, target, 5)
        destination = payload_position(payload)
        if destination not in self.legal_destinations(battle, actor, target):
            raise ActionError("快闪只能瞬移到目标单位周围的合法空格。")
        battle.move_unit(actor, destination, via_skill=True, allow_anywhere=True, max_distance=max(battle.width, battle.height), tags={"frey_quick_flash"})
        cells = square_around_cells(battle, battle.unit_cells(actor), radius=1)
        for unit in battle.units_at_cells(cells):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="快闪",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "frey_quick_flash"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.position is not None and actor.position is not None and battle.distance_between_units(actor, unit) <= 5
        ]
        target_cells = [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)]
        secondary: list[dict[str, int]] = []
        seen: set[tuple[int, int]] = set()
        for target in targets:
            for cell in self.legal_destinations(battle, actor, target):
                key = (cell.x, cell.y)
                if key in seen:
                    continue
                seen.add(key)
                secondary.append(cell.to_dict())
        return {"cells": target_cells, "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": secondary, "requires_target": True}


class FreyGodStabSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "frey_god_stab",
            "神刺",
            "大招：一场战斗一次；选择一条最多 4 格直线，按当前攻击造成技能伤害。",
            max_uses_per_battle=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for origin in battle.unit_cells(actor) or ([actor.position] if actor.position else []):
            if origin is None:
                continue
            for pattern in line_patterns(battle, origin, ALL_DIRECTIONS, 4):
                key = pattern_signature(pattern)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(pattern)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="神刺",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "frey_god_stab"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        return [unit for unit in battle.units_at_cells(cells) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]


class FreyLionSpearSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "frey_lion_spear",
            "狮子神枪",
            "普通技能：每回合最多 1 次；对所有斜线方向最多 4 格造成技能伤害。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        result: list[Position] = []
        seen: set[tuple[int, int]] = set()
        for origin in battle.unit_cells(actor) or ([actor.position] if actor.position else []):
            if origin is None:
                continue
            for direction in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                for cell in battle.line_positions(origin, direction, 4):
                    key = (cell.x, cell.y)
                    if key in seen:
                        continue
                    seen.add(key)
                    result.append(cell)
        return result

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.affected_cells(battle, actor)
        for unit in battle.units_at_cells(cells):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="狮子神枪",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "frey_lion_spear"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor)
        cell_keys = {(cell.x, cell.y) for cell in cells}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": targets, "secondary_cells": [], "requires_target": False}


class ZeroDashSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "zero_dash",
            "冲刺",
            "普通技能：每回合最多 1 次；向指定方向直线移动恰好 8 格，可穿过单位。",
            max_uses_per_turn=1,
            target_mode="cell",
            direction_mode="required",
        )

    def destination_for_direction(self, battle: Battle, actor: HeroUnit, direction: tuple[int, int]) -> Position:
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        return actor.position.offset(direction[0] * 8, direction[1] * 8)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        direction = _payload_direction(payload)
        destination = self.destination_for_direction(battle, actor, direction)
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            straight_only=True,
            ignore_units=True,
            max_distance=8,
            exact_distance=8,
            tags={"zero_dash"},
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells: list[Position] = []
        for direction in ALL_DIRECTIONS:
            try:
                destination = self.destination_for_direction(battle, actor, direction)
                battle.find_path(actor, destination, max_distance=8, exact_distance=8, straight_only=True, ignore_units=True)
            except ActionError:
                continue
            cells.append(destination)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class ZeroPassThroughTrait(Trait):
    def __init__(self) -> None:
        super().__init__("穿人伤害", "移动路径每穿过一个单位，对该单位结算一次伤害，并获得 0.5 魔。")

    def bind(self, owner: HeroUnit) -> "ZeroPassThroughTrait":
        super().bind(owner)
        owner.ignore_units_while_moving = True
        return self

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or len(ctx.path) <= 2:
            return
        for unit in battle.path_crossing_units(owner, ctx.path):
            battle.resolve_damage(
                DamageContext(
                    source=owner,
                    target=unit,
                    attack_power=owner.stat("attack"),
                    is_skill=bool(ctx.via_skill),
                    action_name="穿人伤害",
                    tags={"movement", "pass_through_damage"},
                )
            )
            gained = owner.gain_mana(0.5)
            if gained:
                battle.log(f"{owner.name} 穿过 {unit.name}，获得 {gained} 点魔。")


class FumaPursuitSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "fuma_pursuit",
            "追身",
            "普通技能：3 轮一次；向指定方向攻击前 4 格并移动到第 5 格，伤害破魔。",
            cooldown_turns=3,
            target_mode="cell",
            direction_mode="required",
        )

    def line_for_direction(self, battle: Battle, actor: HeroUnit, direction: tuple[int, int]) -> list[Position]:
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        line = battle.line_positions(actor.position, direction, 5)
        if len(line) < 5:
            raise ActionError("追身需要完整的 5 格直线路径。")
        if not battle.can_place_unit(actor, line[4], ignore=actor, mover=actor):
            raise ActionError("追身第 5 格必须是合法落点。")
        return line

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        direction = _payload_direction(payload)
        line = self.line_for_direction(battle, actor, direction)
        damage_cells = line[:4]
        for unit in battle.units_at_cells(damage_cells):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="追身",
                    ignore_shield=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, damage_cells),
                    tags={"skill", "fuma_pursuit"},
                )
            )
        battle.move_unit(actor, line[4], via_skill=True, straight_only=True, ignore_units=True, max_distance=5, exact_distance=5, tags={"fuma_pursuit"})

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells: list[Position] = []
        secondary: list[Position] = []
        for direction in ALL_DIRECTIONS:
            try:
                line = self.line_for_direction(battle, actor, direction)
            except ActionError:
                continue
            secondary.extend(line[:4])
            cells.append(line[4])
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [cell.to_dict() for cell in secondary], "requires_target": True}

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class FumaTrapEffect(BattleFieldEffect):
    def __init__(self, source_unit_id: str, player_id: int, center: Position) -> None:
        self.source_unit_id = source_unit_id
        self.player_id = player_id
        self.center = center
        super().__init__("陷阱", "敌方回合结束时，对陷阱格和周围造成伤害值 3 的破魔伤害。", duration=None)

    def affected_cells(self, battle: Battle) -> list[Position]:
        return square_around_cells(battle, [self.center], radius=1)

    def board_marker(self, battle: Battle) -> str:
        return "陷"

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if ended_player_id == self.player_id:
            return
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive:
            battle.remove_field_effect(self)
            return
        cells = self.affected_cells(battle)
        for unit in battle.units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=source,
                    target=unit,
                    attack_power=3,
                    is_skill=True,
                    action_name="陷阱",
                    ignore_shield=True,
                    from_field_effect=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"field", "trap"},
                )
            )


class FumaTrapSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "fuma_trap",
            "陷阱",
            "普通技能：费 0.5 魔，每回合最多 1 次；对范内一格设置陷阱，敌方回合结束时对该格和周围造成伤害值 3 的破魔伤害。",
            mana_cost=0.5,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        center = payload_position(payload)
        if battle.unit_distance_to_cell(actor, center) > actor.targeting_range():
            raise ActionError("陷阱目标格超出范围。")
        battle.add_field_effect(FumaTrapEffect(actor.unit_id, actor.player_id, center))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = [
            Position(x, y)
            for x in range(battle.width)
            for y in range(battle.height)
            if battle.unit_distance_to_cell(actor, Position(x, y)) <= actor.targeting_range()
        ]
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class FumaShurikenSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "fuma_shuriken",
            "风魔手里剑",
            "普通技能：每回合最多 1 次，范 3；选择连续 3 格直线，按当前攻击造成技能伤害。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        if actor.position is None:
            return []
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for x in range(battle.width):
            for y in range(battle.height):
                start = Position(x, y)
                for direction in ALL_DIRECTIONS:
                    cells = [start.offset(direction[0] * i, direction[1] * i) for i in range(3)]
                    if any(not battle.in_bounds(cell) for cell in cells):
                        continue
                    if not any(battle.unit_distance_to_cell(actor, cell) <= 3 for cell in cells):
                        continue
                    key = pattern_signature(cells)
                    if key in seen:
                        continue
                    seen.add(key)
                    patterns.append(cells)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="风魔手里剑",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "fuma_shuriken"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        return [unit for unit in battle.units_at_cells(cells) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]


class FumaSkillManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("风魔随机回魔", "每次使用主动技能时，后端公开随机；1/2 几率魔 +1。")

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type != "skill":
            return
        skill_code = str(payload.get("skill_code") or "")
        try:
            skill = owner.get_skill(skill_code)
        except ActionError:
            return
        if skill.timing != "active":
            return
        if random.random() < 0.5:
            gained = owner.gain_mana(1)
            battle.log(f"{owner.name} 的【风魔随机回魔】成功，获得 {gained} 点魔。")
        else:
            battle.log(f"{owner.name} 的【风魔随机回魔】未触发。")


class NianLargeDragonBreathSkill(DragonBreathSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "nian_large_dragon_breath"
        self.name = "龙息（大）"
        self.description = "普通技能：规则同龙息（大），费 2 魔，每回合最多 2 次，近身选择 3*3 区域，按当前攻击造成技能伤害。"

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return nearby_rectangle_patterns(battle, actor, 3, 3)


class NianDragonDanceSkill(Skill):
    def __init__(self) -> None:
        super().__init__("nian_dragon_dance", "龙舞", "普通技能：2 轮一次；自身魔 +4，血回满。", cooldown_turns=2, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        gained = actor.gain_mana(4)
        actor.current_hp = actor.max_health
        battle.log(f"{actor.name} 使用【龙舞】，恢复满血并获得 {gained} 点魔。")


class NianSpiritPressureStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("灵压", attack_delta=1, defense_delta=1, description="攻 +1，守 +1。", duration=3, tick_scope="owner_turn_end")


class NianSpiritPressureSkill(Skill):
    def __init__(self) -> None:
        super().__init__("nian_spirit_pressure", "灵压", "大招：一场战斗一次；自身攻 +1、守 +1，持续 3 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("灵压")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(NianSpiritPressureStatus())
        battle.log(f"{actor.name} 获得【灵压】。")


class NianRoarStatus(StatusEffect):
    def __init__(self, forced_target_id: str, forced_target_name: str) -> None:
        self.forced_target_id = forced_target_id
        super().__init__("怒吼", f"只能对 {forced_target_name} 造成伤害。", duration=2, tick_scope="owner_turn_end")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.target.unit_id == self.forced_target_id:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 受到【怒吼】限制，只能对指定单位造成伤害。"


class NianRoarSkill(Skill):
    def __init__(self) -> None:
        super().__init__("nian_roar", "怒吼", "普通技能：2 轮一次，破魔；按当前攻击造成技能伤害，并使目标 2 轮内只能对年兽造成伤害。", cooldown_turns=2, target_mode="enemy")

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name="怒吼",
                ignore_shield=True,
                tags={"skill", "nian_roar"},
            )
        )
        if damage_followup_effect_applies(ctx):
            target.add_status(NianRoarStatus(actor.unit_id, actor.name))
            battle.log(f"{target.name} 受到【怒吼】限制。")

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class NianNoHealStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("碧玉闪光", "不能回复。", duration=1, tick_scope="owner_turn_end")
        self.flag_name = "cannot_heal"

    def bind(self, owner: HeroUnit) -> "NianNoHealStatus":
        super().bind(owner)
        owner.cannot_heal = True
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_heal = any(getattr(status, "flag_name", "") == "cannot_heal" for status in owner.statuses)


class NianJadeFlashSkill(Skill):
    def __init__(self) -> None:
        super().__init__("nian_jade_flash", "碧玉闪光", "普通技能：每回合最多 1 次；身前 3*3，破魔，没有伤害；被击中单位直到下回合结束前不能回复。", max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return front_rectangle_patterns(battle, actor, width=3, depth=3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.units_at_cells(cells):
            if target.unit_id == actor.unit_id:
                continue
            apply_piercing_status_effect(
                battle,
                actor,
                target,
                action_name="碧玉闪光",
                status=NianNoHealStatus(),
                is_skill=True,
                tags={"skill", "nian_jade_flash"},
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [unit for unit in battle.units_at_cells(self.chosen_cells(battle, actor, payload)) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class BlackCatPawSkill(Skill):
    def __init__(self) -> None:
        super().__init__("black_cat_paw", "猫手", "普通技能：每回合最多 1 次；攻击周围单位，并附带破魔的吸魔效果。", max_uses_per_turn=1, target_mode="self")

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=1)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.affected_cells(battle, actor)
        for target in battle.units_at_cells(cells):
            if target.unit_id == actor.unit_id or target.player_id == actor.player_id:
                continue
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="猫手",
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "black_cat_paw"},
                )
            )
            if damage_followup_effect_applies(ctx, allow_on_shield_break=True):
                lost = target.spend_mana(1)
                gained = actor.gain_mana(lost)
                battle.log(f"{actor.name} 的【猫手】吸取 {target.name} {lost} 点魔，回复 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor)
        cell_keys = {(cell.x, cell.y) for cell in cells}
        targets = [
            unit.unit_id
            for unit in battle.enemy_units(actor.player_id)
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": targets, "secondary_cells": [], "requires_target": False}


class BlackCatFormStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("化猫", "攻1守1速4范1，魔免；攻击后重置移动次数。", duration=None)

    def bind(self, owner: HeroUnit) -> "BlackCatFormStatus":
        super().bind(owner)
        owner.magic_immunity = True
        return self

    def modify_stat(self, stat_name: str, value: float) -> float:
        fixed = {"attack": 1, "defense": 1, "speed": 4, "attack_range": 1}
        if stat_name in fixed:
            return float(fixed[stat_name])
        return value

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
        owner.moved_this_turn = False
        owner.normal_move_steps_used = 0
        owner.normal_move_actions_used = 0
        battle.log(f"{owner.name} 的【化猫】重置了移动次数。")

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.magic_immunity = any(status.name != "化猫" and status.name == "魔免" for status in owner.statuses)


class BlackCatFormSkill(Skill):
    def __init__(self) -> None:
        super().__init__("black_cat_form", "化猫", "开关技能：每回合最多 1 次，仅可在回合开始时使用；开启/关闭化猫形态。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.moved_this_turn or actor.attacks_used:
            return False, "化猫只能在回合开始时使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("化猫")
        if existing is not None:
            actor.remove_status(existing, battle)
            battle.log(f"{actor.name} 关闭【化猫】。")
        else:
            actor.add_status(BlackCatFormStatus())
            battle.log(f"{actor.name} 开启【化猫】。")


class FantasyMoveSkill(Skill):
    def __init__(self) -> None:
        super().__init__("fantasy_move", "幻想", "普通技能：每回合最多 1 次，破魔且无法被回避；按当前攻击造成技能伤害，并强制目标移动 4 格。", max_uses_per_turn=1, target_mode="enemy")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        destination = payload_position(payload)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name="幻想",
                ignore_shield=True,
                cannot_evade=True,
                tags={"skill", "fantasy_move"},
            )
        )
        if damage_followup_effect_applies(ctx):
            battle.move_unit(target, destination, via_skill=True, forced=True, max_distance=4, exact_distance=4, tags={"fantasy_move"})

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def cannot_evade_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def legal_destinations(self, battle: Battle, target: HeroUnit) -> list[Position]:
        result: list[Position] = []
        for x in range(battle.width):
            for y in range(battle.height):
                destination = Position(x, y)
                if battle.is_forced_movement_blocked(destination):
                    continue
                if not battle.can_place_unit(target, destination, ignore=target, mover=target):
                    continue
                try:
                    battle.find_path(target, destination, max_distance=4, exact_distance=4)
                except ActionError:
                    continue
                result.append(destination)
        return result

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        destinations_by_target = {
            target.unit_id: positions_to_dict(self.legal_destinations(battle, target))
            for target in targets
        }
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets if destinations_by_target[target.unit_id]],
            "secondary_cells": [],
            "requires_target": True,
            "destinations_by_target": destinations_by_target,
        }


class RainbowMirrorNoMoveStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("彩虹镜", "本回合不能移动。", duration=1, tick_scope="owner_turn_end")
        self.flag_name = "cannot_move"

    def bind(self, owner: HeroUnit) -> "RainbowMirrorNoMoveStatus":
        super().bind(owner)
        owner.cannot_move = True
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_move = any(getattr(status, "flag_name", "") in {"cannot_move", "cannot_act"} for status in owner.statuses)


class RainbowMirrorSkill(Skill):
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("rainbow_mirror", "彩虹镜", "普通技能：费 0.5 魔，每回合最多 1 次；将一个未移动的己方单位移动到自身周围，该单位本回合不能移动。", mana_cost=0.5, max_uses_per_turn=1, target_mode="ally")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        if target.moved_this_turn or target.normal_move_steps_used > 0:
            raise ActionError("彩虹镜只能选择本回合未移动的己方单位。")
        destination = payload_position(payload)
        legal = [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1) if cell not in battle.unit_cells(actor)]
        if destination not in legal or not battle.can_place_unit(target, destination, ignore=target, mover=target):
            raise ActionError("彩虹镜只能把目标移动到自身周围合法空格。")
        battle.move_unit(target, destination, via_skill=True, allow_anywhere=True, forced=True, max_distance=max(battle.width, battle.height), tags={"rainbow_mirror"})
        target.add_status(RainbowMirrorNoMoveStatus())

    def legal_destinations(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> list[Position]:
        surrounding = [
            cell
            for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
            if cell not in battle.unit_cells(actor)
        ]
        return [
            cell
            for cell in surrounding
            if battle.can_place_unit(target, cell, ignore=target, mover=target)
        ]

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.player_units(actor.player_id)
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and not unit.moved_this_turn
            and unit.normal_move_steps_used <= 0
        ]
        destinations_by_target = {
            target.unit_id: positions_to_dict(self.legal_destinations(battle, actor, target))
            for target in targets
        }
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets if destinations_by_target[target.unit_id]],
            "secondary_cells": [],
            "requires_target": True,
            "destinations_by_target": destinations_by_target,
        }


class FriendlyMirrorStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("友好镜", "不受当前攻击 3 以上单位的普攻和技能伤害。", duration=5, tick_scope="owner_turn_end")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.source is None:
            return
        if ctx.from_field_effect:
            return
        if ctx.source.stat("attack") < 3:
            return
        if ctx.is_skill or "attack" in ctx.tags:
            ctx.cancelled = True
            ctx.reason = f"{owner.name} 的【友好镜】阻止了攻击 3 以上单位的伤害。"


class FriendlyMirrorSkill(Skill):
    def __init__(self) -> None:
        super().__init__("friendly_mirror", "友好镜", "大招：一场战斗一次；5 轮内不受当前攻击 3 以上单位的普攻和技能伤害。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("友好镜")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(FriendlyMirrorStatus())
        battle.log(f"{actor.name} 获得【友好镜】。")


class WorldSeedSkillEffectGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("世界之种技能效果免疫", "不受技能的非伤害效果影响，技能伤害仍然结算。")

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and getattr(owner, "world_seed_terrain", False):
            return True, f"{owner.name} 被视为地形，不能使用技能。"
        return False, ""


class WorldSeedProtectionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("树根守护", "自己的 1/2/3 号树根都存在时，世界之种不受伤害。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if {1, 2, 3}.issubset(alive_world_root_numbers(battle, owner)):
            ctx.cancelled = True
            ctx.reason = f"{owner.name} 的 1/2/3 号树根都在场，不受伤害。"


class WorldSeedRootCleanupTrait(Trait):
    def __init__(self) -> None:
        super().__init__("世界之种连根破坏", "世界之种被破坏时，与其同时召唤的树根也一并破坏。")

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for unit in list(battle.all_units()):
            if unit.alive and getattr(unit, "hero_code", "") == "world_root" and getattr(unit, "seed_id", None) == owner.unit_id:
                unit.alive = False
                battle.log(f"{unit.name} 因 {owner.name} 被破坏而一并破坏。")
                if unit.position is not None:
                    unit.position = None
                if unit.unit_id not in {destroyed.unit_id for destroyed in battle.destroyed_units}:
                    battle.destroyed_units.append(unit)
                battle.remove_unit(unit)


class WorldSeedRootSyncTrait(Trait):
    def __init__(self, root_number: int) -> None:
        self.root_number = root_number
        super().__init__(f"{root_number}号树根", "世界之种的树根，被视为地形。")

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["root_number"] = self.root_number
        return data


class WorldSeedTrait(Trait):
    def __init__(self) -> None:
        super().__init__("世界之种连根", "根据场上自己的树根编号赋予世界之种效果。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        seed = alive_world_seed(battle, owner)
        if seed is None:
            return
        numbers = alive_world_root_numbers(battle, seed)
        if 1 in numbers:
            gained = seed.gain_mana(1)
            if gained:
                battle.log(f"{seed.name} 因 1 号树根自然回魔 {gained}。")
        seed.magic_immunity = 3 in numbers


class WorldSeedSummon(AbstractHero):
    hero_code = "world_seed"
    hero_name = "世界之种"
    role = "地形单位"
    attribute = "木"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=5, defense=6, speed=0, attack_range=2, mana=0)
    footprint_width = 5
    footprint_height = 5
    stat_minimums = {"speed": 0.0, "mana": 0.0}
    raw_skill_text = ""
    raw_trait_text = "地形；不受技能非伤害效果；树根守护"

    def __init__(self, player_id: int, summoner_id: str) -> None:
        self.summoner_id = summoner_id
        self.world_seed_terrain = True
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [WorldSeedSkillEffectGuardTrait(), WorldSeedProtectionTrait(), WorldSeedRootCleanupTrait()]


class WorldRootSummon(AbstractHero):
    hero_code = "world_root"
    hero_name = "树根"
    role = "地形单位"
    attribute = "木"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=5, defense=6, speed=0, attack_range=2, mana=0)
    stat_minimums = {"speed": 0.0, "mana": 0.0}
    raw_skill_text = ""
    raw_trait_text = "地形；不受技能非伤害效果"

    def __init__(self, player_id: int, summoner_id: str, seed_id: str, root_number: int, direction: tuple[int, int]) -> None:
        self.summoner_id = summoner_id
        self.seed_id = seed_id
        self.root_number = root_number
        self.world_seed_terrain = True
        if direction[0] != 0:
            self.footprint_width = 2
            self.footprint_height = 1
        else:
            self.footprint_width = 1
            self.footprint_height = 2
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [WorldSeedSkillEffectGuardTrait(), WorldSeedRootSyncTrait(self.root_number)]


class JudgmentStoneSummon(AbstractHero):
    hero_code = "judgment_stone"
    hero_name = "审判之石"
    role = "召唤物"
    attribute = "木"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=0, defense=999, speed=6, attack_range=0, mana=0)
    stat_minimums = {"attack": 0.0, "attack_range": 0.0, "mana": 0.0}
    raw_skill_text = ""
    raw_trait_text = "飞行；与敌方单位重合时爆裂"

    def __init__(self, player_id: int, summoner_id: str) -> None:
        self.summoner_id = summoner_id
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id
        self.allow_enemy_destination_overlap = True

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait(), JudgmentStoneImpactTrait()]

class JudgmentStoneImpactTrait(Trait):
    def __init__(self) -> None:
        super().__init__("审判之石爆裂", "与敌方单位重合时，对该单位和周围 5*5 造成伤害 5，然后破坏。")

    def on_enter_battle(self, battle: Battle) -> None:
        self._explode_if_overlapping(battle)

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        self._explode_if_overlapping(battle)

    def _explode_if_overlapping(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.position is None:
            return
        overlapping = [unit for unit in battle.units_at_cells(battle.unit_cells(owner)) if unit.player_id != owner.player_id and unit.unit_id != owner.unit_id]
        if not overlapping:
            return
        cells = square_around_cells(battle, battle.unit_cells(owner), radius=2)
        for target in battle.units_at_cells(cells):
            if target.unit_id == owner.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=owner,
                    target=target,
                    attack_power=5,
                    raw_damage=5,
                    is_skill=True,
                    action_name="审判之石",
                    from_field_effect=True,
                    tags={"skill", "judgment_stone"},
                )
            )
        owner.alive = False
        battle.log(f"{owner.name} 爆裂并破坏。")
        battle.cleanup_dead_units()


def alive_world_seeds(battle: Battle, summoner: HeroUnit) -> list[HeroUnit]:
    return [
        unit
        for unit in battle.player_units(summoner.player_id)
        if getattr(unit, "hero_code", "") == "world_seed" and getattr(unit, "summoner_id", None) == summoner.unit_id
    ]


def alive_world_seed(battle: Battle, summoner: HeroUnit) -> HeroUnit | None:
    seeds = alive_world_seeds(battle, summoner)
    return seeds[0] if seeds else None


def alive_world_root_numbers(battle: Battle, seed_or_summoner: HeroUnit) -> set[int]:
    if getattr(seed_or_summoner, "hero_code", "") == "world_seed":
        seed_id = seed_or_summoner.unit_id
        summoner_id = getattr(seed_or_summoner, "summoner_id", None)
    else:
        seed = alive_world_seed(battle, seed_or_summoner)
        seed_id = seed.unit_id if seed is not None else None
        summoner_id = seed_or_summoner.unit_id
    result: set[int] = set()
    for unit in battle.all_units():
        if not unit.alive or getattr(unit, "hero_code", "") != "world_root":
            continue
        if seed_id is not None and getattr(unit, "seed_id", None) != seed_id:
            continue
        if seed_id is None and getattr(unit, "summoner_id", None) != summoner_id:
            continue
        result.add(int(getattr(unit, "root_number", 0) or 0))
    return result


class JudgmentStoneSkill(Skill):
    def __init__(self) -> None:
        super().__init__("judgment_stone", "审判之石", "普通技能：每回合第一次免费，之后费 0.5 魔；召唤飞行审判之石，与敌方单位重合时爆裂。", mana_cost=0.5, target_mode="cell")

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        self.sync_turn_scope(battle)
        return 0.0 if self.uses_this_turn == 0 else self.mana_cost

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells: list[Position] = []
        if actor.position is not None:
            stone = JudgmentStoneSummon(actor.player_id, actor.unit_id)
            for pos in battle.neighbors(actor.position):
                if battle.can_place_unit(stone, pos, mover=stone):
                    cells.append(pos)
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        pos = payload_position(payload)
        stone = JudgmentStoneSummon(actor.player_id, actor.unit_id)
        if actor.position is None or pos not in battle.neighbors(actor.position):
            raise ActionError("审判之石只能召唤在妖精王周围。")
        if not battle.can_place_unit(stone, pos, mover=stone):
            raise ActionError("审判之石的落点不合法。")
        if 2 in alive_world_root_numbers(battle, actor):
            stone.can_act_on_entry_turn = True
            stone.turn_ready = True
        else:
            stone.can_act_on_entry_turn = False
            stone.turn_ready = False
        stone.position = pos
        battle.units[stone.unit_id] = stone
        battle.log(f"{stone.name} 被召唤到战场。")
        for component in list(stone.iter_components()):
            component.on_enter_battle(battle)


class WorldSeedSkill(Skill):
    EDGE_DEFS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
        "north": ((2, 0), (0, -1)),
        "east": ((4, 2), (1, 0)),
        "south": ((2, 4), (0, 1)),
        "west": ((0, 2), (-1, 0)),
    }

    def __init__(self) -> None:
        super().__init__("world_seed", "世界之种", "大招：一场战斗一次；召唤 5*5 世界之种和 3 条树根。", max_uses_per_battle=1, target_mode="cell")

    def _edges(self, payload: dict[str, Any]) -> list[str]:
        raw = payload.get("root_edges") or payload.get("edges") or ["north", "east", "south"]
        edges = [str(edge).lower() for edge in raw]
        if len(edges) != 3 or len(set(edges)) != 3 or any(edge not in self.EDGE_DEFS for edge in edges):
            raise ActionError("世界之种需要选择 3 条不同的边生成树根。")
        return edges

    def _numbers(self, payload: dict[str, Any], edges: list[str]) -> list[int]:
        raw = payload.get("root_numbers")
        if raw is None:
            return [1, 2, 3]
        if isinstance(raw, dict):
            nums = [int(raw[edge]) for edge in edges]
        else:
            nums = [int(value) for value in raw]
        if sorted(nums) != [1, 2, 3]:
            raise ActionError("3 条树根的编号必须是 1、2、3。")
        return nums

    def _root_specs_for_anchor(
        self,
        battle: Battle,
        actor: HeroUnit,
        seed: WorldSeedSummon,
        anchor: Position,
        edges: list[str],
        numbers: list[int],
    ) -> list[tuple[WorldRootSummon, Position]]:
        root_specs: list[tuple[WorldRootSummon, Position]] = []
        for edge, number in zip(edges, numbers):
            midpoint, direction = self.EDGE_DEFS[edge]
            start = anchor.offset(midpoint[0] + direction[0], midpoint[1] + direction[1])
            root = WorldRootSummon(actor.player_id, actor.unit_id, seed.unit_id, number, direction)
            if direction[0] < 0:
                start = start.offset(-1, 0)
            if direction[1] < 0:
                start = start.offset(0, -1)
            if not battle.can_place_unit(root, start):
                raise ActionError("树根的落点不合法。")
            root_specs.append((root, start))
        return root_specs

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells: list[Position] = []
        edges = ["north", "east", "south"]
        numbers = [1, 2, 3]
        for y in range(battle.height):
            for x in range(battle.width):
                anchor = Position(x, y)
                seed = WorldSeedSummon(actor.player_id, actor.unit_id)
                if not battle.can_place_unit(seed, anchor):
                    continue
                try:
                    self._root_specs_for_anchor(battle, actor, seed, anchor, edges, numbers)
                except ActionError:
                    continue
                cells.append(anchor)
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        anchor = payload_position(payload)
        seed = WorldSeedSummon(actor.player_id, actor.unit_id)
        if not battle.can_place_unit(seed, anchor):
            raise ActionError("世界之种的 5*5 落点不合法。")
        edges = self._edges(payload)
        numbers = self._numbers(payload, edges)
        root_specs = self._root_specs_for_anchor(battle, actor, seed, anchor, edges, numbers)
        battle.add_unit(seed, anchor)
        for root, start in root_specs:
            battle.add_unit(root, start)
            battle.log(f"{root.name} 编号为 {root.root_number}。")


class HeavenLockStatus(StatusEffect):
    def __init__(self, source_player_id: int, delay_enemy_turns: int = 2) -> None:
        self.source_player_id = source_player_id
        self.delay_enemy_turns = delay_enemy_turns
        self.activated = False
        super().__init__("天锁", "第二个对方回合开始时受到 3 轮无法移动。", duration=None)

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.player_id == self.source_player_id:
            return
        if not self.activated:
            self.delay_enemy_turns -= 1
            if self.delay_enemy_turns > 0:
                return
            self.activated = True
            owner.cannot_move = True
            self.duration = 3
            self.tick_scope = "owner_turn_end"
            battle.log(f"{owner.name} 的【天锁】生效，无法移动。")

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            owner.cannot_move = any(getattr(status, "activated", False) and status.name == "天锁" for status in owner.statuses)


class HeavenLockSkill(Skill):
    def __init__(self) -> None:
        super().__init__("heaven_lock", "天锁", "普通技能：费 1.5 魔；选定场上一个单位，在之后第二个对方回合开始时使其 3 轮无法移动。", mana_cost=1.5, target_mode="unit")
        self.requires_direct_unit_target_line = False

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and unit.alive and unit.position is not None and not unit.banished
        ]
        return {
            "cells": positions_to_dict([unit.position for unit in targets if unit.position is not None]),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        existing = target.get_status("天锁")
        if existing is not None:
            target.remove_status(existing, battle)
        apply_piercing_status_effect(
            battle,
            actor,
            target,
            action_name="天锁",
            status=HeavenLockStatus(actor.player_id),
            is_skill=True,
            tags={"skill", "heaven_lock"},
        )

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class GhostStepSkill(DashMoveSkill):
    def __init__(self) -> None:
        super().__init__("ghost_step", "鬼步", "随时使用：费 1 魔；移动最多 2 格，每回合最多 3 次。", max_distance=2, mana_cost=1, max_uses_per_turn=3)
        self.timing = "instant"


class IaidoChargeStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("聚气。拔刀斩", "下次攻击前速度视为 1，普攻变为移动 1 格后伤 5、破魔、无法回避。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "speed":
            return 1.0
        if stat_name == "attack_range":
            return value + 1.0
        return value

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            gained = owner.gain_mana_points(1)
            if gained:
                battle.log(f"{owner.name} 因【聚气。拔刀斩】获得 {gained} 点魔力点。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id or "attack" not in ctx.tags:
            return
        ctx.attack_power = 5
        ctx.ignore_shield = True
        ctx.cannot_evade = True

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id:
            owner.remove_status(self, battle)


class IaidoChargeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("iaido_charge", "聚气。拔刀斩", "普通技能：每回合 1 次；本回合不能攻击，下次攻击先移动 1 格，伤 5、破魔、无法回避。", max_uses_per_turn=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(IaidoChargeStatus())
        actor.cannot_attack = True
        battle.log(f"{actor.name} 进入【聚气。拔刀斩】状态。")


class IaidoAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("拔刀斩攻击", "拔刀斩状态下的普攻需要先移动 1 格。")

    def can_attack_target_with_payload(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any]) -> tuple[bool, str]:
        if actor.get_status("聚气。拔刀斩") is None:
            return True, ""
        if payload.get("move_x") is None or payload.get("move_y") is None:
            return False, "拔刀斩需要同时选择移动 1 格的落点。"
        dest = Position(int(payload["move_x"]), int(payload["move_y"]))
        if actor.position is None or actor.position.distance_to(dest) != 1:
            return False, "拔刀斩只能先移动 1 格。"
        if not battle.can_place_unit(actor, dest, ignore=actor, mover=actor):
            return False, "拔刀斩移动落点不合法。"
        original = actor.position
        actor.position = dest
        try:
            if not battle.unit_target_in_range_and_line(actor, target, actor.targeting_range()):
                return False, "拔刀斩移动后目标不在普攻范围内。"
            if payload.get("x") is not None and payload.get("y") is not None:
                clicked = Position(int(payload["x"]), int(payload["y"]))
                if clicked not in battle.unit_cells(target):
                    return False, "所点格子没有命中该目标。"
                if battle.unit_distance_to_cell(actor, clicked) > actor.targeting_range():
                    return False, "拔刀斩移动后所点目标格超出普攻范围。"
        finally:
            actor.position = original
        return True, ""

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type != "attack" or owner.get_status("聚气。拔刀斩") is None:
            return
        if payload.get("move_x") is None or payload.get("move_y") is None:
            return
        battle.move_unit(owner, Position(int(payload["move_x"]), int(payload["move_y"])), via_skill=True, forced=False, max_distance=1, exact_distance=1, tags={"iaido_charge"})


class PerfectDeflectStatus(StatusEffect):
    def __init__(self, charges: int) -> None:
        self.charges = charges
        super().__init__("剩余攻击挡开", f"可挡开 {charges} 次非破魔攻击或技能。", duration=1, tick_scope="owner_turn_start")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or self.charges <= 0:
            return
        if ctx.ignore_shield or ctx.half_ignore_shield:
            return
        if not (ctx.is_skill or "attack" in ctx.tags):
            return
        self.charges -= 1
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【剩余攻击挡开】挡开了伤害。"
        if self.charges <= 0:
            owner.remove_status(self, battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["charges"] = self.charges
        return data


class PerfectSwordsmanDefenseTrait(Trait):
    def __init__(self) -> None:
        super().__init__("剩余攻击转挡开", "回合结束时将剩余攻击次数转为防守回合挡开次数，破魔无效。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_attack = owner.is_clone or any(getattr(status, "flag_name", "") in {"cannot_attack", "cannot_act"} for status in owner.statuses)
        remaining = max(0, owner.attack_actions_per_turn() - owner.attacks_used)
        old = owner.get_status("剩余攻击挡开")
        if old is not None:
            owner.remove_status(old, battle)
        if remaining > 0:
            owner.add_status(PerfectDeflectStatus(remaining))
            battle.log(f"{owner.name} 将 {remaining} 次剩余攻击转为挡开次数。")


class TimeStopInsertedTurnStatus(StatusEffect):
    def __init__(self) -> None:
        self.extra_turn_finished = False
        super().__init__("时停插入回合", "临时插入的回合结束后恢复原回合顺序。", duration=None)

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not self.extra_turn_finished:
            return
        current = battle.turn_slot_index
        ids = battle.turn_order_unit_ids
        for index, unit_id in enumerate(list(ids)):
            if unit_id != owner.unit_id or index == current:
                continue
            del ids[index]
            if index < battle.turn_slot_index:
                battle.turn_slot_index -= 1
            if battle.active_turn_unit_id == owner.unit_id:
                battle.active_turn_unit_id = ids[battle.turn_slot_index] if ids else None
            owner.remove_status(self, battle)
            return

    def on_owner_turn_end(self, battle: Battle) -> None:
        if not self.extra_turn_finished:
            self.extra_turn_finished = True


class TimeStopSkill(Skill):
    def __init__(self) -> None:
        super().__init__("time_stop", "时停", "大招/随时：仅对方回合且 5*5 内有对方单位时可用；结束当前回合并临时插入自身回合。", max_uses_per_battle=1, target_mode="self")
        self.timing = "instant"

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.player_id == battle.active_player:
            return False, "时停只能在对方回合使用。"
        if not any(unit.player_id != actor.player_id and battle.distance_between_units(actor, unit) <= 2 for unit in battle.all_units() if unit.alive and unit.position is not None):
            return False, "时停需要 5*5 内有对方单位。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.unit_id in battle.turn_order_unit_ids:
            old = actor.get_status("时停插入回合")
            if old is not None:
                actor.remove_status(old, battle)
            actor.add_status(TimeStopInsertedTurnStatus())
            battle.turn_order_unit_ids.insert(battle.turn_slot_index + 1, actor.unit_id)
            battle.log(f"{actor.name} 发动【时停】，立即插入自身回合。")
            battle.end_turn()


class FocusSkill(Skill):
    def __init__(self) -> None:
        super().__init__("focus_reset", "定神", "普通技能：每回合 1 次；魔力点 -3，重置【时停】。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.mana_points < 3:
            return False, "魔力点不足。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.sync_turn_scope(battle)
        actor.spend_mana_points(3)
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        skill = actor.get_skill("time_stop")
        skill.uses_this_battle = 0
        skill.cooldown_remaining = 0
        battle.log(f"{actor.name} 使用【定神】重置【时停】。")


class DPantherManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("D。魔力点", "每回合开始按场上己方显示名带 D。的单位数获得魔力点，最多 3。")

    def bind(self, owner: HeroUnit) -> "DPantherManaTrait":
        super().bind(owner)
        owner.mana_points = min(3.0, owner.mana_points)
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        count = sum(1 for unit in battle.player_units(owner.player_id) if "D。" in unit.name)
        before = owner.mana_points
        owner.mana_points = round(min(3.0, owner.mana_points + count), 2)
        gained = round(owner.mana_points - before, 2)
        if gained:
            battle.log(f"{owner.name} 因 D。名字获得 {gained} 点魔力点。")


class MimicSkill(Skill):
    def __init__(self) -> None:
        super().__init__("mimic_skill", "模仿", "普通技能：魔力点 -1；使用周围 11*11 可见单位的一个技能。费魔 3 以下免费，高于 3 只付超出部分。", target_mode="unit")
        self.requires_direct_unit_target_line = False

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        result: list[HeroUnit] = []
        for unit in battle.all_units():
            if unit.is_summon or unit.is_clone:
                continue
            ok, _ = battle.unit_can_be_selected(unit, actor=actor)
            if not ok:
                continue
            if battle.distance_between_units(actor, unit) > 5:
                continue
            if not any(skill.timing in {"active", "instant"} and skill.code != self.code for skill in unit.skills):
                continue
            result.append(unit)
        return result

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets],
            "requires_target": True,
            "selection": {
                "mode": "mimic_skill",
                "targets": [
                    {
                        "unit_id": target.unit_id,
                        "name": target.name,
                        "skills": [
                            {
                                "code": skill.code,
                                "name": skill.name,
                                "target_mode": skill.target_mode,
                                "timing": skill.timing,
                            }
                            for skill in target.skills
                            if skill.timing in {"active", "instant"} and skill.code != self.code
                        ],
                    }
                    for target in targets
                ],
            },
        }

    def _target_skill(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[HeroUnit, Skill, dict[str, Any]]:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            raise ActionError("模仿只能选择周围 11*11 内可见武将。")
        if battle.distance_between_units(actor, target) > 5:
            raise ActionError("模仿只能选择周围 11*11 内的单位。")
        code = str(payload.get("mimic_skill_code") or payload.get("copied_skill_code") or "")
        if not code:
            raise ActionError("模仿需要指定要使用的技能。")
        skill = target.get_skill(code)
        if skill.timing not in {"active", "instant"}:
            raise ActionError("模仿只能使用可主动使用的技能。")
        copied_payload = dict(payload.get("mimic_payload") or payload.get("copied_payload") or payload)
        copied_payload["skill_code"] = code
        copied_payload["unit_id"] = actor.unit_id
        return target, skill, copied_payload

    def _mimic_uses(self, actor: HeroUnit) -> dict[str, dict[str, int]]:
        uses = getattr(actor, "_mimic_uses", None)
        if uses is None:
            uses = {}
            setattr(actor, "_mimic_uses", uses)
        return uses

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.mana_points < 1:
            return False, "魔力点不足。"
        if payload:
            try:
                _, copied, copied_payload = self._target_skill(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
            uses = self._mimic_uses(actor).setdefault(copied.code, {"turn": -1, "turn_uses": 0, "battle_uses": 0})
            if uses["turn"] != battle.turn_number:
                uses["turn"] = battle.turn_number
                uses["turn_uses"] = 0
            if copied.max_uses_per_turn is not None and uses["turn_uses"] >= copied.max_uses_per_turn:
                return False, "模仿的该技能本回合使用次数已满。"
            if copied.max_uses_per_battle is not None and uses["battle_uses"] >= copied.max_uses_per_battle:
                return False, "模仿的该技能本场战斗使用次数已满。"
            cost = max(0.0, copied.mana_cost_for_payload(battle, actor, copied_payload) - 3.0)
            if actor.current_mana + 1e-9 < cost:
                return False, "魔力不足。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.sync_turn_scope(battle)
        _, copied, copied_payload = self._target_skill(battle, actor, payload)
        actor.spend_mana_points(1)
        actor.spend_mana(max(0.0, copied.mana_cost_for_payload(battle, actor, copied_payload) - 3.0))
        self.uses_this_turn += 1
        self.uses_this_battle += 1
        uses = self._mimic_uses(actor).setdefault(copied.code, {"turn": battle.turn_number, "turn_uses": 0, "battle_uses": 0})
        if uses["turn"] != battle.turn_number:
            uses["turn"] = battle.turn_number
            uses["turn_uses"] = 0
        uses["turn_uses"] += 1
        uses["battle_uses"] += 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target, copied, copied_payload = self._target_skill(battle, actor, payload)
        battle.log(f"{actor.name} 模仿 {target.name} 的【{copied.name}】。")
        copied.execute(battle, actor, copied_payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        try:
            _, copied, copied_payload = self._target_skill(battle, actor, payload)
        except ActionError:
            return [payload_target_unit(battle, payload)] if payload.get("target_unit_id") else []
        return copied.get_target_units_for_payload(battle, actor, copied_payload)

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        try:
            _, copied, copied_payload = self._target_skill(battle, actor, payload)
        except ActionError:
            return []
        return copied.get_target_cells_for_payload(battle, actor, copied_payload)


class FriedInspireStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("鼓舞", "本回合移动次数 +1，速度 *2。", duration=1, tick_scope="owner_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "speed":
            return value * 2
        return value

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + 1


class FriedInspireSkill(Skill):
    def __init__(self) -> None:
        super().__init__("fried_inspire", "鼓舞", "普通技能：3*3 内己方单位本回合移动次数 +1，速度 *2；每回合一次免费额外使用。", target_mode="ally", max_uses_per_turn=1)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.player_units(actor.player_id)
            if unit.alive and unit.position is not None and battle.distance_between_units(actor, unit) <= 1
        ]
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets],
            "requires_target": True,
        }

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        if payload is None:
            payload = {}
        free_available = getattr(self, "_free_turn_number", None) != battle.turn_number
        if free_available:
            base_limit = self.max_uses_per_turn
            self.max_uses_per_turn = None
            try:
                return super().can_use(battle, actor, payload)
            finally:
                self.max_uses_per_turn = base_limit
        return super().can_use(battle, actor, payload)

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.sync_turn_scope(battle)
        free_available = getattr(self, "_free_turn_number", None) != battle.turn_number
        if free_available:
            self._free_turn_number = battle.turn_number
            self.uses_this_battle += 1
            return
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        if battle.distance_between_units(actor, target) > 1:
            raise ActionError("鼓舞只能选择 3*3 内己方单位。")
        target.add_status(FriedInspireStatus())
        battle.log(f"{target.name} 受到【鼓舞】，本回合速度翻倍且移动次数 +1。")


class RoyalSoldierAuraBorrowTrait(Trait):
    def __init__(self, summoner_id: str) -> None:
        self.summoner_id = summoner_id
        super().__init__("弗里德共享", "在弗里德周围时借用其常驻特性与当前数值加成。")

    def bind(self, owner: HeroUnit) -> "RoyalSoldierAuraBorrowTrait":
        super().bind(owner)
        owner.has_block_counter = True
        return self

    def fried(self, battle: Battle) -> HeroUnit | None:
        unit = battle.units.get(self.summoner_id)
        if unit is None or not unit.alive or unit.position is None:
            return None
        return unit  # type: ignore[return-value]

    def active(self, battle: Battle) -> bool:
        owner = self.owner
        fried = self.fried(battle)
        return owner is not None and fried is not None and owner.position is not None and battle.distance_between_units(owner, fried) <= 1

    def allows_block_counter(self, battle: Battle, actor: HeroUnit) -> bool:
        return self.active(battle)

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None and self.active(battle):
            gained = owner.gain_mana(1)
            if gained:
                battle.log(f"{owner.name} 借用弗里德的自然回魔，魔 +{gained}。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        if owner is None:
            return value
        battle = getattr(owner, "_last_battle_for_stat", None)
        if battle is None:
            return value
        fried = self.fried(battle)
        if fried is None or owner.position is None or battle.distance_between_units(owner, fried) > 1:
            return value
        base = getattr(fried.base_stats, stat_name if stat_name != "attack_range" else "attack_range")
        delta = fried.stat(stat_name) - float(base)
        return value + max(0.0, delta)


class RoyalSoldierSummon(AbstractHero):
    hero_code = "royal_soldier"
    hero_name = "皇家士兵"
    role = "召唤物"
    attribute = "土"
    race = "召唤物"
    level = 1
    raw_skill_text = ""
    raw_trait_text = "弗里德召唤物"

    def __init__(self, player_id: int, summoner_id: str, attack: int, defense: int, attack_range: int) -> None:
        self.summoner_id = summoner_id
        self.base_stats = Stats(attack=attack, defense=defense, speed=2, attack_range=attack_range, mana=0)
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [RoyalSoldierAuraBorrowTrait(self.summoner_id)]

    def stat(self, stat_name: str) -> float:
        self._last_battle_for_stat = getattr(self, "_last_battle_for_stat", None)
        return super().stat(stat_name)


class RoyalSoldierSkill(Skill):
    def __init__(self) -> None:
        super().__init__("royal_soldier", "皇家士兵", "普通技能：每回合 2 次；召唤皇家士兵，攻/守/范分配 10 点，每项 1~5，速 2；同名己方最多 4 个。", target_mode="cell", max_uses_per_turn=2)

    def allocation(self, payload: dict[str, Any]) -> tuple[int, int, int]:
        attack = int(payload.get("attack") or payload.get("soldier_attack") or 0)
        defense = int(payload.get("defense") or payload.get("soldier_defense") or 0)
        attack_range = int(payload.get("range") or payload.get("attack_range") or payload.get("soldier_range") or 0)
        if any(value < 1 or value > 5 for value in (attack, defense, attack_range)) or attack + defense + attack_range != 10:
            raise ActionError("皇家士兵的攻/守/范必须各为 1~5，且总和为 10。")
        return attack, defense, attack_range

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if actor.position is None or sum(1 for unit in battle.player_units(actor.player_id) if getattr(unit, "hero_code", "") == "royal_soldier") >= 4:
            return {"cells": [], "target_unit_ids": [], "requires_target": True}
        probe = RoyalSoldierSummon(actor.player_id, actor.unit_id, 4, 3, 3)
        cells: list[Position] = []
        for y in range(battle.height):
            for x in range(battle.width):
                pos = Position(x, y)
                if actor.position.distance_to(pos) > actor.targeting_range():
                    continue
                if battle.can_place_unit(probe, pos, mover=probe):
                    cells.append(pos)
        return {
            "cells": positions_to_dict(cells),
            "target_unit_ids": [],
            "requires_target": True,
            "selection": {
                "mode": "royal_soldier",
                "allocations": [
                    {"attack": 5, "defense": 4, "range": 1},
                    {"attack": 5, "defense": 3, "range": 2},
                    {"attack": 4, "defense": 4, "range": 2},
                    {"attack": 4, "defense": 3, "range": 3},
                ],
            },
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if sum(1 for unit in battle.player_units(actor.player_id) if getattr(unit, "hero_code", "") == "royal_soldier") >= 4:
            raise ActionError("场上己方皇家士兵最多存在 4 个。")
        attack, defense, attack_range = self.allocation(payload)
        pos = payload_position(payload)
        if actor.position is None or actor.position.distance_to(pos) > actor.targeting_range():
            raise ActionError("皇家士兵只能召唤在弗里德范围内。")
        soldier = RoyalSoldierSummon(actor.player_id, actor.unit_id, attack, defense, attack_range)
        soldier._last_battle_for_stat = battle
        battle.add_unit(soldier, pos)


class FriedAllyAttackHealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击己方加血", "可以普攻己方单位；命中己方时不造成伤害，改为治疗 1/4。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if "attack" not in ctx.tags or ctx.target.player_id != owner.player_id:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 攻击己方，改为治疗。"
        battle.heal(HealContext(source=owner, target=ctx.target, amount=0.25, action_name="攻击己方加血"))


class FriedAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("弗里德统帅", "周围非武将己方单位动态获得弗里德常驻特性和当前数值加成。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for unit in battle.player_units(owner.player_id):
            setattr(unit, "_last_battle_for_stat", battle)


class LargePiercePlusSkill(PierceSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "large_pierce_plus"
        self.name = "穿刺（大）"
        self.description = "普通技能：通用穿刺扩大 1 格，选择连续 3 格直线。"

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        actor_cells = {(cell.x, cell.y) for cell in battle.unit_cells(actor)}
        origins = battle.unit_cells(actor) or ([actor.position] if actor.position else [])
        for origin in origins:
            for pattern in localized_line_patterns(
                battle,
                origin,
                self.directions(),
                3,
                max_distance=3,
                touch_distance=1,
            ):
                if any((cell.x, cell.y) in actor_cells for cell in pattern):
                    continue
                key = pattern_signature(pattern)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(pattern)
        return patterns


class ManaDrainImmunityTrait(Trait):
    prevents_mana_drain = True

    def __init__(self) -> None:
        super().__init__("无法被吸魔", "免疫双方吸魔效果。")


class AgencyDefenseStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("代行解除守备", defense_delta=4, duration=2, tick_scope="owner_turn_end", description="直到下回合结束前守 +4。")


class AgencyAttachedStatus(StatusEffect):
    def __init__(self, carrier_id: str, stat_name: str, copied_skill_code: str) -> None:
        self.carrier_id = carrier_id
        self.stat_name = stat_name
        self.copied_skill_code = copied_skill_code
        super().__init__("代行契约附着", "附着在己方单位上，获得其一项能力值和一个可见技能。", duration=None)

    def carrier(self, battle: Battle) -> HeroUnit | None:
        unit = battle.units.get(self.carrier_id)
        if unit is None or not unit.alive or unit.position is None:
            return None
        return unit  # type: ignore[return-value]

    def bind(self, owner: HeroUnit) -> "AgencyAttachedStatus":
        super().bind(owner)
        owner.cannot_be_targeted = True
        owner.cannot_attack = True
        owner.cannot_move = True
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.sync_to_carrier(battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        self.sync_to_carrier(battle)

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        self.sync_to_carrier(battle)

    def sync_to_carrier(self, battle: Battle) -> None:
        owner = self.owner
        carrier = self.carrier(battle)
        if owner is None:
            return
        if carrier is None:
            owner.remove_status(self, battle)
            return
        owner.position = carrier.position

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        if owner is None or stat_name != self.stat_name:
            return value
        battle = getattr(owner, "_agency_battle", None)
        if battle is None:
            return value
        carrier = self.carrier(battle)
        if carrier is None:
            return value
        return value + carrier.stat(stat_name)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is not None and ctx.target.unit_id == owner.unit_id:
            ctx.cancelled = True
            ctx.reason = f"{owner.name} 正在附着，无法受到伤害。"

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_be_targeted = False
        owner.cannot_attack = owner.is_clone or any(getattr(status, "flag_name", "") in {"cannot_attack", "cannot_act"} for status in owner.statuses)
        owner.cannot_move = any(getattr(status, "flag_name", "") in {"cannot_move", "cannot_act"} for status in owner.statuses)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update({"carrier_id": self.carrier_id, "stat_name": self.stat_name, "copied_skill_code": self.copied_skill_code})
        return data


class AgencyBorrowedSkill(Skill):
    MOVEMENT_SKILL_CODES = {"fly_leap", "fate_kick", "crazy_sand", "plasma_thruster", "zero_dash", "fuma_pursuit", "fantasy_move", "true_blade_air_slash", "mounted_leap"}

    def __init__(self) -> None:
        super().__init__("agency_borrowed_skill", "代行技能", "附着期间使用代行契约复制的技能。", target_mode="none")
        self.requires_direct_unit_target_line = False

    def attached_status(self, actor: HeroUnit) -> AgencyAttachedStatus:
        status = actor.get_status("代行契约附着")
        if not isinstance(status, AgencyAttachedStatus):
            raise ActionError("未处于代行契约附着状态。")
        return status

    def target_skill(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[HeroUnit, Skill, dict[str, Any]]:
        status = self.attached_status(actor)
        carrier = status.carrier(battle)
        if carrier is None:
            raise ActionError("附着目标不在战场上。")
        skill = carrier.get_skill(status.copied_skill_code)
        copied_payload = dict(payload.get("contract_payload") or payload.get("copied_payload") or payload)
        copied_payload["unit_id"] = actor.unit_id
        copied_payload["skill_code"] = skill.code
        return carrier, skill, copied_payload

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        try:
            _, skill, copied_payload = self.target_skill(battle, actor, payload or {})
        except ActionError as exc:
            return False, str(exc)
        if skill.timing not in {"active", "instant"}:
            return False, "代行契约只能使用可主动使用的技能。"
        if skill.code in self.MOVEMENT_SKILL_CODES and actor.cannot_move:
            return False, "无法移动。"
        if actor.current_mana + 1e-9 < skill.mana_cost_for_payload(battle, actor, copied_payload):
            return False, "魔力不足。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.sync_turn_scope(battle)
        _, skill, copied_payload = self.target_skill(battle, actor, payload)
        actor.spend_mana(skill.mana_cost_for_payload(battle, actor, copied_payload))
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        _, skill, copied_payload = self.target_skill(battle, actor, payload)
        skill.execute(battle, actor, copied_payload)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        try:
            _, skill, _ = self.target_skill(battle, actor, {})
        except ActionError:
            return {"cells": [], "target_unit_ids": [], "requires_target": False}
        return skill.preview(battle, actor)


class AgencyContractSkill(Skill):
    VALID_STATS = {"attack", "defense", "speed", "attack_range", "mana"}

    def __init__(self) -> None:
        super().__init__("agency_contract", "代行契约", "普通技能：每回合一次；附着己方单位并获得一项能力值和一个技能；再次使用则解除并获得守 +4。", target_mode="ally", max_uses_per_turn=1)
        self.requires_direct_unit_target_line = False

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if actor.get_status("代行契约附着") is not None:
            return {
                "cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [actor.unit_id],
                "requires_target": False,
                "selection": {"mode": "agency_contract", "attached": True},
            }
        targets: list[HeroUnit] = []
        for unit in battle.player_units(actor.player_id):
            if unit.unit_id == actor.unit_id or not unit.alive or unit.position is None or unit.banished:
                continue
            if any(skill.timing in {"active", "instant"} for skill in unit.skills):
                targets.append(unit)  # type: ignore[arg-type]
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets],
            "requires_target": True,
            "selection": {
                "mode": "agency_contract",
                "stats": ["attack", "defense", "speed", "attack_range", "mana"],
                "targets": [
                    {
                        "unit_id": target.unit_id,
                        "skills": [
                            {"code": skill.code, "name": skill.name, "target_mode": skill.target_mode}
                            for skill in target.skills
                            if skill.timing in {"active", "instant"}
                        ],
                    }
                    for target in targets
                ],
            },
        }

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        if actor.get_status("代行契约附着") is not None:
            payload = dict(payload or {})
            payload.setdefault("target_unit_id", actor.unit_id)
        return super().can_use(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        attached = actor.get_status("代行契约附着")
        if isinstance(attached, AgencyAttachedStatus):
            carrier = attached.carrier(battle)
            actor.remove_status(attached, battle)
            actor.add_status(AgencyDefenseStatus())
            source_pos = actor.position
            if source_pos is not None:
                for target in battle.enemy_units(actor.player_id):
                    if target.position is None or battle.distance_between_units(actor, target) > 1:
                        continue
                    before = target.current_mana
                    ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True, action_name="代行契约解除", tags={"skill", "agency_contract"}))
                    if damage_followup_effect_applies(ctx) and not is_mana_drain_immune(target):
                        drained = target.spend_mana(min(before, 1.0))
                        actor.gain_mana(drained)
            battle.log(f"{actor.name} 解除【代行契约】。")
            if carrier is not None:
                actor.position = carrier.position
            return
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        if target.unit_id == actor.unit_id:
            raise ActionError("代行契约需要附着在其他己方单位上。")
        stat_name = str(payload.get("stat_name") or "attack")
        if stat_name not in self.VALID_STATS:
            raise ActionError("代行契约需要选择合法能力值。")
        skill_code = str(payload.get("copied_skill_code") or payload.get("contract_skill_code") or "")
        if not skill_code:
            visible = [skill for skill in target.skills if skill.timing in {"active", "instant"}]
            if not visible:
                raise ActionError("附着目标没有可复制技能。")
            skill_code = visible[0].code
        copied = target.get_skill(skill_code)
        if copied.timing not in {"active", "instant"}:
            raise ActionError("代行契约只能复制可主动使用的技能。")
        actor._agency_battle = battle
        actor.position = target.position
        actor.add_status(AgencyAttachedStatus(target.unit_id, stat_name, skill_code))
        battle.log(f"{actor.name} 附着在 {target.name}，获得 {stat_name} 和【{copied.name}】。")


class PassiveNoRetryStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("被动失败封锁", "本回合不能再次使用被动技能。", duration=1, tick_scope="owner_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if skill.timing in {"passive", "reaction"}:
            return True, "本回合不能再次使用被动技能。"
        return False, ""


class AgencyPassivePunishTrait(Trait):
    def __init__(self) -> None:
        super().__init__("被动失败封锁", "单位用被动技能使暮别的攻击没造成伤害后，本回合不能再次使用被动技能。")

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id or "attack" not in ctx.tags:
            return
        if not ctx.target.alive:
            return
        if ctx.target.player_id == owner.player_id:
            return
        if ctx.reason and "被动" in ctx.reason:
            ctx.target.add_status(PassiveNoRetryStatus())


class WuchangMistImmunityStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("侯鸟标记", "不会被无常之雾影响。", duration=2, tick_scope="owner_turn_end")


class WuchangMistField(BattleFieldEffect):
    weather_name = "无常之雾"
    global_weather = True

    def __init__(self, source_unit_id: str) -> None:
        self.source_unit_id = source_unit_id
        super().__init__("无常之雾", "除无常以外的单位攻击或使用主动技能有 1/2 几率失败。", duration=None)

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        actor_id = str(payload.get("unit_id") or "")
        actor = battle.units.get(actor_id)
        if actor is None or getattr(actor, "hero_code", "") == "excel_r027" or actor.has_status("侯鸟标记"):
            return
        if action_type == "skill":
            skill_code = str(payload.get("skill_code") or "")
            try:
                skill = actor.get_skill(skill_code)
            except ActionError:
                return
            if skill.timing != "active":
                return
        elif action_type != "attack":
            return
        if random.random() < 0.5:
            payload["action_failed_by_wuchang_mist"] = True
            battle.log(f"{actor.name} 受到【无常之雾】影响，行动失败。")

    def board_marker(self, battle: Battle) -> str:
        return "雾"


class WuchangMistSkill(Skill):
    def __init__(self) -> None:
        super().__init__("wuchang_mist", "无常之雾", "大招：叠加天气无常之雾；除无常外攻击或主动技能有 1/2 几率失败。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(WuchangMistField(actor.unit_id))


class MigratoryBirdMarkSkill(Skill):
    def __init__(self) -> None:
        super().__init__("migratory_bird_mark", "侯鸟标记", "普通技能：2 轮一次；被击中单位 2 轮内不受无常之雾影响。", cooldown_turns=2, target_mode="enemy")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive and unit.position is not None and not unit.banished
        ]
        cells = [cell for target in targets for cell in battle.unit_cells(target)]
        return {
            "cells": positions_to_dict(cells),
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "secondary_target_unit_ids": [],
            "requires_target": True,
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True, action_name="侯鸟标记", tags={"skill", "migratory_bird_mark"}))
        if damage_followup_effect_applies(ctx, allow_on_shield_break=True):
            target.add_status(WuchangMistImmunityStatus())


class WuchangAttackSealStatus(StatusEffect):
    def __init__(self) -> None:
        self.flag_name = "cannot_attack"
        super().__init__("无常普攻封锁", "不能普攻或使用技能。", duration=2, tick_scope="owner_turn_end")

    def bind(self, owner: HeroUnit) -> "WuchangAttackSealStatus":
        super().bind(owner)
        owner.cannot_attack = True
        owner.cannot_use_skills = True
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_attack = owner.is_clone or any(getattr(status, "flag_name", "") in {"cannot_attack", "cannot_act"} for status in owner.statuses)
        owner.cannot_use_skills = owner.is_clone or any(getattr(status, "flag_name", "") in {"cannot_use_skills", "cannot_act"} for status in owner.statuses)


class WuchangAttackSealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("无常普攻封锁", "被无常普攻击中的单位不能普攻或使用技能 2 轮。")

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id:
            return
        seen: set[str] = set()
        for ctx in damage_contexts:
            if ctx.cancelled or (ctx.raw_damage or 0) <= 0 or ctx.target.unit_id in seen:
                continue
            seen.add(ctx.target.unit_id)
            ctx.target.add_status(WuchangAttackSealStatus())


class BigShensuSkill(ShensuSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "big_shensu"
        self.name = "神速（大）"
        self.description = "普通技能：本回合下一次普通移动距离 +4。"

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        actor.add_status(NextNormalMoveBoostStatus(4))
        battle.log(f"{actor.name} 获得神速（大），本回合下一次普通移动距离 +4。")


class PierceImmunityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("不受破魔", "破魔对该单位失效，护盾和魔免仍可正常阻挡。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        ctx.ignore_shield = False
        ctx.half_ignore_shield = False
        ctx.ignore_magic_immunity = False


class FeiWangSpeedOnHeroKillTrait(Trait):
    def __init__(self) -> None:
        super().__init__("破将加速", "每破坏一个武将速度 +1。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.target.alive or ctx.target.is_summon or ctx.target.is_clone:
            return
        if getattr(ctx.target, "_feiwang_speed_counted", False):
            return
        ctx.target._feiwang_speed_counted = True
        owner.add_status(StatModifierStatus("破将加速", speed_delta=1, duration=None, description="每破坏一个武将速度 +1。"))


class GaleSkill(Skill):
    DIRECTIONS = {
        "east": (1, 0),
        "west": (-1, 0),
        "south": (0, 1),
        "north": (0, -1),
    }

    def __init__(self) -> None:
        super().__init__("gale", "狂风", "普通技能：费 1.5 魔；前方 7*7，破坏召唤物和分身，并将其他单位尽量聚到中心周围；破魔。", mana_cost=1.5, target_mode="cell")

    def direction(self, payload: dict[str, Any]) -> tuple[int, int]:
        raw = str(payload.get("direction") or "east").lower()
        if raw in self.DIRECTIONS:
            return self.DIRECTIONS[raw]
        dx = int(payload.get("dx") or 0)
        dy = int(payload.get("dy") or 0)
        if (dx, dy) in self.DIRECTIONS.values():
            return dx, dy
        raise ActionError("狂风需要选择正方向。")

    def area(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            return []
        dx, dy = self.direction(payload)
        if dx:
            x0 = actor.position.x + dx
            xs = range(x0, x0 + 7 * dx, dx)
            ys = range(actor.position.y - 3, actor.position.y + 4)
            cells = [Position(x, y) for x in xs for y in ys]
        else:
            y0 = actor.position.y + dy
            ys = range(y0, y0 + 7 * dy, dy)
            xs = range(actor.position.x - 3, actor.position.x + 4)
            cells = [Position(x, y) for y in ys for x in xs]
        return [cell for cell in cells if battle.in_bounds(cell)]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.area(battle, actor, payload)
        units = list(battle.units_at_cells(cells))
        for unit in units:
            if unit.is_summon or unit.is_clone:
                unit.alive = False
                battle.log(f"{unit.name} 被【狂风】破坏。")
        battle.cleanup_dead_units()
        remaining = [unit for unit in units if unit.alive and not unit.is_summon and not unit.is_clone and unit.position is not None]
        if not remaining or not cells:
            return
        center = sorted(cells, key=lambda cell: sum(cell.distance_to(other) for other in cells))[len(cells) // 2]
        candidate_cells = sorted(cells, key=lambda cell: (cell.distance_to(center), cell.y, cell.x))
        for unit in sorted(remaining, key=lambda item: battle.distance_between_units(actor, item), reverse=True):
            for dest in candidate_cells:
                if unit.position == dest:
                    break
                if battle.can_place_unit(unit, dest, ignore=unit, mover=unit):
                    battle.move_unit(unit, dest, via_skill=True, forced=True, max_distance=99, ignore_units=True, tags={"gale"})
                    break

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = sorted({cell for direction in self.DIRECTIONS for cell in self.area(battle, actor, {"direction": direction})}, key=lambda cell: (cell.y, cell.x))
        return {
            "cells": positions_to_dict(cells),
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
            "selection": {"mode": "direction", "directions": list(self.DIRECTIONS)},
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.area(battle, actor, payload)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class InnerDimensionSwordStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("里次元大剑", "攻 +2，速 -2；普攻扩散到目标周围敌方单位。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "attack":
            return value + 2
        if stat_name == "speed":
            return max(1.0, value - 2)
        return value

    def basic_attack_area_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position] | None:
        target_id = payload.get("target_unit_id")
        if not target_id:
            return None
        target = battle.units.get(str(target_id))
        if target is None or target.position is None:
            return None
        cells = list(battle.unit_cells(target))
        keys = {position_key(cell) for cell in cells}
        for cell in square_around_cells(battle, battle.unit_cells(target), radius=1):
            if position_key(cell) in keys:
                continue
            if any(unit.player_id != actor.player_id and unit.unit_id != target.unit_id for unit in battle.units_at(cell)):
                keys.add(position_key(cell))
                cells.append(cell)
        return cells


class InnerDimensionSwordSkill(Skill):
    def __init__(self) -> None:
        super().__init__("inner_dimension_sword", "里次元大剑", "开关技能：每回合一次，仅回合开始使用；攻 +2，速 -2，普攻扩散。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used > 0 or actor.performed_active_skill:
            return False, "里次元大剑只能在回合开始阶段使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("里次元大剑")
        if existing is not None:
            actor.remove_status(existing, battle)
            battle.log(f"{actor.name} 关闭【里次元大剑】。")
        else:
            actor.add_status(InnerDimensionSwordStatus())
            battle.log(f"{actor.name} 开启【里次元大剑】。")


class KingsInsightField(BattleFieldEffect):
    weather_name = "王者的看破"
    global_weather = True

    def __init__(self, source_player_id: int) -> None:
        self.source_player_id = source_player_id
        super().__init__("王者的看破", "本回合内，非己方飞王使用被动技能时血 -3/4，然后己方飞王魔 +2。", duration=1)

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if not payload.get("queued_resolution"):
            return
        unit_id = str(payload.get("unit_id") or "")
        actor = battle.units.get(unit_id)
        if actor is None:
            return
        skill_code = str(payload.get("skill_code") or "")
        skill = actor.skill_map().get(skill_code)
        if skill is None or skill.timing not in {"passive", "reaction"}:
            return
        if actor.player_id == self.source_player_id and getattr(actor, "hero_code", "") == "excel_r028":
            return
        battle.resolve_damage(DamageContext(source=None, target=actor, attack_power=0, raw_damage=0.75, is_skill=False, from_field_effect=True, action_name="王者的看破", tags={"weather", "kings_insight"}))
        for unit in battle.player_units(self.source_player_id):
            if getattr(unit, "hero_code", "") == "excel_r028":
                unit.gain_mana(2)

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        return False, ""


class KingsInsightSkill(Skill):
    def __init__(self) -> None:
        super().__init__("kings_insight", "王者的看破", "普通技能：2 轮一次；本回合天气，非己方飞王使用被动技能血 -3/4，己方飞王魔 +2。", cooldown_turns=2, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(KingsInsightField(actor.player_id))


class MagicPointCapTrait(Trait):
    def __init__(self, cap: float) -> None:
        self.cap = cap
        super().__init__(f"魔力点上限 {int(cap)}", f"最多持有 {cap:g} 魔力点。")

    def bind(self, owner: HeroUnit) -> "MagicPointCapTrait":
        super().bind(owner)
        if owner.mana_points <= 0:
            owner.mana_points = min(float(owner.base_stats.mana), self.cap)
        else:
            owner.mana_points = min(owner.mana_points, self.cap)
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            owner.mana_points = min(owner.mana_points, self.cap)


class WeaponTransferStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("武器传送", attack_delta=5, duration=1, tick_scope="owner_turn_end", description="直到回合结束前攻 +5。")


class WeaponTransferSkill(Skill):
    def __init__(self) -> None:
        super().__init__("weapon_transfer", "武器传送", "普通技能：2 轮一次；直到回合结束前攻 +5。", cooldown_turns=2, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(WeaponTransferStatus())


class RedChargeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("red_charge", "蓄力", "普通技能：每回合一次；魔力点 +1。", max_uses_per_turn=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cap = 5.0
        actor.mana_points = min(cap, actor.mana_points + 1)


class DeadlyBowSkill(Skill):
    def __init__(self) -> None:
        super().__init__("deadly_bow", "致命之弓", "普通技能：费 1 魔；声明方向攻击 5 格，破魔；伤害为魔力点数量，用后魔力点变 0。", mana_cost=1, target_mode="cell")

    def cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            return []
        dx = int(payload.get("dx") or 0)
        dy = int(payload.get("dy") or 0)
        if dx == 0 and dy == 0:
            direction = str(payload.get("direction") or "east").lower()
            dx, dy = dict(GaleSkill.DIRECTIONS).get(direction, (1, 0))
        if max(abs(dx), abs(dy)) != 1:
            raise ActionError("致命之弓需要选择方向。")
        return [actor.position.offset(dx * step, dy * step) for step in range(1, 6) if battle.in_bounds(actor.position.offset(dx * step, dy * step))]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        damage = actor.mana_points
        cells = self.cells(battle, actor, payload)
        actor.mana_points = 0
        for target in battle.units_at_cells(cells):
            if target.player_id == actor.player_id:
                continue
            battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=0, raw_damage=damage, is_skill=True, ignore_shield=True, action_name="致命之弓", tags={"skill", "deadly_bow"}))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor, payload)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = sorted({cell for direction in GaleSkill.DIRECTIONS for cell in self.cells(battle, actor, {"direction": direction})}, key=lambda cell: (cell.y, cell.x))
        targets = [
            unit.unit_id
            for unit in battle.enemy_units(actor.player_id)
            if any(unit.unit_id == hit.unit_id for hit in battle.units_at_cells(cells))
        ]
        return {
            "cells": positions_to_dict(cells),
            "target_unit_ids": targets,
            "secondary_cells": [],
            "requires_target": True,
            "selection": {"mode": "direction", "directions": list(GaleSkill.DIRECTIONS)},
        }


class WeaponCopyStatus(StatusEffect):
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        super().__init__("武装复制", "本回合攻击变为目标攻击，并复制普攻相关特性。", duration=1, tick_scope="owner_turn_end")

    def source(self, battle: Battle) -> HeroUnit | None:
        unit = battle.units.get(self.source_id)
        return unit if unit is not None and unit.alive else None  # type: ignore[return-value]

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        battle = getattr(owner, "_weapon_copy_battle", None) if owner is not None else None
        source = self.source(battle) if battle is not None else None
        if stat_name == "attack" and source is not None:
            return source.stat("attack")
        return value

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        source = self.source(battle)
        owner = self.owner
        if source is None or owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        for trait in source.traits:
            if trait.name in {"攻击吸血", "攻击吸魔"}:
                trait.on_after_damage(battle, ctx)


class WeaponCopySkill(Skill):
    def __init__(self) -> None:
        super().__init__("weapon_copy", "武装复制", "普通技能：每回合一次；复制周围一个单位本回合攻击与普攻相关特性。", max_uses_per_turn=1, target_mode="unit")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id
            and unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.distance_between_units(actor, unit) <= 1
        ]
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if battle.distance_between_units(actor, target) > 1:
            raise ActionError("武装复制只能选择周围单位。")
        actor._weapon_copy_battle = battle
        actor.add_status(WeaponCopyStatus(target.unit_id))


class GlobalAttackStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("无限", "攻击全场。", duration=3, tick_scope="owner_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "attack_range":
            return 99
        return value


class InfiniteSkill(Skill):
    def __init__(self) -> None:
        super().__init__("infinite", "无限", "大招：3 轮内攻击全场。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(GlobalAttackStatus())


class PhysicalImmunityStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("无限铠甲", "物免 3 轮。", duration=3, tick_scope="owner_turn_end")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.is_skill or "attack" not in ctx.tags:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 物免。"


class InfiniteArmorSkill(Skill):
    def __init__(self) -> None:
        super().__init__("infinite_armor", "无限铠甲", "大招：物免 3 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(PhysicalImmunityStatus())


class InfiniteRobeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("infinite_robe", "无限法袍", "大招：魔免 3 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(MagicImmunityStatus(duration=3))


class FusionCircleAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击一周", "普攻声明方向，命中自身周围敌方单位。")

    def basic_attack_area_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position] | None:
        if actor.has_status("核冲"):
            return None
        own_cells = battle.unit_cells(actor)
        own = {position_key(cell) for cell in own_cells}
        return [cell for cell in square_around_cells(battle, own_cells, radius=1) if position_key(cell) not in own]


class NuclearRushStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("核冲", "攻 +1；普攻攻击前 4 格，结算后移动到第 5 格。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "attack":
            return value + 1
        return value

    def line(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            return []
        dx = int(payload.get("dx") or 0)
        dy = int(payload.get("dy") or 0)
        if dx == 0 and dy == 0:
            direction = str(payload.get("direction") or "east").lower()
            dx, dy = dict(GaleSkill.DIRECTIONS).get(direction, (1, 0))
        if max(abs(dx), abs(dy)) != 1:
            return []
        return [actor.position.offset(dx * step, dy * step) for step in range(1, 6) if battle.in_bounds(actor.position.offset(dx * step, dy * step))]

    def basic_attack_area_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position] | None:
        if actor.cannot_move:
            return []
        line = self.line(battle, actor, payload)
        return line[:4] if len(line) >= 4 else None

    def basic_attack_preview(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any] | None:
        if actor.cannot_move:
            return {
                "cells": [],
                "target_unit_ids": [],
                "secondary_cells": [],
                "requires_target": True,
                "selection": {"mode": "direction", "directions": []},
            }
        cells: list[Position] = []
        target_ids: list[str] = []
        directions: list[dict[str, Any]] = []
        for direction in GaleSkill.DIRECTIONS:
            area_cells = self.basic_attack_area_cells(battle, actor, {"direction": direction}) or []
            cells.extend(area_cells)
            direction_target_ids: list[str] = []
            for unit in battle.units_at_cells(area_cells):
                if actor.is_enemy_of(unit) and unit.unit_id not in target_ids:
                    target_ids.append(unit.unit_id)
                if actor.is_enemy_of(unit) and unit.unit_id not in direction_target_ids:
                    direction_target_ids.append(unit.unit_id)
            if direction_target_ids:
                directions.append(
                    {
                        "code": direction,
                        "cells": positions_to_dict(area_cells),
                        "target_unit_ids": direction_target_ids,
                    }
                )
        return {
            "cells": positions_to_dict(cells),
            "target_unit_ids": target_ids,
            "secondary_cells": [],
            "requires_target": True,
            "selection": {"mode": "direction", "directions": directions},
        }

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id:
            return
        line = self.line(battle, actor, payload)
        if len(line) >= 5 and battle.can_place_unit(owner, line[4], ignore=owner, mover=owner):
            battle.move_unit(
                owner,
                line[4],
                via_skill=True,
                forced=False,
                straight_only=True,
                ignore_units=True,
                max_distance=5,
                exact_distance=5,
                path=line[:5],
                tags={"nuclear_rush"},
            )


class NuclearRushSkill(Skill):
    def __init__(self) -> None:
        super().__init__("nuclear_rush", "核冲", "开关技能：回合开始使用；攻 +1，普攻攻击 4 格并移动到第 5 格；使用后魔力点 +1。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used > 0 or actor.performed_active_skill:
            return False, "核冲只能在回合开始阶段使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("核冲")
        if existing is not None:
            actor.remove_status(existing, battle)
        else:
            actor.add_status(NuclearRushStatus())
        actor.mana_points = min(6.0, actor.mana_points + 1)


class FusionDeathExplosionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("聚变爆炸", "被破坏后对周围 5*5 造成等于魔力点的半破魔伤害。")

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None or getattr(owner, "_fusion_exploded", False):
            return
        owner._fusion_exploded = True
        damage = min(owner.mana_points, 6.0)
        if damage <= 0:
            return
        cells = square_around_cells(battle, battle.unit_cells(owner), radius=2)
        for target in list(battle.units_at_cells(cells)):
            if target.unit_id == owner.unit_id:
                continue
            battle.resolve_damage(DamageContext(source=owner, target=target, attack_power=0, raw_damage=damage, is_skill=False, half_ignore_shield=True, from_field_effect=True, action_name="聚变爆炸", tags={"fusion_explosion"}))


class SkillDisabledStatus(StatusEffect):
    def __init__(self, skill_code: str, skill_name: str) -> None:
        self.skill_code = skill_code
        super().__init__(f"技能封印：{skill_name}", f"不能使用技能【{skill_name}】。", duration=None)

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill.code == self.skill_code:
            return True, f"技能【{skill.name}】已被封印，不能使用。"
        return False, ""


class HeavenPunishmentSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "heaven_punishment",
            "天罚",
            "普通技能：2 轮一次；选择 5*5 区域；破魔封印其中一个敌方单位的一个公开主动技能。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 5, 5)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def has_public_active_skill(self, unit: HeroUnit) -> bool:
        return any(skill.timing == "active" for skill in unit.skills)

    def affected_enemies(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [
            unit
            for unit in battle.units_at_cells(cells)
            if unit.player_id != actor.player_id and self.has_public_active_skill(unit)  # type: ignore[attr-defined]
        ]  # type: ignore[list-item]

    def choose_target(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], cells: list[Position]) -> HeroUnit:
        enemies = self.affected_enemies(battle, actor, cells)
        if not enemies:
            if payload.get("queued_resolution"):
                raise ActionMiss("【天罚】落在原定区域，没有可封印公开主动技能的敌方单位。")
            raise ActionError("天罚区域内没有可封印公开主动技能的敌方单位。")
        if payload.get("target_unit_id"):
            target = payload_target_unit(battle, payload)
            if target.unit_id not in {unit.unit_id for unit in enemies}:
                raise ActionError("指定目标不在天罚区域内，或没有可封印的公开主动技能。")
            return target
        return enemies[0]

    def choose_skill(self, target: HeroUnit, payload: dict[str, Any]) -> Skill:
        active_skills = [skill for skill in target.skills if skill.timing == "active"]
        if not active_skills:
            raise ActionError("目标没有可封印的公开主动技能。")
        selected = str(payload.get("disabled_skill_code") or "").strip()
        if selected:
            for skill in active_skills:
                if skill.code == selected:
                    return skill
            raise ActionError("只能封印目标当前公开的主动技能。")
        return active_skills[0]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        target = self.choose_target(battle, actor, payload, cells)
        skill = self.choose_skill(target, payload)
        apply_piercing_status_effect(
            battle,
            actor,
            target,
            action_name="天罚",
            status=SkillDisabledStatus(skill.code, skill.name),
            is_skill=True,
            tags={"skill", "heaven_punishment"},
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.enemy_units(actor.player_id)
            if self.has_public_active_skill(unit)
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        if payload.get("target_unit_id"):
            return [self.choose_target(battle, actor, payload, cells)]
        return self.affected_enemies(battle, actor, cells)


class NoiseWaveStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__(
            "乱音电波",
            speed_delta=-1,
            description="速 -1，且不能使用带有位移效果的技能，直到下回合结束前。",
            duration=1,
            tick_scope="owner_turn_end",
        )

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill_has_movement_effect(skill):
            return True, "乱音电波状态下不能使用带有位移效果的技能。"
        return False, ""


class NoiseWaveSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "noise_wave",
            "乱音电波",
            "普通技能：费 1 魔，每回合最多 1 次；3*3 无伤害破魔效果，命中单位速 -1 且不能使用位移技能直到下回合结束前。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            apply_piercing_status_effect(
                battle,
                actor,
                unit,
                action_name="乱音电波",
                status=NoiseWaveStatus(),
                is_skill=True,
                tags={"skill", "noise_wave"},
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class InterferenceSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "interference",
            "干扰",
            "普通技能：2 轮一次；10*10 内分身/复制体破坏，召唤物控制权转为此单位一方。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 10, 10)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in list(battle.units_at_cells(cells)):
            if unit.unit_id == actor.unit_id:
                continue
            if unit.is_clone:
                unit.alive = False
                battle.log_public_event(f"{unit.name} 被【干扰】破坏。", source=actor, target=unit)
                continue
            if unit.is_summon:
                unit.player_id = actor.player_id
                unit.summoner_id = actor.unit_id
                battle.log(f"{actor.name} 用【干扰】取得了 {unit.name} 的控制权。")
        battle.cleanup_dead_units()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if (unit.is_clone or unit.is_summon)
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        return [unit for unit in battle.units_at_cells(cells) if unit.is_clone or unit.is_summon]  # type: ignore[return-value]


class VainGiantShadowStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__(
            "虚荣巨影",
            attack_delta=2,
            description="攻 +2，无法普攻。",
            duration=4,
            tick_scope="owner_turn_end",
        )
        self.flag_name = "cannot_attack"

    def bind(self, owner: HeroUnit) -> "VainGiantShadowStatus":
        super().bind(owner)
        owner.cannot_attack = True
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_attack = owner.is_clone or any(
            getattr(status, "flag_name", "") in {"cannot_attack", "cannot_act"}
            for status in owner.statuses
        )


class VainGiantShadowSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "vain_giant_shadow",
            "虚荣巨影",
            "普通技能：每回合一次；可对任意单位或自己使用，破魔且无法被回避；4 轮内攻 +2，无法普攻。",
            max_uses_per_turn=1,
            target_mode="unit",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        apply_piercing_status_effect(
            battle,
            actor,
            target,
            action_name="虚荣巨影",
            status=VainGiantShadowStatus(),
            is_skill=True,
            tags={"skill", "vain_giant_shadow"},
        )

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def cannot_evade_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.alive
            and not unit.banished
            and unit.position is not None
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class FlorenzaAttackDebuffStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__(
            "弗伦萨普攻弱化",
            attack_delta=-1,
            defense_delta=-1,
            speed_delta=-1,
            description="攻、守、速 -1，到 1。",
            duration=1,
            tick_scope="owner_turn_end",
        )


class FlorenzaAttackFollowupTrait(Trait):
    def __init__(self) -> None:
        super().__init__("弗伦萨普攻破魔效果", "普攻带有破魔吸魔，并使目标攻守速 -1 到下回合结束前。")

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
        seen: set[str] = set()
        for ctx in damage_contexts:
            target = ctx.target
            if target.unit_id in seen or not target.alive or target.player_id == owner.player_id:
                continue
            seen.add(target.unit_id)
            applied = apply_piercing_status_effect(
                battle,
                owner,
                target,
                action_name="弗伦萨普攻破魔效果",
                status=FlorenzaAttackDebuffStatus(),
                is_skill=False,
                tags={"attack", "florenza_followup"},
            )
            if not applied:
                continue
            if is_mana_drain_immune(target):
                battle.log(f"{target.name} 无法被吸魔。")
                continue
            drained = target.spend_mana(1)
            gained = owner.gain_mana(drained)
            if drained or gained:
                battle.log(f"{owner.name} 的普攻破魔效果从 {target.name} 吸取 {drained} 点魔，获得 {gained} 点魔。")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _slug_tail(source_row: int, name: str) -> str:
    ascii_tail = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_").lower()
    return ascii_tail or f"r{source_row:03d}"


def _special_skill_factory(spec: dict[str, Any], fragment: dict[str, Any]) -> Skill | None:
    name = _text(fragment.get("name"))
    source = _text(fragment.get("fragment"))
    if spec.get("code") == "excel_r020" and name == "审判之石":
        return JudgmentStoneSkill()
    if spec.get("code") == "excel_r020" and name == "世界之种":
        return WorldSeedSkill()
    if spec.get("code") == "excel_r020" and name == "天锁":
        return HeavenLockSkill()
    if spec.get("code") == "excel_r021" and name == "鬼步":
        return GhostStepSkill()
    if spec.get("code") == "excel_r021" and (name == "聚气。拔刀斩" or source.startswith("聚气。拔刀斩")):
        return IaidoChargeSkill()
    if spec.get("code") == "excel_r021" and name == "时停":
        return TimeStopSkill()
    if spec.get("code") == "excel_r021" and name == "定神":
        return FocusSkill()
    if spec.get("code") == "excel_r022" and name == "模仿":
        return MimicSkill()
    if spec.get("code") == "excel_r024" and name == "鼓舞":
        return FriedInspireSkill()
    if spec.get("code") == "excel_r024" and name == "皇家士兵":
        return RoyalSoldierSkill()
    if spec.get("code") == "excel_r025" and source == "穿刺（大）":
        return LargePiercePlusSkill()
    if spec.get("code") == "excel_r025" and name == "代行契约":
        return AgencyContractSkill()
    if spec.get("code") == "excel_r027" and name == "无常之雾":
        return WuchangMistSkill()
    if spec.get("code") == "excel_r027" and name == "侯鸟标记":
        return MigratoryBirdMarkSkill()
    if spec.get("code") == "excel_r028" and source == "神速（大）":
        return BigShensuSkill()
    if spec.get("code") == "excel_r028" and name == "狂风":
        return GaleSkill()
    if spec.get("code") == "excel_r028" and source == "穿刺（大）":
        return LargePiercePlusSkill()
    if spec.get("code") == "excel_r028" and name == "里次元大剑":
        return InnerDimensionSwordSkill()
    if spec.get("code") == "excel_r028" and name == "王者的看破":
        return KingsInsightSkill()
    if spec.get("code") == "excel_r029" and name == "武器传送":
        return WeaponTransferSkill()
    if spec.get("code") == "excel_r029" and name == "蓄力":
        return RedChargeSkill()
    if spec.get("code") == "excel_r029" and name == "致命之弓":
        return DeadlyBowSkill()
    if spec.get("code") == "excel_r029" and name == "武装复制":
        return WeaponCopySkill()
    if spec.get("code") == "excel_r029" and name == "无限":
        return InfiniteSkill()
    if spec.get("code") == "excel_r029" and name == "无限铠甲":
        return InfiniteArmorSkill()
    if spec.get("code") == "excel_r029" and name == "无限法袍":
        return InfiniteRobeSkill()
    if spec.get("code") == "excel_r030" and name == "核冲":
        return NuclearRushSkill()
    if spec.get("code") == "excel_r031" and name == "风之语":
        return NatsumeWindWordSkill()
    if spec.get("code") == "excel_r031" and name == "风壁":
        return NatsumeWindWallSkill()
    if spec.get("code") == "excel_r031" and name == "驱散":
        return NatsumeDispelSkill()
    if spec.get("code") == "excel_r032" and name == "大独角兽":
        return GreatUnicornSkill()
    if spec.get("code") == "excel_r032" and source == "穿刺（大）":
        return LargePierceSkill()
    if spec.get("code") == "excel_r032" and name == "晨曦圣光":
        return MorningHolyLightSkill()
    if spec.get("code") == "excel_r033" and name == "波导弹":
        return LaoWaveBulletSkill()
    if spec.get("code") == "excel_r033" and name == "法师之手":
        return LaoMageHandSkill()
    if spec.get("code") == "excel_r033" and name == "法师斗篷":
        return MageCloakSkill()
    if spec.get("code") == "excel_r034" and name == "浮游炮*4":
        return FloatingCannonsSkill()
    if spec.get("code") == "excel_r034" and name == "浮游炮狂暴化":
        return FloatingCannonBerserkSkill()
    if spec.get("code") == "excel_r034" and name == "浮游炮掩护":
        return FloatingCannonCoverSkill()
    if spec.get("code") == "excel_r035" and name == "妖刀":
        return DemonBladeSkill()
    if spec.get("code") == "excel_r035" and source == "吸魔（大）":
        return LargeDrainManaSkill()
    if spec.get("code") == "excel_r035" and name == "山神术" and "室王" in source:
        return MountainGodMuroSkill()
    if spec.get("code") == "excel_r035" and name == "遁术":
        return MountainEscapeSkill()
    if spec.get("code") == "excel_r035" and name == "山神术" and "觉醒" in source:
        return MountainAwakeningSkill()
    if spec.get("code") == "excel_r037" and name == "核变":
        return NuclearMutationSkill()
    if spec.get("code") == "excel_r037" and name == "重力场":
        return GravityFieldSkill()
    if spec.get("code") == "excel_r026" and name == "终结":
        return GuardianFinaleSkill()
    if spec.get("code") == "excel_r093" and source == "穿刺（大）":
        return LargePierceSkill()
    if spec.get("code") == "excel_r093" and name == "凯撒神拳":
        return KaiserFistSkill()
    if spec.get("code") == "excel_r071" and name == "雪崩":
        return SnowAvalancheSkill()
    if spec.get("code") == "excel_r071" and name == "大雪崩":
        return BigAvalancheSkill()
    if spec.get("code") == "excel_r158" and name == "魔界武神之印":
        return MartialGodSealSkill()
    if spec.get("code") == "excel_r158" and name == "地狱之斩":
        return HellSlashSkill()
    if spec.get("code") == "excel_r337" and name == "回复":
        return HealSkill()
    if spec.get("code") == "excel_r337" and name == "湿地草原":
        return WeatherUltimateSkill("wetland_grassland", "湿地草原", marker="湿")
    if spec.get("code") == "excel_r187" and name == "万魔殿":
        return PandemoniumSkill()
    if spec.get("code") == "excel_r113" and name == "净化":
        return PurifyManaSkill()
    if spec.get("code") == "excel_r113" and name == "神圣决斗":
        return SacredDuelSkill()
    if spec.get("code") == "excel_r139" and name == "圣墙":
        return HolyWallSkill()
    if spec.get("code") == "excel_r139" and name == "照明之光":
        return IlluminationLightSkill()
    if spec.get("code") == "excel_r136" and name == "真刀":
        return TrueBladeAirSlashSkill()
    if spec.get("code") == "excel_r136" and name == "凝神":
        return MeditateManaSkill()
    if spec.get("code") == "excel_r047" and name == "百鸟葬":
        return HundredBirdBurialSkill()
    if spec.get("code") == "excel_r137" and name == "吞噬":
        return DevourSkill()
    if spec.get("code") == "excel_r166" and name == "电风":
        return ElectricWindSkill()
    if spec.get("code") == "excel_r188" and name == "天使的气息":
        return SkySanctuarySkill()
    if spec.get("code") == "excel_r188" and name == "元气爆破":
        return VitalityBlastSkill()
    if spec.get("code") == "excel_r036" and name == "治疗":
        return PunisherHealSkill()
    if spec.get("code") == "excel_r036" and name == "圣殿放逐":
        return SanctuaryBanishSkill()
    if spec.get("code") == "excel_r036" and name == "制裁":
        return SanctuaryJudgmentSkill()
    if spec.get("code") == "excel_r056" and name == "混沌":
        return RemiChaosSkill()
    if spec.get("code") == "excel_r056" and name == "蝙蝠":
        return RemiBatSkill()
    if spec.get("code") == "excel_r379" and name == "斩技":
        return SunSlashSkill()
    if spec.get("code") == "excel_r023" and name == "快闪":
        return FreyQuickFlashSkill()
    if spec.get("code") == "excel_r023" and name == "神刺":
        return FreyGodStabSkill()
    if spec.get("code") == "excel_r023" and name == "狮子神枪":
        return FreyLionSpearSkill()
    if spec.get("code") == "excel_r118" and name == "冲刺":
        return ZeroDashSkill()
    if spec.get("code") == "excel_r123" and name == "追身":
        return FumaPursuitSkill()
    if spec.get("code") == "excel_r123" and name == "陷阱":
        return FumaTrapSkill()
    if spec.get("code") == "excel_r123" and name == "风魔手里剑":
        return FumaShurikenSkill()
    if spec.get("code") == "excel_r059" and name == "龙息":
        return NianLargeDragonBreathSkill()
    if spec.get("code") == "excel_r059" and name == "龙舞":
        return NianDragonDanceSkill()
    if spec.get("code") == "excel_r059" and name == "灵压":
        return NianSpiritPressureSkill()
    if spec.get("code") == "excel_r059" and name == "怒吼":
        return NianRoarSkill()
    if spec.get("code") == "excel_r059" and name == "碧玉闪光":
        return NianJadeFlashSkill()
    if spec.get("code") == "excel_r066" and name == "猫手":
        return BlackCatPawSkill()
    if spec.get("code") == "excel_r066" and name == "化猫":
        return BlackCatFormSkill()
    if spec.get("code") == "excel_r127" and name == "幻想":
        return FantasyMoveSkill()
    if spec.get("code") == "excel_r127" and name == "彩虹镜":
        return RainbowMirrorSkill()
    if spec.get("code") == "excel_r127" and name == "友好镜":
        return FriendlyMirrorSkill()
    if spec.get("code") == "excel_r070" and name == "天罚":
        return HeavenPunishmentSkill()
    if spec.get("code") == "excel_r094" and name == "干扰":
        return InterferenceSkill()
    if spec.get("code") == "excel_r094" and name == "乱音电波":
        return NoiseWaveSkill()
    if spec.get("code") == "excel_r326" and name == "虚荣巨影":
        return VainGiantShadowSkill()
    return None


def _common_skill_factory(fragment: dict[str, Any]) -> Skill | None:
    if not bool(fragment.get("common")):
        return None
    name = _text(fragment.get("name"))
    source = _text(fragment.get("fragment"))
    if name == "光墙":
        return LightWallSkill()
    if name == "魔墙":
        return MagicWallSkill()
    if name == "石墙":
        return StoneWallSkill()
    if name == "保护":
        return PassiveProtectionSkill()
    if name == "回避":
        return PassiveEvasionSkill()
    if name == "神速":
        return ShensuSkill()
    if name in {"变硬", "硬化"}:
        return HardenSkill()
    if name == "飞跃":
        return DashMoveSkill(
            "fly_leap",
            "飞跃",
            "普通技能：费 1 魔，每回合最多 1 次，直线飞行移动恰好 4 格。",
            max_distance=4,
            exact_distance=4,
            mana_cost=1,
            max_uses_per_turn=1,
            straight_only=True,
            ignore_units=True,
        )
    if name == "穿刺":
        return PierceSkill()
    if name == "远程穿刺":
        return RemotePierceSkill()
    if name == "震开":
        return KnockbackSkill()
    if name == "机枪":
        return MachineGunSkill()
    if name in {"吸魔", "吸魔（通用）"}:
        return DrainManaSkill()
    if name == "回魔":
        return RecoverManaSkill()
    if name == "魔盾":
        return MagicShieldSkill()
    if name == "分身":
        return SplitSkill()
    if name in {"链条", "锁链"}:
        return ChainPullSkill()
    if name == "龙息":
        return DragonBreathSkill()
    if name == "远程龙息":
        return RemoteDragonBreathSkill()
    if name in {"守*2", "守＊2"}:
        return DefendTwiceSkill()
    if name in {"回血", "治疗"}:
        return HealSkill()
    if name == "洗礼":
        return BaptismSkill()
    if name == "吟唱":
        return ChantSkill()
    if name == "隐身":
        return StealthSkill()
    if name == "撤步射击":
        return BackstepShotSkill()
    if source in {"导弹", "离子盾", "激光"}:
        if source == "导弹":
            return MissileSkill()
        if source == "离子盾":
            return IonShieldSkill()
        return LaserSkill()
    return None


def _common_trait_factory(fragment: dict[str, Any]) -> Trait | None:
    if not bool(fragment.get("common")):
        return None
    text = _text(fragment.get("fragment")).replace(" ", "")
    if text == "飞行":
        return FlyingTrait()
    if text == "可穿人":
        return PassThroughMovementTrait()
    if text == "物免":
        return BasicAttackImmunityTrait()
    if text == "魔免":
        return PermanentMagicImmunityTrait()
    if text in {"攻击吸血", "普攻吸血"}:
        return AttackLifeStealTrait()
    if text in {"攻击吸魔", "普攻吸魔"}:
        return AttackManaDrainTrait()
    if text == "弧形攻击":
        return ArcAttackTrait()
    if text in {"可格挡反击", "可格挡，反击", "格挡反击"}:
        return BlockCounterTrait()
    if text == "自然回魔":
        return NaturalManaRecoveryTrait()
    if text == "自然回血":
        return NaturalHealTrait()
    if text == "自然回复":
        return NaturalRecoveryTrait()
    if text == "原地回魔":
        return StationaryManaRecoveryTrait()
    if text == "原地回血":
        return StationaryHealTrait()
    if text == "原地回复":
        return StationaryRecoveryTrait()
    if text in {"攻击半破魔", "普攻半破魔"}:
        return HalfPierceAttackTrait()
    if text == "普攻破魔":
        return BasicAttackPierceTrait()
    match = re.fullmatch(r"(?:攻击|攻)([二两三四五六七八九十\d]+)次", text)
    if match:
        attacks = _parse_small_int(match.group(1))
        if attacks is not None:
            return AttackCountTrait(attacks)
    return None


def _special_trait_factory(spec: dict[str, Any], fragment: dict[str, Any]) -> Trait | None:
    text = _text(fragment.get("fragment"))
    if spec.get("code") == "excel_r020" and "“1”“2”“3”" in text:
        return WorldSeedTrait()
    if spec.get("code") == "excel_r021" and "剩余" in text and "攻击数" in text:
        return PerfectSwordsmanDefenseTrait()
    if spec.get("code") == "excel_r022" and "每回合开始" in text and "D。" in text:
        return DPantherManaTrait()
    if spec.get("code") == "excel_r024" and text == "攻击己方加血":
        return FriedAllyAttackHealTrait()
    if spec.get("code") == "excel_r024" and "周围的非武将己方单位" in text:
        return FriedAuraTrait()
    if spec.get("code") == "excel_r025" and text == "无法被吸魔":
        return ManaDrainImmunityTrait()
    if spec.get("code") == "excel_r025" and "被动技能" in text and "没能造成伤害" in text:
        return AgencyPassivePunishTrait()
    if spec.get("code") == "excel_r027" and "被此单位普攻击中" in text:
        return WuchangAttackSealTrait()
    if spec.get("code") == "excel_r028" and text == "不受到破魔效果影响":
        return PierceImmunityTrait()
    if spec.get("code") == "excel_r028" and text == "每破坏一个武将速+1":
        return FeiWangSpeedOnHeroKillTrait()
    if spec.get("code") == "excel_r029" and "最多放置5" in text and "魔力点" in text:
        return MagicPointCapTrait(5)
    if spec.get("code") == "excel_r030" and text == "攻击一周":
        return FusionCircleAttackTrait()
    if spec.get("code") == "excel_r030" and "最多被放置6" in text and "魔力点" in text:
        return MagicPointCapTrait(6)
    if spec.get("code") == "excel_r030" and "被破坏之后" in text and "半破魔伤害" in text:
        return FusionDeathExplosionTrait()
    if spec.get("code") == "excel_r031" and "攻击己方加魔" in text and "风壁计数点" in text:
        return NatsumeAllyAttackManaTrait()
    if spec.get("code") == "excel_r032" and (
        "周围7*7" in text
        or "暗属性单位造成的伤害+1" in text
        or "召唤物被破坏" in text
    ):
        return AaronLightAuraTrait()
    if spec.get("code") == "excel_r033" and "魔以外" in text and "伤害不计算" in text:
        return LaoDamageStatCancelTrait()
    if spec.get("code") == "excel_r035" and ("山神计数点" in text):
        return MountainGodCounterTrait()
    if spec.get("code") == "excel_r037" and "范围一格以上" in text:
        return MultiCellAreaDamageGuardTrait()
    if spec.get("code") == "excel_r352" and text == "每次攻击在在周围召唤一个分身":
        return WaterNinjaCloneAfterAttackTrait()
    if spec.get("code") == "excel_r187" and text == "在“万魔殿”中速+3":
        return PandemoniumSpeedTrait()
    if spec.get("code") == "excel_r139" and text == "周围11*11内己方单位每回合血+1/4魔+1":
        return SolaHarvestAuraTrait()
    if spec.get("code") == "excel_r047" and text == "每次攻击后可以移动2格并且直到下回合结束前守+1":
        return JiroboAfterAttackTrait()
    if spec.get("code") == "excel_r137" and text == "此单位半血以上受到致命伤害时，可以剩1/4的血留在场上":
        return UndyingQuarterTrait()
    if spec.get("code") == "excel_r166" and text == "每个己方回合开始时对周围5*5自动使用“电风”":
        return AutoElectricWindTrait()
    if spec.get("code") == "excel_r326" and text == "普攻带有以下破魔效果：吸魔，下回合结束前攻守速-1，到1":
        return FlorenzaAttackFollowupTrait()
    if spec.get("code") == "excel_r036" and text == "周围11*11天气变为“天空圣域”":
        return SkySanctuaryAuraTrait()
    if spec.get("code") == "excel_r056" and text == "吸血":
        return AttackLifeStealTrait()
    if spec.get("code") == "excel_r056" and text == "此单位当血量为0时不会被破坏，而是剩1/4，魔-1，因为此效果魔变为0时破坏":
        return RemiUndyingTrait()
    if spec.get("code") == "excel_r056" and text == "普攻吸魔":
        return AttackManaDrainTrait()
    if spec.get("code") == "excel_r379" and text == "此单位在被破坏后，场上其他的己方单位每个进攻回合额外增加一次伤4的普攻":
        return KikuAfterDeathTrait()
    if spec.get("code") == "excel_r023" and text == "所有技能破魔":
        return FreySkillPierceTrait()
    if spec.get("code") == "excel_r023" and text == "每次受到伤害最多-1/4血":
        return FreyDamageCapTrait()
    if spec.get("code") == "excel_r118" and text == "穿人有伤害":
        return ZeroPassThroughTrait()
    if spec.get("code") == "excel_r123" and text == "每次使用主动技能都有1/2几率魔+1":
        return FumaSkillManaTrait()
    return None


def _parse_small_int(text: str) -> int | None:
    value = _text(text)
    if value.isdigit():
        return int(value)
    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    return mapping.get(value)


def _footprint_size(spec: dict[str, Any]) -> tuple[int, int]:
    fragments = [_text(item.get("fragment")) for item in spec.get("trait_fragments", [])]
    text = "；".join(fragments)
    match = re.search(r"占\s*(\d+)\s*\*\s*(\d+)", text)
    if match:
        return max(1, int(match.group(1))), max(1, int(match.group(2)))
    if re.search(r"占\s*(?:4|四)格", text):
        return 2, 2
    if re.search(r"占\s*(?:9|九)格", text):
        return 3, 3
    return 1, 1


def _make_build_skills(spec: dict[str, Any]) -> Callable[[AbstractHero], list[Skill]]:
    def build_skills(self: AbstractHero) -> list[Skill]:
        skills: list[Skill] = []
        seen_codes: set[str] = set()
        for fragment in spec.get("skill_fragments", []):
            skill = _special_skill_factory(spec, fragment) or _common_skill_factory(fragment)
            if skill is None or skill.code in seen_codes:
                continue
            seen_codes.add(skill.code)
            skills.append(skill)
        if spec.get("code") == "excel_r047" and "jirobo_follow_step" not in seen_codes:
            skills.append(JiroboFollowStepSkill())
        if spec.get("code") == "excel_r025" and "agency_borrowed_skill" not in seen_codes:
            skills.append(AgencyBorrowedSkill())
        return skills

    return build_skills


def _make_build_traits(spec: dict[str, Any]) -> Callable[[AbstractHero], list[Trait]]:
    def build_traits(self: AbstractHero) -> list[Trait]:
        traits: list[Trait] = []
        seen_names: set[str] = set()
        for fragment in spec.get("trait_fragments", []):
            trait = _special_trait_factory(spec, fragment) or _common_trait_factory(fragment)
            if trait is None or trait.name in seen_names:
                continue
            seen_names.add(trait.name)
            traits.append(trait)
        if spec.get("code") == "excel_r021" and "拔刀斩攻击" not in seen_names:
            traits.append(IaidoAttackTrait())
            seen_names.add("拔刀斩攻击")
        if spec.get("code") == "excel_r020" and "世界之种连根" not in seen_names:
            traits.append(WorldSeedTrait())
            seen_names.add("世界之种连根")
        if spec.get("code") == "excel_r032" and "骑士开场坐骑" not in seen_names:
            traits.append(AaronMountedStartTrait())
            seen_names.add("骑士开场坐骑")
        if spec.get("code") == "excel_r034" and "浮游炮回补" not in seen_names:
            traits.append(SakuraFloatingCannonTrait())
            seen_names.add("浮游炮回补")
        return traits

    return build_traits


def _hero_class_from_spec(spec: dict[str, Any]) -> type[AbstractHero]:
    width, height = _footprint_size(spec)
    attrs: dict[str, Any] = {
        "hero_code": spec["code"],
        "hero_name": spec["name"],
        "role": spec["role"],
        "attribute": spec["attribute"],
        "race": spec["race"],
        "level": int(spec["level"]),
        "base_stats": Stats(
            attack=float(spec["attack"]),
            defense=float(spec["defense"]),
            speed=float(spec["speed"]),
            attack_range=float(spec["range"]),
            mana=float(spec["mana"]),
        ),
        "raw_skill_text": spec["raw_skill_text"],
        "raw_trait_text": spec["raw_trait_text"],
        "source_row": int(spec["source_row"]),
        "build_skills": _make_build_skills(spec),
        "build_traits": _make_build_traits(spec),
    }
    if width > 1 or height > 1:
        attrs["footprint_width"] = width
        attrs["footprint_height"] = height
        attrs["entry_footprint_width"] = width
        attrs["entry_footprint_height"] = height
    class_name = f"ExcelHero{int(spec['source_row']):03d}_{_slug_tail(int(spec['source_row']), spec['code'])}"
    return type(class_name, (AbstractHero,), attrs)


IMPLEMENTED_EXCEL_HERO_CODES: frozenset[str] = frozenset(
    {
        "excel_r020",
        "excel_r021",
        "excel_r022",
        "excel_r023",
        "excel_r024",
        "excel_r025",
        "excel_r026",
        "excel_r027",
        "excel_r028",
        "excel_r029",
        "excel_r030",
        "excel_r031",
        "excel_r032",
        "excel_r033",
        "excel_r034",
        "excel_r035",
        "excel_r036",
        "excel_r037",
        "excel_r047",
        "excel_r056",
        "excel_r059",
        "excel_r066",
        "excel_r070",
        "excel_r071",
        "excel_r093",
        "excel_r094",
        "excel_r113",
        "excel_r118",
        "excel_r123",
        "excel_r127",
        "excel_r136",
        "excel_r137",
        "excel_r139",
        "excel_r158",
        "excel_r166",
        "excel_r187",
        "excel_r188",
        "excel_r326",
        "excel_r337",
        "excel_r352",
        "excel_r379",
    }
)


EXCEL_HERO_REGISTRY: dict[str, HeroFactory] = {
    spec["code"]: _hero_class_from_spec(spec)
    for spec in EXCEL_HERO_SPECS
}

EXCEL_HERO_NAMES_BY_CODE: dict[str, str] = {
    spec["code"]: spec["name"]
    for spec in EXCEL_HERO_SPECS
}
