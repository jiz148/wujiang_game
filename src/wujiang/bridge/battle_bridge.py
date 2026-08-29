"""The one and only seam between the strategic and tactical domains.

Everything that turns a campaign conflict into a grid battle room, and
everything that writes a finished battle back into campaign state, lives
here. Neither domain imports the other directly.
"""
from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace
from typing import Any

from wujiang.platform.auth import AuthUser
from wujiang.strategic import StrategyError
from wujiang.strategic import attach_battle_room
from wujiang.strategic import declare_city_attack
from wujiang.strategic import declare_strategic_battle
from wujiang.strategic import resolve_battle_room_result as resolve_strategy_battle_room_result
from wujiang.strategic import set_world_crisis_showdown_resolution
from wujiang.strategic import strategy_battle_rosters
from wujiang.strategic.campaign_runtime import strategy_city_name
from wujiang.tactical.rooms.history import record_finished_room_history
from wujiang.tactical.rooms.multiplayer import GameRoom
from wujiang.tactical.rooms.multiplayer import ROOMS
from wujiang.tactical.rooms.multiplayer import RoomError
from wujiang.platform.http import runtime
from wujiang.strategic import campaign_runtime

def create_strategy_battle_room(
    campaign,
    auth_user,
    battle,
    resolution_mode: str,
    *,
    room_id: str | None = None,
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
        host_account_user_id=auth_user.user_id,
        room_id=room_id,
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


def declare_strategy_engagement_for_world(
    campaign,
    world,
    auth_user,
    *,
    faction_id: str,
    source_kind: str,
    source_entity_id: str,
    resolution_mode: str,
) -> tuple[Any, dict[str, Any] | None]:
    next_world = declare_strategic_battle(
        world,
        faction_id=faction_id,
        source_kind=source_kind,
        source_entity_id=source_entity_id,
        resolution_mode=resolution_mode,
        auto_resolve=resolution_mode == "quick",
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


def start_world_crisis_showdown_for_world(
    campaign,
    world,
    auth_user,
    *,
    faction_id: str,
    issuer_office_id: str,
    resolution_mode: str,
) -> tuple[Any, dict[str, Any] | None]:
    next_world = set_world_crisis_showdown_resolution(
        world,
        faction_id=faction_id,
        issuer_office_id=issuer_office_id,
        resolution_mode=resolution_mode,
        auto_resolve=resolution_mode == "quick",
    )
    if resolution_mode not in {"manual", "watch_ai", "ai_auto"}:
        return next_world, None
    pending_battle = next(
        (
            battle
            for battle in next_world.pending_battles
            if battle.source_kind == "world_crisis" and battle.status == "pending"
        ),
        None,
    )
    if pending_battle is None:
        raise StrategyError("北境决战待处理记录不存在。", status=HTTPStatus.CONFLICT)
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


def strategy_battle_for_room(campaign, room_id: str):
    normalized = str(room_id or "").strip().upper()
    return next(
        (
            battle for battle in campaign.world.pending_battles
            if str(battle.battle_room_id or "").strip().upper() == normalized
        ),
        None,
    )


def strategy_room_participant_user_ids(room) -> tuple[int, ...]:
    return tuple(sorted({
        int(seat.account_user_id)
        for seat in room.seats.values()
        if seat.account_user_id is not None and int(seat.account_user_id) > 0
    }))


def persist_strategy_battle_room(
    room,
    *,
    campaign_id: int | None = None,
    battle_id: str | None = None,
    restarted: bool = False,
):
    checkpoint = campaign_runtime.STRATEGY_STORE.battle_checkpoint(room.room_id)
    resolved_campaign_id = int(campaign_id) if campaign_id is not None else (
        checkpoint.campaign_id if checkpoint is not None else None
    )
    resolved_battle_id = str(battle_id or (checkpoint.battle_id if checkpoint is not None else ""))
    if resolved_campaign_id is None or not resolved_battle_id:
        return None
    participants = strategy_room_participant_user_ids(room)
    if not participants and checkpoint is not None:
        participants = checkpoint.participant_user_ids
    return campaign_runtime.STRATEGY_STORE.save_battle_checkpoint(
        campaign_id=resolved_campaign_id,
        battle_id=resolved_battle_id,
        room_id=room.room_id,
        participant_user_ids=participants,
        room_blob=room.checkpoint_bytes(),
        room_version=room.version,
        status=room.status,
        restarted=restarted,
    )


def persist_created_strategy_battle_rooms(campaign, battle_rooms: list[dict[str, Any]]) -> None:
    for battle_room in battle_rooms:
        room_id = str(battle_room.get("room_id") or "").strip().upper()
        battle = strategy_battle_for_room(campaign, room_id)
        if battle is None:
            continue
        room = ROOMS.get_room(room_id)
        persist_strategy_battle_room(
            room,
            campaign_id=campaign.campaign_id,
            battle_id=battle.battle_id,
        )


def recover_strategy_battle_room(room_id: str, auth_user: AuthUser):
    checkpoint = campaign_runtime.STRATEGY_STORE.battle_checkpoint_for_user(room_id, auth_user.user_id)
    room = GameRoom.from_checkpoint_bytes(checkpoint.room_blob)
    if room.room_id != checkpoint.room_id:
        raise StrategyError("战略战斗检查点与房间编号不匹配，不能静默恢复。", status=HTTPStatus.CONFLICT)
    ROOMS.restore_room(room)
    return room, checkpoint


def strategy_room_token_for_user(room, user_id: int) -> str:
    seat = next(
        (
            seat for seat in room.seats.values()
            if seat.account_user_id is not None and int(seat.account_user_id) == int(user_id)
        ),
        None,
    )
    return str(seat.token or "") if seat is not None and seat.is_human else ""


def require_strategy_room_mutation_allowed(
    room,
    auth_user: AuthUser,
    *,
    allow_battleplay: bool = True,
) -> None:
    checkpoint = campaign_runtime.STRATEGY_STORE.battle_checkpoint(room.room_id)
    if checkpoint is None:
        return
    campaign = campaign_runtime.STRATEGY_STORE.get_campaign_for_user(checkpoint.campaign_id, auth_user.user_id)
    battle = strategy_battle_for_room(campaign, room.room_id)
    if not allow_battleplay:
        raise RoomError("战略战斗房间由战役保存，不能离开、删除或用于再战。")
    if campaign.status == "archived" or str(campaign.world.campaign_conclusion.get("state") or "") == "archived":
        raise RoomError("这场战略战役已经归档，战斗只能只读查看。")
    if battle is None or battle.status != "pending":
        raise RoomError("这场战略战斗已经结算，只能查看，不能再次操作或重开。")


def sync_finished_strategy_battle_room(room) -> dict[str, Any] | None:
    battle = getattr(room, "battle", None)
    winner_team_id = getattr(battle, "winner", None)
    if winner_team_id not in {1, 2}:
        return None
    campaign = campaign_runtime.STRATEGY_STORE.resolve_battle_room_result(
        battle_room_id=getattr(room, "room_id", ""),
        winner_team_id=int(winner_team_id),
        battle_summary=strategy_room_battle_summary(room),
        surviving_grid_units_by_team=strategy_room_survivors_by_team(room),
        surviving_hero_codes_by_team=strategy_room_surviving_hero_codes_by_team(room),
    )
    if campaign is None:
        return None
    return campaign.to_public_dict(resume_status=campaign_runtime.STRATEGY_STORE.resume_status(campaign.campaign_id))


def room_state_with_strategy_sync(
    room,
    player_token: str | None,
    *,
    base_url: str | None,
    recovered: bool = False,
) -> dict[str, Any]:
    record_finished_room_history(room)
    state = room.serialize_state(player_token, base_url=base_url)
    checkpoint = persist_strategy_battle_room(room)
    if checkpoint is not None:
        access_mode = campaign_runtime.STRATEGY_STORE.campaign_access_mode(checkpoint.campaign_id)
        state["battle_recovery"] = {
            "status": "read_only" if access_mode == "read_only" else ("recovered" if recovered else checkpoint.status),
            "access_mode": access_mode,
            "read_only": access_mode == "read_only",
            "checkpoint_version": checkpoint.room_version,
            "updated_at": checkpoint.updated_at,
            "restart_count": checkpoint.restart_count,
            "message": (
                "归档战役已按原账号恢复；地图、复盘和已完成战斗均为只读。"
                if access_mode == "read_only"
                else (
                    "已从服务器检查点恢复到同一战斗进度。"
                    if recovered
                    else "战斗进度已保存到服务器检查点。"
                )
            ),
        }
    strategy_campaign = sync_finished_strategy_battle_room(room)
    if strategy_campaign is not None:
        state["strategy_campaign"] = strategy_campaign
    return state
