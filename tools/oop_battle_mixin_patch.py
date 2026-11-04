"""Emit a source-preserving class-method relocation patch; never edit files."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import symtable


ROOT = Path(__file__).resolve().parents[1]


def class_table(table: symtable.SymbolTable, name: str) -> symtable.SymbolTable:
    return next(child for child in table.get_children() if child.get_name() == name)


def referenced_globals(table: symtable.SymbolTable) -> set[str]:
    found = {symbol.get_name() for symbol in table.get_symbols()
             if symbol.is_global() and symbol.is_referenced()}
    for child in table.get_children():
        found.update(referenced_globals(child))
    return found


def top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="src/wujiang/tactical/engine/core.py")
    parser.add_argument("--owner", default="Battle")
    parser.add_argument("--target-dir", default="src/wujiang/tactical/engine")
    parser.add_argument("--import-module", default="wujiang.tactical.engine.core")
    parser.add_argument("module", help="New engine module stem, e.g. battle_declarations")
    parser.add_argument("mixin", help="New mixin class name")
    parser.add_argument("methods", nargs="+")
    args = parser.parse_args()
    if not args.module.isidentifier() or not args.mixin.isidentifier():
        parser.error("module and mixin must be identifiers")
    source_path = ROOT / args.source
    target = ROOT / args.target_dir / f"{args.module}.py"
    if target.exists():
        parser.error(f"target already exists: {target}")
    source = source_path.read_text(encoding="utf-8-sig")
    lines = source.splitlines()
    tree = ast.parse(source)
    battle = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == args.owner)
    table = class_table(symtable.symtable(source, str(source_path), "exec"), args.owner)
    available = top_level_names(tree)
    methods = {node.name: node for node in battle.body if isinstance(node, ast.FunctionDef)}
    if len(set(args.methods)) != len(args.methods) or any(name not in methods for name in args.methods):
        parser.error("method list has duplicates or unknown names")
    fragments: list[tuple[int, str, str]] = []
    for name in args.methods:
        node = methods[name]
        if node.decorator_list:
            parser.error(f"decorated method needs manual review: {name}")
        start = node.lineno
        original = "\n".join(lines[start - 1:node.end_lineno])
        method_table = next(child for child in table.get_children() if child.get_name() == name)
        dependencies = sorted(referenced_globals(method_table) & available)
        body = original.splitlines()
        if dependencies:
            insertion = node.body[0].lineno - start
            if isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                insertion = node.body[0].end_lineno - start + 1
            imports = [f"        from {args.import_module} import ("]
            imports.extend(f"            {dependency}," for dependency in dependencies)
            imports.append("        )")
            body[insertion:insertion] = imports
        fragments.append((start, original, "\n".join(body)))
    fragments.sort()
    patch = ["*** Begin Patch", "*** Update File: " + str(source_path)]
    for _, original, _ in fragments:
        patch.extend(("@@", *["-" + line for line in original.splitlines()]))
    patch.append("*** Add File: " + str(target))
    additions = [f'"""{args.owner} {args.mixin} methods, retained under the public API."""',
                 "", "from __future__ import annotations", "", f"class {args.mixin}:",
                 '    """A cohesive Battle responsibility with no independent state."""', ""]
    for _, _, relocated in fragments:
        additions.extend(relocated.splitlines())
        additions.extend(("", ""))
    patch.extend("+" + line for line in additions)
    patch.append("*** End Patch")
    print(json.dumps({"patch": "\n".join(patch) + "\n", "methods": args.methods,
                      "lines": sum(len(original.splitlines()) for _, original, _ in fragments)}))


if __name__ == "__main__":
    main()
