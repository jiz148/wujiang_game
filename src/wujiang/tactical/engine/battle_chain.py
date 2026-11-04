"""Battle BattleChainMixin methods, retained under the Battle public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleChainMixin:
    """A cohesive Battle responsibility with no independent state."""

    def queue_followup_action(self, queued_action: QueuedAction) -> None:
        self.pending_followup_actions.append(queued_action)


    def build_skill_effect_action(
        self,
        *,
        actor: Unit,
        display_name: str,
        effect_code: str,
        payload: Optional[dict[str, Any]] = None,
        target_units: Optional[list[Unit]] = None,
        target_cells: Optional[list[Position]] = None,
        include_cell_units: bool = True,
        speed: int = 1,
        hostile: Optional[bool] = None,
        description: str = "",
        suppress_logs: Optional[bool] = None,
        effect_resolver: Optional[Callable[["Battle", Unit, QueuedAction], None]] = None,
        segment_index: Optional[int] = None,
        segment_count: Optional[int] = None,
    ) -> QueuedAction:
        from wujiang.tactical.engine.core import (
            QueuedAction,
        )
        cells = list(target_cells or [])
        resolved_payload = dict(payload or {})
        resolved_payload["effect_code"] = effect_code
        if self.resolving_action is not None:
            resolved_payload.setdefault("declaration_id", self.resolving_action.payload.get("declaration_id"))
        if actor.position is not None:
            resolved_payload.setdefault("declared_source_x", actor.position.x)
            resolved_payload.setdefault("declared_source_y", actor.position.y)
        if segment_index is not None:
            resolved_payload["segment_index"] = segment_index
        if segment_count is not None:
            resolved_payload["segment_count"] = segment_count
        cell_units = self.effect_units_at_cells(cells) if include_cell_units else []
        targets = self.effect_units([*(target_units or []), *cell_units])
        resolved_hostile = hostile if hostile is not None else any(unit.player_id != actor.player_id for unit in targets)
        resolved_suppress_logs = (
            suppress_logs
            if suppress_logs is not None
            else actor.is_stealthed() or any(unit.is_stealthed() for unit in targets)
        )
        return QueuedAction(
            action_type="skill_effect",
            actor_id=actor.unit_id,
            display_name=display_name,
            speed=speed,
            payload=resolved_payload,
            description=description,
            target_unit_ids=[unit.unit_id for unit in targets],
            target_cells=cells,
            source_player_id=actor.player_id,
            hostile=resolved_hostile,
            suppress_logs=resolved_suppress_logs,
            effect_resolver=effect_resolver,
        )


    def queue_skill_effect_action(self, **kwargs: Any) -> None:
        self.queue_followup_action(self.build_skill_effect_action(**kwargs))


    def queue_area_damage_effect(
        self,
        *,
        actor: Unit,
        display_name: str,
        cells: list[Position],
        attack_power: float,
        speed: int = 1,
        raw_damage: Optional[float] = None,
        ignore_shield: bool = False,
        half_ignore_shield: bool = False,
        ignore_magic_immunity: bool = False,
        cannot_evade: bool = False,
        tags: Optional[set[str]] = None,
        exclude_actor: bool = False,
        refresh_cell_targets: bool = False,
        segment_index: Optional[int] = None,
        segment_count: Optional[int] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "cells": [cell.to_dict() for cell in cells],
            "attack_power": attack_power,
            "ignore_shield": ignore_shield,
            "half_ignore_shield": half_ignore_shield,
            "ignore_magic_immunity": ignore_magic_immunity,
            "cannot_evade": cannot_evade,
            "tags": sorted(tags or set()),
            "exclude_actor": exclude_actor,
            "refresh_cell_targets": refresh_cell_targets,
        }
        if raw_damage is not None:
            payload["raw_damage"] = raw_damage
        self.queue_skill_effect_action(
            actor=actor,
            display_name=display_name,
            effect_code="area_damage",
            payload=payload,
            target_cells=list(cells),
            speed=speed,
            segment_index=segment_index,
            segment_count=segment_count,
        )


    def advance_followup_actions(self) -> None:
        if self._draining_followup_actions:
            return
        self._draining_followup_actions = True
        try:
            while self.pending_chain is None and self.pending_followup_actions:
                self.present_reaction_window_or_resolve(self.pending_followup_actions.popleft())
        finally:
            self._draining_followup_actions = False
        if (self._pending_end_turn_after_followups and self.pending_chain is None
                and not self.pending_followup_actions):
            self._pending_end_turn_after_followups = False
            self.finish_any_turn_end_choices()
        if (getattr(self, "_pending_turn_end_after_effects_player_id", None) is not None
                and self.pending_chain is None and not self.pending_followup_actions):
            ending_player = self._pending_turn_end_after_effects_player_id
            self._pending_turn_end_after_effects_player_id = None
            self.complete_turn_end_after_effects(ending_player)
        if self.pending_chain is None and not self.pending_followup_actions:
            self.check_win_condition()


    def present_reaction_window_or_resolve(self, queued_action: QueuedAction) -> None:
        from wujiang.tactical.engine.core import (
            nullcontext,
        )
        if queued_action.payload.get("target_must_occupy_declared_cell"):
            actor = self.units.get(queued_action.actor_id)
            queued_action.target_unit_ids = [unit_id for unit_id in queued_action.target_unit_ids
                if (unit := self.units.get(unit_id)) is not None and unit.alive and not unit.banished
                and any(cell in self.unit_cells(unit) for cell in queued_action.target_cells)]
            queued_action.hostile = actor is not None and any(
                self.units[unit_id].player_id != actor.player_id for unit_id in queued_action.target_unit_ids)
        if queued_action.payload.get("refresh_cell_targets"):
            actor = self.units.get(queued_action.actor_id)
            if actor is None and queued_action.payload.get("after_death_effect"):
                actor = next((unit for unit in self.destroyed_units if unit.unit_id == queued_action.actor_id), None)
            targets = self.effect_units_at_cells(queued_action.target_cells)
            queued_action.target_unit_ids = [unit.unit_id for unit in targets]
            queued_action.hostile = actor is not None and any(unit.player_id != actor.player_id for unit in targets)
        if queued_action.payload.get("separate_attack_segments"):
            actor = self.units.get(queued_action.actor_id)
            if actor is None or not actor.alive or actor.banished:
                self.resolve_queued_action(queued_action)
                return
            state = self._separate_attack_sequences.get(queued_action.payload["declaration_id"], {})
            queued_action.target_unit_ids = [unit.unit_id for unit in self.effect_units_at_cells(queued_action.target_cells)
                                            if (unit.player_id != actor.player_id or
                                                int(queued_action.payload.get("separate_attack_index", 0)) == 0)
                                            and unit.unit_id not in state.get("seen", ())]
        with self.suppress_logs() if self.queued_action_hides_logs(queued_action) else nullcontext():
            window = self.create_reaction_window(queued_action)
            if window is None:
                if queued_action.speed < 3 and queued_action.hostile and queued_action.target_unit_ids:
                    target_names = "、".join(unit.name for unit_id in queued_action.target_unit_ids
                                            if (unit := self.units.get(unit_id)) is not None and unit.alive)
                    if target_names:
                        self.log(f"{target_names} 没有可用的更快连锁，【{queued_action.display_name}】直接结算。")
                self.resolve_queued_action(queued_action)
                self.advance_followup_actions()
                return
            self.pending_chain = window
            reactor = self.pending_chain.current_unit_id()
            self.log(f"等待玩家 {window.reactive_player_id} 的连锁响应。")
            if reactor is not None:
                self.log(f"{self.get_unit(reactor).name} 可以进行连锁。")


    def resolve_skill_effect(self, actor: Unit, queued_action: QueuedAction) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
            DamageContext,
        )
        if queued_action.effect_resolver is not None:
            queued_action.effect_resolver(self, actor, queued_action)
            self.check_win_condition()
            return
        payload = queued_action.payload
        effect_code = payload.get("effect_code")
        if effect_code == "area_damage":
            cells = self.payload_positions(payload, "cells")
            if not cells:
                self.log(f"【{queued_action.display_name}】没有有效结算范围。")
                return
            for unit in self.effect_units_at_cells(cells):
                if payload.get("exclude_actor") and unit.unit_id == actor.unit_id:
                    continue
                self.resolve_damage(
                    DamageContext(
                        source=actor,
                        target=unit,
                        attack_power=float(payload.get("attack_power", 0)),
                        is_skill=True,
                        action_name=queued_action.display_name,
                        ignore_shield=bool(payload.get("ignore_shield")),
                        half_ignore_shield=bool(payload.get("half_ignore_shield")),
                        ignore_magic_immunity=bool(payload.get("ignore_magic_immunity")),
                        cannot_evade=bool(payload.get("cannot_evade")),
                        area_cell_hits=self.unit_hit_count_for_cells(unit, cells),
                        raw_damage=float(payload["raw_damage"]) if payload.get("raw_damage") is not None else None,
                        tags=set(payload.get("tags", [])),
                    )
                )
            self.check_win_condition()
            return
        if effect_code == "pass_through_damage":
            cells = self.payload_positions(payload, "cells")
            crossed_id = payload.get("crossed_unit_id")
            if crossed_id is None:
                recipients = self.effect_units_at_cells(cells)
            else:
                crossed = self.units.get(str(crossed_id))
                recipients = ([crossed] if crossed is not None and
                              any(cell in self.unit_cells(crossed) for cell in cells) else [])
            for unit in recipients:
                self.resolve_damage(DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=float(payload.get("attack_power", 0)),
                    is_skill=False,
                    from_field_effect=True,
                    action_name=queued_action.display_name,
                    tags={"movement", "pass_through_damage"},
                ))
            self.check_win_condition()
            return
        if effect_code == "banish":
            target = self.resolve_declared_target_unit(
                actor,
                payload,
                ignore_stealth=bool(payload.get("ignore_stealth")),
            )
            if target is not None and target.player_id == actor.player_id:
                target = None
            if target is None:
                self.log(f"【{queued_action.display_name}】没有命中有效目标。")
                return
            target_ctx = self.validate_target(
                actor,
                target,
                action_name=queued_action.display_name,
                is_skill=True,
                is_hostile=True,
                ignore_shield=bool(payload.get("ignore_shield")),
                half_ignore_shield=bool(payload.get("half_ignore_shield")),
                ignore_magic_immunity=bool(payload.get("ignore_magic_immunity")),
                cannot_evade=bool(payload.get("cannot_evade")),
                tags=set(payload.get("tags", [])),
            )
            if target_ctx.cancelled:
                self.log(target_ctx.reason)
                return
            turns = int(payload.get("banish_turns", 0))
            self.banish_unit(target, turns)
            success_log = payload.get("success_log")
            if success_log:
                self.log(str(success_log).format(actor=actor.name, target=target.name))
            self.check_win_condition()
            return
        raise ActionError("未知技能后续效果。")


    def resolve_separate_attack_segment(self, actor: Unit, queued_action: QueuedAction) -> None:
        """A single declared basic attack, with one reaction window per frozen recipient footprint."""
        from wujiang.tactical.engine.core import (
            replace,
        )
        payload = queued_action.payload
        key = payload["declaration_id"]
        state = self._separate_attack_sequences.get(key)
        if state is None:
            return
        if actor.can_take_turn_actions(self) and not actor.cannot_attack:
            for target in self.effect_units_at_cells(queued_action.target_cells):
                if target.unit_id in state["seen"] or (target.player_id == actor.player_id
                        and int(payload.get("separate_attack_index", 0)) != 0):
                    continue
                state["seen"].add(target.unit_id)
                hit_payload = {**payload, "attack_cells": [], "area_attack": True}
                ctx = self.resolve_attack_damage(actor, target, action_name=queued_action.display_name,
                                                 tags={"area_attack"}, payload=hit_payload)
                if ctx is not None:
                    state["contexts"].append(ctx)
        if getattr(self, "_measuring_attack_success", False):
            actor.notify_basic_attack_finished(self, payload, state["contexts"], missed=not state["contexts"])
            return
        index = int(payload.get("separate_attack_index", 0)) + 1
        segments = payload["separate_attack_segments"]
        if index < len(segments) and actor.alive and not actor.banished and self.winner is None:
            cells = self.payload_positions(segments[index], "cells")
            next_payload = {**payload, "separate_attack_index": index, "segment_index": index + 1,
                            "segment_count": len(segments)}
            next_payload.pop("evaded_unit_ids", None)
            next_payload.pop("completed_evasion_unit_ids", None)
            next_payload.pop("enemy_skill_prevented_attack", None)
            next_payload.pop("passive_attack_preventer_ids", None)
            self.queue_followup_action(replace(queued_action, payload=next_payload, target_cells=cells,
                                              target_unit_ids=[], description=f"扩散普攻第 {index + 1}/{len(segments)} 段；不另消耗普攻次数。"))
        else:
            contexts = state["contexts"]
            actor.consume_attack_attempt_buffs(self)
            actor.notify_basic_attack_finished(self, payload, contexts, missed=not contexts)
            self._separate_attack_sequences.pop(key, None)
            self.check_win_condition()


    def resolve_queued_action(self, queued_action: QueuedAction) -> None:
        previous_token = self._current_action_resolution_token
        previous_action = self.resolving_action
        self.resolving_action = queued_action
        resolution_token = self._next_action_resolution_token
        self._next_action_resolution_token += 1
        self._current_action_resolution_token = resolution_token
        try:
            self._resolve_queued_action(queued_action)
            receipts = self._skill_effect_receipts.pop(resolution_token, set())
            segment_count = int(queued_action.payload.get("segment_count") or 1)
            segment_index = int(queued_action.payload.get("segment_index") or 1)
            declaration_id = str(queued_action.payload.get("declaration_id") or "")
            if declaration_id and segment_count > 1:
                pending_receipts = self._skill_effect_declaration_receipts.setdefault(declaration_id, set())
                pending_receipts.update(receipts)
                if segment_index < segment_count:
                    receipts = set()
                else:
                    receipts = self._skill_effect_declaration_receipts.pop(declaration_id, set())
            for unit_id in sorted(receipts):
                recipient = self.units.get(unit_id)
                if recipient is not None and recipient.alive and not recipient.banished:
                    for component in list(recipient.iter_components()):
                        component.on_skill_effect_finished(self, queued_action)
        finally:
            self._skill_effect_receipts.pop(resolution_token, None)
            # One-effect defenses remain through all hooks, then expire even without a reaction window.
            declaration_id = queued_action.payload.get("declaration_id")
            for unit in {unit.unit_id: unit for unit in [*self.all_units(), *self.destroyed_units]}.values():
                for status in list(unit.statuses):
                    if (getattr(status, "expires_after_action_token", None) == resolution_token
                            or (declaration_id and getattr(status, "expires_after_declaration_id", None) == declaration_id)):
                        unit.remove_status(status, self)
            self._current_action_resolution_token = previous_token
            self.resolving_action = previous_action
            self.sync_linked_units()


    def _resolve_queued_action(self, queued_action: QueuedAction) -> None:
        from wujiang.tactical.engine.core import (
            Position,
            ReactionOption,
            nullcontext,
        )
        with self.suppress_logs() if self.queued_action_hides_logs(queued_action) else nullcontext():
            actor = self.units.get(queued_action.actor_id)
            if actor is None and queued_action.payload.get("after_death_effect"):
                actor = next((unit for unit in self.destroyed_units if unit.unit_id == queued_action.actor_id), None)
            if actor is None or (not actor.alive and not queued_action.payload.get("after_death_effect")) or actor.banished:
                self.stale_queued_action_count += 1
                self._separate_attack_sequences.pop(queued_action.payload.get("declaration_id"), None)
                self.log(f"【{queued_action.display_name}】未能结算，因为行动者已不在战场。")
                return
            if queued_action.action_type in {"attack", "skill"}:
                for observer in list(self.all_units()):
                    for component in list(observer.iter_components()):
                        redirect = getattr(component, "redirect_queued_action", None)
                        if callable(redirect):
                            redirect(self, queued_action)
            self.emit_visual_event_for_queued_action(actor, queued_action)
            payload = queued_action.payload
            if payload.get("action_failed_by_target_redirect"):
                if queued_action.action_type == "attack":
                    actor.attacks_used += int(payload.get("attack_cost", 1))
                    actor.actions_taken_this_turn.append("attack")
                    actor.consume_attack_attempt_buffs(self)
                    actor.notify_basic_attack_finished(self, payload, [], missed=True)
                elif queued_action.action_type == "skill":
                    skill = actor.get_skill(payload["skill_code"])
                    skill.finalize_use(self, actor)
                    actor.actions_taken_this_turn.append(f"skill:{skill.code}")
                self.log_public_event(f"{actor.name} 的【{queued_action.display_name}】改向后范不足，动作失败。", source=actor)
                return
            if queued_action.action_type == "move":
                destination = Position(int(payload["x"]), int(payload["y"]))
                path = self.payload_positions(payload, "path")
                self.move_unit(actor, destination, path=path or None)
                actor.actions_taken_this_turn.append("move")
                return
            if queued_action.action_type == "attack":
                if payload.get("movement_followup_attack") and queued_action.effect_resolver is not None:
                    queued_action.effect_resolver(self, actor, queued_action)
                    return
                if payload.get("separate_attack_segments") and int(payload.get("separate_attack_index", 0)) > 0:
                    self.resolve_separate_attack_segment(actor, queued_action)
                    return
                if not actor.can_take_turn_actions(self) or actor.cannot_attack:
                    payload["basic_attack_cancelled"] = True
                    actor.attacks_used += int(payload.get("attack_cost", 1))
                    actor.actions_taken_this_turn.append("attack")
                    actor.consume_attack_attempt_buffs(self)
                    actor.notify_basic_attack_finished(self, payload, [], missed=True)
                    self.log(f"{actor.name} 的已声明普攻因行动受限而未命中。")
                    return
                if payload.get("action_failed_by_wuchang_mist"):
                    payload["basic_attack_cancelled"] = True
                    attack_cost = int(payload.get("attack_cost", 1))
                    actor.attacks_used += attack_cost
                    actor.actions_taken_this_turn.append("attack")
                    actor.consume_attack_attempt_buffs(self)
                    actor.notify_basic_attack_finished(self, payload, [], missed=True)
                    self.log(f"{actor.name} 的攻击因【无常之雾】失败。")
                    return
                if not all(component.before_basic_attack_resolution(self, actor, payload) for component in list(actor.iter_components())):
                    payload["basic_attack_cancelled"] = True
                    actor.attacks_used += int(payload.get("attack_cost", 1))
                    actor.actions_taken_this_turn.append("attack")
                    actor.consume_attack_attempt_buffs(self)
                    actor.notify_basic_attack_finished(self, payload, [], missed=True)
                    self.log(f"{actor.name} 的攻击因前移失败而落空。")
                    return
                if not actor.can_take_turn_actions(self) or actor.cannot_attack:
                    payload["basic_attack_cancelled"] = True
                    actor.attacks_used += int(payload.get("attack_cost", 1))
                    actor.actions_taken_this_turn.append("attack")
                    actor.consume_attack_attempt_buffs(self)
                    actor.notify_basic_attack_finished(self, payload, [], missed=True)
                    self.log(f"{actor.name} 的攻击因前移后行动受限而落空。")
                    return
                if payload.get("separate_attack_segments"):
                    actor.attacks_used += int(payload.get("attack_cost", 1))
                    actor.actions_taken_this_turn.append("attack")
                    self._separate_attack_sequences[payload["declaration_id"]] = {"seen": set(), "contexts": []}
                    self.resolve_separate_attack_segment(actor, queued_action)
                    return
                if self.basic_attack_area_cells_for_payload(actor, payload) is not None:
                    def resolve_area_attack() -> None:
                        area_cells = self.basic_attack_area_cells_for_payload(actor, payload)
                        self.basic_area_attack_with_payload(actor, payload, area_cells)

                    self.resolve_from_declared_origin(actor, payload, resolve_area_attack)
                    return
                target: Optional[Unit]
                declared = self.declared_target_position(payload)
                attack_cost = int(payload.get("attack_cost", 1))
                attack_name = str(payload.get("attack_name") or "普攻")
                if declared is not None:
                    target = self.resolve_declared_target_unit(
                        actor,
                        payload,
                        ignore_stealth=bool(payload.get("ignore_stealth")),
                    )
                    if target is None:
                        actor.attacks_used += attack_cost
                        actor.actions_taken_this_turn.append("attack")
                        actor.consume_attack_attempt_buffs(self)
                        self.log(f"{actor.name} 的【{attack_name}】打在 ({declared.x}, {declared.y})，没有命中有效目标。")
                        actor.notify_basic_attack_finished(self, payload, [], missed=True)
                        return
                else:
                    target = self.effect_recipient(self.get_unit(payload["target_unit_id"]))
                self.resolve_from_declared_origin(actor, payload, lambda: self.basic_attack_with_payload(actor, target, payload))
                return
            if queued_action.action_type == "skill":
                if payload.get("action_failed_by_wuchang_mist") or payload.get("action_failed_by_floating_crane"):
                    skill = actor.get_skill(payload["skill_code"])
                    skill.on_target_missed(self, actor, payload)
                    skill.finalize_use(self, actor)
                    actor.actions_taken_this_turn.append(f"skill:{skill.code}")
                    weather_name = "无常之雾" if payload.get("action_failed_by_wuchang_mist") else "浮鹤城"
                    self.log(f"{actor.name} 的【{skill.name}】因【{weather_name}】失败。")
                    return
                resolved_payload = dict(payload)
                resolved_target = self.resolve_declared_target_unit(
                    actor,
                    payload,
                    ignore_stealth=bool(payload.get("ignore_stealth")),
                )
                resolved_payload["resolved_target_unit_id"] = resolved_target.unit_id if resolved_target is not None else None
                if payload.get("resolves_from_current_position"):
                    self.use_skill(actor, payload["skill_code"], resolved_payload)
                else:
                    self.resolve_from_declared_origin(actor, payload, lambda: self.use_skill(actor, payload["skill_code"], resolved_payload))
                return
            if queued_action.action_type == "skill_effect":
                self.resolve_skill_effect(actor, queued_action)
                return
            if queued_action.action_type == "reaction_skill":
                source_action = self.source_action_for_reaction(queued_action)
                reaction_payload = dict(payload)
                skill = actor.get_skill(payload["action_code"])
                if not skill.declared_reaction_landing_valid(self, actor, payload):
                    skill.finalize_use(self, actor)
                    self.log(f"{actor.name} 的【{skill.name}】因之前的连锁改变落点而落空。")
                    return
                resolved_target = self.resolve_declared_target_unit(
                    actor,
                    payload,
                    ignore_stealth=bool(payload.get("ignore_stealth")),
                )
                reaction_payload["resolved_target_unit_id"] = resolved_target.unit_id if resolved_target is not None else None
                option = ReactionOption(
                    unit_id=actor.unit_id,
                    action_code=payload["action_code"],
                    action_name=payload["action_name"],
                    action_type="skill",
                    timing="reaction",
                    chain_speed=queued_action.speed,
                    description=payload.get("description", ""),
                )
                self.execute_reaction_option(option, source_action, reaction_payload)
                if option.action_code == "evasion" and source_action.action_type == "attack":
                    # The reconstructed source is a copy. Return only the completed move receipt.
                    payload["completed_evasion_unit_ids"] = list(source_action.payload.get("completed_evasion_unit_ids", []))
                return
            if queued_action.action_type == "reaction_action":
                self.resolve_reaction_action(actor, payload["action_code"], self.source_action_for_reaction(queued_action))
                return


    def start_action_or_chain(self, payload: dict[str, Any]) -> None:
        from wujiang.tactical.engine.core import (
            nullcontext,
        )
        self.enforce_sandstorm_stealth_rules()
        queued_action = self.build_queued_action(payload)
        actor = self.get_unit(queued_action.actor_id)
        with self.suppress_logs() if self.queued_action_hides_logs(queued_action) else nullcontext():
            reaction_window_timing = "before"
            if queued_action.action_type == "skill":
                skill = actor.get_skill(queued_action.payload["skill_code"])
                self.prepay_skill_resources(skill, actor, queued_action.payload)
                queued_action.payload["resources_prepaid"] = True
                if not actor.alive:
                    self.log_public_event(f"{actor.name} 支付【{skill.name}】费用后被破坏，技能未能结算。", source=actor)
                    self.cleanup_dead_units()
                    return
                reaction_window_timing = skill.reaction_window_timing(self, actor, queued_action.payload)
            if queued_action.action_type in {"attack", "skill"}:
                actor.notify_action_declared(self, queued_action.action_type, queued_action.payload)
                queued_action.payload["declared_source_attack"] = actor.stat("attack")
                declared_ids = [segment["target_unit_id"] for segment in queued_action.payload.get("separate_attack_segments", [])] or queued_action.target_unit_ids
                for target_id in declared_ids:
                    target = self.units.get(target_id)
                    if target is not None:
                        for component in list(target.iter_components()):
                            component.on_target_action_declared(self, actor, queued_action.action_type, queued_action.payload)
            if (queued_action.payload.get("action_failed_by_wuchang_mist") or queued_action.payload.get("action_failed_by_floating_crane")
                    or (queued_action.action_type == "skill" and reaction_window_timing == "after")):
                self.resolve_queued_action(queued_action)
                self.advance_followup_actions()
                return
            self.present_reaction_window_or_resolve(queued_action)


