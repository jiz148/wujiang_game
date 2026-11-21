"""Decision-local stat values with explicit object-owned read dependencies.

Unknown modifiers retain Unit's original evaluator. Every cache hit checks
current fields, component collections, class versions and method code.
"""
from abc import ABCMeta
from collections import deque
import os
from types import FunctionType, GetSetDescriptorType, MemberDescriptorType, MappingProxyType

from wujiang.tactical.prediction import _session
from wujiang.tactical.direct_queries import _raw, _MISSING, component_snapshot

_ENABLED = os.environ.get('WUJIANG_AI_STAT_VIEWS', '1') != '0'
_SCALARS = (str, int, float, bool, type(None))
_STATS = ('attack', 'defense', 'speed', 'attack_range', 'mana')
_LEDGER_FIELDS = ('current_hp', 'current_mana', 'shields', 'temporary_shields',
                  'dodge_charges', 'alive', 'player_id', 'level',
                  'is_siege_structure', '_ai_expected_banish_probability')


class StatDependencies:
    """A reviewed modifier reads only these scalar fields and owner fields."""
    def __init__(self, function, fields, owner_fields):
        self.function, self.code = function, function.__code__
        self.fields, self.owner_fields = tuple(fields), tuple(owner_fields)

    def matches(self, function):
        return function is self.function and function.__code__ is self.code


def stat_dependencies(*fields, owner_fields=(), query_only=False):
    def declare(function):
        if (type(function) is not FunctionType or function.__closure__ or function.__defaults__
                or function.__kwdefaults__ or function.__code__.co_argcount != 3
                or function.__code__.co_kwonlyargcount or function.__code__.co_flags & (4 | 8 | 32 | 128 | 512)):
            raise TypeError('A plain scalar stat modifier is required')
        if any(type(n) is not str or not n.isidentifier() for n in (*fields, *owner_fields)):
            raise TypeError('Plain dependency fields are required')
        setattr(function, '_readonly_stat_dependencies' if query_only else '_stat_dependencies',
                StatDependencies(function, fields, owner_fields))
        return function
    return declare


class UnsupportedStat(Exception):
    pass


