"""Inline only reviewed, empty component-discovery callbacks.

An identity-checked saved graph owns every current component when enumeration
is static. In that case the old preparation pass can discover nothing extra.
Custom discovery, instance overrides, projected traits and unfamiliar access
protocols continue through the original pass on every entry.
"""
import weakref

_MISSING = object()
_UNIT_METHODS = ("iter_components", "projected_traits", "innate_traits_suppressed")
_COMPONENT_METHODS = ("runtime_children", "provided_components", "projected_trait_for")


def _raw_member(kind, name):
    for owner in kind.__mro__:
        if name in vars(owner):
            return vars(owner)[name]
    return _MISSING


def reviewed_contract():
    from wujiang.tactical.engine import core
    from wujiang.tactical.prediction_factors import _original
    methods = [getattr(core.Unit, name) for name in _UNIT_METHODS]
    methods += [getattr(core.BattleComponent, name) for name in _COMPONENT_METHODS]
    methods.append(core.Battle.all_units)
    if not all(_original(method) for method in methods):
        return None
    return tuple((method, method.__code__) for method in methods)


def _build_plan(saved, contract):
    from wujiang.tactical.engine import core
    battle = saved.battle
    if (type(battle) is not core.Battle or "all_units" in vars(battle)
            or core.Battle.all_units is not contract[-1][0]
            or any(hasattr(_raw_member(core.Battle, name), "__set__")
                   for name in ("units", "destroyed_units", "special_mode_codes"))):
        return None
    modes = getattr(battle, "special_mode_codes", ())
    if type(modes) not in (set, frozenset, tuple, list) or any(type(mode) is not str for mode in modes):
        return None
    classes = {core.Battle}
    units = [*battle.all_units(), *battle.destroyed_units]
    for unit in units:
        kind = type(unit)
        if (type(kind).__getattribute__ is not type.__getattribute__
                or kind.__getattribute__ is not object.__getattribute__ or _raw_member(kind, "__getattr__") is not _MISSING
                or any(hasattr(_raw_member(kind, name), "__set__") for name in
                       ("skills", "traits", "statuses", "alive", "banished", "is_clone", "is_summon", "position",
                        "_battle_ref", "_triggered_skill_definitions"))):
            return None
        if any(name in vars(unit) or _raw_member(kind, name) is not contract[i][0]
               for i, name in enumerate(_UNIT_METHODS)):
            return None
        if any(type(items) is not list for items in (unit.skills, unit.traits, unit.statuses)):
            return None
        if any(type(getattr(unit, name)) is not bool for name in ("alive", "banished", "is_clone", "is_summon")):
            return None
        reference = getattr(unit, "_battle_ref", None)
        if reference is not None and (type(reference) is not weakref.ReferenceType or reference() is not battle):
            return None
        definitions = getattr(unit, "_triggered_skill_definitions", {})
        if type(definitions) is not dict:
            return None
        classes.add(kind)
        for component in [*unit.skills, *unit.traits, *unit.statuses, *definitions.values()]:
            kind = type(component)
            if (type(kind).__getattribute__ is not type.__getattribute__
                    or kind.__getattribute__ is not object.__getattribute__
                    or _raw_member(kind, "__getattr__") is not _MISSING
                    or any(hasattr(_raw_member(kind, name), "__set__")
                           for name in ("suppresses_innate_traits", "timing"))):
                return None
            for i, name in enumerate(_COMPONENT_METHODS, len(_UNIT_METHODS)):
                if name in vars(component) or _raw_member(kind, name) is not contract[i][0]:
                    return None
            suppression = vars(component).get("suppresses_innate_traits", _raw_member(kind, "suppresses_innate_traits"))
            if suppression is _MISSING:
                suppression = False
            if type(suppression) not in (bool, int, float, str, bytes, type(None)):
                return None
            if "real_men" in modes and isinstance(component, core.Skill) and type(component.timing) is not str:
                return None
            classes.add(kind)
    # Raw class dictionaries detect added shadows AND descriptor changes such
    # as rebinding the same function through staticmethod. No descriptor runs.
    names = (*_UNIT_METHODS, *_COMPONENT_METHODS, "all_units", "__getattribute__", "__getattr__",
             "suppresses_innate_traits", "timing", "units", "destroyed_units", "special_mode_codes",
             "skills", "traits", "statuses", "alive", "banished", "is_clone", "is_summon", "position",
             "_battle_ref", "_triggered_skill_definitions")
    owners = {owner for cls in classes for owner in cls.__mro__}
    checks = tuple((vars(owner), name, vars(owner).get(name, _MISSING)) for owner in owners for name in names)
    mros = tuple((cls, cls.__mro__) for cls in classes)
    return checks, mros


def static_runtime_scan(saved, session):
    contract = session.get("static_runtime_contract")
    if contract is None or not saved.complete_observation:
        return False
    if any(method.__code__ is not code for method, code in contract):
        return False
    if not hasattr(saved, "static_runtime_plan"):
        saved.static_runtime_plan = _build_plan(saved, contract)
        if saved.static_runtime_plan is not None and session.get("preparation_kernel") is not None:
            types = tuple(kind for kind, _ in saved.static_runtime_plan[1])
            saved.static_runtime_versions = types, session["preparation_kernel"].type_versions(types)
    plan = saved.static_runtime_plan
    if plan is None:
        return False
    checks, mros = plan
    versions = getattr(saved, "static_runtime_versions", None)
    if versions is not None and versions[1] is not None:
        return session["preparation_kernel"].type_versions_match(*versions)
    return (all(cls.__mro__ is mro for cls, mro in mros)
            and all(namespace.get(name, _MISSING) is value for namespace, name, value in checks))
