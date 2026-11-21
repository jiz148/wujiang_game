"""R063 D。毁灭小子: persistent drain and double-edged ethereal form."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, Skill, StatusEffect, Unit,
)
from wujiang.tactical.heroes.common import FlagStatus, positions_to_dict
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def _in_range(battle: Battle, actor: Unit, target: Unit) -> bool:
    return (_present(actor) and _present(target)
            and battle.unit_can_be_selected(target, actor=actor)[0]
            and any(origin.distance_to(cell) <= actor.targeting_range()
                    and battle.cells_are_straight_aligned(origin, cell)
                    for origin in battle.unit_cells(actor) for cell in battle.unit_cells(target)))


class RuinEntangleStatus(StatusEffect):
    def __init__(self, caster_id: str) -> None:
        super().__init__("毁灭纠缠", "本人轮初失去1/4血和最多1魔，来源获得实际损失量。",
                         duration=3, tick_scope="owner_turn_start")
        self.caster_id = caster_id

    def on_owner_turn_start(self, battle: Battle) -> None:
        target = self.owner
        if _present(target):
            caster = battle.units.get(self.caster_id)
            damage = battle.resolve_damage(DamageContext(
                source=caster if _present(caster) else None,
                target=target, attack_power=0, raw_damage=0.25,
                is_skill=False, from_field_effect=True, action_name="毁灭纠缠吸血",
                tags={"periodic", "ruin_entangle"},
            ))
            lost = target.drain_mana(1) if target.alive else 0.0
            if _present(caster):
                if damage.actual_damage > 0:
                    battle.heal(HealContext(source=caster, target=caster,
                                            amount=damage.actual_damage,
                                            action_name="毁灭纠缠吸血"))
                if lost > 0:
                    caster.gain_mana(lost)
            battle.log_public_event(f"{target.name} 的纠缠扣血 {damage.actual_damage:g}、扣魔 {lost:g}。",
                                    source=caster if _present(caster) else None, target=target)
        super().on_owner_turn_start(battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        result = super().to_public_dict(battle)
        result["caster_unit_id"] = self.caster_id
        return result


class EtherealFormStatus(FlagStatus):
    provided_flags = ("cannot_attack",)

    def __init__(self) -> None:
        super().__init__("虚灵", "cannot_attack",
                         description="不能普攻、物免；正数生命伤及回血回魔均加倍，持续两本人轮。",
                         duration=2, tick_scope="owner_turn_end")

    def grants_physical_immunity_to(self, battle: Battle, unit: Unit) -> bool:
        return unit is self.owner

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner and ctx.raw_damage is not None and ctx.raw_damage > 0:
            ctx.raw_damage *= 2

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        if (ctx.target is self.owner and ctx.amount > 0
                and "ethereal_recovery_doubled" not in ctx.tags):
            ctx.amount *= 2
            ctx.tags.add("ethereal_recovery_doubled")

    def modify_direct_heal_amount(self, amount: float) -> float:
        return amount * 2 if amount > 0 else amount

    def modify_mana_gain_amount(self, amount: float) -> float:
        return amount * 2 if amount > 0 else amount


class RuinTargetSkill(Skill):
    ai_payload_value_owned = True

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if _in_range(battle, actor, unit)
                and (self.target_mode != "enemy" or unit.player_id != actor.player_id)]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if not targets:
            return False, "当前范内没有合法目标。"
        if payload and payload.get("target_unit_id"):
            if battle.units.get(str(payload["target_unit_id"])) not in targets:
                return False, "所选目标已离开当前范。"
        return True, ""

    def selected(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Unit:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            raise ActionError(f"{self.name} 需要选择当前范内直线目标。")
        return target

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets
                                             for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True}

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"ignore_shield": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [self.selected(battle, actor, payload)]

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(self.selected(battle, actor, payload))

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.targets(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class RuinEntangleSkill(RuinTargetSkill):
    ai_enemy_impact_requirement = True

    def __init__(self) -> None:
        super().__init__("ruin_entangle", "纠缠", "费2魔；范内一敌，破魔并使其三本人轮初被吸1/4血和最多1魔，不叠加。",
                         mana_cost=2, target_mode="enemy")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = self.selected(battle, actor, payload)
        recipient = piercing_nondamage_recipient(
            battle, actor, target, action_name=self.name, code=self.code)
        if recipient is None:
            return
        for status in list(recipient.statuses):
            if isinstance(status, RuinEntangleStatus):
                recipient.remove_status(status, battle)
        recipient.add_status(RuinEntangleStatus(actor.unit_id), source=actor)
        battle.record_skill_effect(recipient)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return (target in self.targets(battle, actor) and not target.magic_immunity
                and target.dodge_charges <= 0)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if (target not in self.targets(battle, actor) or target.magic_immunity
                or target.dodge_charges > 0 or target.skill_non_damage_effects_blocked()
                or target.has_status("毁灭纠缠")):
            return -1000.0
        return 25.0 + min(3.0, target.current_mana) * 15.0 + min(1.0, target.current_hp) * 55.0


class EtherealFormSkill(RuinTargetSkill):
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("ethereal_form", "虚灵", "费1.5魔；范内一人两本人轮禁普攻、物免、生命伤和回复加倍；破魔。",
                         mana_cost=1.5, target_mode="unit")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = self.selected(battle, actor, payload)
        recipient = piercing_nondamage_recipient(
            battle, actor, target, action_name=self.name, code=self.code)
        if recipient is None:
            return
        for status in list(recipient.statuses):
            if isinstance(status, EtherealFormStatus):
                recipient.remove_status(status, battle)
        recipient.add_status(EtherealFormStatus(), source=actor)
        battle.record_skill_effect(recipient)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if (target not in self.targets(battle, actor) or target.has_status("虚灵")
                or (target.player_id != actor.player_id and
                    (target.magic_immunity or target.dodge_charges > 0
                     or target.skill_non_damage_effects_blocked()))):
            return -1000.0
        attacks = max(0, target.attack_actions_per_turn() - target.attacks_used)
        if target.player_id != actor.player_id:
            spell_threat = sum(enemy.player_id == actor.player_id and enemy.alive
                               and enemy.position is not None and not enemy.cannot_use_skills
                               for enemy in battle.all_units())
            return attacks * 24.0 + min(2, spell_threat) * 18.0 - 16.0
        missing_hp = max(0.0, target.max_health - target.current_hp)
        physical_threat = sum(enemy.player_id != actor.player_id and _present(enemy)
                              and battle.distance_between_units(enemy, target) <= enemy.targeting_range() + 2
                              for enemy in battle.all_units())
        return min(2, physical_threat) * 26.0 + missing_hp * 50.0 - attacks * 28.0 - 12.0
