"""Decision log for a later tendency model.

Each row's label is `intent`. Coordinates and skill codes are the footwork
the executor chose afterwards. Do not train an abstract policy on those.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from wujiang.platform.http.runtime import PROJECT_ROOT

SCHEMA = 1


def log_path() -> Path | None:
    flag = os.environ.get("WUJIANG_TENDENCY_LOG", "1").strip().lower()
    if flag in {"0", "off", "false", "no"}:
        return None
    override = os.environ.get("WUJIANG_TENDENCY_LOG_PATH", "").strip()
    if override:
        return Path(override)
    return PROJECT_ROOT / "var" / "tendency-decisions.jsonl"


def record_decision(row: dict[str, Any]) -> None:
    path = log_path()
    if path is None:
        return
    payload = {"schema": SCHEMA, **row}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        return
