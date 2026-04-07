from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.lib.lite_resume import (
    collect_missing_lite_resume_artifacts,
    normalize_lite_resume_from,
    required_lite_resume_artifacts,
    should_run_lite_asr,
    should_run_lite_mt,
    should_run_lite_tts,
)


class LiteResumeTest(unittest.TestCase):
    def test_normalize_lite_resume_from_accepts_case_and_whitespace(self) -> None:
        self.assertEqual(normalize_lite_resume_from(" TTS "), "tts")
        self.assertIsNone(normalize_lite_resume_from(None))
        self.assertIsNone(normalize_lite_resume_from(" "))

    def test_normalize_lite_resume_from_rejects_invalid_stage(self) -> None:
        with self.assertRaises(ValueError):
            normalize_lite_resume_from("translate")

    def test_required_lite_resume_artifacts_match_stage_boundaries(self) -> None:
        self.assertEqual(required_lite_resume_artifacts("asr"), [])
        self.assertEqual(required_lite_resume_artifacts("mt"), ["audio.wav", "audio.json"])
        self.assertEqual(required_lite_resume_artifacts("tts"), ["audio.wav", "audio.json", "eng.srt"])
        self.assertEqual(required_lite_resume_artifacts("mux"), ["audio.wav", "audio.json", "eng.srt", "tts_full.wav"])

    def test_should_run_helpers_match_pipeline_expectations(self) -> None:
        self.assertTrue(should_run_lite_asr(None))
        self.assertFalse(should_run_lite_asr("mt"))
        self.assertTrue(should_run_lite_mt("mt"))
        self.assertFalse(should_run_lite_mt("tts"))
        self.assertTrue(should_run_lite_tts("tts"))
        self.assertFalse(should_run_lite_tts("mux"))

    def test_collect_missing_lite_resume_artifacts_uses_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "audio.wav").write_bytes(b"wav")
            (work_dir / "audio.json").write_text("[]", encoding="utf-8")
            missing = collect_missing_lite_resume_artifacts(work_dir, "tts")
            self.assertEqual(missing, ["eng.srt"])


if __name__ == "__main__":
    unittest.main()
