"""Eliminate a redundant expectation boundary only for certified pure paths.

Original scoring bodies still execute. Child action scores remain independent
complete expectations. Unknown code, data protocols, or runtime discovery keep
the existing rollback/enumerator. No candidate or stochastic leaf is discarded.
"""
import ast
from collections import OrderedDict, deque
from functools import lru_cache
import hashlib
import pickle
from types import CodeType, GetSetDescriptorType

_HASHES = {
    "score_skill_payload": "31181a95bb2cfb8379fd428e8c07afc9b19b116ba55800642ac30dea7e888677",
    "_score_skill_payload": "0c1a9dd29e0e77790729062bac367d81a449c5988e13a0f97a3a99f0b59336a2",
    "skill_payload_requires_enemy_impact": "ebb1d78772813dcc3b0c1d803eba4da823cfd9f4ccfa0b08d9fa2a814214adc0",
    "skill_from_ai_action": "07e34922ce89fcba0b16d81f7bdb6c3f2bf4e9c82767c8e134d26d83f74241b9",
}
_BOUNDARY = "e2d06321f5abacfa9095e8dac388ab86628ff936ea9898a0a551aff110af59c6"
_UNKNOWN = object()
_METADATA = {"should_throttle_unlimited_nonhostile_skill", "wuchang_action_success_probability",
             "nova_paid_action_adjustment", "cat_retaliation_action_penalty"}


def _normalize(code):
    def constant(value):
        if isinstance(value, CodeType): return _normalize(value)
        if type(value) is frozenset: return ("frozenset", tuple(sorted((constant(x) for x in value), key=repr)))
        if type(value) is tuple: return tuple(constant(x) for x in value)
        return value
    return (code.co_code, code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars,
            code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount, code.co_flags,
            code.co_exceptiontable, tuple(constant(x) for x in code.co_consts))


@lru_cache(maxsize=4096)
def code_hash(code):
    return hashlib.sha256(pickle.dumps(_normalize(code), protocol=4)).hexdigest()


def _boundary(function):
    from wujiang.tactical.prediction import expected_numeric
    try:
        decorate = next(code for code in expected_numeric.__code__.co_consts if isinstance(code, CodeType) and code.co_name == "decorate")
        wrapped = next(code for code in decorate.co_consts if isinstance(code, CodeType) and code.co_name == "wrapped")
    except (AttributeError, StopIteration):
        return False
    if code_hash(wrapped) != _BOUNDARY or getattr(function, "__code__", None) is not wrapped:
        return False
    closure = dict(zip(function.__code__.co_freevars, (cell.cell_contents for cell in function.__closure__ or ())))
    return closure.get("independent") is True and closure.get("function") is getattr(function, "__wrapped__", None)


def _plain(value, session):
    from wujiang.tactical.prediction import _GraphSnapshot
    if value is None or type(value) in (bool, int, float, str, bytes): return True
    kernel = session.get("preparation_kernel")
    if kernel is None: return False
    cache = session.setdefault("plain_input_cache", OrderedDict())
    entry = cache.get(id(value))
    if entry is not None and entry[0] is value and kernel.containers_match(entry[1], deque):
        cache.move_to_end(id(value)); return entry[2]
    rows, seen, active = [], set(), set()
    def visit(item):
        kind = type(item)
        if item is None or kind in (bool, int, float, str, bytes): return True
        if kind not in (dict, list, tuple, set, frozenset, deque): return False
        if id(item) in active: return False
        if id(item) in seen: return True
        seen.add(id(item)); active.add(id(item))
        try:
            if kind in (dict, list, set, deque):
                saved = tuple(item.items()) if kind is dict else tuple(item)
                rows.append((item, saved, _GraphSnapshot.identities(item)))
            if kind is dict:
                return (all(type(key) in (str, int, float, bool, bytes, type(None)) for key in item)
                        and all(visit(child) for child in item.values()))
            return all(visit(child) for child in item)
        finally:
            active.remove(id(item))
    plain = visit(value)
    size = sum(128 + 96 * len(row[1]) for row in rows) + 256
    if size <= 8 * 1024 * 1024:
        if entry is not None:
            session["plain_input_bytes"] -= entry[3]; cache.pop(id(value))
        used = session.get("plain_input_bytes", 0)
        while cache and (len(cache) >= 128 or used + size > 8 * 1024 * 1024):
            _, old = cache.popitem(last=False); used -= old[3]
        cache[id(value)] = value, rows, plain, size
        session["plain_input_bytes"] = used + size
    return plain


