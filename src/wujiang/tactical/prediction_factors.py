"""Exact marginal expectations for certified, recipient-local area damage.

This is a specialization of the prediction query, not a battle resolver. It
still executes the original queued action and final utility for every local
outcome. Unknown hooks keep the general joint enumerator. In particular, a
mean HP loss is never substituted before a death/control threshold.
"""
from __future__ import annotations

from contextlib import contextmanager
import contextlib
from functools import lru_cache
import hashlib
import importlib
import math
import os
from pathlib import Path
import sys
from types import CodeType, FunctionType

from wujiang.tactical.engine import core
from wujiang.tactical.prediction import (ForecastRandom, forecasting_active, note, outcomes,
                                         stable_observations, UnstableObservation)


# Reviewed dependency contracts, pinned to the implementation actually read.
# A later engine/trait edit disables this specialization until reviewed again.
# Normalize CRLF so checkout line endings do not change the contract.
_SOURCES = {
    "wujiang.tactical.heroes.ai_policy": "fd4a60de23c4e97dcd1812c91b8bae63f0e19dc31b86a6b7c8bb5da0285cf805",
    "wujiang.tactical.heroes.mechanics.resource_effect_ai": "59fc73bb39add025fb6daaccb350f3fc8d44d21180bed23ed2a21bfa2580f729",
    "wujiang.tactical.heroes.mechanics.simulated_effect_ai": "a70f77084c36349c4b4b5e6eec747dd3104d8d8e65eddd7e0422462814875ad7",
    "wujiang.tactical.heroes.electronic_infinity": "eb29844e2e3adecd803691cc1ad41fbb9714af34d1485960046a48b3c1456fc2",
    "wujiang.tactical.heroes.flying_dragon_ship": "f10498fb3c4a07103106c07572aff229691357019ce0535f40ca1746566b1fd1",
    "wujiang.tactical.heroes.flame_manager": "92956aea1ca8c55e17a56aefa653cf9a9b7ee276780abe9ccc0728d6770fa6fb",
    "wujiang.tactical.heroes.gentleman": "c47eaba7eeecd076d6900715042349e88aeb7fcc70f51fef4ab94b2fb741d149",
    "wujiang.tactical.heroes.mechanics.ancient_mecha": "f91a95499f5e3f468e92f6656fe28461f4bdf5683e2ca38cd216587b18469f21",
    "wujiang.tactical.heroes.mechanics.barbarian_king": "4f342947bf07301a00b0839c6ee7175409e2220f58f1611855d9fa680b19e5da",
    "wujiang.tactical.heroes.mechanics.bird_king": "47ed47d4fd235379e96d70b4cdb19d2d8c9312cb9fe9ca07031a97709eb7efa9",
    "wujiang.tactical.heroes.mechanics.gladiator_cooperation": "3ebb6db2d6721efe1ff45971577f3a0578680ad3fefc8e910a71e35c2a867814",
    "wujiang.tactical.heroes.mechanics.gladiator_core": "6b6af4befd058a5a27ed67706d327b63b95e55216b150a2daa671c2ad8372dc2",
    "wujiang.tactical.heroes.mechanics.electronic_barrier": "dfa6dde46527a9d104de2a1c158a5961f2fdde13eaccb726239cac5344dd72eb",
    "wujiang.tactical.heroes.mechanics.hell_core": "c7dd2695244a55b342b9cec74d716fcef25a18e542004ef3b20298b0e014e13f",
    "wujiang.tactical.heroes.mechanics.jade": "7af49d6fa113474507f17da95e5c6f07528cc0836e08702ed19102b864c910ff",
    "wujiang.tactical.heroes.mechanics.sanctuary": "5751be8bbb0739aa53437e42b105dcff42991b6c6d8a118ab8a7e31dbc110fa1",
    "wujiang.tactical.heroes.mechanics.giant_shield": "0b5df87036113d62b358b2d0967cd95f63282a89ea3ff33143f6d485b52deae8",
    "wujiang.tactical.heroes.mechanics.ice_emperor": "81d49245a0ba22074d5613a0111c95717c1e074468b119dffb30156c1313f10a",
    "wujiang.tactical.heroes.mechanics.tianyuan": "70aee47dd4f36fb2100088c209361cb177dddf91d9691e140b745a46f343dc23",
    "wujiang.tactical.heroes.mechanics.messenger_crow": "632958697ade8e72a6bc44770d773803e84f461a9d9ccfc4af2158c938653c71",
    "wujiang.tactical.heroes.storm_spirit": "d388eeca89e67a6c228ce5ae74c99578f637fd97d214ee40e0708b63afcf8bab",
    "wujiang.tactical.heroes.super_crow": "9d3978ed1af17ee970041f80b327851c3086109e5cef216c81765c7abde356d9",
    "wujiang.tactical.heroes.wyvern_raider": "bfceea3825506de5f7e140d45f0ba637b4aab51909ef1869ee280f99d5e60264",
    "wujiang.tactical.rooms.ai_targets": "390f6e548aaa40d535c222017da79f50489dbd977e36222298718aae9bda6824",
    "wujiang.tactical.rooms.ai_probe": "72fba98cc9f2814dda6dfada6a9c0446358535c5da0c783f5381cfd40e682885",
    "wujiang.tactical.engine.core": "08ddc2dab3cf88654d0453b4079fbaa589fba9babe53b8159b1f0ccf2e4f87ba",
    "wujiang.tactical.heroes.common": "427b78a3e5f2f4b688fff68ab67e491d3b556f9465f2703ad3d65d1896a1d210",
    "wujiang.tactical.heroes.excel_roster": "e32124761f4088d139db1407985b103419ba64d364c831f031cd6506d1898684",
    "wujiang.tactical.heroes.mechanics.hell_rebirth": "888025e1a3cbd1f9e487990cdc321507dd47dbb3864484bc2163b8ac51230d0c",
    "wujiang.tactical.heroes.mechanics.hell_dog_hunter": "b6f33c32a67245d1d7987533dfc3d8c18a1bc2a51ae69ed7161c19b87fa074ec",
    "wujiang.tactical.heroes.mechanics.hell_dragon": "9745b7ef379350ae975b86cd449a063c87e79c3347579873cfb235735106a173",
    "wujiang.tactical.heroes.mechanics.hell_wargod": "269e3a860b9dce5d32c09eb4d268b851320b852fc5ad4f20ba1d03f1ef5a3f31",
    "wujiang.tactical.heroes.mechanics.weapon_copy": "27747f6f3128e392f0ceaddcc1588b4b7cb17c6e355488cceb4d62c428658fc6",
    "wujiang.tactical.rooms.equipment_effects": "4df3f0232e6d804921df4b5c789791ba178618d58688f27288353c2409dab83b",
    "wujiang.tactical.rooms.synergy": "6e650a65f2023ed76778e6e7e5c5c4202ed2a64b9e74dd1c9c1ebf653305a2b9",
}

