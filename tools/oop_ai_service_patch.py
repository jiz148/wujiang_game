"""Emit, without applying, a source-preserving AI service extraction patch.

The manifest names a contiguous group of top-level functions, a destination
class, explicit room services, and imports. AST load positions are rewritten;
comments and string literals are left untouched. Review the generated patch.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from oop_ai_boundary_report import boundary


ROOT = Path(__file__).resolve().parents[1]


def patch_for(manifest: dict) -> str:
    source_path = Path(manifest["source"])
    module_path = Path(manifest["module"])
    if source_path.suffix != ".py" or module_path.suffix != ".py":
        raise ValueError("source and module must be Python files")
    if (ROOT / module_path).exists():
        raise ValueError(f"destination exists: {module_path}")
    source = ROOT / source_path
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    report = boundary(source, manifest["start"], manifest["end"])
    services = set(manifest["services"])
    unresolved = set(report["needs_central_services"]) - services
    if unresolved:
        raise ValueError(f"unprovided central dependencies: {sorted(unresolved)}")
    first, last = report["line_span"]
    tree = ast.parse("\n".join(lines))
    nodes = [node for node in tree.body if first <= node.lineno <= last]
    if not nodes or any(not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        for node in nodes):
        raise ValueError("group must contain only top-level functions")
    if any(node.decorator_list for node in nodes):
        raise ValueError("decorated functions need a separate reviewed extraction")
    moved = {node.name for node in nodes}
    if moved != set(report["moved_symbols"]):
        raise ValueError("group contains assignments or duplicate definitions")
    rewritten = moved | services
    for node in nodes:
        local_names = {arg.arg for arg in (*node.args.posonlyargs, *node.args.args,
                                          *node.args.kwonlyargs)}
        if node.args.vararg is not None:
            local_names.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            local_names.add(node.args.kwarg.arg)
        local_names.update(child.id for child in ast.walk(node)
                           if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)))
        shadowed = local_names & rewritten
        if shadowed:
            raise ValueError(f"local name would be rewritten in {node.name}: {sorted(shadowed)}")
    edits: dict[int, list[tuple[int, str, str]]] = {}
    for node in nodes:
        header = lines[node.lineno - 1]
        expected = f"def {node.name}("
        if expected not in header:
            raise ValueError(f"unsupported signature for {node.name}")
        edits.setdefault(node.lineno - 1, []).append((
            header.index(expected), expected, f"def {node.name}(self, "))
        for child in ast.walk(node):
            if not isinstance(child, ast.Name) or not isinstance(child.ctx, ast.Load):
                continue
            prefix = "self." if child.id in moved else "self.services." if child.id in services else ""
            if prefix:
                edits.setdefault(child.lineno - 1, []).append((
                    child.col_offset, child.id, prefix + child.id))
    body = lines[first - 1:last]
    for absolute, replacements in edits.items():
        line = body[absolute - first + 1]
        for column, original, replacement in sorted(replacements, reverse=True):
            if line[column:column + len(original)] != original:
                raise ValueError(f"source changed at line {absolute + 1}: {original}")
            line = line[:column] + replacement + line[column + len(original):]
        body[absolute - first + 1] = line
    header = [f'"""{manifest["description"]}"""', "",
              "from __future__ import annotations", "", *manifest["imports"], "",
              f'class {manifest["class"]}:',
              '    """Skill payload construction with explicit room services."""', "",
              '    def __init__(self, services: Any) -> None:',
              '        self.services = services', ""]
    module_lines = header + [("    " + line) if line else "" for line in body]
    if last >= len(lines):
        raise ValueError("extraction requires a following context line")
    output = ["*** Begin Patch", f"*** Add File: {module_path.as_posix()}"]
    output.extend("+" + line for line in module_lines)
    output.extend([f"*** Update File: {source_path.as_posix()}", "@@"])
    output.extend("-" + line for line in lines[first - 1:last])
    output.append(" " + lines[last])
    output.append("*** End Patch")
    return "\n".join(output) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    path = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    print(patch_for(json.loads(path.read_text(encoding="utf-8"))), end="")


if __name__ == "__main__":
    main()
