"""R029 weapon copy, transfer, and infinite armor objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import ActionMiss
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import AttackCountTrait
from wujiang.tactical.heroes.common import MagicImmunityStatus
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.deadly_bow import BASIC_ATTACK_COPY_TRAIT_TYPES
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class WeaponCopyStatus(StatusEffect):
    def __init__(self, attack: float, copied_traits: list[Trait]) -> None:
        self.copied_attack = attack
        self.copied_traits = copied_traits
        names = "、".join(trait.name for trait in copied_traits) or "无"
        super().__init__("武装复制", f"本回合攻击变为 {attack:g}；复制普攻特性：{names}。",
                         duration=1, tick_scope="any_turn_end")

    def runtime_children(self):
        return self.copied_traits

    def provided_components(self):
        return self.copied_traits

    def modify_stat(self, stat_name: str, value: float) -> float:
        return self.copied_attack if stat_name == "attack" else value


class WeaponCopySkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.weapon_copy_score(context)

    def __init__(self) -> None:
        super().__init__("weapon_copy", "武装复制", "普通技能：每回合一次；选择周围可见武将，快照其当前攻及普攻相关特性，持续本回合。", max_uses_per_turn=1, target_mode="unit")

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload=None) -> int:
        return 1

    def reaction_window_timing(self, battle, actor, payload):
        # Reading a visible weapon changes only the caster, not the copied hero.
        return "after"

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        own = set(battle.unit_cells(actor))
        ring = set(square_around_cells(battle, list(own), radius=1)) - own
        return [unit for unit in battle.all_units()
                if isinstance(unit, HeroUnit) and unit is not actor
                and (not unit.is_clone and not unit.is_summon or unit.is_clone and unit.player_id != actor.player_id)
                and not is_army_soldier(unit) and battle.unit_can_be_selected(unit, actor=actor)[0]
                and bool(ring.intersection(battle.unit_cells(unit)))]

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True}

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            raise ActionError("武装复制只能选择周围可见的其他武将本体。")
        return {}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            raise ActionMiss("原格没有可复制的可见武将。")
        if target.is_clone:
            raise ActionMiss("武装复制没有获得有效的武将武装。")
        attack = target.stat("attack")
        # Rebuild definitions: source locks, resource history and owner never migrate.
        existing = {trait.name for trait in actor.traits}
        traits = []
        for component in target.iter_components():
            if not isinstance(component, Trait):
                continue
            definition = component.trait if isinstance(component, BasicAttackOnlyCopy) else component
            if type(definition).__name__ not in BASIC_ATTACK_COPY_TRAIT_TYPES or definition.name in existing:
                continue
            trait = AttackCountTrait(definition.attacks_per_turn) if isinstance(definition, AttackCountTrait) else type(definition)()
            existing.add(trait.name)
            if type(trait).__name__ in {"LinaDestroyRewardTrait", "FeiWangSpeedOnHeroKillTrait", "RedRandomPierceTrait",
                                       "MountainGodCounterTrait", "ErasureApostleDestroyRewardTrait", "UnmountedCombatTrait", "ElectronicLaserDamageBonusTrait", "DoomLightRetaliationTrait"}:
                trait = BasicAttackOnlyCopy(trait)
            trait.bind(actor)
            trait.on_owner_turn_start(battle)
            traits.append(trait)
        previous = actor.get_status("武装复制")
        if previous is not None:
            actor.remove_status(previous, battle)
        actor.add_status(WeaponCopyStatus(attack, traits), source=actor)


class BasicAttackOnlyCopy(Trait):
    """Keep the basic-attack branch of a mixed trait without copying its skill branch."""
    def __init__(self, trait: Trait) -> None:
        super().__init__(trait.name, trait.description + "（武装复制仅保留普攻分支）")
        self.trait = trait

    def bind(self, owner):
        super().bind(owner)
        self.trait.owner = owner
        return self

    def runtime_children(self):
        return (self.trait,)

    def on_owner_turn_start(self, battle):
        self.trait.on_owner_turn_start(battle)

    def on_owner_action_declared(self, battle, action_type, payload):
        if action_type == "attack" and not getattr(battle, "_ai_probe_active", False):
            self.trait.on_owner_action_declared(battle, action_type, payload)

    def on_after_damage(self, battle, ctx):
        if ctx.source is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            self.trait.on_after_damage(battle, ctx)

    def on_before_damage(self, battle, ctx):
        if ctx.source is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            self.trait.on_before_damage(battle, ctx)

    def on_basic_attack_finished(self, battle, actor, payload, damage_contexts, missed):
        self.trait.on_basic_attack_finished(battle, actor, payload, damage_contexts, missed)


class GlobalAttackStatus(StatusEffect):
    overrides_basic_attack_shape = True

    def __init__(self) -> None:
        super().__init__("无限", "每次普攻可指定全场任意一格，仍消耗一次攻击。", duration=3, tick_scope="owner_turn_end")

    def basic_attack_area_cells(self, battle, actor, payload=None):
        payload = payload or {}
        if payload.get("x") is None or payload.get("y") is None:
            return []
        return [Position(int(payload["x"]), int(payload["y"]))]

    def validate_declaration(self, battle, actor, payload):
        try:
            cell = Position(int(payload["x"]), int(payload["y"]))
        except (KeyError, TypeError, ValueError):
            raise ActionError("无限普攻需要指定战场上的一格。") from None
        if not battle.in_bounds(cell):
            raise ActionError("无限普攻指定的格子不在战场上。")

    def basic_attack_payload_metadata(self, battle, actor, payload=None):
        cells = battle.payload_positions(payload or {}, "attack_cells") if (payload or {}).get("queued_resolution") else []
        return {"attack_cells": positions_to_dict(cells or self.basic_attack_area_cells(battle, actor)),
                "area_attack": True, "single_hit_per_recipient": True,
                "allow_empty_attack_cell": True}

    def basic_attack_preview(self, battle, actor, payload=None):
        targets = [unit for unit in battle.all_units() if battle.unit_can_be_selected(unit, actor=actor)[0]]
        cells = [Position(x, y) for y in range(battle.height) for x in range(battle.width)]
        return {"cells": positions_to_dict(cells), "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True, "cell_targeted_attack": True}


class InfiniteSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.red_preparation_score(context)

    def __init__(self) -> None:
        super().__init__("infinite", "无限", "大招：3个己方回合内，每次普攻可指定全场任意一格。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(GlobalAttackStatus())


class PhysicalImmunityStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("无限铠甲", "物免 3 轮。", duration=3, tick_scope="owner_turn_end")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is ctx.target and not ctx.is_skill and "attack" in ctx.tags and not ctx.ignore_physical_immunity:
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 物免。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and not ctx.is_skill and "attack" in ctx.tags and not ctx.ignore_physical_immunity:
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 物免。"


class InfiniteArmorSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.red_preparation_score(context)

    def __init__(self) -> None:
        super().__init__("infinite_armor", "无限铠甲", "大招：物免 3 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(PhysicalImmunityStatus())


class InfiniteRobeSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.red_preparation_score(context)

    def __init__(self) -> None:
        super().__init__("infinite_robe", "无限法袍", "大招：魔免 3 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        status = MagicImmunityStatus(source_name="无限法袍", duration=3)
        status.tick_scope = "owner_turn_end"
        actor.add_status(status)


