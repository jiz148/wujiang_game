import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wujiang.tools.audit_review_packet import write_review_packet


class ReviewPacketTests(unittest.TestCase):
    def test_all_failures_and_info_findings_are_kept_and_missing_trace_is_flagged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = {
                "summary.json": {"finding_count": 2, "match_count": 1,
                                 "matches": [{"target": "hero", "output_dir": "missing"}]},
                "regression_tests.json": {"passed": False, "test_count": 3},
                "hero_metrics.json": {"heroes": []},
            }
            for name, value in data.items():
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            (root / "regression_tests.log").write_text("FAIL: first (Test)\ntraceback\nERROR: second (Test)\n", encoding="utf-8")
            findings = '{"category":"ordinary","severity":"info"}\n{"category":"serious"}\n'
            (root / "findings.jsonl").write_text(findings, encoding="utf-8")
            with patch("wujiang.tools.audit_review_packet.collect_changes", return_value={"errors": ["Git unavailable"]}):
                path = write_review_packet(root, root=root, existing_results=True)
            packet = json.loads((root / "review_packet.json").read_text(encoding="utf-8"))
            self.assertEqual([item["line"] for item in packet["failures"]], [1, 3])
            self.assertEqual(packet["finding_categories"], {"ordinary": 1, "serious": 1})
            self.assertFalse(packet["evidence"][0]["exists"])
            self.assertTrue(packet["integrity_issues"])
            self.assertIn("本命令未执行测试", path.read_text(encoding="utf-8"))
            self.assertIn("Git unavailable", path.read_text(encoding="utf-8"))
            self.assertEqual((root / "findings.jsonl").read_text(), findings)

    def test_summarize_missing_results_never_starts_audit(self):
        from wujiang.tools.all_hero_deep_audit import main
        with tempfile.TemporaryDirectory() as temporary:
            with patch("wujiang.tools.all_hero_deep_audit.run_all_hero_deep_audit") as audit:
                with patch("sys.stderr"), self.assertRaises(SystemExit) as error:
                    main(["--targets", "excel_r126", "--summarize-only", "--out", temporary])
                self.assertEqual(error.exception.code, 2)
                audit.assert_not_called()

    def test_failed_focused_regression_keeps_log_and_skips_ai_matches(self):
        from wujiang.tools.all_hero_deep_audit import run_all_hero_deep_audit

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)

            def failed_regression(run_dir, *, regression_filter):
                self.assertEqual(regression_filter, "NewBatch253")
                (run_dir / "regression_tests.log").write_text("ERROR: fixture\nfull traceback\n", encoding="utf-8")
                (run_dir / "regression_tests.json").write_text(
                    json.dumps({"passed": False, "return_code": 1, "test_count": 7}), encoding="utf-8")
                return {"passed": False, "return_code": 1, "test_count": 7}

            with patch("wujiang.tools.all_hero_deep_audit.public_hero_codes", return_value=["excel_r253"]), \
                 patch("wujiang.tools.all_hero_deep_audit.run_consolidated_regression",
                       side_effect=failed_regression), \
                 patch("wujiang.tools.all_hero_deep_audit.run_per_hero_ai_debug") as matches:
                self.assertEqual(run_all_hero_deep_audit(
                    ["excel_r253"], output_dir=output, regression_filter="NewBatch253"), output)
                matches.assert_not_called()

            self.assertEqual((output / "regression_tests.log").read_text(encoding="utf-8"),
                             "ERROR: fixture\nfull traceback\n")
            overview = json.loads((output / "deep_audit_overview.json").read_text(encoding="utf-8"))
            self.assertEqual(overview["ai_audit_skipped"], "focused_regression_failed")
            self.assertEqual(overview["match_count"], 0)
            self.assertTrue((output / "review_packet.md").exists())
