"""Decision-local reuse of certified, independent nonlethal area outcomes.

The first value for a recipient still runs the original damage engine and
original unit utility. Unknown callbacks, lethal outcomes and coupled effects
use the complete original skill probe. No action or roster solution is stored.
"""
from collections import deque
from abc import ABCMeta
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import os
import weakref
import sys
from pathlib import Path
from types import CodeType, FunctionType, GetSetDescriptorType, ModuleType

from wujiang.tactical.prediction import _session, direct_scoring, note

_SKILLS, _COMPONENTS, _FIELDS = {}, set(), set()
_PROJECTION = ContextVar('independent_area_recipient', default=None)
_MISSING = object()
CONTRACT_DIGEST = 'c646cbb0419c3bb945375929085ffcaf79520be952607a2f9439d1c5d3c9c07e'


@dataclass(frozen=True, slots=True)
class AreaDamageSpec:
    cells: tuple
    parameters: dict


def independent_area_skill(kind):
    def register(cls):
        _SKILLS[cls] = kind
        return cls
    return register


def independent_area_component(cls):
    _COMPONENTS.add(cls)
    return cls


def independent_area_field(cls):
    _FIELDS.add(cls)
    return cls


class CoupledAreaProbe(BaseException):
    pass


def guard_damage(battle, ctx):
    projection = _PROJECTION.get()
    if projection is None:
        return
    expected_battle, actor, target, hits, count = projection
    if (battle is not expected_battle or ctx.source is not actor or ctx.target is not target
            or not ctx.is_skill or ctx.from_field_effect or ctx.area_cell_hits != hits or count[0]):
        raise CoupledAreaProbe()
    count[0] += 1


def _functions(cls):
    for owner in cls.__mro__:
        if not owner.__module__.startswith('wujiang.tactical.'):
            continue
        for value in vars(owner).values():
            if type(value) is FunctionType:
                yield value
            elif type(value) in (staticmethod, classmethod):
                yield value.__func__
            elif type(value) is property:
                yield from (f for f in (value.fget, value.fset, value.fdel) if f is not None)


def contract_classes():
    from wujiang.tactical.engine import core
    from wujiang.tactical import stat_queries
    from wujiang.tactical.rooms.ai_late_scoring import AILateTacticalScoring
    from wujiang.tactical.rooms.ai_mobility_effect_scoring import AIMobilityEffectScoring
    from wujiang.tactical.rooms.ai_weather_scoring import AIWeatherAndThreatScoring
    from wujiang.tactical.rooms.ai_attack_scoring import AIAttackScoring
    from wujiang.tactical.rooms.ai_skill_candidates import _AreaRecipientIndex
    from wujiang.tactical.rooms.ai_damage_probes import DamageImpact,DamageProbeService
    from wujiang.tactical.probe_reuse import _COMPONENTS as probe_components
    from wujiang.tactical.direct_checkpoint import DirectSnapshot
    return (core.Battle,core.Unit,core.Skill,core.BattleComponent,core.StatusEffect,core.BattleFieldEffect,
        core.SummaryDamageRule,core.DamageContext,core.QueuedAction,core.Position,stat_queries.StatView,stat_queries.StatBatch,
        AILateTacticalScoring,AIMobilityEffectScoring,AIWeatherAndThreatScoring,AIAttackScoring,
        AreaDamageSpec,_AreaRecipientIndex,DamageImpact,DamageProbeService,DirectSnapshot,*_SKILLS,*_COMPONENTS,*_FIELDS,*probe_components)


