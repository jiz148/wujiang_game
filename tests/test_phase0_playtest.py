from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.phase0_playtest import build_report, prepare_batch, record_observation, render_report
from wujiang.web.analytics import AnalyticsStore


class Phase0PlaytestBatchTests(unittest.TestCase):
    def test_batch_refuses_accidental_overwrite_but_can_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            batch_root = Path(temporary_directory)
            first = prepare_batch("p0-round-01", batch_root=batch_root)

            with self.assertRaisesRegex(ValueError, "--resume"):
                prepare_batch("p0-round-01", batch_root=batch_root)

            resumed = prepare_batch("p0-round-01", resume=True, batch_root=batch_root)
            self.assertEqual(resumed["manifest"], first["manifest"])
            self.assertTrue(resumed["observations"].exists())

    def test_report_combines_anonymous_observations_and_clean_batch_analytics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            batch_root = Path(temporary_directory)
            paths = prepare_batch("p0-round-02", batch_root=batch_root)
            for index in range(10):
                device = "desktop" if index < 5 else "mobile"
                sample_number = index + 1 if device == "desktop" else index - 4
                completed = index < 7
                record_observation(
                    "p0-round-02",
                    sample_id=f"{device}-{sample_number:02d}",
                    device=device,
                    browser="Test Browser 1280x720" if device == "desktop" else "Test Browser 375x829",
                    completed=completed,
                    stuck_step="completed" if completed else "pierce",
                    external_rules=False,
                    restart=completed,
                    feedback="匿名反馈",
                    first_action_seconds=60 + index,
                    batch_root=batch_root,
                )
            analytics = AnalyticsStore(paths["analytics"])
            for index in range(10):
                session_id = f"anonymous-{index}"
                analytics.record("home_view", session_id, {"entry_state": "home"})
                analytics.record("tutorial_start", session_id, {"tutorial_id": "phase0_fixed"})
                analytics.record(
                    "first_effective_action",
                    session_id,
                    {"tutorial_id": "phase0_fixed", "action_type": "move", "duration_ms": 60_000 + index},
                )
                if index < 7:
                    analytics.record(
                        "tutorial_complete",
                        session_id,
                        {"tutorial_id": "phase0_fixed", "duration_ms": 600_000 + index},
                    )

            report = build_report("p0-round-02", batch_root=batch_root)

            self.assertEqual(report["observations"]["desktop"], 5)
            self.assertEqual(report["observations"]["mobile"], 5)
            self.assertEqual(report["observations"]["manual_completion_rate"], 0.7)
            self.assertEqual(report["analytics"]["metrics"]["tutorial_completion_rate"], 0.7)
            self.assertTrue(report["data_ready_for_phase_review"])
            self.assertTrue(report["owner_confirmation_required"])
            self.assertIn("数据是否已可提交 Phase 0 复盘：是", render_report(report))

    def test_observation_requires_device_scoped_anonymous_id_and_explicit_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            batch_root = Path(temporary_directory)
            prepare_batch("p0-round-03", batch_root=batch_root)

            with self.assertRaisesRegex(ValueError, "前缀"):
                record_observation(
                    "p0-round-03",
                    sample_id="desktop-01",
                    device="mobile",
                    browser="Test Browser",
                    completed=False,
                    stuck_step="select_unit",
                    external_rules=False,
                    restart=False,
                    batch_root=batch_root,
                )

            record_observation(
                "p0-round-03",
                sample_id="mobile-01",
                device="mobile",
                browser="Test Browser",
                completed=False,
                stuck_step="select_unit",
                external_rules=False,
                restart=False,
                batch_root=batch_root,
            )
            with self.assertRaisesRegex(ValueError, "--replace"):
                record_observation(
                    "p0-round-03",
                    sample_id="mobile-01",
                    device="mobile",
                    browser="Test Browser",
                    completed=False,
                    stuck_step="move",
                    external_rules=False,
                    restart=False,
                    batch_root=batch_root,
                )


if __name__ == "__main__":
    unittest.main()
