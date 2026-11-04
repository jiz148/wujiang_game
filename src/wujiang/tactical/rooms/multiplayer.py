from __future__ import annotations

import copy
import logging
import math
import os
import pickle
import random
import secrets
import threading
import time
import zlib
from pathlib import Path
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
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
    DamageChoiceRequired,
    Position,
    SKIRMISH_HERO_TURN_LIMIT,
    Unit,
)
from wujiang.tactical.heroes.registry import RoomBattleEntry, create_room_battle, list_heroes
from wujiang.tactical.rooms.ai import choose_hell_dragon_return_action
from wujiang.tactical.rooms.ai_router import (
    choose_chain_reaction,
    choose_damage_choice_action,
    choose_instant_action,
    choose_respawn_action,
    choose_turn_bundle_action,
)
from wujiang.tactical.rooms.replay import ReplayRecorder
from wujiang.tactical.rooms.room_defaults import (
    AUTO_CONFIGURE_COUNT_DEFAULT,
    AUTO_CONFIGURE_POINTS_DEFAULT,
)
from wujiang.tactical.rooms.postgame import build_postgame_summary
from wujiang.tactical.rooms.launch import is_campaign_launch, make_launch_context, public_launch_context
from wujiang.tactical.rooms.tutorial import TUTORIAL_ID, next_tutorial_step_id, tutorial_step
from wujiang.tactical.rooms.special_modes import (
    SPECIAL_MODE_BY_CODE,
    choose_bp_special_modes,
    normalize_bp_rule_code,
    normalize_bp_rule_mode,
    normalize_bp_rule_pool,
    public_special_modes,
    special_mode_catalog,
)


ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LOGGER = logging.getLogger(__name__)
ROOM_CODE_LENGTH = 6
DEFAULT_ROOM_MODE = "classic"
DEFAULT_RANDOM_ROSTER_SIZE = 1
MIN_ROOM_SEAT_COUNT = 2
MAX_ROOM_SEAT_COUNT = 8
AUTOCHESS_SEAT_COUNT = 8
MIN_ROOM_HERO_LIMIT = 1
MAX_ROOM_HERO_LIMIT = 20
AUTO_CONFIGURE_COUNT_MIN = 1
AUTO_CONFIGURE_COUNT_MAX = 12
AUTO_CONFIGURE_POINTS_MIN = 10
AUTO_CONFIGURE_POINTS_MAX = 50
AUTO_CONFIGURE_ROSTER_CAP = 12
TEAM_IDS = (1, 2)
BP_POOL_SIZE = 66
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
BP_DEFAULT_BOARD_WIDTH = 16
BP_DEFAULT_BOARD_HEIGHT = 11
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
        "description": "双方无需手动选将，开局后随机分配不重复武将，使用更大的战场、随机出生，按低等级、高速度、高攻击的顺序决定先手。",
    },
    "bp": {
        "name": "BP模式",
        "description": "默认标准模式（等级随机规则）逐轮抽等级；沙盒模式（全局选择规则）从66名武将池中禁选。每局启用特殊模式，支持3v3和5v5。",
    },
    "autochess": {
        "name": "自走棋",
        "description": "8 人经营阵容、自动战斗，最后存活者获胜。",
    },
}


class RoomError(Exception):
    pass


BP_FORMAT_FULL = "full"
BP_FORMAT_TIER = "tier"
BP_FORMAT_NAMES = {
    BP_FORMAT_TIER: "标准模式（等级随机规则）",
    BP_FORMAT_FULL: "沙盒模式（全局选择规则）",
}
BP_TIER_LEVELS = tuple(range(1, 11))
BP_TIER_ROUNDS = 3
BP_TIER_ROUNDS_MIN = 1
BP_TIER_ROUNDS_MAX = 12


def bp_tier_round_shape(hero_count: int, *, allow_same_hero: bool = False) -> Optional[tuple[int, int]]:
    """一个等级这一轮，双方各禁几个、各选 1 个。

    标准模式（等级随机规则）是一轮一轮选人，每边每轮只选一名。禁用会从公共池里拿掉武将。
    人够双方各禁 2 再各选 1，就禁 2；只够各禁 1，就禁 1；再少就不禁。
    不允许重复时，选取也会拿走武将，所以至少要 2 人才能开这一轮。
    返回 None 表示这个等级已经不够双方各拿到一名。
    """
    count = int(hero_count)
    if allow_same_hero:
        if count >= 5:
            return (2, 1)
        if count >= 3:
            return (1, 1)
        if count >= 1:
            return (0, 1)
        return None
    if count >= 6:
        return (2, 1)
    if count >= 4:
        return (1, 1)
    if count >= 2:
        return (0, 1)
    return None