def contract_functions():
    """Explicit reviewed owners; the builder does not approve other heroes."""
    from wujiang.tactical.engine import core
    from wujiang.tactical import direct_expectation, stat_queries
    from wujiang.tactical.rooms import ai
    from wujiang.tactical.rooms.ai_late_scoring import AILateTacticalScoring
    from wujiang.tactical.rooms.ai_mobility_effect_scoring import AIMobilityEffectScoring
    from wujiang.tactical.rooms.ai_weather_scoring import AIWeatherAndThreatScoring
    from wujiang.tactical.rooms.ai_attack_scoring import AIAttackScoring
    from wujiang.tactical.heroes.mechanics.area_helpers import resolve_area_damage
    from wujiang.tactical.heroes.mechanics.jade import maybe_queue_jade_reactive_bonus
    from wujiang.tactical.rooms.ai_skill_candidates import _AreaRecipientIndex
    from wujiang.tactical.rooms.ai_damage_probes import DamageImpact,DamageProbeService
    from wujiang.tactical.probe_reuse import _COMPONENTS as probe_components
    from wujiang.tactical.direct_checkpoint import DirectSnapshot,direct_checkpoint
    from wujiang.tactical.query_purity import ReadonlyQueries,position_queries_readonly
    from wujiang.tactical.rooms.ai_mobility_effect_scoring import _PositionBaselineRequest
    from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
    roots = [guard_damage,resolve_area_damage, maybe_queue_jade_reactive_bonus,direct_checkpoint,ai_probe_rollback,position_queries_readonly,
        AILateTacticalScoring.r23_action_state, AILateTacticalScoring.r23_action_delta,
        AILateTacticalScoring.r23_paid_skill_value, AIMobilityEffectScoring.reviewed_r18_effect_score,
        AIMobilityEffectScoring._r18_damage_unit_value, AIWeatherAndThreatScoring.hostile_unit_value,
        AIMobilityEffectScoring._r18_effect_probe,AIMobilityEffectScoring.reviewed_r17_position_value,
        AIMobilityEffectScoring.reviewed_effect_baseline,ai.reviewed_r17_position_value,
        ai.reviewed_effect_baseline,ai._lazy_position_query_bindings_match,
        AIAttackScoring.scored_death_probability, AIWeatherAndThreatScoring.offensive_reach_score_at,
        AIWeatherAndThreatScoring._offensive_reach_original, ai.offensive_reach_score_at,
        ai.r23_action_state, ai.r23_action_delta,
        ai.scored_death_probability, ai.hostile_unit_value]
    # Pin the conservative call closure of the actual probe entries. New engine
    # methods outside that closure must not turn off unrelated, reviewed probes.
    # A new helper used by a changed caller still needs a reviewed fingerprint.
    battle_methods = {name:value for owner in reversed(core.Battle.__mro__)
        for name,value in vars(owner).items() if type(value) is FunctionType and not name.startswith('__')}
    roots.extend(battle_methods[name] for name in ('build_queued_action','resolve_queued_action',
        'prepay_skill_resources','resolve_damage'))
    for cls in (core.Unit, core.Skill, core.BattleComponent, core.StatusEffect,
                core.BattleFieldEffect, core.SummaryDamageRule, core.DamageContext, core.QueuedAction, core.Position,
                stat_queries.StatView, stat_queries.StatBatch, AreaDamageSpec, _AreaRecipientIndex,DamageImpact,DamageProbeService,DirectSnapshot,ReadonlyQueries,_PositionBaselineRequest,
                *_SKILLS, *_COMPONENTS, *_FIELDS,*probe_components):
        roots.extend(_functions(cls))
    roots.extend(f for f in vars(direct_expectation).values() if type(f) is FunctionType)
    functions, pending = set(), list(roots)
    while pending:
        function = pending.pop()
        if type(function) is not FunctionType or function in functions:
            continue
        functions.add(function)
        references = set()
        def inspect_code(code):
            references.update(code.co_names)
            def literal(value):
                if type(value) is str:
                    references.add(value)
                elif type(value) is CodeType:
                    inspect_code(value)
                elif type(value) in (tuple,frozenset):
                    for item in value:
                        literal(item)
            for value in code.co_consts:
                literal(value)
        inspect_code(function.__code__)
        pending.extend(battle_methods[name] for name in references if name in battle_methods)
        inner = getattr(function, '__wrapped__', None)
        if type(inner) is FunctionType:
            pending.append(inner)
        pending.extend(value for name in function.__code__.co_names
            if type(value := function.__globals__.get(name)) is FunctionType
            and value.__module__.startswith('wujiang.tactical.'))
    return tuple(functions)


