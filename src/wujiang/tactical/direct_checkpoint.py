"""Reuse identity-validated original-probe snapshots, without probability replay."""
from abc import ABCMeta
from collections import deque
from contextlib import contextmanager
import os
import random
from types import FunctionType, MethodType, GetSetDescriptorType, MemberDescriptorType
import weakref

from wujiang.tactical.prediction import _GraphSnapshot, _session, note, ForecastRandom
from wujiang.tactical.direct_queries import _query_context, _raw, _NAMES, _MISSING


class UnsupportedGraph(Exception):
    pass


class DirectSnapshot(_GraphSnapshot):
    def __init__(self, battle, kernel, core):
        super().__init__(battle)
        self.kernel = kernel
        self.unit_state_method = core._DIRECT_COMPONENT_CODES[9]
        self.object_types += (core.SummaryDamageRule,)
        self.validated_kinds = {}

    def visit(self, value):
        kind = type(value)
        # Scalars and already visited aliases need no second Python dispatch.
        # Class proofs are local to this capture; every retained snapshot still
        # checks type versions, function code, RNG, slots and the entire graph.
        if value is None or kind in (bool, int, float, str, bytes) or id(value) in self.seen:
            return
        if kind in (dict, list, set, tuple, frozenset, deque, type, FunctionType, MethodType,
                    weakref.ReferenceType, random.Random, ForecastRandom):
            return super().visit(value)
        versions = self.validated_kinds.get(kind)
        if versions is not None and self.kernel.type_versions_match((kind,), versions):
            return super().visit(value)
        if type(kind) not in (type, ABCMeta) or type(kind).__getattribute__ is not type.__getattribute__:
            raise UnsupportedGraph()
        module = kind.__module__
        if (kind.__getattribute__ is not object.__getattribute__
                or _raw(kind, "__getattr__") is not _MISSING
                or type(module) is not str or not module.startswith("wujiang.tactical.")):
            raise UnsupportedGraph()
        namespace = _raw(kind, "__dict__")
        if namespace is not _MISSING and type(namespace) is not GetSetDescriptorType:
            raise UnsupportedGraph()
        state_method = getattr(kind, "__getstate__", None)
        if (state_method is not object.__getstate__
                and state_method is not self.unit_state_method[0]
                and not (kind is self.position_type and state_method is self.frozen_getstate)):
            raise UnsupportedGraph()
        if kind.__reduce__ is not object.__reduce__ or kind.__reduce_ex__ is not object.__reduce_ex__:
            raise UnsupportedGraph()
        if self.is_dataclass(value):
            for name in self.slot_names(kind):
                descriptor = _raw(kind, name)
                if hasattr(descriptor, "__set__") and type(descriptor) is not MemberDescriptorType:
                    raise UnsupportedGraph()
        versions = self.kernel.type_versions((kind,))
        if versions is None:
            raise UnsupportedGraph()
        self.validated_kinds[kind] = versions
        super().visit(value)

    def seal(self):
        if not self.complete_observation:
            raise UnsupportedGraph()
        self.kinds = tuple(self.observation_protocols)
        self.versions = self.kernel.type_versions(self.kinds)
        if self.versions is None:
            raise UnsupportedGraph()
        self.codes = tuple({(method, code) for methods in self.observation_protocols.values()
                            for method, code in methods if type(method) is FunctionType})
        self.position_stamp = tuple((x, y) for _, x, y in self.observed_frozen_slots)
        self.bytes += 512 + len(self.seen) * 48 + len(self.kinds) * 64 + len(self.codes) * 128
        self.bytes += len(self.rngs) * 8192 + len(self.observed_frozen_slots) * 64
        self.bytes += len(self.validated_kinds) * 64
        self.bytes += 64 + len(self.position_stamp) * 64

    def same(self):
        kernel = self.kernel
        return (kernel.type_versions_match(self.kinds, self.versions)
                and kernel.function_codes_match(self.codes)
                and kernel.graph_matches(self.namespaces, self.slots, self.containers, deque)
                and all(rng.getstate() == state for rng, state in self.rngs)
                and kernel.position_stamp(self.observed_frozen_slots)
                    == self.position_stamp)

    def restore(self):
        # The native namespace setter caused an access violation in a late
        # long-match rollback. Keep graph/slot restoration native, but let
        # Python dispatch the namespace assignment through the owner's type.
        for owner, namespace in self.namespaces:
            if owner.__dict__ is not namespace:
                owner.__dict__ = namespace
        self.kernel.restore_slots(self.slots)
        self.kernel.restore_graph(self.containers, deque)
        for rng, state in self.rngs:
            rng.setstate(state)


@contextmanager
def direct_checkpoint(battle):
    from wujiang.tactical.engine import core
    session = _session.get()
    context = (_query_context(battle, "runtime_children")
               if os.environ.get("WUJIANG_AI_DIRECT_CHECKPOINT_REUSE", "1") != "0" else None)
    saved = None
    if context is not None and not session.get("direct_checkpoint_unsupported", False):
        _, kernel, classify, modes, native_plans = context
        cache = session.setdefault("direct_checkpoints", [])
        # Each retained graph was captured only after proving discovery empty.
        # Full identity/state/type checks plus _query_context's original method
        # code checks preserve that proof. Re-scanning every unit/component on
        # each identical damage probe adds no information.
        for index, candidate in enumerate(cache):
            if candidate.battle is battle and candidate.same():
                saved = candidate
                cache.insert(0, cache.pop(index))
                note("direct_checkpoint_reuses")
                note("direct_checkpoint_discovery_reuses")
                break
        if saved is None:
            note("direct_checkpoint_discovery_scans")
            if kernel.empty_component_pass(battle.units, battle, classify, "runtime_children",
                                           "real_men" in modes, core.Skill, _NAMES, _MISSING, *native_plans):
                try:
                    saved = DirectSnapshot(battle, kernel, core)
                    saved.visit(battle)
                    saved.rngs = tuple((rng, rng.getstate()) for rng in saved.observed_rngs)
                    saved.seal()
                except UnsupportedGraph:
                    saved = None
                    session["direct_checkpoint_unsupported"] = True
                    note("direct_checkpoint_unsupported")
                if saved is not None:
                    note("direct_checkpoint_captures")
                    if saved.bytes <= 8 * 1024 * 1024:
                        while cache and (len(cache) >= 16 or sum(row.bytes for row in cache) + saved.bytes > 8 * 1024 * 1024):
                            cache.pop()
                        cache.insert(0, saved)
    if saved is None:
        note("direct_checkpoint_fallbacks")
        with core.battle_state_rollback(battle):
            yield
        return
    rng_state = random.getstate()
    try:
        yield
    finally:
        saved.restore()
        random.setstate(rng_state)
