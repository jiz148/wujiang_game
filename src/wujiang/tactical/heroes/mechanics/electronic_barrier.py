"""Electronic dragon laser and barrier objects."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.mechanics.hell_slash import HellSlashSkill


class ElectronicDragonNameTrait(Trait):
    def __init__(self) -> None:
        super().__init__("名字视为电子龙", "规则检索名字时也匹配“电子龙”，公开显示名不变。")

    def bind(self, owner: HeroUnit) -> "ElectronicDragonNameTrait":
        super().bind(owner)
        aliases = set(getattr(owner, "rule_name_aliases", set()))
        aliases.add("电子龙")
        owner.rule_name_aliases = aliases
        return self


class ElectronicLaserSkill(HellSlashSkill):
    length = 7

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r18_effect_score(self)

    def __init__(self) -> None:
        Skill.__init__(self,
            "electronic_laser",
            "镭射",
            "普通技能：0魔、每本人回合一次；身前连续7格，双方当前攻技能伤害。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        body = set(battle.unit_cells(actor))
        return [cells for cells in super().patterns(battle, actor) if not body.intersection(cells)]


class ElectronicLaserDamageBonusTrait(Trait):
    def __init__(self) -> None:
        super().__init__("镭射龙伤害强化", "直接伤害锁定当前攻击较低的单位时伤害 +1。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id or ctx.from_field_effect:
            return
        if not (ctx.is_skill or "attack" in ctx.tags) or owner.stat("attack") <= ctx.target.stat("attack"):
            return
        if ctx.raw_damage is None:
            ctx.attack_power += 1
        else:
            ctx.raw_damage += 1


class ElectronicBarrierPersonalGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("屏障龙自身减伤", "低防守单位对自身造成直接伤害时伤害 -1。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is not None and any(isinstance(effect, ElectronicBarrierAuraField) and effect.source_unit_id == self.owner.unit_id
                                          for effect in battle.field_effects):
            return
        self.apply_reduction(battle, ctx)

    def apply_reduction(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or battle.effect_recipient(owner) is not ctx.target or ctx.source is None or ctx.from_field_effect:
            return
        if not (ctx.is_skill or "attack" in ctx.tags) or ctx.source.stat("defense") >= ctx.target.stat("defense"):
            return
        receipt = "electronic_barrier_personal:" + owner.unit_id
        if receipt in ctx.modifier_receipts:
            return
        ctx.modifier_receipts.add(receipt)
        if ctx.raw_damage is None:
            ctx.attack_power = max(0.0, ctx.attack_power - 1)
        else:
            ctx.raw_damage = max(0.0, ctx.raw_damage - 1)


class ElectronicBarrierAuraField(BattleFieldEffect):
    def __init__(self, source_unit_id: str, source_player_id: int) -> None:
        self.source_unit_id = source_unit_id
        self.source_player_id = source_player_id
        super().__init__("电子屏障光环", "自身及周围己方单位受伤 -1，己方机甲 -2；自身条件减伤可另加 -1，同名光环不叠加。")

    def source(self, battle: Battle) -> HeroUnit | None:
        unit = battle.units.get(self.source_unit_id)
        if unit is None or not unit.alive or unit.position is None or unit.banished:
            return None
        return unit  # type: ignore[return-value]

    def on_after_damage_modifiers(self, battle: Battle, ctx: DamageContext) -> None:
        source = self.source(battle)
        if source is None:
            return
        if battle.effect_recipient(source) is ctx.target:
            for trait in source.traits:
                if isinstance(trait, ElectronicBarrierPersonalGuardTrait):
                    trait.apply_reduction(battle, ctx)
        position = getattr(ctx.target, "_resolution_actual_position", ctx.target.position)
        target_cells = battle.unit_cells_at(ctx.target, position) if position is not None else []
        if (ctx.target.player_id != self.source_player_id
                or "electronic_barrier_aura" in ctx.modifier_receipts
                or not set(target_cells).intersection(self.affected_cells(battle))):
            return
        is_barrier_owner = any(isinstance(effect, ElectronicBarrierAuraField) and effect.source(battle) is ctx.target
                               for effect in battle.field_effects)
        reduction = 1.0 if is_barrier_owner else 2.0 if ctx.target.race == "机甲" else 1.0
        if ctx.raw_damage is None:
            ctx.attack_power = max(0.0, ctx.attack_power - reduction)
        else:
            ctx.raw_damage = max(0.0, ctx.raw_damage - reduction)
        ctx.modifier_receipts.add("electronic_barrier_aura")

    def affected_cells(self, battle: Battle) -> list[Position]:
        source = self.source(battle)
        if source is None:
            return []
        body = battle.unit_cells_at(source, getattr(source, "_resolution_actual_position", source.position))
        return sorted({cell.offset(dx, dy) for cell in body for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                       if battle.in_bounds(cell.offset(dx, dy))}, key=lambda cell: (cell.y, cell.x))

    def board_marker(self, battle: Battle) -> str:
        return "屏障"


class ElectronicBarrierAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("电子屏障光环", "自身及周围己方单位受伤 -1，己方机甲 -2；自身条件减伤可另加 -1，同名光环不叠加。")

    def on_enter_battle(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        if not any(isinstance(effect, ElectronicBarrierAuraField) and effect.source_unit_id == owner.unit_id for effect in battle.field_effects):
            battle.add_field_effect(ElectronicBarrierAuraField(owner.unit_id, owner.player_id))

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for effect in list(battle.field_effects):
            if isinstance(effect, ElectronicBarrierAuraField) and effect.source_unit_id == owner.unit_id:
                battle.remove_field_effect(effect)


