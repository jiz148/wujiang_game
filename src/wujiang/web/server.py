from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

from wujiang.engine.core import ActionError
from wujiang.heroes.registry import create_battle, list_heroes
from wujiang.strategy import (
    EventLogEntry,
    StrategyError,
    StrategyStore,
    advance_month,
    apply_office_order,
    appoint_strategic_hero_to_office,
    assign_strategic_hero_duty,
    perform_hero_ritual,
    unbind_strategic_hero,
    apply_rebellion_action,
    apply_rebellion_battle,
    apply_strategy_ai_monthly_actions,
    apply_exile_action,
    attach_battle_room,
    choose_player_hero_path,
    declare_city_attack,
    normalize_strategic_hero_deployment,
    increase_city_troops,
    register_city_soldiers,
    transfer_registered_units,
    request_registered_units,
    approve_registered_unit_request,
    construct_city_building,
    resolve_battle_room_result as resolve_strategy_battle_room_result,
    resolve_story_event,
    resolve_action_office,
    set_battle_defender_hero,
    set_city_policy,
    set_strategic_defender_hero,
    strategy_battle_rosters,
    unlock_tactic_tech,
    validate_exile_action,
    validate_rebellion_action,
    validate_rebellion_battle,
    validate_story_event_choice,
)
from wujiang.strategy.command import faction_command_points, strategy_action_command_cost
from wujiang.web.auth import AuthError, AuthUser, UserStore
from wujiang.web.multiplayer import DEFAULT_ROOM_MODE, ROOMS, RoomError, battle_state_for_viewer


PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_ROOT = PROJECT_ROOT / "static"
PUBLIC_BASE_URL: str | None = None
AUTH_STORE = UserStore()
STRATEGY_STORE = StrategyStore()
CITY_MONTHLY_ORDER_LIMIT = 2


class GameSession:
    def __init__(self) -> None:
        self.battle = None

    def serialize_state(self) -> dict[str, Any]:
        if self.battle is None:
            return {"battle": None, "heroes": list_heroes()}
        input_player = self.battle.to_public_dict()["input_player"]
        state = battle_state_for_viewer(self.battle, input_player)
        return {"battle": state, "heroes": list_heroes()}


SESSION = GameSession()


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def normalize_public_base_url(base_url: str | None) -> str | None:
    raw = str(base_url or "").strip()
    if not raw:
        return None
    candidate = raw.rstrip("/")
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("`--public-base-url` 必须是像 `http://203.0.113.10:8000` 这样的完整地址。")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment or " " in parsed.netloc:
        raise ValueError("`--public-base-url` 只能填写站点根地址，不能包含路径、查询参数或空格。")
    return candidate


def configure_public_base_url(base_url: str | None) -> str | None:
    global PUBLIC_BASE_URL
    PUBLIC_BASE_URL = normalize_public_base_url(base_url)
    return PUBLIC_BASE_URL


def first_header_value(raw_value: str | None) -> str:
    return str(raw_value or "").split(",", 1)[0].strip()


def request_base_url(handler: BaseHTTPRequestHandler) -> str | None:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    host = first_header_value(handler.headers.get("X-Forwarded-Host")) or first_header_value(handler.headers.get("Host"))
    if not host:
        return None
    scheme = first_header_value(handler.headers.get("X-Forwarded-Proto")) or "http"
    return f"{scheme}://{host}"


def request_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    body = handler.rfile.read(content_length) if content_length else b"{}"
    return json.loads(body.decode("utf-8"))


