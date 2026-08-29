from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int

    def validated(self) -> RateLimitPolicy:
        if self.limit < 1 or self.window_seconds < 1:
            raise ValueError("Rate-limit policy values must be positive.")
        return self


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int


DEFAULT_RATE_LIMIT_POLICIES = {
    "auth_ip": RateLimitPolicy(20, 300),
    "auth_identity": RateLimitPolicy(5, 300),
    "join": RateLimitPolicy(20, 60),
    "analytics": RateLimitPolicy(60, 60),
    "mutation": RateLimitPolicy(180, 60),
}


class RateLimiter:
    """A bounded, process-local sliding-window limiter for the single-server runtime."""

    def __init__(
        self,
        policies: dict[str, RateLimitPolicy] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_buckets: int = 20_000,
    ) -> None:
        self.policies = {
            name: policy.validated()
            for name, policy in (policies or DEFAULT_RATE_LIMIT_POLICIES).items()
        }
        self._clock = clock
        self._max_buckets = max(100, int(max_buckets))
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def check(self, scope: str, key: str) -> RateLimitDecision:
        policy = self.policies[scope]
        now = self._clock()
        bucket_key = (scope, str(key or "unknown"))
        cutoff = now - policy.window_seconds
        with self._lock:
            bucket = self._buckets[bucket_key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= policy.limit:
                retry = max(1, math.ceil(bucket[0] + policy.window_seconds - now))
                return RateLimitDecision(False, policy.limit, 0, retry)
            bucket.append(now)
            remaining = max(0, policy.limit - len(bucket))
            reset = max(1, math.ceil(bucket[0] + policy.window_seconds - now))
            self._prune_if_needed(now)
            return RateLimitDecision(True, policy.limit, remaining, reset)

    def _prune_if_needed(self, now: float) -> None:
        if len(self._buckets) <= self._max_buckets:
            return
        stale = [
            key for key, bucket in self._buckets.items()
            if not bucket or bucket[-1] <= now - self.policies[key[0]].window_seconds
        ]
        for key in stale:
            self._buckets.pop(key, None)
        while len(self._buckets) > self._max_buckets:
            self._buckets.pop(next(iter(self._buckets)))