def normalize_bp_tier_rounds(value: Any) -> int:
    if isinstance(value, bool) or not str(value).strip().isdigit():
        raise RoomError("轮数必须是 1 到 12 的整数。")
    rounds = int(value)
    if not BP_TIER_ROUNDS_MIN <= rounds <= BP_TIER_ROUNDS_MAX:
        raise RoomError("轮数必须是 1 到 12 的整数。")
    return rounds


def tier_round_steps(starter: int, bans: int, picks: int) -> list[tuple[str, int]]:
    other = 3 - int(starter)
    starter = int(starter)
    return ([("ban", starter)] * int(bans) + [("ban", other)] * int(bans)
            + [("pick", starter)] * int(picks) + [("pick", other)] * int(picks))


def normalize_bp_format(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "full": BP_FORMAT_FULL,
        "全体选择": BP_FORMAT_FULL,
        "沙盒模式": BP_FORMAT_FULL,
        "沙盒模式（全局选择规则）": BP_FORMAT_FULL,
        "tier": BP_FORMAT_TIER,
        "随机等级": BP_FORMAT_TIER,
        "标准模式": BP_FORMAT_TIER,
        "标准模式（等级随机规则）": BP_FORMAT_TIER,
    }
    mapped = aliases.get(text)
    if mapped is None:
        raise RoomError("BP 类型只能是标准模式（等级随机规则）或沙盒模式（全局选择规则）。")
    return mapped


def normalize_bp_tier_pool(value: Any) -> tuple[int, ...]:
    if isinstance(value, str):
        value = [part for part in value.replace("，", ",").split(",") if part.strip()]
    if not isinstance(value, (list, tuple)) or not value:
        raise RoomError("请至少勾选一个可随机的等级。")
    levels: list[int] = []
    for item in value:
        if isinstance(item, bool) or not str(item).strip().isdigit():
            raise RoomError("等级必须是 1 到 10 的整数。")
        level = int(item)
        if level not in BP_TIER_LEVELS:
            raise RoomError("等级必须是 1 到 10 的整数。")
        if level not in levels:
            levels.append(level)
    return tuple(levels)


def heroes_catalog() -> list[dict[str, Any]]:
    return list_heroes()


def hero_lookup() -> dict[str, dict[str, Any]]:
    return {hero["code"]: hero for hero in heroes_catalog()}


def draw_bp_hero_pool(heroes: dict[str, dict[str, Any]], *, size: int = BP_POOL_SIZE,
                      rng: Optional[random.Random] = None) -> tuple[str, ...]:
    """Draw playable heroes using a capacity-constrained bell-shaped level mix."""
    by_level: dict[int, list[str]] = {}
    for code, hero in heroes.items():
        by_level.setdefault(int(hero["level"]), []).append(code)
    if len(heroes) < size:
        raise RoomError(f"BP需要至少{size}名已实现武将，当前只有{len(heroes)}名。")
    randomizer = rng or secrets.SystemRandom()
    weights = {level: math.exp(-0.5 * ((level - 5.5) / 2.15) ** 2) for level in by_level}
    quotas = {level: 0 for level in by_level}
    for _ in range(size):
        best_score = max(weights[level] / (quotas[level] + 1)
                         for level in by_level if quotas[level] < len(by_level[level]))
        candidates = [level for level in by_level if quotas[level] < len(by_level[level])
                      and abs(weights[level] / (quotas[level] + 1) - best_score) < 1e-12]
        quotas[randomizer.choice(candidates)] += 1
    chosen = [code for level in sorted(by_level)
              for code in randomizer.sample(by_level[level], quotas[level])]
    randomizer.shuffle(chosen)
    return tuple(chosen)


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
    if battle.pending_damage_choice is not None:
        return [battle.get_unit(battle.pending_damage_choice["unit_id"])]
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
    if battle.pending_damage_choice is not None or battle.pending_chain is not None or battle.current_respawn_prompt() is not None:
        return []
    return battle.instant_action_units_for_player(viewer_player_id)


