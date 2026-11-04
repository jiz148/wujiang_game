"""One read-only placement batch using the current battle's blocking cells.

No action/score is stored. Only original pure geometry/overlap methods and
plain input fields qualify; unusual methods/descriptors keep ordinary calls.
"""
import os
from collections import OrderedDict
from types import GetSetDescriptorType, MemberDescriptorType

from wujiang.tactical.direct_queries import _raw, _MISSING
from wujiang.tactical.prediction import _session, direct_scoring, note

_FIELDS = ("unit_id", "position", "alive", "banished", "footprint_offsets", "player_id",
           "mounted_on_unit_id", "is_mount", "mount_owner_id", "attached_to_unit_id",
           "allow_enemy_destination_overlap", "standable_terrain")
_UNIT_METHODS = ("footprint_cells_at", "allows_destination_overlap_with")
_BATTLE_METHODS = ("can_place_unit", "unit_cells_at", "in_bounds", "is_occupied", "units_at",
                   "unit_occupies", "blocks_position_for", "units_can_overlap")


def _plain_type(kind, fields):
    return (type(kind).__getattribute__ is type.__getattribute__
            and _raw(kind, "__getattribute__") is object.__getattribute__
            and _raw(kind, "__getattr__") is _MISSING
            and type(_raw(kind, "__dict__")) is GetSetDescriptorType
            and _raw(kind, "__bool__") is _MISSING and _raw(kind, "__len__") is _MISSING
            and all(not hasattr(_raw(kind, name), "__get__") for name in fields))


def _offsets(value):
    return (type(value) in (tuple, list) and len(value) <= 256
            and all(type(pair) is tuple and len(pair) == 2
                    and type(pair[0]) is int and type(pair[1]) is int for pair in value))


