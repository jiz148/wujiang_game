from __future__ import annotations

import re
import random
from contextlib import contextmanager
from itertools import permutations
from typing import Any, Callable

from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import (
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
    TemporaryDefenseStatus,
    Trait,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import (
    AttackCountTrait,
    BackstepShotSkill,
    BaptismSkill,
    BlockCounterTrait,
    ChantSkill,
    DashMoveSkill,
    DefendTwiceSkill,
    DrainManaSkill,
    FlagStatus,
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
from wujiang.tactical.heroes.excel_roster_data import EXCEL_HERO_SPECS
from wujiang.tactical.heroes.next_five import (
    ArcAttackTrait,
    DeclaredAreaSkillMixin,
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
    QuantumShieldSkill,
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

    def blocks_direct_effects(self) -> bool:
        return True

    def accepts_status(self, status: StatusEffect) -> bool:
        return False

    def permits_immune_heal(self, ctx: HealContext) -> bool:
        return ctx.effect_source is self and ctx.source is self.owner and ctx.target is self.owner

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
        if not ctx.damage_target and "damage" not in ctx.tags and "attack" not in ctx.tags:
            ctx.cancelled = True
            ctx.reason = f"{owner.name} 的【终结】免疫伤害以外的效果。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._is_owner_basic_attack(ctx):
            ctx.ignore_shield = True

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.cancelled or ctx.actual_damage <= 0 or not owner.alive:
            return
        battle.heal(HealContext(source=owner, target=owner, amount=0.25, action_name="终结吸血", effect_source=self))

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if self.permits_immune_heal(ctx):
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
        super().__init__("风壁", "挡住下一次敌方普攻或技能的整次伤害和附效，包括破魔；不挡独立场地伤害。", duration=None)
        self.blocked_token = None

    def block(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        if (owner is not ctx.target or source is None or source.player_id == owner.player_id
                or ctx.from_field_effect or not (ctx.is_skill or "attack" in ctx.tags)):
            return
        token = battle.current_action_resolution_token
        if self.blocked_token is not None and self.blocked_token != token:
            return
        if ctx.cancelled:
            return
        first_block = self.blocked_token is None
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【风壁】挡住了【{ctx.action_name}】。"
        if token is None:
            owner.remove_status(self, battle)
        else:
            self.blocked_token = token
            self.expires_after_action_token = token
        if first_block:
            battle.emit_defense_visual_event(source=source, target=owner, action_name=ctx.action_name, defense_reason="block")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        self.block(battle, ctx)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self.block(battle, ctx)


def natsume_effect_snapshot(unit):
    return (unit.alive, unit.banished, unit.position, unit.current_hp, unit.current_mana, unit.mana_points,
            unit.cannot_move, unit.cannot_normal_move, unit.cannot_attack, unit.cannot_use_skills,
            unit.total_shields(), tuple((status.name, status.duration) for status in unit.statuses if status.name != "风壁"),
            tuple(unit.stat(key) for key in ("attack", "defense", "speed", "attack_range")))


def natsume_protection_changes_outcome(battle, queued_action, target):
    from copy import deepcopy
    from dataclasses import replace
    from wujiang.tactical.engine.core import battle_state_rollback
    outcomes = []
    for protected in (False, True):
        with battle_state_rollback(battle):
            if protected:
                target.add_status(WindWallBlockStatus())
            probe = replace(queued_action, payload=deepcopy(queued_action.payload))
            try:
                battle.resolve_queued_action(probe)
            except ActionError:
                return False
            outcomes.append(natsume_effect_snapshot(target))
    return outcomes[0] != outcomes[1]


class NatsumeWindWordSkill(Skill):
    def __init__(self) -> None:
        super().__init__("natsume_wind_word", "风之语", "普通技能：每回合1次、0魔；范内直线点单位，破魔，只回复1/4生命，再尽量沿直线拉到自身邻圈合法格。", max_uses_per_turn=1, target_mode="unit")

    def ignores_shield_for_payload(self, battle, actor, payload):
        return True

    def targets(self, battle, actor):
        return [unit for unit in battle.all_units() if battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def _pull_destination(self, battle, actor, target):
        if actor.position is None or target.position is None or actor is target:
            return None
        own_cells, body = battle.unit_cells(actor), battle.unit_cells(target)
        if any(a.distance_to(b) <= 1 for a in own_cells for b in body):
            return target.position
        candidates = set()
        for a in own_cells:
            for b in body:
                dx, dy = a.x - b.x, a.y - b.y
                if dx and dy and abs(dx) != abs(dy):
                    continue
                sx, sy = (0 if dx == 0 else (1 if dx > 0 else -1)), (0 if dy == 0 else (1 if dy > 0 else -1))
                for step in range(1, max(abs(dx), abs(dy))):
                    dest = target.position.offset(sx * step, sy * step)
                    placed = target.footprint_cells_at(dest)
                    if set(placed).isdisjoint(own_cells) and min(x.distance_to(y) for x in placed for y in own_cells) == 1:
                        candidates.add(dest)
        declared = actor.position
        actor.position = getattr(actor, "_resolution_actual_position", None) or declared
        try:
            return next((cell for cell in sorted(candidates, key=lambda cell: (cell.distance_to(target.position), cell.y, cell.x))
                         if not any(battle.is_forced_movement_blocked(part) for part in target.footprint_cells_at(cell))
                         and battle.can_place_unit(target, cell, ignore=target, mover=target)), None)
        finally:
            actor.position = declared

    def execute(self, battle, actor, payload):
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        if not payload.get("queued_resolution"):
            battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range(), action_name=self.name)
        ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                     is_hostile=actor.is_enemy_of(target), ignore_shield=True,
                                     tags={"skill", "natsume_wind_word"})
        if ctx.cancelled or target.direct_effects_blocked():
            return
        if actor.is_enemy_of(target) and target.total_shields() > 0 and ctx.ignore_shield:
            target.consume_one_shield()
            battle.record_shield_break_summary(actor, target, self.name)
        battle.heal(HealContext(source=actor, target=target, amount=0.25, action_name=self.name, tags={"skill"}))
        if not target.alive or target.position is None:
            return
        destination = self._pull_destination(battle, actor, target)
        if destination is not None and destination != target.position:
            try:
                battle.move_unit(target, destination, via_skill=True, forced=True, allow_anywhere=True, tags={"natsume_wind_word"})
            except ActionError:
                battle.log(f"【风之语】已完成治疗判定，目标无法被拉近。")

    def preview(self, battle, actor):
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


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

    def _selectable_targets(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> list[HeroUnit]:
        if queued_action.action_type not in {"attack", "skill", "skill_effect"}:
            return []
        threatened = battle.effect_units_at_cells(queued_action.target_cells)
        if not threatened:
            threatened = battle.effect_units([battle.units[uid] for uid in queued_action.target_unit_ids if uid in battle.units])
        result = []
        for unit in threatened:
            if (unit.player_id != actor.player_id or not battle.unit_can_be_selected(unit, actor=actor)[0]
                    or unit.direct_effects_blocked() or unit.has_status("风壁")
                    or battle.shield_auto_blocks_chain(unit, queued_action)):
                continue
            if not (unit is battle.effect_recipient(actor) or unit.has_status("风壁计数点")
                    or battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())):
                continue
            if natsume_protection_changes_outcome(battle, queued_action, unit):
                result.append(unit)
        return result

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
        target = battle.effect_recipient(targets[0])
        wall = WindWallBlockStatus()
        target.add_status(wall, source=actor)
        if wall not in target.statuses:
            return
        counter = target.get_status("风壁计数点")
        if counter is not None:
            target.remove_status(counter, battle)
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
        super().__init__("natsume_dispel", "驱散", "普通技能：每回合1次、0魔；固定声明周围11×11，双方召唤物/分身破坏，隐身解除；每个实际受影响受体回复1魔。", max_uses_per_turn=1, target_mode="self")

    def affected_cells(self, battle, actor):
        return square_around_cells(battle, battle.unit_cells(actor), radius=5)

    def affected_units(self, battle, actor):
        return battle.effect_units_at_cells(self.affected_cells(battle, actor), ignore=actor)

    def has_purge_effect(self, unit):
        return unit.is_summon or unit.is_clone or any(status.name == "隐身" for status in unit.statuses)

    def queued_payload_metadata(self, battle, actor, payload):
        return {"natsume_dispel_cells": positions_to_dict(self.affected_cells(battle, actor))}

    def get_target_cells_for_payload(self, battle, actor, payload):
        return self.affected_cells(battle, actor)

    def target_can_react_to_effect(self, battle, actor, target, payload):
        if not self.has_purge_effect(target) or target.direct_effects_blocked():
            return False
        from wujiang.tactical.engine.core import battle_state_rollback
        with battle_state_rollback(battle):
            ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True, is_hostile=True,
                                         ignore_targeting_restrictions=True, tags={"skill", "natsume_dispel"})
            return not ctx.cancelled or ctx.destroyed_as_clone

    def execute(self, battle, actor, payload):
        cells = battle.payload_positions(payload, "natsume_dispel_cells") if payload.get("queued_resolution") else self.affected_cells(battle, actor)
        affected_count = 0
        for unit in list(battle.effect_units_at_cells(cells, ignore=actor)):
            if not unit.alive or not self.has_purge_effect(unit) or unit.direct_effects_blocked():
                continue
            ctx = battle.validate_target(actor, unit, action_name=self.name, is_skill=True, is_hostile=True,
                                         ignore_targeting_restrictions=True, tags={"skill", "natsume_dispel"})
            if ctx.cancelled:
                if ctx.destroyed_as_clone:
                    affected_count += 1
                continue
            changed = False
            if unit.is_summon or unit.is_clone:
                unit.current_hp, unit.alive = 0, False
                changed = True
                battle.log_public_event(f"{unit.name} 被【驱散】破坏。", source=actor, target=unit)
            for status in list(unit.statuses):
                if status.name == "隐身":
                    unit.remove_status(status, battle)
                    changed = True
            if changed:
                affected_count += 1
        battle.cleanup_dead_units()
        gained = actor.gain_mana(affected_count) if actor.alive and actor.unit_id in battle.units else 0
        battle.log(f"【驱散】实际影响 {affected_count} 个单位，回复 {gained:g} 魔。")

    def preview(self, battle, actor):
        return {"cells": positions_to_dict(self.affected_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": False}


class NatsumeAllyAttackManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("风壁赠予", "普攻己方单位时不造成伤害；目标魔 +1，并获得一个风壁计数点。")

    def resolve_allied_basic_support(self, battle, actor, target):
        if self.owner is not actor or target.player_id != actor.player_id:
            return False
        if not target.direct_effects_blocked():
            gained = target.gain_mana(1)
            if not target.has_status("风壁计数点"):
                mark = WindWallCounterStatus()
                mark.is_skill_effect = False
                target.add_status(mark, source=actor)
            battle.log(f"{actor.name} 支援 {target.name}：回复 {gained:g} 魔，风壁计数点{'已就绪' if target.has_status('风壁计数点') else '未能施加'}。")
        return True

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
        metadata = {"allow_allied_attack_target": True}
        target = battle.units.get(str((payload or {}).get("target_unit_id") or ""))
        if target is not None and target.player_id == actor.player_id:
            metadata["attack_effect_summary"] = "对原声明格的己方受体回复1魔并给予一个风壁计数点；不造成伤害或消耗护盾。"
        return metadata



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
        return [GreatUnicornRideableTrait(), BasicAttackPierceTrait()]


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
        if owner is None or owner.alive or not getattr(owner, "destruction_count", 0) or not owner.mount_owner_id:
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
        if owner is not None and actor.unit_id == owner.unit_id:
            return 0.0
        return cost


class AaronDestroyedSummonPendingStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("独角兽遗辉待生效", "自己的召唤物被破坏，下个本人回合获得独角兽遗辉。", duration=None)


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
                damage_target=True,
            )
            if target_ctx.cancelled:
                if target_ctx.reason:
                    battle.log_public_event(target_ctx.reason, source=actor, target=target)
                continue
            existing = target.get_status("被动封锁")
            if existing is None:
                target.add_status(PassiveSkillLockStatus(duration=2))
                battle.log(f"{target.name} 被【晨曦圣光】封锁被动技能。")
            else:
                existing.duration = 2
            if target.attribute == "暗":
                damage_ctx = battle.resolve_damage(
                    DamageContext(
                        source=actor,
                        target=target,
                        attack_power=5,
                        is_skill=True,
                        action_name=self.name,
                        ignore_shield=True,
                        cannot_evade=True,
                        tags={"skill", "morning_holy_light"},
                    )
                )
                if is_hostile and not damage_ctx.shield_consumed and target.total_shields() > 0:
                    target.consume_one_shield()
                    battle.record_shield_break_summary(actor, target, self.name)
                    battle.log_public_event(f"{target.name} 的 1 层护盾被【{self.name}】贯穿并打碎。",
                                            source=actor, target=target)
            elif is_hostile and target.total_shields() > 0:
                target.consume_one_shield()
                battle.record_shield_break_summary(actor, target, self.name)
                battle.log_public_event(f"{target.name} 的 1 层护盾被【{self.name}】贯穿并打碎。",
                                        source=actor, target=target)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class AaronLightAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("晨曦光环", "自身周围 7*7 单位在亚伦己方回合开始时血 +1/4；亚伦及其召唤物对暗属性单位伤害 +1。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None:
            return
        pending = owner.get_status("独角兽遗辉待生效")
        if pending is not None:
            owner.remove_status(pending, battle)
            owner.current_hp = owner.max_health
            owner.current_mana = owner.max_mana()
            old_boost = owner.get_status("独角兽遗辉")
            if old_boost is not None:
                owner.remove_status(old_boost, battle)
            owner.add_status(AaronDestroyedSummonBoostStatus())
            battle.log(f"{owner.name} 在本回合获得【独角兽遗辉】：攻守速 +2、血魔补满、使用技能不费魔。")
        cells = square_around_cells(battle, battle.unit_cells(owner), radius=3)
        for unit in battle.effect_units_at_cells(cells):
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
        if ctx.raw_damage is None:
            ctx.attack_power += 1

    def on_owned_summon_destroyed(self, battle: Battle, summon: HeroUnit) -> None:
        owner = self.owner
        if owner is None or not owner.alive or summon.summoner_id != owner.unit_id:
            return
        if owner.get_status("独角兽遗辉待生效") is None:
            owner.add_status(AaronDestroyedSummonPendingStatus())
            battle.log(f"{owner.name} 的召唤物被破坏，下个本人回合将获得【独角兽遗辉】。")


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

    def _free(self, payload: dict[str, Any] | None) -> bool:
        return bool(payload and (payload.get("free_cast") is True or payload.get("choice_code") == "free"))

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        if self._free(payload):
            return 0.0
        return super().mana_cost_for_payload(battle, actor, payload)

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        if payload and "free_cast" in payload and type(payload["free_cast"]) is not bool:
            return False, "波导弹的免费模式必须明确选择。"
        if payload and payload.get("choice_code") not in {None, "paid", "free"}:
            return False, "请选择波导弹的付费或免费模式。"
        return super().can_use(battle, actor, payload)

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
        attack_power = actor.stat("attack") - (1 if self._free(payload) else 0)
        for target in battle.effect_units_at_cells(cells):
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
        selectable = preview["selection"]["patterns"]
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                        "selection": {"mode": "choice_pattern", "ordered": False,
                                      "choices": [{"code": "paid", "label": "费1魔：完整攻值", "patterns": selectable},
                                                  {"code": "free", "label": "免费：攻值-1", "patterns": selectable}],
                                      "choice_prompt": "选择波导弹费用模式。",
                                      "cell_prompt": "逐格选择4×4结算区域。"}})
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
        if (dx, dy) not in ALL_DIRECTIONS:
            raise ActionError("请选择法师之手推动方向。")
        return dx, dy

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
                is_skill=False,
                ignore_shield=True,
                action_name="法师之手",
                tags={"skill", "attack", "basic_attack_damage", "lao_mage_hand"},
            )
        )
        if not damage_followup_effect_applies(ctx):
            return
        effect_ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                            is_hostile=True, ignore_shield=True,
                                            tags={"skill", "lao_mage_hand"})
        if effect_ctx.cancelled:
            return
        destination = target.position
        if destination is None:
            return
        push_steps: list[Position] = []
        for _ in range(3):
            next_cell = Position(destination.x + dx, destination.y + dy)
            if not battle.in_bounds(next_cell) or not battle.can_place_unit(target, next_cell, ignore=target, mover=target):
                break
            destination = next_cell
            push_steps.append(next_cell)
        if push_steps:
            battle.move_unit(target, destination, via_skill=True, forced=True, max_distance=3,
                             straight_only=True, path=push_steps, tags={"lao_mage_hand"})

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
                "directions": [{"dx": dx, "dy": dy,
                                "label": {(-1, -1): "↖", (-1, 0): "←", (-1, 1): "↙",
                                          (0, -1): "↑", (0, 1): "↓", (1, -1): "↗",
                                          (1, 0): "→", (1, 1): "↘"}[(dx, dy)]}
                               for dx, dy in ALL_DIRECTIONS],
            },
        }

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class MageCloakEquippedStatus(StatModifierStatus):
    def __init__(self, summoner_id: str, cloak_unit_id: str) -> None:
        super().__init__("法师斗篷", speed_delta=3, defense_delta=1, description="速 +3，守 +1，移动次数 +1，飞行，近战普攻伤害 +1。")
        self.summoner_id = summoner_id
        self.cloak_unit_id = cloak_unit_id
        self.detaching = False

    def bind(self, owner: HeroUnit) -> "MageCloakEquippedStatus":
        super().bind(owner)
        owner.ignore_units_while_moving = True
        owner.has_flying = True
        owner.skills.append(MageCloakDetachSkill().bind(owner))
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
        owner.skills = [skill for skill in owner.skills if skill.code != "detach_mage_cloak"]
        owner.has_flying = any(isinstance(component, FlyingTrait) for component in owner.traits) or any(
            isinstance(status, MageCloakEquippedStatus) for status in owner.statuses
        )
        owner.ignore_units_while_moving = any(isinstance(component, PassThroughMovementTrait) for component in owner.traits) or owner.has_flying
        if not self.detaching:
            cloak = battle.units.get(self.cloak_unit_id)
            if cloak is not None and cloak.alive and getattr(cloak, "equipped_to_id", None) == owner.unit_id:
                cloak.alive = False
                cloak.position = owner.position or getattr(owner, "last_position", None)
                battle.log(f"{cloak.name} 随装备状态结束而破坏。")
                battle.capture_destruction_position(cloak)
                if cloak.unit_id not in {unit.unit_id for unit in battle.destroyed_units}:
                    battle.destroyed_units.append(cloak)
                battle.remove_unit(cloak)

    def on_owner_removed(self, battle: Battle) -> None:
        self.on_removed(battle)

    def sync_linked_state(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or self not in owner.statuses:
            return
        cloak = battle.units.get(self.cloak_unit_id)
        summoner = battle.units.get(self.summoner_id)
        if (not owner.alive or cloak is None or not cloak.alive or summoner is None or not summoner.alive):
            owner.remove_status(self, battle)


class MageCloakSummonedStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("法师斗篷已召唤", "已经使用过法师斗篷大招。", duration=None)


class MageCloakEquipSkill(Skill):
    def __init__(self) -> None:
        super().__init__("equip_mage_cloak", "装备斗篷", "装备到范内一个法师单位上，斗篷保留原身份但暂不占格。", target_mode="unit")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.summoner_id is None:
            return False, "法师斗篷没有召唤者。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target.role != "法师":
            raise ActionError("法师斗篷只能装备到法师单位。")
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range(), action_name=self.name)
        if target.get_status("法师斗篷") is not None:
            raise ActionError("该单位已经装备法师斗篷。")
        status = MageCloakEquippedStatus(str(actor.summoner_id), actor.unit_id)
        target.add_status(status, source=actor)
        if status not in target.statuses:
            battle.log(f"{target.name} 未受到【法师斗篷】装备效果。")
            return
        actor.equipped_to_id = target.unit_id
        actor.position = None
        battle.log(f"{actor.name} 装备到 {target.name} 身上。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.position is not None
            and unit.role == "法师"
            and unit.get_status("法师斗篷") is None
            and battle.unit_can_be_selected(unit, actor=actor)[0]
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class MageCloakDetachSkill(Skill):
    def __init__(self) -> None:
        super().__init__("detach_mage_cloak", "解除法师斗篷", "由装备者选择周围完整合法空格，原斗篷满血重新占格。", target_mode="cell")

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        status = actor.get_status("法师斗篷")
        if not isinstance(status, MageCloakEquippedStatus):
            return []
        cloak = battle.units.get(status.cloak_unit_id)
        if cloak is None or not cloak.alive or getattr(cloak, "equipped_to_id", None) != actor.unit_id:
            return []
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if battle.can_place_unit(cloak, cell, ignore=cloak, mover=cloak)]

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        return (True, "") if self.available_cells(battle, actor) else (False, "装备者周围没有斗篷可落的合法空格。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            raise ActionError("请选择装备者周围的合法空格解除斗篷。")
        status = actor.get_status("法师斗篷")
        assert isinstance(status, MageCloakEquippedStatus)
        cloak = battle.units[status.cloak_unit_id]
        status.detaching = True
        actor.remove_status(status, battle)
        cloak.equipped_to_id = None
        cloak.position = destination
        cloak.current_hp = cloak.max_health
        battle.log(f"{actor.name} 解除【法师斗篷】，原斗篷在周围满血重现。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {"cells": [cell.to_dict() for cell in self.available_cells(battle, actor)],
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


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
        super().__init__("summon_mage_cloak", "法师斗篷", "大招：本场一次，在周围合法格召唤可行动的法师斗篷。", max_uses_per_battle=1, target_mode="cell")

    def own_cloak_alive(self, battle: Battle, actor: HeroUnit) -> HeroUnit | None:
        for unit in battle.player_units(actor.player_id):
            if getattr(unit, "hero_code", "") == "mage_cloak" and unit.summoner_id == actor.unit_id and unit.alive:
                return unit  # type: ignore[return-value]
        return None

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if self.own_cloak_alive(battle, actor) is not None:
            return False, "法师斗篷已经在场。"
        if actor.get_status("法师斗篷已召唤") is not None:
            return False, "法师斗篷大招本场已经使用过。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
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
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if battle.can_place_unit(MageCloakSummon(actor.player_id), cell)]

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class LaoDamageStatReadyStatus(StatusEffect):
    def __init__(self, stat_name: str, declaration_id: str) -> None:
        super().__init__("能力抵消准备", f"若该动作造成实际伤害，永久降低{stat_name}1点并抵消该次伤害。", duration=1)
        self.stat_name = stat_name
        self.expires_after_declaration_id = declaration_id


class LaoDamageStatCancelSkill(Skill):
    STAT_NAMES = {"attack": "攻", "defense": "守", "speed": "速", "attack_range": "范"}

    def __init__(self) -> None:
        super().__init__("lao_damage_stat_cancel", "能力抵消", "连锁：选择攻/守/速/范之一永久-1，最低1；只在当前动作实际造成伤害时支付并抵消该次伤害。", timing="passive", target_mode="self")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if actor not in battle.reaction_affected_units(queued_action):
            return False, "该动作不会影响拉奥。"
        if not any(actor.stat(name) > 1 for name in self.STAT_NAMES):
            return False, "没有可支付的能力值。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        stat_name = str((payload or {}).get("stat_name") or "")
        if stat_name not in self.STAT_NAMES or actor.stat(stat_name) <= 1:
            return False, "请选择当前高于1的攻、守、速或范。"
        return True, ""

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "stat_cells", "required_cells": 0,
                              "prompt": "选择要永久降低1点的能力；若该动作未造成实际伤害，则不会支付。",
                              "stats": [{"code": name, "label": label} for name, label in self.STAT_NAMES.items() if actor.stat(name) > 1]}}

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        actor.add_status(LaoDamageStatReadyStatus(str(payload["stat_name"]), str(queued_action.payload.get("declaration_id") or "")))
        battle.log(f"{actor.name} 准备以【{self.STAT_NAMES[str(payload['stat_name'])]}】抵消该动作的实际伤害。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("能力抵消只能在连锁时选择。")


class LaoDamageStatCancelTrait(Trait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("能力抵消", "受伤前可将攻/守/速/范之一 -1 到最低 1，取消该次伤害。")

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.cancelled:
            return
        ready = next((status for status in owner.statuses if isinstance(status, LaoDamageStatReadyStatus)), None)
        action = battle.resolving_action
        amount = ctx.raw_damage if ctx.raw_damage is not None else battle.damage_rule.calculate_damage(ctx.attack_power, owner.stat("defense"))
        if owner.damage_fraction_after_limits(amount) <= 0:
            return
        if (ready is not None and action is not None
                and ready.expires_after_declaration_id == action.payload.get("declaration_id")
                and owner.stat(ready.stat_name) > 1):
            stat = ready.stat_name
        else:
            available = [name for name in LaoDamageStatCancelSkill.STAT_NAMES if owner.stat(name) > 1]
            stat = battle.damage_stat_choice(ctx, available) if available else None
        if not stat:
            return
        kwargs = {
            "attack": {"attack_delta": -1},
            "defense": {"defense_delta": -1},
            "speed": {"speed_delta": -1},
            "attack_range": {"range_delta": -1},
        }[stat]
        owner.add_status(StatModifierStatus("能力抵消", description=f"{stat} -1。", duration=None, **kwargs))
        if ready is not None and stat == ready.stat_name:
            owner.remove_status(ready, battle)
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 将 {stat} -1，抵消了【{ctx.action_name}】的伤害。"


class FloatingCannonBerserkStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("浮游炮狂暴化", "浮游炮攻 +2，速 +2，范 -2，攻 2 次，且只能攻击最近敌方单位。", duration=None)


class FloatingCannonsActiveStatus(StatusEffect):
    def __init__(self, cannon_ids: list[str]) -> None:
        super().__init__("浮游炮展开", "樱火已展开四炮；真正被破坏的原炮会在樱火下回合开始时按原身份补回。", duration=None)
        self.cannon_ids = list(cannon_ids)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        owner = self.owner
        trait = next((item for item in owner.traits if isinstance(item, SakuraFloatingCannonTrait)), None) if owner is not None else None
        data["pending_cannon_count"] = len(trait.pending_cannons) if trait is not None else 0
        return data


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

    def nearest_targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        visible = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and unit.position is not None and not unit.banished
                   and battle.unit_can_be_selected(unit, actor=actor)[0]]
        if not visible:
            return []
        nearest = min(battle.distance_between_units(actor, unit) for unit in visible)
        return [unit for unit in visible if battle.distance_between_units(actor, unit) == nearest]

    def can_fire_at(self, battle: Battle, actor: HeroUnit, target: HeroUnit, origin: Position) -> bool:
        return any(battle.cells_are_straight_aligned(cell, enemy_cell)
                   and cell.distance_to(enemy_cell) <= actor.targeting_range()
                   for cell in battle.unit_cells_at(actor, origin)
                   for enemy_cell in battle.unit_cells(target))

    def route_to(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> list[Position]:
        if actor.position is None:
            return []
        if self.can_fire_at(battle, actor, target, actor.position):
            return [actor.position]
        max_distance = battle.width * battle.height * 5
        reachable = battle.reachable_positions(actor, max_distance=max_distance, use_movement_cost=True)
        firing = [position for position in reachable if self.can_fire_at(battle, actor, target, position)]
        if firing:
            farthest = max(min(cell.distance_to(enemy_cell) for cell in battle.unit_cells_at(actor, position)
                                for enemy_cell in battle.unit_cells(target)) for position in firing)
            destinations = [position for position in firing
                            if min(cell.distance_to(enemy_cell) for cell in battle.unit_cells_at(actor, position)
                                   for enemy_cell in battle.unit_cells(target)) == farthest]
        else:
            if not reachable:
                return []
            closest = min(min(cell.distance_to(enemy_cell) for cell in battle.unit_cells_at(actor, position)
                              for enemy_cell in battle.unit_cells(target)) for position in reachable)
            if closest >= battle.distance_between_units(actor, target):
                return []
            destinations = [position for position in reachable
                            if min(cell.distance_to(enemy_cell) for cell in battle.unit_cells_at(actor, position)
                                   for enemy_cell in battle.unit_cells(target)) == closest]
        routes = [battle.find_path(actor, position, max_distance=max_distance, use_movement_cost=True)
                  for position in destinations]
        return min(routes, key=lambda route: (sum(battle.normal_movement_step_cost(actor, start, end)
                                                   for start, end in zip(route, route[1:])),
                                               len(route), route[-1].y, route[-1].x))

    def action_route(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[list[Position], bool]:
        route = self.route_to(battle, actor, target)
        if not route:
            return [], False
        if len(route) == 1:
            return route, True
        if (actor.cannot_move or actor.cannot_normal_move
                or actor.normal_move_actions_used >= actor.normal_move_actions_per_turn()):
            return [], False
        budget = actor.remaining_normal_move_distance(battle)
        selected = [route[0]]
        spent = 0
        for start, end in zip(route, route[1:]):
            cost = battle.normal_movement_step_cost(actor, start, end)
            if spent + cost > budget:
                break
            spent += cost
            selected.append(end)
        if len(selected) == 1:
            return [], False
        return selected, len(selected) == len(route) and self.can_fire_at(battle, actor, target, route[-1])

    def forced_normal_move_paths(self, battle: Battle, actor: HeroUnit) -> list[list[Position]] | None:
        if not self._berserk(battle):
            return None
        routes = [route for target in self.nearest_targets(battle, actor)
                  for route, can_attack in [self.action_route(battle, actor, target)]
                  if len(route) > 1 and not can_attack]
        return list({tuple(route): route for route in routes}.values())

    def basic_attack_preview(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self._berserk(battle):
            return None
        targets: list[HeroUnit] = []
        for target in self.nearest_targets(battle, actor):
            attack_payload = {**(payload or {}), "target_unit_id": target.unit_id}
            if battle.attack_target_allowed(actor, target, payload=attack_payload)[0]:
                targets.append(target)
        return {"cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
                "target_unit_ids": [target.unit_id for target in targets], "requires_target": True}

    def basic_attack_origins(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None) -> list[Position] | None:
        if not self._berserk(battle) or (payload or {}).get("_attack_pre_move_done"):
            return None
        target = battle.units.get(str((payload or {}).get("target_unit_id") or ""))
        if target not in self.nearest_targets(battle, actor):
            return []
        route, can_attack = self.action_route(battle, actor, target)
        return battle.unit_cells_at(actor, route[-1]) if route and can_attack else []

    def can_attack_target_with_payload(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any] | None) -> tuple[bool, str]:
        if not self._berserk(battle) or (payload or {}).get("_attack_pre_move_done"):
            return True, ""
        if target not in self.nearest_targets(battle, actor):
            return False, "浮游炮狂暴化时只能攻击最近的可见敌方单位。"
        if payload is None or str(payload.get("target_unit_id") or "") != target.unit_id:
            return False, "请选择最近的攻击目标。"
        return True, ""

    def before_basic_attack_resolution(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        if not self._berserk(battle) or payload.get("_attack_pre_move_done"):
            return True
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.nearest_targets(battle, actor):
            return False
        route, can_attack = self.action_route(battle, actor, target)
        if not route or not can_attack:
            return False
        if len(route) > 1:
            try:
                battle.move_unit(actor, route[-1], path=route[1:], max_distance=actor.remaining_normal_move_distance(battle))
            except ActionError:
                return False
            payload["_attack_pre_move_done"] = True
        return actor.alive and not actor.banished


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
        entries = [(cell, FloatingCannonSummon(actor.player_id)) for cell in ordered[:4]]
        cannon_ids = [cannon.unit_id for _, cannon in entries]
        tracker = next((trait for trait in actor.traits if isinstance(trait, SakuraFloatingCannonTrait)), None)
        if tracker is not None:
            tracker.cannon_ids = set(cannon_ids)
            tracker.expanded = True
        if actor.get_status("浮游炮展开") is None:
            actor.add_status(FloatingCannonsActiveStatus(cannon_ids))
        for cell, cannon in entries:
            battle.summon_unit(cannon, cell, summoner=actor)
            if actor.get_status("浮游炮狂暴化") is not None:
                cannon.add_status(FloatingCannonBuffStatus())
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


class FloatingCannonCoverStatus(StatusEffect):
    def __init__(self, declaration_id: str) -> None:
        super().__init__("浮游炮掩护", "仅抵消已声明动作对这个受体的伤害与附效。", duration=None)
        self.declaration_id = declaration_id
        self.expire_with_chain = True

    def block(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        action = battle.resolving_action
        if (owner is None or ctx.target is not owner or action is None
                or action.payload.get("declaration_id") != self.declaration_id
                or ctx.from_field_effect or ctx.cancelled):
            return
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        if source is None or not (ctx.is_skill or "attack" in ctx.tags):
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【浮游炮掩护】挡住了【{ctx.action_name}】。"
        battle.emit_defense_visual_event(source=source, target=owner,
                                         action_name=ctx.action_name, defense_reason="block")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        self.block(battle, ctx)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self.block(battle, ctx)


class FloatingCannonCoverSkill(Skill):
    def __init__(self) -> None:
        super().__init__("floating_cannon_cover", "浮游炮掩护", "被动技能：狂暴化关闭时，指定受保护者和其周围 7*7 内一座自己的浮游炮；真实破坏该炮，仅保护该目标免受当前动作的伤害和附效。", timing="passive", target_mode="unit")

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

    def _threatened_units(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> list[HeroUnit]:
        result: list[HeroUnit] = []
        for unit_id in queued_action.target_unit_ids:
            unit = battle.units.get(unit_id)
            if unit is not None:
                recipient = battle.effect_recipient(unit)
                if (recipient.alive and recipient.position is not None and not recipient.banished
                        and battle.unit_can_be_selected(recipient, actor=actor)[0]
                        and not recipient.direct_effects_blocked()
                        and recipient.get_status("风壁") is None
                        and recipient.get_status("浮游炮掩护") is None
                        and not battle.shield_auto_blocks_chain(recipient, queued_action)
                        and recipient not in result):
                    result.append(recipient)  # type: ignore[arg-type]
        return result

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if actor.get_status("浮游炮狂暴化") is not None:
            return False, "浮游炮狂暴化开启时不能使用掩护。"
        if not any(self._cannons_near(battle, actor, target) for target in self._threatened_units(battle, actor, queued_action)):
            return False, "没有可用于掩护的浮游炮。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        reaction_payload = dict(payload or {})
        ids = reaction_payload.get("target_unit_ids") or []
        target_id = str(reaction_payload.get("target_unit_id") or (ids[0] if ids else ""))
        cannon_id = str(reaction_payload.get("cannon_unit_id") or (ids[1] if len(ids) > 1 else ""))
        target = battle.units.get(target_id)
        target = battle.effect_recipient(target) if target is not None else None
        if target is None or target not in self._threatened_units(battle, actor, queued_action):
            return False, "请选择当前动作实际影响的保护目标。"
        if target.unit_id == cannon_id:
            return False, "不能牺牲正在保护的同一座浮游炮。"
        if cannon_id not in {unit.unit_id for unit in self._cannons_near(battle, actor, target)}:
            return False, "请选择目标周围 7*7 内一座自己的浮游炮。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        ok, reason = self.can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            raise ActionError(reason)
        ids = payload.get("target_unit_ids") or []
        target = battle.effect_recipient(battle.get_unit(str(payload.get("target_unit_id") or ids[0])))
        cannon_id = str(payload.get("cannon_unit_id") or (ids[1] if len(ids) > 1 else ""))
        cannon = battle.get_unit(cannon_id)
        cannon.alive = False
        battle.cleanup_dead_units()
        target.add_status(FloatingCannonCoverStatus(str(queued_action.payload.get("declaration_id") or "")), source=actor)
        battle.log(f"{actor.name} 破坏 {cannon.name}，为 {target.name} 发动【浮游炮掩护】。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("浮游炮掩护只能通过连锁使用。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        targets = [target for target in self._threatened_units(battle, actor, queued_action) if self._cannons_near(battle, actor, target)]
        cannons: list[HeroUnit] = []
        for target in targets:
            for cannon in self._cannons_near(battle, actor, target):
                if cannon is not target and cannon not in cannons:
                    cannons.append(cannon)
        return {
            "cells": [cell.to_dict() for unit in [*targets, *cannons] for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in [*targets, *cannons]],
            "secondary_cells": [cell.to_dict() for cannon in cannons for cell in battle.unit_cells(cannon)],
            "requires_target": True,
            "selection": {"mode": "multi_unit", "min_targets": 2, "max_targets": 2,
                          "prompt": "先点受保护单位，再点要牺牲的浮游炮，最后完成选择。"},
        }


class SakuraFloatingCannonTrait(Trait):
    def __init__(self) -> None:
        super().__init__("浮游炮回补", "已展开浮游炮后，每个樱火回合开始时补回被破坏的浮游炮到周围合法格。")
        self.expanded = False
        self.cannon_ids: set[str] = set()
        self.pending_cannons: list[HeroUnit] = []

    def on_owned_summon_destroyed(self, battle: Battle, summon: HeroUnit) -> None:
        owner = self.owner
        if (owner is not None and self.expanded
                and getattr(summon, "hero_code", "") == "floating_cannon"
                and summon.summoner_id == owner.unit_id
                and summon.unit_id in self.cannon_ids
                and summon not in self.pending_cannons):
            self.pending_cannons.append(summon)

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None or not self.expanded:
            return
        for cannon in list(self.pending_cannons):
            cells = [cell for cell in square_around_cells(battle, battle.unit_cells(owner), radius=1)
                     if battle.can_place_unit(cannon, cell)]
            if not cells:
                continue
            cell = min(cells, key=lambda item: (item.y, item.x))
            cannon.alive = True
            cannon.current_hp = cannon.max_health
            cannon.current_mana = cannon.max_mana()
            cannon.banished = False
            cannon.attacks_used = 0
            cannon.actions_taken_this_turn = []
            cannon.moved_this_turn = False
            battle.summon_unit(cannon, cell, summoner=owner)
            if cannon in battle.destroyed_units:
                battle.destroyed_units.remove(cannon)
            if owner.get_status("浮游炮狂暴化") is not None:
                if cannon.get_status("浮游炮狂暴") is None:
                    cannon.add_status(FloatingCannonBuffStatus())
            else:
                buff = cannon.get_status("浮游炮狂暴")
                if buff is not None:
                    cannon.remove_status(buff, battle)
            self.pending_cannons.remove(cannon)
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
        super().__init__("山神术。室王", "魔无限：施放技能不消耗魔，当前魔无上限。", duration=duration, tick_scope="owner_turn_end")

    def bind(self, owner: HeroUnit) -> "UnlimitedManaTemporaryStatus":
        super().bind(owner)
        owner.allow_unbounded_mana = True
        owner.current_mana = max(owner.current_mana, owner.max_mana())
        return self

    def modify_skill_mana_cost(self, battle: Battle, actor: HeroUnit, skill: Skill,
                               payload: dict[str, Any] | None, cost: float) -> float:
        return 0.0 if self.owner is not None and actor.unit_id == self.owner.unit_id else cost

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
        super().__init__("山神计数", "整次普攻被对方连锁或自己真实破坏单位时获得山神计数点。")

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
        self.add_counter(battle)

    def on_confirmed_damage_destruction(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.target.alive or ctx.target.unit_id == owner.unit_id:
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

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        self.chosen_center(battle, actor, payload)
        values = [2 if random.random() < 0.5 else 1 for _ in range(3)]
        return {"gravity_coin_values": values, "gravity_side": values[0] * values[1] * values[2]}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        center = self.chosen_center(battle, actor, payload)
        coin_values = payload.get("gravity_coin_values")
        if not isinstance(coin_values, list) or len(coin_values) != 3 or any(value not in (1, 2) for value in coin_values):
            coin_values = [2 if random.random() < 0.5 else 1 for _ in range(3)]
        side = coin_values[0] * coin_values[1] * coin_values[2]
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
            if not target.alive:
                continue
            drain_ctx = battle.validate_target(
                actor, target, action_name="重力场吸魔", is_skill=True,
                is_hostile=target.player_id != actor.player_id,
                ignore_shield=True, ignore_magic_immunity=True,
                ignore_targeting_restrictions=True, resolve_defenses=False,
                tags={"skill", "area", "gravity_field", "mana_drain"},
            )
            if drain_ctx.cancelled:
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
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        center = self.chosen_center(battle, actor, payload)
        side = int(payload.get("gravity_side") or 8)
        return self.cells_for_side(battle, center, side)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.get_target_cells_for_payload(battle, actor, payload))  # type: ignore[return-value]

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
            "普通技能：2 轮一次，范 6，直线选择双方合法单位造成攻 +1 的普通技能伤害；最终无生命伤害（含落空）时魔 +2 至上限。",
            cooldown_turns=2,
            target_mode="unit",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return 6

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
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
        if ctx.actual_damage <= 0:
            self.on_target_missed(battle, actor, payload)

    def on_target_missed(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        gained = actor.gain_mana(2)
        battle.log(f"{actor.name} 的【凯撒神拳】未造成生命伤害，魔 +{gained}。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if battle.unit_can_be_selected(unit, actor=actor)[0]
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
        if (owner is None or actor.unit_id != owner.unit_id or not owner.alive or owner.banished
                or owner.is_clone or owner.position is None or payload.get("basic_attack_cancelled")):
            return
        real_position = getattr(owner, "_resolution_actual_position", None) or owner.position
        body = owner.footprint_cells_at(real_position)
        own_keys = {(cell.x, cell.y) for cell in body}
        cells = [
            cell
            for cell in square_around_cells(battle, body, radius=1)
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
    def __init__(self, source_name: str, applied_turn_number: int | None = None) -> None:
        super().__init__(source_name, "下个自己的回合不能行动。", duration=1, tick_scope="owner_turn_end")
        self.flag_name = "cannot_act"
        self.applied_turn_number = applied_turn_number

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

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.applied_turn_number:
            super().on_owner_turn_end(battle)

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
        super().__init__("大雪崩", "非冰武将及其召唤物不能移动或使用主动技能；冰武将及其召唤物速+2；雪崩伤害+1且破魔。", duration=5)

    @staticmethod
    def ice_eligible(battle: Battle, unit: HeroUnit) -> bool:
        root_id = battle.controlling_hero_id(unit)
        root = battle.units.get(root_id) if root_id else None
        return getattr(root or unit, "attribute", None) == "冰"

    def blocks_unit_movement(self, battle: Battle, unit: HeroUnit) -> bool:
        return not unit.direct_effects_blocked() and not self.ice_eligible(battle, unit)

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if skill.timing == "active" and not self.ice_eligible(battle, actor):
            return True, "大雪崩天气中非冰单位不能使用主动技能。"
        return False, ""

    def modify_unit_stat(self, battle: Battle, unit: HeroUnit, stat_name: str, value: float) -> float:
        if stat_name == "speed" and battle.weather_effect_applies(self, unit) and self.ice_eligible(battle, unit) and not unit.direct_effects_blocked():
            return value + 2
        return value

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if "snow_avalanche" not in ctx.tags or not battle.weather_effect_applies(self, ctx.target):
            return
        ctx.attack_power += 1
        ctx.ignore_shield = True

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

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return battle.has_weather("大雪崩")

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
                unit.add_status(CannotActNextTurnStatus("雪崩", battle.turn_number))
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
        battle.add_field_effect(BigAvalancheWeatherEffect(), source=actor)


class MartialGodSealStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("魔界武神之印", "全能力 +2，直到下一个敌方英雄回合结束。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "defense", "speed", "attack_range", "mana"}:
            return value + 2
        return value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if owner is not None and ended_player_id != owner.player_id and not battle.is_army_turn():
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
        target = battle.effect_recipient(actor)
        existing = target.get_status("魔界武神之印")
        if existing is not None:
            target.remove_status(existing, battle)
        target.add_status(MartialGodSealStatus())
        target.gain_mana(2)
        battle.heal(HealContext(source=actor, target=target, amount=0.5, action_name="魔界武神之印"))
        battle.log(f"{target.name} 获得【魔界武神之印】，全能力 +2。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        target = battle.effect_recipient(actor)
        return {"cells": positions_to_dict(battle.unit_cells(target)), "target_unit_ids": [target.unit_id],
                "secondary_cells": [], "requires_target": False}


class HellSlashSkill(Skill):
    length = 10
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
            for pattern in line_patterns(battle, origin, ALL_DIRECTIONS, self.length):
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
        for unit in battle.effect_units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", self.code},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.effect_units_at_cells([Position(x, y) for x, y in cell_keys])
            if battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Any]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


def weather_race_eligible(unit: HeroUnit, race: str) -> bool:
    return getattr(unit, "original_weather_race", unit.race) == race


class WeatherExtraAttackStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("万魔殿追加攻击", "本全局回合内增加的普攻次数；回合末清除。", duration=1, tick_scope="any_turn_end")
        self.extra_attacks = 1

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + self.extra_attacks


class RacialWeatherEffect(BattleFieldEffect):
    def eligible(self, battle: Battle, unit: HeroUnit, name: str) -> bool:
        return (battle.canonical_weather_name(getattr(self, "weather_name", "")) == name
                and unit.alive and unit.position is not None and not unit.banished
                and weather_race_eligible(unit, "恶魔" if name == "万魔殿" else "天使")
                and battle.weather_effect_applies(self, unit))

    def modify_unit_stat(self, battle: Battle, unit: HeroUnit, stat_name: str, value: float) -> float:
        if unit.direct_effects_blocked():
            return value
        if stat_name == "attack" and self.eligible(battle, unit, "万魔殿"):
            return value + 1
        if stat_name == "mana" and self.eligible(battle, unit, "天空圣域"):
            return value + 1
        return value

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if not self.eligible(battle, actor, "万魔殿") or actor.direct_effects_blocked():
            return
        if getattr(battle, "_forecasting_action", False):
            return
        gained = random.random() < 0.5
        if gained:
            status = actor.get_status("万魔殿追加攻击")
            if status is None:
                actor.add_status(WeatherExtraAttackStatus())
            else:
                status.extra_attacks += 1
        battle.log_public_event(f"{actor.name} 的万魔殿判定：{'追加1次普攻' if gained else '不追加普攻'}。", source=actor)

    def on_turn_start(self, battle: Battle, active_unit: HeroUnit | None) -> None:
        if active_unit is None:
            return
        for unit in battle.effect_units(battle.turn_bundle_units(active_unit, include_banished=False)):
            if not self.eligible(battle, unit, "天空圣域"):
                continue
            if getattr(unit, "_sky_weather_opening_turn", None) == battle.turn_number:
                continue
            unit._sky_weather_opening_turn = battle.turn_number
            battle.heal(HealContext(source=None, target=unit, amount=0.25, action_name="天空圣域补给", effect_source=self))
            gained = unit.gain_mana(1)
            if gained > 0:
                battle.log_public_event(f"{unit.name} 受到天空圣域补给，魔 +{gained}。", target=unit)

    def protects_piercing(self, battle: Battle, target: HeroUnit, *, is_skill: bool, tags: set[str], ignore_shield: bool, half_ignore_shield: bool, from_field_effect: bool) -> bool:
        return (not from_field_effect and (is_skill or bool(tags & {"attack", "counter"}))
                and (ignore_shield or half_ignore_shield) and self.eligible(battle, target, "天空圣域"))

    def on_after_damage_modifiers(self, battle: Battle, ctx: DamageContext) -> None:
        if self.protects_piercing(battle, ctx.target, is_skill=ctx.is_skill, tags=ctx.tags,
                                 ignore_shield=ctx.ignore_shield, half_ignore_shield=ctx.half_ignore_shield, from_field_effect=ctx.from_field_effect):
            ctx.cancelled = True
            ctx.preserve_followup_effects = False
            ctx.reason = "天空圣域免疫破魔攻击或技能。"

    def on_after_target_modifiers(self, battle: Battle, ctx: TargetContext) -> None:
        if self.protects_piercing(battle, ctx.target, is_skill=ctx.is_skill, tags=ctx.tags,
                                 ignore_shield=ctx.ignore_shield, half_ignore_shield=ctx.half_ignore_shield, from_field_effect=ctx.from_field_effect):
            ctx.cancelled = True
            ctx.reason = "天空圣域免疫破魔攻击或技能。"

    def blocks_action_effect(self, battle: Battle, unit: HeroUnit, queued_action: Any) -> bool:
        payload = queued_action.payload
        return self.protects_piercing(battle, unit, is_skill=queued_action.action_type in {"skill", "skill_effect"},
                                     tags={"attack"} if queued_action.action_type == "attack" else set(),
                                     ignore_shield=bool(payload.get("ignore_shield")), half_ignore_shield=bool(payload.get("half_ignore_shield")),
                                     from_field_effect=bool(payload.get("from_field_effect")))


class WetlandGrasslandEffect(RacialWeatherEffect):
    global_weather = True
    weather_name = "湿地草原"

    def __init__(self) -> None:
        super().__init__("湿地草原", "双方当前等级1的武将本体普攻和反击破魔；技能与召唤物不因此破魔。")

    @staticmethod
    def eligible_hero(actor: HeroUnit) -> bool:
        return (isinstance(actor, HeroUnit) and not actor.is_summon and not actor.is_clone
                and not is_army_soldier(actor) and actor.level == 1)

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ignore_shield": True} if self.eligible_hero(actor) else {}

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if not ctx.is_skill and not ctx.from_field_effect and "attack" in ctx.tags and self.eligible_hero(ctx.actor):
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is not None and not ctx.is_skill and not ctx.from_field_effect and "attack" in ctx.tags and self.eligible_hero(ctx.source):
            ctx.ignore_shield = True

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        return any(battle.canonical_weather_name(getattr(effect, "weather_name", "")) == self.weather_name
                   for effect in existing_effects)

    def board_marker(self, battle: Battle) -> str:
        return "湿"


class SimpleGlobalWeatherEffect(RacialWeatherEffect):
    global_weather = True

    def __init__(self, weather_name: str, *, duration: int | None = None, marker: str | None = None) -> None:
        self.weather_name = weather_name
        self._marker = marker or weather_name[:1]
        rules = {"万魔殿": "双方恶魔武将及原召唤链攻+1；每次普攻完成后1/2几率增加本回合1次普攻。",
                 "天空圣域": "双方天使武将及原召唤链魔上限+1；本人回合开始血+1/4、当前魔+1；免破魔及半破魔攻击/技能对应段。"}
        super().__init__(weather_name, f"全场天气：{weather_name}。" + rules.get(Battle.canonical_weather_name(weather_name), ""), duration=duration)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if battle.canonical_weather_name(getattr(effect, "weather_name", "")) != battle.canonical_weather_name(self.weather_name):
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
        effect = (WetlandGrasslandEffect() if self.weather_name == "湿地草原"
                  else SimpleGlobalWeatherEffect(self.weather_name, duration=self.weather_duration, marker=self.weather_marker))
        battle.add_field_effect(effect, source=actor)


class PandemoniumSpeedTrait(Trait):
    def __init__(self) -> None:
        super().__init__("万魔殿加速", "在“万魔殿”天气中速 +3。")

    def sync(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        existing = owner.get_status("万魔殿加速")
        if existing is not None:
            owner.remove_status(existing, battle)

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        reference = getattr(owner, "_battle_ref", None)
        battle = reference() if reference is not None else None
        if (stat_name == "speed" and owner is not None and battle is not None
                and not owner.direct_effects_blocked() and battle.unit_in_weather("万魔殿", owner)):
            return value + 3
        return value

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
            if battle.unit_can_be_selected(unit, actor=actor)[0]
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
        target = battle.effect_recipient(target)
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
        self.flag_name = "cannot_normal_move"

    def bind(self, owner: HeroUnit) -> "SacredDuelStatus":
        super().bind(owner)
        owner.cannot_normal_move = True
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
        owner.cannot_normal_move = any(
            getattr(status, "flag_name", "") == "cannot_normal_move"
            for status in owner.statuses
        )


class SacredDuelSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "sacred_duel",
            "神圣决斗",
            "普通技能：5 轮一次，无伤破魔；范内直线选择双方合法单位，5 个目标己方回合末无法普通移动或使用主动技能，普攻与被动（含回避）仍可用。",
            cooldown_turns=5,
            target_mode="unit",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if battle.unit_can_be_selected(unit, actor=actor)[0]
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
        target = battle.effect_recipient(target)
        if target.magic_immunity:
            battle.log_public_event(f"{target.name} 处于魔免状态，神圣决斗无效。", source=actor, target=target)
            return
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
    target_cells_are_geometry_only = True
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
    target_unit_is_anchor = True

    def __init__(self) -> None:
        super().__init__(
            "true_blade_air_slash", "真刀。空气斩",
            "普通技能：费1.5魔，每回合1次；声明五格直线落点和落点范内双方目标，先完成位移再按实际受体守+1破魔；成功补目标当前魔。",
            mana_cost=1.5, max_uses_per_turn=1, target_mode="unit",
        )

    def landing_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        result = []
        for dx, dy in ALL_DIRECTIONS:
            destination = actor.position.offset(dx * 5, dy * 5)
            try:
                battle.find_path(actor, destination, max_distance=5, exact_distance=5, straight_only=True)
            except ActionError:
                continue
            result.append(destination)
        return result

    def target_in_range_from_landing(self, battle: Battle, actor: HeroUnit, target: HeroUnit, landing: Position) -> bool:
        return any(origin.distance_to(cell) <= actor.targeting_range()
                   and battle.cells_are_straight_aligned(origin, cell)
                   for origin in actor.footprint_cells_at(landing) for cell in battle.unit_cells(target))

    def choose_landing(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any]) -> Position:
        if payload.get("x") is None or payload.get("y") is None:
            raise ActionError("真刀需要显式选择五格直线落点。")
        selected = payload_position(payload)
        if selected not in self.landing_cells(battle, actor) or not self.target_in_range_from_landing(battle, actor, target, selected):
            raise ActionError("真刀落点或移动后的目标范围不合法。")
        return selected

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = payload_target_unit(battle, payload)
        landing = self.choose_landing(battle, actor, target, payload)
        cells = [cell for cell in battle.unit_cells(target)
                 if any(origin.distance_to(cell) <= actor.targeting_range()
                        and battle.cells_are_straight_aligned(origin, cell)
                        for origin in actor.footprint_cells_at(landing))]
        cell = min(cells, key=lambda cell: (cell.y, cell.x))
        return {"air_slash_landing": landing.to_dict(), "air_slash_target_cell": cell.to_dict()}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        meta = payload if "air_slash_target_cell" in payload else self.queued_payload_metadata(battle, actor, payload)
        return [Position(**meta["air_slash_target_cell"])]

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        meta = payload if payload.get("queued_resolution") and "air_slash_landing" in payload else self.queued_payload_metadata(battle, actor, payload)
        landing = Position(**meta["air_slash_landing"])
        target_cell = Position(**meta["air_slash_target_cell"])
        actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
        battle.move_unit(actor, landing, via_skill=True, straight_only=True, max_distance=5, exact_distance=5,
                         tags={"movement", "true_blade_air_slash"})
        if not actor.alive or actor.banished or actor.position is None:
            return
        targets = battle.effect_units_at_cells([target_cell])
        if not targets:
            raise ActionMiss("真刀已完成移动，但原声明格没有有效目标。")
        target = targets[0]
        if not self.target_in_range_from_landing(battle, actor, target, actor.position):
            raise ActionMiss("真刀已完成移动，但原声明目标无法命中。")
        target_mana = target.current_mana
        ctx = battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=target.stat("defense") + 1,
            is_skill=True, action_name=self.name, ignore_shield=True,
            tags={"skill", "true_blade_air_slash"},
        ))
        if damage_followup_effect_applies(ctx) and not target.magic_immunity and actor.alive and not actor.banished:
            gained = actor.gain_mana(target_mana)
            battle.log(f"{actor.name} 因【真刀。空气斩】获得 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        landings = self.landing_cells(battle, actor)
        destinations = {
            unit.unit_id: positions_to_dict([cell for cell in landings if self.target_in_range_from_landing(battle, actor, unit, cell)])
            for unit in battle.all_units() if battle.unit_can_be_selected(unit, actor=actor)[0]
        }
        destinations = {unit_id: cells for unit_id, cells in destinations.items() if cells}
        return {
            "cells": positions_to_dict(landings) + positions_to_dict([cell for unit_id in destinations for cell in battle.unit_cells(battle.get_unit(unit_id))]),
            "target_unit_ids": list(destinations), "secondary_cells": positions_to_dict(landings),
            "requires_target": True, "destinations_by_target": destinations,
        }

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


MOVEMENT_SKILL_CODES = {
    "backstep_shot",
    "card_transposition",
    "chain_pull",
    "crazy_sand",
    "descent_moment",
    "detach_mage_cloak",
    "dragon_slash",
    "earth_walker",
    "equip_mage_cloak",
    "evasion",
    "fate_kick",
    "fantasy_move",
    "fly_leap",
    "frey_quick_flash",
    "fuma_pursuit",
    "gale",
    "ghost_step",
    "iaido_charge",
    "iron_chain_path",
    "jirobo_follow_step",
    "lao_mage_hand",
    "mana_pull",
    "mounted_leap",
    "natsume_wind_word",
    "nuclear_rush",
    "plasma_thruster",
    "rainbow_mirror",
    "remi_chaos",
    "shadow_counter",
    "split",
    "thor_rage_impact",
    "true_blade_air_slash",
    "weapon_transfer",
    "zero_dash",
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
            description="本次普攻后到下回合结束前守 +1；当前回合可使用一次百鸟葬追步移动至多 2 格。",
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
        owner.add_status(JiroboAfterAttackStatus())
        battle.log(f"{owner.name} 本次普攻后获得一份追步守备。")


class JiroboFollowStepSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "jirobo_follow_step",
            "百鸟葬追步",
            "特性触发后的可选移动：攻击后当回合可移动至多 2 格。",
            target_mode="cell",
        )

    def _status(self, actor: HeroUnit) -> JiroboAfterAttackStatus | None:
        return next((status for status in actor.statuses
                     if isinstance(status, JiroboAfterAttackStatus) and status.follow_step_available), None)

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
            "普通技能：2轮一次，破魔；双方实际受体当前生命减半，附效可结算时自身当前生命翻倍，封顶且禁疗阻止。",
            cooldown_turns=2,
            target_mode="unit",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        return {
            "cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        target = battle.effect_recipient(target)
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
        if damage_followup_effect_applies(ctx) and not target.magic_immunity and actor.alive and not actor.banished and not actor.cannot_heal:
            actor.heal_fraction(actor.current_hp)
            battle.log(f"{actor.name} 的【吞噬】将当前生命翻倍至 {actor.current_hp:g}。")

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class UndyingQuarterTrait(Trait):
    def __init__(self) -> None:
        super().__init__("不死保留", "半血以上受到致命伤害时，每个伤害实例可保留 1/4 血留场。")
    def lethal_damage_remaining_hp(self, battle: Battle, ctx: DamageContext) -> float | None:
        owner = self.owner
        if (owner is None or ctx.target is not owner or owner.is_clone or not owner.alive
                or owner.current_hp + 1e-9 < owner.max_health / 2):
            return None
        retained = owner.max_health / 4
        battle.log_public_event(f"{owner.name} 触发【不死保留】，以 {retained:g} 点生命留在场上。", source=ctx.source, target=owner)
        return retained


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
            "普通技能：2 轮一次；身前 2*3 双方按当前攻受技能伤害，有效命中者 2 轮不能使用技能，速 -1 到 1。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return front_rectangle_patterns(battle, actor, depth=2, width=3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def apply_to_units(self, battle: Battle, actor: HeroUnit, units: list[HeroUnit], *, action_name: str,
                       cells: list[Position] | None = None) -> None:
        for unit in battle.effect_units(units):
            if not unit.alive or unit.position is None or unit.banished:
                continue
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"), is_skill=True,
                action_name=action_name, tags={"skill", "electric_wind"},
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells) if cells else 1,
            ))
            if not damage_followup_effect_applies(ctx) or not unit.alive or unit.banished or unit.position is None:
                continue
            effect = battle.validate_target(actor, unit, action_name=action_name, is_skill=True,
                                            is_hostile=unit.player_id != actor.player_id,
                                            resolve_defenses=ctx.cancelled and not ctx.shield_consumed,
                                            ignore_targeting_restrictions=True)
            if effect.cancelled:
                continue
            existing = unit.get_status("电风")
            if existing is not None:
                unit.remove_status(existing, battle)
            unit.add_status(ElectricWindStatus())
            battle.log_public_event(f"{unit.name} 被【电风】影响，不能使用技能且速 -1。", source=actor, target=unit)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        self.apply_to_units(battle, actor, battle.effect_units_at_cells(cells), action_name="电风", cells=cells)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class AutoElectricWindTrait(Trait):
    def __init__(self) -> None:
        super().__init__("自动电风", "每个自己的回合开始时，对周围 5*5 内单位自动使用电风；没有合法目标则跳过。")
        self.last_start_turn: int | None = None

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.banished or owner.position is None:
            return
        if self.last_start_turn == battle.turn_number:
            return
        self.last_start_turn = battle.turn_number
        wind = ElectricWindSkill()
        # The automatic cadence is independent, but the named spell still obeys skill seals.
        if owner.cannot_use_skills or any(component.blocks_skill_use(battle, owner, wind)[0]
               or not component.allows_block_counter(battle, owner)
               or (isinstance(component, ThorDestroyedLightningStatus) and component.pending)
               for component in owner.iter_components()):
            return
        if not owner.direct_effects_blocked() and any(effect.blocks_skill_use(battle, owner, wind)[0] for effect in battle.field_effects):
            return
        cells = square_around_cells(battle, battle.unit_cells(owner), radius=2)
        targets = battle.effect_units_at_cells(cells, ignore=battle.effect_recipient(owner))
        if not targets:
            battle.log(f"{owner.name} 的【自动电风】没有合法目标，跳过。")
            return
        wind.apply_to_units(battle, owner, targets, action_name="自动电风", cells=cells)


class SkySanctuarySkill(WeatherUltimateSkill):
    def __init__(self) -> None:
        super().__init__("sky_sanctuary", "天使的气息", weather_name="天空的圣域", marker="圣")


class VitalityBlastSkill(HellSlashSkill):
    length = 5

    def __init__(self) -> None:
        Skill.__init__(self, "vitality_blast", "元气爆破",
                       "大招：0魔、2轮一次；本人实际处于天空圣域时使用，身前5格双方当前攻普通技能伤害。",
                       cooldown_turns=2, target_mode="cell")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not battle.unit_in_weather("天空圣域", actor):
            return False, "需要处于“天空的圣域”天气中。"
        return True, ""

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        body = set(battle.unit_cells(actor))
        return [cells for cells in super().patterns(battle, actor) if not body.intersection(cells)]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not battle.unit_in_weather("天空圣域", actor):
            battle.log("【元气爆破】结算时施法者已不在天空圣域中，动作落空。")
            return
        super().execute(battle, actor, payload)


class SkySanctuaryAuraEffect(RacialWeatherEffect):
    weather_name = "天空圣域"

    def __init__(self, source_unit_id: str) -> None:
        self.source_unit_id = source_unit_id
        super().__init__("天空圣域", "制裁者周围11*11具有完整天空圣域效果：双方天使及原召唤链魔上限+1、本人开局血+1/4和魔+1、免破魔/半破魔攻击技能。", duration=None)

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
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive:
            battle.remove_field_effect(self)
            return
        super().on_turn_start(battle, active_unit)


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
                if not target.alive or not battle.unit_in_weather("天空圣域", target):
                    break
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

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move:
            return False, "当前无法移动，不能使用混沌。"
        if payload is not None and payload.get("x") is not None and payload.get("y") is not None:
            try:
                destination = payload_position(payload)
            except (KeyError, TypeError, ValueError):
                return False, "请选择合法的混沌落点。"
            if destination not in battle.reachable_positions(actor, max_distance=3, exact_distance=3):
                return False, "混沌必须移动恰好 3 格到合法落点。"
        return True, ""

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
        paying = getattr(owner, "_paying_skill_cost", False)
        owner._paying_skill_cost = True
        try:
            spent = owner.spend_mana(1)
        finally:
            owner._paying_skill_cost = paying
        if spent < 1 or owner.current_mana <= 0:
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
        if owner is not None and actor.unit_id == owner.unit_id and (skill.timing in {"passive", "reaction"} or skill.passive and skill.timing != "instant"):
            return True, "被动封锁状态下不能使用被动技能。"
        return False, ""


class SunSlashSkill(Skill):
    def __init__(self) -> None:
        super().__init__("sun_slash", "斩技。阳",
                         "大招：0魔、每场一次；射程直斜双方实际受体，当前攻技能伤害和3个本人回合末的被动技能封锁共破魔。",
                         max_uses_per_battle=1, target_mode="unit")

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range(), action_name=self.name)
        return {}

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.effect_units(battle.all_units())
                   if battle.unit_can_be_selected(unit, actor=actor)[0]
                   and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]
        return {"cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
                "target_unit_ids": [target.unit_id for target in targets], "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [battle.effect_recipient(payload_target_unit(battle, payload))]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        if not payload.get("queued_resolution"):
            self.queued_payload_metadata(battle, actor, payload)
        target_ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
            is_hostile=target.player_id != actor.player_id, ignore_shield=True, resolve_defenses=False,
            damage_target=True, tags={"skill", self.code})
        if target_ctx.cancelled:
            return
        ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"),
            is_skill=True, action_name=self.name, ignore_shield=True, tags={"skill", self.code}))
        target = ctx.target
        if damage_followup_effect_applies(ctx) and target.alive:
            if apply_piercing_status_effect(battle, actor, target, action_name=self.name,
                    status=PassiveSkillLockStatus(), is_skill=True, tags={"skill", self.code},
                    consume_shield=not ctx.shield_consumed, cannot_evade=False):
                battle.log_public_event(f"{target.name} 被【斩技。阳】封锁被动技能。", source=actor, target=target)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class KikuLegacyStatus(StatusEffect):
    applies_after_basic_attack_shape = True

    def __init__(self) -> None:
        super().__init__("菊之遗击", "每个己方进攻英雄回合一次独立普攻，基准伤4，不占普通攻击次数。", duration=None)
        self.is_skill_effect = False
        self._turn_number: int | None = None
        self._legacy_attack_used = False

    def _sync(self, battle: Battle) -> None:
        if self._turn_number != battle.turn_number:
            self._turn_number = battle.turn_number
            self._legacy_attack_used = False

    def basic_attack_action_entries(self, battle: Battle, actor: HeroUnit) -> list[dict[str, Any]]:
        self._sync(battle)
        return [{"code": "kiku_legacy_attack", "name": "菊之遗击",
                 "description": "独立额外普攻：基准伤4，不占普通攻击次数。",
                 "attack_payload": {"attack_variant": "kiku_legacy", "attack_name": "菊之遗击"}}]

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if (payload or {}).get("attack_variant") != "kiku_legacy":
            return {}
        return {"attack_power_override": 4, "attack_cost": 0, "attack_name": "菊之遗击",
                "attack_note": "独立额外普攻，基准伤害值4；不占普通攻击次数。"}

    def can_use_basic_attack(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[bool, str]:
        self._sync(battle)
        if payload.get("attack_variant") == "kiku_legacy" and self._legacy_attack_used:
            return False, "本回合已经使用过【菊之遗击】。"
        return True, ""

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        self._sync(battle)
        if payload.get("attack_variant") == "kiku_legacy" and not payload.get("reaction_attack"):
            self._legacy_attack_used = True

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None and not self.owner.alive:
            self.owner.remove_status(self, battle)


class KikuAfterDeathTrait(Trait):
    def __init__(self) -> None:
        super().__init__("妖仙遗志", "真实被破坏后，只给当时在场的其他己方单位永久同名不叠的伤4额外普攻。")
        self._last_granted_destruction = -1

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.alive or not isinstance(owner, HeroUnit) or owner.is_clone or owner.is_summon or is_army_soldier(owner):
            return
        event = getattr(owner, "destruction_count", 0)
        if event <= self._last_granted_destruction:
            return
        self._last_granted_destruction = event
        for ally in battle.player_units(owner.player_id):
            if (ally.unit_id == owner.unit_id or not ally.alive or ally.banished or ally.position is None
                    or ally.has_status("菊之遗击") or ally.direct_effects_blocked()):
                continue
            status = KikuLegacyStatus()
            ally.add_status(status, source=owner)
            if status in ally.statuses:
                battle.log_public_event(f"{ally.name} 获得【菊之遗击】。", source=owner, target=ally)


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
    all_skills_pierce_shields = True

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
        super().__init__("伤害封顶", "每个伤害实例及固定失血最多失去 1/4 生命。")

    def limit_hp_loss(self, amount: float) -> float:
        return min(0.25, amount)


class FreyQuickFlashSkill(Skill):
    excludes_caster_from_effect = True
    target_unit_is_anchor = True
    selection_cells_are_effect_cells = False
    resolves_from_current_position = True

    def __init__(self) -> None:
        super().__init__(
            "frey_quick_flash", "快闪",
            "普通技能：每回合最多2次，范5；选一个单位及其周围空格，瞬移后可选择对周围造成技能伤害。",
            max_uses_per_turn=2, target_mode="cell",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return 5

    def selection(self, payload: dict[str, Any]) -> tuple[str, bool, Position]:
        choice = str(payload.get("choice_code") or "")
        if choice:
            parts = choice.split(":", 2)
            if len(parts) != 3 or parts[0] != "flash" or parts[1] not in {"strike", "move"}:
                raise ActionError("需要选择快闪锚单位和是否攻击。")
            target_id, strike = parts[2], parts[1] == "strike"
        else:
            target_id = str(payload.get("target_unit_id") or "")
            strike = payload.get("deal_damage", True)
            if not isinstance(strike, bool):
                raise ActionError("快闪伤害选择必须为是或否。")
        if not target_id:
            raise ActionError("需要选择快闪锚单位。")
        if payload.get("cells") is not None:
            cells = payload["cells"]
            if not isinstance(cells, list) or len(cells) != 1:
                raise ActionError("快闪只能选择一个落点。")
            destination = payload_position(cells[0])
        else:
            destination = payload_position(payload)
        return target_id, strike, destination

    def landing_is_empty(self, battle: Battle, actor: HeroUnit, destination: Position) -> bool:
        return destination != actor.position and all(
            battle.in_bounds(cell) and (cell.x, cell.y) not in battle.blocked_cells
            and not any(unit.unit_id != actor.unit_id for unit in battle.units_at(cell))
            for cell in battle.unit_cells_at(actor, destination)
        )

    def legal_destinations(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> list[Position]:
        target_cells = set(battle.unit_cells(target))
        return [cell for cell in square_around_cells(battle, list(target_cells), radius=1)
                if cell not in target_cells and self.landing_is_empty(battle, actor, cell)]

    def validate_selection(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[str, bool, Position]:
        if actor.cannot_move or actor.position is None or getattr(actor, "standable_terrain", False):
            raise ActionError("当前不能快闪移动。")
        target_id, strike, destination = self.selection(payload)
        target = battle.get_unit(target_id)
        battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, 5, action_name=self.name)
        if destination not in self.legal_destinations(battle, actor, target):
            raise ActionError("快闪需要目标周围的完整空落点。")
        return target_id, strike, destination

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move or actor.position is None or getattr(actor, "standable_terrain", False):
            return False, "当前不能快闪移动。"
        if payload and any(key in payload for key in ("target_unit_id", "choice_code", "cells", "x", "y")):
            try:
                self.validate_selection(battle, actor, payload)
            except (ActionError, TypeError, ValueError, KeyError) as exc:
                return False, str(exc)
        return True, ""

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> None:
        self.validate_selection(battle, actor, payload or {})
        super().prepay_resources(battle, actor, payload)

    def ring(self, battle: Battle, actor: HeroUnit, destination: Position) -> list[Position]:
        body = set(battle.unit_cells_at(actor, destination))
        return [cell for cell in square_around_cells(battle, list(body), radius=1) if cell not in body]

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        _, strike, destination = self.selection(payload)
        return self.ring(battle, actor, destination) if strike else []

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload), ignore=actor)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target_id, strike, destination = self.validate_selection(battle, actor, payload)
        return {"frey_flash_anchor": target_id, "frey_flash_destination": destination.to_dict(),
                "frey_flash_cells": positions_to_dict(self.ring(battle, actor, destination) if strike else []),
                "frey_flash_strike": strike}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        declaration = payload if payload.get("queued_resolution") and "frey_flash_destination" in payload else self.queued_payload_metadata(battle, actor, payload)
        destination = payload_position(declaration["frey_flash_destination"])
        if actor.cannot_move or not self.landing_is_empty(battle, actor, destination):
            raise ActionMiss("快闪原落点已失效，本次瞬移和环伤落空。")
        battle.move_unit(actor, destination, via_skill=True, allow_anywhere=True,
                         max_distance=max(battle.width, battle.height), tags={"frey_quick_flash"})
        if not actor.alive or actor.banished:
            return
        cells = battle.payload_positions(declaration, "frey_flash_cells")
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name,
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                tags={"skill", self.code},
            ))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        choices, destinations = [], []
        legal_cells: set[Position] = set()
        if not actor.cannot_move and actor.position is not None:
            for target in battle.all_units():
                try:
                    battle.require_selectable_unit(target, actor=actor, action_name=self.name)
                    battle.require_unit_target_in_range_and_line(actor, target, 5, action_name=self.name)
                except ActionError:
                    continue
                cells = self.legal_destinations(battle, actor, target)
                if not cells:
                    continue
                legal_cells.update(cells)
                for strike in (False, True):
                    code = f"flash:{'strike' if strike else 'move'}:{target.unit_id}"
                    choices.append({"code": code, "label": f"{target.name}（{target.position.x},{target.position.y}）旁·{'瞬移并攻击' if strike else '仅瞬移'}",
                                    "patterns": [[cell.to_dict()] for cell in cells]})
                    for cell in cells:
                        destinations.append({"choice_code": code, "pattern": [cell.to_dict()],
                                             "destination_cells": positions_to_dict(self.ring(battle, actor, cell) if strike else [])})
        return {"cells": positions_to_dict(sorted(legal_cells, key=lambda cell: (cell.y, cell.x))),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                              "choice_prompt": "先选择锚单位及是否攻击，再选择一个蓝色空落点。",
                              "cell_prompt": "选择一个蓝色空落点；环形高亮显示本次攻击范围。"}}


class FreyGodStabSkill(Skill):
    excludes_caster_from_effect = True

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
            for ray in line_patterns(battle, origin, ALL_DIRECTIONS, 4):
                # Start at the outward edge of a multi-cell body, never hit that body.
                if any(cell in battle.unit_cells(actor) for cell in ray):
                    continue
                for length in range(1, len(ray) + 1):
                    pattern = ray[:length]
                    key = pattern_signature(pattern)
                    if key not in seen:
                        seen.add(key)
                        patterns.append(pattern)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"frey_stab_cells": positions_to_dict(match_payload_pattern(payload, self.patterns(battle, actor)))}

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> None:
        match_payload_pattern(payload or {}, self.patterns(battle, actor))
        super().prepay_resources(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "frey_stab_cells") if payload.get("queued_resolution") and "frey_stab_cells" in payload else self.chosen_cells(battle, actor, payload)
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
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
        return battle.effect_units_at_cells(cells, ignore=actor)


