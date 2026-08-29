from __future__ import annotations

import copy
import hashlib
from typing import Any

from wujiang.strategic.models import EventLogEntry, RelicAltar, RelicState, StrategyError, WorldState
from wujiang.strategic.objectives import FIRST_CAMPAIGN_SCENARIO_ID


RELIC_SYSTEM_VERSION = "relic_altar_p6_v1"
DEFAULT_RELIC_MAINTENANCE_ETHER = 10
RELIC_SEARCH_FOOD_COST = 20
RELIC_TRANSFER_FOOD_COST = 10
RELIC_REPAIR_MONEY_COST = 40
RELIC_REPAIR_ETHER_COST = 20
RELIC_ALTAR_MONTHLY_ACTIONS = 1
RELIC_ALTAR_VICTORY_REQUIRED_MONTHS = 3
RELIC_VICTORY_VERSION = "relic_altar_p6_6_v1"


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _stable_index(world: WorldState, key: str, count: int) -> int:
    if count <= 0:
        return 0
    digest = hashlib.sha256(f"{world.seed}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % count


def _faction(world: WorldState, faction_id: str):
    faction = next((item for item in world.factions if item.faction_id == str(faction_id)), None)
    if faction is None:
        raise StrategyError("势力不存在。")
    return faction


def _city(world: WorldState, city_id: str):
    city = next((item for item in world.cities if item.city_id == str(city_id)), None)
    if city is None:
        raise StrategyError("城市不存在。")
    return city


def _relic(world: WorldState, relic_id: str) -> RelicState:
    relic = next((item for item in world.relics if item.relic_id == str(relic_id)), None)
    if relic is None:
        raise StrategyError("圣物不存在。")
    return relic


def _hero(world: WorldState, hero_code: str):
    hero = next((item for item in world.strategic_heroes if item.hero_code == str(hero_code)), None)
    if hero is None:
        raise StrategyError("英灵不存在。")
    return hero


def _require_lord_office(world: WorldState, faction_id: str, issuer_office_id: str) -> None:
    office = next((item for item in world.offices if item.office_id == str(issuer_office_id)), None)
    if office is None or office.faction_id != faction_id or office.office_type != "lord":
        raise StrategyError("只有本势力主公可以签发圣物行动。")


def _node_neighbors(world: WorldState, node_id: str) -> set[str]:
    node = next((item for item in world.nodes if item.node_id == str(node_id)), None)
    return set(node.connected_node_ids) if node is not None else set()


def _hero_name(world: WorldState, hero_code: str) -> str:
    from wujiang.strategic.heroes import strategic_hero_pool_public

    return str(
        next(
            (item.get("name") for item in strategic_hero_pool_public(world) if item.get("code") == hero_code),
            hero_code,
        )
    )


def _relic_control_was_lost_by_faction(relic: RelicState, faction_id: str) -> bool:
    return any(
        (
            item.get("event") == "captured_on_city_control_change"
            and item.get("previous_faction_id") == faction_id
        )
        or (
            item.get("event") == "released"
            and item.get("faction_id") == faction_id
        )
        for item in relic.history
    )


def release_sleeping_heroes_with_lost_relics(
    world: WorldState,
    *,
    faction_id: str,
    hero_codes: list[str] | tuple[str, ...] | set[str] | None = None,
    cause: str = "battle_sleep",
) -> list[str]:
    candidates = set(str(code) for code in hero_codes) if hero_codes is not None else None
    released: list[str] = []
    from wujiang.strategic.heroes import release_sleeping_hero_for_lost_relic

    for hero in world.strategic_heroes:
        if (
            hero.faction_id != faction_id
            or hero.status != "sleeping"
            or (candidates is not None and hero.hero_code not in candidates)
        ):
            continue
        relic = next((item for item in world.relics if item.hero_code == hero.hero_code), None)
        if (
            relic is None
            or relic.owner_faction_id == faction_id
            or not _relic_control_was_lost_by_faction(relic, faction_id)
        ):
            continue
        if release_sleeping_hero_for_lost_relic(
            world,
            hero_code=hero.hero_code,
            previous_faction_id=faction_id,
            relic_id=relic.relic_id,
            cause=cause,
        ):
            released.append(hero.hero_code)
    return released


def apply_city_control_change_consequences(
    world: WorldState,
    *,
    city_id: str,
    previous_faction_id: str,
    new_faction_id: str,
    cause: str,
) -> tuple[WorldState, dict[str, Any]]:
    summary: dict[str, Any] = {
        "city_id": str(city_id),
        "previous_faction_id": str(previous_faction_id),
        "new_faction_id": str(new_faction_id),
        "cause": str(cause),
        "captured_relic_ids": [],
        "captured_relic_names": [],
        "disrupted_altar_ids": [],
        "disrupted_altar_names": [],
        "unbound_hero_codes": [],
    }
    if previous_faction_id == new_faction_id:
        return world, summary
    if not relic_system_enabled(world):
        ritual_bound_before = {
            hero.hero_code
            for hero in world.strategic_heroes
            if hero.ritual_city_id == str(city_id) and hero.faction_id == previous_faction_id
        }
        from wujiang.strategic.heroes import release_ritual_bindings_for_captured_city

        next_world = release_ritual_bindings_for_captured_city(
            world,
            city_id=city_id,
            previous_faction_id=previous_faction_id,
        )
        summary["unbound_hero_codes"] = sorted(
            hero_code
            for hero_code in ritual_bound_before
            if next(
                item for item in next_world.strategic_heroes if item.hero_code == hero_code
            ).faction_id != previous_faction_id
        )
        return next_world, summary
    ensured = ensure_relic_system(world)
    next_world = ensured
    city = _city(next_world, city_id)
    if city.owner_faction_id != new_faction_id:
        raise StrategyError("城市控制权尚未写入，不能结算圣物夺取。")
    previous = _faction(next_world, previous_faction_id)
    new_owner = _faction(next_world, new_faction_id)
    altars = [altar for altar in next_world.relic_altars if altar.city_id == city.city_id]
    altar_ids = {altar.altar_id for altar in altars}
    captured = [
        relic
        for relic in next_world.relics
        if relic.owner_faction_id == previous_faction_id
        and (
            (relic.state == "stored" and relic.location_city_id == city.city_id)
            or (relic.state == "bound_to_altar" and relic.altar_id in altar_ids)
        )
    ]
    for relic in captured:
        relic.owner_faction_id = new_faction_id
        relic.last_changed_month = next_world.current_month
        if new_faction_id not in relic.discovered_by_faction_ids:
            relic.discovered_by_faction_ids.append(new_faction_id)
            relic.discovered_by_faction_ids.sort()
        relic.history.append(
            {
                "month": next_world.current_month,
                "event": "captured_on_city_control_change",
                "city_id": city.city_id,
                "previous_faction_id": previous_faction_id,
                "new_faction_id": new_faction_id,
                "cause": cause,
                "summary": f"{city.name}控制权变化后，圣物由{previous.name}转归{new_owner.name}。",
            }
        )
        summary["captured_relic_ids"].append(relic.relic_id)
        summary["captured_relic_names"].append(relic.name)

    for altar in altars:
        marker_exists = any(
            item.get("event") == "city_control_changed"
            and item.get("month") == next_world.current_month
            and item.get("previous_faction_id") == previous_faction_id
            and item.get("new_faction_id") == new_faction_id
            and item.get("cause") == cause
            for item in altar.history
        )
        if marker_exists:
            continue
        _reset_altar_consecration(
            altar,
            month=next_world.current_month,
            cause=f"city_control_change:{cause}",
            summary=f"{city.name}控制权变化，原圣物胜利准备清零。",
        )
        altar.action_month = next_world.current_month
        altar.actions_used = max(altar.actions_used, RELIC_ALTAR_MONTHLY_ACTIONS)
        if altar.bound_relic_ids:
            altar.state = "damaged"
            altar.damaged_until_month = next_world.current_month
        else:
            altar.state = "dormant"
            altar.damaged_until_month = None
        altar.history.append(
            {
                "month": next_world.current_month,
                "event": "city_control_changed",
                "previous_faction_id": previous_faction_id,
                "new_faction_id": new_faction_id,
                "cause": cause,
                "summary": (
                    f"{city.name}控制权变化，祭坛本月行动耗尽"
                    + ("并进入失养。" if altar.bound_relic_ids else "，空祭坛保持沉寂。")
                ),
            }
        )
        summary["disrupted_altar_ids"].append(altar.altar_id)
        summary["disrupted_altar_names"].append(altar.name)

    summary["captured_relic_ids"].sort()
    summary["captured_relic_names"].sort()
    summary["disrupted_altar_ids"].sort()
    summary["disrupted_altar_names"].sort()
    summary["unbound_hero_codes"].extend(
        release_sleeping_heroes_with_lost_relics(
            next_world,
            faction_id=previous_faction_id,
            hero_codes=[relic.hero_code for relic in captured],
            cause=f"relic_capture:{cause}",
        )
    )
    ritual_bound_before = {
        hero.hero_code
        for hero in next_world.strategic_heroes
        if hero.ritual_city_id == city.city_id and hero.faction_id == previous_faction_id
    }
    from wujiang.strategic.heroes import release_ritual_bindings_for_captured_city

    next_world = release_ritual_bindings_for_captured_city(
        next_world,
        city_id=city.city_id,
        previous_faction_id=previous_faction_id,
    )
    for hero_code in sorted(ritual_bound_before):
        hero = next(item for item in next_world.strategic_heroes if item.hero_code == hero_code)
        if hero.faction_id != previous_faction_id and hero_code not in summary["unbound_hero_codes"]:
            summary["unbound_hero_codes"].append(hero_code)
    summary["unbound_hero_codes"].sort()
    if summary["captured_relic_ids"] or summary["disrupted_altar_ids"]:
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="relics_captured_on_city_control_change",
                message=(
                    f"{new_owner.name}控制{city.name}并取得 {len(summary['captured_relic_ids'])} 件圣物；"
                    f"{len(summary['disrupted_altar_ids'])} 座圣物祭坛完成控制权回写。"
                ),
                related_ids=[
                    city.city_id,
                    previous_faction_id,
                    new_faction_id,
                    *summary["captured_relic_ids"],
                    *summary["disrupted_altar_ids"],
                ],
            )
        )
    next_world.validate()
    return next_world, summary


