"""Print a source-preserving patch for one reviewed next_five mechanism group.

The tool is read-only. Apply its output with apply_patch after inspecting the
dependency report and saving before-migration behavior witnesses.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("src/wujiang/tactical/heroes/next_five.py")
GROUPS = {
    "status": ("replace_status_by_name", "damage_followup_effect_applies",
               "status_replacement", "Shared source-owned status replacement.", {}),
    "clone": ("StandardCloneSummon", "EarthWalkerSkill",
              "clone_split", "Reusable split and clone battlefield objects.", {}),
    "masamune": ("ARC_DIRECTION_CHOICES", "combined_remote_rectangle_patterns",
                 "masamune", "Masamune battlefield objects and mounted combat.",
                 {"replace_status_by_name": "status_replacement"}),
    "erasure": ("ErasureCounterStatus", "alive_owned_dragon_mount",
                "erasure_apostle", "Erasure Apostle battlefield objects.",
                {"SplitSkill": "clone_split"}),
    "li": ("ChainPullSkill", "card_effect_area",
           "li", "Li battlefield objects and skills.",
           {"square_around_cells": "next_five_geometry"}),
    "chanter": ("card_effect_area", "alive_owned_dragon_mount",
                "chanter", "Chanter battlefield objects and cards.",
                {"NaturalManaRecoveryTrait": "mana_recovery",
                 "position_key": "next_five_geometry",
                 "square_around_cells": "next_five_geometry"}),
    "soul": ("PassThroughMovementTrait", None,
             "soul_wraith", "Soul Wraith battlefield objects and shared traits.",
             {"position_key": "next_five_geometry",
              "square_around_cells": "next_five_geometry"}),
    "geometry": ("ALL_DIRECTIONS", "ElementalEffectTrait",
                 "next_five_geometry", "Shared battlefield geometry for next-five heroes.", {}),
    "recovery": ("NaturalManaRecoveryTrait", "RockGodSandstormAura",
                 "mana_recovery", "Reusable natural mana recovery trait.", {}),
    "effects": ("damage_followup_effect_applies", "ElementalEffectTrait",
                "next_five_effects", "Shared piercing effects and remote area patterns.", {}),
    "half_pierce": ("HalfPierceAttackTrait", "AttackLockTrait",
                    "half_pierce", "Reusable half-piercing attack trait.", {}),
    "dragon_breath": ("DragonBreathSkill", "DoomLightStatus",
                      "dragon_breath", "Reusable dragon breath skills.",
                      {"apply_piercing_status_effect": "next_five_effects",
                       "damage_followup_effect_applies": "next_five_effects",
                       "remote_rectangle_patterns": "next_five_effects"}),
    "area_helpers": ("combined_remote_rectangle_patterns", "maybe_queue_jade_reactive_bonus",
                     "area_helpers", "Shared declared-area pattern and damage helpers.",
                     {"remote_rectangle_patterns": "next_five_effects"}),
    "declared_area": ("DeclaredAreaSkillMixin", "JadeMachineGunSkill",
                      "declared_area", "Reusable declared-area skill behavior.", {}),
    "mana_cost": ("ManaPointCostSkill", "EarthWalkerSkill",
                  "mana_point_cost", "Reusable mana-point skill cost behavior.", {}),
    "jade": ("maybe_queue_jade_reactive_bonus", "EarthWalkerSkill",
             "jade", "Jade battlefield objects and skills.",
             {"DeclaredAreaSkillMixin": "declared_area",
              "area_patterns_preview": "area_helpers",
              "combined_remote_rectangle_patterns": "area_helpers",
              "resolve_area_damage": "area_helpers",
              "remote_rectangle_patterns": "next_five_effects"}),
    "n": ("MagneticWaveSkill", "RecoverManaSkill",
          "n", "N battlefield objects and skills.",
          {"DeclaredAreaSkillMixin": "declared_area",
           "ManaPointCostSkill": "mana_point_cost",
           "area_patterns_preview": "area_helpers",
           "damage_followup_effect_applies": "next_five_effects",
           "remote_rectangle_patterns": "next_five_effects"}),
    "blood": ("RecoverManaSkill", "alive_owned_dragon_mount",
              "blood_eater", "Blood Eater battlefield objects and skills.",
              {"HalfPierceAttackTrait": "half_pierce",
               "ManaPointCostSkill": "mana_point_cost"}),
    "dragon": ("alive_owned_dragon_mount", None,
               "dragon_rider", "Dragon Rider battlefield objects and mount.",
               {"ChainPullSkill": "li",
                "DeclaredAreaSkillMixin": "declared_area",
                "DragonBreathSkill": "dragon_breath",
                "area_patterns_preview": "area_helpers",
                "combined_remote_rectangle_patterns": "area_helpers",
                "damage_followup_effect_applies": "next_five_effects",
                "position_key": "next_five_geometry",
                "remote_rectangle_patterns": "next_five_effects"}),
}


def assigned(node: ast.stmt) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else ([node.target] if isinstance(node, ast.AnnAssign) else [])
    return {part.id for target in targets for part in ast.walk(target)
            if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Store)}


def import_sources(tree: ast.Module) -> dict[str, str]:
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                result[name] = f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            for alias in node.names:
                name = alias.asname or alias.name
                result[name] = f"from {node.module} import {alias.name}" + (
                    f" as {alias.asname}" if alias.asname else "")
    return result


def patch_for(name: str) -> str:
    start_name, end_name, module_name, description, external = GROUPS[name]
    source_path = ROOT / SOURCE
    lines = source_path.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    top = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            top[node.name] = node
        for symbol in assigned(node):
            top[symbol] = node
    begin = top[start_name].lineno - 1
    end = top[end_name].lineno - 1 if end_name is not None else len(lines)
    if begin >= end:
        raise ValueError("invalid group boundaries")
    nodes = [node for node in tree.body if begin < node.lineno <= end]
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
    # Existing postponed annotations can be resolved in the new owner module.
    if "Unit" in loads:
        required.append("from wujiang.tactical.engine.core import Unit")
    destination = Path(f"src/wujiang/tactical/heroes/mechanics/{module_name}.py")
    if (ROOT / destination).exists():
        raise ValueError(f"destination already exists: {destination}")
    block = lines[begin:end]
    header = [f'"""{description}"""', "", "from __future__ import annotations", "",
              *sorted(set(required)), "", ""]
    public = sorted(owned)
    source_import = (f"from wujiang.tactical.heroes.mechanics.{module_name} import ("
                     if len(public) > 2 else f"from wujiang.tactical.heroes.mechanics.{module_name} import {', '.join(public)}")
    source_imports = ([source_import, *(f"    {symbol}," for symbol in public), ")"]
                      if len(public) > 2 else [source_import])
    output = ["*** Begin Patch", f"*** Add File: {(ROOT / destination).as_posix()}"]
    output.extend("+" + line for line in header + block)
    output.extend([f"*** Update File: {source_path.as_posix()}", "@@", " import math"])
    output.extend("+" + line for line in source_imports + [""])
    output.extend(["@@", *["-" + line for line in block]])
    if end < len(lines):
        output.append(" " + lines[end])
    output.append("*** End Patch")
    return "\n".join(output) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=sorted(GROUPS))
    args = parser.parse_args()
    print(patch_for(args.group), end="")


if __name__ == "__main__":
    main()
