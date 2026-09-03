"""Rare resources: veins, monthly yield, quotes and money-settled trade."""
from __future__ import annotations

import copy
import hashlib
import random
from typing import Any

from wujiang.strategic.catalog import (
    opening_hero_fill_mode,
    random_campaign_vein_rules,
    rare_resource_def,
    rare_resource_defs,
    rare_resource_ids,
    trade_rules,
)
from wujiang.strategic.models import EventLogEntry, Faction, StrategyError, TradeOffer, WorldState


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _faction(world: WorldState, faction_id: str) -> Faction:
    for faction in world.factions:
        if faction.faction_id == faction_id:
            return faction
    raise StrategyError("势力不存在。")


def empty_rare_stock() -> dict[str, int]:
    return {item["id"]: 0 for item in rare_resource_defs()}


def normalize_rare_stock(raw: Any) -> dict[str, int]:
    stock = empty_rare_stock()
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key not in stock:
                continue
            try:
                stock[str(key)] = max(0, int(value))
            except (TypeError, ValueError):
                stock[str(key)] = 0
    return stock


def trade_good_defs() -> list[dict[str, Any]]:
    goods = [
        {
            "id": item["id"],
            "name": item["name"],
            "kind": "rare",
            "building_id": item["building_id"],
            "building_name": item["building_name"],
            "flavor": item["flavor"],
            "base_price": item["base_price"],
        }
        for item in rare_resource_defs()
    ]
    for item in trade_rules()["basic_goods"]:
        goods.append(
            {
                "id": item["id"],
                "name": item["name"],
                "kind": "basic",
                "building_id": "",
                "building_name": "",
                "flavor": "",
                "base_price": item["base_price"],
            }
        )
    return goods


def trade_good_def(resource_id: str) -> dict[str, Any] | None:
    return next((item for item in trade_good_defs() if item["id"] == str(resource_id)), None)


def faction_good_stock(faction: Faction, resource_id: str) -> int:
    if resource_id in rare_resource_ids():
        return max(0, int((faction.rare_resources or {}).get(resource_id, 0)))
    if resource_id == "food":
        return max(0, int(faction.resources.food))
    if resource_id == "ether":
        return max(0, int(faction.resources.ether))
    if resource_id == "money":
        return max(0, int(faction.resources.money))
    raise StrategyError("不能交易这种资源。")


def set_faction_good_stock(faction: Faction, resource_id: str, amount: int) -> None:
    value = max(0, int(amount))
    if resource_id in rare_resource_ids():
        faction.rare_resources[resource_id] = value
        return
    if resource_id == "food":
        faction.resources.food = value
        return
    if resource_id == "ether":
        faction.resources.ether = value
        return
    if resource_id == "money":
        faction.resources.money = value
        return
    raise StrategyError("不能交易这种资源。")


def quote_unit_price(faction: Faction, resource_id: str) -> int:
    good = trade_good_def(resource_id)
    if good is None:
        raise StrategyError("未知商品。")
    rules = trade_rules()
    stock = max(0, faction_good_stock(faction, resource_id))
    factor = rules["quote_reference_stock"] / max(1, stock)
    factor = max(rules["min_price_factor"], min(rules["max_price_factor"], factor))
    return max(1, int(round(int(good["base_price"]) * factor)))


def suggested_trade_money(*, counterpart: Faction, resource_id: str, amount: int) -> int:
    return quote_unit_price(counterpart, resource_id) * max(1, int(amount))


def city_vein_yield(city, resource_id: str) -> int:
    spec = rare_resource_def(resource_id)
    if spec is None:
        return 0
    return max(0, int((getattr(city, "veins", None) or {}).get(resource_id, 0))) * int(spec["yield_per_vein"])


def faction_vein_counts(world: WorldState, faction_id: str) -> dict[str, int]:
    counts = empty_rare_stock()
    for city in world.cities:
        if city.owner_faction_id != faction_id:
            continue
        for resource_id, amount in dict(getattr(city, "veins", None) or {}).items():
            if resource_id in counts:
                counts[resource_id] += max(0, int(amount))
    return counts


