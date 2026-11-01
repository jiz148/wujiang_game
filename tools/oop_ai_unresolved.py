"""Find unresolved globals in generated AI service modules."""

from __future__ import annotations

import argparse
import builtins
import symtable
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    invalid = False
    for path in args.files:
        table = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
        known = {symbol.get_name() for symbol in table.get_symbols()
                 if symbol.is_imported() or symbol.is_assigned() or symbol.is_namespace()}
        children = list(table.get_children())
        for child in list(children):
            children.extend(child.get_children())
        loaded = {symbol.get_name() for child in children for symbol in child.get_symbols()
                  if symbol.is_global()}
        missing = sorted(loaded - known - set(dir(builtins)))
        print(path, missing)
        invalid |= bool(missing)
    return int(invalid)


if __name__ == "__main__":
    raise SystemExit(main())
