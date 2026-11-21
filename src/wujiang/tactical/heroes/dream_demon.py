"""R095 梦魔: temporary-stat calm, yawn economy and dream form."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import Battle, DamageContext, Skill, StatusEffect, Unit
from wujiang.tactical.heroes.common import payload_target_unit, positions_to_dict


_RESET_STATS = ("attack", "defense", "speed", "attack_range")


class CalmResetStatus(StatusEffect):
    """Ignore stat modifiers present at cast without dispelling their other rules."""

    def __init__(self, suppressed: tuple[StatusEffect, ...]) -> None:
        super().__init__("镇定归原", "施放镇定时已有的攻守速范临时变化无效；不影响其他状态规则或之后的增益。")
        self.suppressed = suppressed

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        if owner is None or stat_name not in _RESET_STATS:
            return value
        suppressed_ids = {id(status) for status in self.suppressed if status in owner.statuses}
        if not suppressed_ids:
            return value
        # Replay the prefix of the ordinary stat pipeline with only the
        # captured status modifiers omitted. Later statuses still run after us.
        recalculated = float(getattr(owner.base_stats, stat_name))
        traits = () if owner.innate_traits_suppressed() else owner.traits
        for component in owner.iter_components():
            if component is self:
                break
            if id(component) in suppressed_ids:
                continue
            before = recalculated
            recalculated = component.modify_stat(stat_name, recalculated)
            for trait in traits:
                recalculated = trait.modify_received_stat_change(component, stat_name,
                                                                  before, recalculated)
        return recalculated

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None and not any(status in self.owner.statuses for status in self.suppressed):
            self.owner.remove_status(self, battle)


def _temporary_stat_sources(unit: Unit) -> list[StatusEffect]:
    """Only removable effects that currently modify the four saved-rule stats."""
    already_suppressed = {id(status) for marker in unit.statuses
                          if isinstance(marker, CalmResetStatus) for status in marker.suppressed}
    result: list[StatusEffect] = []
    for status in unit.statuses:
        if isinstance(status, CalmResetStatus) or id(status) in already_suppressed:
            continue
        if any(status.modify_stat(name, float(getattr(unit.base_stats, name)))
               != float(getattr(unit.base_stats, name)) for name in _RESET_STATS):
            result.append(status)
    return result


def _dream_pierces(battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
    target = battle.units.get(str(payload.get("target_unit_id") or ""))
    return bool(actor.has_status("入梦") and target is not None and target.has_status("哈欠"))


class DreamTargetSkill(Skill):
    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return _dream_pierces(battle, actor, payload) or super().ignores_shield_for_payload(battle, actor, payload)

    def ai_ignores_shield_for_target(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any], target: Unit) -> bool:
        return bool(actor.has_status("入梦") and target.has_status("哈欠")) or super().ai_ignores_shield_for_target(
            battle, actor, payload, target)

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit.alive and not unit.banished and unit.position is not None
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.distance_between_units(actor, unit) <= actor.targeting_range()]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([unit.position for unit in targets]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        target = payload_target_unit(battle, payload)
        return [target] if target in self.targets(battle, actor) else []

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.targets(battle, actor)]


class CalmSkill(DreamTargetSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("calm", "镇定", "费1魔；使一名单位攻守速范的临时变化回到入场基础值，不改变当前血魔。",
                         mana_cost=1, target_mode="unit")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target not in self.targets(battle, actor):
            return -1000.0
        sources = _temporary_stat_sources(target)
        if not sources:
            return -1000.0
        value = 0.0
        for stat_name, weight in (("attack", 22.0), ("defense", 19.0),
                                  ("speed", 17.0), ("attack_range", 15.0)):
            baseline = float(getattr(target.base_stats, stat_name))
            after = baseline
            for status in sources:
                after = status.modify_stat(stat_name, after)
            delta = after - baseline
            value += delta * weight * (1 if target.player_id != actor.player_id else -1)
        return value - 10.0 if value > 10.0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_hostile_target_impact(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], target: Unit) -> bool | None:
        return bool(_temporary_stat_sources(target)) if target.player_id != actor.player_id else None

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            battle.log_public_event("镇定目标已失效。", source=actor)
            return
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=target.player_id != actor.player_id)
        if result.cancelled:
            battle.log_public_event(result.reason or "镇定未命中。", source=actor, target=target)
            return
        reset = _temporary_stat_sources(result.target)
        markers = [status for status in result.target.statuses if isinstance(status, CalmResetStatus)]
        prior = [status for marker in markers for status in marker.suppressed
                 if status in result.target.statuses]
        for marker in markers:
            result.target.remove_status(marker, battle)
        suppressed = tuple(dict.fromkeys([*prior, *reset]))
        if suppressed:
            result.target.add_status(CalmResetStatus(suppressed), source=actor)
        if reset:
            battle.record_skill_effect(result.target)
        battle.log_public_event(f"{result.target.name} 的临时能力变化被镇定清除（{len(reset)}项）。",
                                source=actor, target=result.target)


class YawnStatus(StatusEffect):
    prevents_movement = True

    def __init__(self, source_unit_id: str) -> None:
        super().__init__("哈欠", "不能移动；本人进攻轮初回1/4血、失1魔，施术者回1魔；任一方被普攻击中后结束。")
        self.source_unit_id = source_unit_id

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        source = battle.units.get(self.source_unit_id)
        if owner is None or source is None or not source.alive:
            if owner is not None:
                owner.remove_status(self, battle)
            return
        owner.heal_fraction(0.25)
        lost = owner.drain_mana(1.0)
        gained = source.gain_mana(1.0)
        battle.log_public_event(f"{owner.name} 的哈欠：血+1/4、魔-{lost:g}；{source.name} 魔+{gained:g}。",
                                source=source, target=owner)

    def on_any_basic_attack_finished(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any], damage_contexts: list[DamageContext],
                                     missed: bool) -> None:
        if self.owner is None or missed:
            return
        affected = {self.owner.unit_id, self.source_unit_id}
        if any(ctx.target.unit_id in affected and not ctx.cancelled for ctx in damage_contexts):
            self.owner.remove_status(self, battle)
            battle.log_public_event(f"{self.owner.name} 的哈欠被普攻打断。", target=self.owner)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["source_unit_id"] = self.source_unit_id
        return data


class YawnSkill(DreamTargetSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("yawn", "哈欠",
                         "费1魔；目标不能移动，本人进攻轮初回1/4血、失1魔，施术者回1魔；任一方被普攻击中后结束；不叠加。",
                         mana_cost=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in super().targets(battle, actor) if not unit.has_status("哈欠")]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.targets(battle, actor):
            return False, "没有可施放哈欠的单位。"
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.targets(battle, actor)}:
                return False, "哈欠不叠加或目标不合法。"
        return True, ""

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target not in self.targets(battle, actor):
            return -1000.0
        if target.player_id == actor.player_id:
            return (30.0 if target.current_hp <= 0.25 and target.current_mana <= 1
                    else -1000.0)
        mobility = min(5.0, target.stat("speed")) * 10.0 if not target.cannot_move else 0.0
        drain = min(1.0, target.current_mana) * 20.0
        healing_risk = min(0.25, 1.0 - target.current_hp) * 110.0
        return mobility + drain + (15.0 if actor.current_mana < actor.stat("mana") else 0.0) - healing_risk - 10.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_hostile_target_impact(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], target: Unit) -> bool | None:
        return not target.has_status("哈欠") if target.player_id != actor.player_id else None

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            battle.log_public_event("哈欠目标已失效或已有哈欠。", source=actor)
            return
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=target.player_id != actor.player_id)
        if result.cancelled:
            battle.log_public_event(result.reason or "哈欠未命中。", source=actor, target=target)
            return
        if result.target.has_status("哈欠"):
            return
        result.target.add_status(YawnStatus(actor.unit_id), source=actor)
        battle.record_skill_effect(result.target)
        battle.log_public_event(f"{result.target.name} 被哈欠困住，不能移动。", source=actor, target=result.target)


class DreamFormStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("入梦", "守变1，攻范速各+2；不能使用哈欠，对哈欠目标的普攻与技能破魔。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "defense":
            return 1.0
        return value + 2.0 if stat_name in {"attack", "speed", "attack_range"} else value

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        return (True, "入梦时不能使用哈欠。") if skill.code == "yawn" else (False, "")

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return {"ignore_shield": True} if target is not None and target.has_status("哈欠") else {}

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is self.owner and ctx.is_skill and ctx.target.has_status("哈欠"):
            ctx.ignore_shield = True


class EnterDreamSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("enter_dream", "入梦",
                         "0魔；每本人轮一次仅开局可切换。开启后守变1、攻范速各+2，禁哈欠，对哈欠目标破魔。",
                         max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used or actor.performed_active_skill:
            return False, "入梦只能在本人回合开始阶段切换。"
        return True, ""

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        enemies = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and unit.position is not None]
        if not enemies:
            return -1000.0
        nearest = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        yawn_targets = sum(enemy.has_status("哈欠") for enemy in enemies)
        if actor.has_status("入梦"):
            return 32.0 if nearest <= 3 and actor.current_hp <= 0.65 else -18.0
        return 45.0 + 24.0 * yawn_targets if nearest <= actor.targeting_range() + 2 else -8.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("入梦")
        if existing is not None:
            actor.remove_status(existing, battle)
            battle.log_public_event(f"{actor.name} 退出入梦。", source=actor)
        else:
            actor.add_status(DreamFormStatus(), source=actor)
            battle.log_public_event(f"{actor.name} 进入梦境形态。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [actor.unit_id], "secondary_cells": [],
                "requires_target": False}
