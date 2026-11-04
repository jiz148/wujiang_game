"""Print a reviewable patch routing migrated next_five imports to their owners.

This command reads source files only. Apply its output with apply_patch after
the owner modules have passed import and dependency checks.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/wujiang/tactical/heroes/next_five.py"
LEGACY_MODULE = "wujiang.tactical.heroes.next_five"
OWNER_PREFIX = "wujiang.tactical.heroes.mechanics."


def owners() -> dict[str, str]:
    result: dict[str, str] = {}
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(OWNER_PREFIX):
            continue
        for alias in node.names:
            if alias.asname is not None or alias.name in result:
                raise ValueError(f"ambiguous compatibility export: {alias.name}")
            result[alias.name] = node.module
    return result


def import_lines(indent: str, module: str, aliases: list[ast.alias]) -> list[str]:
    names = [alias.name + (f" as {alias.asname}" if alias.asname else "") for alias in aliases]
    if len(names) == 1:
        return [f"{indent}from {module} import {names[0]}"]
    return [f"{indent}from {module} import (", *(f"{indent}    {name}," for name in names), f"{indent})"]


def main() -> None:
    known = owners()
    patch = ["*** Begin Patch"]
    changed_files = 0
    for path in sorted((ROOT / "src/wujiang/tactical").rglob("*.py")):
        if path == SOURCE:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        tree = ast.parse("\n".join(lines).removeprefix("\ufeff"))
        chunks: list[tuple[int, list[str]]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != LEGACY_MODULE:
                continue
            if not any(alias.name in known for alias in node.names):
                continue
            old = lines[node.lineno - 1:node.end_lineno]
            if any("#" in line for line in old) or any(alias.name == "*" for alias in node.names):
                raise ValueError(f"manual import review required: {path}:{node.lineno}")
            first = old[0].removeprefix("\ufeff")
            indent = first[:len(first) - len(first.lstrip())]
            groups: dict[str, list[ast.alias]] = defaultdict(list)
            for alias in node.names:
                groups[known.get(alias.name, LEGACY_MODULE)].append(alias)
            ordered = [LEGACY_MODULE] if LEGACY_MODULE in groups else []
            ordered.extend(sorted(module for module in groups if module != LEGACY_MODULE))
            replacement = [line for module in ordered for line in import_lines(indent, module, groups[module])]
            if node.lineno == 1 and old[0].startswith("\ufeff"):
                replacement[0] = "\ufeff" + replacement[0]
            chunk = ["@@", *("-" + line for line in old), *("+" + line for line in replacement)]
            chunks.append((node.lineno, chunk))
        if not chunks:
            continue
        changed_files += 1
        patch.append(f"*** Update File: {path.as_posix()}")
        for _, chunk in sorted(chunks):
            patch.extend(chunk)
    if not changed_files:
        raise ValueError("no migrated imports found")
    patch.append("*** End Patch")
    print("\n".join(patch))


if __name__ == "__main__":
    main()
