from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.heroes.registry import create_battle, create_hero  # noqa: E402
from wujiang.tools.match_audit import FindingRecorder, action_diagnostic, parse_roster, record_candidate_gap, run_match_audit  # noqa: E402
from wujiang.web.ai import difficulty_profile  # noqa: E402
from wujiang.tools.batch_match_audit import build_match_plan, run_batch_audit  # noqa: E402
from wujiang.tools.per_hero_ai_debug import build_per_hero_match_plan, run_per_hero_ai_debug  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize_unit_ids(value):
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if isinstance(item, str) and (key == "unit_id" or key.endswith("_unit_id")):
                head, separator, tail = item.rpartition("-")
                normalized[key] = f"{head}-#" if separator and tail.isdigit() else item
            elif isinstance(item, list) and (key == "target_unit_ids" or key.endswith("_unit_ids")):
                normalized[key] = [
                    f"{entry.rpartition('-')[0]}-#" if isinstance(entry, str) and entry.rpartition("-")[2].isdigit() else entry
                    for entry in item
                ]
            else:
                normalized[key] = normalize_unit_ids(item)
        return normalized
    if isinstance(value, list):
        return [normalize_unit_ids(item) for item in value]
    return value


class MatchAuditToolTests(unittest.TestCase):
    def test_parse_roster_accepts_comma_and_semicolon_separators(self) -> None:
        self.assertEqual(parse_roster("bard, ellie;dark_human"), ["bard", "ellie", "dark_human"])
        with self.assertRaises(ValueError):
            parse_roster(" , ; ")

    def test_run_match_audit_writes_trace_report_and_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "audit"
            result = run_match_audit(
                ["bard", "ellie"],
                ["dark_human", "fire_funeral"],
                seed=7,
                max_steps=8,
                output_dir=output_dir,
            )

            self.assertEqual(result.output_dir, output_dir)
            self.assertEqual(result.step_count, 8)
            self.assertTrue(result.manifest_path.exists())
            self.assertTrue(result.trace_path.exists())
            self.assertTrue(result.report_path.exists())
            self.assertTrue(result.findings_jsonl_path.exists())
            self.assertTrue(result.findings_markdown_path.exists())

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["team1"], ["bard", "ellie"])
            self.assertEqual(manifest["team2"], ["dark_human", "fire_funeral"])
            self.assertEqual(manifest["steps_executed"], result.step_count)

            trace = read_jsonl(result.trace_path)
            self.assertEqual(len(trace), result.step_count)
            self.assertIn("decision", trace[0])
            self.assertIn("state_delta", trace[0])
            self.assertIn("new_logs", trace[0])

            findings = read_jsonl(result.findings_jsonl_path)
            self.assertEqual(len(findings), result.finding_count)
            self.assertNotIn("ai_payload_generation_gap", {finding["category"] for finding in findings})
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("# Battle Audit Report", report)
            self.assertIn("## Findings", report)

    def test_match_audit_is_deterministic_for_same_seed_and_rosters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = run_match_audit(
                ["bard", "ellie"],
                ["dark_human", "fire_funeral"],
                seed=11,
                max_steps=5,
                output_dir=root / "first",
            )
            second = run_match_audit(
                ["bard", "ellie"],
                ["dark_human", "fire_funeral"],
                seed=11,
                max_steps=5,
                output_dir=root / "second",
            )

            first_trace = read_jsonl(first.trace_path)
            second_trace = read_jsonl(second.trace_path)
            self.assertEqual([event["reason"] for event in first_trace], [event["reason"] for event in second_trace])
            self.assertEqual(
                [normalize_unit_ids(event["payload"]) for event in first_trace],
                [normalize_unit_ids(event["payload"]) for event in second_trace],
            )

    def test_build_match_plan_covers_roster_with_deterministic_matches(self) -> None:
        first = build_match_plan(["a", "b", "c", "d", "e"], seed=3, rounds=1)
        second = build_match_plan(["a", "b", "c", "d", "e"], seed=3, rounds=1)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        covered = {code for match in first for code in match["team1"] + match["team2"]}
        self.assertEqual(covered, {"a", "b", "c", "d", "e"})

    def test_run_batch_audit_writes_summary_and_aggregated_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "batch"
            result = run_batch_audit(
                ["bard", "ellie", "dark_human", "fire_funeral"],
                seed=5,
                max_matches=1,
                max_steps=4,
                output_dir=output_dir,
            )

            self.assertEqual(result.match_count, 1)
            self.assertTrue(result.summary_path.exists())
            self.assertTrue(result.findings_path.exists())
            self.assertTrue(result.report_path.exists())
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["match_count"], 1)
            self.assertEqual(summary["hero_count"], 4)
            self.assertIn("matches", summary)
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("# Batch Match Audit Summary", report)

    def test_build_per_hero_match_plan_targets_each_requested_hero(self) -> None:
        first = build_per_hero_match_plan(
            ["ellie", "bard"],
            ["ellie", "bard", "dark_human", "fire_funeral", "elite_soldier"],
            seed=13,
            matches_per_hero=2,
        )
        second = build_per_hero_match_plan(
            ["ellie", "bard"],
            ["ellie", "bard", "dark_human", "fire_funeral", "elite_soldier"],
            seed=13,
            matches_per_hero=2,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        self.assertEqual([match["target"] for match in first], ["ellie", "ellie", "bard", "bard"])
        for match in first:
            self.assertEqual(match["team1"][0], match["target"])
            self.assertEqual(len(match["team1"]), 2)
            self.assertEqual(len(match["team2"]), 2)
            self.assertNotIn(match["target"], match["team2"])

    def test_run_per_hero_ai_debug_writes_summary_and_defect_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "per-hero"
            result = run_per_hero_ai_debug(
                ["bard"],
                seed=17,
                matches_per_hero=1,
                max_steps=4,
                output_dir=output_dir,
                label="test",
            )

            self.assertEqual(result.target_count, 1)
            self.assertEqual(result.match_count, 1)
            self.assertTrue(result.summary_path.exists())
            self.assertTrue(result.findings_path.exists())
            self.assertTrue(result.suspected_defects_path.exists())
            self.assertTrue(result.report_path.exists())
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["target_count"], 1)
            self.assertEqual(summary["matches_per_hero"], 1)
            self.assertIn("questionnaire", summary)
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("# Per-Hero AI Debug Summary", report)

    def test_candidate_gap_marks_insufficient_required_cells_as_info(self) -> None:
        findings = FindingRecorder()
        actor = create_hero("excel_r136", 1)

        record_candidate_gap(
            findings,
            step=1,
            source="ai_turn",
            actor=actor,
            diag={
                "kind": "skill",
                "code": "split",
                "raw_payload_count": 0,
                "legal_payload_count": 0,
                "effective_payload_count": 0,
                "candidate_count": 0,
                "preview_cell_count": 2,
                "preview_pattern_count": 0,
                "preview_target_count": 0,
                "preview_candidate_count": 0,
                "required_cells": 3,
            },
        )

        self.assertEqual(len(findings.items), 1)
        self.assertEqual(findings.items[0]["category"], "ai_payload_insufficient_selection")
        self.assertEqual(findings.items[0]["severity"], "info")

    def test_candidate_gap_ignores_attack_preview_without_targets(self) -> None:
        findings = FindingRecorder()
        actor = create_hero("excel_r030", 1)

        record_candidate_gap(
            findings,
            step=1,
            source="ai_turn",
            actor=actor,
            diag={
                "kind": "attack",
                "code": "attack",
                "raw_payload_count": 0,
                "legal_payload_count": 0,
                "effective_payload_count": 0,
                "candidate_count": 0,
                "preview_cell_count": 8,
                "preview_pattern_count": 0,
                "preview_target_count": 0,
                "preview_candidate_count": 0,
                "required_cells": 0,
            },
        )

        self.assertEqual(findings.items, [])

    def test_candidate_gap_ignores_heaven_punishment_without_preview_targets(self) -> None:
        findings = FindingRecorder()
        actor = create_hero("excel_r070", 1)

        record_candidate_gap(
            findings,
            step=1,
            source="ai_turn",
            actor=actor,
            diag={
                "kind": "skill",
                "code": "heaven_punishment",
                "raw_payload_count": 0,
                "legal_payload_count": 0,
                "effective_payload_count": 0,
                "candidate_count": 0,
                "preview_cell_count": 63,
                "preview_pattern_count": 49,
                "preview_target_count": 0,
                "preview_candidate_count": 0,
                "required_cells": 0,
            },
        )

        self.assertEqual(findings.items, [])

    def test_action_diagnostic_marks_repeat_utility_throttle_as_expected(self) -> None:
        battle = create_battle("erasure_apostle", "bard")
        apostle = next(unit for unit in battle.player_units(1) if not unit.is_summon)
        stealth = apostle.get_skill("stealth")
        stealth.uses_this_turn = 1
        action = next(
            item for item in battle.action_snapshot_for(apostle)["actions"] if item.get("code") == "stealth"
        )

        diagnostic = action_diagnostic(
            battle,
            apostle,
            action,
            difficulty_profile("standard"),
            instant_only=False,
        )

        self.assertEqual(diagnostic.get("candidate_count"), 0)
        self.assertEqual(diagnostic.get("expected_filter_reason"), "unlimited_nonhostile_repeat_throttle")


if __name__ == "__main__":
    unittest.main()
