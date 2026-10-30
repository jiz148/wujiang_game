"""R219 bird soul swap skill."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.gladiator_core import gladiator_swap
from wujiang.tactical.heroes.mechanics.gladiator_core import gladiator_swap_destinations


class BirdSoulSkill(Skill):
    requires_direct_unit_target_line = False

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.bird_skill_score()

    def __init__(self, *, free: bool) -> None:
        self.free = free
        super().__init__("bird_soul_free" if free else "bird_soul",
                         "鸟人之魂（免费）" if free else "鸟人之魂（付费）",
                         "与场上一单位合法换位；免费档破魔，每2本人轮一次。" if free else "1魔；与场上一单位合法换位。",
                         mana_cost=0 if free else 1, cooldown_turns=2 if free else 0,
                         target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit.unit_id != actor.unit_id and unit.alive and not unit.banished
                and unit.position is not None and not getattr(unit, "attached_to_unit_id", None)
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and gladiator_swap_destinations(battle, actor, unit) is not None]

    def direct_unit_target_range(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any] | None = None) -> int:
        return max(battle.width, battle.height)

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if payload and payload.get("target_unit_id"):
            return ((True, "") if str(payload["target_unit_id"]) in {unit.unit_id for unit in targets}
                    else (False, "请选择能完整合法换位的其他单位。"))
        return (True, "") if targets else (False, "没有可合法换位的单位。")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return self.free

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target.unit_id not in {unit.unit_id for unit in self.targets(battle, actor)}:
            battle.log_public_event(f"{actor.name} 的鸟人之魂已无合法换位对象。", source=actor)
            return
        hostile = target.player_id != actor.player_id
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=hostile, ignore_shield=self.free,
                                        resolve_defenses=not self.free,
                                        record_effect_on_validation=False)
        if result.cancelled or target.skill_non_damage_effects_blocked():
            battle.log_public_event(result.reason or f"{target.name} 未受鸟人之魂影响。", source=actor, target=target)
            return
        if self.free and hostile and target.total_shields() > 0:
            target.consume_one_shield()
            battle.log_public_event(f"{target.name} 的1层护盾被免费鸟人之魂贯穿。", source=actor, target=target)
        if self.free and hostile and target.dodge_charges > 0:
            target.dodge_charges -= 1
            battle.log_public_event(f"{target.name} 回避了免费鸟人之魂。", source=actor, target=target)
            return
        if self.free and battle.destroy_clone_for_skill_effect(target, source=actor, action_name=self.name):
            return
        if gladiator_swap(battle, actor, target, skill_code=self.code, via_skill=True):
            battle.record_skill_effect(actor)
            battle.record_skill_effect(target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        landings: list[Position] = []
        for unit in targets:
            pair = gladiator_swap_destinations(battle, actor, unit)
            if pair is not None:
                landings.extend(battle.unit_cells_at(actor, pair[0]))
                landings.extend(battle.unit_cells_at(unit, pair[1]))
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": positions_to_dict(landings),
                "requires_target": True}


