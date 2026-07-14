from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wujiang.heroes.registry import HERO_REGISTRY, list_heroes
from wujiang.strategy.heroes import normalize_strategic_hero_deployment, strategic_defender_hero_codes_for_faction
from wujiang.strategy.models import City, Faction, PendingBattle, WorldState
from wujiang.strategy.tactics import city_troop_conversion


TROOPS_PER_GRID_UNIT = 100
MAX_GRID_UNITS_PER_SIDE = 12
MIN_FEATURED_UNITS_ROSTER_SIZE = 2

STRATEGY_UNIT_HERO_CODES: dict[str, str] = {
    "infantry": "strategy_infantry",
    "archer": "strategy_archer",
    "cavalry": "strategy_cavalry",
    "普通步兵": "strategy_infantry",
    "守备兵": "strategy_garrison",
    "弓兵": "strategy_archer",
    "骑兵": "strategy_cavalry",
    "山地兵": "strategy_mountain_soldier",
    "以太侦察兵": "strategy_ether_scout",
    "城墙工兵": "strategy_wall_engineer",
}
REGISTERED_UNIT_LABELS = {"infantry": "步兵", "archer": "弓兵", "cavalry": "骑兵"}
DEFAULT_STRATEGY_UNIT_HERO_CODE = "strategy_infantry"


@dataclass(frozen=True, slots=True)
class StrategyBattleRoster:
    roster: list[str]
    manifest: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class StrategyBattleRosters:
    attacker: StrategyBattleRoster
    defender: StrategyBattleRoster


def _available_hero_codes() -> set[str]:
    return set(HERO_REGISTRY)


def _public_hero_names_by_code() -> dict[str, str]:
    return {str(hero.get("code") or ""): str(hero.get("name") or hero.get("code") or "") for hero in list_heroes()}


def _hero_code_for_unit_type(unit_type: str, available_codes: set[str]) -> str:
    mapped = STRATEGY_UNIT_HERO_CODES.get(unit_type, DEFAULT_STRATEGY_UNIT_HERO_CODE)
    if mapped in available_codes:
        return mapped
    if DEFAULT_STRATEGY_UNIT_HERO_CODE in available_codes:
        return DEFAULT_STRATEGY_UNIT_HERO_CODE
    return sorted(available_codes)[0]


