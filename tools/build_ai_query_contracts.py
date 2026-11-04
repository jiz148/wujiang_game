"""Print reviewed code contracts as JSON; never execute generated code.

The caller installs the JSON with its SHA256 pin in prediction_contracts.py.
Runtime code and current source hashes must both match before any shortcut.
"""
import ast
import hashlib
import inspect
import json
from pathlib import Path
import sys
from types import CodeType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wujiang.tactical.prediction_factors import _SOURCES
from wujiang.tactical.prediction_inline import code_hash
from wujiang.tactical.engine import core

modules = dict(_SOURCES)
modules["wujiang.tactical.rooms.ai"] = ""
indices, restoring = {}, {}
for name in modules:
    path = ROOT / "src" / (name.replace(".", "/") + ".py")
    source = path.read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(source).hexdigest()
    if modules[name] and modules[name] != digest:
        raise ValueError("Reviewed source changed: " + name)
    modules[name] = digest
    entries = {}
    def visit(code):
        if code.co_name != "<module>":
            entries.setdefault(code.co_qualname, []).append(code_hash(code) if code.co_flags & inspect.CO_NEWLOCALS else None)
        for value in code.co_consts:
            if isinstance(value, CodeType): visit(value)
    visit(compile(source, str(path), "exec", dont_inherit=True))
    indices[name] = entries
    if name.endswith("rooms.ai"):
        for node in ast.parse(source).body:
            if not isinstance(node, ast.FunctionDef): continue
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and type(body[0].value.value) is str:
                body.pop(0)
            imports = []
            while body and isinstance(body[0], ast.ImportFrom) and body[0].level == 0:
                item = body.pop(0)
                imports.append((item.module, [alias.name for alias in item.names]))
            if (len(body) == 1 and isinstance(body[0], ast.With) and len(body[0].items) == 1
                    and ast.dump(body[0].items[0].context_expr) == ast.dump(ast.parse("ai_probe_rollback(battle)", mode="eval").body)):
                restoring[node.name] = imports
runtime_codes = {"DamageContext.__init__": code_hash(core.DamageContext.__init__.__code__)}
payload = {"version": 1, "cache_tag": sys.implementation.cache_tag, "sources": modules,
           "codes": indices, "self_restoring": restoring,
           "runtime_codes": runtime_codes}
text = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
print(json.dumps({"sha256": hashlib.sha256(text.encode()).hexdigest(), "data": text}))
