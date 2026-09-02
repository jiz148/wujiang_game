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

from wujiang.tactical.heroes.registry import create_battle, create_hero  # noqa: E402
from wujiang.tools.match_audit import FindingRecorder, action_diagnostic, parse_roster, record_candidate_gap, run_match_audit  # noqa: E402
from wujiang.tactical.rooms.ai import difficulty_profile  # noqa: E402
from wujiang.tools.batch_match_audit import build_match_plan, run_batch_audit  # noqa: E402
from wujiang.tools.per_hero_ai_debug import (  # noqa: E402
    build_per_hero_match_plan,
    completed_audit_manifest,
    run_per_hero_ai_debug,
)
from wujiang.tools.all_hero_deep_audit import (  # noqa: E402
    analyze_target,
    current_batch_output_dir,
    load_current_hero_codes,
    load_design_profiles,
    render_hero_review,
)


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
    def test_current_review_batch_is_loaded_from_explicit_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_path = Path(temp_dir) / "review.json"
            review_path.write_text(
                json.dumps(
                    {"current_hero_codes": ["excel_r126", "excel_r142", "excel_r126"]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.assertEqual(load_current_hero_codes(review_path), ["excel_r126", "excel_r142"])
            self.assertEqual(
                current_batch_output_dir(load_current_hero_codes(review_path)),
                Path("reports/current-hero-deep-audit/excel_r126--excel_r142"),
            )

    def test_current_review_batch_requires_explicit_nonempty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_path = Path(temp_dir) / "review.json"
            review_path.write_text(json.dumps({"heroes": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "current_hero_codes"):
                load_current_hero_codes(review_path)

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

    def test_resume_requires_complete_outputs_and_matching_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_dir = Path(temp_dir)
            manifest = {
                "seed": 23,
                "difficulty": "standard",
                "max_steps": 300,
                "steps_executed": 91,
                "team1": ["excel_r126", "bard"],
                "team2": ["ellie", "fire_funeral"],
            }
            (match_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            for filename in ("trace.jsonl", "battle_report.md", "findings.jsonl", "findings.md"):
                (match_dir / filename).write_text("", encoding="utf-8")

            reusable = completed_audit_manifest(
                match_dir,
                team1=manifest["team1"],
                team2=manifest["team2"],
                seed=23,
                difficulty="standard",
                max_steps=300,
            )
            self.assertEqual(reusable, manifest)
            self.assertIsNone(
                completed_audit_manifest(
                    match_dir,
                    team1=manifest["team1"],
                    team2=manifest["team2"],
                    seed=24,
                    difficulty="standard",
                    max_steps=300,
                )
            )

            (match_dir / "findings.md").unlink()
            self.assertIsNone(
                completed_audit_manifest(
                    match_dir,
                    team1=manifest["team1"],
                    team2=manifest["team2"],
                    seed=23,
                    difficulty="standard",
                    max_steps=300,
                )
            )

    def test_deep_audit_compares_trace_observations_with_design_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_dir = Path(temp_dir) / "match"
            match_dir.mkdir()
            event = {
                "actor": {"hero_code": "excel_r126", "name": "太阳神"},
                "payload": {"type": "skill", "skill_code": "sphinx_cannon"},
                "reason": "ai_turn",
                "success": True,
                "decision": {
                    "action_diagnostics": [
                        {
                            "kind": "skill",
                            "code": "sphinx_cannon",
                            "available": True,
                            "effective_payload_count": 3,
                        }
                    ]
                },
                "new_logs": ["太阳神 使用斯芬克斯炮。"],
                "state_delta": {},
            }
            (match_dir / "trace.jsonl").write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
            design = load_design_profiles(ROOT / "docs" / "武将设计思想.json")["excel_r126"]
            review = analyze_target(
                "excel_r126",
                {"code": "excel_r126", "name": "太阳神"},
                [
                    {
                        "target_match_index": 1,
                        "seed": 7,
                        "team1": ["excel_r126", "bard"],
                        "team2": ["ellie", "fire_funeral"],
                        "winner": 1,
                        "steps": 20,
                        "finding_count": 0,
                        "high_signal_count": 0,
                        "battle_report": str(match_dir / "battle_report.md"),
                        "output_dir": str(match_dir),
                    }
                ],
                [],
                design,
            )

            self.assertEqual(review["metrics"]["action_code_counts"]["sphinx_cannon"], 1)
            self.assertEqual(review["metrics"]["effective_action_decision_points"]["excel_r126:sphinx_cannon"], 1)
            self.assertIn("solar_judgment", review["expected_action_gaps"])
            self.assertNotIn("破坏了地形", review["expected_log_gaps"])
            self.assertTrue(review["design_baseline_available"])
            rendered = render_hero_review(review)
            self.assertIn("核心设计意图", rendered)
            self.assertIn("solar_judgment", rendered)

    def test_deep_audit_attributes_findings_to_the_actual_actor_not_the_planned_match_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_dir = Path(temp_dir) / "match"
            match_dir.mkdir()
            (match_dir / "trace.jsonl").write_text("", encoding="utf-8")
            design = load_design_profiles(ROOT / "docs" / "武将设计思想.json")["excel_r126"]
            finding = {
                "category": "action_error",
                "severity": "error",
                "target": "excel_r126",
                "attributed_code": "excel_r142",
                "target_match_index": 1,
                "actor": {"hero_code": "excel_r142", "name": "猫叔"},
            }
            review = analyze_target(
                "excel_r126",
                {"code": "excel_r126", "name": "太阳神"},
                [
                    {
                        "target_match_index": 1,
                        "seed": 7,
                        "team1": ["excel_r126", "excel_r142"],
                        "team2": ["bard", "ellie"],
                        "winner": 1,
                        "steps": 10,
                        "finding_count": 1,
                        "high_signal_count": 1,
                        "battle_report": str(match_dir / "battle_report.md"),
                        "output_dir": str(match_dir),
                    }
                ],
                [finding],
                design,
            )

            self.assertEqual(review["finding_count"], 0)
            self.assertEqual(review["high_signal_count"], 0)
            self.assertEqual(review["matches"][0]["high_signal_count"], 0)

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
