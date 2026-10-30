"""Soul Wraith battlefield objects and shared traits."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AreaAttackScoreState, HeroAIPolicy
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import FlyingTrait
from wujiang.tactical.heroes.common import PierceSkill
from wujiang.tactical.heroes.common import is_mana_drain_immune
from wujiang.tactical.heroes.mechanics.masamune import ArcAttackTrait
from wujiang.tactical.heroes.mechanics.next_five_geometry import position_key
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback


class SoulWraithAttackPolicy(HeroAIPolicy):
    def post_fast_move_score(self, battle: Battle, actor: Unit,
                             destination: Position, enemies: list[Unit],
                             damaging_skill_codes: set[str], score: float) -> float:
        with ai_probe_rollback(battle):
            actor.position = destination
            immunity = next(trait for trait in actor.traits if trait.name == "孤魂魔免")
            if immunity.active(battle):
                score += 28.0
            else:
                # Skill pressure is dangerous with the explicit 0.5 defense.
                for enemy in enemies:
                    if any(
                        skill.timing in {"active", "instant"}
                        and skill.code in damaging_skill_codes
                        and enemy.current_mana >= skill.mana_cost
                        for skill in enemy.skills
                    ):
                        reach = enemy.normal_move_distance() + max(2, enemy.targeting_range())
                        if battle.distance_between_units(actor, enemy) <= reach:
                            score -= 48.0
        return score

    def begin_area_attack_score(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any], drain_room: float) -> AreaAttackScoreState:
        return AreaAttackScoreState(drain_room)

    def area_hit_bonus(self, battle: Battle, actor: Unit, target: Unit,
                       expected_damage: float, state: object | None) -> float:
        if expected_damage <= 0 or target.is_clone:
            return 0.0
        assert isinstance(state, AreaAttackScoreState)
        drain = 0.0 if is_mana_drain_immune(target) else min(1.0, target.current_mana)
        gain = min(drain, state.remaining_mana)
        state.remaining_mana -= gain
        return drain * 14.0 + gain * 26.0


class PassThroughMovementTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可穿人", "普通移动路径可以穿过单位，但落点仍必须合法。")

    def bind(self, owner: HeroUnit) -> "PassThroughMovementTrait":
        super().bind(owner)
        owner.ignore_units_while_moving = True
        return self


class BasicAttackImmunityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("物免", "免疫敌方普攻造成的伤害和普攻附带效果。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None:
            return
        if ctx.target.unit_id != owner.unit_id or ctx.source.player_id == owner.player_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 物免，免疫普攻伤害和附带效果。"


class AttackManaDrainTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击吸魔", "普攻造成伤害后，目标魔 -1，自己魔 +1。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.cancelled or ctx.actual_damage <= 0:
            return
        if ctx.target.player_id == owner.player_id:
            return
        if is_mana_drain_immune(ctx.target):
            battle.log(f"{ctx.target.name} 无法被吸魔。")
            return
        lost = ctx.target.drain_mana(1.0)
        if lost <= 0:
            return
        gained = owner.gain_mana(lost)
        battle.log(f"{owner.name} 通过攻击吸取了 {ctx.target.name} 的 {lost} 点魔。")
        if gained < lost:
            battle.log(f"{owner.name} 的魔已接近上限，实际回复 {gained} 点魔。")


class NearbyEnemyHeroMagicImmunityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("孤魂魔免", "周围没有敌方武将时，免疫敌方技能伤害和技能附带效果。")

    def surrounding_cells(self, battle: Battle, owner: Unit) -> list[Position]:
        own_cells = battle.unit_cells(owner)
        own_keys = {position_key(cell) for cell in own_cells}
        return [cell for cell in square_around_cells(battle, own_cells, radius=1) if position_key(cell) not in own_keys]

    def active(self, battle: Battle) -> bool:
        owner = self.owner
        if owner is None or owner.position is None or not owner.alive or owner.banished:
            return False
        surrounding = {position_key(cell) for cell in self.surrounding_cells(battle, owner)}
        for unit in battle.enemy_units(owner.player_id):
            if unit.is_summon or unit.is_clone or is_army_soldier(unit) or not isinstance(unit, HeroUnit) or not unit.alive or unit.banished or unit.position is None:
                continue
            if any(position_key(cell) in surrounding for cell in battle.unit_cells(unit)):
                return False
        return True

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if not ctx.is_hostile or not ctx.is_skill or ctx.from_field_effect or ctx.ignore_magic_immunity:
            return
        if self.active(battle):
            ctx.cancelled = True
            ctx.reason = f"{owner.name} 周围没有敌方武将，处于魔免状态。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.target.unit_id != owner.unit_id:
            return
        if ctx.source.player_id == owner.player_id:
            return
        if not ctx.is_skill or ctx.from_field_effect or ctx.ignore_magic_immunity:
            return
        if self.active(battle):
            ctx.cancelled = True
            ctx.reason = f"{owner.name} 周围没有敌方武将，处于魔免状态。"

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["active"] = self.active(battle)
        return data


class SoulWraithGrowthStatus(StatusEffect):
    def __init__(self, stacks: int = 1) -> None:
        super().__init__("销魂成长", "每层攻 +1、速 +1、每回合移动次数 +1；普攻造成伤害后移除。")
        self.stacks = int(stacks)

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "speed"}:
            return value + self.stacks
        return value

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + self.stacks

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["stacks"] = self.stacks
        return data


class SoulWraithFailedAttackGrowthTrait(Trait):
    track_attack_reaction_prevention = True

    def __init__(self) -> None:
        super().__init__("受阻成长", "普攻因对方技能未造成伤害后，攻 +1、速 +1、每回合移动次数 +1，直到普攻造成伤害。")

    def growth_status(self) -> SoulWraithGrowthStatus | None:
        owner = self.owner
        if owner is None:
            return None
        status = owner.get_status("销魂成长")
        return status if isinstance(status, SoulWraithGrowthStatus) else None

    def add_stack(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        status = self.growth_status()
        if status is None:
            owner.add_status(SoulWraithGrowthStatus(1))
            stacks = 1
        else:
            status.stacks += 1
            stacks = status.stacks
        battle.log(f"{owner.name} 的普攻被对方技能阻止，获得 1 层销魂成长（当前 {stacks} 层）。")

    def clear_stacks(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        status = self.growth_status()
        if status is not None:
            owner.remove_status(status, battle)
            battle.log(f"{owner.name} 的普攻造成伤害，销魂成长结束。")

    def attack_dealt_damage(self, owner: Unit, damage_contexts: list[DamageContext]) -> bool:
        return any(
            ctx.source is not None
            and ctx.source.unit_id == owner.unit_id
            and not ctx.is_skill
            and "attack" in ctx.tags
            and not ctx.cancelled
            and (ctx.actual_damage > 0 or ctx.destroyed_as_clone)
            for ctx in damage_contexts
        )

    def enemy_skill_prevented_damage(
        self,
        owner: Unit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> bool:
        return bool(payload.get("enemy_skill_prevented_attack"))

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: Unit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id or not owner.alive or owner.banished or owner.position is None:
            return
        if self.attack_dealt_damage(owner, damage_contexts):
            self.clear_stacks(battle)
            return
        if self.enemy_skill_prevented_damage(owner, payload, damage_contexts, missed):
            self.add_stack(battle)


class SoulWraith(AbstractHero):
    hero_code = "soul_wraith"
    ai_policy = SoulWraithAttackPolicy()
    hero_name = "销魂的死灵"
    role = "剑士"
    attribute = "暗"
    race = "灵体"
    level = 1
    stat_minimums = {"defense": 0.5}
    base_stats = Stats(attack=4, defense=0.5, speed=5, attack_range=1, mana=2)
    raw_skill_text = "穿刺"
    raw_trait_text = "物免；可穿人；飞行；弧形攻击；攻击吸魔；周围没有敌方武将时魔免；攻击对方单位却因为对方技能未能造成伤害后，直到攻击造成伤害前攻+1，速+1，每回合移动次数+1"

    def build_skills(self) -> list[Skill]:
        return [PierceSkill()]

    def build_traits(self) -> list[Trait]:
        return [
            BasicAttackImmunityTrait(),
            PassThroughMovementTrait(),
            FlyingTrait(),
            ArcAttackTrait(),
            AttackManaDrainTrait(),
            NearbyEnemyHeroMagicImmunityTrait(),
            SoulWraithFailedAttackGrowthTrait(),
        ]