def _raw(kind, name):
    for owner in kind.__mro__:
        if name in vars(owner): return vars(owner)[name]
    return _UNKNOWN


def _actor(actor, battle):
    from wujiang.tactical.engine.core import Unit
    if (not issubclass(type(actor), Unit) or type(actor).__getattribute__ is not object.__getattribute__
            or _raw(type(actor), "__getattr__") is not _UNKNOWN
            or type(_data(actor, "hero_code")) is not str
            or type(_data(actor, "unit_id")) is not str): return False
    return battle.units.get(_data(actor, "unit_id")) is actor


def _skill_lookup(actor, action):
    from wujiang.tactical.engine.core import Unit, BattleComponent, Skill
    from wujiang.tactical.prediction_factors import _original
    kind = type(actor)
    if actor.is_summon or actor.is_clone: return False
    for name in ("get_skill", "iter_components", "projected_traits", "innate_traits_suppressed"):
        method = getattr(Unit, name)
        if name in vars(actor) or _raw(kind, name) is not method or not _original(method): return False
    statuses = {id(status) for status in actor.statuses}
    for component in [*actor.skills, *actor.traits, *actor.statuses]:
        cls = type(component)
        if cls.__getattribute__ is not object.__getattribute__ or _raw(cls, "__getattr__") is not _UNKNOWN: return False
        if id(component) in statuses:
            if ("provided_components" in vars(component) or _raw(cls, "provided_components") is not BattleComponent.provided_components): return False
        if issubclass(cls, Skill) and (type(vars(component).get("code", _raw(cls, "code"))) is not str
                or type(vars(component).get("timing", _raw(cls, "timing"))) is not str
                or any(hasattr(_raw(cls, attr), "__set__") for attr in ("code", "timing"))): return False
    return True


def _data(obj, name, default=_UNKNOWN):
    """Inspect a scalar without invoking a property or custom access hook."""
    kind = type(obj)
    if kind.__getattribute__ is not object.__getattribute__ or _raw(kind, "__getattr__") is not _UNKNOWN:
        return _UNKNOWN
    if type(_raw(kind, "__dict__")) is not GetSetDescriptorType:
        return _UNKNOWN
    descriptor = _raw(kind, name)
    if hasattr(descriptor, "__set__"):
        return _UNKNOWN
    return vars(obj).get(name, descriptor if descriptor is not _UNKNOWN else default)


def _schema(saved, key, session, dependencies, build):
    """Cache an effect/type certificate, never a score or mutable field value."""
    kernel = session.get("preparation_kernel")
    if kernel is None: return build()
    cache = getattr(saved, "metadata_schemas", None)
    if cache is None: saved.metadata_schemas = cache = {}
    entry = cache.get(key)
    if (entry is not None and kernel.type_versions_match(entry[1], entry[2])
            and all(function.__code__ is code for function, code in entry[3])):
        return entry[0]
    result = build()
    kinds = tuple({type(obj) for obj, _ in saved.namespaces})
    versions = kernel.type_versions(kinds)
    from wujiang.tactical.prediction import reserve_graph_metadata
    size = 512 + 48 * len(kinds) + 128 * len(dependencies)
    if versions is not None and reserve_graph_metadata(saved, ("metadata_schema", key), size):
        cache[key] = result, kinds, versions, tuple((fn, fn.__code__) for fn in dependencies)
    return result


