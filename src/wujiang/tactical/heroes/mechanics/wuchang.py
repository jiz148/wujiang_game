"""R027 Wuchang mist, migratory mark, and attack seal."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.next_five import damage_followup_effect_applies
from wujiang.tactical.prediction import random


class WuchangMistImmunityStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("侯鸟标记", "不会被无常之雾影响。", duration=2, tick_scope="owner_turn_end")


class WuchangMistField(BattleFieldEffect):
    weather_name = "无常之雾"
    global_weather = True

    def __init__(self, source_unit_id: str) -> None:
        self.source_unit_id = source_unit_id
        super().__init__("无常之雾", "除无常、侯鸟标记保护及效果免疫单位外，正式声明普攻或主动技能有1/2概率失败；失败仍消耗费用、次数和冷却。", duration=None)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        return any(getattr(effect, "weather_name", None) == self.weather_name for effect in existing_effects)

    @staticmethod
    def affects_actor(actor: HeroUnit) -> bool:
        return getattr(actor, "hero_code", "") != "excel_r027" and not actor.has_status("侯鸟标记") and not actor.direct_effects_blocked()

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        actor = battle.units.get(str(payload.get("unit_id") or ""))
        if actor is None or not self.affects_actor(actor) or getattr(battle, "_ai_probe_active", False):
            return
        if payload.get("wuchang_mist_checked"):
            return
        name = "普攻"
        if action_type == "skill":
            try:
                skill = actor.get_skill(str(payload.get("skill_code") or ""))
                if skill.code in {"mimic_skill", "agency_borrowed_skill"}:
                    skill = skill.declared_skill(actor, payload)
            except (ActionError, KeyError):
                return
            if skill.timing != "active":
                return
            name = skill.name
        elif action_type != "attack":
            return
        failed = random.random() < 0.5
        payload["wuchang_mist_checked"] = True
        payload["action_failed_by_wuchang_mist"] = failed
        result = "失败" if failed else "成功"
        battle.log_public_event(f"{actor.name} 的【{name}】受到【无常之雾】影响，判定{result}。", source=actor)

    def board_marker(self, battle: Battle) -> str:
        return "雾"


class WuchangMistSkill(Skill):
    def __init__(self) -> None:
        super().__init__("wuchang_mist", "无常之雾", "大招：永久加入天气无常之雾，同名不叠加；除无常及免雾单位外，每次普攻或主动技能公开判定1/2失败；失败照常消耗。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(WuchangMistField(actor.unit_id))


class MigratoryBirdMarkSkill(Skill):
    def __init__(self) -> None:
        super().__init__("migratory_bird_mark", "侯鸟标记", "普通技能：2轮一次；范内直线选择其他友方或敌方单位，按攻造成技能伤害，不破魔；命中后目标2个己方回合内不受无常之雾影响。", cooldown_turns=2, target_mode="unit")

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [unit for unit in battle.all_units()
                if unit is not actor and unit.alive and unit.position is not None and not unit.banished
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.targets(battle, actor)}:
                return False, "需选择范内直线可见的其他单位。"
        return True, ""

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        if str(payload.get("target_unit_id") or "") not in {unit.unit_id for unit in self.targets(battle, actor)}:
            raise ActionError("需选择范内直线可见的其他单位。")
        return {}

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        if not payload.get("queued_resolution") and target not in self.targets(battle, actor):
            raise ActionError("需选择范内直线可见的其他单位。")
        ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"),
                                                  is_skill=True, action_name=self.name, tags={"skill", self.code}))
        if target.alive and damage_followup_effect_applies(ctx):
            existing = [status for status in target.statuses if status.name == "侯鸟标记"]
            added = WuchangMistImmunityStatus()
            target.add_status(added, source=actor)
            if added in target.statuses:
                for status in existing:
                    target.remove_status(status, battle)


class WuchangAttackSealStatus(StatusEffect):
    provided_flags = frozenset({"cannot_attack", "cannot_use_skills"})

    def __init__(self) -> None:
        super().__init__("无常普攻封锁", "不能普攻或使用技能。", duration=2, tick_scope="owner_turn_end")

    def bind(self, owner: HeroUnit) -> "WuchangAttackSealStatus":
        super().bind(owner)
        for flag in self.provided_flags:
            setattr(owner, flag, True)
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for flag in self.provided_flags:
            remaining = any(
                flag in getattr(component, "provided_flags", ())
                or (getattr(component, "flag_name", "") in {flag, "cannot_act"}
                    and getattr(component, "value", True) and getattr(component, "flag_active", True))
                for component in owner.iter_components()
            )
            setattr(owner, flag, owner.is_clone or remaining)


class WuchangAttackSealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("无常普攻封锁", "普攻实际造成生命损失后，目标不能普攻或使用技能2个己方回合；再次命中刷新。")

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if self.owner is None or actor.unit_id != self.owner.unit_id:
            return
        seen: set[str] = set()
        for ctx in damage_contexts:
            target = ctx.target
            if (ctx.source is not actor or ctx.is_skill or ctx.cancelled or ctx.actual_damage <= 0
                    or not target.alive or target.unit_id in seen):
                continue
            seen.add(target.unit_id)
            existing = [status for status in target.statuses if status.name == "无常普攻封锁"]
            added = WuchangAttackSealStatus()
            target.add_status(added, source=actor)
            if added in target.statuses:
                for status in existing:
                    target.remove_status(status, battle)


