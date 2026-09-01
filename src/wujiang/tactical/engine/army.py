from __future__ import annotations

from typing import Any, Iterable, Optional


ARMY_SLOT_PREFIX = "__army__"
ARMY_ORDERS = ("advance", "seek", "hold", "retreat")
ARMY_ORDER_LABELS = {
    "advance": "\u8fdb\u519b",
    "seek": "\u5bfb\u654c",
    "hold": "\u56fa\u5b88",
    "retreat": "\u540e\u64a4",
}
ARMY_STRIDES = ("full", "step")
ARMY_STRIDE_LABELS = {
    "full": "\u5168\u901f",
    "step": "\u9010\u6b65",
}
ARMY_DIRECTIONS = {
    "N": (0, -1),
    "NE": (1, -1),
    "E": (1, 0),
    "SE": (1, 1),
    "S": (0, 1),
    "SW": (-1, 1),
    "W": (-1, 0),
    "NW": (-1, -1),
}
ARMY_DIRECTION_LABELS = {
    "N": "\u5317",
    "NE": "\u4e1c\u5317",
    "E": "\u4e1c",
    "SE": "\u4e1c\u5357",
    "S": "\u5357",
    "SW": "\u897f\u5357",
    "W": "\u897f",
    "NW": "\u897f\u5317",
}
ARMY_KIND_CODES = {
    "infantry": "strategy_infantry",
    "archer": "strategy_archer",
    "cavalry": "strategy_cavalry",
}
ARMY_KIND_LABELS = {
    "infantry": "\u666e\u901a\u6b65\u5175",
    "archer": "\u5f13\u5175",
    "cavalry": "\u9a91\u5175",
    "garrison": "\u5b88\u5907\u5175",
    "mountain_soldier": "\u5c71\u5730\u5175",
    "ether_scout": "\u4ee5\u592a\u4fa6\u5bdf\u5175",
    "wall_engineer": "\u57ce\u5899\u5de5\u5175",
    "snow_ghost": "\u96ea\u9b3c",
    "arrow_tower": "\u7bad\u5854",
    "cannon": "\u706b\u70ae",
}
ARMY_STRUCTURE_KINDS = frozenset({"arrow_tower"})
ARMY_RANGED_KINDS = frozenset({"archer", "ether_scout", "arrow_tower"})
ARMY_STRIKE_WAVES = ("melee", "ranged", "cannon")
SIEGE_STRUCTURE_HERO_CODES = frozenset({"strategy_arrow_tower"})
SIEGE_ENGINE_HERO_CODES = frozenset({"strategy_cannon"})
ARMY_CODE_TO_KIND = {code: kind for kind, code in ARMY_KIND_CODES.items()}
MAX_ARMY_PER_KIND = 40
MAX_ARMY_PER_SEAT = 50
DEFAULT_ARMY_DIRECTION_BY_PLAYER = {1: "E", 2: "W"}
CANNON_AMMO = {
    "shell": {"id": "shell", "name": "炮弹", "splash": 0, "tier": 0},
    "heavy_shell": {"id": "heavy_shell", "name": "重型炮弹", "splash": 1, "tier": 1},
    "ultra_shell": {"id": "ultra_shell", "name": "超重型炮弹", "splash": 2, "tier": 2},
}
CANNON_AMMO_ALIASES = {
    "炮弹": "shell",
    "重型炮弹": "heavy_shell",
    "超重型炮弹": "ultra_shell",
    "heavy": "heavy_shell",
    "ultra": "ultra_shell",
}


def is_army_soldier(unit: Any) -> bool:
    if unit is None:
        return False
    if bool(getattr(unit, "is_army_soldier", False)):
        return True
    return str(getattr(unit, "hero_code", "") or "").startswith("strategy_")


def army_slot_id(player_id: int) -> str:
    return f"{ARMY_SLOT_PREFIX}{int(player_id)}"


def parse_army_slot(slot_id: Optional[str]) -> Optional[int]:
    text = str(slot_id or "")
    if not text.startswith(ARMY_SLOT_PREFIX):
        return None
    suffix = text[len(ARMY_SLOT_PREFIX) :]
    try:
        player_id = int(suffix)
    except (TypeError, ValueError):
        return None
    if player_id not in (1, 2):
        return None
    return player_id


