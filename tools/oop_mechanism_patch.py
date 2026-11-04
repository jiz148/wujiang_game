"""Generate an apply_patch patch for source-preserving hero-mechanism relocation.

The manifest contains groups with start/end top-level symbol names, destination
module paths, and optional compatibility symbols. This tool never edits files.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("src/wujiang/tactical/heroes/excel_roster.py")
KITS = Path("src/wujiang/tactical/heroes/kits")


def import_sources(tree: ast.Module) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                result[name] = f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                result[name] = f"from {node.module} import {alias.name}" + (
                    f" as {alias.asname}" if alias.asname else ""
                )
    return result


def top_level(tree: ast.Module) -> list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    return [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]


def assigned_names(node: ast.stmt) -> set[str]:
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return set()
    return {child.id for target in targets for child in ast.walk(target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)}


def import_lines(module: str, names: set[str]) -> list[str]:
    joined = ", ".join(sorted(names))
    single = f"from {module} import {joined}"
    if len(single) <= 100:
        return [single]
    return [f"from {module} import (", *(f"    {name}," for name in sorted(names)), ")"]


def patch_for(manifest: dict) -> str:
    source = ROOT / SOURCE
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    tree = ast.parse("\n".join(lines))
    nodes = top_level(tree)
    by_name = {node.name: node for node in nodes}
    if len(by_name) != len(nodes):
        raise ValueError("duplicate top-level definitions require explicit handling")
    imports = import_sources(tree)
    groups = []
    moved: dict[str, str] = {}
    occupied: set[int] = set()
    for item in manifest["groups"]:
        start, end = by_name[item["start"]], by_name[item["end"]]
        if start.lineno >= end.lineno:
            raise ValueError(f"invalid boundaries: {item['start']}..{item['end']}")
        path = Path(item["module"])
        if not path.as_posix().startswith("src/wujiang/tactical/heroes/mechanics/") or path.suffix != ".py":
            raise ValueError(f"destination must be a mechanics Python module: {path}")
        if (ROOT / path).exists():
            raise ValueError(f"destination already exists: {path}")
        members = [node for node in nodes if start.lineno <= node.lineno < end.lineno]
        assigned = {name for node in tree.body if start.lineno <= node.lineno < end.lineno
                    for name in assigned_names(node)}
        module = ".".join(path.with_suffix("").parts[1:])
        for node in members:
            if node.lineno in occupied or node.name in moved:
                raise ValueError(f"overlapping migration at {node.name}")
            occupied.add(node.lineno)
            moved[node.name] = module
        for name in assigned:
            if name in moved:
                raise ValueError(f"overlapping migration at {name}")
            moved[name] = module
        groups.append((item, start.lineno - 1, end.lineno - 1, members, assigned))

    output = ["*** Begin Patch"]
    compat: dict[str, set[str]] = defaultdict(set)
    for node in tree.body:
        if any(begin < node.lineno <= end for _, begin, end, _, _ in groups):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) and child.id in moved:
                compat[moved[child.id]].add(child.id)
    for folder in (ROOT / "src", ROOT / "tests"):
        for path in folder.rglob("*.py"):
            if path == source or ROOT / KITS in path.parents:
                continue
            try:
                external = ast.parse(path.read_text(encoding="utf-8-sig"))
            except (SyntaxError, UnicodeError):
                continue
            for node in ast.walk(external):
                if isinstance(node, ast.ImportFrom) and node.module == "wujiang.tactical.heroes.excel_roster":
                    for alias in node.names:
                        if alias.name in moved:
                            compat[moved[alias.name]].add(alias.name)
    kit_imports: dict[Path, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    kit_contents: dict[Path, str] = {}
    for item, begin, end, members, assigned in groups:
        names = {node.name for node in members} | assigned
        block = "\n".join(lines[begin:end])
        parsed = ast.parse("from __future__ import annotations\n" + block)
        loads = {node.id for node in ast.walk(parsed) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        requires = sorted(loads - names)
        needed_imports: set[str] = set()
        for name in requires:
            if name in moved:
                if moved[name] != moved[next(iter(names))]:
                    needed_imports.add(f"from {moved[name]} import {name}")
            elif name in by_name:
                raise ValueError(f"{item['module']} still depends on central definition {name}")
            elif name in imports:
                needed_imports.add(imports[name])
        module = ".".join(Path(item["module"]).with_suffix("").parts[1:])
        header = [f'"""{item["description"]}"""', "", "from __future__ import annotations", ""]
        header.extend(sorted(needed_imports))
        header.extend(["", ""])
        output.append(f"*** Add File: {item['module']}")
        output.extend("+" + line for line in header + lines[begin:end])
        for name in item.get("compat", []):
            if name not in names:
                raise ValueError(f"compat symbol {name} not in {item['module']}")
            compat[module].add(name)
        for name in names:
            for kit in sorted((ROOT / KITS).glob("*.py")):
                data = kit_contents.setdefault(kit, kit.read_text(encoding="utf-8"))
                if re.search(rf"\br\.{re.escape(name)}\b", data):
                    kit_imports[kit][module].add(name)

    output.append(f"*** Update File: {SOURCE.as_posix()}")
    if compat:
        marker = "HeroFactory = Callable[[int], object]"
        if marker not in lines:
            raise ValueError("central compatibility import marker not found")
        output.append("@@")
        for module, names in sorted(compat.items()):
            output.extend("+" + line for line in import_lines(module, names))
        output.append("+")
        output.append(" " + marker)
    removals: list[tuple[int, int]] = []
    for _, begin, end, _, _ in sorted(groups, key=lambda group: group[1]):
        if removals and removals[-1][1] == begin:
            removals[-1] = (removals[-1][0], end)
        else:
            removals.append((begin, end))
    for begin, end in removals:
        output.append("@@")
        output.extend("-" + line for line in lines[begin:end])
        output.append(" " + lines[end])
    for kit, modules in sorted(kit_imports.items()):
        before = kit_contents[kit].splitlines()
        after = before.copy()
        declarations = []
        for module, names in sorted(modules.items()):
            declarations.extend(import_lines(module, names))
            for name in names:
                after = [re.sub(rf"\br\.{re.escape(name)}\b", name, line) for line in after]
        insert_at = next(i for i, line in enumerate(after) if line.startswith("from wujiang.tactical.heroes.hero_kit import")) + 1
        after[insert_at:insert_at] = declarations
        output.append(f"*** Update File: {kit.relative_to(ROOT).as_posix()}")
        for line in list(difflib.unified_diff(before, after, n=3, lineterm=""))[2:]:
            output.append("@@" if line.startswith("@@ ") else line)
    output.append("*** End Patch")
    return "\n".join(output) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    manifest_path = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    print(patch_for(json.loads(manifest_path.read_text(encoding="utf-8"))), end="")


if __name__ == "__main__":
    main()