class FreyLionSpearSkill(Skill):
    excludes_caster_from_effect = True

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
                    if key in seen or cell in battle.unit_cells(actor):
                        continue
                    seen.add(key)
                    result.append(cell)
        return result

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.affected_cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload), ignore=actor)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"frey_lion_cells": positions_to_dict(self.affected_cells(battle, actor))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "frey_lion_cells") if payload.get("queued_resolution") and "frey_lion_cells" in payload else self.affected_cells(battle, actor)
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
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

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move:
            return False, "无法移动时不能使用冲刺。"
        return True, ""

    def destination_for_direction(self, battle: Battle, actor: HeroUnit, direction: tuple[int, int]) -> Position:
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        return actor.position.offset(direction[0] * 8, direction[1] * 8)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        destination = self.destination_for_direction(battle, actor, _payload_direction(payload))
        if payload_position(payload) != destination:
            raise ActionError("冲刺必须选择该方向的第8格。")
        battle.find_path(actor, destination, max_distance=8, exact_distance=8, straight_only=True, ignore_units=True)
        return {}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        destination = self.destination_for_direction(battle, actor, _payload_direction(payload))
        return battle.find_path(actor, destination, max_distance=8, exact_distance=8, straight_only=True, ignore_units=True)[1:]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        direction = _payload_direction(payload)
        destination = self.destination_for_direction(battle, actor, direction)
        actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
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
        if actor.cannot_move:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
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
        if owner is None or ctx.unit.unit_id != owner.unit_id or len(ctx.path) < 2:
            return
        for unit in battle.path_crossing_units(owner, ctx.path):
            if not owner.alive or owner.banished or owner.position is None:
                break
            if not unit.alive or unit.banished or unit.position is None:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=owner,
                    target=unit,
                    attack_power=owner.stat("attack"),
                    is_skill=False,
                    from_field_effect=True,
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

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        line = self.line_for_direction(battle, actor, _payload_direction(payload))
        if payload_position(payload) != line[4]:
            raise ActionError("追身必须选择该方向的第5格。")
        return {"fuma_pursuit_cells": positions_to_dict(line[:4]), "fuma_pursuit_landing": line[4].to_dict()}

    def damage_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and "fuma_pursuit_cells" in payload:
            return battle.payload_positions(payload, "fuma_pursuit_cells")
        return self.line_for_direction(battle, actor, _payload_direction(payload))[:4]

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.damage_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.damage_cells(battle, actor, payload))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        damage_cells = self.damage_cells(battle, actor, payload)
        landing = (Position(**payload["fuma_pursuit_landing"]) if payload.get("queued_resolution") and "fuma_pursuit_landing" in payload
                   else self.line_for_direction(battle, actor, _payload_direction(payload))[4])
        for unit in battle.effect_units_at_cells(damage_cells):
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
        if actor.alive and actor.position is not None and not actor.banished and not actor.cannot_move:
            actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
            if battle.can_place_unit(actor, landing, ignore=actor, mover=actor):
                try:
                    if landing != actor.position:
                        battle.move_unit(actor, landing, via_skill=True, allow_anywhere=True, tags={"fuma_pursuit"})
                except ActionError:
                    battle.log(f"{actor.name} 的追身完成攻击，但无法到达声明落点。")
            else:
                battle.log(f"{actor.name} 的追身完成攻击，但声明落点已被阻挡。")

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
        return {"cells": [cell.to_dict() for cell in cells],
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(secondary)
                                    if battle.unit_can_be_selected(unit, actor=actor)[0]],
                "secondary_cells": positions_to_dict(secondary), "requires_target": True}

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

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in list(existing_effects):
            if isinstance(effect, FumaTrapEffect) and effect.source_unit_id == self.source_unit_id and effect.center == self.center:
                battle.remove_field_effect(effect)
        return False

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive or source.banished or source.position is None:
            battle.remove_field_effect(self)
            return
        if ended_player_id == self.player_id:
            return
        cells = self.affected_cells(battle)
        for unit in battle.effect_units_at_cells(cells):
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

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        center = payload_position(payload)
        if not battle.in_bounds(center) or battle.unit_distance_to_cell(actor, center) > actor.targeting_range():
            raise ActionError("陷阱目标格超出范围。")
        return {}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.queued_payload_metadata(battle, actor, payload)
        center = payload_position(payload)
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
        for x in range(-2, battle.width):
            for y in range(-2, battle.height):
                start = Position(x, y)
                for direction in ALL_DIRECTIONS:
                    cells = [start.offset(direction[0] * i, direction[1] * i) for i in range(3)]
                    cells = [cell for cell in cells if battle.in_bounds(cell)]
                    if not cells:
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
        for unit in battle.effect_units_at_cells(cells):
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
            if battle.unit_can_be_selected(unit, actor=actor)[0] and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        return battle.effect_units_at_cells(cells)


class FumaSkillManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("风魔随机回魔", "每次使用主动技能时，后端公开随机；1/2 几率魔 +1。")

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            for effect in list(battle.field_effects):
                if isinstance(effect, FumaTrapEffect) and effect.source_unit_id == self.owner.unit_id:
                    battle.remove_field_effect(effect)

    def on_owner_banished(self, battle: Battle) -> None:
        self.on_owner_removed(battle)

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
        if getattr(battle, "_forecasting_action", False):
            return
        if random.random() < 0.5:
            gained = owner.gain_mana(1)
            battle.log_public_event(f"{owner.name} 的【风魔随机回魔】成功，获得 {gained} 点魔。", source=owner)
        else:
            battle.log_public_event(f"{owner.name} 的【风魔随机回魔】未触发。", source=owner)


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
        before_hp = actor.current_hp
        battle.heal(HealContext(source=actor, target=actor, amount=max(0.0, actor.max_health - before_hp), action_name="龙舞"))
        battle.log(f"{actor.name} 使用【龙舞】，回复 {round(actor.current_hp - before_hp, 2)} 点生命并获得 {gained} 点魔。")


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
            status = NianRoarStatus(actor.unit_id, actor.name)
            target.add_status(status)
            if status in target.statuses:
                for existing in list(target.statuses):
                    if existing is not status and isinstance(existing, NianRoarStatus) and existing.forced_target_id == actor.unit_id:
                        target.remove_status(existing, battle)
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
        body = set(battle.unit_cells(actor))
        return [cell for cell in square_around_cells(battle, body, radius=1) if cell not in body]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True  # The mana drain pierces; damage remains ordinary.

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.affected_cells(battle, actor)
        for target in battle.units_at_cells(cells):
            if target.unit_id == actor.unit_id:
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
            if damage_followup_effect_applies(ctx, allow_on_shield_break=True) and not is_mana_drain_immune(target):
                lost = target.spend_mana(1)
                gained = actor.gain_mana(lost)
                battle.log(f"{actor.name} 的【猫手】吸取 {target.name} {lost} 点魔，回复 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor)
        cell_keys = {(cell.x, cell.y) for cell in cells}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and (unit.player_id == actor.player_id or not unit.is_stealthed())
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": targets, "secondary_cells": [], "requires_target": False}


    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.affected_cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.affected_cells(battle, actor))  # type: ignore[return-value]


class BlackCatFormStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("化猫", "攻1守1速4范1，魔免；攻击后重置移动次数。", duration=None)
        self.prior_magic_immunity = False

    def bind(self, owner: HeroUnit) -> "BlackCatFormStatus":
        super().bind(owner)
        self.prior_magic_immunity = owner.magic_immunity
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
        owner.magic_immunity = self.prior_magic_immunity or any(status.name == "魔免" for status in owner.statuses)


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
        super().__init__("fantasy_move", "幻想", "普通技能：每回合最多 1 次，破魔且无法被回避；按当前攻击造成双方技能伤害，并强制实际受体移动 4 格。", max_uses_per_turn=1, target_mode="unit")

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        destination = payload_position(payload)
        if destination not in self.legal_destinations(battle, target):
            raise ActionError("幻想需要目标可合法移动恰好4步的落点。")
        return {"fantasy_landing": destination.to_dict()}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(payload_target_unit(battle, payload))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        target = battle.effect_recipient(target)
        destination = Position(**payload["fantasy_landing"]) if payload.get("queued_resolution") and "fantasy_landing" in payload else payload_position(payload)
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
        if damage_followup_effect_applies(ctx) and target.alive and not target.banished and target.position is not None:
            effect = battle.validate_target(actor, target, action_name="幻想位移", is_skill=True,
                                            is_hostile=actor.is_enemy_of(target), ignore_shield=True, cannot_evade=True,
                                            ignore_targeting_restrictions=True, resolve_defenses=ctx.cancelled and not ctx.shield_consumed)
            if effect.cancelled:
                return
            try:
                battle.move_unit(target, destination, via_skill=True, forced=True, max_distance=4, exact_distance=4, tags={"fantasy_move"})
            except ActionError:
                battle.log(f"{target.name} 受到幻想伤害，但无法到达声明落点。")

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
            for unit in battle.all_units()
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        destinations_by_target = {
            target.unit_id: positions_to_dict(self.legal_destinations(battle, battle.effect_recipient(target)))
            for target in targets
        }
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets if destinations_by_target[target.unit_id]],
            "secondary_cells": [],
            "requires_target": True,
            "destinations_by_target": destinations_by_target,
        }


class RainbowMirrorNoMoveStatus(FlagStatus):
    def __init__(self) -> None:
        super().__init__("彩虹镜", "cannot_normal_move", description="当前全局回合不能普通移动，技能位移和回避仍可。", duration=1, tick_scope="any_turn_end")