def default_kind_command(player_id: int, kind: Optional[str] = None) -> dict[str, str]:
    command = {
        "order": "advance",
        "direction": DEFAULT_ARMY_DIRECTION_BY_PLAYER.get(int(player_id), "E"),
        "stride": "full",
    }
    if str(kind or "") == "cannon":
        command["ammo"] = "shell"
    return command


def normalize_cannon_ammo(ammo: Any, *, allowed: Optional[Iterable[str]] = None) -> str:
    text = str(ammo or "shell").strip().lower()
    if text in CANNON_AMMO_ALIASES:
        text = CANNON_AMMO_ALIASES[text]
    if text not in CANNON_AMMO:
        text = "shell"
    allowed_ids = {str(item) for item in (allowed or CANNON_AMMO)}
    if text not in allowed_ids:
        return "shell" if "shell" in allowed_ids else next(iter(allowed_ids), "shell")
    return text


def cannon_ammo_options(splash_level: int = 0) -> list[dict[str, Any]]:
    tier = max(0, min(2, int(splash_level or 0)))
    return [dict(spec) for spec in CANNON_AMMO.values() if int(spec["tier"]) <= tier]


def cannon_ammo_ids(splash_level: int = 0) -> list[str]:
    return [str(item["id"]) for item in cannon_ammo_options(splash_level)]


def default_army_orders() -> dict[int, dict[str, dict[str, str]]]:
    return {1: {}, 2: {}}


def normalize_army_stride(stride: Any, *, default: str = "full") -> str:
    text = str(stride or "").strip().lower()
    aliases = {
        "step": "step",
        "full": "full",
        "1": "step",
        "once": "step",
        "\u9010\u6b65": "step",
        "\u4e00\u683c": "step",
        "\u5168\u901f": "full",
    }
    if text in aliases:
        return aliases[text]
    return default if default in ARMY_STRIDES else "full"


def _legacy_team_command(team: dict[str, Any]) -> Optional[dict[str, str]]:
    order = team.get("order")
    direction = team.get("direction")
    if order in ARMY_ORDERS and direction in ARMY_DIRECTIONS:
        return {
            "order": str(order),
            "direction": str(direction),
            "stride": normalize_army_stride(team.get("stride")),
        }
    return None


def command_for_kind(orders: Any, player_id: int, kind: str) -> dict[str, str]:
    team = (orders or {}).get(int(player_id)) or {}
    if isinstance(team, dict):
        specific = team.get(kind)
        if isinstance(specific, dict) and specific.get("order"):
            command = {
                "order": str(specific.get("order") or default_kind_command(player_id, kind)["order"]),
                "direction": str(specific.get("direction") or default_kind_command(player_id, kind)["direction"]),
                "stride": normalize_army_stride(specific.get("stride")),
            }
            if str(kind) == "cannon" or specific.get("ammo"):
                command["ammo"] = normalize_cannon_ammo(specific.get("ammo") or "shell")
            return command
        legacy = _legacy_team_command(team)
        if legacy is not None:
            return legacy
    return default_kind_command(player_id, kind)


def normalize_army_direction(direction: Any, *, player_id: int = 1) -> str:
    text = str(direction or "").strip().upper()
    aliases = {
        "\u5317": "N",
        "\u4e1c\u5317": "NE",
        "\u4e1c": "E",
        "\u4e1c\u5357": "SE",
        "\u5357": "S",
        "\u897f\u5357": "SW",
        "\u897f": "W",
        "\u897f\u5317": "NW",
    }
    if text in aliases:
        text = aliases[text]
    if text in ARMY_DIRECTIONS:
        return text
    return DEFAULT_ARMY_DIRECTION_BY_PLAYER.get(int(player_id), "E")


def normalize_army_order_name(order: Any) -> str:
    text = str(order or "").strip().lower()
    aliases = {
        "\u8fdb\u519b": "advance",
        "\u8fdb\u653b": "advance",
        "\u5bfb\u654c": "seek",
        "\u63a5\u654c": "seek",
        "\u8ffd\u51fb": "seek",
        "\u56fa\u5b88": "hold",
        "\u9632\u5b88": "hold",
        "\u540e\u64a4": "retreat",
        "\u64a4\u9000": "retreat",
    }
    if text in aliases:
        text = aliases[text]
    if text not in ARMY_ORDERS:
        raise ValueError("\u519b\u961f\u6307\u4ee4\u53ea\u80fd\u662f\u8fdb\u519b\u3001\u5bfb\u654c\u3001\u56fa\u5b88\u6216\u540e\u64a4\u3002")
    return text


