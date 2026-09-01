from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from wujiang.tactical.engine.core import Position, Stats


@dataclass(frozen=True, slots=True)
class SiegeProfile:
    profile_id: str
    family: str
    tier: int
    name: str
    attack: int
    defense: int
    speed: int
    attack_range: int
    max_health: float
    splash_radius: int
    footprint_width: int
    footprint_height: int
    all_around: bool
    reload_cycle: bool
    is_structure: bool
    cannot_move: bool
    damages_structures: bool
    vfx_style: str
    sound: str
    vfx_duration_ms: int
    attack_name: str


SIEGE_PROFILES: dict[str, SiegeProfile] = {
    "cannon_1": SiegeProfile(
        profile_id="cannon_1",
        family="cannon",
        tier=1,
        name="\u706b\u70ae",
        attack=3,
        defense=2,
        speed=1,
        attack_range=8,
        max_health=4.0,
        splash_radius=0,
        footprint_width=2,
        footprint_height=2,
        all_around=True,
        reload_cycle=True,
        is_structure=False,
        cannot_move=False,
        damages_structures=True,
        vfx_style="shell",
        sound="cannon",
        vfx_duration_ms=1100,
        attack_name="\u70ae\u51fb",
    ),
    "cannon_2": SiegeProfile(
        profile_id="cannon_2",
        family="cannon",
        tier=2,
        name="\u91cd\u70ae",
        attack=4,
        defense=2,
        speed=1,
        attack_range=10,
        max_health=5.0,
        splash_radius=1,
        footprint_width=2,
        footprint_height=2,
        all_around=True,
        reload_cycle=True,
        is_structure=False,
        cannot_move=False,
        damages_structures=True,
        vfx_style="shell",
        sound="cannon",
        vfx_duration_ms=1200,
        attack_name="\u91cd\u70ae\u51fb",
    ),
    "arrow_tower_1": SiegeProfile(
        profile_id="arrow_tower_1",
        family="tower",
        tier=1,
        name="\u7bad\u5854",
        attack=3,
        defense=5,
        speed=0,
        attack_range=5,
        max_health=2.0,
        splash_radius=0,
        footprint_width=1,
        footprint_height=1,
        all_around=True,
        reload_cycle=False,
        is_structure=True,
        cannot_move=True,
        damages_structures=False,
        vfx_style="bolt",
        sound="tower",
        vfx_duration_ms=720,
        attack_name="\u7bad\u51fb",
    ),
    "cannon_tower_1": SiegeProfile(
        profile_id="cannon_tower_1",
        family="tower",
        tier=2,
        name="\u70ae\u5854",
        attack=4,
        defense=4,
        speed=0,
        attack_range=7,
        max_health=8.0,
        splash_radius=1,
        footprint_width=1,
        footprint_height=1,
        all_around=True,
        reload_cycle=True,
        is_structure=True,
        cannot_move=True,
        damages_structures=True,
        vfx_style="shell",
        sound="cannon",
        vfx_duration_ms=1000,
        attack_name="\u70ae\u5854\u8f70\u51fb",
    ),
}

DEFAULT_PROFILE_BY_HERO = {
    "strategy_cannon": "cannon_1",
    "strategy_arrow_tower": "arrow_tower_1",
}


def siege_profile_by_id(profile_id: Any) -> Optional[SiegeProfile]:
    key = str(profile_id or "").strip()
    return SIEGE_PROFILES.get(key)


def siege_profile_of(unit: Any) -> Optional[SiegeProfile]:
    if unit is None:
        return None
    direct = siege_profile_by_id(getattr(unit, "siege_profile_id", ""))
    if direct is not None:
        return direct
    return siege_profile_by_id(DEFAULT_PROFILE_BY_HERO.get(str(getattr(unit, "hero_code", "") or "")))


def siege_stats(profile: SiegeProfile) -> Stats:
    return Stats(
        attack=profile.attack,
        defense=profile.defense,
        speed=profile.speed,
        attack_range=profile.attack_range,
        mana=0,
    )


def apply_siege_profile(unit: Any, profile_id: str, *, restore_hp: bool = False) -> SiegeProfile:
    profile = siege_profile_by_id(profile_id)
    if profile is None:
        raise KeyError(f"unknown siege profile: {profile_id}")
    old_max = float(getattr(unit, "max_health", profile.max_health) or profile.max_health)
    old_hp = float(getattr(unit, "current_hp", profile.max_health) or profile.max_health)
    unit.siege_profile_id = profile.profile_id
    unit.siege_family = profile.family
    unit.siege_tier = profile.tier
    unit.splash_radius = profile.splash_radius
    unit.name = profile.name
    unit.hero_name = profile.name
    unit.base_stats = siege_stats(profile)
    unit.max_health = profile.max_health
    if restore_hp or old_max <= 0:
        unit.current_hp = profile.max_health
    else:
        unit.current_hp = min(profile.max_health, round(profile.max_health * (old_hp / old_max), 4))
    unit.footprint_width = profile.footprint_width
    unit.footprint_height = profile.footprint_height
    unit.entry_footprint_width = profile.footprint_width
    unit.entry_footprint_height = profile.footprint_height
    unit.cannot_move = profile.cannot_move
    unit.cannot_normal_move = profile.cannot_move
    unit.is_siege_structure = profile.is_structure
    if profile.family == "tower" and not profile.damages_structures:
        unit.physical_immunity = True
    unit.siege_reload_cycle = profile.reload_cycle
    if profile.reload_cycle and not hasattr(unit, "siege_loaded"):
        unit.siege_loaded = False
    if profile.reload_cycle and not getattr(unit, "siege_reload_state", ""):
        unit.siege_reload_state = "ready" if bool(getattr(unit, "siege_loaded", False)) else "empty"
    if hasattr(unit, "_normalize_footprint_offsets") and hasattr(unit, "_refresh_footprint_bounds"):
        offsets = [
            (dx, dy)
            for dx in range(profile.footprint_width)
            for dy in range(profile.footprint_height)
        ]
        unit.base_footprint_offsets = unit._normalize_footprint_offsets(offsets)
        unit.footprint_offsets = unit._normalize_footprint_offsets(unit.base_footprint_offsets)
        unit._refresh_footprint_bounds()
    return profile


