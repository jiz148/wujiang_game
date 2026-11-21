"""Prove readonly query hooks without caching their values or live containers.

Rollback may recreate a component list. That changes a value-cache key, but it
does not make an unchanged, scalar-only modifier impure. Check current objects
against reusable type/code plans instead of rebuilding whole-battle StatViews.
"""
from abc import ABCMeta
from types import FunctionType, GetSetDescriptorType
import weakref

from wujiang.tactical.direct_queries import _raw, _MISSING
from wujiang.tactical.prediction import _session

_QUERY_HOOKS = ('modify_normal_move_distance', 'modify_targeting_range', 'modify_attack_actions_per_turn',
    'modify_normal_move_actions_per_turn', 'basic_attack_payload_metadata', 'basic_attack_preview',
    'basic_attack_area_cells', 'basic_attack_origins', 'basic_attack_area_affects_allies',
    'can_attack_target', 'can_attack_target_with_payload', 'can_use_basic_attack',
    'ignores_direct_unit_target_line', 'reveals_stealth_to', 'grants_magic_immunity_to',
    'grants_physical_immunity_to', 'blocks_direct_effects', 'blocks_basic_attack_non_damage_effects',
    'allied_basic_heal_amount', 'allows_block_counter', 'grants_block_counter', 'grants_counter')


def readonly_query_methods(*names):
    """Object-owned declarations for reviewed readonly position-query hooks."""
    def declare(kind):
        from wujiang.tactical.component_providers import EmptyComponentBranch
        proofs = dict(getattr(kind, '_readonly_query_methods', {}))
        for name in names:
            function = getattr(kind, name)
            if name not in _QUERY_HOOKS or type(function) is not FunctionType or function.__closure__:
                raise TypeError('A reviewed position-query method is required')
            proofs[name] = EmptyComponentBranch(name, function)
        kind._readonly_query_methods = proofs
        return kind
    return declare


class UnsupportedQuery(Exception):
    pass


