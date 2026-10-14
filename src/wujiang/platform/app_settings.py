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


_CACHE_PATH = ""
_CACHE: dict | None = None


def _read() -> dict:
    path = settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load() -> dict:
    global _CACHE, _CACHE_PATH
    path = str(settings_path())
    if _CACHE is None or _CACHE_PATH != path:
        _CACHE = _read()
        _CACHE_PATH = path
    return _CACHE


def _store(payload: dict) -> None:
    global _CACHE, _CACHE_PATH
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _CACHE = payload
    _CACHE_PATH = str(path)


def replay_recording() -> str:
    override = os.environ.get("WUJIANG_REPLAY_RECORDING", "").strip().lower()
    if override in REPLAY_MODES:
        return override
    value = str(_load().get("replay_recording") or DEFAULT_REPLAY).strip().lower()
    return value if value in REPLAY_MODES else DEFAULT_REPLAY


def simple_ai_enabled() -> bool:
    """True uses the bounded picker. False uses the detailed scorer in ai.py."""
    override = os.environ.get("WUJIANG_SIMPLE_AI", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    value = _load().get("simple_ai", False)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return False


def public_settings() -> dict:
    return {
        "replay_recording": replay_recording(),
        "simple_ai": simple_ai_enabled(),
    }


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("简化 AI 只能打开或关闭。")


def update_settings(patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise ValueError("设置内容无效。")
    payload = dict(_load())
    touched = False
    if "replay_recording" in patch:
        value = str(patch.get("replay_recording") or "").strip().lower()
        if value not in REPLAY_MODES:
            raise ValueError("对战记录只能是 off、actions 或 full。")
        payload["replay_recording"] = value
        touched = True
    if "simple_ai" in patch:
        payload["simple_ai"] = _as_bool(patch.get("simple_ai"))
        touched = True
    if not touched:
        raise ValueError("没有可保存的设置。")
    _store(payload)
    return public_settings()
