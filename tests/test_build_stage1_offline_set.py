from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipelines.tools.build_stage1_offline_set_impl import build_stage1_offline_rows


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class BuildStage1OfflineSetTest(unittest.TestCase):
    def test_build_rows_extracts_core_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "reports" / "lite_phase1" / "mt_cn20_dataset" / "mt_cases.jsonl"
            _write(
                manifest,
                json.dumps(
                    {
                        "id": "cn20_001",
                        "meta": {"source_id": "movie_explain"},
                        "chs_srt": "/app/reports/lite_phase1/asr_cn20_mt_source/runs/whispercpp_final/cn20_001/chs.srt",
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
            _write(
                root / "reports" / "lite_phase1" / "asr_cn20_mt_source" / "runs" / "whispercpp_final" / "cn20_001" / "chs.srt",
                "1\n00:00:00,000 --> 00:00:01,000\n百姓更是连连叫苦\n\n",
            )
            report_root = root / "reports" / "lite_phase1" / "asr_stage1_cn20_eval_generic_20260330_hostcopy"
            report = {
                "items": [
                    {
                        "idx": 1,
                        "before": "百姓更是连连叫跑",
                        "after_glossary": "百姓更是连连叫跑",
                        "rule_reasons": ["疑似ASR脏词/生造词", "疑似动宾搭配异常"],
                        "severity": "high",
                        "route_tier": "hard",
                        "local_hints": ["叫跑->叫苦"],
                        "candidates": [
                            {"source": "base", "text": "百姓更是连连叫跑"},
                            {"source": "local", "text": "百姓更是连连叫苦"},
                        ],
                    }
                ]
            }
            _write(report_root / "cn20_001" / "asr_stage1_report.json", json.dumps(report, ensure_ascii=False))

            rows = build_stage1_offline_rows(
                report_roots=[report_root],
                cases_manifest=manifest,
                repo_root=root,
                max_items=10,
            )

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["base"], "百姓更是连连叫跑")
            self.assertEqual(row["local"], "百姓更是连连叫苦")
            self.assertEqual(row["optimal"], "百姓更是连连叫苦")
            self.assertEqual(row["error_type"], "搭配/语法")
            self.assertTrue(row["target_change"])
            self.assertTrue(any(c["text"] == "百姓更是连连叫苦" for c in row["candidates"]))

    def test_build_rows_merges_candidates_from_multiple_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "reports" / "lite_phase1" / "mt_cn20_dataset" / "mt_cases.jsonl"
            _write(
                manifest,
                json.dumps(
                    {
                        "id": "cn20_002",
                        "meta": {"source_id": "movie_explain"},
                        "chs_srt": "/app/reports/lite_phase1/asr_cn20_mt_source/runs/whispercpp_final/cn20_002/chs.srt",
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
            _write(
                root / "reports" / "lite_phase1" / "asr_cn20_mt_source" / "runs" / "whispercpp_final" / "cn20_002" / "chs.srt",
                "1\n00:00:00,000 --> 00:00:01,000\n现在各国都发出了高额悬赏金\n\n",
            )
            report_a = root / "reports" / "lite_phase1" / "asr_stage1_cn20_eval_a_hostcopy"
            report_b = root / "reports" / "lite_phase1" / "asr_stage1_cn20_eval_b_hostcopy"
            payload_a = {
                "items": [
                    {
                        "idx": 1,
                        "after_glossary": "现在各国都发出了高额选赏金",
                        "rule_reasons": ["疑似ASR脏词/生造词"],
                        "severity": "medium",
                        "route_tier": "soft",
                        "candidates": [{"source": "local", "text": "现在各国都发出了高额悬赏金"}],
                    }
                ]
            }
            payload_b = {
                "items": [
                    {
                        "idx": 1,
                        "after_glossary": "现在各国都发出了高额选赏金",
                        "rule_reasons": ["疑似ASR脏词/生造词"],
                        "severity": "medium",
                        "route_tier": "soft",
                        "repair_options": [{"source": "derived", "text": "现在各国都发出了高额玄赏金"}],
                    }
                ]
            }
            _write(report_a / "cn20_002" / "asr_stage1_report.json", json.dumps(payload_a, ensure_ascii=False))
            _write(report_b / "cn20_002" / "asr_stage1_report.json", json.dumps(payload_b, ensure_ascii=False))

            rows = build_stage1_offline_rows(
                report_roots=[report_a, report_b],
                cases_manifest=manifest,
                repo_root=root,
                max_items=10,
            )

            self.assertEqual(len(rows), 1)
            texts = {cand["text"] for cand in rows[0]["candidates"]}
            self.assertIn("现在各国都发出了高额悬赏金", texts)
            self.assertIn("现在各国都发出了高额玄赏金", texts)
            self.assertEqual(rows[0]["optimal"], "现在各国都发出了高额悬赏金")
            self.assertEqual(sorted(rows[0]["report_sources"]), ["asr_stage1_cn20_eval_a_hostcopy", "asr_stage1_cn20_eval_b_hostcopy"])


if __name__ == "__main__":
    unittest.main()
