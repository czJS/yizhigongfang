from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipelines.tools import mine_asr_project_confusions as tool


class ProjectConfusionPublishTest(unittest.TestCase):
    def test_build_asset_from_final_reviewed_rows(self) -> None:
        rows = [
            {
                "wrong": "获原",
                "candidate": "货源",
                "pair_type": "double_char",
                "teacher_vote_total": 2,
                "consensus_clip_count": 1,
                "source_video_count": 1,
                "best_pattern_wrong": "生怕获原不够",
                "best_pattern_candidate": "生怕货源不够",
            },
            {
                "wrong": "战门",
                "candidate": "站稳",
                "pair_type": "double_char",
                "teacher_vote_total": 2,
                "consensus_clip_count": 1,
                "source_video_count": 1,
                "second_pass_note": "站稳脚跟更自然",
            },
        ]
        asset, summary = tool._build_asset_from_final_reviewed_rows(rows, source_label="unit_test_run")
        self.assertEqual(asset["version"], 2)
        self.assertEqual(len(asset["items"]), 2)
        self.assertEqual(summary["formal_item_count"], 2)
        self.assertEqual(summary["type_counts"]["double_char"], 2)
        self.assertTrue(any(it["wrong"] == "获原" and it["candidates"] == ["货源"] for it in asset["items"]))
        self.assertTrue(any("例:生怕获原不够->生怕货源不够" in it["notes"] for it in asset["items"]))

    def test_append_archive_section_uses_next_subsection_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc = root / "archive.md"
            doc.write_text("# Doc\n\n## 6. 审核归档\n\n### 6.1 旧版本\n", encoding="utf-8")
            reviewed = root / "final.json"
            reviewed.write_text("[]\n", encoding="utf-8")
            asset = root / "asset.json"
            args = type(
                "Args",
                (),
                {
                    "archive_title": "第二版正式集",
                    "archive_note": "",
                    "source_run": "demo_run",
                    "manual_pool_size": 10,
                    "first_pass_accept": 1,
                    "first_pass_review": 2,
                    "first_pass_reject": 3,
                    "core_accept": 0,
                    "core_review": 0,
                    "core_reject": 0,
                    "second_pass_promoted": 0,
                    "second_pass_kept_review": 0,
                    "second_pass_rejected": 0,
                },
            )()
            header = tool._append_archive_section(
                doc_path=doc,
                reviewed_final_path=reviewed,
                asset_out=asset,
                source_label="demo_label",
                summary={"formal_item_count": 2, "example_kept_pairs": ["获原 -> 货源"]},
                args=args,
                removed_examples=["详情 -> 相信"],
            )
            text = doc.read_text(encoding="utf-8")
            self.assertEqual(header, "### 6.2 第二版正式集")
            self.assertIn("### 6.2 第二版正式集", text)
            self.assertIn("`获原 -> 货源`", text)
            self.assertIn("`详情 -> 相信`", text)

    def test_publish_summary_markdown_contains_key_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reviewed = root / "final.json"
            asset = root / "asset.json"
            args = type(
                "Args",
                (),
                {
                    "manual_pool_size": 738,
                    "first_pass_accept": 534,
                    "first_pass_review": 115,
                    "first_pass_reject": 89,
                    "core_accept": 207,
                    "core_review": 44,
                    "core_reject": 35,
                    "second_pass_promoted": 11,
                    "second_pass_kept_review": 22,
                    "second_pass_rejected": 11,
                },
            )()
            md = tool._render_publish_summary_md(
                reviewed_final_path=reviewed,
                asset_out=asset,
                source_label="demo_label",
                summary={
                    "reviewed_pair_count": 118,
                    "formal_item_count": 118,
                    "type_counts": {"double_char": 80, "short_phrase": 38},
                    "example_kept_pairs": ["获原 -> 货源", "战门 -> 站稳"],
                },
                args=args,
                removed_examples=["详情 -> 相信"],
            )
            self.assertIn("ASR项目混淆正式集发布摘要", md)
            self.assertIn("final.json", md)
            self.assertIn("asset.json", md)
            self.assertIn("`获原 -> 货源`", md)
            self.assertIn("`详情 -> 相信`", md)


if __name__ == "__main__":
    unittest.main()