@lru_cache(maxsize=1)
def _contracts():
    try:
        raw = Path(__file__).with_name('settlement_contracts.json').read_bytes()
        value = json.loads(raw)
        if hashlib.sha256(raw).hexdigest() == CONTRACT_DIGEST and value['version'] == 1:
            return value['codes']
    except (OSError, KeyError, ValueError):
        pass
    return None


class CodeProof:
    def __init__(self, kernel):
        from wujiang.tactical.prediction_inline import code_hash
        expected = _contracts()
        if expected is None:
            raise CoupledAreaProbe()
        functions = contract_functions()
        for function in functions:
            key = function.__module__ + ':' + function.__qualname__
            if code_hash(function.__code__) not in expected.get(key, ()):
                note('area_reuse_changed_code:' + key)
                raise CoupledAreaProbe()
        self.kernel = kernel
        self.codes = tuple((f, f.__code__) for f in functions)
        self.types = contract_classes()
        for kind in self.types:
            getattr(kind,'__getattribute__')
        self.versions = kernel.type_versions(self.types)
        if self.versions is None:
            raise CoupledAreaProbe()
        from wujiang.platform.id_sequence import IdSequence
        modules, self.closures, self.id_bindings = {}, [], []
        for function in functions:
            data = function.__globals__
            module = sys.modules.get(data.get('__name__'))
            if module is None or vars(module) is not data:
                raise CoupledAreaProbe()
            fields = modules.setdefault(id(data),(module,data,{}))[2]
            for name in function.__code__.co_names:
                if name not in data:
                    continue
                value = data[name]
                # Worker jobs install fresh private sequences. Their identity
                # is not a program dependency: these nonlethal probes create no
                # units and every actual probe uses isolated_ids(). Check the
                # exact sequence protocol without retaining a previous job's ID.
                if name in ('_id_counter','_unit_counter') and type(value) is IdSequence:
                    self.id_bindings.append((data,name,IdSequence))
                else:
                    fields[name] = value
                if type(value) is ModuleType:
                    namespace = vars(value)
                    selected = modules.setdefault(id(namespace),(value,namespace,{}))[2]
                    for attribute in function.__code__.co_names:
                        if attribute in namespace:
                            nested = namespace[attribute]
                            if attribute in ('_id_counter','_unit_counter') and type(nested) is IdSequence:
                                self.id_bindings.append((namespace,attribute,IdSequence))
                            else:
                                selected[attribute] = nested
            if data.get(function.__name__) is function:
                fields[function.__name__] = function
            self.closures.extend((cell, cell.cell_contents) for cell in function.__closure__ or ())
        self.global_guard = kernel.make_field_guard([(owner,data,tuple(fields.items())) for owner,data,fields in modules.values()])
        from wujiang.tactical.rooms import ai
        from wujiang.tactical.prediction import _GraphSnapshot
        self.services = [(vars(service),tuple(vars(service).items()),_GraphSnapshot.identities(vars(service)))
            for service in (ai._AI_LATE_SCORING,ai._AI_MOBILITY_EFFECT_SCORING,ai._AI_WEATHER_SCORING,ai._AI_ATTACK_SCORING)]
        self.services.extend((value,tuple(value.items()) if type(value) is dict else tuple(value),_GraphSnapshot.identities(value))
            for value in (_SKILLS,_COMPONENTS,_FIELDS))
        from wujiang.tactical.probe_reuse import _COMPONENTS as probe_components
        self.services.append((probe_components,tuple(probe_components),_GraphSnapshot.identities(probe_components)))

    def matches(self):
        try:
            return (self.kernel.function_codes_match(self.codes)
                and self.kernel.type_versions_match(self.types,self.versions)
                and self.kernel.field_guard_matches(self.global_guard,_MISSING)
                and self.kernel.containers_match(self.services,deque)
                and all(type(data.get(name)) is kind for data,name,kind in self.id_bindings)
                and all(cell.cell_contents is value for cell, value in self.closures))
        except ValueError:  # a replaced wrapper can have an empty closure cell
            return False


