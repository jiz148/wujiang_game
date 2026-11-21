"""Print compact, read-only source and audit context for one new-hero batch.

No game module is imported and no behavior test or AI match is run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from check_new_hero_candidates import ROOT, assigned_literal


XML_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def saved_answers(codes: set[str]) -> dict[str, list[tuple[str, str, str, str]]]:
    """Read the relevant question, options, and saved answer without dumping the workbook."""
    wanted = {prefix: code for code in codes
              for prefix in (f"R{int(code.rsplit('r', 1)[1])}-",
                             f"R{int(code.rsplit('r', 1)[1]):03d}-")}
    answers: dict[str, list[tuple[str, str, str, str]]] = {code: [] for code in codes}
    workbook = ROOT / "docs/武将实现问题清单.xlsx"
    if not workbook.exists():
        return answers
    with zipfile.ZipFile(workbook) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in entry.findall(".//x:t", XML_NS))
                      for entry in root.findall("x:si", XML_NS)]
        for name in archive.namelist():
            if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                continue
            for row in ET.fromstring(archive.read(name)).findall(".//x:sheetData/x:row", XML_NS):
                cells: dict[str, str] = {}
                for cell in row.findall("x:c", XML_NS):
                    ref = cell.get("r", "")
                    column = re.match(r"[A-Z]+", ref)
                    if column is None or column.group() not in {"C", "D", "E", "F"}:
                        continue
                    value = cell.find("x:v", XML_NS)
                    inline = cell.find("x:is", XML_NS)
                    if value is not None:
                        text = shared[int(value.text)] if cell.get("t") == "s" else value.text or ""
                    else:
                        text = "".join(node.text or "" for node in inline.findall(".//x:t", XML_NS)) if inline is not None else ""
                    cells[column.group()] = text.strip()
                identifier = cells.get("C", "")
                for prefix, code in wanted.items():
                    if identifier.startswith(prefix):
                        answers[code].append((identifier, cells.get("D", ""), cells.get("E", ""), cells.get("F", "")))
                        break
    return answers


def report_excerpt(path: Path) -> None:
    if not path.is_dir():
        print(f"Report not found: {path}")
        return
    log = path / "regression_tests.log"
    fatal = path / "fatal_error.log"
    if log.exists():
        content = log.read_text(encoding="utf-8", errors="replace")
        result = re.search(r"Ran \d+ tests?[^\n]*\n\n(?:OK|FAILED[^\n]*)", content)
        print(f"Regression: {result.group().replace(chr(10), ' ') if result else 'see log'}")
        for line in content.splitlines():
            if line.startswith(("FAIL:", "ERROR:")):
                print(f"  {line}")
        if "FAILED" in content:
            lines = content.splitlines()
            for index, line in enumerate(lines):
                if line.startswith(("AssertionError:", "ImportError:", "NameError:", "TypeError:", "AttributeError:")):
                    print(f"  {lines[index - 1].strip() if index else ''} {line.strip()}")
    if fatal.exists():
        print("Fatal error tail:")
        for line in fatal.read_text(encoding="utf-8", errors="replace").splitlines()[-24:]:
            print(f"  {line}")
    summary = path / "summary.json"
    if summary.exists():
        data = json.loads(summary.read_text(encoding="utf-8"))
        for key in ("target_count", "match_count", "high_signal_count", "findings_count"):
            if key in data:
                print(f"{key}: {data[key]}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="+", help="Exact source codes such as excel_r354")
    parser.add_argument("--report", type=Path, help="Existing user-run report directory to summarize")
    args = parser.parse_args()
    specs = assigned_literal(ROOT / "src/wujiang/tactical/heroes/excel_roster_data.py", "EXCEL_HERO_SPECS")
    public = assigned_literal(ROOT / "src/wujiang/tactical/heroes/excel_roster.py", "IMPLEMENTED_EXCEL_HERO_CODES")
    by_code = {entry["code"]: entry for entry in specs}
    archive = json.loads((ROOT / "docs/武将设计思想.json").read_text(encoding="utf-8-sig"))
    designs = {entry["code"]: entry for entry in archive["heroes"]}
    answers = saved_answers(set(args.codes))
    for code, design in designs.items():
        if code in answers and "questionnaire_items" in design:
            current_ids = {item["id"] for item in design["questionnaire_items"]}
            answers[code] = [entry for entry in answers[code] if entry[0] in current_ids]
    for code in args.codes:
        spec = by_code.get(code)
        if spec is None:
            print(f"{code}: unknown source code")
            continue
        print(f"{code} | row {spec['source_row']} | {spec['name']} | {'PUBLIC' if code in public else 'UNIMPLEMENTED'}")
        print(f"  level {spec['level']} {spec['attribute']} {spec['race']} {spec['role']} | "
              f"攻{spec['attack']} 守{spec['defense']} 速{spec['speed']} 范{spec['range']} 魔{spec['mana']}")
        print(f"  skills: {spec['raw_skill_text']}")
        print(f"  traits: {spec['raw_trait_text']}")
        print(f"  saved questions: {len(answers[code])}")
        for identifier, question, options, answer in answers[code]:
            print(f"    {identifier}: {question} | {options} | answer={answer or '(none)'}")
        print(f"  design: {designs[code].get('review_status', 'present') if code in designs else 'missing'}")
    print(f"Public Excel: {len(public)}/{len(by_code)}; remaining: {len(by_code) - len(public)}")
    if args.report:
        report_excerpt(args.report if args.report.is_absolute() else ROOT / args.report)


if __name__ == "__main__":
    main()
