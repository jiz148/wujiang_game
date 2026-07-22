from __future__ import annotations

import copy
import hashlib
from typing import Any

from wujiang.heroes.registry import list_heroes
from wujiang.strategy.models import (
    EventLogEntry,
    Faction,
    HeroRecruitment,
    ResourceBundle,
    StrategicHeroState,
    StrategyError,
    WorldState,
)


SUMMONED_TAG_PREFIX = "strategic_hero_summoned:"
SLEEPING_TAG_PREFIX = "strategic_hero_sleeping:"
SLEEPING_TAG_SEPARATOR = ":until:"
DEFENDER_TAG_PREFIX = "strategic_hero_defender:"
STRATEGIC_HERO_BATTLE_SLEEP_MONTHS = 2
STRATEGIC_HERO_BATTLE_LIMIT = 1
STRATEGIC_HERO_BATTLE_LIMIT_MAX = 3
STRATEGIC_HERO_LIMIT_TECH_BONUSES = {"hero_command": 1}
HERO_RITUAL_ETHER_COST = 30


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _faction(world: WorldState, faction_id: str) -> Faction:
    for faction in world.factions:
        if faction.faction_id == faction_id:
            return faction
    raise StrategyError("势力不存在。")


def _base_hero_pool() -> list[dict[str, Any]]:
    return sorted(list_heroes(), key=lambda hero: str(hero.get("code") or ""))


def strategic_hero_summon_cost(hero: dict[str, Any]) -> int:
    return 20 + int(hero.get("level", 1) or 1) * 5


def strategic_hero_home_faction_id(world: WorldState, hero_code: str) -> str:
    state = next((item for item in world.strategic_heroes if item.hero_code == str(hero_code)), None)
    return str(state.faction_id or "") if state is not None else ""


