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
