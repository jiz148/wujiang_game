"""Inventory central score branches and existing skill-owned dispatch coverage."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wujiang.tactical.heroes.registry import create_hero  # noqa: E402
from wujiang.tools.per_hero_ai_debug import public_hero_codes  # noqa: E402


def literals(node: ast.AST) -> set[str]:
    return {child.value for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)}


def main() -> None:
    source = ROOT / "src/wujiang/tactical/rooms/ai_skill_dispatch_scoring.py"
    owner = next(node for node in ast.parse(source.read_text(encoding="utf-8")).body
                 if isinstance(node, ast.ClassDef) and node.name == "AISkillScoreCoordinator")
    function = next(node for node in owner.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_score_skill_payload")
    owners: dict[str, list[tuple[str, str, bool]]] = {}
    for hero_code in public_hero_codes():
        hero = create_hero(hero_code, 1)
        for skill in hero.skills:
            owners.setdefault(skill.code, []).append((hero_code, type(skill).__name__,
                                                       callable(getattr(skill, "ai_priority_score", None))))
    for node in function.body:
        if not isinstance(node, ast.If):
            continue
        text = ast.unparse(node.test)
        if "code" not in text:
            continue
        codes = sorted(literals(node.test) & owners.keys())
        if not codes:
            continue
        uses = [entry for code in codes for entry in owners[code]]
        owned = sum(entry[2] for entry in uses)
        missing = [(code, hero, cls) for code in codes for hero, cls, has_hook in owners[code] if not has_hook]
        print(f"{node.lineno:5} {node.end_lineno-node.lineno+1:3} {owned:3}/{len(uses):3} "
              f"codes={','.join(codes)} missing={missing[:6]}")


if __name__ == "__main__":
    main()