def battle_state_for_viewer(
    battle: Battle,
    viewer_player_id: Optional[int],
    viewer_seat_id: Optional[int] = None,
) -> dict[str, Any]:
    state = battle.to_public_dict()
    damage_prompt = state.get("pending_damage_choice")
    if (damage_prompt is not None and damage_prompt.get("kind") == "gentleman_guard"
            and viewer_player_id != battle.get_unit(damage_prompt["unit_id"]).player_id):
        # The owner can choose for a clone without exposing the genuine body to
        # the opponent through the prompt's controller or protected-unit IDs.
        state["pending_damage_choice"] = {
            **damage_prompt, "unit_id": None, "protected_unit_id": None,
            "action_name": "绅士防身", "options": [],
        }
    state["hell_dragon_returns"] = [
        {**entry, "can_control": bool(battle.winner is None and viewer_player_id == entry["player_id"] and
         (viewer_seat_id is None or battle_unit_owner_seat_id(
             battle, battle.get_unit(entry["unit_id"])) == viewer_seat_id))}
        for entry in state.get("hell_dragon_returns", [])
    ]
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
                "actions": battle.action_snapshot_for(unit) if unit.alive else {"actions": []},
                "reactions": battle.reaction_snapshot_for(unit),
            }
            for unit in _active_units_for_viewer(battle)
            if unit.player_id == viewer_player_id
            and (viewer_seat_id is None or battle_unit_owner_seat_id(battle, unit) == viewer_seat_id)
        ]
    elif viewer_player_id is not None:
        instant_units = _instant_units_for_viewer(battle, viewer_player_id)
        dragon_returns = [entry for entry in state.get("hell_dragon_returns", [])
                          if battle.winner is None and entry["player_id"] == viewer_player_id and entry["options"]
                          and (viewer_seat_id is None or battle_unit_owner_seat_id(
                              battle, battle.get_unit(entry["unit_id"])) == viewer_seat_id)]
        if instant_units or dragon_returns:
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


_CHESS_WORKERS: dict[str, threading.Thread] = {}
_CHESS_WORKER_GUARD = threading.Lock()
_CHESS_FINAL_FRAME_SECONDS = 1.6


def _autochess_final_frame_elapsed(room: "GameRoom", chess: Any, *, now: float | None = None) -> bool:
    """Keep the final board observable across at least one normal room poll."""
    timestamp = time.monotonic() if now is None else now
    round_key = (id(chess), chess.round_number)
    if getattr(room, "_chess_final_frame_round", None) != round_key:
        room._chess_final_frame_round = round_key
        room._chess_final_frame_since = timestamp
    return timestamp - room._chess_final_frame_since >= _CHESS_FINAL_FRAME_SECONDS


def _ensure_autochess_worker(room: "GameRoom") -> None:
    chess = room.autochess
    if chess is None or chess.phase != "battle":
        return
    with _CHESS_WORKER_GUARD:
        current = _CHESS_WORKERS.get(room.room_id)
        if current is not None and current.is_alive():
            return
        thread = threading.Thread(
            target=_autochess_battle_worker,
            args=(room,),
            name=f"autochess-{room.room_id}",
            daemon=True,
        )
        _CHESS_WORKERS[room.room_id] = thread
        thread.start()


def _autochess_step_delay(speed: float, active_matches: int, elapsed: float = 0.0) -> float:
    # A speed setting describes each board's cadence. Do not charge a full
    # board interval after every board in the sequential round-robin loop.
    # Computation/publication already occupied this interval. At high speed a
    # fixed sleep floor would cap four boards at five steps per board/second.
    return max(0.0, 0.9 / max(speed, 0.1) / max(1, active_matches) - max(0.0, elapsed))


