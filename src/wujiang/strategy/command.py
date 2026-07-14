from __future__ import annotations

from typing import Any, Iterable

from wujiang.strategy.models import City, Faction, WorldState
from wujiang.strategy.rebellion import rebellion_force_troops


FACTION_MONTHLY_COMMAND_POINTS = 4


def strategy_action_command_cost(action_type: str, payload: dict[str, Any] | None = None) -> int:
    normalized_type = str(action_type or "").strip()
    action_payload = payload or {}
    if normalized_type == "declare_attack":
        return 2
    if normalized_type == "rebellion_battle":
        return 2
    if normalized_type == "rebellion_action":
        action_id = str(action_payload.get("rebellion_action_id") or action_payload.get("action_id") or "")
        return 2 if action_id == "suppress" else 1
    if normalized_type in {"send_office_request", "request_registered_units"}:
        return 0
    if normalized_type in {"approve_registered_unit_request", "assign_strategic_hero_duty"}:
        return 0
    if normalized_type == "issue_office_order":
        return 1
    if normalized_type in {
        "set_city_policy",
        "unlock_tactic_tech",
        "perform_hero_ritual",
        "unbind_strategic_hero",
        "exile_action",
        "resolve_story_event",
        "increase_city_troops",
        "register_city_soldiers",
        "transfer_registered_units",
        "construct_city_building",
    }:
        return 1
    return 1


def faction_command_points(
    faction_id: str,
    queued_actions: Iterable[Any],
    *,
    exclude_action_type: str = "",
    exclude_action_key: str = "",
) -> dict[str, int]:
    used = 0
    for action in queued_actions:
        if str(getattr(action, "faction_id", "")) != str(faction_id):
            continue
        if (
            exclude_action_type
            and str(getattr(action, "action_type", "")) == exclude_action_type
            and str(getattr(action, "action_key", "")) == exclude_action_key
        ):
            continue
        used += strategy_action_command_cost(
            str(getattr(action, "action_type", "")),
            getattr(action, "payload", {}) or {},
        )
    return {
        "maximum": FACTION_MONTHLY_COMMAND_POINTS,
        "used": used,
        "remaining": max(0, FACTION_MONTHLY_COMMAND_POINTS - used),
    }


def _adjacent_city_pairs(world: WorldState) -> list[tuple[City, City]]:
    cities_by_node = {city.node_id: city for city in world.cities}
    pairs: list[tuple[City, City]] = []
    seen: set[tuple[str, str]] = set()
    for node in world.nodes:
        source = cities_by_node.get(node.node_id)
        if source is None:
            continue
        for target_node_id in node.connected_node_ids:
            target = cities_by_node.get(target_node_id)
            if target is None:
                continue
            key = tuple(sorted((source.city_id, target.city_id)))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((source, target))
    return pairs


def _owner_support(city: City) -> int:
    return int(city.support_by_faction.get(city.owner_faction_id, 50))


def _briefing_entry(kind: str, title: str, detail: str, *, city_id: str = "", severity: str = "info") -> dict[str, str]:
    payload = {"kind": kind, "title": title, "detail": detail, "severity": severity}
    if city_id:
        payload["city_id"] = city_id
    return payload


def _threat_for_faction(faction: Faction, owned: list[City], borders: list[tuple[City, City]]) -> dict[str, str]:
    rebel_city = max(owned, key=lambda city: rebellion_force_troops(city), default=None)
    if rebel_city is not None and rebellion_force_troops(rebel_city) > 0:
        force = rebellion_force_troops(rebel_city)
        return _briefing_entry(
            "threat",
            f"{rebel_city.name}叛军集结",
            f"叛军规模 {force}，若继续放任将损耗守军与民心。",
            city_id=rebel_city.city_id,
            severity="critical",
        )

    unstable = min(owned, key=_owner_support, default=None)
    if unstable is not None and _owner_support(unstable) < 45:
        support = _owner_support(unstable)
        return _briefing_entry(
            "threat",
            f"{unstable.name}民心动摇",
            f"当前统治支持度仅 {support}，粮荒或征兵可能引发叛乱。",
            city_id=unstable.city_id,
            severity="warning",
        )

    dangerous: list[tuple[int, City, City]] = []
    for own_city, enemy_city in borders:
        if own_city.owner_faction_id != faction.faction_id:
            own_city, enemy_city = enemy_city, own_city
        if own_city.owner_faction_id != faction.faction_id or enemy_city.owner_faction_id == faction.faction_id:
            continue
        gap = enemy_city.resources.troops - own_city.resources.troops
        dangerous.append((gap, own_city, enemy_city))
    if dangerous:
        gap, own_city, enemy_city = max(dangerous, key=lambda item: (item[0], item[2].city_id))
        if gap > 0:
            return _briefing_entry(
                "threat",
                f"{own_city.name}边境承压",
                f"邻接的 {enemy_city.name} 比本城多约 {gap} 兵力，建议增兵或准备防守。",
                city_id=own_city.city_id,
                severity="warning",
            )

    capital = next((city for city in owned if city.city_id == faction.capital_city_id), owned[0] if owned else None)
    return _briefing_entry(
        "threat",
        "边境暂时平稳",
        "本月没有迫近的叛乱或明显兵力劣势，可主动规划扩张。",
        city_id=capital.city_id if capital else "",
    )