class RainbowMirrorSkill(Skill):
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("rainbow_mirror", "彩虹镜", "普通技能：费 0.5 魔；将一个本回合未实际移动的己方单位移至自身周围，当前全局回合不能普通移动。", mana_cost=0.5, target_mode="ally")

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        ensure_ally(actor, target)
        if target.moved_this_turn or target.normal_move_steps_used > 0:
            raise ActionError("彩虹镜只能选择本回合未实际移动的己方单位。")
        destination = payload_position(payload)
        if destination not in self.legal_destinations(battle, actor, target):
            raise ActionError("彩虹镜需要声明周围合法落点。")
        return {"rainbow_landing": destination.to_dict()}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(payload_target_unit(battle, payload))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        target = battle.effect_recipient(target)
        ensure_ally(actor, target)
        if target.moved_this_turn or target.normal_move_steps_used > 0:
            raise ActionMiss("彩虹镜的声明目标已移动，无法再次搬运。")
        frozen = payload.get("queued_resolution") and "rainbow_landing" in payload
        destination = Position(**payload["rainbow_landing"]) if frozen else payload_position(payload)
        if not frozen:
            self.queued_payload_metadata(battle, actor, payload)
        if not battle.can_place_unit(target, destination, ignore=target, mover=target):
            raise ActionMiss("彩虹镜的声明落点已不合法。")
        battle.move_unit(target, destination, via_skill=True, allow_anywhere=True, forced=True, max_distance=max(battle.width, battle.height), tags={"rainbow_mirror"})
        if target.alive and not target.banished:
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
            if not battle.is_forced_movement_blocked(cell)
            and battle.can_place_unit(target, cell, ignore=target, mover=target)
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
            and not battle.effect_recipient(unit).moved_this_turn
            and battle.effect_recipient(unit).normal_move_steps_used <= 0
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        destinations_by_target = {
            target.unit_id: positions_to_dict(self.legal_destinations(battle, actor, battle.effect_recipient(target)))
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
        declared_attack = ctx.declared_source_attack if ctx.declared_source_attack is not None else ctx.source.stat("attack")
        if declared_attack < 3:
            return
        if ctx.is_skill or "attack" in ctx.tags:
            ctx.cancelled = True
            ctx.preserve_followup_effects = True
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

    def accepts_status(self, status: StatusEffect) -> bool:
        return not getattr(status, "is_skill_effect", True)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is ctx.target and ctx.is_skill and not ctx.damage_target:
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 不受技能非伤害效果影响。"

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        action = battle.resolving_action
        if self.owner is ctx.target and ("skill" in ctx.tags or (action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"})):
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 不受技能回复效果影响。"

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

    def on_owner_banished(self, battle: Battle) -> None:
        self.on_owner_removed(battle)

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


class WorldSeedSummon(AbstractHero):
    hero_code = "world_seed"
    hero_name = "世界之种"
    role = "地形单位"
    attribute = "木"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=5, defense=6, speed=0, attack_range=2, mana=1)
    footprint_width = 5
    footprint_height = 5
    stat_minimums = {"speed": 0.0, "mana": 0.0}
    raw_skill_text = ""
    raw_trait_text = "地形；不受技能非伤害效果；树根守护"

    def __init__(self, player_id: int, summoner_id: str) -> None:
        self.summoner_id = summoner_id
        self.world_seed_terrain = True
        self.standable_terrain = True
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id

    @property
    def magic_immunity(self) -> bool:
        ref = getattr(self, "_battle_ref", None)
        battle = ref() if ref is not None else None
        return bool(getattr(self, "_base_magic_immunity", False) or (
            battle is not None and 3 in alive_world_root_numbers(battle, self)
        ))

    @magic_immunity.setter
    def magic_immunity(self, value: bool) -> None:
        self._base_magic_immunity = value

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
    base_stats = Stats(attack=5, defense=6, speed=0, attack_range=2, mana=1)
    stat_minimums = {"speed": 0.0, "mana": 0.0}
    raw_skill_text = ""
    raw_trait_text = "地形；不受技能非伤害效果"

    def __init__(self, player_id: int, summoner_id: str, seed_id: str, root_number: int, direction: tuple[int, int]) -> None:
        self.summoner_id = summoner_id
        self.seed_id = seed_id
        self.root_number = root_number
        self.world_seed_terrain = True
        self.standable_terrain = True
        if direction[0] != 0:
            self.footprint_width = 2
            self.footprint_height = 1
        else:
            self.footprint_width = 1
            self.footprint_height = 2
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id

    def stat(self, stat_name: str) -> float:
        ref = getattr(self, "_battle_ref", None)
        battle = ref() if ref is not None else None
        seed = battle.units.get(self.seed_id) if battle is not None else None
        if seed is not None and seed.alive and not seed.banished:
            return seed.stat(stat_name)
        return super().stat(stat_name)

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
        if owner is None or not owner.alive or owner.position is None or getattr(self, "exploding", False):
            return
        overlapping = [unit for unit in battle.units_at_cells(battle.unit_cells(owner)) if unit.player_id != owner.player_id and unit.unit_id != owner.unit_id]
        if not overlapping:
            return
        self.exploding = True
        cells = square_around_cells(battle, battle.unit_cells(owner), radius=2)
        for target in battle.effect_units_at_cells(cells):
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
        if unit.alive and not unit.banished and unit.position is not None and getattr(unit, "hero_code", "") == "world_seed" and getattr(unit, "summoner_id", None) == summoner.unit_id
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
        if not unit.alive or unit.banished or unit.position is None or getattr(unit, "hero_code", "") != "world_root":
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
        return 0.0 if self.uses_this_turn == 0 else super().mana_cost_for_payload(battle, actor, payload)

    def summon_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        body = set(battle.unit_cells(actor))
        return sorted({pos for cell in body for pos in battle.neighbors(cell)
                       if pos not in body and (pos.x, pos.y) not in battle.blocked_cells and not battle.units_at(pos)},
                      key=lambda pos: (pos.y, pos.x))

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.summon_cells(battle, actor)
        if not cells:
            return False, "周围没有召唤审判之石的空格。"
        if payload is not None and payload.get("x") is not None and payload_position(payload) not in cells:
            return False, "审判之石只能召唤在周围空格。"
        return True, ""

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.summon_cells(battle, actor)), "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        pos = payload_position(payload)
        if pos not in self.summon_cells(battle, actor):
            raise ActionError("审判之石只能召唤在周围空格。")
        stone = JudgmentStoneSummon(actor.player_id, actor.unit_id)
        stone.can_act_on_entry_turn = 2 in alive_world_root_numbers(battle, actor)
        stone.turn_ready = stone.can_act_on_entry_turn
        battle.add_unit(stone, pos)


class WorldSeedSkill(Skill):
    EDGE_DEFS = {"north": ((2, 0), (0, -1)), "east": ((4, 2), (1, 0)),
                 "south": ((2, 4), (0, 1)), "west": ((0, 2), (-1, 0))}
    EDGE_NAMES = {"north": "上", "east": "右", "south": "下", "west": "左"}

    def __init__(self) -> None:
        super().__init__("world_seed", "世界之种", "大招：一场一次；选择三边树根编号，再选种子左上格，召唤完整世界之种和三根。", max_uses_per_battle=1, target_mode="cell")

    def _edges(self, payload: dict[str, Any]) -> list[str]:
        raw = str(payload["choice_code"]).split(",") if payload.get("choice_code") else payload.get("root_edges", payload.get("edges", ["north", "east", "south"]))
        if not isinstance(raw, (list, tuple)):
            raise ActionError("世界之种需要三条不同的边。")
        edges = [str(edge).lower() for edge in raw]
        if len(edges) != 3 or len(set(edges)) != 3 or any(edge not in self.EDGE_DEFS for edge in edges):
            raise ActionError("世界之种需要三条不同的边。")
        return edges

    def _numbers(self, payload: dict[str, Any], edges: list[str]) -> list[int]:
        raw = payload.get("root_numbers")
        try:
            nums = [1, 2, 3] if raw is None else [int(raw[edge]) for edge in edges] if isinstance(raw, dict) else [int(value) for value in raw]
        except (KeyError, TypeError, ValueError):
            raise ActionError("树根编号必须为 1、2、3。")
        if sorted(nums) != [1, 2, 3]:
            raise ActionError("树根编号必须为 1、2、3。")
        return nums

    def anchor(self, payload: dict[str, Any]) -> Position:
        if payload.get("cells") is not None:
            cells = payload.get("cells")
            if not isinstance(cells, list) or len(cells) != 1:
                raise ActionError("请选择世界之种的一个左上格。")
            return payload_position(cells[0])
        return payload_position(payload)

    def root_placements(self, anchor: Position, edges: list[str], numbers: list[int]) -> list[tuple[int, Position, tuple[int, int], list[Position]]]:
        specs = []
        for edge, number in zip(edges, numbers):
            midpoint, direction = self.EDGE_DEFS[edge]
            first = anchor.offset(midpoint[0] + direction[0], midpoint[1] + direction[1])
            second = first.offset(*direction)
            start = Position(min(first.x, second.x), min(first.y, second.y))
            specs.append((number, start, direction, [first, second]))
        return specs

    def layout_cells(self, anchor: Position, edges: list[str], numbers: list[int]) -> list[Position]:
        cells = [anchor.offset(dx, dy) for dy in range(5) for dx in range(5)]
        return cells + [cell for _, _, _, root in self.root_placements(anchor, edges, numbers) for cell in root]

    def legal_layout(self, battle: Battle, anchor: Position, edges: list[str], numbers: list[int]) -> bool:
        return all(battle.in_bounds(cell) and (cell.x, cell.y) not in battle.blocked_cells and not battle.units_at(cell)
                   for cell in self.layout_cells(anchor, edges, numbers))

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload is None or not any(key in payload for key in ("cells", "x", "root_edges", "choice_code")):
            return True, ""
        try:
            edges = self._edges(payload)
            if not self.legal_layout(battle, self.anchor(payload), edges, self._numbers(payload, edges)):
                return False, "世界之种及三条树根需要完整、未占用的落点。"
        except (ActionError, TypeError, ValueError, KeyError) as exc:
            return False, str(exc)
        return True, ""

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        choices, destinations = [], []
        anchors: set[Position] = set()
        layouts: dict[frozenset[str], list[Position]] = {}
        for edges_tuple in permutations(self.EDGE_DEFS, 3):
            edges = list(edges_tuple)
            key = frozenset(edges)
            if key not in layouts:
                layouts[key] = [Position(x, y) for y in range(max(0, battle.height - 4)) for x in range(max(0, battle.width - 4))
                                if self.legal_layout(battle, Position(x, y), edges, [1, 2, 3])]
            legal = layouts[key]
            if not legal:
                continue
            choice = ",".join(edges)
            choices.append({"code": choice, "label": " / ".join(f"{i}号{self.EDGE_NAMES[edge]}" for i, edge in enumerate(edges, 1)),
                            "patterns": [[pos.to_dict()] for pos in legal]})
            anchors.update(legal)
            for pos in legal:
                destinations.append({"choice_code": choice, "pattern": [pos.to_dict()],
                                     "destination_cells": positions_to_dict(self.layout_cells(pos, edges, [1, 2, 3]))})
        return {"cells": positions_to_dict(sorted(anchors, key=lambda pos: (pos.y, pos.x))), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True, "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False}}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        edges = self._edges(payload)
        return self.layout_cells(self.anchor(payload), edges, self._numbers(payload, edges))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        anchor = self.anchor(payload)
        edges = self._edges(payload)
        numbers = self._numbers(payload, edges)
        if not self.legal_layout(battle, anchor, edges, numbers):
            raise ActionError("世界之种及三条树根需要完整、未占用的落点。")
        seed = WorldSeedSummon(actor.player_id, actor.unit_id)
        battle.add_unit(seed, anchor)
        for number, start, direction, _ in self.root_placements(anchor, edges, numbers):
            root = WorldRootSummon(actor.player_id, actor.unit_id, seed.unit_id, number, direction)
            battle.add_unit(root, start)
            battle.log(f"{root.name} 编号为 {root.root_number}。")


class HeavenLockStatus(FlagStatus):
    def __init__(self, source_player_id: int, delay_enemy_turns: int = 2) -> None:
        self.source_player_id = source_player_id
        self.delay_enemy_turns = delay_enemy_turns
        self.activated = False
        self.flag_active = False
        super().__init__("天锁", "cannot_normal_move", description="第二个敌方英雄回合开始时生效，禁止普通移动 3 轮。")

    def bind(self, owner: HeroUnit) -> "HeavenLockStatus":
        StatusEffect.bind(self, owner)
        return self

    @property
    def provided_flags(self) -> frozenset[str]:
        return frozenset({"cannot_normal_move"}) if self.activated else frozenset()

    def on_any_turn_start(self, battle: Battle, active_unit: HeroUnit) -> None:
        owner = self.owner
        if owner is None or owner.banished or not owner.alive or self.activated or active_unit.player_id == self.source_player_id:
            return
        self.delay_enemy_turns -= 1
        if self.delay_enemy_turns <= 0:
            self.activated = True
            self.flag_active = True
            owner.cannot_normal_move = True
            self.duration = 3
            battle.log(f"{owner.name} 的【天锁】生效，不能普通移动。")

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.cannot_normal_move = any(
                (getattr(status, "flag_name", None) == "cannot_normal_move" and getattr(status, "value", True)
                 and (not isinstance(status, HeavenLockStatus) or status.activated))
                or "cannot_normal_move" in getattr(status, "provided_flags", ())
                for status in self.owner.statuses
            )

    def on_owner_banished(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)

    def on_owner_removed(self, battle: Battle) -> None:
        self.on_owner_banished(battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update({"delay_enemy_turns": self.delay_enemy_turns, "activated": self.activated})
        return data


class HeavenLockSkill(Skill):
    def __init__(self) -> None:
        super().__init__("heaven_lock", "天锁", "普通技能：费 1.5 魔；选定场上一个单位，在之后第二个对方回合开始时使其 3 轮无法移动。", mana_cost=1.5, target_mode="unit")
        self.requires_direct_unit_target_line = False

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.alive and unit.position is not None and not unit.banished
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        return {
            "cells": positions_to_dict([unit.position for unit in targets if unit.position is not None]),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        target = battle.effect_recipient(target)
        if target.has_status("天锁"):
            return
        apply_piercing_status_effect(
            battle,
            actor,
            target,
            action_name="天锁",
            status=HeavenLockStatus(actor.player_id),
            is_skill=True,
            tags={"skill", "heaven_lock"},
            refresh_existing=False,
        )

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class GhostStepSkill(DashMoveSkill):
    def __init__(self) -> None:
        super().__init__("ghost_step", "鬼步", "随时使用：费 1 魔；移动最多 2 格，每回合最多 3 次。", max_distance=2, mana_cost=1, max_uses_per_turn=3)
        self.timing = "instant"

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = battle.reachable_positions(actor, max_distance=2)
        if not cells:
            return False, "没有合法鬼步落点。"
        if payload is not None and payload.get("x") is not None and payload_position(payload) not in cells:
            return False, "鬼步落点不可达。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move or payload is None or payload.get("x") is None:
            return False, "请选择合法鬼步落点。"
        return (True, "") if payload_position(payload) in battle.reachable_positions(actor, max_distance=2) else (False, "鬼步落点不可达。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        return self.preview(battle, actor)


class IaidoAttackRules:
    def destination(self, payload: dict[str, Any] | None) -> Position | None:
        payload = payload or {}
        try:
            choice = str(payload.get("choice_code") or "")
            if choice.startswith("iaido:"):
                x, y = choice[6:].split(",")
                return Position(int(x), int(y))
            if payload.get("move_x") is not None and payload.get("move_y") is not None:
                return Position(int(payload["move_x"]), int(payload["move_y"]))
        except (TypeError, ValueError):
            return None
        return None

    def destinations(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.cannot_move or actor.position is None or battle.mounted_unit_for(actor) is not None:
            return []
        return battle.reachable_positions(actor, max_distance=1, exact_distance=1)

    def basic_attack_origins(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None) -> list[Position] | None:
        if not actor.has_status("聚气。拔刀斩") or (payload or {}).get("_attack_pre_move_done"):
            return None
        dest = self.destination(payload)
        destinations = self.destinations(battle, actor)
        return battle.unit_cells_at(actor, dest) if dest in destinations else [cell for pos in destinations for cell in battle.unit_cells_at(actor, pos)] if payload is None else []

    def valid_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit, dest: Position, payload: dict[str, Any]) -> bool:
        cells = battle.unit_cells_at(actor, dest)
        targets = battle.unit_cells(target)
        clicked = Position(int(payload["x"]), int(payload["y"])) if payload.get("x") is not None and payload.get("y") is not None else None
        if clicked is not None:
            targets = [clicked] if clicked in targets else []
        return any(origin.distance_to(cell) <= actor.targeting_range() for origin in cells for cell in targets)

    def can_attack_target_with_payload(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any] | None) -> tuple[bool, str]:
        if not actor.has_status("聚气。拔刀斩") or (payload or {}).get("_attack_pre_move_done"):
            return True, ""
        destinations = self.destinations(battle, actor)
        dest = self.destination(payload)
        if payload is None:
            return (True, "") if any(self.valid_target(battle, actor, target, pos, {}) for pos in destinations) else (False, "没有可命中目标的拔刀落点。")
        if dest not in destinations or not self.valid_target(battle, actor, target, dest, payload):
            return False, "拔刀斩需要合法的一格前移落点和移动后射程内的目标格。"
        return True, ""

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not actor.has_status("聚气。拔刀斩"):
            return {}
        dest = self.destination(payload)
        data = {"attack_power_override": 5, "ignore_shield": True, "cannot_evade": True}
        if dest is not None:
            data.update({"move_x": dest.x, "move_y": dest.y})
        return data

    def basic_attack_preview(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not actor.has_status("聚气。拔刀斩"):
            return None
        choices, cells, ids, destinations = [], [], set(), []
        for dest in self.destinations(battle, actor):
            patterns = []
            choice = f"iaido:{dest.x},{dest.y}"
            for target in battle.enemy_units(actor.player_id):
                if not battle.unit_can_be_selected(target, actor=actor)[0]:
                    continue
                for cell in battle.unit_cells(target):
                    candidate = {"choice_code": choice, "x": cell.x, "y": cell.y}
                    if not battle.attack_target_allowed(actor, target, payload=candidate)[0]:
                        continue
                    patterns.append([cell.to_dict()])
                    cells.append(cell)
                    ids.add(target.unit_id)
                    destinations.append({"choice_code": choice, "pattern": [cell.to_dict()], "destination_cells": positions_to_dict(battle.unit_cells_at(actor, dest))})
            if patterns:
                choices.append({"code": choice, "label": f"前移至 ({dest.x}, {dest.y})", "patterns": patterns})
        return {"cells": positions_to_dict(cells), "target_unit_ids": sorted(ids), "secondary_cells": [],
                "requires_target": True, "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False}}

    def before_basic_attack_resolution(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        if not actor.has_status("聚气。拔刀斩"):
            return True
        dest = self.destination(payload)
        origin = battle.declared_source_position(payload) if payload.get("queued_resolution") else actor.position
        if actor.position != origin or dest not in self.destinations(battle, actor):
            return False
        try:
            battle.move_unit(actor, dest, via_skill=True, max_distance=1, exact_distance=1, tags={"iaido_charge"})
        except ActionError:
            return False
        payload["_attack_pre_move_done"] = True
        return actor.alive and not actor.banished


class IaidoChargeStatus(IaidoAttackRules, StatusEffect):
    def __init__(self) -> None:
        super().__init__("聚气。拔刀斩", "下次普攻前速度视为1；选择前移一格后攻击，伤5、破魔、无法回避。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return 1.0 if stat_name == "speed" else value

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is not None:
            gained = self.owner.gain_mana_points(1)
            battle.log(f"{self.owner.name} 因【聚气。拔刀斩】获得 {gained} 点魔力点。")

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if self.owner is actor:
            actor.remove_status(self, battle)


class IaidoChargeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("iaido_charge", "聚气。拔刀斩", "普通技能：每回合1次；本回合不能攻击，下次普攻先移动1格，伤5、破魔、无法回避。", max_uses_per_turn=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not actor.has_status("聚气。拔刀斩"):
            actor.add_status(IaidoChargeStatus())
        if not actor.has_status("聚气当回合禁攻"):
            actor.add_status(FlagStatus("聚气当回合禁攻", "cannot_attack", duration=1))
        battle.log(f"{actor.name} 进入【聚气。拔刀斩】状态。")


class PerfectDeflectStatus(StatusEffect):
    def __init__(self, charges: int) -> None:
        self.charges = charges
        self.blocked_token = None
        super().__init__("剩余攻击挡开", f"可挡开{charges}次非破魔攻击或技能。", duration=1, tick_scope="owner_turn_start")

    def block(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        if owner is not ctx.target or source is None or source.player_id == owner.player_id or ctx.from_field_effect:
            return
        if ctx.ignore_shield or ctx.half_ignore_shield or not (ctx.is_skill or "attack" in ctx.tags):
            return
        token = battle._current_action_resolution_token
        if token is None:
            token = id(ctx)
        if self.blocked_token != token:
            if self.charges <= 0 or ctx.cancelled:
                return
            self.charges -= 1
            self.blocked_token = token
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【剩余攻击挡开】挡住了【{ctx.action_name}】。"

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if ctx.is_skill and not ctx.damage_target and "attack" not in ctx.tags:
            self.block(battle, ctx)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self.block(battle, ctx)

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
        remaining = max(0, owner.attack_actions_per_turn() - owner.attacks_used)
        old = owner.get_status("剩余攻击挡开")
        if old is not None:
            owner.remove_status(old, battle)
        if remaining > 0:
            owner.add_status(PerfectDeflectStatus(remaining))
            battle.log(f"{owner.name} 将 {remaining} 次剩余攻击转为挡开次数。")


class TimeStopSkill(Skill):
    def __init__(self) -> None:
        super().__init__("time_stop", "时停", "随时大招：敌方回合且周围5×5有敌人时，结束当前回合，临时插入只有自身可行动的回合。", max_uses_per_battle=1, target_mode="self", timing="instant")

    def condition(self, battle: Battle, actor: HeroUnit) -> tuple[bool, str]:
        if actor.player_id == battle.active_player:
            return False, "时停只能在对方回合使用。"
        if not any(unit.alive and not unit.banished and unit.position is not None and battle.distance_between_units(actor, unit) <= 2
                   for unit in battle.enemy_units(actor.player_id)):
            return False, "时停需要5×5内有对方单位。"
        return True, ""

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        return self.condition(battle, actor) if ok else (ok, reason)

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        return self.condition(battle, actor) if ok else (ok, reason)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.unit_id not in battle.turn_order_unit_ids:
            return
        battle._pending_exclusive_turn_unit_id = actor.unit_id
        battle.pending_followup_actions.clear()
        battle._separate_attack_sequences.clear()
        battle.pending_chain = None
        battle.log(f"{actor.name} 发动【时停】，结束当前回合并插入自己的独占回合。")
        battle.resolve_turn_end()


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
        owner.max_mana_points = 3.0
        owner.mana_points = min(3.0, owner.mana_points)
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        count = sum(1 for unit in battle.player_units(owner.player_id) if unit.position is not None and not unit.banished and "D。" in unit.name)
        before = owner.mana_points
        owner.mana_points = round(min(3.0, owner.mana_points + count), 2)
        gained = round(owner.mana_points - before, 2)
        if gained:
            battle.log(f"{owner.name} 因 D。名字获得 {gained} 点魔力点。")


class MimicSkill(Skill):
    excluded_codes = {"mimic_skill", "agency_borrowed_skill", "agency_contract"}

    def __init__(self) -> None:
        super().__init__("mimic_skill", "模仿", "普通技能：消耗1魔力点，模仿11×11内可见武将主动/瞬发技能；实际魔费减3，保留其他成本及自身限制。", target_mode="unit")
        self.requires_direct_unit_target_line = False
        self.borrowed: dict[str, Skill] = {}

    def runtime_children(self):
        return self.borrowed.values()

    def copyable(self, skill: Skill) -> bool:
        return skill.timing in {"active", "instant"} and skill.code not in self.excluded_codes

    def source_skills(self, source: HeroUnit) -> list[Skill]:
        # An installed borrowed skill is an execution aid, not a new public provider.
        return getattr(source, "_mimic_source_skills", source.skills)

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [unit for unit in battle.all_units()
                if not unit.is_summon and not unit.is_clone and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.distance_between_units(actor, unit) <= 5
                and any(self.copyable(skill) for skill in self.source_skills(unit))]

    def borrowed_skill(self, actor: HeroUnit, source: HeroUnit, code: str) -> Skill:
        definition = next((skill for skill in self.source_skills(source) if skill.code == code), None)
        if definition is None:
            raise ActionError("该武将没有可提供的模仿技能。")
        if not self.copyable(definition):
            raise ActionError("不能模仿被动技能或借技包装技能。")
        own = next((skill for skill in actor.skills if skill.code == code), None)
        if own is not None:
            return own
        if code not in self.borrowed:
            fresh = next((skill for skill in source.build_skills() if skill.code == code), None)
            if fresh is None:
                raise ActionError("该技能没有可模仿的公开定义。")
            fresh.bind(actor)
            legacy = getattr(actor, "_mimic_uses", {}).get(code, {})
            fresh.uses_this_battle = int(legacy.get("battle_uses", 0))
            fresh.uses_this_turn = int(legacy.get("turn_uses", 0))
            fresh._uses_turn_number = legacy.get("turn")
            self.borrowed[code] = fresh
        return self.borrowed[code]

    @contextmanager
    def copying(self, actor: HeroUnit, copied: Skill, *, reserve_point: bool = False):
        # Quote the discounted mana cost while preserving all custom payment behavior.
        skills, points = actor.skills, actor.mana_points
        source_skills = getattr(actor, "_mimic_source_skills", None)
        actor._mimic_source_skills = source_skills if source_skills is not None else skills
        point_reserved = getattr(actor, "_mimic_point_reserved", False)
        reserve_here = reserve_point and not point_reserved
        original_cost = copied.mana_cost_for_payload
        nested = getattr(copied, "_mimic_discount_active", False)
        had_override = "mana_cost_for_payload" in copied.__dict__
        old_override = copied.__dict__.get("mana_cost_for_payload")
        actor.skills = [*skills] if copied in skills else [*skills, copied]
        if not nested:
            copied.mana_cost_for_payload = lambda battle, caster, payload=None: max(0.0, original_cost(battle, caster, payload) - 3.0)
            copied._mimic_discount_active = True
        if reserve_here:
            actor.mana_points -= 1
            actor._mimic_point_reserved = True
        try:
            yield
        finally:
            actor.skills = skills
            if source_skills is None:
                actor.__dict__.pop("_mimic_source_skills", None)
            else:
                actor._mimic_source_skills = source_skills
            if not nested:
                copied.__dict__.pop("_mimic_discount_active", None)
            if reserve_here:
                actor.mana_points = points
                actor.__dict__.pop("_mimic_point_reserved", None)
            if had_override:
                copied.mana_cost_for_payload = old_override
            else:
                copied.__dict__.pop("mana_cost_for_payload", None)

    def _target_skill(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[HeroUnit, Skill, dict[str, Any]]:
        source = battle.get_unit(str(payload.get("mimic_source_id") or payload.get("target_unit_id") or ""))
        if source not in self.targets(battle, actor):
            raise ActionError("模仿只能选择11×11内可见武将本体。")
        code = str(payload.get("mimic_skill_code") or payload.get("copied_skill_code") or "")
        copied = self.borrowed_skill(actor, source, code)
        inner = dict(payload.get("mimic_payload") or payload.get("copied_payload") or {})
        for key in list(inner):
            if key.startswith(("declared_", "_")) or key in {"resources_prepaid", "queued_resolution", "resolved_target_unit_id", "declaration_id"}:
                inner.pop(key)
        inner.update(type="skill", unit_id=actor.unit_id, skill_code=code)
        return source, copied, inner

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.mana_points < 1:
            return False, "魔力点不足。"
        if not payload:
            return (True, "") if self.targets(battle, actor) else (False, "周围没有可模仿的武将。")
        try:
            _, copied, inner = self._target_skill(battle, actor, payload)
            with self.copying(actor, copied, reserve_point=True):
                return copied.can_use(battle, actor, inner)
        except (ActionError, AttributeError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc) or "当前不满足被模仿技能的施放条件。"

    def validate_inner_selection(self, battle: Battle, actor: HeroUnit, copied: Skill, inner: dict[str, Any]) -> None:
        preview = copied.preview(battle, actor)
        if copied.target_mode in {"ally", "enemy", "unit"} and inner.get("target_unit_id"):
            target = battle.get_unit(inner["target_unit_id"])
            if copied.target_mode == "ally":
                ensure_ally(actor, target)
            elif copied.target_mode == "enemy":
                ensure_enemy(actor, target)
            if preview.get("requires_target") and target.unit_id not in preview.get("target_unit_ids", []):
                raise ActionError("请选择被模仿技能允许的目标。")
        elif copied.target_mode in {"ally", "enemy", "unit"} and preview.get("requires_target") and not inner.get("target_unit_ids") and not inner.get("cells"):
            raise ActionError("请为被模仿技能选择实际目标。")
        selection = preview.get("selection") or {}
        patterns = selection.get("patterns")
        if selection.get("mode") == "choice_pattern":
            choice = next((item for item in selection.get("choices", []) if item.get("code") == inner.get("choice_code")), None)
            if choice is None:
                raise ActionError("请先选择被模仿技能的模式。")
            patterns = choice.get("patterns", [])
        if selection.get("mode") in {"pattern_cells", "choice_pattern"}:
            match_payload_pattern(inner, [[Position(int(cell["x"]), int(cell["y"])) for cell in pattern] for pattern in patterns or []])
        elif copied.target_mode == "cell" and preview.get("requires_target") and inner.get("x") is not None:
            if payload_position(inner).to_dict() not in preview.get("cells", []):
                raise ActionError("被模仿技能的落点不合法。")

    def build_wrapped_action(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        source, copied, inner = self._target_skill(battle, actor, payload)
        with self.copying(actor, copied, reserve_point=True):
            self.validate_inner_selection(battle, actor, copied, inner)
            declared = battle.build_queued_action(inner)
        inner = dict(declared.payload)
        declared.payload.update(skill_code=self.code, mimic_source_id=source.unit_id,
                                mimic_source_name=source.name, mimic_skill_code=copied.code, copied_payload=inner)
        declared.display_name = f"模仿·{copied.name}"
        return declared

    def declared_skill(self, actor: HeroUnit, payload: dict[str, Any]) -> Skill:
        code = str(payload.get("mimic_skill_code") or "")
        return next((skill for skill in actor.skills if skill.code == code), None) or self.borrowed[code]

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not payload.get("mimic_source_id") or not payload.get("queued_resolution"):
            ok, reason = self.can_use(battle, actor, payload)
            if not ok:
                raise ActionError(reason)
            payload.update(self.build_wrapped_action(battle, actor, payload).payload)
        copied = self.declared_skill(actor, payload)
        actor.spend_mana_points(1)
        with self.copying(actor, copied):
            copied.prepay_resources(battle, actor, payload["copied_payload"])
        self.sync_turn_scope(battle)
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if self.owner is not None and action_type == "skill" and payload.get("skill_code") == self.code:
            copied = self.declared_skill(self.owner, payload)
            copied.on_owner_action_declared(battle, action_type, payload["copied_payload"])

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        copied = self.declared_skill(actor, payload)
        inner = dict(payload["copied_payload"])
        inner.update(resources_prepaid=True, queued_resolution=True)
        target = battle.resolve_declared_target_unit(actor, inner, ignore_stealth=bool(inner.get("ignore_stealth")))
        inner["resolved_target_unit_id"] = target.unit_id if target is not None else None
        battle.log(f"{actor.name} 模仿 {payload.get('mimic_source_name', '武将')} 的【{copied.name}】。")
        try:
            with self.copying(actor, copied):
                battle.use_skill(actor, copied.code, inner)
        finally:
            actor.gain_mana_points(0)

    def reaction_window_timing(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        copied = self.declared_skill(actor, payload)
        return copied.reaction_window_timing(battle, actor, payload["copied_payload"])

    def on_owner_turn_start(self, battle: Battle) -> None:
        super().on_owner_turn_start(battle)
        for skill in [item for item in self.borrowed.values() if item not in self.owner.skills]:
            skill.on_owner_turn_start(battle)

    def on_owner_turn_end(self, battle: Battle) -> None:
        for skill in [item for item in self.borrowed.values() if item not in self.owner.skills]:
            skill.on_owner_turn_end(battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        super().on_any_turn_end(battle, ended_player_id)
        for skill in [item for item in self.borrowed.values() if item not in self.owner.skills]:
            skill.on_any_turn_end(battle, ended_player_id)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        entries = []
        for source in self.targets(battle, actor):
            skills = []
            for definition in self.source_skills(source):
                if not self.copyable(definition):
                    continue
                copied = self.borrowed_skill(actor, source, definition.code)
                try:
                    with self.copying(actor, copied, reserve_point=True):
                        ok, reason = copied.can_use(battle, actor, {})
                        data = copied.to_public_dict(battle)
                        preview = battle.filter_preview_targets(
                            actor, copied.preview(battle, actor), ignore_stealth=copied.ignores_stealth_for_payload(battle, actor, {}),
                            replace_cells=copied.target_mode in {"ally", "enemy", "unit"},
                            require_line_targeting=copied.target_mode in {"ally", "enemy", "unit"} and copied.requires_direct_unit_target_line,
                            line_target_range=copied.direct_unit_target_range(battle, actor, {}))
                    data.update(kind="skill", available=ok and actor.mana_points >= 1, reason=reason, preview=preview)
                    skills.append({"code": copied.code, "name": copied.name, "action": data})
                except (ActionError, AttributeError, KeyError, TypeError, ValueError):
                    continue
            entries.append({"unit_id": source.unit_id, "name": source.name, "skills": skills})
        return {"cells": positions_to_dict([cell for source in self.targets(battle, actor) for cell in battle.unit_cells(source)]),
                "target_unit_ids": [entry["unit_id"] for entry in entries], "requires_target": True,
                "selection": {"mode": "mimic_skill", "targets": entries}}

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        _, copied, inner = self._target_skill(battle, actor, payload)
        with self.copying(actor, copied):
            return copied.get_target_units_for_payload(battle, actor, inner)

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        _, copied, inner = self._target_skill(battle, actor, payload)
        with self.copying(actor, copied):
            return copied.get_target_cells_for_payload(battle, actor, inner)


class FriedInspireStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("鼓舞", "本回合移动次数 +1，速度 *2。", duration=1, tick_scope="any_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value * 2 if stat_name == "speed" else value

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + 1


class FriedInspireSkill(Skill):
    def __init__(self) -> None:
        super().__init__("fried_inspire", "鼓舞", "普通技能：周围3*3己方单位本回合移动次数+1，速度*2，同名不叠加；普通不限次，另有每回合一次免费使用。", target_mode="ally")

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return 1

    def free_available(self, battle: Battle) -> bool:
        return getattr(self, "_free_turn_number", None) != battle.turn_number

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        return 0.0 if self.free_available(battle) else super().mana_cost_for_payload(battle, actor, payload)

    def validate_target(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> HeroUnit:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, 1, action_name=self.name)
        return target

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            try:
                self.validate_target(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.player_units(actor.player_id)
                   if battle.unit_can_be_selected(unit, actor=actor)[0] and battle.unit_target_in_range_and_line(actor, unit, 1)]
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets], "requires_target": True,
                "free_use_available": self.free_available(battle)}

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.validate_target(battle, actor, payload)
        self.sync_turn_scope(battle)
        if self.free_available(battle):
            self._free_turn_number = battle.turn_number
            self.uses_this_battle += 1
        else:
            super().prepay_resources(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = self.validate_target(battle, actor, payload)
        if target.has_status("鼓舞"):
            battle.log(f"{target.name} 本回合已受鼓舞，同名效果不叠加。")
            return
        target.add_status(FriedInspireStatus(), source=actor)
        battle.log(f"{target.name} 受到【鼓舞】，本回合速度翻倍且移动次数 +1。")


class RoyalSoldierAuraBorrowTrait(Trait):
    """State-free projected traits shared by every allied non-hero in the aura."""
    def __init__(self, summoner_id: str | None = None) -> None:
        super().__init__("弗里德共享", "周围统帅共享：攻击己方加血、格挡反击、自然回魔与当前正数能力加成；多源不叠加。")

    def sources(self) -> list[HeroUnit]:
        owner = self.owner
        ref = getattr(owner, "_battle_ref", None)
        battle = ref() if ref else None
        if battle is None or owner is None:
            return []
        return [unit for unit in battle.all_units()
                if any(isinstance(trait, FriedAuraTrait) and trait.covers(battle, owner) for trait in unit.traits)]

    def grants_block_counter(self, battle: Battle, unit: HeroUnit) -> bool:
        return bool(self.sources())

    def allied_basic_heal_amount(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> float:
        return 0.25 if actor.player_id == target.player_id and self.sources() else 0.0

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None and self.sources() and not any(trait.name == "自然回魔" for trait in owner.traits):
            gained = owner.gain_mana(1)
            if gained:
                battle.log(f"{owner.name} 借用弗里德的自然回魔，魔 +{gained}。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        delta = max((max(0.0, source.stat(stat_name) - float(getattr(source.base_stats, stat_name)))
                     for source in self.sources()), default=0.0)
        return value + delta


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
        return []


class RoyalSoldierSkill(Skill):
    selection_cells_are_effect_cells = False

    def __init__(self) -> None:
        super().__init__("royal_soldier", "皇家士兵", "普通技能：每回合2次；攻/守/范分配10点，每项1~5，速2魔0；场上己方同名最多4个。", target_mode="cell", max_uses_per_turn=2)

    def allocation(self, payload: dict[str, Any]) -> tuple[int, int, int]:
        if payload.get("choice_code"):
            raw = str(payload["choice_code"]).split(",")
        else:
            raw = [payload.get("attack", payload.get("soldier_attack")),
                   payload.get("defense", payload.get("soldier_defense")),
                   payload.get("range", payload.get("attack_range", payload.get("soldier_range")))]
        if len(raw) != 3 or any(isinstance(value, bool) or not re.fullmatch(r"[1-5]", str(value)) for value in raw):
            raise ActionError("皇家士兵的攻/守/范必须是1~5的整数。")
        values = tuple(int(value) for value in raw)
        if sum(values) != 10:
            raise ActionError("皇家士兵的攻/守/范总和必须为10。")
        return values

    def destination(self, payload: dict[str, Any]) -> Position:
        if "cells" in payload:
            if not isinstance(payload["cells"], list) or len(payload["cells"]) != 1:
                raise ActionError("皇家士兵需要一个空落点。")
            return payload_position(payload["cells"][0])
        return payload_position(payload)

    def present_count(self, battle: Battle, actor: HeroUnit) -> int:
        return sum(unit.name == RoyalSoldierSummon.hero_name and unit.position is not None and not unit.banished
                   for unit in battle.player_units(actor.player_id))

    def legal_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None or self.present_count(battle, actor) >= 4:
            return []
        return [Position(x, y) for y in range(battle.height) for x in range(battle.width)
                if battle.unit_distance_to_cell(actor, Position(x, y)) <= actor.targeting_range()
                and (x, y) not in battle.blocked_cells and not battle.units_at(Position(x, y))]

    def validate_selection(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[tuple[int, int, int], Position]:
        allocation, destination = self.allocation(payload), self.destination(payload)
        if destination not in self.legal_cells(battle, actor):
            raise ActionError("皇家士兵需要范围内空格，且场上己方同名少于4个。")
        return allocation, destination

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if self.present_count(battle, actor) >= 4:
            return False, "场上己方皇家士兵已达4个。"
        if payload and any(key in payload for key in ("choice_code", "attack", "soldier_attack", "cells", "x")):
            try:
                self.validate_selection(battle, actor, payload)
            except (ActionError, TypeError, ValueError) as exc:
                return False, str(exc)
        return True, ""

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.validate_selection(battle, actor, payload)
        super().prepay_resources(battle, actor, payload)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.legal_cells(battle, actor)
        choices = [{"code": f"{attack},{defense},{attack_range}", "label": f"攻{attack} / 守{defense} / 范{attack_range}",
                    "patterns": [[cell.to_dict()] for cell in cells]}
                   for attack in range(1, 6) for defense in range(1, 6) for attack_range in range(1, 6)
                   if attack + defense + attack_range == 10]
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "requires_target": True,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                              "choice_prompt": "先分配皇家士兵的10点属性，再选择空落点。",
                              "cell_prompt": "选择范围内一个蓝色空格，再完成召唤。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return []

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        try:
            (attack, defense, attack_range), pos = self.validate_selection(battle, actor, payload)
        except ActionError as exc:
            if payload.get("queued_resolution"):
                raise ActionMiss("皇家士兵原召唤位置或名额已失效，本次召唤落空。") from exc
            raise
        soldier = RoyalSoldierSummon(actor.player_id, actor.unit_id, attack, defense, attack_range)
        soldier.turn_ready = False
        soldier.can_act_on_entry_turn = True  # First future owner-turn refresh enables it.
        battle.add_unit(soldier, pos)


class FriedAllyAttackHealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击己方加血", "可以普攻己方单位；命中己方时不造成伤害，改为治疗 1/4。")

    def allied_basic_heal_amount(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> float:
        return 0.25 if actor.player_id == target.player_id else 0.0

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.target.player_id != owner.player_id:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 攻击己方，改为治疗。"
        battle.heal(HealContext(source=owner, target=ctx.target, amount=0.25, action_name="攻击己方加血"))


class FriedAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("弗里德统帅", "周围非武将己方单位动态获得弗里德常驻特性和当前数值加成。")

    def covers(self, battle: Battle, unit: HeroUnit) -> bool:
        owner = self.owner
        if owner is None or owner.is_summon or owner.is_clone or not owner.alive or owner.banished or owner.position is None:
            return False
        if not unit.alive or unit.banished or unit.position is None or not (unit.is_summon or unit.is_clone) or unit.player_id != owner.player_id:
            return False
        body = set(battle.unit_cells_at(owner, getattr(owner, "_resolution_actual_position", owner.position)))
        ring = set(square_around_cells(battle, list(body), radius=1)) - body
        return bool(ring.intersection(battle.unit_cells_at(unit, getattr(unit, "_resolution_actual_position", unit.position))))

    def projected_trait_for(self, battle: Battle, unit: HeroUnit) -> Trait | None:
        if not self.covers(battle, unit):
            return None
        return RoyalSoldierAuraBorrowTrait().bind(unit)


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


    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name, area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", "attack", "pierce"}))


class ManaDrainImmunityTrait(Trait):
    prevents_mana_drain = True

    def __init__(self) -> None:
        super().__init__("无法被吸魔", "免疫双方吸魔效果。")


class AgencyDefenseStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("代行解除守备", defense_delta=4, duration=2, tick_scope="owner_turn_end", description="直到下回合结束前守 +4。")


class AgencyAttachedStatus(StatusEffect):
    provided_flags = ("cannot_move", "cannot_be_targeted")

    def __init__(self, carrier_id: str, stat_name: str, copied_skill_code: str) -> None:
        super().__init__("代行契约附着", "随载体移动，免伤免效果；借用一项当前属性及一个技能，保留自身攻击和施法。", duration=None)
        self.carrier_id, self.stat_name, self.copied_skill_code = carrier_id, stat_name, copied_skill_code

    def carrier(self, battle: Battle) -> HeroUnit | None:
        unit = battle.units.get(self.carrier_id)
        return unit if unit is not None and unit.alive else None

    def bind(self, owner: HeroUnit) -> "AgencyAttachedStatus":
        super().bind(owner)
        owner.attached_to_unit_id = self.carrier_id
        owner.cannot_move = owner.cannot_be_targeted = True
        return self

    def sync_linked_state(self, battle: Battle) -> None:
        owner, carrier = self.owner, self.carrier(battle)
        if owner is None or not owner.alive:
            return
        if carrier is None:
            self.detach(battle)
            return
        carrier_status = carrier.get_status("代行契约附着")
        if isinstance(carrier_status, AgencyAttachedStatus):
            carrier_status.sync_linked_state(battle)
        self.last_carrier_position = getattr(carrier, "_resolution_actual_position", carrier.position) or getattr(self, "last_carrier_position", owner.position)
        if not hasattr(owner, "_resolution_actual_position"):
            owner.position = self.last_carrier_position
        owner.banished = carrier.banished or carrier.position is None
        owner.banish_turns_remaining = carrier.banish_turns_remaining
        owner.banish_return_position = self.last_carrier_position

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.sync_linked_state(battle)

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        self.sync_linked_state(battle)

    def detach(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        carrier = self.carrier(battle)
        destroyed = next((unit for unit in battle.destroyed_units if unit.unit_id == self.carrier_id), None)
        origin = ((getattr(carrier, "_resolution_actual_position", carrier.position) if carrier else None)
                  or (getattr(destroyed, "last_position", None) if destroyed else None)
                  or getattr(self, "last_carrier_position", owner.position))
        owner.remove_status(self, battle)
        owner.banish_return_position = origin
        owner.banish_turns_remaining = 0
        choices = battle.respawn_options_for(owner)
        if choices:
            owner.position, owner.banished = choices[0], False
        else:
            owner.position, owner.banished = origin, True
            battle.log(f"{owner.name} 解除附着后没有合法落点，暂时等待重新出现。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        ref = getattr(self.owner, "_battle_ref", None)
        battle = ref() if ref else None
        carrier = self.carrier(battle) if battle else None
        return carrier.stat(stat_name) if carrier is not None and stat_name == self.stat_name else value

    def blocks_direct_effects(self) -> bool:
        return True

    def limit_hp_loss(self, amount: float) -> float:
        return amount if getattr(self.owner, "_paying_skill_cost", False) else 0.0

    def accepts_status(self, status: StatusEffect) -> bool:
        return False

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is ctx.target and not getattr(self.owner, "_paying_skill_cost", False):
            ctx.cancelled, ctx.reason = True, "附着期间不受伤害和效果影响。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and not getattr(self.owner, "_paying_skill_cost", False):
            ctx.cancelled, ctx.reason = True, "附着期间不受伤害和效果影响。"

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        if self.owner is ctx.target:
            ctx.cancelled = True

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.__dict__.pop("attached_to_unit_id", None)
        for flag in self.provided_flags:
            setattr(owner, flag, any(flag in getattr(status, "provided_flags", ()) or
                                    (getattr(status, "flag_name", "") in {flag, "cannot_act"} and getattr(status, "value", True))
                                    for status in owner.statuses))
        wrapper = next((skill for skill in owner.skills if skill.code == "agency_borrowed_skill"), None)
        if wrapper is not None:
            wrapper.timing = "active"

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(carrier_id=self.carrier_id, stat_name=self.stat_name, copied_skill_code=self.copied_skill_code)
        return data


class AgencyBorrowedSkill(MimicSkill):
    MOVEMENT_SKILL_CODES = {"fly_leap", "fate_kick", "crazy_sand", "plasma_thruster", "zero_dash", "fuma_pursuit",
                           "fantasy_move", "true_blade_air_slash", "mounted_leap", "frey_quick_flash", "teleport",
                           "descent_moment", "earth_walker", "split", "leap", "jirobo_follow_step", "iron_chain_path", "ghost_step"}

    def __init__(self) -> None:
        Skill.__init__(self, "agency_borrowed_skill", "代行技能", "使用已选技能；完整费用与限制按暮别自身计算，跨契约保留。", target_mode="none")
        self.requires_direct_unit_target_line = False
        self.target_unit_is_anchor = True
        self.borrowed: dict[str, Skill] = {}

    @contextmanager
    def copying(self, actor: HeroUnit, copied: Skill, **kwargs):
        previous = actor.skills
        actor.skills = previous if copied in previous else [*previous, copied]
        try:
            yield
        finally:
            actor.skills = previous

    def attached_status(self, actor: HeroUnit) -> AgencyAttachedStatus:
        status = actor.get_status("代行契约附着")
        if not isinstance(status, AgencyAttachedStatus):
            raise ActionError("未处于代行契约附着状态。")
        return status

    def target_skill(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[HeroUnit, Skill, dict[str, Any]]:
        status = self.attached_status(actor)
        carrier = status.carrier(battle)
        if carrier is None or carrier.banished or carrier.position is None:
            raise ActionError("载体不在战场上。")
        code = status.copied_skill_code
        copied = next((item for item in actor.skills if item.code == code), None) or self.borrowed.get(code)
        if copied is None:
            copied = self.borrowed_skill(actor, carrier, code)
        inner = dict(payload.get("contract_payload") or payload.get("copied_payload") or {})
        for key in list(inner):
            if key.startswith(("declared_", "_")) or key in {"resources_prepaid", "queued_resolution", "resolved_target_unit_id", "declaration_id"}:
                inner.pop(key)
        inner.update(type="skill", unit_id=actor.unit_id, skill_code=code)
        return carrier, copied, inner

    _target_skill = target_skill

    def movement_blocked(self, copied: Skill) -> bool:
        return copied.code in self.MOVEMENT_SKILL_CODES

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        try:
            _, copied, inner = self.target_skill(battle, actor, payload or {})
            if self.movement_blocked(copied):
                return False, "附着期间不能使用独立位移技能。"
            with self.copying(actor, copied):
                return copied.can_use(battle, actor, inner)
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        try:
            _, copied, inner = self.target_skill(battle, actor, payload or {})
            with self.copying(actor, copied):
                return copied.mana_cost_for_payload(battle, actor, inner)
        except ActionError:
            return 0.0

    def build_wrapped_action(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        carrier, copied, inner = self.target_skill(battle, actor, payload)
        if self.movement_blocked(copied):
            raise ActionError("附着期间不能使用独立位移技能。")
        with self.copying(actor, copied):
            self.validate_inner_selection(battle, actor, copied, inner)
            reaction_to = battle.pending_chain.queued_action if battle.pending_chain is not None and copied.timing == "instant" else None
            declared = battle.build_queued_action(inner, reaction_to=reaction_to)
        inner = dict(declared.payload)
        declared.payload.update(skill_code=self.code, agency_skill_code=copied.code,
                                agency_carrier_id=carrier.unit_id, copied_payload=inner)
        declared.display_name = f"代行·{copied.name}"
        return declared

    def declared_skill(self, actor: HeroUnit, payload: dict[str, Any]) -> Skill:
        code = str(payload.get("agency_skill_code") or "")
        return next((item for item in actor.skills if item.code == code), None) or self.borrowed[code]

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not payload.get("agency_skill_code"):
            payload.update(self.build_wrapped_action(battle, actor, payload).payload)
        copied = self.declared_skill(actor, payload)
        with self.copying(actor, copied):
            battle.prepay_skill_resources(copied, actor, payload["copied_payload"])
        self.sync_turn_scope(battle)
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        copied = self.declared_skill(actor, payload)
        inner = dict(payload["copied_payload"])
        inner.update(resources_prepaid=True, queued_resolution=True)
        target = battle.resolve_declared_target_unit(actor, inner, ignore_stealth=bool(inner.get("ignore_stealth")))
        inner["resolved_target_unit_id"] = target.unit_id if target is not None else None
        with self.copying(actor, copied):
            battle.resolve_from_declared_origin(actor, inner, lambda: battle.use_skill(actor, copied.code, inner))

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        copied = self.declared_skill(actor, payload)
        inner = dict(payload["copied_payload"], resources_prepaid=True, queued_resolution=True)
        target = battle.resolve_declared_target_unit(actor, inner, ignore_stealth=bool(inner.get("ignore_stealth")))
        inner["resolved_target_unit_id"] = target.unit_id if target is not None else None
        try:
            with self.copying(actor, copied):
                battle.resolve_from_declared_origin(actor, inner, lambda: copied.react(battle, actor, inner, queued_action))
                copied.finalize_use(battle, actor)
        except ActionMiss as exc:
            battle.log(str(exc) or "代行技能落在原定位置，未命中目标。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        try:
            _, copied, _ = self.target_skill(battle, actor, {})
            if self.movement_blocked(copied):
                return False, "附着期间不能使用独立位移技能。"
            with self.copying(actor, copied):
                return copied.can_react_to(battle, actor, queued_action)
        except ActionError as exc:
            return False, str(exc)

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload=None):
        ok, reason = self.can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        _, copied, inner = self.target_skill(battle, actor, payload or {})
        with self.copying(actor, copied):
            return copied.can_react_with_payload(battle, actor, queued_action, inner)

    def queued_reaction_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        return self.build_wrapped_action(battle, actor, payload).payload

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        try:
            carrier, copied, _ = self.target_skill(battle, actor, {})
            with self.copying(actor, copied):
                ok, reason = self.can_use(battle, actor, {})
                data = copied.to_public_dict(battle)
                preview = battle.filter_preview_targets(actor, copied.preview(battle, actor),
                    replace_cells=copied.target_mode in {"ally", "enemy", "unit"},
                    require_line_targeting=copied.requires_direct_unit_target_line and copied.target_mode in {"ally", "enemy", "unit"},
                    line_target_range=copied.direct_unit_target_range(battle, actor, {}))
            data.update(kind="skill", preview=preview, available=ok, reason=reason)
            return {"cells": [], "target_unit_ids": [], "requires_target": True,
                    "selection": {"mode": "mimic_skill", "wrapper_code": self.code, "targets": [
                        {"unit_id": carrier.unit_id, "name": carrier.name, "skills": [
                            {"code": copied.code, "name": copied.name, "action": data}]}]}}
        except ActionError:
            return {"cells": [], "target_unit_ids": [], "requires_target": False}

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any):
        preview = self.preview(battle, actor)
        ok, reason = self.can_react_to(battle, actor, queued_action)
        for target in preview.get("selection", {}).get("targets", []):
            for entry in target["skills"]:
                entry["action"].update(available=ok, reason=reason)
        return preview


class AgencyContractSkill(Skill):
    VALID_STATS = ("attack", "defense", "speed", "attack_range", "mana")
    excludes_allies_from_effect = True
    resolves_from_current_position = True
    selection_cells_are_effect_cells = False

    def __init__(self) -> None:
        super().__init__("agency_contract", "代行契约", "每自己回合1次，绑定可见友方英雄或主动解除。借一项当前属性和一个技能；解除守+4并对声明外圈敌人伤害吸魔。", target_mode="none", max_uses_per_turn=1)
        self.requires_direct_unit_target_line = False
        self.target_unit_is_anchor = True

    def wrapper(self, actor: HeroUnit) -> AgencyBorrowedSkill:
        return actor.get_skill("agency_borrowed_skill")

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [unit for unit in battle.player_units(actor.player_id)
                if unit is not actor and not unit.is_clone and not unit.is_summon
                and not getattr(unit, "attached_to_unit_id", None)
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and any(self.wrapper(actor).copyable(skill) for skill in unit.skills)]

    def validate_binding(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            raise ActionError("请选择可见的其他友方英雄本体作为载体。")
        stat = str(payload.get("stat_name") or "")
        code = str(payload.get("copied_skill_code") or payload.get("contract_skill_code") or "")
        if stat not in self.VALID_STATS:
            raise ActionError("请选择要借用的一项属性。")
        copied = self.wrapper(actor).borrowed_skill(actor, target, code)
        return target, stat, copied

    def can_use(self, battle: Battle, actor: HeroUnit, payload=None):
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or actor.has_status("代行契约附着"):
            return ok, reason
        if not payload:
            return (True, "") if self.targets(battle, actor) else (False, "没有可用载体。")
        try:
            self.validate_binding(battle, actor, payload)
            return True, ""
        except (ActionError, KeyError, ValueError, TypeError) as exc:
            return False, str(exc)

    def ring(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        status = actor.get_status("代行契约附着")
        carrier = status.carrier(battle) if isinstance(status, AgencyAttachedStatus) else None
        body = battle.unit_cells(carrier) if carrier is not None else battle.unit_cells(actor)
        return [cell for cell in square_around_cells(battle, body, radius=1) if cell not in body]

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        if actor.has_status("代行契约附着"):
            return {"agency_cancel": True, "agency_cells": positions_to_dict(self.ring(battle, actor))}
        target, stat, copied = self.validate_binding(battle, actor, payload)
        return {"agency_cancel": False, "agency_carrier_id": target.unit_id,
                "stat_name": stat, "copied_skill_code": copied.code}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        if payload.get("agency_cancel") or actor.has_status("代行契约附着"):
            return battle.payload_positions(payload, "agency_cells") if "agency_cells" in payload else self.ring(battle, actor)
        return []

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        cells = self.get_target_cells_for_payload(battle, actor, payload)
        return [unit for unit in battle.effect_units_at_cells(cells) if unit.player_id != actor.player_id]

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if "agency_cancel" not in payload:
            payload.update(self.queued_payload_metadata(battle, actor, payload))
        if not payload["agency_cancel"]:
            self.validate_binding(battle, actor, payload)
        super().prepay_resources(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if payload.get("agency_cancel"):
            attached = actor.get_status("代行契约附着")
            if not isinstance(attached, AgencyAttachedStatus):
                raise ActionMiss("载体已经被破坏，主动解除落空。")
            cells = self.get_target_cells_for_payload(battle, actor, payload)
            attached.detach(battle)
            actor.add_status(AgencyDefenseStatus(), source=actor)
            for target in battle.effect_units_at_cells(cells):
                if target.player_id == actor.player_id:
                    continue
                ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"),
                    is_skill=True, action_name="代行契约解除", area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "agency_contract"}))
                if damage_followup_effect_applies(ctx) and not is_mana_drain_immune(target):
                    actor.gain_mana(target.spend_mana(min(1.0, target.current_mana)))
            battle.log(f"{actor.name} 主动解除【代行契约】。")
            return
        target, stat, copied = self.validate_binding(battle, actor, payload)
        status = AgencyAttachedStatus(target.unit_id, stat, copied.code)
        actor.add_status(status, source=actor)
        if status not in actor.statuses:
            raise ActionMiss("契约附着未能生效。")
        self.wrapper(actor).timing = copied.timing
        status.sync_linked_state(battle)
        battle.log(f"{actor.name} 附着在 {target.name}，借用 {stat} 和【{copied.name}】。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if actor.has_status("代行契约附着"):
            return {"cells": positions_to_dict(self.ring(battle, actor)), "target_unit_ids": [], "requires_target": False,
                    "selection": {"mode": "agency_contract", "attached": True}}
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets], "requires_target": True,
                "selection": {"mode": "agency_contract", "stats": list(self.VALID_STATS), "targets": [
                    {"unit_id": target.unit_id, "name": target.name, "skills": [
                        {"code": skill.code, "name": skill.name} for skill in target.skills if self.wrapper(actor).copyable(skill)]}
                    for target in targets]}}


class PassiveNoRetryStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("被动失败封锁", "本回合不能再次使用被动技能。", duration=1, tick_scope="any_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        return (True, "本回合不能再次使用被动技能。") if skill.timing in {"passive", "reaction"} else (False, "")


class AgencyPassivePunishTrait(Trait):
    track_attack_reaction_prevention = True
    reaction_prevention_requires_hp = True

    def __init__(self) -> None:
        super().__init__("被动失败封锁", "敌方被动真实阻止普攻伤害后，封锁实际使用者至当前回合末。")

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if any(ctx.actual_damage > 0 for ctx in damage_contexts) or getattr(battle, "_measuring_attack_success", False):
            return
        for unit_id in set(payload.get("passive_attack_preventer_ids", [])):
            target = battle.units.get(unit_id)
            if target is not None and target.alive and target.player_id != actor.player_id and not target.has_status("被动失败封锁"):
                target.add_status(PassiveNoRetryStatus(), source=actor)
                battle.log(f"{target.name} 的被动使 {actor.name} 普攻无伤，本回合被动被封锁。")


class WuchangMistImmunityStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("侯鸟标记", "不会被无常之雾影响。", duration=2, tick_scope="owner_turn_end")


class WuchangMistField(BattleFieldEffect):
    weather_name = "无常之雾"
    global_weather = True

    def __init__(self, source_unit_id: str) -> None:
        self.source_unit_id = source_unit_id
        super().__init__("无常之雾", "除无常以外的单位攻击或使用主动技能有 1/2 几率失败。", duration=None)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        return any(getattr(effect, "weather_name", None) == self.weather_name for effect in existing_effects)

    @staticmethod
    def affects_actor(actor: HeroUnit) -> bool:
        return getattr(actor, "hero_code", "") != "excel_r027" and not actor.has_status("侯鸟标记") and not actor.direct_effects_blocked()

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        actor = battle.units.get(str(payload.get("unit_id") or ""))
        if actor is None or not self.affects_actor(actor) or getattr(battle, "_ai_probe_active", False):
            return
        if payload.get("wuchang_mist_checked"):
            return
        name = "普攻"
        if action_type == "skill":
            try:
                skill = actor.get_skill(str(payload.get("skill_code") or ""))
                if skill.code in {"mimic_skill", "agency_borrowed_skill"}:
                    skill = skill.declared_skill(actor, payload)
            except (ActionError, KeyError):
                return
            if skill.timing != "active":
                return
            name = skill.name
        elif action_type != "attack":
            return
        failed = random.random() < 0.5
        payload["wuchang_mist_checked"] = True
        payload["action_failed_by_wuchang_mist"] = failed
        result = "失败" if failed else "成功"
        battle.log_public_event(f"{actor.name} 的【{name}】受到【无常之雾】影响，判定{result}。", source=actor)

    def board_marker(self, battle: Battle) -> str:
        return "雾"


class WuchangMistSkill(Skill):
    def __init__(self) -> None:
        super().__init__("wuchang_mist", "无常之雾", "大招：永久加入天气无常之雾，同名不叠加；除无常及免雾单位外，每次普攻或主动技能公开判定1/2失败；失败照常消耗。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(WuchangMistField(actor.unit_id))


class MigratoryBirdMarkSkill(Skill):
    def __init__(self) -> None:
        super().__init__("migratory_bird_mark", "侯鸟标记", "普通技能：2轮一次；范内直线选择其他友方或敌方单位，按攻造成技能伤害，不破魔；命中后目标2个己方回合内不受无常之雾影响。", cooldown_turns=2, target_mode="unit")

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [unit for unit in battle.all_units()
                if unit is not actor and unit.alive and unit.position is not None and not unit.banished
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.targets(battle, actor)}:
                return False, "需选择范内直线可见的其他单位。"
        return True, ""

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        if str(payload.get("target_unit_id") or "") not in {unit.unit_id for unit in self.targets(battle, actor)}:
            raise ActionError("需选择范内直线可见的其他单位。")
        return {}

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        if not payload.get("queued_resolution") and target not in self.targets(battle, actor):
            raise ActionError("需选择范内直线可见的其他单位。")
        ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"),
                                                  is_skill=True, action_name=self.name, tags={"skill", self.code}))
        if target.alive and damage_followup_effect_applies(ctx):
            existing = [status for status in target.statuses if status.name == "侯鸟标记"]
            added = WuchangMistImmunityStatus()
            target.add_status(added, source=actor)
            if added in target.statuses:
                for status in existing:
                    target.remove_status(status, battle)


class WuchangAttackSealStatus(StatusEffect):
    provided_flags = frozenset({"cannot_attack", "cannot_use_skills"})

    def __init__(self) -> None:
        super().__init__("无常普攻封锁", "不能普攻或使用技能。", duration=2, tick_scope="owner_turn_end")

    def bind(self, owner: HeroUnit) -> "WuchangAttackSealStatus":
        super().bind(owner)
        for flag in self.provided_flags:
            setattr(owner, flag, True)
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for flag in self.provided_flags:
            remaining = any(
                flag in getattr(component, "provided_flags", ())
                or (getattr(component, "flag_name", "") in {flag, "cannot_act"}
                    and getattr(component, "value", True) and getattr(component, "flag_active", True))
                for component in owner.iter_components()
            )
            setattr(owner, flag, owner.is_clone or remaining)


class WuchangAttackSealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("无常普攻封锁", "普攻实际造成生命损失后，目标不能普攻或使用技能2个己方回合；再次命中刷新。")

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if self.owner is None or actor.unit_id != self.owner.unit_id:
            return
        seen: set[str] = set()
        for ctx in damage_contexts:
            target = ctx.target
            if (ctx.source is not actor or ctx.is_skill or ctx.cancelled or ctx.actual_damage <= 0
                    or not target.alive or target.unit_id in seen):
                continue
            seen.add(target.unit_id)
            existing = [status for status in target.statuses if status.name == "无常普攻封锁"]
            added = WuchangAttackSealStatus()
            target.add_status(added, source=actor)
            if added in target.statuses:
                for status in existing:
                    target.remove_status(status, battle)


class BigShensuSkill(ShensuSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "big_shensu"
        self.name = "神速（大）"
        self.description = "普通技能：本回合下一次普通移动距离 +4。"

    def apply_to_self(self, battle: Battle, actor: HeroUnit) -> None:
        existing = actor.get_status("神速")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(NextNormalMoveBoostStatus(4))
        battle.log(f"{actor.name} 获得神速（大），本回合下一次普通移动距离 +4。")


class PierceImmunityTrait(Trait):
    ignores_incoming_piercing = True

    def __init__(self) -> None:
        super().__init__("不受破魔", "破魔对该单位失效，护盾和魔免仍可正常阻挡。")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is not None and ctx.target is self.owner:
            ctx.ignore_shield = ctx.half_ignore_shield = ctx.ignore_magic_immunity = False

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
        if (ctx.cancelled or ctx.actual_damage <= 0 or ctx.target.alive or ctx.target.is_summon
                or ctx.target.is_clone or is_army_soldier(ctx.target) or not isinstance(ctx.target, HeroUnit) or not owner.alive):
            return
        if any(ctx is previous for previous in getattr(self, "counted_destructions", ())):
            return
        # Context identity marks a destruction event, so revival can yield a new event.
        self.counted_destructions = [ctx]
        owner.add_status(StatModifierStatus("破将加速", speed_delta=1, duration=None, description="每破坏一个武将速度 +1。"))


class GaleSkill(Skill):
    selection_cells_are_effect_cells = False
    DIRECTIONS = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}

    def __init__(self) -> None:
        super().__init__("gale", "狂风", "普通技能：费1.5魔；前方7×7破魔效果，显形，破坏双方召唤物/分身，其余单位尽量聚到中心合法格。", mana_cost=1.5, target_mode="cell")

    def direction(self, payload: dict[str, Any]) -> tuple[int, int]:
        raw = str(payload.get("choice_code") or payload.get("direction") or "").lower()
        if raw in self.DIRECTIONS:
            return self.DIRECTIONS[raw]
        dx, dy = int(payload.get("dx") or 0), int(payload.get("dy") or 0)
        if (dx, dy) in self.DIRECTIONS.values():
            return dx, dy
        raise ActionError("狂风需要选择正方向。")

    def origin(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> Position | None:
        return battle.declared_source_position(payload) if payload.get("queued_resolution") else actor.position

    def center(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> Position | None:
        origin = self.origin(battle, actor, payload)
        dx, dy = self.direction(payload)
        return origin.offset(dx * 4, dy * 4) if origin is not None else None

    def area(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        center = self.center(battle, actor, payload)
        if center is None:
            return []
        return [Position(x, y) for y in range(center.y - 3, center.y + 4)
                for x in range(center.x - 3, center.x + 4) if battle.in_bounds(Position(x, y))]

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        declaration = {**payload, "queued_resolution": False}
        cells = self.area(battle, actor, declaration)
        if not cells:
            raise ActionError("这个方向没有场内范围。")
        return {"gale_cells": positions_to_dict(cells), "gale_center": self.center(battle, actor, declaration).to_dict()}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "gale_cells") if payload.get("queued_resolution") else self.area(battle, actor, payload)
        raw_center = payload.get("gale_center") if payload.get("queued_resolution") else None
        center = Position(int(raw_center["x"]), int(raw_center["y"])) if raw_center else self.center(battle, actor, payload)
        if center is None:
            return
        remaining = []
        for unit in battle.effect_units_at_cells(cells):
            if unit.direct_effects_blocked():
                continue
            ctx = battle.validate_target(actor, unit, action_name=self.name, is_skill=True, is_hostile=True,
                                         ignore_shield=True, ignore_targeting_restrictions=True, tags={"skill", "gale"})
            if ctx.cancelled:
                continue
            if unit.total_shields() > 0 and ctx.ignore_shield:
                unit.consume_one_shield()
                battle.record_shield_break_summary(actor, unit, self.name)
            for status in list(unit.statuses):
                if status.name == "隐身":
                    unit.remove_status(status, battle)
            if unit.is_summon or unit.is_clone:
                unit.alive = False
                battle.log_public_event(f"{unit.name} 被【狂风】破坏。", source=actor, target=unit)
            else:
                remaining.append(unit)
        battle.cleanup_dead_units()
        candidates = sorted(cells, key=lambda cell: (cell.distance_to(center), cell.y, cell.x))
        for unit in sorted(remaining, key=lambda unit: (unit.position.distance_to(center) if unit.position else 99, unit.unit_id)):
            if not unit.alive or unit.position is None or unit.banished:
                continue
            for dest in candidates:
                if dest == unit.position:
                    break
                if (battle.is_forced_movement_blocked(dest)
                        or not battle.can_place_unit(unit, dest, ignore=unit, mover=unit)):
                    continue
                try:
                    battle.move_unit(unit, dest, via_skill=True, forced=True, max_distance=99,
                                     ignore_units=True, tags={"gale"})
                except ActionError:
                    continue
                break

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        choices, destinations = [], []
        for direction, label in (("east", "向右"), ("west", "向左"), ("south", "向下"), ("north", "向上")):
            cells = self.area(battle, actor, {"direction": direction})
            if not cells:
                continue
            center = self.center(battle, actor, {"direction": direction})
            anchor = min(cells, key=lambda cell: (cell.distance_to(center), cell.y, cell.x))
            selection = positions_to_dict([anchor])
            choices.append({"code": direction, "label": label, "patterns": [selection]})
            destinations.append({"choice_code": direction, "pattern": selection, "destination_cells": positions_to_dict(cells)})
        cells = sorted({cell for direction in self.DIRECTIONS for cell in self.area(battle, actor, {"direction": direction})}, key=lambda cell: (cell.y, cell.x))
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                              "choice_prompt": "选择狂风方向，再点击中心标记格确认。",
                              "cell_prompt": "点击中心标记格确认；高亮显示本次7×7范围。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        # This hook describes a new declaration; resolution uses the server-frozen gale_cells.
        return self.area(battle, actor, {**payload, "queued_resolution": False})

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class InnerDimensionSwordStatus(StatusEffect):
    separate_basic_attack_windows = True

    def __init__(self) -> None:
        super().__init__("里次元大剑", "攻 +2，速 -2；普攻扩散到原目标周围敌方单位，每个实际受体独立连锁且仅命中一次。", duration=None)

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
    observes_immune_actions = True
    weather_name = "王者的看破"
    global_weather = True

    def __init__(self, source_player_id: int) -> None:
        self.source_player_id = source_player_id
        super().__init__("王者的看破", "本回合内，非己方飞王使用被动/反应技能时血-3/4，然后在场己方飞王本体魔+2。", duration=1)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if isinstance(effect, KingsInsightField):
                effect.source_player_id = self.source_player_id
                effect.duration = 1
                return True
        return False

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if action_type != "skill" or not payload.get("queued_resolution") or payload.get("kings_insight_checked"):
            return
        actor = battle.units.get(str(payload.get("unit_id") or ""))
        if actor is None:
            return
        skill = actor.skill_map().get(str(payload.get("skill_code") or ""))
        if skill is None or skill.timing not in {"passive", "reaction"}:
            return
        payload["kings_insight_checked"] = True
        if actor.player_id == self.source_player_id and getattr(actor, "hero_code", "") == "excel_r028":
            return
        if not battle.effect_recipient(actor).direct_effects_blocked():
            battle.resolve_damage(DamageContext(source=None, target=actor, attack_power=0, raw_damage=0.75,
                is_skill=False, from_field_effect=True, action_name="王者的看破", tags={"weather", "kings_insight"}))
        for unit in battle.hero_units(self.source_player_id):
            if (unit.alive and unit.position is not None and not unit.banished and not unit.is_summon
                    and not unit.is_clone and getattr(unit, "hero_code", "") == "excel_r028"):
                gained = unit.gain_mana(2)
                if gained:
                    battle.log_public_event(f"{unit.name} 通过【王者的看破】回复 {gained:g} 魔。", target=unit)


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
        owner.max_mana_points = min(getattr(owner, "max_mana_points", float("inf")), self.cap)
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
        super().__init__("武器传送", attack_delta=5, duration=1, tick_scope="any_turn_end", description="直到当前回合结束前攻 +5。")


class WeaponTransferSkill(Skill):
    def __init__(self) -> None:
        super().__init__("weapon_transfer", "武器传送", "普通技能：2 轮一次；直到回合结束前攻 +5。", cooldown_turns=2, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(WeaponTransferStatus())


class RedChargeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("red_charge", "蓄力", "普通技能：每回合一次；魔力点 +1。", max_uses_per_turn=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.gain_mana_points(1)


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


class WeaponCopyStatus(StatusEffect):
    def __init__(self, attack: float, copied_traits: list[Trait]) -> None:
        self.copied_attack = attack
        self.copied_traits = copied_traits
        names = "、".join(trait.name for trait in copied_traits) or "无"
        super().__init__("武装复制", f"本回合攻击变为 {attack:g}；复制普攻特性：{names}。",
                         duration=1, tick_scope="any_turn_end")

    def runtime_children(self):
        return self.copied_traits

    def provided_components(self):
        return self.copied_traits

    def modify_stat(self, stat_name: str, value: float) -> float:
        return self.copied_attack if stat_name == "attack" else value


class WeaponCopySkill(Skill):
    def __init__(self) -> None:
        super().__init__("weapon_copy", "武装复制", "普通技能：每回合一次；选择周围可见武将，快照其当前攻及普攻相关特性，持续本回合。", max_uses_per_turn=1, target_mode="unit")

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload=None) -> int:
        return 1

    def reaction_window_timing(self, battle, actor, payload):
        # Reading a visible weapon changes only the caster, not the copied hero.
        return "after"

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        own = set(battle.unit_cells(actor))
        ring = set(square_around_cells(battle, list(own), radius=1)) - own
        return [unit for unit in battle.all_units()
                if isinstance(unit, HeroUnit) and unit is not actor
                and (not unit.is_clone and not unit.is_summon or unit.is_clone and unit.player_id != actor.player_id)
                and not is_army_soldier(unit) and battle.unit_can_be_selected(unit, actor=actor)[0]
                and bool(ring.intersection(battle.unit_cells(unit)))]

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True}

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            raise ActionError("武装复制只能选择周围可见的其他武将本体。")
        return {}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            raise ActionMiss("原格没有可复制的可见武将。")
        if target.is_clone:
            raise ActionMiss("武装复制没有获得有效的武将武装。")
        attack = target.stat("attack")
        # Rebuild definitions: source locks, resource history and owner never migrate.
        existing = {trait.name for trait in actor.traits}
        traits = []
        for component in target.iter_components():
            if not isinstance(component, Trait):
                continue
            definition = component.trait if isinstance(component, BasicAttackOnlyCopy) else component
            if type(definition).__name__ not in BASIC_ATTACK_COPY_TRAIT_TYPES or definition.name in existing:
                continue
            trait = AttackCountTrait(definition.attacks_per_turn) if isinstance(definition, AttackCountTrait) else type(definition)()
            existing.add(trait.name)
            if type(trait).__name__ in {"LinaDestroyRewardTrait", "FeiWangSpeedOnHeroKillTrait", "RedRandomPierceTrait",
                                       "MountainGodCounterTrait", "ErasureApostleDestroyRewardTrait", "UnmountedCombatTrait", "ElectronicLaserDamageBonusTrait", "DoomLightRetaliationTrait"}:
                trait = BasicAttackOnlyCopy(trait)
            trait.bind(actor)
            trait.on_owner_turn_start(battle)
            traits.append(trait)
        previous = actor.get_status("武装复制")
        if previous is not None:
            actor.remove_status(previous, battle)
        actor.add_status(WeaponCopyStatus(attack, traits), source=actor)


class BasicAttackOnlyCopy(Trait):
    """Keep the basic-attack branch of a mixed trait without copying its skill branch."""
    def __init__(self, trait: Trait) -> None:
        super().__init__(trait.name, trait.description + "（武装复制仅保留普攻分支）")
        self.trait = trait

    def bind(self, owner):
        super().bind(owner)
        self.trait.owner = owner
        return self

    def runtime_children(self):
        return (self.trait,)

    def on_owner_turn_start(self, battle):
        self.trait.on_owner_turn_start(battle)

    def on_owner_action_declared(self, battle, action_type, payload):
        if action_type == "attack" and not getattr(battle, "_ai_probe_active", False):
            self.trait.on_owner_action_declared(battle, action_type, payload)

    def on_after_damage(self, battle, ctx):
        if ctx.source is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            self.trait.on_after_damage(battle, ctx)

    def on_before_damage(self, battle, ctx):
        if ctx.source is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            self.trait.on_before_damage(battle, ctx)

    def on_basic_attack_finished(self, battle, actor, payload, damage_contexts, missed):
        self.trait.on_basic_attack_finished(battle, actor, payload, damage_contexts, missed)


class GlobalAttackStatus(StatusEffect):
    overrides_basic_attack_shape = True

    def __init__(self) -> None:
        super().__init__("无限", "每次普攻覆盖全场敌方，仍消耗一次攻击。", duration=3, tick_scope="owner_turn_end")

    def basic_attack_area_cells(self, battle, actor, payload=None):
        return [Position(x, y) for y in range(battle.height) for x in range(battle.width)]

    def validate_declaration(self, battle, actor, payload):
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id == actor.player_id or not battle.unit_can_be_selected(target, actor=actor)[0]:
            raise ActionError("无限普攻需要选择一个可见敌方目标作为全场攻击锚点。")
        for component in actor.iter_components():
            ok, reason = component.can_attack_target(battle, actor, target)
            if not ok:
                raise ActionError(reason)

    def basic_attack_payload_metadata(self, battle, actor, payload=None):
        cells = battle.payload_positions(payload or {}, "attack_cells") if (payload or {}).get("queued_resolution") else []
        return {"attack_cells": positions_to_dict(cells or self.basic_attack_area_cells(battle, actor)),
                "area_attack": True, "single_hit_per_recipient": True, "friendly_fire": False}

    def basic_attack_preview(self, battle, actor, payload=None):
        targets = [unit for unit in battle.enemy_units(actor.player_id) if battle.unit_can_be_selected(unit, actor=actor)[0]]
        return {"cells": positions_to_dict(self.basic_attack_area_cells(battle, actor)),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class InfiniteSkill(Skill):
    def __init__(self) -> None:
        super().__init__("infinite", "无限", "大招：3个己方回合内，每次普攻覆盖全场敌方。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(GlobalAttackStatus())


class PhysicalImmunityStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("无限铠甲", "物免 3 轮。", duration=3, tick_scope="owner_turn_end")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is ctx.target and not ctx.is_skill and "attack" in ctx.tags and not ctx.ignore_physical_immunity:
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 物免。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and not ctx.is_skill and "attack" in ctx.tags and not ctx.ignore_physical_immunity:
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 物免。"


class InfiniteArmorSkill(Skill):
    def __init__(self) -> None:
        super().__init__("infinite_armor", "无限铠甲", "大招：物免 3 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(PhysicalImmunityStatus())


class InfiniteRobeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("infinite_robe", "无限法袍", "大招：魔免 3 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        status = MagicImmunityStatus(source_name="无限法袍", duration=3)
        status.tick_scope = "owner_turn_end"
        actor.add_status(status)


class FusionCircleAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击一周", "普攻同时命中自身外圈全部敌方，每个实际受体一次。")

    def basic_attack_area_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position] | None:
        if actor.has_status("核冲"):
            return None
        if "attack_cells" in payload:
            return battle.payload_positions(payload, "attack_cells")
        own_cells = battle.unit_cells(actor)
        own = {position_key(cell) for cell in own_cells}
        return [cell for cell in square_around_cells(battle, own_cells, radius=1) if position_key(cell) not in own]

    def basic_attack_payload_metadata(self, battle, actor, payload):
        if actor.has_status("核冲"):
            return {}
        if payload.get("reaction_attack"):
            return {}
        return {"single_hit_per_recipient": True, "area_attack": True,
                "attack_cells": positions_to_dict(self.basic_attack_area_cells(battle, actor, payload) or [])}

    def basic_attack_preview(self, battle, actor, payload):
        if actor.has_status("核冲"):
            return None
        cells = self.basic_attack_area_cells(battle, actor, payload) or []
        return {"cells": positions_to_dict(cells), "secondary_cells": [], "requires_target": True,
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells)
                                    if actor.is_enemy_of(unit) and battle.unit_can_be_selected(unit, actor=actor)[0]]}


class NuclearRushStatus(StatusEffect):
    DIRECTIONS = DeadlyBowSkill.DIRECTIONS

    def __init__(self) -> None:
        super().__init__("核冲", "攻 +1；普攻攻击前4格，之后尝试移到声明的第5格；落点不合法仍完成攻击。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name == "attack" else value

    def line(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            return []
        code = str(payload.get("choice_code") or payload.get("direction") or "")
        direction = self.DIRECTIONS.get(code) if code else (payload.get("dx"), payload.get("dy"))
        if direction not in self.DIRECTIONS.values():
            raise ActionError("核冲需要选择八方向之一。")
        dx, dy = direction
        # Keep the fifth cell even when outside the board; never substitute the last valid cell.
        return [actor.position.offset(dx * step, dy * step) for step in range(1, 6)]

    def basic_attack_payload_metadata(self, battle, actor, payload):
        if payload.get("reaction_attack"):
            return {}
        if payload.get("nuclear_rush_declared"):
            return {}
        if not payload.get("queued_resolution") and not any(key in payload for key in ("choice_code", "direction", "dx", "dy")):
            return {}
        line = self.line(battle, actor, payload)
        cells = [cell for cell in line[:4] if battle.in_bounds(cell)]
        if not cells:
            raise ActionError("该方向在棋盘内没有可攻击的格子。")
        if payload.get("cells") and set(battle.payload_positions(payload, "cells")) != set(cells):
            raise ActionError("请选择该方向的完整攻击区域。")
        return {"nuclear_rush_declared": True, "nuclear_rush_landing": line[4].to_dict(),
                "attack_cells": positions_to_dict(cells), "single_hit_per_recipient": True, "area_attack": True}

    def basic_attack_area_cells(self, battle, actor, payload):
        if "attack_cells" in payload:
            return battle.payload_positions(payload, "attack_cells")
        metadata = self.basic_attack_payload_metadata(battle, actor, payload)
        return battle.payload_positions(metadata, "attack_cells") if metadata else None

    def basic_attack_preview(self, battle, actor, payload):
        choices, destinations, cells, landing_cells = [], [], set(), set()
        for code in self.DIRECTIONS:
            line = self.line(battle, actor, {"direction": code})
            area = [cell for cell in line[:4] if battle.in_bounds(cell)]
            if not area:
                continue
            pattern = positions_to_dict(area)
            landing = line[4]
            legal = (not actor.cannot_move and battle.can_place_unit(actor, landing, ignore=actor, mover=actor))
            if legal:
                landing_cells.update(actor.footprint_cells_at(landing))
            choices.append({"code": code, "label": {"north": "北", "northeast": "东北", "east": "东", "southeast": "东南", "south": "南", "southwest": "西南", "west": "西", "northwest": "西北"}[code] + ("：可落位" if legal else "：仅攻击"), "patterns": [pattern]})
            destinations.append({"choice_code": code, "pattern": pattern,
                                 "destination_cells": positions_to_dict(actor.footprint_cells_at(landing)) if legal else []})
            cells.update(area)
        return {"cells": positions_to_dict(sorted(cells, key=lambda c: (c.y, c.x))),
                "secondary_cells": positions_to_dict(sorted(landing_cells, key=lambda c: (c.y, c.x))),
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(list(cells))
                                    if actor.is_enemy_of(unit) and battle.unit_can_be_selected(unit, actor=actor)[0]],
                "requires_target": True, "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                              "choice_prompt": "选择核冲方向。", "cell_prompt": "选择前4格（边界裁剪）；预览第5格落点。"}}

    def on_basic_attack_finished(self, battle, actor, payload, damage_contexts, missed):
        if (actor is not self.owner or not payload.get("nuclear_rush_declared") or payload.get("basic_attack_cancelled")
                or not actor.alive or actor.banished or actor.position is None or actor.unit_id not in battle.units):
            return
        landing = payload.get("nuclear_rush_landing")
        if not isinstance(landing, dict):
            return
        destination = Position(int(landing["x"]), int(landing["y"]))
        # Resolution uses the old attack origin, but the move starts at the real current body.
        actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
        if destination == actor.position:
            return
        try:
            battle.move_unit(actor, destination, via_skill=True, allow_anywhere=True, tags={"nuclear_rush"})
        except ActionError as exc:
            battle.log(f"{actor.name} 的核冲攻击已完成，第5格未能落位：{exc}")


class NuclearRushSkill(Skill):
    def __init__(self) -> None:
        super().__init__("nuclear_rush", "核冲", "开关技能：回合开始使用；攻 +1，普攻攻击4格并尝试移动到第5格；实际开关后魔力点 +1（最多6）。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used > 0 or actor.performed_active_skill:
            return False, "核冲只能在回合开始阶段使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.direct_effects_blocked():
            return
        before = actor.has_status("核冲")
        existing = actor.get_status("核冲")
        if existing is not None:
            actor.remove_status(existing, battle)
        else:
            actor.add_status(NuclearRushStatus())
        if before != actor.has_status("核冲"):
            actor.gain_mana_points(1)
            battle.log(f"{actor.name} {'开启' if actor.has_status('核冲') else '关闭'}核冲，魔力点 {actor.mana_points:g}/6。")


class FusionDeathExplosionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("聚变爆炸", "每次被破坏后，最后身体周围5×5双方受体各承受一次以魔力点为攻值的半破魔场地伤害。")
        self.last_exploded_destruction = 0

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        event = getattr(owner, "destruction_count", 0)
        if (owner is None or owner.alive or owner.banished or owner.is_clone or owner.is_summon
                or not event or event <= self.last_exploded_destruction):
            return
        self.last_exploded_destruction = event
        body = getattr(owner, "last_destroyed_cells", [])
        cells = square_around_cells(battle, body, radius=2)
        power = min(max(owner.mana_points, 0.0), 6.0)
        battle.log(f"{owner.name} 被破坏后引发聚变爆炸：最后身体周围5×5，攻值 {power:g}，半破魔，影响双方。")
        for target in list(battle.effect_units_at_cells(cells)):
            if target.unit_id == owner.unit_id or not target.alive or target.banished:
                continue
            battle.resolve_damage(DamageContext(source=owner, target=target, attack_power=power,
                                                is_skill=False, half_ignore_shield=True, from_field_effect=True,
                                                action_name="聚变爆炸", tags={"fusion_explosion", "area_attack"}))

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
    target_unit_is_anchor = True

    def __init__(self) -> None:
        super().__init__(
            "heaven_punishment",
            "天罚",
            "普通技能：2轮一次；5*5双方普通伤害，另选其中一名敌人的一项公开主动技能施加破魔封印。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 5, 5)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    @staticmethod
    def public_active_skills(battle: Battle, unit: HeroUnit) -> list[Skill]:
        source = unit
        if unit.is_clone:
            root_id = battle.controlling_hero_id(unit)
            source = battle.units.get(root_id, unit)
        return [skill for skill in source.skills if skill.timing == "active"]

    def has_public_active_skill(self, battle: Battle, unit: HeroUnit) -> bool:
        return bool(self.public_active_skills(battle, unit))

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or not payload:
            return ok, reason
        try:
            cells = self.chosen_cells(battle, actor, payload)
            target = self.choose_target(battle, actor, payload, cells)
            self.choose_skill(battle, target, payload)
        except ActionError as exc:
            return False, str(exc)
        return True, ""

    def affected_enemies(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [
            unit
            for unit in battle.units_at_cells(cells)
            if unit.player_id != actor.player_id and self.has_public_active_skill(battle, unit)
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]  # type: ignore[list-item]

    def choose_target(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], cells: list[Position]) -> HeroUnit:
        enemies = self.affected_enemies(battle, actor, cells)
        if not enemies:
            if payload.get("queued_resolution"):
                raise ActionMiss("【天罚】落在原定区域，没有可封印公开主动技能的敌方单位。")
            raise ActionError("天罚区域内没有可封印公开主动技能的敌方单位。")
        if not payload.get("target_unit_id"):
            raise ActionError("天罚需要选择区域内的一名敌方单位。")
        target = payload_target_unit(battle, payload)
        if target.unit_id not in {unit.unit_id for unit in enemies}:
            raise ActionError("指定目标不在天罚区域内，或没有可封印的公开主动技能。")
        return target

    def choose_skill(self, battle: Battle, target: HeroUnit, payload: dict[str, Any]) -> Skill:
        active_skills = self.public_active_skills(battle, target)
        if not active_skills:
            raise ActionError("目标没有可封印的公开主动技能。")
        selected = str(payload.get("disabled_skill_code") or "").strip()
        if not selected:
            raise ActionError("天罚需要选择目标当前公开的一项主动技能。")
        for skill in active_skills:
            if skill.code == selected:
                return skill
        raise ActionError("只能封印目标当前公开的主动技能。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        selected_recipient = None
        selected_skill = None
        try:
            target = self.choose_target(battle, actor, payload, cells)
            selected_skill = self.choose_skill(battle, target, payload)
            selected_recipient = battle.effect_recipient(target)
        except ActionError:
            if not payload.get("queued_resolution"):
                raise
            battle.log("【天罚】原定封印目标失效，没有可封印的公开主动技能；范围伤害仍在原定区域结算。")
        for recipient in battle.effect_units(battle.units_at_cells(cells)):
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=recipient, attack_power=actor.stat("attack"), is_skill=True,
                action_name="天罚", area_cell_hits=battle.unit_hit_count_for_cells(recipient, cells),
                tags={"skill", "heaven_punishment"},
            ))
            if selected_recipient is not None and selected_skill is not None and recipient.unit_id == selected_recipient.unit_id and recipient.alive and damage_followup_effect_applies(ctx, allow_on_shield_break=True):
                apply_piercing_status_effect(
                    battle, actor, recipient, action_name="天罚封印",
                    status=SkillDisabledStatus(selected_skill.code, selected_skill.name), is_skill=True,
                    consume_shield=not ctx.shield_consumed,
                    tags={"skill", "heaven_punishment"},
                )

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def target_can_react_to_effect(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any]) -> bool:
        selected = battle.units.get(str(payload.get("target_unit_id") or ""))
        selected_id = battle.effect_recipient(selected).unit_id if selected is not None else None
        return target.unit_id == selected_id or target.total_shields() == 0

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.enemy_units(actor.player_id)
            if not unit.is_stealthed() and self.has_public_active_skill(battle, unit)
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        options = [
            {"unit_id": unit.unit_id, "name": unit.name, "skills": [
                {"code": skill.code, "name": skill.name}
                for skill in self.public_active_skills(battle, unit)
            ]}
            for unit in battle.enemy_units(actor.player_id)
            if unit.unit_id in targets and not unit.is_stealthed()
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True,
                        "heaven_punishment_targets": options})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        if payload.get("target_unit_id"):
            return [self.choose_target(battle, actor, payload, cells)]
        return self.affected_enemies(battle, actor, cells)


class NoiseWaveStatus(StatModifierStatus):
    def __init__(self, applied_turn_number: int | None = None) -> None:
        super().__init__(
            "乱音电波",
            speed_delta=-1,
            description="速 -1，且不能使用带有位移效果的技能，直到下回合结束前。",
            duration=1,
            tick_scope="owner_turn_end",
        )
        self.applied_turn_number = applied_turn_number

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.applied_turn_number:
            super().on_owner_turn_end(battle)

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
        seen: set[str] = set()
        for unit in battle.units_at_cells(cells):
            unit = battle.effect_recipient(unit)
            if unit.unit_id in seen:
                continue
            seen.add(unit.unit_id)
            if unit.magic_immunity:
                continue
            apply_piercing_status_effect(
                battle,
                actor,
                unit,
                action_name="乱音电波",
                status=NoiseWaveStatus(battle.turn_number),
                is_skill=True,
                tags={"skill", "noise_wave"},
                ignore_targeting_restrictions=True,
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if battle.unit_can_be_selected(unit, actor=actor)[0]
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
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
            "普通技能：2 轮一次，远程10*10；双方受当前攻普通技能伤害，有效命中破坏分身/复制体并夺取存活召唤物；保留原召唤来源、状态和回合槽。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 10, 10)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        seen: set[str] = set()
        for unit in list(battle.units_at_cells(cells)):
            unit = battle.effect_recipient(unit)
            if not unit.alive or unit.position is None or unit.banished:
                continue
            if unit.unit_id in seen:
                continue
            seen.add(unit.unit_id)
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"), is_skill=True,
                action_name="干扰", area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                tags={"skill", "attack", "interference"},
            ))
            if not unit.alive or not damage_followup_effect_applies(ctx):
                continue
            effect_ctx = battle.validate_target(
                actor, unit, action_name="干扰", is_skill=True,
                is_hostile=unit.player_id != actor.player_id, ignore_targeting_restrictions=True,
                resolve_defenses=False, tags={"skill", "interference"},
            )
            if effect_ctx.cancelled:
                continue
            if unit.is_clone:
                unit.alive = False
                battle.log_public_event(f"{unit.name} 被【干扰】破坏。", source=actor, target=unit)
                continue
            if unit.is_summon and unit.player_id != actor.player_id:
                battle.transfer_unit_control(unit, actor)
                battle.log(f"{actor.name} 用【干扰】取得了 {unit.name} 的控制权。")
        battle.cleanup_dead_units()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if battle.unit_can_be_selected(unit, actor=actor)[0]
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        return battle.units_at_cells(cells)  # type: ignore[return-value]


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
            getattr(component, "flag_name", "") in {"cannot_attack", "cannot_act"}
            or "cannot_attack" in getattr(component, "provided_flags", ())
            or "cannot_attack" in getattr(component, "locked_flags", ())
            for component in owner.iter_components()
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

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range(), action_name=self.name)
        return {}

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [battle.effect_recipient(payload_target_unit(battle, payload))]  # type: ignore[list-item]

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
            for unit in battle.effect_units(battle.all_units())
            if unit.alive
            and not unit.banished
            and unit.position is not None
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class FlorenzaAttackDebuffStatus(StatusEffect):
    def __init__(self, applied_turn_number: int | None = None) -> None:
        super().__init__(
            "弗伦萨普攻弱化",
            "攻、守、速 -1，到 1，持续至下个本人回合结束。",
            duration=1,
            tick_scope="owner_turn_end",
        )
        self.applied_turn_number = applied_turn_number

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "defense", "speed"}:
            return value - min(1.0, max(0.0, value - 1.0))
        return value

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.applied_turn_number:
            super().on_owner_turn_end(battle)


class FlorenzaAttackFollowupTrait(Trait):
    def __init__(self) -> None:
        super().__init__("弗伦萨普攻破魔效果", "普攻带有破魔吸魔，并使目标攻守速 -1 到下回合结束前。")

    def piercing_basic_attack_followup_applies(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> bool:
        return self.owner is actor and not any(
            isinstance(effect, RacialWeatherEffect) and effect.protects_piercing(
                battle, target, is_skill=False, tags={"attack"}, ignore_shield=True,
                half_ignore_shield=False, from_field_effect=False)
            for effect in battle.field_effects
        )

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self.apply_followup(battle, ctx)

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        if "shield_blocked_damage" in ctx.modifier_receipts or ctx.preserve_followup_effects:
            self.apply_followup(battle, ctx)

    def apply_followup(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is not owner or ctx.is_skill or ctx.from_field_effect or "attack" not in ctx.tags:
            return
        target = ctx.target
        if not target.alive or "florenza_followup" in ctx.modifier_receipts:
            return
        ctx.modifier_receipts.add("florenza_followup")
        if ctx.shield_consumed and ctx.cancelled and target.dodge_charges > 0 and not ctx.cannot_evade:
            target.dodge_charges -= 1
            battle.log_public_event(f"{target.name} 闪避了弗伦萨的普攻附带效果。", source=owner, target=target)
            return
        applied = apply_piercing_status_effect(
            battle, owner, target, action_name="弗伦萨普攻破魔效果",
            status=FlorenzaAttackDebuffStatus(battle.turn_number), is_skill=False,
            tags={"attack", "florenza_followup"}, ignore_targeting_restrictions=True,
            consume_shield=not ctx.shield_consumed,
        )
        if not applied:
            return
        if is_mana_drain_immune(target):
            battle.log(f"{target.name} 无法被吸魔。")
            return
        drained = target.spend_mana(1)
        gained = owner.gain_mana(drained)
        if drained or gained:
            battle.log(f"{owner.name} 的普攻破魔效果从 {target.name} 吸取 {drained} 点魔，获得 {gained} 点魔。")


class MessengerPierceSkill(PierceSkill):
    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class MessengerDragonBreathSkill(DragonBreathSkill):
    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class MessengerReincarnationSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "messenger_reincarnation",
            "无极落霞轮回",
            "普通技能：仅可在当前魔为 0 且至少持有 2 魔力点时使用；消耗 2 魔力点，当前魔变满。",
            target_mode="self",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.current_mana > 1e-9:
            return False, "只有当前魔为 0 时才能使用无极落霞轮回。"
        if actor.mana_points + 1e-9 < 2:
            return False, "无极落霞轮回需要 2 魔力点。"
        return True, ""

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"messenger_point_cost_paid": False}

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> None:
        if actor.mana_points + 1e-9 < 2:
            raise ActionError("无极落霞轮回需要2魔力点。")
        super().prepay_resources(battle, actor, payload)
        spent = actor.spend_mana_points(2)
        if payload is not None:
            payload["messenger_point_cost_paid"] = True
        battle.log(f"{actor.name} 为【无极落霞轮回】支付 {spent:g} 魔力点。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not payload.get("messenger_point_cost_paid"):
            # Direct execution is also used by copied skills and focused scenarios.
            if actor.mana_points + 1e-9 < 2:
                raise ActionError("无极落霞轮回需要2魔力点。")
            previous = getattr(actor, "_paying_skill_cost", False)
            actor._paying_skill_cost = True
            try:
                actor.spend_mana_points(2)
            finally:
                actor._paying_skill_cost = previous
        gained = actor.gain_mana(max(0.0, actor.max_mana() - actor.current_mana))
        spent = 2
        battle.log(f"{actor.name} 使用【无极落霞轮回】，消耗 {spent} 魔力点并回复 {gained} 点魔。")


class MessengerCreationSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "messenger_creation",
            "造化",
            "普通技能：每回合最多 1 次，不费魔；魔力点 +1。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        gained = actor.gain_mana_points(1)
        battle.log(f"{actor.name} 使用【造化】，获得 {gained} 魔力点。")


class MessengerDamageSkillManaTrait(Trait):
    damaging_skill_codes = {"dragon_breath", "pierce"}

    def __init__(self) -> None:
        super().__init__("伤害技能魔力点", "入场魔力点等于基础魔；每次使用伤害技能后魔力点 +1。")

    def bind(self, owner: HeroUnit) -> "MessengerDamageSkillManaTrait":
        super().bind(owner)
        owner.mana_points = float(owner.base_stats.mana)
        return self

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type != "skill":
            return
        if str(payload.get("skill_code") or "") not in self.damaging_skill_codes:
            return
        gained = owner.gain_mana_points(1)
        if gained:
            battle.log(f"{owner.name} 使用伤害技能，获得 {gained} 魔力点。")


class MessengerSkillDamagePierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("技能伤害破魔", "自身造成的技能伤害破魔；非伤害附带效果不因此获得破魔。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is not None and ctx.source is not None and ctx.source.unit_id == owner.unit_id and ctx.is_skill and not ctx.from_field_effect:
            ctx.ignore_shield = True


class RedLotusDestroyedHeroesTrait(Trait):
    def __init__(self) -> None:
        super().__init__("破坏武将攻守成长", "双方每有一个被破坏的武将本体，自身攻、守随时 +1。")
        self.destroyed_hero_count = 0

    def on_destroyed_hero_count_changed(self, battle: Battle, count: int) -> None:
        self.destroyed_hero_count = max(0, int(count))

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name not in {"attack", "defense"}:
            return value
        reference = getattr(self.owner, "_battle_ref", None)
        battle = reference() if reference is not None else None
        count = battle.destroyed_hero_count() if battle is not None else self.destroyed_hero_count
        return value + count


class RedLotusSkillDamageImmunityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("技能伤害免疫", "不受到技能直接造成的伤害，但仍会受到技能的非伤害后续效果。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if not ctx.is_skill or ctx.from_field_effect or ctx.cancelled:
            return
        ctx.cancelled = True
        ctx.preserve_followup_effects = True
        ctx.reason = f"{owner.name} 免疫了技能伤害。"
        battle.emit_defense_visual_event(
            source=ctx.source,
            target=owner,
            action_name=ctx.action_name,
            defense_reason="skill_damage_immunity",
        )


class RedLotusUnevadableAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("普攻无法被回避", "自身普攻无法被回避，且不会消耗目标的回避次数。")

    def basic_attack_payload_metadata(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"cannot_evade": True}

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        owner = self.owner
        if owner is not None and ctx.actor.unit_id == owner.unit_id and not ctx.is_skill and "attack" in ctx.tags:
            ctx.cannot_evade = True


class RedLotusOnceDamagePerTurnTrait(Trait):
    def __init__(self) -> None:
        super().__init__("每回合一次伤害", "每个全局武将回合只会实际受到一次伤害。")
        self._turn_number: int | None = None
        self._damage_taken = False

    def _sync_turn(self, battle: Battle) -> None:
        turn_number = int(getattr(battle, "turn_number", 1) or 1)
        if self._turn_number == turn_number:
            return
        self._turn_number = turn_number
        self._damage_taken = False

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.cancelled:
            return
        self._sync_turn(battle)
        if not self._damage_taken:
            return
        ctx.cancelled = True
        ctx.preserve_followup_effects = True
        ctx.reason = f"{owner.name} 本回合已经受到过一次伤害。"
        battle.emit_defense_visual_event(
            source=ctx.source,
            target=owner,
            action_name=ctx.action_name,
            defense_reason="once_damage_per_turn",
        )

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.cancelled:
            return
        self._sync_turn(battle)
        if ctx.actual_damage > 0:
            self._damage_taken = True


class H1LineDamageSkill(Skill):
    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        length: int,
        mana_cost: float = 0,
        max_uses_per_turn: int | None = None,
        cooldown_turns: int = 0,
    ) -> None:
        super().__init__(
            code,
            name,
            description,
            mana_cost=mana_cost,
            max_uses_per_turn=max_uses_per_turn,
            cooldown_turns=cooldown_turns,
            target_mode="cell",
        )
        self.length = length

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return line_patterns(battle, actor.position, ALL_DIRECTIONS, self.length)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def affected_units(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return list(battle.units_at_cells(cells))  # type: ignore[return-value]

    def resolve_hit(self, battle: Battle, actor: HeroUnit, target: HeroUnit, cells: list[Position]) -> DamageContext:
        return battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name=self.name,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", "attack", self.code},
            )
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in self.affected_units(battle, actor, cells):
            self.resolve_hit(battle, actor, target, cells)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cells = [cell for pattern in self.patterns(battle, actor) for cell in pattern]
        targets = [unit.unit_id for unit in battle.units_at_cells(cells)]
        preview.update({"target_unit_ids": list(dict.fromkeys(targets)), "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return self.affected_units(battle, actor, self.chosen_cells(battle, actor, payload))


class BlindManaMovementTrait(Trait):
    def __init__(self) -> None:
        super().__init__("以魔代步", "同回合可多次普通移动；每格消耗 1 魔，每次最远为当前魔的整数部分。")

    def modify_normal_move_distance(self, value: int) -> int:
        owner = self.owner
        return int(owner.current_mana) if owner is not None else 0

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return 1_000_000

    def on_before_normal_move(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id:
            return
        cost = max(0, len(ctx.path) - 1)
        if owner.current_mana + 1e-9 < cost:
            raise ActionError("以魔代步的魔不足。")
        spent = owner.spend_mana(cost)
        if spent:
            battle.log(f"{owner.name} 以魔代步，消耗 {spent:g} 点魔。")


class BlindEvasionAttackResetTrait(Trait):
    def __init__(self) -> None:
        super().__init__("回避后重置普攻", "普攻被实际回避完整躲开后，本回合已用攻击次数归零。")

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id or not owner.alive or payload.get("basic_attack_cancelled") or not payload.get("completed_evasion_unit_ids"):
            return
        cells = set(battle.payload_positions(payload, "attack_cells"))
        hit_ids = {ctx.target.unit_id for ctx in damage_contexts if not ctx.cancelled}
        for unit_id in payload["completed_evasion_unit_ids"]:
            unit = battle.units.get(str(unit_id))
            if unit is None or not unit.alive or unit.banished or unit.unit_id in hit_ids:
                continue
            if not cells or any(cell in cells for cell in battle.unit_cells(unit)):
                continue
            owner.attacks_used = 0
            battle.log(f"{owner.name} 的普攻被回避，本回合攻击次数重置。")
            return


class BlindCircleAttackTrait(FusionCircleAttackTrait):
    def __init__(self) -> None:
        Trait.__init__(self, "攻击一周", "普攻同时命中自身周围 8 格内的全部敌方单位。")

    def basic_attack_preview(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        cells = self.basic_attack_area_cells(battle, actor, payload or {}) or []
        target_ids = [
            unit.unit_id
            for unit in battle.effect_units_at_cells(cells)
            if unit.player_id != actor.player_id and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        return {
            "cells": positions_to_dict(cells),
            "target_unit_ids": target_ids,
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": True,
            "selection": {"mode": "pattern_cells", "patterns": [positions_to_dict(cells)]},
        }


class ThorLineDamageSkill(H1LineDamageSkill):
    excludes_allies_from_effect = True
    target_cells_are_geometry_only = True

    def affected_units(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [unit for unit in battle.effect_units_at_cells(cells) if unit.player_id != actor.player_id]

    def resolve_hit(self, battle: Battle, actor: HeroUnit, target: HeroUnit, cells: list[Position]) -> DamageContext:
        return battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
            action_name=self.name, area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
            tags={"skill", self.code},
        ))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cells = [cell for pattern in self.patterns(battle, actor) for cell in pattern]
        preview.update({"target_unit_ids": [unit.unit_id for unit in self.affected_units(battle, actor, cells)
                                           if battle.unit_can_be_selected(unit, actor=actor)[0]],
                        "secondary_cells": [], "requires_target": True})
        return preview


class ThorHeavyHammerSkill(ThorLineDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "thor_heavy_hammer",
            "重锤",
            "普通技能：费 1 魔，每回合一次；攻击相邻 1 格，敌方连锁技能魔耗翻倍。",
            length=1,
            mana_cost=1,
            max_uses_per_turn=1,
        )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"reaction_mana_multiplier": 2.0}


class ThorRageImpactSkill(ThorLineDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "thor_rage_impact",
            "暴怒冲击",
            "普通技能：费1魔每回合1次；声明前4敌方伤害格与合法第5落点，伤后尝试固定落点，后来受阻仍完成伤害。",
            length=4,
            mana_cost=1,
            max_uses_per_turn=1,
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        if actor.cannot_move:
            return []
        return [cells for cells in super().patterns(battle, actor) if len(cells) == 4
                and battle.can_place_unit(actor, self.landing(cells), ignore=actor, mover=actor)]

    @staticmethod
    def landing(cells: list[Position]) -> Position:
        return cells[-1].offset(cells[1].x - cells[0].x, cells[1].y - cells[0].y)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        cells = match_payload_pattern(payload, self.patterns(battle, actor))
        return {"thor_rage_cells": positions_to_dict(cells), "thor_rage_landing": self.landing(cells).to_dict()}

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and "thor_rage_cells" in payload:
            return [Position(**cell) for cell in payload["thor_rage_cells"]]
        return super().chosen_cells(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        landing = Position(**payload["thor_rage_landing"]) if payload.get("queued_resolution") and "thor_rage_landing" in payload else self.landing(cells)
        for target in self.affected_units(battle, actor, cells):
            if not actor.alive or actor.banished:
                break
            self.resolve_hit(battle, actor, target, cells)
        if actor.position is None or not actor.alive or actor.banished:
            return
        actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
        try:
            battle.move_unit(actor, landing, via_skill=True, ignore_units=True, allow_anywhere=True,
                             max_distance=5, tags={self.code})
        except ActionError:
            battle.log(f"{actor.name} 已完成【暴怒冲击】伤害，但无法到达声明第5格。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = super().preview(battle, actor)
        preview["secondary_cells"] = positions_to_dict([self.landing(cells) for cells in self.patterns(battle, actor)])
        return preview


class H1AllActionLockStatus(StatusEffect):
    locked_flags = ("cannot_move", "cannot_attack", "cannot_use_skills")

    def bind(self, owner: HeroUnit) -> "H1AllActionLockStatus":
        super().bind(owner)
        for flag in self.locked_flags:
            setattr(owner, flag, True)
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for flag in self.locked_flags:
            still_locked = any(
                flag in getattr(status, "locked_flags", ())
                or (isinstance(status, FlagStatus) and status.flag_name == flag and status.value)
                for status in owner.statuses
            )
            if flag in {"cannot_attack", "cannot_use_skills"} and owner.is_clone:
                still_locked = True
            setattr(owner, flag, still_locked)


class ThorDestroyedLightningStatus(H1AllActionLockStatus):
    def __init__(self) -> None:
        super().__init__("毁灭电击", "待下个自己的行动回合开始，期间不能任何行动。", duration=None, tick_scope="owner_turn_end")
        self.active = False
        self.pending = True

    def bind(self, owner: HeroUnit) -> "ThorDestroyedLightningStatus":
        StatusEffect.bind(self, owner)
        return self

    @property
    def locked_flags(self) -> tuple[str, ...]:
        return ("cannot_move", "cannot_attack", "cannot_use_skills") if self.active else ()

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.active, self.pending = True, False
        self.description = "本回合不能普通移动、普攻、主动技能或任何被动反应。"
        for flag in self.locked_flags:
            setattr(self.owner, flag, True)

    def on_owner_turn_end(self, battle: Battle) -> None:
        if not self.active:
            return
        queued_next = self.pending
        self.owner.remove_status(self, battle)
        if queued_next:
            self.owner.add_status(ThorDestroyedLightningStatus())

    def allows_block_counter(self, battle: Battle, actor: HeroUnit) -> bool:
        return not self.active


class ThorDestroyLightningSkill(ThorLineDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "thor_destroy_lightning",
            "毁灭电击",
            "普通技能：费 1 魔，每回合一次；攻击前 5 格，雷属性无效，其他有效命中者下回合不能行动。",
            length=5,
            mana_cost=1,
            max_uses_per_turn=1,
        )

    def affected_units(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [unit for unit in super().affected_units(battle, actor, cells) if unit.attribute != "雷"]

    def target_can_react_to_effect(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any]) -> bool:
        return target.attribute != "雷" and target.player_id != actor.player_id

    def resolve_hit(self, battle: Battle, actor: HeroUnit, target: HeroUnit, cells: list[Position]) -> DamageContext:
        ctx = super().resolve_hit(battle, actor, target, cells)
        if damage_followup_effect_applies(ctx) and target.alive:
            effect = battle.validate_target(actor, target, action_name="毁灭电击封锁", is_skill=True,
                                            is_hostile=True, resolve_defenses=ctx.cancelled and not ctx.shield_consumed,
                                            ignore_targeting_restrictions=True)
            if effect.cancelled:
                return ctx
            existing = target.get_status("毁灭电击")
            if existing is not None:
                existing.pending = True
            else:
                target.add_status(ThorDestroyedLightningStatus())
        return ctx


class BeetleSpearSkill(HellSlashSkill):
    length = 5

    def __init__(self) -> None:
        Skill.__init__(self,
            "beetle_spear",
            "兵刺",
            "普通技能：2 轮一次；攻击身前连续 5 格。",
            cooldown_turns=2,
            target_mode="cell",
        )


class BeetleFullForceStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("全力攻击", attack_delta=2, defense_delta=-3, speed_delta=2, description="攻 +2，速 +2，守 -3。")


class BeetleFullForceSkill(Skill):
    def __init__(self) -> None:
        super().__init__("beetle_full_force", "全力攻击", "开关技能：仅回合开始使用；攻 +2、速 +2、守 -3。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used or actor.performed_active_skill:
            return False, "全力攻击只能在回合开始阶段使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = battle.effect_recipient(actor)
        existing = target.get_status("全力攻击")
        if existing is not None:
            target.remove_status(existing, battle)
            battle.log(f"{target.name} 关闭【全力攻击】。")
        else:
            target.add_status(BeetleFullForceStatus())
            battle.log(f"{target.name} 开启【全力攻击】。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        target = battle.effect_recipient(actor)
        return {"cells": positions_to_dict(battle.unit_cells(target)), "target_unit_ids": [target.unit_id],
                "secondary_cells": [], "requires_target": False}


class BeetleArmorDeploymentStatus(H1AllActionLockStatus):
    def __init__(self, applied_turn_number: int | None = None) -> None:
        super().__init__("铠甲部署", "守 +3，直到自己的下个行动回合结束前不能行动。", duration=1, tick_scope="owner_turn_end")
        self.applied_turn_number = applied_turn_number

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.applied_turn_number:
            super().on_owner_turn_end(battle)

    def allows_block_counter(self, battle: Battle, actor: HeroUnit) -> bool:
        return False

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 3 if stat_name == "defense" else value


class BeetleArmorDeploySkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "beetle_armor_deploy",
            "铠甲部署",
            "被动技能：2 轮一次；受敌方动作影响时回复 1/4，守 +3，直到自己的下个回合结束前不能行动。",
            cooldown_turns=2,
            target_mode="self",
            timing="passive",
        )

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id or battle.reaction_proxy_target(actor, queued_action) is None:
            return False, "铠甲部署只能响应会影响自身的敌方动作。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = battle.effect_recipient(actor)
        if not target.alive or target.position is None or target.banished:
            return
        battle.heal(HealContext(source=actor, target=target, amount=0.25, action_name="铠甲部署"))
        existing = target.get_status("铠甲部署")
        if existing is not None:
            target.remove_status(existing, battle)
        target.add_status(BeetleArmorDeploymentStatus(battle.turn_number))

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        target = battle.effect_recipient(actor)
        return {
            "cells": positions_to_dict(battle.unit_cells(target)),
            "target_unit_ids": [target.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class ElectronicDragonNameTrait(Trait):
    def __init__(self) -> None:
        super().__init__("名字视为电子龙", "规则检索名字时也匹配“电子龙”，公开显示名不变。")

    def bind(self, owner: HeroUnit) -> "ElectronicDragonNameTrait":
        super().bind(owner)
        aliases = set(getattr(owner, "rule_name_aliases", set()))
        aliases.add("电子龙")
        owner.rule_name_aliases = aliases
        return self


class ElectronicLaserSkill(HellSlashSkill):
    length = 7

    def __init__(self) -> None:
        Skill.__init__(self,
            "electronic_laser",
            "镭射",
            "普通技能：0魔、每本人回合一次；身前连续7格，双方当前攻技能伤害。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        body = set(battle.unit_cells(actor))
        return [cells for cells in super().patterns(battle, actor) if not body.intersection(cells)]


class ElectronicLaserDamageBonusTrait(Trait):
    def __init__(self) -> None:
        super().__init__("镭射龙伤害强化", "直接伤害锁定当前攻击较低的单位时伤害 +1。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id or ctx.from_field_effect:
            return
        if not (ctx.is_skill or "attack" in ctx.tags) or owner.stat("attack") <= ctx.target.stat("attack"):
            return
        if ctx.raw_damage is None:
            ctx.attack_power += 1
        else:
            ctx.raw_damage += 1


class ElectronicBarrierPersonalGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("屏障龙自身减伤", "低防守单位对自身造成直接伤害时伤害 -1。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is not None and any(isinstance(effect, ElectronicBarrierAuraField) and effect.source_unit_id == self.owner.unit_id
                                          for effect in battle.field_effects):
            return
        self.apply_reduction(battle, ctx)

    def apply_reduction(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or battle.effect_recipient(owner) is not ctx.target or ctx.source is None or ctx.from_field_effect:
            return
        if not (ctx.is_skill or "attack" in ctx.tags) or ctx.source.stat("defense") >= ctx.target.stat("defense"):
            return
        receipt = "electronic_barrier_personal:" + owner.unit_id
        if receipt in ctx.modifier_receipts:
            return
        ctx.modifier_receipts.add(receipt)
        if ctx.raw_damage is None:
            ctx.attack_power = max(0.0, ctx.attack_power - 1)
        else:
            ctx.raw_damage = max(0.0, ctx.raw_damage - 1)


class ElectronicBarrierAuraField(BattleFieldEffect):
    def __init__(self, source_unit_id: str, source_player_id: int) -> None:
        self.source_unit_id = source_unit_id
        self.source_player_id = source_player_id
        super().__init__("电子屏障光环", "周围双方单位受伤害 -1，机甲改为 -2；同名不叠加。")

    def source(self, battle: Battle) -> HeroUnit | None:
        unit = battle.units.get(self.source_unit_id)
        if unit is None or not unit.alive or unit.position is None or unit.banished:
            return None
        return unit  # type: ignore[return-value]

    def on_after_damage_modifiers(self, battle: Battle, ctx: DamageContext) -> None:
        source = self.source(battle)
        if source is None:
            return
        if battle.effect_recipient(source) is ctx.target:
            for trait in source.traits:
                if isinstance(trait, ElectronicBarrierPersonalGuardTrait):
                    trait.apply_reduction(battle, ctx)
        position = getattr(ctx.target, "_resolution_actual_position", ctx.target.position)
        target_cells = battle.unit_cells_at(ctx.target, position) if position is not None else []
        if "electronic_barrier_aura" in ctx.modifier_receipts or not set(target_cells).intersection(self.affected_cells(battle)):
            return
        reduction = 2.0 if ctx.target.race == "机甲" else 1.0
        if ctx.raw_damage is None:
            ctx.attack_power = max(0.0, ctx.attack_power - reduction)
        else:
            ctx.raw_damage = max(0.0, ctx.raw_damage - reduction)
        ctx.modifier_receipts.add("electronic_barrier_aura")

    def affected_cells(self, battle: Battle) -> list[Position]:
        source = self.source(battle)
        if source is None:
            return []
        body = battle.unit_cells_at(source, getattr(source, "_resolution_actual_position", source.position))
        return sorted({cell.offset(dx, dy) for cell in body for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                       if battle.in_bounds(cell.offset(dx, dy))}, key=lambda cell: (cell.y, cell.x))

    def board_marker(self, battle: Battle) -> str:
        return "屏障"


class ElectronicBarrierAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("电子屏障光环", "自身周围双方单位受到伤害 -1，机甲改为 -2；同名光环不叠加。")

    def on_enter_battle(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        if not any(isinstance(effect, ElectronicBarrierAuraField) and effect.source_unit_id == owner.unit_id for effect in battle.field_effects):
            battle.add_field_effect(ElectronicBarrierAuraField(owner.unit_id, owner.player_id))

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for effect in list(battle.field_effects):
            if isinstance(effect, ElectronicBarrierAuraField) and effect.source_unit_id == owner.unit_id:
                battle.remove_field_effect(effect)


class WingedLeopardLeapSkill(DashMoveSkill):
    def __init__(self) -> None:
        super().__init__(
            "fly_leap",
            "飞跃",
            "普通技能：每回合一次，不费魔，直线飞行移动恰好 3 格。",
            max_distance=3,
            exact_distance=3,
            mana_cost=0,
            max_uses_per_turn=1,
            straight_only=True,
            ignore_units=True,
        )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        destination = payload_position(payload)
        if destination.to_dict() not in self.preview(battle, actor)["cells"]:
            raise ActionError("飞跃必须选择恰好3格且完整身体可落地的位置。")
        return {}


class WingedLeopardMoveResetTrait(Trait):
    def __init__(self) -> None:
        super().__init__("飞跃重置移动", "使用飞跃后重置本回合普通移动次数。")

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or "fly_leap" not in ctx.tags:
            return
        owner.normal_move_actions_used = 0
        owner.normal_move_steps_used = 0
        owner.move_used = False
        battle.log(f"{owner.name} 使用飞跃，重置了本回合普通移动次数。")


class EagleEyeStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("鹰眼", "不能普攻或使用主动技能。", duration=3, tick_scope="owner_turn_end")

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        if self.owner is not None and actor.unit_id == self.owner.unit_id:
            return False, "鹰眼使该单位不能攻击。"
        return True, ""

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if self.owner is not None and actor.unit_id == self.owner.unit_id and skill.timing == "active":
            return True, "鹰眼使该单位不能使用主动技能。"
        return False, ""


class EagleEyeSkill(DeclaredAreaSkillMixin, Skill):
    def __init__(self) -> None:
        super().__init__("eagle_eye", "鹰眼", "普通技能：基础费 0.5 魔，每回合第一次免费；2*3 范围，无伤害，封锁攻击和主动技能 3 轮。", mana_cost=0.5, target_mode="cell")

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        self.sync_turn_scope(battle)
        return 0.0 if self.uses_this_turn == 0 else super().mana_cost_for_payload(battle, actor, payload)

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns = [*remote_rectangle_patterns(battle, actor, 2, 3), *remote_rectangle_patterns(battle, actor, 3, 2)]
        unique: dict[tuple[tuple[int, int], ...], list[Position]] = {}
        for pattern in patterns:
            unique.setdefault(pattern_signature(pattern), pattern)
        return list(unique.values())

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") or match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            ctx = battle.validate_target(
                actor,
                target,
                action_name="鹰眼",
                is_skill=True,
                is_hostile=target.player_id != actor.player_id,
                tags={"skill", "eagle_eye", "control"},
            )
            if ctx.cancelled:
                if ctx.reason:
                    battle.log_public_event(ctx.reason, source=actor, target=target)
                continue
            target = ctx.target
            existing = target.get_status("鹰眼")
            if existing is not None:
                target.remove_status(existing, battle)
            target.add_status(EagleEyeStatus())

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cells = [cell for pattern in self.patterns(battle, actor) for cell in pattern]
        preview.update({
            "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells)
                                if battle.unit_can_be_selected(unit, actor=actor)[0]],
            "secondary_cells": [],
            "requires_target": True,
        })
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return list(battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload)))  # type: ignore[return-value]


def _terrain_unit(unit: HeroUnit) -> bool:
    return bool(
        getattr(unit, "world_seed_terrain", False)
        or getattr(unit, "is_terrain", False)
        or getattr(unit, "role", "") == "地形单位"
    )


def _destroy_terrain_in_cells(battle: Battle, actor: HeroUnit, cells: list[Position], action_name: str) -> None:
    cell_keys = {position_key(cell) for cell in cells}
    destroyed_names: list[str] = []
    for x, y in sorted(cell_keys):
        if (x, y) not in battle.blocked_cells:
            continue
        battle.blocked_cells.remove((x, y))
        destroyed_names.append(f"墙体({x},{y})")
    for unit in list(battle.units_at_cells(cells)):
        if not _terrain_unit(unit):
            continue
        unit.alive = False
        destroyed_names.append(unit.name)
    for effect in list(battle.field_effects):
        if not getattr(effect, "is_terrain", False):
            continue
        if any(position_key(cell) in cell_keys for cell in effect.affected_cells(battle)):
            destroyed_names.append(effect.name)
            battle.remove_field_effect(effect)
    if destroyed_names:
        battle.log(f"{actor.name} 的【{action_name}】破坏了地形：{'、'.join(dict.fromkeys(destroyed_names))}。")
        battle.cleanup_dead_units()


class SolarFlameSkill(RemoteDragonBreathSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "solar_flame"
        self.name = "爆炎"
        self.description = "普通技能：规则同远程龙息；费 2 魔，每回合最多 2 次，远程 2*2 当前攻击伤害。"


class SphinxCannonAreaAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("斯芬克斯炮击", "普攻按范远程选择 3*3 区域，结算后破坏范围内地形。")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 3)

    def basic_attack_area_cells(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> list[Position] | None:
        if payload is None:
            return None
        cells = [payload_position(item) for item in payload.get("attack_cells", payload.get("cells", []))]
        if not cells:
            return None
        return match_payload_pattern({"cells": positions_to_dict(cells)}, self.patterns(battle, actor))

    def basic_attack_area_affects_allies(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        return True

    def basic_attack_preview(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        all_pattern_cells = {cell for pattern in patterns for cell in pattern}
        target_ids = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id
            if any(cell in all_pattern_cells for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": target_ids, "secondary_cells": [], "requires_target": True})
        return preview

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
        cells = battle.payload_positions(payload, "attack_cells")
        if cells:
            _destroy_terrain_in_cells(battle, actor, cells, "斯芬克斯炮击")


class SphinxCannonSummon(AbstractHero):
    hero_code = "sphinx_cannon"
    hero_name = "斯芬克斯炮"
    role = "召唤物"
    attribute = "火"
    race = "机械"
    level = 1
    base_stats = Stats(attack=4, defense=5, speed=1, attack_range=7, mana=0)
    raw_skill_text = ""
    raw_trait_text = "攻3*3；破坏地形"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [SphinxCannonAreaAttackTrait()]


class SphinxCannonSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "sphinx_cannon",
            "斯芬克斯炮",
            "普通技能：2 轮一次；在周围召唤攻4守5速1范7、普攻3*3并破坏地形的炮台。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        probe = SphinxCannonSummon(actor.player_id)
        return [
            cell
            for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
            if battle.can_place_unit(probe, cell)
        ]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            raise ActionError("请选择太阳神周围的合法格召唤斯芬克斯炮。")
        battle.summon_unit(SphinxCannonSummon(actor.player_id), destination, summoner=actor)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": positions_to_dict(self.available_cells(battle, actor)),
            "target_unit_ids": [],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": True,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


class SolarJudgmentSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "solar_judgment",
            "审判",
            "大招：远程 10*10，伤害值 6，半破魔、无视魔免，并破坏地形。",
            max_uses_per_battle=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 10, 10)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in list(battle.units_at_cells(cells)):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=6,
                    is_skill=True,
                    action_name=self.name,
                    half_ignore_shield=True,
                    ignore_magic_immunity=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "attack", self.code},
                )
            )
        _destroy_terrain_in_cells(battle, actor, cells, self.name)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        all_cells = {position_key(cell) for pattern in patterns for cell in pattern}
        preview.update({
            "target_unit_ids": [
                unit.unit_id
                for unit in battle.all_units()
                if any(position_key(cell) in all_cells for cell in battle.unit_cells(unit))
            ],
            "secondary_cells": [],
            "requires_target": True,
        })
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return list(battle.units_at_cells(self.chosen_cells(battle, actor, payload)))  # type: ignore[return-value]

    def half_ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ignore_magic_immunity": True}


class RequiredAttackStatus(StatusEffect):
    """One-turn taunt contract shared by heroes that require a specific basic-attack target."""

    def __init__(self, name: str, description: str, required_target_id: str) -> None:
        super().__init__(name, description, duration=1, tick_scope="owner_turn_end")
        self.required_attack_target_id = required_target_id
        self.requirement_active = False
        self.requirement_satisfied = False

    def _required_target(self, battle: Battle) -> HeroUnit | None:
        target = battle.units.get(self.required_attack_target_id)
        if target is None or not target.alive or target.position is None or target.banished:
            return None
        return target  # type: ignore[return-value]

    def forces_attack_target(self, battle: Battle) -> bool:
        return self.requirement_active and not self.requirement_satisfied and self._required_target(battle) is not None

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.requirement_active = True
        if self._required_target(battle) is None:
            self.requirement_satisfied = True

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        if not self.forces_attack_target(battle):
            return True, ""
        if target.unit_id != self.required_attack_target_id:
            required = self._required_target(battle)
            return False, f"{actor.name} 必须先攻击 {required.name if required is not None else '嘲讽来源'}。"
        return True, ""

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if not self.forces_attack_target(battle):
            return False, ""
        if actor.player_id != battle.active_player or not battle.unit_belongs_to_current_turn(actor):
            return False, ""
        return True, f"{actor.name} 必须先攻击嘲讽来源，本回合目前只能移动。"

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type != "attack" or not self.forces_attack_target(battle):
            return
        target_ids = [str(payload.get("target_unit_id") or "")]
        attack_cells = {position_key(cell) for cell in battle.payload_positions(payload, "attack_cells")}
        required = self._required_target(battle)
        if required is not None and any(position_key(cell) in attack_cells for cell in battle.unit_cells(required)):
            target_ids.append(required.unit_id)
        if self.required_attack_target_id not in target_ids:
            return
        self.requirement_satisfied = True
        battle.log(f"{owner.name} 已按【{self.name}】要求攻击 {required.name if required is not None else '嘲讽来源'}。")


def replace_required_attack_status(battle: Battle, target: HeroUnit, status: RequiredAttackStatus) -> None:
    """Keep one executable forced-target contract; the newest taunt replaces older targets."""
    for existing in list(target.statuses):
        if isinstance(existing, RequiredAttackStatus):
            target.remove_status(existing, battle)
    target.add_status(status)


class CatTauntStatus(RequiredAttackStatus):
    def __init__(self, cat_unit_id: str) -> None:
        super().__init__(
            "嘲讽之吼",
            "下个自己的回合中，攻击猫叔前只能移动。",
            cat_unit_id,
        )


class CatTauntRoarSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "cat_taunt_roar",
            "嘲讽之吼",
            "普通技能：每回合一次；对周围7*7单位造成当前攻伤害；被击中单位下个自己的回合在攻击猫叔前只能移动；破魔。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=3)

    def affected_units(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.affected_cells(battle, actor), ignore=actor)  # type: ignore[return-value]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.affected_cells(battle, actor)
        for target in list(self.affected_units(battle, actor)):
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    ignore_shield=True,
                    tags={"skill", "attack", "control", "pierce", self.code},
                )
            )
            if ctx.cancelled or not target.alive:
                continue
            replace_required_attack_status(battle, target, CatTauntStatus(actor.unit_id))
            battle.log(f"{target.name} 被 {actor.name} 的【嘲讽之吼】锁定。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.affected_units(battle, actor)
        return {
            "cells": positions_to_dict(self.affected_cells(battle, actor)),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": False,
        }

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return self.affected_units(battle, actor)

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.affected_cells(battle, actor)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class CatRetaliationLockStatus(StatusEffect):
    locked_flags = ("cannot_move",)

    def __init__(self) -> None:
        super().__init__("猫叔反制", "当前回合不能移动或使用主动技能。", duration=1, tick_scope="any_turn_end")

    def bind(self, owner: HeroUnit) -> "CatRetaliationLockStatus":
        super().bind(owner)
        owner.cannot_move = True
        return self

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if skill.timing == "active":
            return True, f"{actor.name} 受到猫叔反制，当前回合不能使用主动技能。"
        return False, ""

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_move = any(
            "cannot_move" in getattr(status, "locked_flags", ())
            or (isinstance(status, FlagStatus) and status.flag_name == "cannot_move" and status.value)
            for status in owner.statuses
        )


def _straight_destination_around(battle: Battle, mover: HeroUnit, anchor: HeroUnit) -> Position | None:
    if mover.position is None or anchor.position is None:
        return None
    start = mover.position
    reachable = battle.reachable_positions(
        mover,
        max_distance=battle.width + battle.height,
        straight_only=True,
    )
    ordered = sorted(
        [start, *reachable],
        key=lambda cell: (
            min((cell.distance_to(anchor_cell) for anchor_cell in battle.unit_cells(anchor)), default=10**9),
            start.distance_to(cell),
            cell.y,
            cell.x,
        ),
    )
    destination = ordered[0] if ordered else start
    if destination == start:
        return start
    return destination


class CatRetaliationTrait(Trait):
    def __init__(self) -> None:
        super().__init__("猫叔反制", "任何单位攻击或技能影响猫叔时魔-1，并沿直线尽量被拉近，本回合不能移动或使用主动技能。")
        self.last_trigger_key: tuple[Any, ...] | None = None

    def _trigger(
        self,
        battle: Battle,
        *,
        actor: HeroUnit,
        target: HeroUnit,
        action_name: str,
        is_skill: bool,
        tags: set[str],
        from_field_effect: bool,
    ) -> None:
        owner = self.owner
        if (
            owner is None
            or target.unit_id != owner.unit_id
            or from_field_effect
            or actor.unit_id == owner.unit_id
            or (not is_skill and "attack" not in tags)
        ):
            return
        resolution_token = battle.current_action_resolution_token
        key = (
            ("queued_action", resolution_token)
            if resolution_token is not None
            else ("direct_effect", battle.turn_number, actor.unit_id, action_name, len(actor.actions_taken_this_turn))
        )
        if key == self.last_trigger_key:
            return
        self.last_trigger_key = key
        lost = actor.spend_mana(min(1.0, actor.current_mana))
        start = actor.position
        destination = _straight_destination_around(battle, actor, owner)
        moved = False
        if destination is not None and start is not None and destination != start:
            try:
                battle.move_unit(
                    actor,
                    destination,
                    via_skill=True,
                    forced=True,
                    straight_only=True,
                    max_distance=battle.width + battle.height,
                    tags={"cat_retaliation"},
                )
                moved = True
            except ActionError:
                destination = None
        existing = actor.get_status("猫叔反制")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(CatRetaliationLockStatus())
        if moved:
            movement_text = "并被沿直线尽量拉近猫叔"
        elif destination == start and start is not None:
            movement_text = "且已经处于可达的最近直线位置"
        else:
            movement_text = "，但没有合法直线路径"
        battle.log(f"{actor.name} 因攻击或技能猫叔失去 {lost:g} 魔{movement_text}，本回合不能移动或使用主动技能。")
        battle.record_rule_trigger_summary("cat_retaliation", actor=actor, target=owner)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        self._trigger(
            battle,
            actor=ctx.actor,
            target=ctx.target,
            action_name=ctx.action_name,
            is_skill=ctx.is_skill,
            tags=ctx.tags,
            from_field_effect=ctx.from_field_effect,
        )

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is None:
            return
        self._trigger(
            battle,
            actor=ctx.source,
            target=ctx.target,
            action_name=ctx.action_name,
            is_skill=ctx.is_skill,
            tags=ctx.tags,
            from_field_effect=ctx.from_field_effect,
        )


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


class BoxerTauntStatus(RequiredAttackStatus):
    def __init__(self, boxer_unit_id: str) -> None:
        super().__init__(
            "擂台嘲讽",
            "下个自己的回合中，攻击反击拳手前只能移动。",
            boxer_unit_id,
        )

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.cannot_attack or owner.attacks_used >= owner.attack_actions_per_turn():
            self.requirement_active = False
            self.requirement_satisfied = True
            return
        super().on_owner_turn_start(battle)


class RingTauntSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "ring_taunt",
            "擂台嘲讽",
            "普通技能：每回合一次；对周围单位造成当前攻伤害；被击中单位下回合有攻击数时必须先攻击反击拳手；破魔。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=1)

    def affected_units(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.affected_cells(battle, actor), ignore=actor)  # type: ignore[return-value]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.affected_cells(battle, actor)
        for target in list(self.affected_units(battle, actor)):
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    ignore_shield=True,
                    tags={"skill", "attack", "control", "pierce", self.code},
                )
            )
            if ctx.cancelled or not target.alive:
                continue
            replace_required_attack_status(battle, target, BoxerTauntStatus(actor.unit_id))
            battle.log(f"{target.name} 被 {actor.name} 的【擂台嘲讽】锁定。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.affected_units(battle, actor)
        cells = self.affected_cells(battle, actor)
        return {
            "cells": positions_to_dict(cells),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": False,
        }

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return self.affected_units(battle, actor)

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.affected_cells(battle, actor)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class BoxerBlockCounterSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "boxer_block_counter",
            "同时格挡反击",
            "被动技能：受到敌方速度1动作影响时，守+1并立即对范围内来源反击。",
            timing="passive",
            target_mode="self",
        )

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id or queued_action.speed >= 2:
            return False, "只能连锁敌方速度 1 动作。"
        source = battle.units.get(queued_action.actor_id)
        if source is None or not battle.attack_target_allowed(actor, source)[0]:
            return False, "来源不在反击范围内。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        source = battle.units.get(queued_action.actor_id)
        if source is None:
            return
        actor.add_status(
            TemporaryDefenseStatus(
                "同时格挡反击",
                defense_delta=1,
                description="本次连锁伤害结算时守 +1。",
                expire_with_chain=True,
            )
        )
        if battle.attack_target_allowed(actor, source)[0]:
            battle.resolve_attack_damage(actor, source, action_name=self.name, tags={"counter", self.code})
        battle.log(f"{actor.name} 同时格挡并反击 {source.name}。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("同时格挡反击只能通过连锁使用。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        source = battle.units.get(queued_action.actor_id)
        return {
            "cells": positions_to_dict(battle.unit_cells(source)) if source is not None else [],
            "target_unit_ids": [source.unit_id] if source is not None else [],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": False,
        }


class RedNextDamageStatus(StatusEffect):
    def __init__(self, target_unit_id: str) -> None:
        super().__init__("铁锁追击", "本回合对铁锁路径锚点造成的下一次直接伤害，伤害值 +1。", duration=1, tick_scope="any_turn_end")
        self.target_unit_id = target_unit_id

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (
            owner is None
            or ctx.source is None
            or ctx.source.unit_id != owner.unit_id
            or ctx.target.unit_id != self.target_unit_id
            or ctx.from_field_effect
        ):
            return
        if ctx.raw_damage is None:
            ctx.attack_power += 1
        else:
            ctx.raw_damage = round(ctx.raw_damage + 1, 4)
        owner.remove_status(self, battle)
        battle.log(f"{owner.name} 的【铁锁追击】使对 {ctx.target.name} 的这次伤害值 +1。")


class IronChainPathSkill(H1LineDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "iron_chain_path",
            "铁锁路径",
            "普通技能：每回合第一次免费，之后费0.5魔；破魔伤害前5格敌方，并移动到最近单位或地形周围；下次伤害+1。",
            length=5,
            mana_cost=0.5,
        )

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        self.sync_turn_scope(battle)
        return 0.0 if self.uses_this_turn == 0 else super().mana_cost_for_payload(battle, actor, payload)

    def _stable_unit_key(self, battle: Battle, unit: HeroUnit) -> tuple[int, str]:
        try:
            turn_slot = battle.turn_order_unit_ids.index(unit.unit_id)
        except ValueError:
            turn_slot = len(battle.turn_order_unit_ids)
        return turn_slot, unit.unit_id

    def anchor_for_cells(
        self,
        battle: Battle,
        actor: HeroUnit,
        cells: list[Position],
    ) -> tuple[str, HeroUnit | None, list[Position], str] | None:
        """Return the mandatory nearest anchor in declared near-to-far line order.

        The first tuple item is ``unit`` or ``terrain``.  Terrain-shaped units
        remain terrain anchors, so they provide movement but never the +1
        follow-up intended for a unit anchor.
        """
        for cell in cells:
            units = sorted(
                [unit for unit in battle.units_at(cell) if unit.unit_id != actor.unit_id and unit.alive],
                key=lambda unit: self._stable_unit_key(battle, unit),
            )
            if units:
                unit = units[0]
                anchor_kind = "terrain" if _terrain_unit(unit) else "unit"
                return anchor_kind, unit, list(battle.unit_cells(unit)), unit.name  # type: ignore[arg-type]
            if (cell.x, cell.y) in battle.blocked_cells:
                return "terrain", None, [cell], f"墙体({cell.x},{cell.y})"
            terrain_effects = sorted(
                [
                    effect
                    for effect in battle.field_effects
                    if getattr(effect, "is_terrain", False) and cell in effect.affected_cells(battle)
                ],
                key=lambda effect: effect.component_id,
            )
            if terrain_effects:
                effect = terrain_effects[0]
                return "terrain", None, list(effect.affected_cells(battle)), effect.name
        return None

    def destination_for_anchor(
        self,
        battle: Battle,
        actor: HeroUnit,
        anchor_cells: list[Position],
    ) -> Position | None:
        if actor.position is None or not anchor_cells:
            return None
        start = actor.position
        candidates = [
            start,
            *battle.reachable_positions(actor, max_distance=5, straight_only=True),
        ]
        adjacent = [
            cell
            for cell in candidates
            if min((cell.distance_to(anchor_cell) for anchor_cell in anchor_cells), default=10**9) == 1
        ]
        if not adjacent:
            return None
        return min(adjacent, key=lambda cell: (start.distance_to(cell), cell.y, cell.x))

    def affected_units(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [unit for unit in battle.units_at_cells(cells) if unit.player_id != actor.player_id]  # type: ignore[return-value]

    def resolve_hit(self, battle: Battle, actor: HeroUnit, target: HeroUnit, cells: list[Position]) -> DamageContext:
        return battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name=self.name,
                ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", "attack", self.code},
            )
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        anchor_info = self.anchor_for_cells(battle, actor, cells)
        for target in list(self.affected_units(battle, actor, cells)):
            self.resolve_hit(battle, actor, target, cells)
        if anchor_info is None or actor.position is None:
            return
        anchor_kind, anchor_unit, anchor_cells, anchor_name = anchor_info
        destination = self.destination_for_anchor(battle, actor, anchor_cells)
        if destination is not None and destination != actor.position:
            try:
                battle.move_unit(
                    actor,
                    destination,
                    via_skill=True,
                    forced=False,
                    straight_only=True,
                    max_distance=5,
                    tags={self.code},
                )
            except ActionError:
                destination = None
        movement_text = "并移动到其周围" if destination is not None else "但没有合法直线落点"
        if anchor_kind == "unit" and anchor_unit is not None:
            actor.add_status(RedNextDamageStatus(anchor_unit.unit_id))
            battle.log(
                f"{actor.name} 以 {anchor_name} 为铁锁锚点{movement_text}，"
                "本回合对其下一次直接伤害值 +1。"
            )
        else:
            battle.log(f"{actor.name} 以地形【{anchor_name}】为铁锁锚点{movement_text}；地形锚点不获得追击加伤。")

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class RedDrainManaSkill(DrainManaSkill):
    def __init__(self) -> None:
        super().__init__()

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        ctx = battle.validate_target(
            actor,
            target,
            action_name=self.name,
            is_skill=True,
            is_hostile=True,
            ignore_shield=bool(payload.get("red_random_pierce")),
            tags={"skill", "drain_mana"},
        )
        if ctx.cancelled:
            if ctx.reason:
                battle.log_public_event(ctx.reason, source=actor, target=target)
            return
        if is_mana_drain_immune(target):
            battle.log(f"{target.name} 无法被吸魔。")
            return
        lost = min(target.current_mana, 1.0)
        target.spend_mana(lost)
        gained = actor.gain_mana(lost)
        battle.log(f"{actor.name} 吸取了 {target.name} 的 {gained:g} 点魔力。")


class RedRandomPierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("赤之随机破魔", "每次声明普攻或技能时后端公开随机；1/2几率该整次动作破魔。")

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type not in {"attack", "skill"}:
            return
        success = random.random() < 0.5
        payload["red_random_pierce"] = success
        if success:
            payload["ignore_shield"] = True
        result = "成功，本次动作破魔" if success else "未触发"
        # This result is explicitly public even when the declared area contains
        # a stealthed target and ordinary action logs are suppressed.
        battle.log_public_event(f"{owner.name} 的【赤之随机破魔】{result}。", source=owner)


class RhinoLegacyStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("强袭犀牛遗志", attack_delta=1, defense_delta=1, description="攻 +1，守 +1。", duration=3, tick_scope="owner_turn_end")


class RhinoReturnEffect(BattleFieldEffect):
    def __init__(self, rhino: HeroUnit, death_round: int, death_turn_number: int, turn_ring_size: int) -> None:
        super().__init__("强袭犀牛回归", "被破坏满3轮后，若有己方兽人武将则满状态回归。", duration=None)
        self.rhino = rhino
        self.death_round = death_round
        self.death_turn_number = death_turn_number
        self.return_turn_number = death_turn_number + max(1, turn_ring_size) * 3

    def _candidate_destinations(self, battle: Battle) -> list[tuple[HeroUnit, Position]]:
        rhino = self.rhino
        candidates: list[tuple[HeroUnit, Position]] = []
        orc_heroes = [
            unit
            for unit in battle.player_units(rhino.player_id)
            if not unit.is_summon and not unit.is_clone and unit.race == "兽人" and unit.position is not None and unit.alive
        ]
        turn_slots = {unit_id: index for index, unit_id in enumerate(battle.turn_order_unit_ids)}
        for orc in sorted(
            orc_heroes,
            key=lambda unit: (turn_slots.get(unit.unit_id, len(turn_slots)), unit.unit_id),
        ):
            for cell in sorted(square_around_cells(battle, battle.unit_cells(orc), radius=1), key=lambda item: (item.y, item.x)):
                if battle.can_place_unit(rhino, cell, ignore=rhino):
                    candidates.append((orc, cell))
        return candidates

    def on_turn_start(self, battle: Battle, active_unit: HeroUnit | None) -> None:
        if battle.turn_number < self.return_turn_number:
            return
        candidates = self._candidate_destinations(battle)
        if not candidates:
            battle.log(f"{self.rhino.name} 暂无可用的己方兽人武将或合法邻接格，继续等待回归。")
            return
        anchor, destination = candidates[0]
        rhino = self.rhino
        for status in list(rhino.statuses):
            if isinstance(status, StatModifierStatus) and status.duration is None:
                continue
            rhino.remove_status(status, battle)
        rhino.alive = True
        rhino.banished = False
        rhino.banish_return_position = None
        rhino.banish_turns_remaining = 0
        rhino.current_hp = rhino.max_health
        rhino.current_mana = rhino.max_mana()
        rhino.turn_ready = False
        rhino.move_used = False
        rhino.normal_move_actions_used = 0
        rhino.normal_move_steps_used = 0
        rhino.attacks_used = 0
        rhino.performed_active_skill = False
        rhino.moved_this_turn = False
        rhino.actions_taken_this_turn = []
        rhino.clear_end_of_turn_shields()
        for trait in rhino.traits:
            if isinstance(trait, RhinoDeathTrait):
                trait.triggered = False
        battle.add_unit(rhino, destination)
        battle.destroyed_units = [unit for unit in battle.destroyed_units if unit.unit_id != rhino.unit_id]
        battle.remove_field_effect(self)
        battle.notify_destroyed_hero_count_changed()
        battle.log(f"{rhino.name} 在 {anchor.name} 周围满状态回归。")


class RhinoDeathTrait(Trait):
    def __init__(self) -> None:
        super().__init__("强袭犀牛遗志", "被破坏时强化当前盟友3轮，并在3轮后尝试于己方兽人武将周围回归。")
        self.triggered = False

    def on_enter_battle(self, battle: Battle) -> None:
        self.triggered = False

    def trigger_legacy(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.alive or self.triggered:
            return
        self.triggered = True
        for ally in list(battle.player_units(owner.player_id)):
            if ally.unit_id == owner.unit_id or not ally.alive or ally.position is None:
                continue
            existing = ally.get_status("强袭犀牛遗志")
            if existing is not None:
                ally.remove_status(existing, battle)
            ally.add_status(RhinoLegacyStatus())
        battle.add_field_effect(
            RhinoReturnEffect(
                owner,
                battle.round_number,
                battle.turn_number,
                len(battle.turn_order_unit_ids),
            )
        )
        battle.log(f"{owner.name} 被破坏，当前全部其他己方单位获得攻+1、守+1，持续3轮。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        self.trigger_legacy(battle)

    def on_owner_removed(self, battle: Battle) -> None:
        # Covers destruction effects that set alive=False without creating a damage context.
        self.trigger_legacy(battle)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _slug_tail(source_row: int, name: str) -> str:
    ascii_tail = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_").lower()
    return ascii_tail or f"r{source_row:03d}"


def _special_skill_factory(spec: dict[str, Any], fragment: dict[str, Any]) -> Skill | None:
    name = _text(fragment.get("name"))
    source = _text(fragment.get("fragment"))
    if spec.get("code") == "excel_r126" and name == "爆炎":
        return SolarFlameSkill()
    if spec.get("code") == "excel_r126" and name == "斯芬克斯炮":
        return SphinxCannonSkill()
    if spec.get("code") == "excel_r126" and name == "审判":
        return SolarJudgmentSkill()
    if spec.get("code") == "excel_r142" and name == "嘲讽之吼":
        return CatTauntRoarSkill()
    if spec.get("code") == "excel_r198" and name == "二轮车":
        return SummonBicycleSkill()
    if spec.get("code") == "excel_r206" and name == "擂台嘲讽":
        return RingTauntSkill()
    if spec.get("code") == "excel_r291" and name == "铁锁路径":
        return IronChainPathSkill()
    if spec.get("code") == "excel_r291" and name == "吸魔":
        return RedDrainManaSkill()
    if spec.get("code") == "excel_r143" and name == "重锤":
        return ThorHeavyHammerSkill()
    if spec.get("code") == "excel_r143" and name == "暴怒冲击":
        return ThorRageImpactSkill()
    if spec.get("code") == "excel_r143" and name == "毁灭电击":
        return ThorDestroyLightningSkill()
    if spec.get("code") == "excel_r172" and name == "兵刺":
        return BeetleSpearSkill()
    if spec.get("code") == "excel_r172" and name == "全力攻击":
        return BeetleFullForceSkill()
    if spec.get("code") == "excel_r172" and name == "铠甲部署":
        return BeetleArmorDeploySkill()
    if spec.get("code") == "excel_r224" and name == "离子盾":
        return IonShieldSkill()
    if spec.get("code") == "excel_r224" and name == "镭射":
        return ElectronicLaserSkill()
    if spec.get("code") == "excel_r225" and name == "导弹":
        return MissileSkill()
    if spec.get("code") == "excel_r225" and name == "离子盾":
        return IonShieldSkill()
    if spec.get("code") == "excel_r225" and name == "量子盾":
        return QuantumShieldSkill()
    if spec.get("code") == "excel_r264" and name == "飞跃":
        return WingedLeopardLeapSkill()
    if spec.get("code") == "excel_r264" and name == "鹰眼":
        return EagleEyeSkill()
    if spec.get("code") == "excel_r138" and name == "龙息":
        return MessengerDragonBreathSkill()
    if spec.get("code") == "excel_r138" and name == "穿刺":
        return MessengerPierceSkill()
    if spec.get("code") == "excel_r138" and name == "无极落霞轮回":
        return MessengerReincarnationSkill()
    if spec.get("code") == "excel_r138" and name == "造化":
        return MessengerCreationSkill()
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
    if spec.get("code") == "excel_r142" and "魔-1" in text:
        return CatRetaliationTrait()
    if spec.get("code") == "excel_r198" and "攻击后移动次数+1" in text:
        return ExtraMoveAfterAttackTrait()
    if spec.get("code") == "excel_r206" and "格挡的同时反击" in text:
        return BlockCounterTrait()
    if spec.get("code") == "excel_r291" and "1/2几率破魔" in text:
        return RedRandomPierceTrait()
    if spec.get("code") == "excel_r356" and "攻+1" in text and "守+1" in text:
        return RhinoDeathTrait()
    if spec.get("code") == "excel_r120" and text == "一回合内不限次数移动":
        return BlindManaMovementTrait()
    if spec.get("code") == "excel_r120" and text == "需要用一个魔走一格":
        return None
    if spec.get("code") == "excel_r120" and text == "普攻破魔":
        return BasicAttackPierceTrait()
    if spec.get("code") == "excel_r120" and text == "攻击一周":
        return BlindCircleAttackTrait()
    if spec.get("code") == "excel_r120" and text == "普攻被回避后攻击次数重置":
        return BlindEvasionAttackResetTrait()
    if spec.get("code") == "excel_r224" and "攻比此单位低" in text:
        return ElectronicLaserDamageBonusTrait()
    if spec.get("code") == "excel_r224" and "名字被视为“电子龙”" in text:
        return ElectronicDragonNameTrait()
    if spec.get("code") == "excel_r225" and "守低" in text and "伤害-1" in text:
        return ElectronicBarrierPersonalGuardTrait()
    if spec.get("code") == "excel_r225" and "名字被视为“电子龙”" in text:
        return ElectronicDragonNameTrait()
    if spec.get("code") == "excel_r225" and "周围的单位" in text and "机甲" in text:
        return ElectronicBarrierAuraTrait()
    if spec.get("code") == "excel_r264" and "使用“飞跃”后重置" in text:
        return WingedLeopardMoveResetTrait()
    if spec.get("code") == "excel_r138" and text == "使用伤害技能后魔力点+1":
        return MessengerDamageSkillManaTrait()
    if spec.get("code") == "excel_r138" and text == "技能伤害破魔":
        return MessengerSkillDamagePierceTrait()
    if spec.get("code") == "excel_r327" and "攻，守+被破坏的武将的数量" in text:
        return RedLotusDestroyedHeroesTrait()
    if spec.get("code") == "excel_r327" and text == "不受到技能伤害":
        return RedLotusSkillDamageImmunityTrait()
    if spec.get("code") == "excel_r327" and text == "普攻破魔":
        return BasicAttackPierceTrait()
    if spec.get("code") == "excel_r327" and text == "普攻无法被回避":
        return RedLotusUnevadableAttackTrait()
    if spec.get("code") == "excel_r327" and text == "每回合仅受到一次伤害":
        return RedLotusOnceDamagePerTurnTrait()
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
        if spec.get("code") == "excel_r033" and "lao_damage_stat_cancel" not in seen_codes:
            skills.append(LaoDamageStatCancelSkill())
        if spec.get("code") == "excel_r206" and "boxer_block_counter" not in seen_codes:
            skills.append(BoxerBlockCounterSkill())
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
        "__module__": __name__,
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
    if spec.get("code") == "excel_r029":
        attrs["stat_minimums"] = {"attack": 0.0}
    if width > 1 or height > 1:
        attrs["footprint_width"] = width
        attrs["footprint_height"] = height
        attrs["entry_footprint_width"] = width
        attrs["entry_footprint_height"] = height
    if spec.get("code") == "excel_r032":
        # 亚伦开场会在自己的锚点召唤 1*2 大独角兽。编队落位必须提前
        # 为坐骑预留第二格，否则紧凑战场上的下一名队友会与坐骑重叠。
        attrs["entry_footprint_width"] = 1
        attrs["entry_footprint_height"] = 2
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
        "excel_r120",
        "excel_r123",
        "excel_r126",
        "excel_r127",
        "excel_r136",
        "excel_r137",
        "excel_r138",
        "excel_r139",
        "excel_r142",
        "excel_r143",
        "excel_r158",
        "excel_r166",
        "excel_r172",
        "excel_r187",
        "excel_r188",
        "excel_r198",
        "excel_r206",
        "excel_r224",
        "excel_r225",
        "excel_r264",
        "excel_r291",
        "excel_r326",
        "excel_r327",
        "excel_r337",
        "excel_r352",
        "excel_r356",
        "excel_r379",
    }
)


EXCEL_HERO_REGISTRY: dict[str, HeroFactory] = {
    spec["code"]: _hero_class_from_spec(spec)
    for spec in EXCEL_HERO_SPECS
}
for _excel_hero_class in EXCEL_HERO_REGISTRY.values():
    globals()[_excel_hero_class.__name__] = _excel_hero_class

EXCEL_HERO_NAMES_BY_CODE: dict[str, str] = {
    spec["code"]: spec["name"]
    for spec in EXCEL_HERO_SPECS
}
