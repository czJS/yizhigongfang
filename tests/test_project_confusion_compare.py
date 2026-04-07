from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipelines.tools import compare_project_confusion_assets as compare_tool


class ProjectConfusionCompareTest(unittest.TestCase):
    def test_compare_asset_catalog_detects_added_removed_and_changed(self) -> None:
        before = [
            {"wrong": "获原", "candidates": ["货源"], "type": "double_char"},
            {"wrong": "详情", "candidates": ["相信"], "type": "double_char"},
        ]
        after = [
            {"wrong": "获原", "candidates": ["货源", "货运"], "type": "double_char"},
            {"wrong": "战门", "candidates": ["站稳"], "type": "double_char"},
        ]
        diff = compare_tool._compare_asset_catalog(before, after)
        self.assertEqual(diff["added_wrongs"], ["战门"])
        self.assertEqual(diff["removed_wrongs"], ["详情"])
        self.assertEqual(len(diff["changed_candidates"]), 1)
        self.assertEqual(diff["changed_candidates"][0]["wrong"], "获原")

    def test_load_corpus_lines_autodetects_text_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "corpus.jsonl"
            rows = [
                {"student_zh": "生怕获原不够稳"},
                {"optimized_pattern_wrong": "直播想战门脚跟"},
            ]
            p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
            lines = compare_tool._load_corpus_lines(p)
            self.assertEqual(lines, ["生怕获原不够稳", "直播想战门脚跟"])

    def test_asr_direct_compare_reports_added_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            before = root / "before.json"
            after = root / "after.json"
            before.write_text(json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8")
            after.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "wrong": "获原",
                                "candidates": ["货源"],
                                "type": "double_char",
                                "evidence_count": 2,
                                "sources": ["unit"],
                                "requires_high_risk": True,
                                "max_edit_distance": 2,
                                "notes": "",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            lines = ["国家石油公司已经开始联系俄罗斯供应商了生怕获原不够稳"]
            diff = compare_tool._diff_asr_direct(
                compare_tool._eval_asr_direct(lines, before),
                compare_tool._eval_asr_direct(lines, after),
                max_examples=5,
            )
            self.assertEqual(diff["before_lines_with_hits"], 0)
            self.assertEqual(diff["after_lines_with_hits"], 1)
            self.assertEqual(diff["delta_total_hits"], 1)


if __name__ == "__main__":
    unittest.main()
