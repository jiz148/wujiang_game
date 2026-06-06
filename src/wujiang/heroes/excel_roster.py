from __future__ import annotations

import re
import random
from typing import Any, Callable

from wujiang.engine.core import (
    ActionError,
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
    PassiveEvasionSkill,
    PassiveProtectionSkill,
    PierceSkill,
    ShensuSkill,
    StationaryRecoveryTrait,
    StatModifierStatus,
    StealthSkill,
    StoneWallSkill,
    ensure_ally,
    ensure_distance,
    ensure_enemy,
    line_patterns,
    localized_line_patterns,
    match_payload_pattern,
    pattern_signature,
    pattern_selection_preview,
    payload_position,
    payload_target_unit,
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
    def __init__(self, code: str, name: str, *, duration: int | None = None, marker: str | None = None) -> None:
        duration_text = "永久" if duration is None else f"持续 {duration} 个全局天气倒计时"
        super().__init__(
            code,
            name,
            f"大招：一场战斗一次，将全场天气变为“{name}”，{duration_text}。",
            max_uses_per_battle=1,
            target_mode="self",
        )
        self.weather_duration = duration
        self.weather_marker = marker

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(SimpleGlobalWeatherEffect(self.name, duration=self.weather_duration, marker=self.weather_marker))


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
        for component in actor.iter_components():
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
        target_ids = [
            unit.unit_id
            for unit in battle.enemy_units(actor.player_id)
            if any(self.target_in_range_from_landing(battle, actor, unit, landing) for landing in cells)
        ]
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
        super().__init__("sky_sanctuary", "天空的圣域", marker="圣")


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
    def __init__(self) -> None:
        super().__init__("被动封锁", "不能使用被动技能。", duration=3, tick_scope="owner_turn_end")

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
            if ally.unit_id == owner.unit_id or ally.is_clone or ally.has_status("菊之遗击"):
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

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or len(ctx.path) <= 2:
            return
        passed_cells = set(ctx.path[1:-1])
        hit_units: list[HeroUnit] = []
        for unit in battle.all_units():
            if unit.unit_id == owner.unit_id or unit.position is None or unit.banished:
                continue
            if any(cell in passed_cells for cell in battle.unit_cells(unit)):
                hit_units.append(unit)  # type: ignore[arg-type]
        for unit in hit_units:
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

    def affected_enemies(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [unit for unit in battle.units_at_cells(cells) if unit.player_id != actor.player_id]  # type: ignore[list-item]

    def choose_target(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], cells: list[Position]) -> HeroUnit:
        enemies = self.affected_enemies(battle, actor, cells)
        if not enemies:
            raise ActionError("天罚区域内没有敌方单位。")
        if payload.get("target_unit_id"):
            target = payload_target_unit(battle, payload)
            if target.unit_id not in {unit.unit_id for unit in enemies}:
                raise ActionError("指定目标不在天罚区域内。")
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
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
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


EXCEL_HERO_REGISTRY: dict[str, HeroFactory] = {
    spec["code"]: _hero_class_from_spec(spec)
    for spec in EXCEL_HERO_SPECS
}

EXCEL_HERO_NAMES_BY_CODE: dict[str, str] = {
    spec["code"]: spec["name"]
    for spec in EXCEL_HERO_SPECS
}