# This binding is declared in the reviewed Battle class body, and deliberately
# keeps the public preview method's name/code. Admit only that exact identity;
# unknown aliases and a rebound original alias remain unsupported.
_METHOD_ALIASES = {
    ("wujiang.tactical.engine.core", "Battle"): {
        "_canonical_basic_attack_preview": "basic_attack_preview_for_payload",
    },
}

_HOOKS = (
    "on_before_damage", "on_final_damage", "on_before_hp_loss", "on_after_damage",
    "on_battle_damage", "on_damage_cancelled", "lethal_damage_remaining_hp",
    "limit_hp_loss", "modify_stat", "modify_received_stat_change", "on_unit_destroyed",
    "on_owned_summon_destroyed", "on_owner_removed", "on_destroyed_hero_count_changed",
    "sync_linked_state", "on_skill_effect_finished", "on_removed", "runtime_children",
    "provided_components", "on_confirmed_damage_destruction", "on_hell_forced_skill_death",
    "on_hell_dragon_destroyed",
    "on_before_heal", "on_after_heal", "permits_immune_heal",
    "blocks_direct_effects", "blocks_skill_non_damage_effects", "grants_magic_immunity_to",
)

# All non-default callbacks admitted by this first contract. Damage-context
# writes and recipient-local writes commute between distinct recipients.
_LOCAL = {
    "wujiang.tactical.engine.core": {
        "TemporaryDefenseStatus.modify_stat", "TemporaryDefenseStatus.on_after_damage",
    },
    "wujiang.tactical.heroes.common": {
        "FlagStatus.on_removed", "MagicImmunityStatus.on_removed",
        "StatModifierStatus.modify_stat", "SourcedDefenseStatus.modify_stat", "SlowStatus.modify_stat",
        # All three return before their attack-only logic for this skill query.
        "PrecisionTrainingTrait.on_before_damage", "PrecisionTrainingTrait.on_after_damage",
        "PrecisionTrainingTrait.on_damage_cancelled",
    },
    "wujiang.tactical.rooms.synergy": {"SynergyStatus.modify_stat", "SynergyStatus.on_before_damage"},
    "wujiang.tactical.rooms.equipment_effects": {
        "EquipmentStatus.modify_stat", "EquipmentStatus.on_before_damage",
        "EquipmentStatus.on_after_damage", "EquipmentStatus.on_damage_cancelled",
        "EquipmentStatus.lethal_damage_remaining_hp", "EquipmentStatus.on_before_heal",
    },
    "wujiang.tactical.heroes.mechanics.hell_rebirth": {
        "HellRebirthTrait.on_after_damage", "HellRebirthTrait.on_hell_forced_skill_death",
        "HellAssassinRebirthTrait.on_after_damage", "HellAssassinRebirthTrait.on_hell_forced_skill_death",
    },
    "wujiang.tactical.heroes.mechanics.hell_dog_hunter": {
        "HellHunterRescueTrait.on_after_damage", "HellHunterRescueTrait.on_hell_forced_skill_death",
        "HellSnowRebirthTrait.on_after_damage", "HellSnowRebirthTrait.on_hell_forced_skill_death",
    },
    "wujiang.tactical.heroes.mechanics.hell_wargod": {
        "HellWarGodRebirthTrait.on_after_damage", "HellWarGodRebirthTrait.on_hell_forced_skill_death",
        "HellSikaSacrificeTrait.on_after_damage", "HellWarGodStatus.modify_stat",
    },
    "wujiang.tactical.heroes.mechanics.hell_dragon": {
        "HellDragonSacrificeTrait.on_hell_dragon_destroyed",
        "FlyingDragonOrbStatus.on_before_damage",
    },
    "wujiang.tactical.heroes.mechanics.weapon_copy": {
        # Both implementations only cancel physical attacks; skill hits return.
        "PhysicalImmunityStatus.on_before_damage",
    },
}


