"""R262 bird king feather fan and defense."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.common import ensure_distance
from wujiang.tactical.heroes.common import ensure_enemy
from wujiang.tactical.heroes.common import is_mana_drain_immune
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.mountain_god import LargeDrainManaSkill
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient
from wujiang.tactical.heroes.next_five import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.next_five import remote_rectangle_patterns


class GreatFeatherFanSealStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("大羽毛扇封技", "两个本人轮内不能使用主动技能。", duration=2,
                         tick_scope="owner_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and skill.timing == "active":
            return True, "大羽毛扇使该单位不能使用主动技能。"
        return False, ""


class GreatFeatherFanSkill(DeclaredAreaSkillMixin, Skill):
    def __init__(self) -> None:
        super().__init__("great_feather_fan", "大羽毛扇",
                         "2轮一次；远程4×4无伤破魔；命中后清除全部持续状态并封锁主动技能2轮。",
                         cooldown_turns=2, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 4, 4)

    def chosen_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") or match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload)):
            recipient = piercing_nondamage_recipient(battle, actor, target, action_name=self.name, code=self.code)
            if recipient is None:
                continue
            removed = list(recipient.statuses)
            for status in removed:
                recipient.remove_status(status, battle)
            recipient.add_status(GreatFeatherFanSealStatus(), source=actor)
            battle.record_skill_effect(recipient)
            battle.log_public_event(f"{recipient.name} 的{len(removed)}项持续状态结束，主动技能封锁2轮。",
                                    source=actor, target=recipient)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True


class PoisonFeatherSkill(LargeDrainManaSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "poison_feather"
        self.name = "剧毒羽毛"
        self.description = "每本人轮一次；破魔吸魔（大），使用后自身守-1到最低1。"

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        if not battle.is_declared_resolution(actor, payload, {"skill"}):
            ensure_distance(actor, target, actor.targeting_range() + 1)
        recipient = piercing_nondamage_recipient(battle, actor, target, action_name=self.name, code=self.code)
        if recipient is not None and not is_mana_drain_immune(recipient):
            lost = recipient.drain_mana(1)
            actor.gain_mana(lost)
            if lost:
                battle.record_skill_effect(recipient)
            battle.log_public_event(f"{actor.name} 以剧毒羽毛从{recipient.name}吸取{lost:g}魔。",
                                    source=actor, target=recipient)
        elif recipient is not None:
            battle.log_public_event(f"{recipient.name} 无法被吸魔。", source=actor, target=recipient)
        if actor.stat("defense") > 1:
            actor.base_stats.defense = max(1, actor.base_stats.defense - 1)
        battle.log_public_event(f"{actor.name} 使用剧毒羽毛后守降至{actor.stat('defense'):g}。", source=actor)

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and unit.position is not None and not unit.banished
                   and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range() + 1)
                   and battle.unit_can_be_selected(unit, actor=actor)[0]]
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True}


class BirdKingDefenseTrait(Trait):
    def __init__(self) -> None:
        super().__init__("鸟人王结阵", "本人回合末周围每名己方鸟人使守+1，最多至7。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.position is None:
            return
        birds = [unit for unit in battle.player_units(owner.player_id)
                 if unit is not owner and unit.alive and unit.position is not None and not unit.banished
                 and "鸟人" in unit.name and battle.distance_between_units(owner, unit) == 1]
        gain = min(len(birds), max(0, int(7 - owner.stat("defense"))))
        if gain:
            owner.base_stats.defense += gain
            battle.log_public_event(f"{owner.name} 与{len(birds)}名鸟人结阵，守+{gain}至{owner.stat('defense'):g}。", source=owner)