def _autochess_battle_worker(room: "GameRoom") -> None:
    """Step chess fights beside the request threads.

    One AI decision can search for a while. Doing that inside the state
    request holds the room lock, so the browser's periodic /api/rooms/state
    calls stall and show as failed until the search returns.
    """
    try:
        while True:
            step_started = time.monotonic()
            with room._lock:
                if getattr(room, "_closed", False):
                    return
                chess = room.autochess
                if chess is None or chess.phase != "battle":
                    return
                pending = [match for match in chess.matches if match.result is None]
                if not pending:
                    if chess.matches and _autochess_final_frame_elapsed(room, chess):
                        chess._finish_round()
                        room.status = chess.phase
                        room.touch(persist_chess=True)
                        return
                    if not chess.matches:
                        return
                else:
                    cursor = int(getattr(chess, "next_match_cursor", 0))
                    match = pending[cursor % len(pending)]
                    chess.next_match_cursor = cursor + 1
                    changed = chess._advance_match(match)
                    if changed:
                        from wujiang.tactical.rooms.autochess_frames import publish_match
                        try:
                            publish_match(room, chess, match)
                        except Exception:
                            # A presentation failure must not terminate combat.
                            # The next normal state serialization can recover.
                            room._chess_frame_publish_failed = True
                            _LOGGER.exception("Could not publish committed chess frame for %s", room.room_id)
                speed = float(getattr(room, "chess_battle_speed", DEFAULT_SIMULATION_SPEED) or DEFAULT_SIMULATION_SPEED)
            # sleep(0) still yields after an overdue step, outside the room
            # lock. Keep round-robin order and the final-frame hold unchanged.
            time.sleep(_autochess_step_delay(speed, len(pending), time.monotonic() - step_started))
    finally:
        if getattr(room, "_closed", False):
            room.autochess = None
            room.battle = None
            room.replay = None
        with _CHESS_WORKER_GUARD:
            if _CHESS_WORKERS.get(room.room_id) is threading.current_thread():
                _CHESS_WORKERS.pop(room.room_id, None)


from wujiang.tactical.rooms.room_bp_mixin import RoomBPMixin
from wujiang.tactical.rooms.room_tutorial_mixin import RoomTutorialMixin
from wujiang.tactical.rooms.room_simulation_mixin import RoomSimulationMixin
from wujiang.tactical.rooms.room_lobby_mixin import RoomLobbyMixin
from wujiang.tactical.rooms.room_turn_mixin import RoomTurnMixin
from wujiang.tactical.rooms.room_views_mixin import RoomViewsMixin
from wujiang.tactical.rooms.room_match_mixin import RoomMatchMixin


