"""R069 月面兽。正义盟军: skill tempo, pulse, flash and last stand."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, QueuedAction, Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.ai_candidate_plan import SkillCandidatePlan
from wujiang.tactical.heroes.common import payload_direction, payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.prediction import direct_scoring


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


class MoonRallyStatus(StatusEffect):
    def __init__(self, triggered_turn: int) -> None:
        super().__init__("月面集结", "邻近敌方每次声明技能，攻+1至下次本队回合末。",
                         duration=1)
        self.triggered_turn = triggered_turn

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name == "attack" else value

    def on_owner_turn_end(self, battle: Battle) -> None:
        # The source says next team turn, which may belong to another ally.
        return None

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if (self.owner is not None and ended_player_id == self.owner.player_id
                and battle.turn_number > self.triggered_turn):
            self.owner.remove_status(self, battle)


class MoonFlashStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("闪光弹", "攻、速各-1，最低1；普攻有1/2概率失效，持续三本人轮。",
                         duration=3, tick_scope="owner_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 1) if stat_name in {"attack", "speed"} else value

    def before_basic_attack_resolution(self, battle: Battle, actor: Unit,
                                       payload: dict[str, Any]) -> bool:
        if actor is not self.owner:
            return True
        if direct_scoring() or getattr(battle, "_ai_probe_active", False):
            return True
        succeeds = battle.roll_chance(0.5, action_name="闪光弹普攻有效判定",
                                      beneficiary_player_id=actor.player_id)
        battle.log_public_event(f"{actor.name} 的闪光弹普攻判定：{'有效' if succeeds else '失效'}。",
                                source=actor)
        return succeeds

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ((direct_scoring() or getattr(battle, "_ai_probe_active", False))
                and ctx.source is self.owner and not ctx.is_skill and "attack" in ctx.tags):
            ctx.complete_block_chance = 1.0 - (1.0 - ctx.complete_block_chance) * 0.5
            ctx.complete_block_label = "闪光弹普攻有效判定"


class MoonBeastTrait(Trait):
    def __init__(self) -> None:
        super().__init__("月面兽", "被破坏后攻击周围一周；主动技能重置普通移动及普攻；邻敌施技暂时攻+1。")

    def on_any_skill_declared(self, battle: Battle, actor: Unit, skill: Skill) -> None:
        owner = self.owner
        if not _present(owner):
            return
        if actor is owner:
            if skill.timing == "active":
                owner.move_used = False
                owner.normal_move_actions_used = 0
                owner.normal_move_steps_used = 0
                owner.attacks_used = 0
                battle.log_public_event(f"{owner.name} 因主动技能恢复普通移动与普攻次数。", source=owner)
        elif (actor.player_id != owner.player_id and _present(actor)
              and battle.distance_between_units(owner, actor) <= 1):
            owner.add_status(MoonRallyStatus(battle.turn_number), source=owner)
            battle.log_public_event(f"{owner.name} 因邻敌施技，攻击暂时+1。", source=owner)

    def on_owner_destruction_pending(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.alive or owner.position is None:
            return
        own_cells = set(owner.footprint_cells_at(owner.position))
        ring = [Position(x, y)
                for x in range(min(cell.x for cell in own_cells) - 1,
                               max(cell.x for cell in own_cells) + 2)
                for y in range(min(cell.y for cell in own_cells) - 1,
                               max(cell.y for cell in own_cells) + 2)
                if battle.in_bounds(Position(x, y)) and Position(x, y) not in own_cells]
        targets = [unit for unit in battle.effect_units_at_cells(ring, ignore=owner)
                   if unit.player_id != owner.player_id]
        if not targets:
            return
        battle.queue_skill_effect_action(
            actor=owner, display_name="月面兽最后一击", effect_code="moon_last_stand",
            target_units=targets, target_cells=ring, include_cell_units=False,
            payload={"after_death_effect": True, "attack_power": owner.stat("attack")},
            effect_resolver=self.resolve_last_stand,
        )
        battle.log_public_event(f"{owner.name} 被破坏，向周围一周发动最后一击。", source=owner)

    @staticmethod
    def resolve_last_stand(battle: Battle, owner: Unit, queued: QueuedAction) -> None:
        for target_id in queued.target_unit_ids:
            target = battle.units.get(target_id)
            if _present(target) and target.player_id != owner.player_id:
                battle.resolve_damage(DamageContext(
                    source=owner, target=target,
                    attack_power=float(queued.payload["attack_power"]),
                    is_skill=False, action_name="月面兽最后一击",
                    tags={"attack", "moon_last_stand"},
                ))


class MoonPulseSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    ai_complete_direction_payloads = True
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("moon_pulse", "脉冲", "一回合一次，朝一方向前方五格造成当前攻击力的技能伤害。",
                         max_uses_per_turn=1)

    def cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            raise ActionError("月面兽不在场上。")
        dx, dy = payload_direction(payload)
        return [cell for step in range(1, 6)
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
                return False, "请选择合法方向。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        directions = []
        secondary = []
        for dx, dy in ALL_DIRECTIONS:
            cells = self.cells(battle, actor, {"direction": {"dx": dx, "dy": dy}})
            if cells:
                directions.append({"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"})
                secondary.extend(cells)
        return {"cells": [], "target_unit_ids": [],
                "secondary_cells": positions_to_dict(secondary), "requires_target": True,
                "selection": {"mode": "direction_only", "directions": directions,
                              "prompt": "选择脉冲攻击的方向。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.cells(battle, actor, payload), ignore=actor)

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
        targets = self.get_target_units_for_payload(battle, actor, payload)
        reset = (actor.attacks_used * 18.0 +
                 (12.0 if actor.move_used or actor.normal_move_actions_used else 0.0))
        return sum((55.0 if unit.player_id != actor.player_id else -80.0)
                   for unit in targets) + reset - 8.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id and not unit.magic_immunity
                   for unit in self.get_target_units_for_payload(battle, actor, payload))

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in list(self.get_target_units_for_payload(battle, actor, payload)):
            if target.alive:
                battle.resolve_damage(DamageContext(
                    source=actor, target=target, attack_power=actor.stat("attack"),
                    is_skill=True, action_name=self.name, tags={"skill", self.code},
                ))


class MoonFlashSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=12, prefilter_positive=True,
                                          score_shortlist=True)

    def __init__(self) -> None:
        super().__init__("moon_flash", "闪光弹", "一回合一次，选3×3区域使双方攻速各-1、普攻一半失效，持续三本人轮。",
                         max_uses_per_turn=1, target_mode="cell")

    def anchors(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        origins = battle.unit_cells(actor)
        return [Position(x, y) for x in range(battle.width - 2) for y in range(battle.height - 2)
                if any(origin.distance_to(Position(x, y)) <= actor.targeting_range()
                       for origin in origins)]

    def cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        anchor = payload_position(payload)
        if anchor not in self.anchors(battle, actor):
            raise ActionError("闪光弹需选择范内且完整位于场上的3×3左上角。")
        return [anchor.offset(dx, dy) for dx in range(3) for dy in range(3)]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.anchors(battle, actor):
            return False, "没有完整的3×3区域。"
        if payload is not None and "x" in payload:
            try:
                self.cells(battle, actor, payload)
            except (ActionError, TypeError, ValueError):
                return False, "闪光弹区域不合法。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        anchors = self.anchors(battle, actor)
        return {"cells": positions_to_dict(anchors), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "cell", "prompt": "选择3×3区域的左上角。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.cells(battle, actor, payload))

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.anchors(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_prefilter_score(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                           shared: Any) -> float:
        return self.ai_payload_value(battle, actor, payload)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            targets = self.get_target_units_for_payload(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        reset = (actor.attacks_used * 18.0 +
                 (12.0 if actor.move_used or actor.normal_move_actions_used else 0.0))
        return sum((52.0 if unit.player_id != actor.player_id else -65.0)
                   for unit in targets if not unit.magic_immunity and not unit.has_status("闪光弹")
                   and unit.total_shields() == 0) + reset - 8.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id and not unit.magic_immunity
                   for unit in self.get_target_units_for_payload(battle, actor, payload))

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in list(self.get_target_units_for_payload(battle, actor, payload)):
            if not _present(target):
                continue
            effect = battle.validate_target(actor, target, action_name=self.name,
                                            is_skill=True,
                                            is_hostile=target.player_id != actor.player_id)
            if effect.cancelled:
                continue
            for status in list(target.statuses):
                if isinstance(status, MoonFlashStatus):
                    target.remove_status(status, battle)
            target.add_status(MoonFlashStatus(), source=actor)
            battle.record_skill_effect(target)
            battle.log_public_event(f"{target.name} 被闪光弹影响三本人轮。", source=actor, target=target)
