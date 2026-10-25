"""Full-AI policy identity; the server pins its measured profile before import."""
from contextlib import contextmanager
from contextvars import ContextVar
import os

RULES_VERSION = ("expected-visible-v1" if os.environ.get("WUJIANG_AI_EXPECTATION_METHOD") == "enumerate"
                 else "expected-visible-direct-v2")
_rules = ContextVar("full_ai_prediction_rules", default=os.environ.get("WUJIANG_AI_SCORING_RULES") != "legacy")


def rules_enabled():
    return _rules.get()


@contextmanager
def prediction_rules(enabled=True):
    """Offline policy comparison, independent of the server launch profile."""
    token = _rules.set(bool(enabled))
    try:
        yield
    finally:
        _rules.reset(token)
