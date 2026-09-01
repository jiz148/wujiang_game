from __future__ import annotations

import copy
import pickle
import random
import secrets
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Optional

from wujiang.tactical.engine.army import (
    ARMY_KIND_LABELS,
    army_codes_from_counts,
    default_army_orders,
    empty_army_counts,
    is_army_soldier,
    living_army_units,
    normalize_army_command,
    normalize_army_counts,
)
from wujiang.tactical.engine.core import (
    ActionError,
    Battle,
    Position,
    SKIRMISH_HERO_TURN_LIMIT,
    Unit,
)
from wujiang.tactical.heroes.registry import RoomBattleEntry, create_room_battle, list_heroes
from wujiang.tactical.rooms.ai import (
    choose_chain_reaction,
    choose_instant_action,
    choose_respawn_action,
    choose_turn_bundle_action,
)
from wujiang.tactical.rooms.replay import ReplayRecorder
from wujiang.tactical.rooms.postgame import build_postgame_summary
from wujiang.tactical.rooms.launch import is_campaign_launch, make_launch_context, public_launch_context
from wujiang.tactical.rooms.tutorial import TUTORIAL_ID, next_tutorial_step_id, tutorial_step


ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 6
DEFAULT_ROOM_MODE = "classic"
DEFAULT_RANDOM_ROSTER_SIZE = 1
MIN_ROOM_SEAT_COUNT = 2
MAX_ROOM_SEAT_COUNT = 6
MIN_ROOM_HERO_LIMIT = 1
MAX_ROOM_HERO_LIMIT = 20
AUTO_CONFIGURE_COUNT_MIN = 1
AUTO_CONFIGURE_COUNT_MAX = 12
AUTO_CONFIGURE_COUNT_DEFAULT = 3
AUTO_CONFIGURE_POINTS_MIN = 10
AUTO_CONFIGURE_POINTS_MAX = 50
AUTO_CONFIGURE_POINTS_DEFAULT = 15
AUTO_CONFIGURE_ROSTER_CAP = 12
TEAM_IDS = (1, 2)
TEAM_LABELS = {1: "红队", 2: "蓝队"}
CONTROLLER_TYPES = {"open", "human", "ai"}
DEFAULT_AI_DIFFICULTY = "standard"
AI_DIFFICULTIES = {"easy", "standard", "aggressive"}
HERO_AI_STYLES = {"follow", "rush"}
ARMY_AI_STYLES = {"seek", "advance", "hold"}
DEFAULT_HERO_AI_STYLE = "follow"
DEFAULT_ARMY_AI_STYLE = "seek"
DEFAULT_SIMULATION_SPEED = 1.0
SIMULATION_SPEED_OPTIONS = (0.5, 1.0, 2.0, 4.0, 6.0, 8.0, 16.0)
ROOM_ONLINE_WINDOW_SECONDS = 5.0
ROOM_UNSTABLE_WINDOW_SECONDS = 20.0
TURN_TIMEOUT_OPTIONS = (0, 30, 60, 120)
DEFAULT_TURN_TIMEOUT_SECONDS = 0
ROOM_TURN_TIMEOUT_SECONDS = float(DEFAULT_TURN_TIMEOUT_SECONDS)
DEFAULT_BOARD_WIDTH = 10
DEFAULT_BOARD_HEIGHT = 10
MIN_BOARD_SIZE = 6
MAX_BOARD_SIZE = 100
STALE_QUEUED_ACTOR_LOG_MARKER = "行动者已不在战场"
ROOM_MODES: dict[str, dict[str, str]] = {
    "classic": {
        "name": "标准选将",
        "description": "双方各自选择多个武将，按固定出生与交替行动顺序开始对局。",
    },
    "random": {
        "name": "随机选人",
        "description": "双方无需手动选将，开局后随机分配不重复武将，使用更大的战场、随机出生，并按能力值决定先手。",
    },
}


class RoomError(Exception):
    pass


def heroes_catalog() -> list[dict[str, Any]]:
    return list_heroes()


def hero_lookup() -> dict[str, dict[str, Any]]:
    return {hero["code"]: hero for hero in heroes_catalog()}


def auto_configure_hero_choices() -> list[tuple[str, int]]:
    return [
        (str(hero.get("code") or ""), max(1, int(hero.get("level") or 1)))
        for hero in heroes_catalog()
        if str(hero.get("code") or "").strip()
    ]


def normalize_auto_configure_method(method: Any) -> str:
    normalized = str(method or "count").strip().lower()
    aliases = {"count": "count", "points": "points", "数量": "count", "点数": "points"}
    mapped = aliases.get(normalized)
    if mapped is None:
        raise RoomError("自动配置方式只能是按数量或按点数。")
    return mapped


def normalize_auto_configure_count(count: Any) -> int:
    try:
        value = int(count)
    except (TypeError, ValueError) as exc:
        raise RoomError("武将数量必须是整数。") from exc
    if value < AUTO_CONFIGURE_COUNT_MIN or value > AUTO_CONFIGURE_COUNT_MAX:
        raise RoomError(f"武将数量必须在 {AUTO_CONFIGURE_COUNT_MIN} 到 {AUTO_CONFIGURE_COUNT_MAX} 之间。")
    return value


def normalize_auto_configure_points(points: Any) -> int:
    try:
        value = int(points)
    except (TypeError, ValueError) as exc:
        raise RoomError("点数必须是整数。") from exc
    if value < AUTO_CONFIGURE_POINTS_MIN or value > AUTO_CONFIGURE_POINTS_MAX:
        raise RoomError(f"点数必须在 {AUTO_CONFIGURE_POINTS_MIN} 到 {AUTO_CONFIGURE_POINTS_MAX} 之间。")
    return value


def pick_roster_by_count(
    choices: list[tuple[str, int]],
    count: int,
    *,
    allow_duplicates: bool,
    rng: Any = None,
) -> list[str]:
    picker = rng or random
    codes = [code for code, _level in choices]
    if not codes:
        raise RoomError("当前没有可选择的武将。")
    if allow_duplicates:
        return [picker.choice(codes) for _ in range(count)]
    take = min(count, len(codes))
    return picker.sample(codes, take)


def pick_roster_by_points(
    choices: list[tuple[str, int]],
    points: int,
    *,
    allow_duplicates: bool,
    rng: Any = None,
    roster_cap: int = AUTO_CONFIGURE_ROSTER_CAP,
) -> list[str]:
    picker = rng or random
    if not choices:
        raise RoomError("当前没有可选择的武将。")
    remaining = int(points)
    picked: list[str] = []
    used: set[str] = set()
    while len(picked) < max(1, int(roster_cap)):
        candidates = [
            (code, level)
            for code, level in choices
            if level <= remaining and (allow_duplicates or code not in used)
        ]
        if not candidates:
            break
        code, level = picker.choice(candidates)
        picked.append(code)
        used.add(code)
        remaining -= level
    if not picked:
        raise RoomError("当前点数不足以选出任何武将。")
    return picked


def pick_auto_configure_roster(
    *,
    method: str,
    count: int,
    points: int,
    allow_duplicates: bool,
    rng: Any = None,
) -> list[str]:
    choices = auto_configure_hero_choices()
    if method == "points":
        return pick_roster_by_points(choices, points, allow_duplicates=allow_duplicates, rng=rng)
    return pick_roster_by_count(choices, count, allow_duplicates=allow_duplicates, rng=rng)


def normalize_room_id(room_id: str) -> str:
    return str(room_id or "").strip().upper()


def normalize_player_name(name: str) -> str:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return "未命名玩家"
    return cleaned[:20]


def normalize_room_mode(mode: str) -> str:
    normalized = str(mode or DEFAULT_ROOM_MODE).strip().lower()
    if normalized not in ROOM_MODES:
        raise RoomError("未知的房间模式。")
    return normalized


def normalize_hero_delta(delta: Any) -> int:
    try:
        normalized = int(delta)
    except (TypeError, ValueError) as exc:
        raise RoomError("选将数量变化必须是整数。") from exc
    if normalized == 0:
        raise RoomError("选将数量变化不能为 0。")
    return normalized


def normalize_random_roster_size(size: Any) -> int:
    try:
        normalized = int(size)
    except (TypeError, ValueError) as exc:
        raise RoomError("随机模式的人数 n 必须是正整数。") from exc
    if normalized <= 0:
        raise RoomError("随机模式的人数 n 至少为 1。")
    return normalized


def normalize_turn_timeout(seconds: Any) -> int:
    """0 表示不限时；其余只能是 30 / 60 / 120 秒。"""
    if seconds is None or seconds == "":
        return DEFAULT_TURN_TIMEOUT_SECONDS
    try:
        normalized = int(seconds)
    except (TypeError, ValueError) as exc:
        raise RoomError("回合时限必须是整数。") from exc
    if normalized not in TURN_TIMEOUT_OPTIONS:
        raise RoomError("回合时限只能是 30、60、120 秒或无限。")
    return normalized


