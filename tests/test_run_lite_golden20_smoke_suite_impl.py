from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.tools.run_lite_golden20_smoke_suite_impl import (
    REPRESENTATIVE_CATEGORIES,
    run_golden20_smoke_suite,
    select_case_ids,
    summarize_suite,
)


class RunLiteGolden20SmokeSuiteImplTest(unittest.TestCase):
    def test_select_case_ids_uses_requested_values(self) -> None:
        case_ids = select_case_ids("ignored.jsonl", ["golden20_003", "golden20_011"])
        self.assertEqual(case_ids, ["golden20_003", "golden20_011"])

    def test_select_case_ids_picks_one_case_per_representative_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "cases.jsonl"
            rows = [
                {"id": "golden20_001", "video": "/tmp/1.mp4", "meta": {"category": "narration"}},
                {"id": "golden20_005", "video": "/tmp/5.mp4", "meta": {"category": "explanatory"}},
                {"id": "golden20_009", "video": "/tmp/9.mp4", "meta": {"category": "movie_explain"}},
            ]
            manifest.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
            selected = select_case_ids(str(manifest), [])
            self.assertEqual(selected, ["golden20_001", "golden20_005", "golden20_009"])
            self.assertEqual(len(REPRESENTATIVE_CATEGORIES), 3)

    def test_summarize_suite_counts_passed_cases(self) -> None:
        summary = summarize_suite(
            [
                {"case_id": "golden20_001", "ok": True, "runtime_ratio_vs_source": 2.0},
                {"case_id": "golden20_005", "ok": False, "runtime_ratio_vs_source": 4.0, "timed_out": True},
            ]
        )
        self.assertEqual(summary["total_cases"], 2)
        self.assertEqual(summary["passed_cases"], 1)
        self.assertFalse(summary["all_ok"])
        self.assertEqual(summary["timed_out_cases"], ["golden20_005"])
        self.assertEqual(summary["avg_runtime_ratio_vs_source"], 3.0)

    def test_run_golden20_smoke_suite_runs_each_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = root / "cases.jsonl"
            video1 = root / "golden20_001.mp4"
            video2 = root / "golden20_005.mp4"
            video1.write_text("", encoding="utf-8")
            video2.write_text("", encoding="utf-8")
            manifest.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "golden20_001", "video": str(video1), "meta": {"category": "narration"}}, ensure_ascii=False),
                        json.dumps({"id": "golden20_005", "video": str(video2), "meta": {"category": "explanatory"}}, ensure_ascii=False),
                    ]
                ),
                encoding="utf-8",
            )

            def fake_run_lite_pipeline_smoke(**kwargs):
                return {
                    "ok": True,
                    "return_code": 0,
                    "work_dir": str(kwargs["output_dir"]),
                    "runtime_ratio_vs_source": 2.0,
                    "timed_out": False,
                }

            with patch(
                "pipelines.tools.run_lite_golden20_smoke_suite_impl.run_lite_pipeline_smoke",
                side_effect=fake_run_lite_pipeline_smoke,
            ):
                summary = run_golden20_smoke_suite(
                    repo_root=root,
                    cases_manifest=str(manifest),
                    case_ids=["golden20_001", "golden20_005"],
                    out_root=root / "out",
                    config="configs/defaults.yaml",
                    preset="normal",
                    overrides_json='{"skip_tts": true}',
                    max_runtime_s=240,
                    skip_tts=True,
                    require_quality_report=True,
                    cleanup_artifacts=False,
                    log_max_kb=256,
                )

            self.assertTrue(summary["all_ok"])
            self.assertEqual(summary["case_ids"], ["golden20_001", "golden20_005"])


if __name__ == "__main__":
    unittest.main()