def _opportunity_for_faction(faction: Faction, owned: list[City], borders: list[tuple[City, City]]) -> dict[str, str]:
    openings: list[tuple[int, City, City]] = []
    for own_city, enemy_city in borders:
        if own_city.owner_faction_id != faction.faction_id:
            own_city, enemy_city = enemy_city, own_city
        if own_city.owner_faction_id != faction.faction_id or enemy_city.owner_faction_id == faction.faction_id:
            continue
        advantage = own_city.resources.troops - enemy_city.resources.troops
        openings.append((advantage, own_city, enemy_city))
    if openings:
        advantage, own_city, enemy_city = max(openings, key=lambda item: (item[0], item[2].city_id))
        if advantage >= 100:
            return _briefing_entry(
                "opportunity",
                f"{enemy_city.name}防线薄弱",
                f"从 {own_city.name} 出征约有 {advantage} 兵力优势，适合发动进攻。",
                city_id=own_city.city_id,
                severity="positive",
            )

    ether_city = max(owned, key=lambda city: (city.resources.ether, city.city_id), default=None)
    if ether_city is not None:
        return _briefing_entry(
            "opportunity",
            f"{ether_city.name}以太充盈",
            f"城内储有 {ether_city.resources.ether} 以太，可优先召唤英灵或发展侦察科技。",
            city_id=ether_city.city_id,
            severity="positive",
        )
    return _briefing_entry("opportunity", "重整旗鼓", "当前没有明显战机，优先积累资源并寻找新的突破口。")


def _rival_intent_for_faction(faction: Faction, world: WorldState, borders: list[tuple[City, City]]) -> dict[str, str]:
    contacts: list[tuple[int, City, City]] = []
    for first, second in borders:
        if first.owner_faction_id == faction.faction_id and second.owner_faction_id != faction.faction_id:
            contacts.append((second.resources.troops, first, second))
        elif second.owner_faction_id == faction.faction_id and first.owner_faction_id != faction.faction_id:
            contacts.append((first.resources.troops, second, first))
    if contacts:
        enemy_troops, own_city, enemy_city = max(contacts, key=lambda item: (item[0], item[2].city_id))
        rival = next((item for item in world.factions if item.faction_id == enemy_city.owner_faction_id), None)
        rival_name = rival.name if rival else enemy_city.owner_faction_id
        posture = "可能准备进攻" if enemy_troops > own_city.resources.troops else "正在巩固边境"
        return _briefing_entry(
            "rival_intent",
            f"斥候推测：{rival_name}{posture}",
            f"其边境城市 {enemy_city.name} 驻有约 {enemy_troops} 兵力，情报并非完全可靠。",
            city_id=own_city.city_id,
            severity="warning" if enemy_troops > own_city.resources.troops else "info",
        )
    return _briefing_entry("rival_intent", "尚未接触敌对边境", "斥候没有发现直接接壤的敌军动向。")


def monthly_briefings_public(world: WorldState) -> dict[str, dict[str, Any]]:
    borders = [pair for pair in _adjacent_city_pairs(world) if pair[0].owner_faction_id != pair[1].owner_faction_id]
    result: dict[str, dict[str, Any]] = {}
    for faction in world.factions:
        owned = [city for city in world.cities if city.owner_faction_id == faction.faction_id]
        result[faction.faction_id] = {
            "month": world.current_month,
            "faction_id": faction.faction_id,
            "entries": [
                _threat_for_faction(faction, owned, borders),
                _opportunity_for_faction(faction, owned, borders),
                _rival_intent_for_faction(faction, world, borders),
            ],
        }
    return result
