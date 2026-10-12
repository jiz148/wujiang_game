"""Process-wide settings that are not part of any one room or campaign.

Replay recording used to be unconditional: every battle step stored three full
board snapshots, and there was no way to stop. The setting lives in a small
JSON file so it survives a restart without becoming another database.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from wujiang.platform.http.runtime import PROJECT_ROOT

REPLAY_OFF = "off"
REPLAY_ACTIONS = "actions"
REPLAY_FULL = "full"
REPLAY_MODES = (REPLAY_OFF, REPLAY_ACTIONS, REPLAY_FULL)
DEFAULT_REPLAY = REPLAY_FULL


def settings_path() -> Path:
    override = os.environ.get("WUJIANG_SETTINGS_PATH")
    if override:
        return Path(override)
    return PROJECT_ROOT / "var" / "app-settings.json"


def _read() -> dict:
    path = settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def replay_recording() -> str:
    override = os.environ.get("WUJIANG_REPLAY_RECORDING", "").strip().lower()
    if override in REPLAY_MODES:
        return override
    value = str(_read().get("replay_recording") or DEFAULT_REPLAY).strip().lower()
    return value if value in REPLAY_MODES else DEFAULT_REPLAY


def public_settings() -> dict:
    return {"replay_recording": replay_recording()}


def update_settings(patch: dict) -> dict:
    value = str(patch.get("replay_recording") or "").strip().lower()
    if value not in REPLAY_MODES:
        raise ValueError("对战记录只能是 off、actions 或 full。")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read()
    payload["replay_recording"] = value
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return public_settings()