def _stable_index(world: WorldState, key: str, count: int) -> int:
    if count <= 0:
        return 0
    digest = hashlib.sha256(f"{world.seed}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % count


def ensure_strategic_hero_system(world: WorldState, members: Any = None) -> WorldState:
    next_world = _clone_world(world)
    initializing_hero_system = not next_world.strategic_heroes
    heroes_by_code = {hero.hero_code: hero for hero in next_world.strategic_heroes}
    cities = sorted(next_world.cities, key=lambda city: city.city_id)
    city_ids = {city.city_id for city in cities}
    mobile_commander_codes = {
        army.commander_hero_code for army in next_world.armies if army.status != "disbanded"
    }
    public_heroes = _base_hero_pool()

    for hero in public_heroes:
        code = str(hero.get("code") or "")
        if code in heroes_by_code:
            continue
        city = cities[_stable_index(next_world, f"roaming:{code}", len(cities))] if cities else None
        heroes_by_code[code] = StrategicHeroState(
            hero_code=code,
            status="roaming",
            city_id=city.city_id if city is not None else None,
            loyalty=35 + _stable_index(next_world, f"loyalty:{code}", 46),
        )

    # Migrate old save tags. They remain readable, but all new logic uses the
    # persistent hero state instead of faction-wide summon ownership.
    for faction in next_world.factions:
        sleeping = _sleeping_until_by_code(faction)
        for code in _summoned_codes(faction):
            state = heroes_by_code.get(code)
            if state is None:
                continue
            state.faction_id = faction.faction_id
            if state.hero_code not in mobile_commander_codes or state.city_id not in city_ids:
                state.city_id = faction.capital_city_id
            state.ritual_city_id = state.ritual_city_id or faction.capital_city_id
            state.status = "sleeping" if sleeping.get(code, 0) > next_world.current_month else "serving"
            state.sleeping_until_month = sleeping.get(code) or None

    for state in heroes_by_code.values():
        if state.status == "sleeping" and int(state.sleeping_until_month or 0) <= next_world.current_month:
            state.status = "serving" if state.faction_id else "roaming"
            state.sleeping_until_month = None

    member_by_faction = {
        str(getattr(member, "faction_id", "")): member
        for member in (members or ())
        if str(getattr(member, "role", "")).lower() != "ai" and int(getattr(member, "user_id", 0)) > 0
    }
    controlled_player_user_ids = {
        int(hero.controller_user_id)
        for hero in heroes_by_code.values()
        if hero.controller_type == "player" and hero.controller_user_id is not None
    }
    assigned_codes = {hero.hero_code for hero in heroes_by_code.values() if hero.office_id}
    available_codes = [str(hero.get("code") or "") for hero in public_heroes if str(hero.get("code") or "") not in assigned_codes]

    office_rank = {"lord": 0, "grand_general": 1, "general": 2, "governor": 3}
    for faction in sorted(next_world.factions, key=lambda item: item.faction_id):
        if faction.is_neutral_city_state:
            continue
        founded_during_campaign = any(tag.startswith("hero_founded_faction:") for tag in faction.memory_tags) or any(
            event.category == "hero_founded_faction" and faction.faction_id in event.related_ids
            for event in next_world.event_log
        )
        if founded_during_campaign and not any(tag.startswith("hero_founded_faction:") for tag in faction.memory_tags):
            faction.memory_tags.append("hero_founded_faction:migrated")
        faction_offices = sorted(
            (office for office in next_world.offices if office.faction_id == faction.faction_id and office.status != "disabled"),
            key=lambda office: (office_rank.get(office.office_type, 9), office.office_id),
        )
        human_member = member_by_faction.get(faction.faction_id)
        existing_player_office_id = None
        if human_member is not None:
            human_user_id = int(getattr(human_member, "user_id"))
            existing_player_office_id = next(
                (
                    hero.office_id
                    for hero in heroes_by_code.values()
                    if hero.faction_id == faction.faction_id
                    and hero.controller_type == "player"
                    and int(hero.controller_user_id or 0) == human_user_id
                    and hero.office_id
                ),
                None,
            )
        for office in faction_offices:
            state = heroes_by_code.get(str(office.holder_id or ""))
            if founded_during_campaign and office.office_type != "lord" and state is None:
                office.holder_id = None
                office.holder_type = None
                office.controller_type = "ai"
                office.controller_user_id = None
                office.status = "vacant"
                continue
            if state is None or (state.office_id and state.office_id != office.office_id):
                if not initializing_hero_system:
                    office.holder_id = None
                    office.holder_type = None
                    office.controller_type = "ai"
                    office.controller_user_id = None
                    office.status = "vacant"
                    continue
                if not available_codes:
                    office.holder_id = None
                    office.holder_type = None
                    office.status = "vacant"
                    continue
                selected_index = _stable_index(next_world, f"office:{office.office_id}", len(available_codes))
                selected_code = available_codes.pop(selected_index)
                state = heroes_by_code[selected_code]
            state.status = "serving"
            state.faction_id = faction.faction_id
            if state.hero_code not in mobile_commander_codes or state.city_id not in city_ids:
                state.city_id = faction.capital_city_id
            state.ritual_city_id = state.ritual_city_id or faction.capital_city_id
            state.office_id = office.office_id
            office.holder_id = state.hero_code
            office.holder_type = "hero"
            office.status = "active"
            player_holds_office = human_member is not None and (
                office.office_id == existing_player_office_id
                or (
                    existing_player_office_id is None
                    and int(getattr(human_member, "user_id")) not in controlled_player_user_ids
                    and office.office_type == "lord"
                )
            )
            if player_holds_office:
                user_id = int(getattr(human_member, "user_id"))
                office.controller_type = "player"
                office.controller_user_id = user_id
                state.controller_type = "player"
                state.controller_user_id = user_id
            else:
                office.controller_type = "ai"
                office.controller_user_id = None
                state.controller_type = "ai"
                state.controller_user_id = None

    next_world.strategic_heroes = sorted(heroes_by_code.values(), key=lambda hero: hero.hero_code)
    ritual_city_ids = {
        str(hero.ritual_city_id)
        for hero in next_world.strategic_heroes
        if hero.ritual_city_id
    }
    for city in next_world.cities:
        if city.city_id in ritual_city_ids:
            city.building_levels["ritual_site"] = max(1, int(city.building_levels.get("ritual_site", 0)))
    next_world.validate()
    return next_world


def _summoned_codes(faction: Faction) -> set[str]:
    codes: set[str] = set()
    for tag in faction.memory_tags:
        if tag.startswith(SUMMONED_TAG_PREFIX):
            code = tag[len(SUMMONED_TAG_PREFIX):]
            if code:
                codes.add(code)
    return codes


def _sleeping_until_by_code(faction: Faction) -> dict[str, int]:
    sleeping: dict[str, int] = {}
    for tag in faction.memory_tags:
        if not tag.startswith(SLEEPING_TAG_PREFIX):
            continue
        payload = tag[len(SLEEPING_TAG_PREFIX):]
        code, separator, month_text = payload.partition(SLEEPING_TAG_SEPARATOR)
        if not code or separator != SLEEPING_TAG_SEPARATOR:
            continue
        try:
            month = int(month_text)
        except ValueError:
            continue
        sleeping[code] = max(sleeping.get(code, 0), month)
    return sleeping


def _defender_codes(faction: Faction) -> list[str]:
    codes: list[str] = []
    for tag in faction.memory_tags:
        if tag.startswith(DEFENDER_TAG_PREFIX):
            code = tag[len(DEFENDER_TAG_PREFIX):]
            if code:
                codes = [code]
    return codes


def _hero_name(hero_code: str) -> str:
    for hero in _base_hero_pool():
        if str(hero.get("code") or "") == hero_code:
            return str(hero.get("name") or hero_code)
    return hero_code


def strategic_hero_pool_public(world: WorldState) -> list[dict[str, Any]]:
    states_by_code = {hero.hero_code: hero for hero in world.strategic_heroes}
    payload: list[dict[str, Any]] = []
    for hero in _base_hero_pool():
        code = str(hero.get("code") or "")
        state = states_by_code.get(code) or StrategicHeroState(hero_code=code)
        faction = next((item for item in world.factions if item.faction_id == state.faction_id), None)
        sleeping = state.status == "sleeping" and int(state.sleeping_until_month or 0) > world.current_month
        defender_assigned = bool(faction and code in _defender_codes(faction))
        effective_status = "serving" if state.status == "sleeping" and not sleeping and state.faction_id else state.status
        item = {
            "code": code,
            "name": hero.get("name") or code,
            "role": hero.get("role") or "",
            "attribute": hero.get("attribute") or "",
            "race": hero.get("race") or "",
            "level": int(hero.get("level", 1) or 1),
            "stats": dict(hero.get("stats") or {}),
            "home_faction_id": state.faction_id or "",
            "faction_id": state.faction_id,
            "city_id": state.city_id,
            "ritual_city_id": state.ritual_city_id,
            "office_id": state.office_id,
            "controller_type": state.controller_type,
            "controller_user_id": state.controller_user_id,
            "loyalty": state.loyalty,
            "status": "sleeping" if sleeping else effective_status,
            "sleeping_until_month": state.sleeping_until_month if sleeping else None,
            "assignment_type": state.assignment_type,
            "assignment_target_id": state.assignment_target_id,
            "defender_assigned": defender_assigned,
            "summon_cost_ether": HERO_RITUAL_ETHER_COST,
        }
        payload.append(item)
    return payload


def strategic_heroes_for_faction_public(world: WorldState, faction_id: str) -> list[dict[str, Any]]:
    return [hero for hero in strategic_hero_pool_public(world) if hero["faction_id"] == faction_id]


def strategic_hero_deployment_limit(world: WorldState, faction_id: str) -> int:
    faction = _faction(world, faction_id)
    bonus = sum(STRATEGIC_HERO_LIMIT_TECH_BONUSES.get(tech_id, 0) for tech_id in faction.tactic_techs)
    return max(0, min(STRATEGIC_HERO_BATTLE_LIMIT_MAX, STRATEGIC_HERO_BATTLE_LIMIT + bonus))


def active_strategic_hero_codes_for_faction(
    world: WorldState,
    faction_id: str,
    *,
    limit: int | None = None,
) -> list[str]:
    faction = _faction(world, faction_id)
    max_count = strategic_hero_deployment_limit(world, faction_id) if limit is None else max(0, int(limit))
    public_codes = {str(hero.get("code") or "") for hero in _base_hero_pool()}
    active_codes = [
        hero.hero_code
        for hero in sorted(world.strategic_heroes, key=lambda item: item.hero_code)
        if hero.hero_code in public_codes
        and hero.faction_id == faction_id
        and (
            hero.status == "serving"
            or (hero.status == "sleeping" and int(hero.sleeping_until_month or 0) <= world.current_month)
        )
        and int(hero.sleeping_until_month or 0) <= world.current_month
    ]
    return active_codes[:max_count]


def normalize_strategic_hero_deployment(
    world: WorldState,
    faction_id: str,
    hero_codes: list[str] | tuple[str, ...] | set[str] | None,
    *,
    limit: int | None = None,
) -> list[str]:
    if hero_codes is None:
        return active_strategic_hero_codes_for_faction(world, faction_id, limit=limit)
    normalized: list[str] = []
    for raw_code in hero_codes:
        code = str(raw_code or "").strip()
        if code and code not in normalized:
            normalized.append(code)
    max_count = strategic_hero_deployment_limit(world, faction_id) if limit is None else max(0, int(limit))
    if len(normalized) > max_count:
        raise StrategyError(f"æˆ˜ç•¥è‹±çµå‚æˆ˜ä¸Šé™ä¸º {max_count} åã€‚")
    active_codes = set(active_strategic_hero_codes_for_faction(world, faction_id, limit=9999))
    invalid_codes = [code for code in normalized if code not in active_codes]
    if invalid_codes:
        raise StrategyError("åªèƒ½æŠ•å…¥å·²å¬å”¤ä¸”æœªæ²‰ç¡çš„æœ¬åŠ¿åŠ›æˆ˜ç•¥è‹±çµã€‚")
    return normalized


def strategic_defender_hero_codes_for_faction(world: WorldState, faction_id: str) -> list[str]:
    faction = _faction(world, faction_id)
    configured_codes = _defender_codes(faction)
    if not configured_codes:
        return active_strategic_hero_codes_for_faction(world, faction_id)
    try:
        return normalize_strategic_hero_deployment(world, faction_id, configured_codes)
    except StrategyError:
        return []


def set_strategic_defender_hero(world: WorldState, *, faction_id: str, hero_code: str) -> WorldState:
    code = str(hero_code or "").strip()
    if code:
        normalize_strategic_hero_deployment(world, faction_id, [code])
    next_world = _clone_world(world)
    faction = _faction(next_world, faction_id)
    faction.memory_tags = [tag for tag in faction.memory_tags if not tag.startswith(DEFENDER_TAG_PREFIX)]
    if code:
        faction.memory_tags.append(f"{DEFENDER_TAG_PREFIX}{code}")
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="strategic_hero_defender_set",
            message=f"{faction.name}è®¾ç½®é˜²å®ˆè‹±çµï¼š{_hero_name(code) if code else 'è‡ªåŠ¨'}ã€‚",
            related_ids=[faction_id, code] if code else [faction_id],
        )
    )
    next_world.validate()
    return next_world


