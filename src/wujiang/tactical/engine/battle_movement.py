"""Battle BattleMovementMixin methods, retained under the Battle public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleMovementMixin:
    """A cohesive Battle responsibility with no independent state."""

    def units_at_cells(self, cells: Iterable[Position]) -> list[Unit]:
        # Keep cell-major / insertion order: damage and reaction order depend on
        # it. The index is local to this call, so movement and nested probes can
        # never reuse stale occupancy. Previously every cell scanned all units.
        cells = list(cells)
        if not cells:
            return []
        if len(cells) == 1:
            occupants = {(cells[0].x, cells[0].y): self.units_at(cells[0])}
        else:
            occupants: dict[tuple[int, int], list[Unit]] = {(cell.x, cell.y): [] for cell in cells}
            for unit in self.units.values():
                if not unit.alive or unit.banished or unit.position is None:
                    continue
                x, y = unit.position.x, unit.position.y
                for dx, dy in set(unit.footprint_offsets):
                    bucket = occupants.get((x + dx, y + dy))
                    if bucket is not None:
                        bucket.append(unit)
        units: list[Unit] = []
        seen: set[str] = set()
        for cell in cells:
            for unit in occupants[(cell.x, cell.y)]:
                if unit.unit_id in seen:
                    continue
                seen.add(unit.unit_id)
                units.append(unit)
        return units


    def get_unit(self, unit_id: str) -> Unit:
        from wujiang.tactical.engine.core import (
            ActionError,
        )
        if unit_id not in self.units:
            ghost_reaction = (
                (self.pending_chain is not None and unit_id in self.pending_chain.options_by_unit)
                or (self.resolving_action is not None
                    and self.resolving_action.actor_id == unit_id
                    and self.resolving_action.action_type == "reaction_skill"
                    and self.resolving_action.payload.get("after_death_effect"))
            )
            if ghost_reaction:
                pending = next((unit for unit in self.destroyed_units if unit.unit_id == unit_id), None)
                if pending is not None:
                    return pending
            if unit_id in self.hell_rebirth_unit_ids or unit_id in self.hell_dragon_return_cells:
                pending = next((unit for unit in self.destroyed_units if unit.unit_id == unit_id), None)
                if pending is not None:
                    return pending
            raise ActionError("找不到目标单位。")
        return self.units[unit_id]


    def neighbors(self, position: Position) -> list[Position]:
        from wujiang.tactical.engine.core import (
            Position,
            neighbor_coordinates,
        )
        return [Position(x, y) for x, y in neighbor_coordinates(self.width, self.height, position.x, position.y)]


    def cells_within_distance(self, unit: Unit, distance: int) -> list[Position]:
        """Exact body-to-cell range, row-major, with fresh caller-owned output."""
        from wujiang.tactical.engine.core import (
            Position,
            range_coordinates,
        )
        if unit.position is None:
            return []
        return [Position(x, y) for x, y in range_coordinates(
            self.width, self.height, unit.position.x, unit.position.y, tuple(unit.footprint_offsets), distance)]


    def line_positions(
        self,
        start: Position,
        direction: tuple[int, int],
        length: int,
    ) -> list[Position]:
        result: list[Position] = []
        current = start
        for _ in range(length):
            current = current.offset(*direction)
            if not self.in_bounds(current):
                break
            result.append(current)
        return result


    def terrain_step_allowed(self, unit: Unit, start: Position, end: Position) -> bool:
        if getattr(unit, "has_flying", False):
            return True
        cells = set(self.unit_cells_at(unit, start) + self.unit_cells_at(unit, end))
        return not any(
            other.unit_id != unit.unit_id and getattr(other, "standable_terrain", False)
            and cells.intersection(self.unit_cells(other))
            for other in self.all_units()
        )


    def explicit_path(
        self,
        unit: Unit,
        steps: list[Position],
        *,
        max_distance: int,
        exact_distance: Optional[int] = None,
        straight_only: bool = False,
        ignore_units: bool = False,
        allow_anywhere: bool = False,
        use_movement_cost: bool = False,
    ) -> list[Position]:
        from wujiang.tactical.engine.core import (
            ActionError,
        )
        ignore_units = ignore_units or unit.ignore_units_while_moving
        if unit.position is None:
            raise ActionError("单位不在战场上。")
        if not steps:
            raise ActionError("缺少移动路径。")
        path = [unit.position]
        direction: Optional[tuple[int, int]] = None
        distance_cost = 0
        for index, step in enumerate(steps):
            if not self.in_bounds(step):
                raise ActionError("移动路径超出战场边界。")
            previous = path[-1]
            if not self.terrain_step_allowed(unit, previous, step):
                raise ActionError("只有飞行单位可以移动到地形上或离开地形。")
            if allow_anywhere:
                path.append(step)
                distance_cost += 1
                continue
            dx = step.x - previous.x
            dy = step.y - previous.y
            if max(abs(dx), abs(dy)) != 1:
                raise ActionError("移动路径必须逐格相邻。")
            if straight_only:
                current_direction = (
                    0 if dx == 0 else dx // abs(dx),
                    0 if dy == 0 else dy // abs(dy),
                )
                if direction is None:
                    direction = current_direction
                elif current_direction != direction:
                    raise ActionError("该移动必须沿同一直线前进。")
            step_ignores_units = ignore_units and index < len(steps) - 1
            if not self.can_traverse_position(unit, step, ignore_units=step_ignores_units):
                raise ActionError("移动路径被阻挡。")
            distance_cost += self.normal_movement_step_cost(unit, previous, step) if use_movement_cost else 1
            path.append(step)
        if distance_cost > max_distance:
            raise ActionError("超出位移距离。")
        if exact_distance is not None and len(path) - 1 != exact_distance:
            raise ActionError(f"必须恰好位移 {exact_distance} 格。")
        return path


    def reachable_positions(
        self,
        unit: Unit,
        *,
        max_distance: int,
        exact_distance: Optional[int] = None,
        straight_only: bool = False,
        ignore_units: bool = False,
        allow_anywhere: bool = False,
        use_movement_cost: bool = False,
    ) -> list[Position]:
        from wujiang.tactical.engine.core import (
            Position,
            count,
            deque,
            heapq,
        )
        ignore_units = ignore_units or unit.ignore_units_while_moving
        if unit.position is None:
            return []
        if allow_anywhere:
            return [
                Position(x, y)
                for x in range(self.width)
                for y in range(self.height)
                if self.terrain_step_allowed(unit, unit.position, Position(x, y))
                and self.can_place_unit(unit, Position(x, y), ignore=unit, mover=unit, ignore_units=False)
            ]
        if straight_only:
            result: list[Position] = []
            for direction in (
                (-1, -1),
                (-1, 0),
                (-1, 1),
                (0, -1),
                (0, 1),
                (1, -1),
                (1, 0),
                (1, 1),
            ):
                previous = unit.position
                spent = 0
                for step, candidate in enumerate(self.line_positions(unit.position, direction, max_distance), start=1):
                    if not self.terrain_step_allowed(unit, previous, candidate):
                        break
                    if not self.can_traverse_position(unit, candidate, ignore_units=ignore_units):
                        break
                    spent += self.normal_movement_step_cost(unit, previous, candidate) if use_movement_cost else 1
                    previous = candidate
                    if spent > max_distance:
                        break
                    distance_value = spent if use_movement_cost else step
                    if (
                        (exact_distance is None or distance_value == exact_distance)
                        and self.can_place_unit(unit, candidate, ignore=unit, mover=unit, ignore_units=False)
                    ):
                        result.append(candidate)
            return result
        if use_movement_cost:
            distances: dict[Position, int] = {unit.position: 0}
            queue_counter = count()
            heap: list[tuple[int, int, Position]] = [(0, next(queue_counter), unit.position)]
            while heap:
                dist, _, pos = heapq.heappop(heap)
                if dist != distances.get(pos):
                    continue
                for nxt in self.neighbors(pos):
                    if not self.terrain_step_allowed(unit, pos, nxt):
                        continue
                    step_ignores_units = ignore_units and nxt != unit.position
                    if not self.can_traverse_position(unit, nxt, ignore_units=step_ignores_units):
                        continue
                    next_dist = dist + self.normal_movement_step_cost(unit, pos, nxt)
                    if next_dist > max_distance:
                        continue
                    if next_dist >= distances.get(nxt, 10**9):
                        continue
                    distances[nxt] = next_dist
                    heapq.heappush(heap, (next_dist, next(queue_counter), nxt))
            return [
                pos
                for pos, dist in distances.items()
                if pos != unit.position
                and (exact_distance is None or dist == exact_distance)
                and self.can_place_unit(unit, pos, ignore=unit, mover=unit, ignore_units=False)
            ]
        visited = {unit.position}
        queue: deque[tuple[Position, int]] = deque([(unit.position, 0)])
        result: list[Position] = []
        while queue:
            pos, dist = queue.popleft()
            if dist >= max_distance:
                continue
            for nxt in self.neighbors(pos):
                if not self.terrain_step_allowed(unit, pos, nxt):
                    continue
                if nxt in visited:
                    continue
                if not self.can_traverse_position(unit, nxt, ignore_units=ignore_units):
                    continue
                visited.add(nxt)
                if (
                    (exact_distance is None or dist + 1 == exact_distance)
                    and self.can_place_unit(unit, nxt, ignore=unit, mover=unit, ignore_units=False)
                ):
                    result.append(nxt)
                queue.append((nxt, dist + 1))
        return result


    def find_path(
        self,
        unit: Unit,
        destination: Position,
        *,
        max_distance: int,
        exact_distance: Optional[int] = None,
        straight_only: bool = False,
        ignore_units: bool = False,
        allow_anywhere: bool = False,
        use_movement_cost: bool = False,
    ) -> list[Position]:
        from wujiang.tactical.engine.core import (
            ActionError,
            count,
            deque,
            heapq,
        )
        ignore_units = ignore_units or unit.ignore_units_while_moving
        if unit.position is None:
            raise ActionError("单位不在战场上。")
        if destination == unit.position:
            return [unit.position]
        if not self.terrain_step_allowed(unit, unit.position, destination):
            raise ActionError("只有飞行单位可以移动到地形上或离开地形。")
        if allow_anywhere:
            return [unit.position, destination]
        if straight_only:
            dx = destination.x - unit.position.x
            dy = destination.y - unit.position.y
            if max(abs(dx), abs(dy)) > max_distance:
                raise ActionError("超出位移距离。")
            if dx != 0:
                dx = dx // abs(dx)
            if dy != 0:
                dy = dy // abs(dy)
            current = unit.position
            path = [current]
            spent = 0
            while current != destination:
                previous = current
                current = current.offset(dx, dy)
                if not self.terrain_step_allowed(unit, previous, current):
                    raise ActionError("只有飞行单位可以移动到地形上或离开地形。")
                step_ignores_units = ignore_units and current != destination
                if not self.can_traverse_position(unit, current, ignore_units=step_ignores_units):
                    raise ActionError("移动路径被阻挡。")
                spent += self.normal_movement_step_cost(unit, previous, current) if use_movement_cost else 1
                if spent > max_distance:
                    raise ActionError("超出位移距离。")
                path.append(current)
            if exact_distance is not None and len(path) - 1 != exact_distance:
                raise ActionError(f"必须恰好位移 {exact_distance} 格。")
            return path
        if use_movement_cost:
            queue_counter = count()
            heap: list[tuple[int, int, Position]] = [(0, next(queue_counter), unit.position)]
            parents: dict[Position, Optional[Position]] = {unit.position: None}
            distances: dict[Position, int] = {unit.position: 0}
            while heap:
                dist, _, pos = heapq.heappop(heap)
                if dist != distances.get(pos):
                    continue
                if pos == destination:
                    break
                for nxt in self.neighbors(pos):
                    if not self.terrain_step_allowed(unit, pos, nxt):
                        continue
                    step_ignores_units = ignore_units and nxt != destination
                    if not self.can_traverse_position(unit, nxt, ignore_units=step_ignores_units):
                        continue
                    next_dist = dist + self.normal_movement_step_cost(unit, pos, nxt)
                    if next_dist > max_distance:
                        continue
                    if next_dist >= distances.get(nxt, 10**9):
                        continue
                    parents[nxt] = pos
                    distances[nxt] = next_dist
                    heapq.heappush(heap, (next_dist, next(queue_counter), nxt))
            if destination not in parents:
                raise ActionError("找不到可行的移动路径。")
            path: list[Position] = []
            current: Optional[Position] = destination
            while current is not None:
                path.append(current)
                current = parents[current]
            path.reverse()
            if exact_distance is not None and len(path) - 1 != exact_distance:
                raise ActionError(f"必须恰好位移 {exact_distance} 格。")
            return path
        queue: deque[Position] = deque([unit.position])
        parents: dict[Position, Optional[Position]] = {unit.position: None}
        distances: dict[Position, int] = {unit.position: 0}
        while queue:
            pos = queue.popleft()
            if pos == destination:
                break
            for nxt in self.neighbors(pos):
                if not self.terrain_step_allowed(unit, pos, nxt):
                    continue
                next_dist = distances[pos] + 1
                if next_dist > max_distance or nxt in parents:
                    continue
                step_ignores_units = ignore_units and nxt != destination
                if not self.can_traverse_position(unit, nxt, ignore_units=step_ignores_units):
                    continue
                parents[nxt] = pos
                distances[nxt] = next_dist
                queue.append(nxt)
        if destination not in parents:
            raise ActionError("找不到可行的移动路径。")
        path: list[Position] = []
        current: Optional[Position] = destination
        while current is not None:
            path.append(current)
            current = parents[current]
        path.reverse()
        if exact_distance is not None and len(path) - 1 != exact_distance:
            raise ActionError(f"必须恰好位移 {exact_distance} 格。")
        return path


    def move_unit(
        self,
        unit: Unit,
        destination: Position,
        *,
        via_skill: bool = False,
        straight_only: bool = False,
        ignore_units: bool = False,
        allow_anywhere: bool = False,
        max_distance: Optional[int] = None,
        exact_distance: Optional[int] = None,
        path: Optional[list[Position]] = None,
        triggered_by_reaction: bool = False,
        tags: Optional[set[str]] = None,
        forced: bool = False,
    ) -> MoveContext:
        from wujiang.tactical.engine.core import (
            ActionError,
            MoveContext,
        )
        ignore_units = ignore_units or unit.ignore_units_while_moving

        if unit.banished and "arena" in getattr(self, "special_mode_codes", ()):
            raise ActionError("擂台候场单位不能移动或受到位移效果。")
        if unit.position is None:
            raise ActionError("单位不在战场上。")
        if getattr(unit, "attached_to_unit_id", None):
            raise ActionError("附着期间不能独立移动。")
        if unit.direct_effects_blocked() and (forced or (via_skill and self.resolving_action is not None and self.resolving_action.actor_id != unit.unit_id)):
            raise ActionError(f"{unit.name} 不受外部位移效果影响。")
        if unit.skill_non_damage_effects_blocked() and via_skill:
            raise ActionError(f"{unit.name} 不受技能位移效果影响。")
        if via_skill and getattr(unit, "standable_terrain", False):
            raise ActionError("地形单位不受技能位移效果影响。")
        carried_riders = [(rider, rider.position) for rider in self.riders_for(unit)]
        mounted_on = self.mounted_unit_for(unit)
        if destination == unit.position and path is None:
            raise ActionError("目标位置不能与当前位置相同。")
        if not self.in_bounds(destination):
            raise ActionError("目标位置超出战场边界。")
        if not self.can_place_unit(unit, destination, ignore=unit, mover=unit, ignore_units=False):
            raise ActionError("目标位置已被占用。")
        if unit.cannot_move and (not forced or any(
                getattr(status, "prevents_forced_movement", False) for status in unit.statuses)):
            raise ActionError(f"{unit.name} 当前无法移动。")
        if unit.cannot_normal_move and not via_skill and not forced:
            raise ActionError(f"{unit.name} 当前不能进行常规移动。")
        if max_distance is None:
            max_distance = unit.remaining_normal_move_distance(self) if not via_skill and not forced else int(unit.stat("speed"))
        use_movement_cost = not via_skill and not forced
        path = (
            self.explicit_path(
                unit,
                path,
                max_distance=max_distance,
                exact_distance=exact_distance,
                straight_only=straight_only,
                ignore_units=ignore_units,
                allow_anywhere=allow_anywhere,
                use_movement_cost=use_movement_cost,
            )
            if path is not None
            else self.find_path(
                unit,
                destination,
                max_distance=max_distance,
                exact_distance=exact_distance,
                straight_only=straight_only,
                ignore_units=ignore_units,
                allow_anywhere=allow_anywhere,
                use_movement_cost=use_movement_cost,
            )
        )
        destination = path[-1]
        ctx = MoveContext(
            unit=unit,
            start=unit.position,
            end=destination,
            path=path,
            via_skill=via_skill,
            triggered_by_reaction=triggered_by_reaction,
            tags=tags or set(),
        )
        if not via_skill and not forced and not triggered_by_reaction and unit.is_clone:
            if not self.clone_can_act_this_turn(unit):
                raise ActionError("本武将回合已有另一个分身行动。")
        if not via_skill and not forced and not triggered_by_reaction:
            previous_payment = getattr(unit, "_paying_skill_cost", False)
            unit._paying_skill_cost = True
            try:
                for component in list(unit.iter_components()):
                    component.on_before_normal_move(self, ctx)
            finally:
                if previous_payment:
                    unit._paying_skill_cost = previous_payment
                else:
                    unit.__dict__.pop("_paying_skill_cost", None)
        if not via_skill and not forced and not triggered_by_reaction and unit.is_clone:
            unit._clone_action_turn = self.turn_number
        action = self.resolving_action
        if (via_skill and action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"}
                and action.actor_id != unit.unit_id and not action.payload.get("from_field_effect")
                and self.destroy_clone_for_skill_effect(unit, source=self.units.get(action.actor_id), action_name=action.display_name)):
            ctx.end, ctx.path = ctx.start, [ctx.start]
            return ctx
        unit.position = destination
        if hasattr(unit, "_resolution_actual_position"):
            unit._resolution_actual_position = destination
        if carried_riders:
            delta_x = destination.x - ctx.start.x
            delta_y = destination.y - ctx.start.y
            for carried_rider, start in carried_riders:
                carried_rider.position = start.offset(delta_x, delta_y)
                carried_rider.moved_this_turn = True
        if mounted_on is not None and unit.position not in self.unit_cells(mounted_on):
            self.clear_mounted_state(unit)
        if unit.mounted_on_unit_id is None and not unit.is_mount and len(self.unit_cells(unit)) == 1:
            for ship in self.units_at(unit.position, ignore=unit):
                if ship.allows_multiple_riders and ship.player_id == unit.player_id:
                    self.set_mounted_state(unit, ship)
                    break
        unit.moved_this_turn = unit.moved_this_turn or len(path) > 1
        if not triggered_by_reaction:
            if not via_skill and not forced:
                unit.normal_move_steps_used += max(0, len(path) - 1)
                unit.normal_move_actions_used += 1
                unit.move_used = True
        self.log(f"{unit.name} 移动了。")
        for effect in list(self.field_effects):
            effect.on_unit_moved(self, ctx)
        for other in self.all_units():
            for component in list(other.iter_components()):
                component.on_unit_moved(self, ctx)
        self.enforce_sandstorm_stealth_rules()
        self.cleanup_dead_units()
        if self.resolving_action is None and self.pending_chain is None:
            self.advance_followup_actions()
        return ctx


    def validate_target(
        self,
        actor: Unit,
        target: Unit,
        *,
        action_name: str,
        is_skill: bool,
        is_hostile: bool,
        ignore_shield: bool = False,
        half_ignore_shield: bool = False,
        ignore_magic_immunity: bool = False,
        ignore_physical_immunity: bool = False,
        from_field_effect: bool = False,
        cannot_evade: bool = False,
        ignore_targeting_restrictions: bool = False,
        resolve_defenses: bool = True,
        damage_target: bool = False,
        record_effect_on_validation: bool = True,
        tags: Optional[set[str]] = None,
    ) -> TargetContext:
        from wujiang.tactical.engine.core import (
            TargetContext,
        )
        target = self.effect_recipient(target)
        ctx = TargetContext(
            actor=actor,
            target=target,
            action_name=action_name,
            is_skill=is_skill,
            is_hostile=is_hostile,
            ignore_shield=ignore_shield,
            half_ignore_shield=half_ignore_shield,
            ignore_magic_immunity=ignore_magic_immunity,
            ignore_physical_immunity=ignore_physical_immunity,
            from_field_effect=from_field_effect,
            cannot_evade=cannot_evade,
            damage_target=damage_target,
            tags=tags or set(),
        )
        if not target.alive or target.position is None or target.banished:
            ctx.cancelled = True
            ctx.reason = "目标暂时不在战场上。"
            return ctx
        if not ignore_targeting_restrictions and target.cannot_be_targeted and is_hostile:
            ctx.cancelled = True
            ctx.reason = f"{target.name} 当前无法被选中。"
            return ctx
        for component in self.components_for(actor):
            component.on_targeted(self, ctx)
        for effect in list(self.field_effects):
            effect.on_targeted(self, ctx)
        for component in self.components_for(target):
            component.on_targeted(self, ctx)
        for effect in list(self.field_effects):
            effect.on_after_target_modifiers(self, ctx)
        if ctx.cancelled:
            return ctx
        if (
            is_hostile
            and is_skill
            and not ctx.from_field_effect
            and target.magic_immunity
            and not ctx.ignore_magic_immunity
        ):
            ctx.cancelled = True
            self.emit_defense_visual_event(
                source=actor,
                target=target,
                action_name=action_name,
                defense_reason="magic_immunity",
            )
            ctx.reason = f"{target.name} 处于魔免状态。"
            return ctx
        if (
            is_hostile
            and not is_skill
            and not ctx.from_field_effect
            and bool(getattr(target, "physical_immunity", False))
            and not ctx.ignore_physical_immunity
        ):
            ctx.cancelled = True
            self.emit_defense_visual_event(
                source=actor,
                target=target,
                action_name=action_name,
                defense_reason="physical_immunity",
            )
            ctx.reason = f"{target.name} 处于物免状态。"
            return ctx
        if not resolve_defenses:
            return ctx
        if is_hostile and target.total_shields() > 0:
            if ctx.ignore_shield or ctx.half_ignore_shield:
                return ctx
            target.consume_one_shield()
            ctx.shield_consumed = True
            ctx.cancelled = True
            ctx.reason = f"{target.name} 的护盾抵消了【{action_name}】。"
            self.emit_defense_visual_event(
                source=actor,
                target=target,
                action_name=action_name,
                defense_reason="shield",
            )
            return ctx
        if is_hostile and target.dodge_charges > 0 and not ctx.cannot_evade:
            target.dodge_charges -= 1
            ctx.cancelled = True
            ctx.reason = f"{target.name} 闪避了【{action_name}】。"
            self.emit_defense_visual_event(
                source=actor,
                target=target,
                action_name=action_name,
                defense_reason="dodge",
            )
            return ctx
        if ctx.is_skill and not ctx.damage_target and not ctx.from_field_effect:
            if self.destroy_clone_for_skill_effect(target, source=actor, action_name=action_name):
                ctx.destroyed_as_clone = True
                ctx.cancelled = True
                ctx.reason = "分身已因技能效果消散。"
            if (record_effect_on_validation and not ctx.cancelled
                    and not target.skill_non_damage_effects_blocked()):
                self.record_skill_effect(target)
        return ctx


    def unit_can_be_selected(
        self,
        unit: Unit,
        *,
        actor: Unit | None = None,
        ignore_stealth: bool = False,
    ) -> tuple[bool, str]:
        if not unit.alive or unit.position is None or unit.banished:
            return False, "目标暂时不在战场上。"
        if getattr(unit, "attached_to_unit_id", None):
            return False, f"{unit.name} 正在附着，无法被选中。"
        if unit.cannot_be_targeted and not ignore_stealth:
            return False, f"{unit.name} 当前无法被选中。"
        if (
            unit.has_status("隐身")
            and not ignore_stealth
            and (actor is None or actor.player_id != unit.player_id)
        ):
            return False, f"{unit.name} 当前处于隐身状态。"
        return True, ""


    def require_selectable_unit(
        self,
        unit: Unit,
        *,
        actor: Unit | None = None,
        action_name: str,
        ignore_stealth: bool = False,
        queued_resolution: bool = False,
    ) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
            ActionMiss,
        )
        ok, reason = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
        if ok:
            return
        if queued_resolution:
            raise ActionMiss(reason or f"【{action_name}】落在原定格上，没有命中有效目标。")
        raise ActionError(reason or f"{unit.name} 当前无法作为【{action_name}】的目标。")


    def filter_preview_targets(
        self,
        actor: Unit,
        preview: dict[str, Any],
        *,
        ignore_stealth: bool = False,
        replace_cells: bool = False,
        require_line_targeting: bool = False,
        line_target_range: Optional[int] = None,
    ) -> dict[str, Any]:
        sanitized = dict(preview)
        target_ids: list[str] = []
        target_cells: list[dict[str, int]] = []
        max_distance = actor.targeting_range() if line_target_range is None else line_target_range
        for unit_id in preview.get("target_unit_ids", []):
            unit = self.units.get(unit_id)
            if unit is None or unit.position is None:
                continue
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if not ok:
                continue
            if require_line_targeting and not self.unit_target_in_range_and_line(actor, unit, max_distance):
                continue
            target_ids.append(unit.unit_id)
            target_cells.extend(cell.to_dict() for cell in self.unit_cells(unit))
        sanitized["target_unit_ids"] = target_ids
        if replace_cells:
            sanitized["cells"] = target_cells
        return sanitized


    def is_forced_movement_blocked(self, position: Position) -> bool:
        return any(effect.blocks_forced_movement(self, position) for effect in self.field_effects)


    def normal_movement_step_cost(self, unit: Unit, start: Position, end: Position) -> int:
        cost = 1
        for effect in list(self.field_effects):
            cost = effect.normal_movement_step_cost(self, unit, start, end, cost)
        for component in list(unit.iter_components()):
            cost = component.normal_movement_step_cost(self, unit, start, end, cost)
        return max(1, int(cost))


