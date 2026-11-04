"""Battle BattleExecutionMixin methods, retained under the Battle public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleExecutionMixin:
    """A cohesive Battle responsibility with no independent state."""

    def perform_action(self, payload: dict[str, Any]) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
        )
        pending = self.pending_damage_choice
        if pending is not None:
            if payload.get("type") != "damage_choice" or payload.get("unit_id") != pending["unit_id"]:
                raise ActionError("请先完成本次伤害选择。")
            if pending.get("kind") in {"rotation", "attack_swap", "formation", "optional_swap", "optional_placement", "end_dash", "electronic_teleport", "hell_sacrifice", "hell_death_strike", "hell_rescue", "hell_dragon_first", "hell_dragon_second", "hell_dragon_effect", "gentleman_guard"}:
                choice = str(payload.get("target_unit_id") or "")
                if (choice == "decline" and pending.get("kind") in {"attack_swap", "formation"}) or (choice != "decline" and choice not in pending["options"]):
                    raise ActionError("请选择当前提示中的合法对象与落点。")
            else:
                choice = str(payload.get("stat_name") or "")
                if choice != "decline" and choice not in pending["stats"]:
                    raise ActionError("请选择一项可降低的能力，或放弃抵消。")
            decisions = [*pending["decisions"], choice]
            self.pending_damage_choice = None
            try:
                self._perform_action_with_damage_choices(pending["action_payload"], decisions)
            except Exception:
                self.pending_damage_choice = pending
                raise
            return
        if payload.get("type") == "damage_choice":
            raise ActionError("现在没有待决定的伤害。")
        if not getattr(self, "_ai_probe_active", False) and (any(
            getattr(component, "requires_damage_choice", False)
            for unit in self.all_units() if unit.alive and not unit.banished
            for component in unit.iter_components()
        ) or (payload.get("type") == "respawn_select" and
              str(payload.get("unit_id") or "") in self.hell_rebirth_unit_ids)):
            self._perform_action_with_damage_choices(payload, [])
            return
        self._perform_action_steps(payload)


    def _perform_action_steps(self, payload: dict[str, Any]) -> None:
        self.sync_linked_units()
        self._perform_action(payload)
        self.sync_linked_units()
        self.finish_destroyed_active_turn_if_idle()


    def _perform_action_with_damage_choices(self, payload: dict[str, Any], decisions: list[str]) -> None:
        from wujiang.tactical.engine.core import (
            DamageChoiceRequired,
            _id_counter,
            battle_state_rollback,
            deepcopy,
        )
        try:
            with battle_state_rollback(self, ai_probe=False):
                self._damage_choice_active = True
                self._damage_choice_decisions = decisions
                self._damage_choice_event_index = 0
                self._perform_action_steps(deepcopy(payload))
        except DamageChoiceRequired as required:
            self.pending_damage_choice = {
                "prompt_id": f"damage-{next(_id_counter)}",
                "unit_id": required.unit_id,
                "kind": required.kind,
                "action_name": required.action_name,
                "damage": required.damage,
                "event_index": required.event_index,
                "stats": required.stats,
                "options": required.options,
                "protected_unit_id": required.protected_unit_id,
                "action_payload": deepcopy(payload),
                "decisions": list(decisions),
            }
            return
        try:
            # A real replay can fail even after the speculative pass succeeded.
            # Keep its before-image so the choice can be retried consistently.
            with battle_state_rollback(self, ai_probe=False, commit_on_success=True):
                self._damage_choice_active = True
                self._damage_choice_decisions = decisions
                self._damage_choice_event_index = 0
                self._perform_action_steps(deepcopy(payload))
        except DamageChoiceRequired as required:
            self.pending_damage_choice = {
                "prompt_id": f"damage-{next(_id_counter)}",
                "unit_id": required.unit_id,
                "kind": required.kind,
                "action_name": required.action_name,
                "damage": required.damage,
                "event_index": required.event_index,
                "stats": required.stats,
                "options": required.options,
                "protected_unit_id": required.protected_unit_id,
                "action_payload": deepcopy(payload),
                "decisions": list(decisions),
            }
        finally:
            self._damage_choice_active = False
            self._damage_choice_decisions = []
            self._damage_choice_event_index = 0


    def damage_stat_choice(self, ctx: DamageContext, stats: list[str]) -> Optional[str]:
        from wujiang.tactical.engine.core import (
            DamageChoiceRequired,
        )
        if not getattr(self, "_damage_choice_active", False) or getattr(self, "_ai_probe_active", False):
            return None
        event_index = int(getattr(self, "_damage_choice_event_index", 0))
        self._damage_choice_event_index = event_index + 1
        decisions = getattr(self, "_damage_choice_decisions", [])
        if event_index < len(decisions):
            stat = decisions[event_index]
            return stat if stat in stats else None
        if getattr(self, "_damage_choice_probe_default", False):
            return None
        amount = ctx.raw_damage if ctx.raw_damage is not None else self.damage_rule.calculate_damage(
            ctx.attack_power, ctx.target.stat("defense")
        )
        raise DamageChoiceRequired(
            unit_id=ctx.target.unit_id,
            action_name=("未知伤害" if ctx.source is not None and ctx.source.is_stealthed()
                         and ctx.source.player_id != ctx.target.player_id else ctx.action_name),
            damage=round(min(float(ctx.target.current_hp), float(ctx.target.damage_fraction_after_limits(amount))), 4),
            event_index=event_index,
            stats=list(stats),
        )


    def damage_rotation_choice(self, ctx: DamageContext, options: list[str]) -> Optional[str]:
        from wujiang.tactical.engine.core import (
            DamageChoiceRequired,
        )
        if not getattr(self, "_damage_choice_active", False) or getattr(self, "_ai_probe_active", False):
            return None
        event_index = int(getattr(self, "_damage_choice_event_index", 0))
        self._damage_choice_event_index = event_index + 1
        decisions = getattr(self, "_damage_choice_decisions", [])
        if event_index < len(decisions):
            choice = decisions[event_index]
            return choice if choice in options else None
        if getattr(self, "_damage_choice_probe_default", False):
            return None
        raise DamageChoiceRequired(
            unit_id=ctx.target.unit_id,
            action_name=("未知伤害" if ctx.source is not None and ctx.source.is_stealthed()
                         and ctx.source.player_id != ctx.target.player_id else ctx.action_name),
            damage=round(float(ctx.actual_damage), 4),
            event_index=event_index,
            kind="rotation",
            options=list(options),
        )


    def attack_swap_choice(self, actor: Unit, options: list[str]) -> Optional[str]:
        from wujiang.tactical.engine.core import (
            DamageChoiceRequired,
        )
        if not getattr(self, "_damage_choice_active", False) or getattr(self, "_ai_probe_active", False):
            return options[0] if options else None
        event_index = int(getattr(self, "_damage_choice_event_index", 0))
        self._damage_choice_event_index = event_index + 1
        decisions = getattr(self, "_damage_choice_decisions", [])
        if event_index < len(decisions):
            choice = decisions[event_index]
            return choice if choice in options else None
        if getattr(self, "_damage_choice_probe_default", False):
            return options[0] if options else None
        raise DamageChoiceRequired(
            unit_id=actor.unit_id,
            action_name="普攻换位",
            damage=0,
            event_index=event_index,
            kind="attack_swap",
            options=list(options),
        )


    def formation_placement_choice(self, actor: Unit, options: list[str], index: int) -> Optional[str]:
        from wujiang.tactical.engine.core import (
            DamageChoiceRequired,
        )
        if not options:
            return None
        if not getattr(self, "_damage_choice_active", False) or getattr(self, "_ai_probe_active", False):
            return options[0]
        event_index = int(getattr(self, "_damage_choice_event_index", 0))
        self._damage_choice_event_index = event_index + 1
        decisions = getattr(self, "_damage_choice_decisions", [])
        if event_index < len(decisions):
            choice = decisions[event_index]
            return choice if choice in options else None
        if getattr(self, "_damage_choice_probe_default", False):
            return options[0]
        raise DamageChoiceRequired(
            unit_id=actor.unit_id, action_name=f"剑斗布阵第{index}位", damage=0,
            event_index=event_index, kind="formation", options=list(options),
        )


    def optional_position_choice(self, actor: Unit, options: list[str], *,
                                 action_name: str, kind: str, damage: float = 0,
                                 protected_unit_id: str | None = None) -> Optional[str]:
        from wujiang.tactical.engine.core import (
            DamageChoiceRequired,
        )
        if not options:
            return None
        if not getattr(self, "_damage_choice_active", False) or getattr(self, "_ai_probe_active", False):
            return None
        event_index = int(getattr(self, "_damage_choice_event_index", 0))
        self._damage_choice_event_index = event_index + 1
        decisions = getattr(self, "_damage_choice_decisions", [])
        if event_index < len(decisions):
            choice = decisions[event_index]
            return choice if choice in options else None
        if getattr(self, "_damage_choice_probe_default", False):
            return None
        raise DamageChoiceRequired(unit_id=actor.unit_id, action_name=action_name, damage=round(float(damage), 4),
                                   event_index=event_index, kind=kind, options=list(options),
                                   protected_unit_id=protected_unit_id)


    def destroyed_active_turn_is_idle(self) -> bool:
        from wujiang.tactical.engine.core import (
            parse_army_slot,
        )
        if (
            self.winner is not None
            or self.pending_damage_choice is not None
            or self.pending_chain is not None
            or self.pending_followup_actions
            or self.pending_respawn_unit_ids
            or self.resolving_action is not None
            or self._pending_end_turn_after_followups
            or self._pending_turn_end_after_effects_player_id is not None
            or getattr(self, "_resolving_turn_end_player_id", None) is not None
            or self._pending_turn_advance_after_hell_rebirth
        ):
            return False
        active_unit_id = self.current_turn_slot_unit_id()
        if active_unit_id is None or parse_army_slot(active_unit_id) is not None:
            return False
        active_unit = self.units.get(active_unit_id)
        if active_unit is not None and active_unit.alive:
            return False
        # A surviving unit with a preserved slot can still belong to this
        # controller's portion of the turn. Let the existing bundle AI act.
        return not self.current_turn_bundle_units(include_banished=False)


    def finish_destroyed_active_turn_if_idle(self) -> None:
        if not self.destroyed_active_turn_is_idle():
            return
        self._processed_any_turn_end_choices.clear()
        self.finish_any_turn_end_choices()


    def _perform_action(self, payload: dict[str, Any]) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
            Position,
            QueuedAction,
            is_army_soldier,
        )
        if self.winner is not None:
            raise ActionError("对局已结束，请返回选将页面开始新的对局。")
        action_type = payload.get("type")
        if action_type == "hell_dragon_return":
            if (self.pending_damage_choice is not None or self.pending_chain is not None
                    or self.pending_respawn_unit_ids or self.pending_followup_actions):
                raise ActionError("请先完成当前连锁或选择，再让古龙复活。")
            unit = self.get_unit(str(payload.get("unit_id") or ""))
            if (unit.unit_id not in self.hell_dragon_return_cells or not any(
                    getattr(component, "grants_hell_dragon_return", False)
                    for component in unit.iter_components())):
                raise ActionError("这名古龙没有待使用的献祭复活权。")
            self.restore_hell_dragon(unit, Position(int(payload["x"]), int(payload["y"])))
            return
        if self.pending_respawn_unit_ids:
            if action_type != "respawn_select":
                raise ActionError("当前需要先为消失单位选择重新出现的位置。")
            prompt = self.current_respawn_prompt()
            if prompt is None:
                self.pending_respawn_unit_ids = []
                if self._pending_turn_advance_after_hell_rebirth:
                    self._pending_turn_advance_after_hell_rebirth = False
                    self.advance_turn_slot_index()
                    self.start_current_turn()
                return
            if payload.get("unit_id") != prompt.unit_id:
                raise ActionError("现在需要先处理当前等待重新出现的单位。")
            destination = Position(int(payload["x"]), int(payload["y"]))
            unit = self.get_unit(prompt.unit_id)
            if destination not in self.respawn_options_for(unit):
                raise ActionError("该位置不能作为重新出现的落点。")
            if not self.can_place_unit(unit, destination, ignore=unit, mover=unit):
                raise ActionError("该位置已被占用，无法重新出现。")
            self.pending_respawn_unit_ids.pop(0)
            if unit.unit_id in self.hell_rebirth_unit_ids:
                self.restore_hell_rebirth(unit, destination)
            else:
                self.restore_banished_unit(unit, destination)
            self.advance_respawn_queue()
            if self._pending_turn_advance_after_hell_rebirth and not self.pending_respawn_unit_ids:
                self._pending_turn_advance_after_hell_rebirth = False
                self.advance_turn_slot_index()
                self.start_current_turn()
            return
        if self.pending_chain is not None:
            current_unit_id = self.pending_chain.current_unit_id()
            if action_type == "chain_skip":
                if current_unit_id is None:
                    self.finalize_reaction_window()
                    return
                unit = self.get_unit(current_unit_id)
                if unit.is_stealthed():
                    self.pending_chain.decision_log.append("有单位放弃了连锁。")
                else:
                    self.pending_chain.decision_log.append(f"{unit.name} 放弃连锁。")
                    self.log(f"{unit.name} 放弃了连锁。")
                self.pending_chain.pending_reactor_ids.pop(0)
                self.advance_reaction_window()
                return
            if action_type == "chain_react":
                if current_unit_id is None:
                    self.finalize_reaction_window()
                    return
                if payload.get("unit_id") != current_unit_id:
                    raise ActionError("现在还没有轮到这个单位连锁。")
                options = self.pending_chain.options_by_unit.get(current_unit_id, [])
                action_code = payload.get("action_code")
                chosen = next((option for option in options if option.action_code == action_code), None)
                if chosen is None:
                    raise ActionError("该单位当前不能使用这个连锁动作。")
                reactor = self.get_unit(current_unit_id)
                reaction_payload = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"type", "unit_id", "action_code"}
                }
                if chosen.action_type == "skill":
                    skill = reactor.get_skill(chosen.action_code)
                    ok, reason = skill.can_react_with_payload(
                        self,
                        reactor,
                        self.pending_chain.queued_action,
                        reaction_payload,
                    )
                    if not ok:
                        raise ActionError(reason)
                    for field in ("kings_insight_checked", "wuchang_mist_checked", "action_failed_by_wuchang_mist",
                                  "floating_crane_checked", "action_failed_by_floating_crane"):
                        reaction_payload.pop(field, None)
                    reaction_payload.update(skill.queued_reaction_payload_metadata(self, reactor, reaction_payload))
                    # Preserve a reaction that was already legal while destroyed;
                    # payment killing a living reactor still cancels its action.
                    after_death_reaction = (
                        reactor in self.destroyed_units and not reactor.alive
                        and skill.can_react_after_destroyed(self, reactor, self.pending_chain.queued_action)
                    )
                    supported_ship = self.passive_support_ship(
                        reactor, skill, self.pending_chain.queued_action, reaction_payload
                    )
                    self.prepay_skill_resources(skill, reactor, reaction_payload)
                    if supported_ship is not None:
                        supported_ship.ship_passive_claim_turn = self.turn_number
                        supported_ship.ship_passive_claim_rider_id = reactor.unit_id
                        self.refresh_ship_passive_reaction_options(supported_ship)
                    reaction_payload["resources_prepaid"] = True
                    if not reactor.alive and not after_death_reaction:
                        skill.finalize_use(self, reactor)
                        self.log_public_event(
                            f"{reactor.name} 支付【{skill.name}】费用后被破坏，连锁未能结算。",
                            source=reactor,
                        )
                        self.cleanup_dead_units()
                        if self.winner is not None:
                            self.pending_chain = None
                        else:
                            self.pending_chain.pending_reactor_ids.pop(0)
                            self.advance_reaction_window()
                        return
                    target_ids = self.payload_target_unit_ids(reaction_payload)
                    if target_ids:
                        target = self.get_unit(target_ids[0])
                        declared_target = self.declared_cell_for_target(reactor, target, reaction_payload)
                        if declared_target is not None:
                            reaction_payload["declared_target_x"] = declared_target.x
                            reaction_payload["declared_target_y"] = declared_target.y
                    reactor.notify_action_declared(
                        self,
                        "skill",
                        {
                            **reaction_payload,
                            "unit_id": reactor.unit_id,
                            "skill_code": chosen.action_code,
                            "queued_resolution": True,
                        },
                    )
                    reaction_payload["declared_source_attack"] = reactor.stat("attack")
                    if (((reactor.unit_id not in self.units or not reactor.alive) and not after_death_reaction)
                            or reactor.banished):
                        skill.finalize_use(self, reactor)
                        self.pending_chain.decision_log.append(f"{reactor.name} 的连锁因自身离场而失效。")
                        self.log(f"{reactor.name} 的【{chosen.action_name}】未能结算，因为使用者已不在战场。")
                        self.pending_chain.pending_reactor_ids.pop(0)
                        self.advance_reaction_window()
                        return
                    if chosen.action_code == "evasion" and self.pending_chain.queued_action.action_type == "attack":
                        evaded = self.pending_chain.queued_action.payload.setdefault("evaded_unit_ids", [])
                        if reactor.unit_id not in evaded:
                            evaded.append(reactor.unit_id)
                queued = QueuedAction(
                    action_type="reaction_skill" if chosen.action_type == "skill" else "reaction_action",
                    actor_id=current_unit_id,
                    display_name=chosen.action_name,
                    speed=chosen.chain_speed,
                    payload={
                        "action_code": chosen.action_code,
                        "action_name": chosen.action_name,
                        "description": chosen.description,
                        **reaction_payload,
                        **({"after_death_effect": True} if chosen.action_type == "skill" and after_death_reaction else {}),
                        "source_action_type": self.pending_chain.queued_action.action_type,
                        "source_actor_id": self.pending_chain.queued_action.actor_id,
                        "source_display_name": self.pending_chain.queued_action.display_name,
                        "source_speed": self.pending_chain.queued_action.speed,
                        "source_payload": dict(self.pending_chain.queued_action.payload),
                        "source_target_unit_ids": list(self.pending_chain.queued_action.target_unit_ids),
                        "source_target_cells": [cell.to_dict() for cell in self.pending_chain.queued_action.target_cells],
                        "source_player_id": self.pending_chain.queued_action.source_player_id,
                        "source_hostile": self.pending_chain.queued_action.hostile,
                    },
                    target_unit_ids=list(dict.fromkeys([self.pending_chain.queued_action.actor_id, *[
                        unit.unit_id for unit in self.effect_units_at_cells(self.payload_positions(reaction_payload, "reaction_effect_cells"))
                        if unit.player_id != reactor.player_id
                    ]])),
                    target_cells=self.payload_positions(reaction_payload, "reaction_effect_cells"),
                    source_player_id=reactor.player_id,
                    hostile=True,
                    reaction_source_id=self.pending_chain.queued_action.actor_id,
                    suppress_logs=reactor.is_stealthed() or self.pending_chain.queued_action.suppress_logs,
                )
                self.pending_chain.chosen_reactions.append(queued)
                if reactor.is_stealthed():
                    self.pending_chain.decision_log.append("有单位进行了连锁选择。")
                else:
                    self.pending_chain.decision_log.append(f"{self.get_unit(current_unit_id).name} 选择了 {chosen.action_name}。")
                self.pending_chain.pending_reactor_ids.pop(0)
                self.advance_reaction_window()
                return
            raise ActionError("当前正在等待连锁响应。")
        if action_type == "end_turn":
            self.end_turn()
            return
        if action_type in {"move", "attack", "skill"}:
            actor_id = payload.get("unit_id")
            if actor_id:
                actor = self.get_unit(str(actor_id))
                if is_army_soldier(actor):
                    raise ActionError("士兵由军队指令统一行动，不能单独操控。")
            self.start_action_or_chain(payload)
            return
        if action_type == "chain_skip":
            return
        unit_id = payload.get("unit_id")
        if not unit_id:
            raise ActionError("动作缺少单位。")
        unit = self.get_unit(str(unit_id))
        if is_army_soldier(unit):
            raise ActionError("士兵由军队指令统一行动，不能单独操控。")
        if action_type == "pass_unit":
            self.pass_turn(unit)
            return
        raise ActionError("未知动作类型。")


