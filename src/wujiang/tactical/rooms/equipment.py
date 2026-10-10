"""Read the supplied equipment workbook without a server-side Excel dependency.

The workbook is an authoritative content source. It contains names, attribute
changes, and effect prose, but no rarity, price, or equipment size columns.
"""
from __future__ import annotations

import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


WORKBOOK = Path(__file__).resolve().parents[4] / "data" / "装备.xlsx"
NAMESPACE = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
STAT_NAMES = {"攻": "attack", "守": "defense", "速": "speed", "范": "attack_range",
              "范围": "attack_range", "魔": "mana", "血上限": "max_health"}
STAT_RE = re.compile(r"(血上限|范围|攻|守|速|范|魔)\s*([+-])\s*(\d+(?:\.\d+)?)")


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//m:t", NAMESPACE))
    value = cell.find("m:v", NAMESPACE)
    if value is None:
        return ""
    if kind == "s":
        return shared[int(value.text or 0)]
    return value.text or ""


@lru_cache(maxsize=1)
def load_equipment_catalog() -> dict[str, dict[str, Any]]:
    if not WORKBOOK.exists():
        return {}
    with zipfile.ZipFile(WORKBOOK) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for entry in strings.findall("m:si", NAMESPACE):
                shared.append("".join(node.text or "" for node in entry.findall(".//m:t", NAMESPACE)))
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in sheet.findall(".//m:sheetData/m:row", NAMESPACE):
            values = ["", "", ""]
            for cell in row.findall("m:c", NAMESPACE):
                match = re.match(r"([A-Z]+)", cell.attrib.get("r", ""))
                if match and match.group(1) in ("A", "B", "C"):
                    values[ord(match.group(1)) - ord("A")] = _cell_text(cell, shared).strip()
            rows.append(values)
    if not rows or rows[0] != ["名称", "属性", "效果"]:
        raise ValueError("装备.xlsx 表头必须为：名称、属性、效果。")
    catalog: dict[str, dict[str, Any]] = {}
    for index, (name, attributes, effect) in enumerate(rows[1:], start=2):
        if not name:
            continue
        if name in catalog:
            raise ValueError(f"装备.xlsx 第 {index} 行装备名称重复：{name}")
        stats: dict[str, float] = {}
        for stat_name, sign, raw_amount in STAT_RE.findall(attributes):
            key = STAT_NAMES[stat_name]
            amount = float(raw_amount) * (1 if sign == "+" else -1)
            stats[key] = stats.get(key, 0.0) + amount
        catalog[name] = {"code": name, "name": name, "attributes": attributes or "无",
                         "effect": effect or "无", "stats": stats, "weight": 1,
                         "source_row": index}
    return catalog
