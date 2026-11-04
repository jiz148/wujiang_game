from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from collections import deque
from copy import deepcopy
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, is_dataclass, replace
import heapq
import os
from itertools import count
from functools import lru_cache
from wujiang.tactical.prediction import random, forecasting_active, direct_scoring, note as prediction_note
import weakref
from types import GetSetDescriptorType
from typing import Any, Callable, Iterable, Literal, Optional

from wujiang.platform.id_sequence import IdSequence
from wujiang.platform.ai_native import load_checkpoint, load_copy, load_flat_copy, load_component_scan
from wujiang.tactical.engine.geometry import body_coordinates, neighbor_coordinates, range_coordinates

from wujiang.tactical.engine.army import (
    army_public_state,
    command_hero_units,
    default_army_orders,
    is_army_soldier,
    living_army_units,
    parse_army_slot,
    resolve_army_phase,
    with_army_turn_slots,
)
from wujiang.tactical.heroes.weather_descriptions import hero_weather_effect_text


@lru_cache(maxsize=None)
def _rollback_slot_names(cls: type) -> tuple[str, ...]:
    """Dataclass field layouts do not change between nested battle probes."""
    return tuple(item.name for item in fields(cls))


_ROLLBACK_SCALAR_TYPES = frozenset((type(None), bool, int, float, str, bytes))
_ROLLBACK_TYPE_PLANS_ENABLED = os.environ.get("WUJIANG_AI_ROLLBACK_PLANS", "1") != "0"
_AI_TARGET_IDS_ENABLED = os.environ.get("WUJIANG_AI_ATTACK_IDS", "1") != "0"
_ROLLBACK_NATIVE_ENABLED = os.environ.get("WUJIANG_AI_NATIVE_ROLLBACK", "0") != "0"
_NATIVE_ROLLBACK_COPY = load_copy()
_NATIVE_FLAT_COPY = load_flat_copy()
# Experimental until dense four-board ablations demonstrate a benefit.
_NATIVE_FLAT_COPY_ENABLED = os.environ.get("WUJIANG_AI_NATIVE_FLAT_COPY", "0") != "0"
_NATIVE_CHECKPOINT = load_checkpoint()
# The 2026-10-20 full run measured this loop 6.16% slower. Keep the
# original Python capture/restore loops and the independent native copier.
_NATIVE_CHECKPOINT_ENABLED = os.environ.get("WUJIANG_AI_NATIVE_CHECKPOINT", "0") != "0"
_NATIVE_COMPONENT_SCAN_ENABLED = os.environ.get("WUJIANG_AI_NATIVE_COMPONENT_SCAN", "0") != "0"
_NATIVE_COMPONENT_SCAN = load_component_scan()
if os.environ.get("WUJIANG_AI_REQUIRE_NATIVE", "0") == "1" and (
        _NATIVE_ROLLBACK_COPY is None or not _ROLLBACK_NATIVE_ENABLED):
    raise ImportError("Native rollback required by validation but unavailable/disabled; check native-build.log")