@lru_cache(maxsize=2)
def _program_proof(kernel):
    """Only immutable program metadata survives a decision; battle values do not."""
    return CodeProof(kernel)


_IGNORED = frozenset({'__init__', 'bind', '__init_subclass__', '__subclasshook__', 'to_public_dict',
    'on_any_turn_end', 'on_owner_turn_start', 'on_owner_turn_end', 'on_basic_attack_finished',
    'on_before_any_turn_end', 'on_before_owner_turn_end',
    'on_skill_mana_paid',  # certified zero payment never dispatches this hook
    'modify_normal_move_actions_per_turn', 'modify_normal_move_distance', 'allows_split_normal_movement',
    'ignores_direct_unit_target_line'})


def _plain_kind(kind, core):
    if type(kind) not in (type,ABCMeta) or kind.__getattribute__ is not object.__getattribute__ or any('__getattr__' in vars(c) for c in kind.__mro__):
        return False
    if kind.__setattr__ not in (object.__setattr__,core.Unit.__setattr__) or kind.__delattr__ is not object.__delattr__:
        return False
    if kind.__eq__ is not object.__eq__ or kind.__hash__ is not object.__hash__:
        return False
    for owner in kind.__mro__:
        for name, value in vars(owner).items():
            if type(value) is property and value not in (vars(core.Unit).get(name), vars(core.Skill).get(name)):
                return False
            if not name.startswith('__') and type(value) not in (FunctionType,staticmethod,classmethod,property):
                if any('__get__' in vars(c) for c in type(value).__mro__):
                    return False
    return type(next((vars(c)['__dict__'] for c in kind.__mro__ if '__dict__' in vars(c)), None)) is GetSetDescriptorType


def _plain_data(value,core,seen=None):
    if value is None or type(value) in (int,float,bool,str,bytes) or type(value) is core.Position:
        return True
    if type(value) not in (dict,list,tuple,set,frozenset):
        return False
    if seen is None:
        seen = set()
    if id(value) in seen:
        return True
    seen.add(id(value))
    return (all(_plain_data(k,core,seen) and _plain_data(v,core,seen) for k,v in value.items())
        if type(value) is dict else all(_plain_data(v,core,seen) for v in value))


def _component_shape(kind,core,checked):
    if kind not in checked:
        valid = _plain_kind(kind,core)
        if kind not in _COMPONENTS:
            base = core.Skill if core.Skill in kind.__mro__ else core.BattleComponent
            valid = valid and all(type(getattr(base,name,None)) is not FunctionType
                or getattr(kind,name,None) is getattr(base,name) for name in dir(core.BattleComponent) if name not in _IGNORED)
            valid = valid and not any(name.startswith('on_') and name not in _IGNORED and callable(value) and not hasattr(base,name)
                for owner in kind.__mro__ for name,value in vars(owner).items())
        checked[kind] = valid
    return checked[kind]


def component_preflight(battle,core,kernel,*,probe=False):
    """Reject unsupported hook shapes before building a complete code proof."""
    from wujiang.tactical.probe_reuse import _COMPONENTS as probe_components
    cache=_session.get().setdefault('component_shape_preflight',{})
    for unit in [*battle.units.values(),*battle.destroyed_units]:
        if not _plain_kind(type(unit),core):
            return False
        for name in ('skills','traits','statuses'):
            group=vars(unit).get(name)
            if type(group) not in (list,tuple):
                return False
            for part in group:
                kind=type(part)
                if type(kind) not in (type,ABCMeta) or type(kind).__getattribute__ is not type.__getattribute__:
                    return False
                if probe and kind in probe_components:
                    continue
                getattr(kind,'__getattribute__')
                previous=cache.get(kind)
                if previous is None or not kernel.type_versions_match((kind,),previous[0]):
                    valid=_component_shape(kind,core,{})
                    versions=kernel.type_versions((kind,))
                    if versions is None:
                        return False
                    cache[kind]=(versions,valid)
                if not cache[kind][1]:
                    return False
    return True