def relic_search_will_damage(world: WorldState, *, relic_id: str, hero_code: str) -> bool:
    return _stable_index(
        world,
        f"relic-search-damage:{world.current_month}:{relic_id}:{hero_code}",
        4,
    ) == 0


def validate_relic_search(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    hero_code: str,
    city_id: str,
    issuer_office_id: str,
) -> None:
    ensured = ensure_relic_system(world)
    _faction(ensured, faction_id)
    _require_lord_office(ensured, faction_id, issuer_office_id)
    relic = _relic(ensured, relic_id)
    hero = _hero(ensured, hero_code)
    city = _city(ensured, city_id)
    if relic.state not in {"scattered", "released"} or relic.owner_faction_id is not None:
        raise StrategyError("该圣物已经不在可搜索状态。")
    if faction_id not in relic.discovered_by_faction_ids:
        raise StrategyError("本势力尚未掌握该圣物的线索。")
    if hero.faction_id != faction_id or hero.status != "serving":
        raise StrategyError("只能派遣本势力仕官中的英灵搜索圣物。")
    if hero.sleeping_until_month is not None and hero.sleeping_until_month >= ensured.current_month:
        raise StrategyError("沉睡中的英灵不能搜索圣物。")
    if hero.city_id != city.city_id or city.owner_faction_id != faction_id:
        raise StrategyError("搜索英灵必须驻扎在指定的己方出发城市。")
    if hero.last_personal_action_month == ensured.current_month:
        raise StrategyError("该英灵本月已经执行过个人行动。")
    from wujiang.strategic.hero_personal import require_hero_command_acceptance

    require_hero_command_acceptance(ensured, hero, "relic_search")
    legal_nodes = {city.node_id, *_node_neighbors(ensured, city.node_id)}
    if relic.location_node_id not in legal_nodes:
        raise StrategyError("该线索不在搜索城市或其直接相邻节点。")
    if city.resources.food < RELIC_SEARCH_FOOD_COST:
        raise StrategyError(f"搜索远征需要出发城市 {RELIC_SEARCH_FOOD_COST} 粮。")