class _CoupledEffect(BaseException):
    """Unwind even through gameplay's ordinary Exception handlers."""


@lru_cache(maxsize=None)
def _source_code(module_name):
    module = importlib.import_module(module_name)
    from wujiang.tactical.prediction_contracts import load_contracts
    contracts = load_contracts()
    expected = _SOURCES.get(module_name)
    if expected is None and contracts is not None:
        expected = contracts["sources"].get(module_name)
    try:
        data = Path(module.__file__).read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return {}
    if not expected or hashlib.sha256(data).hexdigest() != expected:
        return {}
    if contracts is not None and contracts["sources"].get(module_name) == expected:
        return {name: tuple(codes) for name, codes in contracts["codes"][module_name].items()}
    # Compile only; never execute source or reconstruct a second game module.
    result = {}
    def visit(code):
        result.setdefault(code.co_qualname, []).append(code)
        for item in code.co_consts:
            if isinstance(item, CodeType):
                visit(item)
    try:
        visit(compile(data, module.__file__, "exec", dont_inherit=True))
    except (SyntaxError, ValueError):
        return {}
    return {name: tuple(codes) for name, codes in result.items()}


@lru_cache(maxsize=4096)
def _trusted(function, code):
    if type(function) is not FunctionType:
        return False
    module = sys.modules[function.__module__]
    if function.__globals__ is not vars(module):
        return False
    candidates = _source_code(function.__module__).get(function.__qualname__, ())
    if candidates and type(candidates[0]) is str:
        from wujiang.tactical.prediction_inline import code_hash
        return code_hash(code) in candidates
    return code in candidates