def record_strategic_hero_battle_losses(
    world: WorldState,
    *,
    attacker_faction_id: str,
    defender_faction_id: str,
    surviving_hero_codes_by_team: dict[int, set[str] | list[str] | tuple[str, ...]] | None,
    committed_hero_codes_by_team: dict[int, list[str] | tuple[str, ...] | set[str] | None] | None = None,
    sleep_months: int = STRATEGIC_HERO_BATTLE_SLEEP_MONTHS,
) -> tuple[WorldState, dict[str, dict[str, list[str]]]]:
    next_world = _clone_world(world)
    result: dict[str, dict[str, list[str]]] = {
        "attacker": {"committed": [], "surviving": [], "sleeping": []},
        "defender": {"committed": [], "surviving": [], "sleeping": []},
    }
    if surviving_hero_codes_by_team is None:
        return next_world, result

    side_rows = (
        ("attacker", 1, attacker_faction_id),
        ("defender", 2, defender_faction_id),
    )
    sleep_until_month = next_world.current_month + max(1, int(sleep_months))
    for side, team_id, faction_id in side_rows:
        faction = _faction(next_world, faction_id)
        configured_codes = None if committed_hero_codes_by_team is None else committed_hero_codes_by_team.get(team_id)
        committed = (
            strategic_defender_hero_codes_for_faction(next_world, faction_id)
            if side == "defender" and configured_codes is None
            else normalize_strategic_hero_deployment(next_world, faction_id, configured_codes)
        )
        surviving = {str(code) for code in surviving_hero_codes_by_team.get(team_id, set())}
        sleeping_before = _sleeping_until_by_code(faction)
        result[side]["committed"] = committed
        result[side]["surviving"] = [code for code in committed if code in surviving]
        for code in committed:
            if code in surviving:
                continue
            if sleeping_before.get(code, 0) >= sleep_until_month:
                continue
            faction.memory_tags.append(f"{SLEEPING_TAG_PREFIX}{code}{SLEEPING_TAG_SEPARATOR}{sleep_until_month}")
            hero_state = next((hero for hero in next_world.strategic_heroes if hero.hero_code == code), None)
            if hero_state is not None:
                hero_state.status = "sleeping"
                hero_state.sleeping_until_month = sleep_until_month
            result[side]["sleeping"].append(code)
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="strategic_hero_sleeping",
                    message=f"{faction.name}çš„æˆ˜ç•¥è‹±çµ {_hero_name(code)} æˆ˜è´¥æ²‰ç¡è‡³ç¬¬ {sleep_until_month} æœˆã€‚",
                    related_ids=[faction_id, code],
                )
            )
    next_world.validate()
    return next_world, result


