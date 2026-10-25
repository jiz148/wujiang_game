"""Black cat form and fantasy movement objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.heroes.common import is_mana_drain_immune
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.next_five import damage_followup_effect_applies
from wujiang.tactical.heroes.next_five import square_around_cells


class BlackCatPawSkill(Skill):
    def __init__(self) -> None:
        super().__init__("black_cat_paw", "猫手", "普通技能：每回合最多 1 次；攻击周围单位，并附带破魔的吸魔效果。", max_uses_per_turn=1, target_mode="self")

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        body = set(battle.unit_cells(actor))
        return [cell for cell in square_around_cells(battle, body, radius=1) if cell not in body]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True  # The mana drain pierces; damage remains ordinary.

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.affected_cells(battle, actor)
        for target in battle.units_at_cells(cells):
            if target.unit_id == actor.unit_id:
                continue
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="猫手",
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "black_cat_paw"},
                )
            )
            if damage_followup_effect_applies(ctx, allow_on_shield_break=True) and not is_mana_drain_immune(target):
                lost = target.drain_mana(1)
                gained = actor.gain_mana(lost)
                battle.log(f"{actor.name} 的【猫手】吸取 {target.name} {lost} 点魔，回复 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor)
        cell_keys = {(cell.x, cell.y) for cell in cells}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and (unit.player_id == actor.player_id or not unit.is_stealthed())
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": targets, "secondary_cells": [], "requires_target": False}


    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.affected_cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.affected_cells(battle, actor))  # type: ignore[return-value]


class BlackCatFormStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("化猫", "攻1守1速4范1，魔免；攻击后重置移动次数。", duration=None)
        self.prior_magic_immunity = False

    def bind(self, owner: HeroUnit) -> "BlackCatFormStatus":
        super().bind(owner)
        self.prior_magic_immunity = owner.magic_immunity
        owner.magic_immunity = True
        return self

    def modify_stat(self, stat_name: str, value: float) -> float:
        fixed = {"attack": 1, "defense": 1, "speed": 4, "attack_range": 1}
        if stat_name in fixed:
            return float(fixed[stat_name])
        return value

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id:
            return
        owner.move_used = False
        owner.moved_this_turn = False
        owner.normal_move_steps_used = 0
        owner.normal_move_actions_used = 0
        battle.log(f"{owner.name} 的【化猫】重置了移动次数。")

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.magic_immunity = self.prior_magic_immunity or any(status.name == "魔免" for status in owner.statuses)


class BlackCatFormSkill(Skill):
    def __init__(self) -> None:
        super().__init__("black_cat_form", "化猫", "开关技能：每回合最多 1 次，仅可在回合开始时使用；开启/关闭化猫形态。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.moved_this_turn or actor.attacks_used:
            return False, "化猫只能在回合开始时使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("化猫")
        if existing is not None:
            actor.remove_status(existing, battle)
            battle.log(f"{actor.name} 关闭【化猫】。")
        else:
            actor.add_status(BlackCatFormStatus())
            battle.log(f"{actor.name} 开启【化猫】。")


class FantasyMoveSkill(Skill):
    def __init__(self) -> None:
        super().__init__("fantasy_move", "幻想", "普通技能：每回合最多 1 次，破魔且无法被回避；按当前攻击造成双方技能伤害，并强制实际受体移动 4 格。", max_uses_per_turn=1, target_mode="unit")

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        destination = payload_position(payload)
        if destination is None or not self.destination_is_legal(battle, target, destination):
            raise ActionError("幻想需要目标可合法移动恰好4步的落点。")
        return {"fantasy_landing": destination.to_dict()}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(payload_target_unit(battle, payload))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        target = battle.effect_recipient(target)
        destination = Position(**payload["fantasy_landing"]) if payload.get("queued_resolution") and "fantasy_landing" in payload else payload_position(payload)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name="幻想",
                ignore_shield=True,
                cannot_evade=True,
                tags={"skill", "fantasy_move"},
            )
        )
        if damage_followup_effect_applies(ctx) and target.alive and not target.banished and target.position is not None:
            effect = battle.validate_target(actor, target, action_name="幻想位移", is_skill=True,
                                            is_hostile=actor.is_enemy_of(target), ignore_shield=True, cannot_evade=True,
                                            ignore_targeting_restrictions=True, resolve_defenses=ctx.cancelled and not ctx.shield_consumed)
            if effect.cancelled:
                return
            try:
                battle.move_unit(target, destination, via_skill=True, forced=True, max_distance=4, exact_distance=4, tags={"fantasy_move"})
            except ActionError:
                battle.log(f"{target.name} 受到幻想伤害，但无法到达声明落点。")

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def cannot_evade_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def destination_is_legal(self, battle: Battle, target: HeroUnit, destination: Position) -> bool:
        if battle.is_forced_movement_blocked(destination):
            return False
        if not battle.can_place_unit(target, destination, ignore=target, mover=target):
            return False
        try:
            battle.find_path(target, destination, max_distance=4, exact_distance=4)
        except ActionError:
            return False
        return True

    def legal_destinations(self, battle: Battle, target: HeroUnit) -> list[Position]:
        result: list[Position] = []
        for x in range(battle.width):
            for y in range(battle.height):
                destination = Position(x, y)
                if self.destination_is_legal(battle, target, destination):
                    result.append(destination)
        return result

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        destinations_by_target = {
            target.unit_id: positions_to_dict(self.legal_destinations(battle, battle.effect_recipient(target)))
            for target in targets
        }
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets if destinations_by_target[target.unit_id]],
            "secondary_cells": [],
            "requires_target": True,
            "destinations_by_target": destinations_by_target,
        }