def normalize_army_command(
    order: Any,
    direction: Any = None,
    *,
    player_id: int = 1,
    previous: Optional[dict[str, str]] = None,
    stride: Any = None,
    ammo: Any = None,
    allowed_ammo: Optional[Iterable[str]] = None,
) -> dict[str, str]:
    current = dict(previous or default_kind_command(player_id))
    normalized_order = normalize_army_order_name(order)
    kept_direction = current.get("direction") or default_kind_command(player_id)["direction"]
    normalized_direction = (
        kept_direction
        if normalized_order in {"hold", "seek"} and (direction is None or str(direction).strip() == "")
        else normalize_army_direction(direction if direction not in {None, ""} else kept_direction, player_id=player_id)
    )
    kept_stride = normalize_army_stride(current.get("stride"))
    normalized_stride = kept_stride if stride in {None, ""} else normalize_army_stride(stride)
    command = {
        "order": normalized_order,
        "direction": normalized_direction,
        "stride": normalized_stride,
    }
    if ammo not in {None, ""} or current.get("ammo"):
        command["ammo"] = normalize_cannon_ammo(
            ammo if ammo not in {None, ""} else current.get("ammo"),
            allowed=allowed_ammo,
        )
    return command


def empty_army_counts() -> dict[str, int]:
    return {kind: 0 for kind in ARMY_KIND_CODES}