def _component_ok(part, actor, core, checked):
    kind = type(part)
    if any(callable(v) for v in vars(part).values()):
        return False
    _component_shape(kind,core,checked)
    if not checked[kind] or any(not _plain_data(v,core) for name,v in vars(part).items() if name != 'owner'):
        return False
    from wujiang.tactical.direct_queries import _raw, _MISSING as missing
    for name in ('grants_stealth','remove_when_source_destroyed','suppresses_innate_traits',
                 'suppresses_projected_traits','prevents_mana_loss','all_skills_pierce_shields','all_damage_half_pierce'):
        value = vars(part).get(name,_raw(kind,name))
        if value is not missing and value is not None and type(value) not in (int,float,bool,str,bytes):
            return False
        if name in ('all_skills_pierce_shields','all_damage_half_pierce') and type(value) in (int,float,bool,str,bytes) and value:
            return False
    return kind not in _COMPONENTS or part.ai_area_independent(actor)


class AreaWorld:
    def __init__(self, battle, actor):
        from wujiang.tactical.engine import core
        from wujiang.tactical.direct_checkpoint import DirectSnapshot, UnsupportedGraph
        from wujiang.tactical.direct_queries import _query_context
        from wujiang.tactical.stat_queries import stat_values_for
        from wujiang.tactical.rooms import ai
        context = _query_context(battle, 'runtime_children')
        if context is None:
            raise CoupledAreaProbe()
        self.kernel = context[1]
        if not hasattr(self.kernel,'make_excluding_field_guard'):
            raise CoupledAreaProbe()
        self.units = tuple(battle.all_units())
        if (type(battle) is not core.Battle or len(self.units) < 12 or len(self.units) > 128
                or battle.pending_chain is not None or battle.resolving_action is not None
                or battle.pending_followup_actions or battle.winner is not None
                or getattr(battle, 'special_mode_codes', ()) or any(callable(v) for v in vars(battle).values())):
            raise CoupledAreaProbe()
        if len({u.unit_id for u in self.units}) != len(self.units) or any(battle.units.get(u.unit_id) is not u for u in self.units):
            raise CoupledAreaProbe()
        if not component_preflight(battle,core,self.kernel):
            raise CoupledAreaProbe()
        self.proof = _program_proof(self.kernel)
        if not self.proof.matches():
            raise CoupledAreaProbe()
        from wujiang.tactical.direct_queries import _raw, _MISSING as missing
        policy = vars(actor).get('ai_policy')
        if policy is not None:
            if not _plain_kind(type(policy),core):
                raise CoupledAreaProbe()
            flag = vars(policy).get('ai_messenger_mana',_raw(type(policy),'ai_messenger_mana'))
            if flag is not missing and (type(flag) not in (bool,int,float,str,bytes,type(None)) or flag):
                raise CoupledAreaProbe()
        checked, unit_kinds = {}, {}
        for unit in [*self.units, *battle.destroyed_units]:
            kind = type(unit)
            if kind not in unit_kinds:
                unit_kinds[kind] = _plain_kind(kind,core) and all(
                    name in {'__init__','__init_subclass__','__subclasshook__','build_skills','build_traits'}
                    or type(getattr(core.Unit,name,None)) is not FunctionType
                    or getattr(kind,name,None) is getattr(core.Unit,name) for name in dir(core.Unit))
            if not unit_kinds[kind] or any(callable(v) and not
                    (name == '_battle_ref' and type(v) is weakref.ReferenceType and v() is battle)
                    for name,v in vars(unit).items()):
                raise CoupledAreaProbe()
            for part in [*unit.skills, *unit.traits, *unit.statuses]:
                if (not _component_ok(part, actor, core, checked) or vars(part).get('owner') is not unit
                        or getattr(part, 'expires_after_action_token', None) is not None
                        or getattr(part, 'expires_after_declaration_id', None)
                        or getattr(part, 'remove_when_source_destroyed', False)):
                    note('area_reuse_unsupported_component:' + type(part).__name__)
                    raise CoupledAreaProbe()
            if unit in self.units:
                if (any(type(getattr(unit,name)) is not bool for name in ('alive','banished','is_clone','is_summon'))
                        or not unit.alive or unit.banished or unit.is_clone or unit.is_stealthed()
                        or unit.mounted_on_unit_id or unit.ridden_by_unit_id
                        or getattr(unit, 'attached_to_unit_id', None)
                        or getattr(unit, 'equipped_to_id', None)
                        or getattr(unit, '_ai_expected_banish_probability', 0)):
                    raise CoupledAreaProbe()
        for effect in battle.field_effects:
            if not _plain_kind(type(effect), core) or any(callable(v) for v in vars(effect).values()):
                raise CoupledAreaProbe()
            if type(effect) in _FIELDS:
                if not effect.ai_area_independent():
                    raise CoupledAreaProbe()
            elif any(type(getattr(core.BattleFieldEffect, name, None)) is FunctionType
                     and getattr(type(effect), name, None) is not getattr(core.BattleFieldEffect, name)
                     for name in dir(core.BattleFieldEffect) if name not in _IGNORED):
                raise CoupledAreaProbe()
            elif any(name.startswith('on_') and name not in _IGNORED and callable(value) and not hasattr(core.BattleFieldEffect,name)
                     for owner in type(effect).__mro__ for name,value in vars(owner).items()):
                raise CoupledAreaProbe()
        values = stat_values_for(list(self.units))
        if values is None or any(not -1e100 <= v <= 1e100 for row in values.values() for v in row.values()):
            raise CoupledAreaProbe()
        if any(type(getattr(u,name)) not in (int,float,bool) or not -1e100 <= getattr(u,name) <= 1e100
               for u in self.units for name in ('current_hp','current_mana','shields','temporary_shields','dodge_charges','level')):
            raise CoupledAreaProbe()
        self.before23 = ai.r23_action_state(battle)
        self.before18 = {u.unit_id: (u.current_hp,u.current_mana,u.total_shields(),u.dodge_charges,u.alive,u.position,None,
            bool(getattr(u.get_status('毁灭电击'), 'pending', False))) for u in self.units}
        self.snapshot = DirectSnapshot(battle, self.kernel, core)
        self.snapshot.visit(battle)
        self.snapshot.rngs = tuple((rng,rng.getstate()) for rng in self.snapshot.observed_rngs)
        self.snapshot.seal()
        unit_ids = {id(u) for u in self.units}
        namespaces = {id(data) for _,data in self.snapshot.namespaces}
        allowed = frozenset({'current_hp','shields','temporary_shields','protection_shields','dodge_charges'})
        outputs = ('logs','summary_events','combat_stats','_next_summary_event_id','_syncing_linked_units')
        rows = []
        for owner,data in self.snapshot.namespaces:
            excluded = tuple(allowed) if id(owner) in unit_ids else outputs if owner is battle else ()
            rows.append((owner,data,tuple((n,v) for n,v in data.items() if n not in excluded),excluded))
        self.readonly_fields = self.kernel.make_excluding_field_guard(rows)
        ignored = namespaces | {id(battle.logs),id(battle.summary_events)}
        def output_ids(value):
            if type(value) in (dict,list,tuple) and id(value) not in ignored:
                ignored.add(id(value))
                for item in value.values() if type(value) is dict else value:
                    output_ids(item)
        output_ids(battle.combat_stats)
        self.readonly_containers = [row for row in self.snapshot.containers if id(row[0]) not in ignored]
        self.battle, self.actor = battle, actor
        self.values, self.valid_skills = {}, {}
        self.reach, self.geometry = {}, {}
        self.completed = set()
        from wujiang.tactical.rooms.ai_skill_candidates import _AreaRecipientIndex
        self.occupancy = _AreaRecipientIndex(battle)
        self.bytes = self.snapshot.bytes + len(self.proof.codes)*192 + sum(128+len(fields)*64 for _,_,fields,_ in rows)

    def same(self, battle, actor):
        return self.battle is battle and self.actor is actor and self.proof.matches() and self.snapshot.same()

    def readonly_same(self):
        return (self.kernel.field_guard_matches(self.readonly_fields,_MISSING)
                and self.kernel.graph_matches([],self.snapshot.slots,self.readonly_containers,deque))