def nearby_roaming_hero_codes(world: WorldState, city_id: str) -> list[str]:
    city = next((item for item in world.cities if item.city_id == str(city_id)), None)
    if city is None:
        raise StrategyError("招募城市不存在。")
    node = next((item for item in world.nodes if item.node_id == city.node_id), None)
    nearby_node_ids = {city.node_id, *(node.connected_node_ids if node is not None else [])}
    nearby_city_ids = {item.city_id for item in world.cities if item.node_id in nearby_node_ids}
    return sorted(
        hero.hero_code
        for hero in world.strategic_heroes
        if hero.status == "roaming" and hero.city_id in nearby_city_ids
    )


def hero_ritual_capacity(world: WorldState, faction_id: str) -> dict[str, int]:
    positions = [
        office
        for office in world.offices
        if office.faction_id == str(faction_id)
        and office.office_type != "lord"
        and office.status != "disabled"
    ]
    lord_holder_ids = {
        str(office.holder_id)
        for office in world.offices
        if office.faction_id == str(faction_id) and office.office_type == "lord" and office.holder_id
    }
    bound = [
        hero
        for hero in world.strategic_heroes
        if hero.faction_id == str(faction_id)
        and hero.ritual_city_id is not None
        and hero.hero_code not in lord_holder_ids
    ]
    maximum = len(positions)
    return {"maximum": maximum, "used": len(bound), "remaining": max(0, maximum - len(bound))}


def perform_hero_ritual(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    faction = _faction(next_world, faction_id)
    city = next((item for item in next_world.cities if item.city_id == str(city_id)), None)
    office = next((item for item in next_world.offices if item.office_id == str(issuer_office_id)), None)
    if city is None or city.owner_faction_id != faction_id:
        raise StrategyError("只能在己方城市举行祭祀。")
    if office is None or office.faction_id != faction_id or office.office_type not in {"lord", "governor"}:
        raise StrategyError("只有主公或城主可以举行召唤祭祀。")
    if office.office_type == "governor" and city.city_id not in office.managed_entity_ids:
        raise StrategyError("城主只能在自己所辖城市举行祭祀。")
    if int(city.building_levels.get("ritual_site", 0)) <= 0:
        raise StrategyError("该城市尚未建造祭祀场。")
    capacity = hero_ritual_capacity(next_world, faction_id)
    if capacity["remaining"] <= 0:
        raise StrategyError("职位容量已满；请先扩充职位或解除一名武将绑定。")
    if city.resources.ether < HERO_RITUAL_ETHER_COST:
        raise StrategyError(f"祭祀需要 {HERO_RITUAL_ETHER_COST} 以太。")
    candidates = [
        hero
        for hero in sorted(next_world.strategic_heroes, key=lambda item: item.hero_code)
        if hero.status == "roaming"
        and hero.faction_id is None
        and hero.ritual_city_id is None
        and hero.controller_type != "player"
    ]
    if not candidates:
        raise StrategyError("当前没有可被召唤的未绑定武将。")
    ritual_number = 1 + sum(
        1
        for event in next_world.event_log
        if event.category == "hero_ritual_summoned" and faction_id in event.related_ids
    )
    selected = candidates[
        _stable_index(
            next_world,
            f"ritual:{next_world.current_month}:{faction_id}:{city.city_id}:{ritual_number}",
            len(candidates),
        )
    ]
    city.resources.ether -= HERO_RITUAL_ETHER_COST
    selected.status = "serving"
    selected.faction_id = faction_id
    selected.city_id = city.city_id
    selected.ritual_city_id = city.city_id
    selected.office_id = None
    selected.loyalty = max(selected.loyalty, 55)
    selected.assignment_type = "reserve"
    selected.assignment_target_id = None
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="hero_ritual_summoned",
            message=f"{faction.name}在{city.name}举行祭祀，召唤出武将：{_hero_name(selected.hero_code)}。",
            related_ids=[faction_id, city.city_id, office.office_id, selected.hero_code],
        )
    )
    next_world.validate()
    return next_world


