"""Find element IDs the frontend looks up but that index.html never defines.

`$("turn-pill")` is just a string, so nothing checks it. Delete the element from
the markup and every static tool still passes; the failure surfaces only when a
player reaches the exact branch that touches it, and it arrives as
`Cannot set properties of null` from inside render() — which the caller usually
catches, leaving a frozen button and a clean console.

That is not hypothetical: removing the oversized title block left renderHeader()
still writing to `#topbar-subline`, which broke every path into battle.

Only literal arguments are checked. An ID assembled at runtime is skipped rather
than guessed at, so this stays silent unless it has found something real.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = ROOT / "static"

# `$("x")`, and the DOM call it wraps.
LOOKUP = re.compile(r"""(?:\$|document\s*\.\s*getElementById)\s*\(\s*(["'])([^"'`\n]+)\1\s*\)""")

# `id="x"` in markup, or inside a JS template that builds markup.
MARKUP_ID = re.compile(r"""\bid\s*=\s*["']([^"'{}]+)["']""")

# `node.id = "x"` — plenty of chrome is built at runtime rather than served.
ASSIGNED_ID = re.compile(r"""\.id\s*=\s*(["'])([^"'`\n]+)\1""")


def known_ids() -> set[str]:
    ids: set[str] = set()
    for path in sorted(STATIC_ROOT.rglob("*.html")):
        ids.update(MARKUP_ID.findall(path.read_text(encoding="utf-8")))
    for path in sorted(STATIC_ROOT.rglob("*.js")):
        source = path.read_text(encoding="utf-8")
        ids.update(MARKUP_ID.findall(source))
        ids.update(element_id for _, element_id in ASSIGNED_ID.findall(source))
    return ids


def scan() -> dict[str, set[str]]:
    known = known_ids()
    findings: dict[str, set[str]] = {}
    for path in sorted(STATIC_ROOT.rglob("*.js")):
        missing = {
            element_id
            for _, element_id in LOOKUP.findall(path.read_text(encoding="utf-8"))
            if element_id not in known
        }
        if missing:
            findings[str(path.relative_to(STATIC_ROOT)).replace("\\", "/")] = missing
    return findings


def main() -> int:
    findings = scan()
    if not findings:
        print("DOM IDS OK: every looked-up element exists in index.html")
        return 0
    total = sum(len(v) for v in findings.values())
    print(f"MISSING ELEMENT IDS: {total} across {len(findings)} module(s)")
    for module, ids in findings.items():
        print(f"  {module}: {', '.join(sorted(ids))}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
