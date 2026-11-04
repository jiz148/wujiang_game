"""Battle BattlePresentationMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class BattlePresentationMixin:
    """A cohesive Battle responsibility with no independent state."""

    def _emit_replay_checkpoint(self, reason: str) -> None:
        if not bool(getattr(self, "fast_ai_simulation", False)):
            return
        if reason == "match_end" and bool(getattr(self, "_replay_match_end_emitted", False)):
            return
        hook = getattr(self, "on_replay_checkpoint", None)
        if not callable(hook):
            return
        if reason == "match_end":
            self._replay_match_end_emitted = True
        hook(reason)


    def log(self, message: str) -> None:
        if self._log_suppression_depth > 0:
            return
        self.logs.append(message)
        self.logs = self.logs[-120:]


    def log_public_event(
        self,
        message: str,
        *,
        source: Unit | None = None,
        target: Unit | None = None,
    ) -> None:
        if target is not None and target.is_stealthed():
            return
        public_message = message
        if source is not None and source.is_stealthed():
            public_message = public_message.replace(source.name, "有单位")
        self.logs.append(public_message)
        self.logs = self.logs[-120:]


    def source_cell_from_payload(
        self,
        actor: Unit | None,
        payload: Optional[dict[str, Any]] = None,
    ) -> Position | None:
        from wujiang.tactical.engine.core import (
            Position,
        )
        if payload and payload.get("declared_source_x") is not None and payload.get("declared_source_y") is not None:
            return Position(int(payload["declared_source_x"]), int(payload["declared_source_y"]))
        if actor is not None and actor.position is not None:
            return actor.position
        return None


    def record_visual_event(
        self,
        *,
        kind: Literal["attack", "skill", "defense"],
        display_name: str,
        actor: Unit | None = None,
        action_type: str = "",
        action_code: str = "",
        target_unit_ids: Optional[list[str]] = None,
        target_cells: Optional[list[Position]] = None,
        source_cell: Position | None = None,
        defense_reason: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        from wujiang.tactical.engine.core import (
            VisualEvent,
        )
        if bool(getattr(self, "fast_ai_simulation", False)) and (
            not bool(getattr(self, "record_live_visual_events", False))
            or bool(getattr(self, "_forecasting_action", False))
        ):
            return
        payload = dict(metadata or {})
        wave = str(getattr(self, "army_strike_wave", "") or "")
        if wave and "army_strike_wave" not in payload:
            payload["army_strike_wave"] = wave
        event = VisualEvent(
            event_id=self._next_visual_event_id,
            kind=kind,
            display_name=display_name,
            actor_id=actor.unit_id if actor is not None else None,
            actor_player_id=actor.player_id if actor is not None else None,
            action_type=action_type,
            action_code=action_code,
            target_unit_ids=list(target_unit_ids or []),
            target_cells=list(target_cells or []),
            source_cell=source_cell,
            defense_reason=defense_reason,
            metadata=payload,
        )
        self._next_visual_event_id += 1
        self.visual_events.append(event)
        self.visual_events = self.visual_events[-40:]


    def _find_summary_unit(self, unit_id: Optional[str]) -> Optional[Unit]:
        if not unit_id:
            return None
        unit = self.units.get(unit_id)
        if unit is not None:
            return unit
        return next((item for item in self.destroyed_units if item.unit_id == unit_id), None)


    def _summary_root_unit(self, unit: Optional[Unit]) -> Optional[Unit]:
        current = unit
        seen: set[str] = set()
        while current is not None and (current.is_summon or current.is_clone):
            if current.unit_id in seen:
                break
            seen.add(current.unit_id)
            parent_id = current.mount_owner_id or current.summoner_id
            parent = self._find_summary_unit(parent_id)
            if parent is None:
                break
            current = parent
        return current


    def _combat_stat_for(self, unit: Optional[Unit]) -> Optional[dict[str, Any]]:
        root = self._summary_root_unit(unit)
        if root is None:
            return None
        existing = self.combat_stats.get(root.unit_id)
        if existing is not None:
            return existing
        entry = {
            "unit_id": root.unit_id,
            "hero_code": str(getattr(root, "hero_code", "") or ""),
            "name": root.name,
            "player_id": root.player_id,
            "owner_seat_id": getattr(root, "owner_seat_id", None),
            "damage_dealt": 0.0,
            "healing_done": 0.0,
            "damage_taken": 0.0,
            "healing_received": 0.0,
            "kills": 0,
            "deaths": 0,
            "shields_broken": 0,
            "chain_reactions": 0,
            "actions": 0,
        }
        self.combat_stats[root.unit_id] = entry
        return entry


    def _append_summary_event(self, kind: str, **payload: Any) -> dict[str, Any]:
        event = {
            "event_id": self._next_summary_event_id,
            "kind": kind,
            "turn_index": max(1, int(self.completed_turns) + 1),
            **payload,
        }
        self._next_summary_event_id += 1
        self.summary_events.append(event)
        self.summary_events = self.summary_events[-300:]
        return event


    def record_rule_trigger_summary(
        self,
        rule_code: str,
        *,
        actor: Unit,
        target: Unit | None = None,
    ) -> dict[str, Any]:
        """Record a rule trigger with the enclosing declared-action identity."""
        return self._append_summary_event(
            "rule_trigger",
            rule_code=rule_code,
            action_resolution_token=self.current_action_resolution_token,
            actor_unit_id=actor.unit_id,
            actor_name=actor.name,
            target_unit_id=target.unit_id if target is not None else None,
            target_name=target.name if target is not None else None,
        )


    def record_damage_summary(self, ctx: DamageContext, actual_damage: float) -> None:
        amount = round(max(0.0, float(actual_damage)), 4)
        if amount <= 0:
            return
        source = self._summary_root_unit(ctx.source)
        target = self._summary_root_unit(ctx.target)
        source_stat = self._combat_stat_for(source)
        target_stat = self._combat_stat_for(target)
        if source_stat is not None:
            source_stat["damage_dealt"] = round(float(source_stat["damage_dealt"]) + amount, 4)
        if target_stat is not None:
            target_stat["damage_taken"] = round(float(target_stat["damage_taken"]) + amount, 4)
        self._append_summary_event(
            "damage",
            actor_unit_id=source.unit_id if source is not None else None,
            actor_name=source.name if source is not None else "场地效果",
            actor_player_id=source.player_id if source is not None else None,
            target_unit_id=target.unit_id if target is not None else ctx.target.unit_id,
            target_name=target.name if target is not None else ctx.target.name,
            target_player_id=target.player_id if target is not None else ctx.target.player_id,
            action_name=ctx.action_name,
            amount=amount,
        )


    def record_heal_summary(self, ctx: HealContext, actual_healing: float) -> None:
        amount = round(max(0.0, float(actual_healing)), 4)
        if amount <= 0:
            return
        source = self._summary_root_unit(ctx.source or ctx.target)
        target = self._summary_root_unit(ctx.target)
        source_stat = self._combat_stat_for(source)
        target_stat = self._combat_stat_for(target)
        if source_stat is not None:
            source_stat["healing_done"] = round(float(source_stat["healing_done"]) + amount, 4)
        if target_stat is not None:
            target_stat["healing_received"] = round(float(target_stat["healing_received"]) + amount, 4)
        self._append_summary_event(
            "healing",
            actor_unit_id=source.unit_id if source is not None else None,
            actor_name=source.name if source is not None else "场地效果",
            actor_player_id=source.player_id if source is not None else None,
            target_unit_id=target.unit_id if target is not None else ctx.target.unit_id,
            target_name=target.name if target is not None else ctx.target.name,
            target_player_id=target.player_id if target is not None else ctx.target.player_id,
            action_name=ctx.action_name,
            amount=amount,
        )


    def record_shield_break_summary(self, source: Optional[Unit], target: Unit, action_name: str) -> None:
        root_source = self._summary_root_unit(source)
        source_stat = self._combat_stat_for(root_source)
        if source_stat is not None:
            source_stat["shields_broken"] = int(source_stat["shields_broken"]) + 1
        self._append_summary_event(
            "shield_break",
            actor_unit_id=root_source.unit_id if root_source is not None else None,
            actor_name=root_source.name if root_source is not None else "场地效果",
            actor_player_id=root_source.player_id if root_source is not None else None,
            target_name=self._summary_root_unit(target).name if self._summary_root_unit(target) is not None else target.name,
            action_name=action_name,
            amount=1,
        )


    def record_defeat_summary(self, source: Optional[Unit], target: Unit, action_name: str) -> None:
        if target.unit_id in self._summary_defeated_unit_ids or target.is_summon or target.is_clone:
            return
        self._summary_defeated_unit_ids.add(target.unit_id)
        root_source = self._summary_root_unit(source)
        root_target = self._summary_root_unit(target)
        source_stat = self._combat_stat_for(root_source)
        target_stat = self._combat_stat_for(root_target)
        if source_stat is not None:
            source_stat["kills"] = int(source_stat["kills"]) + 1
        if target_stat is not None:
            target_stat["deaths"] = int(target_stat["deaths"]) + 1
        self._append_summary_event(
            "defeat",
            actor_unit_id=root_source.unit_id if root_source is not None else None,
            actor_name=root_source.name if root_source is not None else "场地效果",
            actor_player_id=root_source.player_id if root_source is not None else None,
            target_unit_id=root_target.unit_id if root_target is not None else target.unit_id,
            target_name=root_target.name if root_target is not None else target.name,
            target_player_id=root_target.player_id if root_target is not None else target.player_id,
            action_name=action_name,
            amount=1,
        )


    def record_postgame_action(self, actor: Optional[Unit], payload: dict[str, Any]) -> None:
        stat = self._combat_stat_for(actor)
        if stat is None:
            return
        action_type = str(payload.get("type") or "")
        if action_type not in {"chain_skip", "respawn_select", "end_turn", "damage_choice"}:
            stat["actions"] = int(stat["actions"]) + 1
        if action_type == "chain_react":
            stat["chain_reactions"] = int(stat["chain_reactions"]) + 1


    def combat_summary_entries(self) -> list[dict[str, Any]]:
        for unit in [*self.all_units(), *self.destroyed_units]:
            if not unit.is_summon and not unit.is_clone:
                self._combat_stat_for(unit)
        return [dict(entry) for entry in self.combat_stats.values()]


    def emit_visual_event_for_queued_action(self, actor: Unit, queued_action: QueuedAction) -> None:
        payload = queued_action.payload
        action_code = ""
        kind: Literal["attack", "skill", "defense"]
        target_unit_ids = list(queued_action.target_unit_ids)
        target_cells = list(queued_action.target_cells)

        if queued_action.action_type == "attack":
            kind = "attack"
            action_code = str(payload.get("attack_code") or "attack")
        elif queued_action.action_type == "skill":
            kind = "skill"
            action_code = str(payload.get("skill_code") or "")
        elif queued_action.action_type == "skill_effect":
            kind = "skill"
            action_code = str(payload.get("effect_code") or "")
            if not target_cells:
                target_cells = self.payload_positions(payload, "cells")
        elif queued_action.action_type == "reaction_skill":
            kind = "skill"
            action_code = str(payload.get("action_code") or "")
        elif queued_action.action_type == "reaction_action" and str(payload.get("action_code") or "") == "block":
            kind = "defense"
            action_code = "block"
            target_unit_ids = [actor.unit_id]
            target_cells = self.unit_cells(actor)
        elif queued_action.action_type == "reaction_action" and str(payload.get("action_code") or "") == "counter":
            kind = "attack"
            action_code = "counter"
            source = self.units.get(queued_action.reaction_source_id or "")
            target_unit_ids = [source.unit_id] if source is not None else []
            target_cells = self.unit_cells(source) if source is not None else []
        else:
            return

        self.record_visual_event(
            kind=kind,
            display_name=queued_action.display_name,
            actor=actor,
            action_type=queued_action.action_type,
            action_code=action_code,
            target_unit_ids=target_unit_ids,
            target_cells=target_cells,
            source_cell=self.source_cell_from_payload(actor, payload),
            metadata={
                "segment_index": payload.get("segment_index"),
                "segment_count": payload.get("segment_count"),
            },
        )


    def emit_defense_visual_event(
        self,
        *,
        source: Unit | None,
        target: Unit,
        action_name: str,
        defense_reason: str,
    ) -> None:
        self.record_visual_event(
            kind="defense",
            display_name=action_name,
            actor=source,
            action_type="defense",
            action_code=defense_reason,
            target_unit_ids=[target.unit_id],
            target_cells=self.unit_cells(target),
            source_cell=self.source_cell_from_payload(source),
            defense_reason=defense_reason,
        )


    def suppress_logs_for_stealth(self, *units: Unit | None):
        from wujiang.tactical.engine.core import (
            nullcontext,
        )
        if any(unit is not None and unit.is_stealthed() for unit in units):
            return self.suppress_logs()
        return nullcontext()


    def queued_action_hides_logs(self, queued_action: QueuedAction) -> bool:
        if queued_action.suppress_logs:
            return True
        actor = self.units.get(queued_action.actor_id)
        if actor is not None and actor.is_stealthed():
            return True
        for unit_id in queued_action.target_unit_ids:
            target = self.units.get(unit_id)
            if target is not None and target.is_stealthed():
                return True
        return False


    def record_skill_effect(self, target: Unit) -> None:
        token = self._current_action_resolution_token
        if token is not None:
            self._skill_effect_receipts.setdefault(token, set()).add(target.unit_id)


    def format_summary_number(self, value: float) -> str:
        rounded = round(float(value), 4)
        if int(rounded) == rounded:
            return str(int(rounded))
        return f"{rounded}".rstrip("0").rstrip(".")


    def format_action_effect_summary(self, action_name: str, description: str) -> str:
        cleaned_name = str(action_name or "动作").strip()
        cleaned_description = str(description or "").strip()
        if not cleaned_description:
            return f"【{cleaned_name}】"
        if cleaned_description.startswith(f"【{cleaned_name}】"):
            return cleaned_description
        return f"【{cleaned_name}】：{cleaned_description}"


    def basic_attack_effect_notes(self, actor: Unit, payload: Optional[dict[str, Any]] = None) -> list[str]:
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        notes: list[str] = []
        attack_note = str(resolved_payload.get("attack_note") or "").strip()
        if attack_note:
            notes.append(self.format_action_effect_summary(str(resolved_payload.get("attack_name") or "普攻"), attack_note))
        for component in actor.iter_components():
            if getattr(component, "bonus_attack", 0.0) or getattr(component, "ignore_shield", False):
                notes.append(self.format_action_effect_summary(component.name, component.description))
            elif component.name == "压制射击":
                notes.append(self.format_action_effect_summary(component.name, component.description))
        return notes


    def action_snapshot_for(self, unit: Unit) -> dict[str, Any]:
        move_targets = [
            pos.to_dict()
            for pos in self.reachable_positions(
                unit,
                max_distance=unit.remaining_normal_move_distance(self),
                use_movement_cost=True,
            )
        ]
        forced_movement = False
        for component in unit.iter_components():
            forced_paths = component.forced_normal_move_paths(self, unit)
            if forced_paths is not None:
                forced_movement = True
                move_targets = [route[-1].to_dict() for route in forced_paths]
        can_normal_move = (
            unit.can_take_turn_actions(self)
            and unit.remaining_normal_move_distance(self) > 0
            and not unit.cannot_move
            and not unit.cannot_normal_move
            and bool(move_targets)
        )
        attack_targets: list[str] = []
        attack_cells: list[dict[str, int]] = []
        actions = []
        actions.append(
            {
                "code": "move",
                "name": "移动",
                "kind": "move",
                "timing": "active",
                "chain_speed": 1,
                "description": "普通移动。",
                "available": can_normal_move,
                "preview": {
                    "cells": move_targets,
                    "target_unit_ids": [],
                    "requires_target": True,
                    "selection": {
                        "mode": "forced_destination" if forced_movement else "move_path",
                        "max_steps": unit.remaining_normal_move_distance(self),
                    },
                },
            }
        )
        attack_available = (
            unit.can_take_turn_actions(self)
            and not unit.cannot_attack
            and not self.shared_stealth_action_block_reason(unit)
        )
        seen_attack_targets: set[str] = set()
        seen_attack_cells: set[tuple[int, int]] = set()
        for spec in self.basic_attack_action_specs(unit):
            attack_payload = dict(spec.get("attack_payload") or {})
            resolved_payload = self.resolved_basic_attack_payload(unit, attack_payload)
            preview = self.basic_attack_preview_for_payload(unit, resolved_payload)
            action_available = attack_available and self.basic_attack_resource_allowed(unit, resolved_payload)[0]
            for unit_id in preview.get("target_unit_ids", []):
                if unit_id in seen_attack_targets:
                    continue
                seen_attack_targets.add(unit_id)
                attack_targets.append(unit_id)
            for cell in preview.get("cells", []):
                if not isinstance(cell, dict) or cell.get("x") is None or cell.get("y") is None:
                    continue
                key = (int(cell["x"]), int(cell["y"]))
                if key in seen_attack_cells:
                    continue
                seen_attack_cells.add(key)
                attack_cells.append({"x": key[0], "y": key[1]})
            action_entry = {
                "code": str(spec.get("code") or "attack"),
                "name": str(spec.get("name") or "普攻"),
                "kind": "attack",
                "timing": "active",
                "chain_speed": 1,
                "description": (str(spec.get("description") or "普通攻击。") + (" 可选择己方单位（含自身）治疗1/4，消耗一次普攻。" if preview.get("allied_heal_target_ids") and resolved_payload.get("attack_variant") != "allied_heal" else "")),
                "available": action_available,
                "preview": preview,
            }
            if attack_payload:
                action_entry["attack_payload"] = attack_payload
            if spec.get("is_attack_variant"):
                action_entry["is_attack_variant"] = True
            actions.append(action_entry)
        for skill in unit.action_skills():
            data = skill.to_public_dict(self)
            data["available"] = (
                unit.can_take_turn_actions(self) and skill.can_use(self, unit, {})[0]
                if skill.timing == "active"
                else skill.can_use(self, unit, {})[0]
                if skill.timing == "instant"
                else False
            )
            data["kind"] = "skill"
            data["preview"] = self.filter_preview_targets(
                unit,
                skill.preview(self, unit),
                ignore_stealth=skill.ignores_stealth_for_payload(self, unit, {}),
                replace_cells=data["target_mode"] in {"ally", "enemy", "unit"},
                require_line_targeting=data["target_mode"] in {"ally", "enemy", "unit"}
                and skill.requires_direct_unit_target_line,
                line_target_range=skill.direct_unit_target_range(self, unit, {}),
            )
            actions.append(data)
        return {
            "move_targets": move_targets,
            "attack_targets": attack_targets,
            "skills": [skill.to_public_dict(self) for skill in unit.action_skills()],
            "actions": actions,
            "can_move": can_normal_move,
            "attacks_left": max(unit.attack_actions_per_turn() - unit.attacks_used, 0),
        }


    def reaction_snapshot_for(self, unit: Unit) -> dict[str, Any]:
        if self.pending_chain is None:
            return {"actions": []}
        options = self.pending_chain.options_by_unit.get(unit.unit_id, [])
        return {"actions": [option.to_public_dict() for option in options]}


    def to_public_dict(self) -> dict[str, Any]:
        from wujiang.tactical.engine.core import (
            army_public_state,
        )
        respawn_prompt = self.current_respawn_prompt()
        damage_prompt = self.pending_damage_choice
        current_turn_unit = self.current_turn_unit()
        next_turn_unit = self.peek_next_turn_unit()
        army_turn = self.is_army_turn()
        return {
            "board": {
                "width": self.width,
                "height": self.height,
                "terrain": [
                    {"x": x, "y": y, "kind": "wall"}
                    for x, y in sorted(self.blocked_cells)
                ],
            },
            "active_player": self.active_player,
            "active_turn_unit_id": current_turn_unit.unit_id if current_turn_unit is not None else self.current_turn_slot_unit_id(),
            "control_handoff_unit_id": (
                next((unit.unit_id for unit in self.current_turn_bundle_units()), None)
                if current_turn_unit is not None and current_turn_unit.player_id != self.active_player else None
            ),
            "active_turn_unit_name": (
                current_turn_unit.name
                if current_turn_unit is not None
                else ("军队" if army_turn else None)
            ),
            "next_turn_unit_id": next_turn_unit.unit_id if next_turn_unit is not None else None,
            "next_turn_unit_name": next_turn_unit.name if next_turn_unit is not None else None,
            "next_turn_player_id": next_turn_unit.player_id if next_turn_unit is not None else None,
            "input_player": (
                self.get_unit(damage_prompt["unit_id"]).player_id
                if damage_prompt is not None
                else respawn_prompt.player_id
                if respawn_prompt is not None
                else (self.pending_chain.reactive_player_id if self.pending_chain else self.active_player)
            ),
            "turn_number": self.turn_number,
            "round_number": self.round_number,
            "completed_turns": self.completed_turns,
            "turn_timeout_limit": int(getattr(self, "turn_timeout_limit", 0) or 0),
            "turn_timeout_winner": int(getattr(self, "turn_timeout_winner", 2) or 2),
            "turn_order_unit_ids": list(self.turn_order_unit_ids),
            "winner": self.winner,
            "win_reason_code": self.win_reason_code if self.winner is not None else "",
            "win_reason_text": self.win_reason_text if self.winner is not None else "",
            "summary_event_count": self._next_summary_event_id - 1,
            "damage_rule": self.damage_rule.name,
            "units": [unit.to_public_dict(self) for unit in self.all_units()],
            "destroyed_units": [unit.to_public_dict(self) for unit in self.destroyed_units],
            "field_effects": [effect.to_public_dict(self) for effect in self.field_effects],
            "pending_chain": self.pending_chain.to_public_dict(self) if self.pending_chain else None,
            "pending_respawn": respawn_prompt.to_public_dict() if respawn_prompt else None,
            "hell_dragon_returns": [
                {"unit_id": unit_id, "player_id": unit.player_id,
                 "origins": [{"x": cell.x, "y": cell.y} for cell in origins],
                 "options": [{"x": cell.x, "y": cell.y} for cell in self.hell_dragon_return_options_for(unit)]}
                for unit_id, origins in self.hell_dragon_return_cells.items()
                if (unit := next((body for body in self.destroyed_units if body.unit_id == unit_id), None)) is not None
            ],
            "pending_damage_choice": (
                {**{key: damage_prompt[key] for key in ("unit_id", "action_name", "damage", "event_index", "stats")},
                 "kind": damage_prompt.get("kind", "stat"), "options": damage_prompt.get("options", []),
                 "protected_unit_id": damage_prompt.get("protected_unit_id")}
                if damage_prompt is not None else None
            ),
            "logs": self.logs,
            "visual_events": [event.to_public_dict() for event in self.visual_events],
            "is_army_turn": army_turn,
            "army": army_public_state(self),
        }