def _original(function):
    if type(function) is FunctionType and function.__globals__ is vars(contextlib):
        # @contextmanager keeps its implementation in a closure. Validate both
        # the standard wrapper and the enclosed original, not just __wrapped__.
        wrappers = tuple(code for code in contextmanager.__code__.co_consts if isinstance(code, CodeType))
        return (function.__code__ in wrappers and len(function.__closure__ or ()) == 1
                and function.__closure__[0].cell_contents is getattr(function, "__wrapped__", None)
                and _original(function.__wrapped__))
    return type(function) is FunctionType and _trusted(function, function.__code__)


@lru_cache(maxsize=256)
def _method_names(module_name, class_name):
    prefix = class_name + "."
    return frozenset(name[len(prefix):] for name in _source_code(module_name)
                     if name.startswith(prefix) and "." not in name[len(prefix):])


def _class_methods_original(cls):
    # An audited empty class (notably Trait) has a class-body code object but
    # no directly defined methods. Absence of methods is not absence of source.
    if cls.__qualname__ not in _source_code(cls.__module__):
        note("factorization_method_rejected:" + cls.__qualname__ + ":source_unavailable")
        return False
    names = _method_names(cls.__module__, cls.__qualname__)
    aliases = _METHOD_ALIASES.get((cls.__module__, cls.__qualname__), {})
    for alias, original in aliases.items():
        if original not in names or vars(cls).get(alias) is not vars(cls).get(original):
            note("factorization_method_rejected:" + cls.__qualname__ + "." + alias + ":alias_rebound")
            return False
    for name, value in vars(cls).items():
        if name not in names and name not in aliases and isinstance(value, (FunctionType, property, staticmethod, classmethod)):
            note("factorization_method_rejected:" + cls.__qualname__ + "." + name + ":unexpected_member")
            return False
    for name in names:
        value = vars(cls).get(name)
        functions = ((value.fget, value.fset, value.fdel) if isinstance(value, property)
                     else (value.__func__,) if isinstance(value, (staticmethod, classmethod)) else (value,))
        for function in functions:
            if function is not None and (not _original(function)
                    or function.__qualname__ != cls.__qualname__ + "." + name):
                note("factorization_method_rejected:" + cls.__qualname__ + "." + name + ":changed_method")
                return False
        if value is None:
            note("factorization_method_rejected:" + cls.__qualname__ + "." + name + ":missing_method")
            return False
    return True


def engine_factor_support():
    """Inspect engine AND reviewed component hierarchies without a battle."""
    classes = {cls.__qualname__: cls for cls in
               (core.SummaryDamageRule, core.Battle, core.Unit, core.BattleComponent, core.StatusEffect)}
    for module_name, methods in _LOCAL.items():
        module = importlib.import_module(module_name)
        for class_name in {name.split(".", 1)[0] for name in methods}:
            for cls in vars(module)[class_name].__mro__:
                if cls not in (object, core.ABC):
                    classes[cls.__module__ + "." + cls.__qualname__] = cls
    return {name: _class_methods_original(cls) for name, cls in classes.items()}


def _ordinary_unit(unit):
    if (type(unit).__getattribute__ is not object.__getattribute__ or type(unit).__setattr__ is not core.Unit.__setattr__
            or getattr(type(unit), "__getattr__", None) is not None):
        return False
    # Hero construction can vary; every operation used by resolution must use
    # the reviewed Unit implementation, including property getters.
    for name, value in vars(core.Unit).items():
        if name not in {"__init__", "build_skills", "build_traits"} and (callable(value) or isinstance(value, property)):
            if getattr(type(unit), name, None) is not getattr(core.Unit, name):
                return False
        if name in vars(unit) and callable(value):
            return False
    if (type(unit.base_stats) is not core.Stats or type(unit.stat_minimums) is not dict
            or any(type(items) is not list for items in (unit.skills, unit.traits, unit.statuses))):
        return False
    return not (unit.is_clone or unit.is_summon or unit.summoner_id or unit.mounted_on_unit_id
                or unit.ridden_by_unit_id or unit.mount_owner_id or unit.banished or not unit.alive)


