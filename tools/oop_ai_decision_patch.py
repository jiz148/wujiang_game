"""Emit a source-preserving decision coordinator patch; never edit files directly.

Decorated public functions remain decorated thin adapters in ai.py. Their
undecorated bodies move to an injected coordinator, with all central references
listed in the protocol. Refuse shadowed names or unsupported signatures.
"""

from __future__ import annotations

import ast
from pathlib import Path

from oop_ai_boundary_report import boundary


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("src/wujiang/tactical/rooms/ai.py")
DESTINATION = Path("src/wujiang/tactical/rooms/ai_decisions.py")
FIRST = "choose_turn_action"
LAST = "choose_electronic_teleport_action"


def render() -> str:
    if (ROOT / DESTINATION).exists():
        raise ValueError("destination already exists")
    source = ROOT / SOURCE
    lines = source.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    all_functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    start = next(index for index, node in enumerate(all_functions) if node.name == FIRST)
    end = next(index for index, node in enumerate(all_functions) if node.name == LAST)
    nodes = all_functions[start:end + 1]
    if len(nodes) != 15:
        raise ValueError("decision group changed")
    first_line = min([nodes[0].lineno, *(part.lineno for part in nodes[0].decorator_list)])
    last_line = nodes[-1].end_lineno
    report = boundary(source, FIRST, LAST)
    if len(report["needs_central_services"]) != 19 or report["central_callers"]:
        raise ValueError("decision dependency boundary changed")
    moved = {node.name for node in nodes}
    services = set(report["needs_central_services"])
    edits: dict[int, list[tuple[int, str, str]]] = {}
    for node in nodes:
        if node.args.posonlyargs or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
            raise ValueError(f"unsupported signature: {node.name}")
        local = {arg.arg for arg in node.args.args}
        local.update(child.id for child in ast.walk(node)
                     if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)))
        if local & (moved | services):
            raise ValueError(f"shadowed dependency in {node.name}: {local & (moved | services)}")
        header = lines[node.lineno - 1]
        expected = f"def {node.name}("
        if expected not in header:
            raise ValueError(f"signature changed: {node.name}")
        edits.setdefault(node.lineno - 1, []).append((header.index(expected), expected,
                                                       f"def {node.name}(self, "))
        annotations = {id(child) for annotation in
                       [*(arg.annotation for arg in node.args.args), node.returns]
                       if annotation is not None for child in ast.walk(annotation)}
        for child in ast.walk(node):
            if not isinstance(child, ast.Name) or not isinstance(child.ctx, ast.Load):
                continue
            if id(child) in annotations:
                continue
            prefix = "self." if child.id in moved else "self.services." if child.id in services else ""
            if prefix:
                edits.setdefault(child.lineno - 1, []).append(
                    (child.col_offset, child.id, prefix + child.id))

    header = [
        '"""AI turn, reaction, and prompted-action coordination."""', "",
        "from __future__ import annotations", "",
        "from copy import deepcopy",
        "from typing import TYPE_CHECKING, Any, Iterable, Optional, Protocol",
        "from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, Unit, battle_state_rollback",
        "from wujiang.tactical.prediction import direct_scoring, expected_local, record_candidates",
        "from wujiang.tactical.rooms.ai_probe import ai_probe_rollback",
        "from wujiang.tactical.rooms.ai_targets import living_hostile_combatants", "",
        "if TYPE_CHECKING:",
        "    from wujiang.tactical.rooms.ai import AICandidate, DifficultyProfile", "",
        "class DecisionServices(Protocol):",
        *[f"    {name}: Any" for name in sorted(services)], "",
        "class AIDecisionCoordinator:",
        '    """Choose from candidate builders and handle constrained action windows."""', "",
        "    def __init__(self, services: DecisionServices) -> None:",
        "        self.services = services", "",
    ]
    module = list(header)
    wrappers: list[str] = []
    for node in nodes:
        body = lines[node.lineno - 1:node.end_lineno]
        for absolute in range(node.lineno - 1, node.end_lineno):
            line = body[absolute - node.lineno + 1]
            for column, original, replacement in sorted(edits.get(absolute, []), reverse=True):
                if line[column:column + len(original)] != original:
                    raise ValueError(f"source changed at {absolute + 1}: {original}")
                line = line[:column] + replacement + line[column + len(original):]
            body[absolute - node.lineno + 1] = line
        module.extend(("    " + line) if line else "" for line in body)
        module.extend(["", ""])
        start_at = min([node.lineno, *(part.lineno for part in node.decorator_list)])
        wrappers.extend(lines[start_at - 1:node.body[0].lineno - 1])
        arguments = ", ".join(arg.arg for arg in node.args.args)
        wrappers.append(f"    return _AI_DECISION_COORDINATOR.{node.name}({arguments})")
        wrappers.extend(["", ""])

    output = ["*** Begin Patch", f"*** Add File: {DESTINATION.as_posix()}"]
    output.extend("+" + line for line in module[:-2])
    output.extend([f"*** Update File: {SOURCE.as_posix()}", "@@"])
    output.extend("-" + line for line in lines[first_line - 1:last_line])
    output.extend("+" + line for line in wrappers[:-2])
    output.append(" " + lines[last_line])
    output.extend(["*** End Patch"])
    return "\n".join(output) + "\n"


if __name__ == "__main__":
    print(render(), end="")
