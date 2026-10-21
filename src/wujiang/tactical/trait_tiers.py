"""Shared strong/normal labels for implemented traits.

The hero editor and the database read this same file.
Names not listed here default to normal, except 魔免 and 物免.
"""
from __future__ import annotations

import json
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parents[3] / "data" / "trait-tiers.json"
DEFAULT_STRONG = {"魔免", "物免"}
_overrides: dict[str, str] | None = None


class TraitTierError(ValueError):
    pass


def _read() -> dict[str, str]:
    global _overrides
    if _overrides is not None:
        return _overrides
    if not STORE_PATH.exists():
        _overrides = {}
        return _overrides
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TraitTierError("特性分类读不出来。") from exc
    raw = data.get("traits") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        raise TraitTierError("特性分类读不出来。")
    _overrides = {
        str(name): value
        for name, value in raw.items()
        if value in {"strong", "normal"}
    }
    return _overrides


def trait_category(name: str) -> str:
    text = str(name or "")
    saved = _read().get(text)
    if saved in {"strong", "normal"}:
        return saved
    return "strong" if text in DEFAULT_STRONG else "normal"


def trait_point_cost(name: str) -> int:
    return 4 if trait_category(name) == "strong" else 2


def set_trait_category(name: str, category: str) -> str:
    text = str(name or "").strip()
    if not text:
        raise TraitTierError("没有这个特性。")
    if category not in {"strong", "normal"}:
        raise TraitTierError("类别只能是强力或一般。")
    current = _read()
    current[text] = category
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STORE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"traits": current}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(STORE_PATH)
    return category
