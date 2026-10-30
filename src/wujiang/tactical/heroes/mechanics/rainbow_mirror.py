"""Rainbow and friendly mirror objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import ActionMiss
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import FlagStatus
from wujiang.tactical.heroes.common import ensure_ally
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class RainbowMirrorNoMoveStatus(FlagStatus):
    def __init__(self) -> None:
        super().__init__("彩虹镜", "cannot_normal_move", description="当前全局回合不能普通移动，技能位移和回避仍可。", duration=1, tick_scope="any_turn_end")


class RainbowMirrorSkill(Skill):
    requires_direct_unit_target_line = False

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r17_effect_score(self)

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