def _world_for(battle,actor):
    session = _session.get()
    state = session.setdefault('area_settlements', {'worlds':[], 'disabled':False})
    if state['disabled']:
        return None
    world = next((w for w in state['worlds'] if w.same(battle,actor)),None)
    if world is None:
        if len(state['worlds']) >= 4:
            state['disabled'] = True
            return None
        world = AreaWorld(battle,actor)
        if sum(w.bytes for w in state['worlds']) + world.bytes > 8*1024*1024:
            state['disabled'] = True
            return None
        state['worlds'].append(world)
        note('area_reuse_world_captures')
    return world


def try_reach_score(service,battle,actor,destination,compute):
    if not direct_scoring() or _PROJECTION.get() is not None or os.environ.get('WUJIANG_AI_SETTLEMENT_REUSE','1') == '0':
        return None
    from wujiang.tactical.rooms import ai
    from wujiang.tactical.engine import core
    from wujiang.tactical.direct_checkpoint import UnsupportedGraph
    if (service is not ai._AI_WEATHER_SCORING or service.services is not ai or type(destination) is not core.Position
            or any(callable(v) for v in vars(service).values())):
        return None
    try:
        world = _world_for(battle,actor)
        if world is None:
            return None
        key = destination.x,destination.y
        if key in world.reach:
            note('area_reuse_reach_hits')
            return world.reach[key]
        value = compute()
        if type(value) is int and world.same(battle,actor) and len(world.reach) < 256:
            world.reach[key] = value
        return value
    except (CoupledAreaProbe,UnsupportedGraph):
        _session.get()['area_settlements']['disabled'] = True
        note('area_reuse_unsupported_world')
        return None


