"""R369: the Last King's attack reactions, order, sacrifice, and recall."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, Position,
    QueuedAction, Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.common import StatModifierStatus, positions_to_dict
from wujiang.tactical.heroes.shadow_gods import restore_shadow_unit


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


def _named_shadow(unit: Unit) -> bool:
    return "影神" in unit.name


class ShadowSupervisionStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("超视攻归一", "当前实际回合攻击值变为1。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return 1.0 if stat_name == "attack" else value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)


class ShadowSupervisionSkill(Skill):
    def __init__(self) -> None:
        super().__init__("shadow_supervision", "超视",
                         "敌方单位攻击时随时使用；该单位本实际回合攻变1。",
                         timing="instant", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        return False, "超视只能在敌方攻击连锁时使用。"

    def can_react_to(self, battle: Battle, actor: Unit,
                     queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        attacker = battle.units.get(queued_action.actor_id)
        if not ok or queued_action.action_type != "attack" or not _present(attacker):
            return False, reason or "只能响应敌方实际攻击。"
        return (True, "") if attacker.player_id != actor.player_id else (False, "只能响应敌方攻击。")

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any],
              queued_action: QueuedAction) -> None:
        target = battle.units.get(queued_action.actor_id)
        if target is None:
            return
        old = target.get_status("超视攻归一")
        if old is not None:
            target.remove_status(old, battle)
        target.add_status(ShadowSupervisionStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 超视使 {target.name} 本回合攻变1。",
                                source=actor, target=target)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("超视只能连锁使用。")

    def reaction_preview(self, battle: Battle, actor: Unit,
                         queued_action: QueuedAction) -> dict[str, Any]:
        target = battle.units.get(queued_action.actor_id)
        return {"cells": positions_to_dict(battle.unit_cells(target)) if target else [],
                "target_unit_ids": [target.unit_id] if target else [],
                "secondary_cells": [], "requires_target": False}

    def ai_reaction_score(self, context: Any) -> float:
        target = context.battle.units.get(context.queued_action.actor_id)
        return max(0.0, (target.stat("attack") - 1) * 45.0) if target else -100.0


class ShadowAttackUpSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("shadow_attack_up", "攻+1", "每2本人轮一次，范内己方一人攻+1持续2本人轮。",
                         cooldown_turns=2, target_mode="unit")

    def eligible(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.player_units(actor.player_id) if _present(unit)
                and battle.distance_between_units(actor, unit) <= actor.targeting_range()]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.eligible(battle, actor)}:
                return False, "攻+1只能指定范内己方单位。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units[str(payload["target_unit_id"])]
        target.add_status(StatModifierStatus("末世神攻+1", attack_delta=1, duration=2,
                                             tick_scope="owner_turn_end", description="攻+1。"), source=actor)
        battle.log_public_event(f"{target.name} 攻+1，持续2本人轮。", source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible(battle, actor)
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.eligible(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.eligible(battle, actor):
            return -100.0
        usable = target.attacks_used < target.attack_actions_per_turn()
        in_range = bool(battle.basic_attack_target_ids_for_payload(target))
        return (45.0 if usable and in_range else 12.0) + target.stat("attack") * 6.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ShadowProbabilityBonus(StatusEffect):
    def __init__(self, declaration_id: str | None) -> None:
        super().__init__("终极概率增伤", "本声明下一次实际扣血+1。", duration=None)
        self.expires_after_declaration_id = declaration_id
        self.declaration_id = declaration_id

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        queued = battle.resolving_action
        if (ctx.target is self.owner and ctx.raw_damage > 0 and queued is not None
                and queued.payload.get("declaration_id") == self.declaration_id):
            ctx.raw_damage += 1.0
            self.owner.remove_status(self, battle)


class ShadowProbabilitySkill(Skill):
    source_side_reaction = True
    def __init__(self) -> None:
        super().__init__("shadow_probability", "影。终极概率",
                         "敌方单位将受伤时随时使用；该次伤害+1；每实际回合首用免费。",
                         timing="instant", mana_cost=1, target_mode="unit")

    def mana_cost_for_payload(self, battle: Battle, actor: Unit,
                              payload: dict[str, Any] | None = None) -> float:
        return 0.0 if self.uses_this_turn == 0 else super().mana_cost_for_payload(battle, actor, payload)

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        return False, "终极概率只能在敌方将受伤的连锁中使用。"

    def eligible(self, battle: Battle, actor: Unit,
                 queued_action: QueuedAction) -> list[Unit]:
        return [battle.units[unit_id] for unit_id in queued_action.target_unit_ids
                if unit_id in battle.units and _present(battle.units[unit_id])
                and battle.units[unit_id].player_id != actor.player_id
                and battle.queued_action_would_damage_unit(queued_action, battle.units[unit_id])]

    def can_react_to(self, battle: Battle, actor: Unit,
                     queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.action_type not in {"attack", "skill", "skill_effect"} or not self.eligible(
                battle, actor, queued_action):
            return False, "仅能在对方单位将受到实际伤害时使用。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: Unit,
                               queued_action: QueuedAction,
                               payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id") not in {unit.unit_id for unit in self.eligible(
                battle, actor, queued_action)}:
            return False, "请选择将受伤的敌方单位。"
        return True, ""

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any],
              queued_action: QueuedAction) -> None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None:
            return
        target.add_status(ShadowProbabilityBonus(queued_action.payload.get("declaration_id")), source=actor)
        battle.log_public_event(f"{actor.name} 使 {target.name} 本次将受伤害+1。", source=actor, target=target)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("终极概率只能连锁使用。")

    def reaction_preview(self, battle: Battle, actor: Unit,
                         queued_action: QueuedAction) -> dict[str, Any]:
        targets = self.eligible(battle, actor, queued_action)
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_reaction_payloads(self, battle: Battle, actor: Unit,
                             queued_action: QueuedAction,
                             option: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "chain_react", "unit_id": actor.unit_id, "action_code": self.code,
                 "target_unit_id": unit.unit_id} for unit in self.eligible(battle, actor, queued_action)]

    def ai_reaction_score(self, context: Any) -> float:
        target = context.battle.units.get(str(context.payload.get("target_unit_id") or ""))
        return 65.0 + (90.0 if target and target.current_hp <= 1 else 0.0)


class ShadowUltimateOrderStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("影。终极秩序", "本人外单位到本人下轮末不能使用技能。",
                         duration=2, tick_scope="owner_turn_end")


class ShadowUltimateOrderField(BattleFieldEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("影。终极秩序", "施术者以外全部单位不能使用技能。")
        self.source_unit_id = source_unit_id

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        owner = battle.units.get(self.source_unit_id)
        if _present(owner) and owner.has_status("影。终极秩序") and actor is not owner:
            return True, "终极秩序：只有末世神可以使用技能。"
        return False, ""


class ShadowUltimateOrderSkill(Skill):
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("shadow_ultimate_order", "影。终极秩序",
                         "大招；直到本人下轮末，其他所有单位不能使用技能。",
                         max_uses_per_battle=1, target_mode="self")
        self.is_ultimate = True
        self.field: ShadowUltimateOrderField | None = None

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        old = actor.get_status("影。终极秩序")
        if old is not None:
            actor.remove_status(old, battle)
        actor.add_status(ShadowUltimateOrderStatus(), source=actor)
        if self.field in battle.field_effects:
            battle.remove_field_effect(self.field)
        self.field = ShadowUltimateOrderField(actor.unit_id)
        battle.add_field_effect(self.field, source=actor)
        battle.log_public_event(f"{actor.name} 发动终极秩序，其他单位技能被封禁。", source=actor)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.field in battle.field_effects:
            battle.remove_field_effect(self.field)
        self.field = None

    def ai_priority_score(self, context: Any) -> float:
        battle, actor = context.battle, context.actor
        enemies = sum(1 for unit in battle.enemy_units(actor.player_id) if _present(unit))
        allies = sum(1 for unit in battle.player_units(actor.player_id) if _present(unit) and unit is not actor)
        return 45.0 * enemies - 30.0 * allies


class ShadowChainedAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("被连锁反击", "本人的攻击每被连锁一次，对受击者另排攻减守的破魔伤。")

    def on_action_chained(self, battle: Battle, declared_action: QueuedAction,
                          reaction: QueuedAction) -> None:
        owner = self.owner
        if (owner is None or not _present(owner) or declared_action.actor_id != owner.unit_id
                or declared_action.action_type != "attack"):
            return
        targets = [battle.units[unit_id] for unit_id in declared_action.target_unit_ids
                   if unit_id in battle.units and _present(battle.units[unit_id])]
        if not targets:
            return
        target = targets[0]
        battle.queue_skill_effect_action(
            actor=owner, display_name="影神连锁反击", effect_code="shadow_chained_attack",
            target_units=[target], target_cells=battle.unit_cells(target),
            include_cell_units=False, effect_resolver=self.resolve_retaliation,
        )

    def resolve_retaliation(self, battle: Battle, actor: Unit,
                            queued: QueuedAction) -> None:
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if not _present(target):
                continue
            raw = max(0.0, actor.stat("attack") - target.stat("defense"))
            if raw > 0:
                battle.resolve_damage(DamageContext(
                    source=actor, target=target, attack_power=0, raw_damage=raw,
                    is_skill=True, ignore_shield=True, action_name="影神连锁反击",
                    tags={"skill", "shadow_chained_attack"},
                ))


class ShadowRebirthSkill(Skill):
    """Death-only ability marker; its owning trait handles the death transaction."""

    def __init__(self) -> None:
        super().__init__("shadow_rebirth", "影之重生",
                         "真死时可献祭两名己方影神，从其中一人原格满状态复出。",
                         target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        return False, "影之重生仅在本人真正被破坏时触发。"

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("影之重生仅在死亡结算中触发。")


class ShadowRebirthTrait(Trait):
    requires_damage_choice = True
    ai_optional_choice_owned = True

    def __init__(self) -> None:
        super().__init__("影之重生", "真正被破坏时，可牺牲两名友方影神并在一人原地满状态复出。")

    def candidates(self, battle: Battle) -> list[Unit]:
        owner = self.owner
        return [unit for unit in battle.all_units() if owner is not None and unit is not owner
                and unit.player_id == owner.player_id and _present(unit) and _named_shadow(unit)]

    def on_owner_destruction_pending(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.alive or getattr(owner, "_shadow_rebirth_in_progress", False):
            return
        candidates = self.candidates(battle)
        if len(candidates) < 2:
            return
        ids = [unit.unit_id for unit in candidates]
        first = battle.optional_position_choice(owner, ids,
                                                action_name="影之重生：选第一名献祭者",
                                                kind="optional_unit_selection",
                                                choice_context={"shadow_rebirth_step": "first"})
        if first not in ids:
            return
        second = battle.optional_position_choice(owner, [unit_id for unit_id in ids if unit_id != first],
                                                 action_name="影之重生：选第二名献祭者",
                                                 kind="required_unit_selection",
                                                 choice_context={"shadow_rebirth_step": "second"})
        if second not in ids or second == first:
            return
        chosen = battle.optional_position_choice(owner, [first, second],
                                                 action_name="影之重生：选复出原格",
                                                 kind="required_unit_selection",
                                                 choice_context={"shadow_rebirth_step": "origin"})
        if chosen not in {first, second}:
            return
        victims = [battle.units.get(first), battle.units.get(second)]
        if any(not _present(unit) for unit in victims):
            return
        origin = battle.units[chosen].position
        owner._shadow_rebirth_in_progress = True
        try:
            # Keep the owner registered and alive while the two actual deaths
            # run their own callbacks; the outer cleanup then sees the rescue.
            owner.alive = True
            for victim in victims:
                victim.current_hp = 0.0
                victim.alive = False
                battle.log_public_event(f"{owner.name} 献祭 {victim.name}。", source=owner, target=victim)
            battle.cleanup_dead_units()
            if any(victim.unit_id in battle.units for victim in victims) or not battle.can_place_unit(
                    owner, origin, ignore=owner, mover=owner):
                owner.alive = False
                return
            owner.position = origin
            owner.current_hp = owner.max_health
            owner.current_mana = owner.max_mana()
            owner.turn_ready = False
            owner.banished = False
            owner.attacks_used = 0
            owner.move_used = False
            for status in list(owner.statuses):
                if status.duration is not None:
                    owner.remove_status(status, battle)
            battle.log_public_event(f"{owner.name} 从献祭原格满状态重生。", source=owner, target=owner)
        finally:
            owner._shadow_rebirth_in_progress = False

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        step = (prompt.get("choice_context") or {}).get("shadow_rebirth_step")
        if actor is not self.owner or not step:
            return None
        unit = battle.units.get(choice)
        if not _present(unit):
            return -1000.0
        if step == "origin":
            return 40.0 - min((unit.position.distance_to(enemy.position)
                               for enemy in battle.enemy_units(actor.player_id) if enemy.position), default=8)
        return 50.0 - unit.level * 5.0 - unit.current_hp * 10.0


class ShadowKillRecallTrait(Trait):
    requires_damage_choice = True
    ai_optional_choice_owned = True

    def __init__(self) -> None:
        super().__init__("击毁影神召回", "本人每真正击毁一名武将，在周围召回一名已毁影神。")

    def candidates(self, battle: Battle) -> list[Unit]:
        return [unit for unit in battle.destroyed_units if not unit.alive and _named_shadow(unit)
                and battle.unit_entry_allowed(unit)[0]]

    def on_unit_destruction_finalized(self, battle: Battle, destroyed_unit: Unit,
                                      source_unit_id: str | None) -> None:
        owner = self.owner
        if (not _present(owner) or getattr(owner, "_shadow_rebirth_in_progress", False)
                or source_unit_id != owner.unit_id or destroyed_unit is owner
                or destroyed_unit.is_summon or destroyed_unit.is_clone):
            return
        choices = self.candidates(battle)
        if not choices:
            return
        ids = [unit.unit_id for unit in choices]
        chosen = battle.optional_position_choice(owner, ids,
                                                 action_name="击毁后召回一名影神",
                                                 kind="required_unit_selection",
                                                 choice_context={"shadow_kill_recall_step": "unit"})
        target = next((unit for unit in choices if unit.unit_id == chosen), None)
        if target is None or owner.position is None:
            return
        cells = [cell for cell in battle.neighbors(owner.position)
                 if battle.can_place_unit(target, cell, ignore=target, mover=target)]
        options = [f"{cell.x},{cell.y}" for cell in cells]
        chosen_cell = battle.optional_position_choice(owner, options,
                                                      action_name="击毁后召回影神落格",
                                                      kind="required_placement",
                                                      choice_context={"shadow_kill_recall_step": "cell"})
        if chosen_cell not in options:
            return
        x, y = (int(part) for part in chosen_cell.split(",", 1))
        restore_shadow_unit(battle, owner, target, Position(x, y), action_name="击毁奖励")

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if actor is not self.owner:
            return None
        step = (prompt.get("choice_context") or {}).get("shadow_kill_recall_step")
        if step == "unit":
            unit = next((unit for unit in battle.destroyed_units if unit.unit_id == choice), None)
            return unit.level * 12.0 + unit.stat("attack") * 8.0 if unit else -1000.0
        if step == "cell":
            try:
                x, y = (int(part) for part in choice.split(",", 1))
            except (AttributeError, ValueError):
                return -1000.0
            return 30.0 - min((Position(x, y).distance_to(enemy.position)
                               for enemy in battle.enemy_units(actor.player_id) if enemy.position), default=8)
        return None
