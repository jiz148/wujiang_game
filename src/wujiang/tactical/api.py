"""Grid-battle endpoints: rooms, seats, actions, replays and match history."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from wujiang.bridge.battle_bridge import recover_strategy_battle_room
from wujiang.bridge.battle_bridge import require_strategy_room_mutation_allowed
from wujiang.bridge.battle_bridge import room_state_with_strategy_sync
from wujiang.bridge.battle_bridge import strategy_room_token_for_user
from wujiang.platform.auth import AuthError
from wujiang.platform.http.runtime import auth_error_response
from wujiang.platform.http.runtime import auth_token_from_request
from wujiang.platform.http.runtime import authenticated_user_from_request
from wujiang.platform.http.runtime import json_response
from wujiang.platform.http.runtime import request_base_url
from wujiang.platform.match_history import MatchHistoryError
from wujiang.strategic import StrategyError
from wujiang.tactical.rooms.launch import make_launch_context
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.heroes.registry import create_battle
from wujiang.tactical.heroes.registry import list_heroes
from wujiang.tactical.rooms.history import historical_replay_payload
from wujiang.tactical.rooms.history import record_finished_room_history
from wujiang.tactical.rooms.multiplayer import DEFAULT_ROOM_MODE
from wujiang.tactical.rooms.multiplayer import ROOMS
from wujiang.tactical.rooms.multiplayer import RoomError
from wujiang.tactical.rooms.onboarding import onboarding_payload
from wujiang.tactical.rooms.onboarding import quick_ai_match_payload
from wujiang.tactical.rooms.onboarding import recommended_roster_hero_codes
from wujiang.tactical.session import SESSION
from wujiang.tactical.session import extract_room_action
from wujiang.platform.http.context import RequestContext
from wujiang.platform.http.routing import get, post
from wujiang.platform.http import runtime
from wujiang.strategic import campaign_runtime


@get("/api/heroes")
def get_heroes(ctx: RequestContext) -> None:
    handler = ctx.handler
    heroes = list_heroes()
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "heroes": heroes,
            "rooms": ROOMS.list_rooms(base_url=request_base_url(handler)),
            "onboarding": onboarding_payload(heroes),
        },
    )
    return


@get("/api/matches/recent")
def get_matches_recent(ctx: RequestContext) -> None:
    handler = ctx.handler
    query = ctx.query
    try:
        user = authenticated_user_from_request(handler, query=query)
        matches = runtime.MATCH_HISTORY_STORE.list_recent(user.user_id)
    except AuthError as exc:
        auth_error_response(handler, exc)
        return
    json_response(handler, HTTPStatus.OK, {"matches": matches})
    return


@get("/api/progression/overview")
def get_progression_overview(ctx: RequestContext) -> None:
    handler = ctx.handler
    query = ctx.query
    try:
        user = authenticated_user_from_request(handler, query=query)
        progression = runtime.MATCH_HISTORY_STORE.progression_overview(user.user_id)
    except AuthError as exc:
        auth_error_response(handler, exc)
        return
    json_response(handler, HTTPStatus.OK, {"progression": progression})
    return


@get("/api/matches/replay")
def get_matches_replay(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    query = ctx.query
    try:
        user = authenticated_user_from_request(handler, query=query)
        match_id = (query.get("match_id") or [""])[0]
        step_index = (query.get("step_index") or ["-1"])[0]
        payload = historical_replay_payload(user.user_id, match_id, step_index)
    except AuthError as exc:
        auth_error_response(handler, exc)
        return
    except MatchHistoryError as exc:
        json_response(handler, HTTPStatus.NOT_FOUND, {"error": str(exc)})
        return
    json_response(handler, HTTPStatus.OK, payload)
    return


@get("/api/rooms")
def get_rooms(ctx: RequestContext) -> None:
    handler = ctx.handler
    json_response(handler, HTTPStatus.OK, {"rooms": ROOMS.list_rooms(base_url=request_base_url(handler))})
    return


@get("/api/state")
def get_state(ctx: RequestContext) -> None:
    handler = ctx.handler
    json_response(handler, HTTPStatus.OK, SESSION.serialize_state())
    return


@get("/api/rooms/state")
def get_rooms_state(ctx: RequestContext) -> None:
    handler = ctx.handler
    auth_user = ctx.auth_user
    query = ctx.query
    room_id = (query.get("room_id") or query.get("room") or [""])[0]
    player_token = (query.get("player_token") or [""])[0] or None
    auth_user = None
    auth_token = auth_token_from_request(handler, query=query)
    if auth_token:
        try:
            auth_user = runtime.AUTH_STORE.user_for_session(auth_token)
        except AuthError as exc:
            auth_error_response(handler, exc)
            return
    recovered = False
    try:
        room = ROOMS.get_room(room_id)
    except RoomError:
        if auth_user is None:
            json_response(handler, HTTPStatus.NOT_FOUND, {"error": "房间不存在；战略战斗参与者登录后可尝试服务器检查点恢复。"})
            return
        try:
            room, _checkpoint = recover_strategy_battle_room(room_id, auth_user)
            recovered = True
        except StrategyError as exc:
            json_response(
                handler,
                exc.status,
                {
                    "error": str(exc),
                    "battle_recovery": {
                        "status": "restart_required" if exc.status == HTTPStatus.CONFLICT else "unavailable",
                        "can_restart_from_prebattle": exc.status == HTTPStatus.CONFLICT,
                        "room_id": str(room_id or "").strip().upper(),
                    },
                },
            )
            return
        except RoomError as exc:
            json_response(
                handler,
                HTTPStatus.CONFLICT,
                {
                    "error": str(exc),
                    "battle_recovery": {
                        "status": "restart_required",
                        "can_restart_from_prebattle": True,
                        "room_id": str(room_id or "").strip().upper(),
                    },
                },
            )
            return
    if auth_user is not None and not room.seat_for_token(player_token):
        checkpoint = campaign_runtime.STRATEGY_STORE.battle_checkpoint(room.room_id)
        if checkpoint is not None and auth_user.user_id in checkpoint.participant_user_ids:
            campaign_runtime.STRATEGY_STORE.get_campaign_for_user(checkpoint.campaign_id, auth_user.user_id)
            recovered_token = strategy_room_token_for_user(room, auth_user.user_id)
            if recovered_token:
                player_token = recovered_token
                recovered = True
    response = room_state_with_strategy_sync(
        room,
        player_token,
        base_url=request_base_url(handler),
        recovered=recovered,
    )
    if recovered and player_token:
        response["player_token"] = player_token
    json_response(
        handler,
        HTTPStatus.OK,
        response,
    )
    return


@get("/api/rooms/replay")
def get_rooms_replay(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    query = ctx.query
    room_id = (query.get("room_id") or query.get("room") or [""])[0]
    player_token = (query.get("player_token") or [""])[0] or None
    step_index = (query.get("step_index") or ["0"])[0]
    omniscient = (query.get("omniscient") or ["0"])[0] in {"1", "true", "yes", "on"}
    try:
        room = ROOMS.get_room(room_id)
        payload = room.serialize_replay_step(player_token, step_index=step_index, omniscient=omniscient)
    except RoomError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    json_response(handler, HTTPStatus.OK, payload)
    return


@post("/api/new-game")
def post_new_game(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    hero1 = payload.get("player1")
    hero2 = payload.get("player2")
    if not hero1 or not hero2:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "需要同时选择双方武将。"})
        return
    try:
        SESSION.battle = create_battle(str(hero1), str(hero2))
    except KeyError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    json_response(handler, HTTPStatus.OK, SESSION.serialize_state())
    return


@post("/api/action")
def post_action(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    if SESSION.battle is None:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "请先开始对局。"})
        return
    try:
        SESSION.battle.perform_action(payload)
    except ActionError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc), "state": SESSION.serialize_state()})
        return
    json_response(handler, HTTPStatus.OK, SESSION.serialize_state())
    return


@post("/api/rooms/create")
def post_rooms_create(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    assert auth_user is not None
    player_name = payload.get("player_name") or auth_user.username
    room_mode = payload.get("mode", DEFAULT_ROOM_MODE)
    try:
        room, player_id, player_token = ROOMS.create_room(
            str(player_name),
            str(room_mode or DEFAULT_ROOM_MODE),
            account_user_id=auth_user.user_id,
        )
    except RoomError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    response = room.serialize_state(player_token, base_url=request_base_url(handler))
    response["player_token"] = player_token
    response["joined_player_id"] = player_id
    json_response(handler, HTTPStatus.OK, response)
    return


@post("/api/rooms/tutorial-start")
def post_rooms_tutorial_start(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    assert auth_user is not None
    player_name = payload.get("player_name") or auth_user.username
    try:
        room, player_id, player_token = ROOMS.create_room(
            str(player_name),
            "classic",
            account_user_id=auth_user.user_id,
        )
        room.experience_kind = "tutorial"
        room.launch_context = make_launch_context("tutorial")
        room.select_hero(player_token, "fire_funeral", 1, seat_id=1)
        room.set_seat_controller(player_token, 2, "ai")
        room.select_hero(player_token, "ellie", 1, seat_id=2)
        room.start_battle(player_token)
        room.configure_tutorial()
    except RoomError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    response = room.serialize_state(player_token, base_url=request_base_url(handler))
    response["player_token"] = player_token
    response["joined_player_id"] = player_id
    json_response(handler, HTTPStatus.OK, response)
    return


@post("/api/rooms/quick-ai-start")
def post_rooms_quick_ai_start(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    assert auth_user is not None
    player_name = payload.get("player_name") or auth_user.username
    config = quick_ai_match_payload()
    try:
        room, player_id, player_token = ROOMS.create_preconfigured_battle_room(
            host_name=str(player_name),
            opponent_name=str(config["opponent_name"]),
            player1_roster=list(config["player_hero_codes"]),
            player2_roster=list(config["opponent_hero_codes"]),
            start_immediately=True,
ai_difficulty=str(config["ai_difficulty"]),
            host_account_user_id=auth_user.user_id,
        )
        room.experience_kind = "quick_ai"
        room.launch_context = make_launch_context("quick_ai")
        room.resolve_ai_until_human_input()
        room.touch()
    except RoomError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    response = room.serialize_state(player_token, base_url=request_base_url(handler))
    response["player_token"] = player_token
    response["joined_player_id"] = player_id
    response["quick_ai"] = config
    json_response(handler, HTTPStatus.OK, response)
    return


@post("/api/rooms/tutorial-select-unit")
def post_rooms_tutorial_select_unit(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    try:
        room = ROOMS.get_room(str(payload.get("room_id") or ""))
        room.tutorial_select_unit(
            str(payload.get("player_token") or ""),
            str(payload.get("unit_id") or ""),
        )
    except RoomError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room.serialize_state(str(payload.get("player_token") or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/tutorial-retry")
def post_rooms_tutorial_retry(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    try:
        room = ROOMS.get_room(str(payload.get("room_id") or ""))
        room.retry_tutorial_checkpoint(str(payload.get("player_token") or ""))
    except RoomError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room.serialize_state(str(payload.get("player_token") or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/join")
def post_rooms_join(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    assert auth_user is not None
    room_id = payload.get("room_id", "")
    player_name = payload.get("player_name") or auth_user.username
    try:
        room = ROOMS.get_room(str(room_id))
        player_id, player_token = room.join(str(player_name), account_user_id=auth_user.user_id)
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
                base_url=request_base_url(handler),
            )
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    response = room.serialize_state(player_token, base_url=request_base_url(handler))
    response["player_token"] = player_token
    response["joined_player_id"] = player_id
    json_response(handler, HTTPStatus.OK, response)
    return


@post("/api/rooms/select-hero")
def post_rooms_select_hero(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
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
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/apply-recommended-roster")
def post_rooms_apply_recommended_roster(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    seat_id = payload.get("seat_id")
    hero_codes = recommended_roster_hero_codes(str(payload.get("roster_code") or ""))
    if hero_codes is None:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "推荐阵容不存在。"})
        return
    try:
        room = ROOMS.get_room(str(room_id))
        room.set_roster(str(player_token or ""), hero_codes, seat_id=seat_id)
    except RoomError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/set-seat-count")
def post_rooms_set_seat_count(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
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
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/set-seat-team")
def post_rooms_set_seat_team(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
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
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/set-seat-controller")
def post_rooms_set_seat_controller(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
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
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/set-ready")
def post_rooms_set_ready(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    try:
        room = ROOMS.get_room(str(room_id))
        room.set_ready(str(player_token or ""), bool(payload.get("ready", True)))
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload: dict[str, Any] = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/start")
def post_rooms_start(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    try:
        room = ROOMS.get_room(str(room_id))
        room.start_battle(str(player_token or ""), require_confirmation=True)
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/set-mode")
def post_rooms_set_mode(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
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
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/set-default-ai-difficulty")
def post_rooms_set_default_ai_difficulty(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
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
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(handler, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(handler)))
    return


@post("/api/rooms/set-seat-ai-difficulty")
def post_rooms_set_seat_ai_difficulty(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
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
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(handler, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(handler)))
    return


@post("/api/rooms/set-random-roster-size")
def post_rooms_set_random_roster_size(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
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
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(handler, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(handler)))
    return


@post("/api/rooms/set-hero-limit")
def post_rooms_set_hero_limit(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    hero_limit = payload.get("hero_limit", 0)
    try:
        room = ROOMS.get_room(str(room_id))
        room.set_hero_limit(str(player_token or ""), hero_limit)
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/set-turn-timeout")
def post_rooms_set_turn_timeout(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    turn_timeout = payload.get("turn_timeout_seconds", 0)
    try:
        room = ROOMS.get_room(str(room_id))
        room.set_turn_timeout(str(player_token or ""), turn_timeout)
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/set-board-size")
def post_rooms_set_board_size(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    width = payload.get("board_width", payload.get("width", 10))
    height = payload.get("board_height", payload.get("height", 10))
    try:
        room = ROOMS.get_room(str(room_id))
        room.set_board_size(str(player_token or ""), width, height)
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/auto-configure")
def post_rooms_auto_configure(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    try:
        room = ROOMS.get_room(str(room_id))
        room.auto_configure(str(player_token or ""))
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/set-seat-random-quota")
def post_rooms_set_seat_random_quota(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
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
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(handler, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(handler)))
    return


@post("/api/rooms/rematch")
def post_rooms_rematch(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    try:
        room = ROOMS.get_room(str(room_id))
        assert auth_user is not None
        require_strategy_room_mutation_allowed(room, auth_user, allow_battleplay=False)
        record_finished_room_history(room)
        room.restart_lobby(str(player_token or ""))
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(handler, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(handler)))
    return


@post("/api/rooms/simulation-control")
def post_rooms_simulation_control(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    action = payload.get("action", "")
    speed = payload.get("speed")
    try:
        room = ROOMS.get_room(str(room_id))
        assert auth_user is not None
        require_strategy_room_mutation_allowed(room, auth_user)
        room.control_simulation(str(player_token or ""), str(action or ""), speed=speed)
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/delete")
def post_rooms_delete(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    room_id = payload.get("room_id", "")
    player_token = str(payload.get("player_token") or "")
    try:
        room = ROOMS.get_room(str(room_id))
        assert auth_user is not None
        require_strategy_room_mutation_allowed(room, auth_user, allow_battleplay=False)
        ROOMS.delete_room(str(room_id), player_token)
    except RoomError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "deleted_room_id": str(room_id).strip().upper(),
            "rooms": ROOMS.list_rooms(base_url=request_base_url(handler)),
        },
    )
    return


@post("/api/rooms/leave")
def post_rooms_leave(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    room_id = payload.get("room_id", "")
    player_token = str(payload.get("player_token") or "")
    try:
        room = ROOMS.get_room(str(room_id))
        assert auth_user is not None
        require_strategy_room_mutation_allowed(room, auth_user, allow_battleplay=False)
        deleted, leaving_player_id = ROOMS.leave_room(str(room_id), player_token)
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload: dict[str, Any] = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(None, base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    response_payload = {
        "left_room_id": str(room_id).strip().upper(),
        "left_player_id": leaving_player_id,
        "room_deleted": deleted,
        "rooms": ROOMS.list_rooms(base_url=request_base_url(handler)),
    }
    json_response(handler, HTTPStatus.OK, response_payload)
    return


@post("/api/rooms/surrender")
def post_rooms_surrender(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    try:
        room = ROOMS.get_room(str(room_id))
        assert auth_user is not None
        require_strategy_room_mutation_allowed(room, auth_user)
        room.surrender(str(player_token or ""))
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return


@post("/api/rooms/action")
def post_rooms_action(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    room_id = payload.get("room_id", "")
    player_token = payload.get("player_token")
    action_payload = extract_room_action(payload)
    try:
        room = ROOMS.get_room(str(room_id))
        assert auth_user is not None
        require_strategy_room_mutation_allowed(room, auth_user)
        room.perform_action(str(player_token or ""), action_payload)
    except RoomError as exc:
        room = None
        try:
            room = ROOMS.get_room(str(room_id))
        except RoomError:
            pass
        error_payload = {"error": str(exc)}
        if room is not None:
            error_payload["state"] = room.serialize_state(str(player_token or ""), base_url=request_base_url(handler))
        json_response(handler, HTTPStatus.BAD_REQUEST, error_payload)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        room_state_with_strategy_sync(room, str(player_token or ""), base_url=request_base_url(handler)),
    )
    return
