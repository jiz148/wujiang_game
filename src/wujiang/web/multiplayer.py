from __future__ import annotations

import random
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from wujiang.engine.core import ActionError, Battle, Position, Unit
from wujiang.heroes.registry import RoomBattleEntry, create_room_battle, list_heroes
from wujiang.web.ai import (
    choose_chain_reaction,
    choose_instant_action,
    choose_respawn_action,
    choose_turn_action,
)
from wujiang.web.replay import ReplayRecorder


ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 6
DEFAULT_ROOM_MODE = "classic"
DEFAULT_RANDOM_ROSTER_SIZE = 1
MIN_ROOM_SEAT_COUNT = 2
MAX_ROOM_SEAT_COUNT = 6
TEAM_IDS = (1, 2)
TEAM_LABELS = {1: "红队", 2: "蓝队"}
CONTROLLER_TYPES = {"open", "human", "ai"}
DEFAULT_AI_DIFFICULTY = "standard"
AI_DIFFICULTIES = {"easy", "standard", "aggressive"}
DEFAULT_SIMULATION_SPEED = 1.0
SIMULATION_SPEED_OPTIONS = (0.5, 1.0, 2.0, 4.0)
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


def apply_private_clone_labels(state: dict[str, Any], viewer_player_id: Optional[int]) -> None:
    visible_names_by_id: dict[str, str] = {}
    for unit_payload in state.get("units", []):
        visible_name = clone_visible_name(unit_payload, viewer_player_id)
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
    return battle.current_turn_bundle_units(include_banished=False)


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
    apply_private_clone_labels(state, viewer_player_id)
    return state


@dataclass(slots=True)
class PlayerSeat:
    player_id: int
    team_id: int
    controller_type: str = "open"
    token: Optional[str] = None
    name: str = ""
    hero_counts: dict[str, int] = field(default_factory=dict)
    random_quota: int = 0
    ai_difficulty_override: Optional[str] = None
    joined_at: Optional[float] = None
    last_seen_at: Optional[float] = None

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
    def can_join(self) -> bool:
        return self.controller_type == "open"

    @property
    def hero_total_count(self) -> int:
        return sum(max(int(count), 0) for count in self.hero_counts.values())

    def claim(self, player_name: str) -> str:
        if not self.can_join:
            raise RoomError(f"席位 {self.player_id} 当前不能加入。")
        self.controller_type = "human"
        self.token = secrets.token_urlsafe(18)
        self.name = normalize_player_name(player_name)
        self.joined_at = time.time()
        self.last_seen_at = self.joined_at
        return self.token

    def set_ai(self) -> None:
        if self.is_human:
            raise RoomError("已有真人加入的席位不能直接改成 AI。")
        self.controller_type = "ai"
        self.token = None
        self.name = f"AI {self.player_id}"
        self.joined_at = time.time()
        self.last_seen_at = self.joined_at

    def set_open(self) -> None:
        self.controller_type = "open"
        self.token = None
        self.name = ""
        self.clear_roster()
        self.random_quota = 0
        self.ai_difficulty_override = None
        self.joined_at = None
        self.last_seen_at = None

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

    def matches_name(self, player_name: str) -> bool:
        return self.is_human and self.name == normalize_player_name(player_name)

    def reclaim(self, player_name: str) -> str:
        if not self.matches_name(player_name) or not self.token:
            raise RoomError("无法用该昵称恢复这个席位。")
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
        return {
            "player_id": self.player_id,
            "team_id": self.team_id,
            "team_name": team_name(self.team_id),
            "controller_type": self.controller_type,
            "occupied": self.occupied,
            "is_human": self.is_human,
            "is_ai": self.is_ai,
            "joinable": self.can_join,
            "name": self.name or None,
            "hero_counts": {code: int(count) for code, count in sorted(self.hero_counts.items()) if count > 0},
            "hero_roster": roster,
            "hero_total_count": self.hero_total_count,
            "hero_summary": self.hero_summary(heroes_by_code),
            "hero_code": single_hero_code,
            "hero_name": single_hero["name"] if single_hero else None,
            "random_quota": self.random_quota,
            "ai_difficulty_override": self.ai_difficulty_override,
            "is_host": self.player_id == host_player_id,
        }


