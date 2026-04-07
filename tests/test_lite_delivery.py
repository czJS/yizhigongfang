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

from pipelines.lib.asr.lite_asr import Segment
from pipelines.lib.media.lite_delivery import (
    apply_subtitle_postprocess,
    apply_tts_plan,
    maybe_build_display_subtitles,
)


class LiteDeliveryTest(unittest.TestCase):
    def test_apply_subtitle_postprocess_normalizes_and_wraps(self) -> None:
        segs = [Segment(start=0.0, end=1.0, text="中", translation="Hello   world  from\nCursor testing")]
        meta = apply_subtitle_postprocess(
            segs,
            wrap_enable=True,
            max_chars_per_line=12,
            max_lines=2,
        )
        self.assertEqual(meta["normalized"], 1)
        self.assertEqual(meta["wrapped"], 1)
        self.assertIn("\n", segs[0].translation or "")

    def test_apply_tts_plan_keeps_non_overlapping_timeline(self) -> None:
        segs = [
            Segment(start=0.0, end=0.5, text="一", translation="This is a fairly long English subtitle line."),
            Segment(start=0.6, end=1.0, text="二", translation="This is another long subtitle line for planning."),
        ]
        doc = apply_tts_plan(
            segs,
            video_path=Path("/nonexistent.mp4"),
            max_speed=1.1,
            wps=2.6,
            min_dur=1.5,
            max_cps=20.0,
            mux_slow_max_ratio=1.18,
            tts_plan_safety_margin=0.02,
            tts_fit_min_words=3,
        )
        self.assertTrue(doc["enabled"])
        self.assertEqual(len(doc["plans"]), 2)
        self.assertGreaterEqual(segs[1].start, segs[0].end)

    def test_apply_tts_plan_cap_end_uses_video_only_budget(self) -> None:
        segs = [Segment(start=0.0, end=1.0, text="一", translation="This is a long subtitle line for timing pressure.")]
        with patch("pipelines.lib.media.lite_delivery.probe_duration_s", return_value=60.0):
            doc = apply_tts_plan(
                segs,
                video_path=Path("/tmp/video.mp4"),
                max_speed=1.1,
                wps=2.6,
                min_dur=1.5,
                max_cps=20.0,
                mux_slow_max_ratio=1.18,
                tts_plan_safety_margin=0.02,
                tts_fit_min_words=3,
            )
        self.assertNotIn("tail_pad_max_s", doc["params"])
        self.assertGreaterEqual(doc["params"]["cap_end"], 70.78)
        self.assertAlmostEqual(doc["params"]["cap_end"], 74.38)
        self.assertEqual(doc["source_duration_s"], 60.0)
        self.assertIn("plan_end_s", doc)

    def test_maybe_build_display_subtitles_writes_artifacts(self) -> None:
        segs = [
            Segment(start=0.0, end=1.5, text="", translation="A very long English subtitle line that should be wrapped for display."),
            Segment(start=1.6, end=3.0, text="", translation="Another readable subtitle line for display."),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            display_srt = root / "display.srt"
            meta_json = root / "display_meta.json"
            ok = maybe_build_display_subtitles(
                video_path=Path("/nonexistent.mp4"),
                seg_en=segs,
                display_srt=display_srt,
                display_meta_json=meta_json,
                display_srt_enable=True,
                display_use_for_embed=False,
                max_chars_per_line=24,
                max_lines=2,
                merge_enable=False,
                split_enable=True,
            )
            self.assertTrue(ok)
            self.assertTrue(display_srt.exists())
            self.assertTrue(meta_json.exists())
            meta = json.loads(meta_json.read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(meta["display_items"]), 2)


if __name__ == "__main__":
    unittest.main()
