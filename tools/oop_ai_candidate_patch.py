"""Emit four source-preserving candidate-builder modules and thin AI adapters."""

from __future__ import annotations

import ast
from pathlib import Path

from oop_ai_boundary_report import boundary


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("src/wujiang/tactical/rooms/ai.py")
GROUPS = (
    ("MoveCandidateBuilder", "ai_move_candidates", (("fast_move_shortlist", "build_zero_crossing_move_candidates"),)),
    ("AttackCandidateBuilder", "ai_attack_candidates", (("build_attack_candidates", "ancient_turret_prefilter_score"),)),
    ("SkillCandidateBuilder", "ai_skill_candidates", (("build_skill_candidates", "build_skill_candidates"),
                                                        ("dedupe_area_payloads_by_affected_units", "trim_skill_payloads_for_ai"))),
    ("ReactionCandidateBuilder", "ai_reaction_candidates", (("build_reaction_candidates", "active_damage_mana_reserve"),)),
)


def main() -> None:
    source = ROOT / SOURCE
    lines = source.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    definitions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    extracted: list[tuple[int, int, list[str]]] = []
    output = ["*** Begin Patch"]
    for class_name, module_name, ranges in GROUPS:
        destination = Path(f"src/wujiang/tactical/rooms/{module_name}.py")
        if (ROOT / destination).exists():
            raise ValueError(f"destination exists: {destination}")
        nodes: list[ast.FunctionDef] = []
        dependencies: set[str] = set()
        for first, last in ranges:
            report = boundary(source, first, last)
            dependencies.update(report["needs_central_services"])
            selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and definitions[first].lineno <= node.lineno <= definitions[last].end_lineno]
            if not selected or selected[0].name != first or selected[-1].name != last:
                raise ValueError(f"range changed: {first}..{last}")
            nodes.extend(selected)
        moved = {node.name for node in nodes}
        dependencies -= moved
        edits: dict[int, list[tuple[int, str, str]]] = {}
        for node in nodes:
            if node.args.posonlyargs or node.args.vararg or node.args.kwarg:
                raise ValueError(f"unsupported signature: {node.name}")
            local = {arg.arg for arg in (*node.args.args, *node.args.kwonlyargs)}
            local.update(child.id for child in ast.walk(node)
                         if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)))
            if local & (moved | dependencies):
                raise ValueError(f"shadowed dependency in {node.name}: {local & (moved | dependencies)}")
            original = f"def {node.name}("
            header = lines[node.lineno - 1]
            if original not in header:
                raise ValueError(f"signature changed: {node.name}")
            edits.setdefault(node.lineno - 1, []).append((header.index(original), original,
                                                           f"def {node.name}(self, "))
            annotations = {id(child) for annotation in
                           [*(arg.annotation for arg in (*node.args.args, *node.args.kwonlyargs)), node.returns]
                           if annotation is not None for child in ast.walk(annotation)}
            for child in ast.walk(node):
                if not isinstance(child, ast.Name) or not isinstance(child.ctx, ast.Load) or id(child) in annotations:
                    continue
                prefix = "self." if child.id in moved else "self.services." if child.id in dependencies else ""
                if prefix:
                    edits.setdefault(child.lineno - 1, []).append(
                        (child.col_offset, child.id, prefix + child.id))
        module = [
            f'"""{class_name} for the shared AI candidate pipeline."""', "",
            "from __future__ import annotations", "",
            "from typing import TYPE_CHECKING, Any, Iterable, Optional, Protocol",
            "from wujiang.tactical.engine.core import ActionError, Battle, Position, QueuedAction, Unit",
            "from wujiang.tactical.prediction import forecasting_active, note",
            "from wujiang.tactical.heroes.ai_policy import AIReactionCandidateContext",
            "from wujiang.tactical.heroes.ai_candidate_plan import DEFAULT_CANDIDATES",
            "from wujiang.tactical.rooms.ai_targets import living_hostile_combatants", "",
            "if TYPE_CHECKING:",
            "    from wujiang.tactical.rooms.ai import AICandidate, DifficultyProfile", "",
            f"class {class_name}Services(Protocol):",
            *[f"    {name}: Any" for name in sorted(dependencies)], "",
            f"class {class_name}:",
            f'    """Own {module_name.replace("ai_", "").replace("_", " ")} construction."""', "",
            f"    def __init__(self, services: {class_name}Services) -> None:",
            "        self.services = services", "",
        ]
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
        output.append(f"*** Add File: {destination.as_posix()}")
        output.extend("+" + line for line in module[:-2])
        for first, last in ranges:
            selected = [node for node in nodes if definitions[first].lineno <= node.lineno <= definitions[last].end_lineno]
            first_line = min([selected[0].lineno, *(part.lineno for part in selected[0].decorator_list)])
            last_line = selected[-1].end_lineno
            wrappers: list[str] = []
            for node in selected:
                start_at = min([node.lineno, *(part.lineno for part in node.decorator_list)])
                wrappers.extend(lines[start_at - 1:node.body[0].lineno - 1])
                args = ", ".join([*(arg.arg for arg in node.args.args),
                                  *(f"{arg.arg}={arg.arg}" for arg in node.args.kwonlyargs)])
                wrappers.append(f"    return _AI_{class_name.upper()}.{node.name}({args})")
                wrappers.extend(["", ""])
            extracted.append((first_line, last_line, wrappers[:-2]))
    output.extend([f"*** Update File: {SOURCE.as_posix()}"])
    for first, last, wrappers in sorted(extracted):
        output.append("@@")
        output.extend("-" + line for line in lines[first - 1:last])
        output.extend("+" + line for line in wrappers)
        output.append(" " + lines[last])
    output.append("*** End Patch")
    print("\n".join(output))


if __name__ == "__main__":
    main()
