"""Save a deterministic public/action witness for one hero-kit migration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.tactical.heroes.registry import create_battle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", help="Hero code to witness in a fresh duel against bard")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--compare", type=Path, help="Fail if the new witness differs byte-for-byte")
    args = parser.parse_args()

    battle = create_battle(args.code, "bard")
    actor = next(unit for unit in battle.hero_units(1) if unit.hero_code == args.code)
    witness = {
        "skills": [[skill.code, type(skill).__name__, skill.to_public_dict(battle)] for skill in actor.skills],
        "traits": [[trait.name, type(trait).__name__, trait.to_public_dict(battle)] for trait in actor.traits],
        "actions": battle.action_snapshot_for(actor),
        "battle": battle.to_public_dict(),
    }
    content = json.dumps(witness, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(content)
    if args.compare is not None:
        equal = args.compare.read_bytes() == content
        print(f"{args.code}: byte_equal={equal}; saved={args.out}")
        return 0 if equal else 1
    print(f"{args.code}: saved={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
