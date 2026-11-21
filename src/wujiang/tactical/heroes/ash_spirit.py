"""R370 Ash Spirit: facing punch, searing bindings, cover, and ember jumps."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, QueuedAction, Skill,
    StatusEffect, Unit,
)
from wujiang.tactical.heroes.common import (
    match_payload_pattern, payload_position, pattern_selection_preview, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.electric_wind import front_rectangle_patterns
from wujiang.tactical.heroes.spirit_remnants import (
    SpiritPointTrait, SpiritRemnant, remnant_at, remnants_for, spirit_points,
    square_cells, visible_units_at,
)


_ASH_POINTS = "灰烬计数点"
_EMBER = "残焰"


class AshPointTrait(SpiritPointTrait):
    def __init__(self) -> None:
        super().__init__(_ASH_POINTS, 3, gain_on_owner_only=False,
                         spending_skill_codes=frozenset({"spirit_ember"}))


class SunChainStatus(StatusEffect):
    def __init__(self, source_unit_id: str, source_player_id: int) -> None:
        super().__init__("炎阳索束缚", "两轮内不能移动或用位移技能；己方轮末失最大血1/4。",
                         duration=2, tick_scope="any_turn_end")
        self.source_unit_id = source_unit_id
        self.source_player_id = source_player_id

    def can_use_normal_move(self, battle: Battle, actor: Unit,
                            destination: Position | None = None) -> tuple[bool, str]:
        return False, "炎阳索束缚：不能移动。"

    def blocks_skill_use(self, battle: Battle, actor: Unit,
                         skill: Skill) -> tuple[bool, str]:
        return (True, "炎阳索束缚：不能使用位移技能。") if skill.is_displacement_skill else (False, "")

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is None or ended_player_id != self.source_player_id:
            return
        source = battle.units.get(self.source_unit_id)
        if self.owner.alive:
            battle.resolve_damage(DamageContext(
                source=source, target=self.owner, attack_power=0,
                raw_damage=self.owner.max_health / 4, is_skill=True,
                from_field_effect=True, action_name="炎阳索轮末灼烧",
                tags={"skill", "spirit_sun_chain", "periodic"},
            ))
        if source is None or battle.unit_belongs_to_current_turn(source):
            self.decrement(battle)


class SunChainSkill(Skill):
    ai_payload_value_owned = True
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("spirit_sun_chain", "炎阳索",
                         "每2本人轮一次；周围至多两单位束缚2轮，并在友方轮末失血1/4。",
                         cooldown_turns=2, target_mode="multi_unit")
        self._count_turn = -1
        self._applied_this_turn = 0

    def _sync(self, battle: Battle) -> None:
        if self._count_turn != battle.turn_number:
            self._count_turn = battle.turn_number
            self._applied_this_turn = 0

    def eligible(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.position is None:
            return []
        cells = square_cells(battle, actor.position, 1)
        return [unit for unit in visible_units_at(battle, cells, ignore=actor)
                if unit.player_id != actor.player_id]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        self._sync(battle)
        if self._applied_this_turn >= 2 or not self.eligible(battle, actor):
            return False, "本回合炎阳索已用满，或周围没有目标。"
        ids = [str(unit_id) for unit_id in (payload or {}).get("target_unit_ids", [])]
        if ids and (len(ids) > 2 - self._applied_this_turn or len(ids) != len(set(ids))
                    or not set(ids) <= {unit.unit_id for unit in self.eligible(battle, actor)}):
            return False, "炎阳索最多选择两名周围敌方单位。"
        return True, ""

    def apply_to(self, battle: Battle, actor: Unit, target: Unit) -> None:
        self._sync(battle)
        if self._applied_this_turn >= 2 or not target.alive or target.player_id == actor.player_id:
            return
        self._applied_this_turn += 1
        if target.total_shields() > 0:
            target.consume_one_shield()
            battle.log_public_event(f"{target.name} 的护盾挡下炎阳索。", source=actor, target=target)
            return
        if target.magic_immunity or target.direct_effects_blocked() or target.skill_non_damage_effects_blocked():
            return
        old = next((status for status in target.statuses
                    if isinstance(status, SunChainStatus) and status.source_unit_id == actor.unit_id), None)
        if old is not None:
            target.remove_status(old, battle)
        target.add_status(SunChainStatus(actor.unit_id, actor.player_id), source=actor)
        battle.record_skill_effect(target)
        battle.log_public_event(f"{actor.name} 以炎阳索束缚 {target.name}。", source=actor, target=target)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        targets = [battle.units[unit_id] for unit_id in payload.get("target_unit_ids", [])
                   if unit_id in battle.units]
        for target in targets:
            battle.queue_skill_effect_action(
                actor=actor, display_name=self.name, effect_code="spirit_sun_chain_hit",
                target_units=[target], target_cells=battle.unit_cells(target),
                include_cell_units=False, effect_resolver=self._resolve_hit,
            )

    def _resolve_hit(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if target is not None:
                self.apply_to(battle, actor, target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "multi_unit", "min_targets": 1,
                              "max_targets": max(0, 2 - self._applied_this_turn)}}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [battle.units[unit_id] for unit_id in payload.get("target_unit_ids", [])
                if unit_id in battle.units]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        targets = sorted((unit for unit in self.eligible(battle, actor)
                          if self._target_value(unit) > 0),
                         key=self._target_value, reverse=True)
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_ids": [unit.unit_id for unit in targets[:2]]}] if targets else []

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def _target_value(self, target: Unit) -> float:
        if target.magic_immunity or target.direct_effects_blocked() or target.skill_non_damage_effects_blocked():
            return 0.0
        if target.total_shields() > 0:
            return 24.0
        if target.has_status("炎阳索束缚"):
            return 12.0
        return 50.0 + target.stat("speed") * 8.0 + target.max_health * 2.0

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        ids = payload.get("target_unit_ids", [])
        return sum(self._target_value(unit) for unit in self.eligible(battle, actor)
                   if unit.unit_id in ids) - 12.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ShadowlessFistSkill(Skill):
    ai_payload_value_owned = True
    ai_optional_choice_owned = True

    def __init__(self) -> None:
        super().__init__("spirit_shadowless_fist", "无影拳",
                         "每本人轮一次；身前5×5逐单位攻击，随后可对其中两人施炎阳索。",
                         max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return front_rectangle_patterns(battle, actor, depth=5, width=5)

    def chosen_cells(self, battle: Battle, actor: Unit,
                     payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return visible_units_at(battle, self.chosen_cells(battle, actor, payload), ignore=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return pattern_selection_preview(self.patterns(battle, actor))

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        return sum((90.0 if unit.player_id != actor.player_id else -120.0)
                   for unit in self.get_target_units_for_payload(battle, actor, payload))

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any],
                                  targets: list[Unit] | None = None,
                                  shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        # This area attack has its own code, so the generic catalog of damaging
        # skill codes cannot recognize its recipients during candidate gating.
        return any(unit.player_id != actor.player_id and unit.alive
                   for unit in self.get_target_units_for_payload(battle, actor, payload))

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        battle.queue_skill_effect_action(
            actor=actor, display_name=self.name, effect_code="spirit_shadowless_fist_hit",
            target_units=visible_units_at(battle, cells, ignore=actor),
            target_cells=cells, include_cell_units=False,
            effect_resolver=self._resolve_punch,
        )

    def _resolve_punch(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        targets = [battle.units[unit_id] for unit_id in queued.target_unit_ids
                   if unit_id in battle.units and battle.units[unit_id].alive]
        for target in targets:
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name, tags={"skill", self.code},
            ))
        chain = actor.get_skill("spirit_sun_chain")
        choices = [unit.unit_id for unit in targets if unit.alive and unit.player_id != actor.player_id]
        for _ in range(min(2, len(choices))):
            selected = battle.optional_position_choice(
                actor, choices, action_name="无影拳后选炎阳索目标",
                kind="optional_unit_selection", choice_context={"ash_combo": True})
            if selected not in choices:
                break
            choices.remove(selected)
            target = battle.units.get(selected)
            if target is not None:
                battle.queue_skill_effect_action(
                    actor=actor, display_name="无影拳追接炎阳索",
                    effect_code="spirit_sun_chain_hit", target_units=[target],
                    target_cells=battle.unit_cells(target), include_cell_units=False,
                    effect_resolver=chain._resolve_hit,
                )

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if actor is not self.owner or not (prompt.get("choice_context") or {}).get("ash_combo"):
            return None
        unit = battle.units.get(choice)
        chain = actor.get_skill("spirit_sun_chain")
        return chain._target_value(unit) if unit is not None and unit.alive else -1000.0


class FireCoverStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("烈火罩", "2额外生命仅吸技能伤；轮末周围固定3伤。",
                         duration=2, tick_scope="owner_turn_end")
        self.extra_health = 2.0

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is not self.owner or not ctx.is_skill or self.extra_health <= 0 or ctx.raw_damage <= 0:
            return
        blocked = min(self.extra_health, ctx.raw_damage)
        self.extra_health = round(self.extra_health - blocked, 4)
        ctx.raw_damage = max(0.0, ctx.raw_damage - blocked)
        battle.log_public_event(f"烈火罩替 {ctx.target.name} 吸收 {blocked:g} 技能伤。",
                                source=ctx.source, target=ctx.target)

    def blocks_skill_non_damage_effects(self) -> bool:
        return self.extra_health > 0

    def accepts_status(self, status: StatusEffect) -> bool:
        return self.extra_health <= 0 or not getattr(status, "is_skill_effect", True)

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        action = battle.resolving_action
        if (self.extra_health > 0 and self.owner is ctx.target
                and ("skill" in ctx.tags or (action is not None and action.action_type in
                     {"skill", "reaction_skill", "skill_effect"}))):
            ctx.cancelled = True
            ctx.reason = f"{ctx.target.name} 的烈火罩不受技能回复效果影响。"

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None and owner.position is not None and owner.alive:
            targets = visible_units_at(battle, square_cells(battle, owner.position, 1), ignore=owner)
            for target in targets:
                battle.resolve_damage(DamageContext(
                    source=owner, target=target, attack_power=0, raw_damage=3,
                    is_skill=True, from_field_effect=True, action_name="烈火罩轮末火焰",
                    tags={"skill", "spirit_fire_cover", "periodic"},
                ))
        super().on_owner_turn_end(battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["extra_health"] = self.extra_health
        return data


class FireCoverSkill(Skill):
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("spirit_fire_cover", "烈火罩",
                         "4轮一次；2轮额外生命2只挡技能伤，每本人轮末周围固定3伤。",
                         cooldown_turns=4, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        old = actor.get_status("烈火罩")
        if old is not None:
            actor.remove_status(old, battle)
        actor.add_status(FireCoverStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 获得2点烈火罩额外生命。", source=actor)

    def ai_priority_score(self, context: Any) -> float:
        actor, battle = context.actor, context.battle
        nearby = sum(1 for unit in battle.enemy_units(actor.player_id)
                     if unit.position is not None and actor.position is not None
                     and battle.distance_between_units(actor, unit) <= 2)
        return 55.0 + 28.0 * nearby if actor.get_status("烈火罩") is None else -1000.0


class EmberSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("spirit_ember", _EMBER,
                         "耗1灰烬计数点；范5放置可激活的残焰。", target_mode="cell")

    def legal_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or spirit_points(actor, _ASH_POINTS).points < 1:
            return []
        return [cell for y in range(battle.height) for x in range(battle.width)
                if (cell := Position(x, y)).distance_to(actor.position) <= 5
                and battle.can_place_unit(actor, cell, ignore=actor, mover=actor)
                and remnant_at(battle, actor, _EMBER, cell) is None]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.legal_cells(battle, actor)
        if not cells:
            return False, "灰烬点不足或没有合法残焰格。"
        if payload and payload.get("x") is not None and payload_position(payload) not in cells:
            return False, "请选择范5内合法残焰格。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        battle.add_field_effect(SpiritRemnant(_EMBER, actor.unit_id, cell), source=actor)
        battle.log_public_event(f"{actor.name} 于({cell.x},{cell.y})放置残焰。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.legal_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        cells = sorted(self.legal_cells(battle, actor),
                       key=lambda cell: min((cell.distance_to(unit.position) for unit in battle.enemy_units(
                           actor.player_id) if unit.position is not None), default=99))[:16]
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in cells]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        cell = payload_position(payload)
        enemies = [unit for unit in visible_units_at(battle, square_cells(battle, cell, 1))
                   if unit.player_id != actor.player_id]
        return 24.0 + len(enemies) * 28.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ActivateEmberSkill(Skill):
    is_displacement_skill = True
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("spirit_activate_ember", "激活残焰",
                         "随时移动到己方残焰，销毁后周围受当前攻伤。",
                         timing="instant", target_mode="cell")

    def legal(self, battle: Battle, actor: Unit) -> list[SpiritRemnant]:
        return [effect for effect in remnants_for(battle, actor, _EMBER)
                if battle.can_place_unit(actor, effect.cell, ignore=actor, mover=actor)]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = {effect.cell for effect in self.legal(battle, actor)}
        if not cells or (payload and payload.get("x") is not None
                         and payload_position(payload) not in cells):
            return False, "没有可激活的合法残焰落格。"
        return True, ""

    def can_react_to(self, battle: Battle, actor: Unit,
                     queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        return (True, "") if ok and self.legal(battle, actor) else (False, reason or "没有可激活残焰。")

    def can_react_with_payload(self, battle: Battle, actor: Unit,
                               queued_action: QueuedAction,
                               payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("x") is not None and payload_position(payload) not in {
                effect.cell for effect in self.legal(battle, actor)}:
            return False, "请选择可激活的残焰。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        marker = remnant_at(battle, actor, _EMBER, cell)
        if marker is None or marker not in self.legal(battle, actor):
            raise ActionError("残焰已消失或落点受阻。")
        battle.move_unit(actor, cell, via_skill=True, allow_anywhere=True,
                         tags={"spirit_activate_ember"})
        battle.remove_field_effect(marker)
        cells = square_cells(battle, cell, 1)
        battle.queue_skill_effect_action(
            actor=actor, display_name="残焰爆发", effect_code="spirit_ember_burst",
            target_units=visible_units_at(battle, cells, ignore=actor), target_cells=cells,
            include_cell_units=False, effect_resolver=self._burst,
        )

    def _burst(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        for unit_id in queued.target_unit_ids:
            target = battle.units.get(unit_id)
            if target is not None and target.alive:
                battle.resolve_damage(DamageContext(
                    source=actor, target=target, attack_power=actor.stat("attack"),
                    is_skill=True, action_name="残焰爆发", tags={"skill", self.code},
                ))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict([effect.cell for effect in self.legal(battle, actor)]),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def reaction_preview(self, battle: Battle, actor: Unit,
                         queued_action: QueuedAction) -> dict[str, Any]:
        return self.preview(battle, actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": effect.cell.x, "y": effect.cell.y} for effect in self.legal(battle, actor)]

    def ai_reaction_payloads(self, battle: Battle, actor: Unit,
                             queued_action: QueuedAction,
                             option: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "chain_react", "unit_id": actor.unit_id, "action_code": self.code,
                 "x": effect.cell.x, "y": effect.cell.y} for effect in self.legal(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        cell = payload_position(payload)
        enemies = [unit for unit in visible_units_at(battle, square_cells(battle, cell, 1))
                   if unit.player_id != actor.player_id]
        return len(enemies) * 75.0 + (25.0 if actor.current_hp < actor.max_health / 2 else 0.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_reaction_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