def _metadata(function, battle, args, kwargs, session, saved):
    from wujiang.tactical.prediction_factors import _original
    from wujiang.tactical.engine.core import Battle, Unit, Skill
    from wujiang.tactical.rooms import ai
    if kwargs or not args or not _actor(args[0], battle) or not _original(function): return False
    actor = args[0]
    fields = _data(battle, "field_effects")
    empty_fields = type(fields) is list and not fields
    if function.__name__ == "should_throttle_unlimited_nonhostile_skill":
        if len(args) != 3 or not _plain(args[1], session) or not _plain(args[2], session):
            return False
        if not _original(ai.skill_from_ai_action) or not _boundary(ai.skill_payload_requires_enemy_impact): return False
        def skill_data():
            if not _skill_lookup(actor, args[1]) or type(_data(actor, "name")) is not str: return False
            components = [*actor.skills, *actor.traits, *actor.statuses,
                          *getattr(actor, "_triggered_skill_definitions", {}).values()]
            for component in components:
                if type(component).__getattribute__ is not object.__getattribute__: return False
                if issubclass(type(component), Skill):
                    if any(type(_data(component, name)) is not str for name in ("code", "timing")): return False
                    if any(type(_data(component, name, None)) not in (int, float, bool, type(None))
                           for name in ("max_uses_per_turn", "max_uses_per_battle", "cooldown_turns", "uses_this_turn")):
                        return False
            return True
        return _schema(saved, ("skill_data", id(actor)), session, (Unit.get_skill, ai.skill_from_ai_action), skill_data)
    if function.__name__ == "wuchang_action_success_probability":
        from wujiang.tactical.prediction_structure import inert_imports
        return (len(args) == 2 and empty_fields and _plain(args[1], session)
                and inert_imports(function, (("wujiang.tactical.heroes.excel_roster", ("WuchangMistField",)),))
                and "has_weather" not in vars(battle) and _raw(type(battle), "has_weather") is Battle.has_weather
                and _original(Battle.has_weather))
    if function.__name__ == "nova_paid_action_adjustment":
        return (len(args) == 3 and empty_fields and type(args[1]) is str and _plain(args[2], session)
                and "has_weather" not in vars(battle) and _raw(type(battle), "has_weather") is Battle.has_weather
                and _original(Battle.has_weather)
                and _schema(saved, "no_paid_observer", session, (Battle.has_weather,), lambda: all(
                        type(_data(unit, "hero_code")) is str and type(_data(unit, "alive")) is bool
                        and not (_data(unit, "hero_code") == "excel_r305" and _data(unit, "alive"))
                        for unit in battle.units.values())))
    if len(args) != 2 or type(args[1]) not in (list, tuple) or not _original(ai.has_cat_retaliation): return False
    for target in args[1]:
        if not _actor(target, battle): return False
        if type(_data(target, "unit_id")) is not str: return False
        if target is actor: continue
        # Static discovery was proved before entry. Preserve the original
        # enumeration and only omit the outer query if every name is plain.
        if not _schema(saved, ("no_cat", id(target)), session, (Unit.iter_components, ai.has_cat_retaliation),
                lambda: all(type(_data(component, "name")) is str and _data(component, "name") != "猫叔反制"
                            for component in target.iter_components())):
            return False
    return True


@lru_cache(maxsize=4)
def _dispatch_tree(filename):
    import inspect
    import __future__
    from wujiang.tactical.rooms import ai
    try:
        function = ai._score_skill_payload.__wrapped__
        if function.__code__.co_filename != filename:
            return None
        source = inspect.getsource(function)
        flags = function.__code__.co_flags & sum(getattr(__future__, name).compiler_flag for name in __future__.all_feature_names)
        compiled = compile(source, filename, "exec", flags=flags, dont_inherit=True)
        body = next(code for code in compiled.co_consts if isinstance(code, CodeType) and code.co_name == "_score_skill_payload")
    except (OSError, SyntaxError, ValueError, StopIteration, AttributeError, TypeError):
        return None
    if code_hash(body) != _HASHES["_score_skill_payload"]:
        return None
    tree = ast.parse(source)
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_score_skill_payload")