def normalize_army_counts(raw: Any) -> dict[str, int]:
    source = raw if isinstance(raw, dict) else {}
    counts = empty_army_counts()
    for kind in ARMY_KIND_CODES:
        try:
            value = int(source.get(kind, 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("\u58eb\u5175\u6570\u91cf\u5fc5\u987b\u662f\u6574\u6570\u3002") from exc
        if value < 0:
            raise ValueError("\u58eb\u5175\u6570\u91cf\u4e0d\u80fd\u4e3a\u8d1f\u6570\u3002")
        if value > MAX_ARMY_PER_KIND:
            raise ValueError(f"{ARMY_KIND_LABELS[kind]}\u6bcf\u5e2d\u6700\u591a {MAX_ARMY_PER_KIND} \u4e2a\u3002")
        counts[kind] = value
    if sum(counts.values()) > MAX_ARMY_PER_SEAT:
        raise ValueError(f"\u6bcf\u4e2a\u5e2d\u4f4d\u6700\u591a\u643a\u5e26 {MAX_ARMY_PER_SEAT} \u540d\u58eb\u5175\u3002")
    return counts


def army_codes_from_counts(counts: dict[str, int]) -> list[str]:
    codes: list[str] = []
    normalized = normalize_army_counts(counts)
    for kind, code in ARMY_KIND_CODES.items():
        codes.extend([code] * int(normalized.get(kind, 0) or 0))
    return codes


def army_kind_for_unit(unit: Any) -> Optional[str]:
    code = str(getattr(unit, "hero_code", "") or "")
    if code in ARMY_CODE_TO_KIND:
        return ARMY_CODE_TO_KIND[code]
    if code.startswith("strategy_"):
        return code[len("strategy_") :] or None
    return None


def army_kind_label(kind: str) -> str:
    return ARMY_KIND_LABELS.get(kind) or kind


def army_strike_wave(unit: Any) -> str:
    kind = army_kind_for_unit(unit) or ""
    if kind == "cannon" or str(getattr(unit, "siege_family", "") or "") == "cannon":
        return "cannon"
    if kind in ARMY_RANGED_KINDS:
        return "ranged"
    try:
        reach = int(unit.stat("attack_range"))
    except Exception:
        reach = 1
    if reach >= 3:
        return "ranged"
    return "melee"


def normalize_army_kind(kind: Any) -> str:
    text = str(kind or "").strip().lower()
    if text in ARMY_KIND_LABELS or text in ARMY_KIND_CODES:
        return text
    for code, mapped in ARMY_CODE_TO_KIND.items():
        if text == code:
            return mapped
    if text.startswith("strategy_"):
        stripped = text[len("strategy_") :]
        if stripped:
            return stripped
    for mapped, label in ARMY_KIND_LABELS.items():
        if text == label:
            return mapped
    raise ValueError("\u672a\u77e5\u5175\u79cd\u3002")


def living_army_units(battle: Any, player_id: int) -> list[Any]:
    units = [
        unit
        for unit in battle.player_units(player_id)
        if is_army_soldier(unit)
        and unit.alive
        and not unit.banished
        and unit.position is not None
    ]
    units.sort(key=lambda unit: (unit.position.y, unit.position.x, unit.unit_id))
    return units


def present_army_kinds(battle: Any, player_id: int) -> list[str]:
    kinds: list[str] = []
    for unit in living_army_units(battle, player_id):
        kind = army_kind_for_unit(unit)
        if kind and kind not in kinds:
            kinds.append(kind)
    preferred = [kind for kind in ARMY_KIND_CODES if kind in kinds]
    extras = [kind for kind in kinds if kind not in preferred and kind not in ARMY_STRUCTURE_KINDS]
    return preferred + extras


def present_army_structures(battle: Any, player_id: int) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for unit in living_army_units(battle, player_id):
        kind = army_kind_for_unit(unit)
        if kind not in ARMY_STRUCTURE_KINDS:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return [
        {"kind": kind, "name": army_kind_label(kind), "count": count}
        for kind, count in counts.items()
        if count > 0
    ]


def apply_army_order(
    orders: dict[int, dict[str, dict[str, str]]],
    player_id: int,
    command: dict[str, str],
    *,
    kind: Optional[str] = None,
    kinds: Optional[Iterable[str]] = None,
) -> dict[str, dict[str, str]]:
    team_id = 1 if int(player_id) != 2 else 2
    team = {
        key: dict(value)
        for key, value in (orders.get(team_id) or {}).items()
        if isinstance(value, dict) and key not in {"order", "direction"}
    }
    targets = [kind] if kind else list(kinds or ARMY_KIND_CODES)
    for item in targets:
        if not item:
            continue
        team[str(item)] = dict(command)
    orders[team_id] = team
    return team


def command_hero_units(battle: Any, player_id: Optional[int] = None) -> list[Any]:
    players = (player_id,) if player_id is not None else (1, 2)
    heroes: list[Any] = []
    for current in players:
        heroes.extend(
            unit
            for unit in battle.player_units(current)
            if unit.alive and not unit.is_summon and not is_army_soldier(unit)
        )
    return heroes


def with_army_turn_slots(battle: Any, hero_unit_ids: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_id in hero_unit_ids:
        unit_id = str(raw_id)
        if parse_army_slot(unit_id) is not None:
            continue
        unit = battle.units.get(unit_id)
        if unit is not None and is_army_soldier(unit):
            continue
        if unit_id in seen:
            continue
        ordered.append(unit_id)
        seen.add(unit_id)
    for player_id in (1, 2):
        if any(is_army_soldier(unit) for unit in battle.player_units(player_id)):
            slot = army_slot_id(player_id)
            if slot not in seen:
                ordered.append(slot)
                seen.add(slot)
    return ordered


def army_counts_for_player(battle: Any, player_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for unit in living_army_units(battle, player_id):
        kind = army_kind_for_unit(unit)
        if kind is None:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def army_public_state(battle: Any) -> dict[str, Any]:
    orders = getattr(battle, "army_orders", None) or default_army_orders()
    public_orders: dict[int, dict[str, dict[str, str]]] = {}
    present: dict[int, list[dict[str, Any]]] = {}
    structures: dict[int, list[dict[str, Any]]] = {}
    for player_id in (1, 2):
        kinds = present_army_kinds(battle, player_id)
        counts = army_counts_for_player(battle, player_id)
        public_orders[player_id] = {kind: command_for_kind(orders, player_id, kind) for kind in kinds}
        present[player_id] = [
            {"kind": kind, "name": army_kind_label(kind), "count": int(counts.get(kind, 0))}
            for kind in kinds
        ]
        structures[player_id] = present_army_structures(battle, player_id)
    return {
        "is_army_turn": bool(getattr(battle, "is_army_turn", lambda: False)()),
        "army_turn_player_id": getattr(battle, "army_turn_player_id", lambda: None)(),
        "orders": public_orders,
        "has_army": {
            1: bool(living_army_units(battle, 1)),
            2: bool(living_army_units(battle, 2)),
        },
        "counts": {
            1: army_counts_for_player(battle, 1),
            2: army_counts_for_player(battle, 2),
        },
        "present_kinds": present,
        "structures": structures,
        "kinds": [
            {"kind": kind, "name": ARMY_KIND_LABELS[kind], "code": code}
            for kind, code in ARMY_KIND_CODES.items()
        ],
        "order_options": [
            {"code": code, "name": ARMY_ORDER_LABELS[code]}
            for code in ARMY_ORDERS
        ],
        "direction_options": [
            {"code": code, "name": ARMY_DIRECTION_LABELS[code]}
            for code in ARMY_DIRECTIONS
        ],
        "stride_options": [
            {"code": code, "name": ARMY_STRIDE_LABELS[code]}
            for code in ARMY_STRIDES
        ],
        "ammo_options": {
            player_id: list((getattr(battle, "siege_ammo_by_player", None) or {}).get(player_id) or cannon_ammo_options(0))
            for player_id in (1, 2)
        },
        "move_traces": list(getattr(battle, "army_move_traces", None) or []),
        "march_id": str(getattr(battle, "army_march_id", "") or ""),
    }


def _progress(position: Any, direction: tuple[int, int]) -> int:
    return int(position.x) * direction[0] + int(position.y) * direction[1]


def _vector_to_direction(dx: float, dy: float) -> str:
    sx = 0 if abs(dx) < 0.35 else (1 if dx > 0 else -1)
    sy = 0 if abs(dy) < 0.35 else (1 if dy > 0 else -1)
    if sx == 0 and sy == 0:
        return "W"
    for code, vector in ARMY_DIRECTIONS.items():
        if vector == (sx, sy):
            return code
    return "W"


def combat_enemies(battle: Any, player_id: int) -> list[Any]:
    from wujiang.tactical.engine.siege import is_siege_structure

    team_id = int(player_id)
    return [
        unit
        for unit in battle.all_units()
        if unit.alive
        and not unit.banished
        and unit.position is not None
        and int(unit.player_id) != team_id
        and not is_siege_structure(unit)
    ]


def _seek_direction_for_unit(battle: Any, unit: Any) -> tuple[int, int]:
    if unit is None or unit.position is None:
        return (0, 0)
    enemies = combat_enemies(battle, unit.player_id)
    if not enemies:
        return (0, 0)
    nearest = min(enemies, key=lambda enemy: battle.distance_between_units(unit, enemy))
    heading = _vector_to_direction(nearest.position.x - unit.position.x, nearest.position.y - unit.position.y)
    return ARMY_DIRECTIONS.get(heading, (0, 0))


def march_direction(command: dict[str, str]) -> tuple[int, int]:
    facing = ARMY_DIRECTIONS.get(str(command.get("direction") or ""), (0, 0))
    if command.get("order") == "retreat":
        return (-facing[0], -facing[1])
    return facing


def march_vector(battle: Any, unit: Any, command: dict[str, str]) -> tuple[int, int]:
    if command.get("order") == "seek":
        return _seek_direction_for_unit(battle, unit)
    return march_direction(command)


def apply_siege_defender_ai(battle: Any, player_id: int) -> None:
    if not bool(getattr(battle, "siege_defender_ai", False)) or int(player_id) != 2:
        return
    if not getattr(battle, "blocked_cells", None):
        return
    from wujiang.tactical.engine.siege import siege_profile_of

    defenders = living_army_units(battle, 2)
    enemies = [
        unit
        for unit in battle.player_units(1)
        if unit.alive and not unit.banished and unit.position is not None
    ]
    anchors = [unit for unit in defenders if bool(getattr(unit, "is_siege_structure", False))] or defenders
    engines = [
        unit
        for unit in enemies
        if (profile := siege_profile_of(unit)) is not None and profile.damages_structures
    ]
    at_the_wall = any(
        battle.distance_between_units(enemy, anchor) <= 3
        for enemy in enemies
        for anchor in anchors
    )
    if at_the_wall or not engines:
        command = {"order": "hold", "direction": "W", "stride": "full", "ammo": "shell"}
    else:
        threat = min(
            engines,
            key=lambda unit: min(battle.distance_between_units(unit, anchor) for anchor in anchors) if anchors else 0,
        )
        cx = sum(unit.position.x for unit in defenders) / max(1, len(defenders))
        cy = sum(unit.position.y for unit in defenders) / max(1, len(defenders))
        command = {
            "order": "advance",
            "direction": _vector_to_direction(threat.position.x - cx, threat.position.y - cy),
            "stride": "full",
        }
    for kind in present_army_kinds(battle, 2):
        if kind == "cannon":
            continue
        apply_army_order(battle.army_orders, 2, command, kind=kind)


def apply_cannon_creep_ai(battle: Any, player_id: int) -> None:
    team_id = 1 if int(player_id) != 2 else 2
    ai_players = {int(item) for item in (getattr(battle, "army_ai_players", None) or ())}
    if team_id not in ai_players and not (bool(getattr(battle, "siege_defender_ai", False)) and team_id == 2):
        return
    cannons = [unit for unit in living_army_units(battle, team_id) if army_kind_for_unit(unit) == "cannon"]
    if not cannons:
        return
    enemies = [
        unit
        for unit in battle.all_units()
        if unit.alive and not unit.banished and unit.position is not None and int(unit.player_id) != team_id
    ]
    if not enemies:
        return
    orders = getattr(battle, "army_orders", None) or default_army_orders()
    current = command_for_kind(orders, team_id, "cannon")
    cx = sum(unit.position.x for unit in cannons) / len(cannons)
    cy = sum(unit.position.y for unit in cannons) / len(cannons)
    nearest = min(enemies, key=lambda unit: min(battle.distance_between_units(cannon, unit) for cannon in cannons))
    heading = _vector_to_direction(nearest.position.x - cx, nearest.position.y - cy)
    min_distance = min(battle.distance_between_units(cannon, nearest) for cannon in cannons)
    any_loaded = any(bool(getattr(unit, "siege_loaded", False)) for unit in cannons)
    from wujiang.tactical.engine.siege import siege_attack_in_range

    can_fire = any(
        siege_attack_in_range(battle, cannon, enemy)
        for cannon in cannons
        for enemy in enemies
    )
    if min_distance < 2:
        order = "retreat"
    elif can_fire or not any_loaded:
        order = "hold"
    else:
        order = "advance"
    apply_army_order(
        battle.army_orders,
        team_id,
        {
            "order": order,
            "direction": heading if order != "hold" else (current.get("direction") or heading),
            "stride": "step",
            "ammo": current.get("ammo") or "shell",
        },
        kind="cannon",
    )


def apply_army_style_ai(battle: Any, player_id: int) -> None:
    team_id = 1 if int(player_id) != 2 else 2
    ai_players = {int(item) for item in (getattr(battle, "army_ai_players", None) or ())}
    if team_id not in ai_players:
        return
    styles = getattr(battle, "army_ai_styles", None) or {}
    style = str(styles.get(team_id) or styles.get(str(team_id)) or "seek")
    if style == "seek":
        apply_army_seek_ai(battle, player_id)
        return
    kinds = [kind for kind in present_army_kinds(battle, team_id) if kind not in {"cannon", *ARMY_STRUCTURE_KINDS}]
    if not kinds:
        return
    command = {
        "order": "hold" if style == "hold" else "advance",
        "direction": DEFAULT_ARMY_DIRECTION_BY_PLAYER.get(team_id, "E"),
        "stride": "full",
    }
    for kind in kinds:
        apply_army_order(battle.army_orders, team_id, command, kind=kind)


def apply_army_seek_ai(battle: Any, player_id: int) -> None:
    team_id = 1 if int(player_id) != 2 else 2
    ai_players = {int(item) for item in (getattr(battle, "army_ai_players", None) or ())}
    if team_id not in ai_players:
        return
    kinds = [kind for kind in present_army_kinds(battle, team_id) if kind not in {"cannon", *ARMY_STRUCTURE_KINDS}]
    if not kinds:
        return
    command = {"order": "seek", "direction": DEFAULT_ARMY_DIRECTION_BY_PLAYER.get(team_id, "E"), "stride": "full"}
    for kind in kinds:
        apply_army_order(battle.army_orders, team_id, command, kind=kind)


def _step_candidates(battle: Any, unit: Any, direction: tuple[int, int]) -> list[Any]:
    origin = unit.position
    scored: list[tuple[int, int, Any]] = []
    for neighbor in battle.neighbors(origin):
        dx = neighbor.x - origin.x
        dy = neighbor.y - origin.y
        alignment = dx * direction[0] + dy * direction[1]
        if alignment < 0:
            continue
        scored.append((alignment, -abs(dx - direction[0]) - abs(dy - direction[1]), neighbor))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2].y, item[2].x))
    return [cell for _align, _near, cell in scored]


def _prepare_siege_reload_state(unit: Any) -> None:
    if not bool(getattr(unit, "siege_reload_cycle", False)):
        return
    if str(getattr(unit, "siege_reload_state", "") or "") == "loading" and bool(getattr(unit, "siege_loaded", False)):
        unit.siege_reload_state = "ready"


def _move_soldier(battle: Any, unit: Any, command: dict[str, str]) -> list[Any]:
    if command.get("order") == "hold" or unit.cannot_move or unit.cannot_normal_move:
        return []
    direction = march_vector(battle, unit, command)
    if direction == (0, 0) or unit.position is None:
        return []
    steps = 1 if command.get("stride") == "step" else max(0, int(unit.stat("speed")))
    path = [unit.position]
    with battle.suppress_logs():
        for _ in range(steps):
            destination = None
            for candidate in _step_candidates(battle, unit, direction):
                if battle.can_place_unit(unit, candidate, ignore=unit, mover=unit):
                    destination = candidate
                    break
            if destination is None:
                break
            battle.move_unit(unit, destination, forced=True, max_distance=1)
            if unit.position is not None:
                path.append(unit.position)
    return path


def _choose_army_target(battle: Any, unit: Any) -> Any:
    from wujiang.tactical.engine.siege import (
        is_siege_structure,
        siege_attack_in_range,
        siege_min_attack_range,
        source_can_damage_siege_structure,
    )

    reach = unit.targeting_range()
    min_range = siege_min_attack_range(unit)
    candidates = []
    for target in battle.all_units():
        if not target.alive or target.banished or target.position is None:
            continue
        if not unit.is_enemy_of(target):
            continue
        if is_siege_structure(target) and not source_can_damage_siege_structure(unit, target):
            continue
        if not battle.unit_target_in_range_and_line(unit, target, reach):
            continue
        if not siege_attack_in_range(battle, unit, target):
            continue
        distance = min(
            origin.distance_to(cell)
            for origin in battle.unit_cells(unit)
            for cell in battle.unit_cells(target)
        )
        if min_range and distance < min_range:
            continue
        candidates.append((distance, float(target.current_hp), target.unit_id, target))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][3]


