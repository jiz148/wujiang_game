"""Read-only dependency boundary for a contiguous AI extraction batch.

Use names of top-level definitions as boundaries. The report shows exactly
which central symbols a new module would still need and which callers need a
compatibility import. A two-way dependency rejects a naive module move.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("src/wujiang/tactical/rooms/ai.py")
DEFINITION = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def names_defined(node: ast.stmt) -> set[str]:
    if isinstance(node, DEFINITION):
        return {node.name}
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {child.id for target in targets for child in ast.walk(target)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)}
    return set()


def names_loaded(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)}


def boundary(source: Path, start: str, end: str) -> dict:
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    definitions = {node.name: node for node in tree.body if isinstance(node, DEFINITION)}
    if start not in definitions or end not in definitions:
        raise ValueError("start and end must name top-level definitions")
    first, last = definitions[start].lineno, definitions[end].end_lineno
    if first > last:
        raise ValueError("start follows end")
    inside = [node for node in tree.body if first <= node.lineno <= last]
    outside = [node for node in tree.body if node not in inside]
    moved = set().union(*(names_defined(node) for node in inside))
    retained = set().union(*(names_defined(node) for node in outside))
    outbound = sorted(set().union(*(names_loaded(node) for node in inside)) & retained)
    inbound = sorted(set().union(*(names_loaded(node) for node in outside)) & moved)
    imports = sorted({name for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
                      for name in names_defined_import(node)}
                     & set().union(*(names_loaded(node) for node in inside)))
    return {
        "source": str(source.relative_to(ROOT)), "start": start, "end": end,
        "line_span": [first, last], "lines": last - first + 1,
        "moved_symbols": sorted(moved), "needs_central_services": outbound,
        "central_callers": inbound, "needed_imports": imports,
        "naive_module_cycle": bool(outbound and inbound),
    }


def names_defined_import(node: ast.Import | ast.ImportFrom) -> set[str]:
    return {alias.asname or alias.name.split(".")[0]
            for alias in node.names if alias.name != "*"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    source = args.source if args.source.is_absolute() else ROOT / args.source
    print(json.dumps(boundary(source, args.start, args.end),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