def _probe_recipient_batches(world,service,actor,skill,kind,spec,groups):
    from wujiang.tactical.engine import core
    from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
    battle = world.battle
    collected = {}
    for hits,units in groups.items():
        with ai_probe_rollback(battle):
            for unit in units:
                count = [0]
                token = _PROJECTION.set((battle,actor,unit,hits,count))
                try:
                    parameters = dict(spec.parameters)
                    parameters['tags'] = set(parameters.get('tags',()))
                    battle.resolve_damage(core.DamageContext(source=actor,target=unit,area_cell_hits=hits,**parameters))
                finally:
                    _PROJECTION.reset(token)
                if (not unit.alive or unit.position != world.before18[unit.unit_id][5]
                        or battle.pending_followup_actions or count[0] != 1
                        or not world.readonly_same()):
                    return None
                if kind == 'r23':
                    value = service.r23_action_delta(actor,{unit.unit_id:world.before23[unit.unit_id]})
                else:
                    before = world.before18[unit.unit_id]
                    value = service._r18_damage_unit_value(actor,unit,before,before[0]-unit.current_hp)[0]
                if type(value) not in (int,float) or not math.isfinite(value):
                    return None
                collected[(skill,kind,unit.unit_id,hits)] = value
                note('area_reuse_recipient_probes')
        note('area_reuse_recipient_batches')
    return collected