class StatView:
    def __init__(self, unit, battle, kernel, core):
        if not original_stat_bindings(core):
            raise UnsupportedStat()
        self.unit, self.battle, self.kernel = unit, battle, kernel
        self.fields, self.slots, self.containers = {}, [], []
        self.kinds, self.methods = {}, dict(core._DIRECT_STAT_METHODS)
        self.validated_names = {}
        self.values = {}
        self.add_fields(unit, ('_battle_ref', 'base_stats', 'banished', 'alive', 'is_summon', 'is_clone',
                              'skills', 'traits', 'statuses', '_triggered_skill_definitions', 'stat_minimums',
                              'iter_components', 'projected_traits', 'innate_traits_suppressed', 'is_bp_benched',
                              '_stat_without_query_cache', 'stat', 'unit_id'))
        self.add_type(type(unit), (*_LEDGER_FIELDS, 'total_shields'))
        self.add_fields(unit, ('total_shields',))
        if ('total_shields' in vars(unit) or _raw(type(unit), 'total_shields') is not core.Unit.total_shields
                or type(unit.unit_id) is not str):
            raise UnsupportedStat()
        if unit.banished or _raw(type(unit), 'stat') is not core.Unit.stat:
            raise UnsupportedStat()
        if (_raw(type(unit), '_stat_without_query_cache') is not core.Unit._stat_without_query_cache
                or type(unit.base_stats) is not core.Stats or type(battle) is not core.Battle):
            raise UnsupportedStat()
        self.add_fields(battle, ('units', 'field_effects', 'special_mode_codes', 'all_units'))
        for group in (unit.skills, unit.traits, unit.statuses, unit.stat_minimums):
            self.add_container(group)
        if any(type(key) is not str or type(value) not in (int, float, bool) for key, value in unit.stat_minimums.items()):
            raise UnsupportedStat()
        definitions = vars(unit).get('_triggered_skill_definitions')
        if definitions is not None:
            self.add_container(definitions)
        modes = vars(battle).get('special_mode_codes')
        if modes is not None:
            self.add_container(modes)
        self.add_container(battle.field_effects)
        self.add_type(core.Stats, _STATS, slots=True)
        values = tuple((name, getattr(unit.base_stats, name)) for name in _STATS)
        if any(type(value) not in (float, int, bool) for _, value in values):
            raise UnsupportedStat()
        self.slots.append((unit.base_stats, values))
        for effect in battle.field_effects:
            self.add_fields(effect, ('modify_unit_stat',))
            method = _raw(type(effect), 'modify_unit_stat')
            if method is not core.BattleFieldEffect.modify_unit_stat or 'modify_unit_stat' in vars(effect):
                raise UnsupportedStat()
            self.methods.setdefault(method, method.__code__)
        parts = component_snapshot(battle, unit)
        if parts is None:
            raise UnsupportedStat()
        self.components = tuple(parts)
        for part in parts:
            self.add_fields(part, ('provided_components', 'projected_trait_for', 'modify_stat',
                                   'modify_received_stat_change', 'suppresses_innate_traits', 'owner'))
            if any(name in vars(part) for name in ('provided_components', 'projected_trait_for', 'modify_stat', 'modify_received_stat_change')):
                raise UnsupportedStat()
            for name in ('provided_components', 'projected_trait_for', 'modify_received_stat_change'):
                method = _raw(type(part), name)
                if type(method) is not FunctionType:
                    raise UnsupportedStat()
                self.methods.setdefault(method, method.__code__)
            provider = _raw(type(part), 'provided_components')
            if provider is not core.BattleComponent.provided_components:
                proof = vars(provider).get('_empty_component_branch')
                if proof is None or not proof.matches(provider) or vars(part).get(proof.field, _MISSING) is not None:
                    raise UnsupportedStat()
                self.add_fields(part, (proof.field,))
            method = _raw(type(part), 'modify_stat')
            self.methods.setdefault(method, method.__code__)
            if method is not core.BattleComponent.modify_stat:
                proof = vars(method).get('_stat_dependencies') if type(method) is FunctionType else None
                if type(proof) is not StatDependencies or not proof.matches(method):
                    raise UnsupportedStat()
                self.scalar_fields(part, proof.fields)
                if proof.owner_fields and part.owner is not None:
                    self.scalar_fields(part.owner, proof.owner_fields)
        for trait in unit.traits:
            if _raw(type(trait), 'modify_received_stat_change') is not core.BattleComponent.modify_received_stat_change:
                raise UnsupportedStat()
        # Without projection the dependency is structural, independent of HP,
        # mana and positions. Actual projecting traits use the original query.
        if unit.is_clone or unit.is_summon:
            self.add_fields(unit, ('position',))
            self.add_container(battle.units)
            for source in battle.units.values():
                self.add_fields(source, ('traits',))
                self.add_container(source.traits)
                for trait in source.traits:
                    self.add_fields(trait, ('projected_trait_for',))
                    if ('projected_trait_for' in vars(trait)
                            or _raw(type(trait), 'projected_trait_for') is not core.BattleComponent.projected_trait_for):
                        raise UnsupportedStat()
        self.types = tuple(self.kinds)
        self.versions = kernel.type_versions(self.types)
        self.codes = tuple(self.methods.items())
        if self.versions is None:
            raise UnsupportedStat()
        self.field_rows = [(owner, data, tuple(fields.items())) for owner, data, fields in self.fields.values()]
        self.guard = kernel.make_field_guard(self.field_rows)
        self.bytes = 512 + sum(128 + len(fields)*64 for _, _, fields in self.field_rows)
        self.bytes += sum(128 + len(fields)*64 for _, fields in self.slots)
        self.bytes += sum(128 + len(values)*64 for _, values, _ in self.containers)

    def add_type(self, kind, names, slots=False):
        key = kind,slots
        checked = self.validated_names.get(key)
        if checked is None:
            if type(kind) not in (type, ABCMeta) or kind.__getattribute__ is not object.__getattribute__ or _raw(kind, '__getattr__') is not _MISSING:
                raise UnsupportedStat()
            if not slots and type(_raw(kind, '__dict__')) is not GetSetDescriptorType:
                raise UnsupportedStat()
            checked = self.validated_names[key] = set()
        for name in names:
            if name in checked:
                continue
            descriptor = _raw(kind, name)
            if hasattr(descriptor, '__get__') and not (type(descriptor) is FunctionType or slots and type(descriptor) is MemberDescriptorType):
                raise UnsupportedStat()
            checked.add(name)
        self.kinds[kind] = True

    def add_fields(self, owner, names):
        self.add_type(type(owner), names)
        data = vars(owner)
        if type(data) is not dict:
            raise UnsupportedStat()
        record = self.fields.setdefault(id(owner), (owner, data, {}))
        record[2].update((name, data.get(name, _MISSING)) for name in names)

    def scalar_fields(self, owner, names):
        self.add_type(type(owner), names)
        values = []
        for name in names:
            value = getattr(owner, name)
            if type(value) not in _SCALARS and not (name == 'owner' and value is self.unit):
                raise UnsupportedStat()
            values.append((name, value))
        self.add_fields(owner, names)

    def add_container(self, value):
        if type(value) in (tuple, frozenset):
            return
        if type(value) not in (dict, list, set):
            raise UnsupportedStat()
        from wujiang.tactical.prediction import _GraphSnapshot
        saved = tuple(value.items()) if type(value) is dict else tuple(value)
        self.containers.append((value, saved, _GraphSnapshot.identities(value)))

    def matches(self):
        k = self.kernel
        return (k.type_versions_match(self.types, self.versions) and k.function_codes_match(self.codes)
                and k.graph_matches([], self.slots, self.containers, deque)
                and k.field_guard_matches(self.guard, _MISSING))


