from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class AsrMergeStats:
    segments_in: int
    segments_out: int
    merged_groups: int
    merged_segments: int


def merge_short_segments(
    segments: List[Dict[str, Any]],
    *,
    min_dur_s: float = 0.8,
    min_chars: int = 6,
    max_gap_s: float = 0.25,
    max_group_chars: int = 120,
) -> Tuple[List[Dict[str, Any]], AsrMergeStats]:
    """
    Conservative postprocess: merge very short segments to reduce 'short-window guessing' ASR errors.

    Contract:
    - input segments are dicts with start/end/text
    - output keeps ordering and merges adjacent segments only (never reorders)
    - safe defaults; no content rewriting
    """
    min_dur_s = max(0.05, float(min_dur_s or 0.8))
    min_chars = max(1, int(min_chars or 6))
    max_gap_s = max(0.0, float(max_gap_s or 0.25))
    max_group_chars = max(10, int(max_group_chars or 120))

    def _get(seg: Dict[str, Any]) -> Tuple[float, float, str]:
        try:
            s = float(seg.get("start", 0.0))
        except Exception:
            s = 0.0
        try:
            e = float(seg.get("end", s))
        except Exception:
            e = s
        t = str(seg.get("text", "") or "").strip()
        return s, e, t

    out: List[Dict[str, Any]] = []
    merged_groups = 0
    merged_segments = 0
    buf: List[Dict[str, Any]] = []

    def flush():
        nonlocal merged_groups, merged_segments, buf
        if not buf:
            return
        if len(buf) > 1:
            merged_groups += 1
            merged_segments += (len(buf) - 1)
        s0, _, _ = _get(buf[0])
        _, e1, _ = _get(buf[-1])
        text = "".join([_get(x)[2] for x in buf]).strip()
        out.append({"start": float(s0), "end": float(e1), "text": text})
        buf = []

    for seg in segments or []:
        s, e, t = _get(seg)
        dur = max(e - s, 0.0)
        if not buf:
            buf = [{"start": s, "end": e, "text": t}]
            continue
        ps, pe, pt = _get(buf[-1])
        gap = max(s - pe, 0.0)
        cur_len = len("".join([_get(x)[2] for x in buf])) + len(t)

        # if current segment is short (dur or chars), attempt to merge into current group if gap is small
        shortish = (dur < min_dur_s) or (len(t) < min_chars)
        can_merge = gap <= max_gap_s and cur_len <= max_group_chars
        if shortish and can_merge:
            buf.append({"start": s, "end": e, "text": t})
            continue

        # if buffer itself is short, try to merge it with current when gap small
        buf_s, buf_e, buf_t = _get(buf[0])
        buf_dur = max(_get(buf[-1])[1] - buf_s, 0.0)
        buf_short = (buf_dur < min_dur_s) or (len("".join([_get(x)[2] for x in buf])) < min_chars)
        if buf_short and gap <= max_gap_s and cur_len <= max_group_chars:
            buf.append({"start": s, "end": e, "text": t})
            continue

        flush()
        buf = [{"start": s, "end": e, "text": t}]

    flush()
    return out, AsrMergeStats(segments_in=len(segments or []), segments_out=len(out), merged_groups=merged_groups, merged_segments=merged_segments)


_RE_ASCII = re.compile(r"[A-Za-z]")
_RE_PUNCT_SPACE = re.compile(r"[\s\u3000，。！？；：、】【、,.!?;:（）()“”\"'·—…\-]+")


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, dele, sub))
        prev = cur
    return prev[-1]


def is_safe_asr_llm_fix(before: str, after: str) -> bool:
    """
    Safety gate for ASR LLM fixes:
    - keep Chinese-only (no English letters)
    - avoid empty output
    - avoid extreme length changes
    """
    b = str(before or "").strip()
    a = str(after or "").strip()
    if not b:
        return False
    if not a:
        return False
    if _RE_ASCII.search(a):
        return False
    # length ratio guard (conservative)
    r = len(a) / max(1, len(b))
    if r < 0.7 or r > 1.3:
        return False
    # For homophone/typo fixes we only allow *very small* edits (punctuation ignored).
    nb = _RE_PUNCT_SPACE.sub("", b)
    na = _RE_PUNCT_SPACE.sub("", a)
    if not nb or not na:
        return False
    dist = _levenshtein(nb, na)
    # Dynamic cap: most fixes should be 1-char change; allow 2 for long lines.
    cap = 1 if len(nb) <= 32 else 2
    if dist > cap:
        return False
    return True


def extract_digits(s: str) -> List[str]:
    return re.findall(r"\d+(?:\.\d+)?", str(s or ""))


