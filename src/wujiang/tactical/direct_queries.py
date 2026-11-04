"""Skip complete no-op notification passes in direct scoring only.

Live/legacy callbacks are untouched. Every current component is checked; the
cache retains class proofs, never battle results or a stale component list.
"""
import os

from wujiang.tactical.prediction import _session, note

_MISSING = object()
_SCALARS = (bool, int, float, str, bytes, type(None))
_NAMES = ("iter_components", "projected_traits", "innate_traits_suppressed", "_battle_ref",
          "banished", "alive", "is_clone", "is_summon", "skills", "traits", "statuses",
          "_triggered_skill_definitions", "provided_components", "projected_trait_for",
          "suppresses_innate_traits", "timing")


def _raw(kind, name):
    for owner in kind.__mro__:
        if name in vars(owner):
            return vars(owner)[name]
    return _MISSING


def _class_plan(kind, hook, unit, kernel, cache, core):
    key = kind, hook, unit
    previous = cache.get(key)
    if previous is not None:
        versions, codes, result = previous
        if kernel.type_versions_match((kind,), versions) and kernel.function_codes_match(codes):
            return result
    contract = core._DIRECT_COMPONENT_DISCOVERY
    expected = (contract[:3] if unit else (contract[3], contract[4], contract[8]) if hook == "runtime_children" else contract[3:5] if hook == "components" else
                (contract[3], contract[4], contract[5 if hook == "sync_linked_state" else 6]))
    names = (("iter_components", "projected_traits", "innate_traits_suppressed") if unit else
             ("provided_components", "projected_trait_for"))
    if not unit and hook != "components":
        names += (hook,)
    if (type(kind).__getattribute__ is not type.__getattribute__
            or _raw(kind, "__getattribute__") is not object.__getattribute__
            or _raw(kind, "__getattr__") is not _MISSING):
        return False
    if any(_raw(kind, name) is not method for name, method in zip(names, expected)):
        return False
    fields = (("skills", "traits", "statuses", "_triggered_skill_definitions", "_battle_ref",
               "banished", "is_summon", "is_clone", "alive", "position") if unit else
              ("suppresses_innate_traits", "timing"))
    if any(hasattr(_raw(kind, name), "__set__") for name in fields):
        return False
    suppression = _raw(kind, "suppresses_innate_traits")
    if not unit and suppression is not _MISSING and type(suppression) not in _SCALARS:
        return False
    # An ordinary class lookup assigns CPython's inherited invalidation tag.
    getattr(kind, names[0])
    versions = kernel.type_versions((kind,))
    if versions is None:
        return False
    codes = tuple((method, method.__code__) for method in expected)
    if len(cache) < 1024:
        cache[key] = versions, codes, (suppression, _raw(kind, "timing"))
    return suppression, _raw(kind, "timing")


def _query_context(battle, hook):
    session = _session.get()
    if session is None:
        return None
    contexts = session.setdefault("direct_query_contexts", {})
    context = contexts.get(hook, _MISSING)
    if context is _MISSING:
        contexts[hook] = None
        if os.environ.get("WUJIANG_AI_DIRECT_EMPTY_HOOKS", "1") == "0":
            return None
        from wujiang.tactical.engine import core
        if (type(battle) is not core.Battle or core.Battle.__getattribute__ is not object.__getattribute__
                or _raw(core.Battle, "__getattr__") is not _MISSING
                or any(hasattr(_raw(core.Battle, name), "__set__") for name in ("units", "special_mode_codes"))
                or core.Battle.all_units is not core._DIRECT_COMPONENT_DISCOVERY[7]):
            return None
        from wujiang.platform.ai_prediction_native import load_kernel
        kernel = load_kernel()
        if kernel is None or not hasattr(kernel, "component_snapshot"):
            return None
        versions = kernel.type_versions((core.Battle,))
        if versions is None:
            return None
        cache = session.setdefault("direct_hook_types", {})
        def classify(kind, unit):
            result = _class_plan(kind, hook, unit, kernel, cache, core)
            return cache.get((kind, hook, unit), False) if result is not False else False
        context = core, kernel, classify, ({}, {}), (core.Battle,), versions, core.Skill
        contexts[hook] = context
    if context is None:
        return None
    core, kernel, classify, native_plans, kinds, versions, skill = context
    if (type(battle) is not kinds[0] or core.Battle is not kinds[0] or core.Skill is not skill
            or not kernel.type_versions_match(kinds, versions)
            or not kernel.function_codes_match(core._DIRECT_COMPONENT_CODES)):
        return None
    data = vars(battle)
    if "all_units" in data or type(data.get("units")) is not dict:
        return None
    modes = data.get("special_mode_codes", ())
    if type(modes) not in (tuple, list, set, frozenset) or any(type(value) is not str for value in modes):
        return None
    return core, kernel, classify, modes, native_plans


def empty_hook_pass(battle, hook):
    context = _query_context(battle, hook)
    if context is None:
        return False
    core, kernel, classify, modes, native_plans = context
    if not kernel.empty_component_pass(battle.units, battle, classify, hook,
                                       "real_men" in modes, core.Skill, _NAMES, _MISSING, *native_plans):
        return False
    note("direct_empty_" + hook + "_passes")
    return True


def component_snapshot(battle, unit):
    context = _query_context(battle, "components")
    if context is None:
        return None
    core, kernel, classify, modes, native_plans = context
    if "real_men" in modes or not hasattr(kernel, "component_snapshot"):
        return None
    result = kernel.component_snapshot(battle.units, battle, unit, classify, "components",
                                       "arena" in modes, core.Skill, _NAMES, _MISSING, *native_plans)
    if result is not None:
        note("direct_component_snapshots")
    return result
