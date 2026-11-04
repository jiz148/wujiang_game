"""Complete-state prediction keys, reusing only identity-validated graphs.

The state is serialized once, with the existing strict serializer. Query
arguments use persistent references only for objects that were in that exact
Pickler memo. Independent equal containers retain their independent identity
and aliases. Unsupported graphs/protocols keep the original joint serializer.
"""
from io import BytesIO
import pickle
import sys
from types import FunctionType


def graph_observation_stamp(saved):
    from wujiang.tactical.prediction import _session
    session = _session.get()
    kernel = saved.kernel if session is not None and session.get("structural_batch", False) else None
    plan = getattr(saved, "observation_protocol_plan", None)
    if plan is not None and kernel is not None:
        if not kernel.type_versions_match(plan[0], plan[1]) or not kernel.function_codes_match(plan[2]):
            return None
    for kind, methods in (() if plan is not None and kernel is not None else saved.observation_protocols.items()):
        names = saved.observation_protocol_names + (("getstate",) if any(rng.__class__ is kind for rng in saved.observed_rngs) else ())
        if tuple((getattr(kind, name, None), getattr(getattr(kind, name, None), "__code__", None))
                 for name in names) != methods:
            return None
    if kernel is not None and plan is None:
        kinds = tuple(saved.observation_protocols)
        versions = kernel.type_versions(kinds)
        if versions is not None:
            codes = tuple({(method, code) for methods in saved.observation_protocols.values()
                           for method, code in methods if type(method) is FunctionType})
            from wujiang.tactical.prediction import reserve_graph_metadata
            if reserve_graph_metadata(saved, "observation_protocol_plan", 384 + 48 * len(kinds) + 128 * len(codes)):
                saved.observation_protocol_plan = kinds, versions, codes
    if kernel is not None:
        positions = kernel.position_stamp(saved.observed_frozen_slots)
        if positions is None: return None
        return tuple((rng, rng.getstate()) for rng in saved.observed_rngs), positions
    for value, _, _ in saved.observed_frozen_slots:
        if type(value.x) is not int or type(value.y) is not int:
            return None
    return (tuple((rng, rng.getstate()) for rng in saved.observed_rngs),
            tuple((value.x, value.y) for value, _, _ in saved.observed_frozen_slots))


def complete_graph_key(battle, request, saved):
    from wujiang.tactical.rooms.ai_acceleration import _TacticalQueryPickler
    from wujiang.tactical.prediction import note, scoring_random_state, _session, _remove_graph
    stamp = graph_observation_stamp(saved)
    if stamp is None:
        return None
    if getattr(saved, "rejected_memo_stamp", None) == stamp:
        return None
    state = getattr(saved, "serialized_query_state", None)
    if state is None or state[0] != stamp:
        try:
            stream = BytesIO()
            writer = _TacticalQueryPickler(stream, protocol=pickle.HIGHEST_PROTOCOL)
            writer.dump(battle)
            data = stream.getvalue()
            if len(data) > 1024 * 1024:
                return None
            # Keep memo objects alive, including temporary __getstate__ dicts.
            # A bare set of ids would permit reuse after an object's death.
            memo = writer.memo.copy()
            bindings = []
            for _, obj in memo.values():
                if isinstance(obj, type) or type(obj) is FunctionType:
                    module = sys.modules.get(obj.__module__)
                    name = obj.__qualname__
                    if module is None or "." in name or vars(module).get(name) is not obj:
                        return None
                    bindings.append((vars(module), name, obj))
            state = stamp, data, memo, tuple(battle.__dict__), tuple(bindings)
            # Account for the strongly held Pickler memo and ephemeral Unit
            # state dictionaries, not just the graph's rollback containers.
            size = sys.getsizeof(data) + sys.getsizeof(memo) + sum(
                sys.getsizeof(row) + 32 + (sys.getsizeof(row[1]) if type(row[1]) in (dict, list, tuple) else 0)
                for row in memo.values())
            previous = getattr(saved, "serialized_query_bytes", 0)
            session = _session.get()
            if session is None or saved not in session["graph_cache"]:
                return None
            if saved.bytes - previous + size > 8 * 1024 * 1024:
                saved.rejected_memo_stamp = stamp
                note("complete_state_memo_budget_fallback")
                return None
            while session["graph_cache_bytes"] - previous + size > 8 * 1024 * 1024:
                removed = next(graph for graph in reversed(session["graph_cache"]) if graph is not saved)
                _remove_graph(session, removed)
            saved.bytes += size - previous
            session["graph_cache_bytes"] += size - previous
            saved.serialized_query_bytes = size
            saved.serialized_query_state = state
            note("complete_state_serializations")
        except Exception:
            return None
    else:
        note("complete_state_serialization_reuses")
    if any(namespace.get(name) is not obj for namespace, name, obj in state[4]):
        return None
    memo = state[2]
    class QueryPickler(_TacticalQueryPickler):
        def persistent_id(self, obj):
            found = memo.get(id(obj))
            if found is not None and found[1] is obj:
                return found[0]
            return None
    try:
        stream = BytesIO()
        QueryPickler(stream, protocol=pickle.HIGHEST_PROTOCOL).dump(request)
        # The saved graph itself prevents a recycled token, and makes this
        # stricter than value-only state equality. Its full data remains in key.
        return (state[1], scoring_random_state(), saved, state[3]), stream.getvalue()
    except Exception:
        return None
