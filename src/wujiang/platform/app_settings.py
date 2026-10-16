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


def autochess_replay_enabled() -> bool:
    """Autochess fights stay unrecorded unless this is turned on."""
    override = os.environ.get("WUJIANG_AUTOCHESS_REPLAY", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return _as_bool_or_default(_load().get("autochess_replay", False), False)


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


AI_FULL = "full"
AI_SIMPLE = "simple"
AI_TENDENCY = "tendency"
AI_MODEL = "model"
AI_MODES = (AI_FULL, AI_SIMPLE, AI_TENDENCY, AI_MODEL)


def ai_decision_mode() -> str:
    """full keeps the old scorer, simple is the fixed list, tendency picks an intent."""
    override = os.environ.get("WUJIANG_AI_MODE", "").strip().lower()
    if override in AI_MODES:
        return override
    stored = str(_load().get("ai_mode") or "").strip().lower()
    if stored in AI_MODES:
        return stored
    return AI_SIMPLE if simple_ai_enabled() else AI_FULL


def public_settings() -> dict:
    mode = ai_decision_mode()
    return {
        "replay_recording": replay_recording(),
        "autochess_replay": autochess_replay_enabled(),
        "simple_ai": mode == AI_SIMPLE,
        "ai_mode": mode,
    }


def _as_bool_or_default(value, default: bool | None) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


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
    if "autochess_replay" in patch:
        enabled = _as_bool_or_default(patch.get("autochess_replay"), None)
        if enabled is None:
            raise ValueError("自走棋录像只能打开或关闭。")
        payload["autochess_replay"] = enabled
        touched = True
    if "ai_mode" in patch:
        mode = str(patch.get("ai_mode") or "").strip().lower()
        if mode not in AI_MODES:
            raise ValueError("AI 决策只能是 full、simple、tendency 或 model。")
        payload["ai_mode"] = mode
        payload["simple_ai"] = mode == AI_SIMPLE
        touched = True
    elif "simple_ai" in patch:
        enabled = _as_bool(patch.get("simple_ai"))
        payload["simple_ai"] = enabled
        payload["ai_mode"] = AI_SIMPLE if enabled else AI_FULL
        touched = True
    if not touched:
        raise ValueError("没有可保存的设置。")
    _store(payload)
    return public_settings()