def faction_monthly_rare_income(world: WorldState, faction_id: str) -> dict[str, int]:
    income = empty_rare_stock()
    for resource_id in income:
        spec = rare_resource_def(resource_id)
        if spec is None:
            continue
        income[resource_id] = faction_vein_counts(world, faction_id)[resource_id] * int(spec["yield_per_vein"])
    return income


def apply_monthly_vein_income(world: WorldState, events: list[EventLogEntry], month: int) -> None:
    for faction in world.factions:
        if faction.is_world_crisis:
            continue
        income = faction_monthly_rare_income(world, faction.faction_id)
        gained = []
        for resource_id, amount in income.items():
            if amount <= 0:
                continue
            faction.rare_resources[resource_id] = int(faction.rare_resources.get(resource_id, 0)) + amount
            spec = rare_resource_def(resource_id)
            gained.append(f"{(spec or {}).get('name') or resource_id} +{amount}")
        if gained:
            events.append(
                EventLogEntry(
                    month=month,
                    category="rare_resource_income",
                    message=f"{faction.name}开采稀有资源：{'，'.join(gained)}。",
                    related_ids=[faction.faction_id],
                    visibility="player_visible",
                )
            )


def _place_vein_tokens(cities: list[Any], tokens: list[str], rng: random.Random, *, max_types: int) -> None:
    if not cities or not tokens:
        return
    for resource_id in tokens:
        eligible = [
            city
            for city in cities
            if resource_id in (getattr(city, "veins", None) or {})
            or len(getattr(city, "veins", None) or {}) < max_types
        ] or list(cities)
        city = rng.choice(eligible)
        city.veins[resource_id] = int((city.veins or {}).get(resource_id, 0)) + 1


def place_configured_veins(cities: list[Any], vein_counts: dict[str, int], rng: random.Random) -> None:
    tokens: list[str] = []
    for resource_id in rare_resource_ids():
        tokens.extend([resource_id] * max(0, int(vein_counts.get(resource_id, 0))))
    rng.shuffle(tokens)
    _place_vein_tokens(cities, tokens, rng, max_types=max(1, len(rare_resource_ids())))


def place_random_faction_veins(cities: list[Any], rng: random.Random, *, minimum: int, maximum: int) -> None:
    if not cities:
        return
    rules = random_campaign_vein_rules()
    total = rng.randint(min(minimum, maximum), max(minimum, maximum))
    ids = rare_resource_ids()
    tokens = [rng.choice(ids) for _ in range(total)]
    _place_vein_tokens(cities, tokens, rng, max_types=int(rules["max_vein_types_per_city"]))


def scatter_random_campaign_veins(world: WorldState, rng: random.Random) -> None:
    rules = random_campaign_vein_rules()
    for faction in world.factions:
        owned = [city for city in world.cities if city.owner_faction_id == faction.faction_id]
        if faction.is_major:
            bounds = rules["veins_per_major_faction"]
        else:
            bounds = rules["veins_per_independent"]
        place_random_faction_veins(owned, rng, minimum=int(bounds["min"]), maximum=int(bounds["max"]))
        starter = faction_monthly_rare_income(world, faction.faction_id)
        for resource_id in rare_resource_ids():
            baseline = 16 if faction.is_major else 8
            faction.rare_resources[resource_id] = (
                int(faction.rare_resources.get(resource_id, 0))
                + baseline
                + int(starter.get(resource_id, 0)) * 2
            )


