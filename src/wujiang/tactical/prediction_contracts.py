"""Pinned build-time fingerprints; no runtime source compilation on a match."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys

CONTRACT_DIGEST = "93317ba7b868138d20768287e14f81ac1244dc7c67614ea0da2e4c49842d9514"


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
