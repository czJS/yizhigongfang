from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pipelines.lib.glossary.entity_protect import protect_entities, restore_entities
from pipelines.lib.mt.mt_split import split_translation_by_src_lengths
from pipelines.lib.mt.sentence_unit import build_sentence_unit_groups
from pipelines.lib.text.translate_post import apply_replacements, clean_en, dedupe_phrases, dedupe_repeats, protect_nums, restore, rule_polish
from pipelines.lib.text.zh_text import clean_zh_text
from pipelines.lib.asr.lite_asr import Segment


def translate_segments(
    segments: List[Segment],
    translate_fn,
    translate_batch_fn=None,
    polish_fn=None,
    lt_fn=None,
    replacement_rules: Optional[List[dict]] = None,
    entity_map: Optional[Dict[str, str]] = None,
    *,
    sentence_unit_enable: bool = False,
    sentence_unit_min_chars: int = 12,
    sentence_unit_max_chars: int = 60,
    sentence_unit_max_segs: int = 3,
    sentence_unit_max_gap_s: float = 0.6,
    sentence_unit_boundary_punct: str = "。！？!?.,",
    sentence_unit_break_words: Optional[List[str]] = None,
) -> List[Segment]:
    """
    合并翻译再回填；数字占位保护；规则级英文清理；可选词典替换/LT/外部润色（默认关闭）
    """
    merged = build_sentence_unit_groups(
        segments,
        enable=bool(sentence_unit_enable),
        min_chars=int(sentence_unit_min_chars),
        max_chars=int(sentence_unit_max_chars),
        max_segs=int(sentence_unit_max_segs),
        max_gap_s=float(sentence_unit_max_gap_s),
        boundary_punct=str(sentence_unit_boundary_punct or "。！？!?.,"),
        break_words=list(sentence_unit_break_words or []),
    )

    prepared = []
    batch_inputs: List[str] = []
    for group in merged:
        idxs = [i for i, _ in group]
        texts = [s.text for _, s in group]
        merged_text = clean_zh_text(" ".join((t or "").strip() for t in texts))
        protected_text = merged_text
        ent_used: List[Tuple[str, str]] = []
        if entity_map:
            protected_text, ent_used = protect_entities(protected_text, entity_map)
        protected_text, nums = protect_nums(protected_text)
        prepared.append((idxs, texts, nums, ent_used))
        batch_inputs.append(protected_text)

    if translate_batch_fn:
        batch_outputs = list(translate_batch_fn(batch_inputs))
        if len(batch_outputs) != len(prepared):
            raise RuntimeError(
                f"批量翻译返回数量异常: expected={len(prepared)} actual={len(batch_outputs)}"
            )
    else:
        batch_outputs = [translate_fn(text) for text in batch_inputs]

    results: List[Segment] = []
    for (idxs, texts, nums, ent_used), en in zip(prepared, batch_outputs):
        en = restore(en, nums)
        en = restore_entities(en, ent_used)

        pieces = split_translation_by_src_lengths(texts, str(en))
        for i, piece in enumerate(pieces):
            seg_idx = idxs[i] if i < len(idxs) else idxs[-1]
            seg = segments[seg_idx]
            piece_clean = dedupe_phrases(dedupe_repeats(rule_polish(clean_en(piece))))
            if replacement_rules:
                piece_clean = apply_replacements(piece_clean, replacement_rules)
            if lt_fn:
                try:
                    piece_clean = lt_fn(piece_clean).strip()
                except Exception:
                    pass
            if polish_fn:
                polished = polish_fn(piece_clean).strip()
                # 拒答或空输出则回退
                if not polished or polished.lower().startswith("i'm sorry"):
                    polished = piece_clean
                piece_clean = polished
            results.append(Segment(start=seg.start, end=seg.end, text=seg.text, translation=piece_clean))

    results.sort(key=lambda s: s.start)
    return results