def unbind_strategic_hero(
    world: WorldState,
    *,
    faction_id: str,
    hero_code: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    faction = _faction(next_world, faction_id)
    lord = next((item for item in next_world.offices if item.office_id == str(issuer_office_id)), None)
    hero = next((item for item in next_world.strategic_heroes if item.hero_code == str(hero_code)), None)
    if lord is None or lord.faction_id != faction_id or lord.office_type != "lord":
        raise StrategyError("只有本势力主公可以解除武将绑定。")
    if hero is None or hero.faction_id != faction_id or hero.ritual_city_id is None:
        raise StrategyError("该武将没有绑定本势力祭祀场。")
    if hero.office_id == lord.office_id:
        raise StrategyError("主公不能解除自己的祭祀绑定。")
    origin_city_id = hero.ritual_city_id
    _release_hero_binding(next_world, hero)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="hero_ritual_unbound",
            message=f"{faction.name}解除{_hero_name(hero.hero_code)}与祭祀场的绑定。",
            related_ids=[faction_id, origin_city_id, lord.office_id, hero.hero_code],
        )
    )
    next_world.validate()
    return next_world


def _release_hero_binding(world: WorldState, hero: StrategicHeroState) -> None:
    office = next((item for item in world.offices if item.office_id == hero.office_id), None)
    if office is not None and office.holder_id == hero.hero_code:
        office.holder_id = None
        office.holder_type = None
        office.controller_type = "ai"
        office.controller_user_id = None
        office.status = "vacant"
    hero.city_id = hero.ritual_city_id or hero.city_id
    hero.ritual_city_id = None
    hero.faction_id = None
    hero.office_id = None
    hero.status = "roaming"
    hero.sleeping_until_month = None
    hero.assignment_type = "reserve"
    hero.assignment_target_id = None


def release_ritual_bindings_for_captured_city(
    world: WorldState,
    *,
    city_id: str,
    previous_faction_id: str,
) -> WorldState:
    released = [
        hero
        for hero in world.strategic_heroes
        if hero.ritual_city_id == str(city_id) and hero.faction_id == str(previous_faction_id)
    ]
    for hero in released:
        _release_hero_binding(world, hero)
        world.event_log.append(
            EventLogEntry(
                month=world.current_month,
                category="hero_ritual_unbound_on_capture",
                message=f"城市失守，{_hero_name(hero.hero_code)}与当地祭祀场解除绑定。",
                related_ids=[previous_faction_id, str(city_id), hero.hero_code],
            )
        )
    world.validate()
    return world


def issue_hero_recruitment(
    world: WorldState,
    *,
    faction_id: str,
    city_id: str,
    issuer_office_id: str,
) -> WorldState:
    faction = _faction(world, faction_id)
    city = next((item for item in world.cities if item.city_id == str(city_id)), None)
    if city is None or city.owner_faction_id != faction_id:
        raise StrategyError("只能在己方城市发布招募武将命令。")
    office = next((item for item in world.offices if item.office_id == str(issuer_office_id)), None)
    if office is None or office.faction_id != faction_id or office.office_type not in {"lord", "governor"}:
        raise StrategyError("只有本势力主公或城主可以发布招募武将命令。")
    if office.office_type == "governor" and city.city_id not in office.managed_entity_ids:
        raise StrategyError("城主只能在自己所辖的城市发布招募武将命令。")
    next_world = _clone_world(world)
    nearby = nearby_roaming_hero_codes(next_world, city_id)
    scored: list[tuple[int, str]] = []
    for code in nearby:
        hero = next(item for item in next_world.strategic_heroes if item.hero_code == code)
        score = _stable_index(next_world, f"recruit:{next_world.current_month}:{faction_id}:{city_id}:{code}", 101)
        willingness = score + hero.loyalty // 4 + int(city.support_by_faction.get(faction_id, 50)) // 5
        if willingness >= 45:
            scored.append((willingness, code))
    candidates = [code for _, code in sorted(scored, key=lambda item: (-item[0], item[1]))[:3]]
    recruitment_id = f"recruitment:{next_world.current_month}:{faction_id}:{city_id}:{len(next_world.hero_recruitments) + 1}"
    next_world.hero_recruitments.append(
        HeroRecruitment(
            recruitment_id=recruitment_id,
            faction_id=faction_id,
            city_id=city_id,
            issuer_office_id=office.office_id,
            issued_month=next_world.current_month,
            status="responses" if candidates else "no_response",
            candidate_hero_codes=candidates,
        )
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="hero_recruitment_responses" if candidates else "hero_recruitment_no_response",
            message=(
                f"{faction.name}在{city.name}发布招募令，{len(candidates)}名附近在野武将前来应召。"
                if candidates
                else f"{faction.name}在{city.name}发布招募令，但本月没有附近在野武将应召。"
            ),
            related_ids=[recruitment_id, faction_id, city_id, *candidates],
        )
    )
    next_world.validate()
    return next_world