def _component_type_contract(kind):
    if (kind.__getattribute__ is not object.__getattribute__
            or kind.__setattr__ is not object.__setattr__
            or getattr(kind, "__getattr__", None) is not None):
        return False
    for cls in kind.__mro__:
        if any(isinstance(value, property) and cls.__module__ != core.__name__ for value in vars(cls).values()):
            return False
    overridden = False
    for name in _HOOKS:
        function = getattr(kind, name, None)
        default = getattr(core.BattleComponent, name, None)
        if function is default:
            continue
        if (not _original(function)
                or function.__qualname__ not in _LOCAL.get(function.__module__, ())):
            return False
        overridden = True
    if overridden:
        # Helpers reached by a non-default contract must also be original;
        # subclass/instance overrides cannot silently replace a reviewed helper.
        for cls in kind.__mro__:
            if cls in (object, core.ABC):
                continue
            if cls.__module__ not in _SOURCES or not _class_methods_original(cls):
                return False
    return True


def _component_contract(component, source, reviewed):
    kind = type(component)
    if kind not in reviewed:
        reviewed[kind] = _component_type_contract(kind)
    if not reviewed[kind] or any(name in vars(component) for name in _HOOKS):
        return False
    if any(callable(value) for value in vars(component).values()):
        return False
    if any(cls.__module__ == "wujiang.tactical.rooms.equipment_effects" and cls.__name__ == "EquipmentStatus"
           for cls in type(component).__mro__):
        # A counter can kill the source; source healing/stacking can affect later
        # hits. Keep these in the joint evaluator, even when currently capped.
        if component.code == "德古拉之爪" or (component.owner is source and
                component.code in {"吸血面具", "嗜血大剑", "腰带"}):
            return False
    return True


def _reject(reason):
    note("factorization_rejected_" + reason)
    return False


def area_query(queued):
    return (type(queued) is core.QueuedAction and queued.action_type == "skill_effect"
            and type(queued.payload) is dict and queued.payload.get("effect_code") == "area_damage"
            and queued.effect_resolver is None)


def _certified(battle, targets, queued, *, settled=False):
    if type(battle) is not core.Battle or not getattr(battle, "_ai_probe_active", False):
        return _reject("battle")
    if (battle.field_effects or getattr(battle, "special_mode_codes", ())
            or type(getattr(battle, "chess_rng", None)) is not ForecastRandom):
        return _reject("field_or_rng")
    if (type(battle.damage_rule) is not core.SummaryDamageRule
            or any(callable(value) for value in vars(battle.damage_rule).values())
            or not _class_methods_original(core.SummaryDamageRule)):
        return _reject("damage_rule")
    for cls in (core.Battle, core.Unit, core.BattleComponent, core.StatusEffect):
        if not _class_methods_original(cls):
            return _reject("changed_engine")
    if any(callable(value) for value in vars(battle).values()):
        return _reject("instance_resolver")
    from wujiang.tactical.rooms import synergy, equipment_effects
    if not _original(synergy.synergy_stat) or not _original(equipment_effects._rng):
        return _reject("changed_synergy")
    source = battle.units.get(queued.actor_id)
    if source is None or not area_query(queued) or queued.payload.get("segment_count", 1) != 1:
        return _reject("action")
    bodies = [*battle.units.values(), *battle.destroyed_units]
    reviewed = {}  # Per query only: runtime monkeypatches are checked again.
    for unit in bodies:
        # Dead bodies are only consulted by optional death choices, which always
        # decline during this probe. Still check their callbacks for expiry.
        if unit in battle.units.values() and not _ordinary_unit(unit):
            return _reject("linked_or_custom_unit")
        if getattr(unit, "_triggered_skill_definitions", None):
            return _reject("dynamic_components")
        components = [*unit.skills, *unit.traits, *unit.statuses]
        for component in components:
            if component.owner is not unit or not _component_contract(component, source, reviewed):
                note("factorization_unsupported_component:" + type(component).__module__ + "." + type(component).__qualname__)
                return _reject("callback")
        if unit.is_stealthed() or any(getattr(status, "remove_when_source_destroyed", False) for status in unit.statuses):
            return _reject("visibility_or_source_lifetime")
    cells = battle.payload_positions(queued.payload, "cells")
    affected = battle.effect_units_at_cells(cells)
    if not settled and any(unit is source for unit in affected) and not queued.payload.get("exclude_actor"):
        return _reject("source_in_area")
    if not all(battle.units.get(unit.unit_id) is unit for unit in targets):
        return _reject("recipient")
    # No summons anywhere means the reviewed dog-death callback cannot destroy
    # another recipient. Other admitted death callbacks only update their owner,
    # rebirth bookkeeping, or enqueue deferred effects; this query does not run
    # those effects. Optional sacrifice/rescue/strike choices decline in probes.
    return True


