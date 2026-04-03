from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from wujiang.engine.core import ActionError
from wujiang.heroes.registry import create_battle, list_heroes
from wujiang.web.multiplayer import ROOMS, RoomError


PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_ROOT = PROJECT_ROOT / "static"
PUBLIC_BASE_URL: str | None = None


class GameSession:
    def __init__(self) -> None:
        self.battle = None

    def serialize_state(self) -> dict[str, Any]:
        if self.battle is None:
            return {"battle": None, "heroes": list_heroes()}
        state = self.battle.to_public_dict()
        input_player = state["input_player"]
        state["active_units"] = [
            {
                "unit_id": unit.unit_id,
                "name": unit.name,
                "actions": self.battle.action_snapshot_for(unit),
                "reactions": self.battle.reaction_snapshot_for(unit),
            }
            for unit in self.battle.player_units(input_player)
        ]
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


def extract_room_action(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("action")
    if isinstance(nested, dict):
        return nested
    return {key: value for key, value in payload.items() if key not in {"room_id", "player_token", "player_name", "hero_code"}}


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
            json_response(self, HTTPStatus.OK, room.serialize_state(player_token, base_url=request_base_url(self)))
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = request_json(self)
        except json.JSONDecodeError:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "请求体不是有效 JSON。"})
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

        if parsed.path == "/api/rooms/create":
            player_name = payload.get("player_name", "")
            room, player_id, player_token = ROOMS.create_room(str(player_name))
            response = room.serialize_state(player_token, base_url=request_base_url(self))
            response["player_token"] = player_token
            response["joined_player_id"] = player_id
            json_response(self, HTTPStatus.OK, response)
            return

        if parsed.path == "/api/rooms/join":
            room_id = payload.get("room_id", "")
            player_name = payload.get("player_name", "")
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
            try:
                room = ROOMS.get_room(str(room_id))
                room.select_hero(str(player_token or ""), str(hero_code))
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
            json_response(self, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(self)))
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
            json_response(self, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(self)))
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
            json_response(self, HTTPStatus.OK, room.serialize_state(str(player_token or ""), base_url=request_base_url(self)))
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
