"""Reuse original attack probes only at the same complete checkpoint.

This serves position/threat queries without changing their ordering or sums.
Unknown hooks keep executing. The existing checkpoint owns the complete battle
observation; no second serialization, position-only key, or probability replay
is introduced here.
"""
from contextvars import ContextVar
from functools import wraps
import math
import os
from types import FunctionType
import weakref

from wujiang.tactical.prediction import _session, direct_scoring, note

_ACTIVE = ContextVar('ai_original_attack_probe_active',default=False)
_POSITION = ContextVar('ai_position_probe_scope',default=False)
_COMPONENTS=set()


def original_probe_component(cls):
    """An owner whose probe effects stay in the fully checkpointed graph."""
    _COMPONENTS.add(cls)
    return cls


def position_probe_scope(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        session=_session.get() if direct_scoring() else None
        units=session.setdefault('position_probe_units',set()) if session is not None else set()
        unit=kwargs.get('unit',args[3] if len(args)>3 else None)
        units.add(id(unit))
        # A single unit evaluated at distinct landings (e.g. teleport) offered
        # no repeated pair values in bounded measurements. Keep that work on
        # the original path; a multi-unit valuation can share identical probes.
        token=_POSITION.set(len(units)>1)
        try:
            return function(*args,**kwargs)
        finally:
            _POSITION.reset(token)
    return wrapped


def _qualified(battle,actor,target,kernel):
    from wujiang.tactical.engine import core
    from wujiang.tactical.direct_queries import component_snapshot
    from wujiang.tactical.settlement_reuse import (
        _program_proof,_plain_kind,_plain_data,_component_ok,_FIELDS,_IGNORED,component_preflight,CoupledAreaProbe)
    if (type(battle) is not core.Battle or type(battle.damage_rule) is not core.SummaryDamageRule
            or getattr(battle,'special_mode_codes',()) or len(battle.units)<8 or len(battle.units)>128):
        return None
    if not component_preflight(battle,core,kernel,probe=True):
        return None
    proof=_program_proof(kernel)
    if not proof.matches():
        return None
    checked, kinds={},{}
    for unit in [*battle.units.values(),*battle.destroyed_units]:
        kind=type(unit)
        if kind not in kinds:
            kinds[kind]=_plain_kind(kind,core) and all(
                name in {'__init__','__init_subclass__','__subclasshook__','build_skills','build_traits'}
                or type(getattr(core.Unit,name,None)) is not FunctionType
                or getattr(kind,name,None) is getattr(core.Unit,name) for name in dir(core.Unit))
        if not kinds[kind] or any(callable(v) and not (
                name=='_battle_ref' and type(v) is weakref.ReferenceType and v() is battle)
                for name,v in vars(unit).items()):
            note('attack_probe_unsupported_unit:'+kind.__name__)
            return None
        excluded={'_battle_ref','base_stats','skills','traits','statuses','ai_policy','_triggered_skill_definitions'}
        if (any(not _plain_data(v,core) for name,v in vars(unit).items() if name not in excluded)
                or type(unit.base_stats) is not core.Stats
                or any(type(getattr(unit.base_stats,n)) not in (int,float,bool)
                       for n in ('attack','defense','speed','attack_range','mana'))):
            return None
        parts=component_snapshot(battle,unit)
        if parts is None:
            return None
        for part in parts:
            supported=(_plain_kind(type(part),core) and all(_plain_data(v,core)
                for name,v in vars(part).items() if name!='owner')) if type(part) in _COMPONENTS else _component_ok(part,actor,core,checked)
            if not supported or vars(part).get('owner') is not unit:
                note('attack_probe_unsupported_component:'+type(part).__name__)
                return None
    for effect in battle.field_effects:
        kind=type(effect)
        if not _plain_kind(kind,core) or any(callable(v) for v in vars(effect).values()):
            return None
        if kind in _FIELDS:
            if not effect.ai_area_independent():
                return None
        elif any(type(getattr(core.BattleFieldEffect,name,None)) is FunctionType
                 and getattr(kind,name,None) is not getattr(core.BattleFieldEffect,name)
                 for name in dir(core.BattleFieldEffect) if name not in _IGNORED):
            return None
        elif any(name.startswith('on_') and name not in _IGNORED and callable(value)
                 and not hasattr(core.BattleFieldEffect,name)
                 for owner in kind.__mro__ for name,value in vars(owner).items()):
            return None
    if any(callable(v) for v in vars(battle).values()):
        return None
    if any(battle.units.get(vars(unit).get('unit_id')) is not unit for unit in (actor,target)):
        return None
    return proof


def cached_attack_probe(battle,actor,target,payload,attack_power,area_cell_hits,compute):
    if (not direct_scoring() or not _POSITION.get() or _ACTIVE.get()
            or os.environ.get('WUJIANG_AI_ATTACK_PROBE_REUSE','1')=='0'
            or type(payload) is not dict or payload
            or type(attack_power) not in (int,float,bool) or not math.isfinite(attack_power)
            or type(area_cell_hits) is not int or not 1<=area_cell_hits<=128):
        return compute()
    from wujiang.tactical.direct_expectation import ExpectedDamage
    from wujiang.tactical.rooms.ai_damage_probes import DamageImpact
    from wujiang.tactical.settlement_reuse import CoupledAreaProbe
    from wujiang.tactical.direct_checkpoint import DirectSnapshot,UnsupportedGraph
    session=_session.get()
    state=session.setdefault('original_attack_probes',{'snapshots':weakref.WeakKeyDictionary(),'count':0})
    if state.get('disabled'):
        return compute()
    key=id(actor),id(target),type(attack_power),attack_power.hex() if type(attack_power) is float else attack_power,area_cell_hits
    saved=session.get('last_direct_checkpoint')
    if type(saved) is not DirectSnapshot:
        saved=None
    entry=state['snapshots'].get(saved) if saved is not None else None
    if entry is not None and key in entry['values'] and saved.battle is battle and saved.same():
        if entry['proof'] is None:
            try:
                entry['proof']=_qualified(battle,actor,target,saved.kernel)
            except (CoupledAreaProbe,UnsupportedGraph,AttributeError,TypeError,ValueError):
                entry['proof']=None
            if entry['proof'] is None:
                state['disabled']=True
                note('attack_probe_unsupported_state')
        if entry['proof'] is not None and entry['proof'].matches():
            amount,probability,changed=entry['values'][key]
            note('attack_probe_value_hits')
            return DamageImpact(ExpectedDamage(amount,probability) if probability is not None else amount,changed)
    token=_ACTIVE.set(True)
    try:
        value=compute()
    finally:
        _ACTIVE.reset(token)
    if state['count']>=2048 or type(value) is not DamageImpact or type(value.changed_target) is not bool:
        return value
    amount=value.damage
    if type(amount) not in (int,float,ExpectedDamage) or not math.isfinite(amount):
        return value
    probability=amount.kill_probability if type(amount) is ExpectedDamage else None
    if probability is not None and (type(probability) not in (int,float,bool) or not 0<=probability<=1):
        return value
    saved=session.get('last_direct_checkpoint')
    if type(saved) is not DirectSnapshot or saved.battle is not battle:
        return value
    entry=state['snapshots'].get(saved)
    if entry is None:
        entry={'proof':None,'values':{}}
        state['snapshots'][saved]=entry
    entry['values'][key]=(float(amount) if type(amount) is ExpectedDamage else amount,probability,value.changed_target)
    state['count']+=1
    note('attack_probe_value_captures')
    return value
