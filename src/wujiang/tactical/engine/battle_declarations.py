"""Battle BattleDeclarationMixin methods, retained under the Battle public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleDeclarationMixin:
    """A cohesive Battle responsibility with no independent state."""

    def build_queued_action(self, payload: dict[str, Any], *, reaction_to: Optional[QueuedAction] = None) -> QueuedAction:
        from wujiang.tactical.engine.core import (
            ActionError,
            Position,
            QueuedAction,
            _id_counter,
        )
        action_type = payload.get("type")
        if action_type in {"end_turn", "pass_unit", "chain_react", "chain_skip"}:
            raise ActionError("该动作不能进入连锁栈。")
        queued_payload = dict(payload)
        queued_payload["declaration_id"] = f"action-{next(_id_counter)}"
        queued_payload.pop("enemy_skill_prevented_attack", None)
        queued_payload.pop("passive_attack_preventer_ids", None)
        queued_payload.pop("evaded_unit_ids", None)
        queued_payload.pop("completed_evasion_unit_ids", None)
        queued_payload.pop("declared_source_attack", None)
        queued_payload.pop("descent_extra_declared", None)
        queued_payload.pop("_attack_pre_move_done", None)
        queued_payload.pop("allied_heal_attack", None)
        queued_payload.pop("wuchang_mist_checked", None)
        queued_payload.pop("action_failed_by_wuchang_mist", None)
        queued_payload.pop("floating_crane_checked", None)
        queued_payload.pop("action_failed_by_floating_crane", None)
        queued_payload.pop("separate_attack_segments", None)
        queued_payload.pop("separate_attack_index", None)
        queued_payload.pop("piercing_followup", None)
        queued_payload.pop("target_must_occupy_declared_cell", None)
        queued_payload.pop("kings_insight_checked", None)
        queued_payload.pop("chain_prompt_prefix", None)
        queued_payload["queued_resolution"] = True
        actor = self.get_unit(payload["unit_id"])
        if action_type == "move":
            if not actor.can_take_turn_actions(self):
                raise ActionError("这个单位当前不能行动。")
            remaining_move = actor.remaining_normal_move_distance(self)
            if (
                actor.normal_move_actions_used >= actor.normal_move_actions_per_turn()
                and not actor.allows_split_normal_movement(self)
            ):
                raise ActionError("本回合已经移动过了。")
            if remaining_move <= 0:
                raise ActionError("本回合移动距离已经用完。")
            if actor.cannot_move:
                raise ActionError(f"{actor.name} 当前无法移动。")
            if actor.cannot_normal_move:
                raise ActionError(f"{actor.name} 当前不能进行常规移动。")
            explicit_steps = self.payload_positions(payload, "path")
            if explicit_steps:
                path = self.explicit_path(
                    actor,
                    explicit_steps,
                    max_distance=remaining_move,
                    use_movement_cost=True,
                )
                queued_payload["path"] = [step.to_dict() for step in path[1:]]
                queued_payload["x"] = path[-1].x
                queued_payload["y"] = path[-1].y
            elif payload.get("x") is None or payload.get("y") is None:
                raise ActionError("移动需要指定目标位置。")
            for component in actor.iter_components():
                forced_paths = component.forced_normal_move_paths(self, actor)
                if forced_paths is None:
                    continue
                destination = Position(int(queued_payload["x"]), int(queued_payload["y"]))
                selected = next((route for route in forced_paths if route[-1] == destination), None)
                if selected is None or (explicit_steps and path != selected):
                    raise ActionError("狂暴浮游炮必须沿最近目标的最短路线行动。")
                queued_payload["path"] = [step.to_dict() for step in selected[1:]]
            return QueuedAction(
                action_type="move",
                actor_id=actor.unit_id,
                display_name="移动",
                speed=1,
                payload=queued_payload,
                description="移动到指定格子，不会直接造成伤害或附加效果。",
                target_unit_ids=[],
                target_cells=[],
                source_player_id=actor.player_id,
                hostile=False,
                suppress_logs=actor.is_stealthed(),
            )
        if action_type == "attack":
            for key in ("attack_cost", "attack_power_override", "attack_bonus"):
                queued_payload.pop(key, None)
            queued_payload.pop("attack_cells", None)
            queued_payload.pop("single_hit_per_recipient", None)
            queued_payload.pop("allow_empty_attack_cell", None)
            queued_payload.pop("nuclear_rush_landing", None)
            queued_payload.pop("nuclear_rush_declared", None)
            queued_payload.pop("basic_attack_cancelled", None)
            queued_payload.pop("attack_effect_summary", None)
            if queued_payload.get("attack_variant") != "allied_heal":
                override = next((component for component in actor.iter_components() if getattr(component, "overrides_basic_attack_shape", False)), None)
                if override is not None:
                    override.validate_declaration(self, actor, queued_payload)
            queued_payload = self.resolved_basic_attack_payload(actor, queued_payload)
            ok, reason = self.basic_attack_resource_allowed(actor, queued_payload)
            if not ok:
                raise ActionError(reason)
            forced_target = self.forced_basic_attack_target(actor)
            if forced_target is not None and forced_target.player_id == actor.player_id:
                queued_payload["forced_allied_attack_target_id"] = forced_target.unit_id
            separate = (not queued_payload.get("allow_empty_attack_cell")
                        and any(component.separate_basic_attack_windows for component in actor.iter_components()))
            if separate and queued_payload.get("attack_variant") != "allied_heal":
                queued_payload.pop("attack_cells", None)
                primary = self.effect_recipient(self.get_unit(str(payload.get("target_unit_id") or "")))
                ok, reason = self.attack_target_allowed(actor, primary, payload=queued_payload)
                if not ok:
                    raise ActionError(reason)
            area_cells = self.basic_attack_area_cells_for_payload(actor, queued_payload)
            if area_cells is not None:
                attack_cost = int(queued_payload.get("attack_cost", 1))
                if not actor.can_take_turn_actions(self):
                    raise ActionError("这个单位当前不能行动。")
                block_reason = self.shared_stealth_action_block_reason(actor)
                if block_reason:
                    raise ActionError(block_reason)
                if actor.cannot_attack:
                    raise ActionError(f"{actor.name} 当前不能攻击。")
                for effect in list(self.field_effects):
                    ok, reason = effect.can_attack_target(self, actor, actor)
                    if not ok:
                        raise ActionError(reason)
                if actor.position is not None:
                    queued_payload["declared_source_x"] = actor.position.x
                    queued_payload["declared_source_y"] = actor.position.y
                queued_payload["attack_cells"] = [cell.to_dict() for cell in area_cells]
                affects_allies = all(
                    component.basic_attack_area_affects_allies(self, actor, queued_payload)
                    for component in actor.iter_components()
                )
                forced_allied_target_id = str(queued_payload.get("forced_allied_attack_target_id") or "")
                impact = None
                raw_impact = queued_payload.get("impact_cell")
                if isinstance(raw_impact, dict) and raw_impact.get("x") is not None and raw_impact.get("y") is not None:
                    impact = Position(int(raw_impact["x"]), int(raw_impact["y"]))
                from wujiang.tactical.engine.siege import is_siege_structure, structure_hit_by_impact

                target_units = []
                for unit in self.effect_units_at_cells(area_cells):
                    if (
                        not affects_allies
                        and unit.player_id == actor.player_id
                        and unit.unit_id != forced_allied_target_id
                    ):
                        continue
                    if queued_payload.get("structures_need_direct_hit") and is_siege_structure(unit):
                        if not structure_hit_by_impact(self, unit, impact):
                            continue
                    target_units.append(unit)
                if separate:
                    # The spreading effect explicitly targets enemies; the declared primary may be allied.
                    target_units = [unit for unit in target_units
                                    if unit.player_id != actor.player_id or unit.unit_id == primary.unit_id]
                if not target_units and not queued_payload.get("allow_empty_attack_cell"):
                    raise ActionError("攻击区域内没有有效目标。")
                if separate:
                    target_units.sort(key=lambda unit: (unit.unit_id != primary.unit_id, unit.position.y, unit.position.x, unit.unit_id))
                    segments = [{"target_unit_id": unit.unit_id, "cells": [cell.to_dict() for cell in self.unit_cells(unit) if cell in area_cells]}
                                for unit in target_units]
                    queued_payload["separate_attack_segments"] = segments
                    queued_payload["separate_attack_index"] = 0
                    area_cells = self.payload_positions(segments[0], "cells")
                    target_units = target_units[:1]
                target_ids: list[str] = []
                for unit in target_units:
                    if unit.unit_id not in target_ids:
                        target_ids.append(unit.unit_id)
                return QueuedAction(
                    action_type="attack",
                    actor_id=actor.unit_id,
                    display_name=str(queued_payload.get("attack_name") or "普攻"),
                    speed=1,
                    payload=queued_payload,
                    target_unit_ids=target_ids,
                    target_cells=area_cells,
                    source_player_id=actor.player_id,
                    hostile=True,
                    suppress_logs=actor.is_stealthed() or any(self.get_unit(unit_id).is_stealthed() for unit_id in target_ids),
                )
            target = self.effect_recipient(self.get_unit(payload["target_unit_id"]))
            if queued_payload.get("attack_variant") == "allied_heal" and self.allied_basic_heal_amount(actor, target) <= 0:
                raise ActionError("当前不能对这个单位使用普攻治疗。")
            attack_cost = int(queued_payload.get("attack_cost", 1))
            if not actor.can_take_turn_actions(self):
                raise ActionError("\u8fd9\u4e2a\u5355\u4f4d\u5f53\u524d\u4e0d\u80fd\u884c\u52a8\u3002")
            ignore_stealth = self.attack_ignores_stealth(actor, target)
            ok, reason = self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=queued_payload)
            if not ok:
                raise ActionError(reason)
            queued_payload["declared_source_x"] = actor.position.x
            queued_payload["declared_source_y"] = actor.position.y
            declared_target = self.declared_cell_for_target(actor, target, queued_payload)
            if declared_target is not None and queued_payload.get("x") is None and queued_payload.get("y") is None:
                candidate_cells = sorted(self.unit_cells(target), key=lambda cell: (cell.y, cell.x))
                valid_cells = [cell for cell in candidate_cells if self.attack_target_allowed(
                    actor, target, ignore_stealth=ignore_stealth,
                    payload={**queued_payload, "x": cell.x, "y": cell.y},
                )[0]]
                if valid_cells and declared_target not in valid_cells:
                    declared_target = valid_cells[0]
            if declared_target is not None:
                queued_payload["declared_target_x"] = declared_target.x
                queued_payload["declared_target_y"] = declared_target.y
            ignore_shield, half_ignore_shield = self.attack_shield_flags(actor, target, payload=queued_payload)
            queued_payload["ignore_shield"] = ignore_shield
            queued_payload["half_ignore_shield"] = half_ignore_shield
            queued_payload["ignore_stealth"] = ignore_stealth
            if self.allied_basic_heal_amount(actor, target) > 0:
                queued_payload["allow_allied_attack_target"] = True
                queued_payload["allied_heal_attack"] = True
            elif forced_target is not None and target.unit_id == forced_target.unit_id and target.player_id == actor.player_id:
                queued_payload["allow_allied_attack_target"] = True
            return QueuedAction(
                action_type="attack",
                actor_id=actor.unit_id,
                display_name=str(queued_payload.get("attack_name") or "普攻"),
                speed=1,
                payload=queued_payload,
                target_unit_ids=[target.unit_id],
                target_cells=self.unit_cells(target),
                source_player_id=actor.player_id,
                hostile=target.player_id != actor.player_id,
                suppress_logs=actor.is_stealthed() or target.is_stealthed(),
            )
        if action_type == "skill":
            skill = actor.get_skill(payload["skill_code"])
            ok, reason = skill.can_react_with_payload(self, actor, reaction_to, payload) if reaction_to is not None else skill.can_use(self, actor, payload)
            if not ok:
                raise ActionError(reason)
            wrapped = skill.build_wrapped_action(self, actor, payload)
            if wrapped is not None:
                return wrapped
            queued_payload.update(skill.queued_payload_metadata(self, actor, payload))
            queued_payload["resolves_from_current_position"] = skill.resolves_from_current_position
            if actor.position is not None:
                queued_payload["declared_source_x"] = actor.position.x
                queued_payload["declared_source_y"] = actor.position.y
            queued_payload["ignore_shield"] = skill.ignores_shield_for_payload(self, actor, payload) or any(
                component.all_skills_pierce_shields for component in actor.iter_components()
            )
            queued_payload["half_ignore_shield"] = skill.half_ignores_shield_for_payload(self, actor, payload)
            queued_payload["ignore_stealth"] = skill.ignores_stealth_for_payload(self, actor, payload)
            queued_payload["cannot_evade"] = skill.cannot_evade_for_payload(self, actor, payload)
            if skill.ignores_magic_immunity_for_payload(self, actor, payload):
                queued_payload["ignore_magic_immunity"] = True
            declared_targets = self.payload_target_unit_ids(payload)
            if skill.target_mode in {"ally", "enemy", "unit"} and skill.requires_direct_unit_target_line:
                for target_id in declared_targets:
                    target = self.get_unit(target_id)
                    self.require_selectable_unit(
                        target,
                        actor=actor,
                        action_name=skill.name,
                        ignore_stealth=queued_payload["ignore_stealth"],
                    )
                    self.require_unit_target_in_range_and_line(
                        actor,
                        target,
                        skill.direct_unit_target_range(self, actor, payload),
                        action_name=skill.name,
                    )
            elif payload.get("target_unit_id"):
                target = self.get_unit(payload["target_unit_id"])
                self.require_selectable_unit(
                    target,
                    actor=actor,
                    action_name=skill.name,
                    ignore_stealth=queued_payload["ignore_stealth"],
                )
            target_units = [
                unit
                for unit in skill.get_target_units_for_payload(self, actor, queued_payload)
                if unit.alive
            ]
            target_cells = list(skill.get_target_cells_for_payload(self, actor, queued_payload))
            targets: list[str] = []
            for unit in self.effect_units(
                [*target_units, *([] if skill.target_cells_are_geometry_only else self.units_at_cells(target_cells))],
                ignore=actor if skill.excludes_caster_from_effect else None,
            ):
                if skill.excludes_allies_from_effect and unit.player_id == actor.player_id:
                    continue
                if unit.unit_id not in targets:
                    targets.append(unit.unit_id)
            if declared_targets:
                target = self.get_unit(declared_targets[0])
                declared_target = self.declared_cell_for_target(actor, target, payload)
                if declared_target is not None:
                    queued_payload["declared_target_x"] = declared_target.x
                    queued_payload["declared_target_y"] = declared_target.y
            hostile = any(self.get_unit(unit_id).player_id != actor.player_id for unit_id in targets)
            return QueuedAction(
                action_type="skill",
                actor_id=actor.unit_id,
                display_name=skill.name,
                speed=skill.chain_speed,
                payload=queued_payload,
                target_unit_ids=targets,
                target_cells=target_cells,
                source_player_id=actor.player_id,
                hostile=hostile,
                suppress_logs=actor.is_stealthed() or any(self.get_unit(unit_id).is_stealthed() for unit_id in targets),
            )
        raise ActionError("未知动作类型。")


    def source_action_for_reaction(self, queued_action: QueuedAction) -> QueuedAction:
        from wujiang.tactical.engine.core import (
            Position,
            QueuedAction,
        )
        payload = queued_action.payload
        source_cells = [
            Position(int(cell["x"]), int(cell["y"]))
            for cell in payload.get("source_target_cells", [])
        ]
        return QueuedAction(
            action_type=payload.get("source_action_type", "attack"),
            actor_id=payload["source_actor_id"],
            display_name=payload.get("source_display_name", ""),
            speed=int(payload.get("source_speed", 1)),
            payload=dict(payload.get("source_payload", {})),
            target_unit_ids=list(payload.get("source_target_unit_ids", [])),
            target_cells=source_cells,
            source_player_id=payload.get("source_player_id"),
            hostile=bool(payload.get("source_hostile", True)),
            reaction_source_id=queued_action.reaction_source_id,
        )


    def queued_action_effect_summary(self, queued_action: QueuedAction) -> str:
        from wujiang.tactical.engine.core import (
            ActionError,
        )
        actor = self.units.get(queued_action.actor_id)
        payload = queued_action.payload
        parts: list[str] = []

        if queued_action.action_type == "move":
            parts.append(self.format_action_effect_summary(queued_action.display_name, "仅改变站位，不会直接造成伤害或附加效果。"))
        elif queued_action.action_type == "attack":
            attack_power = self.basic_attack_preview_power(actor, payload) if actor is not None else 0
            attack_cost = int(payload.get("attack_cost", 1))
            parts.append(
                self.format_action_effect_summary(
                    queued_action.display_name,
                    str(payload.get("attack_effect_summary") or f"对原声明格进行一次普攻，按攻 {self.format_summary_number(attack_power)} 结算伤害。"),
                )
            )
            if payload.get("separate_attack_segments"):
                parts.append(f"逐目标独立连锁，第 {int(payload.get('separate_attack_index', 0)) + 1}/{len(payload['separate_attack_segments'])} 段；整次只消耗一次普攻。")
            if attack_cost > 1:
                parts.append(f"这次普攻会占用 {attack_cost} 次普攻次数。")
            if actor is not None:
                parts.extend(self.basic_attack_effect_notes(actor, payload))
        elif queued_action.action_type == "skill":
            if payload.get("skill_code") == "gravity_field" and payload.get("gravity_coin_values"):
                parts.append(f"公开硬币结果 {payload['gravity_coin_values']}，冻结范围边长 {payload['gravity_side']}。")
            if payload.get("skill_code") == "deadly_bow":
                parts.append(f"声明点数攻值 {self.format_summary_number(float(payload.get('deadly_bow_points', 0)))}；已消耗全部魔力点，按原5格结算。")
            if actor is not None and payload.get("skill_code"):
                try:
                    skill = actor.get_skill(str(payload["skill_code"]))
                    if skill.description:
                        parts.append(self.format_action_effect_summary(queued_action.display_name, skill.description))
                except ActionError:
                    pass
            if not parts:
                parts.append(self.format_action_effect_summary(queued_action.display_name, "会按原声明继续结算。"))
        elif queued_action.action_type == "skill_effect":
            effect_code = str(payload.get("effect_code") or "")
            if queued_action.description:
                parts.append(self.format_action_effect_summary(queued_action.display_name, queued_action.description))
            elif effect_code == "banish":
                turns = float(payload.get("banish_turns", 0))
                rounds = self.format_summary_number(turns / 2) if turns else "0"
                parts.append(
                    self.format_action_effect_summary(
                        queued_action.display_name,
                        f"若原声明格上的目标仍有效，则使其消失 {rounds}轮。",
                    )
                )
            elif effect_code == "area_damage":
                attack_power = self.format_summary_number(float(payload.get("attack_power", 0)))
                parts.append(
                    self.format_action_effect_summary(
                        queued_action.display_name,
                        f"对原声明范围结算一次范围伤害，伤害值为 {attack_power}。",
                    )
                )
            else:
                parts.append(self.format_action_effect_summary(queued_action.display_name, "后续效果会继续结算。"))
        else:
            parts.append(self.format_action_effect_summary(queued_action.display_name, "会继续结算。"))

        if payload.get("ignore_shield"):
            parts.append("破魔")
        if payload.get("piercing_followup"):
            parts.append("仅后续附效破魔")
        if payload.get("half_ignore_shield"):
            parts.append("半破魔")
        if payload.get("ignore_magic_immunity"):
            parts.append("无视魔免")
        if payload.get("cannot_evade"):
            parts.append("无法回避")

        target_names = [
            self.units[unit_id].name
            for unit_id in queued_action.target_unit_ids
            if unit_id in self.units and self.units[unit_id].alive
        ]
        if target_names:
            parts.append(f"影响单位：{'、'.join(target_names)}。")

        if queued_action.target_cells:
            cell_labels = "、".join(f"({cell.x}, {cell.y})" for cell in queued_action.target_cells)
            parts.append(f"原声明格：{cell_labels}。")
        if payload.get("nuclear_rush_declared") and isinstance(payload.get("nuclear_rush_landing"), dict):
            landing = payload["nuclear_rush_landing"]
            parts.append(f"攻击后尝试移动至声明的第5格 ({landing['x']}, {landing['y']})；无法落位仍完成攻击。")

        ordered: list[str] = []
        for part in parts:
            cleaned = str(part).strip()
            if cleaned and cleaned not in ordered:
                ordered.append(cleaned)
        return " ".join(ordered)


