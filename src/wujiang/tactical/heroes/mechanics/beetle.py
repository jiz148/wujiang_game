"""R172 beetle spear and armor objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.hell_slash import HellSlashSkill
from wujiang.tactical.heroes.mechanics.thor import H1AllActionLockStatus


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


