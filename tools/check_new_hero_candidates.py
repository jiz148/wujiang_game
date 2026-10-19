"""Static gate for selecting Excel heroes that are not publicly implemented.

This reads source declarations only. It does not create a battle or run tests.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def assigned_literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
                return frozenset(ast.literal_eval(value.args[0]))
            return ast.literal_eval(value)
    raise ValueError(f"{name} was not found in {path}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="+", help="Excel hero codes to verify before designing a new batch")
    args = parser.parse_args()
    specs = assigned_literal(
        ROOT / "src/wujiang/tactical/heroes/excel_roster_data.py", "EXCEL_HERO_SPECS"
    )
    public = assigned_literal(
        ROOT / "src/wujiang/tactical/heroes/excel_roster.py", "IMPLEMENTED_EXCEL_HERO_CODES"
    )
    by_code = {spec["code"]: spec for spec in specs}
    archive = json.loads((ROOT / "docs/武将设计思想.json").read_text(encoding="utf-8-sig"))
    documented = {hero["code"] for hero in archive["heroes"]}
    explanation = (ROOT / "docs/武将说明.md").read_text(encoding="utf-8")
    rejected = False
    for code in args.codes:
        spec = by_code.get(code)
        if spec is None:
            print(f"{code}: UNKNOWN SOURCE CODE")
            rejected = True
            continue
        existing = code in public
        print(
            f"{code} {spec['name']}: "
            f"{'ALREADY PUBLIC' if existing else 'UNIMPLEMENTED'}; "
            f"design-archive={'yes' if code in documented else 'no'}; "
            f"hero-description={'yes' if code in explanation else 'no'}"
        )
        rejected |= existing
    print(f"Public Excel heroes: {len(public)}/{len(by_code)}; unimplemented: {len(by_code) - len(public)}")
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