def search_relic(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    hero_code: str,
    city_id: str,
    issuer_office_id: str,
) -> WorldState:
    ensured = ensure_relic_system(world)
    validate_relic_search(
        ensured,
        faction_id=faction_id,
        relic_id=relic_id,
        hero_code=hero_code,
        city_id=city_id,
        issuer_office_id=issuer_office_id,
    )
    next_world = _clone_world(ensured)
    faction = _faction(next_world, faction_id)
    relic = _relic(next_world, relic_id)
    hero = _hero(next_world, hero_code)
    city = _city(next_world, city_id)
    damaged = relic_search_will_damage(
        next_world,
        relic_id=relic.relic_id,
        hero_code=hero.hero_code,
    )
    city.resources.food -= RELIC_SEARCH_FOOD_COST
    hero.last_personal_action_month = next_world.current_month
    relic.state = "stored"
    relic.condition = "damaged" if damaged else "intact"
    relic.location_node_id = city.node_id
    relic.location_city_id = city.city_id
    relic.owner_faction_id = faction_id
    relic.altar_id = None
    relic.last_changed_month = next_world.current_month
    if faction_id not in relic.discovered_by_faction_ids:
        relic.discovered_by_faction_ids.append(faction_id)
        relic.discovered_by_faction_ids.sort()
    if relic.relic_id not in city.relics_stored:
        city.relics_stored.append(relic.relic_id)
        city.relics_stored.sort()
    result = "但圣物已经受损" if damaged else "且圣物保持完整"
    relic.history.append(
        {
            "month": next_world.current_month,
            "event": "searched_and_stored",
            "faction_id": faction_id,
            "hero_code": hero_code,
            "city_id": city_id,
            "condition": relic.condition,
            "summary": f"搜索队将圣物带回{city.name}保管，{result}。",
        }
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="relic_searched",
            message=f"{faction.name}派遣{_hero_name(next_world, hero_code)}找回{relic.name}，存入{city.name}{'；圣物受损' if damaged else ''}。",
            related_ids=[faction_id, city_id, hero_code, relic_id, issuer_office_id],
        )
    )
    next_world.validate()
    return next_world


def validate_relic_transfer(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    target_city_id: str,
    issuer_office_id: str,
) -> None:
    ensured = ensure_relic_system(world)
    _require_lord_office(ensured, faction_id, issuer_office_id)
    relic = _relic(ensured, relic_id)
    if relic.state != "stored" or relic.owner_faction_id != faction_id or relic.location_city_id is None:
        raise StrategyError("只能转移本势力正在城市保管的圣物。")
    source = _city(ensured, relic.location_city_id)
    target = _city(ensured, target_city_id)
    if target.city_id == source.city_id:
        raise StrategyError("圣物已经存放在目标城市。")
    if source.owner_faction_id != faction_id or target.owner_faction_id != faction_id:
        raise StrategyError("圣物转移的起点和终点必须都由本势力控制。")
    if target.node_id not in _node_neighbors(ensured, source.node_id):
        raise StrategyError("圣物每月只能沿一条直接相邻的己方路线转移。")
    if source.resources.food < RELIC_TRANSFER_FOOD_COST:
        raise StrategyError(f"圣物转移需要出发城市 {RELIC_TRANSFER_FOOD_COST} 粮。")


def transfer_relic(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    target_city_id: str,
    issuer_office_id: str,
) -> WorldState:
    ensured = ensure_relic_system(world)
    validate_relic_transfer(
        ensured,
        faction_id=faction_id,
        relic_id=relic_id,
        target_city_id=target_city_id,
        issuer_office_id=issuer_office_id,
    )
    next_world = _clone_world(ensured)
    faction = _faction(next_world, faction_id)
    relic = _relic(next_world, relic_id)
    source = _city(next_world, str(relic.location_city_id))
    target = _city(next_world, target_city_id)
    source.resources.food -= RELIC_TRANSFER_FOOD_COST
    source.relics_stored = [item for item in source.relics_stored if item != relic.relic_id]
    if relic.relic_id not in target.relics_stored:
        target.relics_stored.append(relic.relic_id)
        target.relics_stored.sort()
    relic.location_city_id = target.city_id
    relic.location_node_id = target.node_id
    relic.last_changed_month = next_world.current_month
    relic.history.append(
        {
            "month": next_world.current_month,
            "event": "transferred",
            "faction_id": faction_id,
            "source_city_id": source.city_id,
            "target_city_id": target.city_id,
            "summary": f"圣物由{source.name}转移至{target.name}保管。",
        }
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="relic_transferred",
            message=f"{faction.name}将{relic.name}由{source.name}转移至{target.name}。",
            related_ids=[faction_id, source.city_id, target.city_id, relic_id, issuer_office_id],
        )
    )
    next_world.validate()
    return next_world


def validate_relic_repair(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    issuer_office_id: str,
) -> None:
    ensured = ensure_relic_system(world)
    faction = _faction(ensured, faction_id)
    _require_lord_office(ensured, faction_id, issuer_office_id)
    relic = _relic(ensured, relic_id)
    if relic.state != "stored" or relic.owner_faction_id != faction_id or relic.location_city_id is None:
        raise StrategyError("只能修复本势力正在城市保管的圣物。")
    if relic.condition != "damaged":
        raise StrategyError("该圣物无需修复。")
    city = _city(ensured, relic.location_city_id)
    if city.owner_faction_id != faction_id:
        raise StrategyError("圣物所在城市已不受本势力控制。")
    if faction.resources.money < RELIC_REPAIR_MONEY_COST:
        raise StrategyError(f"修复圣物需要势力 {RELIC_REPAIR_MONEY_COST} 金钱。")
    if city.resources.ether < RELIC_REPAIR_ETHER_COST:
        raise StrategyError(f"修复圣物需要所在城市 {RELIC_REPAIR_ETHER_COST} 以太。")


def repair_relic(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    issuer_office_id: str,
) -> WorldState:
    ensured = ensure_relic_system(world)
    validate_relic_repair(
        ensured,
        faction_id=faction_id,
        relic_id=relic_id,
        issuer_office_id=issuer_office_id,
    )
    next_world = _clone_world(ensured)
    faction = _faction(next_world, faction_id)
    relic = _relic(next_world, relic_id)
    city = _city(next_world, str(relic.location_city_id))
    faction.resources.money -= RELIC_REPAIR_MONEY_COST
    city.resources.ether -= RELIC_REPAIR_ETHER_COST
    relic.condition = "intact"
    relic.last_changed_month = next_world.current_month
    relic.history.append(
        {
            "month": next_world.current_month,
            "event": "repaired",
            "faction_id": faction_id,
            "city_id": city.city_id,
            "summary": f"在{city.name}支付金钱与以太完成圣物修复。",
        }
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="relic_repaired",
            message=f"{faction.name}在{city.name}修复了{relic.name}。",
            related_ids=[faction_id, city.city_id, relic_id, issuer_office_id],
        )
    )
    next_world.validate()
    return next_world


