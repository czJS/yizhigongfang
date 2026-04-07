from __future__ import annotations

import unittest

from pipelines.quality_pipeline_impl import (
    Segment,
    _apply_zh_post_polish_results,
    _pick_local_zh_repair,
    _should_accept_llm_polish,
)


class ZhRepairCandidatesTest(unittest.TestCase):
    def test_local_candidate_can_fix_likely_asr_one_char_error(self) -> None:
        fixed, hints = _pick_local_zh_repair(
            line="百姓更是连连叫跑",
            spans=[],
            rule_reasons=["疑似动宾搭配异常", "疑似动词缺失/错置"],
            same_pinyin_path="assets/zh_phrase/pycorrector_same_pinyin.txt",
            lexicon_path="assets/zh_phrase/chinese_xinhua_ci_2to4.txt",
            proper_nouns_path="assets/zh_phrase/thuocl_proper_nouns.txt",
        )
        self.assertEqual(fixed, "百姓更是连连叫苦")
        self.assertIn("叫跑->叫苦", hints)

    def test_apply_results_allows_real_span_change(self) -> None:
        segs = [Segment(start=0.0, end=1.0, text="羽人为了挽救自己的国家", translation=None)]
        artifacts = _apply_zh_post_polish_results(
            segments=segs,
            spans_by_idx={1: [{"start": 0, "end": 2, "text": "羽人", "source": "llm"}]},
            rule_reasons_by_idx={1: ["疑似专名/称谓一致性"]},
            polish_idxs={1},
            llm_lines_by_idx={1: "羽族为了挽救自己的国家"},
            zh_post_polish_enable=True,
        )
        self.assertEqual(segs[0].text, "羽族为了挽救自己的国家")
        self.assertTrue(artifacts.llm_meta_items[0]["changed"])
        self.assertEqual(artifacts.llm_meta_items[0]["change_kind"], "consistency_fix")

    def test_rejects_low_confidence_llm_guess_without_local_basis(self) -> None:
        accepted = _should_accept_llm_polish(
            base="开门于蚊子正面硬刚",
            opt="开门于陵子正面硬刚",
            rule_reasons=["疑似不通顺搭配", "短句但含异常词"],
            local_hints=[],
            same_pinyin_path="assets/zh_phrase/pycorrector_same_pinyin.txt",
            lexicon_path="assets/zh_phrase/chinese_xinhua_ci_2to4.txt",
            proper_nouns_path="assets/zh_phrase/thuocl_proper_nouns.txt",
        )
        self.assertFalse(accepted)

    def test_accepts_contextual_llm_fix_when_local_hint_supports_it(self) -> None:
        accepted = _should_accept_llm_polish(
            base="百姓更是连连叫跑",
            opt="百姓更是连连叫苦",
            rule_reasons=["疑似动宾搭配异常", "疑似动词缺失/错置"],
            local_hints=["叫跑->叫苦"],
            same_pinyin_path="assets/zh_phrase/pycorrector_same_pinyin.txt",
            lexicon_path="assets/zh_phrase/chinese_xinhua_ci_2to4.txt",
            proper_nouns_path="assets/zh_phrase/thuocl_proper_nouns.txt",
        )
        self.assertTrue(accepted)


if __name__ == "__main__":
    unittest.main()
