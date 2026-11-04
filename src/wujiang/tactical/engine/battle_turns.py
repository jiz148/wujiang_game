"""Battle BattleTurnMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleTurnMixin:
    """A cohesive Battle responsibility with no independent state."""

    def start_battle(self) -> None:
        from wujiang.tactical.engine.core import (
            DEFAULT_HERO_TURN_LIMIT,
            is_army_soldier,
        )
        command_count = len([unit for unit in self.all_units() if not unit.is_summon and not is_army_soldier(unit)])
        self.initial_hero_count = command_count
        configured_limit = int(getattr(self, "turn_timeout_limit", 0) or 0)
        self.turn_timeout_limit = configured_limit if configured_limit > 0 else DEFAULT_HERO_TURN_LIMIT
        if int(getattr(self, "turn_timeout_winner", 0) or 0) not in {1, 2}:
            self.turn_timeout_winner = 2
        self.completed_turns = 0
        self.ensure_turn_order()
        self.start_current_turn()


    def end_turn(self) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
            resolve_army_phase,
        )
        if self.pending_chain is not None:
            raise ActionError("当前正在等待连锁结算，不能结束回合。")
        if self.is_army_turn():
            player_id = self.army_turn_player_id()
            if player_id is not None:
                resolve_army_phase(self, player_id)
            self.resolve_turn_end()
            return
        else:
            self._processed_any_turn_end_choices.clear()
            hero = self.current_turn_unit()
            if hero is not None and hero.alive and not hero.banished:
                for component in list(hero.iter_components()):
                    before_end = getattr(component, "on_before_owner_turn_end", None)
                    if callable(before_end) and before_end(self):
                        if self.pending_chain is not None or self.pending_followup_actions:
                            self._pending_end_turn_after_followups = True
                            self.advance_followup_actions()
                            return
                        break
        self.finish_any_turn_end_choices()


    def finish_any_turn_end_choices(self) -> None:
        """Run optional hero-turn hooks before the shared turn-end cleanup."""
        if self.winner is None:
            for unit in list(self.all_units()):
                if not unit.alive or unit.banished or unit.position is None:
                    continue
                for component in list(unit.iter_components()):
                    hook = getattr(component, "on_before_any_turn_end", None)
                    key = (unit.unit_id, component.name)
                    if not callable(hook) or key in self._processed_any_turn_end_choices:
                        continue
                    self._processed_any_turn_end_choices.add(key)
                    hook(self)
                    if self.pending_chain is not None or self.pending_followup_actions:
                        self._pending_end_turn_after_followups = True
                        self.advance_followup_actions()
                        return
        self._processed_any_turn_end_choices.clear()
        self.resolve_turn_end()


    def is_army_turn(self) -> bool:
        from wujiang.tactical.engine.core import (
            parse_army_slot,
        )
        return parse_army_slot(self.current_turn_slot_unit_id()) is not None


    def army_turn_player_id(self) -> Optional[int]:
        from wujiang.tactical.engine.core import (
            parse_army_slot,
        )
        return parse_army_slot(self.current_turn_slot_unit_id())


    def has_command_hero_slots(self) -> bool:
        from wujiang.tactical.engine.core import (
            parse_army_slot,
        )
        return any(parse_army_slot(unit_id) is None for unit_id in self.turn_order_unit_ids)


    def set_army_order(
        self,
        player_id: int,
        order: str,
        direction: str | None = None,
        kind: str | None = None,
        stride: str | None = None,
        ammo: str | None = None,
    ) -> dict[str, str]:
        from wujiang.tactical.engine.army import (
            apply_army_order,
            command_for_kind,
            normalize_army_command,
            normalize_army_kind,
            present_army_kinds,
        )

        team_id = 1 if int(player_id) != 2 else 2
        target_kind = None if kind in {None, ""} else normalize_army_kind(kind)
        previous = command_for_kind(self.army_orders, team_id, target_kind or "infantry")
        allowed_ammo = None
        ammo_options = (getattr(self, "siege_ammo_by_player", None) or {}).get(team_id)
        if ammo_options:
            allowed_ammo = [str(item.get("id") or item) for item in ammo_options]
        command = normalize_army_command(
            order,
            direction,
            player_id=team_id,
            previous=previous,
            stride=stride,
            ammo=ammo,
            allowed_ammo=allowed_ammo,
        )
        apply_army_order(
            self.army_orders,
            team_id,
            command,
            kind=target_kind,
            kinds=present_army_kinds(self, team_id) or None,
        )
        return dict(command)


    def ensure_turn_order(self) -> None:
        from wujiang.tactical.engine.core import (
            command_hero_units,
            is_army_soldier,
            with_army_turn_slots,
        )
        if self.turn_order_unit_ids:
            self.turn_order_unit_ids = with_army_turn_slots(self, self.turn_order_unit_ids)
            if self.turn_slot_index >= len(self.turn_order_unit_ids):
                self.turn_slot_index = 0
            self.active_turn_unit_id = self.current_turn_slot_unit_id()
            return
        hero_ids = [unit.unit_id for unit in command_hero_units(self, 1)] + [
            unit.unit_id for unit in command_hero_units(self, 2)
        ]
        if not hero_ids and not any(is_army_soldier(unit) for unit in self.all_units()):
            self.active_turn_unit_id = None
            return
        self.configure_turn_order(hero_ids)


    def configure_turn_order(self, hero_unit_ids: Iterable[str], *, starting_index: int = 0) -> None:
        from wujiang.tactical.engine.core import (
            with_army_turn_slots,
        )
        self._exclusive_turn_unit_id = None
        self._pending_exclusive_turn_unit_id = None
        self.turn_order_unit_ids = with_army_turn_slots(self, hero_unit_ids)
        if not self.turn_order_unit_ids:
            self.turn_slot_index = 0
            self.active_turn_unit_id = None
            return
        self.turn_slot_index = max(0, min(int(starting_index), len(self.turn_order_unit_ids) - 1))
        self.active_turn_unit_id = self.turn_order_unit_ids[self.turn_slot_index]


    def current_turn_slot_unit_id(self) -> Optional[str]:
        if getattr(self, "_exclusive_turn_unit_id", None):
            return self._exclusive_turn_unit_id
        if not self.turn_order_unit_ids:
            return None
        if self.turn_slot_index >= len(self.turn_order_unit_ids):
            self.turn_slot_index = 0
        return self.turn_order_unit_ids[self.turn_slot_index]


    def current_turn_unit(self) -> Optional[Unit]:
        from wujiang.tactical.engine.core import (
            parse_army_slot,
        )
        unit_id = self.current_turn_slot_unit_id()
        if unit_id is None or parse_army_slot(unit_id) is not None:
            return None
        return self.units.get(unit_id)


    def controlling_hero_id(self, unit: Unit | None) -> Optional[str]:
        if unit is None:
            return None
        controller_id = getattr(unit, "controller_hero_id", None)
        if controller_id:
            return controller_id
        if not unit.is_summon:
            return unit.unit_id
        if unit.mount_owner_id:
            return self.controlling_hero_id(self.units.get(unit.mount_owner_id))
        if unit.summoner_id:
            return self.controlling_hero_id(self.units.get(unit.summoner_id))
        return None


    def unit_turn_slot_id(self, unit: Unit) -> Optional[str]:
        # A control transfer changes the controller, not the original turn bundle.
        preserved = getattr(unit, "preserved_turn_slot_id", None)
        if preserved:
            return preserved
        if not unit.is_summon:
            return unit.unit_id
        parent = self.units.get(unit.mount_owner_id or unit.summoner_id or "")
        return self.unit_turn_slot_id(parent) if parent is not None else None


    def transfer_unit_control(self, unit: Unit, controller: Unit) -> None:
        if unit.player_id == controller.player_id:
            return
        unit.preserved_turn_slot_id = self.unit_turn_slot_id(unit)
        for rider in self.riders_for(unit):
            self.clear_mounted_state(rider)
        unit.player_id = controller.player_id
        unit.controller_hero_id = self.controlling_hero_id(controller)
        unit.owner_seat_id = getattr(controller, "owner_seat_id", None)


    def turn_bundle_units(
        self,
        hero: Unit | str | None,
        *,
        include_banished: bool = True,
    ) -> list[Unit]:
        from wujiang.tactical.engine.core import (
            Unit,
            is_army_soldier,
        )
        hero_id = hero.unit_id if isinstance(hero, Unit) else hero
        if not hero_id:
            return []
        if getattr(self, "_exclusive_turn_unit_id", None) == hero_id:
            hero_unit = self.units.get(hero_id)
            return [hero_unit] if hero_unit is not None and hero_unit.alive and (include_banished or not hero_unit.banished) else []
        if self.legacy_player_turn_mode:
            hero_unit = self.units.get(hero_id)
            if hero_unit is None:
                return []
            units = [
                unit
                for unit in self.all_units()
                if unit.alive
                and (self.units.get(self.unit_turn_slot_id(unit) or "", unit).player_id
                     if getattr(unit, "preserved_turn_slot_id", None) else unit.player_id) == hero_unit.player_id
                and not is_army_soldier(unit)
                and (include_banished or not unit.banished)
            ]
            units.sort(key=lambda unit: (0 if unit.unit_id == hero_id else 1, unit.unit_id))
            return units
        units = [
            unit
            for unit in self.all_units()
            if unit.alive
            and self.unit_turn_slot_id(unit) == hero_id
            and not is_army_soldier(unit)
            and (include_banished or not unit.banished)
        ]
        units.sort(key=lambda unit: (0 if unit.unit_id == hero_id else 1, unit.unit_id))
        return units


    def current_turn_bundle_units(self, *, include_banished: bool = True) -> list[Unit]:
        return [unit for unit in self.turn_bundle_units(self.current_turn_slot_unit_id(), include_banished=include_banished)
                if unit.player_id == self.active_player]


    def unit_belongs_to_current_turn(self, unit: Unit | None) -> bool:
        from wujiang.tactical.engine.core import (
            is_army_soldier,
        )
        if unit is None:
            return False
        if getattr(self, "_exclusive_turn_unit_id", None):
            return unit.unit_id == self._exclusive_turn_unit_id
        if self.is_army_turn() and is_army_soldier(unit):
            return unit.player_id == self.army_turn_player_id()
        if is_army_soldier(unit):
            return False
        if self.legacy_player_turn_mode:
            hero = self.current_turn_unit()
            slot_owner = self.units.get(self.unit_turn_slot_id(unit) or "", unit)
            return hero is not None and slot_owner.player_id == hero.player_id
        return self.unit_turn_slot_id(unit) == self.current_turn_slot_unit_id()


    def clone_can_act_this_turn(self, unit: Unit) -> bool:
        if not unit.is_clone:
            return True
        root_id = self.unit_turn_slot_id(unit)
        return not any(other.is_clone and other.unit_id != unit.unit_id
                       and self.unit_turn_slot_id(other) == root_id
                       and getattr(other, "_clone_action_turn", None) == self.turn_number
                       for other in [*self.all_units(), *self.destroyed_units])


    def destroy_clone_for_skill_effect(self, target: Unit, *, source: Unit | None, action_name: str) -> bool:
        if not target.is_clone or not target.alive or target.direct_effects_blocked():
            return False
        target.current_hp = 0.0
        target.alive = False
        self.log_public_event(f"{target.name} 的分身受到【{action_name}】技能效果而消散。", source=source, target=target)
        self.cleanup_dead_units()
        return True


    def units_can_act_in_current_turn(self) -> bool:
        return any(unit.can_take_turn_actions(self) for unit in self.current_turn_bundle_units())


    def advance_turn_slot_index(self) -> None:
        pending = getattr(self, "_pending_exclusive_turn_unit_id", None)
        self._pending_exclusive_turn_unit_id = None
        if pending is not None:
            self._exclusive_turn_unit_id = pending
            self.active_turn_unit_id = pending
            self.turn_number += 1
            return
        self._exclusive_turn_unit_id = None
        if not self.turn_order_unit_ids:
            self.active_turn_unit_id = None
            return
        previous_index = self.turn_slot_index
        self.turn_slot_index = (self.turn_slot_index + 1) % len(self.turn_order_unit_ids)
        if self.turn_slot_index <= previous_index:
            self.round_number += 1
            for effect in list(self.field_effects):
                hook = getattr(effect, "on_round_complete", None)
                if callable(hook):
                    hook(self)
        self.turn_number += 1
        self.active_turn_unit_id = self.turn_order_unit_ids[self.turn_slot_index]


    def start_current_turn(self) -> None:
        from wujiang.tactical.engine.core import (
            is_army_soldier,
            living_army_units,
            parse_army_slot,
            resolve_army_phase,
        )
        self.ensure_turn_order()
        if self.winner is not None or not self.turn_order_unit_ids:
            return
        attempts = 0
        while attempts < len(self.turn_order_unit_ids):
            # Dead slots can be skipped below. Check at each candidate so a
            # skipped final slot cannot give either side an extra action.
            chess_cap = int(getattr(self, "chess_round_cap", 0) or 0)
            if chess_cap and self.round_number > chess_cap:
                from wujiang.tactical.rooms.autochess import chess_timeout_result
                self.cleanup_dead_units()
                if self.winner is not None:
                    return
                result = chess_timeout_result(self)
                self.chess_timeout_result = result
                self.winner = int(result["winner"])
                self.win_reason_code = "chess_timeout"
                timeout_reason = ("存活单位数" if result["criterion"] == "units"
                                  else "单位数相同后的随机结果")
                self.win_reason_text = (f"自走棋进行 {chess_cap} 个完整战斗轮后，"
                                        f"按{timeout_reason}判定玩家 {self.winner} 获胜，"
                                        f"败方应扣 {result['damage']} 血。")
                self.log(self.win_reason_text)
                self._append_summary_event("match_end", actor_unit_id=None, actor_name="系统",
                                           actor_player_id=self.winner, target_name="",
                                           action_name="自走棋超时判定", amount=0)
                self._emit_replay_checkpoint("match_end")
                return
            hero_id = self.current_turn_slot_unit_id()
            army_player_id = parse_army_slot(hero_id)
            if army_player_id is not None:
                self.active_turn_unit_id = hero_id
                self.active_player = army_player_id
                if self.completed_turns > 0 and self.has_command_hero_slots():
                    if living_army_units(self, army_player_id):
                        resolve_army_phase(self, army_player_id)
                    if self.winner is not None:
                        return
                    self.resolve_turn_end(auto_started=True)
                    return
                self.log(f"第 {self.round_number} 轮，玩家 {army_player_id} 的军队回合开始。")
                self.check_win_condition()
                return
            hero = self.units.get(hero_id) if hero_id else None
            if (hero is None or not hero.alive or ("arena" in getattr(self, "special_mode_codes", ()) and hero.banished)
                    or hero.is_summon or is_army_soldier(hero)
                    or getattr(hero, "equipped_to_id", None)):
                self.advance_turn_slot_index()
                attempts += 1
                continue
            self.active_turn_unit_id = hero.unit_id
            self.active_player = hero.player_id
            self._turn_control_completed_players = set()
            self.pending_respawn_unit_ids = []
            bundle_units = self.turn_bundle_units(hero, include_banished=True)
            for unit in bundle_units:
                unit.refresh_for_turn(self)
            for unit in bundle_units:
                if not unit.banished or getattr(unit, "attached_to_unit_id", None):
                    continue
                if unit.banish_turns_remaining > 0:
                    unit.banish_turns_remaining = max(unit.banish_turns_remaining - 1, 0)
                if unit.banish_turns_remaining == 0:
                    self.schedule_respawn(unit)
            self.advance_respawn_queue()
            self.enforce_sandstorm_stealth_rules()
            for observer in list(self.all_units()):
                for component in list(observer.iter_components()):
                    component.on_any_turn_start(self, hero)
            for effect in list(self.field_effects):
                effect.on_turn_start(self, hero)
            self.log(f"第 {self.round_number} 轮，玩家 {hero.player_id} 的【{hero.name}】回合开始。")
            self.check_win_condition()
            if self.winner is not None:
                return
            return


    def peek_next_turn_unit(self) -> Optional[Unit]:
        from wujiang.tactical.engine.core import (
            is_army_soldier,
            parse_army_slot,
        )
        if self.winner is not None or not self.turn_order_unit_ids:
            return None
        total = len(self.turn_order_unit_ids)
        for offset in range(1, total + 1):
            index = (self.turn_slot_index + offset) % total
            slot_id = self.turn_order_unit_ids[index]
            if parse_army_slot(slot_id) is not None:
                continue
            hero = self.units.get(slot_id)
            if (hero is None or not hero.alive or hero.is_summon or is_army_soldier(hero)
                    or getattr(hero, "equipped_to_id", None)):
                continue
            return hero
        return None


    def resolve_turn_end(self, *, auto_started: bool = False) -> None:
        hero = self.current_turn_unit()
        ending_player = self.active_player
        bundle_units = self.turn_bundle_units(self.current_turn_slot_unit_id(), include_banished=True)
        completed_players = getattr(self, "_turn_control_completed_players", set())
        completed_players.add(ending_player)
        self._turn_control_completed_players = completed_players
        pending_players = sorted({unit.player_id for unit in bundle_units if unit.alive and not unit.banished}
                                 - completed_players)
        if pending_players:
            self.active_player = pending_players[0]
            self.log(f"原回合槽内交接给玩家 {self.active_player} 操作取得控制权的单位。")
            self._emit_replay_checkpoint("control_handoff")
            return
        # The shared global end still belongs to the original slot's side.
        if hero is not None:
            ending_player = hero.player_id
        else:
            destroyed_hero = next((unit for unit in self.destroyed_units
                                   if unit.unit_id == self.current_turn_slot_unit_id()), None)
            if destroyed_hero is not None:
                ending_player = destroyed_hero.player_id
        self._resolving_turn_end_player_id = ending_player
        if hero is not None and not auto_started:
            self.log(f"{hero.name} 结束了自己的回合。")
        elif self.is_army_turn() and not auto_started:
            self.log(f"玩家 {self.army_turn_player_id()} 的军队结束了回合。")
        for unit in bundle_units:
            unit.finish_turn(self)
        for unit in self.all_units():
            if unit.total_shields() > 0:
                unit.clear_end_of_turn_shields()
        for effect in list(self.field_effects):
            effect.on_any_turn_end(self, ending_player)
        for unit in self.all_units():
            for component in list(unit.iter_components()):
                component.on_any_turn_end(self, ending_player)
        for effect in list(self.field_effects):
            hook = getattr(effect, "on_after_any_turn_end", None)
            if callable(hook):
                hook(self, ending_player)
        if self.pending_followup_actions:
            self._pending_turn_end_after_effects_player_id = ending_player
            self.advance_followup_actions()
            return
        self.complete_turn_end_after_effects(ending_player)


    def complete_turn_end_after_effects(self, ending_player: int) -> None:
        from wujiang.tactical.engine.core import (
            parse_army_slot,
        )
        self._resolving_turn_end_player_id = None
        if parse_army_slot(self.current_turn_slot_unit_id()) is None:
            self.advance_hell_rebirths(ending_player)
        self.enforce_sandstorm_stealth_rules()
        self.cleanup_dead_units()
        if self.winner is not None:
            self._emit_replay_checkpoint("match_end")
            return
        self.completed_turns += 1
        self._emit_replay_checkpoint("turn_end")
        if self.turn_timeout_limit > 0 and self.completed_turns >= self.turn_timeout_limit:
            winner = int(getattr(self, "turn_timeout_winner", 2) or 2)
            if winner not in {1, 2}:
                winner = 2
            self.winner = winner
            self.win_reason_code = "turn_limit"
            self.win_reason_text = f"达到 {self.turn_timeout_limit} 个武将回合上限后判定玩家 {self.winner} 获胜。"
            self._append_summary_event(
                "match_end",
                actor_unit_id=None,
                actor_name="系统",
                actor_player_id=self.winner,
                target_name="",
                action_name="回合上限判定",
                amount=0,
            )
            self.log(
                f"对局已达到 {self.turn_timeout_limit} 个武将回合上限，"
                f"判定玩家 {self.winner} 获胜。"
            )
            self._emit_replay_checkpoint("match_end")
            return
        if self._pending_turn_advance_after_hell_rebirth:
            if self.current_respawn_prompt() is not None:
                return
            self._pending_turn_advance_after_hell_rebirth = False
        self.advance_turn_slot_index()
        self.start_current_turn()