def relic_altar_actions_remaining(world: WorldState, altar: RelicAltar) -> int:
    used = altar.actions_used if altar.action_month == world.current_month else 0
    return max(0, RELIC_ALTAR_MONTHLY_ACTIONS - used)


def _use_relic_altar_action(world: WorldState, altar: RelicAltar) -> None:
    if altar.action_month != world.current_month:
        altar.action_month = world.current_month
        altar.actions_used = 0
    if altar.actions_used >= RELIC_ALTAR_MONTHLY_ACTIONS:
        raise StrategyError("该圣物祭坛本月已经执行过绑定或释放。")
    altar.actions_used += 1


def _reset_altar_consecration(
    altar: RelicAltar,
    *,
    month: int,
    cause: str,
    summary: str,
) -> bool:
    had_progress = bool(
        altar.consecration_progress
        or altar.consecration_faction_id
        or altar.consecration_relic_id
    )
    if had_progress:
        altar.history.append(
            {
                "month": month,
                "event": "consecration_reset",
                "cause": cause,
                "previous_faction_id": altar.consecration_faction_id,
                "previous_relic_id": altar.consecration_relic_id,
                "previous_progress": altar.consecration_progress,
                "summary": summary,
            }
        )
    altar.consecration_faction_id = None
    altar.consecration_relic_id = None
    altar.consecration_progress = 0
    altar.consecration_required = RELIC_ALTAR_VICTORY_REQUIRED_MONTHS
    altar.consecration_started_month = None
    altar.consecration_last_month = None
    return had_progress


def validate_bind_relic(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    altar_id: str,
    issuer_office_id: str,
) -> None:
    ensured = ensure_relic_system(world)
    _require_lord_office(ensured, faction_id, issuer_office_id)
    relic = _relic(ensured, relic_id)
    altar = next((item for item in ensured.relic_altars if item.altar_id == str(altar_id)), None)
    if altar is None:
        raise StrategyError("圣物祭坛不存在。")
    city = _city(ensured, altar.city_id)
    if relic.state != "stored" or relic.owner_faction_id != faction_id or relic.location_city_id is None:
        raise StrategyError("只能绑定本势力正在城市保管的圣物。")
    if relic.condition != "intact":
        raise StrategyError("受损圣物必须先修复才能绑定祭坛。")
    if city.owner_faction_id != faction_id:
        raise StrategyError("只能使用本势力城市中的圣物祭坛。")
    if relic.location_city_id != city.city_id:
        raise StrategyError("圣物与圣物祭坛必须位于同一座城市。")
    if len(altar.bound_relic_ids) >= altar.capacity:
        raise StrategyError("该圣物祭坛已经达到绑定容量。")
    if relic_altar_actions_remaining(ensured, altar) <= 0:
        raise StrategyError("该圣物祭坛本月已经执行过绑定或释放。")


def bind_relic(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    altar_id: str,
    issuer_office_id: str,
) -> WorldState:
    ensured = ensure_relic_system(world)
    validate_bind_relic(
        ensured,
        faction_id=faction_id,
        relic_id=relic_id,
        altar_id=altar_id,
        issuer_office_id=issuer_office_id,
    )
    next_world = _clone_world(ensured)
    faction = _faction(next_world, faction_id)
    relic = _relic(next_world, relic_id)
    altar = next(item for item in next_world.relic_altars if item.altar_id == altar_id)
    city = _city(next_world, altar.city_id)
    _use_relic_altar_action(next_world, altar)
    _reset_altar_consecration(
        altar,
        month=next_world.current_month,
        cause="binding_changed",
        summary=f"{altar.name}更换绑定圣物，胜利准备从下一次维护重新开始。",
    )
    city.relics_stored = [item for item in city.relics_stored if item != relic.relic_id]
    altar.bound_relic_ids.append(relic.relic_id)
    altar.bound_relic_ids.sort()
    altar.state = "active"
    altar.damaged_until_month = None
    relic.state = "bound_to_altar"
    relic.location_node_id = city.node_id
    relic.location_city_id = city.city_id
    relic.owner_faction_id = faction_id
    relic.altar_id = altar.altar_id
    relic.last_changed_month = next_world.current_month
    relic.history.append(
        {
            "month": next_world.current_month,
            "event": "bound_to_altar",
            "faction_id": faction_id,
            "city_id": city.city_id,
            "altar_id": altar.altar_id,
            "summary": f"圣物在{city.name}绑定到{altar.name}。",
        }
    )
    altar.history.append(
        {
            "month": next_world.current_month,
            "event": "relic_bound",
            "relic_id": relic.relic_id,
            "summary": f"{relic.name}完成祭坛绑定。",
        }
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="relic_bound",
            message=f"{faction.name}将{relic.name}绑定到{altar.name}。",
            related_ids=[faction_id, city.city_id, altar.altar_id, relic.relic_id, issuer_office_id],
        )
    )
    next_world.validate()
    return next_world


def _released_relic_node_id(world: WorldState, relic: RelicState) -> str:
    current_node_id = relic.location_node_id
    candidates = sorted(
        (node for node in world.nodes if node.node_id != current_node_id),
        key=lambda item: item.node_id,
    )
    if not candidates:
        candidates = sorted(world.nodes, key=lambda item: item.node_id)
    release_number = 1 + sum(1 for item in relic.history if item.get("event") == "released")
    return candidates[
        _stable_index(
            world,
            f"relic-release:{world.current_month}:{relic.relic_id}:{release_number}",
            len(candidates),
        )
    ].node_id