def _settled_groups(battle, targets, queued):
    """Connected damage dependencies, including deaths of secondary victims.

    Every admitted callback is recipient-local except the reviewed War God
    death sweep. Join its owner to ALL possible sweep victims, even currently
    nonlethal ones. This also retains source healing/stacking, repeated hits,
    and further death sweeps inside one exact joint tree.
    """
    from wujiang.tactical.heroes.mechanics.hell_wargod import (
        HellWarGodRebirthTrait, resolve_hell_war_god_death_sweep)
    if not _original(resolve_hell_war_god_death_sweep):
        _reject("changed_death_resolver")
        return None
    units = list(battle.units.values())
    # A newly created body could be a chain intermediary without having an
    # entry in the caller's before-state utility. Do not omit such bodies.
    if {unit.unit_id for unit in targets} != set(battle.units):
        _reject("settled_recipient_set")
        return None
    parents = {unit.unit_id: unit.unit_id for unit in units}
    def root(key):
        while parents[key] != key:
            key = parents[key]
        return key
    for unit in [*units, *battle.destroyed_units]:
        # Extra queued actions can change absolute resolution-token expiry.
        # Such statuses need the general tree, even if their owner is remote.
        if any(getattr(status, "expires_after_action_token", None) is not None
               or getattr(status, "expires_after_declaration_id", None) for status in unit.statuses):
            _reject("settled_action_expiry")
            return None
    for unit in units:
        if not any(isinstance(component, HellWarGodRebirthTrait)
                   for component in [*unit.skills, *unit.traits, *unit.statuses]):
            continue
        body = set(battle.unit_cells(unit))
        cells = {neighbor for cell in body for neighbor in battle.neighbors(cell) if neighbor not in body}
        for target in battle.effect_units_at_cells(cells):
            if target.player_id != unit.player_id:
                parents[root(target.unit_id)] = root(unit.unit_id)
    groups = {}
    for unit in targets:
        groups.setdefault(root(unit.unit_id), []).append(unit)
    return tuple(groups.values())


class _SettledResolver:
    def __init__(self, battle, queued, group):
        self.battle, self.queued = battle, queued
        self.recipients = frozenset(unit.unit_id for unit in group)

    def __call__(self, ctx):
        from wujiang.tactical.heroes.mechanics.hell_wargod import resolve_hell_war_god_death_sweep
        caller = sys._getframe(1).f_code
        action = self.battle.resolving_action
        if not ctx.is_skill or not (
                (caller is core.Battle.resolve_skill_effect.__code__ and action is self.queued)
                or (caller is resolve_hell_war_god_death_sweep.__code__
                    and action.effect_resolver is resolve_hell_war_god_death_sweep)):
            raise _CoupledEffect()
        if ctx.target.unit_id in self.recipients:
            return core.Battle.resolve_damage(self.battle, ctx)
        return ctx


class _GroupRecipients:
    """Filter only the canonical area loop, before constructing other contexts."""
    def __init__(self, battle, queued, recipients):
        self.battle, self.queued, self.recipients = battle, queued, recipients

    def __call__(self, cells):
        values = core.Battle.effect_units_at_cells(self.battle, cells)
        if (sys._getframe(1).f_code is core.Battle.resolve_skill_effect.__code__
                and self.battle.resolving_action is self.queued):
            return [unit for unit in values if unit.unit_id in self.recipients]
        return values


