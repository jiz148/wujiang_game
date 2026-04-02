from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wujiang.engine.core import ActionError
from wujiang.heroes.registry import create_battle, list_heroes


PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_ROOT = PROJECT_ROOT / "static"


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


class WujiangHandler(BaseHTTPRequestHandler):
    server_version = "WujiangHTTP/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/heroes":
            json_response(self, HTTPStatus.OK, {"heroes": list_heroes()})
            return
        if parsed.path == "/api/state":
            json_response(self, HTTPStatus.OK, SESSION.serialize_state())
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length) if content_length else b"{}"
            payload = json.loads(body.decode("utf-8"))
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


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), WujiangHandler)
    print(f"Wujiang server running at http://{host}:{port}")
    httpd.serve_forever()
