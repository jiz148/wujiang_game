"""R353 纷乱闪灵: protection, movement tax and stolen support."""

from __future__ import annotations

import random
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, MoveContext,
    Position, QueuedAction, Skill, StatusEffect, TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.common import (
    FlagStatus, MultiTargetChainShieldSkill, payload_direction,
)
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


def _replace_status(battle: Battle, target: Unit, status: StatusEffect, source: Unit) -> bool:
    """Refresh a named one-round control without multiplying its tax or chance."""
    prior = [previous for previous in target.statuses if previous.name == status.name]
    target.add_status(status, source=source)
    if status in target.statuses:
        for previous in prior:
            target.remove_status(previous, battle)
        return True
    return False


class WispFreeTargetingTrait(Trait):
    def __init__(self) -> None:
        super().__init__("九宫闪灵", "攻击和技能可在周围9×9的任意格选择。")

    def modify_targeting_range(self, value: int) -> int:
        return max(value, 4)

    def ignores_direct_unit_target_line(self, battle: Battle, actor: Unit) -> bool:
        return actor is self.owner


class WispEvasionImmunityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("回避后魔免", "回避后到当前英雄行动回合结束魔免。")

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        if ctx.unit is self.owner and "evasion" in ctx.tags and not ctx.cancelled:
            ctx.unit.add_status(FlagStatus("闪灵回避魔免", "magic_immunity",
                duration=1, tick_scope="any_turn_end"))
            battle.log_public_event(f"{ctx.unit.name} 回避后获得本轮魔免。", source=ctx.unit)


class WispFullHealthSurvivalTrait(Trait):
    def __init__(self) -> None:
        super().__init__("满血灵体续命", "满血承受致命伤时保留最大血的1/4。")

    def lethal_damage_remaining_hp(self, battle: Battle, ctx: DamageContext) -> float | None:
        owner = self.owner
        if ctx.target is owner and owner is not None and abs(owner.current_hp - owner.max_health) < 1e-9:
            ctx.modifier_receipts.add("wisp_full_health_survival")
            return owner.max_health / 4
        return None

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner and "wisp_full_health_survival" in ctx.modifier_receipts:
            battle.log_public_event(f"{ctx.target.name} 在满血致命伤下保留了1/4血。",
                                    source=ctx.source, target=ctx.target)


class HauntingGuardStatus(StatusEffect):
    def __init__(self, declaration_id: str) -> None:
        super().__init__("作祟保护", "仅抵消指定敌方主动技能对此受体的效果。",
                         duration=1, tick_scope="any_turn_end")
        self.declaration_id = declaration_id

    def _matches(self, battle: Battle, source: Unit | None, is_skill: bool) -> bool:
        action = battle.resolving_action
        return bool(is_skill and action is not None
                    and (action.action_type == "skill"
                         or (action.action_type == "skill_effect"
                             and action.payload.get("origin_action_type") == "skill"))
                    and action.payload.get("declaration_id") == self.declaration_id
                    and source is not None and self.owner is not None
                    and source.player_id != self.owner.player_id)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if ctx.target is self.owner and self._matches(battle, ctx.actor, ctx.is_skill):
            ctx.cancelled = True
            ctx.reason = f"{ctx.target.name} 受到作祟保护，本次主动技能对此受体无效。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner and self._matches(battle, ctx.source, ctx.is_skill):
            ctx.cancelled = True
            ctx.reason = f"{ctx.target.name} 受到作祟保护，本次主动技能对此受体无效。"