class _EmptySync:
    def __init__(self, battle):
        self.battle = battle

    def __call__(self):
        # Certification admits only the original empty sync callbacks, static
        # component discovery and ordinary scalar flags. Keep the original
        # post-pass flag even when it was absent before this queue entry.
        if not getattr(self.battle, "_syncing_linked_units", False):
            self.battle._syncing_linked_units = False
        note("empty_linked_sync_passes_shared")


def _local_structure(battle, utility):
    from wujiang.tactical.prediction import _session
    session = _session.get()
    if session is None or not session.get("structural_batch", False):
        return False
    from dataclasses import _HAS_DEFAULT_FACTORY
    from wujiang.tactical.prediction_contracts import load_contracts
    from wujiang.tactical.prediction_inline import code_hash
    contracts = load_contracts()
    constructor = core.DamageContext.__init__
    if (contracts is None or type(core.DamageContext) is not type or type(constructor) is not FunctionType
            or core.DamageContext.__new__ is not object.__new__
            or core.DamageContext.__setattr__ is not object.__setattr__
            or code_hash(constructor.__code__) != contracts.get("runtime_codes", {}).get("DamageContext.__init__")
            or any(cell.cell_contents is not _HAS_DEFAULT_FACTORY and cell.cell_contents is not set
                   for cell in constructor.__closure__ or ())
            or any(value is not _HAS_DEFAULT_FACTORY and type(value) not in (type(None), str, int, float, bool)
                   for value in constructor.__defaults__ or ())):
        return False
    from wujiang.tactical.rooms import ai
    if (type(utility) is not FunctionType or utility.__globals__ is not vars(ai)
            or not _original(ai.r23_action_delta) or not _original(ai._r23_expected_settled_value)
            or not any(value is utility.__code__ and value.co_name == "<lambda>"
                       for value in ai._r23_expected_settled_value.__code__.co_consts)):
        return False
    from wujiang.tactical.prediction_runtime import static_runtime_scan
    return any(saved.battle is battle and saved.complete_observation and saved.matches()
               and static_runtime_scan(saved, session) for saved in session["graph_cache"])


@contextmanager
def _project_settled_group(battle, queued, group, *, local=False):
    # Certification rejects an existing instance resolver before entering.
    battle.resolve_damage = _SettledResolver(battle, queued, group)
    if local:
        battle.effect_units_at_cells = _GroupRecipients(battle, queued, frozenset(unit.unit_id for unit in group))
        battle.sync_linked_units = _EmptySync(battle)
    try:
        yield
    finally:
        vars(battle).pop("resolve_damage", None)
        if local:
            vars(battle).pop("effect_units_at_cells", None)
            vars(battle).pop("sync_linked_units", None)


