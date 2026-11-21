"""R132: temporary skill exchange and an atomic exchange of control."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.common import payload_target_unit, positions_to_dict
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


class FriendlyDamageImmunity(Trait):
    def __init__(self) -> None:
        super().__init__("己方伤害免疫", "不受到当前己方来源造成的伤害。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target is not owner or ctx.source is None or ctx.cancelled:
            return
        if ctx.source.player_id != owner.player_id:
            return
        ctx.cancelled = True
        ctx.preserve_followup_effects = True
        ctx.reason = f"{owner.name} 不受到己方伤害。"
        battle.log_public_event(ctx.reason, source=ctx.source, target=owner)


class ExchangedSkills(StatusEffect):
    def __init__(self, destroyed: Unit) -> None:
        super().__init__("光环换技", "与已破坏单位交换技能，持续三个本人进攻回合。",
                         duration=3, tick_scope="owner_turn_end")
        self.destroyed = destroyed
        self.original: list[Skill] = []
        self.borrowed: list[Skill] = []
        self._restored = False

    def bind(self, owner: Unit) -> ExchangedSkills:
        super().bind(owner)
        self.original, self.borrowed = list(owner.skills), list(self.destroyed.skills)
        owner.skills, self.destroyed.skills = self.borrowed, self.original
        for skill in owner.skills:
            skill.bind(owner)
        for skill in self.destroyed.skills:
            skill.bind(self.destroyed)
        return self

    def _restore(self) -> None:
        if self._restored or self.owner is None:
            return
        owner = self.owner
        owner_extras = [skill for skill in owner.skills if skill not in self.borrowed]
        destroyed_extras = [skill for skill in self.destroyed.skills if skill not in self.original]
        owner.skills = [*self.original, *owner_extras]
        self.destroyed.skills = [*self.borrowed, *destroyed_extras]
        for skill in owner.skills:
            skill.bind(owner)
        for skill in self.destroyed.skills:
            skill.bind(self.destroyed)
        self._restored = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        for skill in self.original:
            if skill.cooldown_remaining > 0:
                skill.cooldown_remaining -= 1
        super().on_owner_turn_end(battle)

    def on_removed(self, battle: Battle) -> None:
        self._restore()

    def on_owner_removed(self, battle: Battle) -> None:
        self._restore()

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["exchanged_with_unit_id"] = self.destroyed.unit_id
        return data


class DevilAuraSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("devil_aura", "光环",
                         "每3本人轮一次；与一个已破坏单位交换技能三个本人进攻回合。",
                         cooldown_turns=3, target_mode="none")

    @staticmethod
    def candidates(battle: Battle) -> list[Unit]:
        return [unit for unit in battle.destroyed_units if unit not in battle.units.values()]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.has_status("光环换技"):
            return False, "已有光环换技尚未结束。"
        options = {unit.unit_id for unit in self.candidates(battle)}
        if not options:
            return False, "场上尚无已破坏单位。"
        if payload is not None and "option_code" in payload and payload["option_code"] not in options:
            return False, "请选择已破坏单位。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "option", "requires_unit": False,
                              "prompt": "选择一名已破坏单位交换技能。",
                              "options": [{"code": unit.unit_id, "label": unit.name}
                                          for unit in self.candidates(battle)]}}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "option_code": unit.unit_id} for unit in self.candidates(battle)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = next((unit for unit in self.candidates(battle)
                       if unit.unit_id == payload.get("option_code")), None)
        if target is None:
            return -1000.0
        def skill_value(skill: Skill) -> float:
            if not skill.is_active or skill.cooldown_remaining > 1:
                return 0.0
            if skill.mana_cost > actor.current_mana:
                return 0.0
            return 22.0 + (5.0 if skill.mana_cost == 0 else 0.0)
        old = sum(skill_value(skill) for skill in actor.skills)
        new = sum(skill_value(skill) for skill in target.skills)
        return (new - old) * 1.4 - 10.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = next((unit for unit in self.candidates(battle)
                       if unit.unit_id == payload.get("option_code")), None)
        if target is None:
            raise ActionError("光环的已破坏目标已失效。")
        actor.add_status(ExchangedSkills(target), source=actor)
        battle.log_public_event(f"{actor.name} 与已破坏的 {target.name} 交换技能三个本人回合。",
                                source=actor, target=target)


class BodySwapSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("body_swap", "换身",
                         "每2本人轮一次；范内单体无伤换控制者；破魔、不能回避、无视魔免。",
                         cooldown_turns=2, target_mode="unit")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def cannot_evade_for_payload(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any]) -> bool:
        return True

    def ignores_magic_immunity_for_payload(self, battle: Battle, actor: Unit,
                                           payload: dict[str, Any]) -> bool:
        return True

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if unit is not actor and _present(unit)
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.targets(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return _present(target) and target.player_id != actor.player_id

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if not _present(target) or target.player_id == actor.player_id:
            return -1000.0
        def value(unit: Unit) -> float:
            return (65.0 if not unit.is_summon else 16.0) + unit.current_hp * 45.0 + \
                unit.stat("attack") * 8.0 + unit.stat("defense") * 5.0 + \
                unit.stat("speed") * 4.0 + unit.current_mana * 3.0
        return (value(target) - value(actor)) * 1.25 - 20.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target is actor or target not in self.targets(battle, actor):
            raise ActionError("换身目标不在合法范围。")
        recipient = piercing_nondamage_recipient(
            battle, actor, target, action_name=self.name, code=self.code,
            cannot_evade=True, ignore_magic_immunity=True,
        )
        if recipient is None:
            return
        battle.exchange_unit_control(actor, recipient)
        battle.record_skill_effect(recipient)
        battle.log_public_event(f"{actor.name} 与 {recipient.name} 交换了控制权，原回合槽位不变。",
                                source=actor, target=recipient)
