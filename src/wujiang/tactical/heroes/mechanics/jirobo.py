"""Jirobo follow-step, devour, and undying traits."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import ActionMiss
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies


class JiroboAfterAttackStatus(StatModifierStatus):
    def __init__(self, applied_turn_number: int | None = None) -> None:
        super().__init__(
            "次郎坊攻击后守备",
            defense_delta=1,
            description="本次普攻后到下回合结束前守 +1；当前回合可使用一次百鸟葬追步移动至多 2 格。",
            duration=1,
            tick_scope="owner_turn_end",
        )
        self.follow_step_available = True
        self.applied_turn_number = applied_turn_number
        self.follow_skill = None

    def bind(self, owner: HeroUnit) -> "JiroboAfterAttackStatus":
        super().bind(owner)
        if not any(skill.code == "jirobo_follow_step" for skill in owner.skills):
            definitions = owner.__dict__.setdefault("_triggered_skill_definitions", {})
            if "jirobo_follow_step" not in definitions:
                definitions["jirobo_follow_step"] = JiroboFollowStepSkill().bind(owner)
            self.follow_skill = definitions["jirobo_follow_step"]
        return self

    def provided_components(self):
        return (self.follow_skill,) if self.follow_step_available and self.follow_skill is not None else ()

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        self.follow_step_available = False
        super().on_any_turn_end(battle, ended_player_id)

    def on_owner_turn_end(self, battle: Battle) -> None:
        self.follow_step_available = False
        if battle.turn_number != self.applied_turn_number:
            super().on_owner_turn_end(battle)


class JiroboAfterAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击后追步守备", "每次普攻后，直到下回合结束前守 +1，并可在当前回合移动至多 2 格。")

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if (owner is None or actor.unit_id != owner.unit_id or not owner.alive or owner.banished
                or owner.is_clone or payload.get("basic_attack_cancelled")):
            return
        owner.add_status(JiroboAfterAttackStatus(battle.turn_number))
        battle.log(f"{owner.name} 本次普攻后获得一份追步守备。")


class JiroboFollowStepSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "jirobo_follow_step",
            "百鸟葬追步",
            "特性触发后的可选移动：攻击后当回合可移动至多 2 格。",
            target_mode="cell",
        )

    def _status(self, actor: HeroUnit) -> JiroboAfterAttackStatus | None:
        return next((status for status in actor.statuses
                     if isinstance(status, JiroboAfterAttackStatus) and status.follow_step_available), None)

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move:
            return False, "当前无法移动，不能追步。"
        status = self._status(actor)
        if status is None or not status.follow_step_available:
            return False, "需要先完成一次普攻后才能追步。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        status = self._status(actor)
        if status is None or not status.follow_step_available:
            if payload.get("queued_resolution"):
                raise ActionMiss("追步机会已经失效，已声明的追步未能移动。")
            raise ActionError("需要先完成一次普攻后才能追步。")
        destination = payload_position(payload)
        battle.move_unit(actor, destination, via_skill=True, max_distance=2, tags={"movement", "jirobo_follow_step"})
        status.follow_step_available = False

    def finalize_use(self, battle: Battle, actor: HeroUnit) -> None:
        return None

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        status = self._status(actor)
        if actor.cannot_move or status is None or not status.follow_step_available:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        cells = battle.reachable_positions(actor, max_distance=2)
        return {
            "cells": [cell.to_dict() for cell in cells],
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
        }


class DevourSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r17_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "undead_boy_devour",
            "吞噬",
            "普通技能：2轮一次，破魔；双方实际受体当前生命减半，附效可结算时自身当前生命翻倍，封顶且禁疗阻止。",
            cooldown_turns=2,
            target_mode="unit",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.all_units()
            if unit.alive
            and unit.position is not None
            and not unit.banished
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        return {
            "cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
            "target_unit_ids": [target.unit_id for target in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        target = battle.effect_recipient(target)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=0,
                raw_damage=round(target.current_hp / 2, 4),
                is_skill=True,
                action_name="吞噬",
                ignore_shield=True,
                tags={"skill", "devour"},
            )
        )
        if damage_followup_effect_applies(ctx) and not target.magic_immunity and actor.alive and not actor.banished and not actor.cannot_heal:
            actor.heal_fraction(actor.current_hp)
            battle.log(f"{actor.name} 的【吞噬】将当前生命翻倍至 {actor.current_hp:g}。")

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class UndyingQuarterTrait(Trait):
    def __init__(self) -> None:
        super().__init__("不死保留", "半血以上受到致命伤害时，每个伤害实例可保留 1/4 血留场。")
    def lethal_damage_remaining_hp(self, battle: Battle, ctx: DamageContext) -> float | None:
        owner = self.owner
        if (owner is None or ctx.target is not owner or owner.is_clone or not owner.alive
                or owner.current_hp + 1e-9 < owner.max_health / 2):
            return None
        retained = owner.max_health / 4
        battle.log_public_event(f"{owner.name} 触发【不死保留】，以 {retained:g} 点生命留在场上。", source=ctx.source, target=owner)
        return retained


