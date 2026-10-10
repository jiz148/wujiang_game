"""Battle effects for the independent auto chess equipment workbook.

Every equipped item is a persistent component of its original hero.  The
workbook remains the source of names, printed stats, and descriptions.
"""
from __future__ import annotations

import random

from wujiang.tactical.engine.core import Battle, DamageContext, HealContext, StatusEffect
from wujiang.tactical.heroes.common import (
    BlockCounterTrait, FlagStatus, FlyingTrait, InvincibleUntilActionStatus,
)
from wujiang.tactical.heroes.next_five import ArcAttackTrait


def _rng(battle: Battle) -> random.Random:
    return getattr(battle, "chess_rng", None) or random


class EquipmentStatus(StatusEffect):
    def __init__(self, code: str, attributes: str, effect: str) -> None:
        super().__init__(f"装备·{code}", f"{attributes}；{effect}")
        self.code = code
        self.owner_turns = 0
        self.bonus_defense = 0
        self.survival_used = False
        self.no_more_healing = False
        self.last_aura_round = -1
        self.wind_bonus = False

    def bind(self, owner):
        super().bind(owner)
        if self.code == "鸟人的圣衣" and not any(
                isinstance(trait, FlyingTrait) for trait in owner.traits):
            owner.traits.append(FlyingTrait().bind(owner))
        if self.code in {"朋克警棍", "嗜血大剑"} and not any(
                isinstance(trait, BlockCounterTrait) for trait in owner.traits):
            owner.traits.append(BlockCounterTrait().bind(owner))
        if self.code in {"短棍", "大剑", "嗜血大剑"} and not any(
                isinstance(trait, ArcAttackTrait) for trait in owner.traits):
            owner.traits.append(ArcAttackTrait().bind(owner))
        duration_bonus = {"月之书": 2, "圣洁之甲": 4, "德圣的祝福": 4}.get(self.code, 0)
        if duration_bonus:
            owner.chess_duration_bonus = getattr(owner, "chess_duration_bonus", 0) + duration_bonus
        return self

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        if owner is None:
            return value
        if stat_name == "attack":
            if self.code == "锈剑" and owner.role == "剑士":
                return value + 1
            if self.code == "鸟人的圣衣" and owner.race == "鸟人":
                return value + 1
            if self.code == "自动臂" and owner.race == "机甲":
                return value + 1
            if self.code == "德圣的祝福" and owner.attribute == "光":
                return value + 1
            if self.code == "机械头盔" and owner.race == "机甲":
                return value + 1
        if stat_name == "defense":
            if self.code == "腰带":
                return value + self.bonus_defense
            if self.code == "自动臂" and owner.race == "机甲":
                return value + 1
        if stat_name == "speed" and self.code == "风力弓" and owner.race == "精灵":
            return value + 1
        if stat_name == "mana" and self.code == "水晶球" and owner.role == "法师":
            return value + 1
        if stat_name == "attack_range" and self.code in {"木弓", "风力弓"} and owner.role == "弓箭手":
            return value + 1
        return value

    def modify_attack_actions_per_turn(self, value: int) -> int:
        if self.code in {"幽灵手套", "点金手"}:
            return value + 1
        if self.code == "风力弓" and self.wind_bonus:
            return value + 1
        if self.code == "布手套" and self.owner_turns % 4 == 0 and self.owner_turns > 0:
            return value + 1
        return value

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + 1 if self.code == "鸟人的圣衣" or self.code == "风力弓" and self.wind_bonus else value

    def modify_normal_move_distance(self, value: int) -> int:
        return value + 1 if self.code in {"钩子鞋", "风力弓"} else value

    def modify_targeting_range(self, value: int) -> int:
        return value + 1 if self.code == "以太魔眼" else value

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.owner_turns += 1
        self.wind_bonus = False
        if self.code == "命运纽带":
            self.survival_used = False
        owner = self.owner
        if owner is None or not owner.alive:
            return
        self.bonus_defense = 0
        if not self.no_more_healing and self.code in {"洁白羽毛", "天使之剑", "年兽细胞"}:
            battle.heal(HealContext(source=owner, target=owner, amount=0.125,
                                    action_name=self.name))
        if not self.no_more_healing and self.code in {"贪魔之卵"}:
            battle.heal(HealContext(source=owner, target=owner, amount=0.5,
                                    action_name=self.name))
        mana_gain = {"魔眼": 0.5, "以太魔眼": 1, "鬼符": 0.5,
                     "年兽细胞": 0.125, "魔剑": 1, "贪魔之卵": 2,
                     "流浪贵族杖": 1.5}.get(self.code, 0)
        if self.code == "鬼符" and owner.attribute != "暗":
            mana_gain = 0
        if mana_gain:
            owner.base_stats.mana += mana_gain
            owner.gain_mana(mana_gain)
        if self.code == "鬼符" and owner.attribute == "暗":
            owner.take_damage_fraction(0.125)
        if self.code == "贪魔之卵" and self.owner_turns % 10 == 1:
            owner.current_mana = owner.max_mana()
        if self.code == "天使之剑":
            owner.has_flying = owner.current_hp >= owner.max_health or any(
                isinstance(trait, FlyingTrait) for trait in owner.traits)
            owner.ignore_units_while_moving = owner.has_flying
        if self.code == "裁决大剑" and self.owner_turns % 10 == 1:
            owner.add_status(FlagStatus("裁决大剑·魔免", "magic_immunity",
                                        duration=1, tick_scope="owner_turn_end"))
        if self.code == "幽灵盾" and self.owner_turns == 1:
            owner.add_status(InvincibleUntilActionStatus())
        if self.code == "机械头盔" and self.owner_turns % 10 == 1 and owner.position is not None:
            for target in list(battle.all_units()):
                if target.player_id == owner.player_id or target.position is None or not target.alive:
                    continue
                if max(abs(target.position.x - owner.position.x),
                       abs(target.position.y - owner.position.y)) > 3:
                    continue
                while target.total_shields() > 0:
                    target.consume_one_shield()
                target.add_status(FlagStatus("机械头盔·禁足", "cannot_move",
                                             duration=2, tick_scope="owner_turn_end"))

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.code not in {"永火熔岩", "嗜血大剑"} or self.owner is None:
            return
        owner = self.owner
        round_number = int(getattr(battle, "round_number", 0))
        if (self.last_aura_round == round_number or not owner.alive or
                owner.position is None or owner.banished):
            return
        self.last_aura_round = round_number
        radius = 2 if self.code == "永火熔岩" else 3
        amount = 0.125 if self.code == "永火熔岩" else 0.25
        for target in list(battle.all_units()):
            if (target is owner or not target.alive or target.position is None or
                    target.banished or max(abs(target.position.x - owner.position.x),
                                           abs(target.position.y - owner.position.y)) > radius):
                continue
            old_hp = target.current_hp
            battle.resolve_damage(DamageContext(source=owner, target=target,
                attack_power=0, is_skill=False, action_name=self.name,
                raw_damage=amount, from_field_effect=True, cannot_evade=True))
            if self.code == "嗜血大剑" and old_hp > target.current_hp:
                battle.heal(HealContext(source=owner, target=owner, amount=0.5,
                                        action_name=self.name))

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.cancelled:
            return
        if ctx.source is owner:
            if self.code == "重爪" and not ctx.is_skill and _rng(battle).random() < 0.5:
                ctx.cancelled = True
                ctx.reason = f"{owner.name} 的重爪攻击落空。"
                return
            if self.code == "锈剑" and owner.role == "剑士" and not ctx.is_skill:
                ctx.attack_power += 1
            if self.code == "自动臂" and owner.race == "机甲" and not ctx.is_skill:
                ctx.attack_power += 1
            if self.code in {"幽灵手套", "魔杖"} and ctx.is_skill:
                ctx.attack_power += 1
            if self.code == "魔剑" and ctx.is_skill:
                ctx.attack_power += 3
            if self.code == "流浪贵族杖" and ctx.is_skill:
                ctx.attack_power += 2
                if self.owner_turns % 10 == 1:
                    ctx.ignore_magic_immunity = True
            if self.code == "光子斩击斧" and not ctx.is_skill and owner.attacks_used == 0:
                ctx.half_ignore_shield = True
        if ctx.target is owner:
            if self.code in {"木盾", "幽灵盾"}:
                if ctx.source is not None and ctx.source.position is not None and owner.position is not None:
                    distance = max(abs(ctx.source.position.x - owner.position.x),
                                   abs(ctx.source.position.y - owner.position.y))
                    if distance > 1 or _rng(battle).random() < 0.5:
                        ctx.attack_power -= 1
            if self.code in {"废铁头盔", "机械头盔"}:
                if ctx.source is not None and ctx.source.position is not None and owner.position is not None:
                    if max(abs(ctx.source.position.x - owner.position.x),
                           abs(ctx.source.position.y - owner.position.y)) > 1:
                        ctx.attack_power -= 1
            if self.code in {"圣洁之甲", "黑白鳞片", "德圣的祝福"} and ctx.is_skill:
                ctx.attack_power -= 2 if self.code == "黑白鳞片" else 1
            if self.code == "电子原件" and owner.race == "机甲" and ctx.is_skill:
                ctx.attack_power -= 1
            if self.code == "精灵便帽" and owner.race == "精灵" and not ctx.is_skill:
                if getattr(self, "last_cap_dodge_turn", -1) != battle.completed_turns and _rng(battle).random() < 0.5:
                    self.last_cap_dodge_turn = battle.completed_turns
                    ctx.cancelled = True
                    ctx.reason = f"{owner.name} 用精灵便帽回避了普攻。"

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.actual_damage <= 0:
            return
        if ctx.source is owner:
            rate = {"吸血面具": 0.25, "德古拉之爪": 0.5,
                    "嗜血大剑": 1.0}.get(self.code, 0)
            if rate:
                battle.heal(HealContext(source=owner, target=owner,
                    amount=ctx.actual_damage * rate, action_name=self.name))
            if self.code == "腰带":
                self.bonus_defense += 1
        if ctx.target is owner and self.code == "天使之剑":
            owner.has_flying = owner.current_hp >= owner.max_health or any(
                isinstance(trait, FlyingTrait) for trait in owner.traits)
            owner.ignore_units_while_moving = owner.has_flying
        if (ctx.target is owner and self.code == "德古拉之爪" and ctx.source is not None
                and ctx.source is not owner and ctx.source.position is not None
                and owner.position is not None and "equipment_counter" not in ctx.tags
                and max(abs(ctx.source.position.x - owner.position.x),
                        abs(ctx.source.position.y - owner.position.y)) <= 1
                and _rng(battle).random() < 0.5):
            battle.resolve_damage(DamageContext(source=owner, target=ctx.source,
                attack_power=owner.stat("attack"), is_skill=False,
                action_name="德古拉之爪反击", tags={"equipment_counter"}))

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        if (self.code == "风力弓" and ctx.source is self.owner and not ctx.is_skill
                and not self.wind_bonus):
            self.wind_bonus = True

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        if self.no_more_healing and ctx.target is self.owner:
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 的{self.code}阻止了本场战斗的恢复。"

    def lethal_damage_remaining_hp(self, battle: Battle, ctx: DamageContext) -> float | None:
        if self.owner is None or self.survival_used:
            return None
        if self.code == "气息之刃" and self.owner.current_hp >= self.owner.max_health:
            self.survival_used = self.no_more_healing = True
            return 0.0625
        if self.code == "命运纽带" and _rng(battle).random() < 1 / 6:
            self.survival_used = self.no_more_healing = True
            return 0.0625
        return None