def validate_release_relic(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    issuer_office_id: str,
) -> None:
    ensured = ensure_relic_system(world)
    _require_lord_office(ensured, faction_id, issuer_office_id)
    relic = _relic(ensured, relic_id)
    if relic.state != "bound_to_altar" or relic.owner_faction_id != faction_id or relic.altar_id is None:
        raise StrategyError("只能释放本势力已经绑定到祭坛的圣物。")
    altar = next((item for item in ensured.relic_altars if item.altar_id == relic.altar_id), None)
    if altar is None or relic.relic_id not in altar.bound_relic_ids:
        raise StrategyError("圣物与祭坛绑定记录不一致。")
    if relic_altar_actions_remaining(ensured, altar) <= 0:
        raise StrategyError("该圣物祭坛本月已经执行过绑定或释放。")


def release_relic(
    world: WorldState,
    *,
    faction_id: str,
    relic_id: str,
    issuer_office_id: str,
) -> WorldState:
    ensured = ensure_relic_system(world)
    validate_release_relic(
        ensured,
        faction_id=faction_id,
        relic_id=relic_id,
        issuer_office_id=issuer_office_id,
    )
    next_world = _clone_world(ensured)
    faction = _faction(next_world, faction_id)
    relic = _relic(next_world, relic_id)
    altar = next(item for item in next_world.relic_altars if item.altar_id == relic.altar_id)
    city = _city(next_world, altar.city_id)
    _use_relic_altar_action(next_world, altar)
    _reset_altar_consecration(
        altar,
        month=next_world.current_month,
        cause="relic_released",
        summary=f"{relic.name}被主动释放，{altar.name}的胜利准备清零。",
    )
    new_node_id = _released_relic_node_id(next_world, relic)
    altar.bound_relic_ids = [item for item in altar.bound_relic_ids if item != relic.relic_id]
    altar.state = "active" if altar.bound_relic_ids else "dormant"
    altar.damaged_until_month = None
    relic.state = "released"
    relic.location_node_id = new_node_id
    relic.location_city_id = None
    relic.owner_faction_id = None
    relic.altar_id = None
    relic.last_changed_month = next_world.current_month
    if faction_id not in relic.discovered_by_faction_ids:
        relic.discovered_by_faction_ids.append(faction_id)
        relic.discovered_by_faction_ids.sort()
    node = next(item for item in next_world.nodes if item.node_id == new_node_id)
    relic.history.append(
        {
            "month": next_world.current_month,
            "event": "released",
            "faction_id": faction_id,
            "source_city_id": city.city_id,
            "location_node_id": new_node_id,
            "summary": f"圣物从{altar.name}主动释放，重新散布至{node.name}附近。",
        }
    )
    release_sleeping_heroes_with_lost_relics(
        next_world,
        faction_id=faction_id,
        hero_codes=[relic.hero_code],
        cause="voluntary_relic_release",
    )
    altar.history.append(
        {
            "month": next_world.current_month,
            "event": "relic_released",
            "relic_id": relic.relic_id,
            "summary": f"{relic.name}被主动释放。",
        }
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="relic_released",
            message=f"{faction.name}从{altar.name}释放了{relic.name}；新的追踪线索指向{node.name}。",
            related_ids=[faction_id, city.city_id, altar.altar_id, relic.relic_id, new_node_id, issuer_office_id],
        )
    )
    next_world.validate()
    return next_world


def advance_relic_maintenance(world: WorldState) -> WorldState:
    if not relic_system_enabled(world):
        return world
    ensured = ensure_relic_system(world)
    next_world = _clone_world(ensured)
    for altar in next_world.relic_altars:
        if not altar.bound_relic_ids:
            _reset_altar_consecration(
                altar,
                month=next_world.current_month,
                cause="altar_empty",
                summary=f"{altar.name}没有绑定圣物，胜利准备清零。",
            )
            altar.state = "dormant"
            altar.damaged_until_month = None
            continue
        if any(
            item.get("month") == next_world.current_month
            and item.get("event") in {"maintenance_paid", "maintenance_failed"}
            for item in altar.history
        ):
            continue
        city = _city(next_world, altar.city_id)
        bound_relics = [_relic(next_world, relic_id) for relic_id in altar.bound_relic_ids]
        maintenance_cost = sum(relic.maintenance_ether_cost for relic in bound_relics)
        owner_ids = {relic.owner_faction_id for relic in bound_relics}
        can_pay = (
            len(owner_ids) == 1
            and city.owner_faction_id in owner_ids
            and city.resources.ether >= maintenance_cost
        )
        if can_pay:
            city.resources.ether -= maintenance_cost
            altar.state = "active"
            altar.damaged_until_month = None
            event_name = "maintenance_paid"
            summary = f"{city.name}支付 {maintenance_cost} 以太，祭坛维持启用。"
            category = "relic_maintenance_paid"
            owner = next(
                (
                    faction
                    for faction in next_world.factions
                    if faction.faction_id == city.owner_faction_id
                ),
                None,
            )
            eligible_relic = (
                bound_relics[0]
                if len(bound_relics) == 1 and bound_relics[0].condition == "intact"
                else None
            )
            if owner is not None and owner.is_major and eligible_relic is not None:
                was_complete = (
                    altar.consecration_faction_id == owner.faction_id
                    and altar.consecration_relic_id == eligible_relic.relic_id
                    and altar.consecration_progress >= altar.consecration_required
                )
                continues = (
                    altar.consecration_faction_id == owner.faction_id
                    and altar.consecration_relic_id == eligible_relic.relic_id
                    and altar.consecration_last_month == next_world.current_month - 1
                )
                if not continues:
                    _reset_altar_consecration(
                        altar,
                        month=next_world.current_month,
                        cause="consecration_restarted",
                        summary=f"{altar.name}重新开始连续维护准备。",
                    )
                    altar.consecration_faction_id = owner.faction_id
                    altar.consecration_relic_id = eligible_relic.relic_id
                    altar.consecration_progress = 1
                    altar.consecration_started_month = next_world.current_month
                else:
                    altar.consecration_progress = min(
                        altar.consecration_required,
                        altar.consecration_progress + 1,
                    )
                altar.consecration_last_month = next_world.current_month
                consecration_complete = (
                    altar.consecration_progress >= altar.consecration_required
                )
                if not was_complete:
                    consecration_summary = (
                        f"{owner.name}在{altar.name}完成圣物胜利准备。"
                        if consecration_complete
                        else (
                            f"{owner.name}在{altar.name}的圣物胜利准备推进至 "
                            f"{altar.consecration_progress}/{altar.consecration_required}。"
                        )
                    )
                    altar.history.append(
                        {
                            "month": next_world.current_month,
                            "event": (
                                "consecration_completed"
                                if consecration_complete
                                else "consecration_advanced"
                            ),
                            "faction_id": owner.faction_id,
                            "relic_id": eligible_relic.relic_id,
                            "progress": altar.consecration_progress,
                            "required": altar.consecration_required,
                            "summary": consecration_summary,
                        }
                    )
                    next_world.event_log.append(
                        EventLogEntry(
                            month=next_world.current_month,
                            category=(
                                "relic_altar_consecration_completed"
                                if consecration_complete
                                else "relic_altar_consecration_advanced"
                            ),
                            message=consecration_summary,
                            related_ids=[
                                owner.faction_id,
                                city.city_id,
                                altar.altar_id,
                                eligible_relic.relic_id,
                            ],
                        )
                    )
            else:
                _reset_altar_consecration(
                    altar,
                    month=next_world.current_month,
                    cause="ineligible_altar",
                    summary=f"{altar.name}不满足主要势力完整圣物准备条件，进度清零。",
                )
        else:
            progress_reset = _reset_altar_consecration(
                altar,
                month=next_world.current_month,
                cause="maintenance_failed",
                summary=f"{city.name}维护失败，{altar.name}的胜利准备清零。",
            )
            altar.state = "damaged"
            altar.damaged_until_month = next_world.current_month
            event_name = "maintenance_failed"
            summary = (
                f"{city.name}无法全额支付 {maintenance_cost} 以太，祭坛进入失养"
                + ("，圣物胜利准备清零。" if progress_reset else "。")
            )
            category = "relic_maintenance_failed"
        altar.history.append(
            {
                "month": next_world.current_month,
                "event": event_name,
                "ether_cost": maintenance_cost,
                "summary": summary,
            }
        )
        for relic in bound_relics:
            relic.history.append(
                {
                    "month": next_world.current_month,
                    "event": event_name,
                    "altar_id": altar.altar_id,
                    "ether_cost": relic.maintenance_ether_cost,
                    "summary": summary,
                }
            )
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category=category,
                message=summary,
                related_ids=[city.city_id, altar.altar_id, *altar.bound_relic_ids],
            )
        )
    next_world.validate()
    return next_world


