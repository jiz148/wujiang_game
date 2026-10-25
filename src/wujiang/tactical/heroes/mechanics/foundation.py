"""Reusable recovery, magic immunity, piercing, and guardian mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import PierceSkill
from wujiang.tactical.heroes.common import localized_line_patterns
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature


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


