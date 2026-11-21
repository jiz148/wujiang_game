"""R096 雪女: her declared blizzard and frost defense loss."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, QueuedAction, Skill, StatusEffect, Unit, direct_scoring
from wujiang.tactical.heroes.ai_candidate_plan import SkillCandidatePlan
from wujiang.tactical.heroes.common import MagicWallSkill, match_payload_pattern, payload_target_unit, positions_to_dict
from wujiang.tactical.heroes.mechanics.area_helpers import area_patterns_preview, combined_remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies


class IceWallSkill(MagicWallSkill):
    """冰墙 uses the same multi-ally chain protection as 魔墙."""

    def __init__(self) -> None:
        super().__init__()
        self.code = "ice_wall"
        self.name = "冰墙"
        self.description = "被动连锁；每名受影响友军费1魔并获得本连锁临时1盾。"

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("冰墙只能通过连锁使用。")


class IceThornsStatus(StatusEffect):
    def __init__(self, source_unit_id: str, layers: int = 1) -> None:
        super().__init__("菱撒", "敌方攻击或技能声明影响本单位时反伤；再次施放提高伤害，最高5。")
        self.source_unit_id = source_unit_id
        self.layers = max(1, min(3, layers))
        self._declared_ids: set[str] = set()

    @property
    def retaliatory_damage(self) -> int:
        return 2 + self.layers

    def on_target_action_declared(self, battle: Battle, actor: Unit,
                                  action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if (owner is None or not owner.alive or not actor.alive
                or actor.player_id == owner.player_id or action_type not in {"attack", "skill"}):
            return
        declaration = str(payload.get("declaration_id", id(payload)))
        if declaration in self._declared_ids:
            return
        self._declared_ids.add(declaration)
        battle.queue_skill_effect_action(
            actor=owner, display_name="菱撒反制", effect_code="ice_thorns_retaliation",
            target_units=[actor], target_cells=battle.unit_cells(actor),
            include_cell_units=False, effect_resolver=self._retaliate,
        )

    def _retaliate(self, battle: Battle, protected: Unit, queued: QueuedAction) -> None:
        source = battle.units.get(self.source_unit_id) or protected
        for target_id in queued.target_unit_ids:
            target = battle.units.get(target_id)
            if target is not None and target.alive:
                battle.resolve_damage(DamageContext(
                    source=source, target=target, attack_power=0,
                    raw_damage=self.retaliatory_damage, is_skill=True,
                    action_name="菱撒反制", tags={"skill", "ice_thorns_retaliation"},
                ))

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(layers=self.layers, retaliatory_damage=self.retaliatory_damage)
        return data


class IceThornsSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("ice_thorns", "菱撒",
                         "费1.5魔；每本人轮一次，只能对本队单位使用；同一受体重复施放使反制伤害3→4→5。",
                         mana_cost=1.5, max_uses_per_turn=1, target_mode="ally")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit.alive and not unit.banished and unit.position is not None
                and unit.player_id == actor.player_id
                and battle.distance_between_units(actor, unit) <= actor.targeting_range()
                and not (isinstance(unit.get_status("菱撒"), IceThornsStatus)
                         and unit.get_status("菱撒").layers >= 3)]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([unit.position for unit in targets]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.targets(battle, actor):
            return False, "没有能受菱撒保护或提升的己方单位。"
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.targets(battle, actor)}:
                return False, "菱撒目标不合法或已达最高反制伤害。"
        return True, ""

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        target = payload_target_unit(battle, payload)
        return [target] if target in self.targets(battle, actor) else []

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.targets(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target not in self.targets(battle, actor):
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and unit.position is not None]
        if not enemies:
            return -1000.0
        distance = min(battle.distance_between_units(target, enemy) for enemy in enemies)
        previous = target.get_status("菱撒")
        improvement = 1 if isinstance(previous, IceThornsStatus) else 3
        return (26.0 + improvement * 15.0 + max(0, 4 - distance) * 9.0
                if distance <= target.targeting_range() + 3 else -8.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            battle.log_public_event("菱撒目标已不合法。", source=actor)
            return
        ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True, is_hostile=False)
        if ctx.cancelled:
            battle.log_public_event(ctx.reason or "菱撒未生效。", source=actor, target=target)
            return
        target = ctx.target
        existing = target.get_status("菱撒")
        if isinstance(existing, IceThornsStatus):
            existing.layers = min(3, existing.layers + 1)
            existing.source_unit_id = actor.unit_id
        else:
            existing = IceThornsStatus(actor.unit_id)
            target.add_status(existing, source=actor)
        battle.record_skill_effect(target)
        battle.log_public_event(f"{target.name} 的菱撒反制伤害升至{existing.retaliatory_damage}。",
                                source=actor, target=target)


class BlizzardDefenseLoss(StatusEffect):
    def __init__(self) -> None:
        super().__init__("吹雪削守", "守-2，最低1；源文未规定自然期限。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 2.0) if stat_name == "defense" else value


class BlizzardSkill(Skill):
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=16)
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("blizzard_r096", "吹雪",
                         "费2魔；选择2×6区域造成破魔技能伤；每名命中者有1/2概率守-2，最低1。",
                         mana_cost=2, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return combined_remote_rectangle_patterns(battle, actor, [(2, 6), (6, 2)])

    def cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        declared = battle.payload_positions(payload, "declared_blizzard_cells")
        return declared or match_payload_pattern(payload, self.patterns(battle, actor))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.cells(battle, actor, payload)))

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_blizzard_cells": positions_to_dict(self.cells(battle, actor, payload))}

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_prefilter_score(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                           shared: Any) -> float:
        try:
            cells = self.cells(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        score = 0.0
        for target in battle.effect_units_at_cells(cells):
            hits = battle.unit_hit_count_for_cells(target, cells)
            power = actor.stat("attack") + max(0, hits - 1)
            damage = battle.damage_rule.calculate_damage(power, target.stat("defense"))
            value = min(target.current_hp, damage) * 100.0
            if target.stat("defense") > 1 and not target.skill_non_damage_effects_blocked():
                value += 18.0 * min(2.0, target.stat("defense") - 1)
            score += value if target.player_id != actor.player_id else -1.5 * value
        return score - 12.0 if score > 0 else -1000.0

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.direct_expectation import damage_result
        from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

        try:
            cells = self.cells(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        score = 0.0
        for target in battle.effect_units_at_cells(cells):
            with ai_probe_rollback(battle):
                probe_actor = battle.get_unit(actor.unit_id)
                probe_target = battle.get_unit(target.unit_id)
                defense = probe_target.stat("defense")
                hp_before = probe_target.current_hp
                ctx = battle.resolve_damage(DamageContext(
                    source=probe_actor, target=probe_target, attack_power=probe_actor.stat("attack"),
                    is_skill=True, action_name=self.name, ignore_shield=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(probe_target, cells),
                    tags={"skill", self.code},
                ))
                damage = min(hp_before, damage_result(ctx, ctx.actual_damage))
                can_reduce_defense = (probe_target.alive and damage_followup_effect_applies(ctx)
                                      and defense > 1 and not probe_target.skill_non_damage_effects_blocked())
            value = max(0.0, damage) * 100.0
            if can_reduce_defense:
                value += 18.0 * min(2.0, defense - 1)  # half-chance value
            score += value if target.player_id != actor.player_id else -1.5 * value
        return score - 12.0 if score > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(target.player_id != actor.player_id for target in
                   self.get_target_units_for_payload(battle, actor, payload))

    def ai_candidate_filtered_reason(self, battle: Battle, actor: Unit,
                                     payloads: list[dict[str, Any]]) -> str | None:
        if payloads and all(self.ai_payload_value(battle, actor, payload) <= 0 for payload in payloads):
            return "no_positive_net_area_impact"
        return None

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name, ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", self.code},
            ))
            if not target.alive or not damage_followup_effect_applies(ctx):
                continue
            if direct_scoring() or getattr(battle, "_ai_probe_active", False):
                continue
            if battle.roll_chance(0.5, action_name="吹雪削守判定",
                                  beneficiary_player_id=actor.player_id,
                                  good_when_true=target.player_id != actor.player_id):
                target.add_status(BlizzardDefenseLoss(), source=actor)
                battle.log_public_event(f"{target.name} 被吹雪削守2点。", source=actor, target=target)
            else:
                battle.log_public_event(f"{target.name} 挡住了吹雪削守。", source=actor, target=target)
