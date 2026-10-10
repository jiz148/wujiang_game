"""Weather descriptions shared by roster, room, and battle views."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.heroes.weather_effects_data import (
    WEATHER_HERO_EFFECTS_BY_NAME,
    WEATHER_HERO_EFFECTS_BY_ROW,
)


def hero_weather_effect_text(hero: Any) -> str:
    row = getattr(hero, "source_row", None)
    if row is not None:
        description = WEATHER_HERO_EFFECTS_BY_ROW.get(int(row))
        if description:
            return description
    return WEATHER_HERO_EFFECTS_BY_NAME.get(str(getattr(hero, "name", "")), "")