def legal_placement_batch(battle, mover, positions):
    """Return every legal input cell in order, or None for original execution."""
    if not direct_scoring() or os.environ.get("WUJIANG_AI_DIRECT_PLACEMENT_BATCHES", "1") == "0":
        return None
    from wujiang.tactical.engine import core
    battle_type, unit_type, position_type, body_coordinates, bindings = core._DIRECT_PLACEMENT_QUERIES
    if (core.Battle is not battle_type or core.Unit is not unit_type or core.Position is not position_type
            or core.body_coordinates is not body_coordinates or type(battle) is not battle_type
            or any(type(kind).__getattribute__ is not type.__getattribute__ for kind in (unit_type, position_type))
            or not _plain_type(battle_type, ("units", "width", "height", "blocked_cells"))):
        return None
    for owner, name, function, code, defaults, kwdefaults, closure in bindings:
        current_kw = function.__kwdefaults__
        if (getattr(owner, name, None) is not function or function.__code__ is not code
                or function.__defaults__ is not defaults or type(current_kw) not in (dict, type(None))):
            return None
        current_kw = tuple(dict.items(current_kw)) if current_kw is not None else ()
        if (len(current_kw) != len(kwdefaults)
                or any(a is not c or b is not d for (a, b), (c, d) in zip(current_kw, kwdefaults))):
            return None
        try:
            current_closure = tuple(cell.cell_contents for cell in function.__closure__ or ())
        except ValueError:
            return None
        if len(current_closure) != len(closure) or any(a is not b for a, b in zip(current_closure, closure)):
            return None
    if (position_type.__getattribute__ is not object.__getattribute__
            or _raw(position_type, "__getattr__") is not _MISSING
            or any(type(_raw(position_type, name)) is not MemberDescriptorType for name in ("x", "y"))):
        return None
    def cell_ok(cell):
        return type(cell) is position_type and type(cell.x) is int and type(cell.y) is int
    data = vars(battle)
    units, width, height, terrain = (data.get(name) for name in ("units", "width", "height", "blocked_cells"))
    if (any(name in data for name in _BATTLE_METHODS) or type(units) is not dict
            or len(units) > 256 or type(width) is not int or type(height) is not int
            or type(terrain) is not set or not _offsets(tuple(terrain))
            or type(positions) is not list or len(positions) > 4096
            or not all(cell_ok(cell) for cell in positions)):
        return None
    kinds, rows = {}, []
    session = _session.get()
    # Retain only CPython class proofs, with inherited-attribute invalidation.
    # Every current instance field is still read and checked below.
    from wujiang.platform.ai_prediction_native import load_kernel
    kernel = load_kernel()
    certificates = session.setdefault("placement_type_proofs", {})
    for unit in [mover, *units.values()]:
        kind = type(unit)
        if (type(kind).__getattribute__ is not type.__getattribute__
                or type(kind).__hash__ is not type.__hash__):
            return None
        if kind not in kinds:
            versions = certificates.get(kind)
            if versions is None or kernel is None or not kernel.type_versions_match((kind,), versions):
                if (not _plain_type(kind, _FIELDS)
                        or any(_raw(kind, name) is not getattr(unit_type, name) for name in _UNIT_METHODS)):
                    return None
                if kernel is not None and len(certificates) < 256:
                    getattr(kind, "__getattribute__")  # Assign inherited lookup tag.
                    versions = kernel.type_versions((kind,))
                    if versions is not None:
                        certificates[kind] = versions
            kinds[kind] = True
        state = vars(unit)
        if any(name in state for name in _UNIT_METHODS):
            return None
        # getattr cannot run user code after the type/descriptor checks above.
        values = {name: getattr(unit, name, None) for name in _FIELDS}
        if (type(values["unit_id"]) is not str or type(values["player_id"]) is not int
                or any(type(values[name]) is not bool for name in
                       ("alive", "banished", "is_mount", "allow_enemy_destination_overlap"))
                or values["standable_terrain"] is not None and type(values["standable_terrain"]) is not bool
                or any(values[name] is not None and type(values[name]) is not str
                       for name in ("mounted_on_unit_id", "mount_owner_id", "attached_to_unit_id"))
                or values["position"] is not None and not cell_ok(values["position"])
                or not _offsets(values["footprint_offsets"])):
            return None
        rows.append((unit, values))
    # All calls in the original placement query are now proven pure. Their
    # repeated overlap tests can share one current blocking-cell set.
    blocked = set(terrain)
    mover_id = rows[0][1]["unit_id"]
    for unit, row in rows[1:]:
        anchor = row["position"]
        if row["unit_id"] == mover_id or not row["alive"] or row["banished"] or anchor is None:
            continue
        if battle.blocks_position_for(unit, mover=mover):
            blocked.update((anchor.x + dx, anchor.y + dy) for dx, dy in row["footprint_offsets"])
    offsets = rows[0][1]["footprint_offsets"]
    def legal(x, y):
        return all(0 <= x + dx < width and 0 <= y + dy < height
                   and (x + dx, y + dy) not in blocked for dx, dy in offsets)
    # This is a cache of an already computed geometry query in this decision,
    # keyed by all pure inputs, never a preferred move or action plan. Overlap
    # and life/body changes have already been folded into the fresh mask.
    if (0 < width * height <= 1024 and len(positions) >= width * height
            and all(0 <= cell.x < width and 0 <= cell.y < height for cell in positions)):
        key = width, height, tuple(offsets), frozenset(blocked)
        cache = session.setdefault("placement_masks", OrderedDict())
        mask = cache.get(key)
        if mask is None:
            mask = tuple(legal(x, y) for y in range(height) for x in range(width))
            size = 512 + 64 * (len(blocked) + len(offsets)) + 8 * len(mask)
            if size <= 2 * 1024 * 1024:
                used = session.get("placement_mask_bytes", 0)
                while cache and (len(cache) >= 128 or used + size > 2 * 1024 * 1024):
                    _, (_, removed) = cache.popitem(last=False)
                    used -= removed
                cache[key] = mask, size
                session["placement_mask_bytes"] = used + size
            note("direct_placement_mask_misses")
        else:
            mask = mask[0]
            cache.move_to_end(key)
            note("direct_placement_mask_hits")
        result = [cell for cell in positions if mask[cell.y * width + cell.x]]
    else:
        result = [cell for cell in positions if legal(cell.x, cell.y)]
    note("direct_placement_batches")
    note("direct_placement_cells", len(positions))
    note("direct_placement_units_indexed", len(rows) - 1)
    return result
