"""Optional native artifact identity/loading, without game imports or compilation."""
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[3]
MODULE_NAME = "_wujiang_rollback_native"
SOURCES = ("src/wujiang/tactical/engine/native/rollback_copy.c",
           "src/wujiang/platform/ai_native.py", "tools/build_ai_native.py")


def artifact_spec():
    abi = {"implementation": sys.implementation.name, "version": sys.version,
           "cache_tag": sys.implementation.cache_tag, "platform": sysconfig.get_platform(),
           "machine": platform.machine(), "debug": bool(sysconfig.get_config_var("Py_DEBUG")),
           "gil_disabled": bool(sysconfig.get_config_var("Py_GIL_DISABLED"))}
    digest = hashlib.sha256(json.dumps(abi, sort_keys=True).encode())
    for name in SOURCES:
        digest.update(name.encode() + b"\0" + (ROOT / name).read_bytes())
    identity = digest.hexdigest()[:24]
    directory = ROOT / ".runtime" / "ai-native" / identity
    return {"source_id": identity, "abi": abi, "directory": directory,
            "binary": directory / (MODULE_NAME + sysconfig.get_config_var("EXT_SUFFIX"))}


@lru_cache(maxsize=1)
def native_status():
    try:
        spec = artifact_spec()
        if sys.implementation.name != "cpython" or spec["abi"]["gil_disabled"]:
            return {"available": False, "reason": "requires CPython with GIL"}
        manifest = json.loads((spec["directory"] / "manifest.json").read_text(encoding="utf-8"))
        if manifest["source_id"] != spec["source_id"] or manifest["abi"] != spec["abi"]:
            raise ValueError("native artifact ABI/source mismatch")
        checksum = hashlib.sha256(spec["binary"].read_bytes()).hexdigest()
        if checksum != manifest["sha256"]:
            raise ValueError("native binary checksum mismatch")
        return {"available": True, "source_id": spec["source_id"], "sha256": checksum,
                "binary": str(spec["binary"]), "compiler": manifest["compiler"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"available": False, "reason": str(exc)}


@lru_cache(maxsize=1)
def _load_module():
    status = native_status()
    if not status["available"]:
        return None
    try:
        spec = importlib.util.spec_from_file_location(MODULE_NAME, status["binary"])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.source_id != status["source_id"]:
            raise ImportError("loaded native source identity mismatch")
        return module
    except Exception as exc:
        status.update(available=False, reason=str(exc))
        return None


@lru_cache(maxsize=1)
def load_copy():
    module = _load_module()
    return module.copy_value if module is not None else None


@lru_cache(maxsize=1)
def load_flat_copy():
    module = _load_module()
    return module.copy_flat_value if module is not None else None


@lru_cache(maxsize=1)
def load_flat_copy_statistics():
    module = _load_module()
    return module.flat_copy_statistics if module is not None else None


@lru_cache(maxsize=1)
def load_rng_mask():
    module = _load_module()
    return module.new_rng_mask if module is not None else None


@lru_cache(maxsize=1)
def load_component_scan():
    module = _load_module()
    if module is None:
        return None
    return module.component_status_scan, module.component_scan_statistics


@lru_cache(maxsize=1)
def load_checkpoint():
    module = _load_module()
    if module is None:
        return None
    return module.capture, module.restore
