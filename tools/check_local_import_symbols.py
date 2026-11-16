"""Check direct local from-import names without importing game modules or running tests."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def bound_names(module: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in module.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0]
                         for alias in node.names if alias.name != "*")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def module_file(module_name: str) -> Path | None:
    candidate = SRC.joinpath(*module_name.split("."))
    for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
        if path.is_file():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    missing: list[str] = []
    checked = 0
    for importer in args.files:
        source = ast.parse(importer.read_text(encoding="utf-8-sig"), filename=str(importer))
        for node in ast.walk(source):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            dependency = module_file(node.module)
            if dependency is None:
                if node.module == "wujiang" or node.module.startswith("wujiang."):
                    missing.append(f"{importer}:{node.lineno}: module {node.module} missing")
                continue
            exports = bound_names(ast.parse(dependency.read_text(encoding="utf-8-sig"),
                                           filename=str(dependency)))
            for alias in node.names:
                if alias.name == "*":
                    continue
                checked += 1
                if alias.name not in exports and module_file(f"{node.module}.{alias.name}") is None:
                    missing.append(f"{importer}:{node.lineno}: {node.module}.{alias.name} missing")
    if missing:
        print("\n".join(missing))
        return 1
    print(f"Checked {checked} direct local import names across {len(args.files)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
