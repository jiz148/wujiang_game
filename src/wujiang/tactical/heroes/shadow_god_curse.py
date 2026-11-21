"""R368: straight-line interference, global attack permission, and displacement limit."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, Position, QueuedAction,
    Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.common import payload_position, positions_to_dict


_DIRECTIONS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
               (1, -1), (1, 0), (1, 1))


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


class ShadowInterferenceLock(StatusEffect):
    def __init__(self) -> None:
        super().__init__("影之干扰禁动", "下个本人回合不能主动行动。", duration=None)
        self.active = False

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.active = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.active and self.owner is not None:
            self.owner.remove_status(self, battle)

    def can_use_basic_attack(self, battle: Battle, actor: Unit,
                             payload: dict[str, Any]) -> tuple[bool, str]:
        return (False, "影之干扰：本回合不能行动。") if self.active else (True, "")

    def can_use_normal_move(self, battle: Battle, actor: Unit,
                            destination: Position | None = None) -> tuple[bool, str]:
        return (False, "影之干扰：本回合不能行动。") if self.active else (True, "")

    def blocks_skill_use(self, battle: Battle, actor: Unit,
                         skill: Skill) -> tuple[bool, str]:
        return (False, "") if not self.active else (True, "影之干扰：本回合不能行动。")


class ShadowInterferenceSkill(Skill):
    ai_payload_value_owned = True
    target_cells_are_geometry_only = True
    is_ultimate = True

    def __init__(self) -> None:
        super().__init__("shadow_interference", "影之干扰",
                         "大招；选直线至7格，破魔命中者下个本人回合不能行动。",
                         max_uses_per_battle=1, target_mode="cell")

    def lines(self, battle: Battle, actor: Unit) -> dict[Position, list[Position]]:
        if actor.position is None:
            return {}
        return {line[-1]: line for direction in _DIRECTIONS
                if (line := battle.line_positions(actor.position, direction, 7))}

    def _line(self, battle: Battle, actor: Unit,
              payload: dict[str, Any]) -> list[Position]:
        line = self.lines(battle, actor).get(payload_position(payload))
        if line is None:
            raise ActionError("请选择影之干扰的一条合法直线。")
        return line

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("x") is not None:
            try:
                self._line(battle, actor, payload)
            except (ActionError, ValueError, TypeError) as exc:
                return False, str(exc)
        return True, ""

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self._line(battle, actor, payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        line = self._line(battle, actor, payload)
        battle.queue_skill_effect_action(
            actor=actor, display_name="影之干扰直线", effect_code="shadow_interference_line",
            target_units=battle.effect_units_at_cells(line, ignore=actor),
            target_cells=line, include_cell_units=False,
            payload={"ignore_shield": True}, effect_resolver=self.resolve_line,
        )

    def resolve_line(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for target in battle.effect_units_at_cells(queued.target_cells, ignore=actor):
            if not _present(target):
                continue
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, ignore_shield=True, action_name=self.name,
                tags={"skill", self.code},
            ))
            if ctx.actual_damage > 0 and target.alive:
                previous = target.get_status("影之干扰禁动")
                if previous is not None:
                    target.remove_status(previous, battle)
                target.add_status(ShadowInterferenceLock(), source=actor)
                battle.log_public_event(f"{target.name} 的下个本人回合被影之干扰封住。",
                                        source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        lines = self.lines(battle, actor)
        return {"cells": positions_to_dict(list(lines)), "target_unit_ids": [],
                "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.lines(battle, actor)]

    def ai_effect_units(self, battle: Battle, actor: Unit,
                        payload: dict[str, Any]) -> list[Unit]:
        try:
            return battle.effect_units_at_cells(self._line(battle, actor, payload), ignore=actor)
        except (ActionError, TypeError, ValueError):
            return []

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        units = self.ai_effect_units(battle, actor, payload)
        return sum((110.0 if unit.player_id != actor.player_id else -140.0)
                   for unit in units if _present(unit)) - 20.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ShadowGravityWaveStatus(StatusEffect):
    def __init__(self, board_extent: int) -> None:
        super().__init__("影神力。重力波", "本实际回合普攻可攻击场上任意单位。", duration=None)
        self.board_extent = board_extent

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(value, self.board_extent) if stat_name == "attack_range" else value

    def ignores_direct_unit_target_line(self, battle: Battle, actor: Unit) -> bool:
        return True

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)


class ShadowGravityWaveSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("shadow_gravity_wave", "影神力。重力波",
                         "每2本人轮一次，友方一人在本实际回合可普攻场上任意单位。",
                         cooldown_turns=2, target_mode="unit")

    def eligible(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.player_units(actor.player_id)
                if _present(unit) and battle.distance_between_units(actor, unit) <= actor.targeting_range()]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.eligible(battle, actor)}:
                return False, "重力波只能指定范内己方单位。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units[str(payload["target_unit_id"])]
        previous = target.get_status("影神力。重力波")
        if previous is not None:
            target.remove_status(previous, battle)
        target.add_status(ShadowGravityWaveStatus(battle.width + battle.height - 2), source=actor)
        battle.log_public_event(f"{target.name} 本回合获得重力波全场普攻。", source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible(battle, actor)
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.eligible(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.eligible(battle, actor) or target.has_status("影神力。重力波"):
            return -100.0
        if target.attacks_used >= target.attack_actions_per_turn() or not battle.unit_belongs_to_current_turn(target):
            return -30.0
        available = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        currently = set(battle.basic_attack_target_ids_for_payload(target))
        new = [unit for unit in available if unit.unit_id not in currently]
        return 60.0 + max((unit.current_hp for unit in new), default=0.0) * 30.0 if new else -20.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ShadowSummonedMoveLimitStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("咒血王召唤禁位移", "复出后2本人轮，敌方全队每实际回合仅一次位移技能。",
                         duration=2, tick_scope="owner_turn_end")


class ShadowSummonedMoveLimitField(BattleFieldEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("咒血王召唤禁位移", "敌方每实际回合全队共一次位移技能。")
        self.source_unit_id = source_unit_id
        self.turn_number = -1
        self.used = 0

    def _active(self, battle: Battle) -> Unit | None:
        owner = battle.units.get(self.source_unit_id)
        return owner if _present(owner) and owner.has_status("咒血王召唤禁位移") else None

    def blocks_skill_payload(self, battle: Battle, actor: Unit, skill: Skill,
                             payload: dict[str, Any] | None) -> tuple[bool, str]:
        owner = self._active(battle)
        if owner is None or actor.player_id == owner.player_id or not skill.is_displacement_skill:
            return False, ""
        if self.turn_number != battle.turn_number:
            self.turn_number, self.used = battle.turn_number, 0
        queued = battle.resolving_action
        if (self.used >= 1 and not (queued is not None and payload is not None
                                    and battle.is_declared_resolution(actor, payload, {"skill"}))):
            return True, "咒血王压制：本实际回合敌方全队已用过一次位移技能。"
        return False, ""

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        owner = self._active(battle)
        actor = battle.units.get(str(payload.get("unit_id") or ""))
        if owner is None or actor is None or actor.player_id == owner.player_id or action_type != "skill":
            return
        skill = actor.skill_map().get(str(payload.get("skill_code") or ""))
        if skill is None or not skill.is_displacement_skill:
            return
        if self.turn_number != battle.turn_number:
            self.turn_number, self.used = battle.turn_number, 0
        self.used += 1


class ShadowSummonedMoveLimitTrait(Trait):
    def __init__(self) -> None:
        super().__init__("召唤后禁位移", "真正召回后2本人轮，敌方全队每实际回合只能用一次位移技能。")
        self.field: ShadowSummonedMoveLimitField | None = None

    def on_restored_by_apocalypse(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            owner.add_status(ShadowSummonedMoveLimitStatus(), source=owner)
            self.field = ShadowSummonedMoveLimitField(owner.unit_id)
            battle.add_field_effect(self.field, source=owner)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.field in battle.field_effects:
            battle.remove_field_effect(self.field)
        self.field = None
