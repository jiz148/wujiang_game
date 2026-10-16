"""Weather descriptions shared by roster, room, and battle views."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.heroes.weather_effects_data import (
    WEATHER_HERO_EFFECTS_BY_NAME,
    WEATHER_HERO_EFFECTS_BY_ROW,
)


def hero_weather_effect_text(hero: Any) -> str:
    row = getattr(hero, "source_row", None)
    if row == 263 or str(getattr(hero, "name", "")) == "冰帝。浮鹤":
        return "浮鹤城：除冰帝。浮鹤及同名分身外，双方单位范-3到最低1；正式声明主动技能各有1/2概率失败，失败仍消耗费用、次数与冷却。"
    if row is not None:
        description = WEATHER_HERO_EFFECTS_BY_ROW.get(int(row))
        if description:
            return description
    return WEATHER_HERO_EFFECTS_BY_NAME.get(str(getattr(hero, "name", "")), "")
