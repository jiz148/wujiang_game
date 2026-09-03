"""Campaign endpoints: creation, membership, monthly orders and battles."""
from __future__ import annotations

from http import HTTPStatus
import hashlib

from wujiang.bridge.battle_bridge import create_strategy_battle_room
from wujiang.bridge.battle_bridge import declare_strategy_attack_for_world
from wujiang.bridge.battle_bridge import declare_strategy_engagement_for_world
from wujiang.bridge.battle_bridge import resolve_strategy_battle_player_choice
from wujiang.bridge.battle_bridge import persist_created_strategy_battle_rooms
from wujiang.bridge.battle_bridge import persist_strategy_battle_room
from wujiang.bridge.battle_bridge import start_world_crisis_showdown_for_world
from wujiang.bridge.battle_bridge import strategy_battle_for_room
from wujiang.platform.auth import AuthError
from wujiang.platform.http.runtime import auth_error_response
from wujiang.platform.http.runtime import authenticated_user_from_request
from wujiang.platform.http.runtime import json_response
from wujiang.strategic import StrategyError
from wujiang.strategic import advance_month
from wujiang.strategic import apply_quick_campaign_opening_choice
from wujiang.strategic import apply_strategy_ai_monthly_actions
from wujiang.strategic import apply_strategy_ai_showdown_action
from wujiang.strategic import campaign_variant_catalog_public
from wujiang.strategic import choose_player_hero_path
from wujiang.strategic import continue_campaign_as_sandbox
from wujiang.strategic import first_campaign_contract
from wujiang.strategic import true_campaign_contract
from wujiang.strategic import world_catalog_public
from wujiang.strategic.world_crisis import apply_campaign_play_settings
from wujiang.strategic import normalize_strategic_hero_deployment
from wujiang.strategic import quick_campaign_contract
from wujiang.strategic import quick_campaign_opening_status
from wujiang.strategic import require_campaign_orders_open
from wujiang.strategic import set_battle_defender_hero
from wujiang.strategic import set_city_policy
from wujiang.strategic import set_strategic_defender_hero
from wujiang.strategic import unlock_tactic_tech
from wujiang.strategic.campaign_runtime import record_strategy_snapshot_safe
from wujiang.strategic.campaign_runtime import strategy_error_response
from wujiang.strategic.command import faction_command_points
from wujiang.strategic.command import strategy_action_command_cost
from wujiang.strategic.service import apply_strategy_action_queue
from wujiang.strategic.service import campaign_member_faction_id
from wujiang.strategic.service import enforce_city_order_limit
from wujiang.strategic.service import enforce_faction_command_points
from wujiang.strategic.service import normalize_strategy_action_payload
from wujiang.strategic.service import require_campaign_owner
from wujiang.strategic.service import require_strategy_action_office
from wujiang.strategic.service import strategy_defender_hero_codes_from_payload
from wujiang.strategic.service import strategy_hero_codes_from_payload
from wujiang.tactical.rooms.multiplayer import GameRoom
from wujiang.tactical.rooms.multiplayer import ROOMS
from wujiang.tactical.rooms.multiplayer import RoomError
from wujiang.platform.http.context import RequestContext
from wujiang.platform.http.routing import get, post
from wujiang.platform.http import runtime
from wujiang.strategic import campaign_runtime


def _public_campaign(campaign, user, resume_status=None):
    if resume_status is None:
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign.campaign_id)
    return campaign.to_public_dict(
        resume_status=resume_status,
        viewer_user_id=int(getattr(user, "user_id", 0) or 0) or None,
    )


@get("/api/strategy/campaigns")
def get_strategy_campaigns(ctx: RequestContext) -> None:
    handler = ctx.handler
    query = ctx.query
    try:
        user = authenticated_user_from_request(handler, query=query)
        campaigns = campaign_runtime.STRATEGY_STORE.list_campaigns_for_user(user.user_id)
        focus_id = int(ctx.query_value("focus_id") or 0)
        summary_only = ctx.query_value("summary") == "1"
    except AuthError as exc:
        auth_error_response(handler, exc)
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "focus_id 必须是整数。"})
        return
    listed = []
    for campaign in campaigns:
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign.campaign_id)
        if summary_only or (focus_id and campaign.campaign_id != focus_id):
            listed.append(campaign.to_list_dict(resume_status=resume_status))
            continue
        listed.append(_public_campaign(campaign, user, resume_status))
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "campaign_variants": campaign_variant_catalog_public(),
            "world_catalog": world_catalog_public(),
            "campaigns": listed,
        },
    )
    return


