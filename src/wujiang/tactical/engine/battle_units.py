"""Battle BattleUnitsMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleUnitsMixin:
    """A cohesive Battle responsibility with no independent state."""

    def add_unit(self, unit: Unit, position: Position, *, allow_stealth_overlap: bool = False, trigger_enter_effects: bool = True) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
            weakref,
        )
        if not self.can_place_unit(
            unit,
            position,
            ignore=unit,
            mover=unit,
            allow_stealth_overlap=allow_stealth_overlap,
        ):
            raise ActionError("目标位置已被占用。")
        unit.position = position
        unit._battle_ref = weakref.ref(self)
        if unit.is_summon and not unit.can_act_on_entry_turn and self.current_turn_unit() is not None:
            # Direct add_unit summons follow the same entry-turn delay as summon_unit.
            # Explicit entry-action exceptions set can_act_on_entry_turn before placement.
            unit.turn_ready = False
            unit.can_act_on_entry_turn = True
        if unit.is_summon and not hasattr(unit, "original_weather_race"):
            parent = self.units.get(unit.summoner_id or "")
            unit.original_weather_race = getattr(parent, "original_weather_race", parent.race if parent else unit.race)
        self.units[unit.unit_id] = unit
        self.log(f"{unit.name} 进入战场。")
        for component in list(unit.iter_components()):
            if trigger_enter_effects:
                component.on_enter_battle(self)
            component.on_destroyed_hero_count_changed(self, self.destroyed_hero_count())
        for effect in list(self.field_effects):
            effect.on_unit_entered(self, unit)


    def remove_unit(self, unit: Unit) -> None:
        if unit.unit_id not in self.units or getattr(unit, "_removal_in_progress", False):
            return
        self.capture_destruction_position(unit)
        unit._removal_in_progress = True
        try:
            self._remove_unit(unit)
        finally:
            unit._removal_in_progress = False


    def capture_destruction_position(self, unit: Unit) -> None:
        if unit.alive or unit.position is None:
            return
        action = self.resolving_action
        skill_death = getattr(unit, "_hell_death_from_damage", None)
        if hasattr(unit, "_hell_death_from_damage"):
            delattr(unit, "_hell_death_from_damage")
        if skill_death is None:
            skill_death = action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"}
        for component in list(unit.iter_components()):
            callback = getattr(component, "on_hell_dragon_destroyed", None)
            if callable(callback):
                callback(self)
        if skill_death and not getattr(unit, "_suppress_hell_forced_skill_death", False):
            for component in list(unit.iter_components()):
                callback = getattr(component, "on_hell_forced_skill_death", None)
                if callable(callback):
                    callback(self)
        if hasattr(unit, "_suppress_hell_forced_skill_death"):
            delattr(unit, "_suppress_hell_forced_skill_death")
        # Damage may temporarily use the declared origin; death belongs to the real body.
        unit.last_position = getattr(unit, "_resolution_actual_position", None) or unit.position
        unit.last_destroyed_cells = unit.footprint_cells_at(unit.last_position)
        unit.destruction_count = getattr(unit, "destruction_count", 0) + 1
        unit.position = None
        self.log(f"{unit.name} 被击破。")
        for observer in list(self.all_units()):
            for component in list(observer.iter_components()):
                component.on_unit_destroyed(self, unit)
        unit.__dict__.pop("_destroying_source_unit_id", None)
        if unit.is_summon and unit.summoner_id:
            summoner = self.units.get(unit.summoner_id)
            if summoner is not None and summoner.alive:
                for component in list(summoner.iter_components()):
                    component.on_owned_summon_destroyed(self, unit)


    def _remove_unit(self, unit: Unit) -> None:
        for component in list(unit.iter_components()):
            component.on_owner_removed(self)
        for rider in self.riders_for(unit):
            self.clear_mounted_state(rider)
        if unit.mounted_on_unit_id:
            self.clear_mounted_state(unit)
        if unit.unit_id in self.units:
            del self.units[unit.unit_id]


    def add_field_effect(self, effect: BattleFieldEffect, *, source: Unit | None = None) -> None:
        if ("soft_persimmon" in getattr(self, "special_mode_codes", ())
                and not getattr(effect, "global_weather", False)
                and not hasattr(effect, "source_unit_id")):
            origin = source or (self.units.get(self.resolving_action.actor_id)
                                if self.resolving_action is not None else None)
            if origin is not None:
                effect.source_unit_id = origin.unit_id
        candidates = self.field_effects
        if getattr(effect, "global_weather", False):
            owner = source.player_id if source is not None else getattr(effect, "weather_owner_player_id", None)
            if owner is None:
                owner = getattr(effect, "source_player_id", None)
            if owner is None:
                weather_source = self.units.get(str(getattr(effect, "source_unit_id", "")))
                owner = weather_source.player_id if weather_source is not None else None
            if owner is None and self.resolving_action is not None:
                action_source = self.units.get(self.resolving_action.actor_id)
                owner = action_source.player_id if action_source is not None else None
            effect.weather_owner_player_id = owner
            effect.weather_owner_player_ids = {owner} if owner is not None else set()
            for existing in list(self.field_effects):
                if owner is None or not getattr(existing, "global_weather", False):
                    continue
                owners = set(getattr(existing, "weather_owner_player_ids", set()))
                if not owners and getattr(existing, "weather_owner_player_id", None) is not None:
                    owners.add(existing.weather_owner_player_id)
                if owner not in owners or self.canonical_weather_name(getattr(existing, "weather_name", "")) == self.canonical_weather_name(getattr(effect, "weather_name", "")):
                    continue
                owners.remove(owner)
                if owners:
                    existing.weather_owner_player_ids = owners
                    existing.weather_owner_player_id = next(iter(owners))
                    if getattr(existing, "source_player_id", None) == owner:
                        existing.source_player_id = existing.weather_owner_player_id
                else:
                    self.remove_field_effect(existing)
            candidates = [item for item in self.field_effects if getattr(item, "global_weather", False)
                          and (owner in getattr(item, "weather_owner_player_ids", set())
                               or self.canonical_weather_name(getattr(item, "weather_name", "")) == self.canonical_weather_name(getattr(effect, "weather_name", "")))]
        if effect.merge_into_existing(self, candidates):
            if getattr(effect, "global_weather", False) and owner is not None:
                for existing in candidates:
                    if self.canonical_weather_name(getattr(existing, "weather_name", "")) == self.canonical_weather_name(getattr(effect, "weather_name", "")):
                        existing.weather_owner_player_ids = set(getattr(existing, "weather_owner_player_ids", set())) | {owner}
                        existing.weather_owner_player_id = owner
                        break
            self.enforce_sandstorm_stealth_rules()
            return
        self.field_effects.append(effect)
        self.log(f"场地效果【{effect.name}】生效。")
        self.enforce_sandstorm_stealth_rules()


    def remove_field_effect(self, effect: BattleFieldEffect) -> None:
        if effect in self.field_effects:
            self.field_effects.remove(effect)
            self.log(f"场地效果【{effect.name}】结束。")
            if getattr(effect, "weather_name", None):
                for unit in self.all_units():
                    unit.clamp_mana()


    def weather_effect_applies(self, effect: BattleFieldEffect, unit: Unit) -> bool:
        """One contribution per named weather at the recipient's real footprint."""
        name = self.canonical_weather_name(getattr(effect, "weather_name", ""))
        position = getattr(unit, "_resolution_actual_position", None) or unit.position
        cells = self.unit_cells_at(unit, position) if position is not None and not unit.banished else []
        if not cells:
            return False
        for existing in self.field_effects:
            if self.canonical_weather_name(getattr(existing, "weather_name", "")) != name:
                continue
            affected = existing.affected_cells(self)
            if (getattr(existing, "global_weather", False) and not affected) or any(cell in affected for cell in cells):
                return existing is effect
        return False


    def notify_field_basic_attack_finished(self, actor: Unit, payload: dict[str, Any], contexts: list[DamageContext], *, missed: bool = False) -> None:
        if getattr(self, "_measuring_attack_success", False):
            return
        for effect in list(self.field_effects):
            effect.on_basic_attack_finished(self, actor, payload, contexts, missed)


    def has_weather(self, name: str) -> bool:
        return any(self.canonical_weather_name(getattr(effect, "weather_name", "")) == self.canonical_weather_name(name) for effect in self.field_effects)


    def cell_has_weather(self, name: str, cell: Position) -> bool:
        for effect in self.field_effects:
            if self.canonical_weather_name(getattr(effect, "weather_name", "")) != self.canonical_weather_name(name):
                continue
            affected = effect.affected_cells(self)
            if not affected:
                if getattr(effect, "global_weather", False):
                    return True
                continue
            if any(target.x == cell.x and target.y == cell.y for target in affected):
                return True
        return False


    def unit_in_weather(self, name: str, unit: Unit) -> bool:
        position = getattr(unit, "_resolution_actual_position", None) or unit.position
        return position is not None and not unit.banished and any(self.cell_has_weather(name, cell) for cell in self.unit_cells_at(unit, position))


    def enforce_sandstorm_stealth_rules(self) -> None:
        for unit in list(self.all_units()):
            if not unit.alive or unit.position is None or unit.banished:
                continue
            if not self.unit_in_weather("沙尘", unit):
                continue
            for status in list(unit.statuses):
                if status.name == "隐身" or getattr(status, "grants_stealth", False):
                    unit.remove_status(status, self)


    def all_units(self) -> list[Unit]:
        return list(self.units.values())


    def components_for(self, unit: Unit) -> list[BattleComponent]:
        from wujiang.tactical.engine.core import (
            direct_scoring,
        )
        if direct_scoring():
            from wujiang.tactical.direct_queries import component_snapshot
            result = component_snapshot(self, unit)
            if result is not None:
                return result
        return list(unit.iter_components())


    def sync_linked_units(self) -> None:
        from wujiang.tactical.engine.core import (
            direct_scoring,
        )
        if getattr(self, "_syncing_linked_units", False):
            return
        self._syncing_linked_units = True
        try:
            if direct_scoring():
                from wujiang.tactical.direct_queries import empty_hook_pass
                if empty_hook_pass(self, "sync_linked_state"):
                    return
            for unit in self.all_units():
                for component in list(unit.iter_components()):
                    component.sync_linked_state(self)
        finally:
            self._syncing_linked_units = False


    def prepay_skill_resources(self, skill: Skill, actor: Unit, payload: dict[str, Any]) -> None:
        previous = getattr(actor, "_paying_skill_cost", False)
        actor._paying_skill_cost = True
        try:
            mana_before = actor.current_mana
            skill.prepay_resources(self, actor, payload)
            if skill.timing == "active":
                actor._last_active_skill_code = skill.code
            mana_paid = round(max(0.0, mana_before - actor.current_mana), 2)
            if mana_paid > 0:
                for effect in list(self.field_effects):
                    hook = getattr(effect, "on_skill_mana_paid", None)
                    if callable(hook):
                        hook(self, actor, skill, mana_paid)
                for observer in list(self.all_units()):
                    if not observer.alive or observer.banished or observer.position is None:
                        continue
                    for component in list(observer.iter_components()):
                        hook = getattr(component, "on_skill_mana_paid", None)
                        if callable(hook):
                            hook(self, actor, skill, mana_paid)
            if actor.alive:
                for observer in list(self.all_units()):
                    if not observer.alive or observer.banished or observer.position is None:
                        continue
                    for component in list(observer.iter_components()):
                        component.on_any_skill_declared(self, actor, skill)
        finally:
            if previous:
                actor._paying_skill_cost = previous
            else:
                actor.__dict__.pop("_paying_skill_cost", None)


    def mounted_unit_for(self, rider: Unit | None) -> Optional[Unit]:
        if rider is None or not rider.mounted_on_unit_id:
            return None
        mount = self.units.get(rider.mounted_on_unit_id)
        if mount is None or not mount.alive or mount.banished or mount.position is None:
            return None
        return mount


    def rider_for(self, mount: Unit | None) -> Optional[Unit]:
        riders = self.riders_for(mount)
        return riders[0] if riders else None


    def riders_for(self, mount: Unit | None) -> list[Unit]:
        if mount is None:
            return []
        return [rider for rider in self.units.values()
                if rider.mounted_on_unit_id == mount.unit_id and rider.alive
                and not rider.banished and rider.position is not None]


    def set_mounted_state(self, rider: Unit, mount: Unit) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
        )
        current_mount = self.mounted_unit_for(rider)
        if current_mount is not None and current_mount.unit_id != mount.unit_id:
            self.clear_mounted_state(rider)
        if mount.allows_multiple_riders:
            if rider.player_id != mount.player_id or rider.position not in self.unit_cells(mount):
                raise ActionError("只能在己方船体内登船。")
            if any(other.unit_id != rider.unit_id and other.position == rider.position
                   for other in self.riders_for(mount)):
                raise ActionError("此船位已有骑手。")
        else:
            current_rider = self.rider_for(mount)
            if current_rider is not None and current_rider.unit_id != rider.unit_id:
                self.clear_mounted_state(current_rider)
        rider.mounted_on_unit_id = mount.unit_id
        if not mount.ridden_by_unit_id or not mount.allows_multiple_riders:
            mount.ridden_by_unit_id = rider.unit_id
        rider_changed = getattr(mount, "on_rider_changed", None)
        if callable(rider_changed):
            rider_changed(self)


    def clear_mounted_state(self, rider: Unit | None) -> None:
        if rider is None:
            return
        mount = self.units.get(rider.mounted_on_unit_id) if rider.mounted_on_unit_id else None
        rider.mounted_on_unit_id = None
        if mount is not None and mount.ridden_by_unit_id == rider.unit_id:
            mount.ridden_by_unit_id = next(
                (other.unit_id for other in self.riders_for(mount)), None
            )
        if mount is not None:
            rider_changed = getattr(mount, "on_rider_changed", None)
            if callable(rider_changed):
                rider_changed(self)


    def units_can_overlap(self, left: Unit, right: Unit) -> bool:
        return (
            left.unit_id != right.unit_id
            and (
                left.mounted_on_unit_id == right.unit_id
                or right.mounted_on_unit_id == left.unit_id
                or (left.is_mount and left.mount_owner_id == right.unit_id)
                or (right.is_mount and right.mount_owner_id == left.unit_id)
                or getattr(left, "attached_to_unit_id", None) == right.unit_id
                or getattr(right, "attached_to_unit_id", None) == left.unit_id
                or left.allows_destination_overlap_with(self, right)
                or right.allows_destination_overlap_with(self, left)
            )
        )


    def effect_recipient(self, unit: Unit) -> Unit:
        mount = self.mounted_unit_for(unit)
        return mount if mount is not None else unit


    def effect_units(self, units: Iterable[Unit], *, ignore: Optional[Unit] = None) -> list[Unit]:
        recipients: list[Unit] = []
        seen: set[str] = set()
        ignored_id = self.effect_recipient(ignore).unit_id if ignore is not None else None
        for unit in units:
            recipient = self.effect_recipient(unit)
            if recipient.unit_id == ignored_id:
                continue
            if recipient.unit_id in seen:
                continue
            seen.add(recipient.unit_id)
            recipients.append(recipient)
        return recipients


    def effect_units_at_cells(self, cells: Iterable[Position], *, ignore: Optional[Unit] = None) -> list[Unit]:
        return self.effect_units(self.units_at_cells(cells), ignore=ignore)


    def reaction_proxy_target(self, unit: Unit, queued_action: QueuedAction) -> Optional[Unit]:
        if unit.unit_id in queued_action.target_unit_ids:
            return unit
        mount = self.mounted_unit_for(unit)
        if mount is not None and mount.unit_id in queued_action.target_unit_ids:
            return mount
        if (mount is not None and mount.allows_multiple_riders
                and any(target.unit_id == mount.unit_id
                        for target in self.effect_units_at_cells(queued_action.target_cells))):
            return mount
        return None


    def passive_support_ship(self, rider: Unit, skill: Skill, queued_action: QueuedAction,
                             payload: Optional[dict[str, Any]] = None) -> Optional[Unit]:
        if skill.timing != "passive":
            return None
        ship = self.mounted_unit_for(rider)
        if ship is None or not ship.allows_multiple_riders:
            return None
        selected_ids = {str(unit_id) for unit_id in self.payload_target_unit_ids(payload or {})}
        if selected_ids:
            return ship if any(
                target_id in self.units and self.effect_recipient(self.units[target_id]).unit_id == ship.unit_id
                for target_id in selected_ids
            ) else None
        declared_ids = set(queued_action.target_unit_ids)
        if ship.unit_id in declared_ids:
            return ship
        if any(ship.unit_id == unit.unit_id for unit in self.effect_units_at_cells(queued_action.target_cells)):
            return ship
        return None


    def refresh_ship_passive_reaction_options(self, ship: Unit) -> None:
        window = self.pending_chain
        if window is None:
            return
        ship_cells = {(cell.x, cell.y) for cell in self.unit_cells(ship)}
        for rider in self.riders_for(ship):
            if rider.unit_id == getattr(ship, "ship_passive_claim_rider_id", None):
                continue
            filtered: list[ReactionOption] = []
            for option in window.options_by_unit.get(rider.unit_id, []):
                skill = rider.skill_map().get(option.action_code) if option.action_type == "skill" else None
                if skill is None or skill.timing != "passive" or ship.unit_id not in option.preview.get("target_unit_ids", []):
                    filtered.append(option)
                    continue
                remaining = [unit_id for unit_id in option.preview.get("target_unit_ids", [])
                             if unit_id != ship.unit_id]
                if remaining:
                    option.preview = dict(option.preview, target_unit_ids=remaining,
                                          cells=[cell for cell in option.preview.get("cells", [])
                                                 if (int(cell["x"]), int(cell["y"])) not in ship_cells])
                    filtered.append(option)
            window.options_by_unit[rider.unit_id] = filtered


    def unit_can_use_block_counter(self, unit: Unit) -> bool:
        components = list(unit.iter_components())
        if not unit.has_block_counter and not any(component.grants_block_counter(self, unit) for component in components):
            return False
        return all(component.allows_block_counter(self, unit) for component in components)


    def unit_can_use_counter(self, unit: Unit) -> bool:
        components = list(unit.iter_components())
        return (self.unit_can_use_block_counter(unit)
                or (any(component.grants_counter(self, unit) for component in components)
                    and all(component.allows_block_counter(self, unit) for component in components)))


    def player_units(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.units.values()
            if unit.player_id == player_id and unit.alive
        ]


    def enemy_units(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.units.values()
            if unit.player_id != player_id and unit.alive
        ]