def cached_stat_value(unit, name):
    session = _session.get()
    if not _ENABLED or session is None or type(name) is not str or name not in _STATS:
        return None
    from wujiang.tactical.engine import core
    state = session.get('stat_views')
    if state is None:
        from wujiang.platform.ai_prediction_native import load_kernel
        kernel = load_kernel()
        if kernel is None or not hasattr(kernel, 'make_field_guard'):
            session['stat_views'] = False
            return None
        state = session['stat_views'] = {'kernel': kernel, 'views': {}, 'bytes': 0, 'unsupported': {}}
    if state is False:
        return None
    key = id(unit)
    if state['unsupported'].get(key) is unit:
        return None
    entries = state['views'].get(key, ())
    for view in entries:
        if view.unit is unit and view.matches():
            if name not in view.values:
                view.values[name] = unit._stat_without_query_cache(name)
            session['statistics']['stat_view_hits'] = session['statistics'].get('stat_view_hits', 0) + 1
            return view.values[name]
    reference = vars(unit).get('_battle_ref')
    battle = reference() if reference is not None else None
    if battle is None:
        return None
    try:
        view = StatView(unit, battle, state['kernel'], core)
        if not view.matches():
            raise UnsupportedStat()
    except (UnsupportedStat, AttributeError):
        if len(state['unsupported']) < 256:
            state['unsupported'][key] = unit
        session['statistics']['stat_view_fallbacks'] = session['statistics'].get('stat_view_fallbacks', 0) + 1
        return None
    value = unit._stat_without_query_cache(name)
    if view.matches() and view.bytes <= 1024*1024:
        view.values[name] = value
        if len(state['views']) < 256 and state['bytes'] + view.bytes <= 8*1024*1024:
            old = list(entries)
            if len(old) >= 3:
                state['bytes'] -= old.pop().bytes
            state['views'][key] = [view, *old]
            state['bytes'] += view.bytes
        session['statistics']['stat_view_captures'] = session['statistics'].get('stat_view_captures', 0) + 1
    return value


