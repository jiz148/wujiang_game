"""R326 vain giant shadow and Florenza follow-up."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import is_mana_drain_immune
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.racial_weather import RacialWeatherEffect
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect


class VainGiantShadowStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__(
            "虚荣巨影",
            attack_delta=2,
            description="攻 +2，无法普攻。",
            duration=4,
            tick_scope="owner_turn_end",
        )
        self.flag_name = "cannot_attack"

    def bind(self, owner: HeroUnit) -> "VainGiantShadowStatus":
        super().bind(owner)
        owner.cannot_attack = True
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_attack = owner.is_clone or any(
            getattr(component, "flag_name", "") in {"cannot_attack", "cannot_act"}
            or "cannot_attack" in getattr(component, "provided_flags", ())
            or "cannot_attack" in getattr(component, "locked_flags", ())
            for component in owner.iter_components()
        )


class VainGiantShadowSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.control_score(self)

    def __init__(self) -> None:
        super().__init__(
            "vain_giant_shadow",
            "虚荣巨影",
            "普通技能：每回合一次；可对任意单位或自己使用，破魔且无法被回避；4 轮内攻 +2，无法普攻。",
            max_uses_per_turn=1,
            target_mode="unit",
        )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range(), action_name=self.name)
        return {}

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [battle.effect_recipient(payload_target_unit(battle, payload))]  # type: ignore[list-item]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        apply_piercing_status_effect(
            battle,
            actor,
            target,
            action_name="虚荣巨影",
            status=VainGiantShadowStatus(),
            is_skill=True,
            tags={"skill", "vain_giant_shadow"},
        )

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def cannot_evade_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.effect_units(battle.all_units())
            if unit.alive
            and not unit.banished
            and unit.position is not None
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        return {
            "cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }


class FlorenzaAttackDebuffStatus(StatusEffect):
    def __init__(self, applied_turn_number: int | None = None) -> None:
        super().__init__(
            "弗伦萨普攻弱化",
            "攻、守、速 -1，到 1，持续至下个本人回合结束。",
            duration=1,
            tick_scope="owner_turn_end",
        )
        self.applied_turn_number = applied_turn_number

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "defense", "speed"}:
            return value - min(1.0, max(0.0, value - 1.0))
        return value

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.applied_turn_number:
            super().on_owner_turn_end(battle)


class FlorenzaAttackFollowupTrait(Trait):
    def __init__(self) -> None:
        super().__init__("弗伦萨普攻破魔效果", "普攻带有破魔吸魔，并使目标攻守速 -1 到下回合结束前。")

    def piercing_basic_attack_followup_applies(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> bool:
        return self.owner is actor and not any(
            isinstance(effect, RacialWeatherEffect) and effect.protects_piercing(
                battle, target, is_skill=False, tags={"attack"}, ignore_shield=True,
                half_ignore_shield=False, from_field_effect=False)
            for effect in battle.field_effects
        )

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self.apply_followup(battle, ctx)

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        if "shield_blocked_damage" in ctx.modifier_receipts or ctx.preserve_followup_effects:
            self.apply_followup(battle, ctx)

    def apply_followup(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is not owner or ctx.is_skill or ctx.from_field_effect or "attack" not in ctx.tags:
            return
        target = ctx.target
        if not target.alive or "florenza_followup" in ctx.modifier_receipts:
            return
        ctx.modifier_receipts.add("florenza_followup")
        if ctx.shield_consumed and ctx.cancelled and target.dodge_charges > 0 and not ctx.cannot_evade:
            target.dodge_charges -= 1
            battle.log_public_event(f"{target.name} 闪避了弗伦萨的普攻附带效果。", source=owner, target=target)
            return
        applied = apply_piercing_status_effect(
            battle, owner, target, action_name="弗伦萨普攻破魔效果",
            status=FlorenzaAttackDebuffStatus(battle.turn_number), is_skill=False,
            tags={"attack", "florenza_followup"}, ignore_targeting_restrictions=True,
            consume_shield=not ctx.shield_consumed,
        )
        if not applied:
            return
        if is_mana_drain_immune(target):
            battle.log(f"{target.name} 无法被吸魔。")
            return
        drained = target.drain_mana(1)
        gained = owner.gain_mana(drained)
        if drained or gained:
            battle.log(f"{owner.name} 的普攻破魔效果从 {target.name} 吸取 {drained} 点魔，获得 {gained} 点魔。")


