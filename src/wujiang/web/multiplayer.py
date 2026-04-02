from __future__ import annotations

import secrets
import string
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from wujiang.engine.core import ActionError, Battle
from wujiang.heroes.registry import create_battle, list_heroes


ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 6


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


def battle_state_for_viewer(battle: Battle, viewer_player_id: Optional[int]) -> dict[str, Any]:
    state = battle.to_public_dict()
    input_player = state["input_player"]
    state["viewer_player_id"] = viewer_player_id
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
    def __init__(self, room_id: str) -> None:
        self.room_id = normalize_room_id(room_id)
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
            raise RoomError("只有房主可以删除房间。")
        return seat

    def open_seat(self) -> Optional[PlayerSeat]:
        for player_id in (1, 2):
            seat = self.seats[player_id]
            if not seat.occupied:
                return seat
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
            seat = self.require_seat(token)
            if hero_code not in hero_lookup():
                raise RoomError("所选武将不存在。")
            seat.hero_code = hero_code
            self.touch()

    def can_start(self) -> bool:
        return all(seat.occupied and seat.hero_code for seat in self.seats.values()) and self.battle is None

    def start_battle(self, token: str) -> None:
        with self._lock:
            self.require_seat(token)
            if self.status != "lobby":
                raise RoomError("当前房间已经在对局中。")
            if not self.can_start():
                raise RoomError("需要双方都加入房间并各自选好武将后，才能开始对局。")
            player1_code = self.seats[1].hero_code
            player2_code = self.seats[2].hero_code
            self.battle = create_battle(str(player1_code), str(player2_code))
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
            return {
                "room_id": self.room_id,
                "status": self.status,
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
            room_state = {
                "room_id": self.room_id,
                "status": self.status,
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

    def create_room(self, player_name: str) -> tuple[GameRoom, int, str]:
        with self._lock:
            room = GameRoom(self._generate_room_id())
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


    def list_rooms(self, *, base_url: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            rooms = list(self._rooms.values())
        rooms.sort(key=lambda room: room.updated_at, reverse=True)
        return [room.serialize_summary(base_url=base_url) for room in rooms]


ROOMS = RoomRegistry()
