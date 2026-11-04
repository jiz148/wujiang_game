"""Battle BattleGeometryMixin methods, retained under the Battle public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleGeometryMixin:
    """A cohesive Battle responsibility with no independent state."""

    def in_bounds(self, position: Position) -> bool:
        return 0 <= position.x < self.width and 0 <= position.y < self.height


    def unit_cells_at(self, unit: Unit, position: Position) -> list[Position]:
        return unit.footprint_cells_at(position)


    def unit_cells(self, unit: Unit) -> list[Position]:
        if unit.position is None or not unit.alive or unit.banished:
            return []
        return self.unit_cells_at(unit, unit.position)


    def unit_occupies(self, unit: Unit, position: Position) -> bool:
        if unit.position is None or not unit.alive or unit.banished:
            return False
        # Compare relative body offsets without allocating Position objects for
        # every occupant tested by movement, previews and area candidates.
        return (position.x - unit.position.x, position.y - unit.position.y) in unit.footprint_offsets


    def path_crossing_units(self, mover: Unit, path: list[Position]) -> list[Unit]:
        """Return an event each time the path enters a unit after fully leaving it."""
        return [unit for unit, _ in self.path_crossing_events(mover, path)]


    def path_crossing_events(self, mover: Unit, path: list[Position]) -> list[tuple[Unit, Position]]:
        """Return the recipient and entered body cell for each distinct passage."""
        if len(path) < 2:
            return []
        by_id: dict[str, Unit] = {}
        bodies: dict[str, set[Position]] = {}
        mover_recipient = self.effect_recipient(mover)
        for unit in self.all_units():
            recipient = self.effect_recipient(unit)
            if recipient.unit_id == mover_recipient.unit_id or not self.unit_cells(unit):
                continue
            by_id[recipient.unit_id] = recipient
            bodies.setdefault(recipient.unit_id, set()).update(self.unit_cells(unit))

        def overlapping_ids(anchor: Position) -> set[str]:
            mover_cells = set(self.unit_cells_at(mover, anchor))
            return {
                unit_id for unit_id, cells in bodies.items() if cells & mover_cells
            }

        previous_overlaps = overlapping_ids(path[0])
        crossed: list[tuple[Unit, Position]] = []
        for anchor in path[1:]:
            current_overlaps = overlapping_ids(anchor)
            mover_cells = set(self.unit_cells_at(mover, anchor))
            for unit_id, unit in by_id.items():
                if unit_id in current_overlaps - previous_overlaps:
                    entered_cell = min(bodies[unit_id] & mover_cells, key=lambda cell: (cell.y, cell.x))
                    crossed.append((unit, entered_cell))
            previous_overlaps = current_overlaps
        return crossed


    def can_place_unit(
        self,
        unit: Unit,
        position: Position,
        *,
        ignore: Optional[Unit] = None,
        mover: Optional[Unit] = None,
        ignore_units: bool = False,
        allow_stealth_overlap: bool = False,
    ) -> bool:
        for cell in self.unit_cells_at(unit, position):
            if not self.in_bounds(cell):
                return False
            if (cell.x, cell.y) in self.blocked_cells:
                return False
            if not ignore_units and self.is_occupied(
                cell,
                ignore=ignore,
                mover=mover or unit,
                allow_stealth_overlap=allow_stealth_overlap,
            ):
                return False
        return True


    def can_traverse_position(self, unit: Unit, position: Position, *, ignore_units: bool) -> bool:
        if ignore_units and unit.has_flying:
            return all(self.in_bounds(cell) for cell in self.unit_cells_at(unit, position))
        return self.can_place_unit(unit, position, ignore=unit, mover=unit, ignore_units=ignore_units)


    def unit_distance_to_cell(self, unit: Unit, cell: Position) -> int:
        if unit.position is None:
            return 10**9
        x, y = unit.position.x - cell.x, unit.position.y - cell.y
        # This query deliberately includes dead/banished bodies at their saved
        # anchor, just as the previous fallback to unit_cells_at did.
        return min(max(abs(x + dx), abs(y + dy)) for dx, dy in unit.footprint_offsets)


    def distance_between_units(self, left: Unit, right: Unit) -> int:
        if (left.position is None or right.position is None
                or not left.alive or not right.alive or left.banished or right.banished
                or not left.footprint_offsets or not right.footprint_offsets):
            return 10**9
        x, y = left.position.x - right.position.x, left.position.y - right.position.y
        return min(max(abs(x + lx - rx), abs(y + ly - ry))
                   for lx, ly in left.footprint_offsets for rx, ry in right.footprint_offsets)


    def cells_are_straight_aligned(self, start: Position, end: Position) -> bool:
        dx = end.x - start.x
        dy = end.y - start.y
        return dx == 0 or dy == 0 or abs(dx) == abs(dy)


    def unit_target_in_range_and_line(self, actor: Unit, target: Unit, max_distance: int) -> bool:
        actor_cells = self.unit_cells(actor)
        target_cells = self.unit_cells(target)
        if not actor_cells or not target_cells:
            return False
        if actor.unit_id == target.unit_id:
            return True
        ignore_line = any(component.ignores_direct_unit_target_line(self, actor) for component in actor.iter_components())
        for origin in actor_cells:
            for target_cell in target_cells:
                if origin.distance_to(target_cell) > max_distance:
                    continue
                if ignore_line or self.cells_are_straight_aligned(origin, target_cell):
                    return True
        return False


    def require_unit_target_in_range_and_line(
        self,
        actor: Unit,
        target: Unit,
        max_distance: int,
        *,
        action_name: str,
    ) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
        )
        if not self.unit_target_in_range_and_line(actor, target, max_distance):
            raise ActionError(f"{target.name} 不在【{action_name}】可直线选中的范围内。")


    def unit_hit_count_for_cells(self, unit: Unit, cells: Iterable[Position]) -> int:
        cell_keys = {(cell.x, cell.y) for cell in cells}
        return sum(1 for cell in self.unit_cells(unit) if (cell.x, cell.y) in cell_keys)


    def units_at(self, position: Position, *, ignore: Optional[Unit] = None) -> list[Unit]:
        units: list[Unit] = []
        for unit in self.units.values():
            if ignore is not None and unit.unit_id == ignore.unit_id:
                continue
            if unit.alive and not unit.banished and self.unit_occupies(unit, position):
                units.append(unit)
        return units


    def blocks_position_for(
        self,
        occupant: Unit,
        *,
        mover: Optional[Unit] = None,
        allow_stealth_overlap: bool = False,
    ) -> bool:
        if getattr(occupant, "attached_to_unit_id", None):
            return False
        if (mover is not None and occupant.allows_multiple_riders
                and mover.player_id == occupant.player_id and not mover.is_mount
                and len(mover.footprint_offsets) == 1):
            return False  # An allied unit may board an empty ship cell.
        if mover is not None and getattr(occupant, "standable_terrain", False) and not getattr(mover, "standable_terrain", False):
            return False
        if mover is not None and self.units_can_overlap(occupant, mover):
            return False
        if allow_stealth_overlap and occupant.is_stealthed():
            return False
        return True


    def is_occupied(
        self,
        position: Position,
        *,
        ignore: Optional[Unit] = None,
        mover: Optional[Unit] = None,
        allow_stealth_overlap: bool = False,
    ) -> bool:
        return any(
            self.blocks_position_for(unit, mover=mover, allow_stealth_overlap=allow_stealth_overlap)
            for unit in self.units_at(position, ignore=ignore)
        )


    def unit_at(self, position: Position) -> Optional[Unit]:
        occupants = self.units_at(position)
        if not occupants:
            return None
        visible = [unit for unit in occupants if not unit.is_stealthed()]
        return next((unit for unit in visible if not getattr(unit, "standable_terrain", False)), None) or (visible[0] if visible else occupants[0])


    def selectable_unit_at(
        self,
        position: Position,
        *,
        actor: Optional[Unit] = None,
        ignore_stealth: bool = False,
        preferred_unit_id: Optional[str] = None,
    ) -> Optional[Unit]:
        occupants = self.units_at(position)
        if preferred_unit_id:
            preferred = next((unit for unit in occupants if unit.unit_id == preferred_unit_id), None)
            if preferred is not None:
                ok, _ = self.unit_can_be_selected(preferred, actor=actor, ignore_stealth=ignore_stealth)
                if ok:
                    return preferred
        for unit in sorted(occupants, key=lambda candidate: bool(getattr(candidate, "standable_terrain", False))):
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if ok:
                return unit
        return None


    def targetable_units_at(
        self,
        position: Position,
        *,
        actor: Optional[Unit] = None,
        ignore_stealth: bool = False,
    ) -> list[Unit]:
        targets: list[Unit] = []
        for unit in self.units_at(position):
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if ok:
                targets.append(unit)
        return targets


    def units_sharing_position(self, unit: Unit) -> list[Unit]:
        if unit.position is None:
            return []
        shared: list[Unit] = []
        seen: set[str] = set()
        for cell in self.unit_cells(unit):
            for other in self.units_at(cell, ignore=unit):
                if other.unit_id in seen:
                    continue
                seen.add(other.unit_id)
                if other.alive and not other.banished:
                    shared.append(other)
        return shared


