"""Pinned build-time fingerprints; no runtime source compilation on a match."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys

CONTRACT_DIGEST = "4201bec5bfa6ced7b88bb20944a414f420808ce6b01f79c7c3a1a4579c78f786"


@lru_cache(maxsize=1)
def load_contracts():
    try:
        data = Path(__file__).with_suffix(".json").read_bytes()
        if hashlib.sha256(data).hexdigest() != CONTRACT_DIGEST:
            return None
        value = json.loads(data)
        if value["version"] != 1 or value["cache_tag"] != sys.implementation.cache_tag:
            return None
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return None