def expected_settled_area_utilities(battle, targets, queued, utility, *, utility_key):
    """E of final per-unit utility after the whole pending death-effect chain.

    Unlike the single-action area specialization, linked recipients stay in
    the SAME probability tree. Unknown dependencies/resolvers fall back to the
    complete joint query; no branch limit or averaged damage is used.
    """
    if (not forecasting_active() or os.environ.get("WUJIANG_AI_EXPECTATION_FACTORS", "1") == "0"
            or len(battle.pending_followup_actions) != 1
            or battle.pending_followup_actions[0] is not queued or not area_query(queued)):
        return None
    with stable_observations(battle):
        if not _certified(battle, targets, queued, settled=True):
            return None
        groups = _settled_groups(battle, targets, queued)
    if groups is None:
        return None
    local = _local_structure(battle, utility)
    if local:
        affected = {unit.unit_id for unit in battle.effect_units_at_cells(battle.payload_positions(queued.payload, "cells"))
                    if not queued.payload.get("exclude_actor") or unit.unit_id != queued.actor_id}
        active, unchanged = [], []
        for group in groups:
            if any(unit.unit_id in affected for unit in group):
                active.append(group)
            else:
                unchanged.extend(group)
        note("settled_unaffected_groups_shared", len(groups) - len(active))
        # Unaffected utility is evaluated once with the canonical queue's
        # bookkeeping, retaining prior payment/resource changes and order.
        groups = tuple(active + ([unchanged] if unchanged else []))
    from wujiang.tactical.heroes.mechanics.hell_wargod import resolve_hell_war_god_death_sweep
    values = {}
    try:
        for group in groups:
            def local_utilities():
                with _project_settled_group(battle, queued, group, local=local):
                    while battle.pending_followup_actions:
                        action = battle.pending_followup_actions.popleft()
                        if action is not queued and not (
                                type(action) is core.QueuedAction and action.action_type == "skill_effect"
                                and action.effect_resolver is resolve_hell_war_god_death_sweep
                                and action.payload.get("effect_code") == "hell_war_god_death_sweep"
                                and action.payload.get("after_death_effect")):
                            raise _CoupledEffect()
                        battle.resolve_queued_action(action)
                    return tuple(utility(group))
            # These groups have distinct one-use contexts. Do not encode the
            # entire before-state twice per group for a cache with no reuse.
            from wujiang.tactical.prediction import _session
            cached = not _session.get().get("structural_batch", False)
            marginals = outcomes(battle, local_utilities, vector=True, marginal_utilities=True,
                cache_request=("settled_group_final_utilities", utility_key, queued, group) if cached else None,
                eager_cache=cached, readonly_inputs=local)
            for target, marginal in zip(group, marginals):
                values[target.unit_id] = math.fsum(value * probability for value, probability in marginal)
    except (_CoupledEffect, core.ActionError, KeyError, TypeError, ValueError):
        note("settled_factorization_runtime_fallbacks")
        return None
    note("settled_factorized_queries")
    note("settled_factorized_groups", len(groups))
    note("settled_factorized_utilities", len(targets))
    for group in groups:
        note("settled_factorized_groups_size:" + str(len(group)))
    return tuple(values[target.unit_id] for target in targets)


class _RecipientResolver:
    # A module-level callable remains serializable by the existing complete
    # observation key; a local closure installed on Battle would disable it.
    def __init__(self, battle, target):
        self.battle, self.target = battle, target

    def __call__(self, ctx):
        if sys._getframe(1).f_code is not core.Battle.resolve_skill_effect.__code__ or not ctx.is_skill:
            raise _CoupledEffect()
        if ctx.target is self.target:
            return core.Battle.resolve_damage(self.battle, ctx)
        return ctx  # Canonical area loop discards the returned context.


@contextmanager
def _project_recipient(battle, target):
    missing = object()
    previous = vars(battle).get("resolve_damage", missing)
    battle.resolve_damage = _RecipientResolver(battle, target)
    try:
        yield
    finally:
        if previous is missing:
            vars(battle).pop("resolve_damage", None)
        else:
            battle.resolve_damage = previous


def expected_area_utilities(battle, targets, queued, utility, *, utility_key=None):
    """Return E[final utility_i] separately, or None for a coupled action.

    The caller's full-state vector cache encloses this operation. Correlation
    *within* each target (dodge, shield, survival, death and status expiry) is
    retained by the ordinary exact enumerator. E[sum U_i] = sum E[U_i] needs no
    Cartesian product of unrelated recipients' outcomes.
    """
    if (not forecasting_active() or os.environ.get("WUJIANG_AI_EXPECTATION_FACTORS", "1") == "0"
            or not area_query(queued)):
        return None
    try:
        with stable_observations(battle):
            certified = _certified(battle, targets, queued)
    except UnstableObservation:
        # Abort this query so its enclosing rollback runs before scalar
        # fallback; never continue from a state changed during inspection.
        note("factorization_unsafe_inspection")
        raise
    if not certified:
        return None
    values = []
    try:
        for target in targets:
            def local_utility():
                with _project_recipient(battle, target):
                    return utility([target])[0]
            distribution = outcomes(battle, local_utility,
                cache_request=("recipient_final_utility", utility_key, queued, target) if utility_key is not None else None,
                eager_cache=utility_key is not None)
            values.append(math.fsum(value * probability for value, probability in distribution))
    except _CoupledEffect:
        note("factorization_runtime_fallbacks")
        return None
    note("factorized_utility_queries")
    note("factorized_recipient_utilities", len(targets))
    return tuple(values)
