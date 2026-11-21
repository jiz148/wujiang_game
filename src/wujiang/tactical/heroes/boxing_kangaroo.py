"""R077 拳击袋鼠：方向地裂、兽化、逐次闪避和第二拳破魔。"""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, QueuedAction, Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.common import (
    DashMoveSkill, StatModifierStatus, payload_direction, payload_position, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS


class KangarooLeapSkill(DashMoveSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("kangaroo_leap", "飞跃（大）",
                         "1魔、每本人轮一次；沿合法直线飞跃恰好6格，可穿越单位。",
                         max_distance=6, exact_distance=6, mana_cost=1,
                         max_uses_per_turn=1, straight_only=True, ignore_units=True)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            destination = payload_position(payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        if destination not in battle.reachable_positions(
                actor, max_distance=6, exact_distance=6, straight_only=True,
                ignore_units=True):
            return -1000.0
        enemies = [enemy for enemy in battle.enemy_units(actor.player_id)
                   if enemy.alive and enemy.position is not None]
        if not enemies or actor.position is None:
            return -1000.0
        before = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        after = min(battle.unit_distance_to_cell(enemy, destination) for enemy in enemies)
        gain = before - after
        if gain <= 0:
            return -1000.0
        attack_range = actor.stat("attack_range")
        immediate_attack = 30.0 if after <= attack_range and before > attack_range else 0.0
        return gain * 13.0 + immediate_attack - (12.0 if actor.current_mana <= 1 else 0.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class EarthFissureSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    ai_complete_direction_payloads = True
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("earth_fissure", "地裂",
                         "本场一次；选方向朝身体外直线四格，对双方各造成其结算时当前血量一半的技能伤害。",
                         max_uses_per_battle=1)

    def cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        declared = battle.payload_positions(payload, "declared_fissure_cells")
        if declared:
            return declared
        if actor.position is None:
            raise ActionError("拳击袋鼠不在场上。")
        dx, dy = payload_direction(payload)
        return [cell for step in range(1, 5)
                if battle.in_bounds(cell := actor.position.offset(dx * step, dy * step))]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload is not None and "direction" in payload:
            try:
                if not self.cells(battle, actor, payload):
                    return False, "该方向没有有效格。"
            except (ActionError, TypeError, ValueError):
                return False, "请选择地裂的合法方向。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        directions = []
        all_cells: set[Position] = set()
        if actor.position is not None:
            for dx, dy in ALL_DIRECTIONS:
                cells = self.cells(battle, actor, {"direction": {"dx": dx, "dy": dy}})
                if not cells:
                    continue
                directions.append({"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"})
                all_cells.update(cells)
        return {"cells": [], "target_unit_ids": [],
                "secondary_cells": positions_to_dict(sorted(all_cells, key=lambda cell: (cell.y, cell.x))),
                "requires_target": True,
                "selection": {"mode": "direction_only", "directions": directions,
                              "prompt": "选择地裂的四格方向。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.cells(battle, actor, payload), ignore=actor))

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_fissure_cells": positions_to_dict(self.cells(battle, actor, payload))}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        if actor.position is None:
            return []
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "direction": {"dx": dx, "dy": dy}}
                for dx, dy in ALL_DIRECTIONS
                if battle.in_bounds(actor.position.offset(dx, dy))]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            targets = self.get_target_units_for_payload(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        score = 0.0
        enemy_impact = False
        for target in targets:
            if target.magic_immunity:
                continue
            if target.total_shields() > 0 or target.dodge_charges > 0:
                benefit = 12.0
            else:
                benefit = min(target.current_hp, target.current_hp / 2) * 130.0
            if target.player_id == actor.player_id:
                score -= benefit * 1.5
            else:
                score += benefit
                enemy_impact = True
        return score - 10.0 if enemy_impact and score > 10 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            if not target.alive:
                continue
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=0, is_skill=True,
                action_name="地裂", area_cell_hits=1,
                tags={"skill", "kangaroo_earth_fissure"},
            ))

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.source and "kangaroo_earth_fissure" in ctx.tags:
            ctx.raw_damage = max(0.0, ctx.target.current_hp / 2)


class BeastFormStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("兽化", attack_delta=2, defense_delta=-1, range_delta=-1,
                         description="攻+2、范-1、守-1；再次于本人回合开始切换可关闭。")


class BeastFormSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("beast_form", "兽化",
                         "0魔；每本人轮一次，仅回合开始可开关；开启时攻+2、范-1、守-1。",
                         max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used or actor.performed_active_skill:
            return False, "兽化只能在本人回合开始阶段切换。"
        return True, ""

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        enemies = [enemy for enemy in battle.enemy_units(actor.player_id)
                   if enemy.alive and enemy.position is not None]
        if not enemies:
            return -1000.0
        nearest = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        active = actor.has_status("兽化")
        if active:
            return 12.0 if nearest >= 3 else -30.0
        return 38.0 if nearest <= actor.normal_move_distance() + 2 else 5.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("兽化")
        if existing is not None:
            actor.remove_status(existing, battle)
            battle.log_public_event(f"{actor.name} 关闭兽化。", source=actor)
        else:
            actor.add_status(BeastFormStatus(), source=actor)
            battle.log_public_event(f"{actor.name} 开启兽化。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}


class NextDamageDodgeStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("闪避预备", "下一次可能损血的独立伤害对本人无效。")

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is not ctx.target or ctx.raw_damage is None or ctx.raw_damage <= 0:
            return
        ctx.raw_damage = 0.0
        ctx.modifier_receipts.add("kangaroo_next_damage_dodge")
        battle.log_public_event(f"{ctx.target.name} 闪避了下一次伤害。",
                                source=ctx.target, target=ctx.target)
        ctx.target.remove_status(self, battle)


class NextDamageDodgeSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__("kangaroo_dodge", "闪避",
                         "被动；每本人轮一次，可在受动作影响时连锁，使下一次可能损血的独立伤害无效。",
                         max_uses_per_turn=1, timing="passive", target_mode="self")

    def can_react_to(self, battle: Battle, actor: Unit,
                     queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if battle.reaction_proxy_target(actor, queued_action) is None:
            return False, "闪避只能对影响自己的动作连锁。"
        if actor.has_status("闪避预备"):
            return False, "已有下一次伤害闪避。"
        return True, ""

    def reaction_preview(self, battle: Battle, actor: Unit,
                         queued_action: QueuedAction) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("闪避只能通过连锁使用。")

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any],
              queued_action: QueuedAction) -> None:
        actor.add_status(NextDamageDodgeStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 使用闪避，下一次可能损血的伤害无效。", source=actor)

    def ai_reaction_score(self, context: Any) -> float:
        if context.actor.has_status("闪避预备"):
            return -1000.0
        threat = context.threat(context.proxy_target())
        return threat + (75.0 if threat >= context.actor.current_hp * 100 else 0.0) - 35.0


class SecondAttackPierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("第二次普攻破魔", "每本人轮第二次普攻的伤害与附带效果破魔。")

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if actor is self.owner and actor.attacks_used == 1:
            return {"ignore_shield": True}
        return {}
