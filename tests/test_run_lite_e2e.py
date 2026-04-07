from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "run_lite_e2e.py"
SPEC = importlib.util.spec_from_file_location("run_lite_e2e_module", MODULE_PATH)
assert SPEC and SPEC.loader
run_lite_e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_lite_e2e)


class RunLiteE2ETest(unittest.TestCase):
    def test_pick_runtime_python_preserves_venv_path_without_resolving_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            bin_dir = repo_root / ".venv-lite-arm64" / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            runtime_python = bin_dir / "python"
            runtime_python.write_text("#!/bin/sh\n", encoding="utf-8")
            picked = run_lite_e2e._pick_runtime_python(
                repo_root,
                {"paths": {"lite_runtime_python": ".venv-lite-arm64/bin/python"}},
                {},
            )
            self.assertEqual(picked, str(runtime_python))

    def test_hydrate_effective_runtime_paths_backfills_vad_model(self) -> None:
        hydrated = run_lite_e2e._hydrate_effective_runtime_paths(
            {"vad_enable": True},
            {"vad_model": "assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin"},
        )
        self.assertEqual(
            hydrated["vad_model"],
            "assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin",
        )

    def test_to_cli_args_includes_vad_model_after_hydration(self) -> None:
        effective = run_lite_e2e._hydrate_effective_runtime_paths(
            {
                "vad_enable": True,
                "vad_threshold": 0.5,
                "vad_min_dur": 0.18,
                "sample_rate": 16000,
                "whispercpp_beam_size": 5,
                "mt_batch_enable": True,
                "mt_batch_size": 8,
            },
            {"vad_model": "assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin"},
        )
        args = run_lite_e2e._to_cli_args(effective, ROOT)
        self.assertIn("--vad-enable", args)
        self.assertIn("--vad-model", args)
        self.assertIn("--whispercpp-beam-size", args)
        self.assertIn("--mt-batch-enable", args)
        self.assertIn("--mt-batch-size", args)
        idx = args.index("--vad-model")
        self.assertTrue(args[idx + 1].endswith("assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin"))

    def test_align_lite_subtitle_policy_keeps_stricter_lite_defaults(self) -> None:
        aligned = run_lite_e2e._align_lite_subtitle_policy({}, {"quality_gates": {"max_chars_per_line": 80, "max_cps": 20.0}})
        self.assertEqual(aligned["subtitle_max_chars_per_line"], 42)
        self.assertEqual(aligned["subtitle_wrap_max_lines"], 2)
        self.assertEqual(aligned["subtitle_max_cps"], 20.0)

    def test_align_lite_subtitle_policy_honors_tighter_quality_gates(self) -> None:
        aligned = run_lite_e2e._align_lite_subtitle_policy({}, {"quality_gates": {"max_chars_per_line": 36, "max_cps": 18.0}})
        self.assertEqual(aligned["subtitle_max_chars_per_line"], 36)
        self.assertEqual(aligned["subtitle_max_cps"], 18.0)

    def test_align_lite_subtitle_policy_keeps_delivery_defaults(self) -> None:
        aligned = run_lite_e2e._align_lite_subtitle_policy({}, {"quality_gates": {"max_chars_per_line": 80, "max_cps": 20.0}})
        self.assertEqual(aligned["tts_plan_safety_margin"], 0.02)
        self.assertNotIn("mux_tail_pad_max_s", aligned)

    def test_compute_effective_timeout_uses_dynamic_budget_for_longer_source(self) -> None:
        with patch.object(run_lite_e2e, "_probe_duration_seconds", return_value=60.0):
            effective_timeout_s, source_duration_s, timeout_mode = run_lite_e2e._compute_effective_timeout_s(240, source_video=ROOT / "dummy.mp4")
        self.assertEqual(effective_timeout_s, 273)
        self.assertEqual(source_duration_s, 60.0)
        self.assertEqual(timeout_mode, "dynamic")

    def test_compute_effective_timeout_keeps_fixed_budget_when_already_large_enough(self) -> None:
        with patch.object(run_lite_e2e, "_probe_duration_seconds", return_value=52.0):
            effective_timeout_s, source_duration_s, timeout_mode = run_lite_e2e._compute_effective_timeout_s(240, source_video=ROOT / "dummy.mp4")
        self.assertEqual(effective_timeout_s, 240)
        self.assertEqual(source_duration_s, 52.0)
        self.assertEqual(timeout_mode, "fixed")

    def test_compute_effective_timeout_handles_disabled_timeout(self) -> None:
        effective_timeout_s, source_duration_s, timeout_mode = run_lite_e2e._compute_effective_timeout_s(0, source_video=ROOT / "dummy.mp4")
        self.assertEqual(effective_timeout_s, 0)
        self.assertIsNone(source_duration_s)
        self.assertEqual(timeout_mode, "disabled")

    def test_expected_timeout_grace_artifacts_match_mux_complete_milestone(self) -> None:
        expected = run_lite_e2e._expected_timeout_grace_artifacts({})
        self.assertEqual(
            expected,
            [
                "audio.json",
                "chs.srt",
                "eng.srt",
                "tts_plan.json",
                "tts_full.wav",
                "output_en.mp4",
            ],
        )

    def test_has_timeout_grace_artifacts_requires_mux_complete_milestone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            for name in run_lite_e2e._expected_timeout_grace_artifacts({}):
                (out_dir / name).write_text("ok", encoding="utf-8")
            self.assertTrue(run_lite_e2e._has_timeout_grace_artifacts(out_dir, {}))

    def test_has_timeout_grace_artifacts_rejects_pre_mux_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            for name in ["audio.json", "chs.srt", "eng.srt", "tts_plan.json", "tts_full.wav"]:
                (out_dir / name).write_text("ok", encoding="utf-8")
            self.assertFalse(run_lite_e2e._has_timeout_grace_artifacts(out_dir, {}))

    def test_has_timeout_grace_artifacts_respects_skip_tts_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            for name in ["audio.json", "chs.srt", "eng.srt"]:
                (out_dir / name).write_text("ok", encoding="utf-8")
            self.assertTrue(run_lite_e2e._has_timeout_grace_artifacts(out_dir, {"skip_tts": True}))
            self.assertFalse(run_lite_e2e._has_timeout_grace_artifacts(out_dir, {}))


if __name__ == "__main__":
    unittest.main()
