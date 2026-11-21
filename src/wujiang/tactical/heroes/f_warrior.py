"""R134: source-specific enlarged variants of existing common skills."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import Battle, Position, Unit
from wujiang.tactical.heroes.common import (
    DashMoveSkill, DrainManaSkill, MachineGunSkill, NextNormalMoveBoostStatus,
    ShensuSkill, is_mana_drain_immune,
)
from wujiang.tactical.heroes.ai_impact_rule import R18_IMPACT
from wujiang.tactical.heroes.mechanics.dragon_breath import DragonBreathSkill
from wujiang.tactical.heroes.mechanics.foundation import LargePierceSkill
from wujiang.tactical.heroes.mechanics.next_five_geometry import nearby_rectangle_patterns
from wujiang.tactical.heroes.temple_archangel import LargeHardenSkill


class FBigLeapSkill(DashMoveSkill):
    def __init__(self) -> None:
        # The shared fly_leap action code keeps the established AI movement route.
        super().__init__("fly_leap", "飞跃（大）",
                         "1魔、每本人轮一次；直线飞行恰4格，可穿过单位。",
                         max_distance=4, exact_distance=4, mana_cost=1,
                         max_uses_per_turn=1, straight_only=True, ignore_units=True)


class FBigShensuSkill(ShensuSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "big_shensu"
        self.name = "神速（大）"
        self.description = "1魔、每本人轮一次；下次普通移动格数+4。"

    def apply_to_self(self, battle: Battle, actor: Unit) -> None:
        previous = actor.get_status("神速")
        if previous is not None:
            actor.remove_status(previous, battle)
        actor.add_status(NextNormalMoveBoostStatus(4), source=actor)
        battle.log_public_event(f"{actor.name} 的下次普通移动格数+4。", source=actor, target=actor)


class FBigHardenSkill(LargeHardenSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "big_harden"
        self.name = "硬化（大）"
        self.description = "与普通硬化同费同次数和期限，守改为+2，持续两本人轮。"


class FLargeManaDrainSkill(DrainManaSkill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__()
        self.code = "large_mana_drain"
        self.name = "吸魔（大）"
        self.description = "与普通吸魔同费同次数；单体选择距离增加1格，吸最多1魔。"

    def direct_unit_target_range(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range() + 1

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if (target is None or not target.alive or target.position is None
                or target.player_id == actor.player_id or target.current_mana <= 0
                or target.magic_immunity or target.total_shields() > 0
                or is_mana_drain_immune(target)
                or not battle.unit_target_in_range_and_line(actor, target,
                                                             self.direct_unit_target_range(battle, actor))):
            return -1000.0
        return min(1.0, target.current_mana) * 45.0 - 5.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0


class FLargeDragonBreathSkill(DragonBreathSkill):
    ai_area_bonus_codes = frozenset({"dragon_breath", "remote_dragon_breath", "large_dragon_breath"})
    ai_effective_impact_rule = R18_IMPACT

    def __init__(self) -> None:
        super().__init__()
        self.code = "large_dragon_breath"
        self.name = "龙吸（大）"
        self.description = "2魔、每本人轮最多2次；近身选择3×3区域，按当前攻伤害双方。"

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return nearby_rectangle_patterns(battle, actor, 3, 3)


class FLargeMachineGunSkill(MachineGunSkill):
    ai_area_bonus_codes = frozenset({"machine_gun", "large_machine_gun"})
    ai_effective_impact_rule = R18_IMPACT
    line_length = 4

    def __init__(self) -> None:
        super().__init__()
        self.code = "large_machine_gun"
        self.name = "机枪（大）"
        self.description = "0魔、每本人轮一次；选择贴身连续直线4格，按当前攻伤害双方。"


__all__ = [
    "LargePierceSkill", "FBigLeapSkill", "FBigShensuSkill", "FBigHardenSkill",
    "FLargeManaDrainSkill", "FLargeDragonBreathSkill", "FLargeMachineGunSkill",
]
