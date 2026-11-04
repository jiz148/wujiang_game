"""Battle BattleReactionMixin methods, retained under the Battle public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleReactionMixin:
    """A cohesive Battle responsibility with no independent state."""

    def available_reaction_options(self, unit: Unit, queued_action: QueuedAction) -> list[ReactionOption]:
        from wujiang.tactical.engine.core import (
            Position,
            ReactionOption,
        )
        if self.shield_auto_blocks_chain(unit, queued_action):
            return []
        source = self.units.get(queued_action.actor_id)
        if source is not None and any(
            callable(block := getattr(component, "blocks_target_reaction", None))
            and block(self, unit, queued_action)
            for component in source.iter_components()
        ):
            return []
        bypass_free_block_counter = bool(
            queued_action.action_type == "skill" and source is not None
            and (skill := source.skill_map().get(str(queued_action.payload.get("skill_code") or ""))) is not None
            and getattr(skill, "bypasses_block_counter", False)
        )
        affected_by_source = set(queued_action.target_unit_ids)
        affected_by_source.update(target.unit_id for target in self.effect_units_at_cells(queued_action.target_cells))
        bystander_skill_locked = bool(
            queued_action.action_type in {"attack", "skill", "skill_effect"}
            and source is not None
            and any(getattr(component, "blocks_bystander_reactions", False)
                    for component in source.iter_components())
            and unit.unit_id not in affected_by_source
        )
        options: list[ReactionOption] = []
        for skill in ([] if bystander_skill_locked else unit.skills):
            ok, _ = skill.can_react_to(self, unit, queued_action)
            if not ok:
                continue
            preview = skill.reaction_preview(self, unit, queued_action)
            ship = self.mounted_unit_for(unit) if skill.timing == "passive" else None
            if (ship is not None and ship.allows_multiple_riders
                    and getattr(ship, "ship_passive_claim_turn", -1) == self.turn_number
                    and getattr(ship, "ship_passive_claim_rider_id", None) != unit.unit_id):
                available_ids = [unit_id for unit_id in preview.get("target_unit_ids", [])
                                 if unit_id != ship.unit_id]
                if not available_ids:
                    continue
                preview = dict(preview, target_unit_ids=available_ids,
                               cells=[cell for cell in preview.get("cells", [])
                                      if Position(int(cell["x"]), int(cell["y"])) not in self.unit_cells(ship)])
            options.append(
                ReactionOption(
                    unit_id=unit.unit_id,
                    action_code=skill.code,
                    action_name=skill.name,
                    action_type="skill",
                    timing=skill.timing,
                    chain_speed=skill.chain_speed,
                    description=skill.description,
                    preview=preview,
                )
            )
        reaction_target = self.reaction_proxy_target(unit, queued_action)
        if (not bypass_free_block_counter and self.unit_can_use_counter(unit)
                and queued_action.speed < 2 and reaction_target is not None):
            if self.unit_can_use_block_counter(unit):
                options.append(
                    ReactionOption(
                        unit_id=unit.unit_id,
                        action_code="block",
                        action_name="格挡",
                        action_type="reaction_action",
                        timing="reaction",
                        chain_speed=2,
                        description="下一次伤害结算时守 +1，只持续到这次连锁结算结束。",
                        preview={"cells": [cell.to_dict() for cell in self.unit_cells(reaction_target)], "target_unit_ids": [], "requires_target": False},
                    )
                )
            attacker = self.units.get(queued_action.actor_id)
            if (
                attacker is not None
                and self.attack_target_allowed(unit, attacker)[0]
            ):
                counter_preview = {"cells": [cell.to_dict() for cell in self.unit_cells(attacker)],
                                   "target_unit_ids": [attacker.unit_id], "requires_target": False}
                description = "对你所连锁的攻击或技能使用者进行一次普攻式反击，需在自身攻击范围内。"
                for component in unit.iter_components():
                    presentation = getattr(component, "counter_reaction_presentation", None)
                    if callable(presentation):
                        counter_preview, description = presentation(self, unit, counter_preview, description)
                options.append(
                    ReactionOption(
                        unit_id=unit.unit_id,
                        action_code="counter",
                        action_name="反击",
                        action_type="reaction_action",
                        timing="reaction",
                        chain_speed=2,
                        description=description,
                        preview=counter_preview,
                    )
                )
        return options


    def shield_auto_blocks_chain(self, unit: Unit, queued_action: QueuedAction) -> bool:
        if queued_action.payload.get("pass_through_damage"):
            return False
        recipient = self.effect_recipient(unit)
        anti_pierce = any(component.ignores_incoming_piercing for component in recipient.iter_components())
        actor = self.units.get(queued_action.actor_id)
        piercing_followup = queued_action.action_type == "attack" and actor is not None and any(
            callable(hook := getattr(component, "piercing_basic_attack_followup_applies", None))
            and hook(self, actor, recipient)
            for component in actor.iter_components()
        )
        return (
            queued_action.action_type in {"attack", "skill", "skill_effect"}
            and queued_action.speed == 1
            and recipient.total_shields() > 0
            and (anti_pierce or not (queued_action.payload.get("ignore_shield") or queued_action.payload.get("half_ignore_shield")
                                     or queued_action.payload.get("piercing_followup") or piercing_followup))
        )


    def action_ignores_stealth(self, queued_action: QueuedAction) -> bool:
        return bool(queued_action.payload.get("ignore_stealth"))


    def target_can_chain_against(self, unit: Unit, queued_action: QueuedAction) -> bool:
        if not unit.alive or unit.position is None or unit.banished:
            return False
        if any(effect.blocks_action_effect(self, self.effect_recipient(unit), queued_action) for effect in self.field_effects):
            return False
        if queued_action.action_type == "skill":
            source = self.units.get(queued_action.actor_id)
            skill = source.skill_map().get(queued_action.payload.get("skill_code")) if source is not None else None
            if skill is not None:
                return skill.target_can_react_to_effect(self, source, self.effect_recipient(unit), queued_action.payload)
        return True


    def reaction_affected_units(self, queued_action: QueuedAction) -> list[Unit]:
        actor = self.units.get(queued_action.actor_id)
        if actor is None and queued_action.payload.get("after_death_effect"):
            actor = next((unit for unit in self.destroyed_units if unit.unit_id == queued_action.actor_id), None)
        if actor is None or (not actor.alive and not queued_action.payload.get("after_death_effect")) or actor.banished:
            return []
        affected: list[Unit] = []
        seen: set[str] = set()
        for unit_id in queued_action.target_unit_ids:
            if unit_id in seen:
                continue
            seen.add(unit_id)
            unit = self.units.get(unit_id)
            if unit is None or not unit.alive or unit.banished:
                continue
            if unit.player_id == actor.player_id:
                continue
            if not self.target_can_chain_against(unit, queued_action):
                continue
            if self.shield_auto_blocks_chain(unit, queued_action):
                continue
            affected.append(unit)
        return affected


    def create_reaction_window(self, queued_action: QueuedAction) -> Optional[ReactionWindow]:
        from wujiang.tactical.engine.core import (
            ReactionWindow,
            forecasting_active,
            prediction_note,
            random,
        )
        if queued_action.speed >= 3 or not queued_action.hostile or not queued_action.target_unit_ids:
            return None
        affected_units = self.reaction_affected_units(queued_action)
        if not affected_units:
            return None
        reactive_player_id = affected_units[0].player_id
        candidate_ids: list[str] = []
        options_by_unit: dict[str, list[ReactionOption]] = {}
        candidates_by_id: dict[str, Unit] = {}
        dead_reactors = [
            unit for unit in self.destroyed_units
            if unit.player_id == reactive_player_id and unit.last_position is not None
            and not unit.alive and not unit.banished
            and any(skill.can_react_after_destroyed(self, unit, queued_action) for skill in unit.skills)
        ]
        for unit in [*affected_units, *self.player_units(reactive_player_id), *dead_reactors]:
            if unit.unit_id in candidate_ids:
                continue
            if (((not unit.alive or unit.position is None) and unit not in dead_reactors)
                    or unit.banished):
                continue
            options = self.available_reaction_options(unit, queued_action)
            if options:
                candidate_ids.append(unit.unit_id)
                candidates_by_id[unit.unit_id] = unit
                options_by_unit[unit.unit_id] = options
        if not candidate_ids:
            return None
        if forecasting_active():
            # Full-AI rules score under the public "opponent does not chain"
            # assumption. Window display order contributes no outcome utility;
            # avoid drawing live tie priorities for that unexecuted window.
            prediction_note("neutral_reaction_order_ties", len(candidate_ids))
        candidate_ids.sort(
            key=lambda unit_id: (
                -float(candidates_by_id[unit_id].stat("speed")),
                -int(candidates_by_id[unit_id].level),
                0.5 if forecasting_active() else random.random(),
            )
        )
        return ReactionWindow(
            reactive_player_id=reactive_player_id,
            queued_action=queued_action,
            pending_reactor_ids=candidate_ids,
            options_by_unit=options_by_unit,
        )


    def execute_reaction_option(
        self,
        option: ReactionOption,
        queued_action: QueuedAction,
        reaction_payload: Optional[dict[str, Any]] = None,
    ) -> None:
        actor = self.get_unit(option.unit_id)
        if option.action_type == "skill":
            skill = actor.get_skill(option.action_code)
            skill.react(self, actor, reaction_payload or {}, queued_action)
            skill.finalize_use(self, actor)
            self.log(f"{actor.name} 连锁使用【{skill.name}】。")
            return
        self.resolve_reaction_action(actor, option.action_code, queued_action)


    def resolve_reaction_action(self, actor: Unit, action_code: str, queued_action: QueuedAction) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
            TemporaryDefenseStatus,
        )
        if action_code == "block":
            actor.add_status(
                TemporaryDefenseStatus(
                    "格挡",
                    defense_delta=1,
                    description="下一次伤害结算时守 +1。",
                    expire_with_chain=True,
                )
            )
            self.log(f"{actor.name} 进入格挡姿态。")
            return
        if action_code == "counter":
            source = self.units.get(queued_action.actor_id)
            if source is None:
                return
            ok, _ = self.attack_target_allowed(actor, source)
            if not ok:
                return
            ctx = self.resolve_attack_damage(actor, source, action_name="\u53cd\u51fb", tags={"counter"})
            actor.notify_basic_attack_finished(self, {"counter": True, "reaction_attack": True},
                                               [ctx] if ctx is not None else [], missed=ctx is None)
            self.log(f"{actor.name} \u53d1\u52a8\u4e86\u53cd\u51fb\u3002")
            return
        raise ActionError("未知连锁动作。")


    def finalize_reaction_window(self) -> None:
        if self.pending_chain is None:
            return
        queued = self.pending_chain.queued_action
        reactions = list(reversed(self.pending_chain.chosen_reactions))
        queued.payload["enemy_reacted"] = any(reaction.source_player_id != queued.source_player_id for reaction in reactions)
        actor = self.units.get(queued.actor_id)
        track_prevention = queued.action_type == "attack" and actor is not None and any(
            getattr(component, "track_attack_reaction_prevention", False) for component in actor.iter_components()
        )
        queued.payload["enemy_skill_prevented_attack"] = False
        queued.payload["passive_attack_preventer_ids"] = []
        self.pending_chain = None
        chain_turn = self.turn_number
        try:
            for reaction in reactions:
                enemy_skill = track_prevention and reaction.action_type == "reaction_skill" and reaction.source_player_id != queued.source_player_id
                would_hit = self.attack_would_deal_damage(queued) if enemy_skill else False
                self.resolve_queued_action(reaction)
                if reaction.action_type == "reaction_skill" and reaction.payload.get("action_code") == "evasion":
                    completed = queued.payload.setdefault("completed_evasion_unit_ids", [])
                    for unit_id in reaction.payload.get("completed_evasion_unit_ids", []):
                        if unit_id not in completed:
                            completed.append(unit_id)
                if self.turn_number != chain_turn:
                    return
                if would_hit and not self.attack_would_deal_damage(queued):
                    queued.payload["enemy_skill_prevented_attack"] = True
                    reactor = self.units.get(reaction.actor_id)
                    if reactor is not None:
                        used = reactor.get_skill(reaction.payload.get("action_code") or reaction.payload["skill_code"])
                        if used.timing in {"passive", "reaction"}:
                            queued.payload["passive_attack_preventer_ids"].append(reactor.unit_id)
                if self.winner is not None:
                    return
            self.resolve_queued_action(queued)
        finally:
            self.expire_chain_temporary_statuses()
            self.advance_followup_actions()


    def advance_reaction_window(self) -> None:
        if self.pending_chain is None:
            return
        while self.pending_chain.pending_reactor_ids and not self.pending_chain.options_by_unit.get(self.pending_chain.pending_reactor_ids[0]):
            self.pending_chain.pending_reactor_ids.pop(0)
        if not self.pending_chain.pending_reactor_ids:
            self.finalize_reaction_window()