def relic_system_enabled(world: WorldState) -> bool:
    return str(world.campaign_contract.get("id") or "") == FIRST_CAMPAIGN_SCENARIO_ID


def _initial_relic_node_id(world: WorldState, hero_code: str) -> str | None:
    nodes = sorted(world.nodes, key=lambda item: item.node_id)
    if not nodes:
        return None
    return nodes[_stable_index(world, f"relic-location:{hero_code}", len(nodes))].node_id


def _initial_altar_city_ids(world: WorldState) -> list[str]:
    return sorted(
        {
            str(faction.capital_city_id)
            for faction in world.factions
            if faction.is_major and faction.capital_city_id
        }
    )


def _initial_clue_node_id(world: WorldState, faction_id: str) -> str | None:
    faction = next((item for item in world.factions if item.faction_id == faction_id), None)
    capital = next(
        (city for city in world.cities if faction is not None and city.city_id == faction.capital_city_id),
        None,
    )
    if capital is None:
        return None
    connected = set(next(node.connected_node_ids for node in world.nodes if node.node_id == capital.node_id))
    adjacent_cities = sorted(
        (
            city
            for city in world.cities
            if city.node_id in connected and city.owner_faction_id != faction_id
        ),
        key=lambda item: item.city_id,
    )
    candidates = adjacent_cities or sorted(
        (city for city in world.cities if city.owner_faction_id != faction_id),
        key=lambda item: item.city_id,
    )
    if not candidates:
        return capital.node_id
    return candidates[_stable_index(world, f"initial-relic-clue-city:{faction_id}", len(candidates))].node_id


def ensure_relic_system(world: WorldState) -> WorldState:
    if not relic_system_enabled(world):
        return world
    expected_altar_city_ids = set(_initial_altar_city_ids(world))
    system_complete = (
        {item.hero_code for item in world.relics}
        == {item.hero_code for item in world.strategic_heroes}
        and {item.city_id for item in world.relic_altars}.issuperset(expected_altar_city_ids)
    )
    available_routes = list(world.campaign_contract.get("available_victory_routes") or [])
    locked_systems = list(world.campaign_contract.get("locked_systems") or [])
    contract_current = (
        "relic_altar_victory" in available_routes
        and "relic_altar" not in locked_systems
        and RELIC_VICTORY_VERSION in world.memory_tags
    )
    if system_complete and contract_current:
        return world

    next_world = _clone_world(world)
    available_routes = list(next_world.campaign_contract.get("available_victory_routes") or [])
    if "relic_altar_victory" not in available_routes:
        available_routes.append("relic_altar_victory")
    next_world.campaign_contract["available_victory_routes"] = available_routes
    next_world.campaign_contract["locked_systems"] = [
        item
        for item in next_world.campaign_contract.get("locked_systems", [])
        if item != "relic_altar"
    ]
    if RELIC_VICTORY_VERSION not in next_world.memory_tags:
        next_world.memory_tags.append(RELIC_VICTORY_VERSION)
    if system_complete:
        next_world.validate()
        return next_world
    from wujiang.strategic.heroes import strategic_hero_pool_public

    hero_names = {
        str(item.get("code") or ""): str(item.get("name") or item.get("code") or "")
        for item in strategic_hero_pool_public(next_world)
    }
    relics_by_hero = {item.hero_code: item for item in next_world.relics}
    for hero in sorted(next_world.strategic_heroes, key=lambda item: item.hero_code):
        if hero.hero_code in relics_by_hero:
            continue
        hero_name = hero_names.get(hero.hero_code, hero.hero_code)
        relics_by_hero[hero.hero_code] = RelicState(
            relic_id=f"relic:{hero.hero_code}",
            hero_code=hero.hero_code,
            name=f"{hero_name}的圣物",
            state="scattered",
            condition="intact",
            location_node_id=_initial_relic_node_id(next_world, hero.hero_code),
            maintenance_ether_cost=DEFAULT_RELIC_MAINTENANCE_ETHER,
            last_changed_month=next_world.current_month,
        )
    next_world.relics = sorted(relics_by_hero.values(), key=lambda item: item.relic_id)

    altars_by_city = {item.city_id: item for item in next_world.relic_altars}
    for city_id in _initial_altar_city_ids(next_world):
        if city_id in altars_by_city:
            continue
        city = next(city for city in next_world.cities if city.city_id == city_id)
        altar = RelicAltar(
            altar_id=f"relic_altar:{city_id}",
            city_id=city_id,
            name=f"{city.name}圣物祭坛",
            state="dormant",
            history=[
                {
                    "month": next_world.current_month,
                    "event": "altar_archived",
                    "summary": "古代祭坛已登记；P6.1 暂不开放绑定行动。",
                }
            ],
        )
        next_world.relic_altars.append(altar)
        altars_by_city[city_id] = altar
    next_world.relic_altars.sort(key=lambda item: item.altar_id)
    altar_ids_by_city = {item.city_id: item.altar_id for item in next_world.relic_altars}
    for city in next_world.cities:
        altar_id = altar_ids_by_city.get(city.city_id)
        if altar_id and altar_id not in city.altars:
            city.altars.append(altar_id)
            city.altars.sort()

    available_relics = list(next_world.relics)
    for faction in sorted(
        (item for item in next_world.factions if item.is_major),
        key=lambda item: item.faction_id,
    ):
        if any(faction.faction_id in relic.discovered_by_faction_ids for relic in available_relics):
            continue
        undiscovered = [
            relic
            for relic in available_relics
            if not relic.discovered_by_faction_ids
        ] or available_relics
        if not undiscovered:
            break
        relic = undiscovered[
            _stable_index(next_world, f"initial-relic-clue:{faction.faction_id}", len(undiscovered))
        ]
        clue_node_id = _initial_clue_node_id(next_world, faction.faction_id)
        if clue_node_id is not None:
            relic.location_node_id = clue_node_id
        if faction.faction_id not in relic.discovered_by_faction_ids:
            relic.discovered_by_faction_ids.append(faction.faction_id)
            relic.discovered_by_faction_ids.sort()
            relic.history.append(
                {
                    "month": next_world.current_month,
                    "event": "initial_rumor",
                    "faction_id": faction.faction_id,
                    "summary": "势力在邻近区域掌握了一条圣物传闻。",
                }
            )

    next_world.memory_tags.append(RELIC_SYSTEM_VERSION)
    next_world.memory_tags = list(dict.fromkeys(next_world.memory_tags))
    next_world.validate()
    return next_world


