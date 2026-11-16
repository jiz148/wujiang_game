"""The three Earth Fissure bamboo heroes; each light owns its delayed rule."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, QueuedAction, Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import payload_target_unit, positions_to_dict
from wujiang.tactical.heroes.mechanics.status_replacement import replace_status_by_name


def _visible_targets(battle: Battle, actor: Unit, reach: int, *, allies_only: bool = False) -> list[Unit]:
    return [unit for unit in battle.all_units()
            if (not allies_only or unit.player_id == actor.player_id)
            and battle.unit_can_be_selected(unit, actor=actor)[0]
            and battle.unit_target_in_range_and_line(actor, unit, reach)]


def _nearby_allies(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.all_units() if unit.player_id == actor.player_id
            and battle.unit_can_be_selected(unit, actor=actor)[0]
            and battle.distance_between_units(actor, unit) <= 4]


def _missing_health(unit: Unit) -> float:
    return max(0.0, unit.max_health - unit.current_hp)


class BambooSurvivalTrait(Trait):
    def __init__(self) -> None:
        super().__init__("地缝竹满血续命", "满血时受到对方致命伤害，保留1/4血。")

    def lethal_damage_remaining_hp(self, battle: Battle, ctx: DamageContext) -> float | None:
        if (ctx.target is self.owner and ctx.source is not None
                and ctx.source.player_id != ctx.target.player_id
                and abs(ctx.target.current_hp - ctx.target.max_health) < 1e-9):
            ctx.modifier_receipts.add("bamboo_survival")
            return ctx.target.max_health / 4
        return None

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner and ctx.actual_damage > 0 and "bamboo_survival" in ctx.modifier_receipts:
            battle.log_public_event(f"{ctx.target.name} 的竹之生机令其保留1/4血。",
                                    source=ctx.source, target=ctx.target)


class BambooWoundHealingTrait(Trait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("地缝竹受伤疗愈", "受到对方实际伤害后，可令本人周围9×9内一名己方单位回复同量生命。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target is not owner or not owner.alive or owner.position is None
                or ctx.source is None or ctx.source.player_id == owner.player_id or ctx.actual_damage <= 0):
            return
        options = [unit.unit_id for unit in _nearby_allies(battle, owner)]
        choice = battle.optional_position_choice(owner, options,
            action_name="地缝竹受伤后选择疗愈目标", kind="optional_heal_target",
            damage=ctx.actual_damage)
        target = battle.units.get(choice or "")
        if target is None or target.unit_id not in options:
            return
        battle.heal(HealContext(source=owner, target=target, amount=ctx.actual_damage,
                                action_name=self.name, effect_source=self))


class BambooLightStatus(StatusEffect):
    def __init__(self, name: str, description: str, *, source: Unit, created_turn: int) -> None:
        super().__init__(name, description)
        self.source_unit_id = source.unit_id
        self.source_player_id = source.player_id
        self.created_turn = created_turn

    def source(self, battle: Battle) -> Unit | None:
        return battle.units.get(self.source_unit_id) or next(
            (unit for unit in battle.destroyed_units if unit.unit_id == self.source_unit_id), None)

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is not None and battle.turn_number != self.created_turn:
            self.owner.remove_status(self, battle)


class FireBambooStatus(BambooLightStatus):
    def __init__(self, source: Unit, created_turn: int) -> None:
        super().__init__("火之竹光", "造成给对方单位的生命伤变为对本人回复，至本人下一回合结束。",
                         source=source, created_turn=created_turn)

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.source is self.owner and ctx.target.player_id != ctx.source.player_id
                and ctx.target.is_clone and not ctx.cancelled):
            ctx.cancelled = True
            ctx.preserve_followup_effects = True
            battle.log_public_event(f"{ctx.source.name} 的伤害转化为回复，分身未受伤害。",
                                    source=ctx.source, target=ctx.target)

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.source is not self.owner or ctx.target.player_id == ctx.source.player_id
                or ctx.raw_damage is None or ctx.raw_damage <= 0):
            return
        amount = min(ctx.raw_damage, ctx.target.current_hp)
        ctx.raw_damage = 0.0
        ctx.modifier_receipts.add("fire_bamboo_conversion")
        recipient = ctx.target
        if recipient.alive:
            battle.heal(HealContext(source=ctx.source, target=recipient, amount=amount,
                                    action_name=self.name, effect_source=self))
        battle.log_public_event(f"{ctx.target.name} 本应受到的 {amount:g} 点生命伤转为火之竹光回复。",
                                source=ctx.source, target=ctx.target)


def _ice_bamboo_penalty(battle: Battle, actor: Unit, action: QueuedAction) -> None:
    target = battle.units.get(str(action.payload.get("marked_unit_id") or ""))
    if target is None or not target.alive or target.banished or target.position is None:
        return
    battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=0,
        raw_damage=4, is_skill=True, action_name="冰之竹光", tags={"ice_bamboo_penalty"}))


class IceBambooStatus(BambooLightStatus):
    def __init__(self, source: Unit, created_turn: int) -> None:
        super().__init__("冰之竹光", "下个本人回合须造成实际生命伤，否则回合末受到固定4伤。",
                         source=source, created_turn=created_turn)
        self.caused_damage = False
        self.armed = False

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.armed = True
        self.caused_damage = False

    def on_battle_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.armed and ctx.source is self.owner and ctx.actual_damage > 0:
            self.caused_damage = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not self.armed:
            return
        if not self.caused_damage and owner.alive and owner.position is not None:
            source = self.source(battle)
            if source is not None:
                battle.queue_skill_effect_action(actor=source, display_name="冰之竹光惩罚",
                    effect_code="ice_bamboo_penalty", effect_resolver=_ice_bamboo_penalty,
                    payload={"marked_unit_id": owner.unit_id, "after_death_effect": True},
                    target_units=[owner], include_cell_units=False, hostile=source.player_id != owner.player_id)
        owner.remove_status(self, battle)


class RockBambooStatus(BambooLightStatus):
    def __init__(self, source: Unit, created_turn: int) -> None:
        super().__init__("岩之竹光", "费魔使用技能时，周围9×9内全部对方单位回复等量魔，至本人下一回合结束。",
                         source=source, created_turn=created_turn)

    def on_skill_mana_paid(self, battle: Battle, actor: Unit, skill: Skill, mana_paid: float) -> None:
        if actor is not self.owner or mana_paid <= 0:
            return
        for unit in list(battle.all_units()):
            if (unit.player_id != actor.player_id and unit.alive and not unit.banished
                    and unit.position is not None and battle.distance_between_units(actor, unit) <= 4):
                gained = unit.gain_mana(mana_paid)
                battle.log_public_event(f"{unit.name} 因岩之竹光回复 {gained:g} 魔。",
                                        source=actor, target=unit)


class BambooLightSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    status_type: type[BambooLightStatus]

    def __init__(self, code: str, name: str, description: str) -> None:
        super().__init__(code, name, description, mana_cost=0.5, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return _visible_targets(battle, actor, actor.targeting_range())

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.targets(battle, actor):
            return False, "范内直线没有可选的竹光目标。"
        if payload and payload.get("target_unit_id") and not any(
                unit.unit_id == payload["target_unit_id"] for unit in self.targets(battle, actor)):
            return False, "竹光目标须在范内直线。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            raise ActionError("竹光目标已不在范内直线。")
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
            is_hostile=target.player_id != actor.player_id, cannot_evade=True,
            record_effect_on_validation=False)
        if result.cancelled:
            battle.log_public_event(result.reason or f"{target.name} 未受{self.name}影响。",
                                    source=actor, target=target)
            return
        recipient = result.target
        if recipient.skill_non_damage_effects_blocked():
            return
        mark = self.status_type(actor, battle.turn_number)
        replace_status_by_name(battle, recipient, mark)
        if mark not in recipient.statuses:
            return
        battle.record_skill_effect(recipient)
        battle.log_public_event(f"{recipient.name} 受到【{self.name}】影响。", source=actor, target=recipient)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [],
                "requires_target": True}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor) or target is None:
            return -1000.0
        if target.magic_immunity and target.player_id != actor.player_id:
            return -1000.0
        if target.skill_non_damage_effects_blocked():
            return -1000.0
        if target.player_id == actor.player_id or target.total_shields() > 0:
            return -1000.0
        if target.get_status(self.name) is not None:
            return -1000.0
        return self._hostile_value(battle, actor, target)

    def _hostile_value(self, battle: Battle, actor: Unit, target: Unit) -> float:
        return 0.0

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -1000.0


class FireBambooSkill(BambooLightSkill):
    status_type = FireBambooStatus

    def __init__(self) -> None:
        super().__init__("fire_bamboo_light", "火之竹光",
                         "0.5魔；无伤不可闪避。目标至下一本人回合结束前，对对方的伤害转为自身回复。")

    def _hostile_value(self, battle: Battle, actor: Unit, target: Unit) -> float:
        danger = sum(battle.distance_between_units(target, ally) <= target.targeting_range() + 1
                     for ally in battle.all_units() if ally.player_id == actor.player_id and ally.alive)
        return 28.0 + 12.0 * min(2, danger) if danger else -1000.0


class IceBambooSkill(BambooLightSkill):
    status_type = IceBambooStatus

    def __init__(self) -> None:
        super().__init__("ice_bamboo_light", "冰之竹光",
                         "0.5魔；无伤不可闪避。目标下一本人回合未造成伤害则回合末受到固定4伤。")

    def _hostile_value(self, battle: Battle, actor: Unit, target: Unit) -> float:
        if target.current_hp <= 4:
            return 42.0
        can_hit = any(battle.distance_between_units(target, ally) <= target.targeting_range() + 1
                      for ally in battle.all_units() if ally.player_id == actor.player_id and ally.alive)
        return 16.0 if can_hit else 36.0


class RockBambooSkill(BambooLightSkill):
    status_type = RockBambooStatus

    def __init__(self) -> None:
        super().__init__("rock_bamboo_light", "岩之竹光",
                         "0.5魔；无伤不可闪避。目标费魔技能时其周围9×9内对方单位回等量魔。")

    def _hostile_value(self, battle: Battle, actor: Unit, target: Unit) -> float:
        # The affected 9×9 belongs to the marked target, not to the caster.
        allies = [unit for unit in battle.all_units() if unit.player_id == actor.player_id
                  and unit.alive and unit.position is not None
                  and battle.distance_between_units(target, unit) <= 4
                  and unit.current_mana < unit.stat("mana")]
        return (18.0 + min(2, len(allies)) * 12.0) if allies and target.current_mana > 0 else -1000.0


class SoulBambooSkill(Skill):
    ai_payload_value_owned = True
    ai_optional_choice_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("soul_bamboo_light", "魂之竹光",
                         "范4、每本人回合一次；仅在本人血恰为1/4时使用，使一名己方单位满血。",
                         max_uses_per_turn=1, target_mode="ally")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return _visible_targets(battle, actor, 4, allies_only=True)

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if abs(actor.current_hp - actor.max_health / 4) > 1e-9:
            return False, "只有本人血量恰为1/4时才能使用魂之竹光。"
        if not self.targets(battle, actor):
            return False, "范4直线内没有可选的己方目标。"
        if payload and payload.get("target_unit_id") and not any(
                unit.unit_id == payload["target_unit_id"] for unit in self.targets(battle, actor)):
            return False, "魂之竹光须选择范4直线内己方单位。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            raise ActionError("魂之竹光目标已失效。")
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=False, record_effect_on_validation=False)
        if not result.cancelled:
            battle.heal(HealContext(source=actor, target=result.target, amount=result.target.max_health,
                                    action_name=self.name, effect_source=self))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [],
                "requires_target": True}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target not in self.targets(battle, actor):
            return -1000.0
        return _missing_health(target) * (32.0 if not target.is_summon else 18.0) - 3.0

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -1000.0

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if prompt.get("kind") != "optional_heal_target":
            return None
        target = battle.units.get(choice)
        if target is None or target.player_id != actor.player_id:
            return -1000.0
        amount = min(float(prompt.get("damage") or 0), _missing_health(target))
        return amount * (32.0 if not target.is_summon else 18.0)
