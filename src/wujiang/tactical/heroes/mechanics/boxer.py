"""R206 ring taunt and block-counter skill."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HeroUnit, Position, Skill,
    TemporaryDefenseStatus,
)
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.cat_taunt import (
    RequiredAttackStatus, replace_required_attack_status,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells

class BoxerTauntStatus(RequiredAttackStatus):
    def __init__(self, boxer_unit_id: str) -> None:
        super().__init__(
            "擂台嘲讽",
            "下个自己的回合中，攻击反击拳手前只能移动。",
            boxer_unit_id,
        )

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.cannot_attack or owner.attacks_used >= owner.attack_actions_per_turn():
            self.requirement_active = False
            self.requirement_satisfied = True
            return
        super().on_owner_turn_start(battle)


class RingTauntSkill(Skill):
    excludes_caster_from_effect = True

    def __init__(self) -> None:
        super().__init__(
            "ring_taunt",
            "擂台嘲讽",
            "普通技能：每回合一次；对周围单位造成当前攻伤害；被击中单位下回合有攻击数时必须先攻击反击拳手；破魔。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=1)

    def affected_units(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.affected_cells(battle, actor), ignore=actor)  # type: ignore[return-value]

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") or self.affected_cells(battle, actor)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.affected_cells(battle, actor))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in list(battle.effect_units_at_cells(cells, ignore=actor)):
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    ignore_shield=True,
                    tags={"skill", "attack", "control", "pierce", self.code},
                )
            )
            if ctx.cancelled or not target.alive:
                continue
            replace_required_attack_status(battle, target, BoxerTauntStatus(actor.unit_id))
            battle.log(f"{target.name} 被 {actor.name} 的【擂台嘲讽】锁定。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.affected_units(battle, actor)
        cells = self.affected_cells(battle, actor)
        return {
            "cells": positions_to_dict(cells),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": False,
        }

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload), ignore=actor)  # type: ignore[return-value]

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class BoxerBlockCounterSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__(
            "boxer_block_counter",
            "同时格挡反击",
            "被动技能：受到敌方速度1动作影响时，守+1并立即对范围内来源反击。",
            timing="passive",
            target_mode="self",
        )

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        battle, actor = context.battle, context.actor
        attacker = battle.get_unit(context.queued_action.actor_id)
        threat = context.threat(context.proxy_target())
        expected = context.damage(attacker, battle.basic_attack_preview_power(actor))
        prevention = context.temporary_defense_prevention()
        missing_hp = max(0.0, actor.max_health - actor.current_hp)
        lifesteal_value = min(0.25, missing_hp) * 100.0 if expected > 0 else 0.0
        score = threat + prevention + expected * 95.0 + lifesteal_value + 36.0
        if expected >= attacker.current_hp - 1e-9:
            score += 90.0
        score -= context.retaliation_penalty([attacker])
        return score

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id or queued_action.speed >= 2:
            return False, "只能连锁敌方速度 1 动作。"
        source = battle.units.get(queued_action.actor_id)
        if source is None or not battle.attack_target_allowed(actor, source)[0]:
            return False, "来源不在反击范围内。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        source = battle.units.get(queued_action.actor_id)
        if source is None:
            return
        actor.add_status(
            TemporaryDefenseStatus(
                "同时格挡反击",
                defense_delta=1,
                description="本次连锁伤害结算时守 +1。",
                expire_with_chain=True,
            )
        )
        if battle.attack_target_allowed(actor, source)[0]:
            battle.resolve_attack_damage(actor, source, action_name=self.name, tags={"counter", self.code})
        battle.log(f"{actor.name} 同时格挡并反击 {source.name}。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("同时格挡反击只能通过连锁使用。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        source = battle.units.get(queued_action.actor_id)
        return {
            "cells": positions_to_dict(battle.unit_cells(source)) if source is not None else [],
            "target_unit_ids": [source.unit_id] if source is not None else [],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": False,
        }


