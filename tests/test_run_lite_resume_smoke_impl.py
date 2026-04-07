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

from pipelines.tools.run_lite_resume_smoke_impl import _build_resume_overrides, run_lite_resume_smoke


class RunLiteResumeSmokeImplTest(unittest.TestCase):
    def test_build_resume_overrides_forces_resume_from_and_enables_tts_when_needed(self) -> None:
        raw = _build_resume_overrides('{"skip_tts": true}', "tts", '{"foo": 1}', skip_tts=False)
        doc = json.loads(raw)
        self.assertEqual(doc["resume_from"], "tts")
        self.assertFalse(doc["skip_tts"])
        self.assertEqual(doc["foo"], 1)

    def test_build_resume_overrides_respects_skip_tts_contract_when_requested(self) -> None:
        raw = _build_resume_overrides('{"skip_tts": true}', "tts", '{"foo": 1}', skip_tts=True)
        doc = json.loads(raw)
        self.assertEqual(doc["resume_from"], "tts")
        self.assertTrue(doc["skip_tts"])
        self.assertEqual(doc["foo"], 1)

    def test_run_lite_resume_smoke_fails_fast_when_precheck_artifacts_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            out_dir = repo_root / "outputs" / "resume"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "audio.wav").write_bytes(b"wav")
            (out_dir / "audio.json").write_text("[]", encoding="utf-8")

            summary = run_lite_resume_smoke(
                repo_root=repo_root,
                video=Path("/tmp/input.mp4"),
                output_dir=out_dir,
                config="configs/defaults.yaml",
                preset="normal",
                base_overrides_json="",
                resume_from="tts",
                resume_overrides_json="",
                max_runtime_s=0,
                skip_tts=False,
                require_quality_report=False,
                cleanup_artifacts=False,
                log_max_kb=256,
                prepare_base=False,
            )

            self.assertFalse(summary["ok"])
            self.assertEqual(summary["failure_category"], "resume_precheck_failed")
            self.assertEqual(summary["precheck_missing_artifacts"], ["eng.srt"])

    def test_run_lite_resume_smoke_runs_base_then_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            out_dir = repo_root / "outputs" / "resume"

            calls = []

            def _fake_run(**kwargs):
                calls.append(kwargs)
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True, exist_ok=True)
                for name in ("audio.json", "audio.wav", "chs.srt", "eng.srt", "tts_plan.json", "tts_full.wav", "output_en.mp4", "output_en_sub.mp4"):
                    (output_dir / name).write_text("ok", encoding="utf-8")
                return {"ok": True, "failure_category": None, "failed_stage_guess": None}

            with patch("pipelines.tools.run_lite_resume_smoke_impl.run_lite_pipeline_smoke", side_effect=_fake_run):
                summary = run_lite_resume_smoke(
                    repo_root=repo_root,
                    video=Path("/tmp/input.mp4"),
                    output_dir=out_dir,
                    config="configs/defaults.yaml",
                    preset="normal",
                    base_overrides_json='{"skip_tts": true}',
                    resume_from="tts",
                    resume_overrides_json='{"foo": "bar"}',
                    max_runtime_s=0,
                    skip_tts=False,
                    require_quality_report=False,
                    cleanup_artifacts=False,
                    log_max_kb=256,
                    prepare_base=True,
                )

            self.assertTrue(summary["ok"])
            self.assertEqual(len(calls), 2)
            resume_doc = json.loads(calls[1]["overrides_json"])
            self.assertEqual(resume_doc["resume_from"], "tts")
            self.assertFalse(resume_doc["skip_tts"])
            self.assertEqual(resume_doc["foo"], "bar")

    def test_run_lite_resume_smoke_keeps_skip_tts_when_resume_contract_is_subtitle_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            out_dir = repo_root / "outputs" / "resume"

            calls = []

            def _fake_run(**kwargs):
                calls.append(kwargs)
                output_dir = kwargs["output_dir"]
                output_dir.mkdir(parents=True, exist_ok=True)
                for name in ("audio.json", "audio.wav", "chs.srt", "eng.srt", "tts_plan.json"):
                    (output_dir / name).write_text("ok", encoding="utf-8")
                return {"ok": True, "failure_category": None, "failed_stage_guess": None}

            with patch("pipelines.tools.run_lite_resume_smoke_impl.run_lite_pipeline_smoke", side_effect=_fake_run):
                summary = run_lite_resume_smoke(
                    repo_root=repo_root,
                    video=Path("/tmp/input.mp4"),
                    output_dir=out_dir,
                    config="configs/defaults.yaml",
                    preset="normal",
                    base_overrides_json='{"skip_tts": true}',
                    resume_from="tts",
                    resume_overrides_json='{"foo": "bar"}',
                    max_runtime_s=0,
                    skip_tts=True,
                    require_quality_report=False,
                    cleanup_artifacts=False,
                    log_max_kb=256,
                    prepare_base=True,
                )

            self.assertTrue(summary["ok"])
            self.assertEqual(len(calls), 2)
            resume_doc = json.loads(calls[1]["overrides_json"])
            self.assertEqual(resume_doc["resume_from"], "tts")
            self.assertTrue(resume_doc["skip_tts"])
            self.assertEqual(resume_doc["foo"], "bar")


if __name__ == "__main__":
    unittest.main()