def _cannon_splash_for_unit(battle: Any, unit: Any) -> int:
    from wujiang.tactical.engine.siege import siege_profile_of

    kind = army_kind_for_unit(unit)
    if kind != "cannon":
        profile = siege_profile_of(unit)
        return int(profile.splash_radius) if profile is not None else 0
    orders = getattr(battle, "army_orders", None) or default_army_orders()
    command = command_for_kind(orders, unit.player_id, "cannon")
    ammo = normalize_cannon_ammo(command.get("ammo") or "shell")
    return int(CANNON_AMMO[ammo]["splash"])


def _attack_with_soldier(battle: Any, unit: Any) -> bool:
    from wujiang.tactical.engine.siege import (
        blast_cells,
        closest_cell_pair,
        emit_siege_visual_event,
        is_siege_structure,
        siege_profile_of,
        siege_shell_payload,
        structure_hit_by_impact,
    )

    if unit.cannot_attack or not unit.alive or unit.banished or unit.position is None:
        return False
    if bool(getattr(unit, "siege_reload_cycle", False)) and not bool(getattr(unit, "siege_loaded", False)):
        unit.siege_loaded = True
        unit.siege_reload_state = "loading"
        battle.log(f"{unit.name} \u5b8c\u6210\u88c5\u586b\u3002")
        return False
    target = _choose_army_target(battle, unit)
    if target is None:
        return False
    profile = siege_profile_of(unit)
    splash = _cannon_splash_for_unit(battle, unit)
    source, impact = closest_cell_pair(battle, unit, target)
    cells = blast_cells(battle, impact, splash)
    try:
        if profile is not None and (profile.family == "cannon" or splash > 0):
            battle.basic_area_attack_with_payload(
                unit,
                siege_shell_payload(
                    unit,
                    cells,
                    impact,
                    splash_radius=splash,
                    attack_name=profile.attack_name if profile is not None else "炮击",
                ),
                cells,
            )
        else:
            battle.basic_attack(unit, target)
            if profile is None:
                battle.record_visual_event(
                    kind="attack",
                    display_name="攻击",
                    actor=unit,
                    action_type="attack",
                    action_code="army_strike",
                    target_unit_ids=[target.unit_id],
                    target_cells=list(battle.unit_cells(target)),
                    source_cell=source,
                )
    except Exception:
        return False
    if bool(getattr(unit, "siege_reload_cycle", False)):
        unit.siege_loaded = False
        unit.siege_reload_state = "empty"
    if profile is not None:
        hit_ids = []
        for other in battle.effect_units_at_cells(cells):
            if is_siege_structure(other) and not structure_hit_by_impact(battle, other, impact):
                continue
            hit_ids.append(other.unit_id)
        if not hit_ids:
            hit_ids = [target.unit_id]
        emit_siege_visual_event(battle, unit, target, cells, hit_ids, source=source, impact=impact)
    battle.cleanup_dead_units()
    return True


