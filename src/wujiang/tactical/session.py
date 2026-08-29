"""Single-player demo session kept for the legacy /api/state endpoints."""
from __future__ import annotations

from typing import Any

from wujiang.tactical.heroes.registry import list_heroes
from wujiang.tactical.rooms.multiplayer import battle_state_for_viewer
class GameSession:
    def __init__(self) -> None:
        self.battle = None

    def serialize_state(self) -> dict[str, Any]:
        if self.battle is None:
            return {"battle": None, "heroes": list_heroes()}
        input_player = self.battle.to_public_dict()["input_player"]
        state = battle_state_for_viewer(self.battle, input_player)
        return {"battle": state, "heroes": list_heroes()}





def extract_room_action(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("action")
    if isinstance(nested, dict):
        return nested
    return {
        key: value
        for key, value in payload.items()
        if key not in {"room_id", "player_token", "player_name", "hero_code", "delta"}
    }



SESSION = GameSession()
