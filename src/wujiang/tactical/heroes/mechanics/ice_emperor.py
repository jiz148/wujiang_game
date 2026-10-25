"""R263 ice emperor zero and crane city."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.common import BlockCounterTrait
from wujiang.tactical.heroes.common import DashMoveSkill
from wujiang.tactical.heroes.common import FlyingTrait
from wujiang.tactical.heroes.common import MagicImmunityStatus
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.mechanics.foundation import PermanentMagicImmunityTrait
from wujiang.tactical.heroes.mechanics.racial_weather import SimpleGlobalWeatherEffect
from wujiang.tactical.heroes.next_five import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.next_five import remote_rectangle_patterns
from wujiang.tactical.prediction import random


class AbsoluteZeroStatus(StatusEffect):
    prevents_movement = True
    prevents_forced_movement = True
    suppresses_innate_traits = True
    movement_skill_codes = frozenset({"leap", "fly_leap", "chain_pull", "evasion", "backstep_shot",
        "mounted_leap", "zero_dash", "fuma_pursuit", "ghost_step", "shield_charge", "hell_teleport",
        "bird_dash", "bird_dash_free", "bird_soul", "bird_soul_free", "flash_slash", "crazy_sand",
        "plasma_thruster", "true_blade_air_slash", "frey_quick_flash", "iron_chain_path"})

    def __init__(self) -> None:
        super().__init__("绝对零度", "5轮不能移动、使用位移技能或锁链，固有特性暂停。",
                         duration=5, tick_scope="owner_turn_end")

    def bind(self, owner: Unit) -> "AbsoluteZeroStatus":
        super().bind(owner)
        if any(isinstance(trait, FlyingTrait) for trait in owner.traits):
            owner.has_flying = any(isinstance(component, FlyingTrait)
                                   for status in owner.statuses for component in status.provided_components())
        if any(isinstance(trait, BlockCounterTrait) for trait in owner.traits):
            owner.has_block_counter = any(isinstance(component, BlockCounterTrait)
                                          for status in owner.statuses for component in status.provided_components())
        if any(isinstance(trait, PermanentMagicImmunityTrait) for trait in owner.traits):
            owner.magic_immunity = any(isinstance(status, MagicImmunityStatus)
                                       or "magic_immunity" in getattr(status, "provided_flags", ())
                                       for status in owner.statuses)
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.innate_traits_suppressed():
            return
        if any(isinstance(trait, FlyingTrait) for trait in owner.traits):
            owner.has_flying = True
        if any(isinstance(trait, BlockCounterTrait) for trait in owner.traits):
            owner.has_block_counter = True
        if any(isinstance(trait, PermanentMagicImmunityTrait) for trait in owner.traits):
            owner.magic_immunity = True

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and (skill.code in self.movement_skill_codes or isinstance(skill, DashMoveSkill)):
            return True, "绝对零度使该单位不能使用位移技能或锁链。"
        return False, ""


class AbsoluteZeroSkill(DeclaredAreaSkillMixin, Skill):
    def __init__(self) -> None:
        super().__init__("absolute_zero", "绝对零度", "每本人轮一次；远程2×2无伤冻结双方受体5轮，封移动、位移技能、锁链及固有特性。",
                         max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 2, 2)

    def chosen_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") or match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload)):
            result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                is_hostile=target.player_id != actor.player_id, record_effect_on_validation=False,
                tags={"skill", self.code, "control"})
            recipient = result.target
            if result.cancelled or recipient.skill_non_damage_effects_blocked():
                battle.log_public_event(result.reason or f"{recipient.name} 未受绝对零度影响。", source=actor, target=recipient)
                continue
            existing = recipient.get_status("绝对零度")
            if existing is not None:
                recipient.remove_status(existing, battle)
            recipient.add_status(AbsoluteZeroStatus(), source=actor)
            battle.record_skill_effect(recipient)
            battle.log_public_event(f"{recipient.name} 被绝对零度冻结5轮。", source=actor, target=recipient)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))


class FloatingCraneCityField(SimpleGlobalWeatherEffect):
    weather_name = "浮鹤城"
    observes_immune_actions = True

    def __init__(self) -> None:
        super().__init__(self.weather_name, marker="鹤")
        self.description = "全场除同名冰帝外范-3到1；主动技能正式声明后独立判定1/2失败，失败仍耗资源。"

    @staticmethod
    def exempt(unit: Unit) -> bool:
        return unit.name.startswith("冰帝。浮鹤")

    def modify_unit_stat(self, battle: Battle, unit: Unit, stat_name: str, value: float) -> float:
        if stat_name == "attack_range" and not self.exempt(unit) and battle.weather_effect_applies(self, unit):
            return max(1.0, value - 3.0)
        return value

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        actor = battle.units.get(str(payload.get("unit_id") or ""))
        if (actor is None or action_type != "skill" or self.exempt(actor)
                or not battle.weather_effect_applies(self, actor)
                or getattr(battle, "_ai_probe_active", False) or payload.get("floating_crane_checked")):
            return
        try:
            skill = actor.get_skill(str(payload.get("skill_code") or ""))
        except ActionError:
            return
        if skill.timing != "active":
            return
        failed = random.random() < 0.5
        payload["floating_crane_checked"] = True
        payload["action_failed_by_floating_crane"] = failed
        battle.log_public_event(f"{actor.name} 的【{skill.name}】受浮鹤城影响，判定{'失败' if failed else '成功'}。", source=actor)


class FloatingCraneCitySkill(Skill):
    def __init__(self) -> None:
        super().__init__("floating_crane_city", "浮鹤城", "大招：一场一次；全场非同名单位范-3至1，主动技能一半概率失败。",
                         max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(FloatingCraneCityField(), source=actor)


class IceEmperorPierceGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("冰帝破魔减伤", "受到破魔伤害时伤害威力-3，最低1。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and (ctx.ignore_shield or ctx.half_ignore_shield):
            if ctx.raw_damage is None:
                ctx.attack_power = max(1.0, ctx.attack_power - 3.0)
            else:
                ctx.raw_damage = max(1.0, ctx.raw_damage - 3.0)