class HauntingSkill(MultiTargetChainShieldSkill):
    def __init__(self) -> None:
        super().__init__("haunting", "作祟", "被动；每本人轮一次；取消敌方主动技能对一名己方受体的效果。",
                         max_uses_per_turn=1, target_mode="ally", timing="passive")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("作祟只能在敌方主动技能的连锁窗口使用。")

    def affordable_target_limit(self, battle: Battle, actor: Unit,
                                queued_action: QueuedAction | None = None) -> int:
        return min(1, super().affordable_target_limit(battle, actor, queued_action))

    def can_react_to(self, battle: Battle, actor: Unit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        active_source = queued_action.action_type == "skill" or (
            queued_action.action_type == "skill_effect"
            and queued_action.payload.get("origin_action_type") == "skill")
        if not active_source or queued_action.source_player_id == actor.player_id:
            return False, "作祟只能响应敌方主动技能。"
        if not self.selectable_targets(battle, actor, queued_action, ignore_existing_shields=True):
            return False, "没有被当前主动技能影响的己方受体。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: Unit,
                               queued_action: QueuedAction, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        if len(battle.payload_target_unit_ids(payload or {})) > 1:
            return False, "作祟一次只能保护一名受体。"
        return True, ""

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        target_ids = battle.payload_target_unit_ids(payload)
        selectable = {unit.unit_id: unit for unit in self.selectable_targets(
            battle, actor, queued_action, ignore_existing_shields=True)}
        if not target_ids and actor.unit_id in selectable:
            target_ids = [actor.unit_id]
        elif not target_ids and len(selectable) == 1:
            target_ids = [next(iter(selectable))]
        if len(target_ids) != 1 or target_ids[0] not in selectable:
            battle.log_public_event(f"{actor.name} 的作祟未找到仍受影响的友军。", source=actor)
            return
        target = selectable[target_ids[0]]
        guard = HauntingGuardStatus(str(queued_action.payload.get("declaration_id") or ""))
        target.add_status(guard, source=actor)
        if guard not in target.statuses:
            battle.log_public_event(f"{actor.name} 的作祟未能保护 {target.name}。", source=actor, target=target)
            return
        battle.record_skill_effect(target)
        battle.log_public_event(f"{actor.name} 以作祟保护 {target.name}。", source=actor, target=target)

    def ai_reaction_score(self, context: Any) -> float:
        target = context.primary_target() or context.proxy_target()
        return context.threat(target) + 22.0 if target is not None else -1000.0


class PoltergeistStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("骚灵现象", "每次移动有1/2几率失败。", duration=1,
                         tick_scope="owner_turn_end")

    def on_before_move(self, battle: Battle, ctx: MoveContext) -> None:
        if ctx.unit is not self.owner or ctx.cancelled or getattr(battle, "_ai_probe_active", False):
            return
        failed = battle.roll_chance(0.5, action_name="骚灵移动判定",
                                    beneficiary_player_id=3 - ctx.unit.player_id)
        battle.log_public_event(f"{ctx.unit.name} 的骚灵移动判定：{'失败' if failed else '成功'}。", source=ctx.unit)
        if failed:
            ctx.cancelled = True
            ctx.reason = f"{ctx.unit.name} 因骚灵现象移动失败。"


class PoltergeistSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("poltergeist", "骚灵现象", "2轮一次；敌方当前在场单位每次移动独立1/2失败，持续1轮。",
                         cooldown_turns=2, target_mode="self")

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return [unit for unit in battle.all_units() if _present(unit) and unit.player_id != actor.player_id]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in self.get_target_units_for_payload(battle, actor, payload):
            result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                is_hostile=True, record_effect_on_validation=False)
            if result.cancelled or result.target.skill_non_damage_effects_blocked():
                continue
            if _replace_status(battle, result.target, PoltergeistStatus(), actor):
                battle.record_skill_effect(result.target)
        battle.log_public_event(f"{actor.name} 发动骚灵现象。", source=actor)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        targets = self.get_target_units_for_payload(battle, actor, payload)
        return sum((6.0 if unit.total_shields() or unit.dodge_charges else
                    18.0 if unit.remaining_normal_move_distance(battle) > 0 else 6.0)
                   for unit in targets if not unit.magic_immunity
                   and not unit.has_status("骚灵现象")) - 8.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class BlindnessStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("致盲", "普通移动和每次普攻额外费1魔；不足则不能使用。", duration=1,
                         tick_scope="owner_turn_end")

    def can_use_normal_move(self, battle: Battle, actor: Unit) -> tuple[bool, str]:
        if actor is self.owner and actor.current_mana + 1e-9 < 1:
            return False, "致盲状态下移动还需1魔。"
        return True, ""

    def can_use_basic_attack(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[bool, str]:
        if (actor is self.owner and not battle.is_declared_resolution(actor, payload, {"attack"})
                and actor.current_mana + 1e-9 < 1):
            return False, "致盲状态下普攻还需1魔。"
        return True, ""

    def on_before_normal_move(self, battle: Battle, ctx: MoveContext) -> None:
        if ctx.unit is self.owner:
            ctx.unit.spend_mana(1)
            battle.log_public_event(f"{ctx.unit.name} 为致盲移动额外支付1魔。", source=ctx.unit)

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if action_type == "attack" and self.owner is not None:
            self.owner.spend_mana(1)
            battle.log_public_event(f"{self.owner.name} 为致盲普攻额外支付1魔。", source=self.owner)


class WispEnemyMarkSkill(Skill):
    """Single enemy controls share the same legal target preview."""

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        # These piercing marks have no damage; the generic damage probe cannot
        # determine whether their status is worth applying.
        return bool(self.ai_payload_value_owned and self.ai_payload_value(battle, actor, payload) > 0)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units() if _present(unit)
                   and unit.player_id != actor.player_id
                   and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]
        return {"cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True}

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            target = battle.units.get(str(payload["target_unit_id"]))
            if not _present(target) or target.player_id == actor.player_id:
                return False, "需要选择一名在场敌方单位。"
        return True, ""


class BlindSkill(WispEnemyMarkSkill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("wisp_blind", "致盲", "费0.5魔；破魔命中，使目标1轮内每次移动或普攻额外费1魔。",
                         mana_cost=0.5, target_mode="enemy")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.get_unit(str(payload.get("target_unit_id") or ""))
        recipient = piercing_nondamage_recipient(battle, actor, target, action_name=self.name, code=self.code)
        if recipient is not None:
            if _replace_status(battle, recipient, BlindnessStatus(), actor):
                battle.record_skill_effect(recipient)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if (not _present(target) or target.player_id == actor.player_id
                or target.has_status("致盲") or target.magic_immunity):
            return -1000.0
        return 12.0 + (30.0 if target.current_mana < 1 else 16.0) + target.stat("attack") * 2

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


_CARDINAL = ((-1, 0), (1, 0), (0, -1), (0, 1))


class ParanormalTerrorSkill(Skill):
    ai_payload_value_owned = True
    ai_complete_direction_payloads = True
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("paranormal_terror", "灵异恐吓", "4轮一次；身前4×4内被击中的敌方召唤物换为己方控制。",
                         cooldown_turns=4, target_mode="cell", direction_mode="required")

    def cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        direction = payload_direction(payload)
        if direction not in _CARDINAL or actor.position is None:
            raise ActionError("灵异恐吓需要选择正交方向。")
        dx, dy = direction
        lateral = (-dy, dx)
        return [cell for forward in range(1, 5) for side in range(-1, 3)
                if battle.in_bounds(cell := actor.position.offset(dx * forward + lateral[0] * side,
                                                                  dy * forward + lateral[1] * side))]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = ({cell for dx, dy in _CARDINAL
                  for cell in self.cells(battle, actor, {"dx": dx, "dy": dy})}
                 if actor.position is not None else set())
        return {"cells": [cell.to_dict() for cell in sorted(cells, key=lambda cell: (cell.y, cell.x))],
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "direction_only", "directions": [
                    {"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"} for dx, dy in _CARDINAL],
                    "prompt": "选择灵异恐吓的前方。"}}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "direction": {"dx": dx, "dy": dy}} for dx, dy in _CARDINAL]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and ("direction" in payload or "dx" in payload):
            try:
                self.cells(battle, actor, payload)
            except (ActionError, TypeError, ValueError):
                return False, "需要选择正交方向。"
        return True, ""

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [unit for unit in battle.effect_units_at_cells(self.cells(battle, actor, payload))
                if unit.is_summon and unit.player_id != actor.player_id]

    def ai_effect_units(self, battle: Battle, actor: Unit,
                        payload: dict[str, Any]) -> list[Unit]:
        return self.get_target_units_for_payload(battle, actor, payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in self.get_target_units_for_payload(battle, actor, payload):
            result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                is_hostile=True, record_effect_on_validation=False)
            recipient = result.target
            if result.cancelled or not _present(recipient) or recipient.skill_non_damage_effects_blocked():
                continue
            if battle.destroy_clone_for_skill_effect(recipient, source=actor, action_name=self.name):
                continue
            battle.transfer_unit_control(recipient, actor)
            battle.record_skill_effect(recipient)
            battle.log_public_event(f"{actor.name} 夺取了 {recipient.name} 的控制权。", source=actor, target=recipient)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            targets = self.get_target_units_for_payload(battle, actor, payload)
        except ActionError:
            return -1000.0
        return sum((12.0 if unit.is_clone else
                    6.0 if unit.total_shields() or unit.dodge_charges else
                    36.0 + unit.stat("attack") * 8.0)
                   for unit in targets if not unit.magic_immunity)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class StolenHealingStatus(StatusEffect):
    def __init__(self, caster_id: str) -> None:
        super().__init__("幻变魅影", "本轮实际回复改由纷乱闪灵获得。", duration=1,
                         tick_scope="owner_turn_end")
        self.caster_id = caster_id

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        owner = self.owner
        caster = battle.units.get(self.caster_id)
        if ctx.target is not owner or owner is None or not _present(caster) or ctx.amount <= 0:
            return
        if owner.cannot_heal or owner.direct_effects_blocked():
            return
        ceiling = owner.max_health if not owner.allow_overheal else float("inf")
        for component in owner.iter_components():
            proposed = component.max_recoverable_hp()
            if proposed is not None:
                ceiling = min(ceiling, proposed)
        transferable = max(0.0, min(ctx.amount, ceiling - owner.current_hp))
        if transferable <= 0:
            return
        ctx.target = caster
        ctx.amount = transferable
        battle.log_public_event(f"{owner.name} 的治疗被幻变魅影转给 {caster.name}。", source=caster, target=owner)


