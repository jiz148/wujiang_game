"""N battlefield objects and skills."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext, HeroAIPolicy
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import DrainManaSkill
from wujiang.tactical.heroes.common import PassiveProtectionSkill
from wujiang.tactical.heroes.common import PierceSkill
from wujiang.tactical.heroes.mechanics.area_helpers import area_patterns_preview
from wujiang.tactical.heroes.mechanics.clone_split import SplitSkill
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.mana_point_cost import ManaPointCostSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
import math


class NAttackPolicy(HeroAIPolicy):
    def single_target_attack_bonus(self, battle: Battle, actor: Unit, target: Unit,
                                   payload: dict[str, Any], expected_damage: float) -> float:
        return 18.0 + (20.0 if actor.current_mana < 1 else 0.0)


class MagneticWaveSkill(DeclaredAreaSkillMixin, ManaPointCostSkill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.magnetic_wave_score(context)

    def __init__(self) -> None:
        super().__init__(
            "magnetic_wave",
            "磁力波",
            "随时使用技能：费 2 魔力点，每回合最多 1 次，远程选择完整 3*3 区域；按当前攻击造成伤害，被命中的单位本回合不能行动。",
            mana_point_cost=2,
            max_uses_per_turn=1,
            target_mode="cell",
            timing="instant",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return super().chosen_cells(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.effect_units_at_cells(cells):
            damage_ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="磁力波",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "magnetic_wave"},
                )
            )
            self.apply_followup_effect(battle, actor, unit, damage_ctx)

    def apply_followup_effect(self, battle: Battle, actor: HeroUnit, unit: Unit, damage_ctx: DamageContext) -> None:
        if not (unit.alive and damage_followup_effect_applies(damage_ctx) and battle.unit_belongs_to_current_turn(unit) and unit.turn_ready):
            return
        if damage_ctx.cancelled and damage_ctx.preserve_followup_effects:
            effect = battle.validate_target(actor, unit, action_name=self.name, is_skill=True,
                                            is_hostile=unit.player_id != actor.player_id,
                                            ignore_targeting_restrictions=True, tags={"skill", self.code})
            if effect.cancelled:
                return
        unit.turn_ready = False
        battle.log(f"{unit.name} 受到磁力波影响，本回合不能行动。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]

class NSkill(ManaPointCostSkill):
    def __init__(self) -> None:
        super().__init__(
            "n_skill",
            "N",
            "主动技能：费 1 魔力点，自己魔 +1。",
            mana_point_cost=1,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        gained = actor.gain_mana(1)
        battle.log(f"{actor.name} 使用【N】，魔力 +{gained}。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class NAttackManaPointTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击魔力点+1", "每次声明普攻时，魔力点 +1。")

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if self.owner is None or action_type != "attack":
            return
        gained = self.owner.gain_mana_points(1)
        if gained > 0:
            battle.log(f"{self.owner.name} 因声明普攻获得了 {gained} 点魔力点。")


class NAttackCountTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击数=魔+1", "自己的回合开始时，按当前魔决定本回合攻击次数。")
        self.snapshot_attack_count = 1

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        self.snapshot_attack_count = max(1, int(math.floor(self.owner.current_mana + 1e-9)) + 1)

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return max(value, self.snapshot_attack_count)


class UnlimitedManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("魔无上限", "当前魔和魔上限都不受基础魔值封顶。")

    def bind(self, owner: HeroUnit) -> "UnlimitedManaTrait":
        super().bind(owner)
        owner.allow_unbounded_mana = True
        return self


class NManaGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("魔护体", "当前魔大于 0 时，伤害无效并改为失去 1 点魔。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is None or ctx.target.unit_id != self.owner.unit_id:
            return
        if self.owner.current_mana <= 0 or ctx.cancelled or (ctx.raw_damage is not None and ctx.raw_damage <= 0):
            return
        self.owner.spend_mana(1)
        ctx.cancelled = True
        ctx.preserve_followup_effects = True
        ctx.reason = f"{self.owner.name} 的魔抵消了这次伤害，并失去 1 点魔。"
        battle.emit_defense_visual_event(
            source=ctx.source,
            target=self.owner,
            action_name=ctx.action_name,
            defense_reason="mana_guard",
        )


class N(AbstractHero):
    hero_code = "n"
    ai_policy = NAttackPolicy()
    hero_name = "N"
    role = "勇者"
    attribute = "光"
    race = "人类"
    level = 4
    base_stats = Stats(attack=2, defense=3, speed=3, attack_range=1, mana=2)
    raw_skill_text = "保护 穿刺 分身 吸魔 磁力波（一回合一次；随时使用；3*3；魔力点-2；被击中单位本回合不能行动） N（魔力点-1；魔+1）"
    raw_trait_text = "攻击魔力点+1；每回合开始时决定攻击数=魔+1；魔无上限；魔大于0时不受到伤害，受到伤害时魔-1"

    def build_skills(self) -> list[Skill]:
        return [
            PassiveProtectionSkill(),
            PierceSkill(),
            SplitSkill(),
            DrainManaSkill(),
            MagneticWaveSkill(),
            NSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            NAttackManaPointTrait(),
            NAttackCountTrait(),
            UnlimitedManaTrait(),
            NManaGuardTrait(),
        ]


