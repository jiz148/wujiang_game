"""R378 Xinyue's public die, temporary race change, and inverted recovery."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, HealContext, Position, Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.common import positions_to_dict


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def _recovery_inverted(unit: Unit) -> bool:
    return unit.race == "兽人" and any(
        isinstance(trait, WerewolfRecoveryTrait) for trait in unit.traits
    )


class WerewolfFormStatus(StatusEffect):
    def __init__(self, previous_race: str) -> None:
        super().__init__("心月兽人形态", "当前种族变为兽人，持续一轮。",
                         duration=1, tick_scope="owner_turn_start")
        self.previous_race = previous_race

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None and self.owner.race == "兽人":
            self.owner.race = self.previous_race

    def on_permanent_race_changed(self, previous_race: str, new_race: str) -> None:
        self.previous_race = new_race


class WerewolfDiceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("心月变身骰", "每个实际回合开始公开掷六面骰；奇数变为兽人一轮。")

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        owner = self.owner
        if not _present(owner):
            return
        result = battle.roll_die(6, action_name="心月变身骰")
        battle.log_public_event(f"{owner.name} 掷出 {result} 点变身骰。", source=owner, target=owner)
        if result % 2 == 0:
            return
        existing = owner.get_status("心月兽人形态")
        if isinstance(existing, WerewolfFormStatus):
            existing.duration = 1
        else:
            status = WerewolfFormStatus(owner.race)
            owner.add_status(status, source=owner)
            if status not in owner.statuses:
                return
            owner.race = "兽人"
        battle.log_public_event(f"{owner.name} 进入兽人形态。", source=owner, target=owner)


class WerewolfRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("心月兽化", "兽人时攻守速+2、范1；回血变减血，回魔变减魔。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if self.owner is None or self.owner.race != "兽人":
            return value
        if stat_name in {"attack", "defense", "speed"}:
            return value + 2
        return 1.0 if stat_name == "attack_range" else value

    def modify_targeting_range(self, value: int) -> int:
        return 1 if self.owner is not None and self.owner.race == "兽人" else value

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        if ctx.target is self.owner and ctx.amount > 0 and _recovery_inverted(self.owner):
            ctx.amount = -ctx.amount
            ctx.tags.add("recovery_inverted")

    def modify_direct_heal_amount(self, amount: float) -> float:
        return -amount if self.owner is not None and amount > 0 and _recovery_inverted(self.owner) else amount

    def modify_mana_gain_amount(self, amount: float) -> float:
        return -amount if self.owner is not None and amount > 0 and _recovery_inverted(self.owner) else amount


class XinyueRecoverySkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    recovery_kind = ""

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.position is None:
            return []
        result: list[Unit] = []
        for unit in battle.all_units():
            if not _present(unit) or not battle.unit_can_be_selected(unit, actor=actor)[0]:
                continue
            if unit is actor or any(
                origin.distance_to(cell) <= actor.targeting_range()
                and battle.cells_are_straight_aligned(origin, cell)
                for origin in battle.unit_cells(actor) for cell in battle.unit_cells(unit)
            ):
                result.append(unit)
        return result

    def target(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Unit:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            raise ActionError(f"{self.name} 需要选择当前范内合法单位。")
        return target

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(self.target(battle, actor, payload))

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [self.target(battle, actor, payload)]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit.unit_id, "x": unit.position.x, "y": unit.position.y}
                for unit in self.targets(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target = battle.effect_recipient(self.target(battle, actor, payload))
        except ActionError:
            return -1000.0
        inverted = _recovery_inverted(target)
        if self.recovery_kind == "health":
            amount = min(0.75, target.current_hp if inverted else
                         max(0.0, target.max_health - target.current_hp))
            weight = 120.0
        else:
            amount = min(3.0, target.current_mana if inverted else
                         max(0.0, target.max_mana() - target.current_mana))
            weight = 22.0
        beneficial = (target.player_id == actor.player_id) != inverted
        return amount * weight * (1.0 if beneficial else -1.5) - 8.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class SecretMedicineSkill(XinyueRecoverySkill):
    recovery_kind = "health"

    def __init__(self) -> None:
        super().__init__("secret_medicine", "秘制药剂", "每本人轮一次；破魔；所选单位血+3/4。",
                         max_uses_per_turn=1, target_mode="unit")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {**super().queued_payload_metadata(battle, actor, payload), "ignore_shield": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = self.target(battle, actor, payload)
        checked = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                         is_hostile=target.player_id != actor.player_id,
                                         ignore_shield=True)
        if checked.cancelled:
            battle.log_public_event(checked.reason or f"{self.name} 未命中。", source=actor, target=target)
            return
        battle.heal(HealContext(source=actor, target=checked.target, amount=0.75,
                                action_name=self.name, effect_source=self))


class MindSupplySkill(XinyueRecoverySkill):
    recovery_kind = "mana"

    def __init__(self) -> None:
        super().__init__("mind_supply", "心灵补给", "每本人轮一次；不可回避；所选单位魔+3。",
                         max_uses_per_turn=1, target_mode="unit")

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {**super().queued_payload_metadata(battle, actor, payload), "cannot_evade": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = self.target(battle, actor, payload)
        checked = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                         is_hostile=target.player_id != actor.player_id,
                                         cannot_evade=True)
        if checked.cancelled:
            battle.log_public_event(checked.reason or f"{self.name} 未命中。", source=actor, target=target)
            return
        before = checked.target.current_mana
        checked.target.gain_mana(3)
        if checked.target.current_mana > before:
            battle.log_public_event(f"{checked.target.name} 的魔增加 {checked.target.current_mana - before:g}。",
                                    source=actor, target=checked.target)