def _relic_public(relic: RelicState, world: WorldState) -> dict[str, Any]:
    node = next((item for item in world.nodes if item.node_id == relic.location_node_id), None)
    city = next((item for item in world.cities if item.city_id == relic.location_city_id), None)
    if city is None and node is not None:
        city = next((item for item in world.cities if item.node_id == node.node_id), None)
    return {
        "id": relic.relic_id,
        "hero_code": relic.hero_code,
        "name": relic.name,
        "state": relic.state,
        "state_label": {
            "scattered": "散落",
            "stored": "保管",
            "bound_to_altar": "祭坛绑定",
            "released": "已释放",
        }.get(relic.state, relic.state),
        "condition": relic.condition,
        "condition_label": "受损" if relic.condition == "damaged" else "完整",
        "location_node_id": relic.location_node_id,
        "location_node_name": node.name if node is not None else "",
        "location_city_id": city.city_id if city is not None else relic.location_city_id,
        "location_city_name": city.name if city is not None else "",
        "owner_faction_id": relic.owner_faction_id,
        "altar_id": relic.altar_id,
        "maintenance_ether_cost": relic.maintenance_ether_cost,
        "last_changed_month": relic.last_changed_month,
    }


def relic_system_public(world: WorldState) -> dict[str, Any]:
    from wujiang.strategic.hero_personal import hero_command_accepts

    if not relic_system_enabled(world):
        return {
            "enabled": False,
            "phase": "locked",
            "total_relics": 0,
            "altars": [],
            "intel_by_faction": {},
        }
    ensured = ensure_relic_system(world)
    majors = sorted(
        (item for item in ensured.factions if item.is_major),
        key=lambda item: item.faction_id,
    )
    intel_by_faction: dict[str, Any] = {}
    for faction in majors:
        known = [
            _relic_public(relic, ensured)
            for relic in ensured.relics
            if faction.faction_id in relic.discovered_by_faction_ids
        ]
        search_options: list[dict[str, Any]] = []
        for relic in ensured.relics:
            if (
                faction.faction_id not in relic.discovered_by_faction_ids
                or relic.state not in {"scattered", "released"}
            ):
                continue
            clue = _relic_public(relic, ensured)
            origins: list[dict[str, Any]] = []
            for hero in ensured.strategic_heroes:
                if (
                    hero.faction_id != faction.faction_id
                    or hero.status != "serving"
                    or hero.city_id is None
                    or hero.last_personal_action_month == ensured.current_month
                    or (
                        hero.sleeping_until_month is not None
                        and hero.sleeping_until_month >= ensured.current_month
                    )
                ):
                    continue
                city = next((item for item in ensured.cities if item.city_id == hero.city_id), None)
                if city is None or city.owner_faction_id != faction.faction_id:
                    continue
                if relic.location_node_id not in {city.node_id, *_node_neighbors(ensured, city.node_id)}:
                    continue
                accepts_command = hero_command_accepts(ensured, hero, "relic_search")
                has_food = city.resources.food >= RELIC_SEARCH_FOOD_COST
                origins.append(
                    {
                        "hero_code": hero.hero_code,
                        "hero_name": _hero_name(ensured, hero.hero_code),
                        "city_id": city.city_id,
                        "city_name": city.name,
                        "available": has_food and accepts_command,
                        "reason": (
                            ""
                            if has_food and accepts_command
                            else (
                                f"{city.name}粮食不足 {RELIC_SEARCH_FOOD_COST}"
                                if not has_food
                                else "该英灵本月拒绝圣物搜索命令"
                            )
                        ),
                    }
                )
            search_options.append(
                {
                    "relic_id": relic.relic_id,
                    "relic_name": relic.name,
                    "clue_city_id": clue["location_city_id"],
                    "clue_city_name": clue["location_city_name"],
                    "origins": origins,
                    "command_cost": 1,
                    "food_cost": RELIC_SEARCH_FOOD_COST,
                    "damage_risk_percent": 25,
                }
            )
        transfer_options: list[dict[str, Any]] = []
        repair_options: list[dict[str, Any]] = []
        binding_options: list[dict[str, Any]] = []
        release_options: list[dict[str, Any]] = []
        for relic in ensured.relics:
            if relic.state != "stored" or relic.owner_faction_id != faction.faction_id or not relic.location_city_id:
                continue
            source = _city(ensured, relic.location_city_id)
            targets = [
                {"city_id": city.city_id, "city_name": city.name}
                for city in ensured.cities
                if city.owner_faction_id == faction.faction_id
                and city.node_id in _node_neighbors(ensured, source.node_id)
            ]
            transfer_options.append(
                {
                    "relic_id": relic.relic_id,
                    "relic_name": relic.name,
                    "source_city_id": source.city_id,
                    "source_city_name": source.name,
                    "targets": sorted(targets, key=lambda item: str(item["city_id"])),
                    "available": bool(targets) and source.resources.food >= RELIC_TRANSFER_FOOD_COST,
                    "command_cost": 1,
                    "food_cost": RELIC_TRANSFER_FOOD_COST,
                }
            )
            if relic.condition == "damaged":
                repair_options.append(
                    {
                        "relic_id": relic.relic_id,
                        "relic_name": relic.name,
                        "city_id": source.city_id,
                        "city_name": source.name,
                        "available": (
                            faction.resources.money >= RELIC_REPAIR_MONEY_COST
                            and source.resources.ether >= RELIC_REPAIR_ETHER_COST
                        ),
                        "command_cost": 1,
                        "money_cost": RELIC_REPAIR_MONEY_COST,
                        "ether_cost": RELIC_REPAIR_ETHER_COST,
                    }
                )
            if relic.condition == "intact":
                altar = next(
                    (
                        item
                        for item in ensured.relic_altars
                        if item.city_id == source.city_id
                        and len(item.bound_relic_ids) < item.capacity
                    ),
                    None,
                )
                if altar is not None:
                    binding_options.append(
                        {
                            "relic_id": relic.relic_id,
                            "relic_name": relic.name,
                            "altar_id": altar.altar_id,
                            "altar_name": altar.name,
                            "city_id": source.city_id,
                            "city_name": source.name,
                            "available": (
                                source.owner_faction_id == faction.faction_id
                                and relic_altar_actions_remaining(ensured, altar) > 0
                            ),
                            "command_cost": 1,
                            "maintenance_ether_cost": relic.maintenance_ether_cost,
                            "altar_actions_remaining": relic_altar_actions_remaining(ensured, altar),
                        }
                    )
        for relic in ensured.relics:
            if (
                relic.state != "bound_to_altar"
                or relic.owner_faction_id != faction.faction_id
                or relic.altar_id is None
            ):
                continue
            altar = next(item for item in ensured.relic_altars if item.altar_id == relic.altar_id)
            city = _city(ensured, altar.city_id)
            release_options.append(
                {
                    "relic_id": relic.relic_id,
                    "relic_name": relic.name,
                    "altar_id": altar.altar_id,
                    "altar_name": altar.name,
                    "city_id": city.city_id,
                    "city_name": city.name,
                    "available": relic_altar_actions_remaining(ensured, altar) > 0,
                    "command_cost": 1,
                    "altar_actions_remaining": relic_altar_actions_remaining(ensured, altar),
                }
            )
        intel_by_faction[faction.faction_id] = {
            "known_relics": known,
            "known_count": len(known),
            "unknown_count": max(0, len(ensured.relics) - len(known)),
            "search_options": search_options,
            "transfer_options": transfer_options,
            "repair_options": repair_options,
            "binding_options": binding_options,
            "release_options": release_options,
        }
    altars: list[dict[str, Any]] = []
    for altar in ensured.relic_altars:
        city = next((item for item in ensured.cities if item.city_id == altar.city_id), None)
        bound_relics = [
            relic
            for relic in ensured.relics
            if relic.relic_id in altar.bound_relic_ids
        ]
        maintenance_cost = sum(relic.maintenance_ether_cost for relic in bound_relics)
        altars.append(
            {
                "id": altar.altar_id,
                "name": altar.name,
                "city_id": altar.city_id,
                "city_name": city.name if city is not None else "",
                "owner_faction_id": city.owner_faction_id if city is not None else None,
                "level": altar.level,
                "state": altar.state,
                "state_label": {
                    "dormant": "沉寂",
                    "active": "启用",
                    "damaged": "失养",
                }.get(altar.state, altar.state),
                "capacity": altar.capacity,
                "bound_count": len(altar.bound_relic_ids),
                "damaged_until_month": altar.damaged_until_month,
                "monthly_maintenance_ether": maintenance_cost,
                "maintenance_affordable": (
                    city is not None
                    and city.resources.ether >= maintenance_cost
                    and all(relic.owner_faction_id == city.owner_faction_id for relic in bound_relics)
                ),
                "actions_used": altar.actions_used if altar.action_month == ensured.current_month else 0,
                "actions_remaining": relic_altar_actions_remaining(ensured, altar),
                "consecration": {
                    "faction_id": altar.consecration_faction_id,
                    "relic_id": altar.consecration_relic_id,
                    "progress": altar.consecration_progress,
                    "required": altar.consecration_required,
                    "started_month": altar.consecration_started_month,
                    "last_progress_month": altar.consecration_last_month,
                    "earliest_completion_month": (
                        ensured.current_month
                        + max(0, altar.consecration_required - altar.consecration_progress)
                        if altar.bound_relic_ids and city is not None
                        else None
                    ),
                    "active": altar.consecration_progress > 0,
                    "completed": (
                        altar.consecration_progress >= altar.consecration_required
                    ),
                },
            }
        )
    return {
        "enabled": True,
        "phase": "p6_6_relic_victory",
        "version": RELIC_SYSTEM_VERSION,
        "total_relics": len(ensured.relics),
        "altars": altars,
        "intel_by_faction": intel_by_faction,
        "rules": {
            "ritual_site": "祭祀场：1 军令 + 30 城市以太，确定性随机召唤未绑定英灵。",
            "relic_altar": "圣物祭坛：绑定完整圣物后，连续完成 3 次月初全额维护即可达成圣物胜利；断供、释放或易主会清零进度。",
            "current_scope": "P6.6 已开放圣物祭坛胜利、公开准备进度与同规则 AI 竞速/反制。",
        },
    }
