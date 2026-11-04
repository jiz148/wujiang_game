"""Static completeness check for source-preserving Battle and GameRoom splits."""

from __future__ import annotations

import ast
from collections import Counter
from copy import deepcopy
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def class_methods(source: str, class_names: set[str]) -> list[str]:
    tree = ast.parse(source)
    return [method.name for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name in class_names
            for method in node.body if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))]


def method_nodes(source: str, class_names: set[str]) -> dict[str, ast.FunctionDef]:
    return {method.name: method for node in ast.parse(source).body
            if isinstance(node, ast.ClassDef) and node.name in class_names
            for method in node.body if isinstance(method, ast.FunctionDef)}


def check(path: str, owner: str, module_glob: str) -> None:
    current = ROOT / path
    original = subprocess.check_output(["git", "show", "HEAD:" + path], cwd=ROOT,
                                       encoding="utf-8-sig")
    previous = set(class_methods(original, {owner}))
    names = class_methods(current.read_text(encoding="utf-8-sig"), {owner})
    old_nodes = method_nodes(original, {owner})
    moved_nodes: dict[str, ast.FunctionDef] = {}
    for module in current.parent.glob(module_glob):
        text = module.read_text(encoding="utf-8-sig")
        mixin_names = {node.name for node in ast.parse(text).body
                       if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin")}
        names.extend(class_methods(text, mixin_names))
        moved_nodes.update(method_nodes(text, mixin_names))
    counts = Counter(names)
    missing = sorted(previous - set(counts))
    duplicate = sorted(name for name, count in counts.items() if count > (3 if name == "chess_rng" else 1))
    print(f"{owner}: previous={len(previous)} current={len(counts)} missing={missing} duplicates={duplicate}")
    differences = []
    for name, relocated in moved_nodes.items():
        if name not in old_nodes:
            continue
        restored = deepcopy(relocated)
        restored.body = [node for node in restored.body if not (
            isinstance(node, ast.ImportFrom) and node.module in {
                "wujiang.tactical.engine.core", "wujiang.tactical.rooms.multiplayer"})]
        if ast.dump(restored, include_attributes=False) != ast.dump(old_nodes[name], include_attributes=False):
            differences.append(name)
    print(f"{owner}: moved={len(moved_nodes)} AST differences from HEAD={differences}")
    if missing or duplicate:
        raise SystemExit(1)


if __name__ == "__main__":
    check("src/wujiang/tactical/engine/core.py", "Battle", "battle_*.py")
    check("src/wujiang/tactical/rooms/multiplayer.py", "GameRoom", "room_*_mixin.py")