@post("/api/strategy/campaigns/create")
def post_strategy_campaigns_create(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        mode = str(payload.get("mode") or "random_campaign").strip() or "random_campaign"
        if mode == "true_campaign":
            campaign_contract = true_campaign_contract(
                payload.get("scenario_id"),
                variant_id=str(payload.get("variant_id") or "classic_frontier"),
            )
        else:
            campaign_contract = first_campaign_contract(
                str(payload.get("variant_id") or "classic_frontier"),
                major_faction_count=int(payload.get("major_faction_count") or 2),
            )
        if "crisis_earliest_year" in payload or "year_limit" in payload:
            campaign_contract = apply_campaign_play_settings(
                campaign_contract,
                crisis_earliest_year=int(payload.get("crisis_earliest_year", 10) or 10),
                year_limit=int(payload.get("year_limit", 0) or 0),
            )
        campaign = campaign_runtime.STRATEGY_STORE.create_campaign(
            owner=auth_user,
            name=str(payload.get("name") or "新战役"),
            seed=int(payload.get("seed", 1)),
            neutral_city_states=True,
            campaign_contract=campaign_contract,
        )
        resume_status = campaign_runtime.STRATEGY_STORE.mark_online(campaign.campaign_id, auth_user)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign.campaign_id, auth_user.user_id)
        record_strategy_snapshot_safe(campaign, checkpoint="created")
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役参数格式不正确。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/quick-start")
def post_strategy_campaigns_quick_start(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign = campaign_runtime.STRATEGY_STORE.create_campaign(
            owner=auth_user,
            name=str(payload.get("name") or "六个月边境决断"),
            seed=int(payload.get("seed", 1)),
            neutral_city_states=True,
            campaign_contract=quick_campaign_contract(),
        )
        record_strategy_snapshot_safe(campaign, checkpoint="created")
        campaign = campaign_runtime.STRATEGY_STORE.lock_initial_players(campaign.campaign_id, auth_user.user_id)
        from wujiang.strategic.ai_goals import ensure_ai_strategic_goal

        for member in campaign.members:
            if str(getattr(member, "role", "")).lower() == "ai":
                ensure_ai_strategic_goal(campaign.world, member.faction_id)
        campaign = campaign_runtime.STRATEGY_STORE.update_world(
            campaign.campaign_id,
            auth_user.user_id,
            campaign.world,
        )
        resume_status = campaign_runtime.STRATEGY_STORE.mark_online(campaign.campaign_id, auth_user)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign.campaign_id, auth_user.user_id)
        record_strategy_snapshot_safe(campaign, checkpoint="locked")
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役参数格式不正确。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/join")
def post_strategy_campaigns_join(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign = campaign_runtime.STRATEGY_STORE.join_campaign_by_code(
            str(payload.get("join_code") or ""),
            auth_user,
            join_host_faction=bool(payload.get("join_host_faction", False)),
        )
        resume_status = campaign_runtime.STRATEGY_STORE.mark_online(campaign.campaign_id, auth_user)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign.campaign_id, auth_user.user_id)
        record_strategy_snapshot_safe(campaign, checkpoint="roster")
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/lock")
def post_strategy_campaigns_lock(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.lock_initial_players(campaign_id, auth_user.user_id)
        from wujiang.strategic.ai_goals import ensure_ai_strategic_goal

        for member in campaign.members:
            if str(getattr(member, "role", "")).lower() == "ai":
                ensure_ai_strategic_goal(campaign.world, member.faction_id)
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, campaign.world)
        resume_status = campaign_runtime.STRATEGY_STORE.mark_online(campaign.campaign_id, auth_user)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign.campaign_id, auth_user.user_id)
        record_strategy_snapshot_safe(campaign, checkpoint="locked")
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "æˆ˜å½¹ ID å¿…é¡»æ˜¯æ•´æ•°ã€‚"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/rotate-join-code")
def post_strategy_campaigns_rotate_join_code(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.rotate_join_code(campaign_id, auth_user.user_id)
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign.campaign_id)
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/revoke-join-code")
def post_strategy_campaigns_revoke_join_code(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.revoke_join_code(campaign_id, auth_user.user_id)
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign.campaign_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/enter")
def post_strategy_campaigns_enter(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        resume_status = campaign_runtime.STRATEGY_STORE.mark_online(campaign_id, auth_user)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/leave")
def post_strategy_campaigns_leave(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        resume_status = campaign_runtime.STRATEGY_STORE.mark_offline(campaign_id, auth_user.user_id)
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(handler, HTTPStatus.OK, {"resume": resume_status.to_dict()})
    return


@post("/api/strategy/campaigns/delete")
def post_strategy_campaigns_delete(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign_runtime.STRATEGY_STORE.delete_campaign(campaign_id, auth_user.user_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(handler, HTTPStatus.OK, {"deleted": True, "campaign_id": campaign_id})
    return


@post("/api/strategy/campaigns/resume")
def post_strategy_campaigns_resume(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/choose-hero-path")
def post_strategy_campaigns_choose_hero_path(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        if campaign.status == "active":
            require_campaign_orders_open(campaign.world)
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
            faction_name=str(payload.get("faction_name") or ""),
            allow_reselect=campaign.status == "lobby",
        )
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign_id)
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/quick-opening-choice")
def post_strategy_campaigns_quick_opening_choice(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        if campaign.status != "active":
            raise StrategyError("快速战役必须开始后才能选择开局国策。", status=HTTPStatus.CONFLICT)
        require_campaign_orders_open(campaign.world)
        faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
        next_world = apply_quick_campaign_opening_choice(
            campaign.world,
            faction_id=faction_id,
            choice_id=str(payload.get("choice_id") or ""),
        )
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/guide-action")
def post_strategy_campaigns_guide_action(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
        from wujiang.strategic.campaign_tutorial import update_campaign_tutorial

        next_world = update_campaign_tutorial(
            campaign.world,
            faction_id=faction_id,
            action=str(payload.get("action") or ""),
        )
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/month-ready")
def post_strategy_campaigns_month_ready(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        resume_status = campaign_runtime.STRATEGY_STORE.set_month_ready(
            campaign_id,
            auth_user.user_id,
            ready=bool(payload.get("ready", True)),
        )
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/office-change/request")
def post_strategy_campaigns_office_change_request(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.request_office_change(
            campaign_id,
            auth_user.user_id,
            request_type=str(payload.get("request_type") or ""),
            office_id=str(payload.get("office_id") or ""),
            target_user_id=int(payload.get("target_user_id") or 0),
        )
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "官职请求参数格式不正确。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/office-change/respond")
def post_strategy_campaigns_office_change_respond(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.respond_office_change(
            campaign_id,
            auth_user.user_id,
            request_id=int(payload.get("request_id")),
            accept=bool(payload.get("accept", False)),
        )
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "官职确认参数格式不正确。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/office-takeover/grant")
def post_strategy_campaigns_office_takeover_grant(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.grant_office_takeover(
            campaign_id,
            auth_user.user_id,
            office_id=str(payload.get("office_id") or ""),
            delegate_user_id=int(payload.get("delegate_user_id")),
        )
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "临时代管参数格式不正确。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(handler, HTTPStatus.OK, {"campaign": _public_campaign(campaign, auth_user, resume_status)})
    return


@post("/api/strategy/campaigns/office-takeover/revoke")
def post_strategy_campaigns_office_takeover_revoke(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.revoke_office_takeover(
            campaign_id,
            auth_user.user_id,
            takeover_id=int(payload.get("takeover_id")),
        )
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "结束代管参数格式不正确。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(handler, HTTPStatus.OK, {"campaign": _public_campaign(campaign, auth_user, resume_status)})
    return


@post("/api/strategy/campaigns/close-month-deadline")
def post_strategy_campaigns_close_month_deadline(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        resume_status = campaign_runtime.STRATEGY_STORE.close_month_deadline(
            campaign_id,
            auth_user.user_id,
        )
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/advance-month")
def post_strategy_campaigns_advance_month(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        try:
            campaign_id = int(payload.get("campaign_id"))
        except (TypeError, ValueError):
            json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
            return
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_owner(campaign, auth_user.user_id)
        require_campaign_orders_open(campaign.world)
        quick_opening = quick_campaign_opening_status(
            campaign.world,
            campaign_member_faction_id(campaign, auth_user.user_id),
        )
        if quick_opening is not None and quick_opening["available"]:
            raise StrategyError("请先完成三选一开局国策，再推进到下一个月。")
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_advance_month(
            campaign_id,
            auth_user.user_id,
        )
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        action_month = campaign.world.current_month
        previous_world = campaign.world
        command_remaining_by_faction = {
            faction.faction_id: faction_command_points(faction.faction_id, campaign.queued_actions)["remaining"]
            for faction in campaign.world.factions
        }
        next_world, battle_rooms = apply_strategy_action_queue(campaign)
        controlled_faction_ids = {
            member.faction_id
            for member in campaign.members
            if str(getattr(member, "role", "")).lower() != "ai" and int(member.user_id) > 0
        }
        controlled_faction_ids.difference_update(
            campaign_runtime.STRATEGY_STORE.temporary_ai_faction_ids(campaign_id, action_month)
        )
        from wujiang.strategic.office_automation import apply_player_office_automation

        next_world = apply_player_office_automation(
            next_world,
            controlled_faction_ids=controlled_faction_ids,
            queued_actions=campaign.queued_actions,
            command_remaining_by_faction=command_remaining_by_faction,
        )
        next_world = apply_strategy_ai_monthly_actions(
            next_world,
            controlled_faction_ids=controlled_faction_ids,
            command_remaining_by_faction=command_remaining_by_faction,
        )
        next_world = advance_month(next_world)
        next_world = apply_strategy_ai_showdown_action(
            next_world,
            controlled_faction_ids=controlled_faction_ids,
        )
        from wujiang.strategic.monthly_cycle import record_monthly_report

        next_world = record_monthly_report(
            previous_world,
            next_world,
            resolved_actions=campaign.queued_actions,
        )
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
        campaign_runtime.STRATEGY_STORE.mark_queued_actions_resolved(campaign_id, auth_user.user_id, action_month)
        campaign = campaign_runtime.STRATEGY_STORE.expire_office_takeovers(campaign_id, auth_user.user_id)
        persist_created_strategy_battle_rooms(campaign, battle_rooms)
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign_id)
        record_strategy_snapshot_safe(campaign, checkpoint="month")
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "campaign": _public_campaign(campaign, auth_user, resume_status),
            **({"battle_rooms": battle_rooms} if battle_rooms else {}),
        },
    )
    return


@post("/api/strategy/campaigns/continue-sandbox")
def post_strategy_campaigns_continue_sandbox(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_owner(campaign, auth_user.user_id)
        next_world = continue_campaign_as_sandbox(campaign.world)
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
        record_strategy_snapshot_safe(campaign, checkpoint="sandbox")
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/archive")
def post_strategy_campaigns_archive(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_owner(campaign, auth_user.user_id)
        from wujiang.strategic.campaign_retrospective import archive_campaign

        next_world = archive_campaign(campaign.world)
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
        resume_status = campaign_runtime.STRATEGY_STORE.resume_status(campaign_id)
        record_strategy_snapshot_safe(campaign, checkpoint="archived")
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/queue-action")
def post_strategy_campaigns_queue_action(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_orders_open(campaign.world)
        action_type, action_key, action_payload = normalize_strategy_action_payload(
            campaign,
            auth_user.user_id,
            str(payload.get("action_type") or ""),
            payload.get("action_payload") or payload.get("payload") or {},
        )
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
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
        previous_action = next(
            (
                action
                for action in campaign.queued_actions
                if action.user_id == auth_user.user_id
                and action.month == campaign.world.current_month
                and action.action_type == action_type
                and action.action_key == action_key
            ),
            None,
        )
        campaign = campaign_runtime.STRATEGY_STORE.queue_action(
            campaign_id=campaign_id,
            user=auth_user,
            action_type=action_type,
            action_key=action_key,
            payload=action_payload,
        )
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "æˆ˜å½¹ ID å¿…é¡»æ˜¯æ•´æ•°ã€‚"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "campaign": _public_campaign(campaign, auth_user, resume_status),
            "submission": {
                "replaced": previous_action is not None,
                "previous_action": previous_action.to_dict() if previous_action is not None else None,
                "command_points": faction_command_points(
                    campaign_member_faction_id(campaign, auth_user.user_id),
                    campaign.queued_actions,
                ),
                "resource_balance": next(
                    faction.resources.to_dict()
                    for faction in campaign.world.factions
                    if faction.faction_id == campaign_member_faction_id(campaign, auth_user.user_id)
                ),
                "affected_months": [campaign.world.current_month, campaign.world.current_month + 1],
                **(
                    {
                        "execution": {
                            "issuer_office_id": str(action_payload.get("issuer_office_id") or ""),
                            "executor_office_id": str(action_payload.get("receiver_office_id") or ""),
                            "command_cost": strategy_action_command_cost(action_type, action_payload),
                            "expected_completion_month": campaign.world.current_month + 1,
                            "result_summary": "推进月份时送达接收职位并生成执行回执。",
                        }
                    }
                    if action_type in {"issue_office_order", "send_office_request"}
                    else {}
                ),
            },
        },
    )
    return


@post("/api/strategy/campaigns/cancel-action")
def post_strategy_campaigns_cancel_action(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        action_id = int(payload.get("action_id") or payload.get("id") or 0)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_orders_open(campaign.world)
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.cancel_queued_action(
            campaign_id=campaign_id,
            user=auth_user,
            action_id=action_id,
        )
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 和军令 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "campaign": _public_campaign(campaign, auth_user, resume_status),
            "command_points": faction_command_points(
                campaign_member_faction_id(campaign, auth_user.user_id),
                campaign.queued_actions,
            ),
        },
    )
    return


@post("/api/strategy/campaigns/set-city-policy")
def post_strategy_campaigns_set_city_policy(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        city_id = str(payload.get("city_id") or "")
        policy = str(payload.get("policy") or "")
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_orders_open(campaign.world)
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
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/set-defense-hero")
def post_strategy_campaigns_set_defense_hero(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        hero_code = str(payload.get("hero_code") or "")
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_orders_open(campaign.world)
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
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/set-battle-defense-hero")
def post_strategy_campaigns_set_battle_defense_hero(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        battle_id = str(payload.get("battle_id") or "")
        hero_codes = strategy_defender_hero_codes_from_payload(payload)
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_orders_open(campaign.world)
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
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "æˆ˜å½¹ ID å¿…é¡»æ˜¯æ•´æ•°ã€‚"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/unlock-tactic-tech")
def post_strategy_campaigns_unlock_tactic_tech(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        tech_id = str(payload.get("tech_id") or "")
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_orders_open(campaign.world)
        faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
        require_strategy_action_office(
            campaign,
            user_id=auth_user.user_id,
            action_type="unlock_tactic_tech",
            payload={**payload, "tech_id": tech_id},
        )
        next_world = unlock_tactic_tech(campaign.world, faction_id=faction_id, tech_id=tech_id)
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {"campaign": _public_campaign(campaign, auth_user, resume_status)},
    )
    return


@post("/api/strategy/campaigns/resolve-world-crisis-showdown")
def post_strategy_campaigns_resolve_world_crisis_showdown(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        resolution_mode = str(payload.get("resolution_mode") or "quick").strip()
        issuer_office_id = str(payload.get("issuer_office_id") or "").strip()
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(
            campaign_id, auth_user.user_id
        )
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(
            campaign_id, auth_user.user_id
        )
        require_campaign_orders_open(campaign.world)
        faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
        next_world, battle_room = start_world_crisis_showdown_for_world(
            campaign,
            campaign.world,
            auth_user,
            faction_id=faction_id,
            issuer_office_id=issuer_office_id,
            resolution_mode=resolution_mode,
        )
        campaign = campaign_runtime.STRATEGY_STORE.update_world(
            campaign_id, auth_user.user_id, next_world
        )
        if battle_room is not None:
            persist_created_strategy_battle_rooms(campaign, [battle_room])
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "campaign": _public_campaign(campaign, auth_user, resume_status),
            **({"battle_room": battle_room} if battle_room is not None else {}),
        },
    )
    return


@post("/api/strategy/campaigns/resolve-strategic-battle")
def post_strategy_campaigns_resolve_strategic_battle(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        source_kind = str(payload.get("source_kind") or "").strip()
        source_entity_id = str(payload.get("source_entity_id") or "").strip()
        resolution_mode = str(payload.get("resolution_mode") or "quick").strip()
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_orders_open(campaign.world)
        faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
        issuer_office_id = str(payload.get("issuer_office_id") or "")
        issuer = next(
            (
                office for office in campaign.world.offices
                if office.office_id == issuer_office_id
                and office.faction_id == faction_id
                and office.status == "active"
            ),
            None,
        )
        if source_kind == "encounter":
            encounter = next(
                (item for item in campaign.world.encounters if item.encounter_id == source_entity_id),
                None,
            )
            if encounter is None or faction_id not in encounter.faction_army_ids:
                raise StrategyError("只能处理己方参与的遭遇战。", status=HTTPStatus.FORBIDDEN)
            commander_ids = {
                army.commander_office_id for army in campaign.world.armies
                if army.army_id in encounter.faction_army_ids[faction_id]
            }
            if issuer is None or issuer.office_id not in commander_ids:
                raise StrategyError("只有参战军队的玩家将军可以处理遭遇战。", status=HTTPStatus.FORBIDDEN)
        elif source_kind == "siege":
            siege = next((item for item in campaign.world.sieges if item.siege_id == source_entity_id), None)
            if siege is None or faction_id not in {siege.attacker_faction_id, siege.defender_faction_id}:
                raise StrategyError("只能处理己方参与的围城战。", status=HTTPStatus.FORBIDDEN)
            if faction_id == siege.attacker_faction_id:
                commander_ids = {
                    army.commander_office_id for army in campaign.world.armies
                    if army.army_id in siege.attacker_army_ids
                }
                allowed = issuer is not None and issuer.office_id in commander_ids
            else:
                allowed = (
                    issuer is not None
                    and issuer.office_type == "governor"
                    and siege.city_id in issuer.managed_entity_ids
                )
            if not allowed:
                raise StrategyError("只有参战将军或守城城主可以处理围城战。", status=HTTPStatus.FORBIDDEN)
        else:
            raise StrategyError("战略战斗来源无效。")
        next_world, battle_room = declare_strategy_engagement_for_world(
            campaign,
            campaign.world,
            auth_user,
            faction_id=faction_id,
            source_kind=source_kind,
            source_entity_id=source_entity_id,
            resolution_mode=resolution_mode,
        )
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
        if battle_room is not None:
            persist_created_strategy_battle_rooms(campaign, [battle_room])
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "campaign": _public_campaign(campaign, auth_user, resume_status),
            **({"battle_room": battle_room} if battle_room is not None else {}),
        },
    )
    return


@post("/api/strategy/campaigns/resolve-battle-choice")
def post_strategy_campaigns_resolve_battle_choice(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        battle_id = str(payload.get("battle_id") or "").strip()
        choice = str(payload.get("choice") or "").strip()
        composition = payload.get("composition") if isinstance(payload.get("composition"), dict) else {}
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        faction_id = campaign_member_faction_id(campaign, auth_user.user_id)
        next_world, battle_room = resolve_strategy_battle_player_choice(
            campaign,
            campaign.world,
            auth_user,
            faction_id=faction_id,
            battle_id=battle_id,
            choice=choice,
            composition=composition,
        )
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
        if battle_room is not None:
            persist_created_strategy_battle_rooms(campaign, [battle_room])
    except (TypeError, ValueError):
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "campaign": _public_campaign(campaign, auth_user, resume_status),
            **({"battle_room": battle_room} if battle_room is not None else {}),
        },
    )
    return


@post("/api/strategy/campaigns/declare-attack")
def post_strategy_campaigns_declare_attack(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        campaign_id = int(payload.get("campaign_id"))
        source_city_id = str(payload.get("source_city_id") or "")
        target_city_id = str(payload.get("target_city_id") or "")
        resolution_mode = str(payload.get("resolution_mode") or "quick")
        resume_status = campaign_runtime.STRATEGY_STORE.require_can_resume(campaign_id, auth_user.user_id)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(campaign_id, auth_user.user_id)
        require_campaign_orders_open(campaign.world)
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
        committed_raw = payload.get("committed_troops", payload.get("attacker_troops"))
        committed_troops = int(committed_raw) if committed_raw not in {None, ""} else None
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
            committed_troops=committed_troops,
        )
        campaign = campaign_runtime.STRATEGY_STORE.update_world(campaign_id, auth_user.user_id, next_world)
        if battle_room is not None:
            persist_created_strategy_battle_rooms(campaign, [battle_room])
    except (TypeError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"error": "战役 ID 必须是整数。"})
        return
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "campaign": _public_campaign(campaign, auth_user, resume_status),
            **({"battle_room": battle_room} if battle_room is not None else {}),
        },
    )
    return


@post("/api/strategy/campaigns/restart-battle-from-snapshot")
def post_strategy_campaigns_restart_battle_from_snapshot(ctx: RequestContext) -> None:
    handler = ctx.handler
    payload = ctx.payload
    auth_user = ctx.auth_user
    try:
        assert auth_user is not None
        room_id = str(payload.get("room_id") or "").strip().upper()
        checkpoint = campaign_runtime.STRATEGY_STORE.battle_checkpoint(room_id)
        if checkpoint is None:
            raise StrategyError("没有可重开的战略战斗记录。", status=HTTPStatus.NOT_FOUND)
        campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(checkpoint.campaign_id, auth_user.user_id)
        if auth_user.user_id not in checkpoint.participant_user_ids:
            raise StrategyError("只有原战略战斗参与者可以安全重开。", status=HTTPStatus.FORBIDDEN)
        battle = strategy_battle_for_room(campaign, room_id)
        if battle is None or battle.battle_id != checkpoint.battle_id or battle.status != "pending":
            raise StrategyError("这场战略战斗已经结算或不再可重开。", status=HTTPStatus.CONFLICT)
        checkpoint_healthy = (
            hashlib.sha256(checkpoint.room_blob).hexdigest() == checkpoint.checkpoint_hash
        )
        if checkpoint_healthy:
            try:
                restored = GameRoom.from_checkpoint_bytes(checkpoint.room_blob)
                checkpoint_healthy = restored.room_id == room_id
            except RoomError:
                checkpoint_healthy = False
        if checkpoint_healthy:
            raise StrategyError("服务器检查点仍可正常恢复，无需从战前快照重开。", status=HTTPStatus.CONFLICT)
        ROOMS.discard_room(room_id)
        battle_room = create_strategy_battle_room(
            campaign,
            auth_user,
            battle,
            battle.resolution_mode,
            room_id=room_id,
        )
        room = ROOMS.get_room(room_id)
        saved = persist_strategy_battle_room(
            room,
            campaign_id=campaign.campaign_id,
            battle_id=battle.battle_id,
            restarted=True,
        )
        battle_room["recovery"] = {
            "status": "restarted_from_prebattle",
            "restart_count": saved.restart_count if saved is not None else checkpoint.restart_count + 1,
            "message": "检查点不可恢复，已从战前不可变快照安全重开；未重复扣除战略成本，也未写入胜负。",
        }
    except StrategyError as exc:
        strategy_error_response(handler, exc)
        return
    except RoomError as exc:
        json_response(handler, HTTPStatus.CONFLICT, {"error": str(exc)})
        return
    json_response(
        handler,
        HTTPStatus.OK,
        {
            "campaign": _public_campaign(campaign, auth_user),
            "battle_room": battle_room,
        },
    )
    return
