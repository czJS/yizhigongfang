from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple


def build_sentence_unit_groups(
    segments: List[Any],
    *,
    enable: bool,
    min_chars: int = 12,
    max_chars: int = 60,
    max_segs: int = 3,
    max_gap_s: float = 0.6,
    boundary_punct: str = "。！？!?.,",
    break_words: Optional[List[str]] = None,
) -> List[List[Tuple[int, Any]]]:
    """
    Build sentence-unit groups for translation.

    This was extracted from an older lite pipeline implementation (legacy code path)
    to reduce duplication while keeping behavior stable.

    Notes:
    - `segments` items are expected to have `.text`, `.start`, `.end` fields.
    - When `enable=False`, this returns the legacy heuristic grouping exactly.
    """
    # Backward compatible behavior when disabled: keep the old heuristic exactly.
    if not enable:
        legacy_punct = set("。！？!?.,")
        legacy_max_chars = 40  # legacy heuristic
        merged: List[List[Tuple[int, Any]]] = []
        buf: List[Tuple[int, Any]] = []
        buf_chars = 0
        for idx, seg in enumerate(segments):
            buf.append((idx, seg))
            buf_chars += len(getattr(seg, "text", "") or "")
            seg_text = getattr(seg, "text", "") or ""
            if (seg_text and seg_text[-1] in legacy_punct) or len(buf) >= 2 or buf_chars >= legacy_max_chars:
                merged.append(buf)
                buf = []
                buf_chars = 0
        if buf:
            merged.append(buf)
        return merged

    boundary = set(boundary_punct or "。！？!?.,")
    bw = [w for w in (break_words or []) if str(w).strip()]
    merged2: List[List[Tuple[int, Any]]] = []
    buf2: List[Tuple[int, Any]] = []
    buf_chars2 = 0

    # Structural threshold: prefer merging fragments until we have something "translatable enough".
    # This is intentionally lightweight and heuristic-based (min-risk).
    _verbish = re.compile(r"(是|有|在|要|会|能|可以|必须|应该|觉得|认为|知道|说|讲|问|去|来|做|看到|听到)")

    def _has_predicate(s: str) -> bool:
        ss = (s or "").strip()
        if not ss:
            return False
        return bool(_verbish.search(ss))

    for idx, seg in enumerate(segments):
        seg_text = (getattr(seg, "text", "") or "").strip()

        # Discourse break words: if a new segment starts with "但/而/于是/然后..." we tend to start a new unit.
        if buf2 and bw:
            if any(seg_text.startswith(w) for w in bw):
                merged2.append(buf2)
                buf2 = []
                buf_chars2 = 0

        # If there is a large gap, do not merge across it (min-risk).
        if buf2:
            prev = buf2[-1][1]
            gap = float(getattr(seg, "start", 0.0) or 0.0) - float(getattr(prev, "end", 0.0) or 0.0)
            if gap > float(max_gap_s):
                merged2.append(buf2)
                buf2 = []
                buf_chars2 = 0

        buf2.append((idx, seg))
        buf_chars2 += len(getattr(seg, "text", "") or "")

        # Stop conditions (min-risk): size, length, punctuation when enough context.
        enough = (buf_chars2 >= int(min_chars)) or _has_predicate("".join((getattr(s, "text", "") or "") for _i, s in buf2))
        hit_boundary = bool(seg_text) and (seg_text[-1] in boundary)
        too_many = len(buf2) >= int(max_segs)
        too_long = buf_chars2 >= int(max_chars)
        if too_many or too_long or (enough and hit_boundary):
            merged2.append(buf2)
            buf2 = []
            buf_chars2 = 0

    if buf2:
        merged2.append(buf2)
    return merged2

