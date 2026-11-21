"""R371 Earth Spirit: placed rocks, physical routes, and magnetic control."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, QueuedAction, Skill, StatusEffect, Unit
from wujiang.tactical.heroes.common import payload_direction, payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.spirit_remnants import SpiritPointTrait, SpiritRemnant, remnant_at, remnants_for, square_cells, visible_units_at


ROCK = "残岩"
CARDINALS = ((0, -1), (1, 0), (0, 1), (-1, 0))


def _dir(origin: Position, target: Position) -> tuple[int, int]:
    dx, dy = target.x - origin.x, target.y - origin.y
    return ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0))


def _step(cell: Position, direction: tuple[int, int]) -> Position:
    return cell.offset(*direction)


def _rock_at(battle: Battle, cell: Position) -> SpiritRemnant | None:
    return next((effect for effect in battle.field_effects
                 if isinstance(effect, SpiritRemnant) and effect.name == ROCK and effect.cell == cell), None)


def _occupants(battle: Battle, cell: Position, actor: Unit) -> list[Unit]:
    return visible_units_at(battle, [cell], ignore=actor)


class EarthPointTrait(SpiritPointTrait):
    def __init__(self) -> None:
        # The source lists no spender. The six points are public state, not a hidden cost.
        super().__init__("大地计数点", 6, gain_on_owner_only=False)


class RockSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("spirit_rock", ROCK, "在合法指定格放置不阻挡移动的残岩。", target_mode="cell")

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        return [cell for y in range(battle.height) for x in range(battle.width)
                if (cell := Position(x, y)) != actor.position
                and battle.can_place_unit(actor, cell, ignore=actor, mover=actor)
                and _rock_at(battle, cell) is None]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.cells(battle, actor)
        if not cells or payload and payload.get("x") is not None and payload_position(payload) not in cells:
            return False, "没有合法的残岩放置格。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        battle.add_field_effect(SpiritRemnant(ROCK, actor.unit_id, cell), source=actor)
        battle.log_public_event(f"{actor.name} 在({cell.x},{cell.y})放置残岩。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.cells(battle, actor)), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        # Prioritize rocks that a roll or a pull can actually use.
        useful = [cell for cell in self.cells(battle, actor)
                  if actor.position is not None and (cell.x == actor.position.x or cell.y == actor.position.y)
                  and cell.distance_to(actor.position) <= 5]
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in useful[:20]]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        cell = payload_position(payload)
        nearby = sum(unit.player_id != actor.player_id for unit in visible_units_at(
            battle, square_cells(battle, cell, 2)))
        return 15.0 + nearby * 20.0 if actor.position is not None and nearby else -30.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class EarthRollSkill(Skill):
    is_displacement_skill = True
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    ai_complete_direction_payloads = True
    ai_optional_choice_owned = True

    def __init__(self) -> None:
        super().__init__("spirit_roll", "巨石翻滚", "每本人轮一次；直行3，触岩销毁后再直行5；撞敌选其周围落格。",
                         max_uses_per_turn=1, target_mode="cell", direction_mode="required")

    def route(self, battle: Battle, actor: Unit, direction: tuple[int, int]) -> tuple[Position, SpiritRemnant | None, Unit | None]:
        if actor.position is None or direction not in CARDINALS:
            raise ActionError("请选择正交直线方向。")
        current, rock, enemy, limit = actor.position, None, None, 3
        for index in range(8):
            if index >= limit:
                break
            cell = _step(current, direction)
            if not battle.in_bounds(cell):
                break
            occupants = _occupants(battle, cell, actor)
            if occupants:
                enemy = next((unit for unit in occupants if unit.player_id != actor.player_id), None)
                break
            if not battle.can_place_unit(actor, cell, ignore=actor, mover=actor):
                break
            current = cell
            found = _rock_at(battle, cell)
            if found is not None and rock is None:
                rock, limit = found, min(8, index + 1 + 5)
        return current, rock, enemy

    def directions(self, battle: Battle, actor: Unit) -> list[tuple[int, int]]:
        return [direction for direction in CARDINALS
                if (end := self.route(battle, actor, direction))[0] != actor.position or end[2] is not None]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        directions = self.directions(battle, actor)
        if not directions:
            return False, "没有可翻滚路线。"
        if payload and ("direction" in payload or "dx" in payload):
            if payload_direction(payload) not in directions:
                return False, "翻滚方向不可达。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        directions = self.directions(battle, actor) if actor.position is not None else []
        cells = [battle.line_positions(actor.position, direction, 8) for direction in directions]
        return {"cells": positions_to_dict(list(dict.fromkeys(cell for line in cells for cell in line))),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "direction_only", "directions": [
                    {"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"} for dx, dy in directions]}}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        _, _, enemy = self.route(battle, actor, payload_direction(payload))
        return [enemy] if enemy is not None else []

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        end, rock, enemy = self.route(battle, actor, payload_direction(payload))
        if rock is not None:
            battle.remove_field_effect(rock)
        if enemy is not None and enemy.position is not None:
            choices = [cell for cell in battle.neighbors(enemy.position)
                       if battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]
            if choices:
                options = [f"{cell.x},{cell.y}" for cell in choices]
                selected = battle.optional_position_choice(actor, options,
                    action_name="巨石翻滚撞敌落格", kind="required_placement",
                    choice_context={"earth_roll_target": enemy.unit_id})
                if selected in options:
                    x, y = (int(part) for part in selected.split(",", 1))
                    end = Position(x, y)
        if end != actor.position:
            battle.move_unit(actor, end, via_skill=True, allow_anywhere=True, max_distance=8,
                             tags={self.code})

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "direction": {"dx": dx, "dy": dy}} for dx, dy in self.directions(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        end, rock, enemy = self.route(battle, actor, payload_direction(payload))
        if actor.position is None:
            return -1000.0
        start_distance = min((battle.distance_between_units(actor, unit) for unit in battle.enemy_units(actor.player_id)
                              if unit.position is not None), default=99)
        end_distance = min((end.distance_to(unit.position) for unit in battle.enemy_units(actor.player_id)
                            if unit.position is not None), default=99)
        return 15.0 + (start_distance - end_distance) * 8.0 + (25.0 if enemy else 0.0) + (5.0 if rock else 0.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if not (prompt.get("choice_context") or {}).get("earth_roll_target"):
            return None
        x, y = (int(part) for part in choice.split(",", 1))
        cell = Position(x, y)
        return 35.0 - min((cell.distance_to(unit.position) for unit in battle.enemy_units(actor.player_id)
                           if unit.position is not None), default=8) * 4.0


class RockSlowStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("巨石冲击减速", "速-2，最低1，到下次本人轮末。", duration=1)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 2.0) if stat_name == "speed" else value


class MagneticLockStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("磁力之握封技", "下次本人轮末前不能用主动技能。", duration=1)

    def blocks_skill_use(self, battle: Battle, actor: Unit,
                         skill: Skill) -> tuple[bool, str]:
        return (True, "磁力之握封锁主动技能。") if skill.timing == "active" else (False, "")


class RockTargetSkill(Skill):
    """Cell selection shared by the two rock projectile skills, without central dispatch."""
    ai_payload_value_owned = True
    collision_status_cls: type[StatusEffect]

    def rocks(self, battle: Battle, actor: Unit) -> list[SpiritRemnant]:
        return [effect for effect in battle.field_effects
                if isinstance(effect, SpiritRemnant) and effect.name == ROCK]

    def selected(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[Position, SpiritRemnant | None, Unit | None]:
        cell = payload_position(payload)
        rock = _rock_at(battle, cell)
        units = _occupants(battle, cell, actor)
        return cell, rock, units[0] if units else None

    def _queue_collision(self, battle: Battle, actor: Unit, target: Unit) -> None:
        battle.queue_skill_effect_action(actor=actor, display_name=self.name,
            effect_code=f"{self.code}_collision", target_units=[target],
            target_cells=battle.unit_cells(target), include_cell_units=False,
            payload={"ignore_shield": True, "tags": ["skill", "piercing", self.code]},
            effect_resolver=self._resolve_collision)

    def _resolve_collision(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if target is None or not target.alive:
                continue
            battle.resolve_damage(DamageContext(source=actor, target=target,
                attack_power=actor.stat("attack"), is_skill=True,
                action_name=self.name, tags={"skill", self.code}))
            if target.alive:
                apply_piercing_status_effect(battle, actor, target, action_name=self.name,
                    status=self.collision_status_cls(), is_skill=True, tags={"skill", self.code},
                    ignore_targeting_restrictions=True)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class BoulderStrikeSkill(RockTargetSkill):
    collision_status_cls = RockSlowStatus

    def __init__(self) -> None:
        super().__init__("spirit_boulder_strike", "巨石冲击", "每本人轮一次；推单位3格，或射残岩5格撞敌并破魔减速。",
                         max_uses_per_turn=1, target_mode="cell")

    def targets(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        nearby = square_cells(battle, actor.position, 1)
        return [cell for cell in nearby if cell != actor.position and
                (_rock_at(battle, cell) is not None or _occupants(battle, cell, actor))]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if not targets or payload and payload.get("x") is not None and payload_position(payload) not in targets:
            return False, "请选择周围单位或残岩。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.targets(battle, actor)
        return {"cells": positions_to_dict(cells), "target_unit_ids": [unit.unit_id for cell in cells
                for unit in _occupants(battle, cell, actor)], "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        _, rock, unit = self.selected(battle, actor, payload)
        return [unit] if unit is not None and rock is None else []

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell, rock, target = self.selected(battle, actor, payload)
        if actor.position is None:
            return
        direction = _dir(actor.position, cell)
        if rock is None and target is not None and target.position is not None:
            battle.queue_skill_effect_action(actor=actor, display_name=self.name,
                effect_code="spirit_boulder_unit_push", target_units=[target],
                target_cells=battle.unit_cells(target), include_cell_units=False,
                payload={"direction": {"dx": direction[0], "dy": direction[1]}},
                effect_resolver=self._resolve_push)
            return
        if rock is None:
            return
        current = cell
        for _ in range(5):
            next_cell = _step(current, direction)
            if not battle.in_bounds(next_cell):
                break
            occupants = _occupants(battle, next_cell, actor)
            if occupants:
                for occupant in occupants:
                    self._queue_collision(battle, actor, occupant)
                break
            current = next_cell
        battle.remove_field_effect(rock)

    def _resolve_push(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        direction = payload_direction(queued.payload)
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if target is None or target.position is None or not target.alive:
                continue
            checked = battle.validate_target(actor, target, action_name=self.name,
                is_skill=True, is_hostile=target.player_id != actor.player_id,
                ignore_targeting_restrictions=True)
            if checked.cancelled:
                continue
            destination = target.position
            for _ in range(3):
                next_cell = _step(destination, direction)
                if not battle.in_bounds(next_cell) or not battle.can_place_unit(target, next_cell, mover=target):
                    break
                destination = next_cell
            if destination != target.position:
                battle.move_unit(target, destination, via_skill=True, forced=True,
                                 allow_anywhere=True, max_distance=3, tags={self.code})

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.targets(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        cell, rock, target = self.selected(battle, actor, payload)
        if rock is None:
            return 20.0 if target is not None and target.player_id != actor.player_id else -60.0
        direction = _dir(actor.position, cell)
        current = cell
        for _ in range(5):
            current = _step(current, direction)
            if not battle.in_bounds(current):
                break
            units = _occupants(battle, current, actor)
            if units:
                return sum(85.0 if unit.player_id != actor.player_id else -100.0 for unit in units)
        return -30.0


class MagneticGripSkill(RockTargetSkill):
    collision_status_cls = MagneticLockStatus

    def __init__(self) -> None:
        super().__init__("spirit_magnetic_grip", "磁力之握", "每本人轮一次；范5拖残岩到周围，经过单位造成伤害及破魔封主动。",
                         max_uses_per_turn=1, target_mode="cell")

    def targets(self, battle: Battle, actor: Unit) -> list[SpiritRemnant]:
        if actor.position is None:
            return []
        return [rock for rock in self.rocks(battle, actor)
                if rock.cell.distance_to(actor.position) <= 5
                and (rock.cell.x == actor.position.x or rock.cell.y == actor.position.y)
                and rock.cell != actor.position]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = {rock.cell for rock in self.targets(battle, actor)}
        if not cells or payload and payload.get("x") is not None and payload_position(payload) not in cells:
            return False, "请选择范5内同一直线的残岩。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        rocks = self.targets(battle, actor)
        return {"cells": positions_to_dict([rock.cell for rock in rocks]),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        rock = _rock_at(battle, payload_position(payload))
        if rock is None or actor.position is None:
            return
        direction = _dir(rock.cell, actor.position)
        current = rock.cell
        passed: set[str] = set()
        while _step(current, direction) != actor.position:
            current = _step(current, direction)
            for unit in _occupants(battle, current, actor):
                if unit.unit_id not in passed:
                    passed.add(unit.unit_id)
                    self._queue_collision(battle, actor, unit)
        rock.cell = current
        battle.log_public_event(f"残岩被拉到 {actor.name} 周围。", source=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": rock.cell.x, "y": rock.cell.y} for rock in self.targets(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        rock = _rock_at(battle, payload_position(payload))
        if rock is None or actor.position is None:
            return -1000.0
        direction, current, value = _dir(rock.cell, actor.position), rock.cell, 0.0
        while _step(current, direction) != actor.position:
            current = _step(current, direction)
            value += sum(80.0 if unit.player_id != actor.player_id else -100.0
                         for unit in _occupants(battle, current, actor))
        return value - 10.0


class MagnetizedStatus(StatusEffect):
    def __init__(self, source: Unit) -> None:
        super().__init__("磁化", "施术方轮末失最大血1/4；临残岩延长2轮，最多8轮。",
                         duration=1, tick_scope="any_turn_end")
        self.source_unit_id = source.unit_id
        self.source_player_id = source.player_id

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        source = battle.units.get(self.source_unit_id)
        target = self.owner
        if source is None or target is None or not target.alive or ended_player_id != self.source_player_id:
            return
        battle.resolve_damage(DamageContext(source=source, target=target, attack_power=0,
            raw_damage=target.max_health / 4, is_skill=True, from_field_effect=True,
            action_name="磁化轮末", tags={"skill", "spirit_magnetize", "periodic"}))
        if target.alive and target.position is not None and source is not None and battle.unit_belongs_to_current_turn(source):
            if any(rock.cell.distance_to(target.position) <= 1 for rock in remnants_for(battle, source, ROCK)):
                self.duration = min(8, self.duration + 2)
            self.decrement(battle)


class MagnetizeSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("spirit_magnetize", "磁化", "4本人轮一次；7×7敌方受1轮持续失血，临岩延长至最多8轮。",
                         cooldown_turns=4, target_mode="self")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in visible_units_at(battle, square_cells(battle, actor.position, 3), ignore=actor)
                if unit.player_id != actor.player_id] if actor.position is not None else []

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        return (True, "") if ok and self.targets(battle, actor) else (False, reason or "周围没有敌人。")

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.targets(battle, actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict(square_cells(battle, actor.position, 3)) if actor.position else [],
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": False}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in self.targets(battle, actor):
            battle.queue_skill_effect_action(actor=actor, display_name=self.name,
                effect_code="spirit_magnetize_hit", target_units=[target],
                target_cells=battle.unit_cells(target), include_cell_units=False,
                payload={"ignore_shield": True, "tags": ["skill", "piercing", self.code]},
                effect_resolver=self._resolve_hit)

    def _resolve_hit(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if target is not None and target.alive:
                apply_piercing_status_effect(battle, actor, target, action_name=self.name,
                    status=MagnetizedStatus(actor), is_skill=True,
                    tags={"skill", self.code}, ignore_targeting_restrictions=True)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        return sum(75.0 + (25.0 if any(rock.cell.distance_to(unit.position) <= 1
                   for rock in remnants_for(battle, actor, ROCK)) else 0.0)
                   for unit in self.targets(battle, actor))

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return bool(self.targets(battle, actor))

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
