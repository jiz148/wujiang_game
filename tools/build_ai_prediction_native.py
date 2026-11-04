"""Build the separate expectation-preparation kernel, without running gameplay."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wujiang.platform.ai_prediction_native import artifact_spec, native_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="Allow installing local Zig 0.14.1 on Windows")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    spec = artifact_spec()
    if sys.implementation.name != "cpython" or spec["abi"]["gil_disabled"]:
        parser.error("Prediction kernel requires CPython with GIL")
    if not args.force and native_status()["available"]:
        print(json.dumps(native_status(), ensure_ascii=False), flush=True)
        return 0
    directory = spec["directory"]
    directory.mkdir(parents=True, exist_ok=True)
    source = ROOT / "src/wujiang/tactical/engine/native/prediction_prepare.c"
    includes = sorted({sysconfig.get_path("include"), sysconfig.get_path("platinclude")})
    if os.name == "nt":
        zig = ROOT / ".runtime/ai-native-toolchain-0.14.1/ziglang/zig.exe"
        if not zig.exists() and args.bootstrap:
            subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(zig.parent.parent),
                            "ziglang==0.14.1", "--disable-pip-version-check", "--no-warn-script-location"], check=True)
        if not zig.exists():
            parser.error("Missing local compiler; rerun with --bootstrap")
        if spec["abi"]["machine"].upper() not in ("AMD64", "X86_64"):
            parser.error("Windows prediction kernel currently supports x64 Python only")
        compiler = [str(zig), "cc", "-target", "x86_64-windows-gnu"]
        link = [str(Path(sys.base_prefix) / "libs" / f"python{sys.version_info.major}{sys.version_info.minor}.lib")]
    else:
        compiler = shlex.split(os.environ.get("CC", sysconfig.get_config_var("CC") or "cc"))
        if not shutil.which(compiler[0]):
            parser.error("Install a C compiler and matching CPython development headers")
        link = ["-undefined", "dynamic_lookup"] if sys.platform == "darwin" else []
    temporary = directory / ("building" + sysconfig.get_config_var("EXT_SUFFIX"))
    command = [*compiler, "-shared", "-O2", "-fno-fast-math", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", *([] if os.name == "nt" else ["-fPIC"]),
               *("-I" + path for path in includes), f'-DWUJIANG_PREDICTION_SOURCE_ID="{spec["source_id"]}"',
               str(source), *link, "-o", str(temporary)]
    subprocess.run(command, check=True, cwd=directory)
    temporary.replace(spec["binary"])
    manifest = {"source_id": spec["source_id"], "abi": spec["abi"], "compiler": compiler,
                "sha256": hashlib.sha256(spec["binary"].read_bytes()).hexdigest()}
    pending = directory / "manifest.pending.json"
    pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(directory / "manifest.json")
    native_status.cache_clear()
    print(json.dumps(native_status(), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