class GameRoom:
    def __init__(self, room_id: str, *, mode: str = DEFAULT_ROOM_MODE, seat_count: int = MIN_ROOM_SEAT_COUNT) -> None:
        self.room_id = normalize_room_id(room_id)
        self.mode = normalize_room_mode(mode)
        self.random_roster_size = DEFAULT_RANDOM_ROSTER_SIZE
        self.default_ai_difficulty = DEFAULT_AI_DIFFICULTY
        self.host_player_id = 1
        self.seats = self._build_seats(normalize_room_seat_count(seat_count))
        self._reset_random_quotas_to_defaults()
        self.battle: Optional[Battle] = None
        self.replay: Optional[ReplayRecorder] = None
        self.simulation_paused = False
        self.simulation_speed = DEFAULT_SIMULATION_SPEED
        self.simulation_last_advanced_at: Optional[float] = None
        self.status = "lobby"
        self.version = 0
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._lock = threading.RLock()

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

    def _has_interactive_human_presence(self) -> bool:
        return any(
            seat.is_human and self._seat_has_owned_hero_presence(seat)
            for seat in self.seats.values()
        )

    def _simulation_enabled(self) -> bool:
        return self.battle is not None and self.battle.winner is None and not self._has_interactive_human_presence()

    def _simulation_interval_seconds(self) -> float:
        return max(0.1, 0.9 / max(self.simulation_speed, 0.1))

    def _record_replay_step(self, reason: str) -> None:
        if self.battle is None:
            return
        if self.replay is None:
            self.replay = ReplayRecorder(self.room_id, self.mode)
        seat_views = {
            str(seat.player_id): battle_state_for_viewer(self.battle, seat.team_id, seat.player_id)
            for seat in self.seats.values()
            if seat.occupied
        }
        self.replay.append_step(
            reason=reason,
            omniscient_battle=self.battle.to_public_dict(),
            spectator_battle=battle_state_for_viewer(self.battle, None, None),
            seat_views=seat_views,
        )

    def _ensure_replay_saved(self) -> None:
        if self.battle is None or self.battle.winner is None or self.replay is None:
            return
        self.replay.finish_and_save(room_summary=self.serialize_summary())

    def _perform_battle_action(self, payload: dict[str, Any], *, reason: str) -> None:
        if self.battle is None:
            raise RoomError("å½“å‰æˆ¿é—´è¿˜æ²¡æœ‰å¼€å§‹å¯¹å±€ã€‚")
        self.battle.perform_action(payload)
        self._record_replay_step(reason)
        if self.battle.winner is not None:
            self.status = "finished"
            self._ensure_replay_saved()

    def _advance_simulation_due(self, *, force_steps: Optional[int] = None) -> int:
        if self.battle is None or self.battle.winner is not None or not self._simulation_enabled():
            return 0
        if self.simulation_paused and force_steps is None:
            return 0
        if force_steps is None:
            now = time.time()
            last_tick = self.simulation_last_advanced_at if self.simulation_last_advanced_at is not None else now
            elapsed = max(0.0, now - last_tick)
            due_steps = int(elapsed / self._simulation_interval_seconds())
            if due_steps <= 0:
                return 0
            force_steps = min(due_steps, 4)
        steps = self._resolve_ai_until_human_input(max_steps=max(0, int(force_steps)))
        if steps > 0:
            self.simulation_last_advanced_at = time.time()
        return steps

    def _start_blocker(self) -> Optional[str]:
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
                    return f"{team_name(team_id)} 的随机武将配额之和必须等于 n = {self.random_roster_size}。"
            return None
        for seat in sorted(self.seats.values(), key=lambda item: item.player_id):
            if seat.hero_total_count <= 0:
                return f"席位 {seat.player_id} 还没有配置武将。"
        return None

    def create_host(self, player_name: str) -> tuple[int, str]:
        with self._lock:
            seat = self.seats[self.host_player_id]
            token = seat.claim(player_name)
            self.touch()
            return seat.player_id, token

    def join(self, player_name: str) -> tuple[int, str]:
        with self._lock:
            if self.status != "lobby":
                existing = self.seat_for_name(player_name)
                if existing is not None:
                    token = existing.reclaim(player_name)
                    self.touch()
                    return existing.player_id, token
                raise RoomError("对局已经开始，只能用原来的昵称恢复原席位。")
            seat = self.open_seat()
            if seat is None:
                existing = self.seat_for_name(player_name)
                if existing is not None:
                    token = existing.reclaim(player_name)
                    self.touch()
                    return existing.player_id, token
                raise RoomError("房间已经满员。")
            token = seat.claim(player_name)
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
                self.touch()
                return
            if seat.is_human:
                raise RoomError("已有真人加入的席位不能直接改成 AI。")
            seat.set_ai()
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
            if hero_code not in hero_lookup():
                raise RoomError("所选武将不存在。")
            seat.adjust_hero_count(hero_code, normalize_hero_delta(delta))
            self.touch()

    def can_start(self) -> bool:
        return self._start_blocker() is None

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
        return entries

    def start_battle(self, token: str) -> None:
        with self._lock:
            self.require_seat(token)
            if self.status != "lobby":
                raise RoomError("当前房间已经在对局中。")
            blocker = self._start_blocker()
            if blocker is not None:
                raise RoomError(blocker)
            if self.mode == "random":
                assignments = self._team_random_rosters()
                for seat in self.seats.values():
                    seat.replace_roster(assignments[seat.team_id].get(seat.player_id, []))
            player1_entries = self._battle_entries_for_team(1)
            player2_entries = self._battle_entries_for_team(2)
            self.battle = create_room_battle(player1_entries, player2_entries, mode=self.mode)
            self.status = "battle"
            self._resolve_ai_until_human_input()
            self.touch()

    def restart_lobby(self, token: str) -> None:
        with self._lock:
            self.require_seat(token)
            if self.status != "finished":
                raise RoomError("只有对局结束后，才能重新开始选将。")
            self.battle = None
            self.status = "lobby"
            for seat in self.seats.values():
                seat.clear_roster()
            if self.mode == "random":
                self._reset_random_quotas_to_defaults()
            self.touch()

    def leave(self, token: str) -> int:
        with self._lock:
            seat = self.require_seat(token)
            if self.status == "battle":
                raise RoomError("对局进行中不能直接离开房间，请先投降或等待对局结束。")
            leaving_player_id = seat.player_id
            seat.release()
            if leaving_player_id == self.host_player_id:
                self.host_player_id = self._first_human_player_id() or 1
            self.touch()
            return leaving_player_id

    def surrender(self, token: str) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if self.battle is None or self.status != "battle":
                raise RoomError("当前房间不在对局中，不能投降。")
            winner = 2 if seat.team_id == 1 else 1
            self.battle.pending_chain = None
            self.battle.pending_respawn_unit_ids = []
            self.battle.winner = winner
            self.battle.log(
                f"{seat.name or f'\u5e2d\u4f4d {seat.player_id}'} "
                f"\u6295\u964d\u3002{team_name(winner)}\u83b7\u80dc\u3002"
            )
            self.status = "finished"
            self.touch()

    def current_input_player_id(self) -> Optional[int]:
        if self.battle is None:
            return None
        return int(self.battle.to_public_dict()["input_player"])

    def _unit_owner_seat_id(self, unit: Unit | None) -> Optional[int]:
        if self.battle is None:
            return None
        return battle_unit_owner_seat_id(self.battle, unit)

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

    def _resolve_ai_until_human_input(self) -> None:
        if self.battle is None or self.battle.winner is not None:
            self.status = "finished" if self.battle and self.battle.winner is not None else self.status
            return
        safety = 0
        while self.battle is not None and self.battle.winner is None and safety < 512:
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
                    self.battle.perform_action(payload)
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
                    self.battle.perform_action(payload or {"type": "chain_skip"})
                else:
                    instant_payload = self._choose_ai_instant_payload()
                    if instant_payload is not None:
                        self.battle.perform_action(instant_payload)
                    else:
                        seat = self._current_prompt_seat()
                        if seat is None or not seat.is_ai:
                            break
                        current_unit = self.battle.current_turn_unit()
                        if current_unit is None:
                            break
                        difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                        self.battle.perform_action(choose_turn_action(self.battle, current_unit, difficulty))
            except ActionError:
                seat = self._current_prompt_seat()
                if seat is None or not seat.is_ai:
                    break
                if self.battle.pending_chain is not None:
                    self.battle.perform_action({"type": "chain_skip"})
                elif self.battle.current_respawn_prompt() is not None:
                    prompt = self.battle.current_respawn_prompt()
                    if prompt is None:
                        break
                    unit = self.battle.get_unit(prompt.unit_id)
                    options = sorted(self.battle.respawn_options_for(unit), key=lambda item: (item.x, item.y))
                    if not options:
                        break
                    fallback = options[0]
                    self.battle.perform_action(
                        {"type": "respawn_select", "unit_id": unit.unit_id, "x": fallback.x, "y": fallback.y}
                    )
                else:
                    self.battle.perform_action({"type": "end_turn"})
            safety += 1
        self.status = "finished" if self.battle and self.battle.winner is not None else "battle"

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
            if not seat.is_ai or seat.team_id == active_team:
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

    def perform_action(self, token: str, payload: dict[str, Any]) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if self.battle is None:
                raise RoomError("当前房间还没有开始对局。")
            current_player = self.current_input_player_id()
            instant_override = self.allows_instant_action_override(seat, payload)
            if current_player != seat.team_id and not instant_override:
                raise RoomError("现在还没轮到你这边操作。")
            actor_unit_id = payload.get("unit_id")
            if actor_unit_id:
                actor = self.battle.get_unit(str(actor_unit_id))
                if actor.player_id != seat.team_id:
                    raise RoomError("不能操作对方单位。")
                if self._unit_owner_seat_id(actor) != seat.player_id:
                    raise RoomError("不能操作同队其他席位拥有的单位。")
            elif not instant_override:
                responsible_seat = self._current_prompt_seat()
                if responsible_seat is not None and responsible_seat.player_id != seat.player_id:
                    raise RoomError("现在还没轮到你控制的单位。")
            try:
                self.battle.perform_action(payload)
            except ActionError as exc:
                raise RoomError(str(exc)) from exc
            self._resolve_ai_until_human_input()
            self.status = "finished" if self.battle.winner is not None else "battle"
            self.touch()

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
                "status": self.status,
                "mode": mode_meta["code"],
                "mode_name": mode_meta["name"],
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "invite_path": self.invite_path(),
                "invite_url": self.invite_url(base_url),
                "host_player_id": self.host_player_id,
                "random_roster_size": self.random_roster_size,
                "default_ai_difficulty": self.default_ai_difficulty,
                "occupied_seat_count": occupied_count,
                "human_seat_count": self.human_seat_count(),
                "ai_seat_count": self.ai_seat_count(),
                "seat_count": len(self.seats),
                "is_full": is_full,
                "can_join": self.status == "lobby" and any(seat.can_join for seat in self.seats.values()),
                "can_start": self.can_start(),
                "start_blocker": self._start_blocker(),
                "can_rematch": self.status == "finished",
                "seats": seats,
            }

    def serialize_state(self, viewer_token: Optional[str] = None, *, base_url: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            viewer = self.seat_for_token(viewer_token)
            viewer_player_id = viewer.player_id if viewer else None
            viewer_team_id = viewer.team_id if viewer else None
            viewer_name = viewer.name if viewer else None
            heroes_by_code = hero_lookup()
            mode_meta = room_mode_payload(self.mode)
            room_state = {
                "room_id": self.room_id,
                "status": self.status,
                "mode": mode_meta["code"],
                "mode_name": mode_meta["name"],
                "mode_description": mode_meta["description"],
                "available_modes": room_mode_list_payload(),
                "version": self.version,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "invite_path": self.invite_path(),
                "invite_url": self.invite_url(base_url),
                "host_player_id": self.host_player_id,
                "random_roster_size": self.random_roster_size,
                "default_ai_difficulty": self.default_ai_difficulty,
                "seat_count": len(self.seats),
                "seat_count_min": MIN_ROOM_SEAT_COUNT,
                "seat_count_max": MAX_ROOM_SEAT_COUNT,
                "viewer_player_id": viewer_player_id,
                "viewer_team_id": viewer_team_id,
                "viewer_name": viewer_name,
                "viewer_is_host": viewer_player_id == self.host_player_id if viewer_player_id is not None else False,
                "occupied_seat_count": self.occupied_seat_count(),
                "human_seat_count": self.human_seat_count(),
                "ai_seat_count": self.ai_seat_count(),
                "is_full": all(seat.occupied for seat in self.seats.values()),
                "can_start": self.can_start(),
                "start_blocker": self._start_blocker(),
                "can_rematch": self.status == "finished",
                "seats": [seat.to_public_dict(heroes_by_code, self.host_player_id) for seat in self.seats.values()],
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

    def _start_blocker(self) -> Optional[str]:
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
            "can_control": viewer is not None and viewer.player_id == self.host_player_id and self._simulation_enabled(),
            "live_step_index": self.replay.last_index if self.replay is not None and self.replay.step_count > 0 else 0,
        }

    def start_battle(self, token: str) -> None:
        with self._lock:
            self.require_seat(token)
            if self.status != "lobby":
                raise RoomError("å½“å‰æˆ¿é—´å·²ç»åœ¨å¯¹å±€ä¸­ã€‚")
            blocker = self._start_blocker()
            if blocker is not None:
                raise RoomError(blocker)
            if self.mode == "random":
                assignments = self._team_random_rosters()
                for seat in self.seats.values():
                    seat.replace_roster(assignments[seat.team_id].get(seat.player_id, []))
            player1_entries = self._battle_entries_for_team(1)
            player2_entries = self._battle_entries_for_team(2)
            self.battle = create_room_battle(player1_entries, player2_entries, mode=self.mode)
            self.replay = ReplayRecorder(self.room_id, self.mode)
            self.simulation_paused = False
            self.simulation_speed = DEFAULT_SIMULATION_SPEED
            self.simulation_last_advanced_at = time.time()
            self.status = "battle"
            self._record_replay_step("battle_start")
            if not self._simulation_enabled():
                self._resolve_ai_until_human_input()
            self.touch()

    def restart_lobby(self, token: str) -> None:
        with self._lock:
            self.require_seat(token)
            if self.status != "finished":
                raise RoomError("åªæœ‰å¯¹å±€ç»“æŸåŽï¼Œæ‰èƒ½é‡æ–°å¼€å§‹é€‰å°†ã€‚")
            self.battle = None
            self.replay = None
            self.simulation_paused = False
            self.simulation_speed = DEFAULT_SIMULATION_SPEED
            self.simulation_last_advanced_at = None
            self.status = "lobby"
            for seat in self.seats.values():
                seat.clear_roster()
            if self.mode == "random":
                self._reset_random_quotas_to_defaults()
            self.touch()

    def surrender(self, token: str) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if self.battle is None or self.status != "battle":
                raise RoomError("å½“å‰æˆ¿é—´ä¸åœ¨å¯¹å±€ä¸­ï¼Œä¸èƒ½æŠ•é™ã€‚")
            winner = 2 if seat.team_id == 1 else 1
            self.battle.pending_chain = None
            self.battle.pending_respawn_unit_ids = []
            self.battle.winner = winner
            self.battle.log(f"{seat.name or f'å¸­ä½ {seat.player_id}'} æŠ•é™ã€‚{team_name(winner)}èŽ·èƒœã€‚")
            self.status = "finished"
            self._record_replay_step("surrender")
            self._ensure_replay_saved()
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
                    else:
                        seat = self._current_prompt_seat()
                        if seat is None or not seat.is_ai:
                            break
                        current_unit = self.battle.current_turn_unit()
                        if current_unit is None:
                            break
                        difficulty = seat.ai_difficulty_override or self.default_ai_difficulty
                        self._perform_battle_action(
                            choose_turn_action(self.battle, current_unit, difficulty),
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

    def perform_action(self, token: str, payload: dict[str, Any]) -> None:
        with self._lock:
            seat = self.require_seat(token)
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
                self._perform_battle_action(payload, reason="player_action")
            except ActionError as exc:
                raise RoomError(str(exc)) from exc
            if not self._simulation_enabled():
                self._resolve_ai_until_human_input()
            elif self.battle is not None and self.battle.winner is None:
                self.simulation_last_advanced_at = time.time()
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
            if self._simulation_enabled():
                self._advance_simulation_due()
            viewer_player_id = viewer.player_id if viewer else None
            viewer_team_id = viewer.team_id if viewer else None
            viewer_name = viewer.name if viewer else None
            heroes_by_code = hero_lookup()
            mode_meta = room_mode_payload(self.mode)
            room_state = {
                "room_id": self.room_id,
                "status": self.status,
                "mode": mode_meta["code"],
                "mode_name": mode_meta["name"],
                "mode_description": mode_meta["description"],
                "available_modes": room_mode_list_payload(),
                "version": self.version,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "invite_path": self.invite_path(),
                "invite_url": self.invite_url(base_url),
                "host_player_id": self.host_player_id,
                "random_roster_size": self.random_roster_size,
                "default_ai_difficulty": self.default_ai_difficulty,
                "seat_count": len(self.seats),
                "seat_count_min": MIN_ROOM_SEAT_COUNT,
                "seat_count_max": MAX_ROOM_SEAT_COUNT,
                "viewer_player_id": viewer_player_id,
                "viewer_team_id": viewer_team_id,
                "viewer_name": viewer_name,
                "viewer_is_host": viewer_player_id == self.host_player_id if viewer_player_id is not None else False,
                "occupied_seat_count": self.occupied_seat_count(),
                "human_seat_count": self.human_seat_count(),
                "ai_seat_count": self.ai_seat_count(),
                "is_full": all(seat.occupied for seat in self.seats.values()),
                "can_start": self.can_start(),
                "start_blocker": self._start_blocker(),
                "can_rematch": self.status == "finished",
                "seats": [seat.to_public_dict(heroes_by_code, self.host_player_id) for seat in self.seats.values()],
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

    def create_room(self, player_name: str, mode: str = DEFAULT_ROOM_MODE) -> tuple[GameRoom, int, str]:
        with self._lock:
            room = GameRoom(self._generate_room_id(), mode=mode)
            player_id, token = room.create_host(player_name)
            self._rooms[room.room_id] = room
            return room, player_id, token

    def get_room(self, room_id: str) -> GameRoom:
        normalized = normalize_room_id(room_id)
        with self._lock:
            room = self._rooms.get(normalized)
        if room is None:
            raise RoomError("房间不存在，可能是房间码输错了。")
        return room

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
        return [room.serialize_summary(base_url=base_url) for room in rooms]


ROOMS = RoomRegistry()