def normalize_board_axis(value: Any, *, label: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RoomError(f"{label}必须是整数。") from exc
    if normalized < MIN_BOARD_SIZE or normalized > MAX_BOARD_SIZE:
        raise RoomError(f"{label}只能在 {MIN_BOARD_SIZE} 到 {MAX_BOARD_SIZE} 之间。")
    return normalized


def normalize_room_hero_limit(size: Any) -> int:
    """0 表示不限制；1–20 是每个席位的武将上限。"""
    if size in {None, "", False}:
        return 0
    try:
        normalized = int(size)
    except (TypeError, ValueError) as exc:
        raise RoomError("武将数量上限必须是整数。") from exc
    if normalized == 0:
        return 0
    if normalized < MIN_ROOM_HERO_LIMIT or normalized > MAX_ROOM_HERO_LIMIT:
        raise RoomError(f"武将数量上限只能在 {MIN_ROOM_HERO_LIMIT} 到 {MAX_ROOM_HERO_LIMIT} 之间。")
    return normalized


def normalize_room_seat_count(size: Any) -> int:
    try:
        normalized = int(size)
    except (TypeError, ValueError) as exc:
        raise RoomError("席位数必须是整数。") from exc
    if normalized < MIN_ROOM_SEAT_COUNT or normalized > MAX_ROOM_SEAT_COUNT:
        raise RoomError(f"房间席位数只能在 {MIN_ROOM_SEAT_COUNT} 到 {MAX_ROOM_SEAT_COUNT} 之间。")
    return normalized


def normalize_team_id(team_id: Any) -> int:
    try:
        normalized = int(team_id)
    except (TypeError, ValueError) as exc:
        raise RoomError("席位队伍只能是 1 或 2。") from exc
    if normalized not in TEAM_IDS:
        raise RoomError("席位队伍只能是 1 或 2。")
    return normalized


def normalize_controller_type(controller_type: str) -> str:
    normalized = str(controller_type or "").strip().lower()
    if normalized not in CONTROLLER_TYPES:
        raise RoomError("席位状态只能是 open、human 或 ai。")
    return normalized


def normalize_random_quota(quota: Any) -> int:
    try:
        normalized = int(quota)
    except (TypeError, ValueError) as exc:
        raise RoomError("随机模式配额必须是非负整数。") from exc
    if normalized < 0:
        raise RoomError("随机模式配额不能小于 0。")
    return normalized


def normalize_ai_difficulty(difficulty: Any) -> str:
    normalized = str(difficulty or DEFAULT_AI_DIFFICULTY).strip().lower()
    if normalized not in AI_DIFFICULTIES:
        raise RoomError("AI 难度只能是 easy、standard 或 aggressive。")
    return normalized


def normalize_hero_ai_style(style: Any) -> str:
    aliases = {"跟队": "follow", "突击": "rush", "balanced": "follow", "aggressive": "rush"}
    normalized = aliases.get(str(style or "").strip(), str(style or DEFAULT_HERO_AI_STYLE).strip().lower())
    if normalized not in HERO_AI_STYLES:
        raise RoomError("武将 AI 倾向只能是跟队或突击。")
    return normalized


def normalize_army_ai_style(style: Any) -> str:
    aliases = {"寻敌": "seek", "进军": "advance", "固守": "hold"}
    normalized = aliases.get(str(style or "").strip(), str(style or DEFAULT_ARMY_AI_STYLE).strip().lower())
    if normalized not in ARMY_AI_STYLES:
        raise RoomError("士兵 AI 倾向只能是寻敌、进军或固守。")
    return normalized


# AI 回放选格：路径仍一格一格走；范围技能一次轮询露出全部格子，不加快轮询。
SIMULATION_ACTION_ANNOUNCE_SECONDS = 1.3
SIMULATION_ACTION_CONFIRM_SECONDS = 0.7
SIMULATION_PATH_SELECT_SECONDS = 0.18


def pending_preview_select_step(*, using_path: bool, remaining: int) -> tuple[int, float]:
    remaining = max(0, int(remaining))
    if remaining <= 0:
        return 0, SIMULATION_ACTION_CONFIRM_SECONDS
    if using_path:
        delay = SIMULATION_PATH_SELECT_SECONDS if remaining > 1 else SIMULATION_ACTION_CONFIRM_SECONDS
        return 1, delay
    return remaining, SIMULATION_ACTION_CONFIRM_SECONDS


def normalize_simulation_speed(speed: Any) -> float:
    try:
        normalized = float(speed)
    except (TypeError, ValueError) as exc:
        raise RoomError("å›žæ”¾ / æ¨¡æ‹Ÿé€Ÿåº¦å¿…é¡»æ˜¯æ•°å­—ã€‚") from exc
    if normalized not in SIMULATION_SPEED_OPTIONS:
        choices = ", ".join(str(value) for value in SIMULATION_SPEED_OPTIONS)
        raise RoomError(f"å›žæ”¾ / æ¨¡æ‹Ÿé€Ÿåº¦åªèƒ½æ˜¯ {choices}ã€‚")
    return normalized


def max_random_roster_size() -> int:
    return max(1, len(hero_lookup()) // 2)


def validate_random_roster_size_for_catalog(roster_size: Any) -> int:
    size = normalize_random_roster_size(roster_size)
    hero_codes = tuple(hero_lookup().keys())
    max_size = max_random_roster_size()
    if size * 2 > len(hero_codes):
        raise RoomError(f"当前武将池最多只支持随机模式 n = {max_size}，因为同一局中不会出现重复武将。")
    return size


def room_mode_payload(mode: str) -> dict[str, str]:
    normalized = normalize_room_mode(mode)
    meta = ROOM_MODES[normalized]
    description = meta["description"]
    if normalized == "random":
        description = "双方无需手动选将，由房主设置 n 后，开局时双方各随机获得 n 个不重复的武将。同一局场上不会出现相同武将。地图大小和行动顺序与标准模式相同，但出生点为随机。"
    return {
        "code": normalized,
        "name": meta["name"],
        "description": description,
    }


def room_mode_list_payload() -> list[dict[str, str]]:
    return [room_mode_payload(code) for code in ROOM_MODES]


def random_room_hero_codes(roster_size: int) -> tuple[list[str], list[str]]:
    hero_codes = tuple(hero_lookup().keys())
    size = validate_random_roster_size_for_catalog(roster_size)
    sampled_codes = random.sample(hero_codes, size * 2)
    return (
        sampled_codes[:size],
        sampled_codes[size:],
    )


def default_team_for_seat(player_id: int) -> int:
    return 1 if int(player_id) % 2 == 1 else 2


def team_name(team_id: int) -> str:
    return TEAM_LABELS[normalize_team_id(team_id)]

def clone_visible_name(unit_payload: dict[str, Any], viewer_player_id: Optional[int]) -> str:
    name = str(unit_payload.get("name") or "")
    if not unit_payload.get("is_clone"):
        return name
    if viewer_player_id is None or unit_payload.get("player_id") != viewer_player_id:
        return name.replace("（分身）", "")
    return name if name.endswith("（分身）") else f"{name}（分身）"


def disguise_clone_payload_for_enemy_view(
    battle: Battle,
    unit_payload: dict[str, Any],
    source_payloads_by_id: dict[str, dict[str, Any]],
) -> None:
    unit_id = str(unit_payload.get("id") or "")
    actual_unit = battle.get_unit(unit_id) if unit_id else None
    source_id = battle.controlling_hero_id(actual_unit)
    source_payload = copy.deepcopy(source_payloads_by_id.get(source_id or ""))
    if not source_payload:
        unit_payload["is_clone"] = False
        return
    preserved = {
        "id": unit_payload.get("id"),
        "player_id": unit_payload.get("player_id"),
        "alive": unit_payload.get("alive"),
        "banished": unit_payload.get("banished"),
        "banish_turns_remaining": unit_payload.get("banish_turns_remaining"),
        "banish_return_position": copy.deepcopy(unit_payload.get("banish_return_position")),
        "position": copy.deepcopy(unit_payload.get("position")),
        "footprint": copy.deepcopy(unit_payload.get("footprint")),
        "occupied_cells": copy.deepcopy(unit_payload.get("occupied_cells")),
        "mount_owner_id": unit_payload.get("mount_owner_id"),
        "mounted_on_unit_id": unit_payload.get("mounted_on_unit_id"),
        "ridden_by_unit_id": unit_payload.get("ridden_by_unit_id"),
    }
    source_payload.update(preserved)
    source_payload["is_clone"] = False
    unit_payload.clear()
    unit_payload.update(source_payload)


def apply_private_clone_labels(
    state: dict[str, Any],
    battle: Battle,
    viewer_player_id: Optional[int],
) -> None:
    visible_names_by_id: dict[str, str] = {}
    source_payloads_by_id = {
        str(unit_payload.get("id") or ""): copy.deepcopy(unit_payload)
        for unit_payload in state.get("units", [])
    }
    for unit_payload in state.get("units", []):
        visible_name = clone_visible_name(unit_payload, viewer_player_id)
        if unit_payload.get("is_clone") and (viewer_player_id is None or unit_payload.get("player_id") != viewer_player_id):
            disguise_clone_payload_for_enemy_view(battle, unit_payload, source_payloads_by_id)
        unit_payload["name"] = visible_name
        visible_names_by_id[str(unit_payload.get("id"))] = visible_name
    for active_unit in state.get("active_units", []):
        unit_id = str(active_unit.get("unit_id") or "")
        if unit_id in visible_names_by_id:
            active_unit["name"] = visible_names_by_id[unit_id]




def battle_unit_owner_seat_id(battle: Battle, unit: Unit | None) -> Optional[int]:
    if unit is None:
        return None
    owner = getattr(unit, "owner_seat_id", None)
    if owner is not None:
        return int(owner)
    hero_id = battle.controlling_hero_id(unit)
    if not hero_id:
        return None
    hero = battle.get_unit(hero_id)
    hero_owner = getattr(hero, "owner_seat_id", None)
    return int(hero_owner) if hero_owner is not None else None


def _active_units_for_viewer(battle: Battle) -> list[Unit]:
    prompt = battle.current_respawn_prompt()
    if prompt is not None:
        return [battle.get_unit(prompt.unit_id)]
    if battle.pending_chain is not None:
        current_unit_id = battle.pending_chain.current_unit_id()
        return [battle.get_unit(current_unit_id)] if current_unit_id else []
    return [
        unit
        for unit in battle.current_turn_bundle_units(include_banished=False)
        if not is_army_soldier(unit)
    ]


def _instant_units_for_viewer(battle: Battle, viewer_player_id: int) -> list[Unit]:
    if battle.pending_chain is not None or battle.current_respawn_prompt() is not None:
        return []
    return battle.instant_action_units_for_player(viewer_player_id)


def battle_state_for_viewer(
    battle: Battle,
    viewer_player_id: Optional[int],
    viewer_seat_id: Optional[int] = None,
) -> dict[str, Any]:
    state = battle.to_public_dict()
    input_player = state["input_player"]
    state["viewer_player_id"] = viewer_player_id
    hidden_unit_ids = {
        unit.unit_id
        for unit in battle.all_units()
        if unit.has_status("隐身") and (viewer_player_id is None or unit.player_id != viewer_player_id)
    }
    if hidden_unit_ids:
        state["units"] = [unit for unit in state["units"] if unit["id"] not in hidden_unit_ids]
        filtered_events: list[dict[str, Any]] = []
        for event in state.get("visual_events", []):
            actor_id = str(event.get("actor_id") or "")
            if actor_id and actor_id in hidden_unit_ids:
                continue
            target_unit_ids = [
                str(unit_id)
                for unit_id in event.get("target_unit_ids", [])
                if str(unit_id) not in hidden_unit_ids
            ]
            if event.get("kind") == "defense" and not target_unit_ids:
                continue
            event_payload = dict(event)
            event_payload["target_unit_ids"] = target_unit_ids
            filtered_events.append(event_payload)
        state["visual_events"] = filtered_events
    state["active_units"] = []
    if viewer_player_id is not None and viewer_player_id == input_player:
        state["active_units"] = [
            {
                "unit_id": unit.unit_id,
                "name": unit.name,
                "actions": battle.action_snapshot_for(unit),
                "reactions": battle.reaction_snapshot_for(unit),
            }
            for unit in _active_units_for_viewer(battle)
            if unit.player_id == viewer_player_id
            and (viewer_seat_id is None or battle_unit_owner_seat_id(battle, unit) == viewer_seat_id)
        ]
    elif viewer_player_id is not None:
        instant_units = _instant_units_for_viewer(battle, viewer_player_id)
        if instant_units:
            state["input_player"] = viewer_player_id
            state["active_units"] = [
                {
                    "unit_id": unit.unit_id,
                    "name": unit.name,
                    "actions": battle.action_snapshot_for(unit),
                    "reactions": battle.reaction_snapshot_for(unit),
                }
                for unit in instant_units
                if viewer_seat_id is None or battle_unit_owner_seat_id(battle, unit) == viewer_seat_id
            ]
    apply_private_clone_labels(state, battle, viewer_player_id)
    return state


@dataclass(slots=True)
class PlayerSeat:
    player_id: int
    team_id: int
    controller_type: str = "open"
    token: Optional[str] = None
    name: str = ""
    hero_counts: dict[str, int] = field(default_factory=dict)
    army_counts: dict[str, int] = field(default_factory=empty_army_counts)
    random_quota: int = 0
    ai_difficulty_override: Optional[str] = None
    joined_at: Optional[float] = None
    last_seen_at: Optional[float] = None
    ready: bool = False
    account_user_id: Optional[int] = None
    ai_takeover: bool = False
    hero_ai_style: str = DEFAULT_HERO_AI_STYLE
    army_ai_style: str = DEFAULT_ARMY_AI_STYLE

    @property
    def occupied(self) -> bool:
        return self.controller_type != "open"

    @property
    def is_human(self) -> bool:
        return self.controller_type == "human"

    @property
    def is_ai(self) -> bool:
        return self.controller_type == "ai"

    @property
    def is_ai_controlled(self) -> bool:
        return self.is_ai or bool(getattr(self, "ai_takeover", False))

    @property
    def can_join(self) -> bool:
        return self.controller_type == "open"

    @property
    def hero_total_count(self) -> int:
        return sum(max(int(count), 0) for count in self.hero_counts.values())

    def claim(self, player_name: str, *, account_user_id: Optional[int] = None) -> str:
        if not self.can_join:
            raise RoomError(f"席位 {self.player_id} 当前不能加入。")
        self.controller_type = "human"
        self.token = secrets.token_urlsafe(18)
        self.name = normalize_player_name(player_name)
        self.joined_at = time.time()
        self.last_seen_at = self.joined_at
        self.ready = False
        self.account_user_id = int(account_user_id) if account_user_id is not None else None
        self.ai_takeover = False
        self.hero_ai_style = DEFAULT_HERO_AI_STYLE
        self.army_ai_style = DEFAULT_ARMY_AI_STYLE
        return self.token

    def set_ai(self) -> None:
        if self.is_human:
            raise RoomError("已有真人加入的席位不能直接改成 AI。")
        self.controller_type = "ai"
        self.token = None
        self.name = f"AI {self.player_id}"
        self.joined_at = time.time()
        self.last_seen_at = self.joined_at
        self.ready = False
        self.account_user_id = None
        self.ai_takeover = False
        self.hero_ai_style = "rush"
        self.army_ai_style = "seek"

    def set_open(self) -> None:
        self.controller_type = "open"
        self.token = None
        self.name = ""
        self.clear_roster()
        self.random_quota = 0
        self.ai_difficulty_override = None
        self.joined_at = None
        self.last_seen_at = None
        self.ready = False
        self.account_user_id = None
        self.ai_takeover = False
        self.hero_ai_style = DEFAULT_HERO_AI_STYLE
        self.army_ai_style = DEFAULT_ARMY_AI_STYLE
        self.army_counts = empty_army_counts()

    def clear_roster(self) -> None:
        self.hero_counts.clear()

    def replace_roster(self, roster: list[str]) -> None:
        self.clear_roster()
        for hero_code in roster:
            self.adjust_hero_count(hero_code, 1)

    def release(self) -> None:
        self.set_open()

    def mark_seen(self) -> None:
        if self.is_human:
            self.last_seen_at = time.time()

    def connection_status(self, *, now: Optional[float] = None) -> str:
        if self.is_ai:
            return "ai"
        if not self.is_human or self.last_seen_at is None:
            return "open"
        age = max(0.0, (time.time() if now is None else now) - self.last_seen_at)
        if age <= ROOM_ONLINE_WINDOW_SECONDS:
            return "online"
        if age <= ROOM_UNSTABLE_WINDOW_SECONDS:
            return "unstable"
        return "offline"

    def matches_name(self, player_name: str) -> bool:
        return self.is_human and self.name == normalize_player_name(player_name)

    def reclaim(self, player_name: str, *, account_user_id: Optional[int] = None) -> str:
        if not self.matches_name(player_name) or not self.token:
            raise RoomError("无法用该昵称恢复这个席位。")
        if self.account_user_id is not None and int(account_user_id or 0) != self.account_user_id:
            raise RoomError("这个席位属于另一个登录账号，无法恢复。")
        self.mark_seen()
        return self.token

    def adjust_hero_count(self, hero_code: str, delta: int) -> None:
        next_count = self.hero_counts.get(hero_code, 0) + delta
        if next_count < 0:
            raise RoomError("该武将当前数量不能减到 0 以下。")
        if next_count == 0:
            self.hero_counts.pop(hero_code, None)
            return
        self.hero_counts[hero_code] = next_count

    def trim_roster_from_end(self, limit: int) -> None:
        """从名单末尾往下砍，直到不超过上限。

        席位上的标签按加入顺序往下排，最下面就是最后加进来的那个；超限时从那里开始删。
        """
        if limit <= 0:
            return
        while self.hero_total_count > limit and self.hero_counts:
            last_code = next(reversed(self.hero_counts))
            self.adjust_hero_count(last_code, -1)

    def expanded_roster(self) -> list[str]:
        roster: list[str] = []
        for hero_code, count in sorted(self.hero_counts.items()):
            roster.extend([hero_code] * max(int(count), 0))
        return roster

    def single_hero_code(self) -> Optional[str]:
        roster = self.expanded_roster()
        return roster[0] if len(roster) == 1 else None

    def hero_summary(self, heroes_by_code: dict[str, dict[str, Any]]) -> Optional[str]:
        entries: list[str] = []
        for hero_code, count in sorted(self.hero_counts.items()):
            if count <= 0:
                continue
            hero = heroes_by_code.get(hero_code)
            label = hero["name"] if hero else hero_code
            entries.append(f"{label} × {count}")
        return " / ".join(entries) if entries else None

    def to_public_dict(self, heroes_by_code: dict[str, dict[str, Any]], host_player_id: int) -> dict[str, Any]:
        roster = []
        for hero_code, count in sorted(self.hero_counts.items()):
            if count <= 0:
                continue
            hero = heroes_by_code.get(hero_code)
            roster.append(
                {
                    "code": hero_code,
                    "name": hero["name"] if hero else hero_code,
                    "count": int(count),
                }
            )
        single_hero_code = self.single_hero_code()
        single_hero = heroes_by_code.get(single_hero_code or "")
        now = time.time()
        last_seen_age_seconds = (
            max(0, int(now - self.last_seen_at))
            if self.is_human and self.last_seen_at is not None
            else None
        )
        return {
            "player_id": self.player_id,
            "team_id": self.team_id,
            "team_name": team_name(self.team_id),
            "controller_type": self.controller_type,
            "occupied": self.occupied,
            "is_human": self.is_human,
            "is_ai": self.is_ai,
            "ai_takeover": bool(getattr(self, "ai_takeover", False)),
            "is_ai_controlled": self.is_ai_controlled,
            "hero_ai_style": getattr(self, "hero_ai_style", DEFAULT_HERO_AI_STYLE),
            "army_ai_style": getattr(self, "army_ai_style", DEFAULT_ARMY_AI_STYLE),
            "joinable": self.can_join,
            "name": self.name or None,
            "hero_counts": {code: int(count) for code, count in sorted(self.hero_counts.items()) if count > 0},
            "army_counts": {kind: int(self.army_counts.get(kind, 0) or 0) for kind in ARMY_KIND_LABELS},
            "army_total_count": sum(int(self.army_counts.get(kind, 0) or 0) for kind in ARMY_KIND_LABELS),
            "hero_roster": roster,
            "hero_total_count": self.hero_total_count,
            "hero_summary": self.hero_summary(heroes_by_code),
            "hero_code": single_hero_code,
            "hero_name": single_hero["name"] if single_hero else None,
            "random_quota": self.random_quota,
            "ai_difficulty_override": self.ai_difficulty_override,
            "is_host": self.player_id == host_player_id,
            "ready": self.is_ai or (self.is_human and self.ready),
            "connection_status": self.connection_status(now=now),
            "last_seen_age_seconds": last_seen_age_seconds,
        }


class GameRoom:
    def __init__(self, room_id: str, *, mode: str = DEFAULT_ROOM_MODE, seat_count: int = MIN_ROOM_SEAT_COUNT) -> None:
        self.room_id = normalize_room_id(room_id)
        self.mode = normalize_room_mode(mode)
        self.experience_kind = "custom"
        self.launch_context = make_launch_context("skirmish")
        self.tutorial_state: Optional[dict[str, Any]] = None
        self.tutorial_checkpoint: Optional[Battle] = None
        self.random_roster_size = DEFAULT_RANDOM_ROSTER_SIZE
        self.hero_limit = 0
        self.board_width = DEFAULT_BOARD_WIDTH
        self.board_height = DEFAULT_BOARD_HEIGHT
        self.turn_timeout_seconds = DEFAULT_TURN_TIMEOUT_SECONDS
        self.hero_turn_limit = SKIRMISH_HERO_TURN_LIMIT
        self.turn_limit_winner = 2
        self.default_ai_difficulty = DEFAULT_AI_DIFFICULTY
        self.host_player_id = 1
        self.seats = self._build_seats(normalize_room_seat_count(seat_count))
        self.army_orders_by_team = default_army_orders()
        self._reset_random_quotas_to_defaults()
        self.battle: Optional[Battle] = None
        self.replay: Optional[ReplayRecorder] = None
        self.match_number = 0
        self.current_match_id: Optional[str] = None
        self.simulation_paused = False
        self.fast_ai_simulation = False
        self.simulation_speed = DEFAULT_SIMULATION_SPEED
        self.simulation_last_advanced_at: Optional[float] = None
        self.pending_simulation_action: Optional[dict[str, Any]] = None
        self.last_action_id = 0
        self.last_action_meta: Optional[dict[str, Any]] = None
        self.turn_prompt_key: Optional[str] = None
        self.turn_prompt_started_at: Optional[float] = None
        self.turn_deadline_at: Optional[float] = None
        self.last_turn_timeout: Optional[dict[str, Any]] = None
        self.status = "lobby"
        self.version = 0
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._lock = threading.RLock()

    def __getstate__(self) -> dict[str, Any]:
        state = dict(self.__dict__)
        state.pop("_lock", None)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        if not hasattr(self, "turn_timeout_seconds"):
            self.turn_timeout_seconds = DEFAULT_TURN_TIMEOUT_SECONDS
        if not hasattr(self, "board_width"):
            self.board_width = DEFAULT_BOARD_WIDTH
        if not hasattr(self, "board_height"):
            self.board_height = DEFAULT_BOARD_HEIGHT
        if not hasattr(self, "launch_context"):
            self.launch_context = make_launch_context("skirmish")
        if not hasattr(self, "hero_turn_limit"):
            self.hero_turn_limit = SKIRMISH_HERO_TURN_LIMIT
        if not hasattr(self, "turn_limit_winner"):
            self.turn_limit_winner = 2
        self._lock = threading.RLock()

    def checkpoint_bytes(self) -> bytes:
        """Serialize an internal, authoritative room checkpoint for trusted storage."""
        with self._lock:
            return zlib.compress(pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL), level=6)

    @classmethod
    def from_checkpoint_bytes(cls, payload: bytes) -> GameRoom:
        try:
            restored = pickle.loads(zlib.decompress(bytes(payload)))
        except Exception as exc:
            raise RoomError("战略战斗检查点已损坏，不能静默恢复。") from exc
        if not isinstance(restored, cls):
            raise RoomError("战略战斗检查点类型无效，不能静默恢复。")
        restored.room_id = normalize_room_id(restored.room_id)
        restored._lock = threading.RLock()
        return restored

    def _build_seats(self, seat_count: int) -> dict[int, PlayerSeat]:
        return {
            player_id: PlayerSeat(
                player_id=player_id,
                team_id=default_team_for_seat(player_id),
            )
            for player_id in range(1, seat_count + 1)
        }

    def touch(self) -> None:
        self.version += 1
        self.updated_at = time.time()

    def invalidate_readiness(self) -> None:
        for seat in self.seats.values():
            if seat.is_human:
                seat.ready = False

    def configure_tutorial(self) -> None:
        with self._lock:
            if self.battle is None:
                raise RoomError("教学战斗尚未创建。")
            fire = next((unit for unit in self.battle.player_units(1) if unit.hero_code == "fire_funeral"), None)
            ellie = next((unit for unit in self.battle.player_units(2) if unit.hero_code == "ellie"), None)
            if fire is None or ellie is None:
                raise RoomError("教学固定阵容不完整。")
            fire.position = Position(3, 4)
            ellie.position = Position(6, 4)
            self.battle.configure_turn_order([fire.unit_id, ellie.unit_id], starting_index=0)
            self.battle.active_turn_unit_id = fire.unit_id
            self.experience_kind = "tutorial"
            self.tutorial_state = {
                "tutorial_id": TUTORIAL_ID,
                "step_id": "select_unit",
                "completed_step_ids": [],
                "started_at": time.time(),
                "first_effective_action_at": None,
                "completed_at": None,
                "retry_count": 0,
            }
            self.tutorial_checkpoint = None
            self.replay = ReplayRecorder(self.room_id, self.mode, match_id=self.current_match_id)
            self._record_replay_step("tutorial_start")
            self.touch()

    def tutorial_public_state(self) -> Optional[dict[str, Any]]:
        if self.tutorial_state is None:
            return None
        step_id = str(self.tutorial_state["step_id"])
        return {
            **self.tutorial_state,
            "step": tutorial_step(step_id),
            "can_retry_checkpoint": self.tutorial_checkpoint is not None,
        }

    def _tutorial_advance(self, completed_step_id: str) -> None:
        if self.tutorial_state is None or self.tutorial_state["step_id"] != completed_step_id:
            return
        completed = self.tutorial_state["completed_step_ids"]
        if completed_step_id not in completed:
            completed.append(completed_step_id)
        self.tutorial_state["step_id"] = next_tutorial_step_id(completed_step_id)

    def tutorial_select_unit(self, token: str, unit_id: str) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if seat.team_id != 1 or self.tutorial_state is None or self.battle is None:
                raise RoomError("当前不是可操作的新手教学。")
            if self.tutorial_state["step_id"] != "select_unit":
                return
            unit = self.battle.get_unit(str(unit_id or ""))
            if unit.hero_code != "fire_funeral" or unit.player_id != 1:
                raise RoomError("这一步请点击你控制的火葬者。")
            self._tutorial_advance("select_unit")
            self.touch()

    def _validate_tutorial_action(self, payload: dict[str, Any]) -> None:
        if self.tutorial_state is None or self.battle is None:
            return
        step_id = str(self.tutorial_state["step_id"])
        action_type = str(payload.get("type") or "")
        fire = next(unit for unit in self.battle.player_units(1) if unit.hero_code == "fire_funeral")
        ellie = next(unit for unit in self.battle.player_units(2) if unit.hero_code == "ellie")
        if step_id == "select_unit":
            raise RoomError("先点击火葬者完成选中教学。")
        if step_id == "move":
            if action_type != "move" or str(payload.get("unit_id") or "") != fire.unit_id:
                raise RoomError("这一步只需要让火葬者移动到金色目标格。")
            if (int(payload.get("x", -1)), int(payload.get("y", -1))) != (4, 4):
                raise RoomError("请移动到火葬者与艾莉之间的金色目标格。")
            return
        if step_id == "basic_attack":
            if action_type != "attack" or str(payload.get("target_unit_id") or "") != ellie.unit_id:
                raise RoomError("这一步请选择普通攻击并以艾莉为目标。")
            return
        if step_id == "active_skill":
            if action_type != "skill" or str(payload.get("skill_code") or "") != "pierce":
                raise RoomError("这一步请使用火葬者的主动技能“穿刺”。")
            selected_cells = {
                (int(cell.get("x", -1)), int(cell.get("y", -1)))
                for cell in (payload.get("cells") or [])
                if isinstance(cell, dict)
            }
            if (ellie.position.x, ellie.position.y) not in selected_cells:
                raise RoomError("请用穿刺选择朝向艾莉的两格直线。")
            return
        if step_id == "end_turn":
            if action_type != "end_turn":
                raise RoomError("这一步只需要点击结束回合。")
            return
        if step_id == "chain_response" and action_type not in {"chain_react", "chain_skip"}:
            raise RoomError("等待艾莉触发连锁后，请选择响应或放弃连锁。")

    def _update_tutorial_after_action(self, payload: dict[str, Any]) -> None:
        if self.tutorial_state is None or self.battle is None:
            return
        step_id = str(self.tutorial_state["step_id"])
        action_type = str(payload.get("type") or "")
        if step_id == "move" and action_type == "move":
            self._tutorial_advance("move")
            if self.tutorial_state["first_effective_action_at"] is None:
                self.tutorial_state["first_effective_action_at"] = time.time()
        elif step_id == "basic_attack" and action_type == "attack":
            self._tutorial_advance("basic_attack")
        elif step_id == "active_skill" and action_type == "skill":
            self._tutorial_advance("active_skill")
        elif step_id == "end_turn" and action_type == "end_turn":
            self._tutorial_advance("end_turn")
        elif step_id == "chain_response" and action_type in {"chain_react", "chain_skip"}:
            self._tutorial_advance("chain_response")
            if self.battle.winner is None:
                self.tutorial_checkpoint = copy.deepcopy(self.battle)
        if self.tutorial_state["step_id"] == "win_objective" and self.battle.winner == 1:
            completed = self.tutorial_state["completed_step_ids"]
            if "win_objective" not in completed:
                completed.append("win_objective")
            self.tutorial_state["completed_at"] = time.time()

    def retry_tutorial_checkpoint(self, token: str) -> None:
        with self._lock:
            self.require_seat(token)
            if self.tutorial_state is None or self.tutorial_checkpoint is None:
                raise RoomError("当前教学阶段还没有可恢复的检查点。")
            self.battle = copy.deepcopy(self.tutorial_checkpoint)
            self.battle.winner = None
            self.status = "battle"
            self.tutorial_state["step_id"] = "win_objective"
            self.tutorial_state["completed_at"] = None
            self.tutorial_state["retry_count"] += 1
            self.replay = ReplayRecorder(self.room_id, self.mode, match_id=self.current_match_id)
            self._record_replay_step("tutorial_checkpoint_retry")
            self.touch()

    def occupied_seat_count(self) -> int:
        return sum(1 for seat in self.seats.values() if seat.occupied)

    def human_seat_count(self) -> int:
        return sum(1 for seat in self.seats.values() if seat.is_human)

    def ai_seat_count(self) -> int:
        return sum(1 for seat in self.seats.values() if seat.is_ai)

    def seat_for_token(self, token: Optional[str]) -> Optional[PlayerSeat]:
        if not token:
            return None
        for seat in self.seats.values():
            if seat.is_human and seat.token == token:
                seat.mark_seen()
                return seat
        return None

    def require_seat(self, token: Optional[str]) -> PlayerSeat:
        seat = self.seat_for_token(token)
        if seat is None:
            raise RoomError("当前房间身份无效，请重新加入房间。")
        return seat

    def require_host(self, token: Optional[str]) -> PlayerSeat:
        seat = self.require_seat(token)
        if seat.player_id != self.host_player_id:
            raise RoomError("只有房主可以执行这个操作。")
        return seat

    def open_seat(self) -> Optional[PlayerSeat]:
        for player_id in sorted(self.seats):
            seat = self.seats[player_id]
            if seat.can_join:
                return seat
        return None

    def seat_for_name(self, player_name: str) -> Optional[PlayerSeat]:
        matches = [seat for seat in self.seats.values() if seat.matches_name(player_name)]
        if len(matches) > 1:
            raise RoomError("房间内存在同名玩家，无法仅凭昵称恢复席位。")
        return matches[0] if matches else None

    def _first_human_player_id(self) -> Optional[int]:
        for player_id in sorted(self.seats):
            if self.seats[player_id].is_human:
                return player_id
        return None

    def _seat(self, player_id: Any) -> PlayerSeat:
        normalized = int(player_id)
        seat = self.seats.get(normalized)
        if seat is None:
            raise RoomError(f"席位 {normalized} 不存在。")
        return seat

    def _team_seats(self, team_id: int) -> list[PlayerSeat]:
        normalized_team = normalize_team_id(team_id)
        return [seat for seat in sorted(self.seats.values(), key=lambda item: item.player_id) if seat.team_id == normalized_team]

    def _team_quota_sum(self, team_id: int) -> int:
        return sum(seat.random_quota for seat in self._team_seats(team_id))

    def _reset_random_quotas_to_defaults(self) -> None:
        for seat in self.seats.values():
            seat.random_quota = 0
        for team_id in TEAM_IDS:
            team_seats = self._team_seats(team_id)
            if team_seats:
                team_seats[0].random_quota = self.random_roster_size

    def _seat_has_owned_hero_presence(self, seat: PlayerSeat) -> bool:
        if self.battle is None:
            return False
        return any(
            getattr(unit, "owner_seat_id", None) == seat.player_id and not unit.is_summon
            for unit in self.battle.all_units()
        )

    def _has_human_ai_takeover(self) -> bool:
        return any(seat.is_human and bool(getattr(seat, "ai_takeover", False)) for seat in self.seats.values())

    def _has_interactive_human_presence(self) -> bool:
        return any(
            seat.is_human
            and not bool(getattr(seat, "ai_takeover", False))
            and self._seat_has_owned_hero_presence(seat)
            for seat in self.seats.values()
        )

    def _simulation_enabled(self) -> bool:
        return self.battle is not None and self.battle.winner is None and not self._has_interactive_human_presence()

    def _simulation_interval_seconds(self) -> float:
        return max(0.1, 0.9 / max(self.simulation_speed, 0.1))

    def _scaled_simulation_delay(self, seconds: float) -> float:
        return max(0.05, float(seconds) / max(self.simulation_speed, 0.1))

    def _action_display_name(self, actor: Optional[Unit], payload: dict[str, Any]) -> str:
        action_type = str(payload.get("type") or "")
        if action_type == "move":
            return "移动"
        if action_type == "attack":
            return "普攻"
        if action_type == "skill" and actor is not None:
            try:
                return actor.get_skill(str(payload.get("skill_code") or "")).name
            except ActionError:
                return str(payload.get("skill_code") or "技能")
        if action_type == "chain_react" and actor is not None:
            action_code = str(payload.get("action_code") or "")
            if action_code == "block":
                return "格挡"
            if action_code == "counter":
                return "反击"
            try:
                return actor.get_skill(action_code).name
            except ActionError:
                return action_code or "连锁"
        if action_type == "respawn_select":
            return "重新出现"
        if action_type == "end_turn":
            return "结束回合"
        if action_type == "chain_skip":
            return "不连锁"
        return action_type or "动作"

    def _build_last_action_meta(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        actor: Optional[Unit],
        log_lines: list[str],
    ) -> dict[str, Any]:
        self.last_action_id += 1
        path = payload.get("path") if isinstance(payload.get("path"), list) else []
        cells = payload.get("cells") if isinstance(payload.get("cells"), list) else []
        target_unit_ids = payload.get("target_unit_ids") if isinstance(payload.get("target_unit_ids"), list) else []
        target_unit_id = payload.get("target_unit_id")
        if target_unit_id:
            target_unit_ids = [*target_unit_ids, str(target_unit_id)]
        point_cells: list[dict[str, int]] = []
        if payload.get("x") is not None and payload.get("y") is not None:
            point_cells.append({"x": int(payload["x"]), "y": int(payload["y"])})
        point_cells.extend(
            {"x": int(cell["x"]), "y": int(cell["y"])}
            for cell in cells
            if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None
        )
        return {
            "id": self.last_action_id,
            "reason": reason,
            "action_type": str(payload.get("type") or ""),
            "display_name": self._action_display_name(actor, payload),
            "actor_id": actor.unit_id if actor is not None else str(payload.get("unit_id") or ""),
            "actor_name": actor.name if actor is not None else "",
            "actor_player_id": actor.player_id if actor is not None else None,
            "actor_is_ai": self._actor_is_ai_controlled(actor),
            "path": [
                {"x": int(cell["x"]), "y": int(cell["y"])}
                for cell in path
                if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None
            ],
            "cells": point_cells,
            "target_unit_ids": [str(unit_id) for unit_id in target_unit_ids],
            "log_lines": [str(line) for line in log_lines if str(line).strip()],
        }

    def _visible_last_action_for_viewer(self, viewer: Optional[PlayerSeat]) -> Optional[dict[str, Any]]:
        if self.last_action_meta is None or self.battle is None:
            return None
        payload = dict(self.last_action_meta)
        actor_id = str(payload.get("actor_id") or "")
        if actor_id:
            try:
                actor = self.battle.get_unit(actor_id)
            except Exception:
                actor = None
            if actor is not None and actor.has_status("隐身") and (viewer is None or viewer.team_id != actor.player_id):
                return None
        hidden_unit_ids = {
            unit.unit_id
            for unit in self.battle.all_units()
            if unit.has_status("隐身") and (viewer is None or viewer.team_id != unit.player_id)
        }
        payload["target_unit_ids"] = [
            str(unit_id)
            for unit_id in payload.get("target_unit_ids", [])
            if str(unit_id) not in hidden_unit_ids
        ]
        return payload

    def _build_pending_action_meta(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        actor: Optional[Unit],
    ) -> dict[str, Any]:
        path = payload.get("path") if isinstance(payload.get("path"), list) else []
        cells = payload.get("cells") if isinstance(payload.get("cells"), list) else []
        target_unit_ids = payload.get("target_unit_ids") if isinstance(payload.get("target_unit_ids"), list) else []
        target_unit_id = payload.get("target_unit_id")
        if target_unit_id:
            target_unit_ids = [*target_unit_ids, str(target_unit_id)]
        point_cells: list[dict[str, int]] = []
        if payload.get("x") is not None and payload.get("y") is not None:
            point_cells.append({"x": int(payload["x"]), "y": int(payload["y"])})
        point_cells.extend(
            {"x": int(cell["x"]), "y": int(cell["y"])}
            for cell in cells
            if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None
        )
        return {
            "id": self.last_action_id + 1,
            "reason": reason,
            "action_type": str(payload.get("type") or ""),
            "display_name": self._action_display_name(actor, payload),
            "actor_id": actor.unit_id if actor is not None else str(payload.get("unit_id") or ""),
            "actor_name": actor.name if actor is not None else "",
            "actor_player_id": actor.player_id if actor is not None else None,
            "actor_is_ai": self._actor_is_ai_controlled(actor),
            "path": [
                {"x": int(cell["x"]), "y": int(cell["y"])}
                for cell in path
                if isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None
            ],
            "cells": point_cells,
            "target_unit_ids": [str(unit_id) for unit_id in target_unit_ids],
            "visible_count": 0,
            "phase": "announce",
            "payload": dict(payload),
            "next_due_at": time.time() + self._scaled_simulation_delay(SIMULATION_ACTION_ANNOUNCE_SECONDS),
        }

    def _visible_pending_action_for_viewer(self, viewer: Optional[PlayerSeat]) -> Optional[dict[str, Any]]:
        if self.pending_simulation_action is None or self.battle is None:
            return None
        payload = {
            key: value
            for key, value in self.pending_simulation_action.items()
            if key not in {"payload", "next_due_at"}
        }
        actor_id = str(payload.get("actor_id") or "")
        if actor_id:
            try:
                actor = self.battle.get_unit(actor_id)
            except Exception:
                actor = None
            if actor is not None and actor.has_status("éšèº«") and (viewer is None or viewer.team_id != actor.player_id):
                return None
        hidden_unit_ids = {
            unit.unit_id
            for unit in self.battle.all_units()
            if unit.has_status("éšèº«") and (viewer is None or viewer.team_id != unit.player_id)
        }
        payload["target_unit_ids"] = [
            str(unit_id)
            for unit_id in payload.get("target_unit_ids", [])
            if str(unit_id) not in hidden_unit_ids
        ]
        return payload

    def _sync_army_ai_players(self) -> None:
        if self.battle is None:
            return
        self.battle.army_ai_players = {
            int(seat.team_id) for seat in self.seats.values() if seat.is_ai_controlled
        }
        self.battle.army_ai_styles = {
            int(seat.team_id): getattr(seat, "army_ai_style", DEFAULT_ARMY_AI_STYLE)
            for seat in self.seats.values()
            if seat.is_ai_controlled
        }
        self.battle.hero_ai_styles = {
            int(seat.team_id): getattr(seat, "hero_ai_style", DEFAULT_HERO_AI_STYLE)
            for seat in self.seats.values()
            if seat.is_ai_controlled
        }

    def _bind_replay_checkpoint(self) -> None:
        if self.battle is None:
            return
        self.battle.on_replay_checkpoint = self._record_replay_step

    def _record_replay_step(self, reason: str) -> None:
        if self.battle is None:
            return
        if bool(getattr(self, "fast_ai_simulation", False)) and reason not in {
            "battle_start",
            "surrender",
            "turn_end",
            "match_end",
        }:
            return
        if self.replay is None:
            self.replay = ReplayRecorder(self.room_id, self.mode, match_id=self.current_match_id)
        omniscient_battle = self.battle.to_public_dict()
        spectator_battle = battle_state_for_viewer(self.battle, None, None)
        seat_views = {
            str(seat.player_id): battle_state_for_viewer(self.battle, seat.team_id, seat.player_id)
            for seat in self.seats.values()
            if seat.occupied
        }
        if (
            reason == "match_end"
            and self.replay.steps
            and not any(unit.get("position") for unit in (omniscient_battle.get("units") or []))
        ):
            previous = self.replay.steps[-1]
            omniscient_battle = dict(omniscient_battle)
            omniscient_battle["units"] = list(previous.omniscient_battle.get("units") or [])
            if not omniscient_battle.get("destroyed_units"):
                omniscient_battle["destroyed_units"] = list(previous.omniscient_battle.get("destroyed_units") or [])
            spectator_battle = dict(previous.spectator_battle)
            spectator_battle["winner"] = omniscient_battle.get("winner")
            spectator_battle["win_reason_text"] = omniscient_battle.get("win_reason_text")
            spectator_battle["win_reason_code"] = omniscient_battle.get("win_reason_code")
            seat_views = {
                key: {**value, "winner": omniscient_battle.get("winner")}
                for key, value in previous.seat_views.items()
            }
        self.replay.append_step(
            reason=reason,
            omniscient_battle=omniscient_battle,
            spectator_battle=spectator_battle,
            seat_views=seat_views,
        )

    def _ensure_replay_saved(self) -> None:
        if self.battle is None or self.battle.winner is None or self.replay is None:
            return
        self.replay.finish_and_save(room_summary=self.serialize_summary())

    def _next_ai_planned_action(self) -> Optional[tuple[dict[str, Any], str, Optional[Unit]]]:
        if self.battle is None or self.battle.winner is not None:
            return None
        try:
            if self.battle.current_respawn_prompt() is not None:
                seat = self._current_prompt_seat()
                if seat is None or not seat.is_ai_controlled:
                    return None
                prompt = self.battle.current_respawn_prompt()
                if prompt is None:
                    return None
                unit = self.battle.get_unit(prompt.unit_id)
                options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
                if not options:
                    return None
                difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                payload = choose_respawn_action(self.battle, unit, options, difficulty)
                return (payload, "ai_respawn", unit) if payload is not None else None
            if self.battle.pending_chain is not None:
                seat = self._current_prompt_seat()
                if seat is None or not seat.is_ai_controlled:
                    return None
                current_unit_id = self.battle.pending_chain.current_unit_id()
                if not current_unit_id:
                    return None
                reactor = self.battle.get_unit(current_unit_id)
                options = self.battle.reaction_snapshot_for(reactor).get("actions", [])
                difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                payload = choose_chain_reaction(self.battle, reactor, options, difficulty)
                return (payload or {"type": "chain_skip"}, "ai_chain", reactor)
            instant_payload = self._choose_ai_instant_payload()
            if instant_payload is not None:
                actor = self.battle.get_unit(str(instant_payload.get("unit_id") or ""))
                return instant_payload, "ai_instant", actor
            if self.battle.is_army_turn():
                return {"type": "end_turn"}, "army_turn", None
            seat = self._current_prompt_seat()
            if seat is None or not seat.is_ai_controlled:
                return None
            current_unit = self.battle.current_turn_unit()
            if current_unit is None:
                return None
            difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
            payload, actor = choose_turn_bundle_action(
                self.battle,
                self.battle.current_turn_bundle_units(include_banished=False),
                difficulty,
            )
            return payload, "ai_turn", actor or current_unit
        except ActionError:
            seat = self._current_prompt_seat()
            if seat is None or not seat.is_ai_controlled or self.battle is None:
                return None
            if self.battle.pending_chain is not None:
                current_unit_id = self.battle.pending_chain.current_unit_id()
                reactor = self.battle.get_unit(current_unit_id) if current_unit_id else None
                return {"type": "chain_skip"}, "ai_chain_fallback", reactor
            if self.battle.current_respawn_prompt() is not None:
                prompt = self.battle.current_respawn_prompt()
                if prompt is None:
                    return None
                unit = self.battle.get_unit(prompt.unit_id)
                options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
                if not options:
                    return None
                fallback = options[0]
                return (
                    {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y},
                    "ai_respawn_fallback",
                    unit,
                )
            actor = self.battle.current_turn_unit()
            return {"type": "end_turn"}, "ai_turn_fallback", actor

    def _prepare_pending_simulation_action(self) -> bool:
        planned = self._next_ai_planned_action()
        if planned is None:
            return False
        payload, reason, actor = planned
        self.pending_simulation_action = self._build_pending_action_meta(payload, reason=reason, actor=actor)
        self.touch()
        return True

    def _advance_pending_simulation_action(self, *, ignore_due: bool = False) -> bool:
        pending = self.pending_simulation_action
        if pending is None:
            return False
        if not ignore_due and time.time() < float(pending.get("next_due_at") or 0):
            return False
        preview_cells = list(pending.get("path") or pending.get("cells") or [])
        visible_count = max(0, int(pending.get("visible_count") or 0))
        if visible_count < len(preview_cells):
            using_path = bool(pending.get("path"))
            step, delay = pending_preview_select_step(
                using_path=using_path,
                remaining=len(preview_cells) - visible_count,
            )
            visible_count += max(1, step)
            pending["visible_count"] = visible_count
            pending["phase"] = "selecting" if visible_count < len(preview_cells) else "confirm"
            pending["next_due_at"] = time.time() + self._scaled_simulation_delay(delay)
            self.touch()
            return True
        payload = dict(pending.get("payload") or {})
        reason = str(pending.get("reason") or "ai_action")
        self.pending_simulation_action = None
        try:
            self._perform_battle_action(payload, reason=reason)
        except ActionError as exc:
            self._record_failed_ai_action(payload, reason=reason, error=exc)
            raise
        self.touch()
        return True

    def _record_failed_ai_action(self, payload: dict[str, Any], *, reason: str, error: ActionError) -> None:
        if self.battle is None:
            return
        actor = None
        actor_unit_id = payload.get("unit_id")
        if actor_unit_id:
            try:
                actor = self.battle.get_unit(str(actor_unit_id))
            except Exception:
                actor = None
        self.last_action_meta = self._build_last_action_meta(
            payload,
            reason=f"{reason}_failed",
            actor=actor,
            log_lines=[f"AI 行动未能执行：{error}"],
        )
        self._record_replay_step(f"{reason}_failed")
        self.touch()

    def _perform_ai_fallback_after_error(self) -> bool:
        if self.battle is None:
            return False
        seat = self._current_prompt_seat()
        if self.battle.pending_chain is not None:
            if seat is None or not seat.is_ai_controlled:
                return False
            self._perform_battle_action({"type": "chain_skip"}, reason="ai_chain_fallback")
            return True
        if self.battle.current_respawn_prompt() is not None:
            if seat is None or not seat.is_ai_controlled:
                return False
            prompt = self.battle.current_respawn_prompt()
            if prompt is None:
                return False
            unit = self.battle.get_unit(prompt.unit_id)
            options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
            if not options:
                return False
            fallback = options[0]
            self._perform_battle_action(
                {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y},
                reason="ai_respawn_fallback",
            )
            return True
        current_unit = self.battle.current_turn_unit()
        if current_unit is None or not self.battle.units_can_act_in_current_turn():
            self._perform_battle_action({"type": "end_turn"}, reason="ai_turn_fallback")
            return True
        if seat is None or not seat.is_ai_controlled:
            return False
        self._perform_battle_action({"type": "end_turn"}, reason="ai_turn_fallback")
        return True

    def _recover_stalled_inactive_turn(self) -> bool:
        if self.battle is None or self.battle.winner is not None:
            return False
        if self.battle.pending_chain is not None or self.battle.current_respawn_prompt() is not None:
            return False
        if self.battle.is_army_turn():
            self._perform_battle_action({"type": "end_turn"}, reason="army_turn")
            return True
        current_unit = self.battle.current_turn_unit()
        if current_unit is not None and self.battle.units_can_act_in_current_turn():
            return False
        self._perform_battle_action({"type": "end_turn"}, reason="ai_turn_fallback")
        return True

    def _perform_battle_action(self, payload: dict[str, Any], *, reason: str) -> None:
        if self.battle is None:
            raise RoomError("å½“å‰æˆ¿é—´è¿˜æ²¡æœ‰å¼€å§‹å¯¹å±€ã€‚")
        actor: Optional[Unit] = None
        actor_unit_id = payload.get("unit_id")
        if actor_unit_id:
            try:
                actor = self.battle.get_unit(str(actor_unit_id))
            except Exception:
                actor = None
        elif payload.get("type") == "chain_skip" and self.battle.pending_chain is not None:
            current_unit_id = self.battle.pending_chain.current_unit_id()
            if current_unit_id:
                actor = self.battle.get_unit(current_unit_id)
        before_log_count = len(self.battle.logs)
        before_stale_count = int(getattr(self.battle, "stale_queued_action_count", 0))
        self.battle.perform_action(payload)
        self.battle.record_postgame_action(actor, payload)
        new_logs = self.battle.logs[before_log_count:]
        self.last_action_meta = self._build_last_action_meta(
            payload,
            reason=reason,
            actor=actor,
            log_lines=new_logs,
        )
        self._record_replay_step(reason)
        if self.battle.winner is not None:
            self.status = "finished"
            self._ensure_replay_saved()
        after_stale_count = int(getattr(self.battle, "stale_queued_action_count", 0))
        stale_queued_action = after_stale_count > before_stale_count or any(
            STALE_QUEUED_ACTOR_LOG_MARKER in line for line in new_logs
        )
        if reason.startswith("ai_") and stale_queued_action:
            raise ActionError("AI action actor is no longer present.")

    def _advance_simulation_due(self, *, force_steps: Optional[int] = None) -> int:
        if self.battle is None or self.battle.winner is not None:
            return 0
        if self.simulation_paused and force_steps is None and self._simulation_enabled():
            return 0
        step_budget = max(0, int(force_steps)) if force_steps is not None else 4
        steps = 0
        safety = 0
        while self.battle is not None and self.battle.winner is None and safety < 64:
            if force_steps is not None and steps >= step_budget:
                break
            if self.pending_simulation_action is not None:
                if force_steps is None and time.time() < float(self.pending_simulation_action.get("next_due_at") or 0):
                    break
                try:
                    if not self._advance_pending_simulation_action(ignore_due=force_steps is not None):
                        break
                except ActionError:
                    if not self._perform_ai_fallback_after_error():
                        break
                steps += 1
            else:
                if self._recover_stalled_inactive_turn():
                    steps += 1
                    safety += 1
                    continue
                if not self._prepare_pending_simulation_action():
                    break
                steps += 1
            safety += 1
            if force_steps is None and self.pending_simulation_action is not None:
                break
        if steps > 0:
            self.simulation_last_advanced_at = time.time()
        self.status = "finished" if self.battle and self.battle.winner is not None else "battle"
        return steps

    def create_host(self, player_name: str, *, account_user_id: Optional[int] = None) -> tuple[int, str]:
        with self._lock:
            seat = self.seats[self.host_player_id]
            token = seat.claim(player_name, account_user_id=account_user_id)
            self.invalidate_readiness()
            self.touch()
            return seat.player_id, token

    def join(self, player_name: str, *, account_user_id: Optional[int] = None) -> tuple[int, str]:
        with self._lock:
            if self.status != "lobby":
                existing = self.seat_for_name(player_name)
                if existing is not None:
                    token = existing.reclaim(player_name, account_user_id=account_user_id)
                    self.touch()
                    return existing.player_id, token
                raise RoomError("对局已经开始，只能用原来的昵称恢复原席位。")
            seat = self.open_seat()
            if seat is None:
                existing = self.seat_for_name(player_name)
                if existing is not None:
                    token = existing.reclaim(player_name, account_user_id=account_user_id)
                    self.touch()
                    return existing.player_id, token
                raise RoomError("房间已经满员。")
            token = seat.claim(player_name, account_user_id=account_user_id)
            self.invalidate_readiness()
            self.touch()
            return seat.player_id, token

    def set_seat_count(self, token: str, seat_count: Any) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整席位数。")
            next_count = normalize_room_seat_count(seat_count)
            current_count = len(self.seats)
            if next_count == current_count:
                return
            if next_count < current_count:
                for player_id in range(next_count + 1, current_count + 1):
                    if self.seats[player_id].occupied:
                        raise RoomError(f"席位 {player_id} 仍被占用，不能直接缩减房间席位数。")
                for player_id in range(current_count, next_count, -1):
                    self.seats.pop(player_id, None)
            else:
                for player_id in range(current_count + 1, next_count + 1):
                    self.seats[player_id] = PlayerSeat(
                        player_id=player_id,
                        team_id=default_team_for_seat(player_id),
                    )
            if self.mode == "random":
                self._reset_random_quotas_to_defaults()
            self.invalidate_readiness()
            self.touch()

    def set_seat_team(self, token: str, seat_id: Any, team_id: Any) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整席位队伍。")
            seat = self._seat(seat_id)
            next_team = normalize_team_id(team_id)
            if next_team == seat.team_id:
                return
            seat.team_id = next_team
            self.invalidate_readiness()
            self.touch()

    def set_seat_controller(self, token: str, seat_id: Any, controller_type: str) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整席位状态。")
            seat = self._seat(seat_id)
            next_controller = normalize_controller_type(controller_type)
            if seat.player_id == self.host_player_id and next_controller != "human":
                raise RoomError("不能把房主席位改成开放或 AI。")
            if next_controller == seat.controller_type:
                return
            if next_controller == "human":
                if seat.is_human:
                    return
                raise RoomError("真人席位需要由玩家自己加入。")
            if next_controller == "open":
                if seat.is_human:
                    raise RoomError("已有真人加入的席位不能由房主直接清空。")
                seat.set_open()
                self.invalidate_readiness()
                self.touch()
                return
            if seat.is_human:
                raise RoomError("已有真人加入的席位不能直接改成 AI。")
            seat.set_ai()
            self.invalidate_readiness()
            self.touch()

    def set_default_ai_difficulty(self, token: str, difficulty: Any) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整 AI 默认难度。")
            next_difficulty = normalize_ai_difficulty(difficulty)
            if next_difficulty == self.default_ai_difficulty:
                return
            self.default_ai_difficulty = next_difficulty
            self.invalidate_readiness()
            self.touch()

    def set_seat_ai_difficulty(self, token: str, seat_id: Any, difficulty: Any) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能调整席位 AI 难度。")
            seat = self._seat(seat_id)
            if not seat.is_ai:
                raise RoomError("只有 AI 席位才能单独设置 AI 难度。")
            next_difficulty = normalize_ai_difficulty(difficulty)
            if next_difficulty == seat.ai_difficulty_override:
                return
            seat.ai_difficulty_override = next_difficulty
            self.invalidate_readiness()
            self.touch()

    def set_mode(self, token: str, mode: str) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能切换房间模式。")
            next_mode = normalize_room_mode(mode)
            if next_mode == self.mode:
                return
            self.mode = next_mode
            for seat in self.seats.values():
                seat.clear_roster()
            if self.mode == "random":
                self._reset_random_quotas_to_defaults()
            self.invalidate_readiness()
            self.touch()

    def set_random_roster_size(self, token: str, roster_size: Any) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置随机模式的人数 n。")
            if self.mode != "random":
                raise RoomError("只有随机模式才能设置人数 n。")
            next_size = validate_random_roster_size_for_catalog(roster_size)
            if next_size == self.random_roster_size:
                return
            self.random_roster_size = next_size
            self._reset_random_quotas_to_defaults()
            self.invalidate_readiness()
            self.touch()

    def set_random_quota(self, token: str, seat_id: Any, quota: Any) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置随机配额。")
            if self.mode != "random":
                raise RoomError("只有随机模式才需要设置随机配额。")
            seat = self._seat(seat_id)
            next_quota = normalize_random_quota(quota)
            if next_quota == seat.random_quota:
                return
            seat.random_quota = next_quota
            self.invalidate_readiness()
            self.touch()

    def set_board_size(self, token: str, width: Any, height: Any) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置战场大小。")
            next_width = normalize_board_axis(width, label="战场宽度")
            next_height = normalize_board_axis(height, label="战场高度")
            if next_width == self.board_width and next_height == self.board_height:
                return
            self.board_width = next_width
            self.board_height = next_height
            self.touch()

    def auto_configure(
        self,
        token: str,
        *,
        method: Any = "count",
        count: Any = AUTO_CONFIGURE_COUNT_DEFAULT,
        points: Any = AUTO_CONFIGURE_POINTS_DEFAULT,
        allow_duplicates: bool = False,
    ) -> None:
        normalized_method = normalize_auto_configure_method(method)
        normalized_count = AUTO_CONFIGURE_COUNT_DEFAULT
        normalized_points = AUTO_CONFIGURE_POINTS_DEFAULT
        if normalized_method == "count":
            normalized_count = normalize_auto_configure_count(count)
        else:
            normalized_points = normalize_auto_configure_points(points)
        allow_repeat = bool(allow_duplicates)
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能自动配置。")
            for seat in self.seats.values():
                if not seat.is_human and seat.controller_type != "ai":
                    seat.set_ai()
            if self.mode != "random":
                rosters = [
                    pick_auto_configure_roster(
                        method=normalized_method,
                        count=normalized_count,
                        points=normalized_points,
                        allow_duplicates=allow_repeat,
                    )
                    for _ in self.seats
                ]
                needed = max((len(roster) for roster in rosters), default=0)
                if self.hero_limit > 0 and needed > self.hero_limit:
                    self.hero_limit = needed
                for seat, roster in zip(self.seats.values(), rosters):
                    if seat.is_human and seat.ready:
                        seat.ready = False
                    seat.replace_roster(roster)
            self.invalidate_readiness()
            self.touch()

    def set_turn_timeout(self, token: str, seconds: Any) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置回合时限。")
            next_timeout = normalize_turn_timeout(seconds)
            if next_timeout == self.turn_timeout_seconds:
                return
            self.turn_timeout_seconds = next_timeout
            self.touch()

    def set_hero_limit(self, token: str, hero_limit: Any) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "lobby":
                raise RoomError("只有在大厅中才能设置武将数量上限。")
            next_limit = normalize_room_hero_limit(hero_limit)
            if next_limit == self.hero_limit:
                return
            self.hero_limit = next_limit
            if next_limit > 0:
                for seat in self.seats.values():
                    seat.trim_roster_from_end(next_limit)
            self.invalidate_readiness()
            self.touch()

    def _editable_seat(self, token: str, seat_id: Any | None) -> PlayerSeat:
        viewer = self.require_seat(token)
        if seat_id in {None, "", viewer.player_id}:
            return viewer
        target = self._seat(seat_id)
        if viewer.player_id != self.host_player_id:
            raise RoomError("只能编辑自己控制的席位。")
        if not target.is_ai:
            raise RoomError("房主当前只能代为配置 AI 席位。")
        return target

    def select_hero(self, token: str, hero_code: str, delta: int = 1, seat_id: Any | None = None) -> None:
        with self._lock:
            if self.status != "lobby":
                raise RoomError("对局已经开始，不能再更改武将。")
            if self.mode == "random":
                raise RoomError("随机选人模式下不需要手动选将。")
            seat = self._editable_seat(token, seat_id)
            if seat.is_human and seat.ready:
                raise RoomError("准备之后不能再改阵容，请先取消准备。")
            if hero_code not in hero_lookup():
                raise RoomError("所选武将不存在。")
            next_delta = normalize_hero_delta(delta)
            if next_delta > 0 and self.hero_limit > 0 and seat.hero_total_count + next_delta > self.hero_limit:
                raise RoomError(f"每个席位最多选择 {self.hero_limit} 名武将。")
            seat.adjust_hero_count(hero_code, next_delta)
            self.invalidate_readiness()
            self.touch()

    def set_roster(self, token: str, hero_codes: list[str], seat_id: Any | None = None) -> None:
        with self._lock:
            if self.status != "lobby":
                raise RoomError("对局已经开始，不能再更改武将。")
            if self.mode == "random":
                raise RoomError("随机选人模式下不需要手动选将。")
            seat = self._editable_seat(token, seat_id)
            if seat.is_human and seat.ready:
                raise RoomError("准备之后不能再改阵容，请先取消准备。")
            known = hero_lookup()
            normalized = [str(code or "").strip() for code in hero_codes]
            if not normalized or any(code not in known for code in normalized):
                raise RoomError("推荐阵容包含不存在的武将。")
            if self.hero_limit > 0 and len(normalized) > self.hero_limit:
                raise RoomError(f"每个席位最多选择 {self.hero_limit} 名武将。")
            seat.hero_counts.clear()
            for code in normalized:
                seat.hero_counts[code] = seat.hero_counts.get(code, 0) + 1
            self.invalidate_readiness()
            self.touch()

    def can_start(self) -> bool:
        return self._start_blocker() is None

    def set_ready(self, token: str, ready: bool) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if self.status != "lobby":
                raise RoomError("只有在房间大厅中才能确认准备。")
            next_ready = bool(ready)
            if next_ready:
                blocker = self._configuration_blocker()
                if blocker is not None:
                    raise RoomError(blocker)
            if seat.ready == next_ready:
                return
            seat.ready = next_ready
            self.touch()

    def human_ready_count(self) -> int:
        return sum(1 for seat in self.seats.values() if seat.is_human and seat.ready)

    def _team_random_rosters(self) -> dict[int, dict[int, list[str]]]:
        team1_roster, team2_roster = random_room_hero_codes(self.random_roster_size)
        rosters_by_team = {1: team1_roster, 2: team2_roster}
        assignments: dict[int, dict[int, list[str]]] = {1: {}, 2: {}}
        for team_id in TEAM_IDS:
            offset = 0
            seats = self._team_seats(team_id)
            for seat in seats:
                quota = seat.random_quota
                assignments[team_id][seat.player_id] = list(rosters_by_team[team_id][offset : offset + quota])
                offset += quota
            if offset != len(rosters_by_team[team_id]):
                raise RoomError(f"{team_name(team_id)} 的随机配额总数与 n 不一致。")
        return assignments

    def _battle_entries_for_team(self, team_id: int) -> list[RoomBattleEntry]:
        entries: list[RoomBattleEntry] = []
        for seat in self._team_seats(team_id):
            for hero_code in seat.expanded_roster():
                entries.append(
                    RoomBattleEntry(
                        hero_code=hero_code,
                        player_id=team_id,
                        owner_seat_id=seat.player_id,
                    )
                )
            for hero_code in army_codes_from_counts(seat.army_counts):
                entries.append(
                    RoomBattleEntry(
                        hero_code=hero_code,
                        player_id=team_id,
                        owner_seat_id=seat.player_id,
                    )
                )
        return entries

    def set_army_composition(self, token: str, composition: Any, seat_id: Any | None = None) -> None:
        with self._lock:
            if self.status != "lobby":
                raise RoomError("对局已经开始，不能再增减士兵。")
            seat = self._editable_seat(token, seat_id)
            if seat.is_human and seat.ready:
                raise RoomError("准备之后不能再改士兵，请先取消准备。")
            try:
                seat.army_counts = normalize_army_counts(composition)
            except ValueError as exc:
                raise RoomError(str(exc)) from exc
            self.invalidate_readiness()
            self.touch()

    def set_army_order(
        self,
        token: str,
        order: Any,
        direction: Any = None,
        team_id: Any | None = None,
        kind: Any | None = None,
        stride: Any | None = None,
        ammo: Any | None = None,
    ) -> dict[str, str]:
        with self._lock:
            from wujiang.tactical.engine.army import (
                apply_army_order,
                command_for_kind,
                normalize_army_kind,
                present_army_kinds,
            )

            seat = self.require_seat(token)
            try:
                next_team = 1 if int(team_id or seat.team_id) != 2 else 2
            except (TypeError, ValueError) as exc:
                raise RoomError("队伍编号无效。") from exc
            if next_team != seat.team_id and seat.player_id != self.host_player_id:
                raise RoomError("只能设置己方军队指令。")
            if self.status == "lobby":
                has_army = any(
                    sum(int(item.army_counts.get(item_kind, 0) or 0) for item_kind in ARMY_KIND_LABELS) > 0
                    for item in self._team_seats(next_team)
                )
                present_kinds = [
                    item_kind
                    for item_kind in ARMY_KIND_LABELS
                    if any(int(item.army_counts.get(item_kind, 0) or 0) > 0 for item in self._team_seats(next_team))
                ]
            else:
                has_army = self.battle is not None and bool(living_army_units(self.battle, next_team))
                present_kinds = present_army_kinds(self.battle, next_team) if self.battle is not None else []
            if not has_army:
                raise RoomError("这一方还没有士兵，不能设置军队指令。")
            if (
                self.battle is not None
                and bool(getattr(seat, "ai_takeover", False))
                and next_team == seat.team_id
            ):
                raise RoomError("当前已交给 AI 接管，请先停止接管再调整军队指令。")
            try:
                target_kind = None if kind in {None, ""} else normalize_army_kind(kind)
                previous = command_for_kind(self.army_orders_by_team, next_team, target_kind or "infantry")
                command = normalize_army_command(
                    order,
                    direction,
                    player_id=next_team,
                    previous=previous,
                    stride=stride,
                    ammo=ammo,
                )
            except ValueError as exc:
                raise RoomError(str(exc)) from exc
            apply_army_order(
                self.army_orders_by_team,
                next_team,
                command,
                kind=target_kind,
                kinds=present_kinds or None,
            )
            if self.battle is not None:
                self.battle.set_army_order(
                    next_team,
                    command["order"],
                    command["direction"],
                    kind=target_kind,
                    stride=command.get("stride"),
                    ammo=command.get("ammo"),
                )
            self.touch()
            return dict(command)

    def leave(self, token: str) -> int:
        with self._lock:
            seat = self.require_seat(token)
            if self.status == "battle":
                self.surrender(token)
            leaving_player_id = seat.player_id
            seat.release()
            if leaving_player_id == self.host_player_id:
                self.host_player_id = self._first_human_player_id() or 1
            self.invalidate_readiness()
            self.touch()
            return leaving_player_id

    def current_input_player_id(self) -> Optional[int]:
        if self.battle is None:
            return None
        return int(self.battle.to_public_dict()["input_player"])

    def _unit_owner_seat_id(self, unit: Unit | None) -> Optional[int]:
        if self.battle is None:
            return None
        return battle_unit_owner_seat_id(self.battle, unit)

    def _seat_for_actor(self, unit: Unit | None) -> Optional[PlayerSeat]:
        owner_seat_id = self._unit_owner_seat_id(unit)
        if owner_seat_id is None:
            return None
        return self.seats.get(owner_seat_id)

    def _actor_is_ai_controlled(self, actor: Optional[Unit]) -> bool:
        if actor is None:
            return False
        seat = self._seat_for_actor(actor)
        return bool(seat is not None and seat.is_ai_controlled)

    def _current_prompt_seat(self) -> Optional[PlayerSeat]:
        if self.battle is None:
            return None
        prompt = self.battle.current_respawn_prompt()
        if prompt is not None:
            return self.seats.get(self._unit_owner_seat_id(self.battle.get_unit(prompt.unit_id)) or -1)
        if self.battle.pending_chain is not None:
            current_unit_id = self.battle.pending_chain.current_unit_id()
            if current_unit_id:
                return self.seats.get(self._unit_owner_seat_id(self.battle.get_unit(current_unit_id)) or -1)
            return None
        current_unit = self.battle.current_turn_unit()
        if current_unit is None:
            return None
        return self.seats.get(self._unit_owner_seat_id(current_unit) or -1)

    def _current_turn_prompt(self) -> tuple[Optional[str], Optional[str], Optional[PlayerSeat]]:
        if self.battle is None or self.battle.winner is not None:
            return None, None, None
        seat = self._current_prompt_seat()
        prompt = self.battle.current_respawn_prompt()
        if prompt is not None:
            return f"respawn:{prompt.unit_id}", "respawn", seat
        if self.battle.pending_chain is not None:
            current_unit_id = self.battle.pending_chain.current_unit_id()
            return (
                f"chain:{self.battle.completed_turns}:{current_unit_id or 'none'}",
                "chain",
                seat,
            )
        current_unit = self.battle.current_turn_unit()
        if current_unit is None:
            return None, None, seat
        return f"turn:{self.battle.completed_turns}:{current_unit.unit_id}", "turn", seat

    def _sync_turn_timer(self, *, now: Optional[float] = None) -> None:
        current_time = time.time() if now is None else now
        prompt_key, _prompt_kind, seat = self._current_turn_prompt()
        enabled = (
            self.status == "battle"
            and self.tutorial_state is None
            and int(getattr(self, "turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS) or 0) > 0
            and prompt_key is not None
            and seat is not None
            and seat.is_human
            and not bool(getattr(seat, "ai_takeover", False))
        )
        if not enabled:
            self.turn_prompt_key = None
            self.turn_prompt_started_at = None
            self.turn_deadline_at = None
            return
        if prompt_key != self.turn_prompt_key or self.turn_deadline_at is None:
            self.turn_prompt_key = prompt_key
            self.turn_prompt_started_at = current_time
            self.turn_deadline_at = current_time + float(self.turn_timeout_seconds)

    def _process_due_turn_timeout(self, *, now: Optional[float] = None) -> bool:
        current_time = time.time() if now is None else now
        self._sync_turn_timer(now=current_time)
        if self.turn_deadline_at is None or current_time < self.turn_deadline_at or self.battle is None:
            return False
        _prompt_key, prompt_kind, seat = self._current_turn_prompt()
        if seat is None or not seat.is_human or prompt_kind is None:
            self._sync_turn_timer(now=current_time)
            return False
        if prompt_kind == "chain":
            payload = {"type": "chain_skip"}
            action_label = "自动放弃连锁"
        elif prompt_kind == "respawn":
            prompt = self.battle.current_respawn_prompt()
            if prompt is None:
                return False
            unit = self.battle.get_unit(prompt.unit_id)
            options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
            if not options:
                return False
            fallback = options[0]
            payload = {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y}
            action_label = "自动选择复活位置"
        else:
            payload = {"type": "end_turn"}
            action_label = "自动结束回合"
        self.battle.log(f"{seat.name or f'席位 {seat.player_id}'} 操作超时，系统已{action_label}。")
        self._perform_battle_action(payload, reason="turn_timeout")
        self.last_turn_timeout = {
            "seat_id": seat.player_id,
            "player_name": seat.name or None,
            "prompt_kind": prompt_kind,
            "action": payload["type"],
            "occurred_at": current_time,
        }
        self.turn_prompt_key = None
        self.turn_prompt_started_at = None
        self.turn_deadline_at = None
        if self.battle.winner is None:
            self._advance_simulation_due(force_steps=1)
        self.status = "finished" if self.battle.winner is not None else "battle"
        self._sync_turn_timer(now=current_time)
        self.touch()
        return True

    def _turn_timer_public_state(self) -> dict[str, Any]:
        now = time.time()
        self._sync_turn_timer(now=now)
        _prompt_key, prompt_kind, seat = self._current_turn_prompt()
        remaining = None
        if self.turn_deadline_at is not None:
            remaining = max(0, int(self.turn_deadline_at - now + 0.999))
        return {
            "enabled": self.turn_deadline_at is not None,
            "duration_seconds": int(getattr(self, "turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS) or 0),
            "deadline_at": self.turn_deadline_at,
            "remaining_seconds": remaining,
            "prompt_kind": prompt_kind if self.turn_deadline_at is not None else None,
            "prompt_seat_id": seat.player_id if self.turn_deadline_at is not None and seat is not None else None,
            "last_timeout": dict(self.last_turn_timeout) if self.last_turn_timeout is not None else None,
            "server_now": now,
        }

    def allows_instant_action_override(self, seat: PlayerSeat, payload: dict[str, Any]) -> bool:
        if self.battle is None or self.battle.pending_chain is not None or self.battle.current_respawn_prompt() is not None:
            return False
        if payload.get("type") != "skill":
            return False
        actor_unit_id = payload.get("unit_id")
        skill_code = str(payload.get("skill_code") or "")
        if not actor_unit_id or not skill_code:
            return False
        actor = self.battle.get_unit(str(actor_unit_id))
        if actor.player_id != seat.team_id:
            return False
        if self._unit_owner_seat_id(actor) != seat.player_id:
            return False
        skill = actor.get_skill(skill_code)
        if skill.timing != "instant":
            return False
        ok, _ = skill.can_use(self.battle, actor, payload)
        return ok

    def _choose_ai_instant_payload(self) -> Optional[dict[str, Any]]:
        if (
            self.battle is None
            or self.battle.winner is not None
            or self.battle.pending_chain is not None
            or self.battle.current_respawn_prompt() is not None
        ):
            return None
        active_team = self.battle.active_player
        for seat in sorted(self.seats.values(), key=lambda item: item.player_id):
            if not seat.is_ai_controlled or seat.team_id == active_team:
                continue
            owned_units = [
                unit
                for unit in self.battle.instant_action_units_for_player(seat.team_id)
                if self._unit_owner_seat_id(unit) == seat.player_id
            ]
            if not owned_units:
                continue
            difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
            payload = choose_instant_action(self.battle, owned_units, difficulty)
            if payload is not None:
                return payload
        return None

    def invite_path(self) -> str:
        return f"/?room={self.room_id}"

    def invite_url(self, base_url: Optional[str]) -> str:
        if not base_url:
            return self.invite_path()
        return f"{base_url}{self.invite_path()}"

    def serialize_summary(self, *, base_url: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            heroes_by_code = hero_lookup()
            seats = [seat.to_public_dict(heroes_by_code, self.host_player_id) for seat in self.seats.values()]
            occupied_count = self.occupied_seat_count()
            is_full = occupied_count == len(self.seats)
            mode_meta = room_mode_payload(self.mode)
            return {
                "room_id": self.room_id,
                "match_id": self.current_match_id,
                "status": self.status,
                "mode": mode_meta["code"],
                "mode_name": mode_meta["name"],
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "invite_path": self.invite_path(),
                "invite_url": self.invite_url(base_url),
                "host_player_id": self.host_player_id,
                "random_roster_size": self.random_roster_size,
                "hero_limit": self.hero_limit,
                "board_width": int(getattr(self, "board_width", DEFAULT_BOARD_WIDTH) or DEFAULT_BOARD_WIDTH),
                "board_height": int(getattr(self, "board_height", DEFAULT_BOARD_HEIGHT) or DEFAULT_BOARD_HEIGHT),
                "turn_timeout_seconds": int(getattr(self, "turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS) or 0),
                "hero_turn_limit": int(getattr(self, "hero_turn_limit", 0) or SKIRMISH_HERO_TURN_LIMIT),
                "turn_limit_winner": int(getattr(self, "turn_limit_winner", 2) or 2),
                "default_ai_difficulty": self.default_ai_difficulty,
                "occupied_seat_count": occupied_count,
                "human_seat_count": self.human_seat_count(),
                "ai_seat_count": self.ai_seat_count(),
                "seat_count": len(self.seats),
                "is_full": is_full,
                "can_join": self.status == "lobby" and any(seat.can_join for seat in self.seats.values()),
                "can_start": self.can_start(),
                "configuration_ready": self._configuration_blocker() is None,
                "start_blocker": self._start_blocker(),
                "human_ready_count": self.human_ready_count(),
                "can_rematch": self.status == "finished" and public_launch_context(self).get("allow_rematch", True),
                "launch_context": public_launch_context(self),
                "postgame": build_postgame_summary(
                    self.battle,
                    self.replay,
                    seat_names={seat.player_id: seat.name for seat in self.seats.values() if seat.name},
                ),
                "seats": seats,
            }

    def _start_blocker(self) -> Optional[str]:
        blocker = self._configuration_blocker()
        if blocker is not None:
            return blocker
        waiting = [seat.name or f"席位 {seat.player_id}" for seat in self.seats.values() if seat.is_human and not seat.ready]
        if waiting:
            return f"等待真人席位确认准备：{'、'.join(waiting)}。"
        return None

    def _configuration_blocker(self) -> Optional[str]:
        if self.battle is not None:
            return "当前房间已经在对局中。"
        if not self.seats:
            return "房间里还没有席位。"
        if any(not seat.occupied for seat in self.seats.values()):
            return "仍有开放席位未被真人或 AI 占用。"
        if self.human_seat_count() <= 0:
            return "当前至少需要一个真人席位才能开始。"
        if self.mode == "random":
            for team_id in TEAM_IDS:
                if self._team_quota_sum(team_id) != self.random_roster_size:
                    return f"{team_name(team_id)}的随机武将配额之和必须等于 n = {self.random_roster_size}。"
            return None
        for team_id in TEAM_IDS:
            if sum(seat.hero_total_count for seat in self._team_seats(team_id)) <= 0:
                return f"{team_name(team_id)}还没有配置任何武将。"
        return None

    def _legacy_start_blocker(self) -> Optional[str]:
        if self.battle is not None:
            return "å½“å‰æˆ¿é—´å·²ç»åœ¨å¯¹å±€ä¸­ã€‚"
        if not self.seats:
            return "æˆ¿é—´é‡Œè¿˜æ²¡æœ‰å¸­ä½ã€‚"
        if any(not seat.occupied for seat in self.seats.values()):
            return "ä»æœ‰å¼€æ”¾å¸­ä½æœªè¢«çœŸäººæˆ– AI å ç”¨ã€‚"
        if self.human_seat_count() <= 0:
            return "å½“å‰è‡³å°‘éœ€è¦ä¸€ä¸ªçœŸäººå¸­ä½æ‰èƒ½å¼€å§‹ã€‚"
        if self.mode == "random":
            for team_id in TEAM_IDS:
                if self._team_quota_sum(team_id) != self.random_roster_size:
                    return f"{team_name(team_id)} çš„éšæœºæ­¦å°†é…é¢ä¹‹å’Œå¿…é¡»ç­‰äºŽ n = {self.random_roster_size}ã€‚"
            return None
        for team_id in TEAM_IDS:
            if sum(seat.hero_total_count for seat in self._team_seats(team_id)) <= 0:
                return f"{team_name(team_id)} è¿˜æ²¡æœ‰é…ç½®ä»»ä½•æ­¦å°†ã€‚"
        return None

    def _replay_state_for_viewer(self, viewer: Optional[PlayerSeat]) -> dict[str, Any]:
        if self.replay is None or self.replay.step_count <= 0:
            return {
                "available": False,
                "step_count": 0,
                "last_step_index": 0,
                "saved_path": None,
                "finished": False,
                "can_use_omniscient": False,
                "default_view": "spectator",
            }
        return {
            "available": True,
            "step_count": self.replay.step_count,
            "last_step_index": self.replay.last_index,
            "saved_path": self.replay.saved_path,
            "finished": self.replay.saved_path is not None,
            "can_use_omniscient": self.status == "finished",
            "default_view": "seat" if viewer is not None else "spectator",
        }

    def _simulation_state_for_viewer(self, viewer: Optional[PlayerSeat]) -> dict[str, Any]:
        return {
            "enabled": self._simulation_enabled(),
            "paused": self.simulation_paused,
            "speed": self.simulation_speed,
            "speed_options": list(SIMULATION_SPEED_OPTIONS),
            "can_control": (
                viewer is not None
                and viewer.player_id == self.host_player_id
                and self._simulation_enabled()
                and not self._has_human_ai_takeover()
            ),
            "live_step_index": self.replay.last_index if self.replay is not None and self.replay.step_count > 0 else 0,
            "last_action": self._visible_last_action_for_viewer(viewer),
            "pending_action": self._visible_pending_action_for_viewer(viewer),
        }

    def start_battle(self, token: str, *, require_confirmation: bool = False) -> None:
        with self._lock:
            if require_confirmation:
                self.require_host(token)
            else:
                self.require_seat(token)
            if self.status != "lobby":
                raise RoomError("å½“å‰æˆ¿é—´å·²ç»åœ¨å¯¹å±€ä¸­ã€‚")
            blocker = self._start_blocker() if require_confirmation else self._configuration_blocker()
            if blocker is not None:
                raise RoomError(blocker)
            if self.mode == "random":
                assignments = self._team_random_rosters()
                for seat in self.seats.values():
                    seat.replace_roster(assignments[seat.team_id].get(seat.player_id, []))
            player1_entries = self._battle_entries_for_team(1)
            player2_entries = self._battle_entries_for_team(2)
            self.match_number += 1
            self.current_match_id = f"{self.room_id}-{self.match_number}"
            try:
                self.battle = create_room_battle(
                    player1_entries,
                    player2_entries,
                    mode=self.mode,
                    board_width=int(getattr(self, "board_width", DEFAULT_BOARD_WIDTH) or DEFAULT_BOARD_WIDTH),
                    board_height=int(getattr(self, "board_height", DEFAULT_BOARD_HEIGHT) or DEFAULT_BOARD_HEIGHT),
                    turn_timeout_limit=int(getattr(self, "hero_turn_limit", 0) or SKIRMISH_HERO_TURN_LIMIT),
                    turn_timeout_winner=int(getattr(self, "turn_limit_winner", 2) or 2),
                )
            except (ActionError, ValueError) as exc:
                raise RoomError("当前战场放不下这些武将，请把战场调大后再开局。") from exc
            self.battle.army_orders = default_army_orders()
            for team_id, commands in self.army_orders_by_team.items():
                if not isinstance(commands, dict):
                    continue
                if commands.get("order"):
                    self.battle.set_army_order(
                        int(team_id),
                        commands.get("order", "advance"),
                        commands.get("direction"),
                        stride=commands.get("stride"),
                    )
                    continue
                for kind, command in commands.items():
                    if not isinstance(command, dict):
                        continue
                    self.battle.set_army_order(
                        int(team_id),
                        command.get("order", "advance"),
                        command.get("direction"),
                        kind=kind,
                        stride=command.get("stride"),
                        ammo=command.get("ammo"),
                    )
            self.replay = ReplayRecorder(self.room_id, self.mode, match_id=self.current_match_id)
            self.simulation_paused = False
            self.simulation_speed = DEFAULT_SIMULATION_SPEED
            self.simulation_last_advanced_at = time.time()
            self.pending_simulation_action = None
            self.last_action_id = 0
            self.last_action_meta = None
            self.turn_prompt_key = None
            self.turn_prompt_started_at = None
            self.turn_deadline_at = None
            self.last_turn_timeout = None
            self.status = "battle"
            for seat in self.seats.values():
                seat.ai_takeover = False
            self._bind_replay_checkpoint()
            self._record_replay_step("battle_start")
            self._sync_army_ai_players()
            self._advance_simulation_due(force_steps=1)
            self._sync_turn_timer()
            self.touch()

    def restart_lobby(self, token: str) -> None:
        with self._lock:
            self.require_host(token)
            if self.status != "finished":
                raise RoomError("åªæœ‰å¯¹å±€ç»“æŸåŽï¼Œæ‰èƒ½é‡æ–°å¼€å§‹é€‰å°†ã€‚")
            self._ensure_replay_saved()
            self.battle = None
            self.replay = None
            self.current_match_id = None
            self.simulation_paused = False
            self.simulation_speed = DEFAULT_SIMULATION_SPEED
            self.simulation_last_advanced_at = None
            self.pending_simulation_action = None
            self.last_action_id = 0
            self.last_action_meta = None
            self.turn_prompt_key = None
            self.turn_prompt_started_at = None
            self.turn_deadline_at = None
            self.last_turn_timeout = None
            self.status = "lobby"
            for seat in self.seats.values():
                seat.ready = False
                seat.ai_takeover = False
            self.touch()

    def _resolve_ai_until_human_input(self, max_steps: Optional[int] = None) -> int:
        if self.battle is None or self.battle.winner is not None:
            self.status = "finished" if self.battle and self.battle.winner is not None else self.status
            return 0
        safety = 0
        steps = 0
        while self.battle is not None and self.battle.winner is None and safety < 512:
            if max_steps is not None and steps >= max_steps:
                break
            try:
                if self.battle.current_respawn_prompt() is not None:
                    seat = self._current_prompt_seat()
                    if seat is None or not seat.is_ai:
                        break
                    prompt = self.battle.current_respawn_prompt()
                    if prompt is None:
                        break
                    unit = self.battle.get_unit(prompt.unit_id)
                    options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
                    if not options:
                        break
                    difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                    payload = choose_respawn_action(self.battle, unit, options, difficulty)
                    if payload is None:
                        break
                    self._perform_battle_action(payload, reason="ai_respawn")
                    steps += 1
                elif self.battle.pending_chain is not None:
                    seat = self._current_prompt_seat()
                    if seat is None or not seat.is_ai:
                        break
                    current_unit_id = self.battle.pending_chain.current_unit_id()
                    if not current_unit_id:
                        break
                    reactor = self.battle.get_unit(current_unit_id)
                    options = self.battle.reaction_snapshot_for(reactor).get("actions", [])
                    difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                    payload = choose_chain_reaction(self.battle, reactor, options, difficulty)
                    self._perform_battle_action(payload or {"type": "chain_skip"}, reason="ai_chain")
                    steps += 1
                else:
                    instant_payload = self._choose_ai_instant_payload()
                    if instant_payload is not None:
                        self._perform_battle_action(instant_payload, reason="ai_instant")
                        steps += 1
                    elif self.battle.is_army_turn():
                        self._perform_battle_action({"type": "end_turn"}, reason="army_turn")
                        steps += 1
                    else:
                        seat = self._current_prompt_seat()
                        if seat is None or not seat.is_ai:
                            break
                        current_unit = self.battle.current_turn_unit()
                        if current_unit is None:
                            break
                        difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                        payload, _actor = choose_turn_bundle_action(
                            self.battle,
                            self.battle.current_turn_bundle_units(include_banished=False),
                            difficulty,
                        )
                        self._perform_battle_action(
                            payload,
                            reason="ai_turn",
                        )
                        steps += 1
            except ActionError:
                seat = self._current_prompt_seat()
                if seat is None or not seat.is_ai:
                    break
                if self.battle.pending_chain is not None:
                    self._perform_battle_action({"type": "chain_skip"}, reason="ai_chain_fallback")
                    steps += 1
                elif self.battle.current_respawn_prompt() is not None:
                    prompt = self.battle.current_respawn_prompt()
                    if prompt is None:
                        break
                    unit = self.battle.get_unit(prompt.unit_id)
                    options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
                    if not options:
                        break
                    fallback = options[0]
                    self._perform_battle_action(
                        {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y},
                        reason="ai_respawn_fallback",
                    )
                    steps += 1
                else:
                    self._perform_battle_action({"type": "end_turn"}, reason="ai_turn_fallback")
                    steps += 1
            safety += 1
        self.status = "finished" if self.battle and self.battle.winner is not None else "battle"
        return steps

    def resolve_ai_until_human_input(self) -> int:
        with self._lock:
            return self._resolve_ai_until_human_input()

    def run_ai_simulation_to_end(self, *, max_steps: int = 5000) -> int:
        with self._lock:
            if self.battle is None:
                return 0
            self.fast_ai_simulation = True
            self.battle.fast_ai_simulation = True
            self._bind_replay_checkpoint()
            self.simulation_paused = False
            self.pending_simulation_action = None
            self._sync_army_ai_players()
            steps = self._resolve_ai_until_human_input(max_steps=max_steps)
            if self.battle.winner is not None:
                self.status = "finished"
                self._ensure_replay_saved()
            self.fast_ai_simulation = False
            if self.battle is not None:
                self.battle.fast_ai_simulation = False
            self.touch()
            return steps

    def set_ai_takeover(self, token: str, enabled: Any) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if self.tutorial_state is not None or str(self.experience_kind or "") == "tutorial":
                raise RoomError("教学模式不能交给 AI 接管。")
            if self.battle is None or self.status != "battle" or self.battle.winner is not None:
                raise RoomError("只有正在进行的对局里才能切换 AI 接管。")
            if not seat.is_human:
                raise RoomError("只有玩家席位才能切换 AI 接管。")
            if isinstance(enabled, str):
                next_enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
            else:
                next_enabled = bool(enabled)
            if next_enabled == bool(getattr(seat, "ai_takeover", False)):
                return
            seat.ai_takeover = next_enabled
            if not next_enabled:
                current = self._current_prompt_seat()
                if current is not None and current.player_id == seat.player_id:
                    self.pending_simulation_action = None
            self._sync_army_ai_players()
            self._sync_turn_timer()
            if next_enabled:
                self.battle.log(f"{seat.name or f'席位 {seat.player_id}'} 让 AI 接管了操作。")
                self._advance_simulation_due(force_steps=2)
            else:
                self.battle.log(f"{seat.name or f'席位 {seat.player_id}'} 收回了操作。")
            self.touch()

    def set_ai_styles(self, token: str, *, hero_ai_style: Any = None, army_ai_style: Any = None) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if self.tutorial_state is not None or str(self.experience_kind or "") == "tutorial":
                raise RoomError("教学模式不能调整 AI 倾向。")
            if self.battle is None or self.status != "battle" or self.battle.winner is not None:
                raise RoomError("只有正在进行的对局里才能调整 AI 倾向。")
            if not seat.is_human:
                raise RoomError("只有玩家席位才能调整 AI 倾向。")
            if hero_ai_style is not None:
                seat.hero_ai_style = normalize_hero_ai_style(hero_ai_style)
            if army_ai_style is not None:
                seat.army_ai_style = normalize_army_ai_style(army_ai_style)
            self._sync_army_ai_players()
            self.touch()

    def perform_action(self, token: str, payload: dict[str, Any]) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if bool(getattr(seat, "ai_takeover", False)):
                raise RoomError("当前已交给 AI 接管，请先停止接管再操作。")
            if self.battle is None:
                raise RoomError("å½“å‰æˆ¿é—´è¿˜æ²¡æœ‰å¼€å§‹å¯¹å±€ã€‚")
            current_player = self.current_input_player_id()
            instant_override = self.allows_instant_action_override(seat, payload)
            if current_player != seat.team_id and not instant_override:
                raise RoomError("çŽ°åœ¨è¿˜æ²¡è½®åˆ°ä½ è¿™è¾¹æ“ä½œã€‚")
            actor_unit_id = payload.get("unit_id")
            if actor_unit_id:
                actor = self.battle.get_unit(str(actor_unit_id))
                if actor.player_id != seat.team_id:
                    raise RoomError("ä¸èƒ½æ“ä½œå¯¹æ–¹å•ä½ã€‚")
                if self._unit_owner_seat_id(actor) != seat.player_id:
                    raise RoomError("ä¸èƒ½æ“ä½œåŒé˜Ÿå…¶ä»–å¸­ä½æ‹¥æœ‰çš„å•ä½ã€‚")
            elif not instant_override:
                responsible_seat = self._current_prompt_seat()
                if responsible_seat is not None and responsible_seat.player_id != seat.player_id:
                    raise RoomError("çŽ°åœ¨è¿˜æ²¡è½®åˆ°ä½ æŽ§åˆ¶çš„å•ä½ã€‚")
            try:
                self._validate_tutorial_action(payload)
                self._perform_battle_action(payload, reason="player_action")
            except ActionError as exc:
                raise RoomError(str(exc)) from exc
            self._update_tutorial_after_action(payload)
            if self.battle is not None and self.battle.winner is None:
                if self.tutorial_state is not None:
                    self._resolve_ai_until_human_input()
                else:
                    self._advance_simulation_due(force_steps=1)
            self.status = "finished" if self.battle and self.battle.winner is not None else "battle"
            self.touch()

    def control_simulation(self, token: str, action: str, *, speed: Any = None) -> None:
        with self._lock:
            self.require_host(token)
            if self.battle is None or self.replay is None:
                raise RoomError("å½“å‰è¿˜æ²¡æœ‰å¯æŽ§åˆ¶çš„ AI å¯¹å±€ / å›žæ”¾ã€‚")
            normalized = str(action or "").strip().lower()
            if normalized == "set_speed":
                self.simulation_speed = normalize_simulation_speed(speed)
                self.touch()
                return
            if normalized == "pause":
                if not self._simulation_enabled():
                    raise RoomError("å½“å‰æ²¡æœ‰å¯ä»¥æš‚åœçš„è‡ªåŠ¨æ¨¡æ‹Ÿã€‚")
                self.simulation_paused = True
                self.touch()
                return
            if normalized == "resume":
                if not self._simulation_enabled():
                    raise RoomError("å½“å‰æ²¡æœ‰å¯ä»¥ç»§ç»­çš„è‡ªåŠ¨æ¨¡æ‹Ÿã€‚")
                self.simulation_paused = False
                self.simulation_last_advanced_at = time.time()
                self.touch()
                return
            if normalized == "step":
                if not self._simulation_enabled():
                    raise RoomError("å½“å‰æ²¡æœ‰å¯ä»¥å•æ­¥æŽ¨è¿›çš„è‡ªåŠ¨æ¨¡æ‹Ÿã€‚")
                self.simulation_paused = True
                self._advance_simulation_due(force_steps=1)
                self.touch()
                return
            raise RoomError("æœªçŸ¥çš„æ¨¡æ‹ŸæŽ§åˆ¶æŒ‡ä»¤ã€‚")

    def serialize_replay_step(
        self,
        viewer_token: Optional[str],
        *,
        step_index: Any,
        omniscient: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if self.replay is None or self.replay.step_count <= 0:
                raise RoomError("å½“å‰æˆ¿é—´è¿˜æ²¡æœ‰å¯ç”¨çš„å›žæ”¾æ•°æ®ã€‚")
            viewer = self.seat_for_token(viewer_token)
            viewer_seat_id = viewer.player_id if viewer is not None else None
            try:
                requested_index = int(step_index)
            except (TypeError, ValueError) as exc:
                raise RoomError("å›žæ”¾æ­¥æ•°å¿…é¡»æ˜¯æ•´æ•°ã€‚") from exc
            resolved_index = max(0, min(requested_index, self.replay.last_index))
            allow_omniscient = bool(omniscient and self.status == "finished")
            return {
                "replay": {
                    **self._replay_state_for_viewer(viewer),
                    "requested_step_index": requested_index,
                    "step_index": resolved_index,
                    "omniscient": allow_omniscient,
                },
                "battle": self.replay.battle_for_step(
                    resolved_index,
                    seat_id=viewer_seat_id,
                    omniscient=allow_omniscient,
                ),
            }

    def serialize_state(self, viewer_token: Optional[str] = None, *, base_url: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            viewer = self.seat_for_token(viewer_token)
            if self.battle is not None and self.battle.winner is None:
                self._advance_simulation_due()
                self._process_due_turn_timeout()
            viewer_player_id = viewer.player_id if viewer else None
            viewer_team_id = viewer.team_id if viewer else None
            viewer_name = viewer.name if viewer else None
            heroes_by_code = hero_lookup()
            mode_meta = room_mode_payload(self.mode)
            room_state = {
                "room_id": self.room_id,
                "match_id": self.current_match_id,
                "status": self.status,
                "mode": mode_meta["code"],
                "mode_name": mode_meta["name"],
                "mode_description": mode_meta["description"],
                "experience_kind": self.experience_kind,
                "launch_context": public_launch_context(self),
                "tutorial": self.tutorial_public_state(),
                "available_modes": room_mode_list_payload(),
                "version": self.version,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "invite_path": self.invite_path(),
                "invite_url": self.invite_url(base_url),
                "host_player_id": self.host_player_id,
                "random_roster_size": self.random_roster_size,
                "hero_limit": self.hero_limit,
                "board_width": int(getattr(self, "board_width", DEFAULT_BOARD_WIDTH) or DEFAULT_BOARD_WIDTH),
                "board_height": int(getattr(self, "board_height", DEFAULT_BOARD_HEIGHT) or DEFAULT_BOARD_HEIGHT),
                "turn_timeout_seconds": int(getattr(self, "turn_timeout_seconds", DEFAULT_TURN_TIMEOUT_SECONDS) or 0),
                "hero_turn_limit": int(getattr(self, "hero_turn_limit", 0) or SKIRMISH_HERO_TURN_LIMIT),
                "turn_limit_winner": int(getattr(self, "turn_limit_winner", 2) or 2),
                "default_ai_difficulty": self.default_ai_difficulty,
                "seat_count": len(self.seats),
                "seat_count_min": MIN_ROOM_SEAT_COUNT,
                "seat_count_max": MAX_ROOM_SEAT_COUNT,
                "viewer_player_id": viewer_player_id,
                "viewer_team_id": viewer_team_id,
                "viewer_name": viewer_name,
                "viewer_ai_takeover": bool(viewer is not None and getattr(viewer, "ai_takeover", False)),
                "viewer_hero_ai_style": getattr(viewer, "hero_ai_style", DEFAULT_HERO_AI_STYLE) if viewer else DEFAULT_HERO_AI_STYLE,
                "viewer_army_ai_style": getattr(viewer, "army_ai_style", DEFAULT_ARMY_AI_STYLE) if viewer else DEFAULT_ARMY_AI_STYLE,
                "viewer_is_host": viewer_player_id == self.host_player_id if viewer_player_id is not None else False,
                "occupied_seat_count": self.occupied_seat_count(),
                "human_seat_count": self.human_seat_count(),
                "ai_seat_count": self.ai_seat_count(),
                "is_full": all(seat.occupied for seat in self.seats.values()),
                "can_start": self.can_start(),
                "configuration_ready": self._configuration_blocker() is None,
                "start_blocker": self._start_blocker(),
                "human_ready_count": self.human_ready_count(),
                "can_rematch": (
                    self.status == "finished"
                    and viewer_player_id == self.host_player_id
                    and public_launch_context(self).get("allow_rematch", True)
                ),
                "seats": [seat.to_public_dict(heroes_by_code, self.host_player_id) for seat in self.seats.values()],
                "army_orders": {
                    1: dict(self.army_orders_by_team.get(1) or default_army_orders()[1]),
                    2: dict(self.army_orders_by_team.get(2) or default_army_orders()[2]),
                },
                "army_kinds": [
                    {"kind": kind, "name": ARMY_KIND_LABELS[kind]}
                    for kind in ("infantry", "archer", "cavalry")
                ],
                "turn_timer": self._turn_timer_public_state(),
                "postgame": build_postgame_summary(
                    self.battle,
                    self.replay,
                    seat_names={seat.player_id: seat.name for seat in self.seats.values() if seat.name},
                ),
                "replay": self._replay_state_for_viewer(viewer),
                "simulation": self._simulation_state_for_viewer(viewer),
            }
            battle_state = (
                battle_state_for_viewer(self.battle, viewer_team_id, viewer_player_id)
                if self.battle is not None
                else None
            )
            return {
                "heroes": heroes_catalog(),
                "room": room_state,
                "battle": battle_state,
            }


    def surrender(self, token: str) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if self.battle is None or self.status != "battle":
                raise RoomError("\u5f53\u524d\u623f\u95f4\u4e0d\u5728\u5bf9\u5c40\u4e2d\uff0c\u4e0d\u80fd\u6295\u964d\u3002")
            winner = 2 if seat.team_id == 1 else 1
            self.battle.pending_chain = None
            self.battle.pending_respawn_unit_ids = []
            self.battle.winner = winner
            self.battle.win_reason_code = "surrender"
            self.battle.win_reason_text = f"{seat.name or f'席位 {seat.player_id}'} 投降。"
            self.battle._append_summary_event(
                "match_end",
                actor_unit_id=None,
                actor_name=seat.name or f"席位 {seat.player_id}",
                actor_player_id=winner,
                target_name=seat.name or f"席位 {seat.player_id}",
                action_name="投降",
                amount=0,
            )
            self.battle.log(
                f"{seat.name or f'\u5e2d\u4f4d {seat.player_id}'} "
                f"\u6295\u964d\u3002{team_name(winner)}\u83b7\u80dc\u3002"
            )
            self.status = "finished"
            self._record_replay_step("surrender")
            self._ensure_replay_saved()
            self.touch()


class RoomRegistry:
    def __init__(self) -> None:
        self._rooms: dict[str, GameRoom] = {}
        self._lock = threading.RLock()

    def _generate_room_id(self) -> str:
        while True:
            room_id = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
            if room_id not in self._rooms:
                return room_id

    def create_room(
        self,
        player_name: str,
        mode: str = DEFAULT_ROOM_MODE,
        *,
        account_user_id: Optional[int] = None,
    ) -> tuple[GameRoom, int, str]:
        with self._lock:
            room = GameRoom(self._generate_room_id(), mode=mode)
            player_id, token = room.create_host(player_name, account_user_id=account_user_id)
            self._rooms[room.room_id] = room
            return room, player_id, token

    def create_preconfigured_battle_room(
        self,
        *,
        host_name: str,
        opponent_name: str,
        player1_roster: list[str],
        player2_roster: list[str],
        start_immediately: bool = True,
        host_becomes_ai_after_start: bool = False,
        ai_difficulty: str = DEFAULT_AI_DIFFICULTY,
        host_account_user_id: Optional[int] = None,
        room_id: Optional[str] = None,
        board_width: Optional[int] = None,
        board_height: Optional[int] = None,
        experience_kind: str = "custom",
        launch_context: Optional[dict[str, Any]] = None,
        hero_turn_limit: Optional[int] = None,
        turn_limit_winner: Optional[int] = None,
    ) -> tuple[GameRoom, int, str]:
        with self._lock:
            normalized_room_id = normalize_room_id(room_id) if room_id else self._generate_room_id()
            if not normalized_room_id:
                raise RoomError("预设战斗房间编号无效。")
            if normalized_room_id in self._rooms:
                raise RoomError("预设战斗房间编号已被占用。")
            room = GameRoom(normalized_room_id, mode="classic", seat_count=2)
            room.experience_kind = str(experience_kind or "custom")
            if launch_context:
                room.launch_context = make_launch_context(
                    str(launch_context.get("source") or experience_kind or "skirmish"),
                    **{key: value for key, value in launch_context.items() if key != "source"},
                )
            if board_width:
                room.board_width = int(board_width)
            if board_height:
                room.board_height = int(board_height)
            if hero_turn_limit is not None:
                room.hero_turn_limit = max(1, int(hero_turn_limit))
            if turn_limit_winner in {1, 2}:
                room.turn_limit_winner = int(turn_limit_winner)
            player_id, token = room.create_host(host_name, account_user_id=host_account_user_id)
            room.set_seat_controller(token, 2, "ai")
            room.default_ai_difficulty = normalize_ai_difficulty(ai_difficulty)
            room.seats[2].ai_difficulty_override = room.default_ai_difficulty
            room.seats[2].name = normalize_player_name(opponent_name)
            room.seats[1].replace_roster(list(player1_roster))
            room.seats[2].replace_roster(list(player2_roster))
            if start_immediately:
                room.start_battle(token)
            if host_becomes_ai_after_start:
                room.seats[1].controller_type = "ai"
                room.seats[1].token = None
                room.seats[1].name = normalize_player_name(host_name)
                room._sync_army_ai_players()
                room.touch()
            self._rooms[room.room_id] = room
            return room, player_id, token

    def get_room(self, room_id: str) -> GameRoom:
        normalized = normalize_room_id(room_id)
        with self._lock:
            room = self._rooms.get(normalized)
        if room is None:
            raise RoomError("房间不存在，可能是房间码输错了。")
        return room

    def restore_room(self, room: GameRoom) -> GameRoom:
        normalized = normalize_room_id(room.room_id)
        if not normalized:
            raise RoomError("恢复的房间编号无效。")
        with self._lock:
            room.room_id = normalized
            self._rooms[normalized] = room
        return room

    def discard_room(self, room_id: str) -> None:
        normalized = normalize_room_id(room_id)
        with self._lock:
            self._rooms.pop(normalized, None)

    def delete_room(self, room_id: str, token: str) -> None:
        normalized = normalize_room_id(room_id)
        with self._lock:
            room = self._rooms.get(normalized)
            if room is None:
                raise RoomError("房间不存在，可能是房间码输错了。")
            room.require_host(token)
            del self._rooms[normalized]

    def leave_room(self, room_id: str, token: str) -> tuple[bool, int]:
        normalized = normalize_room_id(room_id)
        with self._lock:
            room = self._rooms.get(normalized)
            if room is None:
                raise RoomError("房间不存在，可能是房间码输错了。")
            leaving_player_id = room.leave(token)
            deleted = room.human_seat_count() == 0
            if deleted:
                del self._rooms[normalized]
            return deleted, leaving_player_id

    def list_rooms(self, *, base_url: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            rooms = list(self._rooms.values())
        rooms.sort(key=lambda room: room.updated_at, reverse=True)
        return [
            room.serialize_summary(base_url=base_url)
            for room in rooms
            if not is_campaign_launch(room)
        ]


ROOMS = RoomRegistry()
