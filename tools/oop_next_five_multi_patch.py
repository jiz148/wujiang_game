"""Print source-preserving patches for the remaining interleaved next_five heroes.

Each manifest entry owns disjoint top-level ranges. The dependency check rejects
unreviewed references to definitions that would stay in the old module.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

from oop_next_five_patch import ROOT, SOURCE, assigned, import_sources


GROUPS = {
    "sandstorm": (
        [("SandstormWeatherEffect", "AttackLockTrait"),
         ("RockGodSandstormAura", "RockGodSandstormTrait")],
        "sandstorm", "Shared sandstorm battlefield objects.", {},
    ),
    "element": (
        [("ElementalEffectTrait", "AttackLockTrait"),
         ("ElementHunter", "UndeadKingLina"),
         ("EarthWalkerSkill", None)],
        "element_hunter", "Element Hunter battlefield objects and skills.", {},
    ),
    "lina": (
        [("AttackLockTrait", "RockGodSandstormTrait"),
         ("UndeadKingLina", "RockGod")],
        "undead_king_lina", "Undead King Lina battlefield objects.",
        {"CrazySandSkill": "sandstorm", "RendingSkill": "element_hunter",
         "SandstormWeatherEffect": "sandstorm", "WindSandSkill": "sandstorm"},
    ),
    "rock": (
        [("RockGodSandstormTrait", "DoomLightStatus"),
         ("RockAbsorbLinkStatus", "RockGod"),
         ("RockGod", "DoomlightDragon")],
        "rock_god", "Rock God battlefield objects and skills.",
        {"RockGodSandstormAura": "sandstorm"},
    ),
    "doom": (
        [("DoomLightStatus", None)],
        "doomlight_dragon", "Doomlight Dragon battlefield objects and skills.", {},
    ),
}


def patch_for(name: str) -> str:
    ranges, module_name, description, external = GROUPS[name]
    source_path = ROOT / SOURCE
    lines = source_path.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    top: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            top[node.name] = node
        for symbol in assigned(node):
            top[symbol] = node
    spans: list[tuple[int, int]] = []
    for start_name, end_name in ranges:
        begin = top[start_name].lineno - 1
        end = top[end_name].lineno - 1 if end_name is not None else len(lines)
        if begin >= end or (spans and begin < spans[-1][1]):
            raise ValueError(f"overlapping or reversed ranges: {name}")
        spans.append((begin, end))
    nodes = [node for node in tree.body if any(begin < node.lineno <= end for begin, end in spans)]
    owned = {node.name for node in nodes if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    owned.update(symbol for node in nodes for symbol in assigned(node))
    loads = {part.id for node in nodes for part in ast.walk(node)
             if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load)}
    imports = import_sources(tree)
    cross = loads & (set(top) - owned)
    if cross - set(external) or set(external) - cross - set(imports):
        raise ValueError(f"unreviewed cross-module dependencies: {sorted(cross)}; expected {sorted(external)}")
    required = sorted({imports[symbol] for symbol in loads & set(imports)})
    required.extend(f"from wujiang.tactical.heroes.mechanics.{module} import {symbol}"
                    for symbol, module in sorted(external.items()))
    if "Unit" in loads:
        required.append("from wujiang.tactical.engine.core import Unit")
    destination = Path(f"src/wujiang/tactical/heroes/mechanics/{module_name}.py")
    if (ROOT / destination).exists():
        raise ValueError(f"destination already exists: {destination}")
    blocks = [lines[begin:end] for begin, end in spans]
    header = [f'"""{description}"""', "", "from __future__ import annotations", "",
              *sorted(set(required)), "", ""]
    public = sorted(owned)
    source_imports = ([f"from wujiang.tactical.heroes.mechanics.{module_name} import (",
                       *(f"    {symbol}," for symbol in public), ")"]
                      if len(public) > 2 else
                      [f"from wujiang.tactical.heroes.mechanics.{module_name} import {', '.join(public)}"])
    patch = ["*** Begin Patch", f"*** Add File: {(ROOT / destination).as_posix()}"]
    file_lines = header + [line for index, block in enumerate(blocks)
                           for line in (([""] if index else []) + block)]
    patch.extend("+" + line for line in file_lines)
    patch.extend([f"*** Update File: {source_path.as_posix()}", "@@", " import math"])
    patch.extend("+" + line for line in source_imports + [""])
    for begin, end in spans:
        patch.extend(["@@", *("-" + line for line in lines[begin:end])])
    patch.append("*** End Patch")
    return "\n".join(patch) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=sorted(GROUPS))
    args = parser.parse_args()
    print(patch_for(args.group), end="")


if __name__ == "__main__":
    main()
