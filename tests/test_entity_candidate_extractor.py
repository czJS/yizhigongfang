from __future__ import annotations

import unittest

from pipelines.lib.asr.lite_asr import Segment
from pipelines.lib.glossary.entity_protect import build_auto_entity_map
from pipelines.lib.text.zh_text import extract_entity_candidates_from_segments


class EntityCandidateExtractorTest(unittest.TestCase):
    def test_extract_entity_candidates_captures_names_titles_and_orgs(self) -> None:
        segs = [
            Segment(start=0.0, end=1.0, text="那时，我的舅父杨紫静先生，"),
            Segment(start=1.0, end=2.0, text="但龙叔还是答应了"),
            Segment(start=2.0, end=3.0, text="中情局为龙叔准备了新身份以及联络暗号"),
            Segment(start=3.0, end=4.0, text="手下给龙叔植入了追踪器"),
            Segment(start=4.0, end=5.0, text="于是在见到典御长后"),
            Segment(start=5.0, end=6.0, text="典御长根本不鸟他"),
            Segment(start=6.0, end=7.0, text="典御长都要亲自独查"),
        ]
        cands = extract_entity_candidates_from_segments(segs, min_len=2, max_len=8, min_freq=4, max_items=8)
        self.assertIn("杨紫静先生", cands)
        self.assertIn("龙叔", cands)
        self.assertIn("中情局", cands)
        self.assertIn("典御长", cands)
        self.assertNotIn("各个国", cands)
        self.assertNotIn("不准他", cands)
        self.assertNotIn("中情局为龙叔", cands)
        self.assertNotIn("手下给龙叔", cands)

    def test_build_auto_entity_map_filters_generic_title_translations(self) -> None:
        segs = [
            Segment(start=0.0, end=1.0, text="但龙叔还是答应了"),
            Segment(start=1.0, end=2.0, text="中情局为龙叔准备了新身份"),
            Segment(start=2.0, end=3.0, text="典御长根本不鸟他"),
            Segment(start=3.0, end=4.0, text="典御长都要亲自独查"),
        ]

        def fake_translate(text: str) -> str:
            mapping = {
                "龙叔": "Uncle Long",
                "中情局": "CIA",
                "典御长": "Sir.",
            }
            return mapping.get(text, text)

        entity_map = build_auto_entity_map(
            segs,
            fake_translate,
            min_len=2,
            max_len=8,
            min_freq=4,
            max_items=8,
            extract_candidates_fn=extract_entity_candidates_from_segments,
        )
        self.assertEqual(entity_map.get("龙叔"), "Uncle Long")
        self.assertEqual(entity_map.get("中情局"), "CIA")
        self.assertNotIn("典御长", entity_map)


if __name__ == "__main__":
    unittest.main()