def expire_trade_offers(world: WorldState, events: list[EventLogEntry], month: int) -> None:
    for offer in world.trade_offers:
        if offer.status != "pending":
            continue
        if offer.expires_month is not None and int(offer.expires_month) <= month:
            offer.status = "expired"
            try:
                proposer_name = _faction(world, offer.proposer_faction_id).name
                target_name = _faction(world, offer.target_faction_id).name
                expired_message = f"{proposer_name}向{target_name}的贸易请求已过期。"
            except StrategyError:
                expired_message = "一笔贸易请求已过期。"
            events.append(
                EventLogEntry(
                    month=month,
                    category="resource_trade",
                    message=expired_message,
                    related_ids=[offer.offer_id, offer.proposer_faction_id, offer.target_faction_id],
                    visibility="player_visible",
                )
            )


def _offer_id(world: WorldState, *, proposer_id: str, target_id: str, direction: str, resource_id: str) -> str:
    digest = hashlib.sha256(
        f"{world.seed}:{world.current_month}:{proposer_id}:{target_id}:{direction}:{resource_id}".encode("utf-8")
    ).hexdigest()[:10]
    return f"trade_{digest}"


def validate_resource_trade_proposal(
    world: WorldState,
    *,
    actor_faction_id: str,
    target_faction_id: str,
    direction: str,
    resource_id: str,
    amount: int,
    money: int,
) -> None:
    if actor_faction_id == target_faction_id:
        raise StrategyError("不能和自己做贸易。")
    actor = _faction(world, actor_faction_id)
    target = _faction(world, target_faction_id)
    if actor.is_world_crisis or target.is_world_crisis:
        raise StrategyError("不能和这场危机做贸易。")
    if direction not in {"sell", "buy"}:
        raise StrategyError("贸易方向无效。")
    if trade_good_def(resource_id) is None:
        raise StrategyError("未知商品。")
    if int(amount) <= 0:
        raise StrategyError("交易数量必须为正数。")
    if int(money) <= 0:
        raise StrategyError("贸易必须以金钱结算，金额必须为正数。")
    if direction == "sell" and faction_good_stock(actor, resource_id) < int(amount):
        raise StrategyError("己方这种资源不够出售。")
    if direction == "buy" and actor.resources.money < int(money):
        raise StrategyError("金钱不足，付不起这单。")
    pending = [
        offer
        for offer in world.trade_offers
        if offer.status == "pending"
        and offer.proposer_faction_id == actor_faction_id
        and offer.target_faction_id == target_faction_id
        and offer.resource_id == resource_id
        and offer.direction == direction
    ]
    if pending:
        raise StrategyError("对同一势力的同种商品已有一笔未决请求。")


def propose_resource_trade(
    world: WorldState,
    *,
    actor_faction_id: str,
    target_faction_id: str,
    direction: str,
    resource_id: str,
    amount: int,
    money: int,
    issuer_office_id: str = "",
) -> WorldState:
    next_world = _clone_world(world)
    validate_resource_trade_proposal(
        next_world,
        actor_faction_id=actor_faction_id,
        target_faction_id=target_faction_id,
        direction=direction,
        resource_id=resource_id,
        amount=amount,
        money=money,
    )
    actor = _faction(next_world, actor_faction_id)
    target = _faction(next_world, target_faction_id)
    good = trade_good_def(resource_id) or {"name": resource_id}
    offer = TradeOffer(
        offer_id=_offer_id(
            next_world,
            proposer_id=actor_faction_id,
            target_id=target_faction_id,
            direction=direction,
            resource_id=resource_id,
        ),
        proposer_faction_id=actor_faction_id,
        target_faction_id=target_faction_id,
        direction=direction,
        resource_id=resource_id,
        amount=int(amount),
        money=int(money),
        status="pending",
        created_month=next_world.current_month,
        expires_month=next_world.current_month + int(trade_rules()["offer_duration_months"]),
        issuer_office_id=str(issuer_office_id or ""),
    )
    next_world.trade_offers.append(offer)
    verb = "出售" if direction == "sell" else "求购"
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="resource_trade",
            message=f"{actor.name}向{target.name}{verb}{good['name']} {amount}，开价金钱 {money}。",
            related_ids=[offer.offer_id, actor_faction_id, target_faction_id, resource_id],
            visibility="player_visible",
        )
    )
    next_world.validate()
    return next_world