class GameRoom(RoomBPMixin, RoomTutorialMixin, RoomSimulationMixin, RoomLobbyMixin, RoomTurnMixin, RoomViewsMixin, RoomMatchMixin):
    def __init__(self, room_id: str, *, mode: str = DEFAULT_ROOM_MODE, seat_count: int = MIN_ROOM_SEAT_COUNT) -> None:
        self.room_id = normalize_room_id(room_id)
        self.mode = normalize_room_mode(mode)
        self.experience_kind = "custom"
        self.launch_context = make_launch_context("skirmish")
        self.tutorial_state: Optional[dict[str, Any]] = None
        self.tutorial_checkpoint: Optional[Battle] = None
        self.random_roster_size = DEFAULT_RANDOM_ROSTER_SIZE
        self.hero_limit = 0
        self.army_enabled = False
        self.bp_team_size = 3
        self.bp_level_caps = {3: 15, 5: 25}
        self.bp_show_win_count = True
        self.bp_wins = {1: 0, 2: 0}
        self.bp_counted_match_ids: set[str] = set()
        self.bp_captains: dict[int, int] = {}
        self.bp_format = BP_FORMAT_TIER
        self.bp_allow_same_hero = False
        self.bp_tier_pool = BP_TIER_LEVELS
        self.bp_tier_rounds = BP_TIER_ROUNDS
        self.bp_tier_plan: list[dict[str, Any]] = []
        self.bp_first_pick_team: Optional[int] = None
        self.bp_auto_banned_levels: tuple[int, ...] = ()
        self.bp_hero_pool: tuple[str, ...] = ()
        self.bp_special_modes: tuple[str, ...] = ()
        self.bp_rule_mode = "random"
        self.bp_fixed_rule = "none"
        self.bp_rule_pool = tuple(SPECIAL_MODE_BY_CODE)
        self.bp_actions: list[dict[str, Any]] = []
        self.bp_assignments: dict[str, int | str] = {}
        self.bp_assign_opened = False
        self.bp_team_ai = {1: True, 2: True}
        self.bp_virtual_seat_ids: list[int] = []
        self.autochess = None
        self.chess_prep_seconds = 90
        self.chess_battle_speed = DEFAULT_SIMULATION_SPEED
        self.board_width = BP_DEFAULT_BOARD_WIDTH if self.mode == "bp" else DEFAULT_BOARD_WIDTH
        self.board_height = BP_DEFAULT_BOARD_HEIGHT if self.mode == "bp" else DEFAULT_BOARD_HEIGHT
        self.turn_timeout_seconds = DEFAULT_TURN_TIMEOUT_SECONDS
        self.hero_turn_limit = SKIRMISH_HERO_TURN_LIMIT
        self.turn_limit_winner = 2
        self.default_ai_difficulty = DEFAULT_AI_DIFFICULTY
        self.host_player_id = 1
        self.owner_user_id: Optional[int] = None
        self.seats = self._build_seats(AUTOCHESS_SEAT_COUNT if self.mode == "autochess" else normalize_room_seat_count(seat_count))
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
        if self.mode == "autochess":
            from wujiang.tactical.rooms.autochess import AutoChessState
            self.autochess = AutoChessState(AUTOCHESS_SEAT_COUNT)

    def __getstate__(self) -> dict[str, Any]:
        state = dict(self.__dict__)
        state["replay"] = None
        state.pop("_lock", None)
        state.pop("_bp_pair_cache", None)
        state.pop("_bp_legal_cache", None)
        state.pop("_chess_state_cache", None)
        state.pop("_chess_state_cache_at", None)
        state.pop("_chess_frame_publish_failed", None)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self.replay = None
        if not hasattr(self, "turn_timeout_seconds"):
            self.turn_timeout_seconds = DEFAULT_TURN_TIMEOUT_SECONDS
        if not hasattr(self, "board_width"):
            self.board_width = BP_DEFAULT_BOARD_WIDTH if self.mode == "bp" else DEFAULT_BOARD_WIDTH
        if not hasattr(self, "board_height"):
            self.board_height = BP_DEFAULT_BOARD_HEIGHT if self.mode == "bp" else DEFAULT_BOARD_HEIGHT
        if not hasattr(self, "launch_context"):
            self.launch_context = make_launch_context("skirmish")
        if not hasattr(self, "hero_turn_limit"):
            self.hero_turn_limit = SKIRMISH_HERO_TURN_LIMIT
        if not hasattr(self, "turn_limit_winner"):
            self.turn_limit_winner = 2
        if not hasattr(self, "bp_team_size"):
            self.bp_team_size = 3
        if not hasattr(self, "bp_level_caps"):
            self.bp_level_caps = {3: 15, 5: 25}
        if not hasattr(self, "bp_show_win_count"):
            self.bp_show_win_count = True
        if not hasattr(self, "bp_wins"):
            self.bp_wins = {1: 0, 2: 0}
        if not hasattr(self, "bp_counted_match_ids"):
            self.bp_counted_match_ids = set()
        if not hasattr(self, "bp_captains"):
            self.bp_captains = {}
        if not hasattr(self, "bp_format"):
            self.bp_format = BP_FORMAT_FULL
        if not hasattr(self, "bp_allow_same_hero"):
            self.bp_allow_same_hero = False
        if not hasattr(self, "bp_tier_pool"):
            self.bp_tier_pool = BP_TIER_LEVELS
        if not hasattr(self, "bp_tier_rounds"):
            self.bp_tier_rounds = BP_TIER_ROUNDS
        if not hasattr(self, "bp_tier_plan"):
            self.bp_tier_plan = []
        if not hasattr(self, "bp_first_pick_team"):
            self.bp_first_pick_team = None
        if not hasattr(self, "bp_auto_banned_levels"):
            self.bp_auto_banned_levels = ()
        if not hasattr(self, "bp_hero_pool"):
            self.bp_hero_pool = ()
        if not hasattr(self, "bp_special_modes"):
            self.bp_special_modes = ()
        if not hasattr(self, "bp_rule_mode"):
            self.bp_rule_mode = "random"
        if not hasattr(self, "bp_fixed_rule"):
            self.bp_fixed_rule = "none"
        if not hasattr(self, "bp_rule_pool"):
            self.bp_rule_pool = tuple(SPECIAL_MODE_BY_CODE)
        if not hasattr(self, "bp_actions"):
            self.bp_actions = []
        if not hasattr(self, "bp_assignments"):
            self.bp_assignments = {}
        if not hasattr(self, "bp_assign_opened"):
            self.bp_assign_opened = False
        if not hasattr(self, "bp_team_ai"):
            self.bp_team_ai = {1: True, 2: True}
        if not hasattr(self, "bp_virtual_seat_ids"):
            self.bp_virtual_seat_ids = []
        if not hasattr(self, "autochess"):
            self.autochess = None
        if not hasattr(self, "chess_prep_seconds"):
            self.chess_prep_seconds = 90
        if not hasattr(self, "chess_battle_speed"):
            self.chess_battle_speed = DEFAULT_SIMULATION_SPEED
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

    def touch(self, *, persist_chess: bool = True) -> None:
        self._record_bp_win_if_finished()
        self.version += 1
        self.updated_at = time.time()
        if self.mode == "autochess" and persist_chess and self.autochess.phase != "battle":
            self._last_chess_persist_at = self.updated_at
            ROOMS.persist(self)

    def _record_bp_win_if_finished(self) -> None:
        match_id = self.current_match_id
        if (self.mode != "bp" or self.status != "finished" or self.battle is None
                or self.battle.winner not in TEAM_IDS or not match_id
                or match_id in self.bp_counted_match_ids):
            return
        self.bp_wins[self.battle.winner] = self.bp_wins.get(self.battle.winner, 0) + 1
        self.bp_counted_match_ids.add(match_id)

    def invalidate_readiness(self) -> None:
        for seat in self.seats.values():
            if seat.is_human:
                seat.ready = False

































































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

















































































