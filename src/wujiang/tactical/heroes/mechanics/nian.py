"""R059 Nian dragon breath, roar, and jade flash."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import ensure_enemy
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.mechanics.electric_wind import front_rectangle_patterns
from wujiang.tactical.heroes.mechanics.dragon_breath import DragonBreathSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_geometry import nearby_rectangle_patterns


class NianLargeDragonBreathSkill(DragonBreathSkill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r13_effect_score(self)

    def __init__(self) -> None:
        super().__init__()
        self.code = "nian_large_dragon_breath"
        self.name = "龙息（大）"
        self.description = "普通技能：规则同龙息（大），费 2 魔，每回合最多 2 次，近身选择 3*3 区域，按当前攻击造成技能伤害。"

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return nearby_rectangle_patterns(battle, actor, 3, 3)


class NianDragonDanceSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r13_effect_score(self)

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
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r13_effect_score(self)

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
    def __init__(self, applied_turn_number: int | None = None) -> None:
        super().__init__("碧玉闪光", "不能回复。", duration=1, tick_scope="owner_turn_end")
        self.flag_name = "cannot_heal"
        self.applied_turn_number = applied_turn_number

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.applied_turn_number:
            super().on_owner_turn_end(battle)

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
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r13_effect_score(self)

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
                status=NianNoHealStatus(battle.turn_number),
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


