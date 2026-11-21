"""R376 Rosita: declaration-strength ward, ice-eye stacks, and death lock."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    Battle, BattleFieldEffect, DamageContext, Position, QueuedAction, Skill,
    StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy
from wujiang.tactical.heroes.ai_candidate_plan import SkillCandidatePlan
from wujiang.tactical.heroes.mechanics.foundation import RemotePierceSkill
from wujiang.tactical.heroes.mechanics.li import ChainPullSkill


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


class RositaDamageWard(Trait):
    def __init__(self) -> None:
        super().__init__("冰眼守御", "来袭声明时攻高于本人守，公式威力先减少本人当前守。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if ctx.target is not owner or ctx.raw_damage is not None or ctx.cancelled:
            return
        defense = owner.stat("defense")
        declared_attack = (ctx.declared_source_attack if ctx.declared_source_attack is not None
                           else ctx.attack_power)
        if declared_attack <= defense:
            return
        ctx.attack_power = max(0.0, ctx.attack_power - defense)
        battle.log_public_event(f"{owner.name} 的冰眼守御使本次公式威力降低 {defense:g}。",
                                source=owner, target=owner)


class IceEyeCounter(StatusEffect):
    def __init__(self) -> None:
        super().__init__("冰眼计数点", "每次被罗西塔实际命中增加；达到8点触发本回合冰爆。", duration=None)
        self.points = 0
        self.last_burst_turn = -1

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["points"] = self.points
        return data


class IceEyeSlow(StatusEffect):
    def __init__(self) -> None:
        super().__init__("冰眼迟滞", "速-1，最低1，持续目标一轮。",
                         duration=1, tick_scope="owner_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 1.0) if stat_name == "speed" else value


class IceEyeMarkTrait(Trait):
    def __init__(self) -> None:
        super().__init__("冰眼命中", "普攻或技能命中给冰眼点与一轮速-1；8点后本回合一次冰爆。")

    def apply_hit(self, battle: Battle, target: Unit) -> None:
        actor = self.owner
        if actor is None or not _present(target) or target.player_id == actor.player_id:
            return
        mark = next((status for status in target.statuses if isinstance(status, IceEyeCounter)), None)
        if mark is None:
            mark = IceEyeCounter()
            target.add_status(mark, source=actor)
            if mark not in target.statuses:
                return
        mark.points += 1
        target.add_status(IceEyeSlow(), source=actor)
        battle.log_public_event(f"{target.name} 的冰眼点数达到 {mark.points}，速降低1。",
                                source=actor, target=target)
        if mark.points >= 8 and mark.last_burst_turn != battle.turn_number:
            mark.last_burst_turn = battle.turn_number
            battle.queue_skill_effect_action(
                actor=actor, display_name="冰眼八点冰爆", effect_code="ice_eye_threshold",
                target_units=[target], target_cells=battle.unit_cells(target), include_cell_units=False,
                payload={"target_unit_id": target.unit_id}, effect_resolver=self.resolve_burst,
            )

    @staticmethod
    def resolve_burst(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        target = battle.units.get(str(queued.payload.get("target_unit_id") or ""))
        if not _present(target):
            return
        battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=target.base_stats.defense + 1,
            is_skill=True, action_name="冰眼八点冰爆", tags={"skill", "ice_eye_threshold"},
        ))

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.source is self.owner and not ctx.cancelled and _present(ctx.target)
                and ctx.target.player_id != self.owner.player_id
                and "ice_eye_threshold" not in ctx.tags
                and "frost_venom_retaliation" not in ctx.tags
                and (ctx.is_skill or "attack" in ctx.tags)):
            self.apply_hit(battle, ctx.target)


def _mark_value(target: Unit) -> float:
    mark = target.get_status("冰眼计数点")
    points = mark.points if isinstance(mark, IceEyeCounter) else 0
    return 7.0 + min(7, points) * 7.0


class RositaAIPolicy(HeroAIPolicy):
    def final_attack_bonus(self, battle: Battle, actor: Unit, target: Unit,
                           payload: dict, shared: Any) -> float:
        if target.player_id == actor.player_id or target.total_shields() > 0:
            return 0.0
        return _mark_value(target)


class RositaChainPullSkill(ChainPullSkill):
    def ai_effect_score(self, context: Any) -> float:
        score = super().ai_effect_score(context)
        if score <= -1000:
            return score
        return score + sum(_mark_value(unit) for unit in context.effect_units(self)
                           if unit.player_id != context.actor.player_id
                           and unit.total_shields() <= 0 and not unit.magic_immunity)

    def on_hit_target(self, battle: Battle, actor: Unit, target: Unit) -> None:
        for trait in actor.traits:
            if isinstance(trait, IceEyeMarkTrait):
                trait.apply_hit(battle, target)
                break


class RositaRemotePierceSkill(RemotePierceSkill):
    ai_payload_value_owned = True
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=24, prefilter_positive=True)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": [cell.to_dict() for cell in pattern]}
                for pattern in self.patterns(battle, actor)]

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        cells = battle.payload_positions(payload, "cells")
        for target in battle.effect_units_at_cells(cells):
            recipient = battle.effect_recipient(target)
            if not _present(recipient) or recipient.player_id == actor.player_id or recipient.magic_immunity:
                continue
            if recipient.total_shields() > 0:
                return True
            power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(recipient, cells) - 1)
            if battle.damage_rule.calculate_damage(power, recipient.stat("defense")) > 0:
                return True
        return False

    def ai_prefilter_score(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any], shared: Any) -> float:
        return self._candidate_value(battle, actor, payload, shared)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        return self._candidate_value(battle, actor, payload)

    def _candidate_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any], shared: Any = None) -> float:
        cells = battle.payload_positions(payload, "cells")
        if not cells:
            return -1000.0
        score = 0.0
        useful_enemy = False
        for target in battle.effect_units_at_cells(cells):
            recipient = battle.effect_recipient(target)
            if not _present(recipient) or recipient.magic_immunity:
                continue
            if recipient.total_shields() > 0:
                impact = 20.0
            else:
                power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(recipient, cells) - 1)
                damage = min(recipient.current_hp,
                             shared.estimate_damage(battle, recipient, power) if shared is not None
                             else battle.damage_rule.calculate_damage(power, recipient.stat("defense")))
                impact = damage * 100.0 + (90.0 if damage >= recipient.current_hp and damage > 0 else 0.0)
                if recipient.player_id != actor.player_id and damage > 0:
                    impact += _mark_value(recipient)
            if recipient.player_id == actor.player_id:
                score -= impact * 1.5
            else:
                useful_enemy |= impact > 0
                score += impact
        return score if useful_enemy else -1000.0

    def ai_control_bonus(self, context: Any, targets: list[Unit]) -> float:
        return sum(_mark_value(unit) for unit in targets
                   if unit.player_id != context.actor.player_id
                   and unit.total_shields() <= 0 and not unit.magic_immunity)


class FrostRetaliationField(BattleFieldEffect):
    def __init__(self, source_unit_id: str, source_player_id: int,
                 killer_unit_id: str, death_turn: int) -> None:
        super().__init__("极寒毒锁余毒", "本实际回合末向击破者反噬。", duration=None)
        self.source_unit_id = source_unit_id
        self.source_player_id = source_player_id
        self.killer_unit_id = killer_unit_id
        self.death_turn = death_turn

    def delays_elimination(self, battle: Battle, eliminated_player_id: int) -> bool:
        return (eliminated_player_id == self.source_player_id and battle.turn_number == self.death_turn
                and _present(battle.units.get(self.killer_unit_id)))

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if battle.turn_number != self.death_turn:
            return
        source = next((unit for unit in battle.destroyed_units
                       if unit.unit_id == self.source_unit_id), None)
        target = battle.units.get(self.killer_unit_id)
        battle.remove_field_effect(self)
        if source is None or not _present(target):
            return
        battle.queue_skill_effect_action(
            actor=source, display_name="极寒毒锁回合末反噬",
            effect_code="frost_venom_retaliation", target_units=[target],
            target_cells=battle.unit_cells(target), include_cell_units=False,
            payload={"after_death_effect": True, "target_unit_id": target.unit_id},
            effect_resolver=self.resolve_revenge,
        )

    @staticmethod
    def resolve_revenge(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        target = battle.units.get(str(queued.payload.get("target_unit_id") or ""))
        if not _present(target):
            return
        battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=target.base_stats.defense + 1,
            is_skill=True, action_name="极寒毒锁反噬",
            tags={"skill", "frost_venom_retaliation"},
        ))


class FrostVenomLockStatus(StatusEffect):
    def __init__(self, activated_turn: int) -> None:
        super().__init__("极寒毒锁", "至下一己方回合开始；被敌方伤害击破时回合末反噬杀手。", duration=None)
        self.activated_turn = activated_turn

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        owner = self.owner
        if owner is not None and active_unit.player_id == owner.player_id and battle.turn_number > self.activated_turn:
            owner.remove_status(self, battle)

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        if destroyed_unit is not owner:
            return
        killer_id = str(getattr(owner, "_destroying_source_unit_id", "") or "")
        killer = battle.units.get(killer_id)
        if killer is None or killer.player_id == owner.player_id:
            return
        battle.add_field_effect(FrostRetaliationField(owner.unit_id, owner.player_id,
                                                     killer.unit_id, battle.turn_number), source=owner)
        battle.log_public_event(f"{owner.name} 被 {killer.name} 击破，极寒毒锁将在本回合末反噬。",
                                source=owner, target=killer)


class FrostVenomLockSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("frost_venom_lock", "极寒毒锁",
                         "每2本人轮一次；至下一己方回合初，敌人伤害真毁本人则本回合末反噬杀手。",
                         cooldown_turns=2, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        old = actor.get_status("极寒毒锁")
        if old is not None:
            actor.remove_status(old, battle)
        actor.add_status(FrostVenomLockStatus(battle.turn_number), source=actor)
        battle.log_public_event(f"{actor.name} 开启极寒毒锁。", source=actor, target=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [actor.position.to_dict()] if actor.position else [],
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.has_status("极寒毒锁") or not _present(actor):
            return -100.0
        nearby = any(_present(enemy) and battle.distance_between_units(actor, enemy) <=
                     enemy.targeting_range() + enemy.normal_move_distance()
                     for enemy in battle.enemy_units(actor.player_id))
        return 35.0 + (1 - actor.current_hp / max(actor.max_health, 0.01)) * 65.0 if nearby else -20.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