def _trade_offer(world: WorldState, offer_id: str) -> TradeOffer:
    offer = next((item for item in world.trade_offers if item.offer_id == str(offer_id)), None)
    if offer is None:
        raise StrategyError("贸易请求不存在。")
    return offer


def accept_resource_trade(
    world: WorldState,
    *,
    actor_faction_id: str,
    offer_id: str,
    issuer_office_id: str = "",
) -> WorldState:
    next_world = _clone_world(world)
    offer = _trade_offer(next_world, offer_id)
    if offer.status != "pending":
        raise StrategyError("这笔贸易已经结束。")
    if offer.target_faction_id != actor_faction_id:
        raise StrategyError("只能接受发给本势力的贸易请求。")
    proposer = _faction(next_world, offer.proposer_faction_id)
    target = _faction(next_world, offer.target_faction_id)
    if offer.direction == "sell":
        seller, buyer = proposer, target
    else:
        seller, buyer = target, proposer
    if faction_good_stock(seller, offer.resource_id) < offer.amount:
        raise StrategyError("卖方这种资源已经不够了。")
    if buyer.resources.money < offer.money:
        raise StrategyError("买方金钱不足。")
    set_faction_good_stock(seller, offer.resource_id, faction_good_stock(seller, offer.resource_id) - offer.amount)
    set_faction_good_stock(buyer, offer.resource_id, faction_good_stock(buyer, offer.resource_id) + offer.amount)
    seller.resources.money += offer.money
    buyer.resources.money -= offer.money
    offer.status = "accepted"
    offer.resolved_month = next_world.current_month
    offer.resolver_office_id = str(issuer_office_id or "")
    good = trade_good_def(offer.resource_id) or {"name": offer.resource_id}
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="resource_trade",
            message=f"{target.name}接受了{proposer.name}的贸易：{good['name']} {offer.amount}，金钱 {offer.money}。",
            related_ids=[offer.offer_id, proposer.faction_id, target.faction_id, offer.resource_id],
            visibility="player_visible",
        )
    )
    from wujiang.strategic.vision import reveal_diplomatic_contact

    reveal_diplomatic_contact(next_world, proposer.faction_id, target.faction_id)
    next_world.validate()
    return next_world


def reject_resource_trade(
    world: WorldState,
    *,
    actor_faction_id: str,
    offer_id: str,
    issuer_office_id: str = "",
) -> WorldState:
    next_world = _clone_world(world)
    offer = _trade_offer(next_world, offer_id)
    if offer.status != "pending":
        raise StrategyError("这笔贸易已经结束。")
    if offer.target_faction_id != actor_faction_id:
        raise StrategyError("只能拒绝发给本势力的贸易请求。")
    offer.status = "rejected"
    offer.resolved_month = next_world.current_month
    offer.resolver_office_id = str(issuer_office_id or "")
    proposer = _faction(next_world, offer.proposer_faction_id)
    target = _faction(next_world, offer.target_faction_id)
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="resource_trade",
            message=f"{target.name}拒绝了{proposer.name}的贸易请求。",
            related_ids=[offer.offer_id, proposer.faction_id, target.faction_id],
            visibility="player_visible",
        )
    )
    next_world.validate()
    return next_world


def validate_resource_trade_response(world: WorldState, *, actor_faction_id: str, offer_id: str, accept: bool) -> None:
    offer = _trade_offer(world, offer_id)
    if offer.status != "pending":
        raise StrategyError("这笔贸易已经结束。")
    if offer.target_faction_id != actor_faction_id:
        raise StrategyError("只能处理发给本势力的贸易请求。")
    if accept:
        proposer = _faction(world, offer.proposer_faction_id)
        target = _faction(world, offer.target_faction_id)
        seller, buyer = (proposer, target) if offer.direction == "sell" else (target, proposer)
        if faction_good_stock(seller, offer.resource_id) < offer.amount:
            raise StrategyError("卖方这种资源已经不够了。")
        if buyer.resources.money < offer.money:
            raise StrategyError("买方金钱不足。")


