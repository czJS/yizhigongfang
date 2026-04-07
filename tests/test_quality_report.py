from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.lib.quality.quality_report import generate_quality_report


def _write_srt(path: Path, text: str, *, end_s: float = 5.0) -> None:
    path.write_text(
        f"1\n00:00:00,000 --> 00:00:{int(end_s):02d},000\n{text}\n",
        encoding="utf-8",
    )


class QualityReportTest(unittest.TestCase):
    def test_lite_mode_uses_stricter_line_length_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            _write_srt(work_dir / "eng.srt", "This subtitle line is intentionally longer than forty-two characters")

            report = generate_quality_report(
                task_id="lite:test",
                mode="lite",
                work_dir=work_dir,
                source_video=None,
                cfg={"quality_gates": {"max_chars_per_line": 80, "max_cps": 20.0}},
            )

            self.assertEqual(report["checks"]["line_length"]["max_chars_per_line"], 42)
            self.assertGreater(report["checks"]["line_length"]["hits_n"], 0)

    def test_quality_mode_keeps_configured_line_length_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            _write_srt(work_dir / "eng.srt", "This subtitle line is intentionally longer than forty-two characters")

            report = generate_quality_report(
                task_id="quality:test",
                mode="quality",
                work_dir=work_dir,
                source_video=None,
                cfg={"quality_gates": {"max_chars_per_line": 80, "max_cps": 20.0}},
            )

            self.assertEqual(report["checks"]["line_length"]["max_chars_per_line"], 80)
            self.assertEqual(report["checks"]["line_length"]["hits_n"], 0)


if __name__ == "__main__":
    unittest.main()
