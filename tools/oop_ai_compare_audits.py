"""Print the first semantic difference between two bounded AI audit outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def normalized_finding(line: str) -> dict:
    return {key: value for key, value in json.loads(line).items()
            if key not in {"battle_report", "match_output_dir"}}


def first_difference(left, right, path=""):
    if type(left) is not type(right):
        return path, left, right
    if isinstance(left, dict):
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                return f"{path}.{key}", left.get(key), right.get(key)
            result = first_difference(left[key], right[key], f"{path}.{key}")
            if result is not None:
                return result
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path}.length", len(left), len(right)
        for index, (a, b) in enumerate(zip(left, right)):
            result = first_difference(a, b, f"{path}[{index}]")
            if result is not None:
                return result
        return None
    return (path, left, right) if left != right else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    before, after = args.before, args.after
    different = False
    expected = list(sorted(before.glob("*/*/trace.jsonl")))
    for path in expected:
        counterpart = after / path.relative_to(before)
        if not counterpart.is_file():
            print("missing", counterpart)
            different = True
            continue
        left = path.read_text(encoding="utf-8").splitlines()
        right = counterpart.read_text(encoding="utf-8").splitlines()
        if left == right:
            continue
        different = True
        print("trace differs", path.relative_to(before), len(left), len(right))
        for index, (first, second) in enumerate(zip(left, right)):
            if first != second:
                a, b = json.loads(first), json.loads(second)
                keys = sorted(key for key in a.keys() | b.keys() if a.get(key) != b.get(key))
                print("line", index + 1, "keys", keys)
                for key in keys[:8]:
                    print("   first nested difference", first_difference(a.get(key), b.get(key)))
                    if key == "decision" and isinstance(a.get(key), dict):
                        for label, entry in (("before", a[key]), ("after", b[key])):
                            print("  ", label, "diagnostics", [
                                (item.get("code"), item.get("raw_payload_count"),
                                 item.get("legal_payload_count"),
                                 item.get("effective_payload_count"),
                                 item.get("candidate_count"))
                                for item in entry.get("action_diagnostics", [])])
                break
    if len(list(after.glob("*/*/trace.jsonl"))) != len(expected):
        print("trace count differs")
        different = True
    left = [normalized_finding(line) for line in (before / "findings.jsonl").read_text(encoding="utf-8").splitlines()]
    right = [normalized_finding(line) for line in (after / "findings.jsonl").read_text(encoding="utf-8").splitlines()]
    print("findings", len(left), len(right))
    different |= left != right
    for index, (first, second) in enumerate(zip(left, right)):
        if first != second:
            print("first finding difference", index, repr(first)[:800], repr(second)[:800])
            break
    return int(different)


if __name__ == "__main__":
    raise SystemExit(main())
