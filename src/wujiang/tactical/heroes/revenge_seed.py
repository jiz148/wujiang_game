"""R050 复仇之种: allied losses empower one strike; incoming hurt is shared."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, QueuedAction, Skill, Trait, Unit,
)
from wujiang.tactical.heroes.common import positions_to_dict


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


class RevengeSeedSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("revenge_seed", "复仇", "每本人轮一次；破魔、不可回避。敌方轮每失去一名友方真武将，此技能伤害×2。",
                         max_uses_per_turn=1, target_mode="enemy")
        self.revenge_count = 0

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        if (not _present(owner) or destroyed_unit is owner
                or destroyed_unit.player_id != owner.player_id
                or destroyed_unit.is_summon or destroyed_unit.is_clone
                or battle.active_player == owner.player_id):
            return
        self.revenge_count += 1
        battle.log_public_event(f"{owner.name} 的复仇累积至 {self.revenge_count} 层。", source=owner)

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.source is self.owner and self.code in ctx.tags
                and ctx.raw_damage is not None and ctx.raw_damage > 0):
            ctx.raw_damage *= 2 ** self.revenge_count

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        result = super().to_public_dict(battle)
        result["revenge_count"] = self.revenge_count
        return result

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.position is None:
            return []
        return [unit for unit in battle.all_units()
                if _present(unit) and unit.player_id != actor.player_id
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and any(origin.distance_to(cell) <= actor.targeting_range()
                        and battle.cells_are_straight_aligned(origin, cell)
                        for origin in battle.unit_cells(actor) for cell in battle.unit_cells(unit))]

    def direct_unit_target_range(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range()

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if not targets:
            return False, "当前范内没有可复仇的敌方单位。"
        if payload and payload.get("target_unit_id"):
            if battle.units.get(str(payload["target_unit_id"])) not in targets:
                return False, "复仇目标已不在当前范内。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets
                                             for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True,
                "revenge_count": self.revenge_count}

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def cannot_evade_for_payload(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"ignore_shield": True, "cannot_evade": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return [target] if target in self.targets(battle, actor) else []

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            raise ActionError("复仇目标已失效。")
        battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=actor.stat("attack"),
            is_skill=True, action_name=self.name, ignore_shield=True,
            cannot_evade=True, tags={"skill", self.code},
        ))

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.targets(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return target in self.targets(battle, actor) and not target.magic_immunity

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            return -1000.0
        if target.magic_immunity:
            return -20.0
        damage = battle.damage_rule.calculate_damage(actor.stat("attack"), target.stat("defense"))
        damage = target.damage_fraction_after_limits(damage)
        return damage * (2 ** self.revenge_count) * 65.0 + (10.0 if target.total_shields() else 0.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class RevengeDamageSharingTrait(Trait):
    def __init__(self) -> None:
        super().__init__("复仇分担", "受到其他单位伤害时先减1；余伤同时施加给自身和伤害来源。")

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (ctx.target is not owner or ctx.raw_damage is None or ctx.raw_damage <= 0
                or "revenge_share" in ctx.tags):
            return
        source = ctx.source
        if not _present(source) or source is owner:
            return
        ctx.raw_damage = max(0.0, ctx.raw_damage - 1.0)
        if ctx.raw_damage <= 0:
            return
        battle.queue_skill_effect_action(
            actor=owner, display_name="复仇分担", effect_code="revenge_share",
            target_units=[source], target_cells=battle.unit_cells(source),
            include_cell_units=False, payload={"share_amount": ctx.raw_damage,
                                               "after_death_effect": True,
                                               "share_is_skill": ctx.is_skill},
            effect_resolver=self.resolve_shared_damage,
        )

    @staticmethod
    def resolve_shared_damage(battle: Battle, owner: Unit, queued: QueuedAction) -> None:
        for target_id in queued.target_unit_ids:
            target = battle.units.get(target_id)
            if _present(target):
                battle.resolve_damage(DamageContext(
                    source=owner, target=target, attack_power=0,
                    raw_damage=float(queued.payload["share_amount"]),
                    is_skill=bool(queued.payload["share_is_skill"]),
                    action_name="复仇分担", tags={"revenge_share"},
                ))