def original_stat_bindings(core):
    return all(getattr(owner, name) is function and function.__code__ is code
               for owner, name, function, code in core._DIRECT_STAT_BINDINGS)


def cached_components(unit):
    """Fresh eager snapshots only; never replace the live lazy iterator."""
    session = _session.get()
    state = session.get('stat_views') if session is not None else None
    if not state:
        return None
    for view in state['views'].get(id(unit), ()):
        if view.unit is unit and view.matches():
            return list(view.components)
    return None


class StatBatch:
    def __init__(self, units, names, kernel, core):
        self.units, self.names, self.kernel = tuple(units), names, kernel
        session = _session.get()
        state = session.get('stat_views') if session is not None else None
        self.views = []
        for unit in units:
            view = next((v for v in state['views'].get(id(unit), ()) if v.unit is unit and v.matches()), None) if state else None
            self.views.append(view if view is not None else StatView(unit, unit._battle_ref(), kernel, core))
        kinds, codes, fields, slots, containers = {}, {}, {}, {}, {}
        for view in self.views:
            kinds.update(view.kinds)
            codes.update(view.methods)
            for owner, data, selected in view.field_rows:
                row = fields.setdefault(id(owner), (owner, data, {}))
                row[2].update(selected)
            for owner, selected in view.slots:
                row = slots.setdefault(id(owner), (owner, {}))
                row[1].update(selected)
            for row in view.containers:
                containers[id(row[0])] = row
        self.types = tuple(kinds)
        self.versions = kernel.type_versions(self.types)
        self.codes = tuple(codes.items())
        self.slots = [(owner, tuple(selected.items())) for owner, selected in slots.values()]
        self.containers = list(containers.values())
        self.guard = kernel.make_field_guard([(owner, data, tuple(selected.items())) for owner, data, selected in fields.values()])
        self.bytes = sum(view.bytes for view in self.views)
        self.values = None
        if self.versions is None:
            raise UnsupportedStat()

    def matches(self):
        k = self.kernel
        return (k.type_versions_match(self.types, self.versions) and k.function_codes_match(self.codes)
                and k.graph_matches([], self.slots, self.containers, deque)
                and k.field_guard_matches(self.guard, _MISSING))


def stat_values_for(units, names=('attack', 'defense', 'speed')):
    """Return one certified immutable stat batch, or None for original reads."""
    session = _session.get()
    if not _ENABLED or session is None or not units:
        return None
    state = session.get('stat_views')
    if not state:
        return None
    key = tuple(id(unit) for unit in units), names
    cache = state.setdefault('batches', {})
    entries = cache.get(key, ())
    for batch in entries:
        if batch.matches():
            if not _plain_ledger_fields(units):
                return None
            session['statistics']['stat_batch_hits'] = session['statistics'].get('stat_batch_hits', 0) + 1
            return batch.values
    from wujiang.tactical.engine import core
    try:
        batch = StatBatch(units, names, state['kernel'], core)
        if not batch.matches():
            raise UnsupportedStat()
        if not _plain_ledger_fields(units):
            raise UnsupportedStat()
    except (UnsupportedStat, AttributeError):
        return None
    batch.values = MappingProxyType({id(view.unit): MappingProxyType({name: (view.values[name] if name in view.values else view.unit._stat_without_query_cache(name))
        for name in names}) for view in batch.views})
    retained = [batch, *entries[:1]]
    change = sum(item.bytes for item in retained) - sum(item.bytes for item in entries)
    used = state.get('batch_bytes', 0)
    if (batch.matches() and batch.bytes <= 1024*1024 and len(cache) < 32
            and used + change <= 8*1024*1024):
        cache[key] = retained
        state['batch_bytes'] = used + change
    return batch.values


def _plain_ledger_fields(units):
    # Stat batches move pure reads ahead of the ledger's resource arithmetic.
    # Unknown descriptors, shield methods and numeric operator overloads must
    # keep that original interleaving instead of reusing the batch.
    return all(type(getattr(unit, name, 0)) in (int, float, bool) for unit in units for name in _LEDGER_FIELDS)
