from __future__ import annotations

import json
import unittest
from pathlib import Path

from pipelines.lib.asr import lite_asr_stage1
import pipelines.quality_pipeline_impl as quality_pipeline_impl


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_PATH = REPO_ROOT / "assets" / "zh_phrase" / "asr_project_confusions.json"


class ProjectConfusionAssetTest(unittest.TestCase):
    def test_asset_has_expected_shape_and_count(self) -> None:
        payload = json.loads(ASSET_PATH.read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload, dict) else payload
        self.assertIsInstance(items, list)
        self.assertGreater(len(items), 0)
        self.assertTrue(all(isinstance(it, dict) for it in items))
        self.assertTrue(all(str(it.get("wrong") or "").strip() for it in items))
        self.assertTrue(all(list(it.get("candidates") or []) for it in items))
        wrongs = [str(it.get("wrong") or "").strip() for it in items]
        self.assertEqual(len(wrongs), len(set(wrongs)))

    def test_lite_pipeline_can_load_and_hit_asset(self) -> None:
        items = lite_asr_stage1._load_project_confusions(ASSET_PATH)
        self.assertGreater(len(items), 0)

        hits = lite_asr_stage1._project_confusion_hits(
            "国家石油公司已经开始联系俄罗斯供应商了，生怕获原不够稳。",
            path=ASSET_PATH,
        )
        self.assertTrue(any(it["wrong"] == "获原" and "货源" in it["candidates"] for it in hits))

        removed = lite_asr_stage1._project_confusion_hits(
            "如果我可以详情经过一般程序申请。",
            path=ASSET_PATH,
        )
        self.assertFalse(any(it["wrong"] == "详情" for it in removed))

    def test_quality_pipeline_can_load_and_hit_asset(self) -> None:
        old_path = quality_pipeline_impl._DEFAULT_PROJECT_CONFUSIONS_PATH
        try:
            quality_pipeline_impl._DEFAULT_PROJECT_CONFUSIONS_PATH = str(ASSET_PATH)
            quality_pipeline_impl._PROJECT_CONFUSION_CACHE.clear()

            items = quality_pipeline_impl._load_project_confusions(str(ASSET_PATH))
            self.assertGreater(len(items), 0)

            hits = quality_pipeline_impl._project_confusion_hits("2026年直播想战门脚跟，一方面得吃透新规。")
            self.assertTrue(any(it["wrong"] == "战门" and "站稳" in it["candidates"] for it in hits))

            removed = quality_pipeline_impl._project_confusion_hits("所以我主张麦克风提告。")
            self.assertFalse(any(it["wrong"] == "主张" for it in removed))
        finally:
            quality_pipeline_impl._DEFAULT_PROJECT_CONFUSIONS_PATH = old_path
            quality_pipeline_impl._PROJECT_CONFUSION_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