def _delegates(function, actor, action, payload, profile, instant_only):
    from wujiang.tactical.rooms import ai
    env = {"code": action.get("code") or payload.get("skill_code") or "", "instant_only": instant_only}
    def expr(node):
        if isinstance(node, ast.Constant): return node.value
        if isinstance(node, ast.Name): return env.get(node.id, _UNKNOWN)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "actor" and node.attr == "hero_code": return actor.hero_code
        if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
            values = [expr(item) for item in node.elts]
            return _UNKNOWN if any(value is _UNKNOWN for value in values) else tuple(values)
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                result = expr(value)
                if result is _UNKNOWN: return _UNKNOWN
                if isinstance(node.op, ast.And) and not result: return False
                if isinstance(node.op, ast.Or) and result: return True
            return isinstance(node.op, ast.And)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = expr(node.left), expr(node.comparators[0])
            if left is _UNKNOWN or right is _UNKNOWN: return _UNKNOWN
            op = node.ops[0]
            if isinstance(op, ast.Eq): return left == right
            if isinstance(op, ast.NotEq): return left != right
            if isinstance(op, ast.In): return left in right
            if isinstance(op, ast.NotIn): return left not in right
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "code" and len(node.args) == 1):
            prefix = expr(node.args[0]); return env["code"].startswith(prefix) if type(prefix) is str else _UNKNOWN
        return _UNKNOWN
    def pure_arg(node):
        if isinstance(node, ast.Name): return node.id in {"battle", "actor", "action", "payload", "profile", "skill", "code", "instant_only"}
        if isinstance(node, ast.Constant): return type(node.value) in (bool, int, float, str, type(None))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "skill_from_ai_action":
            return (not node.keywords and len(node.args) == 3
                    and all(isinstance(arg, ast.Name) and arg.id == name
                            for arg, name in zip(node.args, ("actor", "action", "code")))
                    and code_hash(ai.skill_from_ai_action.__code__) == _HASHES["skill_from_ai_action"]
                    and _skill_lookup(actor, action))
        return False
    def body(nodes):
        for node in nodes:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant): continue
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name == "code": continue
                if name == "skill" and pure_arg(node.value): continue
                return False
            if isinstance(node, ast.If):
                result = expr(node.test)
                if result is _UNKNOWN: return False
                if result: return body(node.body)
                if node.orelse: return body(node.orelse)
                continue
            if isinstance(node, ast.Return):
                value = node.value
                if isinstance(value, ast.Constant) and type(value.value) in (bool, int, float): return True
                return (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                        and _boundary(function.__globals__.get(value.func.id))
                        and all(pure_arg(arg) for arg in value.args)
                        and all(pure_arg(kw.value) for kw in value.keywords))
            return False
        return False
    tree = _dispatch_tree(function.__code__.co_filename)
    return tree is not None and body(tree.body)


def can_inline(function, battle, args, kwargs, session):
    name = function.__name__
    metadata = name in _METADATA and session.get("structural_batch", False)
    if (function.__module__ != "wujiang.tactical.rooms.ai" or not metadata and (name not in _HASHES
            or code_hash(function.__code__) != _HASHES[name])): return False
    if metadata and name in {"wuchang_action_success_probability", "nova_paid_action_adjustment"}:
        # Field-heavy electronic boards are a common unsupported case. Reject
        # them before scanning a saved graph, without invoking a descriptor.
        fields = _data(battle, "field_effects")
        if type(fields) is not list or fields:
            return False
    from wujiang.tactical.prediction_runtime import static_runtime_scan
    saved = next((saved for saved in session["graph_cache"] if saved.battle is battle
                  and saved.complete_observation and saved.matches() and static_runtime_scan(saved, session)), None)
    if saved is None: return False
    if metadata:
        return _metadata(function, battle, args, kwargs, session, saved)
    if len(args) < 3 or not _actor(args[0], battle): return False
    actor, action, payload = args[:3]
    if type(action) is not dict or type(payload) is not dict or not _plain(action, session) or not _plain(payload, session): return False
    code = action.get("code") or payload.get("skill_code") or ""
    if type(code) is not str: return False
    if name == "skill_payload_requires_enemy_impact":
        if kwargs or len(args) != 3 or code in {"mimic_skill", "agency_borrowed_skill", "migratory_bird_mark"}: return False
        groups = ("MOVE_SKILL_CODES", "SUMMON_SKILL_CODES", "HEAL_SKILL_CODES", "ALLY_BUFF_SKILL_CODES",
                  "SELF_BUFF_SKILL_CODES", "DAMAGING_SKILL_CODES", "HOSTILE_EFFECT_SKILL_CODES")
        if not all(type(function.__globals__.get(key)) in (set, frozenset) and _plain(function.__globals__[key], session) for key in groups): return False
        explicit = {"vain_giant_shadow", "gladiator_soul", "electronic_repair", "gladiator_gale", "fuma_trap",
                    "paralysis_card", "poison_card", "drain_card", "descent_moment", "smoke_spray", "afterimage",
                    "weapon_copy", "stance", "great_holy_light", "great_funeral"}
        return code in explicit or any(code in function.__globals__[key] for key in groups)
    if len(args) != 4 or set(kwargs) != {"instant_only"} or type(kwargs["instant_only"]) is not bool: return False
    from wujiang.tactical.rooms import ai
    if type(args[3]) is not ai.DifficultyProfile: return False
    if name == "score_skill_payload":
        return actor.hero_code != "excel_r123" and _boundary(ai._score_skill_payload) and _boundary(ai.nova_paid_action_adjustment)
    return name == "_score_skill_payload" and _delegates(function, actor, action, payload, args[3], kwargs["instant_only"])
