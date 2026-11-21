"""R119 Huangquan: triple speed, permanent toggle and split movement."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import Battle, Skill, StatusEffect, Trait, Unit
from wujiang.tactical.heroes.common import positions_to_dict


class TripleSpeedStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("三倍速", "本轮当前速变为三倍。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value * 3 if stat_name == "speed" else value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None and ended_player_id == self.owner.player_id:
            self.owner.remove_status(self, battle)


class TripleSpeedSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("triple_speed", "三倍速", "每2本人轮一次；本轮速变为三倍。",
                         cooldown_turns=2, target_mode="self")

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.has_status("三倍速") or actor.remaining_normal_move_distance(battle) <= 0:
            return -1000.0
        enemies = [enemy for enemy in battle.enemy_units(actor.player_id)
                   if enemy.alive and enemy.position is not None]
        nearest = min((battle.distance_between_units(actor, enemy) for enemy in enemies), default=0)
        return 32.0 if nearest > actor.normal_move_distance() and nearest <= actor.normal_move_distance() * 3 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        old = actor.get_status("三倍速")
        if old is not None:
            actor.remove_status(old, battle)
        actor.add_status(TripleSpeedStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 的当前速变为三倍。", source=actor)


class CorruptionStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("腐化", "攻守速各+1；无法使用需要消耗魔力的技能，直到轮初关闭。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name in {"attack", "defense", "speed"} else value

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if skill.code == "corruption":
            return False, ""
        try:
            cost = skill.mana_cost_for_payload(battle, actor, None)
        except (TypeError, ValueError):
            cost = skill.mana_cost
        return (True, "腐化形态不能用魔。") if cost > 0 else (False, "")


class CorruptionSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("corruption", "腐化",
                         "每本人轮一次，仅轮初开关；开时攻守速各+1且不能用魔。",
                         max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used or actor.performed_active_skill:
            return False, "腐化只能在本人轮开始阶段切换。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        enemies = [enemy for enemy in battle.enemy_units(actor.player_id)
                   if enemy.alive and enemy.position is not None]
        if not enemies:
            return -1000.0
        nearest = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        if actor.has_status("腐化"):
            return 28.0 if nearest > 3 and actor.current_mana >= 2 else -1000.0
        return 26.0 if nearest <= 2 and actor.current_mana < 2 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        old = actor.get_status("腐化")
        if old is not None:
            actor.remove_status(old, battle)
            battle.log_public_event(f"{actor.name} 关闭腐化。", source=actor)
        else:
            actor.add_status(CorruptionStatus(), source=actor)
            battle.log_public_event(f"{actor.name} 开启腐化。", source=actor)


class HuangquanAnyTimeMoveTrait(Trait):
    def __init__(self) -> None:
        super().__init__("任意时机移动", "本人进攻轮可把普通移动拆分并穿插在其他行动之间，总距离不变。")

    def allows_split_normal_movement(self, battle: Battle, actor: Unit) -> bool:
        return actor is self.owner

