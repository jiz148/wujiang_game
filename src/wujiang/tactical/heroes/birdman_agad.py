"""R360 Agad's aerial suppression, first strike and dual racial identity."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Skill, StatusEffect, TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.common import FlyingTrait, InvincibleUntilActionStatus, ensure_enemy, payload_target_unit


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def _native_flight_active(unit: Unit) -> bool:
    return (any(isinstance(trait, FlyingTrait) for trait in unit.traits)
            and not unit.innate_traits_suppressed()
            and not any(getattr(status, "suppresses_native_flying", False) for status in unit.statuses))


class AgadDropSuppression(StatusEffect):
    suppresses_native_flying = True

    def __init__(self, applied_turn: int) -> None:
        super().__init__("打落压制", "原生飞行无效、速-3最低1，持续三个受体本人轮。")
        self.applied_turn = applied_turn
        self.own_ends = 0

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 3.0) if stat_name == "speed" else value

    def bind(self, owner: Unit) -> "AgadDropSuppression":
        super().bind(owner)
        FlyingTrait.refresh_flight_state(owner, suppressing=True)
        return self

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            FlyingTrait.refresh_flight_state(self.owner)

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number <= self.applied_turn:
            return
        self.own_ends += 1
        if self.own_ends >= 3 and self.owner is not None:
            self.owner.remove_status(self, battle)


class AgadFirstStrikeStatus(StatusEffect):
    """One declared action pierces shields and cannot be evaded."""

    def __init__(self, declaration_id: str) -> None:
        super().__init__("行刺首击", "本次普攻或技能破魔且无法被回避。")
        self.expires_after_declaration_id = declaration_id

    def _applies(self, battle: Battle, actor: Unit) -> bool:
        queued = battle.resolving_action
        return (actor is self.owner and queued is not None
                and str(queued.payload.get("declaration_id") or "") == self.expires_after_declaration_id
                and queued.action_type in {"attack", "skill"})

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self._applies(battle, ctx.actor):
            ctx.ignore_shield = True
            ctx.cannot_evade = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is not None and self._applies(battle, ctx.source):
            ctx.ignore_shield = True
            ctx.cannot_evade = True


class AgadStealthStatus(InvincibleUntilActionStatus):
    def __init__(self, applied_turn: int) -> None:
        super().__init__()
        self.description = "两本人轮内隐身；第一次普攻或技能破魔且无法被回避，使用即解除。"
        self.applied_turn = applied_turn
        self.own_ends = 0

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if self.owner is None or action_type not in {"attack", "skill"}:
            return
        declaration_id = str(payload.get("declaration_id") or "")
        if declaration_id:
            payload["ignore_shield"] = True
            payload["cannot_evade"] = True
            self.owner.add_status(AgadFirstStrikeStatus(declaration_id))
        battle.log(f"{self.owner.name} 的首次现身行动获得破魔且无法被回避。")
        self.owner.remove_status(self, battle)

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number <= self.applied_turn:
            return
        self.own_ends += 1
        if self.own_ends >= 2 and self.owner is not None:
            self.owner.remove_status(self, battle)


class AgadDropSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("agad_drop", "打落",
                         "本人轮一次：对范内目标以当前攻一半造成普通技能伤；若命中原生飞行者，另使其原生飞行无效、血减半及速-3三轮。",
                         max_uses_per_turn=1, target_mode="enemy")

    def eligible_targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [target for target in battle.enemy_units(actor.player_id)
                if _present(target) and battle.unit_can_be_selected(target, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, target, actor.targeting_range())]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible_targets(battle, actor)
        return {"cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
                "target_unit_ids": [target.unit_id for target in targets], "secondary_cells": [],
                "requires_target": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        if not battle.is_declared_resolution(actor, payload, {"skill"}) and target not in self.eligible_targets(battle, actor):
            raise ActionError("打落目标不在合法范围或直线上。")
        ctx = battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=actor.stat("attack") / 2.0,
            is_skill=True, action_name=self.name, tags={"skill", self.code},
        ))
        target = ctx.target
        if (ctx.cancelled or (ctx.shield_consumed and not ctx.ignore_shield)
                or not _present(target) or not _native_flight_active(target)):
            return
        declared = battle.resolving_action
        first_strike = bool(declared and declared.payload.get("ignore_shield")
                            and declared.payload.get("cannot_evade"))
        battle.queue_skill_effect_action(
            actor=actor, display_name="打落飞行压制", effect_code="agad_drop_followup",
            payload={"target_unit_id": target.unit_id, "first_strike": first_strike,
                     "ignore_shield": first_strike, "cannot_evade": first_strike},
            target_units=[target], effect_resolver=self.resolve_followup,
        )

    @staticmethod
    def resolve_followup(battle: Battle, actor: Unit, queued: Any) -> None:
        target = battle.units.get(str(queued.payload.get("target_unit_id") or ""))
        if not _present(target):
            return
        target_ctx = battle.validate_target(
            actor, target, action_name="打落飞行压制", is_skill=True, is_hostile=True,
            ignore_shield=bool(queued.payload.get("first_strike")),
            cannot_evade=bool(queued.payload.get("first_strike")),
            record_effect_on_validation=False,
        )
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        old = target.get_status("打落压制")
        if old is not None:
            target.remove_status(old, battle)
        target.add_status(AgadDropSuppression(battle.turn_number), source=actor)
        target.current_hp = round(target.current_hp / 2.0, 4)
        battle.record_skill_effect(target)
        battle.log(f"{target.name} 被打落：原生飞行无效、血减半，速-3持续三轮。")

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.eligible_targets(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return [{**context.base_payload, "target_unit_id": item["target_unit_id"]}
                for item in self.ai_payload_candidates(context.battle, context.actor, context.action)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target not in self.eligible_targets(battle, actor):
            return -100.0
        damage = min(target.current_hp, battle.damage_rule.calculate_damage(
            actor.stat("attack") / 2.0, target.stat("defense")))
        if target.magic_immunity:
            return -35.0
        if target.total_shields() and not actor.has_status("行刺首击") and not actor.is_stealthed():
            return 10.0
        value = damage * 90.0
        if _native_flight_active(target):
            value += target.current_hp * 45.0 + max(0.0, target.stat("speed") - 1) * 8.0 + 18.0
        return value - 8.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class AgadAssassinateSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("agad_assassinate", "行刺",
                         "四轮一次：隐身两轮；隐身中第一次普攻或技能破魔且无法被回避。",
                         cooldown_turns=4, max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.is_stealthed():
            return False, "已经处于隐身状态。"
        if battle.unit_in_weather("沙尘", actor):
            return False, "沙尘天气中不能使用隐身。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(AgadStealthStatus(battle.turn_number), source=actor)
        battle.record_skill_effect(actor)
        battle.log(f"{actor.name} 使用【行刺】，进入两轮隐身。")
        battle.clear_all_stealth_if_all_heroes_stealthed()

    def ai_requires_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return False

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return ([{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]
                if self.ai_payload_value(battle, actor, {}) > 0 else [])

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return [{**context.base_payload}]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.is_stealthed() or not _present(actor):
            return -100.0
        enemies = [enemy for enemy in battle.enemy_units(actor.player_id) if _present(enemy)]
        if not enemies:
            return -50.0
        nearby = [enemy for enemy in enemies
                  if battle.distance_between_units(actor, enemy) <= actor.targeting_range() + 1]
        shielded = any(enemy.total_shields() for enemy in nearby)
        attack_open = actor.attacks_used < actor.attack_actions_per_turn()
        return 28.0 + (48.0 if shielded else 0.0) + (18.0 if actor.current_hp <= actor.max_health / 2.0 else 0.0) - (14.0 if attack_open and not shielded else 0.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class AgadAngelIdentityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("天使兼属", "同时被视为鸟人与天使武将。")

    def counts_as_race(self, race: str) -> bool:
        return race == "天使"


class AgadUltimateResetTrait(Trait):
    def __init__(self) -> None:
        super().__init__("击破大招重置", "真正击破敌方武将后重置本人已持有的大招。")

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        if (not _present(owner) or destroyed_unit.player_id == owner.player_id
                or destroyed_unit.is_summon or destroyed_unit.is_clone
                or str(getattr(destroyed_unit, "_destroying_source_unit_id", "")) != owner.unit_id):
            return
        reset = []
        for skill in owner.skills:
            if skill.is_ultimate and (skill.uses_this_battle or skill.cooldown_remaining):
                skill.uses_this_battle = 0
                skill.cooldown_remaining = 0
                reset.append(skill.name)
        if reset:
            battle.log(f"{owner.name} 击破敌方武将，重置大招：{'、'.join(reset)}。")
