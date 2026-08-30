"""Where a grid battle came from, and what the host experience allows.

The tactical room is reused by skirmish lobbies, tutorials, quick AI, and
campaign sieges. Those hosts already decided the roster, the board, and where
the player should go when the fight ends. The room carries that contract so
the battlefield UI does not guess from leftover client state.
"""
from __future__ import annotations

from typing import Any


LAUNCH_SOURCES = ("skirmish", "campaign", "tutorial", "quick_ai")

_LAUNCH_PRESETS: dict[str, dict[str, Any]] = {
    "skirmish": {
        "source": "skirmish",
        "return_flow": "skirmish",
        "allow_lobby": True,
        "allow_rematch": True,
        "allow_roster_edit": True,
    },
    "campaign": {
        "source": "campaign",
        "return_flow": "campaign",
        "allow_lobby": False,
        "allow_rematch": False,
        "allow_roster_edit": False,
    },
    "tutorial": {
        "source": "tutorial",
        "return_flow": "skirmish",
        "allow_lobby": True,
        "allow_rematch": True,
        "allow_roster_edit": False,
    },
    "quick_ai": {
        "source": "quick_ai",
        "return_flow": "skirmish",
        "allow_lobby": True,
        "allow_rematch": True,
        "allow_roster_edit": False,
    },
}


def make_launch_context(source: str = "skirmish", **overrides: Any) -> dict[str, Any]:
    preset = _LAUNCH_PRESETS.get(str(source or "skirmish"), _LAUNCH_PRESETS["skirmish"])
    context = dict(preset)
    for key, value in overrides.items():
        if value is None:
            continue
        if key in {"campaign_id"}:
            context[key] = int(value)
            continue
        if key in {"battle_id"}:
            context[key] = str(value)
            continue
        if key in {"allow_lobby", "allow_rematch", "allow_roster_edit"}:
            context[key] = bool(value)
            continue
        if key in {"source", "return_flow"}:
            context[key] = str(value)
            continue
    return context


def infer_launch_source(room: Any) -> str:
    raw = getattr(room, "launch_context", None)
    if isinstance(raw, dict) and raw.get("source") in LAUNCH_SOURCES:
        return str(raw["source"])
    kind = str(getattr(room, "experience_kind", "") or "")
    if kind in {"strategy_campaign", "campaign"}:
        return "campaign"
    if kind in LAUNCH_SOURCES:
        return kind
    return "skirmish"


def public_launch_context(room: Any) -> dict[str, Any]:
    raw = getattr(room, "launch_context", None)
    extras = raw if isinstance(raw, dict) else {}
    return make_launch_context(
        infer_launch_source(room),
        campaign_id=extras.get("campaign_id"),
        battle_id=extras.get("battle_id"),
        allow_lobby=extras.get("allow_lobby"),
        allow_rematch=extras.get("allow_rematch"),
        allow_roster_edit=extras.get("allow_roster_edit"),
        return_flow=extras.get("return_flow"),
    )


def bind_campaign_launch(room: Any, *, campaign_id: int | None = None, battle_id: str = "") -> None:
    room.experience_kind = "strategy_campaign"
    room.launch_context = make_launch_context(
        "campaign",
        campaign_id=campaign_id,
        battle_id=battle_id,
    )


def is_campaign_launch(room: Any) -> bool:
    return infer_launch_source(room) == "campaign"
