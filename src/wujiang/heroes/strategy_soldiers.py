from __future__ import annotations

from wujiang.engine.core import Skill, Stats, Trait
from wujiang.heroes.base import AbstractHero


class StrategySoldier(AbstractHero):
    role = "士兵"
    attribute = "土"
    race = "人类"
    level = 1
    base_stats = Stats(attack=2, defense=2, speed=2, attack_range=1, mana=0)
    raw_skill_text = "战略模式基础士兵；无主动技能。"
    raw_trait_text = "战略模式单位。"

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return []


class StrategyInfantry(StrategySoldier):
    hero_code = "strategy_infantry"
    hero_name = "普通步兵"
    role = "步兵"
    base_stats = Stats(attack=2, defense=2, speed=2, attack_range=1, mana=0)


class StrategyGarrison(StrategySoldier):
    hero_code = "strategy_garrison"
    hero_name = "守备兵"
    role = "守备"
    base_stats = Stats(attack=2, defense=3, speed=1, attack_range=1, mana=0)


class StrategyArcher(StrategySoldier):
    hero_code = "strategy_archer"
    hero_name = "弓兵"
    role = "弓兵"
    base_stats = Stats(attack=2, defense=1, speed=2, attack_range=4, mana=0)


class StrategyCavalry(StrategySoldier):
    hero_code = "strategy_cavalry"
    hero_name = "骑兵"
    role = "骑兵"
    base_stats = Stats(attack=3, defense=2, speed=4, attack_range=1, mana=0)


class StrategyMountainSoldier(StrategySoldier):
    hero_code = "strategy_mountain_soldier"
    hero_name = "山地兵"
    role = "山地兵"
    base_stats = Stats(attack=3, defense=2, speed=3, attack_range=1, mana=0)


class StrategyEtherScout(StrategySoldier):
    hero_code = "strategy_ether_scout"
    hero_name = "以太侦察兵"
    role = "侦察兵"
    attribute = "雷"
    base_stats = Stats(attack=2, defense=1, speed=4, attack_range=3, mana=0)


class StrategyWallEngineer(StrategySoldier):
    hero_code = "strategy_wall_engineer"
    hero_name = "城墙工兵"
    role = "工兵"
    base_stats = Stats(attack=1, defense=3, speed=2, attack_range=2, mana=0)