def accept_hero_recruitment(
    world: WorldState,
    *,
    faction_id: str,
    recruitment_id: str,
    hero_code: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    recruitment = next(
        (item for item in next_world.hero_recruitments if item.recruitment_id == str(recruitment_id)),
        None,
    )
    lord = next((item for item in next_world.offices if item.office_id == str(issuer_office_id)), None)
    if lord is None or lord.faction_id != faction_id or lord.office_type != "lord":
        raise StrategyError("只有本势力主公可以录用应召武将。")
    if recruitment is None or recruitment.faction_id != faction_id or recruitment.status not in {"responses", "recommended"}:
        raise StrategyError("该招募令没有可录用的应召武将。")
    code = str(hero_code or "")
    if code not in recruitment.candidate_hero_codes:
        raise StrategyError("该武将没有响应这份招募令。")
    recruitment_office = next(
        (item for item in next_world.offices if item.office_id == recruitment.issuer_office_id),
        None,
    )
    if recruitment_office is None:
        raise StrategyError("该招募令的签发职位不存在。")
    if recruitment_office.office_type == "governor" and recruitment.recommended_hero_code != code:
        raise StrategyError("城主签发的招募令必须先由城主举荐，再由主公批准。")
    hero = next((item for item in next_world.strategic_heroes if item.hero_code == code), None)
    if hero is None or hero.status != "roaming":
        raise StrategyError("该武将已经不在野，不能重复录用。")
    hero.status = "serving"
    hero.faction_id = faction_id
    hero.city_id = recruitment.city_id
    hero.loyalty = max(hero.loyalty, 55)
    recruitment.status = "accepted"
    recruitment.accepted_hero_code = code
    for other in next_world.hero_recruitments:
        if other.recruitment_id != recruitment.recruitment_id and other.status == "responses":
            other.candidate_hero_codes = [item for item in other.candidate_hero_codes if item != code]
            if not other.candidate_hero_codes:
                other.status = "no_response"
    faction = _faction(next_world, faction_id)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="strategic_hero_recruited",
            message=f"{faction.name}录用武将：{_hero_name(code)}。",
            related_ids=[recruitment.recruitment_id, faction_id, code],
        )
    )
    next_world.validate()
    return next_world


def recommend_hero_recruitment(
    world: WorldState,
    *,
    faction_id: str,
    recruitment_id: str,
    hero_code: str,
    issuer_office_id: str,
) -> WorldState:
    next_world = _clone_world(world)
    office = next((item for item in next_world.offices if item.office_id == str(issuer_office_id)), None)
    recruitment = next(
        (item for item in next_world.hero_recruitments if item.recruitment_id == str(recruitment_id)),
        None,
    )
    if office is None or office.faction_id != faction_id or office.office_type != "governor":
        raise StrategyError("只有本势力城主可以举荐自己招募令的应召武将。")
    if (
        recruitment is None
        or recruitment.faction_id != faction_id
        or recruitment.issuer_office_id != office.office_id
        or recruitment.status != "responses"
    ):
        raise StrategyError("该城主没有可举荐的招募响应。")
    code = str(hero_code or "")
    if code not in recruitment.candidate_hero_codes:
        raise StrategyError("该武将没有响应这份招募令。")
    hero = next((item for item in next_world.strategic_heroes if item.hero_code == code), None)
    if hero is None or hero.status != "roaming":
        raise StrategyError("该武将已经不在野，不能举荐。")
    recruitment.status = "recommended"
    recruitment.recommended_hero_code = code
    recruitment.recommended_by_office_id = office.office_id
    faction = _faction(next_world, faction_id)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="strategic_hero_recommended",
            message=f"{faction.name}城主向主公举荐武将：{_hero_name(code)}。",
            related_ids=[recruitment.recruitment_id, faction_id, office.office_id, code],
        )
    )
    next_world.validate()
    return next_world


def assign_strategic_hero_duty(
    world: WorldState,
    *,
    faction_id: str,
    issuer_office_id: str,
    hero_code: str,
    assignment_type: str,
    target_id: str = "",
) -> WorldState:
    next_world = _clone_world(world)
    issuer = next((office for office in next_world.offices if office.office_id == str(issuer_office_id)), None)
    hero = next((item for item in next_world.strategic_heroes if item.hero_code == str(hero_code)), None)
    normalized = str(assignment_type or "reserve")
    if issuer is None or issuer.faction_id != faction_id or issuer.office_type != "lord":
        raise StrategyError("只有本势力主公可以分配武将任务。")
    if hero is None or hero.status != "serving" or hero.faction_id != faction_id:
        raise StrategyError("只能为本势力已仕官武将分配任务。")
    if normalized not in {"reserve", "administration", "training", "garrison", "campaign"}:
        raise StrategyError("武将任务类型无效。")
    normalized_target = str(target_id or "").strip() or None
    if normalized in {"garrison", "training"}:
        city = next((item for item in next_world.cities if item.city_id == normalized_target), None)
        if city is None or city.owner_faction_id != faction_id:
            raise StrategyError("驻守或训练任务必须指定一座己方城市。")
    hero.assignment_type = normalized
    hero.assignment_target_id = normalized_target
    faction = _faction(next_world, faction_id)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="strategic_hero_assignment",
            message=f"{faction.name}主公安排{_hero_name(hero.hero_code)}执行任务：{normalized}。",
            related_ids=[faction_id, issuer.office_id, hero.hero_code, *( [normalized_target] if normalized_target else [] )],
        )
    )
    next_world.validate()
    return next_world


