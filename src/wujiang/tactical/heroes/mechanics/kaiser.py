"""R093 Kaiser fist and action lock mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import payload_target_unit


class KaiserFistSkill(Skill):
    def ai_effective_enemy_impact(self, battle, actor, payload, targets=None, shared=None, action=None):
        if targets and shared.kaiser_fist_score(battle, actor, self, payload, shared.difficulty_profile("standard")) > 0:
            return True
        return None

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.kaiser_fist_score(context)

    def ai_attack_power(self, battle, actor, payload, target, cells) -> float:
        return actor.stat("attack") + 1

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


