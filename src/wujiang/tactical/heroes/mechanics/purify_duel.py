"""R113 purify mana and sacred duel mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.heroes.common import ensure_enemy
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect


class PurifyManaSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r15_effect_score(self)

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
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r15_effect_score(self)

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


