"""Auto chess synergy catalog and battle components.

The workbook is authoritative for displayed tier descriptions. Behavior for
each tag lives in :mod:`synergy_effects`.
"""
from __future__ import annotations

import re
import zipfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from wujiang.tactical.rooms.synergy_effects import SUPPORTED_TAGS, SynergyStatus, SynergyField


CATEGORY_LABELS = {"role": "职业", "attribute": "属性", "race": "种族"}
_CATEGORY_KEYS = {value: key for key, value in CATEGORY_LABELS.items()}
_WORKBOOK = Path(__file__).resolve().parents[4] / "data" / "自走棋羁绊效果.xlsx"
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@lru_cache(maxsize=1)
def synergy_catalog() -> dict[tuple[str, str, int], str]:
    """Read the source workbook without a server-side Excel dependency."""
    with zipfile.ZipFile(_WORKBOOK) as archive:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(t.text or "" for t in item.findall(".//m:t", _NS))
                       for item in root.findall("m:si", _NS)]
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in root.findall(".//m:sheetData/m:row", _NS):
            values = ["", "", "", ""]
            for cell in row.findall("m:c", _NS):
                match = re.match(r"([A-Z]+)", cell.get("r", ""))
                if match is None or match.group(1) not in ("A", "B", "C", "D"):
                    continue
                value = cell.find("m:v", _NS)
                inline = cell.find("m:is", _NS)
                if value is not None:
                    text = strings[int(value.text or 0)] if cell.get("t") == "s" else value.text or ""
                else:
                    text = "".join(t.text or "" for t in inline.findall(".//m:t", _NS)) if inline is not None else ""
                values[ord(match.group(1)) - ord("A")] = text.strip()
            rows.append(values)
    if not rows or rows[0] != ["条件类别", "羁绊标签", "数量", "效果（可修改）"]:
        raise ValueError("自走棋羁绊效果.xlsx 表头或列顺序有误。")
    result: dict[tuple[str, str, int], str] = {}
    for index, (category, name, count, effect) in enumerate(rows[1:], start=2):
        if not category and not name:
            continue
        if category not in _CATEGORY_KEYS or count not in ("3", "6") or not name or not effect:
            raise ValueError(f"自走棋羁绊效果.xlsx 第 {index} 行条件或效果无效。")
        key = (_CATEGORY_KEYS[category], name, int(count) // 3)
        if key in result:
            raise ValueError(f"自走棋羁绊效果.xlsx 第 {index} 行重复：{key}")
        result[key] = effect
    for category, name, _ in result:
        if (category, name, 1) not in result or (category, name, 2) not in result:
            raise ValueError(f"羁绊 {category}/{name} 缺少 3 人或 6 人效果。")
    if {(category, name) for category, name, _ in result} != SUPPORTED_TAGS:
        raise ValueError("羁绊源表标签与已实现的战斗效果不一致，请同步修改。")
    return result


def synergy_tier(count: int) -> int:
    return 2 if count >= 6 else 1 if count >= 3 else 0


def synergy_description(category: str, key: str, tier: int) -> str:
    return synergy_catalog().get((category, key, tier), "")


def synergy_counts(pieces: list[Any], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Count original fielded chess pieces, never summoned or cloned bodies."""
    result: list[dict[str, Any]] = []
    for category in CATEGORY_LABELS:
        counts = Counter(str(catalog[piece.code][category]) for piece in pieces)
        for key, count in sorted(counts.items()):
            tier = synergy_tier(count)
            result.append({"category": category, "category_name": CATEGORY_LABELS[category],
                           "name": key, "count": count, "tier": tier,
                           "next_threshold": 3 if count < 3 else 6 if count < 6 else None,
                           "effect": synergy_description(category, key, tier) if tier else "",
                           "first": synergy_description(category, key, 1),
                           "second": synergy_description(category, key, 2)})
    return result
