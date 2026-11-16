"""R351: a wide berserker whose destruction can turn it over to the enemy."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, HealContext, Position, Skill, Trait, Unit, is_army_soldier
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


def _true_hero(unit: Unit) -> bool:
    return not unit.is_summon and not unit.is_clone and not is_army_soldier(unit)


def _ring(battle: Battle, unit: Unit) -> list[Position]:
    body = set(battle.unit_cells(unit))
    return sorted({cell for origin in body for cell in battle.neighbors(origin) if cell not in body},
                  key=lambda cell: (cell.y, cell.x))


class BeastCircleAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击一周", "普攻命中完整2×2身体外邻一周的双方实际受体，各结算一次。")

    def basic_attack_area_cells(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any] | None = None) -> list[Position] | None:
        if (payload or {}).get("reaction_attack") or not _present(actor):
            return None
        return _ring(battle, actor)

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        cells = self.basic_attack_area_cells(battle, actor, payload)
        return ({"attack_cells": positions_to_dict(cells), "area_attack": True,
                 "single_hit_per_recipient": True} if cells else {})

    def basic_attack_preview(self, battle: Battle, actor: Unit,
                             payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        cells = self.basic_attack_area_cells(battle, actor, payload)
        if cells is None:
            return None
        return {"cells": positions_to_dict(cells), "secondary_cells": [],
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells)
                                    if unit.unit_id != actor.unit_id], "requires_target": True}


class BeastAttackCountTrait(Trait):
    def __init__(self) -> None:
        super().__init__("敌将激战", "每本人回合普攻次数在基础一次上增加当前敌方在场真武将数。")

    def modify_attack_actions_per_turn(self, value: int) -> int:
        owner = self.owner
        ref = getattr(owner, "_battle_ref", None) if owner is not None else None
        battle = ref() if ref is not None else None
        if battle is None or owner is None:
            return value
        return value + sum(_present(unit) and _true_hero(unit) and unit.player_id != owner.player_id
                           for unit in battle.all_units())


class HeatConductionSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("heat_conduction", "热导", "本人轮一次；选择己方狂战武将，回复1/4生命或1魔。",
                         max_uses_per_turn=1, target_mode="cell")

    def candidates(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if _present(unit) and _true_hero(unit)
                and unit.player_id == actor.player_id and unit.role == "狂战"
                and (unit is actor or battle.unit_target_in_range_and_line(
                    actor, unit, actor.targeting_range()))]

    def _choice(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[Unit, str]:
        mode = str(payload.get("choice_code") or "")
        if mode not in {"heal", "mana"}:
            raise ActionError("热导需要选择回血或回魔。")
        cells = battle.payload_positions(payload, "cells")
        if len(cells) != 1:
            raise ActionError("热导需要选择一名己方狂战武将。")
        candidates = [unit for unit in self.candidates(battle, actor) if cells[0] in battle.unit_cells(unit)]
        if len(candidates) != 1:
            raise ActionError("热导目标必须是己方狂战武将。")
        return candidates[0], mode

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload:
            try:
                self._choice(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        units = self.candidates(battle, actor)
        patterns = [[cell.to_dict()] for unit in units for cell in battle.unit_cells(unit)]
        return {"cells": [cell for pattern in patterns for cell in pattern],
                "target_unit_ids": [unit.unit_id for unit in units], "secondary_cells": [],
                "requires_target": True,
                "selection": {"mode": "choice_pattern", "ordered": False,
                              "choices": [{"code": "heal", "label": "血+1/4", "patterns": patterns},
                                          {"code": "mana", "label": "魔+1", "patterns": patterns}],
                              "choice_prompt": "热导：选择资源和己方狂战武将。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        target, _ = self._choice(battle, actor, payload)
        return battle.unit_cells(target)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        target, _ = self._choice(battle, actor, payload)
        return [target]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target, mode = self._choice(battle, actor, payload)
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=False, record_effect_on_validation=False)
        if result.cancelled or result.target.skill_non_damage_effects_blocked():
            return
        target = result.target
        if mode == "heal":
            healed = battle.heal(HealContext(source=actor, target=target, amount=0.25,
                                             action_name=self.name, tags={"skill", self.code},
                                             effect_source=self))
            change = healed.actual_heal
        else:
            change = target.gain_mana(1)
        battle.log_public_event(f"{actor.name} 用热导给 {target.name} {'回血' if mode == 'heal' else '回魔'} {change:g}。",
                                source=actor, target=target)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            target, mode = self._choice(battle, actor, payload)
        except ActionError:
            return -100.0
        if target.skill_non_damage_effects_blocked():
            return -100.0
        if mode == "heal":
            return max(0.0, min(0.25, target.max_health - target.current_hp)) * 110.0 - 2.0
        return max(0.0, min(1.0, target.max_mana() - target.current_mana)) * 18.0 - 2.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100.0


class BeastSelfDestructSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("beast_self_destruct", "自爆", "每场一次大招；真正破坏自身并触发死亡热爆。",
                         max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        battle.log_public_event(f"{actor.name} 自爆。", source=actor)
        actor.current_hp = 0
        actor.alive = False
        battle.cleanup_dead_units()

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        if actor.position is None:
            return -1000.0
        if not any(_present(unit) and _true_hero(unit) and unit is not actor
                   and unit.player_id == actor.player_id for unit in battle.all_units()):
            return -1000.0
        cells = [Position(actor.position.x + dx, actor.position.y + dy)
                 for dx in range(-5, 6) for dy in range(-5, 6)
                 if battle.in_bounds(Position(actor.position.x + dx, actor.position.y + dy))]
        score = sum((1 if unit.player_id != actor.player_id else -1.4)
                    * min(unit.current_hp, battle.damage_rule.calculate_damage(4, unit.stat("defense"))) * 100
                    for unit in battle.effect_units_at_cells(cells)
                    if unit is not actor and not unit.total_shields()
                    and not getattr(unit, "physical_immunity", False))
        return score - (actor.current_hp * 150 + 45)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0


class BeastDevourSkill(Skill):
    """The dead-state skill is offered by the death trait, never as a live action."""

    def __init__(self) -> None:
        super().__init__("beast_devour", "吞噬", "真死后选敌方单位、2×2合法落格和方向；投敌复出并作5破魔方向伤。",
                         max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        return False, "吞噬仅在真正破坏后的特殊窗口使用。"

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("请在真正破坏后的吞噬选择窗口使用。")


def _landing_cells(battle: Battle, owner: Unit, target: Unit) -> list[Position]:
    if not _present(target):
        return []
    old_player = owner.player_id
    owner.player_id = target.player_id
    try:
        if not battle.unit_entry_allowed(owner)[0]:
            return []
        neighbors = {cell for origin in battle.unit_cells(target) for cell in battle.neighbors(origin)}
        result: list[Position] = []
        for x in range(battle.width):
            for y in range(battle.height):
                anchor = Position(x, y)
                if not battle.can_place_unit(owner, anchor, ignore=owner, mover=owner):
                    continue
                if any(cell in neighbors for cell in owner.footprint_cells_at(anchor)):
                    result.append(anchor)
        return result
    finally:
        owner.player_id = old_player


def _blast_cells(battle: Battle, owner: Unit) -> list[Position]:
    origin = owner.last_position
    if origin is None:
        return []
    return [cell for dx in range(-5, 6) for dy in range(-5, 6)
            if battle.in_bounds(cell := Position(origin.x + dx, origin.y + dy))]


def _devour_line(battle: Battle, actor: Unit, anchor: Position,
                 direction: tuple[int, int]) -> list[Position]:
    body = set(actor.footprint_cells_at(anchor))
    dx, dy = direction
    line: set[Position] = set()
    for origin in body:
        cell = origin.offset(dx, dy)
        while battle.in_bounds(cell) and cell not in body:
            line.add(cell)
            cell = cell.offset(dx, dy)
    return sorted(line, key=lambda cell: (cell.y, cell.x))


def _devour_direction_value(battle: Battle, actor: Unit, anchor: Position,
                            direction: tuple[int, int]) -> float:
    new_player = 2 if actor.player_id == 1 else 1
    victims = battle.effect_units_at_cells(_devour_line(battle, actor, anchor, direction))
    return sum((1.0 if unit.player_id == new_player else -1.3)
               * min(unit.current_hp, battle.damage_rule.calculate_damage(5, unit.stat("defense"))) * 100
               for unit in victims if unit is not actor and not unit.magic_immunity)


def resolve_beast_death_burst(battle: Battle, actor: Unit, queued: Any) -> None:
    for recipient in battle.effect_units_at_cells(battle.payload_positions(queued.payload, "cells")):
        battle.resolve_damage(DamageContext(source=actor, target=recipient, attack_power=4,
            is_skill=False, action_name="热源死亡爆发",
            tags={"death_trait", "heat_source_burst"}))


def resolve_beast_devour(battle: Battle, actor: Unit, queued: Any) -> None:
    if actor.alive or actor not in battle.destroyed_units:
        return
    target = battle.units.get(str(queued.payload.get("target_unit_id") or ""))
    cells = battle.payload_positions(queued.payload, "cells")
    if not _present(target) or target.player_id == actor.player_id or not cells or cells[0] not in _landing_cells(battle, actor, target):
        battle.log_public_event(f"{actor.name} 的吞噬目标或落格已失效。", source=actor)
        return
    old_player = actor.player_id
    old_controller = getattr(actor, "controller_hero_id", None)
    old_seat = getattr(actor, "owner_seat_id", None)
    old_slot = getattr(actor, "preserved_turn_slot_id", None)
    battle.transfer_unit_control(actor, target)
    if not battle.unit_entry_allowed(actor)[0] or not battle.can_place_unit(actor, cells[0], mover=actor):
        actor.player_id = old_player
        actor.controller_hero_id = old_controller
        actor.owner_seat_id = old_seat
        actor.preserved_turn_slot_id = old_slot
        battle.log_public_event(f"{actor.name} 的吞噬回场受阻。", source=actor)
        return
    for status in list(actor.statuses):
        actor.remove_status(status, battle)
    actor.alive = True
    actor.banished = False
    actor.current_hp = actor.max_health
    actor.current_mana = actor.max_mana()
    actor.turn_ready = False
    actor.can_act_on_entry_turn = False
    actor.move_used = False
    actor.attacks_used = 0
    actor.normal_move_actions_used = 0
    actor.normal_move_steps_used = 0
    actor.performed_active_skill = False
    actor.actions_taken_this_turn = []
    actor.clear_end_of_turn_shields()
    battle.add_unit(actor, cells[0])
    battle.destroyed_units = [unit for unit in battle.destroyed_units if unit.unit_id != actor.unit_id]
    battle.notify_destroyed_hero_count_changed()
    battle.log_public_event(f"{actor.name} 通过吞噬从玩家 {old_player} 转为玩家 {actor.player_id} 的武将。",
                            source=actor, target=target)
    direction = queued.payload.get("direction") or []
    if len(direction) != 2:
        return
    dx, dy = int(direction[0]), int(direction[1])
    line = _devour_line(battle, actor, cells[0], (dx, dy))
    if line:
        battle.queue_skill_effect_action(actor=actor, display_name="吞噬破魔喷发", effect_code="beast_devour_blast",
            payload={"cells": positions_to_dict(line),
                     "attack_power": 5, "ignore_shield": True, "refresh_cell_targets": True},
            target_cells=line,
            effect_resolver=resolve_beast_devour_blast)


def resolve_beast_devour_blast(battle: Battle, actor: Unit, queued: Any) -> None:
    for recipient in battle.effect_units_at_cells(battle.payload_positions(queued.payload, "cells")):
        if recipient is actor:
            continue
        battle.resolve_damage(DamageContext(source=actor, target=recipient, attack_power=5,
            is_skill=True, ignore_shield=True, action_name="吞噬破魔喷发",
            tags={"skill", "beast_devour", "piercing"}))


class BeastDeathTrait(Trait):
    requires_damage_choice = True
    ai_optional_choice_owned = True

    def __init__(self) -> None:
        super().__init__("热源死爆", "真正破坏后11×11威力4爆发；可选择投敌复出并作方向破魔伤。")
        self.last_devour_turn = -1

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        if owner is None or destroyed_unit is not owner or owner.last_position is None:
            return
        cells = _blast_cells(battle, owner)
        if cells:
            battle.queue_skill_effect_action(actor=owner, display_name="热源死亡爆发",
                effect_code="beast_death_burst",
                payload={"after_death_effect": True, "cells": positions_to_dict(cells),
                         "attack_power": 4, "refresh_cell_targets": True},
                target_cells=cells, effect_resolver=resolve_beast_death_burst)
        if self.last_devour_turn == battle.turn_number:
            return
        targets = [unit for unit in battle.all_units() if _present(unit)
                   and unit.player_id != owner.player_id and _landing_cells(battle, owner, unit)]
        if not targets:
            return
        selected = battle.optional_position_choice(owner, [unit.unit_id for unit in targets],
            action_name="吞噬：选择敌方单位（可放弃）", kind="optional_unit_selection",
            choice_context={"phase": "target"})
        target = next((unit for unit in targets if unit.unit_id == selected), None)
        if target is None:
            return
        landings = _landing_cells(battle, owner, target)
        options = [f"{cell.x},{cell.y}" for cell in landings]
        chosen = battle.optional_position_choice(owner, options, action_name=f"吞噬：选择{target.name}周围落格",
            kind="required_placement", choice_context={"phase": "landing", "target_unit_id": target.unit_id})
        if chosen not in options:
            return
        x, y = (int(part) for part in chosen.split(",", 1))
        directions = [f"{dx},{dy}" for dx, dy in ALL_DIRECTIONS]
        direction = battle.optional_position_choice(owner, directions,
            action_name="吞噬：选择破魔喷发方向", kind="required_direction",
            choice_context={"phase": "direction", "target_unit_id": target.unit_id,
                            "x": x, "y": y})
        if direction not in directions:
            return
        dx, dy = (int(part) for part in direction.split(",", 1))
        self.last_devour_turn = battle.turn_number
        battle.queue_skill_effect_action(actor=owner, display_name="吞噬复出",
            effect_code="beast_devour_rebirth",
            payload={"after_death_effect": True, "target_unit_id": target.unit_id,
                     "cells": [{"x": x, "y": y}], "direction": [dx, dy]},
            target_cells=[], include_cell_units=False, hostile=False,
            effect_resolver=resolve_beast_devour)

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        name = str(prompt.get("action_name") or "")
        if not name.startswith("吞噬"):
            return None
        kind = str(prompt.get("kind") or "")
        if kind == "optional_unit_selection":
            target = battle.units.get(choice)
            if target is None:
                return -1000.0
            return max((_devour_direction_value(battle, actor, cell, direction)
                        for cell in _landing_cells(battle, actor, target)
                        for direction in ALL_DIRECTIONS), default=-100.0) - 150.0
        if kind == "required_placement":
            try:
                x, y = (int(part) for part in choice.split(",", 1))
            except ValueError:
                return -1000.0
            return max((_devour_direction_value(battle, actor, Position(x, y), direction)
                        for direction in ALL_DIRECTIONS), default=-100.0)
        if kind == "required_direction":
            try:
                dx, dy = (int(part) for part in choice.split(",", 1))
                context = prompt.get("choice_context") or {}
                anchor = Position(int(context["x"]), int(context["y"]))
            except (KeyError, TypeError, ValueError):
                return -1000.0
            return _devour_direction_value(battle, actor, anchor, (dx, dy))
        return None