def resolve_army_phase(battle: Any, player_id: int) -> dict[str, int]:
    apply_siege_defender_ai(battle, player_id)
    apply_cannon_creep_ai(battle, player_id)
    apply_army_style_ai(battle, player_id)
    soldiers = living_army_units(battle, player_id)
    orders = getattr(battle, "army_orders", None) or default_army_orders()
    summaries: list[str] = []
    for kind in present_army_kinds(battle, player_id):
        command = command_for_kind(orders, player_id, kind)
        order_name = ARMY_ORDER_LABELS.get(command.get("order", "hold"), "\u56fa\u5b88")
        direction_name = ARMY_DIRECTION_LABELS.get(command.get("direction", ""), "")
        heading = f"\uff0c\u65b9\u5411{direction_name}" if command.get("order") != "hold" and direction_name else ""
        summaries.append(f"{army_kind_label(kind)}{order_name}{heading}")
    battle.active_player = int(player_id)
    detail = "\uff1b".join(summaries) if summaries else "\u65e0\u58eb\u5175"
    battle.log(f"\u73a9\u5bb6 {player_id} \u7684\u519b\u961f\u56de\u5408\uff1a{detail}\u3002")
    if not soldiers:
        return {"moved": 0, "attacked": 0}
    for unit in soldiers:
        unit.refresh_for_turn(battle)
        _prepare_siege_reload_state(unit)
    moved = 0
    marching = []
    for unit in soldiers:
        kind = army_kind_for_unit(unit) or "infantry"
        command = command_for_kind(orders, player_id, kind)
        if command.get("order") == "hold":
            continue
        direction = march_vector(battle, unit, command)
        if direction == (0, 0):
            continue
        marching.append((unit, command, direction))
    marching.sort(
        key=lambda item: (
            -_progress(item[0].position, item[2]),
            item[0].position.y,
            item[0].position.x,
            item[0].unit_id,
        )
    )
    traces: list[dict[str, Any]] = []
    for unit, command, _direction in marching:
        if not unit.alive or unit.banished or unit.position is None:
            continue
        path = _move_soldier(battle, unit, command)
        if len(path) > 1:
            moved += 1
            traces.append(
                {
                    "unit_id": unit.unit_id,
                    "path": [{"x": int(cell.x), "y": int(cell.y)} for cell in path],
                }
            )
    battle.army_move_traces = traces
    battle.army_march_id = f"{int(getattr(battle, 'completed_turns', 0) or 0)}:{int(player_id)}:{moved}"
    attacked = 0
    strikers = living_army_units(battle, player_id)
    strikers.sort(
        key=lambda unit: (
            ARMY_STRIKE_WAVES.index(army_strike_wave(unit)) if army_strike_wave(unit) in ARMY_STRIKE_WAVES else 1,
            unit.position.y if unit.position is not None else 0,
            unit.position.x if unit.position is not None else 0,
            unit.unit_id,
        )
    )
    for unit in strikers:
        if battle.winner is not None:
            break
        battle.army_strike_wave = army_strike_wave(unit)
        if _attack_with_soldier(battle, unit):
            attacked += 1
    battle.army_strike_wave = ""
    for unit in living_army_units(battle, player_id):
        unit.finish_turn(battle)
    battle.log(f"\u519b\u961f\u884c\u52a8\u5b8c\u6bd5\uff1a{moved} \u4eba\u79fb\u52a8\uff0c{attacked} \u4eba\u51fa\u624b\u3002")
    battle.check_win_condition()
    return {"moved": moved, "attacked": attacked}