def choose_player_hero_path(
    world: WorldState,
    *,
    user_id: int,
    hero_code: str,
    path: str,
    assigned_faction_id: str,
    target_faction_id: str = "",
    allow_reselect: bool = False,
) -> WorldState:
    from wujiang.strategy.offices import ensure_office_system

    next_world = _clone_world(world)
    code = str(hero_code or "").strip()
    normalized_path = str(path or "").strip()
    if normalized_path not in {"lord", "found", "roaming", "join"}:
        raise StrategyError("武将初始道路无效。")
    chosen = next((hero for hero in next_world.strategic_heroes if hero.hero_code == code), None)
    if chosen is None or (
        chosen.status != "roaming"
        and not (chosen.controller_type == "player" and int(chosen.controller_user_id or 0) == int(user_id))
    ):
        raise StrategyError("只能选择尚未被占用的在野武将，或继续使用自己的当前武将。")

    current = next(
        (
            hero
            for hero in next_world.strategic_heroes
            if hero.controller_type == "player" and int(hero.controller_user_id or 0) == int(user_id)
        ),
        None,
    )
    if current is not None and current.hero_code != chosen.hero_code and not allow_reselect:
        raise StrategyError("战役开始后不能改为控制另一名武将；请继续操作当前武将。")
    if current is not None and current.hero_code != chosen.hero_code:
        current.controller_type = "ai"
        current.controller_user_id = None
        current_office = next((office for office in next_world.offices if office.office_id == current.office_id), None)
        if current_office is not None:
            current_office.controller_type = "ai"
            current_office.controller_user_id = None

    if chosen.office_id:
        old_office = next((office for office in next_world.offices if office.office_id == chosen.office_id), None)
        if old_office is not None and old_office.holder_id == chosen.hero_code:
            old_office.holder_id = None
            old_office.holder_type = None
            old_office.controller_type = "ai"
            old_office.controller_user_id = None
            old_office.status = "vacant"
        chosen.office_id = None
    chosen.controller_type = "player"
    chosen.controller_user_id = int(user_id)

    if normalized_path in {"lord", "found"}:
        if normalized_path == "found":
            city = next((item for item in next_world.cities if item.city_id == chosen.city_id), None)
            if city is None:
                raise StrategyError("在野武将必须位于一座城市，才能建立势力。")
            previous_owner_id = city.owner_faction_id
            faction_numbers = []
            for item in next_world.factions:
                try:
                    faction_numbers.append(int(item.faction_id.rsplit("_", 1)[-1]))
                except ValueError:
                    continue
            faction_id = f"faction_{max(faction_numbers, default=0) + 1}"
            faction = Faction(
                faction_id=faction_id,
                name=f"{_hero_name(code)}军",
                controller_user_id=int(user_id),
                is_ai=False,
                capital_city_id=city.city_id,
                resources=ResourceBundle(food=100, money=100, population=0, ether=10, troops=50),
                diplomacy={item.faction_id: "neutral" for item in next_world.factions},
                memory_tags=[f"hero_founded_faction:{code}"],
            )
            next_world.factions.append(faction)
            for item in next_world.factions:
                if item.faction_id != faction_id:
                    item.diplomacy[faction_id] = "neutral"
            city.owner_faction_id = faction_id
            city.support_by_faction.setdefault(faction_id, 50)
            previous_owner = next((item for item in next_world.factions if item.faction_id == previous_owner_id), None)
            if previous_owner is not None and previous_owner.capital_city_id == city.city_id:
                replacement = next(
                    (item for item in next_world.cities if item.owner_faction_id == previous_owner_id),
                    None,
                )
                previous_owner.capital_city_id = replacement.city_id if replacement is not None else None
            next_world = ensure_office_system(next_world)
            for office in next_world.offices:
                if office.faction_id == faction_id and office.office_type != "lord":
                    office.holder_id = None
                    office.holder_type = None
                    office.controller_type = "ai"
                    office.controller_user_id = None
                    office.status = "vacant"
            chosen = next(item for item in next_world.strategic_heroes if item.hero_code == code)
        else:
            faction_id = str(assigned_faction_id or target_faction_id)
        faction = _faction(next_world, faction_id)
        lord = next(
            (office for office in next_world.offices if office.faction_id == faction_id and office.office_type == "lord"),
            None,
        )
        if lord is None:
            raise StrategyError("目标势力没有可接任的主公职位。")
        previous = next((hero for hero in next_world.strategic_heroes if hero.hero_code == lord.holder_id), None)
        if previous is not None and previous.hero_code != chosen.hero_code:
            previous.office_id = None
            previous.controller_type = "ai"
            previous.controller_user_id = None
        chosen.status = "serving"
        chosen.faction_id = faction_id
        chosen.city_id = faction.capital_city_id or chosen.city_id
        chosen.office_id = lord.office_id
        lord.holder_id = chosen.hero_code
        lord.holder_type = "hero"
        lord.controller_type = "player"
        lord.controller_user_id = int(user_id)
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="hero_founded_faction" if normalized_path == "found" else "hero_became_lord",
                message=(
                    f"{_hero_name(code)}在{next((city.name for city in next_world.cities if city.city_id == chosen.city_id), '城中')}"
                    f"建立{faction.name}。"
                    if normalized_path == "found"
                    else f"{_hero_name(code)}成为{faction.name}主公。"
                ),
                related_ids=[code, faction_id, lord.office_id],
            )
        )
    else:
        chosen.status = "roaming"
        chosen.faction_id = None
        chosen.office_id = None
        if not chosen.city_id and next_world.cities:
            chosen.city_id = next_world.cities[_stable_index(next_world, f"player-roaming:{user_id}:{code}", len(next_world.cities))].city_id
        if normalized_path == "join":
            faction_id = str(target_faction_id or "")
            faction = _faction(next_world, faction_id)
            lord = next(
                (office for office in next_world.offices if office.faction_id == faction_id and office.office_type == "lord"),
                None,
            )
            if lord is None:
                raise StrategyError("目标势力没有主公，无法提交投靠请求。")
            existing_request = next(
                (
                    item
                    for item in next_world.hero_recruitments
                    if item.status == "responses"
                    and item.faction_id == faction_id
                    and code in item.candidate_hero_codes
                ),
                None,
            )
            if existing_request is not None:
                raise StrategyError("该武将已经向这个势力提交了投靠请求。")
            request_id = f"allegiance:{next_world.current_month}:{code}:{faction_id}"
            next_world.hero_recruitments.append(
                HeroRecruitment(
                    recruitment_id=request_id,
                    faction_id=faction_id,
                    city_id=chosen.city_id or faction.capital_city_id or next_world.cities[0].city_id,
                    issuer_office_id=lord.office_id,
                    issued_month=next_world.current_month,
                    status="responses",
                    candidate_hero_codes=[code],
                )
            )
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="hero_requested_allegiance",
                    message=f"在野武将{_hero_name(code)}请求投靠{faction.name}。",
                    related_ids=[request_id, code, faction_id],
                )
            )
    next_world.validate()
    return next_world