def source_can_damage_siege_structure(source: Any, target: Any) -> bool:
    target_profile = siege_profile_of(target)
    if target_profile is None or not target_profile.is_structure:
        return True
    source_profile = siege_profile_of(source)
    return bool(source_profile and source_profile.damages_structures)


def closest_cell_pair(battle: Any, actor: Any, target: Any) -> tuple[Position, Position]:
    pairs: list[tuple[int, Position, Position]] = []
    for origin in battle.unit_cells(actor):
        for cell in battle.unit_cells(target):
            pairs.append((origin.distance_to(cell), origin, cell))
    pairs.sort(key=lambda item: (item[0], item[1].y, item[1].x, item[2].y, item[2].x))
    return pairs[0][1], pairs[0][2]


def blast_cells(battle: Any, center: Position, radius: int) -> list[Position]:
    reach = max(0, int(radius))
    cells: list[Position] = []
    for y in range(center.y - reach, center.y + reach + 1):
        for x in range(center.x - reach, center.x + reach + 1):
            if x < 0 or y < 0 or x >= battle.width or y >= battle.height:
                continue
            if max(abs(x - center.x), abs(y - center.y)) > reach:
                continue
            cells.append(Position(x, y))
    return cells


def emit_siege_visual_event(
    battle: Any,
    unit: Any,
    target: Any,
    cells: list[Position],
    target_ids: list[str],
    source: Position | None = None,
    impact: Position | None = None,
) -> None:
    profile = siege_profile_of(unit)
    if profile is None or unit.position is None:
        return
    if source is None or impact is None:
        if target is not None and target.position is not None:
            source, impact = closest_cell_pair(battle, unit, target)
        elif cells:
            source = unit.position
            impact = cells[0]
        else:
            return
    blast = cells or [impact]
    battle.record_visual_event(
        kind="attack",
        display_name=profile.attack_name,
        actor=unit,
        action_type="attack",
        action_code=f"siege_{profile.vfx_style}",
        target_unit_ids=list(target_ids),
        target_cells=list(blast),
        source_cell=source,
        metadata={
            "vfx_style": profile.vfx_style,
            "sound": profile.sound,
            "splash_radius": profile.splash_radius,
            "siege_family": profile.family,
            "siege_tier": profile.tier,
            "siege_profile_id": profile.profile_id,
            "impact_cell": impact.to_dict(),
            "duration_ms": profile.vfx_duration_ms,
        },
    )


def is_siege_structure(unit: Any) -> bool:
    profile = siege_profile_of(unit)
    return bool(profile and profile.is_structure) or bool(getattr(unit, "is_siege_structure", False))


def siege_min_attack_range(unit: Any) -> int:
    profile = siege_profile_of(unit)
    if profile is not None and profile.family == "cannon":
        return 2
    return 0


def siege_attack_in_range(battle: Any, actor: Any, target: Any) -> bool:
    reach = actor.targeting_range()
    distance = battle.distance_between_units(actor, target)
    if distance > reach:
        return False
    return distance >= siege_min_attack_range(actor)


def apply_siege_tech_bonuses(unit: Any, bonuses: dict[str, int] | None) -> None:
    profile = siege_profile_of(unit)
    if profile is None or not bonuses:
        return
    stats = unit.base_stats
    if profile.family == "cannon":
        unit.base_stats = Stats(
            attack=stats.attack + int(bonuses.get("cannon_attack", 0) or 0),
            defense=stats.defense,
            speed=stats.speed,
            attack_range=stats.attack_range + int(bonuses.get("cannon_range", 0) or 0),
            mana=stats.mana,
        )
        return
    if profile.family == "tower":
        unit.base_stats = Stats(
            attack=stats.attack + int(bonuses.get("tower_attack", 0) or 0),
            defense=stats.defense + int(bonuses.get("tower_defense", 0) or 0),
            speed=stats.speed,
            attack_range=stats.attack_range + int(bonuses.get("tower_range", 0) or 0),
            mana=stats.mana,
        )


def siege_shell_payload(
    unit: Any,
    cells: list[Position],
    impact: Position,
    *,
    splash_radius: int = 0,
    attack_name: str = "",
) -> dict[str, Any]:
    profile = siege_profile_of(unit)
    return {
        "attack_name": attack_name or (profile.attack_name if profile is not None else "\u70ae\u51fb"),
        "attack_cells": [cell.to_dict() for cell in cells],
        "friendly_fire": True,
        "ignore_physical_immunity": True,
        "ignore_magic_immunity": True,
        "siege_shell": True,
        "structures_need_direct_hit": True,
        "impact_cell": impact.to_dict(),
        "splash_radius": int(splash_radius),
    }


def structure_hit_by_impact(battle: Any, unit: Any, impact: Position | None) -> bool:
    if impact is None:
        return False
    return impact in battle.unit_cells(unit)


def siege_public_dict(unit: Any) -> dict[str, Any]:
    profile = siege_profile_of(unit)
    if profile is None:
        return {}
    return {
        "siege_profile_id": profile.profile_id,
        "siege_family": profile.family,
        "siege_tier": profile.tier,
        "splash_radius": profile.splash_radius,
        "vfx_style": profile.vfx_style,
        "damages_structures": profile.damages_structures,
    }