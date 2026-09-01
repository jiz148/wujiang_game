from __future__ import annotations

from wujiang.tactical.engine.core import Skill, Stats, Trait
from wujiang.tactical.engine.siege import (
    apply_siege_profile,
    siege_profile_of,
    source_can_damage_siege_structure,
)
from wujiang.tactical.heroes.base import AbstractHero


class StrategySoldier(AbstractHero):
    is_army_soldier = True
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
    base_stats = Stats(attack=2, defense=1, speed=2, attack_range=3, mana=0)


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


class StrategySnowGhost(StrategySoldier):
    hero_code = "strategy_snow_ghost"
    hero_name = "雪鬼"
    role = "雪鬼"
    attribute = "冰"
    race = "雪鬼"
    base_stats = Stats(attack=3, defense=3, speed=2, attack_range=1, mana=0)
    raw_skill_text = "北方寒潮孕育的先锋；无主动技能。"
    raw_trait_text = "雪鬼单位。"


class SiegeBatteryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻城火力", "按攻城档案开火：可全方位射击，升级后扩大爆炸范围与射程。")

    def ignores_direct_unit_target_line(self, battle, actor) -> bool:
        profile = siege_profile_of(self.owner)
        return actor is self.owner and bool(profile and profile.all_around)


class ArrowTowerBastionTrait(Trait):
    def __init__(self) -> None:
        super().__init__(
            "城防工事",
            "固定工事。物免；魔法伤害 -1。只有直接命中的火炮弹才能对其造成伤害，且火炮伤害不被减免。",
        )

    def ignores_direct_unit_target_line(self, battle, actor) -> bool:
        profile = siege_profile_of(self.owner)
        return actor is self.owner and bool(profile and profile.all_around)

    def on_before_damage(self, battle, ctx) -> None:
        if ctx.target is not self.owner or ctx.cancelled:
            return
        if bool(getattr(ctx, "siege_shell", False)) or "siege_shell" in getattr(ctx, "tags", set()):
            return
        if source_can_damage_siege_structure(ctx.source, self.owner):
            if ctx.is_skill and not bool(getattr(ctx, "ignore_magic_immunity", False)):
                ctx.attack_power = max(0.0, float(ctx.attack_power) - 1)
                if ctx.raw_damage is not None:
                    ctx.raw_damage = max(0.0, float(ctx.raw_damage) - 1)
            return
        ctx.cancelled = True
        ctx.reason = f"{self.owner.name} 不受普通攻击破坏，需要火炮一类攻城火力才能摧毁。"


class StrategyArrowTower(StrategySoldier):
    hero_code = "strategy_arrow_tower"
    hero_name = "箭塔"
    role = "工事"
    siege_profile_id = "arrow_tower_1"
    is_siege_structure = True
    base_stats = Stats(attack=3, defense=5, speed=0, attack_range=5, mana=0)
    max_health = 2.0
    raw_skill_text = "每个军队回合自动射击范围内的敌人。"
    raw_trait_text = "守 5。物免；魔法伤害 -1。只有直接命中的火炮弹才能摧毁它。"

    def __init__(self, player_id: int, **kwargs) -> None:
        super().__init__(player_id, **kwargs)
        apply_siege_profile(self, self.siege_profile_id, restore_hp=True)
        self.physical_immunity = True

    def build_traits(self) -> list[Trait]:
        return [ArrowTowerBastionTrait(), SiegeBatteryTrait()]


class StrategySiegeCannon(StrategySoldier):
    hero_code = "strategy_cannon"
    hero_name = "火炮"
    role = "火炮"
    siege_profile_id = "cannon_1"
    footprint_width = 2
    footprint_height = 2
    entry_footprint_width = 2
    entry_footprint_height = 2
    siege_reload_cycle = True
    base_stats = Stats(attack=3, defense=2, speed=1, attack_range=8, mana=0)
    max_health = 4.0
    raw_skill_text = "不动的军队回合会自动装填；装填完毕后，移动结束仍可开炮。无法对范 1 开火。伤害不分敌我。"
    raw_trait_text = "占 4 格。炮弹无视物免与魔免；必须直接命中箭塔才能对其造成伤害。"

    def __init__(self, player_id: int, **kwargs) -> None:
        super().__init__(player_id, **kwargs)
        apply_siege_profile(self, self.siege_profile_id, restore_hp=True)
        self.siege_loaded = False
        self.siege_reload_state = "empty"

    def build_traits(self) -> list[Trait]:
        return [SiegeBatteryTrait()]