class PhantomChangeSkill(WispEnemyMarkSkill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("phantom_change", "幻变魅影", "每本人轮一次；破魔标记一名敌方，1轮内将其实际回复转给自己。",
                         max_uses_per_turn=1, target_mode="enemy")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.get_unit(str(payload.get("target_unit_id") or ""))
        recipient = piercing_nondamage_recipient(battle, actor, target, action_name=self.name, code=self.code)
        if recipient is not None:
            if _replace_status(battle, recipient, StolenHealingStatus(actor.unit_id), actor):
                battle.record_skill_effect(recipient)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if (not _present(target) or target.player_id == actor.player_id
                or target.has_status("幻变魅影") or target.magic_immunity):
            return -1000.0
        if target.current_hp >= target.max_health or target.cannot_heal:
            return -20.0
        healers = [unit for unit in battle.all_units() if _present(unit)
                   and unit.player_id == target.player_id
                   and any(skill.code == "heal" or "回血" in skill.name or "治疗" in skill.name
                           for skill in unit.action_skills())]
        regeneration = any("回血" in component.name or "回复" in component.name
                           for component in target.iter_components())
        if not healers and not regeneration:
            return -20.0
        opportunity = 1.0 if target in healers or regeneration else 0.6
        denied = target.max_health - target.current_hp
        gained = min(actor.max_health - actor.current_hp, denied)
        return (denied * 40 + gained * 25) * opportunity - 6

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
