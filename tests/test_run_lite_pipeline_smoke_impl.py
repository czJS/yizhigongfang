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

from pipelines.tools.run_lite_pipeline_smoke_impl import _resolve_video, build_smoke_command, run_lite_pipeline_smoke


class RunLitePipelineSmokeImplTest(unittest.TestCase):
    def test_resolve_video_from_manifest_case_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            video = root / "golden20_001.mp4"
            video.write_text("", encoding="utf-8")
            manifest = root / "cases.jsonl"
            manifest.write_text(
                json.dumps({"id": "golden20_001", "video": str(video)}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            resolved = _resolve_video("", str(manifest), "golden20_001")
            self.assertEqual(resolved, video.resolve())

    def test_build_smoke_command_includes_expected_args(self) -> None:
        repo_root = Path("/repo")
        cmd = build_smoke_command(
            repo_root=repo_root,
            video=Path("/tmp/input.mp4"),
            output_dir=Path("/tmp/out"),
            config="configs/defaults.yaml",
            preset="normal",
            overrides_json='{"skip_tts": true}',
            max_runtime_s=120,
            cleanup_artifacts=True,
            log_max_kb=256,
        )
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1], str((repo_root / "scripts" / "run_lite_e2e.py").resolve()))
        self.assertIn("--video", cmd)
        self.assertIn("--overrides-json", cmd)
        self.assertIn("--cleanup-artifacts", cmd)
        self.assertIn("--max-runtime-s", cmd)

    def test_run_lite_pipeline_smoke_writes_logs_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            out_dir = repo_root / "outputs" / "smoke"
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in (
                "audio.json",
                "chs.srt",
                "eng.srt",
                "tts_plan.json",
                "tts_full.wav",
                "output_en.mp4",
                "output_en_sub.mp4",
                "quality_report.json",
            ):
                path = out_dir / name
                if name.endswith(".json"):
                    payload = {"passed": True}
                    if name == "quality_report.json":
                        payload["metrics"] = {"source_duration_s": 52.0}
                    elif name == "audio.json":
                        payload = {"ok": True}
                    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                else:
                    path.write_text("ok", encoding="utf-8")
            (out_dir / "lite_run_meta.json").write_text(
                json.dumps({"return_code": 0, "elapsed_s": 104.0, "timed_out": False}, ensure_ascii=False),
                encoding="utf-8",
            )

            class _Proc:
                returncode = 0
                stdout = "runner ok"
                stderr = ""

            with patch("pipelines.tools.run_lite_pipeline_smoke_impl.subprocess.run", return_value=_Proc()):
                summary = run_lite_pipeline_smoke(
                    repo_root=repo_root,
                    video=Path("/tmp/input.mp4"),
                    output_dir=out_dir,
                    config="configs/defaults.yaml",
                    preset="normal",
                    overrides_json="",
                    max_runtime_s=0,
                    skip_tts=False,
                    require_quality_report=True,
                    cleanup_artifacts=False,
                    log_max_kb=256,
                )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["return_code"], 0)
            self.assertTrue((out_dir / "smoke_runner.stdout.log").exists())
            self.assertIn("--preset", summary["command"])
            self.assertEqual(summary["runtime_ratio_vs_source"], 2.0)
            self.assertFalse(summary["timed_out"])

    def test_run_lite_pipeline_smoke_fails_when_artifacts_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            out_dir = repo_root / "outputs" / "smoke"

            class _Proc:
                returncode = 0
                stdout = ""
                stderr = ""

            with patch("pipelines.tools.run_lite_pipeline_smoke_impl.subprocess.run", return_value=_Proc()):
                summary = run_lite_pipeline_smoke(
                    repo_root=repo_root,
                    video=Path("/tmp/input.mp4"),
                    output_dir=out_dir,
                    config="configs/defaults.yaml",
                    preset="normal",
                    overrides_json="",
                    max_runtime_s=0,
                    skip_tts=True,
                    require_quality_report=False,
                    cleanup_artifacts=False,
                    log_max_kb=256,
                )

            self.assertFalse(summary["ok"])
            self.assertIn("audio.json", summary["missing_artifacts"])

    def test_run_lite_pipeline_smoke_marks_timeout_from_run_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            out_dir = repo_root / "outputs" / "smoke"
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in ("audio.json", "chs.srt"):
                (out_dir / name).write_text("ok", encoding="utf-8")
            (out_dir / "quality_report.json").write_text(
                json.dumps({"passed": False, "metrics": {"source_duration_s": 60.0}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (out_dir / "lite_run_meta.json").write_text(
                json.dumps({"return_code": -9, "elapsed_s": 240.2, "timed_out": True}, ensure_ascii=False),
                encoding="utf-8",
            )

            class _Proc:
                returncode = 247
                stdout = ""
                stderr = ""

            with patch("pipelines.tools.run_lite_pipeline_smoke_impl.subprocess.run", return_value=_Proc()):
                summary = run_lite_pipeline_smoke(
                    repo_root=repo_root,
                    video=Path("/tmp/input.mp4"),
                    output_dir=out_dir,
                    config="configs/defaults.yaml",
                    preset="normal",
                    overrides_json="",
                    max_runtime_s=240,
                    skip_tts=True,
                    require_quality_report=True,
                    cleanup_artifacts=False,
                    log_max_kb=256,
                )

            self.assertTrue(summary["timed_out"])
            self.assertEqual(summary["runtime_ratio_vs_source"], 4.0033)
            self.assertEqual(summary["failure_category"], "timeout")


if __name__ == "__main__":
    unittest.main()
