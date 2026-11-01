"""Pinned build-time fingerprints; no runtime source compilation on a match."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys

CONTRACT_DIGEST = "ce3ac099a756f4833ac8e527976cf5b38aa7d831fc2ea083b471c351131c806b"


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
