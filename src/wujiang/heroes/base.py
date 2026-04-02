from __future__ import annotations

from abc import ABC
from itertools import count
from typing import Optional

from wujiang.engine.core import HeroUnit, Stats


_unit_counter = count(1)


def build_unit_id(prefix: str) -> str:
    return f"{prefix}-{next(_unit_counter)}"


class AbstractHero(HeroUnit, ABC):
    hero_code = "hero"
    hero_name = "武将"
    hero_title = ""
    role = ""
    attribute = ""
    race = ""
    level = 1
    base_stats = Stats(attack=1, defense=1, speed=1, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = ""
    max_health = 1.0

    def __init__(self, player_id: int, *, unit_id: Optional[str] = None, is_summon: bool = False) -> None:
        super().__init__(
            unit_id=unit_id or build_unit_id(self.hero_code),
            player_id=player_id,
            name=self.hero_name,
            title=self.hero_title or self.hero_name,
            role=self.role,
            attribute=self.attribute,
            race=self.race,
            level=self.level,
            base_stats=self.base_stats,
            raw_skill_text=self.raw_skill_text,
            raw_trait_text=self.raw_trait_text,
            max_health=self.max_health,
            is_summon=is_summon,
        )