def _grid_unit_count(troop_count: int) -> int:
    troops = max(0, int(troop_count))
    if troops <= 0:
        return 0
    return max(1, min(MAX_GRID_UNITS_PER_SIDE, (troops + TROOPS_PER_GRID_UNIT - 1) // TROOPS_PER_GRID_UNIT))


def _city_with_troops(city: City, troop_count: int) -> City:
    raw = city.to_dict()
    resources = dict(raw["resources"])
    resources["troops"] = max(0, int(troop_count))
    raw["resources"] = resources
    return City.from_dict(raw)


def _allocate_units(rows: list[dict[str, Any]], total_units: int) -> list[int]:
    if total_units <= 0 or not rows:
        return []
    exacts = [total_units * max(0, int(row.get("ratio", 0))) / 100 for row in rows]
    allocations = [int(value) for value in exacts]
    while sum(allocations) < total_units:
        candidates = sorted(
            range(len(rows)),
            key=lambda index: (
                exacts[index] - allocations[index],
                int(rows[index].get("ratio", 0)),
                -index,
            ),
            reverse=True,
        )
        allocations[candidates[0]] += 1

    if total_units >= MIN_FEATURED_UNITS_ROSTER_SIZE:
        featured_indexes = [
            index
            for index, row in enumerate(rows)
            if row.get("source") == "city_feature" and int(row.get("ratio", 0)) > 0
        ]
        for featured_index in featured_indexes:
            if allocations[featured_index] > 0:
                continue
            donor_indexes = sorted(
                [
                    index
                    for index, count in enumerate(allocations)
                    if index != featured_index and count > 1
                ],
                key=lambda index: (
                    rows[index].get("source") == "default",
                    allocations[index],
                    int(rows[index].get("ratio", 0)),
                ),
                reverse=True,
            )
            if donor_indexes:
                allocations[donor_indexes[0]] -= 1
                allocations[featured_index] += 1
    return allocations


def roster_for_city_troops(
    city: City,
    faction: Faction,
    *,
    troop_count: int,
    available_hero_codes: set[str] | None = None,
) -> StrategyBattleRoster:
    available_codes = available_hero_codes or _available_hero_codes()
    if not available_codes:
        raise ValueError("Strategy battle roster requires at least one registered hero.")
    total_units = _grid_unit_count(troop_count)
    if total_units <= 0:
        return StrategyBattleRoster(roster=[], manifest=[])

    conversion_rows = city_troop_conversion(_city_with_troops(city, troop_count), faction)
    allocations = _allocate_units(conversion_rows, total_units)
    roster: list[str] = []
    manifest: list[dict[str, Any]] = []
    for row, unit_count in zip(conversion_rows, allocations):
        if unit_count <= 0:
            continue
        unit_type = str(row.get("unit_type") or "")
        hero_code = _hero_code_for_unit_type(unit_type, available_codes)
        roster.extend([hero_code] * unit_count)
        manifest.append(
            {
                "unit_type": unit_type,
                "source": str(row.get("source") or ""),
                "ratio": int(row.get("ratio", 0)),
                "troops": int(row.get("troops", 0)),
                "grid_units": unit_count,
                "hero_code": hero_code,
            }
        )
    return StrategyBattleRoster(roster=roster, manifest=manifest)


def roster_for_registered_units(
    registered_units: dict[str, int],
    *,
    available_hero_codes: set[str] | None = None,
    limit: int = MAX_GRID_UNITS_PER_SIDE,
) -> StrategyBattleRoster:
    available_codes = available_hero_codes or _available_hero_codes()
    roster: list[str] = []
    manifest: list[dict[str, Any]] = []
    remaining = max(0, int(limit))
    for unit_type in ("cavalry", "archer", "infantry"):
        count = min(remaining, max(0, int(registered_units.get(unit_type, 0))))
        if count <= 0:
            continue
        hero_code = _hero_code_for_unit_type(unit_type, available_codes)
        roster.extend([hero_code] * count)
        manifest.append(
            {
                "unit_type": REGISTERED_UNIT_LABELS[unit_type],
                "unit_id": unit_type,
                "source": "registered_unit",
                "ratio": 0,
                "troops": 0,
                "grid_units": count,
                "hero_code": hero_code,
            }
        )
        remaining -= count
        if remaining <= 0:
            break
    return StrategyBattleRoster(roster=roster, manifest=manifest)


def _merge_rosters(primary: StrategyBattleRoster, fallback: StrategyBattleRoster) -> StrategyBattleRoster:
    remaining = max(0, MAX_GRID_UNITS_PER_SIDE - len(primary.roster))
    if remaining <= 0:
        return primary
    fallback_roster = fallback.roster[:remaining]
    manifest = list(primary.manifest)
    left = len(fallback_roster)
    for row in fallback.manifest:
        if left <= 0:
            break
        used = min(left, int(row.get("grid_units", 0)))
        if used > 0:
            next_row = dict(row)
            next_row["grid_units"] = used
            manifest.append(next_row)
            left -= used
    return StrategyBattleRoster(roster=[*primary.roster, *fallback_roster], manifest=manifest)


def _with_active_strategic_heroes(
    roster: StrategyBattleRoster,
    *,
    world: WorldState,
    faction_id: str,
    hero_codes: list[str] | tuple[str, ...] | set[str] | None,
    available_hero_codes: set[str],
) -> StrategyBattleRoster:
    hero_names = _public_hero_names_by_code()
    hero_codes = [
        code
        for code in normalize_strategic_hero_deployment(world, faction_id, hero_codes)
        if code in available_hero_codes
    ]
    if not hero_codes:
        return roster
    next_roster = list(roster.roster)
    next_manifest = list(roster.manifest)
    for code in hero_codes:
        next_roster.append(code)
        next_manifest.append(
            {
                "unit_type": hero_names.get(code, code),
                "source": "strategic_hero",
                "ratio": 0,
                "troops": 0,
                "grid_units": 1,
                "hero_code": code,
            }
        )
    return StrategyBattleRoster(roster=next_roster, manifest=next_manifest)


def strategy_battle_rosters(world: WorldState, battle: PendingBattle) -> StrategyBattleRosters:
    cities_by_id = {city.city_id: city for city in world.cities}
    factions_by_id = {faction.faction_id: faction for faction in world.factions}
    source_city = cities_by_id[battle.source_city_id]
    target_city = cities_by_id[battle.target_city_id]
    attacker_faction = factions_by_id[battle.attacker_faction_id]
    defender_faction = factions_by_id[battle.defender_faction_id]
    available_codes = _available_hero_codes()
    attacker_roster = _merge_rosters(
        roster_for_registered_units(battle.attacker_registered_units, available_hero_codes=available_codes),
        roster_for_city_troops(
            source_city,
            attacker_faction,
            troop_count=battle.attacker_troops,
            available_hero_codes=available_codes,
        ),
    )
    defender_roster = _merge_rosters(
        roster_for_registered_units(battle.defender_registered_units, available_hero_codes=available_codes),
        roster_for_city_troops(
            target_city,
            defender_faction,
            troop_count=battle.defender_troops,
            available_hero_codes=available_codes,
        ),
    )
    return StrategyBattleRosters(
        attacker=_with_active_strategic_heroes(
            attacker_roster,
            world=world,
            faction_id=battle.attacker_faction_id,
            hero_codes=battle.attacker_hero_codes,
            available_hero_codes=available_codes,
        ),
        defender=_with_active_strategic_heroes(
            defender_roster,
            world=world,
            faction_id=battle.defender_faction_id,
            hero_codes=(
                strategic_defender_hero_codes_for_faction(world, battle.defender_faction_id)
                if battle.defender_hero_codes is None
                else battle.defender_hero_codes
            ),
            available_hero_codes=available_codes,
        ),
    )
