from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.tools.run_lite_smoke_impl import collect_missing_artifacts, summarize_lite_smoke


class RunLiteSmokeImplTest(unittest.TestCase):
    def test_collect_missing_artifacts_for_full_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "audio.json").write_text("{}", encoding="utf-8")
            missing = collect_missing_artifacts(work_dir, skip_tts=False)
            self.assertIn("chs.srt", missing)
            self.assertIn("tts_full.wav", missing)
            self.assertIn("output_en_sub.mp4", missing)

    def test_summarize_lite_smoke_passes_for_subtitle_only_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            for name in ("audio.json", "chs.srt", "eng.srt"):
                (work_dir / name).write_text("ok", encoding="utf-8")
            summary = summarize_lite_smoke(work_dir, skip_tts=True, require_quality_report=False)
            self.assertTrue(summary["ok"])
            self.assertEqual(summary["missing_artifacts"], [])

    def test_summarize_lite_smoke_requires_quality_report_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            for name in (
                "audio.json",
                "chs.srt",
                "eng.srt",
                "tts_plan.json",
                "tts_full.wav",
                "output_en.mp4",
                "output_en_sub.mp4",
            ):
                (work_dir / name).write_text("ok", encoding="utf-8")
            summary = summarize_lite_smoke(work_dir, skip_tts=False, require_quality_report=True)
            self.assertFalse(summary["ok"])
            self.assertFalse(summary["quality_report_exists"])

    def test_summarize_lite_smoke_includes_runtime_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            for name in ("audio.json", "chs.srt", "eng.srt"):
                (work_dir / name).write_text("ok", encoding="utf-8")
            (work_dir / "lite_run_meta.json").write_text('{"return_code": 0, "elapsed_s": 104.0, "timed_out": false}', encoding="utf-8")
            (work_dir / "quality_report.json").write_text(
                '{"passed": true, "metrics": {"source_duration_s": 52.0}}',
                encoding="utf-8",
            )
            summary = summarize_lite_smoke(work_dir, skip_tts=True, require_quality_report=True)
            self.assertEqual(summary["run_return_code"], 0)
            self.assertEqual(summary["run_elapsed_s"], 104.0)
            self.assertFalse(summary["run_timed_out"])
            self.assertEqual(summary["source_duration_s"], 52.0)
            self.assertEqual(summary["runtime_ratio_vs_source"], 2.0)
            self.assertIsNone(summary["failure_category"])

    def test_summarize_lite_smoke_diagnoses_timeout_and_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            for name in ("audio.json", "chs.srt"):
                (work_dir / name).write_text("ok", encoding="utf-8")
            (work_dir / "lite_run_meta.json").write_text(
                '{"return_code": -9, "elapsed_s": 240.2, "timed_out": true}',
                encoding="utf-8",
            )
            summary = summarize_lite_smoke(work_dir, skip_tts=True, require_quality_report=True)
            self.assertEqual(summary["failure_category"], "timeout")
            self.assertEqual(summary["failed_stage_guess"], "mt")
            self.assertIn("quality_report_missing", summary["failure_reasons"])


if __name__ == "__main__":
    unittest.main()