class RoomRegistry:
    def __init__(self) -> None:
        self._rooms: dict[str, GameRoom] = {}
        self._lock = threading.RLock()

    def _generate_room_id(self) -> str:
        while True:
            room_id = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
            if room_id not in self._rooms and not self._chess_path(room_id).exists():
                return room_id

    @staticmethod
    def _chess_path(room_id: str) -> Path:
        normalized = normalize_room_id(room_id)
        if len(normalized) != 6 or not normalized.isascii() or not normalized.isalnum():
            raise RoomError("房间编号无效。")
        configured = os.environ.get("WUJIANG_AUTOCHESS_DIR")
        directory = Path(configured) if configured else Path(__file__).resolve().parents[4] / "var" / "autochess"
        return directory / f"{normalized}.room"

    def persist(self, room: GameRoom) -> None:
        if room.mode != "autochess":
            return
        path = self._chess_path(room.room_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(room.checkpoint_bytes())
        temporary.replace(path)

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
            self.persist(room)
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
                path = self._chess_path(normalized)
                if path.exists():
                    room = GameRoom.from_checkpoint_bytes(path.read_bytes())
                    if room.mode != "autochess":
                        raise RoomError("房间存档模式无效。")
                    self._rooms[normalized] = room
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

    def delete_room(self, room_id: str, token: str, *, owner_user_id: Optional[int] = None) -> None:
        normalized = normalize_room_id(room_id)
        with self._lock:
            room = self._rooms.get(normalized)
            if room is None:
                raise RoomError("房间不存在，可能是房间码输错了。")
            owner = getattr(room, "owner_user_id", None)
            is_owner = owner_user_id is not None and owner is not None and int(owner) == int(owner_user_id)
            if not is_owner and room.human_seat_count() > 0:
                room.require_host(token)
            room._closed = True
            del self._rooms[normalized]
            if room.mode == "autochess":
                self._chess_path(normalized).unlink(missing_ok=True)

    def leave_room(self, room_id: str, token: str) -> tuple[bool, int]:
        normalized = normalize_room_id(room_id)
        with self._lock:
            room = self._rooms.get(normalized)
            if room is None:
                raise RoomError("房间不存在，可能是房间码输错了。")
            if room.mode == "autochess" and room.status != "lobby":
                seat = room.require_seat(token)
                return False, seat.player_id
            leaving_player_id = room.leave(token)
            # 人走了房间还在，席位空出来，同一个房间码可以再连回来。
            # 不想留的，从房间列表里删。
            return False, leaving_player_id

    def list_rooms(self, *, base_url: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            chess_dir = self._chess_path("ABCDEF").parent
            if chess_dir.is_dir():
                for path in chess_dir.glob("*.room"):
                    if path.stem in self._rooms:
                        continue
                    try:
                        restored = GameRoom.from_checkpoint_bytes(path.read_bytes())
                    except (OSError, RoomError):
                        continue
                    if restored.mode != "autochess" or restored.room_id != path.stem:
                        continue
                    self._rooms[restored.room_id] = restored
            rooms = [
                room for room in self._rooms.values()
                if not is_campaign_launch(room)
            ]
        rooms.sort(key=lambda room: room.updated_at, reverse=True)
        return [room.serialize_summary(base_url=base_url) for room in rooms]


ROOMS = RoomRegistry()
