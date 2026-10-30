"""Blood Eater battlefield objects and skills."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import DrainManaSkill
from wujiang.tactical.heroes.common import FlagStatus
from wujiang.tactical.heroes.common import LightWallSkill
from wujiang.tactical.heroes.common import SourcedDefenseStatus
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import dedupe_positions
from wujiang.tactical.heroes.common import ensure_ally
from wujiang.tactical.heroes.common import ensure_distance
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.half_pierce import HalfPierceAttackTrait
from wujiang.tactical.heroes.mechanics.mana_point_cost import ManaPointCostSkill


class RecoverManaSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "recover_mana",
            "回魔",
            "普通技能：费 1 魔；选择范内直线上的一个单位，被击中的单位魔 +1。",
            mana_cost=1,
            target_mode="unit",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        target_ctx = battle.validate_target(
            actor, target, action_name=self.name, is_skill=True,
            is_hostile=target.player_id != actor.player_id, tags={"mana_recovery"},
        )
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        gained = target.gain_mana(1)
        battle.log(f"{actor.name} 对 {target.name} 使用回魔，{target.name} 获得 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit for unit in battle.all_units()
            if unit.alive and not unit.banished and unit.position is not None
            and battle.unit_can_be_selected(unit, actor=actor)[0]
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
        ]
        return {
            "cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class MagicShieldSkill(LightWallSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "magic_shield"
        self.name = "魔盾"
        self.description = "被动技能：与光墙相同，连锁速度 2；可保护当前受影响的己方目标，每个目标费 1 魔并获得 1 层只持续本次连锁的临时护盾。"


class BloodSupportScoring:
    """Shared probe for the three ally support skills; each skill owns its hooks."""
    ai_early_effect_score = True

    def ai_reject_duplicate(self) -> bool:
        return True

    def ai_support_units(self, actor: HeroUnit, target: HeroUnit) -> list[HeroUnit]:
        return [target]

    def ai_attack_opportunity(self, context: AISkillScoreContext, target: HeroUnit) -> float:
        return 0.0

    def ai_final_adjustment(self, context: AISkillScoreContext, units: list[HeroUnit],
                            before: dict, score: float) -> float:
        return score

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id != actor.player_id:
            return -1000.0
        if self.ai_reject_duplicate() and any(
            status.name == self.name and getattr(status, "source_unit_id", None) == actor.unit_id
            for status in target.statuses
        ):
            return -1000.0
        units = self.ai_support_units(actor, target)
        before = {unit.unit_id: (unit.current_hp, unit.current_mana,
                                 unit.stat("attack"), unit.stat("defense")) for unit in units}
        attack_before = self.ai_attack_opportunity(context, target)
        with context.probe_rollback():
            try:
                self.execute(battle, actor, payload)
            except ActionError:
                return -1000.0
            score = -float(self.mana_cost) * 16.0
            for unit in units:
                hp, mana, attack, defense = before[unit.unit_id]
                score += (unit.current_hp - hp) * 180.0 + (unit.current_mana - mana) * 30.0
                nearby = [enemy for enemy in context.hostile_units()
                          if battle.distance_between_units(unit, enemy)
                          <= unit.targeting_range() + unit.normal_move_distance()]
                score += max(0.0, unit.stat("attack") - attack) * min(3, unit.attack_actions_per_turn()) * (26.0 if nearby else 5.0)
                score += max(0.0, unit.stat("defense") - defense) * (45.0 if nearby else 5.0)
            attack_after = self.ai_attack_opportunity(context, target)
        if attack_after > attack_before + 1.0:
            score = max(score, attack_after + 25.0)
        return self.ai_final_adjustment(context, units, before, score)


class BloodGuardSkill(BloodSupportScoring, Skill):
    def __init__(self) -> None:
        super().__init__(
            "blood_guard",
            "守*2",
            "普通技能：费 1 魔，每回合最多 1 次，对自己或己方武将使用；目标守 +1，持续 2 轮，来自同一武将的不叠加。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="ally",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or not payload:
            return ok, reason
        try:
            target = payload_target_unit(battle, payload)
            ensure_ally(actor, target)
            ensure_distance(actor, target, actor.targeting_range())
            if target.is_summon or target.is_clone:
                return False, "只能对己方武将使用。"
            if any(status.name == "守*2" and getattr(status, "source_unit_id", None) == actor.unit_id for status in target.statuses):
                return False, "来自同一武将的效果不能叠加。"
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        if target.is_summon or target.is_clone:
            raise ActionError("守*2只能对己方武将使用。")
        existing = next(
            (
                status
                for status in target.statuses
                if isinstance(status, SourcedDefenseStatus)
                and status.name == "守*2"
                and status.source_unit_id == actor.unit_id
            ),
            None,
        )
        if existing is not None:
            raise ActionError("来自同一武将的守*2效果不能叠加。")
        target.add_status(
            SourcedDefenseStatus(
                "守*2",
                source_unit_id=actor.unit_id,
                defense_delta=1,
                duration=2,
                tick_scope="owner_turn_start",
                description="守 +1。",
            )
        )
        battle.log(f"{target.name} 获得了来自 {actor.name} 的守*2加成。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.player_units(actor.player_id)
            if not unit.is_summon
            and not unit.is_clone
            and unit.position is not None
            and actor.position is not None
            and not any(status.name == "守*2" and getattr(status, "source_unit_id", None) == actor.unit_id for status in unit.statuses)
            and battle.distance_between_units(actor, unit) <= actor.targeting_range()
        ]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class BloodRiteAttackStatus(StatModifierStatus):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__(
            "噬血术",
            attack_delta=1,
            duration=2,
            tick_scope="owner_turn_start",
            description="攻 +1。",
        )
        self.source_unit_id = source_unit_id


class BloodArtSkill(BloodSupportScoring, Skill):
    def ai_attack_opportunity(self, context: AISkillScoreContext, target: HeroUnit) -> float:
        return context.best_attack_score() if target is context.actor else 0.0

    def __init__(self) -> None:
        super().__init__(
            "blood_art",
            "噬血术",
            "普通技能：费 1 魔，对自己或己方武将使用；命中后目标攻 +1，持续 2 轮，来自同一武将的不叠加。",
            mana_cost=1,
            target_mode="ally",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or not payload:
            return ok, reason
        try:
            target = payload_target_unit(battle, payload)
            ensure_ally(actor, target)
            ensure_distance(actor, target, actor.targeting_range())
            if target.is_summon or target.is_clone:
                return False, "只能对己方武将使用。"
            if any(status.name == "噬血术" and getattr(status, "source_unit_id", None) == actor.unit_id for status in target.statuses):
                return False, "来自同一武将的效果不能叠加。"
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        if target.is_summon or target.is_clone:
            raise ActionError("噬血术只能对自己或己方武将使用。")
        target_ctx = battle.validate_target(actor, target, action_name="噬血术", is_skill=True, is_hostile=False)
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        if any(
            isinstance(status, BloodRiteAttackStatus) and status.source_unit_id == actor.unit_id
            for status in target.statuses
        ):
            raise ActionError("来自同一武将的噬血术效果不能叠加。")
        target.add_status(BloodRiteAttackStatus(actor.unit_id))
        battle.log(f"{target.name} 被噬血术命中，攻 +1。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.player_units(actor.player_id)
            if not unit.is_summon
            and not unit.is_clone
            and unit.position is not None
            and actor.position is not None
            and not any(status.name == "噬血术" and getattr(status, "source_unit_id", None) == actor.unit_id for status in unit.statuses)
            and battle.distance_between_units(actor, unit) <= actor.targeting_range()
        ]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class BloodDanceLockStatus(FlagStatus):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__(
            "噬血之舞",
            "cannot_move",
            description="不能移动或使用位移技能，直到施术者的下个回合开始前。",
        )
        self.source_unit_id = source_unit_id


class BloodDanceSkill(BloodSupportScoring, Skill):
    def ai_reject_duplicate(self) -> bool:
        return False

    def ai_support_units(self, actor: HeroUnit, target: HeroUnit) -> list[HeroUnit]:
        return list(dict.fromkeys([actor, target]))

    def ai_final_adjustment(self, context: AISkillScoreContext, units: list[HeroUnit],
                            before: dict, score: float) -> float:
        battle = context.battle
        for unit in units:
            if unit.position is not None and not unit.cannot_move and unit.remaining_normal_move_distance(battle) > 0:
                current = context.move_score_for(unit, unit.position)
                cells = context.reachable_positions(unit)
                gain = max((context.move_score_for(unit, cell) - current for cell in cells), default=0.0)
                if gain > 8.0 and before[unit.unit_id][0] > 0.25:
                    score -= 80.0 + gain
        return score

    def __init__(self) -> None:
        super().__init__(
            "blood_dance",
            "噬血之舞",
            "普通技能：每回合最多 1 次，仅能对己方单位使用，目标和自己同时血 +1/4、魔 +1，并且直到噬血下个回合开始前不能移动或使用位移技能。",
            max_uses_per_turn=1,
            target_mode="ally",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or not payload:
            return ok, reason
        try:
            target = payload_target_unit(battle, payload)
            ensure_ally(actor, target)
            ensure_distance(actor, target, actor.targeting_range())
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        for unit in dict.fromkeys([actor, target]):
            battle.heal(HealContext(source=actor, target=unit, amount=0.25, action_name="噬血之舞"))
            gained = unit.gain_mana(1)
            unit.add_status(BloodDanceLockStatus(actor.unit_id))
            battle.log(f"{unit.name} 因噬血之舞获得 {gained} 点魔，并暂时不能位移。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.player_units(actor.player_id)
            if unit.position is not None
            and actor.position is not None
            and battle.distance_between_units(actor, unit) <= actor.targeting_range()
        ]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class SacrificeRitualSkill(ManaPointCostSkill):
    ai_early_effect_score = True
    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        try:
            target = self.candidate_by_payload(battle, context.payload)
            destination = context.destination()
        except ActionError:
            return -1000.0
        if target.player_id != actor.player_id or destination is None:
            return -1000.0
        exposed = sum(1 for enemy in context.hostile_units()
                      if context.distance_to_position(enemy, destination) <= enemy.targeting_range())
        value = (65.0 if not target.is_summon else 30.0) + min(40.0, target.stat("attack") * target.attack_actions_per_turn() * 5.0) + min(20.0, target.max_mana() * 4.0)
        return value - exposed * 35.0 - (45.0 if actor.mana_points >= 8 else 12.0)

    def __init__(self) -> None:
        super().__init__(
            "sacrifice_ritual",
            "献祭仪式",
            "普通技能：费 4 魔力点，选择一个被破坏的单位，并选择噬血周围合法格召唤；该单位血上限为 1/4，当前血为 1/4，魔回满。",
            mana_point_cost=4,
            target_mode="cell",
        )

    def revival_controller(self, battle: Battle, unit: HeroUnit) -> HeroUnit | None:
        original = battle.units.get(unit.mount_owner_id or unit.summoner_id or "")
        if original is not None and original.alive and not original.banished and original.player_id == unit.player_id:
            return original
        candidates = [hero for hero in battle.hero_units(unit.player_id) if hero.alive and not hero.banished]
        order = {unit_id: i for i, unit_id in enumerate(battle.turn_order_unit_ids)}
        return min(candidates, key=lambda hero: (order.get(hero.unit_id, len(order)), hero.unit_id), default=None)

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or not payload:
            return ok, reason
        try:
            target = self.candidate_by_payload(battle, payload)
            if payload_position(payload) not in self.legal_destinations(battle, actor, target):
                return False, "该位置不能作为献祭仪式的召唤落点。"
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)
        return True, ""

    def destroyed_candidates(self, battle: Battle) -> list[HeroUnit]:
        return [
            unit
            for unit in battle.destroyed_units
            if unit.unit_id not in battle.units and not unit.alive
            and (not unit.is_summon or self.revival_controller(battle, unit) is not None)
        ]  # type: ignore[list-item]

    def candidate_by_payload(self, battle: Battle, payload: dict[str, Any]) -> HeroUnit:
        unit_id = str(payload.get("revive_unit_id") or "").strip()
        for unit in self.destroyed_candidates(battle):
            if unit.unit_id == unit_id:
                return unit
        raise ActionError("需要选择一个被破坏的单位。")

    def legal_destinations(self, battle: Battle, actor: HeroUnit, unit: HeroUnit) -> list[Position]:
        if actor.position is None:
            return []
        cells = dedupe_positions(
            [
                neighbor
                for cell in battle.unit_cells(actor)
                for neighbor in battle.neighbors(cell)
            ]
        )
        return [cell for cell in cells if battle.can_place_unit(unit, cell, ignore=unit)]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = self.candidate_by_payload(battle, payload)
        destination = payload_position(payload)
        if destination not in self.legal_destinations(battle, actor, target):
            raise ActionError("该位置不能作为献祭仪式的召唤落点。")
        for status in list(target.statuses):
            target.remove_status(status, battle)
        target.alive = True
        target.banished = False
        target.banish_return_position = None
        target.banish_turns_remaining = 0
        target.max_health = 0.25
        target.current_hp = 0.25
        target.current_mana = target.max_mana()
        # turn_ready prevents entry actions; the next owner refresh may act normally.
        target.can_act_on_entry_turn = True
        target.turn_ready = False
        target.move_used = False
        target.normal_move_actions_used = 0
        target.normal_move_steps_used = 0
        target.attacks_used = 0
        target.performed_active_skill = False
        target.moved_this_turn = False
        target.actions_taken_this_turn = []
        target.clear_end_of_turn_shields()
        if target.is_summon:
            controller = self.revival_controller(battle, target)
            if controller is None:
                raise ActionError("召回单位没有同阵营的维持武将。")
            target.summoner_id = controller.unit_id
            target.mount_owner_id = controller.unit_id if target.mount_owner_id else None
        battle.add_unit(target, destination, trigger_enter_effects=False)
        battle.destroyed_units = [unit for unit in battle.destroyed_units if unit.unit_id != target.unit_id]
        battle.notify_destroyed_hero_count_changed()
        battle.log(f"{actor.name} 通过献祭仪式召回了 {target.name}。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        candidates = self.destroyed_candidates(battle)
        all_cells: list[Position] = []
        candidate_data: list[dict[str, Any]] = []
        for unit in candidates:
            cells = self.legal_destinations(battle, actor, unit)
            all_cells.extend(cells)
            candidate_data.append(
                {
                    "id": unit.unit_id,
                    "name": unit.name,
                    "cells": positions_to_dict(cells),
                }
            )
        return {
            "cells": positions_to_dict(dedupe_positions(all_cells)),
            "target_unit_ids": [],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": True,
            "selection": {
                "mode": "revive_unit_cell",
                "candidates": candidate_data,
            },
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


class BloodManaPointTrait(Trait):
    def __init__(self) -> None:
        super().__init__("噬血魔力点", "每次场上有单位受到其他单位伤害并血 -1/4 以上时，魔力点 +1，最多 8 点。")

    def bind(self, owner: HeroUnit) -> "BloodManaPointTrait":
        super().bind(owner)
        owner.mana_points = float(owner.base_stats.mana)
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for unit in battle.all_units():
            for status in list(unit.statuses):
                if isinstance(status, BloodDanceLockStatus) and status.source_unit_id == owner.unit_id:
                    unit.remove_status(status, battle)

    def on_battle_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.position is None:
            return
        if ctx.source is None or ctx.source.unit_id == ctx.target.unit_id:
            return
        if ctx.cancelled or ctx.actual_damage < 0.25:
            return
        if owner.mana_points >= 8:
            return
        before = owner.mana_points
        owner.mana_points = round(min(8.0, owner.mana_points + 1), 2)
        gained = round(owner.mana_points - before, 2)
        if gained > 0:
            battle.log(f"{owner.name} 因噬血获得 {gained} 点魔力点。")


class BloodSkillDamageGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("八点魔力点防护", "持有 8 个魔力点时不受到技能伤害，但不免疫技能的非伤害效果或回复。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if ctx.cancelled or not ctx.is_skill or owner.mana_points < 8 or ctx.raw_damage == 0:
            return
        ctx.cancelled = True
        ctx.preserve_followup_effects = True
        ctx.reason = f"{owner.name} 持有 8 个魔力点，免疫了技能伤害。"
        battle.emit_defense_visual_event(
            source=ctx.source,
            target=owner,
            action_name=ctx.action_name,
            defense_reason="mana_guard",
        )


class BloodEater(AbstractHero):
    hero_code = "blood_eater"
    hero_name = "噬血"
    role = "贤者"
    attribute = "火"
    race = "兽人"
    level = 4
    base_stats = Stats(attack=3, defense=2, speed=3, attack_range=3, mana=5)
    raw_skill_text = "回魔 吸魔 魔盾 【1守*2（2轮）【1噬血术（一个单位攻+1；2轮；不可叠加）噬血之舞（一回合一次；仅能对己方单位使用，那个单位和此单位同时血+1/4，魔+1，同时直到下个噬血回合开始前不能移动或者使用位移技能） 献祭仪式（魔力点-4；选择一个被破坏的单位；召唤到周围；那个单位血上限为1/4，魔回满）"
    raw_trait_text = "普攻半破魔；每次场上有单位受到其他单位伤害并血-1/4以上时给此单位+1魔力点；此单位最多持有8个魔力点；当持有8个魔力点时此单位不受到技能伤害"

    def build_skills(self) -> list[Skill]:
        return [
            RecoverManaSkill(),
            DrainManaSkill(),
            MagicShieldSkill(),
            BloodGuardSkill(),
            BloodArtSkill(),
            BloodDanceSkill(),
            SacrificeRitualSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            HalfPierceAttackTrait(),
            BloodManaPointTrait(),
            BloodSkillDamageGuardTrait(),
        ]