def rare_resources_public(world: WorldState, faction_id: str) -> dict[str, Any]:
    faction = _faction(world, faction_id)
    veins = faction_vein_counts(world, faction_id)
    income = faction_monthly_rare_income(world, faction_id)
    goods = []
    for item in trade_good_defs():
        goods.append(
            {
                **item,
                "stock": faction_good_stock(faction, item["id"]),
                "veins": veins.get(item["id"], 0) if item["kind"] == "rare" else 0,
                "monthly_income": income.get(item["id"], 0) if item["kind"] == "rare" else 0,
                "unit_price": quote_unit_price(faction, item["id"]),
            }
        )
    counterparts = []
    for other in world.factions:
        if other.faction_id == faction_id or other.is_world_crisis:
            continue
        quotes = [
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "stock": faction_good_stock(other, item["id"]),
                "unit_price": quote_unit_price(other, item["id"]),
            }
            for item in trade_good_defs()
        ]
        counterparts.append(
            {
                "faction_id": other.faction_id,
                "name": other.name,
                "faction_type": other.faction_type,
                "nation_id": other.nation_id,
                "quotes": quotes,
            }
        )
    offers = [
        offer.to_public_dict()
        for offer in world.trade_offers
        if offer.proposer_faction_id == faction_id or offer.target_faction_id == faction_id
    ]
    return {
        "label": "稀有资源",
        "goods": goods,
        "counterparts": counterparts,
        "offers": offers,
        "opening_hero_fill": opening_hero_fill_mode(world),
    }


def _ai_should_accept_trade(world: WorldState, offer: TradeOffer) -> bool:
    proposer = _faction(world, offer.proposer_faction_id)
    target = _faction(world, offer.target_faction_id)
    seller, buyer = (proposer, target) if offer.direction == "sell" else (target, proposer)
    if faction_good_stock(seller, offer.resource_id) < offer.amount:
        return False
    if buyer.resources.money < offer.money:
        return False
    fair = max(1, quote_unit_price(target, offer.resource_id) * offer.amount)
    if offer.direction == "sell":
        if target.resources.money - offer.money < 40:
            return False
        return offer.money <= int(fair * 1.35)
    leftover = faction_good_stock(target, offer.resource_id) - offer.amount
    good = trade_good_def(offer.resource_id) or {}
    if leftover < (2 if good.get("kind") == "rare" else 0):
        return False
    return offer.money >= int(fair * 0.65)


def apply_ai_trade_responses(world: WorldState, *, controlled_faction_ids: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None) -> WorldState:
    controlled = {str(item) for item in (controlled_faction_ids or [])}
    next_world = world
    pending = [
        offer.offer_id
        for offer in next_world.trade_offers
        if offer.status == "pending" and offer.target_faction_id not in controlled
    ]
    for offer_id in pending:
        offer = next((item for item in next_world.trade_offers if item.offer_id == offer_id), None)
        if offer is None or offer.status != "pending":
            continue
        target = _faction(next_world, offer.target_faction_id)
        if target.is_world_crisis or faction_id_is_player_controlled(target, controlled):
            continue
        try:
            if _ai_should_accept_trade(next_world, offer):
                next_world = accept_resource_trade(next_world, actor_faction_id=target.faction_id, offer_id=offer_id)
            else:
                next_world = reject_resource_trade(next_world, actor_faction_id=target.faction_id, offer_id=offer_id)
        except StrategyError:
            try:
                next_world = reject_resource_trade(next_world, actor_faction_id=target.faction_id, offer_id=offer_id)
            except StrategyError:
                continue
    return next_world


def faction_id_is_player_controlled(faction: Faction, controlled: set[str]) -> bool:
    if faction.faction_id in controlled:
        return True
    if faction.is_ai:
        return False
    return int(getattr(faction, "controller_user_id", 0) or 0) > 0
