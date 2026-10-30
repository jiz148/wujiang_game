"""R379 Kiku sun slash and post-death attack behavior."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import Battle, DamageContext, HeroUnit, Skill, StatusEffect, Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.mechanics.next_five_effects import (
    apply_piercing_status_effect,
    damage_followup_effect_applies,
)


class PassiveSkillLockStatus(StatusEffect):
    def __init__(self, *, duration: int = 3) -> None:
        super().__init__("被动封锁", "不能使用被动技能。", duration=duration, tick_scope="owner_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and (skill.timing in {"passive", "reaction"} or skill.passive and skill.timing != "instant"):
            return True, "被动封锁状态下不能使用被动技能。"
        return False, ""


class SunSlashSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.sun_slash_score(self)

    def __init__(self) -> None:
        super().__init__("sun_slash", "斩技。阳",
                         "大招：0魔、每场一次；射程直斜双方实际受体，当前攻技能伤害和3个本人回合末的被动技能封锁共破魔。",
                         max_uses_per_battle=1, target_mode="unit")

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range(), action_name=self.name)
        return {}

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.effect_units(battle.all_units())
                   if battle.unit_can_be_selected(unit, actor=actor)[0]
                   and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]
        return {"cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
                "target_unit_ids": [target.unit_id for target in targets], "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [battle.effect_recipient(payload_target_unit(battle, payload))]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        if not payload.get("queued_resolution"):
            self.queued_payload_metadata(battle, actor, payload)
        target_ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
            is_hostile=target.player_id != actor.player_id, ignore_shield=True, resolve_defenses=False,
            damage_target=True, tags={"skill", self.code})
        if target_ctx.cancelled:
            return
        ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"),
            is_skill=True, action_name=self.name, ignore_shield=True, tags={"skill", self.code}))
        target = ctx.target
        if damage_followup_effect_applies(ctx) and target.alive:
            if apply_piercing_status_effect(battle, actor, target, action_name=self.name,
                    status=PassiveSkillLockStatus(), is_skill=True, tags={"skill", self.code},
                    consume_shield=not ctx.shield_consumed, cannot_evade=False):
                battle.log_public_event(f"{target.name} 被【斩技。阳】封锁被动技能。", source=actor, target=target)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class KikuLegacyStatus(StatusEffect):
    applies_after_basic_attack_shape = True

    def __init__(self) -> None:
        super().__init__("菊之遗击", "每个己方进攻英雄回合一次独立普攻，基准伤4，不占普通攻击次数。", duration=None)
        self.is_skill_effect = False
        self._turn_number: int | None = None
        self._legacy_attack_used = False

    def _sync(self, battle: Battle) -> None:
        if self._turn_number != battle.turn_number:
            self._turn_number = battle.turn_number
            self._legacy_attack_used = False

    def basic_attack_action_entries(self, battle: Battle, actor: HeroUnit) -> list[dict[str, Any]]:
        self._sync(battle)
        return [{"code": "kiku_legacy_attack", "name": "菊之遗击",
                 "description": "独立额外普攻：基准伤4，不占普通攻击次数。",
                 "attack_payload": {"attack_variant": "kiku_legacy", "attack_name": "菊之遗击"}}]

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if (payload or {}).get("attack_variant") != "kiku_legacy":
            return {}
        return {"attack_power_override": 4, "attack_cost": 0, "attack_name": "菊之遗击",
                "attack_note": "独立额外普攻，基准伤害值4；不占普通攻击次数。"}

    def can_use_basic_attack(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[bool, str]:
        self._sync(battle)
        if payload.get("attack_variant") == "kiku_legacy" and self._legacy_attack_used:
            return False, "本回合已经使用过【菊之遗击】。"
        return True, ""

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        self._sync(battle)
        if payload.get("attack_variant") == "kiku_legacy" and not payload.get("reaction_attack"):
            self._legacy_attack_used = True

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None and not self.owner.alive:
            self.owner.remove_status(self, battle)


class KikuAfterDeathTrait(Trait):
    def __init__(self) -> None:
        super().__init__("妖仙遗志", "真实被破坏后，只给当时在场的其他己方单位永久同名不叠的伤4额外普攻。")
        self._last_granted_destruction = -1

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.alive or not isinstance(owner, HeroUnit) or owner.is_clone or owner.is_summon or is_army_soldier(owner):
            return
        event = getattr(owner, "destruction_count", 0)
        if event <= self._last_granted_destruction:
            return
        self._last_granted_destruction = event
        for ally in battle.player_units(owner.player_id):
            if (ally.unit_id == owner.unit_id or not ally.alive or ally.banished or ally.position is None
                    or ally.has_status("菊之遗击") or ally.direct_effects_blocked()):
                continue
            status = KikuLegacyStatus()
            ally.add_status(status, source=owner)
            if status in ally.statuses:
                battle.log_public_event(f"{ally.name} 获得【菊之遗击】。", source=owner, target=ally)


