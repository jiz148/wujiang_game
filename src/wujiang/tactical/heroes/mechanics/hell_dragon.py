"""Hell dragon orb and sacrifice mechanics."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import HELL_FIRE_SHORTLIST, MagicAreaPrefilter

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.mechanics.weapon_copy import PhysicalImmunityStatus
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.dragon_breath import DragonBreathSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns


class HellDragonBreathSkill(DragonBreathSkill):
    def __init__(self) -> None:
        super().__init__()
        self.name = "龙吸"
        self.description = "普通技能：沿近身龙息的2×2范围和2魔、每本人轮两次额度结算。"


class HellDragonFireSkill(MagicAreaPrefilter, DeclaredAreaSkillMixin, Skill):
    ai_candidate_plan = HELL_FIRE_SHORTLIST
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.paid_skill_value()

    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("hell_dragon_fire", "地狱之火",
                         "普通技能：2轮一次；范内远程选择5×5区域，按当前攻伤双方。",
                         cooldown_turns=2, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 5, 5)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        battle.queue_area_damage_effect(actor=actor, display_name=self.name, cells=cells,
                                        attack_power=actor.stat("attack"), tags={"skill", self.code})

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        affected = battle.effect_units_at_cells([cell for pattern in patterns for cell in pattern])
        preview.update({"target_unit_ids": [unit.unit_id for unit in affected],
                        "secondary_cells": [], "requires_target": True})
        return preview


class FlyingDragonOrbStatus(PhysicalImmunityStatus):
    def __init__(self) -> None:
        super().__init__()
        self.name = "飞龙宝珠"
        self.description = "三个本人轮内物免；敌方技能仍可影响。"

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is not None and ctx.actor is not None and ctx.actor.player_id == self.owner.player_id:
            return
        super().on_targeted(battle, ctx)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is not None and ctx.source is not None and ctx.source.player_id == self.owner.player_id:
            return
        super().on_before_damage(battle, ctx)


class FlyingDragonOrbSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.red_preparation_score(context)

    def __init__(self) -> None:
        super().__init__("flying_dragon_orb", "飞龙宝珠", "大招：本场一次，物免3轮。",
                         max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(FlyingDragonOrbStatus())


class HellDragonSacrificeTrait(Trait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("古龙献祭再临", "真死亡后可技能破坏两名己方地狱单位，只放行一名死亡效果，之后任意时间于原格满状态复活。")

    def sacrifice_candidates(self, battle: Battle) -> list[Unit]:
        owner = self.owner
        if owner is None:
            return []
        return [unit for unit in battle.all_units()
                if unit.unit_id != owner.unit_id and unit.player_id == owner.player_id
                and unit.alive and not unit.banished and unit.position is not None
                and "地狱" in unit.name and battle.effect_recipient(unit) is unit
                and (not unit.is_summon or not unit.summoner_id
                     or (battle.units.get(unit.summoner_id) is not None
                         and battle.units[unit.summoner_id].alive))]

    def on_hell_dragon_destroyed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.unit_id in battle.hell_dragon_return_cells:
            return
        candidates = self.sacrifice_candidates(battle)
        if len(candidates) < 2:
            battle.log_public_event(f"{owner.name} 已阵亡，场上不足两名可献祭的地狱友军。", target=owner)
            return
        ids = [unit.unit_id for unit in candidates]
        first = battle.optional_position_choice(owner, ids, action_name="古龙献祭第一名", kind="hell_dragon_first")
        if first not in ids:
            battle.log_public_event(f"{owner.name} 放弃本次地狱献祭。", target=owner)
            return
        second_options = [unit_id for unit_id in ids if unit_id != first]
        second = battle.optional_position_choice(owner, second_options, action_name="古龙献祭第二名", kind="hell_dragon_second")
        if second not in second_options:
            battle.log_public_event(f"{owner.name} 未完成双人献祭。", target=owner)
            return
        allowed = battle.optional_position_choice(owner, [first, second],
                                                  action_name="选择一名可发动死亡效果", kind="hell_dragon_effect")
        if allowed not in {first, second}:
            allowed = first
        originals = [battle.units.get(first), battle.units.get(second)]
        if any(unit is None or not unit.alive or unit.position is None for unit in originals):
            return
        origins = [unit.position for unit in originals if unit is not None]
        destroyed: list[Unit] = []
        for target in originals:
            if target is None:
                continue
            result = battle.validate_target(owner, target, action_name="古龙献祭", is_skill=True,
                                            is_hostile=True, ignore_targeting_restrictions=True,
                                            damage_target=True,
                                            record_effect_on_validation=False)
            recipient = result.target
            if result.cancelled or recipient is not target or recipient.skill_non_damage_effects_blocked():
                battle.log_public_event(result.reason or f"{recipient.name} 挡下古龙献祭。",
                                        source=owner, target=recipient)
                continue
            recipient.current_hp = 0
            recipient.alive = False
            recipient._hell_death_from_damage = True
            if target.unit_id != allowed:
                recipient._suppress_hell_forced_skill_death = True
            destroyed.append(recipient)
            battle.log_public_event(f"{owner.name} 献祭了{recipient.name}。", source=owner, target=recipient)
        if len({unit.unit_id for unit in destroyed}) == 2 and battle.side_still_has_a_fighter(owner.player_id):
            battle.hell_dragon_return_cells[owner.unit_id] = list(dict.fromkeys(origins))
            battle.log_public_event(f"{owner.name} 取得待复活权，可在两名献祭者的原格择时满状态复活。", target=owner)
        elif len({unit.unit_id for unit in destroyed}) == 2:
            battle.log_public_event(f"{owner.name} 这一方已经没有能继续战斗的单位，献祭复活不再生效。", target=owner)
        else:
            battle.log_public_event(f"{owner.name} 未能真正破坏两名目标，未取得复活权。", target=owner)
        battle.check_win_condition()