def _rollback_legacy_copy(value):
    if type(value) in _ROLLBACK_SCALAR_TYPES:
        return value
    if isinstance(value, dict):
        return {key: item if type(item) in _ROLLBACK_SCALAR_TYPES else _rollback_legacy_copy(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [item if type(item) in _ROLLBACK_SCALAR_TYPES else _rollback_legacy_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(item if type(item) in _ROLLBACK_SCALAR_TYPES else _rollback_legacy_copy(item) for item in value)
    if isinstance(value, set):
        return set(value)
    if isinstance(value, deque):
        return deque(item if type(item) in _ROLLBACK_SCALAR_TYPES else _rollback_legacy_copy(item) for item in value)
    return value


def _rollback_copy_dict(value):
    return {key: item if type(item) in _ROLLBACK_SCALAR_TYPES else _rollback_copy_value(item)
            for key, item in value.items()}


def _rollback_copy_list(value):
    return [item if type(item) in _ROLLBACK_SCALAR_TYPES else _rollback_copy_value(item) for item in value]


def _rollback_copy_tuple(value):
    return tuple(item if type(item) in _ROLLBACK_SCALAR_TYPES else _rollback_copy_value(item) for item in value)


def _rollback_copy_deque(value):
    return deque(item if type(item) in _ROLLBACK_SCALAR_TYPES else _rollback_copy_value(item) for item in value)


def _rollback_copy_fallback(value):
    # Preserve isinstance's ordered virtual __class__ checks and every custom
    # iterator/items hook. Unsupported classes never enter a guessed plan.
    if type(value) in _ROLLBACK_SCALAR_TYPES:
        return value
    if isinstance(value, dict):
        return _rollback_copy_dict(value)
    if isinstance(value, list):
        return _rollback_copy_list(value)
    if isinstance(value, tuple):
        return _rollback_copy_tuple(value)
    if isinstance(value, set):
        return set(value)
    if isinstance(value, deque):
        return _rollback_copy_deque(value)
    return value


_ROLLBACK_BUILTIN_COPIERS = {dict: _rollback_copy_dict, list: _rollback_copy_list,
                             tuple: _rollback_copy_tuple, set: set, deque: _rollback_copy_deque}


@lru_cache(maxsize=256)
def _rollback_type_plan(cls):
    """Retain type dispatch only; field names, values and snapshots stay live.

    Like the existing slot-layout cache, plans describe loaded Python class
    definitions. Custom metaclasses bypass this cache before hashing a class.
    """
    copier = _ROLLBACK_BUILTIN_COPIERS.get(cls)
    if copier is not None:
        return copier, False
    namespaces = tuple(base.__dict__ for base in cls.__mro__)
    getattribute = next((namespace["__getattribute__"] for namespace in namespaces
                         if "__getattribute__" in namespace), None)
    if (getattribute is not object.__getattribute__
            or any("__getattr__" in namespace for namespace in namespaces)
            or any("__class__" in base.__dict__ for base in cls.__mro__ if base is not object)):
        return _rollback_copy_fallback, False
    for container, handler in _ROLLBACK_BUILTIN_COPIERS.items():
        if issubclass(cls, container):
            copier = handler
            break
    dictionary = next((namespace["__dict__"] for namespace in namespaces if "__dict__" in namespace), None)
    return copier, type(dictionary) is GetSetDescriptorType


def _rollback_copy_value(value):
    cls = type(value)
    # Guard before lru_cache/dict lookup: a custom metaclass may make class
    # hashing/equality observable, although the old copier never hashes it.
    if type(cls) is not type and type(cls) is not ABCMeta:
        return _rollback_copy_fallback(value)
    copier = _rollback_type_plan(cls)[0]
    return value if copier is None else copier(value)


def _rollback_namespace_eligible(obj):
    cls = type(obj)
    return (_ROLLBACK_TYPE_PLANS_ENABLED and (type(cls) is type or type(cls) is ABCMeta)
            and _rollback_type_plan(cls)[1])


def _rollback_restore_special(obj, snapshot):
    if hasattr(obj, "__dict__"):
        obj.__dict__.clear()
        obj.__dict__.update(snapshot)
    else:
        for name, value in snapshot.items():
            setattr(obj, name, value)


@contextmanager
def battle_state_rollback(battle: "Battle", *, ai_probe: bool = True,
                          commit_on_success: bool = False) -> Iterable[None]:
    """Restore probes always; an actual choice commits only after successful resolution."""
    # Discovery and RNG retain their original order. Native capture/restore
    # uses the same namespace plan and falls back for special protocols.
    native_copy = _NATIVE_ROLLBACK_COPY if _ROLLBACK_NATIVE_ENABLED else None
    if native_copy is not None and _NATIVE_FLAT_COPY_ENABLED and _NATIVE_FLAT_COPY is not None:
        native_copy = _NATIVE_FLAT_COPY
    native_checkpoint = (_NATIVE_CHECKPOINT if native_copy is not None and _NATIVE_CHECKPOINT_ENABLED else None)
    def copy_dict(value):
        # A custom instance namespace starts at items(), without the nested
        # value dispatch. Preserve that entry protocol with the original code.
        return (native_copy(value, _rollback_copy_value, _ROLLBACK_SCALAR_TYPES) if type(value) is dict
                else _rollback_copy_dict(value))
    copy_namespace = copy_dict if native_copy is not None else _rollback_copy_dict
    def state(obj: Any) -> dict[str, Any]:
        cls = type(obj)
        if _ROLLBACK_TYPE_PLANS_ENABLED and (type(cls) is type or type(cls) is ABCMeta) and _rollback_type_plan(cls)[1]:
            return copy_namespace(obj.__dict__)
        if hasattr(obj, "__dict__"):
            return (copy_namespace(obj.__dict__) if _ROLLBACK_TYPE_PLANS_ENABLED
                    else _rollback_legacy_copy(obj.__dict__))
        snapshot = {}
        for name in _rollback_slot_names(type(obj)):
            value = getattr(obj, name)
            snapshot[name] = value if type(value) in _ROLLBACK_SCALAR_TYPES else (
                _rollback_copy_value(value) if _ROLLBACK_TYPE_PLANS_ENABLED else _rollback_legacy_copy(value))
        return snapshot

    units = list({unit.unit_id: unit for unit in [*battle.all_units(), *battle.destroyed_units]}.values())
    objects = [battle, *units, *battle.field_effects]
    if battle.pending_chain is not None:
        objects.extend([battle.pending_chain, battle.pending_chain.queued_action])
        objects.extend(battle.pending_chain.chosen_reactions)
        for options in battle.pending_chain.options_by_unit.values():
            objects.extend(options)
    objects.extend(battle.pending_followup_actions)
    if battle.resolving_action is not None:
        objects.append(battle.resolving_action)
    for unit in units:
        components = list(unit.iter_components())
        objects.extend(components)
        for component in components:
            objects.extend(component.runtime_children())
        objects.append(unit.base_stats)
    # QueuedAction, ReactionWindow and ReactionOption use slots. Their mutable
    # payloads and reactor lists must roll back with units, including nested probes.
    # Keep the filter interleaved with capture, as in the original comprehension.
    eligible = (obj for obj in {id(obj): obj for obj in objects}.values()
                if hasattr(obj, "__dict__") or (is_dataclass(obj) and not obj.__dataclass_params__.frozen))
    saved = (native_checkpoint[0](eligible, _rollback_namespace_eligible, state,
                                 _rollback_copy_value, _ROLLBACK_SCALAR_TYPES, _rollback_copy_dict)
             if native_checkpoint is not None else [(obj, state(obj)) for obj in eligible])
    # base_stats is already in objects and saved above, including every slot.
    rng = random.getstate()
    restore = True
    try:
        if not commit_on_success:
            if ai_probe:
                battle._ai_probe_active = True
            else:
                battle.on_replay_checkpoint = None
        yield
        restore = not commit_on_success
    finally:
        if restore:
            if native_checkpoint is not None:
                native_checkpoint[1](saved, _rollback_namespace_eligible, _rollback_restore_special)
            else:
                for obj, snapshot in reversed(saved):
                    if _rollback_namespace_eligible(obj):
                        obj.__dict__.clear()
                        obj.__dict__.update(snapshot)
                    else:
                        _rollback_restore_special(obj, snapshot)
            random.setstate(rng)


DEFAULT_HERO_TURN_LIMIT = 200
SKIRMISH_HERO_TURN_LIMIT = 50
SIEGE_HERO_TURN_LIMIT = 200
ENCOUNTER_HERO_TURN_LIMIT = 50


_id_counter = IdSequence(1)

# A dependency witness, not an RNG replacement. In particular, probes retain
# the historical consumption of each battle's independent random stream.
_battle_rng_readers = ContextVar("battle_rng_readers", default=())


def _note_battle_rng_access():
    for witness in _battle_rng_readers.get():
        witness[0] = True


@contextmanager
def battle_rng_dependencies():
    witness = [False]
    token = _battle_rng_readers.set((*_battle_rng_readers.get(), witness))
    try:
        yield witness
    finally:
        _battle_rng_readers.reset(token)


class ActionError(RuntimeError):
    """Raised when an action cannot be performed."""


class ActionMiss(ActionError):
    """Raised when a queued action resolves on its original cell but misses."""


class DamageChoiceRequired(Exception):
    def __init__(self, *, unit_id: str, action_name: str, damage: float,
                 event_index: int, stats: list[str] | None = None,
                 kind: str = "stat", options: list[str] | None = None,
                 protected_unit_id: str | None = None) -> None:
        self.unit_id = unit_id
        self.action_name = action_name
        self.damage = damage
        self.event_index = event_index
        self.stats = stats or []
        self.kind = kind
        self.options = options or []
        self.protected_unit_id = protected_unit_id


@dataclass(frozen=True, slots=True)
class Position:
    x: int
    y: int

    def distance_to(self, other: "Position") -> int:
        return max(abs(self.x - other.x), abs(self.y - other.y))

    def offset(self, dx: int, dy: int) -> "Position":
        return Position(self.x + dx, self.y + dy)

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}


_CANONICAL_POSITION_TO_DICT = Position.to_dict


@dataclass(slots=True)
class Stats:
    attack: int
    defense: int
    speed: int
    attack_range: int
    mana: float

    def to_dict(self) -> dict[str, float]:
        return {
            "attack": self.attack,
            "defense": self.defense,
            "speed": self.speed,
            "attack_range": self.attack_range,
            "mana": self.mana,
        }


class DamageRule(ABC):
    name = "abstract"

    @abstractmethod
    def calculate_damage(self, attack_power: float, defense: float) -> float:
        raise NotImplementedError


class SummaryDamageRule(DamageRule):
    """Implements the markdown rule under docs/."""

    name = "summary"

    def calculate_damage(self, attack_power: float, defense: float) -> float:
        if attack_power > defense:
            return 1.0
        gap = max(defense - attack_power + 1, 1)
        return 1 / (2 ** gap)


class SpreadsheetDamageRule(DamageRule):
    """Keeps the alternate Excel damage rule available as a strategy."""

    name = "spreadsheet"

    def calculate_damage(self, attack_power: float, defense: float) -> float:
        if attack_power > defense:
            return 1.0
        gap = max((defense - attack_power) * 2, 1)
        return 1 / gap


class BattleComponent(ABC):
    """Common hook surface shared by skills, traits, and statuses."""

    kind = "component"
    all_skills_pierce_shields = False
    ignores_incoming_piercing = False
    separate_basic_attack_windows = False

    def limit_hp_loss(self, amount: float) -> float:
        """Pure final-loss limit; may reduce, never amplify, an individual loss."""
        return amount

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.owner: Optional["Unit"] = None
        self.component_id = f"cmp-{next(_id_counter)}"

    def bind(self, owner: "Unit") -> "BattleComponent":
        self.owner = owner
        return self

    def sync_linked_state(self, battle: "Battle") -> None:
        return None

    def blocks_direct_effects(self) -> bool:
        return False

    def blocks_skill_non_damage_effects(self) -> bool:
        return False

    def grants_magic_immunity_to(self, battle: "Battle", unit: "Unit") -> bool:
        return False

    def projected_trait_for(self, battle: "Battle", unit: "Unit") -> Optional["Trait"]:
        return None

    def grants_block_counter(self, battle: "Battle", unit: "Unit") -> bool:
        return False

    def grants_counter(self, battle: "Battle", unit: "Unit") -> bool:
        return False

    def allied_basic_heal_amount(self, battle: "Battle", actor: "Unit", target: "Unit") -> float:
        return 0.0

    def resolve_allied_basic_support(self, battle: "Battle", actor: "Unit", target: "Unit") -> bool:
        return False

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value

    def can_use_basic_attack(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> tuple[bool, str]:
        return True, ""

    def modify_targeting_range(self, value: int) -> int:
        return value

    def ignores_direct_unit_target_line(self, battle: "Battle", actor: "Unit") -> bool:
        return False

    def modify_normal_move_distance(self, value: int) -> int:
        return value

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value

    def modify_skill_mana_cost(
        self,
        battle: "Battle",
        actor: "Unit",
        skill: "Skill",
        payload: Optional[dict[str, Any]],
        cost: float,
    ) -> float:
        return cost

    def allows_split_normal_movement(self, battle: "Battle", actor: "Unit") -> bool:
        return False

    def normal_movement_step_cost(
        self,
        battle: "Battle",
        unit: "Unit",
        start: "Position",
        end: "Position",
        current_cost: int,
    ) -> int:
        return current_cost

    def on_owner_turn_start(self, battle: "Battle") -> None:
        return None

    def on_owner_turn_end(self, battle: "Battle") -> None:
        return None

    def on_any_turn_start(self, battle: "Battle", active_unit: "Unit") -> None:
        return None

    def on_any_turn_end(self, battle: "Battle", ended_player_id: int) -> None:
        return None

    def on_targeted(self, battle: "Battle", ctx: "TargetContext") -> None:
        return None

    def on_owner_action_declared(
        self,
        battle: "Battle",
        action_type: str,
        payload: dict[str, Any],
    ) -> None:
        return None

    def on_basic_attack_finished(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
        damage_contexts: list["DamageContext"],
        missed: bool,
    ) -> None:
        return None

    def on_any_basic_attack_finished(
        self, battle: "Battle", actor: "Unit", payload: dict[str, Any],
        damage_contexts: list["DamageContext"], missed: bool,
    ) -> None:
        """Observe a completed basic attack after its recipient effects have settled."""
        return None

    def on_skill_effect_finished(self, battle: "Battle", queued_action: "QueuedAction") -> None:
        """Observe one completed skill that actually affected this component's owner."""
        return None

    def on_target_action_declared(self, battle: "Battle", actor: "Unit", action_type: str, payload: dict[str, Any]) -> None:
        return None

    def on_unit_moved(self, battle: "Battle", ctx: "MoveContext") -> None:
        return None

    def on_unit_destroyed(self, battle: "Battle", destroyed_unit: "Unit") -> None:
        """Observe a real destruction after its final board cells are recorded."""
        return None

    def on_any_skill_declared(self, battle: "Battle", actor: "Unit", skill: "Skill") -> None:
        """Observe one formally prepaid skill declaration, independent of its hit count."""
        return None

    def on_before_normal_move(self, battle: "Battle", ctx: "MoveContext") -> None:
        """Pay validated normal movement costs before moving or entering effects."""
        return None

    def on_before_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def on_final_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        """React after ordinary defenses, immediately before HP loss."""
        return None

    def on_before_hp_loss(self, battle: "Battle", ctx: "DamageContext") -> None:
        """Adjust a positive HP-loss opportunity after shields, immunity and evasion."""
        return None

    def on_after_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def on_battle_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        """Observe each actual HP loss once, before local after-damage callbacks."""
        return None

    def lethal_damage_remaining_hp(self, battle: "Battle", ctx: "DamageContext") -> Optional[float]:
        """Offer retained HP only after all defenses and loss limits pass."""
        return None

    def runtime_children(self) -> Iterable["BattleComponent"]:
        return ()

    def provided_components(self) -> Iterable["BattleComponent"]:
        """Temporary capabilities owned by this component, active only while it is present."""
        return ()

    def accepts_status(self, status: "StatusEffect") -> bool:
        return True

    def modify_received_stat_change(self, status: "BattleComponent", stat_name: str, before: float, after: float) -> float:
        return after

    def on_owner_banished(self, battle: "Battle") -> None:
        return None

    def on_damage_cancelled(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def permits_immune_heal(self, ctx: "HealContext") -> bool:
        """Only an explicitly identified component may exempt its own healing."""
        return False

    def on_before_heal(self, battle: "Battle", ctx: "HealContext") -> None:
        return None

    def on_after_heal(self, battle: "Battle", ctx: "HealContext") -> None:
        return None

    def on_owner_removed(self, battle: "Battle") -> None:
        return None

    def on_owned_summon_destroyed(self, battle: "Battle", summon: "Unit") -> None:
        return None

    def on_enter_battle(self, battle: "Battle") -> None:
        return None

    def on_destroyed_hero_count_changed(self, battle: "Battle", count: int) -> None:
        return None

    def on_removed(self, battle: "Battle") -> None:
        return None

    def blocks_skill_use(
        self,
        battle: "Battle",
        actor: "Unit",
        skill: "Skill",
    ) -> tuple[bool, str]:
        return False, ""

    def can_attack_target(
        self,
        battle: "Battle",
        actor: "Unit",
        target: "Unit",
    ) -> tuple[bool, str]:
        return True, ""

    def can_attack_target_with_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        target: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        return True, ""

    def basic_attack_action_entries(
        self,
        battle: "Battle",
        actor: "Unit",
    ) -> list[dict[str, Any]]:
        return []

    def basic_attack_preview(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        return None

    def basic_attack_payload_metadata(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {}

    def basic_attack_origins(self, battle: "Battle", actor: "Unit", payload: Optional[dict[str, Any]]) -> Optional[list["Position"]]:
        return None

    def before_basic_attack_resolution(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> bool:
        return True

    def resolve_empty_basic_attack(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> bool:
        """Replace an explicitly empty-cell attack with a component's non-damage effect."""
        return False

    def forced_normal_move_paths(self, battle: "Battle", actor: "Unit") -> Optional[list[list["Position"]]]:
        return None

    def basic_attack_area_cells(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[list["Position"]]:
        return None

    def basic_attack_area_affects_allies(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        return True

    def allows_block_counter(self, battle: "Battle", actor: "Unit") -> bool:
        return True

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        return {
            "id": self.component_id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
        }


class Trait(BattleComponent):
    kind = "trait"


class StatusEffect(BattleComponent):
    kind = "status"

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        duration: Optional[int] = None,
        tick_scope: Literal["owner_turn_start", "owner_turn_end", "any_turn_end"] = "owner_turn_end",
    ) -> None:
        super().__init__(name=name, description=description)
        self.duration = duration
        self.tick_scope = tick_scope

    def decrement(self, battle: "Battle") -> None:
        if self.duration is None:
            return
        self.duration -= 1
        if self.duration <= 0 and self.owner is not None:
            self.owner.remove_status(self, battle)

    def on_owner_turn_start(self, battle: "Battle") -> None:
        if self.tick_scope == "owner_turn_start":
            self.decrement(battle)

    def on_owner_turn_end(self, battle: "Battle") -> None:
        if self.tick_scope == "owner_turn_end":
            self.decrement(battle)

    def on_any_turn_end(self, battle: "Battle", ended_player_id: int) -> None:
        if self.tick_scope == "any_turn_end":
            self.decrement(battle)

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["duration"] = self.duration
        return data


class BattleFieldEffect(BattleComponent):
    kind = "field"

    def blocks_action_effect(self, battle: "Battle", unit: "Unit", queued_action: "QueuedAction") -> bool:
        return False

    def on_after_damage_modifiers(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def on_after_target_modifiers(self, battle: "Battle", ctx: "TargetContext") -> None:
        return None

    def blocks_unit_movement(self, battle: "Battle", unit: "Unit") -> bool:
        return False

    def modify_unit_stat(self, battle: "Battle", unit: "Unit", stat_name: str, value: float) -> float:
        return value

    def adjust_unit_targeting_range(self, battle: "Battle", unit: "Unit", value: int) -> int:
        return value

    def on_unit_entered(self, battle: "Battle", unit: "Unit") -> None:
        return None

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        duration: Optional[int] = None,
    ) -> None:
        super().__init__(name=name, description=description)
        self.duration = duration

    def merge_into_existing(
        self,
        battle: "Battle",
        existing_effects: list["BattleFieldEffect"],
    ) -> bool:
        return False

    def on_any_turn_end(self, battle: "Battle", ended_player_id: int) -> None:
        if self.duration is None:
            return
        self.duration -= 1
        if self.duration <= 0:
            battle.remove_field_effect(self)

    def on_turn_start(self, battle: "Battle", active_unit: Optional["Unit"]) -> None:
        return None

    def blocks_forced_movement(self, battle: "Battle", position: "Position") -> bool:
        return False

    def affected_cells(self, battle: "Battle") -> list["Position"]:
        return []

    def board_marker(self, battle: "Battle") -> str:
        return self.name[:2]

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["duration"] = self.duration
        data["cells"] = [cell.to_dict() for cell in self.affected_cells(battle)]
        data["board_marker"] = self.board_marker(battle)
        weather_name = getattr(self, "weather_name", None)
        if weather_name is not None:
            data["weather_name"] = weather_name
            data["weather_owner_player_id"] = getattr(self, "weather_owner_player_id", None)
        return data


class TemporaryDefenseStatus(StatusEffect):
    def __init__(
        self,
        name: str,
        defense_delta: float,
        description: str,
        *,
        expire_with_chain: bool = False,
    ) -> None:
        super().__init__(name, description, duration=1, tick_scope="owner_turn_end")
        self.defense_delta = defense_delta
        self.expire_with_chain = expire_with_chain

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "defense":
            return value + self.defense_delta
        return value

    def on_after_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        if self.owner is None:
            return
        if ctx.target.unit_id != self.owner.unit_id:
            return
        self.owner.remove_status(self, battle)


class Skill(BattleComponent, ABC):
    excludes_caster_from_effect = False
    excludes_allies_from_effect = False
    ai_enemy_impact_requirement: Optional[bool] = None
    ai_probe_hostile_effect = False
    ai_always_hostile_effect = False
    ai_legality_requires_rollback = False
    ai_area_bonus_codes: frozenset[str] = frozenset()

    def ai_area_hit_bonus(self) -> float:
        return 18.0 if self.code in self.ai_area_bonus_codes else 0.0

    def ai_damage_target_score(self, target: "Unit", hostile_value: float) -> Optional[float]:
        return None

    def ai_control_bonus(self, context: Any, targets: list["Unit"]) -> float:
        """Value this skill's control beyond its ordinary damage estimate."""
        return 0.0

    def ai_r15_control_value(self, battle: "Battle", target: "Unit") -> float:
        return 0.0

    def ai_r15_post_effect_bonus(self, actor: "Unit", unit: "Unit", *,
                                 alive: bool, player: int, mana: float,
                                 value: float, statuses: set[str],
                                 control: float) -> float:
        return 0.0

    ai_simulated_control_status: Optional[str] = None

    def ai_simulated_control_value(self, battle: "Battle", actor: "Unit", target: "Unit", services: Any) -> float:
        return 0.0

    def ai_simulation_before(self, battle: "Battle", actor: "Unit", services: Any) -> Any:
        return None

    def ai_simulation_after(self, battle: "Battle", actor: "Unit", queued: "QueuedAction",
                            services: Any, profile: Any, saved: Any, before: dict[str, tuple],
                            units: list["Unit"]) -> float:
        return 0.0

    def ai_preparation_score(self, battle: "Battle", actor: "Unit", services: Any, profile: Any) -> float:
        return -8.0

    ai_future_opportunity_excluded = False
    ai_future_heal = False
    ai_costly_window_excluded = False
    ai_r12_direct_return = False
    ai_action_opportunity_excluded = False
    ai_action_control_status: Optional[str] = None
    ai_action_control_requires_attack_pause = False
    ai_damage_probe_resolves_as_attack = False
    ai_damage_probe_shielded_damage = False
    ai_reaction_simulation_bonus = 4.0
    ai_nonhostile_throttle_exempt = False
    ai_candidate_requires_explicit_legality = False
    ai_paid_reaction_shield = False

    def ai_damage_probe_hit_count(self, battle: "Battle", target: "Unit", cells: list["Position"]) -> int:
        return max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)

    def ai_kings_insight_score(self, battle: "Battle", actor: "Unit", payload: dict[str, Any],
                               services: Any, profile: Any) -> float:
        return services.skill_damage_score(battle, actor, self, payload, profile)

    def ai_messenger_preparation_score(self, actor: "Unit", opportunity: float) -> float:
        return -10.0

    def ai_fusion_preparation_blocked(self, actor: "Unit") -> bool:
        return False

    def ai_taunt_control_score(self, battle: "Battle", actor: "Unit", payload: dict[str, Any],
                               targets: list["Unit"], services: Any, profile: Any) -> float:
        return -20.0

    ai_red_defense_physical = False

    def ai_red_preparation_score(self, battle: "Battle", actor: "Unit", services: Any, profile: Any) -> float:
        return -20.0

    def ai_r12_control_before(self, battle: "Battle", actor: "Unit", units: list["Unit"],
                              services: Any, profile: Any) -> dict[str, float]:
        return {}

    def ai_r12_unit_bonus(self, battle: "Battle", actor: "Unit", unit: "Unit", *,
                          locked: bool, control_before: dict[str, float],
                          services: Any, profile: Any) -> float:
        return 0.0

    def ai_r12_mana_weight(self, actor: "Unit", unit: "Unit") -> float:
        return 22.0

    def ai_r12_effect_override(self, battle: "Battle", actor: "Unit", payload: dict[str, Any],
                               services: Any, profile: Any) -> Optional[float]:
        return None

    kind = "skill"
    requires_direct_unit_target_line = True
    target_unit_is_anchor = False
    target_cells_are_geometry_only = False
    selection_cells_are_effect_cells = True
    resolves_from_current_position = False

    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        mana_cost: float = 0.0,
        cooldown_turns: int = 0,
        max_uses_per_turn: Optional[int] = None,
        max_uses_per_battle: Optional[int] = None,
        target_mode: Literal["none", "self", "ally", "enemy", "cell", "unit"] = "none",
        passive: bool = False,
        timing: Literal["active", "passive", "instant", "reaction"] = "active",
        direction_mode: Literal["none", "optional", "required"] = "none",
    ) -> None:
        super().__init__(name=name, description=description)
        self.code = code
        self.mana_cost = mana_cost
        self.cooldown_turns = cooldown_turns
        self.max_uses_per_turn = max_uses_per_turn
        self.max_uses_per_battle = max_uses_per_battle
        self.target_mode = target_mode
        self.passive = passive
        self.timing = "passive" if passive and timing == "active" else timing
        self.direction_mode = direction_mode
        self.uses_this_turn = 0
        self.uses_this_battle = 0
        self.cooldown_remaining = 0
        self._uses_turn_number: Optional[int] = None

    @property
    def is_active(self) -> bool:
        return self.timing == "active"

    def effective_skill_for_payload(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> "Skill":
        """Return the skill whose timing and effects this payload actually uses."""
        return self

    def declared_reaction_landing_valid(
        self, battle: "Battle", actor: "Unit", payload: dict[str, Any]
    ) -> bool:
        """Whether a paid movement reaction can still land after faster reactions."""
        return True

    def directly_damages_on_use(self) -> Optional[bool]:
        """Declare direct damage when a skill's effect is queued by another object."""
        return None

    def ai_requires_enemy_impact(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> Optional[bool]:
        """Override the shared AI's enemy-impact gate for this skill when needed."""
        return self.ai_enemy_impact_requirement

    def ai_hostile_target_impact(self, battle: "Battle", actor: "Unit", payload: dict[str, Any], target: "Unit") -> Optional[bool]:
        """Return an exact AI effect result, or defer to the shared effect probe."""
        return None

    def ai_effect_units(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> Optional[list["Unit"]]:
        """Supply a skill-owned effect set when cell occupancy is only geometry."""
        return None

    def ai_effective_enemy_impact(self, battle: "Battle", actor: "Unit", payload: dict[str, Any],
                                  targets: Optional[list["Unit"]] = None, shared: Any = None,
                                  action: Optional[dict[str, Any]] = None) -> Optional[bool]:
        """Declare a skill-specific effective enemy hit before or after target discovery."""
        return None

    def ai_ignores_shield_for_target(self, battle: "Battle", actor: "Unit", payload: dict[str, Any],
                                     target: "Unit") -> bool:
        return bool(self.ignores_shield_for_payload(battle, actor, payload))

    def ai_extra_effect_applies(self, target: "Unit") -> bool:
        return True

    def ai_raw_damage_for_impact(self, target: "Unit") -> Optional[tuple[float, bool]]:
        """Return raw damage and shield piercing for unusual damage formulas."""
        return None

    @property
    def is_reaction(self) -> bool:
        return self.timing in {"passive", "instant", "reaction"}

    @property
    def chain_speed(self) -> int:
        mapping = {
            "active": 1,
            "passive": 2,
            "reaction": 2,
            "instant": 3,
        }
        return mapping[self.timing]

    def can_use(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        self.sync_turn_scope(battle)
        if self.timing in {"passive", "reaction"}:
            return False, "该技能需要在连锁时使用。"
        can_use_as_instant = self.timing == "instant" and actor.player_id != battle.active_player
        if not can_use_as_instant and not actor.can_take_turn_actions(battle):
            return False, "这个单位当前不能行动。"
        if not can_use_as_instant and actor.player_id != battle.active_player:
            return False, "还没有轮到这个单位行动。"
        if actor.banished:
            return False, "该单位暂时不在战场上。"
        if actor.cannot_use_skills:
            return False, "这个单位当前不能使用技能。"
        for effect in (effect for effect in list(battle.field_effects)
                       if not actor.direct_effects_blocked()
                       or getattr(effect, "ignores_direct_effect_immunity", False)):
            blocked, reason = effect.blocks_skill_use(battle, actor, self)
            if blocked:
                return False, reason
            payload_check = getattr(effect, "blocks_skill_payload", None)
            if callable(payload_check):
                blocked, reason = payload_check(battle, actor, self, payload)
                if blocked:
                    return False, reason
        for component in list(actor.iter_components()):
            blocked, reason = component.blocks_skill_use(battle, actor, self)
            if blocked:
                return False, reason
        if self.cooldown_remaining > 0 and not self.allows_additional_use_during_cooldown(battle, actor):
            return False, f"还需冷却 {self.cooldown_remaining} 个己方回合。"
        if self.max_uses_per_turn is not None and self.uses_this_turn >= self.max_uses_per_turn:
            return False, "本回合使用次数已满。"
        if self.max_uses_per_battle is not None and self.uses_this_battle >= self.max_uses_per_battle:
            return False, "本场战斗使用次数已满。"
        if getattr(self, "_ancient_stolen_charges", 1) <= 0:
            return False, "夺得技能的一次使用权已用完。"
        if actor.current_mana + 1e-9 < self.mana_cost_for_payload(battle, actor, payload):
            return False, "魔力不足。"
        block_reason = battle.shared_stealth_action_block_reason(actor)
        if block_reason:
            return False, block_reason
        return True, ""

    def allows_additional_use_during_cooldown(self, battle: "Battle", actor: "Unit") -> bool:
        return False

    def on_owner_turn_start(self, battle: "Battle") -> None:
        self.sync_turn_scope(battle)

    def on_any_turn_end(self, battle: "Battle", ended_player_id: int) -> None:
        # Cooldowns are rounds of the owning hero, not turns of every ally/enemy.
        if self.cooldown_remaining > 0 and battle.unit_belongs_to_current_turn(self.owner):
            self.cooldown_remaining -= 1

    def sync_turn_scope(self, battle: "Battle") -> None:
        current_turn_number = int(getattr(battle, "turn_number", 1) or 1)
        if self._uses_turn_number == current_turn_number:
            return
        self._uses_turn_number = current_turn_number
        self.uses_this_turn = 0

    def mana_cost_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> float:
        cost = float(self.mana_cost)
        if self.is_reaction and battle.pending_chain is not None:
            multiplier = float(battle.pending_chain.queued_action.payload.get("reaction_mana_multiplier", 1.0) or 1.0)
            cost *= max(0.0, multiplier)
        for component in list(actor.iter_components()):
            if component is self:
                continue
            cost = component.modify_skill_mana_cost(battle, actor, self, payload, cost)
        return max(0.0, float(cost))

    def mana_cost_text(self) -> Optional[str]:
        return None

    def direct_unit_target_range(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> int:
        return actor.targeting_range()

    def prepay_resources(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        self.sync_turn_scope(battle)
        actor.spend_mana(self.mana_cost_for_payload(battle, actor, payload))
        self.uses_this_turn += 1
        self.uses_this_battle += 1
        if hasattr(self, "_ancient_stolen_charges"):
            self._ancient_stolen_charges -= 1
        if self.cooldown_turns:
            self.cooldown_remaining = self.cooldown_turns

    def finalize_use(self, battle: "Battle", actor: "Unit") -> None:
        if self.timing == "active":
            actor.performed_active_skill = True

    def spend_resources(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        self.prepay_resources(battle, actor, payload)
        self.finalize_use(battle, actor)

    @abstractmethod
    def execute(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def can_react_to(
        self,
        battle: "Battle",
        actor: "Unit",
        queued_action: "QueuedAction",
    ) -> tuple[bool, str]:
        self.sync_turn_scope(battle)
        if not self.is_reaction:
            return False, "不是连锁技能。"
        if actor.banished or (not actor.alive and not self.can_react_after_destroyed(battle, actor, queued_action)):
            return False, "单位不在战场上。"
        if actor.cannot_use_skills:
            return False, "这个单位当前不能使用技能。"
        for effect in (effect for effect in list(battle.field_effects)
                       if not actor.direct_effects_blocked()
                       or getattr(effect, "ignores_direct_effect_immunity", False)):
            blocked, reason = effect.blocks_skill_use(battle, actor, self)
            if blocked:
                return False, reason
        for component in list(actor.iter_components()):
            blocked, reason = component.blocks_skill_use(battle, actor, self)
            if blocked:
                return False, reason
        if queued_action.speed >= self.chain_speed:
            return False, "连锁速度不够快。"
        if self.cooldown_remaining > 0:
            return False, "技能冷却中。"
        if self.max_uses_per_turn is not None and self.uses_this_turn >= self.max_uses_per_turn:
            return False, "本回合使用次数已满。"
        if self.max_uses_per_battle is not None and self.uses_this_battle >= self.max_uses_per_battle:
            return False, "本场战斗使用次数已满。"
        if actor.current_mana + 1e-9 < self.mana_cost:
            return False, "魔力不足。"
        block_reason = battle.shared_stealth_action_block_reason(actor)
        if block_reason:
            return False, block_reason
        return True, ""

    def can_react_after_destroyed(
        self, battle: "Battle", actor: "Unit", queued_action: "QueuedAction",
    ) -> bool:
        return False

    def can_react_with_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        queued_action: "QueuedAction",
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        ok, reason = self.can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        for effect in list(battle.field_effects):
            payload_check = getattr(effect, "blocks_skill_payload", None)
            if callable(payload_check):
                blocked, reason = payload_check(battle, actor, self, payload)
                if blocked:
                    return False, reason
        if actor.current_mana + 1e-9 < self.mana_cost_for_payload(battle, actor, payload):
            return False, "魔力不足。"
        ship = battle.passive_support_ship(actor, self, queued_action, payload)
        if (ship is not None and ship.ship_passive_claim_turn == battle.turn_number
                and ship.ship_passive_claim_rider_id != actor.unit_id):
            return False, "这艘船本回合已有另一名乘员对船使用被动技能。"
        return True, ""

    def react(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
        queued_action: "QueuedAction",
    ) -> None:
        self.execute(battle, actor, payload)

    def on_target_missed(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> None:
        """Resolve an explicit miss benefit after the declared cell becomes empty."""
        return None

    def preview(self, battle: "Battle", actor: "Unit") -> dict[str, Any]:
        return {
            "cells": [],
            "target_unit_ids": [],
            "secondary_cells": [],
            "secondary_target_unit_ids": [],
            "requires_target": self.target_mode in {"ally", "enemy", "cell", "unit"},
        }

    def reaction_preview(
        self,
        battle: "Battle",
        actor: "Unit",
        queued_action: "QueuedAction",
    ) -> dict[str, Any]:
        return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": False}

    def reaction_window_timing(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> Literal["before", "after"]:
        return "before"

    def target_can_react_to_effect(self, battle: "Battle", actor: "Unit", target: "Unit", payload: dict[str, Any]) -> bool:
        return True

    def get_target_units_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> list["Unit"]:
        if self.target_mode in {"ally", "enemy", "unit"} and payload.get("target_unit_id"):
            return [battle.get_unit(payload["target_unit_id"])]
        return []

    def get_target_cells_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> list["Position"]:
        if self.target_mode in {"ally", "enemy", "unit"} and payload.get("target_unit_id"):
            target = battle.get_unit(payload["target_unit_id"])
            return battle.unit_cells(target)
        if self.target_mode == "cell" and payload.get("x") is not None and payload.get("y") is not None:
            return [Position(int(payload["x"]), int(payload["y"]))]
        if self.target_mode == "self" and actor.position is not None:
            return battle.unit_cells(actor)
        return []

    def ignores_shield_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> bool:
        return any(component.all_skills_pierce_shields for component in actor.iter_components())

    def half_ignores_shield_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> bool:
        return False

    def ignores_stealth_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> bool:
        return False

    def ignores_magic_immunity_for_payload(
        self, battle: "Battle", actor: "Unit", payload: dict[str, Any]
    ) -> bool:
        return self.mana_cost_for_payload(battle, actor, payload) <= 0 and any(
            getattr(component, "free_skills_ignore_magic_immunity", False)
            for component in actor.iter_components() if component is not self
        )

    def cannot_evade_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> bool:
        return False

    def queued_payload_metadata(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Trusted metadata copied onto this skill's queued action."""
        return {}

    def build_wrapped_action(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> Optional["QueuedAction"]:
        return None

    def queued_reaction_payload_metadata(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> dict[str, Any]:
        return {}

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        self.sync_turn_scope(battle)
        owner = self.owner
        effective_mana_cost = self.mana_cost
        if owner is not None:
            effective_mana_cost = self.mana_cost_for_payload(battle, owner, {})
        data = super().to_public_dict(battle)
        data.update(
            {
                "code": self.code,
                "mana_cost": effective_mana_cost,
                "base_mana_cost": self.mana_cost,
                "mana_cost_text": self.mana_cost_text(),
                "cooldown_turns": self.cooldown_turns,
                "cooldown_remaining": self.cooldown_remaining,
                "max_uses_per_turn": self.max_uses_per_turn,
                "max_uses_per_battle": self.max_uses_per_battle,
                "target_mode": self.target_mode,
                "passive": self.passive,
                "timing": self.timing,
                "chain_speed": self.chain_speed,
                "direction_mode": self.direction_mode,
                "uses_this_turn": self.uses_this_turn,
                "uses_this_battle": self.uses_this_battle,
            }
        )
        return data


@dataclass(slots=True)
class MoveContext:
    unit: "Unit"
    start: Position
    end: Position
    path: list[Position]
    via_skill: bool = False
    triggered_by_reaction: bool = False
    tags: set[str] = field(default_factory=set)


@dataclass(slots=True)
class TargetContext:
    actor: "Unit"
    target: "Unit"
    action_name: str
    is_skill: bool
    is_hostile: bool
    ignore_shield: bool = False
    half_ignore_shield: bool = False
    ignore_magic_immunity: bool = False
    ignore_physical_immunity: bool = False
    from_field_effect: bool = False
    cannot_evade: bool = False
    shield_consumed: bool = False
    damage_target: bool = False
    cancelled: bool = False
    reason: str = ""
    tags: set[str] = field(default_factory=set)
    destroyed_as_clone: bool = False


@dataclass(slots=True)
class DamageContext:
    source: Optional["Unit"]
    target: "Unit"
    attack_power: float
    is_skill: bool
    action_name: str
    ignore_shield: bool = False
    half_ignore_shield: bool = False
    ignore_magic_immunity: bool = False
    ignore_physical_immunity: bool = False
    siege_shell: bool = False
    from_field_effect: bool = False
    cannot_evade: bool = False
    area_cell_hits: int = 1
    raw_damage: Optional[float] = None
    shield_consumed: bool = False
    protection_broken: bool = False
    cancelled: bool = False
    preserve_followup_effects: bool = False
    reason: str = ""
    lethal: bool = False
    destroyed_as_clone: bool = False
    tags: set[str] = field(default_factory=set)
    actual_damage: float = 0.0
    declared_source_attack: Optional[float] = None
    modifier_receipts: set[str] = field(default_factory=set)
    defense_override: Optional[float] = None
    complete_block_chance: float = 0.0
    complete_block_label: str = ""
    forecast_hit_probability: float = 1.0
    forecast_attack_changes: Optional[tuple] = None
    forecast_damage_rows: Optional[tuple] = None
    forecast_kill_probability: float = 0.0
    forecast_shield_pierce_probability: float = 0.0

    @property
    def damage(self) -> float:
        if self.destroyed_as_clone:
            return 0.0
        if self.lethal:
            return float(self.raw_damage or 1.0)
        return float(self.raw_damage or 0.0)


@dataclass(slots=True)
class HealContext:
    source: Optional["Unit"]
    target: "Unit"
    amount: float
    action_name: str
    cancelled: bool = False
    reason: str = ""
    tags: set[str] = field(default_factory=set)
    effect_source: Optional[BattleComponent] = None
    actual_heal: float = 0.0


@dataclass(slots=True)
class VisualEvent:
    event_id: int
    kind: Literal["attack", "skill", "defense"]
    display_name: str
    actor_id: Optional[str] = None
    actor_player_id: Optional[int] = None
    action_type: str = ""
    action_code: str = ""
    target_unit_ids: list[str] = field(default_factory=list)
    target_cells: list[Position] = field(default_factory=list)
    source_cell: Optional[Position] = None
    defense_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "kind": self.kind,
            "display_name": self.display_name,
            "actor_id": self.actor_id,
            "actor_player_id": self.actor_player_id,
            "action_type": self.action_type,
            "action_code": self.action_code,
            "target_unit_ids": list(self.target_unit_ids),
            "target_cells": [cell.to_dict() for cell in self.target_cells],
            "source_cell": self.source_cell.to_dict() if self.source_cell is not None else None,
            "defense_reason": self.defense_reason,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class QueuedAction:
    action_type: Literal["move", "attack", "skill", "skill_effect", "reaction_skill", "reaction_action"]
    actor_id: str
    display_name: str
    speed: int
    payload: dict[str, Any]
    description: str = ""
    target_unit_ids: list[str] = field(default_factory=list)
    target_cells: list[Position] = field(default_factory=list)
    source_player_id: Optional[int] = None
    hostile: bool = False
    reaction_source_id: Optional[str] = None
    suppress_logs: bool = False
    effect_resolver: Optional[Callable[["Battle", "Unit", "QueuedAction"], None]] = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "actor_id": self.actor_id,
            "display_name": self.display_name,
            "speed": self.speed,
            "payload": {key: value for key, value in self.payload.items() if key != "separate_attack_segments"},
            "description": self.description,
            "target_unit_ids": self.target_unit_ids,
            "target_cells": [cell.to_dict() for cell in self.target_cells],
            "source_player_id": self.source_player_id,
            "hostile": self.hostile,
            "reaction_source_id": self.reaction_source_id,
        }


@dataclass(slots=True)
class ReactionOption:
    unit_id: str
    action_code: str
    action_name: str
    action_type: Literal["skill", "reaction_action"]
    timing: str
    chain_speed: int
    description: str
    preview: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "action_code": self.action_code,
            "action_name": self.action_name,
            "action_type": self.action_type,
            "timing": self.timing,
            "chain_speed": self.chain_speed,
            "description": self.description,
            "preview": self.preview,
        }


@dataclass(slots=True)
class ReactionWindow:
    reactive_player_id: int
    queued_action: QueuedAction
    pending_reactor_ids: list[str]
    options_by_unit: dict[str, list[ReactionOption]]
    chosen_reactions: list[QueuedAction] = field(default_factory=list)
    decision_log: list[str] = field(default_factory=list)

    def current_unit_id(self) -> Optional[str]:
        return self.pending_reactor_ids[0] if self.pending_reactor_ids else None

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        return {
            "reactive_player_id": self.reactive_player_id,
            "queued_action": self.queued_action.to_public_dict(),
            "queued_action_effect_summary": battle.queued_action_effect_summary(self.queued_action),
            "pending_reactor_ids": self.pending_reactor_ids,
            "current_unit_id": self.current_unit_id(),
            "options_by_unit": {
                unit_id: [option.to_public_dict() for option in options]
                for unit_id, options in self.options_by_unit.items()
            },
            "chosen_reactions": [action.to_public_dict() for action in self.chosen_reactions],
            "decision_log": self.decision_log,
        }


@dataclass(slots=True)
class RespawnPrompt:
    unit_id: str
    player_id: int
    origin: Position
    options: list[Position]
    kind: str = "banish"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "player_id": self.player_id,
            "origin": self.origin.to_dict(),
            "options": [cell.to_dict() for cell in self.options],
            "kind": self.kind,
        }


class Unit(ABC):
    def __getstate__(self) -> dict[str, Any]:
        state = dict(self.__dict__)
        state.pop("_battle_ref", None)
        return state

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in {"current_hp", "current_mana"}:
            object.__setattr__(self, name, value)
            return
        previous = self.__dict__.get(name)
        battle_ref = self.__dict__.get("_battle_ref")
        battle = battle_ref() if battle_ref is not None else None
        modes = getattr(battle, "special_mode_codes", ()) if battle is not None else ()
        if previous is not None and value != previous and "arena" in modes and self.__dict__.get("banished", False):
            return
        if previous is not None and value > previous and battle is not None:
            if "no_recovery" in modes:
                return
        object.__setattr__(self, name, value)
        if previous is None or value <= previous or battle is None or "healer_attack" not in modes:
            return
        gained = round(value - previous, 4)
        for effect in list(battle.field_effects):
            if name == "current_hp" and not self.__dict__.get("_inside_battle_heal", False):
                hook = getattr(effect, "on_direct_heal", None)
                if callable(hook):
                    action = battle.resolving_action
                    origin = battle.units.get(action.actor_id) if action is not None else None
                    hook(battle, self, gained, source=origin)
            elif name == "current_mana":
                hook = getattr(effect, "on_mana_gained", None)
                if callable(hook):
                    hook(battle, self, gained)

    def __init__(
        self,
        *,
        unit_id: str,
        player_id: int,
        name: str,
        title: str,
        role: str,
        attribute: str,
        race: str,
        level: int,
        base_stats: Stats,
        raw_skill_text: str,
        raw_trait_text: str,
        max_health: float = 1.0,
        is_summon: bool = False,
        is_clone: bool = False,
    ) -> None:
        self.unit_id = unit_id
        self.player_id = player_id
        self.name = name
        self.title = title
        self.role = role
        self.attribute = attribute
        self.race = race
        self.level = level
        self.base_stats = Stats(
            attack=base_stats.attack,
            defense=base_stats.defense,
            speed=base_stats.speed,
            attack_range=base_stats.attack_range,
            mana=base_stats.mana,
        )
        self.current_hp = max_health
        self.max_health = max_health
        self.current_mana = base_stats.mana
        self.mana_points = 0.0
        self.position: Optional[Position] = None
        self.last_position: Optional[Position] = None
        self.footprint_width = max(1, int(getattr(self, "footprint_width", 1)))
        self.footprint_height = max(1, int(getattr(self, "footprint_height", 1)))
        configured_stat_minimums = dict(getattr(self, "stat_minimums", {}))
        self.stat_minimums = {
            "attack": float(configured_stat_minimums.get("attack", 1.0)),
            "defense": float(configured_stat_minimums.get("defense", 1.0)),
            "speed": float(configured_stat_minimums.get("speed", 1.0)),
            "attack_range": float(configured_stat_minimums.get("attack_range", 1.0)),
        }
        base_offsets = getattr(self, "base_footprint_offsets", None)
        if base_offsets is None:
            base_offsets = [
                (dx, dy)
                for dx in range(self.footprint_width)
                for dy in range(self.footprint_height)
            ]
        self.base_footprint_offsets = self._normalize_footprint_offsets(base_offsets)
        current_offsets = getattr(self, "footprint_offsets", None)
        self.footprint_offsets = self._normalize_footprint_offsets(current_offsets or self.base_footprint_offsets)
        self._refresh_footprint_bounds()
        self.alive = True
        self.banished = False
        self.banish_return_position: Optional[Position] = None
        self.banish_turns_remaining = 0
        self.shields = 0
        self.temporary_shields = 0
        self.dodge_charges = 0
        self.magic_immunity = False
        self.physical_immunity = False
        self.cannot_be_targeted = False
        self.cannot_move = False
        self.cannot_normal_move = False
        self.cannot_heal = False
        self.cannot_attack = is_clone
        self.cannot_use_skills = is_clone
        self.allow_overheal = False
        self.ignore_units_while_moving = False
        self.allow_enemy_destination_overlap = False
        self.has_flying = False
        self.has_block_counter = False
        self.protection_shields = 0
        self.is_summon = is_summon
        self.is_clone = is_clone
        self.summoner_id: Optional[str] = None
        self.is_mount = False
        self.mount_owner_id: Optional[str] = None
        self.mounted_on_unit_id: Optional[str] = None
        self.ridden_by_unit_id: Optional[str] = None
        self.allows_multiple_riders = False
        self.can_act_on_entry_turn = False
        self.turn_ready = True
        self.move_used = False
        self.normal_move_steps_used = 0
        self.normal_move_actions_used = 0
        self.attacks_used = 0
        self.performed_active_skill = False
        self.moved_this_turn = False
        self.actions_taken_this_turn: list[str] = []
        self.base_attack_actions_per_turn = 1
        self.allow_unbounded_mana = False
        self.raw_skill_text = raw_skill_text
        self.raw_trait_text = raw_trait_text
        self.skills: list[Skill] = [skill.bind(self) for skill in self.build_skills()]
        self.traits: list[Trait] = [trait.bind(self) for trait in self.build_traits()]
        self.statuses: list[StatusEffect] = []

    @abstractmethod
    def build_skills(self) -> list[Skill]:
        raise NotImplementedError

    @abstractmethod
    def build_traits(self) -> list[Trait]:
        raise NotImplementedError

    def projected_traits(self) -> Iterable[Trait]:
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if any(getattr(status, "suppresses_projected_traits", False) for status in getattr(self, "statuses", ())):
            return
        if battle is None or not (self.is_summon or self.is_clone) or not self.alive or self.banished or self.position is None:
            return
        seen: set[str] = set()
        for source in battle.all_units():
            if not source.alive or source.banished or source.position is None:
                continue
            for trait in (() if source.innate_traits_suppressed() else source.traits):
                projected = trait.projected_trait_for(battle, self)
                if projected is not None and projected.name not in seen:
                    seen.add(projected.name)
                    yield projected

    def iter_components(self) -> Iterable[BattleComponent]:
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is not None and self.banished and "arena" in getattr(battle, "special_mode_codes", ()):
            return
        active_skills_only = battle is not None and "real_men" in getattr(battle, "special_mode_codes", ())
        yield from (skill for skill in self.skills if not active_skills_only or skill.timing == "active")
        if not self.innate_traits_suppressed():
            yield from self.traits
        seen_skills = {id(skill) for skill in self.skills}
        for status in self.statuses:
            yield status
            for component in status.provided_components():
                if isinstance(component, Skill):
                    if active_skills_only and component.timing != "active":
                        continue
                    if id(component) in seen_skills:
                        continue
                    seen_skills.add(id(component))
                yield component
        for skill in getattr(self, "_triggered_skill_definitions", {}).values():
            if id(skill) not in seen_skills and (not active_skills_only or skill.timing == "active"):
                yield skill
        yield from self.projected_traits()

    def innate_traits_suppressed(self) -> bool:
        return any(getattr(status, "suppresses_innate_traits", False) for status in getattr(self, "statuses", ()))

    def action_skills(self) -> list[Skill]:
        skills = list(self.skills)
        seen = {skill.code for skill in skills}
        for status in self.statuses:
            for component in status.provided_components():
                if isinstance(component, Skill) and component.code not in seen:
                    skills.append(component)
                    seen.add(component.code)
        return skills

    def skill_map(self) -> dict[str, Skill]:
        return {skill.code: skill for skill in self.action_skills()}

    def get_skill(self, code: str) -> Skill:
        for component in self.iter_components():
            if isinstance(component, Skill) and component.code == code:
                return component
        raise ActionError(f"{self.name} 没有技能 {code}")

    def add_status(self, status: StatusEffect, *, source: Optional["Unit"] = None) -> None:
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is not None and self.banished and "arena" in getattr(battle, "special_mode_codes", ()):
            return
        if source is None and battle is not None:
            source = battle.units.get(getattr(status, "source_unit_id", ""))
            action = battle.resolving_action
            if source is None and action is not None:
                source = battle.units.get(action.actor_id)
        if not hasattr(status, "is_skill_effect"):
            action = battle.resolving_action if battle is not None else None
            status.is_skill_effect = action is None or action.action_type in {"skill", "reaction_skill", "skill_effect"}
        action = battle.resolving_action if battle is not None else None
        if (action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"}
                and status.duration is not None and not getattr(status, "chess_duration_extended", False)):
            caster = battle.units.get(action.actor_id)
            bonus = int(getattr(caster, "chess_duration_bonus", 0)) if caster is not None and source is caster else 0
            if bonus:
                status.duration += bonus
                status.chess_duration_extended = True
        if source is not None:
            status.source_player_id = source.player_id
            if (battle is not None and "soft_persimmon" in getattr(battle, "special_mode_codes", ())
                    and not hasattr(status, "source_unit_id")):
                status.source_unit_id = source.unit_id
        if battle is not None and any(
            callable(hook := getattr(effect, "blocks_status_application", None))
            and hook(battle, source, self, status)
            for effect in battle.field_effects
        ):
            return
        if any(not component.accepts_status(status) for component in self.iter_components()):
            return
        if (battle is not None and self.is_clone and getattr(status, "is_skill_effect", False)
                and source is not None and battle.destroy_clone_for_skill_effect(self, source=source, action_name=status.name)):
            return
        self.statuses.append(status.bind(self))

    def remove_status(self, status: StatusEffect, battle: Optional["Battle"] = None) -> None:
        if status in self.statuses:
            self.statuses.remove(status)
            if battle is not None:
                status.on_removed(battle)
            if battle is not None:
                battle.log(f"{self.name} 的状态【{status.name}】结束。")

    def has_status(self, name: str) -> bool:
        return any(status.name == name for status in self.statuses)

    def get_status(self, name: str) -> Optional[StatusEffect]:
        for status in self.statuses:
            if status.name == name:
                return status
        return None

    def is_stealthed(self) -> bool:
        return self.has_status("隐身")

    @staticmethod
    def _normalize_footprint_offsets(offsets: Iterable[tuple[int, int] | Position]) -> list[tuple[int, int]]:
        normalized: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for offset in offsets:
            if isinstance(offset, Position):
                pair = (int(offset.x), int(offset.y))
            else:
                pair = (int(offset[0]), int(offset[1]))
            if pair in seen:
                continue
            seen.add(pair)
            normalized.append(pair)
        return normalized or [(0, 0)]

    def _refresh_footprint_bounds(self) -> None:
        xs = [dx for dx, _ in self.footprint_offsets]
        ys = [dy for _, dy in self.footprint_offsets]
        self.footprint_min_dx = min(xs)
        self.footprint_min_dy = min(ys)
        self.footprint_max_dx = max(xs)
        self.footprint_max_dy = max(ys)
        self.footprint_width = max(1, self.footprint_max_dx - self.footprint_min_dx + 1)
        self.footprint_height = max(1, self.footprint_max_dy - self.footprint_min_dy + 1)

    def set_footprint_offsets(self, offsets: Iterable[tuple[int, int] | Position]) -> None:
        self.footprint_offsets = self._normalize_footprint_offsets(offsets)
        self._refresh_footprint_bounds()

    def set_footprint_cells(self, cells: Iterable[Position]) -> None:
        if self.position is None:
            raise ActionError("单位不在战场上。")
        self.set_footprint_offsets((cell.x - self.position.x, cell.y - self.position.y) for cell in cells)

    def reset_footprint_to_base(self) -> None:
        self.set_footprint_offsets(self.base_footprint_offsets)

    def footprint_cells_at(self, position: Position) -> list[Position]:
        return [Position(x, y) for x, y in body_coordinates(position.x, position.y, tuple(self.footprint_offsets))]

    def footprint_cells(self) -> list[Position]:
        if self.position is None or not self.alive or self.banished:
            return []
        return self.footprint_cells_at(self.position)

    def notify_action_declared(
        self,
        battle: "Battle",
        action_type: str,
        payload: dict[str, Any],
    ) -> None:
        for effect in list(battle.field_effects):
            if self.direct_effects_blocked() and not getattr(effect, "observes_immune_actions", False):
                continue
            effect.on_owner_action_declared(battle, action_type, payload)
        for component in list(self.iter_components()):
            component.on_owner_action_declared(battle, action_type, payload)

    def notify_basic_attack_finished(
        self,
        battle: "Battle",
        payload: dict[str, Any],
        damage_contexts: list["DamageContext"],
        *,
        missed: bool = False,
    ) -> None:
        if getattr(battle, "_measuring_attack_success", False):
            battle._attack_probe_success = any(
                ctx.source is not None and ctx.source.unit_id == self.unit_id
                and not ctx.is_skill and not ctx.cancelled
                and (ctx.actual_damage > 0 or (ctx.destroyed_as_clone and not getattr(battle, "_attack_probe_hp_only", False)))
                for ctx in damage_contexts
            )
            return
        for component in list(self.iter_components()):
            component.on_basic_attack_finished(battle, self, payload, damage_contexts, missed)
        battle.notify_field_basic_attack_finished(self, payload, damage_contexts, missed=missed)
        for unit in list(battle.all_units()):
            for component in list(unit.iter_components()):
                component.on_any_basic_attack_finished(battle, self, payload, damage_contexts, missed)

    def consume_attack_attempt_buffs(self, battle: "Battle") -> None:
        for status in list(self.statuses):
            if getattr(status, "consume_on_attack_attempt", False):
                self.remove_status(status, battle)

    def attack_actions_per_turn(self) -> int:
        value = self.base_attack_actions_per_turn
        for component in self.iter_components():
            value = component.modify_attack_actions_per_turn(value)
        return max(1, value)

    def normal_move_distance(self) -> int:
        value = int(self.stat("speed"))
        for component in self.iter_components():
            value = component.modify_normal_move_distance(value)
        return max(0, value)

    def normal_move_actions_per_turn(self) -> int:
        value = 1
        for component in self.iter_components():
            value = component.modify_normal_move_actions_per_turn(value)
        return max(1, int(value))

    def allows_split_normal_movement(self, battle: "Battle") -> bool:
        return any(component.allows_split_normal_movement(battle, self) for component in self.iter_components())

    def remaining_normal_move_distance(self, battle: "Battle") -> int:
        total = self.normal_move_distance()
        if not self.allows_split_normal_movement(battle):
            return 0 if self.normal_move_actions_used >= self.normal_move_actions_per_turn() else total
        return max(0, total - self.normal_move_steps_used)

    def total_shields(self) -> int:
        return self.shields + self.temporary_shields

    def add_temporary_shields(self, amount: int) -> None:
        if not self.direct_effects_blocked():
            self.temporary_shields += amount

    def consume_one_shield(self) -> bool:
        if self.temporary_shields > 0:
            self.temporary_shields -= 1
            return True
        if self.shields > 0:
            self.shields -= 1
            if self.protection_shields > 0:
                self.protection_shields -= 1
            return True
        return False

    def clear_end_of_turn_shields(self) -> None:
        self.shields = 0
        self.temporary_shields = 0
        self.protection_shields = 0

    def max_mana(self) -> float:
        value = max(float(self.stat("mana")), 0.0)
        if self.allow_unbounded_mana:
            value = max(value, float(self.current_mana))
        return round(value, 2)

    def clamp_mana(self) -> None:
        clamped = round(min(max(self.current_mana, 0.0), self.max_mana()), 2)
        if clamped > self.current_mana:
            # Correcting an invalid negative balance is not a gameplay recovery.
            object.__setattr__(self, "current_mana", clamped)
        else:
            self.current_mana = clamped

    def direct_effects_blocked(self) -> bool:
        return not getattr(self, "_paying_skill_cost", False) and any(component.blocks_direct_effects() for component in self.iter_components())

    def skill_non_damage_effects_blocked(self) -> bool:
        return any(component.blocks_skill_non_damage_effects() for component in self.iter_components())

    @property
    def magic_immunity(self) -> bool:
        if getattr(self, "_base_magic_immunity", False):
            return True
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is None or not self.alive or self.banished or self.position is None:
            return False
        if "real_men" in getattr(battle, "special_mode_codes", ()):
            return True
        return any(
            trait.grants_magic_immunity_to(battle, self)
            for source in battle.all_units()
            if source.alive and not source.banished and source.position is not None
            for trait in source.traits
        )

    @magic_immunity.setter
    def magic_immunity(self, value: bool) -> None:
        self._base_magic_immunity = bool(value)

    def gain_mana(self, amount: float) -> float:
        if self.direct_effects_blocked():
            return 0.0
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is not None and self.banished and "arena" in getattr(battle, "special_mode_codes", ()):
            return 0.0
        if amount > 0 and battle is not None and "no_recovery" in getattr(battle, "special_mode_codes", ()):
            return 0.0
        before = self.current_mana
        requested = round(self.current_mana + amount, 2)
        maximum = max(self.max_mana(), requested) if self.allow_unbounded_mana else self.max_mana()
        self.current_mana = round(min(max(requested, 0.0), maximum), 2)
        gained = round(self.current_mana - before, 2)
        return gained

    def spend_mana(self, amount: float) -> float:
        if self.direct_effects_blocked():
            return 0.0
        if any(getattr(component, "prevents_mana_loss", False) for component in self.iter_components()):
            return 0.0
        before = self.current_mana
        self.current_mana = round(max(0.0, self.current_mana - amount), 2)
        return round(before - self.current_mana, 2)

    def gain_mana_points(self, amount: float) -> float:
        if self.direct_effects_blocked():
            return 0.0
        before = self.mana_points
        self.mana_points = round(min(getattr(self, "max_mana_points", float("inf")), max(self.mana_points + amount, 0.0)), 2)
        return round(self.mana_points - before, 2)

    def drain_mana(self, amount: float) -> float:
        """Return the actual resource loss, distinct from paying a cost."""
        before = self.current_mana
        self.spend_mana(amount)
        return round(max(0.0, before - self.current_mana), 2)

    def spend_mana_points(self, amount: float) -> float:
        if self.direct_effects_blocked():
            return 0.0
        before = self.mana_points
        self.mana_points = round(max(self.mana_points - amount, 0.0), 2)
        return round(before - self.mana_points, 2)

    @property
    def cannot_move(self) -> bool:
        if getattr(self, "_cannot_move", False):
            return True
        if any(getattr(status, "prevents_movement", False) for status in self.statuses):
            return True
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        return bool(battle is not None and any(
            effect.blocks_unit_movement(battle, self) for effect in battle.field_effects
        ))

    @cannot_move.setter
    def cannot_move(self, value: bool) -> None:
        self._cannot_move = bool(value)

    def stat(self, stat_name: Literal["attack", "defense", "speed", "attack_range", "mana"]) -> float:
        base_value = getattr(self.base_stats, stat_name)
        value = float(base_value)
        for component in self.iter_components():
            before = value
            value = component.modify_stat(stat_name, value)
            for trait in (() if self.innate_traits_suppressed() else self.traits):
                value = trait.modify_received_stat_change(component, stat_name, before, value)
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is not None and not (self.banished and "arena" in getattr(battle, "special_mode_codes", ())):
            for effect in battle.field_effects:
                value = effect.modify_unit_stat(battle, self, stat_name, value)
        if stat_name in self.stat_minimums:
            return max(self.stat_minimums[stat_name], value)
        return value

    def targeting_range(self) -> int:
        value = int(self.stat("attack_range"))
        for component in self.iter_components():
            value = component.modify_targeting_range(value)
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is not None:
            for effect in battle.field_effects:
                value = effect.adjust_unit_targeting_range(battle, self, value)
        if battle is not None and "sympathy" in getattr(battle, "special_mode_codes", ()):
            if any(self.unit_id in getattr(effect, "_sympathy", {}).get("attack_range", set())
                   for effect in battle.field_effects):
                return 1
        return max(1, value)

    def is_enemy_of(self, other: "Unit") -> bool:
        return self.player_id != other.player_id

    def heal_fraction(self, amount: float, *, ignore_effect_immunity: bool = False) -> None:
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is not None and self.banished and "arena" in getattr(battle, "special_mode_codes", ()):
            return
        if amount > 0 and battle is not None and "no_recovery" in getattr(battle, "special_mode_codes", ()):
            return
        if self.direct_effects_blocked() and not ignore_effect_immunity:
            return
        if self.allow_overheal:
            self.current_hp = round(self.current_hp + amount, 4)
        else:
            self.current_hp = round(min(self.max_health, self.current_hp + amount), 4)

    def damage_fraction_after_limits(self, amount: float) -> float:
        amount = max(0.0, float(amount))
        for component in self.iter_components():
            amount = min(amount, max(0.0, component.limit_hp_loss(amount)))
        return round(amount, 4)

    def take_damage_fraction(self, amount: float) -> None:
        amount = self.damage_fraction_after_limits(amount)
        self.current_hp = round(max(0.0, self.current_hp - amount), 4)
        if self.current_hp <= 0:
            self.alive = False

    def can_take_turn_actions(self, battle: "Battle") -> bool:
        return (
            self.alive
            and not self.banished
            and not getattr(self, "equipped_to_id", None)
            and self.player_id == battle.active_player
            and battle.unit_belongs_to_current_turn(self)
            and self.turn_ready
            and battle.clone_can_act_this_turn(self)
        )

    def refresh_for_turn(self, battle: "Battle") -> None:
        self.move_used = False
        self.normal_move_steps_used = 0
        self.normal_move_actions_used = 0
        self.attacks_used = 0
        self.performed_active_skill = False
        self.moved_this_turn = False
        self.actions_taken_this_turn = []
        if self.is_summon and not self.can_act_on_entry_turn:
            self.turn_ready = False
            self.can_act_on_entry_turn = True
        else:
            self.turn_ready = True
        for component in list(self.iter_components()):
            component.on_owner_turn_start(battle)

    def finish_turn(self, battle: "Battle") -> None:
        for component in list(self.iter_components()):
            component.on_owner_turn_end(battle)

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        return {
            "id": self.unit_id,
            "player_id": self.player_id,
            "name": self.name,
            "title": self.title,
            "role": self.role,
            "attribute": self.attribute,
            "race": self.race,
            "level": self.level,
            "alive": self.alive,
            "banished": self.banished,
            "banish_turns_remaining": self.banish_turns_remaining,
            "banish_return_position": self.banish_return_position.to_dict() if self.banish_return_position else None,
            "is_summon": self.is_summon,
            "hero_code": str(getattr(self, "hero_code", "") or ""),
            "is_army_soldier": is_army_soldier(self),
            "is_clone": self.is_clone,
            "is_mount": self.is_mount,
            "allows_multiple_riders": self.allows_multiple_riders,
            "mount_owner_id": self.mount_owner_id,
            "mounted_on_unit_id": self.mounted_on_unit_id,
            "ridden_by_unit_id": self.ridden_by_unit_id,
            "ridden_by_unit_ids": (
                [rider.unit_id for rider in battle.riders_for(self)]
                if self.allows_multiple_riders else
                ([self.ridden_by_unit_id] if self.ridden_by_unit_id else [])
            ),
            "turn_ready": self.turn_ready,
            "position": self.position.to_dict() if self.position else None,
            "last_position": (
                self.last_position.to_dict()
                if getattr(self, "last_position", None)
                else None
            ),
            "footprint": {
                "width": self.footprint_width,
                "height": self.footprint_height,
                "min_dx": self.footprint_min_dx,
                "min_dy": self.footprint_min_dy,
                "max_dx": self.footprint_max_dx,
                "max_dy": self.footprint_max_dy,
                "offsets": [{"x": dx, "y": dy} for dx, dy in self.footprint_offsets],
            },
            "occupied_cells": [cell.to_dict() for cell in battle.unit_cells(self)],
            "hp": self.current_hp,
            "max_hp": self.max_health,
            "mana": self.current_mana,
            "max_mana": self.max_mana(),
            "unbounded_mana": bool(self.allow_unbounded_mana),
            "mana_points": self.mana_points,
            "base_stats": self.base_stats.to_dict(),
            "stats": {
                "attack": self.stat("attack"),
                "defense": self.stat("defense"),
                "speed": self.stat("speed"),
                "attack_range": self.targeting_range(),
                "mana": self.current_mana,
                "max_mana": self.max_mana(),
                "mana_points": self.mana_points,
            },
            "move_used": self.move_used,
            "normal_move_steps_used": self.normal_move_steps_used,
            "normal_move_actions_used": self.normal_move_actions_used,
            "normal_move_actions_per_turn": self.normal_move_actions_per_turn(),
            "attacks_used": self.attacks_used,
            "attacks_per_turn": self.attack_actions_per_turn(),
            "performed_active_skill": self.performed_active_skill,
            "moved_this_turn": self.moved_this_turn,
            "shields": self.shields,
            "protection_shields": self.protection_shields,
            "temporary_shields": self.temporary_shields,
            "total_shields": self.total_shields(),
            "dodge_charges": self.dodge_charges,
            "magic_immunity": self.magic_immunity,
            "physical_immunity": bool(getattr(self, "physical_immunity", False)),
            "cannot_be_targeted": self.cannot_be_targeted,
            "cannot_move": self.cannot_move,
            "cannot_normal_move": self.cannot_normal_move,
            "cannot_heal": self.cannot_heal,
            "cannot_attack": self.cannot_attack,
            "cannot_use_skills": self.cannot_use_skills,
            "is_siege_structure": bool(getattr(self, "is_siege_structure", False)),
            "siege_reload_cycle": bool(getattr(self, "siege_reload_cycle", False)),
            "siege_loaded": bool(getattr(self, "siege_loaded", False)),
            "siege_reload_state": str(
                getattr(self, "siege_reload_state", "")
                or ("ready" if bool(getattr(self, "siege_loaded", False)) else "empty")
            ),
            "siege_profile_id": str(getattr(self, "siege_profile_id", "") or ""),
            "siege_family": str(getattr(self, "siege_family", "") or ""),
            "siege_tier": int(getattr(self, "siege_tier", 0) or 0),
            "splash_radius": int(getattr(self, "splash_radius", 0) or 0),
            "ignore_units_while_moving": self.ignore_units_while_moving,
            "allow_enemy_destination_overlap": self.allow_enemy_destination_overlap,
            "standable_terrain": bool(getattr(self, "standable_terrain", False)),
            "raw_skill_text": self.raw_skill_text,
            "raw_trait_text": self.raw_trait_text,
            "weather_effect_text": hero_weather_effect_text(self),
            "skills": [skill.to_public_dict(battle) for skill in self.action_skills()],
            "traits": [trait.to_public_dict(battle) for trait in [*self.traits, *self.projected_traits()]],
            "statuses": [status.to_public_dict(battle) for status in self.statuses],
        }

    def allows_destination_overlap_with(self, battle: "Battle", occupant: "Unit") -> bool:
        return self.allow_enemy_destination_overlap and occupant.player_id != self.player_id


class HeroUnit(Unit, ABC):
    pass


_PYTHON_INNATE_TRAITS_SUPPRESSED = Unit.innate_traits_suppressed
_PYTHON_INNATE_TRAITS_SUPPRESSED_CODE = _PYTHON_INNATE_TRAITS_SUPPRESSED.__code__
_COMPONENT_SCAN_ANY = _PYTHON_INNATE_TRAITS_SUPPRESSED.__builtins__["any"]
_COMPONENT_SCAN_GLOBALS = _PYTHON_INNATE_TRAITS_SUPPRESSED.__globals__
_COMPONENT_SCAN_BUILTINS = _PYTHON_INNATE_TRAITS_SUPPRESSED.__builtins__
_COMPONENT_SCAN_NAMES = ("statuses", "suppresses_innate_traits", "getattr")


def _native_innate_traits_suppressed(self) -> bool:
    original = _PYTHON_INNATE_TRAITS_SUPPRESSED
    if (_NATIVE_COMPONENT_SCAN is not None and original.__code__ is _PYTHON_INNATE_TRAITS_SUPPRESSED_CODE
            and any is _COMPONENT_SCAN_ANY):
        return _NATIVE_COMPONENT_SCAN[0](self, _COMPONENT_SCAN_GLOBALS, _COMPONENT_SCAN_BUILTINS, _COMPONENT_SCAN_NAMES)
    return original(self)


# Bound callbacks serialize by the method name. Preserve the original lookup
# and keyword signature even when a callback is retained in a battle graph.
_native_innate_traits_suppressed.__name__ = _PYTHON_INNATE_TRAITS_SUPPRESSED.__name__
_native_innate_traits_suppressed.__qualname__ = _PYTHON_INNATE_TRAITS_SUPPRESSED.__qualname__


# The default retains the exact old function, without a per-call flag check.
# Offline ablation patches this binding together with its spawn environment.
if _NATIVE_COMPONENT_SCAN_ENABLED and _NATIVE_COMPONENT_SCAN is not None:
    Unit.innate_traits_suppressed = _native_innate_traits_suppressed


from wujiang.tactical.engine.battle_declarations import BattleDeclarationMixin
from wujiang.tactical.engine.battle_reactions import BattleReactionMixin
from wujiang.tactical.engine.battle_chain import BattleChainMixin
from wujiang.tactical.engine.battle_execution import BattleExecutionMixin
from wujiang.tactical.engine.battle_combat import BattleCombatMixin
from wujiang.tactical.engine.battle_movement import BattleMovementMixin
from wujiang.tactical.engine.battle_geometry import BattleGeometryMixin
from wujiang.tactical.engine.battle_turns import BattleTurnMixin
from wujiang.tactical.engine.battle_lifecycle import BattleLifecycleMixin
from wujiang.tactical.engine.battle_units import BattleUnitsMixin
from wujiang.tactical.engine.battle_presentation import BattlePresentationMixin


class Battle(BattleDeclarationMixin, BattleReactionMixin, BattleChainMixin, BattleExecutionMixin, BattleCombatMixin, BattleMovementMixin, BattleGeometryMixin, BattleTurnMixin, BattleLifecycleMixin, BattleUnitsMixin, BattlePresentationMixin):
    @property
    def chess_rng(self):
        # Keep the original instance-dictionary field and pickle layout. Even
        # a read followed by setstate(old_state) makes a query RNG-dependent.
        _note_battle_rng_access()
        try:
            return self.__dict__["chess_rng"]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute 'chess_rng'") from None

    @chess_rng.setter
    def chess_rng(self, value):
        _note_battle_rng_access()
        self.__dict__["chess_rng"] = value

    @chess_rng.deleter
    def chess_rng(self):
        _note_battle_rng_access()
        try:
            del self.__dict__["chess_rng"]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute 'chess_rng'") from None

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self.__dict__.setdefault("resolving_action", None)
        self.__dict__.setdefault("_separate_attack_sequences", {})
        self.__dict__.setdefault("pending_damage_choice", None)
        self.__dict__.setdefault("hell_rebirth_unit_ids", set())
        self.__dict__.setdefault("hell_dragon_return_cells", {})
        self.__dict__.setdefault("_pending_turn_advance_after_hell_rebirth", False)
        self.__dict__.setdefault("_pending_turn_end_after_effects_player_id", None)
        # Rebind after checkpoint loading or deepcopy; weak references are not saved.
        for unit in [*self.units.values(), *getattr(self, "destroyed_units", [])]:
            unit._battle_ref = weakref.ref(self)

    def __init__(
        self,
        *,
        width: int = 8,
        height: int = 8,
        damage_rule: Optional[DamageRule] = None,
    ) -> None:
        self.width = width
        self.height = height
        self.damage_rule = damage_rule or SummaryDamageRule()
        self.units: dict[str, Unit] = {}
        self.destroyed_units: list[Unit] = []
        self.field_effects: list[BattleFieldEffect] = []
        self.active_player = 1
        self.turn_number = 1
        self.round_number = 1
        self.winner: Optional[int] = None
        self.logs: list[str] = []
        self.pending_chain: Optional[ReactionWindow] = None
        self.pending_followup_actions: deque[QueuedAction] = deque()
        self._separate_attack_sequences: dict[str, dict[str, Any]] = {}
        self.pending_respawn_unit_ids: list[str] = []
        self.hell_rebirth_unit_ids: set[str] = set()
        self.hell_dragon_return_cells: dict[str, list[Position]] = {}
        self._pending_turn_advance_after_hell_rebirth = False
        self.pending_damage_choice: Optional[dict[str, Any]] = None
        self.turn_order_unit_ids: list[str] = []
        self.turn_slot_index = 0
        self.active_turn_unit_id: Optional[str] = None
        self.legacy_player_turn_mode = False
        self.initial_hero_count = 0
        self.turn_timeout_limit = 0
        self.turn_timeout_winner = 2
        self.completed_turns = 0
        self._log_suppression_depth = 0
        self._draining_followup_actions = False
        self._pending_end_turn_after_followups = False
        self._pending_turn_end_after_effects_player_id: Optional[int] = None
        self._processed_any_turn_end_choices: set[tuple[str, str]] = set()
        self.visual_events: list[VisualEvent] = []
        self._next_visual_event_id = 1
        self.stale_queued_action_count = 0
        self._next_action_resolution_token = 1
        self._current_action_resolution_token: Optional[int] = None
        self._skill_effect_receipts: dict[int, set[str]] = {}
        self._skill_effect_declaration_receipts: dict[str, set[str]] = {}
        self.resolving_action: Optional[QueuedAction] = None
        self.combat_stats: dict[str, dict[str, Any]] = {}
        self.summary_events: list[dict[str, Any]] = []
        self._next_summary_event_id = 1
        self._summary_defeated_unit_ids: set[str] = set()
        self.win_reason_code = ""
        self.win_reason_text = ""
        self.blocked_cells: set[tuple[int, int]] = set()
        self.army_orders: dict[int, dict[str, str]] = default_army_orders()
        self.army_ai_players: set[int] = set()
        self.army_strike_wave: str = ""
        self.fast_ai_simulation = False
        self.on_replay_checkpoint = None
        self._replay_match_end_emitted = False

    @property
    def current_action_resolution_token(self) -> Optional[int]:
        """Stable only while one declared queued action is resolving."""
        return self._current_action_resolution_token



















    @contextmanager
    def suppress_logs(self) -> Iterable[None]:
        self._log_suppression_depth += 1
        try:
            yield
        finally:
            self._log_suppression_depth = max(0, self._log_suppression_depth - 1)









    @staticmethod
    def canonical_weather_name(name: str) -> str:
        return "天空圣域" if name in {"天空圣域", "天空的圣域"} else name









































































    def shared_stealth_action_block_reason(self, unit: Unit) -> str:
        if unit.position is None or not unit.is_stealthed():
            return ""
        if not self.units_sharing_position(unit):
            return ""
        return f"{unit.name} 隐身时若与其他单位同格，不能攻击或使用技能。"

    def controllable_hero_units(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.player_units(player_id)
            if unit.alive and not unit.banished and not unit.is_summon
        ]

    def hero_units(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.player_units(player_id)
            if unit.alive and not unit.is_summon
        ]

    def on_field_hero_units(self) -> list[Unit]:
        return [
            unit
            for unit in self.all_units()
            if unit.alive
            and not unit.is_summon
            and not is_army_soldier(unit)
            and not unit.banished
            and unit.position is not None
        ]

    def clear_all_stealth_if_all_heroes_stealthed(self) -> None:
        heroes = self.on_field_hero_units()
        if not heroes:
            return
        if any(not hero.has_status("隐身") for hero in heroes):
            return
        self.log("场上所有武将都处于隐身状态，所有在场武将的隐身自动解除。")
        for hero in heroes:
            stealth = hero.get_status("隐身")
            if stealth is not None:
                hero.remove_status(stealth, self)































    def basic_attack_area_cells_for_payload(
        self,
        actor: Unit,
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[list[Position]]:
        if payload and payload.get("attack_variant") == "allied_heal":
            return None
        if payload and payload.get("queued_resolution"):
            declared_cells = self.payload_positions(payload, "attack_cells")
            if declared_cells:
                return declared_cells
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        override = next((component for component in actor.iter_components()
                         if getattr(component, "overrides_basic_attack_shape", False)), None)
        if override is not None:
            return override.basic_attack_area_cells(self, actor, resolved_payload)
        for component in actor.iter_components():
            cells = component.basic_attack_area_cells(self, actor, resolved_payload)
            if cells is not None:
                return cells
        return None


    def attack_ignores_shield(self, actor: Unit, target: Unit, *, payload: Optional[dict[str, Any]] = None) -> bool:
        ignore_shield, _ = self.attack_shield_flags(actor, target, payload=payload)
        return ignore_shield

    def attack_shield_flags(
        self,
        actor: Unit,
        target: Unit,
        *,
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, bool]:
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        ctx = TargetContext(
            actor=actor,
            target=target,
            action_name="普攻",
            is_skill=False,
            is_hostile=True,
            tags={"attack"},
        )
        for component in self.components_for(actor):
            component.on_targeted(self, ctx)
        for effect in list(self.field_effects):
            effect.on_targeted(self, ctx)
        if resolved_payload.get("ignore_shield"):
            ctx.ignore_shield = True
        if resolved_payload.get("half_ignore_shield"):
            ctx.half_ignore_shield = True
        return ctx.ignore_shield, ctx.half_ignore_shield

    def attack_ignores_stealth(self, actor: Unit, target: Unit) -> bool:
        return False

    def attack_target_allowed(
        self,
        actor: Unit,
        target: Unit,
        *,
        ignore_stealth: bool = False,
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        block_reason = self.shared_stealth_action_block_reason(actor)
        if block_reason:
            return False, block_reason
        if actor.cannot_attack:
            return False, f"{actor.name} 当前不能攻击。"
        if not actor.alive or actor.banished or actor.position is None or target.position is None:
            return False, "攻击对象不在战场上。"
        ok, reason = self.unit_can_be_selected(target, actor=actor, ignore_stealth=ignore_stealth)
        if not ok:
            return False, reason
        for effect in list(self.field_effects):
            ok, reason = effect.can_attack_target(self, actor, target)
            if not ok:
                return False, reason
        origins = self.unit_cells(actor)
        for component in actor.iter_components():
            override = component.basic_attack_origins(self, actor, payload)
            if override is not None:
                origins = override
        declared_resolution = self.is_declared_resolution(actor, payload or {}, {"attack"})
        target_cells = self.unit_cells(target)
        if not declared_resolution and (
            not origins or min(origin.distance_to(cell) for origin in origins for cell in target_cells) > actor.targeting_range()
        ):
            return False, "目标超出普攻范围。"
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        area_attack = (resolved_payload.get("attack_variant") != "allied_heal"
                       and self.basic_attack_area_cells_for_payload(actor, resolved_payload) is not None)
        ignore_line = any(component.ignores_direct_unit_target_line(self, actor) for component in actor.iter_components())
        if not declared_resolution and not area_attack and not ignore_line and not any(
            origin.distance_to(cell) <= actor.targeting_range() and self.cells_are_straight_aligned(origin, cell)
            for origin in origins for cell in target_cells
        ):
            return False, "目标不在普攻直线上。"
        from wujiang.tactical.engine.siege import siege_min_attack_range

        min_range = siege_min_attack_range(actor)
        if min_range and self.distance_between_units(actor, target) < min_range:
            return False, f"{actor.name} 无法对范 {min_range - 1} 开火。"
        if payload is not None and payload.get("x") is not None and payload.get("y") is not None:
            clicked = Position(int(payload["x"]), int(payload["y"]))
            if clicked not in target_cells:
                return False, "所点格子没有命中该目标。"
            if not declared_resolution and min(origin.distance_to(clicked) for origin in origins) > actor.targeting_range():
                return False, "所点目标格超出普攻范围。"
            if not declared_resolution and not area_attack and not ignore_line and not any(
                self.cells_are_straight_aligned(origin, clicked) for origin in origins
            ):
                return False, "所点目标格不在普攻直线上。"
        for component in list(actor.iter_components()):
            ok, reason = component.can_attack_target(self, actor, target)
            if not ok:
                return False, reason
        component_payload = None if payload is None else resolved_payload
        for component in list(actor.iter_components()):
            ok, reason = component.can_attack_target_with_payload(self, actor, target, component_payload)
            if not ok:
                return False, reason
        return True, ""

    def allied_basic_heal_amount(self, actor: Unit, target: Unit) -> float:
        if actor.player_id != target.player_id:
            return 0.0
        return max((component.allied_basic_heal_amount(self, actor, target) for component in actor.iter_components()), default=0.0)







    def declared_source_position(self, payload: dict[str, Any]) -> Optional[Position]:
        if payload.get("declared_source_x") is None or payload.get("declared_source_y") is None:
            return None
        return Position(int(payload["declared_source_x"]), int(payload["declared_source_y"]))

    def declared_target_position(self, payload: dict[str, Any]) -> Optional[Position]:
        if payload.get("declared_target_x") is None or payload.get("declared_target_y") is None:
            return None
        return Position(int(payload["declared_target_x"]), int(payload["declared_target_y"]))

    def payload_positions(self, payload: dict[str, Any], key: str) -> list[Position]:
        cells = payload.get(key)
        if not isinstance(cells, list):
            return []
        result: list[Position] = []
        for cell in cells:
            if not isinstance(cell, dict) or cell.get("x") is None or cell.get("y") is None:
                raise ActionError("坐标格式不正确。")
            result.append(Position(int(cell["x"]), int(cell["y"])))
        return result

    def declared_cell_for_target(
        self,
        actor: Unit,
        target: Unit,
        payload: dict[str, Any],
    ) -> Optional[Position]:
        target_cells = self.unit_cells(target)
        if not target_cells:
            return target.position
        if payload.get("x") is not None and payload.get("y") is not None:
            clicked = Position(int(payload["x"]), int(payload["y"]))
            if clicked in target_cells:
                return clicked
        existing = self.declared_target_position(payload)
        if existing is not None and existing in target_cells:
            return existing
        if target.position in target_cells:
            return target.position
        actor_cells = self.unit_cells(actor)
        if not actor_cells and actor.position is not None:
            actor_cells = self.unit_cells_at(actor, actor.position)
        if actor_cells:
            return min(
                target_cells,
                key=lambda cell: (
                    min(origin.distance_to(cell) for origin in actor_cells),
                    cell.y,
                    cell.x,
                ),
            )
        return sorted(target_cells, key=lambda cell: (cell.y, cell.x))[0]

    def payload_target_unit_ids(self, payload: dict[str, Any]) -> list[str]:
        target_ids = payload.get("target_unit_ids")
        if isinstance(target_ids, list):
            result: list[str] = []
            seen: set[str] = set()
            for unit_id in target_ids:
                unit_key = str(unit_id or "").strip()
                if not unit_key or unit_key in seen:
                    continue
                seen.add(unit_key)
                result.append(unit_key)
            return result
        target_id = payload.get("target_unit_id")
        if target_id:
            return [str(target_id)]
        return []

    def resolve_declared_target_unit(
        self,
        actor: Unit,
        payload: dict[str, Any],
        *,
        ignore_stealth: bool = False,
    ) -> Optional[Unit]:
        preferred_ids = self.payload_target_unit_ids(payload)
        declared = self.declared_target_position(payload)
        if declared is not None:
            for unit_id in preferred_ids:
                preferred = self.selectable_unit_at(
                    declared,
                    actor=actor,
                    ignore_stealth=ignore_stealth,
                    preferred_unit_id=unit_id,
                )
                if preferred is not None:
                    return self.effect_recipient(preferred)
            selected = self.selectable_unit_at(declared, actor=actor, ignore_stealth=ignore_stealth)
            return self.effect_recipient(selected) if selected is not None else None
        for unit_id in preferred_ids:
            unit = self.units.get(unit_id)
            if unit is None:
                continue
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if ok:
                return self.effect_recipient(unit)
        return None

    def resolve_from_declared_origin(
        self,
        actor: Unit,
        payload: dict[str, Any],
        resolver: Any,
    ) -> Any:
        declared = None if payload.get("_attack_pre_move_done") else self.declared_source_position(payload)
        if declared is None or actor.position is None or actor.position == declared:
            return resolver()
        actual_position = actor.position
        previous_actual = getattr(actor, "_resolution_actual_position", None)
        actor._resolution_actual_position = previous_actual or actual_position
        actor.position = declared
        try:
            result = resolver()
        except Exception:
            if actor.alive and not actor.banished and actor.position is not None:
                actor.position = getattr(actor, "_resolution_actual_position", actual_position)
            raise
        finally:
            resolved_actual = getattr(actor, "_resolution_actual_position", actual_position)
            if previous_actual is None:
                actor.__dict__.pop("_resolution_actual_position", None)
            else:
                actor._resolution_actual_position = previous_actual
        if actor.position == declared and resolved_actual == actual_position:
            actor.position = actual_position
        return result

    def is_declared_resolution(self, actor: Unit, payload: dict[str, Any], action_types: set[str]) -> bool:
        queued = self.resolving_action
        declaration_id = payload.get("declaration_id")
        return bool(queued is not None and queued.actor_id == actor.unit_id
                    and queued.action_type in action_types and queued.payload.get("queued_resolution")
                    and declaration_id and declaration_id == queued.payload.get("declaration_id"))


    def resolved_basic_attack_payload(
        self,
        actor: Unit,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        resolved = dict(payload or {})
        components = list(actor.iter_components())
        override = next((component for component in components if getattr(component, "overrides_basic_attack_shape", False)), None)
        for component in components:
            if override is not None and (component is override or type(component).basic_attack_area_cells is not BattleComponent.basic_attack_area_cells
                                         or type(component).basic_attack_preview is not BattleComponent.basic_attack_preview):
                continue
            resolved.update(component.basic_attack_payload_metadata(self, actor, resolved))
        if override is not None and resolved.get("attack_variant") != "allied_heal":
            resolved.update(override.basic_attack_payload_metadata(self, actor, resolved))
        for component in components:
            if getattr(component, "applies_after_basic_attack_shape", False):
                resolved.update(component.basic_attack_payload_metadata(self, actor, resolved))
        if "attack_cost" not in resolved or resolved.get("attack_variant") == "allied_heal":
            resolved["attack_cost"] = 1
        for effect in list(self.field_effects):
            resolved.update(effect.basic_attack_payload_metadata(self, actor, resolved))
        return resolved

    def basic_attack_resource_allowed(self, actor: Unit, payload: dict[str, Any]) -> tuple[bool, str]:
        cost = int(payload.get("attack_cost", 1))
        if cost < 0:
            return False, "普攻次数消耗无效。"
        if payload.get("attack_variant") == "kiku_legacy" and not actor.has_status("菊之遗击"):
            return False, "当前没有【菊之遗击】。"
        if (cost > 0 and actor.attacks_used + cost > actor.attack_actions_per_turn()
                and not self.is_declared_resolution(actor, payload, {"attack"})):
            return False, "本回合攻击次数已用完。"
        for component in actor.iter_components():
            ok, reason = component.can_use_basic_attack(self, actor, payload)
            if not ok:
                return False, reason
        return True, ""

    def basic_attack_action_specs(self, unit: Unit) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = [
            {
                "code": "attack",
                "name": "普攻",
                "description": "普通攻击。",
                "attack_payload": {"attack_variant": "default"},
            }
        ]
        if self.allied_basic_heal_amount(unit, unit) > 0:
            actions.append({
                "code": "allied_heal_attack", "name": "普攻治疗",
                "description": "消耗一次普攻，治疗一个己方单位1/4（可选自身）。",
                "attack_payload": {"attack_variant": "allied_heal"},
            })
        seen_codes = {entry["code"] for entry in actions}
        for component in unit.iter_components():
            for entry in component.basic_attack_action_entries(self, unit):
                code = str(entry.get("code") or "").strip()
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)
                actions.append(dict(entry))
        return actions

    def basic_attack_preview_for_payload(
        self,
        actor: Unit,
        payload: Optional[dict[str, Any]] = None,
        *, _target_ids_only: bool = False,
    ) -> dict[str, Any]:
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        if not self.basic_attack_resource_allowed(actor, resolved_payload)[0]:
            return {"cells": [], "target_unit_ids": [], "requires_target": True}
        healing_only = resolved_payload.get("attack_variant") == "allied_heal"
        if not healing_only:
            override = next((component for component in actor.iter_components() if getattr(component, "overrides_basic_attack_shape", False)), None)
            if override is not None:
                return override.basic_attack_preview(self, actor, resolved_payload)
            for component in actor.iter_components():
                preview = component.basic_attack_preview(self, actor, resolved_payload)
                if preview is not None:
                    return preview
        attack_targets: list[str] = []
        attack_cells: list[dict[str, int]] = []
        candidates = list(self.player_units(actor.player_id)) if healing_only else list(self.all_units())
        forced_target = self.forced_basic_attack_target(actor)
        if not healing_only and forced_target is not None and all(unit.unit_id != forced_target.unit_id for unit in candidates):
            candidates.append(forced_target)
        for target in candidates:
            ignore_stealth = self.attack_ignores_stealth(actor, target)
            if self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=resolved_payload)[0]:
                attack_targets.append(target.unit_id)
                valid_cells = []
                for cell in self.unit_cells(target):
                    cell_payload = dict(resolved_payload)
                    cell_payload["x"] = cell.x
                    cell_payload["y"] = cell.y
                    if self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=cell_payload)[0]:
                        valid_cells.append(cell)
                if not valid_cells:
                    valid_cells = self.unit_cells(target)
                if not _target_ids_only:
                    attack_cells.extend(cell.to_dict() for cell in valid_cells)
                else:
                    # All legality/metadata/component hooks above still run in
                    # their original order. Only exact immutable Position's
                    # pure dict construction is dispensable for an ID query.
                    for cell in valid_cells:
                        if type(cell) is not Position or Position.to_dict is not _CANONICAL_POSITION_TO_DICT:
                            cell.to_dict()
        allied_heal_targets = [target_id for target_id in attack_targets
                              if self.allied_basic_heal_amount(actor, self.get_unit(target_id)) > 0]
        if _target_ids_only:
            return {"target_unit_ids": attack_targets}
        return {"cells": attack_cells, "target_unit_ids": attack_targets, "requires_target": True,
                "allied_heal_target_ids": allied_heal_targets}

    _canonical_basic_attack_preview = basic_attack_preview_for_payload

    def basic_attack_target_ids_for_payload(self, actor: Unit, payload=None):
        preview = self.basic_attack_preview_for_payload
        if (_AI_TARGET_IDS_ENABLED
                and getattr(preview, "__func__", None) is Battle._canonical_basic_attack_preview):
            return preview(actor, payload, _target_ids_only=True).get("target_unit_ids", [])
        return preview(actor, payload).get("target_unit_ids", [])

    def forced_basic_attack_target(self, actor: Unit) -> Optional[Unit]:
        for component in actor.iter_components():
            target_id = str(getattr(component, "required_attack_target_id", "") or "")
            if not target_id:
                continue
            forces = getattr(component, "forces_attack_target", None)
            if callable(forces) and not bool(forces(self)):
                continue
            target = self.units.get(target_id)
            if target is not None and target.alive and target.position is not None and not target.banished:
                return target
        return None





    def basic_attack_preview_power(self, actor: Unit, payload: Optional[dict[str, Any]] = None) -> float:
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        if resolved_payload.get("attack_power_override") is not None:
            return float(resolved_payload["attack_power_override"])
        attack_power = actor.stat("attack")
        attack_power += float(resolved_payload.get("attack_bonus", 0.0) or 0.0)
        for component in actor.iter_components():
            bonus_attack = float(getattr(component, "bonus_attack", 0.0) or 0.0)
            if bonus_attack:
                attack_power += bonus_attack
        return attack_power





















    def attack_would_deal_damage(self, queued: QueuedAction) -> bool:
        with battle_state_rollback(self), self.suppress_logs():
            self._measuring_attack_success = True
            self._attack_probe_success = False
            probe_actor = self.units.get(queued.actor_id)
            self._attack_probe_hp_only = probe_actor is not None and any(getattr(component, "reaction_prevention_requires_hp", False) for component in probe_actor.iter_components())
            try:
                self.resolve_queued_action(replace(queued, payload=dict(queued.payload)))
            except ActionError:
                return False
            return self._attack_probe_success




    def pass_turn(self, actor: Unit) -> None:
        actor.turn_ready = False
        self.log(f"{actor.name} 结束了自己的行动。")




















    def unit_has_available_instant_action(self, unit: Unit) -> bool:
        if unit is None or not unit.alive or unit.banished:
            return False
        if self.pending_chain is not None or self.current_respawn_prompt() is not None:
            return False
        return any(skill.timing == "instant" and skill.can_use(self, unit, {})[0] for skill in unit.skills)

    def instant_action_units_for_player(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.player_units(player_id)
            if self.unit_has_available_instant_action(unit)
        ]



_CANONICAL_CHESS_RNG_DESCRIPTOR = Battle.chess_rng

_DIRECT_COMPONENT_DISCOVERY = (
    Unit.iter_components, Unit.projected_traits, Unit.innate_traits_suppressed,
    BattleComponent.provided_components, BattleComponent.projected_trait_for,
    BattleComponent.sync_linked_state, BattleComponent.on_destroyed_hero_count_changed,
    Battle.all_units,
    BattleComponent.runtime_children, Unit.__getstate__, SummaryDamageRule.calculate_damage,
)
_DIRECT_COMPONENT_CODES = tuple((method, method.__code__) for method in _DIRECT_COMPONENT_DISCOVERY)

# Original pure placement query implementations. The optional batch rejects
# runtime method/code/default/closure replacements and all field descriptors.
_DIRECT_PLACEMENT_QUERIES = (Battle, Unit, Position, body_coordinates, tuple(
    (owner, name, function, function.__code__, function.__defaults__,
     tuple((function.__kwdefaults__ or {}).items()),
     tuple(cell.cell_contents for cell in function.__closure__ or ()))
    for owner, name, function in (
        *((Battle, name, getattr(Battle, name)) for name in (
            "can_place_unit", "unit_cells_at", "in_bounds", "is_occupied", "units_at",
            "unit_occupies", "blocks_position_for", "units_can_overlap")),
        (Unit, "footprint_cells_at", Unit.footprint_cells_at),
        (Unit, "allows_destination_overlap_with", Unit.allows_destination_overlap_with),
        (Position, "__init__", Position.__init__),
        (body_coordinates, "__wrapped__", body_coordinates.__wrapped__),
    )
))
