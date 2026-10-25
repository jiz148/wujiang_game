"""Independent optional preparation kernel for expectation experiments only.

No game imports, automatic compilation or changes to the server's existing
rollback artifact. Sources and ABI are checked before a research process loads
this module. The server's legacy scoring never calls load_kernel.
"""
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[3]
MODULE_NAME = "_wujiang_prediction_native"
SOURCES = ("src/wujiang/tactical/engine/native/prediction_prepare.c",
           "src/wujiang/platform/ai_prediction_native.py", "tools/build_ai_prediction_native.py")


def artifact_spec():
    abi = {"implementation": sys.implementation.name, "version": sys.version,
           "cache_tag": sys.implementation.cache_tag, "platform": sysconfig.get_platform(),
           "machine": platform.machine(), "debug": bool(sysconfig.get_config_var("Py_DEBUG")),
           "gil_disabled": bool(sysconfig.get_config_var("Py_GIL_DISABLED"))}
    digest = hashlib.sha256(json.dumps(abi, sort_keys=True).encode())
    for name in SOURCES:
        digest.update(name.encode() + b"\0" + (ROOT / name).read_bytes())
    identity = digest.hexdigest()[:24]
    directory = ROOT / ".runtime" / "ai-prediction-native" / identity
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
            raise ValueError("prediction artifact ABI/source mismatch")
        checksum = hashlib.sha256(spec["binary"].read_bytes()).hexdigest()
        if checksum != manifest["sha256"]:
            raise ValueError("prediction binary checksum mismatch")
        return {"available": True, "source_id": spec["source_id"], "sha256": checksum,
                "binary": str(spec["binary"]), "compiler": manifest["compiler"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"available": False, "reason": str(exc)}


@lru_cache(maxsize=1)
def load_kernel():
    status = native_status()
    if not status["available"]:
        return None
    try:
        spec = importlib.util.spec_from_file_location(MODULE_NAME, status["binary"])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.source_id != status["source_id"]:
            raise ImportError("loaded prediction source identity mismatch")
        return module
    except Exception as exc:
        status.update(available=False, reason=str(exc))
        return None
