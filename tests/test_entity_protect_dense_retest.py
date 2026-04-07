from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipelines.lib.asr.lite_asr import Segment
from pipelines.tools import run_entity_protect_dense_retest as tool


class EntityProtectDenseRetestTest(unittest.TestCase):
    def test_dense_entity_candidates_extract_repeated_names(self) -> None:
        segs = [
            Segment(start=0.0, end=1.0, text="但龙叔还是答应了"),
            Segment(start=1.1, end=2.0, text="中情局为龙叔准备了新身份"),
            Segment(start=2.1, end=3.0, text="手下给龙叔植入了追踪器"),
            Segment(start=3.1, end=4.0, text="典御长根本不鸟他"),
            Segment(start=4.1, end=5.0, text="典御长都要亲自独查"),
        ]
        cands = tool._dense_entity_candidates(segs, min_freq=2, max_items=8)
        self.assertIn("龙叔", cands)
        self.assertIn("典御长", cands)

    def test_load_segments_falls_back_to_chs_srt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            srt = root / "chs.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n龙叔到了\n\n2\n00:00:01,100 --> 00:00:02,000\n中情局也到了\n",
                encoding="utf-8",
            )
            segs = tool._load_segments({"id": "x", "chs_srt": str(srt)})
            self.assertEqual(len(segs), 2)
            self.assertEqual(segs[0].text, "龙叔到了")


if __name__ == "__main__":
    unittest.main()