class ReadonlyQueries:
    def __init__(self, kernel, core):
        self.kernel, self.core = kernel, core
        self.plans = {}
        self.current = {}

    def plan(self, kind, unit=False):
        key = kind, unit
        if key in self.current:
            return self.current[key]
        saved = self.plans.get(key)
        if (saved is not None and self.kernel.type_versions_match((kind,), saved[0])
                and self.kernel.function_codes_match(saved[1])
                and all(proof.matches(function) for proof, function in saved[2].get('proofs', ()))):
            self.current[key] = saved[2]
            return saved[2]
        core = self.core
        if (type(kind) not in (type, ABCMeta) or type(kind).__getattribute__ is not type.__getattribute__
                or kind.__getattribute__ is not object.__getattribute__
                or _raw(kind, '__getattr__') is not _MISSING
                or type(_raw(kind, '__dict__')) is not GetSetDescriptorType):
            raise UnsupportedQuery()
        codes, result = {}, {'proofs': []}
        def fields(names):
            for name in names:
                if hasattr(_raw(kind, name), '__get__') and type(_raw(kind, name)) is not FunctionType:
                    raise UnsupportedQuery()
        if unit:
            from wujiang.tactical.stat_queries import _LEDGER_FIELDS
            if any(isinstance(value, property) and _raw(kind, name) is not getattr(core.Unit, name, None)
                   for owner in kind.__mro__ for name, value in vars(owner).items()):
                raise UnsupportedQuery()
            names = ('stat', '_stat_without_query_cache', 'iter_components', 'projected_traits',
                     'innate_traits_suppressed', 'is_bp_benched', 'total_shields',
                     'normal_move_distance', 'targeting_range', 'attack_actions_per_turn')
            for name in names:
                function = _raw(kind, name)
                if function is not getattr(core.Unit, name):
                    raise UnsupportedQuery()
                codes[function] = function.__code__
            fields((*names, 'base_stats', 'skills', 'traits', 'statuses', '_triggered_skill_definitions',
                    'stat_minimums', 'is_clone', 'is_summon', 'banished', 'alive', '_battle_ref', 'position', *_LEDGER_FIELDS))
        else:
            for owner in kind.__mro__:
                for name, value in vars(owner).items():
                    if isinstance(value, property):
                        if value is not _raw(core.Skill, name) or _raw(kind, name) is not value:
                            raise UnsupportedQuery()
                        codes[value.fget] = value.fget.__code__
            names = ('provided_components', 'modify_stat', 'modify_received_stat_change', 'projected_trait_for')
            fields((*names, 'owner', 'suppresses_innate_traits', 'suppresses_projected_traits'))
            for name in names:
                function = _raw(kind, name)
                if type(function) is not FunctionType:
                    raise UnsupportedQuery()
                codes[function] = function.__code__
                if name in ('modify_received_stat_change', 'projected_trait_for') and function is not getattr(core.BattleComponent, name):
                    raise UnsupportedQuery()
            function = _raw(kind, 'modify_stat')
            if function is not core.BattleComponent.modify_stat:
                from wujiang.tactical.stat_queries import StatDependencies
                proof = vars(function).get('_stat_dependencies') or vars(function).get('_readonly_stat_dependencies')
                if type(proof) is not StatDependencies or not proof.matches(function):
                    raise UnsupportedQuery()
                fields(proof.fields)
                result['stat'] = proof
            function = _raw(kind, 'provided_components')
            if function is not core.BattleComponent.provided_components:
                from wujiang.tactical.component_providers import EmptyComponentBranch
                fixed = vars(function).get('_readonly_component_fields')
                empty = vars(function).get('_empty_component_branch')
                if type(fixed) is tuple and fixed and all(type(p) is EmptyComponentBranch and p.matches(function) for p in fixed):
                    fields(p.field for p in fixed)
                    result['children'] = tuple(p.field for p in fixed)
                    result['optional_children'] = vars(function).get('_readonly_optional_children') is True
                    result['proofs'].extend((proof, function) for proof in fixed)
                elif type(empty) is EmptyComponentBranch and empty.matches(function):
                    fields((empty.field,))
                    result['empty'] = empty.field
                    result['proofs'].append((empty, function))
                else:
                    raise UnsupportedQuery()
            from wujiang.tactical.component_providers import EmptyComponentBranch
            declarations = getattr(kind, '_readonly_query_methods', {})
            for name in _QUERY_HOOKS:
                function = _raw(kind, name)
                if type(function) is not FunctionType:
                    raise UnsupportedQuery()
                codes[function] = function.__code__
                if function is getattr(core.BattleComponent, name):
                    continue
                proof = declarations.get(name) if type(declarations) is dict else None
                if type(function) is not FunctionType or type(proof) is not EmptyComponentBranch or not proof.matches(function):
                    raise UnsupportedQuery()
                codes[function] = function.__code__
                result['proofs'].append((proof, function))
        getattr(kind, 'stat' if unit else 'modify_stat')
        versions = self.kernel.type_versions((kind,))
        if versions is None:
            raise UnsupportedQuery()
        if len(self.plans) < 256:
            self.plans[key] = versions, tuple(codes.items()), result
        self.current[key] = result
        return result

    def component(self, part, unit):
        core = self.core
        if not isinstance(part, core.BattleComponent):
            raise UnsupportedQuery()
        plan = self.plan(type(part))
        data = vars(part)
        if (any(name in data for name in ('provided_components', 'modify_stat', 'modify_received_stat_change', 'projected_trait_for'))
                or any(name in data for name in _QUERY_HOOKS)
                or data.get('owner') is not unit):
            raise UnsupportedQuery()
        for name in ('suppresses_innate_traits', 'suppresses_projected_traits'):
            if type(getattr(part, name, False)) not in (bool, int, float, str, type(None)):
                raise UnsupportedQuery()
        proof = plan.get('stat')
        if proof is not None:
            for owner, names in ((part, proof.fields), (unit, proof.owner_fields)):
                for name in names:
                    if hasattr(_raw(type(owner), name), '__get__'):
                        raise UnsupportedQuery()
                    value = getattr(owner, name)
                    if type(value) not in (bool, int, float, str, type(None)) and not (name == 'owner' and value is unit):
                        raise UnsupportedQuery()
        if 'empty' in plan and data.get(plan['empty'], _MISSING) is not None:
            raise UnsupportedQuery()
        from wujiang.tactical.settlement_reuse import _plain_data
        children = plan.get('children', ())
        if any(not _plain_data(value, core) for name, value in data.items() if name != 'owner' and name not in children):
            raise UnsupportedQuery()
        values = tuple(data.get(name, _MISSING) for name in children)
        if not plan.get('optional_children') and any(value is None for value in values):
            raise UnsupportedQuery()
        return values

    def check(self, units):
        core = self.core
        from wujiang.tactical.stat_queries import _STATS, _plain_ledger_fields, original_stat_bindings
        self.current = {}
        if len(units) > 128 or not original_stat_bindings(core):
            return False
        self.plan(type(units[0]), unit=True)
        reference = vars(units[0]).get('_battle_ref')
        if type(reference) is not weakref.ReferenceType:
            return False
        battle = reference()
        if (type(battle) is not core.Battle or 'all_units' in vars(battle)
                or getattr(battle, 'special_mode_codes', ())):
            return False
        for effect in battle.field_effects:
            if type(effect).__getattribute__ is not object.__getattribute__ or _raw(type(effect), '__getattr__') is not _MISSING:
                return False
            if ('modify_unit_stat' in vars(effect) or _raw(type(effect), 'modify_unit_stat') is not core.BattleFieldEffect.modify_unit_stat):
                return False
            if any(name in vars(effect) or _raw(type(effect), name) is not getattr(core.BattleFieldEffect, name)
                   for name in ('basic_attack_payload_metadata', 'adjust_unit_targeting_range')):
                return False
        for unit in units:
            self.plan(type(unit), unit=True)
            data = vars(unit)
            reference = data.get('_battle_ref')
            if type(reference) is not weakref.ReferenceType or reference() is not battle:
                return False
            if any(type(getattr(unit, name)) not in (bool, int, float) for name in ('is_clone', 'is_summon', 'banished', 'alive')):
                return False
            if type(unit.position) not in (core.Position, type(None)):
                return False
            if (any(name in data for name in ('stat', '_stat_without_query_cache', 'iter_components', 'projected_traits',
                    'innate_traits_suppressed', 'is_bp_benched', 'total_shields'))
                    or unit.is_clone or unit.is_summon or type(unit.base_stats) is not core.Stats
                    or any(type(getattr(unit.base_stats, name)) not in (bool, int, float) for name in _STATS)
                    or type(unit.stat_minimums) is not dict
                    or any(type(k) is not str or type(v) not in (bool, int, float) for k,v in unit.stat_minimums.items())):
                return False
            groups = [unit.skills, unit.traits, unit.statuses]
            if any(type(group) is not list for group in groups):
                return False
            definitions = data.get('_triggered_skill_definitions', {})
            if type(definitions) is not dict:
                return False
            parts = [*unit.skills, *unit.traits, *unit.statuses, *definitions.values()]
            for part in parts:
                children = self.component(part, unit)
                for child in children:
                    if child is not None:
                        # iter_components yields each direct child without
                        # recursively invoking its own provider.
                        self.component(child, unit)
        return _plain_ledger_fields(units)


def position_queries_readonly(units):
    session = _session.get()
    state = session.get('stat_views') if session is not None else None
    if not state or not units or state.get('readonly_queries_disabled'):
        return False
    proof = state.get('readonly_queries')
    if proof is None:
        from wujiang.tactical.engine import core
        proof = state['readonly_queries'] = ReadonlyQueries(state['kernel'], core)
    try:
        valid = proof.check(units)
    except (UnsupportedQuery, AttributeError, TypeError):
        valid = False
    if not valid:
        # Eligibility may later improve, but the original scorer remains
        # correct. Avoid paying for a failed proof on every remaining payload.
        state['readonly_queries_disabled'] = True
    return valid
