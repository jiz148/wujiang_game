from __future__ import annotations

import copy

from wujiang.strategy.models import EventLogEntry, Faction, StrategyError, WorldState


NEUTRAL_INCITEMENT_MONEY_COST = 60


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def faction_by_id(world: WorldState, faction_id: str) -> Faction:
    faction = next((item for item in world.factions if item.faction_id == str(faction_id)), None)
    if faction is None:
        raise StrategyError("势力不存在。")
    return faction


def adjacent_city_ids(world: WorldState, city_id: str) -> set[str]:
    city = next((item for item in world.cities if item.city_id == str(city_id)), None)
    if city is None:
        return set()
    node = next((item for item in world.nodes if item.node_id == city.node_id), None)
    if node is None:
        return set()
    connected = set(node.connected_node_ids)
    return {item.city_id for item in world.cities if item.node_id in connected}


def incitement_attack_pair(world: WorldState, neutral_faction_id: str) -> tuple[str, str] | None:
    neutral = faction_by_id(world, neutral_faction_id)
    target_faction_id = str(neutral.incited_against_faction_id or "")
    if not neutral.is_neutral_city_state or not target_faction_id:
        return None
    owned = sorted(
        (city for city in world.cities if city.owner_faction_id == neutral.faction_id),
        key=lambda city: (-city.resources.troops, city.city_id),
    )
    cities_by_id = {city.city_id: city for city in world.cities}
    candidates: list[tuple[int, int, str, str]] = []
    for source in owned:
        for target_id in adjacent_city_ids(world, source.city_id):
            target = cities_by_id[target_id]
            if target.owner_faction_id == target_faction_id:
                candidates.append((target.resources.troops, target.defense, source.city_id, target.city_id))
    if not candidates:
        return None
    _, _, source_city_id, target_city_id = min(candidates)
    return source_city_id, target_city_id


def validate_neutral_city_state_incitement(
    world: WorldState,
    *,
    instigator_faction_id: str,
    neutral_faction_id: str,
    target_faction_id: str,
) -> None:
    instigator = faction_by_id(world, instigator_faction_id)
    neutral = faction_by_id(world, neutral_faction_id)
    target = faction_by_id(world, target_faction_id)
    if instigator.is_neutral_city_state:
        raise StrategyError("中立城邦不能教唆其他城邦。")
    if not neutral.is_neutral_city_state:
        raise StrategyError("只能教唆中立城邦。")
    if target.is_neutral_city_state:
        raise StrategyError("教唆目标必须是玩家或主要 AI 势力。")
    if target.faction_id == instigator.faction_id:
        raise StrategyError("不能教唆中立城邦攻击己方。")
    if instigator.resources.money < NEUTRAL_INCITEMENT_MONEY_COST:
        raise StrategyError(f"教唆中立城邦需要 {NEUTRAL_INCITEMENT_MONEY_COST} 金钱。")
    from wujiang.strategy.diplomacy import diplomacy_cooldown_until

    cooldown_until = diplomacy_cooldown_until(world, instigator.faction_id, neutral.faction_id, "incite")
    if world.current_month < cooldown_until:
        raise StrategyError(f"教唆该城邦冷却至第 {cooldown_until} 月。")

    neutral_city_ids = [city.city_id for city in world.cities if city.owner_faction_id == neutral.faction_id]
    target_city_ids = {city.city_id for city in world.cities if city.owner_faction_id == target.faction_id}
    if not neutral_city_ids:
        raise StrategyError("该中立城邦已经失去城市，无法被教唆。")
    if not any(adjacent_city_ids(world, city_id) & target_city_ids for city_id in neutral_city_ids):
        raise StrategyError("该中立城邦没有与目标势力接壤。")


def incite_neutral_city_state(
    world: WorldState,
    *,
    instigator_faction_id: str,
    neutral_faction_id: str,
    target_faction_id: str,
) -> WorldState:
    validate_neutral_city_state_incitement(
        world,
        instigator_faction_id=instigator_faction_id,
        neutral_faction_id=neutral_faction_id,
        target_faction_id=target_faction_id,
    )
    next_world = _clone_world(world)
    instigator = faction_by_id(next_world, instigator_faction_id)
    neutral = faction_by_id(next_world, neutral_faction_id)
    target = faction_by_id(next_world, target_faction_id)
    instigator.resources.money -= NEUTRAL_INCITEMENT_MONEY_COST
    neutral.incited_against_faction_id = target.faction_id
    neutral.incited_by_faction_id = instigator.faction_id
    neutral.relations[instigator.faction_id] = max(-100, neutral.relations.get(instigator.faction_id, 0) - 20)
    from wujiang.strategy.peaceful_integration import adjust_neutral_influence

    adjust_neutral_influence(
        next_world,
        major_faction_id=instigator.faction_id,
        neutral_faction_id=neutral.faction_id,
        influence_delta=-15,
        support_delta=-8,
    )
    from wujiang.strategy.diplomacy import record_diplomatic_memory, set_diplomacy_cooldown

    set_diplomacy_cooldown(next_world, instigator.faction_id, neutral.faction_id, "incite")
    record_diplomatic_memory(
        next_world,
        category="incitement",
        major=instigator,
        neutral=neutral,
        summary=f"{instigator.name}教唆{neutral.name}攻击{target.name}，其外交信誉受损。",
        action_id="incite",
        reputation_delta=-5,
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="neutral_city_state_incited",
            message=f"{instigator.name}教唆{neutral.name}攻击{target.name}。",
            related_ids=[instigator.faction_id, neutral.faction_id, target.faction_id],
        )
    )
    next_world.validate()
    return next_world
