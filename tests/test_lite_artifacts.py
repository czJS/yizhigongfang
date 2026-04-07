from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.lib.lite_artifacts import (
    BASE_REQUIRED_LITE_ARTIFACTS,
    FULL_REQUIRED_LITE_ARTIFACTS,
    collect_lite_cleanup_targets,
    list_existing_lite_artifacts,
)


class LiteArtifactsTest(unittest.TestCase):
    def test_list_existing_lite_artifacts_returns_known_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "audio.json").write_text("{}", encoding="utf-8")
            (work_dir / "eng.srt").write_text("ok", encoding="utf-8")
            (work_dir / "random.txt").write_text("nope", encoding="utf-8")

            found = list_existing_lite_artifacts(work_dir)
            self.assertEqual([item["name"] for item in found], ["audio.json", "eng.srt"])

    def test_collect_lite_cleanup_targets_preserves_deliverables(self) -> None:
        targets = collect_lite_cleanup_targets(
            include_resume=True,
            include_review=True,
            include_diagnostics=True,
        )
        self.assertIn("audio.json", targets)
        self.assertIn("review_audit.jsonl", targets)
        self.assertNotIn("eng.srt", targets)
        self.assertNotIn("chs.srt", targets)

    def test_required_artifact_sets_match_smoke_contract_shape(self) -> None:
        self.assertEqual(BASE_REQUIRED_LITE_ARTIFACTS, ["audio.json", "chs.srt", "eng.srt"])
        self.assertEqual(FULL_REQUIRED_LITE_ARTIFACTS, ["tts_plan.json", "tts_full.wav", "output_en.mp4", "output_en_sub.mp4"])


if __name__ == "__main__":
    unittest.main()