def try_area_score(service, battle, actor, payload, kind, skill=None):
    if (not direct_scoring() or _PROJECTION.get() is not None
            or os.environ.get('WUJIANG_AI_SETTLEMENT_REUSE','1') == '0'):
        return None
    from wujiang.tactical.rooms import ai
    if service not in (ai._AI_LATE_SCORING, ai._AI_MOBILITY_EFFECT_SCORING) or service.services is not ai or any(callable(v) for v in vars(service).values()):
        return None
    if type(payload) is not dict or set(payload) - {'type','unit_id','skill_code','cells'} or type(payload.get('cells')) is not list:
        return None
    if (payload.get('type','skill' if kind == 'r23' else None) != 'skill'
            or payload.get('unit_id',actor.unit_id if kind == 'r23' else None) != actor.unit_id):
        return None
    if any(type(c) is not dict or set(c) != {'x','y'} or any(type(v) is not int for v in c.values()) for c in payload['cells']):
        return None
    if skill is None:
        data = object.__getattribute__(actor, '__dict__')
        if type(data.get('skills')) is not list:
            return None
        skill = next((s for s in data['skills'] if vars(s).get('code') == payload.get('skill_code')), None)
    if _SKILLS.get(type(skill)) != kind:
        return None
    if skill.owner is not actor or not any(s is skill for s in actor.skills) or payload.get('skill_code') != skill.code:
        return None
    session = _session.get()
    state = session.setdefault('area_settlements', {'worlds':[], 'disabled':False})
    if state['disabled']:
        return None
    from wujiang.tactical.direct_checkpoint import UnsupportedGraph
    from wujiang.tactical.engine import core
    try:
        world = _world_for(battle,actor)
        if world is None:
            return None
        if skill not in world.valid_skills:
            from wujiang.tactical.engine import core
            from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
            supported = (skill.mana_cost == 0 and skill.timing == 'active' and skill.target_mode == 'cell'
                and type(skill).prepay_resources is core.Skill.prepay_resources
                and type(skill).mana_cost_for_payload is core.Skill.mana_cost_for_payload
                and type(skill).mana_payer is core.Skill.mana_payer
                and actor.current_mana == round(actor.current_mana,2))
            with ai_probe_rollback(battle):
                supported = supported and skill.can_use(battle, actor, None)[0]
            world.valid_skills[skill] = supported
        if not world.valid_skills[skill]:
            return None
        from wujiang.tactical.heroes.common import pattern_signature, dedupe_positions
        if skill not in world.geometry:
            index = {}
            for pattern in skill.ai_direct_area_patterns(battle,actor):
                signature = pattern_signature(pattern)
                if signature not in index:
                    cells = dedupe_positions(pattern)
                    cell_set = set(cells)
                    recipients = tuple((u,sum(p in cell_set for p in world.occupancy.bodies[id(u)]))
                        for u in world.occupancy.at(cells))
                    index[signature] = tuple((p.x,p.y) for p in cells),recipients
            size = sum(256+len(signature)*64+len(row[1])*96 for signature,row in index.items())
            if len(index) > 1024 or sum(w.bytes for w in state['worlds']) + size > 8*1024*1024:
                world.valid_skills[skill] = False
                return None
            world.geometry[skill] = index
            world.bytes += size
        signature = tuple(sorted({(c['x'],c['y']) for c in payload['cells']}))
        entry = world.geometry[skill].get(signature)
        if entry is None:
            return None
        coordinates,recipients = entry
        from wujiang.tactical.engine import core
        spec = skill.ai_direct_area_spec(battle, actor, payload,tuple(core.Position(x,y) for x,y in coordinates))
        if type(spec) is not AreaDamageSpec:
            return None
        if (skill,kind) not in world.completed:
            groups = {}
            seen = set()
            for _,recipients_for_pattern in world.geometry[skill].values():
                for unit,hits in recipients_for_pattern:
                    key = unit.unit_id,hits
                    if key not in seen:
                        groups.setdefault(hits,[]).append(unit)
                        seen.add(key)
            if len(world.values) + len(seen) > 512:
                world.valid_skills[skill] = False
                return None
            values = _probe_recipient_batches(world,service,actor,skill,kind,spec,groups)
            world.completed.add((skill,kind))
            if values is None:
                world.valid_skills[skill] = False
                note('area_reuse_coupled_recipient_fallbacks')
                return None
            world.values.update(values)
        keys = [(skill,kind,u.unit_id,hits) for u,hits in recipients]
        if any(key not in world.values for key in keys):
            return None
        note('area_reuse_recipient_hits',len(keys))
        active = {key[2]:world.values[key] for key in keys}
        total = 0.0
        for unit in world.units:
            total += active.get(unit.unit_id,0.0)
        note('area_reuse_complete_skill_probes_avoided')
        return total - 8.0
    except (CoupledAreaProbe, UnsupportedGraph,core.ActionError,KeyError,ValueError,TypeError,AttributeError):
        state['disabled'] = True
        note('area_reuse_unsupported_world')
        return None
