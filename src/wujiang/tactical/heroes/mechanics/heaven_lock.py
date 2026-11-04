"""R020 heaven lock skill and status."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError, Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.heroes.common import FlagStatus
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect


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
    def ai_control_bonus(self, context: AISkillScoreContext, targets: list[Unit]) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        target_id = payload.get("target_unit_id")
        target = battle.get_unit(str(target_id)) if target_id else (targets[0] if targets else None)
        if (target is None or target.player_id == actor.player_id or target.has_status("天锁")
                or target.cannot_normal_move or target.stat("speed") <= 0):
            return -1000.0
        with context.probe_rollback():
            try:
                self.execute(battle, actor, payload)
            except ActionError:
                return -1000.0
            if not target.has_status("天锁"):
                return -1000.0
        return 24.0 + min(6.0, target.stat("speed")) * 7.0 - 1.5 * 12.0

    def ai_hostile_target_impact(self, battle, actor, payload, target):
        return (not target.has_status("天锁") and not target.cannot_normal_move
                and target.stat("speed") > 0 and not getattr(target, "world_seed_terrain", False))

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