def auth_token_from_request(
    handler: BaseHTTPRequestHandler,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, list[str]] | None = None,
) -> str:
    auth_header = str(handler.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    if payload is not None:
        token = payload.get("session_token") or payload.get("auth_token")
        if token:
            return str(token)
    if query is not None:
        token_values = query.get("session_token") or query.get("auth_token") or []
        if token_values:
            return str(token_values[0])
    return ""


def auth_error_response(handler: BaseHTTPRequestHandler, exc: AuthError) -> None:
    json_response(handler, int(exc.status), {"error": str(exc)})


def strategy_error_response(handler: BaseHTTPRequestHandler, exc: StrategyError) -> None:
    json_response(handler, int(exc.status), {"error": str(exc)})


def authenticated_user_from_request(
    handler: BaseHTTPRequestHandler,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, list[str]] | None = None,
):
    token = auth_token_from_request(handler, payload=payload, query=query)
    if not token:
        raise AuthError("请先登录账号。", status=HTTPStatus.UNAUTHORIZED)
    return AUTH_STORE.user_for_session(token)


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
    if action_type in {"set_city_policy", "rebellion_action", "rebellion_battle"}:
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
    if normalized_type == "set_city_policy":
        city_id = str(payload.get("city_id") or "").strip()
        policy = str(payload.get("policy") or "").strip()
        normalized_payload = {"city_id": city_id, "policy": policy}
        set_city_policy(campaign.world, faction_id=faction_id, city_id=city_id, policy=policy)
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
        unlock_tactic_tech(campaign.world, faction_id=faction_id, tech_id=tech_id)
        return finalize(tech_id, normalized_payload)
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
    if normalized_type in {"increase_city_troops", "register_city_soldiers", "construct_city_building"}:
        city_id = str(payload.get("city_id") or "").strip()
        building_id = str(payload.get("building_id") or "").strip()
        unit_count = max(1, min(3, int(payload.get("unit_count") or 1)))
        normalized_payload = {"city_id": city_id}
        if normalized_type == "construct_city_building":
            normalized_payload["building_id"] = building_id
        if normalized_type == "register_city_soldiers":
            normalized_payload["unit_count"] = unit_count
        action_target = "increase" if normalized_type == "increase_city_troops" else building_id or str(unit_count)
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
        normalized_payload = {
            "source_city_id": source_city_id,
            "target_city_id": target_city_id,
            "resolution_mode": resolution_mode,
            "attacker_hero_codes": attacker_hero_codes,
        }
        result = finalize(f"{source_city_id}->{target_city_id}", normalized_payload)
        issuer = next(
            office for office in campaign.world.offices if office.office_id == normalized_payload["issuer_office_id"]
        )
        if issuer.office_type == "lord":
            commander_code = str(issuer.holder_id or "")
            if not commander_code:
                raise StrategyError("主公职位没有武将担任，不能亲征。")
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
        if office_order_type not in {"order", "request", "attack_city", "defend_city"}:
            raise StrategyError("职位命令类型无效。")
        normalized_payload = {
            "receiver_office_id": receiver_office_id,
            "objective": objective,
            "target_entity_id": target_entity_id,
            "priority": priority,
            "deadline_month": deadline_month,
            "office_order_type": office_order_type,
        }
        _, action_key, normalized_payload = finalize(
            f"{receiver_office_id}:{len(campaign.world.office_orders) + 1}",
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


def strategy_city_name(campaign, city_id: str) -> str:
    for city in campaign.world.cities:
        if getattr(city, "city_id", "") == city_id:
            return city.name
    return str(city_id)


def create_strategy_battle_room(
    campaign,
    auth_user,
    battle,
    resolution_mode: str,
) -> dict[str, Any]:
    rosters = strategy_battle_rosters(campaign.world, battle)
    if not rosters.attacker.roster or not rosters.defender.roster:
        raise StrategyError("战略战斗参战单位不足，无法创建真实格子战房间。", status=HTTPStatus.CONFLICT)
    source_name = strategy_city_name(campaign, battle.source_city_id)
    target_name = strategy_city_name(campaign, battle.target_city_id)
    room, _player_id, player_token = ROOMS.create_preconfigured_battle_room(
        host_name=f"{auth_user.username} · {source_name}",
        opponent_name=f"{target_name}守军",
        player1_roster=rosters.attacker.roster,
        player2_roster=rosters.defender.roster,
        start_immediately=True,
        host_becomes_ai_after_start=resolution_mode in {"watch_ai", "ai_auto"},
    )
    return {
        "room_id": room.room_id,
        "invite_path": room.invite_path(),
        "invite_url": room.invite_url(None),
        "player_token": player_token if resolution_mode == "manual" else "",
        "mode": room.mode,
        "status": room.status,
        "winner": getattr(room.battle, "winner", None),
        "attacker_roster": rosters.attacker.roster,
        "defender_roster": rosters.defender.roster,
        "attacker_roster_manifest": rosters.attacker.manifest,
        "defender_roster_manifest": rosters.defender.manifest,
    }


def strategy_room_battle_summary(room) -> str:
    battle = getattr(room, "battle", None)
    logs = getattr(battle, "logs", []) or []
    return " ".join(str(item) for item in logs[-5:])


def strategy_room_survivors_by_team(room) -> dict[int, int]:
    battle = getattr(room, "battle", None)
    surviving_grid_units_by_team = {1: 0, 2: 0}
    if battle is None:
        return surviving_grid_units_by_team
    for unit in battle.all_units():
        if getattr(unit, "is_summon", False) or getattr(unit, "is_clone", False):
            continue
        if not getattr(unit, "alive", False) or getattr(unit, "banished", False):
            continue
        hero_code = str(getattr(unit, "hero_code", "") or "")
        if not hero_code.startswith("strategy_"):
            continue
        player_id = int(getattr(unit, "player_id", 0))
        if player_id in surviving_grid_units_by_team:
            surviving_grid_units_by_team[player_id] += 1
    return surviving_grid_units_by_team


def strategy_room_surviving_hero_codes_by_team(room) -> dict[int, set[str]]:
    battle = getattr(room, "battle", None)
    surviving_hero_codes_by_team: dict[int, set[str]] = {1: set(), 2: set()}
    if battle is None:
        return surviving_hero_codes_by_team
    for unit in battle.all_units():
        if getattr(unit, "is_summon", False) or getattr(unit, "is_clone", False):
            continue
        if not getattr(unit, "alive", False) or getattr(unit, "banished", False):
            continue
        hero_code = str(getattr(unit, "hero_code", "") or "")
        if not hero_code or hero_code.startswith("strategy_"):
            continue
        player_id = int(getattr(unit, "player_id", 0))
        if player_id in surviving_hero_codes_by_team:
            surviving_hero_codes_by_team[player_id].add(hero_code)
    return surviving_hero_codes_by_team


def declare_strategy_attack_for_world(
    campaign,
    world,
    auth_user,
    *,
    faction_id: str,
    source_city_id: str,
    target_city_id: str,
    resolution_mode: str,
    attacker_hero_codes: list[str] | tuple[str, ...] | set[str] | None = None,
    attacker_office_id: str = "",
) -> tuple[Any, dict[str, Any] | None]:
    next_world = declare_city_attack(
        world,
        faction_id=faction_id,
        source_city_id=source_city_id,
        target_city_id=target_city_id,
        resolution_mode=resolution_mode,
        auto_resolve=resolution_mode == "quick",
        attacker_hero_codes=attacker_hero_codes,
        attacker_office_id=attacker_office_id,
    )
    if resolution_mode not in {"manual", "watch_ai", "ai_auto"}:
        return next_world, None

    pending_battle = next_world.pending_battles[-1]
    battle_room = create_strategy_battle_room(
        campaign=SimpleNamespace(world=next_world),
        auth_user=auth_user,
        battle=pending_battle,
        resolution_mode=resolution_mode,
    )
    next_world = attach_battle_room(
        next_world,
        battle_id=pending_battle.battle_id,
        room_id=battle_room["room_id"],
        invite_path=battle_room["invite_path"],
    )
    if resolution_mode == "ai_auto":
        room = ROOMS.get_room(str(battle_room["room_id"]))
        simulation_steps = room.run_ai_simulation_to_end()
        battle_room["status"] = room.status
        battle_room["winner"] = getattr(room.battle, "winner", None)
        battle_room["simulation_steps"] = simulation_steps
        winner_team_id = getattr(room.battle, "winner", None)
        if winner_team_id in {1, 2}:
            next_world = resolve_strategy_battle_room_result(
                next_world,
                battle_room_id=str(battle_room["room_id"]),
                winner_team_id=int(winner_team_id),
                battle_summary=strategy_room_battle_summary(room),
                surviving_grid_units_by_team=strategy_room_survivors_by_team(room),
                surviving_hero_codes_by_team=strategy_room_surviving_hero_codes_by_team(room),
            )
    return next_world, battle_room


def sync_finished_strategy_battle_room(room) -> dict[str, Any] | None:
    battle = getattr(room, "battle", None)
    winner_team_id = getattr(battle, "winner", None)
    if winner_team_id not in {1, 2}:
        return None
    campaign = STRATEGY_STORE.resolve_battle_room_result(
        battle_room_id=getattr(room, "room_id", ""),
        winner_team_id=int(winner_team_id),
        battle_summary=strategy_room_battle_summary(room),
        surviving_grid_units_by_team=strategy_room_survivors_by_team(room),
        surviving_hero_codes_by_team=strategy_room_surviving_hero_codes_by_team(room),
    )
    if campaign is None:
        return None
    return campaign.to_public_dict(resume_status=STRATEGY_STORE.resume_status(campaign.campaign_id))


def room_state_with_strategy_sync(room, player_token: str | None, *, base_url: str | None) -> dict[str, Any]:
    state = room.serialize_state(player_token, base_url=base_url)
    strategy_campaign = sync_finished_strategy_battle_room(room)
    if strategy_campaign is not None:
        state["strategy_campaign"] = strategy_campaign
    return state


def extract_room_action(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("action")
    if isinstance(nested, dict):
        return nested
    return {
        key: value
        for key, value in payload.items()
        if key not in {"room_id", "player_token", "player_name", "hero_code", "delta"}
    }


class WujiangHandler(BaseHTTPRequestHandler):
    server_version = "WujiangHTTP/0.2"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/heroes":
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "heroes": list_heroes(),
                    "rooms": ROOMS.list_rooms(base_url=request_base_url(self)),
                },
            )
            return
        if parsed.path == "/api/auth/me":
            token = auth_token_from_request(self, query=query)
            if not token:
                json_response(self, HTTPStatus.OK, {"user": None})
                return
            try:
                user = AUTH_STORE.user_for_session(token)
            except AuthError as exc:
                auth_error_response(self, exc)
                return
            json_response(self, HTTPStatus.OK, {"user": user.to_public_dict()})
            return
        if parsed.path == "/api/strategy/campaigns":
            try:
                user = authenticated_user_from_request(self, query=query)
                campaigns = STRATEGY_STORE.list_campaigns_for_user(user.user_id)
            except AuthError as exc:
                auth_error_response(self, exc)
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "campaigns": [
                        campaign.to_public_dict(resume_status=STRATEGY_STORE.resume_status(campaign.campaign_id))
                        for campaign in campaigns
                    ],
                },
            )
            return
        if parsed.path == "/api/rooms":
            json_response(self, HTTPStatus.OK, {"rooms": ROOMS.list_rooms(base_url=request_base_url(self))})
            return
        if parsed.path == "/api/state":
            json_response(self, HTTPStatus.OK, SESSION.serialize_state())
            return
        if parsed.path == "/api/rooms/state":
            room_id = (query.get("room_id") or query.get("room") or [""])[0]
            player_token = (query.get("player_token") or [""])[0] or None
            try:
                room = ROOMS.get_room(room_id)
            except RoomError as exc:
                json_response(self, HTTPStatus.NOT_FOUND, {"error": "房间不存在，可能是房间码输错了。"})
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, player_token, base_url=request_base_url(self)),
            )
            return
        if parsed.path == "/api/rooms/replay":
            room_id = (query.get("room_id") or query.get("room") or [""])[0]
            player_token = (query.get("player_token") or [""])[0] or None
            step_index = (query.get("step_index") or ["0"])[0]
            omniscient = (query.get("omniscient") or ["0"])[0] in {"1", "true", "yes", "on"}
            try:
                room = ROOMS.get_room(room_id)
                payload = room.serialize_replay_step(player_token, step_index=step_index, omniscient=omniscient)
            except RoomError as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            json_response(self, HTTPStatus.OK, payload)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = request_json(self)
        except json.JSONDecodeError:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "请求体不是有效 JSON。"})
            return

        auth_user = None
        if (
            parsed.path in {"/api/new-game", "/api/action"}
            or parsed.path.startswith("/api/rooms/")
            or parsed.path.startswith("/api/strategy/")
        ):
            try:
                auth_user = authenticated_user_from_request(self, payload=payload)
            except AuthError as exc:
                auth_error_response(self, exc)
                return

        if parsed.path == "/api/new-game":
            hero1 = payload.get("player1")
            hero2 = payload.get("player2")
            if not hero1 or not hero2:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "需要同时选择双方武将。"})
                return
            try:
                SESSION.battle = create_battle(str(hero1), str(hero2))
            except KeyError as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            json_response(self, HTTPStatus.OK, SESSION.serialize_state())
            return

        if parsed.path == "/api/action":
            if SESSION.battle is None:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "请先开始对局。"})
                return
            try:
                SESSION.battle.perform_action(payload)
            except ActionError as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc), "state": SESSION.serialize_state()})
                return
            json_response(self, HTTPStatus.OK, SESSION.serialize_state())
            return

        if parsed.path == "/api/auth/register":
            try:
                user, session_token = AUTH_STORE.register(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                )
            except AuthError as exc:
                auth_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"user": user.to_public_dict(), "session_token": session_token},
            )
            return

        if parsed.path == "/api/auth/login":
            try:
                user, session_token = AUTH_STORE.authenticate(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                )
            except AuthError as exc:
                auth_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"user": user.to_public_dict(), "session_token": session_token},
            )
            return

        if parsed.path == "/api/auth/logout":
            AUTH_STORE.logout(auth_token_from_request(self, payload=payload))
            json_response(self, HTTPStatus.OK, {"ok": True})
            return

        if parsed.path == "/api/strategy/campaigns/create":
            try:
                assert auth_user is not None
                campaign = STRATEGY_STORE.create_campaign(
                    owner=auth_user,
                    name=str(payload.get("name") or "新战役"),
                    seed=int(payload.get("seed", 1)),
                    city_count=int(payload.get("city_count", 8)),
                    faction_count=int(payload.get("faction_count", 2)),
                )
                resume_status = STRATEGY_STORE.mark_online(campaign.campaign_id, auth_user)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign.campaign_id, auth_user.user_id)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役参数格式不正确。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/join":
            try:
                assert auth_user is not None
                campaign = STRATEGY_STORE.join_campaign_by_code(
                    str(payload.get("join_code") or ""),
                    auth_user,
                )
                resume_status = STRATEGY_STORE.mark_online(campaign.campaign_id, auth_user)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign.campaign_id, auth_user.user_id)
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/lock":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                campaign = STRATEGY_STORE.lock_initial_players(campaign_id, auth_user.user_id)
                resume_status = STRATEGY_STORE.mark_online(campaign.campaign_id, auth_user)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign.campaign_id, auth_user.user_id)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "æˆ˜å½¹ ID å¿…é¡»æ˜¯æ•´æ•°ã€‚"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/rotate-join-code":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                campaign = STRATEGY_STORE.rotate_join_code(campaign_id, auth_user.user_id)
                resume_status = STRATEGY_STORE.resume_status(campaign.campaign_id)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/enter":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                resume_status = STRATEGY_STORE.mark_online(campaign_id, auth_user)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/leave":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                resume_status = STRATEGY_STORE.mark_offline(campaign_id, auth_user.user_id)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(self, HTTPStatus.OK, {"resume": resume_status.to_dict()})
            return

        if parsed.path == "/api/strategy/campaigns/resume":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                resume_status = STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/choose-hero-path":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
                path = str(payload.get("path") or "")
                if campaign.status != "lobby" and path == "lord":
                    raise StrategyError("战役开始后不能直接接任既有势力主公。", status=HTTPStatus.CONFLICT)
                assigned_faction_id = next(
                    (
                        member.faction_id
                        for member in campaign.members
                        if int(member.user_id) == int(auth_user.user_id)
                    ),
                    "",
                )
                next_world = choose_player_hero_path(
                    campaign.world,
                    user_id=auth_user.user_id,
                    hero_code=str(payload.get("hero_code") or ""),
                    path=path,
                    assigned_faction_id=assigned_faction_id,
                    target_faction_id=str(payload.get("target_faction_id") or ""),
                    allow_reselect=campaign.status == "lobby",
                )
                campaign = STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
                resume_status = STRATEGY_STORE.resume_status(campaign_id)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/advance-month":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                resume_status = STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
                require_campaign_owner(campaign, auth_user.user_id)
                require_strategy_action_office(
                    campaign,
                    user_id=auth_user.user_id,
                    action_type="advance_month",
                    payload=payload,
                )
                action_month = campaign.world.current_month
                next_world, battle_rooms = apply_strategy_action_queue(campaign)
                controlled_faction_ids = {
                    member.faction_id
                    for member in campaign.members
                    if str(getattr(member, "role", "")).lower() != "ai" and int(member.user_id) > 0
                }
                next_world = apply_strategy_ai_monthly_actions(
                    next_world,
                    controlled_faction_ids=controlled_faction_ids,
                )
                next_world = advance_month(next_world)
                campaign = STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
                STRATEGY_STORE.mark_queued_actions_resolved(campaign_id, auth_user.user_id, action_month)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "campaign": campaign.to_public_dict(resume_status=resume_status),
                    **({"battle_rooms": battle_rooms} if battle_rooms else {}),
                },
            )
            return

        if parsed.path == "/api/strategy/campaigns/queue-action":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
                action_type, action_key, action_payload = normalize_strategy_action_payload(
                    campaign,
                    auth_user.user_id,
                    str(payload.get("action_type") or ""),
                    payload.get("action_payload") or payload.get("payload") or {},
                )
                resume_status = STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
                enforce_city_order_limit(
                    campaign,
                    user_id=auth_user.user_id,
                    action_type=action_type,
                    action_key=action_key,
                    payload=action_payload,
                )
                enforce_faction_command_points(
                    campaign,
                    user_id=auth_user.user_id,
                    action_type=action_type,
                    action_key=action_key,
                    payload=action_payload,
                )
                campaign = STRATEGY_STORE.queue_action(
                    campaign_id=campaign_id,
                    user=auth_user,
                    action_type=action_type,
                    action_key=action_key,
                    payload=action_payload,
                )
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "æˆ˜å½¹ ID å¿…é¡»æ˜¯æ•´æ•°ã€‚"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/set-city-policy":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                city_id = str(payload.get("city_id") or "")
                policy = str(payload.get("policy") or "")
                resume_status = STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
                faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
                require_strategy_action_office(
                    campaign,
                    user_id=auth_user.user_id,
                    action_type="set_city_policy",
                    payload={**payload, "city_id": city_id},
                )
                next_world = set_city_policy(
                    campaign.world,
                    faction_id=faction_id,
                    city_id=city_id,
                    policy=policy,
                )
                campaign = STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/set-defense-hero":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                hero_code = str(payload.get("hero_code") or "")
                resume_status = STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
                faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
                require_strategy_action_office(
                    campaign,
                    user_id=auth_user.user_id,
                    action_type="set_strategic_defender_hero",
                    payload=payload,
                )
                next_world = set_strategic_defender_hero(
                    campaign.world,
                    faction_id=faction_id,
                    hero_code=hero_code,
                )
                campaign = STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/set-battle-defense-hero":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                battle_id = str(payload.get("battle_id") or "")
                hero_codes = strategy_defender_hero_codes_from_payload(payload)
                resume_status = STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
                faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
                require_strategy_action_office(
                    campaign,
                    user_id=auth_user.user_id,
                    action_type="set_battle_defender_hero",
                    payload={**payload, "battle_id": battle_id},
                )
                next_world = set_battle_defender_hero(
                    campaign.world,
                    faction_id=faction_id,
                    battle_id=battle_id,
                    hero_code=hero_codes,
                )
                campaign = STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "æˆ˜å½¹ ID å¿…é¡»æ˜¯æ•´æ•°ã€‚"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/unlock-tactic-tech":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                tech_id = str(payload.get("tech_id") or "")
                resume_status = STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
                faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
                require_strategy_action_office(
                    campaign,
                    user_id=auth_user.user_id,
                    action_type="unlock_tactic_tech",
                    payload={**payload, "tech_id": tech_id},
                )
                next_world = unlock_tactic_tech(campaign.world, faction_id=faction_id, tech_id=tech_id)
                campaign = STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {"campaign": campaign.to_public_dict(resume_status=resume_status)},
            )
            return

        if parsed.path == "/api/strategy/campaigns/declare-attack":
            try:
                assert auth_user is not None
                campaign_id = int(payload.get("campaign_id"))
                source_city_id = str(payload.get("source_city_id") or "")
                target_city_id = str(payload.get("target_city_id") or "")
                resolution_mode = str(payload.get("resolution_mode") or "quick")
                resume_status = STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
                campaign = STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
                faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
                attack_office = require_strategy_action_office(
                    campaign,
                    user_id=auth_user.user_id,
                    action_type="declare_attack",
                    payload={**payload, "source_city_id": source_city_id, "target_city_id": target_city_id},
                )
                attacker_hero_codes = normalize_strategic_hero_deployment(
                    campaign.world,
                    faction_id,
                    strategy_hero_codes_from_payload(payload),
                )
                next_world, battle_room = declare_strategy_attack_for_world(
                    campaign,
                    campaign.world,
                    auth_user,
                    faction_id=faction_id,
                    source_city_id=source_city_id,
                    target_city_id=target_city_id,
                    resolution_mode=resolution_mode,
                    attacker_hero_codes=attacker_hero_codes,
                    attacker_office_id=attack_office.office_id,
                )
                campaign = STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
                return
            except StrategyError as exc:
                strategy_error_response(self, exc)
                return
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "campaign": campaign.to_public_dict(resume_status=resume_status),
                    **({"battle_room": battle_room} if battle_room is not None else {}),
                },
            )
            return

        if parsed.path == "/api/rooms/create":
            assert auth_user is not None
            player_name = payload.get("player_name") or auth_user.username
            room_mode = payload.get("mode", DEFAULT_ROOM_MODE)
            try:
                room, player_id, player_token = ROOMS.create_room(str(player_name), str(room_mode or DEFAULT_ROOM_MODE))
            except RoomError as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            response = room.serialize_state(player_token, base_url=request_base_url(self))
            response["player_token"] = player_token
            response["joined_player_id"] = player_id
            json_response(self, HTTPStatus.OK, response)
            return

        if parsed.path == "/api/rooms/join":
            assert auth_user is not None
            room_id = payload.get("room_id", "")
            player_name = payload.get("player_name") or auth_user.username
            try:
                room = ROOMS.get_room(str(room_id))
                player_id, player_token = room.join(str(player_name))
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload: dict[str, Any] = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(
                        None,
                        base_url=request_base_url(self),
                    )
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            response = room.serialize_state(player_token, base_url=request_base_url(self))
            response["player_token"] = player_token
            response["joined_player_id"] = player_id
            json_response(self, HTTPStatus.OK, response)
            return

        if parsed.path == "/api/rooms/select-hero":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            hero_code = payload.get("hero_code", "")
            delta = payload.get("delta", 1)
            seat_id = payload.get("seat_id")
            try:
                room = ROOMS.get_room(str(room_id))
                room.select_hero(str(player_token or ""), str(hero_code), delta, seat_id=seat_id)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload: dict[str, Any] = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(self)),
            )
            return

        if parsed.path == "/api/rooms/set-seat-count":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            seat_count = payload.get("seat_count", 2)
            try:
                room = ROOMS.get_room(str(room_id))
                room.set_seat_count(str(player_token or ""), seat_count)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(self)),
            )
            return

        if parsed.path == "/api/rooms/set-seat-team":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            seat_id = payload.get("seat_id")
            team_id = payload.get("team_id")
            try:
                room = ROOMS.get_room(str(room_id))
                room.set_seat_team(str(player_token or ""), seat_id, team_id)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(self)),
            )
            return

        if parsed.path == "/api/rooms/set-seat-controller":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            seat_id = payload.get("seat_id")
            controller_type = payload.get("controller_type", "open")
            try:
                room = ROOMS.get_room(str(room_id))
                room.set_seat_controller(str(player_token or ""), seat_id, str(controller_type or "open"))
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(self)),
            )
            return

        if parsed.path == "/api/rooms/start":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            try:
                room = ROOMS.get_room(str(room_id))
                room.start_battle(str(player_token or ""))
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(self)),
            )
            return

        if parsed.path == "/api/rooms/set-mode":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            room_mode = payload.get("mode", DEFAULT_ROOM_MODE)
            try:
                room = ROOMS.get_room(str(room_id))
                room.set_mode(str(player_token or ""), str(room_mode or DEFAULT_ROOM_MODE))
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(self)),
            )
            return

        if parsed.path == "/api/rooms/set-default-ai-difficulty":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            difficulty = payload.get("difficulty", "standard")
            try:
                room = ROOMS.get_room(str(room_id))
                room.set_default_ai_difficulty(str(player_token or ""), difficulty)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(self, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(self)))
            return

        if parsed.path == "/api/rooms/set-seat-ai-difficulty":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            seat_id = payload.get("seat_id")
            difficulty = payload.get("difficulty", "standard")
            try:
                room = ROOMS.get_room(str(room_id))
                room.set_seat_ai_difficulty(str(player_token or ""), seat_id, difficulty)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(self, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(self)))
            return

        if parsed.path == "/api/rooms/set-random-roster-size":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            roster_size = payload.get("random_roster_size", 1)
            try:
                room = ROOMS.get_room(str(room_id))
                room.set_random_roster_size(str(player_token or ""), roster_size)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(self, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(self)))
            return

        if parsed.path == "/api/rooms/set-seat-random-quota":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            seat_id = payload.get("seat_id")
            quota = payload.get("quota", 0)
            try:
                room = ROOMS.get_room(str(room_id))
                room.set_random_quota(str(player_token or ""), seat_id, quota)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(self, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(self)))
            return

        if parsed.path == "/api/rooms/rematch":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            try:
                room = ROOMS.get_room(str(room_id))
                room.restart_lobby(str(player_token or ""))
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(self, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(self)))
            return

        if parsed.path == "/api/rooms/simulation-control":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            action = payload.get("action", "")
            speed = payload.get("speed")
            try:
                room = ROOMS.get_room(str(room_id))
                room.control_simulation(str(player_token or ""), str(action or ""), speed=speed)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(self)),
            )
            return

        if parsed.path == "/api/rooms/delete":
            room_id = payload.get("room_id", "")
            player_token = str(payload.get("player_token") or "")
            try:
                ROOMS.delete_room(str(room_id), player_token)
            except RoomError as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "deleted_room_id": str(room_id).strip().upper(),
                    "rooms": ROOMS.list_rooms(base_url=request_base_url(self)),
                },
            )
            return

        if parsed.path == "/api/rooms/leave":
            room_id = payload.get("room_id", "")
            player_token = str(payload.get("player_token") or "")
            try:
                deleted, leaving_player_id = ROOMS.leave_room(str(room_id), player_token)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload: dict[str, Any] = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(None, base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            response_payload = {
                "left_room_id": str(room_id).strip().upper(),
                "left_player_id": leaving_player_id,
                "room_deleted": deleted,
                "rooms": ROOMS.list_rooms(base_url=request_base_url(self)),
            }
            json_response(self, HTTPStatus.OK, response_payload)
            return

        if parsed.path == "/api/rooms/surrender":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            try:
                room = ROOMS.get_room(str(room_id))
                room.surrender(str(player_token or ""))
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(self)),
            )
            return

        if parsed.path == "/api/rooms/action":
            room_id = payload.get("room_id", "")
            player_token = payload.get("player_token")
            action_payload = extract_room_action(payload)
            try:
                room = ROOMS.get_room(str(room_id))
                room.perform_action(str(player_token or ""), action_payload)
            except RoomError as exc:
                room = None
                try:
                    room = ROOMS.get_room(str(room_id))
                except RoomError:
                    pass
                error_payload = {"error": str(exc)}
                if room is not None:
                    error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(self))
                json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
                return
            json_response(
                self,
                HTTPStatus.OK,
                room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(self)),
            )
            return

        json_response(self, HTTPStatus.NOT_FOUND, {"error": "未知接口。"})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return None

    def serve_static(self, url_path: str) -> None:
        relative = "index.html" if url_path in {"", "/"} else url_path.lstrip("/")
        file_path = (STATIC_ROOT / relative).resolve()
        if not str(file_path).startswith(str(STATIC_ROOT.resolve())) or not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime_type, _ = mimetypes.guess_type(file_path.name)
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_server(host: str = "127.0.0.1", port: int = 8000, public_base_url: str | None = None) -> None:
    share_base_url = configure_public_base_url(public_base_url)
    httpd = ThreadingHTTPServer((host, port), WujiangHandler)
    print(f"Wujiang server running at http://{host}:{port}")
    if host == "0.0.0.0":
        print(f"Local browser URL: http://127.0.0.1:{port}")
    if share_base_url:
        print(f"Share this homepage with friends: {share_base_url}/")
        print(f"Copied room invite links will use: {share_base_url}/?room=ROOMID")
    elif host == "0.0.0.0":
        print(f"Share your LAN/public IP manually, for example: http://<your-ip>:{port}/")
    httpd.serve_forever()
