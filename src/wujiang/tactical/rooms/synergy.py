"""Auto chess battle-start synergies for role, attribute, and race."""
from __future__ import annotations

import random
from collections import Counter
from typing import Any

from wujiang.tactical.engine.core import Battle, DamageContext, StatusEffect


CATEGORY_LABELS = {"role": "职业", "attribute": "属性", "race": "种族"}
ROLE_STATS = {"剑士": "attack", "法师": "mana", "骑士": "defense",
              "弓箭手": "attack_range", "勇者": "attack", "贤者": "mana"}
ATTRIBUTE_STATS = {"火": "attack", "光": "defense", "雷": "speed",
                   "水": "mana", "风": "speed", "土": "defense"}
RACE_STATS = {"人类": "defense", "机甲": "defense", "精灵": "speed", "灵体": "mana"}
STAT_LABELS = {"attack": "攻击", "defense": "防御", "speed": "速度",
               "attack_range": "范围", "mana": "魔法"}


def synergy_tier(count: int) -> int:
    return 2 if count >= 6 else 1 if count >= 3 else 0


def synergy_stat(category: str, key: str) -> str | None:
    if category == "role":
        return ROLE_STATS.get(key, "speed")
    if category == "attribute":
        return None if key == "暗" else ATTRIBUTE_STATS.get(key, "attack")
    return RACE_STATS.get(key, "speed")


def synergy_description(category: str, key: str, tier: int) -> str:
    if category == "attribute" and key == "暗":
        return f"全体暗属性武将受到可闪避伤害时有 {tier * 10}% 几率闪避"
    stat = synergy_stat(category, key)
    return f"全体{key}武将{STAT_LABELS[stat]} +{tier}"


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
                           "effect": synergy_description(category, key, tier) if tier else ""})
    return result


class SynergyStatus(StatusEffect):
    def __init__(self, category: str, key: str, tier: int) -> None:
        super().__init__(f"自走棋羁绊·{CATEGORY_LABELS[category]}·{key}",
                         synergy_description(category, key, tier))
        self.category = category
        self.key = key
        self.tier = tier

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + self.tier if stat_name == synergy_stat(self.category, self.key) else value

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if (self.category != "attribute" or self.key != "暗" or
                ctx.target is not self.owner or ctx.cancelled or ctx.cannot_evade or ctx.from_field_effect):
            return
        rng = getattr(battle, "chess_rng", None) or random
        if rng.random() < self.tier * 0.1:
            ctx.cancelled = True
            ctx.reason = f"{ctx.target.name} 的暗属性羁绊闪避了伤害。"
