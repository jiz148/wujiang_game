"""R031 Natsume wind wall and allied attack mechanics."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext
    from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import ShensuSkill
from wujiang.tactical.heroes.common import payload_target_units
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class NatsumeShensuSkill(ShensuSkill):
    def ai_self_buff_score(self, context: AISkillScoreContext) -> float:
        return context.shared.natsume_shensu_score(context)


def _natsume_active_score(skill: Skill, context: AISkillScoreContext) -> float:
    battle, actor, payload = context.battle, context.actor, context.payload
    target = battle.units.get(str(payload.get("target_unit_id") or ""))
    target = battle.effect_recipient(target) if target is not None else None
    danger_before = context.unit_danger(target) if target is not None else 0.0
    caster_danger = context.unit_danger(actor) if target is not None and target.player_id != actor.player_id else 0.0
    with context.probe_rollback():
        before = context.outcome_snapshot()
        hidden = {unit.unit_id for unit in battle.all_units() if unit.has_status("隐身")}
        declared = {**payload, **skill.queued_payload_metadata(battle, actor, payload)}
        skill.execute(battle, actor, declared)
        value = context.outcome_value(before)
        for unit, hp, _, _, _, _ in before.values():
            if unit.player_id != actor.player_id:
                value -= max(0.0, unit.current_hp - hp) * 110.0
            if unit.alive and unit.unit_id in hidden and not unit.has_status("隐身"):
                value += 65.0 if unit.player_id != actor.player_id else -85.0
        if target is not None and target.alive:
            danger_after = context.unit_danger(target)
            value += danger_before - danger_after if target.player_id == actor.player_id else (danger_after - danger_before) * 0.5
            if target.player_id != actor.player_id:
                value += caster_danger - context.unit_danger(actor)
    return value if value > 0 else -20.0


class WindWallCounterStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("风壁计数点", "可让夏目的风壁远程保护此单位一次。", duration=None)


class WindWallBlockStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("风壁", "挡住下一次敌方普攻或技能的整次伤害和附效，包括破魔；不挡独立场地伤害。", duration=None)
        self.blocked_token = None

    def block(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        if (owner is not ctx.target or source is None or source.player_id == owner.player_id
                or ctx.from_field_effect or not (ctx.is_skill or "attack" in ctx.tags)):
            return
        token = battle.current_action_resolution_token
        if self.blocked_token is not None and self.blocked_token != token:
            return
        if ctx.cancelled:
            return
        first_block = self.blocked_token is None
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【风壁】挡住了【{ctx.action_name}】。"
        if token is None:
            owner.remove_status(self, battle)
        else:
            self.blocked_token = token
            self.expires_after_action_token = token
        if first_block:
            battle.emit_defense_visual_event(source=source, target=owner, action_name=ctx.action_name, defense_reason="block")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        self.block(battle, ctx)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self.block(battle, ctx)


def natsume_effect_snapshot(unit):
    return (unit.alive, unit.banished, unit.position, unit.current_hp, unit.current_mana, unit.mana_points,
            unit.cannot_move, unit.cannot_normal_move, unit.cannot_attack, unit.cannot_use_skills,
            unit.total_shields(), tuple((status.name, status.duration) for status in unit.statuses if status.name != "风壁"),
            tuple(unit.stat(key) for key in ("attack", "defense", "speed", "attack_range")))


def natsume_protection_changes_outcome(battle, queued_action, target):
    from copy import deepcopy
    from dataclasses import replace
    from wujiang.tactical.engine.core import battle_state_rollback
    outcomes = []
    for protected in (False, True):
        with battle_state_rollback(battle):
            if protected:
                target.add_status(WindWallBlockStatus())
            probe = replace(queued_action, payload=deepcopy(queued_action.payload))
            try:
                battle.resolve_queued_action(probe)
            except ActionError:
                return False
            outcomes.append(natsume_effect_snapshot(target))
    return outcomes[0] != outcomes[1]


class NatsumeWindWordSkill(Skill):
    ai_early_effect_score = True

    def __init__(self) -> None:
        super().__init__("natsume_wind_word", "风之语", "普通技能：每回合1次、0魔；范内直线点单位，破魔，只回复1/4生命，再尽量沿直线拉到自身邻圈合法格。", max_uses_per_turn=1, target_mode="unit")

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        return _natsume_active_score(self, context)

    def ignores_shield_for_payload(self, battle, actor, payload):
        return True

    def targets(self, battle, actor):
        return [unit for unit in battle.all_units() if battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def _pull_destination(self, battle, actor, target):
        if actor.position is None or target.position is None or actor is target:
            return None
        own_cells, body = battle.unit_cells(actor), battle.unit_cells(target)
        if any(a.distance_to(b) <= 1 for a in own_cells for b in body):
            return target.position
        candidates = set()
        for a in own_cells:
            for b in body:
                dx, dy = a.x - b.x, a.y - b.y
                if dx and dy and abs(dx) != abs(dy):
                    continue
                sx, sy = (0 if dx == 0 else (1 if dx > 0 else -1)), (0 if dy == 0 else (1 if dy > 0 else -1))
                for step in range(1, max(abs(dx), abs(dy))):
                    dest = target.position.offset(sx * step, sy * step)
                    placed = target.footprint_cells_at(dest)
                    if set(placed).isdisjoint(own_cells) and min(x.distance_to(y) for x in placed for y in own_cells) == 1:
                        candidates.add(dest)
        declared = actor.position
        actor.position = getattr(actor, "_resolution_actual_position", None) or declared
        try:
            return next((cell for cell in sorted(candidates, key=lambda cell: (cell.distance_to(target.position), cell.y, cell.x))
                         if not any(battle.is_forced_movement_blocked(part) for part in target.footprint_cells_at(cell))
                         and battle.can_place_unit(target, cell, ignore=target, mover=target)), None)
        finally:
            actor.position = declared

    def execute(self, battle, actor, payload):
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        if not payload.get("queued_resolution"):
            battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range(), action_name=self.name)
        ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                     is_hostile=actor.is_enemy_of(target), ignore_shield=True,
                                     tags={"skill", "natsume_wind_word"})
        if ctx.cancelled or target.direct_effects_blocked():
            return
        if actor.is_enemy_of(target) and target.total_shields() > 0 and ctx.ignore_shield:
            target.consume_one_shield()
            battle.record_shield_break_summary(actor, target, self.name)
        battle.heal(HealContext(source=actor, target=target, amount=0.25, action_name=self.name, tags={"skill"}))
        if not target.alive or target.position is None:
            return
        destination = self._pull_destination(battle, actor, target)
        if destination is not None and destination != target.position:
            try:
                battle.move_unit(target, destination, via_skill=True, forced=True, allow_anywhere=True, tags={"natsume_wind_word"})
            except ActionError:
                battle.log(f"【风之语】已完成治疗判定，目标无法被拉近。")

    def preview(self, battle, actor):
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class NatsumeWindWallSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__(
            "natsume_wind_wall",
            "风壁",
            "被动技能：连锁速度 2，费 1 魔；保护一个当前受影响的己方单位，使下一次伤害和效果无效；可额外保护带有风壁计数点的单位并摘除该计数点。",
            mana_cost=1,
            target_mode="ally",
            timing="passive",
        )

    def _selectable_targets(self, battle: Battle, actor: HeroUnit, queued_action: Any, *,
                            enforce_range: bool = True) -> list[HeroUnit]:
        if queued_action.action_type not in {"attack", "skill", "skill_effect"}:
            return []
        threatened = battle.effect_units_at_cells(queued_action.target_cells)
        if not threatened:
            threatened = battle.effect_units([battle.units[uid] for uid in queued_action.target_unit_ids if uid in battle.units])
        result = []
        for unit in threatened:
            if (unit.player_id != actor.player_id or not battle.unit_can_be_selected(unit, actor=actor)[0]
                    or unit.direct_effects_blocked() or unit.has_status("风壁")
                    or battle.shield_auto_blocks_chain(unit, queued_action)):
                continue
            if not (not enforce_range or unit is battle.effect_recipient(actor) or unit.has_status("风壁计数点")
                    or battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())):
                continue
            if natsume_protection_changes_outcome(battle, queued_action, unit):
                result.append(unit)
        return result

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if not self._selectable_targets(battle, actor, queued_action):
            return False, "当前动作没有风壁可保护的己方目标。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        selectable = {unit.unit_id for unit in self._selectable_targets(battle, actor, queued_action)}
        if not selectable:
            return False, "当前动作没有风壁可保护的己方目标。"
        reaction_payload = dict(payload or {})
        if not reaction_payload.get("target_unit_id") and len(selectable) == 1:
            reaction_payload["target_unit_id"] = next(iter(selectable))
        try:
            targets = payload_target_units(battle, reaction_payload)
        except ActionError as exc:
            return False, str(exc)
        if len(targets) != 1:
            return False, "风壁一次只能保护一个目标。"
        if targets[0].unit_id not in selectable:
            return False, "这个目标当前不能被风壁保护。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        resolving = battle.resolving_action
        committed = bool(resolving is not None and resolving.action_type == "reaction_skill"
                         and resolving.actor_id == actor.unit_id
                         and resolving.payload.get("action_code") == self.code
                         and resolving.payload.get("resources_prepaid"))
        selectable = {unit.unit_id for unit in self._selectable_targets(
            battle, actor, queued_action, enforce_range=not committed,
        )}
        reaction_payload = dict(payload)
        if not reaction_payload.get("target_unit_id") and len(selectable) == 1:
            reaction_payload["target_unit_id"] = next(iter(selectable))
        targets = payload_target_units(battle, reaction_payload)
        if len(targets) != 1:
            raise ActionError("这个目标当前不能被风壁保护。")
        if targets[0].unit_id not in selectable:
            if committed:
                battle.log(f"{actor.name} 的【风壁】原定保护目标已不受这次动作影响，结算落空。")
                return
            raise ActionError("这个目标当前不能被风壁保护。")
        target = battle.effect_recipient(targets[0])
        wall = WindWallBlockStatus()
        target.add_status(wall, source=actor)
        if wall not in target.statuses:
            return
        counter = target.get_status("风壁计数点")
        if counter is not None:
            target.remove_status(counter, battle)
        battle.log(f"{actor.name} 为 {target.name} 张开【风壁】。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("风壁只能通过连锁使用。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        targets = self._selectable_targets(battle, actor, queued_action)
        return {
            "cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
            "selection": {"mode": "multi_unit", "min_targets": 1, "max_targets": 1},
        }

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        battle, actor, queued_action, payload = (context.battle, context.actor,
                                                  context.queued_action, context.payload)
        if not self.can_react_with_payload(battle, actor, queued_action, payload)[0]:
            return -1000.0
        ids = payload.get("target_unit_ids") or [payload.get("target_unit_id")]
        target = battle.units.get(str(ids[0] or ""))
        if target is None:
            targets = self._selectable_targets(battle, actor, queued_action)
            target = targets[0] if len(targets) == 1 else None
        if target is None or target.unit_id in context.shield_protected_ids():
            return -1000.0
        values = []
        for protected in (False, True):
            with ai_probe_rollback(battle):
                before = context.outcome_snapshot()
                was_banished, previous_position = target.banished, target.position
                danger = context.unit_danger(target)
                if protected:
                    target.add_status(WindWallBlockStatus(), source=actor)
                battle.resolve_queued_action(replace(queued_action, payload=deepcopy(queued_action.payload)))
                value = context.outcome_value(before)
                for unit, _, _, _, names, _ in before.values():
                    if unit.player_id == actor.player_id:
                        current = {status.name for status in unit.statuses if status.name != "风壁"}
                        value -= len(current - names) * 45.0
                        value -= len(names - current) * 25.0
                if not was_banished and target.banished:
                    value -= 110.0
                if target.alive and target.position != previous_position:
                    value += danger - context.unit_danger(target)
                values.append(value)
        gain = values[1] - values[0]
        return gain - 18.0 if gain > 0 else -1000.0


class NatsumeDispelSkill(Skill):
    ai_early_effect_score = True
    ai_enemy_impact_requirement = False

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        enemy = ally = 0
        for unit in self.affected_units(battle, actor):
            if not self.has_purge_effect(unit) or not self.target_can_react_to_effect(battle, actor, unit, context.payload):
                continue
            if unit.player_id == actor.player_id:
                ally += 1
            else:
                enemy += 1
        ordinary = self.ai_effect_score(context)
        if enemy < 2:
            return ordinary
        mana_gain = min(enemy + ally, max(0.0, actor.max_mana() - actor.current_mana))
        return max(ordinary, enemy * 65.0 - ally * 85.0 + mana_gain * 18.0)

    def __init__(self) -> None:
        super().__init__("natsume_dispel", "驱散", "普通技能：每回合1次、0魔；固定声明周围11×11，双方召唤物/分身破坏，隐身解除；每个实际受影响受体回复1魔。", max_uses_per_turn=1, target_mode="self")

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        return _natsume_active_score(self, context)

    def affected_cells(self, battle, actor):
        return square_around_cells(battle, battle.unit_cells(actor), radius=5)

    def affected_units(self, battle, actor):
        return battle.effect_units_at_cells(self.affected_cells(battle, actor), ignore=actor)

    def has_purge_effect(self, unit):
        return unit.is_summon or unit.is_clone or any(status.name == "隐身" for status in unit.statuses)

    def queued_payload_metadata(self, battle, actor, payload):
        return {"natsume_dispel_cells": positions_to_dict(self.affected_cells(battle, actor))}

    def get_target_cells_for_payload(self, battle, actor, payload):
        return self.affected_cells(battle, actor)

    def target_can_react_to_effect(self, battle, actor, target, payload):
        if not self.has_purge_effect(target) or target.direct_effects_blocked():
            return False
        from wujiang.tactical.engine.core import battle_state_rollback
        with battle_state_rollback(battle):
            ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True, is_hostile=True,
                                         ignore_targeting_restrictions=True, tags={"skill", "natsume_dispel"})
            return not ctx.cancelled or ctx.destroyed_as_clone

    def execute(self, battle, actor, payload):
        cells = battle.payload_positions(payload, "natsume_dispel_cells") if payload.get("queued_resolution") else self.affected_cells(battle, actor)
        affected_count = 0
        for unit in list(battle.effect_units_at_cells(cells, ignore=actor)):
            if not unit.alive or not self.has_purge_effect(unit) or unit.direct_effects_blocked():
                continue
            ctx = battle.validate_target(actor, unit, action_name=self.name, is_skill=True, is_hostile=True,
                                         ignore_targeting_restrictions=True, tags={"skill", "natsume_dispel"})
            if ctx.cancelled:
                if ctx.destroyed_as_clone:
                    affected_count += 1
                continue
            changed = False
            if unit.is_summon or unit.is_clone:
                unit.current_hp, unit.alive = 0, False
                changed = True
                battle.log_public_event(f"{unit.name} 被【驱散】破坏。", source=actor, target=unit)
            for status in list(unit.statuses):
                if status.name == "隐身":
                    unit.remove_status(status, battle)
                    changed = True
            if changed:
                affected_count += 1
        battle.cleanup_dead_units()
        gained = actor.gain_mana(affected_count) if actor.alive and actor.unit_id in battle.units else 0
        battle.log(f"【驱散】实际影响 {affected_count} 个单位，回复 {gained:g} 魔。")

    def preview(self, battle, actor):
        return {"cells": positions_to_dict(self.affected_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": False}


class NatsumeAllyAttackManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("风壁赠予", "普攻己方单位时不造成伤害；目标魔 +1，并获得一个风壁计数点。")

    def resolve_allied_basic_support(self, battle, actor, target):
        if self.owner is not actor or target.player_id != actor.player_id:
            return False
        if not target.direct_effects_blocked():
            gained = target.gain_mana(1)
            if not target.has_status("风壁计数点"):
                mark = WindWallCounterStatus()
                mark.is_skill_effect = False
                target.add_status(mark, source=actor)
            battle.log(f"{actor.name} 支援 {target.name}：回复 {gained:g} 魔，风壁计数点{'已就绪' if target.has_status('风壁计数点') else '未能施加'}。")
        return True

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id:
            return True, ""
        if target.player_id == actor.player_id:
            return True, ""
        return True, ""

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id:
            return {}
        metadata = {"allow_allied_attack_target": True}
        target = battle.units.get(str((payload or {}).get("target_unit_id") or ""))
        if target is not None and target.player_id == actor.player_id:
            metadata["attack_effect_summary"] = "对原声明格的己方受体回复1魔并给予一个风壁计数点；不造成伤害或消耗护盾。"
        return metadata



