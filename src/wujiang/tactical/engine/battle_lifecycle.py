"""Battle BattleLifecycleMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleLifecycleMixin:
    """A cohesive Battle responsibility with no independent state."""

    def respawn_options_for(self, unit: Unit) -> list[Position]:
        from wujiang.tactical.engine.core import (
            Position,
        )
        if unit.unit_id in self.hell_rebirth_unit_ids:
            return self.hell_rebirth_options_for(unit)
        origin = unit.banish_return_position or unit.position
        if origin is None:
            return []
        if self.can_place_unit(unit, origin, ignore=unit, mover=unit):
            return [origin]
        best_distance: Optional[int] = None
        options: list[Position] = []
        for y in range(self.height):
            for x in range(self.width):
                cell = Position(x, y)
                if not self.can_place_unit(unit, cell, ignore=unit, mover=unit):
                    continue
                distance = origin.distance_to(cell)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    options = [cell]
                elif distance == best_distance:
                    options.append(cell)
        return sorted(options, key=lambda cell: (cell.y, cell.x))


    def current_respawn_prompt(self) -> Optional[RespawnPrompt]:
        from wujiang.tactical.engine.core import (
            Position,
            RespawnPrompt,
        )
        if self.winner is not None:
            return None
        while self.pending_respawn_unit_ids:
            unit_id = self.pending_respawn_unit_ids[0]
            unit = self.units.get(unit_id)
            hell_rebirth = unit_id in self.hell_rebirth_unit_ids
            if unit is None and hell_rebirth:
                unit = next((item for item in self.destroyed_units if item.unit_id == unit_id), None)
            if unit is None or (not hell_rebirth and (not unit.alive or not unit.banished)):
                self.pending_respawn_unit_ids.pop(0)
                continue
            origin = (unit.last_position if hell_rebirth else unit.banish_return_position or unit.position)
            if origin is None:
                origin = Position(0, 0) if hell_rebirth else None
                if origin is None:
                    self.pending_respawn_unit_ids.pop(0)
                    continue
            options = self.respawn_options_for(unit)
            if not options:
                self.pending_respawn_unit_ids.pop(0)
                self.log(f"{unit.name} 暂时没有可重新出现的空格，将继续等待。")
                continue
            return RespawnPrompt(unit.unit_id, unit.player_id, origin, options,
                                 "hell_rebirth" if hell_rebirth else "banish")
        return None


    def restore_banished_unit(self, unit: Unit, destination: Position) -> None:
        origin = unit.banish_return_position or unit.position
        unit.banished = False
        unit.banish_turns_remaining = 0
        unit.position = destination
        self.sync_linked_units()
        if origin is not None and destination == origin:
            self.log(f"{unit.name} 在原位重新出现。")
        else:
            self.log(f"{unit.name} 在 ({destination.x}, {destination.y}) 重新出现。")
        self.clear_all_stealth_if_all_heroes_stealthed()


    def schedule_respawn(self, unit: Unit) -> None:
        options = self.respawn_options_for(unit)
        if not options:
            self.log(f"{unit.name} 暂时没有可重新出现的空格，将继续等待。")
            return
        origin = unit.banish_return_position or unit.position
        if origin is not None and len(options) == 1 and options[0] == origin:
            self.restore_banished_unit(unit, origin)
            return
        if unit.unit_id not in self.pending_respawn_unit_ids:
            self.pending_respawn_unit_ids.append(unit.unit_id)
            self.log(f"{unit.name} 即将重新出现，请选择其落点。")


    def side_still_has_a_fighter(self, player_id: int) -> bool:
        """A pending hell return is not a living body and cannot keep the match open."""
        return any(
            not bool(getattr(unit, "is_siege_structure", False))
            for unit in self.hero_units(player_id)
        )


    def schedule_hell_rebirth(self, unit: Unit) -> None:
        """Queue a return only while this side still has someone on the field."""
        if unit.unit_id in self.hell_rebirth_unit_ids or self.winner is not None:
            return
        self.hell_rebirth_unit_ids.add(unit.unit_id)
        if getattr(self, "_resolving_turn_end_player_id", None) not in {None, unit.player_id}:
            unit._hell_rebirth_skip_turn_number = self.turn_number
        self.log(f"{unit.name} 将在下个敌方武将回合结束时选择回场位置。")


    def hell_dragon_return_options_for(self, unit: Unit) -> list[Position]:
        origins = self.hell_dragon_return_cells.get(unit.unit_id, [])
        if unit.alive or unit.unit_id not in {body.unit_id for body in self.destroyed_units}:
            return []
        return [cell for cell in origins if self.can_place_unit(unit, cell, mover=unit)]


    def restore_hell_dragon(self, unit: Unit, destination: Position) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
        )
        if self.winner is not None:
            raise ActionError("对局已结束，献祭复活不再回场。")
        if destination not in self.hell_dragon_return_options_for(unit):
            raise ActionError("古龙献祭者的原格暂不能完整容纳古龙。")
        for status in list(unit.statuses):
            if status.duration is not None:
                unit.remove_status(status, self)
        unit.alive = True
        unit.banished = False
        unit.current_hp = unit.max_health
        unit.current_mana = unit.max_mana()
        unit.turn_ready = False
        unit.can_act_on_entry_turn = True
        unit.move_used = False
        unit.normal_move_actions_used = 0
        unit.normal_move_steps_used = 0
        unit.attacks_used = 0
        unit.performed_active_skill = False
        unit.moved_this_turn = False
        unit.actions_taken_this_turn = []
        unit.clear_end_of_turn_shields()
        unit.position = destination
        for component in list(unit.iter_components()):
            callback = getattr(component, "on_restored_body", None)
            if callable(callback):
                callback(self)
        self.destroyed_units = [body for body in self.destroyed_units if body.unit_id != unit.unit_id]
        self.hell_dragon_return_cells.pop(unit.unit_id, None)
        self.units[unit.unit_id] = unit
        self.log_public_event(f"{unit.name} 在献祭者原格 ({destination.x}, {destination.y}) 满状态复活。", target=unit)
        self.notify_destroyed_hero_count_changed()
        self.sync_linked_units()


    def hell_rebirth_options_for(self, unit: Unit) -> list[Position]:
        enemy_anchors_only = any(getattr(component, "rebirth_enemy_anchors_only", False)
                                 for component in unit.iter_components())
        cells = {cell for anchor in self.all_units()
                 if anchor.alive and not anchor.banished and anchor.position is not None
                 and (not enemy_anchors_only or anchor.player_id != unit.player_id)
                 for body in self.unit_cells(anchor) for cell in self.neighbors(body)}
        extra_legal = next((callback for component in unit.iter_components()
                            if callable(callback := getattr(component, "hell_rebirth_destination_legal", None))), None)
        return sorted((cell for cell in cells if self.can_place_unit(unit, cell, mover=unit)
                       and (extra_legal is None or extra_legal(self, cell))),
                      key=lambda cell: (cell.y, cell.x))


    def advance_hell_rebirths(self, ended_player_id: int) -> None:
        if self.winner is not None:
            return
        pending_bodies = {unit.unit_id: unit for unit in [*self.destroyed_units, *self.all_units()]
                          if not unit.alive}
        for unit in pending_bodies.values():
            if (unit.unit_id not in self.hell_rebirth_unit_ids or unit.player_id == ended_player_id
                    or getattr(unit, "_hell_rebirth_skip_turn_number", None) == self.turn_number):
                continue
            if not self.hell_rebirth_options_for(unit):
                self.log(f"{unit.name} 周围暂无合法回场格，等待下个敌方武将回合结束。")
                continue
            if unit.unit_id not in self.pending_respawn_unit_ids:
                self.pending_respawn_unit_ids.append(unit.unit_id)
                self.log(f"{unit.name} 的地狱再临已到时，请选择落点。")
                self._pending_turn_advance_after_hell_rebirth = True


    def restore_hell_rebirth(self, unit: Unit, destination: Position) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
        )
        if self.winner is not None:
            raise ActionError("对局已结束，地狱再临不再回场。")
        if unit.unit_id not in self.hell_rebirth_unit_ids or destination not in self.hell_rebirth_options_for(unit):
            raise ActionError("地狱再临落点已失效。")
        for status in list(unit.statuses):
            if status.duration is not None:
                unit.remove_status(status, self)
        unit.alive = True
        unit.banished = False
        unit.current_hp = unit.max_health
        unit.current_mana = 0
        unit._hell_rebirth_skip_turn_number = None
        unit.turn_ready = False
        unit.can_act_on_entry_turn = True
        unit.move_used = False
        unit.normal_move_actions_used = 0
        unit.normal_move_steps_used = 0
        unit.attacks_used = 0
        unit.performed_active_skill = False
        unit.moved_this_turn = False
        unit.actions_taken_this_turn = []
        unit.clear_end_of_turn_shields()
        unit.position = destination
        self.destroyed_units = [item for item in self.destroyed_units if item.unit_id != unit.unit_id]
        self.units[unit.unit_id] = unit
        self.hell_rebirth_unit_ids.discard(unit.unit_id)
        self.log(f"{unit.name} 以0魔在 ({destination.x}, {destination.y}) 地狱再临。")
        for component in list(unit.iter_components()):
            callback = getattr(component, "on_hell_rebirth", None)
            if callable(callback):
                callback(self)
        self.notify_destroyed_hero_count_changed()
        self.sync_linked_units()


    def restore_destroyed_hell_ally(self, unit: Unit, destination: Position) -> bool:
        """Bring back one destroyed original body at the hunter's old cell."""
        if (unit.alive or unit not in self.destroyed_units
                or (unit.is_summon and unit.summoner_id and not (
                    self.units.get(unit.summoner_id) and self.units[unit.summoner_id].alive))
                or not self.can_place_unit(unit, destination, mover=unit)):
            return False
        for status in list(unit.statuses):
            if status.duration is not None:
                unit.remove_status(status, self)
        unit.alive = True
        unit.banished = False
        unit.current_hp = unit.max_health
        unit.current_mana = 0
        for component in list(unit.iter_components()):
            callback = getattr(component, "on_restored_body", None)
            if callable(callback):
                callback(self)
        unit.turn_ready = False
        unit.can_act_on_entry_turn = False
        unit.move_used = False
        unit.normal_move_actions_used = 0
        unit.normal_move_steps_used = 0
        unit.attacks_used = 0
        unit.performed_active_skill = False
        unit.moved_this_turn = False
        unit.actions_taken_this_turn = []
        unit.clear_end_of_turn_shields()
        unit.position = destination
        self.destroyed_units = [body for body in self.destroyed_units if body.unit_id != unit.unit_id]
        self.hell_rebirth_unit_ids.discard(unit.unit_id)
        self.pending_respawn_unit_ids = [unit_id for unit_id in self.pending_respawn_unit_ids if unit_id != unit.unit_id]
        self.units[unit.unit_id] = unit
        for component in list(unit.iter_components()):
            callback = getattr(component, "on_hell_rescue", None)
            if callable(callback):
                callback(self)
        self.log_public_event(f"{unit.name} 以0魔在 ({destination.x}, {destination.y}) 被地狱猎人救回。", target=unit)
        self.notify_destroyed_hero_count_changed()
        self.sync_linked_units()
        return True


    def advance_respawn_queue(self) -> None:
        while True:
            prompt = self.current_respawn_prompt()
            if prompt is None:
                return
            if (prompt.unit_id not in self.hell_rebirth_unit_ids
                    and len(prompt.options) == 1 and prompt.options[0] == prompt.origin):
                unit = self.get_unit(prompt.unit_id)
                self.pending_respawn_unit_ids.pop(0)
                self.restore_banished_unit(unit, prompt.origin)
                continue
            return


    def cleanup_dead_units(self) -> None:
        for effect in list(self.field_effects):
            hook = getattr(effect, "on_before_dead_cleanup", None)
            if callable(hook):
                hook(self)
        while True:
            dead_ids = {unit.unit_id for unit in self.all_units() if not unit.alive}
            chained_summons = [
                unit
                for unit in self.all_units()
                if unit.alive and unit.summoner_id in dead_ids
            ]
            if not chained_summons:
                break
            for summon in chained_summons:
                summon.alive = False
                self.log(f"{summon.name} \u7684\u53ec\u5524\u8005\u5df2\u88ab\u51fb\u7834\uff0c\u56e0\u6b64\u4e00\u5e76\u6d88\u6563\u3002")
        while True:
            # A death callback may deal damage and enter cleanup again. Its
            # owner is still registered until remove_unit returns; the nested
            # pass must leave that corpse to the outer pass.
            dead_units = [unit for unit in self.all_units()
                          if not unit.alive and not getattr(unit, "_removal_in_progress", False)]
            if not dead_units:
                break
            for unit in dead_units:
                self.capture_destruction_position(unit)
                if unit.unit_id not in {destroyed.unit_id for destroyed in self.destroyed_units}:
                    self.destroyed_units.append(unit)
                self.remove_unit(unit)
            # Death traits can destroy additional units. Resolve their real
            # deaths and dependent summons before judging victory.
            chained_summons = [unit for unit in self.all_units()
                               if unit.alive and unit.summoner_id in {body.unit_id for body in dead_units}]
            for summon in chained_summons:
                summon.alive = False
                self.log(f"{summon.name} 的召唤者已被击破，因此一并消散。")
        for unit in self.all_units():
            for status in list(unit.statuses):
                if (getattr(status, "remove_when_source_destroyed", False)
                        and self.units.get(getattr(status, "source_unit_id", "")) is None):
                    unit.remove_status(status, self)
        self.hell_rebirth_unit_ids.intersection_update(
            unit.unit_id for unit in self.destroyed_units if not unit.alive
        )
        self.sync_linked_units()
        self.notify_destroyed_hero_count_changed()
        self.clear_all_stealth_if_all_heroes_stealthed()
        self.check_win_condition()
        for effect in list(self.field_effects):
            hook = getattr(effect, "on_after_dead_cleanup", None)
            if callable(hook):
                hook(self)


    def destroyed_hero_count(self) -> int:
        from wujiang.tactical.engine.core import (
            HeroUnit,
            is_army_soldier,
        )
        units = {unit.unit_id: unit for unit in [*self.destroyed_units, *self.all_units()]}
        return sum(not unit.alive and isinstance(unit, HeroUnit)
                   and not unit.is_summon and not unit.is_clone and not is_army_soldier(unit)
                   for unit in units.values())


    def notify_destroyed_hero_count_changed(self) -> None:
        from wujiang.tactical.engine.core import (
            direct_scoring,
        )
        count = self.destroyed_hero_count()
        if direct_scoring():
            from wujiang.tactical.direct_queries import empty_hook_pass
            if empty_hook_pass(self, "on_destroyed_hero_count_changed"):
                return
        for unit in self.all_units():
            for component in list(unit.iter_components()):
                component.on_destroyed_hero_count_changed(self, count)


    def banish_unit(self, unit: Unit, turns: int) -> None:
        if unit.direct_effects_blocked():
            return
        action = self.resolving_action
        if (action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"}
                and not action.payload.get("from_field_effect")
                and self.destroy_clone_for_skill_effect(unit, source=self.units.get(action.actor_id), action_name=action.display_name)):
            return
        if unit.allows_multiple_riders:
            for rider in self.riders_for(unit):
                self.clear_mounted_state(rider)
        unit.banished = True
        unit.banish_turns_remaining = turns
        unit.banish_return_position = unit.position
        for component in list(unit.iter_components()):
            component.on_owner_banished(self)
        self.sync_linked_units()
        self.log(f"{unit.name} 消失了，暂时无法行动。")
        self.clear_all_stealth_if_all_heroes_stealthed()


    def summon_unit(
        self,
        unit: Unit,
        position: Position,
        *,
        summoner: Optional[Unit] = None,
        allow_stealth_overlap: bool = False,
    ) -> None:
        unit.summoner_id = summoner.unit_id if summoner is not None else None
        unit.can_act_on_entry_turn = True
        unit.turn_ready = False
        self.add_unit(unit, position, allow_stealth_overlap=allow_stealth_overlap)
        self.log(f"{unit.name} 被召唤到战场。")


    def check_win_condition(self) -> None:
        from wujiang.tactical.engine.core import (
            forecasting_active,
        )
        if self.winner is not None or self.pending_followup_actions or self.pending_chain is not None:
            return
        alive_players = {
            player_id
            for player_id in (1, 2)
            if self.side_still_has_a_fighter(player_id)
        }
        # A hunter's death rescue resolves in the current action. Finish that
        # already-declared rescue before judging whether its team was wiped out.
        # Delayed hell rebirths and the dragon's optional return do not count.
        pending_effects = list(self.pending_followup_actions)
        if self.pending_chain is not None:
            pending_effects.append(self.pending_chain.queued_action)
        for action in pending_effects:
            if action.payload.get("effect_code") != "hell_hunter_rescue":
                continue
            rescued_id = str(action.payload.get("rescued_unit_id") or "")
            rescued = next((unit for unit in self.destroyed_units
                            if unit.unit_id == rescued_id and not unit.alive
                            and not unit.is_summon and not unit.is_clone), None)
            if rescued is not None:
                alive_players.add(rescued.player_id)
        if (forecasting_active() and getattr(self, "_ai_has_unseen_enemies", False)
                and alive_players == {getattr(self, "_ai_view_player_id", None)}):
            return  # Unseen enemies omitted from scoring are not a public win.
        if len(alive_players) == 1 and self.units:
            self.winner = alive_players.pop()
            losing_player = 2 if self.winner == 1 else 1
            waiting_returns = set(self.hell_rebirth_unit_ids)
            self.pending_respawn_unit_ids = [
                unit_id for unit_id in self.pending_respawn_unit_ids if unit_id not in waiting_returns
            ]
            self.hell_rebirth_unit_ids.clear()
            self.hell_dragon_return_cells.clear()
            self.win_reason_code = "elimination"
            self.win_reason_text = f"玩家 {losing_player} 的全部武将被击破。"
            self._append_summary_event(
                "match_end",
                actor_unit_id=None,
                actor_name="系统",
                actor_player_id=self.winner,
                target_name=f"玩家 {losing_player}",
                action_name="全部武将被击破",
                amount=0,
            )
            self.log(f"玩家 {self.winner} 获胜。")
            self._emit_replay_checkpoint("match_end")


