"""Check focused scenario names and required DamageContext arguments statically."""

from __future__ import annotations

import ast
import builtins
import pathlib
import symtable
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tools/check_focused_test_symbols.py <class-name> [...]", file=sys.stderr)
        return 2
    path = pathlib.Path(__file__).resolve().parents[1] / "tests" / "test_behavior.py"
    test_source = path.read_text(encoding="utf-8")
    root = symtable.symtable(test_source, str(path), "exec")
    test_classes = {
        node.name: node for node in ast.parse(test_source, filename=str(path)).body
        if isinstance(node, ast.ClassDef)
    }
    core_path = path.parents[1] / "src" / "wujiang" / "tactical" / "engine" / "core.py"
    damage_class = next(
        node for node in ast.parse(core_path.read_text(encoding="utf-8"), filename=str(core_path)).body
        if isinstance(node, ast.ClassDef) and node.name == "DamageContext"
    )
    required_damage_args = [
        node.target.id for node in damage_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.value is None
    ]
    module_names = set(dir(builtins)) | {
        symbol.get_name() for symbol in root.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }
    classes = {item.get_name(): item for item in root.get_children()
               if item.get_type() == "class"}
    errors: list[str] = []

    def inspect_scope(scope: symtable.SymbolTable, class_name: str) -> None:
        for symbol in scope.get_symbols():
            if symbol.is_global() and symbol.get_name() not in module_names:
                errors.append(f"{class_name}.{scope.get_name()}: {symbol.get_name()}")
        for child in scope.get_children():
            inspect_scope(child, class_name)

    for class_name in sys.argv[1:]:
        cls = classes.get(class_name)
        if cls is None:
            errors.append(f"missing scenario class: {class_name}")
            continue
        for child in cls.get_children():
            inspect_scope(child, class_name)
        for node in ast.walk(test_classes[class_name]):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "DamageContext"):
                continue
            if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
                keyword.arg is None for keyword in node.keywords
            ):
                continue
            supplied = set(required_damage_args[:len(node.args)]) | {
                keyword.arg for keyword in node.keywords
            }
            missing = [name for name in required_damage_args if name not in supplied]
            if missing:
                errors.append(f"{class_name}:{node.lineno} DamageContext missing {', '.join(missing)}")
    if errors:
        print("Focused scenario static errors:\n" + "\n".join(sorted(set(errors))))
        return 1
    print(f"Checked global names and DamageContext calls in {len(sys.argv) - 1} focused scenario class(es).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
