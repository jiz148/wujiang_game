"""Saved skill-random heroes.

These records stay out of HERO_REGISTRY and list_heroes(), so other modes
never draw them. The main-menu database reads this file directly.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

STORE_PATH = Path(__file__).resolve().parents[3] / "data" / "custom-heroes.json"
STAT_KEYS = ("attack", "defense", "speed", "attack_range", "mana")
_LOCK = threading.Lock()


class CustomHeroError(ValueError):
    pass


def _load_unlocked() -> list[dict[str, Any]]:
    if not STORE_PATH.exists():
        return []
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CustomHeroError("自建武将库读不出来，这次没有写入。") from exc
    heroes = data.get("heroes") if isinstance(data, dict) else None
    if not isinstance(heroes, list):
        raise CustomHeroError("自建武将库读不出来，这次没有写入。")
    return [item for item in heroes if isinstance(item, dict)]


def _write_unlocked(heroes: list[dict[str, Any]]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STORE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"heroes": heroes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(STORE_PATH)


def list_custom_heroes() -> list[dict[str, Any]]:
    with _LOCK:
        return _load_unlocked()


def _part(index: dict[str, dict[str, Any]], part_id: str, kind: str) -> dict[str, str]:
    entry = index.get(part_id)
    if entry is None or entry.get("kind") != kind:
        label = "技能" if kind == "skill" else "特性"
        raise CustomHeroError(f"有{label}不在可选列表里。")
    return {
        "id": part_id,
        "name": str(entry.get("name") or "未命名"),
        "description": str(entry.get("description") or ""),
    }


def save_custom_hero(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CustomHeroError("武将配置无效。")
    try:
        level = int(raw.get("level"))
    except (TypeError, ValueError):
        raise CustomHeroError("等级要是 1 到 10 的整数。") from None
    if level < 1 or level > 10:
        raise CustomHeroError("等级要是 1 到 10 的整数。")
    from wujiang.tactical.rooms.forge import public_catalog

    catalog = public_catalog()
    index = {str(item.get("id")): item for item in list(catalog.get("skills") or []) + list(catalog.get("traits") or [])}
    name = str(raw.get("name") or "未命名").strip()[:12] or "未命名"
    stats: dict[str, int] = {}
    for key in STAT_KEYS:
        try:
            value = int(raw.get(key) if raw.get(key) is not None else 0)
        except (TypeError, ValueError):
            raise CustomHeroError("属性必须是整数。") from None
        if value < 0 or value > 12:
            raise CustomHeroError("属性要在 0 到 12 之间。")
        stats[key] = value
    skill_ids = list(dict.fromkeys(str(item) for item in (raw.get("skills") or []) if str(item)))
    trait_ids = list(dict.fromkeys(str(item) for item in (raw.get("traits") or []) if str(item)))
    record = {
        "id": f"custom-{uuid.uuid4().hex[:12]}",
        "origin": "custom",
        "tags": ["自建"],
        "name": name,
        "role": str(raw.get("role") or "").strip()[:24],
        "attribute": str(raw.get("attribute") or "").strip()[:24],
        "race": str(raw.get("race") or "").strip()[:24],
        "level": level,
        "intro": str(raw.get("intro") or "").strip()[:240],
        "stats": stats,
        "skills": [_part(index, part_id, "skill") for part_id in skill_ids],
        "traits": [_part(index, part_id, "trait") for part_id in trait_ids],
        "created_at": int(time.time()),
    }
    with _LOCK:
        heroes = _load_unlocked()
        heroes.append(record)
        _write_unlocked(heroes)
    return record


def delete_custom_hero(hero_id: str) -> None:
    target = str(hero_id or "").strip()
    if not target.startswith("custom-"):
        raise CustomHeroError("没有这名自建武将。")
    with _LOCK:
        heroes = _load_unlocked()
        kept = [item for item in heroes if str(item.get("id") or "") != target]
        if len(kept) == len(heroes):
            raise CustomHeroError("没有这名自建武将。")
        _write_unlocked(kept)