def open_spontaneous_allegiance_request(world: WorldState) -> WorldState:
    next_world = _clone_world(world)
    pending_codes = {
        code
        for request in next_world.hero_recruitments
        if request.status == "responses"
        for code in request.candidate_hero_codes
    }
    roaming = sorted(
        (
            hero
            for hero in next_world.strategic_heroes
            if hero.status == "roaming" and hero.controller_type == "ai" and hero.hero_code not in pending_codes
        ),
        key=lambda hero: hero.hero_code,
    )
    if not roaming:
        return next_world
    chosen = roaming[_stable_index(next_world, f"spontaneous:{next_world.current_month}", len(roaming))]
    city = next((item for item in next_world.cities if item.city_id == chosen.city_id), None)
    node = next((item for item in next_world.nodes if item.node_id == city.node_id), None) if city is not None else None
    nearby_node_ids = {city.node_id, *(node.connected_node_ids if node is not None else [])} if city is not None else set()
    nearby_faction_ids = sorted(
        {
            item.owner_faction_id
            for item in next_world.cities
            if item.node_id in nearby_node_ids
        }
    )
    candidate_lords = sorted(
        (
            office
            for office in next_world.offices
            if office.office_type == "lord"
            and office.status == "active"
            and office.faction_id in nearby_faction_ids
        ),
        key=lambda office: office.faction_id,
    )
    if not candidate_lords:
        return next_world
    lord = candidate_lords[_stable_index(next_world, f"spontaneous-target:{next_world.current_month}:{chosen.hero_code}", len(candidate_lords))]
    faction = _faction(next_world, lord.faction_id)
    request_id = f"allegiance-ai:{next_world.current_month}:{chosen.hero_code}:{faction.faction_id}"
    next_world.hero_recruitments.append(
        HeroRecruitment(
            recruitment_id=request_id,
            faction_id=faction.faction_id,
            city_id=chosen.city_id or faction.capital_city_id or next_world.cities[0].city_id,
            issuer_office_id=lord.office_id,
            issued_month=next_world.current_month,
            status="responses",
            candidate_hero_codes=[chosen.hero_code],
        )
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="hero_requested_allegiance",
            message=f"在野武将{_hero_name(chosen.hero_code)}主动请求投靠{faction.name}。",
            related_ids=[request_id, chosen.hero_code, faction.faction_id],
        )
    )
    next_world.validate()
    return next_world


def appoint_strategic_hero_to_office(
    world: WorldState,
    *,
    faction_id: str,
    issuer_office_id: str,
    target_office_id: str,
    hero_code: str,
) -> WorldState:
    next_world = _clone_world(world)
    issuer = next((office for office in next_world.offices if office.office_id == str(issuer_office_id)), None)
    target = next((office for office in next_world.offices if office.office_id == str(target_office_id)), None)
    hero = next((item for item in next_world.strategic_heroes if item.hero_code == str(hero_code)), None)
    if issuer is None or issuer.faction_id != faction_id or issuer.office_type != "lord":
        raise StrategyError("只有本势力主公可以任命武将职位。")
    if target is None or target.faction_id != faction_id or target.office_type == "lord" or target.status == "disabled":
        raise StrategyError("只能任命本势力可用的大将军、将军或城主职位。")
    if hero is None or hero.status != "serving" or hero.faction_id != faction_id:
        raise StrategyError("只能任命已通过招募进入本势力的在职武将。")

    if target.holder_type == "hero" and target.holder_id and target.holder_id != hero.hero_code:
        previous = next((item for item in next_world.strategic_heroes if item.hero_code == target.holder_id), None)
        if previous is not None:
            previous.office_id = None
    if hero.office_id and hero.office_id != target.office_id:
        previous_office = next((office for office in next_world.offices if office.office_id == hero.office_id), None)
        if previous_office is not None:
            previous_office.holder_id = None
            previous_office.holder_type = None
            previous_office.controller_type = "ai"
            previous_office.controller_user_id = None
            previous_office.status = "vacant"
    hero.office_id = target.office_id
    target.holder_id = hero.hero_code
    target.holder_type = "hero"
    target.controller_type = hero.controller_type
    target.controller_user_id = hero.controller_user_id
    target.status = "active"
    faction = _faction(next_world, faction_id)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="strategic_hero_appointed",
            message=f"{faction.name}任命{_hero_name(hero.hero_code)}担任{target.office_type}。",
            related_ids=[faction_id, issuer.office_id, target.office_id, hero.hero_code],
        )
    )
    next_world.validate()
    return next_world


def validate_summon_strategic_hero(world: WorldState, *, faction_id: str, hero_code: str) -> None:
    raise StrategyError("不能直接召唤或指定获得武将；请由主公发布招募武将命令。")


def summon_strategic_hero(world: WorldState, *, faction_id: str, hero_code: str) -> WorldState:
    validate_summon_strategic_hero(world, faction_id=faction_id, hero_code=hero_code)
    return world
