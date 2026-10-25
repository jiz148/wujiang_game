"""R138 nameless messenger skills and traits."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import PierceSkill
from wujiang.tactical.heroes.next_five import DragonBreathSkill


class MessengerPierceSkill(PierceSkill):
    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class MessengerDragonBreathSkill(DragonBreathSkill):
    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class MessengerReincarnationSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "messenger_reincarnation",
            "无极落霞轮回",
            "普通技能：仅可在当前魔为 0 且至少持有 2 魔力点时使用；消耗 2 魔力点，当前魔变满。",
            target_mode="self",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.current_mana > 1e-9:
            return False, "只有当前魔为 0 时才能使用无极落霞轮回。"
        if actor.mana_points + 1e-9 < 2:
            return False, "无极落霞轮回需要 2 魔力点。"
        return True, ""

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"messenger_point_cost_paid": False}

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> None:
        if actor.mana_points + 1e-9 < 2:
            raise ActionError("无极落霞轮回需要2魔力点。")
        super().prepay_resources(battle, actor, payload)
        spent = actor.spend_mana_points(2)
        if payload is not None:
            payload["messenger_point_cost_paid"] = True
        battle.log(f"{actor.name} 为【无极落霞轮回】支付 {spent:g} 魔力点。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not payload.get("messenger_point_cost_paid"):
            # Direct execution is also used by copied skills and focused scenarios.
            if actor.mana_points + 1e-9 < 2:
                raise ActionError("无极落霞轮回需要2魔力点。")
            previous = getattr(actor, "_paying_skill_cost", False)
            actor._paying_skill_cost = True
            try:
                actor.spend_mana_points(2)
            finally:
                actor._paying_skill_cost = previous
        gained = actor.gain_mana(max(0.0, actor.max_mana() - actor.current_mana))
        spent = 2
        battle.log(f"{actor.name} 使用【无极落霞轮回】，消耗 {spent} 魔力点并回复 {gained} 点魔。")


class MessengerCreationSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "messenger_creation",
            "造化",
            "普通技能：每回合最多 1 次，不费魔；魔力点 +1。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        gained = actor.gain_mana_points(1)
        battle.log(f"{actor.name} 使用【造化】，获得 {gained} 魔力点。")


class MessengerDamageSkillManaTrait(Trait):
    damaging_skill_codes = {"dragon_breath", "pierce"}

    def __init__(self) -> None:
        super().__init__("伤害技能魔力点", "入场魔力点等于基础魔；每次使用伤害技能后魔力点 +1。")

    def bind(self, owner: HeroUnit) -> "MessengerDamageSkillManaTrait":
        super().bind(owner)
        owner.mana_points = float(owner.base_stats.mana)
        return self

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type != "skill":
            return
        if str(payload.get("skill_code") or "") not in self.damaging_skill_codes:
            return
        gained = owner.gain_mana_points(1)
        if gained:
            battle.log(f"{owner.name} 使用伤害技能，获得 {gained} 魔力点。")


class MessengerSkillDamagePierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("技能伤害破魔", "自身造成的技能伤害破魔；非伤害附带效果不因此获得破魔。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is not None and ctx.source is not None and ctx.source.unit_id == owner.unit_id and ctx.is_skill and not ctx.from_field_effect:
            ctx.ignore_shield = True


