"""Share the checkpoint of a source-proven, wholly self-restoring query.

Probability replay and argument copies remain unchanged. Only the redundant
outer world checkpoint is omitted; the original inner probe still restores
every branch, including exceptions. Unknown code/protocols use the old path.
"""
import builtins
from contextvars import ContextVar
from functools import lru_cache
import sys
from types import CodeType, FunctionType, ModuleType
from collections import deque
from abc import ABCMeta

prepared_graph = ContextVar("ai_prepared_inner_checkpoint", default=None)
_IMPORT = builtins.__import__
_MISSING = object()


@lru_cache(maxsize=1)
def _evaluation_code():
    from wujiang.tactical.prediction import expected_numeric
    code = expected_numeric.__code__
    for name in ("decorate", "wrapped", "evaluate"):
        code = next(value for value in code.co_consts if isinstance(value, CodeType) and value.co_name == name)
    return code


def _member(kind, name):
    return next((vars(base)[name] for base in kind.__mro__ if name in vars(base)), _MISSING)


def inert_imports(function, imports):
    if imports and function.__builtins__.get("__import__") is not _IMPORT:
        return False
    for module_name, names in imports:
        module = sys.modules.get(module_name)
        if (type(module) is not ModuleType or "__getattr__" in vars(module)
                or any(name == "*" or name not in vars(module) for name in names)):
            return False
    return True


def inert_argument_copy(value, seen=None, active=None):
    kind = type(value)
    if type(kind) not in (type, ABCMeta):
        return False
    if value is None or kind in (bool, int, float, str, bytes):
        return True
    if kind not in (dict, list, tuple, set, deque):
        return (not issubclass(kind, (dict, list, tuple, set, deque))
                and kind.__getattribute__ is object.__getattribute__
                and _member(kind, "__class__") is vars(object)["__class__"]
                and _member(kind, "__getattr__") is _MISSING)
    if seen is None: seen, active = set(), set()
    if id(value) in active: return False
    if id(value) in seen: return True
    seen.add(id(value)); active.add(id(value))
    try:
        if kind is dict:
            return (all(key is None or type(key) in (bool, int, float, str, bytes) for key in value)
                    and all(inert_argument_copy(item, seen, active) for item in value.values()))
        # Set construction hashes each element. Only primitive sets qualify.
        if kind is set:
            return all(item is None or type(item) in (bool, int, float, str, bytes) for item in value)
        return all(inert_argument_copy(item, seen, active) for item in value)
    finally:
        active.remove(id(value))


def callback_checkpoint(battle, callback, session):
    if not session.get("structural_batch", False) or type(callback) is not FunctionType:
        return None
    if callback.__code__ is not _evaluation_code():
        return None
    closure = dict(zip(callback.__code__.co_freevars, (cell.cell_contents for cell in callback.__closure__ or ())))
    function = closure.get("function")
    if type(function) is not FunctionType or function.__module__ != "wujiang.tactical.rooms.ai":
        return None
    from wujiang.tactical.prediction_contracts import load_contracts
    contracts = load_contracts()
    if contracts is None or function.__name__ not in contracts["self_restoring"]:
        return None
    from wujiang.tactical.rooms import ai
    from wujiang.tactical import prediction
    from wujiang.tactical.engine import core
    from wujiang.platform.ai_native import load_copy
    from wujiang.tactical.prediction_factors import _original
    if (not _original(function) or not _original(ai.ai_probe_rollback)
            or function.__globals__.get("ai_probe_rollback") is not ai.ai_probe_rollback
            or ai.prediction_rollback is not prediction.prediction_rollback):
        return None
    if (closure.get("_rollback_copy_value") is not core._rollback_copy_value
            or not _original(core._rollback_copy_value)
            or (core._ROLLBACK_NATIVE_ENABLED and core._NATIVE_ROLLBACK_COPY is not load_copy())):
        return None
    imports = contracts["self_restoring"][function.__name__]
    if not inert_imports(function, imports):
        return None
    if not inert_argument_copy((closure.get("args"), closure.get("kwargs"))):
        return None
    from wujiang.tactical.prediction_runtime import static_runtime_scan
    for saved in session["graph_cache"]:
        if (saved.battle is battle and saved.complete_observation and saved.matches()
                and static_runtime_scan(saved, session)):
            return saved
    return None


def copy_scoring_arguments(function, args, fallback):
    from wujiang.tactical import prediction
    session = prediction._session.get()
    if (session is not None and session.get("structural_batch", False)
            and function.__module__ == "wujiang.tactical.rooms.ai"
            and function.__name__ in {"skill_payload_requires_enemy_impact", "skill_payload_has_effective_enemy_impact"}
            and len(args) == 3 and type(args[1]) is dict):
        from wujiang.tactical.prediction_inline import _plain
        from wujiang.tactical.prediction_factors import _original
        from wujiang.tactical.rooms import ai
        action = args[1]
        plain = _plain(action, session)
        code = action.get("code") if plain else None
        one_read = (type(code) is str and bool(code) and code not in {"heaven_punishment", "mimic_skill", "agency_borrowed_skill"}
                    and (function.__name__ != "skill_payload_requires_enemy_impact" or action.get("target_mode") != "cell"))
        if one_read and _original(function) and _original(ai.skill_from_ai_action):
            # These source-reviewed branches read action metadata before any
            # effect probe. Other branches may consult it after a game hook
            # or pass it to another scorer and must retain the private copy.
            # Payloads retain the original no-memo copy/alias semantics. The
            # original closed-input restoration remains active on every leaf.
            copied = prediction._copy_arguments((args[0], args[2]), fallback)
            prediction.note("shared_action_metadata_arguments")
            return copied[0], args[1], copied[1]
    return prediction._copy_arguments(args, fallback)
