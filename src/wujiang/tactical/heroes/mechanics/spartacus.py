"""R196 Spartacus placement and shatter mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import MoveContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.gladiator_core import gladiator_swap
from wujiang.tactical.heroes.mechanics.gladiator_core import gladiator_swap_destinations
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class SpartacusShatterStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("天崩地裂强化", attack_delta=1, defense_delta=1,
                         duration=1, tick_scope="owner_turn_end")
        self.is_skill_effect = True


class SpartacusDoubleAttackStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("斯巴达克斯双攻", "触发所在轮和下个本人轮可普攻2次。",
                         duration=2, tick_scope="owner_turn_end")
        self.is_skill_effect = False

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return max(value, 2)


def spartacus_ally_placement_options(battle: Battle, actor: Unit) -> list[str]:
    if actor.position is None:
        return []
    body = set(battle.unit_cells(actor))
    ring = sorted(set(square_around_cells(battle, body, radius=1)) - body,
                  key=lambda cell: (cell.y, cell.x))
    options: list[str] = []
    for ally in battle.player_units(actor.player_id):
        if (ally is actor or not ally.alive or ally.banished or ally.position is None
                or getattr(ally, "attached_to_unit_id", None) or getattr(ally, "standable_terrain", False)
                or ally.direct_effects_blocked() or ally.skill_non_damage_effects_blocked()):
            continue
        for cell in ring:
            if cell == ally.position or not battle.can_place_unit(ally, cell, ignore=ally, mover=ally):
                continue
            rider = battle.rider_for(ally)
            if rider is not None and rider.position is not None:
                rider_end = rider.position.offset(cell.x - ally.position.x, cell.y - ally.position.y)
                if not battle.can_place_unit(rider, rider_end, ignore=rider, mover=rider):
                    continue
            options.append(f"{ally.unit_id}@{cell.x},{cell.y}")
    return options


def resolve_spartacus_self_influence(battle: Battle, actor: Unit, queued_action: Any) -> None:
    if actor.alive and not actor.banished and actor.position is not None:
        battle.record_skill_effect(actor)


def resolve_spartacus_placement(battle: Battle, actor: Unit, queued_action: Any) -> None:
    if not actor.alive or actor.banished or actor.position is None:
        return
    options = spartacus_ally_placement_options(battle, actor)
    choice = battle.optional_position_choice(actor, options, action_name="天崩地裂友军搬运",
                                             kind="optional_placement")
    if choice not in options:
        return
    unit_id, position = choice.rsplit("@", 1)
    x, y = (int(value) for value in position.split(",", 1))
    ally = battle.units.get(unit_id)
    if ally is None:
        return
    try:
        battle.move_unit(ally, Position(x, y), via_skill=True, forced=True,
                         allow_anywhere=True, tags={"spartacus_placement", f"source:{actor.unit_id}"})
        battle.log_public_event(f"{actor.name} 以天崩地裂将{ally.name}搬到 ({x},{y})。",
                                source=actor, target=ally)
    except ActionError:
        battle.log_public_event(f"{actor.name} 的天崩地裂搬运落点已不可用。", source=actor)


class EarthShatterSkill(Skill):
    excludes_caster_from_effect = True
    target_cells_are_geometry_only = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        if not context.effective_enemy_impact():
            return -1000.0
        value = context.paid_skill_value()
        return value - 28.0 if value > 28.0 else -1000.0

    def __init__(self) -> None:
        super().__init__("earth_shatter", "天崩地裂",
                         "0魔大招、每场一次；先攻守+1至本人轮末，再对身体外7×7双方单位造成当前攻技能伤害，最后可搬一友军到周围。",
                         max_uses_per_battle=1, target_mode="self")

    def affected_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and "shatter_cells" in payload:
            return [Position(**cell) for cell in payload["shatter_cells"]]
        body = set(battle.unit_cells(actor))
        return sorted(set(square_around_cells(battle, body, radius=3)) - body,
                      key=lambda cell: (cell.y, cell.x))

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"shatter_cells": positions_to_dict(self.affected_cells(battle, actor, payload))}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.affected_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.affected_cells(battle, actor, payload), ignore=actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        if actor.get_status("天崩地裂强化") is None:
            actor.add_status(SpartacusShatterStatus(), source=actor)
        else:
            actor.get_status("天崩地裂强化").duration = 1
        cells = self.affected_cells(battle, actor, payload)
        battle.log_public_event(f"{actor.name} 的天崩地裂攻守+1，锁定身体外7×7区域。", source=actor)
        battle.queue_area_damage_effect(actor=actor, display_name=self.name, cells=cells,
                                        attack_power=actor.stat("attack"), tags={"skill", self.code},
                                        exclude_actor=True)
        battle.queue_skill_effect_action(actor=actor, display_name="天崩地裂强化后效",
                                         effect_code="spartacus_self_influence", target_units=[actor],
                                         effect_resolver=resolve_spartacus_self_influence, hostile=False)
        battle.queue_skill_effect_action(actor=actor, display_name="天崩地裂友军搬运",
                                         effect_code="spartacus_placement", target_units=[actor],
                                         effect_resolver=resolve_spartacus_placement, hostile=False)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor, {})
        return {"cells": positions_to_dict(cells), "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [unit.unit_id for unit in self.get_target_units_for_payload(battle, actor, {})
                                    if battle.unit_can_be_selected(unit, actor=actor)[0]],
                "requires_target": False, "possible_ally_placement": True}


class SpartacusSkillSwapTrait(Trait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("斯巴达克斯技能换位", "本人实际受到技能影响后，可与其他己方单位合法换位。")

    def on_skill_effect_finished(self, battle: Battle, queued_action: Any) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.banished or owner.position is None:
            return
        options = [unit.unit_id for unit in battle.player_units(owner.player_id)
                   if unit.unit_id != owner.unit_id and unit.alive and not unit.banished
                   and gladiator_swap_destinations(battle, owner, unit, via_skill=False) is not None]
        choice = battle.optional_position_choice(owner, options, action_name="斯巴达克斯技能换位",
                                                 kind="optional_swap")
        ally = battle.units.get(choice or "")
        if ally is not None and choice in options:
            gladiator_swap(battle, owner, ally, skill_code="spartacus_skill_swap", via_skill=False)


class SpartacusMovedByAllyTrait(Trait):
    def __init__(self) -> None:
        super().__init__("斯巴达克斯协同", "其他友方效果真正搬运本人后，双攻两轮、重置大招且魔能力值+1。")

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or ctx.start == ctx.end:
            return
        source_id = next((tag.removeprefix("source:") for tag in ctx.tags if tag.startswith("source:")), None)
        if source_id is None and battle.resolving_action is not None:
            source_id = battle.resolving_action.actor_id
        source = battle.units.get(source_id or "")
        if source is None or source.unit_id == owner.unit_id or source.player_id != owner.player_id:
            return
        status = owner.get_status("斯巴达克斯双攻")
        if status is None:
            owner.add_status(SpartacusDoubleAttackStatus(), source=source)
        else:
            status.duration = 2
        owner.base_stats.mana += 1
        owner.current_mana = round(min(owner.max_mana(), owner.current_mana + 1), 2)
        for skill in owner.skills:
            if skill.max_uses_per_battle is not None and skill.uses_this_battle > 0:
                skill.uses_this_battle = 0
        battle.log_public_event(f"{owner.name} 受友军搬运：双攻、魔能力值+1并重置已用大招。",
                                source=source, target=owner)


