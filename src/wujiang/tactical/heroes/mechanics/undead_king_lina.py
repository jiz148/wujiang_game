"""Undead King Lina battlefield objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy
from wujiang.tactical.heroes.common import AttackCountTrait
from wujiang.tactical.heroes.common import HardenSkill
from wujiang.tactical.heroes.common import KnockbackSkill
from wujiang.tactical.heroes.common import StealthSkill
from wujiang.tactical.heroes.mechanics.element_hunter import RendingSkill
from wujiang.tactical.heroes.mechanics.half_pierce import HalfPierceAttackTrait
from wujiang.tactical.heroes.mechanics.sandstorm import CrazySandSkill
from wujiang.tactical.heroes.mechanics.sandstorm import SandstormWeatherEffect
from wujiang.tactical.heroes.mechanics.sandstorm import WindSandSkill
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback


class AttackLockTrait(Trait):
    def __init__(self) -> None:
        super().__init__("执念目标", "普攻对象被破坏前，不能普攻其他单位。")
        self.locked_target_id: str | None = None

    def _current_locked_target(self, battle: Battle) -> HeroUnit | None:
        if not self.locked_target_id:
            return None
        target = battle.units.get(self.locked_target_id)
        if target is None or not target.alive:
            self.locked_target_id = None
            return None
        effective_target = battle.effect_recipient(target)
        if effective_target.unit_id != target.unit_id:
            self.locked_target_id = effective_target.unit_id
        return effective_target  # type: ignore[return-value]

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        if self.owner is None or actor.unit_id != self.owner.unit_id:
            return True, ""
        locked = self._current_locked_target(battle)
        if locked is not None and locked.unit_id != target.unit_id:
            return False, f"{actor.name} 必须先攻击 {locked.name}，直到其被破坏。"
        return True, ""

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if self.owner is None or action_type != "attack":
            return
        target_id = str(payload.get("target_unit_id") or "").strip()
        if target_id:
            target = battle.units.get(target_id)
            if target is not None:
                self.locked_target_id = battle.effect_recipient(target).unit_id
            else:
                self.locked_target_id = target_id

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is None or ctx.source is None or ctx.source.unit_id != self.owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags:
            return
        if ctx.target.alive:
            self.locked_target_id = ctx.target.unit_id
        elif self.locked_target_id == ctx.target.unit_id:
            self.locked_target_id = None


class LinaDestroyRewardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("击破重置", "自己的普攻或技能每破坏一个武将或守 4 以上单位，攻击重置，并获得该目标剩余魔；次数不限，不重置移动。")

    def _eligible_target(self, target: HeroUnit) -> bool:
        return (not target.is_summon and not target.is_clone) or target.stat("defense") >= 4

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.target.alive or ctx.from_field_effect or not ((ctx.is_skill and "skill" in ctx.tags) or "attack" in ctx.tags):
            return
        if not self._eligible_target(ctx.target):  # type: ignore[arg-type]
            return
        gained = owner.gain_mana(ctx.target.current_mana)
        owner.attacks_used = 0
        battle.log(f"{owner.name} 击破目标，攻击已重置，并获得 {gained} 点魔。")


class NoEnemyHealAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("压制回复", "周围 7*7 内的敌方单位不能回复。")

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.banished or owner.position is None or ctx.target.player_id == owner.player_id:
            return
        if battle.distance_between_units(owner, ctx.target) <= 3:
            ctx.cancelled = True
            ctx.reason = f"{ctx.target.name} 处于 {owner.name} 的压制回复范围内，不能回复。"


class LinaSandstormRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("沙尘自然回复", "沙尘天气且非隐身时，在自己的己方回合开始时自然回血并自然回魔。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not battle.unit_in_weather("沙尘", owner) or owner.is_stealthed():
            return
        gained = owner.gain_mana(1)
        if gained:
            battle.log(f"{owner.name} 因沙尘自然回魔，获得 {gained} 点魔。")
        battle.heal(HealContext(source=owner, target=owner, amount=0.25, action_name="自然回复", tags={"natural_recovery"}))



def lina_has_attack_followup(battle: Battle, actor: HeroUnit, defeated_ids: set[str]) -> bool:
    """Count only a currently reachable next victim; a kill does not restore movement."""
    if actor.position is None or actor.cannot_attack:
        return False
    return any(
        unit.unit_id not in defeated_ids
        and unit.alive and unit.position is not None and not unit.banished
        and (not unit.is_stealthed() or battle.attack_ignores_stealth(actor, unit))
        and battle.distance_between_units(actor, unit) <= actor.targeting_range()
        for unit in battle.enemy_units(actor.player_id)
    )


class LinaAttackPolicy(HeroAIPolicy):
    """Value eligible kills, recovered mana, follow-up attacks, and attack lock."""

    def sandstorm_score(self, battle: Battle, actor: HeroUnit, score: float) -> float:
        if not battle.unit_in_weather("沙尘", actor):
            with ai_probe_rollback(battle):
                hp_before = actor.current_hp
                battle.heal(HealContext(source=actor, target=actor, amount=0.25, action_name="AI沙尘恢复估值"))
                score += max(0.0, actor.current_hp - hp_before) * 80.0
            score += min(1.0, max(0.0, actor.max_mana() - actor.current_mana)) * 16.0
        return score

    def late_attack_synergy_bonus(self, battle: Battle, actor: HeroUnit,
                                  target: HeroUnit, damage: float) -> float:
        score = 0.0
        reward = next((trait for trait in actor.traits if trait.name == "击破重置"), None)
        if reward is not None and reward._eligible_target(target) and damage >= target.current_hp:
            score += min(target.current_mana, max(0.0, actor.max_mana() - actor.current_mana)) * 18.0
            if lina_has_attack_followup(battle, actor, {target.unit_id}):
                score += 55.0
        lock = next((trait for trait in actor.traits if trait.name == "执念目标"), None)
        remaining = max(1, actor.attack_actions_per_turn() - actor.attacks_used)
        if lock is not None and not lock.locked_target_id and damage * remaining < target.current_hp:
            score -= 28.0
        return score


class UndeadKingLina(AbstractHero):
    hero_code = "undead_king_lina"
    ai_policy = LinaAttackPolicy()
    hero_name = "不死王利娜"
    role = "刺客"
    attribute = "土"
    race = "灵体"
    level = 8
    base_stats = Stats(attack=4, defense=4, speed=4, attack_range=3, mana=5)
    footprint_width = 2
    footprint_height = 2
    raw_skill_text = "隐身 变硬 ￥撕裂 风沙（每回合一次；远程2*4或4*2；当前攻伤害，有单位则全场沙尘至自己下次回合开始） 震开 狂沙（2己方轮一次；锁定直线5格伤害，瞬移到第6格；完整身体落点受阻时不移动）"
    raw_trait_text = "攻击两次；攻击半破魔；在攻击对象死之前无法攻击其他单位；占4格；每破坏一个武将或守在4以上的单位，攻击重置，并且魔+那个单位剩余的魔；此单位周围7*7的对方单位不能回复；在沙尘天气并且非隐身时中自然回复"

    def build_skills(self) -> list[Skill]:
        return [
            StealthSkill(),
            HardenSkill(),
            RendingSkill(),
            WindSandSkill(),
            KnockbackSkill(),
            CrazySandSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            AttackCountTrait(2),
            HalfPierceAttackTrait(),
            AttackLockTrait(),
            LinaDestroyRewardTrait(),
            NoEnemyHealAuraTrait(),
            LinaSandstormRecoveryTrait(),
        ]


