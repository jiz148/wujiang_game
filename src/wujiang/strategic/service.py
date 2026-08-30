"""Campaign service layer: validation, command accounting and action queueing.

Sits between the strategic domain model and the HTTP routes in
``wujiang.strategic.api`` so neither the transport nor the tactical domain
needs to know how a campaign order is normalised."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from wujiang.bridge.battle_bridge import declare_strategy_attack_for_world
from wujiang.platform.auth import AuthUser
from wujiang.strategic import EventLogEntry
from wujiang.strategic import StrategyError
from wujiang.strategic import apply_exile_action
from wujiang.strategic import apply_faction_diplomacy_action
from wujiang.strategic import apply_neutral_diplomacy_action
from wujiang.strategic import apply_occupation_policy
from wujiang.strategic import apply_office_order
from wujiang.strategic import apply_peaceful_integration
from wujiang.strategic import apply_rebellion_action
from wujiang.strategic import apply_rebellion_battle
from wujiang.strategic import apply_rebellion_funding
from wujiang.strategic import appoint_strategic_hero_to_office
from wujiang.strategic import approve_registered_unit_request
from wujiang.strategic import assign_strategic_hero_duty
from wujiang.strategic import bind_relic
from wujiang.strategic import construct_city_building
from wujiang.strategic import upgrade_city_settlement
from wujiang.strategic import declare_city_attack
from wujiang.strategic.battles import (
    city_attack_commitment,
    normalize_committed_attack_troops,
    pending_battle_hero_codes,
)
from wujiang.strategic import disband_army
from wujiang.strategic import form_or_reinforce_army
from wujiang.strategic import halt_army_march
from wujiang.strategic import increase_city_troops
from wujiang.strategic import load_army_supply
from wujiang.strategic import normalize_strategic_hero_deployment
from wujiang.strategic import order_army_intercept
from wujiang.strategic import order_army_march
from wujiang.strategic import order_army_reinforce
from wujiang.strategic import order_army_retreat
from wujiang.strategic import order_siege_attacker_stance
from wujiang.strategic import order_siege_defender_stance
from wujiang.strategic import perform_hero_ritual
from wujiang.strategic import register_city_soldiers
from wujiang.strategic import release_relic
from wujiang.strategic import repair_relic
from wujiang.strategic import request_registered_units
from wujiang.strategic import resolve_action_office
from wujiang.strategic import resolve_story_event
from wujiang.strategic import resolve_world_crisis_choice
from wujiang.strategic import search_relic
from wujiang.strategic import set_city_policy
from wujiang.strategic import transfer_registered_units
from wujiang.strategic import transfer_relic
from wujiang.strategic import unbind_strategic_hero
from wujiang.strategic import cancel_tactic_research, unlock_tactic_tech
from wujiang.strategic import validate_bind_relic
from wujiang.strategic import validate_exile_action
from wujiang.strategic import validate_faction_diplomacy_action
from wujiang.strategic import validate_neutral_diplomacy_action
from wujiang.strategic import validate_occupation_policy
from wujiang.strategic import validate_peaceful_integration
from wujiang.strategic import validate_rebellion_action
from wujiang.strategic import validate_rebellion_battle
from wujiang.strategic import validate_rebellion_funding
from wujiang.strategic import validate_release_relic
from wujiang.strategic import validate_relic_repair
from wujiang.strategic import validate_relic_search
from wujiang.strategic import validate_relic_transfer
from wujiang.strategic import validate_story_event_choice
from wujiang.strategic import validate_world_crisis_choice
from wujiang.strategic.campaign_runtime import CITY_MONTHLY_ORDER_LIMIT
from wujiang.strategic.command import faction_command_points
from wujiang.strategic.command import strategy_action_command_cost
from wujiang.strategic.neutral_city_states import incite_neutral_city_state
from wujiang.strategic.neutral_city_states import validate_neutral_city_state_incitement

def campaign_member_faction_id(campaign, user_id: int) -> str:
    controlled_hero = next(
        (
            hero
            for hero in campaign.world.strategic_heroes
            if hero.controller_type == "player" and int(hero.controller_user_id or 0) == int(user_id)
        ),
        None,
    )
    if controlled_hero is not None:
        if controlled_hero.status != "serving" or not controlled_hero.faction_id:
            raise StrategyError("你的武将目前在野，必须先建立势力或获准投靠主公。", status=HTTPStatus.FORBIDDEN)
        return str(controlled_hero.faction_id)
    for member in campaign.members:
        if member.user_id == int(user_id):
            return member.faction_id
    raise StrategyError("你不是这个战役的成员，不能操作该战役。", status=HTTPStatus.FORBIDDEN)


def strategy_city_for_order(campaign, city_id: str, faction_id: str):
    normalized_id = str(city_id or "").strip()
    if not normalized_id:
        return None
    city = next((item for item in campaign.world.cities if item.city_id == normalized_id), None)
    if city is None:
        raise StrategyError("城市不存在。", status=HTTPStatus.NOT_FOUND)
    if city.owner_faction_id != faction_id:
        raise StrategyError("只能从己方城市下达军令。", status=HTTPStatus.FORBIDDEN)
    return city


def strategy_action_city_id(action_type: str, payload: dict[str, Any]) -> str:
    if action_type in {"set_city_policy", "rebellion_action", "rebellion_battle", "choose_occupation_policy", "fund_rebellion"}:
        return str(payload.get("city_id") or payload.get("target_city_id") or "").strip()
    if action_type == "declare_attack":
        return str(payload.get("source_city_id") or "").strip()
    if action_type in {
        "perform_hero_ritual",
        "increase_city_troops",
        "register_city_soldiers",
        "transfer_registered_units",
        "request_registered_units",
        "construct_city_building",
        "upgrade_city_settlement",
        "form_army",
    }:
        return str(payload.get("city_id") or "").strip()
    return ""


def enforce_city_order_limit(
    campaign,
    *,
    user_id: int,
    action_type: str,
    action_key: str,
    payload: dict[str, Any],
) -> None:
    city_id = strategy_action_city_id(action_type, payload)
    if not city_id:
        return
    faction_id = campaign_member_faction_id(campaign, user_id)
    count = 0
    for action in campaign.queued_actions:
        if action.faction_id != faction_id:
            continue
        if action.action_type == action_type and action.action_key == action_key:
            continue
        if strategy_action_city_id(action.action_type, action.payload) == city_id:
            count += 1
    if count >= CITY_MONTHLY_ORDER_LIMIT:
        city_name = next((city.name for city in campaign.world.cities if city.city_id == city_id), city_id)
        raise StrategyError(f"{city_name} 本月军令已满：每座城市每月最多 {CITY_MONTHLY_ORDER_LIMIT} 条军令。", status=HTTPStatus.CONFLICT)


def enforce_faction_command_points(
    campaign,
    *,
    user_id: int,
    action_type: str,
    action_key: str,
    payload: dict[str, Any],
) -> None:
    faction_id = campaign_member_faction_id(campaign, user_id)
    command = faction_command_points(
        faction_id,
        campaign.queued_actions,
        exclude_action_type=action_type,
        exclude_action_key=action_key,
    )
    cost = strategy_action_command_cost(action_type, payload)
    if cost > command["remaining"]:
        raise StrategyError(
            f"本势力军令不足：本月剩余 {command['remaining']} 点，该行动需要 {cost} 点。",
            status=HTTPStatus.CONFLICT,
        )


def require_campaign_owner(campaign, user_id: int) -> None:
    if int(getattr(campaign, "owner_user_id", 0)) != int(user_id):
        raise StrategyError("只有战役房主可以推进月度结算。", status=HTTPStatus.FORBIDDEN)


def require_strategy_action_office(
    campaign,
    *,
    user_id: int,
    action_type: str,
    payload: dict[str, Any],
):
    faction_id = campaign_member_faction_id(campaign, user_id)
    return resolve_action_office(
        campaign.world,
        user_id=user_id,
        faction_id=faction_id,
        action_type=action_type,
        payload=payload,
        requested_office_id=str(payload.get("issuer_office_id") or payload.get("office_id") or ""),
    )


def queued_attack_hero_codes(
    campaign,
    *,
    exclude_action_type: str = "",
    exclude_action_key: str = "",
) -> set[str]:
    codes: set[str] = set()
    for action in campaign.queued_actions:
        if action.action_type != "declare_attack":
            continue
        if (
            exclude_action_type
            and action.action_type == exclude_action_type
            and action.action_key == exclude_action_key
        ):
            continue
        codes.update(strategy_hero_codes_from_payload(action.payload))
        commander = str(action.payload.get("commander_hero_code") or "").strip()
        if commander:
            codes.add(commander)
    return codes


def queued_attack_troops_from_city(
    campaign,
    city_id: str,
    *,
    exclude_action_type: str = "",
    exclude_action_key: str = "",
) -> int:
    reserved = 0
    source_id = str(city_id or "")
    for action in campaign.queued_actions:
        if action.action_type != "declare_attack":
            continue
        if (
            exclude_action_type
            and action.action_type == exclude_action_type
            and action.action_key == exclude_action_key
        ):
            continue
        if str(action.payload.get("source_city_id") or "") != source_id:
            continue
        raw = action.payload.get("committed_troops")
        if raw in {None, ""}:
            raw = action.payload.get("attacker_troops")
        if raw in {None, ""}:
            city = next((item for item in campaign.world.cities if item.city_id == source_id), None)
            reserved += city_attack_commitment(city.resources.troops if city is not None else 0)
        else:
            reserved += max(0, int(raw))
    return reserved


def parse_optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)


def strategy_hero_codes_from_payload(payload: dict[str, Any]) -> list[str]:
    raw_codes = payload.get("attacker_hero_codes")
    if raw_codes is None:
        raw_codes = payload.get("strategic_hero_codes")
    if raw_codes is None:
        raw_code = str(payload.get("attacker_hero_code") or payload.get("strategic_hero_code") or "").strip()
        return [raw_code] if raw_code else []
    if not isinstance(raw_codes, list):
        raise StrategyError("å‚æˆ˜è‹±çµåˆ—è¡¨å¿…é¡»æ˜¯æ•°ç»„ã€‚")
    return [str(code or "").strip() for code in raw_codes if str(code or "").strip()]


def strategy_defender_hero_codes_from_payload(payload: dict[str, Any]) -> list[str]:
    raw_codes = payload.get("hero_codes")
    if raw_codes is None:
        raw_code = str(payload.get("hero_code") or "").strip()
        return [raw_code] if raw_code else []
    if not isinstance(raw_codes, list):
        raise StrategyError("防守英灵列表必须是数组。")
    return [str(code or "").strip() for code in raw_codes if str(code or "").strip()]


def normalize_strategy_action_payload(campaign, user_id: int, action_type: str, payload: Any) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise StrategyError("Strategy action payload must be an object.")
    normalized_type = str(action_type or "").strip()
    faction_id = campaign_member_faction_id(campaign, user_id)
    requested_office_id = str(payload.get("issuer_office_id") or payload.get("office_id") or "").strip()

    def finalize(action_key: str, normalized_payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        office = resolve_action_office(
            campaign.world,
            user_id=user_id,
            faction_id=faction_id,
            action_type=normalized_type,
            payload=normalized_payload,
            requested_office_id=requested_office_id,
        )
        normalized_payload["issuer_office_id"] = office.office_id
        return normalized_type, action_key, normalized_payload

    def ensure_relic_altar_queue_available(altar_id: str, action_key: str) -> None:
        for queued in campaign.queued_actions:
            if (
                queued.faction_id == faction_id
                and queued.action_type in {"bind_relic", "release_relic"}
                and str(queued.payload.get("altar_id") or "") == altar_id
                and not (queued.action_type == normalized_type and queued.action_key == action_key)
            ):
                raise StrategyError("该圣物祭坛本月已经安排过绑定或释放行动。")
    if normalized_type == "set_city_policy":
        city_id = str(payload.get("city_id") or "").strip()
        policy = str(payload.get("policy") or "").strip()
        normalized_payload = {"city_id": city_id, "policy": policy}
        set_city_policy(campaign.world, faction_id=faction_id, city_id=city_id, policy=policy)
        return finalize(city_id, normalized_payload)
    if normalized_type == "incite_neutral_city_state":
        neutral_faction_id = str(payload.get("neutral_faction_id") or "").strip()
        target_faction_id = str(payload.get("target_faction_id") or "").strip()
        normalized_payload = {
            "neutral_faction_id": neutral_faction_id,
            "target_faction_id": target_faction_id,
        }
        validate_neutral_city_state_incitement(
            campaign.world,
            instigator_faction_id=faction_id,
            neutral_faction_id=neutral_faction_id,
            target_faction_id=target_faction_id,
        )
        return finalize(f"{neutral_faction_id}:{target_faction_id}", normalized_payload)
    if normalized_type == "neutral_diplomacy":
        neutral_faction_id = str(payload.get("neutral_faction_id") or "").strip()
        diplomacy_action_id = str(payload.get("diplomacy_action_id") or payload.get("action_id") or "").strip()
        normalized_payload = {
            "neutral_faction_id": neutral_faction_id,
            "diplomacy_action_id": diplomacy_action_id,
        }
        validate_neutral_diplomacy_action(
            campaign.world,
            actor_faction_id=faction_id,
            neutral_faction_id=neutral_faction_id,
            action_id=diplomacy_action_id,
        )
        return finalize(neutral_faction_id, normalized_payload)
    if normalized_type == "faction_diplomacy":
        target_faction_id = str(payload.get("target_faction_id") or "").strip()
        diplomacy_action_id = str(payload.get("diplomacy_action_id") or payload.get("action_id") or "").strip()
        normalized_payload = {
            "target_faction_id": target_faction_id,
            "diplomacy_action_id": diplomacy_action_id,
        }
        validate_faction_diplomacy_action(
            campaign.world,
            actor_faction_id=faction_id,
            target_faction_id=target_faction_id,
            action_id=diplomacy_action_id,
        )
        return finalize(f"{target_faction_id}:{diplomacy_action_id}", normalized_payload)
    if normalized_type == "world_crisis_choice":
        choice_id = str(payload.get("choice_id") or payload.get("action_id") or "").strip()
        target_faction_id = str(payload.get("target_faction_id") or "").strip()
        normalized_payload = {
            "choice_id": choice_id,
            "target_faction_id": target_faction_id,
        }
        validate_world_crisis_choice(
            campaign.world,
            faction_id=faction_id,
            choice_id=choice_id,
            target_faction_id=target_faction_id,
        )
        return finalize("snow_ghost_north_v1", normalized_payload)
    if normalized_type == "peaceful_integration":
        neutral_faction_id = str(payload.get("neutral_faction_id") or "").strip()
        normalized_payload = {"neutral_faction_id": neutral_faction_id}
        validate_peaceful_integration(
            campaign.world,
            actor_faction_id=faction_id,
            neutral_faction_id=neutral_faction_id,
        )
        return finalize(neutral_faction_id, normalized_payload)
    if normalized_type == "choose_occupation_policy":
        city_id = str(payload.get("city_id") or "").strip()
        policy_id = str(payload.get("policy_id") or "").strip()
        normalized_payload = {"city_id": city_id, "policy_id": policy_id}
        validate_occupation_policy(campaign.world, faction_id=faction_id, city_id=city_id, policy_id=policy_id)
        return finalize(city_id, normalized_payload)
    if normalized_type == "fund_rebellion":
        city_id = str(payload.get("city_id") or "").strip()
        normalized_payload = {"city_id": city_id}
        validate_rebellion_funding(campaign.world, sponsor_faction_id=faction_id, city_id=city_id)
        return finalize(city_id, normalized_payload)
    if normalized_type == "resolve_story_event":
        event_id = str(payload.get("event_id") or "").strip()
        choice_id = str(payload.get("choice_id") or "").strip()
        normalized_payload = {"event_id": event_id, "choice_id": choice_id}
        validate_story_event_choice(
            campaign.world,
            faction_id=faction_id,
            event_id=event_id,
            choice_id=choice_id,
        )
        return finalize(event_id, normalized_payload)
    if normalized_type == "unlock_tactic_tech":
        tech_id = str(payload.get("tech_id") or "").strip()
        normalized_payload = {"tech_id": tech_id}
        for queued in campaign.queued_actions:
            if (
                queued.faction_id == faction_id
                and queued.action_type == "unlock_tactic_tech"
                and queued.action_key != tech_id
            ):
                raise StrategyError("已有科技正在研究，取消后才能改研其他。")
        unlock_tactic_tech(campaign.world, faction_id=faction_id, tech_id=tech_id)
        return finalize(tech_id, normalized_payload)
    if normalized_type == "cancel_tactic_research":
        normalized_payload = {}
        cancel_tactic_research(campaign.world, faction_id=faction_id)
        return finalize("research", normalized_payload)
    if normalized_type == "exile_action":
        exile_action_id = str(payload.get("exile_action_id") or payload.get("action_id") or "").strip()
        target_city_id = str(payload.get("target_city_id") or "").strip()
        normalized_payload = {"exile_action_id": exile_action_id}
        if target_city_id:
            normalized_payload["target_city_id"] = target_city_id
        validate_exile_action(
            campaign.world,
            faction_id=faction_id,
            action_id=exile_action_id,
            target_city_id=target_city_id,
        )
        return finalize(f"{exile_action_id}:{target_city_id or 'self'}", normalized_payload)
    if normalized_type == "rebellion_action":
        rebellion_action_id = str(payload.get("rebellion_action_id") or payload.get("action_id") or "").strip()
        city_id = str(payload.get("city_id") or payload.get("target_city_id") or "").strip()
        normalized_payload = {"rebellion_action_id": rebellion_action_id, "city_id": city_id}
        validate_rebellion_action(
            campaign.world,
            faction_id=faction_id,
            action_id=rebellion_action_id,
            city_id=city_id,
        )
        return finalize(f"{rebellion_action_id}:{city_id}", normalized_payload)
    if normalized_type == "rebellion_battle":
        city_id = str(payload.get("city_id") or payload.get("target_city_id") or "").strip()
        raw_troops = payload.get("troops")
        troops = int(raw_troops) if raw_troops not in {None, ""} else None
        committed = validate_rebellion_battle(
            campaign.world,
            faction_id=faction_id,
            city_id=city_id,
            troops=troops,
        )
        normalized_payload = {"city_id": city_id, "troops": committed}
        return finalize(city_id, normalized_payload)
    if normalized_type == "perform_hero_ritual":
        city_id = str(payload.get("city_id") or "").strip()
        normalized_payload = {"city_id": city_id}
        _, action_key, normalized_payload = finalize(city_id, normalized_payload)
        perform_hero_ritual(
            campaign.world,
            faction_id=faction_id,
            city_id=city_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "search_relic":
        relic_id = str(payload.get("relic_id") or "").strip()
        hero_code = str(payload.get("hero_code") or "").strip()
        city_id = str(payload.get("city_id") or "").strip()
        normalized_payload = {
            "relic_id": relic_id,
            "hero_code": hero_code,
            "city_id": city_id,
        }
        _, action_key, normalized_payload = finalize(relic_id, normalized_payload)
        for queued in campaign.queued_actions:
            if (
                queued.faction_id == faction_id
                and queued.action_type == "search_relic"
                and queued.action_key != action_key
                and str(queued.payload.get("hero_code") or "") == hero_code
            ):
                raise StrategyError("该英灵本月已经被委派搜索另一件圣物。")
        validate_relic_search(
            campaign.world,
            faction_id=faction_id,
            relic_id=relic_id,
            hero_code=hero_code,
            city_id=city_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "transfer_relic":
        relic_id = str(payload.get("relic_id") or "").strip()
        target_city_id = str(payload.get("target_city_id") or "").strip()
        normalized_payload = {"relic_id": relic_id, "target_city_id": target_city_id}
        _, action_key, normalized_payload = finalize(relic_id, normalized_payload)
        validate_relic_transfer(
            campaign.world,
            faction_id=faction_id,
            relic_id=relic_id,
            target_city_id=target_city_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "repair_relic":
        relic_id = str(payload.get("relic_id") or "").strip()
        normalized_payload = {"relic_id": relic_id}
        _, action_key, normalized_payload = finalize(relic_id, normalized_payload)
        validate_relic_repair(
            campaign.world,
            faction_id=faction_id,
            relic_id=relic_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "bind_relic":
        relic_id = str(payload.get("relic_id") or "").strip()
        altar_id = str(payload.get("altar_id") or "").strip()
        normalized_payload = {"relic_id": relic_id, "altar_id": altar_id}
        _, action_key, normalized_payload = finalize(altar_id, normalized_payload)
        ensure_relic_altar_queue_available(altar_id, action_key)
        validate_bind_relic(
            campaign.world,
            faction_id=faction_id,
            relic_id=relic_id,
            altar_id=altar_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "release_relic":
        relic_id = str(payload.get("relic_id") or "").strip()
        relic = next((item for item in campaign.world.relics if item.relic_id == relic_id), None)
        if relic is None or not relic.altar_id:
            raise StrategyError("只能释放本势力已经绑定到祭坛的圣物。")
        altar_id = relic.altar_id
        normalized_payload = {"relic_id": relic_id, "altar_id": altar_id}
        _, action_key, normalized_payload = finalize(altar_id, normalized_payload)
        ensure_relic_altar_queue_available(altar_id, action_key)
        validate_release_relic(
            campaign.world,
            faction_id=faction_id,
            relic_id=relic_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "unbind_strategic_hero":
        hero_code = str(payload.get("hero_code") or "").strip()
        normalized_payload = {"hero_code": hero_code}
        _, action_key, normalized_payload = finalize(hero_code, normalized_payload)
        unbind_strategic_hero(
            campaign.world,
            faction_id=faction_id,
            hero_code=hero_code,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type in {
        "increase_city_troops",
        "register_city_soldiers",
        "construct_city_building",
        "upgrade_city_settlement",
    }:
        city_id = str(payload.get("city_id") or "").strip()
        building_id = str(payload.get("building_id") or "").strip()
        settlement = str(payload.get("settlement") or "").strip()
        unit_count = max(1, min(3, int(payload.get("unit_count") or 1)))
        normalized_payload = {"city_id": city_id}
        if normalized_type == "construct_city_building":
            normalized_payload["building_id"] = building_id
        if normalized_type == "register_city_soldiers":
            normalized_payload["unit_count"] = unit_count
        if normalized_type == "upgrade_city_settlement":
            normalized_payload["settlement"] = settlement
        action_target = "increase" if normalized_type == "increase_city_troops" else building_id or settlement or str(unit_count)
        _, action_key, normalized_payload = finalize(f"{city_id}:{action_target}", normalized_payload)
        kwargs = {
            "faction_id": faction_id,
            "city_id": city_id,
            "issuer_office_id": normalized_payload["issuer_office_id"],
        }
        if normalized_type == "increase_city_troops":
            increase_city_troops(campaign.world, **kwargs)
        elif normalized_type == "register_city_soldiers":
            register_city_soldiers(campaign.world, unit_count=unit_count, **kwargs)
        elif normalized_type == "upgrade_city_settlement":
            upgrade_city_settlement(campaign.world, settlement=settlement, **kwargs)
        else:
            construct_city_building(campaign.world, building_id=building_id, **kwargs)
        return normalized_type, action_key, normalized_payload
    if normalized_type in {"transfer_registered_units", "request_registered_units"}:
        city_id = str(payload.get("city_id") or "").strip()
        unit_type = str(payload.get("unit_type") or "").strip()
        count = max(1, int(payload.get("count") or 1))
        general_office_id = str(payload.get("general_office_id") or "").strip()
        normalized_payload = {"city_id": city_id, "unit_type": unit_type, "count": count}
        if normalized_type == "transfer_registered_units":
            normalized_payload["general_office_id"] = general_office_id
        _, action_key, normalized_payload = finalize(
            f"{city_id}:{general_office_id}:{unit_type}",
            normalized_payload,
        )
        if normalized_type == "transfer_registered_units":
            transfer_registered_units(
                campaign.world,
                faction_id=faction_id,
                city_id=city_id,
                general_office_id=general_office_id,
                unit_type=unit_type,
                count=count,
                issuer_office_id=normalized_payload["issuer_office_id"],
            )
        else:
            request_registered_units(
                campaign.world,
                faction_id=faction_id,
                city_id=city_id,
                unit_type=unit_type,
                count=count,
                issuer_office_id=normalized_payload["issuer_office_id"],
            )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "approve_registered_unit_request":
        request_id = str(payload.get("request_id") or "").strip()
        normalized_payload = {"request_id": request_id}
        _, action_key, normalized_payload = finalize(request_id, normalized_payload)
        approve_registered_unit_request(
            campaign.world,
            faction_id=faction_id,
            request_id=request_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "form_army":
        city_id = str(payload.get("city_id") or "").strip()
        raw_units = payload.get("unit_inventory")
        if not isinstance(raw_units, dict):
            raise StrategyError("编军单位必须是兵种数量对象。")
        unit_inventory = {
            str(unit_type): int(count or 0)
            for unit_type, count in raw_units.items()
            if int(count or 0) != 0
        }
        supply = int(payload.get("supply") or 0)
        normalized_payload = {"city_id": city_id, "unit_inventory": unit_inventory, "supply": supply}
        _, action_key, normalized_payload = finalize(requested_office_id or city_id, normalized_payload)
        form_or_reinforce_army(
            campaign.world,
            faction_id=faction_id,
            city_id=city_id,
            unit_inventory=unit_inventory,
            supply=supply,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "disband_army":
        army_id = str(payload.get("army_id") or "").strip()
        normalized_payload = {"army_id": army_id}
        _, action_key, normalized_payload = finalize(army_id, normalized_payload)
        disband_army(
            campaign.world,
            faction_id=faction_id,
            army_id=army_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "set_army_movement":
        army_id = str(payload.get("army_id") or "").strip()
        movement_order = str(payload.get("movement_order") or "march").strip().lower()
        destination_node_id = str(payload.get("destination_node_id") or "").strip()
        if movement_order not in {"march", "hold", "intercept", "reinforce", "retreat"}:
            raise StrategyError("军队机动命令无效。")
        normalized_payload = {
            "army_id": army_id,
            "movement_order": movement_order,
            "destination_node_id": destination_node_id if movement_order in {"march", "retreat"} else "",
            "target_army_id": str(payload.get("target_army_id") or "").strip() if movement_order == "intercept" else "",
            "target_encounter_id": str(payload.get("target_encounter_id") or "").strip() if movement_order == "reinforce" else "",
        }
        _, action_key, normalized_payload = finalize(army_id, normalized_payload)
        if movement_order == "march":
            order_army_march(
                campaign.world,
                faction_id=faction_id,
                army_id=army_id,
                destination_node_id=destination_node_id,
                issuer_office_id=normalized_payload["issuer_office_id"],
            )
        elif movement_order == "hold":
            halt_army_march(
                campaign.world,
                faction_id=faction_id,
                army_id=army_id,
                issuer_office_id=normalized_payload["issuer_office_id"],
            )
        elif movement_order == "intercept":
            order_army_intercept(
                campaign.world,
                faction_id=faction_id,
                army_id=army_id,
                target_army_id=normalized_payload["target_army_id"],
                issuer_office_id=normalized_payload["issuer_office_id"],
            )
        elif movement_order == "reinforce":
            order_army_reinforce(
                campaign.world,
                faction_id=faction_id,
                army_id=army_id,
                encounter_id=normalized_payload["target_encounter_id"],
                issuer_office_id=normalized_payload["issuer_office_id"],
            )
        else:
            order_army_retreat(
                campaign.world,
                faction_id=faction_id,
                army_id=army_id,
                destination_node_id=destination_node_id,
                issuer_office_id=normalized_payload["issuer_office_id"],
            )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "load_army_supply":
        army_id = str(payload.get("army_id") or "").strip()
        supply = int(payload.get("supply") or 0)
        normalized_payload = {"army_id": army_id, "supply": supply}
        _, action_key, normalized_payload = finalize(army_id, normalized_payload)
        load_army_supply(
            campaign.world,
            faction_id=faction_id,
            army_id=army_id,
            supply=supply,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "set_siege_attacker_stance":
        siege_id = str(payload.get("siege_id") or "").strip()
        stance = str(payload.get("stance") or "").strip().lower()
        destination_node_id = str(payload.get("destination_node_id") or "").strip()
        normalized_payload = {
            "siege_id": siege_id,
            "stance": stance,
            "destination_node_id": destination_node_id if stance == "withdraw" else "",
        }
        _, action_key, normalized_payload = finalize(siege_id, normalized_payload)
        order_siege_attacker_stance(
            campaign.world,
            faction_id=faction_id,
            siege_id=siege_id,
            stance=stance,
            destination_node_id=normalized_payload["destination_node_id"],
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "set_siege_defender_stance":
        siege_id = str(payload.get("siege_id") or "").strip()
        stance = str(payload.get("stance") or "").strip().lower()
        siege = next((item for item in campaign.world.sieges if item.siege_id == siege_id), None)
        normalized_payload = {
            "siege_id": siege_id,
            "stance": stance,
            "city_id": siege.city_id if siege is not None else "",
        }
        _, action_key, normalized_payload = finalize(siege_id, normalized_payload)
        order_siege_defender_stance(
            campaign.world,
            faction_id=faction_id,
            siege_id=siege_id,
            stance=stance,
            issuer_office_id=normalized_payload["issuer_office_id"],
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "appoint_strategic_hero":
        target_office_id = str(payload.get("target_office_id") or "").strip()
        hero_code = str(payload.get("hero_code") or "").strip()
        normalized_payload = {"target_office_id": target_office_id, "hero_code": hero_code}
        _, action_key, normalized_payload = finalize(target_office_id, normalized_payload)
        appoint_strategic_hero_to_office(
            campaign.world,
            faction_id=faction_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
            target_office_id=target_office_id,
            hero_code=hero_code,
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "assign_strategic_hero_duty":
        hero_code = str(payload.get("hero_code") or "").strip()
        assignment_type = str(payload.get("assignment_type") or "reserve").strip()
        target_id = str(payload.get("target_id") or "").strip()
        normalized_payload = {
            "hero_code": hero_code,
            "assignment_type": assignment_type,
            "target_id": target_id,
        }
        _, action_key, normalized_payload = finalize(hero_code, normalized_payload)
        assign_strategic_hero_duty(
            campaign.world,
            faction_id=faction_id,
            issuer_office_id=normalized_payload["issuer_office_id"],
            hero_code=hero_code,
            assignment_type=assignment_type,
            target_id=target_id,
        )
        return normalized_type, action_key, normalized_payload
    if normalized_type == "declare_attack":
        source_city_id = str(payload.get("source_city_id") or "").strip()
        target_city_id = str(payload.get("target_city_id") or "").strip()
        resolution_mode = str(payload.get("resolution_mode") or "quick").strip() or "quick"
        attacker_hero_codes = normalize_strategic_hero_deployment(
            campaign.world,
            faction_id,
            strategy_hero_codes_from_payload(payload),
        )
        action_key = f"{source_city_id}->{target_city_id}"
        busy_heroes = pending_battle_hero_codes(campaign.world) | queued_attack_hero_codes(
            campaign,
            exclude_action_type="declare_attack",
            exclude_action_key=action_key,
        )
        overlapping = [code for code in attacker_hero_codes if code in busy_heroes]
        if overlapping:
            raise StrategyError("这些武将已参加其他出征，不能同时出现在两场进攻里。")
        source_city = next((item for item in campaign.world.cities if item.city_id == source_city_id), None)
        city_troops = int(source_city.resources.troops) if source_city is not None else 0
        available_troops = city_troops - queued_attack_troops_from_city(
            campaign,
            source_city_id,
            exclude_action_type="declare_attack",
            exclude_action_key=action_key,
        )
        committed_troops = normalize_committed_attack_troops(
            parse_optional_int(payload.get("committed_troops", payload.get("attacker_troops"))),
            available_troops,
        )
        normalized_payload = {
            "source_city_id": source_city_id,
            "target_city_id": target_city_id,
            "resolution_mode": resolution_mode,
            "attacker_hero_codes": attacker_hero_codes,
            "committed_troops": committed_troops,
        }
        result = finalize(action_key, normalized_payload)
        issuer = next(
            office for office in campaign.world.offices if office.office_id == normalized_payload["issuer_office_id"]
        )
        if issuer.office_type == "lord":
            commander_code = str(issuer.holder_id or "")
            lord_hero = next(
                (item for item in campaign.world.strategic_heroes if item.hero_code == commander_code),
                None,
            )
            lord_city = str((lord_hero.city_id if lord_hero is not None else "") or (lord_hero.assignment_target_id if lord_hero is not None else "") or "")
            if commander_code and lord_city == source_city_id and commander_code not in busy_heroes:
                normalized_payload["commander_hero_code"] = commander_code
                normalized_payload["attacker_hero_codes"] = normalize_strategic_hero_deployment(
                    campaign.world,
                    faction_id,
                    [commander_code, *normalized_payload["attacker_hero_codes"]],
                )
        declare_city_attack(
            campaign.world,
            faction_id=faction_id,
            source_city_id=source_city_id,
            target_city_id=target_city_id,
            resolution_mode=resolution_mode,
            auto_resolve=resolution_mode == "quick",
            attacker_hero_codes=normalized_payload["attacker_hero_codes"],
            attacker_office_id=issuer.office_id,
            committed_troops=committed_troops,
        )
        return result
    if normalized_type in {"issue_office_order", "send_office_request"}:
        receiver_office_id = str(payload.get("receiver_office_id") or "").strip()
        objective = str(payload.get("objective") or "").strip()
        target_entity_id = str(payload.get("target_entity_id") or "").strip()
        priority = int(payload.get("priority") or 1)
        raw_deadline = payload.get("deadline_month")
        deadline_month = int(raw_deadline) if raw_deadline not in {None, ""} else None
        office_order_type = (
            "request"
            if normalized_type == "send_office_request"
            else str(payload.get("office_order_type") or "order").strip()
        )
        if office_order_type not in {
            "order",
            "request",
            "attack_city",
            "defend_city",
            "set_policy",
            "levy_garrison",
            "reinforce_city",
        }:
            raise StrategyError("职位命令类型无效。")
        city_policy = str(payload.get("city_policy") or "").strip()
        normalized_payload = {
            "receiver_office_id": receiver_office_id,
            "objective": objective,
            "target_entity_id": target_entity_id,
            "priority": priority,
            "deadline_month": deadline_month,
            "office_order_type": office_order_type,
            "city_policy": city_policy,
        }
        queued_office_order_count = sum(
            1
            for action in campaign.queued_actions
            if action.action_type in {"issue_office_order", "send_office_request"}
        )
        _, action_key, normalized_payload = finalize(
            f"{receiver_office_id}:{len(campaign.world.office_orders) + queued_office_order_count + 1}",
            normalized_payload,
        )
        apply_office_order(
            campaign.world,
            issuer_office_id=normalized_payload["issuer_office_id"],
            receiver_office_id=receiver_office_id,
            order_type=office_order_type,
            objective=objective,
            target_entity_id=target_entity_id,
            priority=priority,
            deadline_month=deadline_month,
            details={"policy": city_policy} if office_order_type == "set_policy" else None,
        )
        return normalized_type, action_key, normalized_payload
    raise StrategyError("Unknown strategy action type.")


def apply_strategy_action_queue(campaign):
    next_world = campaign.world
    battle_rooms: list[dict[str, Any]] = []
    for action in campaign.queued_actions:
        try:
            faction_id = campaign_member_faction_id(campaign, action.user_id)
            payload = action.payload
            office = resolve_action_office(
                next_world,
                user_id=action.user_id,
                faction_id=faction_id,
                action_type=action.action_type,
                payload=payload,
                requested_office_id=str(payload.get("issuer_office_id") or ""),
            )
            if action.action_type == "set_city_policy":
                next_world = set_city_policy(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    policy=str(payload.get("policy") or ""),
                )
            elif action.action_type == "incite_neutral_city_state":
                next_world = incite_neutral_city_state(
                    next_world,
                    instigator_faction_id=faction_id,
                    neutral_faction_id=str(payload.get("neutral_faction_id") or ""),
                    target_faction_id=str(payload.get("target_faction_id") or ""),
                )
            elif action.action_type == "neutral_diplomacy":
                next_world = apply_neutral_diplomacy_action(
                    next_world,
                    actor_faction_id=faction_id,
                    neutral_faction_id=str(payload.get("neutral_faction_id") or ""),
                    action_id=str(payload.get("diplomacy_action_id") or ""),
                )
            elif action.action_type == "faction_diplomacy":
                next_world = apply_faction_diplomacy_action(
                    next_world,
                    actor_faction_id=faction_id,
                    target_faction_id=str(payload.get("target_faction_id") or ""),
                    action_id=str(payload.get("diplomacy_action_id") or ""),
                )
            elif action.action_type == "world_crisis_choice":
                next_world = resolve_world_crisis_choice(
                    next_world,
                    faction_id=faction_id,
                    choice_id=str(payload.get("choice_id") or ""),
                    target_faction_id=str(payload.get("target_faction_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "peaceful_integration":
                next_world = apply_peaceful_integration(
                    next_world,
                    actor_faction_id=faction_id,
                    neutral_faction_id=str(payload.get("neutral_faction_id") or ""),
                )
            elif action.action_type == "choose_occupation_policy":
                next_world = apply_occupation_policy(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    policy_id=str(payload.get("policy_id") or ""),
                )
            elif action.action_type == "fund_rebellion":
                next_world = apply_rebellion_funding(
                    next_world,
                    sponsor_faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                )
            elif action.action_type == "resolve_story_event":
                next_world = resolve_story_event(
                    next_world,
                    faction_id=faction_id,
                    event_id=str(payload.get("event_id") or ""),
                    choice_id=str(payload.get("choice_id") or ""),
                )
            elif action.action_type == "unlock_tactic_tech":
                next_world = unlock_tactic_tech(
                    next_world,
                    faction_id=faction_id,
                    tech_id=str(payload.get("tech_id") or ""),
                )
            elif action.action_type == "cancel_tactic_research":
                next_world = cancel_tactic_research(next_world, faction_id=faction_id)
            elif action.action_type == "exile_action":
                next_world = apply_exile_action(
                    next_world,
                    faction_id=faction_id,
                    action_id=str(payload.get("exile_action_id") or payload.get("action_id") or ""),
                    target_city_id=str(payload.get("target_city_id") or ""),
                )
            elif action.action_type == "rebellion_action":
                next_world = apply_rebellion_action(
                    next_world,
                    faction_id=faction_id,
                    action_id=str(payload.get("rebellion_action_id") or payload.get("action_id") or ""),
                    city_id=str(payload.get("city_id") or payload.get("target_city_id") or ""),
                )
            elif action.action_type == "rebellion_battle":
                next_world = apply_rebellion_battle(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or payload.get("target_city_id") or ""),
                    troops=int(payload.get("troops")) if payload.get("troops") not in {None, ""} else None,
                )
            elif action.action_type == "perform_hero_ritual":
                next_world = perform_hero_ritual(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "search_relic":
                next_world = search_relic(
                    next_world,
                    faction_id=faction_id,
                    relic_id=str(payload.get("relic_id") or ""),
                    hero_code=str(payload.get("hero_code") or ""),
                    city_id=str(payload.get("city_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "transfer_relic":
                next_world = transfer_relic(
                    next_world,
                    faction_id=faction_id,
                    relic_id=str(payload.get("relic_id") or ""),
                    target_city_id=str(payload.get("target_city_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "repair_relic":
                next_world = repair_relic(
                    next_world,
                    faction_id=faction_id,
                    relic_id=str(payload.get("relic_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "bind_relic":
                next_world = bind_relic(
                    next_world,
                    faction_id=faction_id,
                    relic_id=str(payload.get("relic_id") or ""),
                    altar_id=str(payload.get("altar_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "release_relic":
                next_world = release_relic(
                    next_world,
                    faction_id=faction_id,
                    relic_id=str(payload.get("relic_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "unbind_strategic_hero":
                next_world = unbind_strategic_hero(
                    next_world,
                    faction_id=faction_id,
                    hero_code=str(payload.get("hero_code") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "increase_city_troops":
                next_world = increase_city_troops(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "register_city_soldiers":
                next_world = register_city_soldiers(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    unit_count=int(payload.get("unit_count") or 1),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "transfer_registered_units":
                next_world = transfer_registered_units(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    general_office_id=str(payload.get("general_office_id") or ""),
                    unit_type=str(payload.get("unit_type") or ""),
                    count=int(payload.get("count") or 1),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "request_registered_units":
                next_world = request_registered_units(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    unit_type=str(payload.get("unit_type") or ""),
                    count=int(payload.get("count") or 1),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "approve_registered_unit_request":
                next_world = approve_registered_unit_request(
                    next_world,
                    faction_id=faction_id,
                    request_id=str(payload.get("request_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "form_army":
                next_world = form_or_reinforce_army(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    unit_inventory=dict(payload.get("unit_inventory") or {}),
                    supply=int(payload.get("supply") or 0),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "disband_army":
                next_world = disband_army(
                    next_world,
                    faction_id=faction_id,
                    army_id=str(payload.get("army_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "set_army_movement":
                movement_order = str(payload.get("movement_order") or "march")
                if movement_order == "hold":
                    next_world = halt_army_march(
                        next_world,
                        faction_id=faction_id,
                        army_id=str(payload.get("army_id") or ""),
                        issuer_office_id=office.office_id,
                    )
                elif movement_order == "march":
                    next_world = order_army_march(
                        next_world,
                        faction_id=faction_id,
                        army_id=str(payload.get("army_id") or ""),
                        destination_node_id=str(payload.get("destination_node_id") or ""),
                        issuer_office_id=office.office_id,
                    )
                elif movement_order == "intercept":
                    next_world = order_army_intercept(
                        next_world,
                        faction_id=faction_id,
                        army_id=str(payload.get("army_id") or ""),
                        target_army_id=str(payload.get("target_army_id") or ""),
                        issuer_office_id=office.office_id,
                    )
                elif movement_order == "reinforce":
                    next_world = order_army_reinforce(
                        next_world,
                        faction_id=faction_id,
                        army_id=str(payload.get("army_id") or ""),
                        encounter_id=str(payload.get("target_encounter_id") or ""),
                        issuer_office_id=office.office_id,
                    )
                else:
                    next_world = order_army_retreat(
                        next_world,
                        faction_id=faction_id,
                        army_id=str(payload.get("army_id") or ""),
                        destination_node_id=str(payload.get("destination_node_id") or ""),
                        issuer_office_id=office.office_id,
                    )
            elif action.action_type == "load_army_supply":
                next_world = load_army_supply(
                    next_world,
                    faction_id=faction_id,
                    army_id=str(payload.get("army_id") or ""),
                    supply=int(payload.get("supply") or 0),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "set_siege_attacker_stance":
                next_world = order_siege_attacker_stance(
                    next_world,
                    faction_id=faction_id,
                    siege_id=str(payload.get("siege_id") or ""),
                    stance=str(payload.get("stance") or ""),
                    destination_node_id=str(payload.get("destination_node_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "set_siege_defender_stance":
                next_world = order_siege_defender_stance(
                    next_world,
                    faction_id=faction_id,
                    siege_id=str(payload.get("siege_id") or ""),
                    stance=str(payload.get("stance") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "appoint_strategic_hero":
                next_world = appoint_strategic_hero_to_office(
                    next_world,
                    faction_id=faction_id,
                    issuer_office_id=office.office_id,
                    target_office_id=str(payload.get("target_office_id") or ""),
                    hero_code=str(payload.get("hero_code") or ""),
                )
            elif action.action_type == "assign_strategic_hero_duty":
                next_world = assign_strategic_hero_duty(
                    next_world,
                    faction_id=faction_id,
                    issuer_office_id=office.office_id,
                    hero_code=str(payload.get("hero_code") or ""),
                    assignment_type=str(payload.get("assignment_type") or "reserve"),
                    target_id=str(payload.get("target_id") or ""),
                )
            elif action.action_type == "construct_city_building":
                next_world = construct_city_building(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    building_id=str(payload.get("building_id") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "upgrade_city_settlement":
                next_world = upgrade_city_settlement(
                    next_world,
                    faction_id=faction_id,
                    city_id=str(payload.get("city_id") or ""),
                    settlement=str(payload.get("settlement") or ""),
                    issuer_office_id=office.office_id,
                )
            elif action.action_type == "declare_attack":
                resolution_mode = str(payload.get("resolution_mode") or "quick")
                action_user = AuthUser(
                    user_id=int(action.user_id),
                    username=str(action.username or f"User {action.user_id}"),
                    created_at=0.0,
                )
                next_world, battle_room = declare_strategy_attack_for_world(
                    campaign,
                    next_world,
                    action_user,
                    faction_id=faction_id,
                    source_city_id=str(payload.get("source_city_id") or ""),
                    target_city_id=str(payload.get("target_city_id") or ""),
                    resolution_mode=resolution_mode,
                    attacker_hero_codes=strategy_hero_codes_from_payload(payload),
                    attacker_office_id=office.office_id,
                    committed_troops=parse_optional_int(
                        payload.get("committed_troops", payload.get("attacker_troops"))
                    ),
                )
                if battle_room is not None:
                    battle_room["queued_action_id"] = action.action_id
                    battle_room["queued_user_id"] = action.user_id
                    battle_rooms.append(battle_room)
            elif action.action_type in {"issue_office_order", "send_office_request"}:
                next_world = apply_office_order(
                    next_world,
                    issuer_office_id=office.office_id,
                    receiver_office_id=str(payload.get("receiver_office_id") or ""),
                    order_type=str(payload.get("office_order_type") or ("request" if action.action_type == "send_office_request" else "order")),
                    objective=str(payload.get("objective") or ""),
                    target_entity_id=str(payload.get("target_entity_id") or ""),
                    priority=int(payload.get("priority") or 1),
                    deadline_month=(
                        int(payload["deadline_month"])
                        if payload.get("deadline_month") not in {None, ""}
                        else None
                    ),
                    details={"policy": str(payload.get("city_policy") or "")} if str(payload.get("office_order_type") or "") == "set_policy" else None,
                )
            else:
                raise StrategyError("Unknown strategy action type.")
        except StrategyError as exc:
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="queued_action_failed",
                    message=f"Queued action from {action.username} skipped: {exc}",
                    related_ids=[str(action.action_id), str(action.user_id), str(action.faction_id)],
                )
            )
            next_world.validate()
    return next_world, battle_rooms
