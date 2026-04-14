from __future__ import annotations

import random
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from wujiang.engine.core import ActionError, Battle
from wujiang.heroes.registry import create_battle, list_heroes


ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 6
DEFAULT_ROOM_MODE = "classic"
ROOM_MODES: dict[str, dict[str, str]] = {
    "classic": {
        "name": "标准选将",
        "description": "双方先各自选将，在 8x8 战场固定出生后开始对局。",
    },
    "random": {
        "name": "随机选人",
        "description": "双方无需手动选将，开局后随机分配武将，使用更大的战场、随机出生，并按能力值决定先手。",
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


def room_mode_payload(mode: str) -> dict[str, str]:
    normalized = normalize_room_mode(mode)
    meta = ROOM_MODES[normalized]
    return {
        "code": normalized,
        "name": meta["name"],
        "description": meta["description"],
    }


def room_mode_list_payload() -> list[dict[str, str]]:
    return [room_mode_payload(code) for code in ROOM_MODES]


def random_room_hero_codes() -> tuple[str, str]:
    hero_codes = tuple(hero_lookup().keys())
    return random.choice(hero_codes), random.choice(hero_codes)


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


def battle_state_for_viewer(battle: Battle, viewer_player_id: Optional[int]) -> dict[str, Any]:
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
    state["active_units"] = []
    if viewer_player_id is not None and viewer_player_id == input_player:
        state["active_units"] = [
            {
                "unit_id": unit.unit_id,
                "name": unit.name,
                "actions": battle.action_snapshot_for(unit),
                "reactions": battle.reaction_snapshot_for(unit),
            }
            for unit in battle.player_units(input_player)
        ]
    apply_private_clone_labels(state, viewer_player_id)
    return state


@dataclass(slots=True)
class PlayerSeat:
    player_id: int
    token: Optional[str] = None
    name: str = ""
    hero_code: Optional[str] = None
    joined_at: Optional[float] = None
    last_seen_at: Optional[float] = None

    @property
    def occupied(self) -> bool:
        return bool(self.token)

    def claim(self, player_name: str) -> str:
        if self.occupied:
            raise RoomError(f"玩家 {self.player_id} 的席位已被占用。")
        self.token = secrets.token_urlsafe(18)
        self.name = normalize_player_name(player_name)
        self.joined_at = time.time()
        self.last_seen_at = self.joined_at
        return self.token

    def release(self) -> None:
        self.token = None
        self.name = ""
        self.hero_code = None
        self.joined_at = None
        self.last_seen_at = None

    def mark_seen(self) -> None:
        if self.occupied:
            self.last_seen_at = time.time()

    def to_public_dict(self, heroes_by_code: dict[str, dict[str, Any]], host_player_id: int) -> dict[str, Any]:
        hero = heroes_by_code.get(self.hero_code or "")
        return {
            "player_id": self.player_id,
            "occupied": self.occupied,
            "name": self.name or None,
            "hero_code": self.hero_code,
            "hero_name": hero["name"] if hero else None,
            "is_host": self.player_id == host_player_id,
        }


class GameRoom:
    def __init__(self, room_id: str, *, mode: str = DEFAULT_ROOM_MODE) -> None:
        self.room_id = normalize_room_id(room_id)
        self.mode = normalize_room_mode(mode)
        self.host_player_id = 1
        self.seats = {
            1: PlayerSeat(player_id=1),
            2: PlayerSeat(player_id=2),
        }
        self.battle: Optional[Battle] = None
        self.status = "lobby"
        self.version = 0
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._lock = threading.RLock()

    def touch(self) -> None:
        self.version += 1
        self.updated_at = time.time()

    def occupied_seat_count(self) -> int:
        return sum(1 for seat in self.seats.values() if seat.occupied)

    def seat_for_token(self, token: Optional[str]) -> Optional[PlayerSeat]:
        if not token:
            return None
        for seat in self.seats.values():
            if seat.token == token:
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
        for player_id in (1, 2):
            seat = self.seats[player_id]
            if not seat.occupied:
                return seat
        return None

    def _first_occupied_player_id(self) -> Optional[int]:
        for player_id in (1, 2):
            if self.seats[player_id].occupied:
                return player_id
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
                raise RoomError("对局已经开始，当前房间不能再加入新的玩家。")
            seat = self.open_seat()
            if seat is None:
                raise RoomError("房间已经满员。")
            token = seat.claim(player_name)
            self.touch()
            return seat.player_id, token

    def select_hero(self, token: str, hero_code: str) -> None:
        with self._lock:
            if self.status != "lobby":
                raise RoomError("对局已经开始，不能再更改武将。")
            if self.mode == "random":
                raise RoomError("随机选人模式下不需要手动选将。")
            seat = self.require_seat(token)
            if hero_code not in hero_lookup():
                raise RoomError("所选武将不存在。")
            seat.hero_code = hero_code
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
                seat.hero_code = None
            self.touch()

    def can_start(self) -> bool:
        if self.battle is not None:
            return False
        if not all(seat.occupied for seat in self.seats.values()):
            return False
        if self.mode == "random":
            return True
        return all(seat.hero_code for seat in self.seats.values())

    def start_battle(self, token: str) -> None:
        with self._lock:
            self.require_seat(token)
            if self.status != "lobby":
                raise RoomError("当前房间已经在对局中。")
            if not self.can_start():
                raise RoomError("需要双方都加入房间并各自准备完成后，才能开始对局。")
            player1_code = self.seats[1].hero_code
            player2_code = self.seats[2].hero_code
            if self.mode == "random":
                player1_code, player2_code = random_room_hero_codes()
                self.seats[1].hero_code = player1_code
                self.seats[2].hero_code = player2_code
            self.battle = create_battle(str(player1_code), str(player2_code), mode=self.mode)
            self.status = "battle"
            self.touch()

    def restart_lobby(self, token: str) -> None:
        with self._lock:
            self.require_seat(token)
            if self.status != "finished":
                raise RoomError("只有对局结束后，才能重新开始选将。")
            self.battle = None
            self.status = "lobby"
            for seat in self.seats.values():
                seat.hero_code = None
            self.touch()

    def leave(self, token: str) -> int:
        with self._lock:
            seat = self.require_seat(token)
            if self.status == "battle":
                raise RoomError("对局进行中不能直接离开房间，请先投降或等待对局结束。")
            leaving_player_id = seat.player_id
            seat.release()
            if leaving_player_id == self.host_player_id:
                self.host_player_id = self._first_occupied_player_id() or 1
            self.touch()
            return leaving_player_id

    def surrender(self, token: str) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if self.battle is None or self.status != "battle":
                raise RoomError("当前房间不在对局中，不能投降。")
            winner = 2 if seat.player_id == 1 else 1
            self.battle.pending_chain = None
            self.battle.pending_respawn_unit_ids = []
            self.battle.winner = winner
            self.battle.log(f"玩家 {seat.player_id} 投降。玩家 {winner} 获胜。")
            self.status = "finished"
            self.touch()

    def current_input_player_id(self) -> Optional[int]:
        if self.battle is None:
            return None
        return int(self.battle.to_public_dict()["input_player"])

    def perform_action(self, token: str, payload: dict[str, Any]) -> None:
        with self._lock:
            seat = self.require_seat(token)
            if self.battle is None:
                raise RoomError("当前房间还没有开始对局。")
            current_player = self.current_input_player_id()
            if current_player != seat.player_id:
                raise RoomError("现在还没轮到你操作。")
            actor_unit_id = payload.get("unit_id")
            if actor_unit_id:
                actor = self.battle.get_unit(str(actor_unit_id))
                if actor.player_id != seat.player_id:
                    raise RoomError("不能操作对方单位。")
            try:
                self.battle.perform_action(payload)
            except ActionError as exc:
                raise RoomError(str(exc)) from exc
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
            occupied_count = sum(1 for seat in self.seats.values() if seat.occupied)
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
                "occupied_seat_count": occupied_count,
                "seat_count": len(self.seats),
                "is_full": is_full,
                "can_join": self.status == "lobby" and not is_full,
                "can_start": self.can_start(),
                "can_rematch": self.status == "finished",
                "seats": seats,
            }

    def serialize_state(self, viewer_token: Optional[str] = None, *, base_url: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            viewer = self.seat_for_token(viewer_token)
            viewer_player_id = viewer.player_id if viewer else None
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
                "viewer_player_id": viewer_player_id,
                "viewer_name": viewer_name,
                "viewer_is_host": viewer_player_id == self.host_player_id if viewer_player_id is not None else False,
                "can_start": self.can_start(),
                "can_rematch": self.status == "finished",
                "is_full": all(seat.occupied for seat in self.seats.values()),
                "seats": [seat.to_public_dict(heroes_by_code, self.host_player_id) for seat in self.seats.values()],
            }
            battle_state = battle_state_for_viewer(self.battle, viewer_player_id) if self.battle is not None else None
            return {
                "heroes": heroes_catalog(),
                "room": room_state,
                "battle": battle_state,
            }


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
            deleted = room.occupied_seat_count() == 0
            if deleted:
                del self._rooms[normalized]
            return deleted, leaving_player_id

    def list_rooms(self, *, base_url: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            rooms = list(self._rooms.values())
        rooms.sort(key=lambda room: room.updated_at, reverse=True)
        return [room.serialize_summary(base_url=base_url) for room in rooms]


ROOMS = RoomRegistry()
