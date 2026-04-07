from __future__ import annotations

"""
Display subtitle builder (screen-friendly).

This is pipeline-level logic (pure rules), so it lives under `pipelines/lib/`.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple


_WS_RE = re.compile(r"\s+")


def _norm_en(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").replace("\n", " ")).strip()


def _wrap_en(s: str, *, max_chars_per_line: int, max_lines: int = 2) -> str:
    """
    Soft-wrap English into <= max_lines lines to reduce overly-long-line warnings.
    Deterministic; does NOT change total character count much (CPS isn't reduced by wrapping).
    """
    t = _norm_en(s)
    if not t or max_lines <= 1 or len(t) <= max_chars_per_line:
        return t
    words = t.split(" ")
    if len(words) <= 1:
        return "\n".join(
            [t[i : i + max_chars_per_line] for i in range(0, min(len(t), max_chars_per_line * max_lines), max_chars_per_line)]
        )
    lines: List[str] = []
    cur: List[str] = []
    for w in words:
        if not cur:
            cur = [w]
            continue
        cand = (" ".join(cur + [w])).strip()
        if len(lines) >= max_lines - 1:
            cur.append(w)
            continue
        if len(cand) <= max_chars_per_line:
            cur.append(w)
        else:
            lines.append(" ".join(cur).strip())
            cur = [w]
    if cur:
        lines.append(" ".join(cur).strip())
    lines = [ln.strip() for ln in lines if ln.strip()][:max_lines]
    clamped: List[str] = []
    for ln in lines:
        if len(ln) <= max_chars_per_line:
            clamped.append(ln)
        else:
            clamped.append(ln[: max_chars_per_line - 1].rstrip() + "…")
    return "\n".join(clamped).strip()


def _best_split_point(text: str) -> int:
    """
    Pick a split point for English text (single line) near the center.
    Prefer punctuation, then spaces. Returns an index into the string.
    """
    t = _norm_en(text)
    if not t:
        return -1
    mid = len(t) // 2
    punct = [m.start() for m in re.finditer(r"[.!?;:]", t)]
    cand = [p for p in punct if 3 <= p <= len(t) - 3]
    if cand:
        cand.sort(key=lambda i: abs(i - mid))
        return cand[0] + 1
    spaces = [m.start() for m in re.finditer(r"\s+", t)]
    cand2 = [p for p in spaces if 3 <= p <= len(t) - 3]
    if cand2:
        cand2.sort(key=lambda i: abs(i - mid))
        return cand2[0]
    return -1


@dataclass
class DisplayItem:
    start: float
    end: float
    text: str
    src_indices: List[int]  # 0-based indices into source segments


def build_display_items(
    *,
    src: Sequence[Tuple[float, float, str]],
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    merge_enable: bool = True,
    merge_max_gap_s: float = 0.25,
    merge_max_chars: int = 80,
    split_enable: bool = True,
    split_max_chars: int = 86,
) -> Tuple[List[DisplayItem], Dict[str, Any]]:
    """
    Build display subtitles that are "screen-friendly".
    - May merge adjacent short items (changes line count)
    - May split overlong items (changes line count)
    - Always deterministic, purely rule-based, and keeps time spans within original coverage
    """
    items: List[DisplayItem] = []
    for i, (st, ed, txt) in enumerate(src):
        st_f = float(st)
        ed_f = float(ed)
        if ed_f <= st_f:
            ed_f = st_f + 0.001
        items.append(DisplayItem(start=st_f, end=ed_f, text=_norm_en(txt), src_indices=[i]))

    merged: List[DisplayItem] = []
    if merge_enable and items:
        cur = items[0]
        for nxt in items[1:]:
            gap = max(0.0, float(nxt.start) - float(cur.end))
            cur_txt = _norm_en(cur.text)
            nxt_txt = _norm_en(nxt.text)
            should_merge = (
                gap <= float(merge_max_gap_s)
                and (len(cur_txt) + 1 + len(nxt_txt)) <= int(merge_max_chars)
                and (len(cur_txt) <= 22 or len(nxt_txt) <= 22)
                and not re.search(r"[.!?]$", cur_txt)
            )
            if should_merge:
                cur = DisplayItem(
                    start=float(cur.start),
                    end=float(nxt.end),
                    text=_norm_en(cur_txt + " " + nxt_txt),
                    src_indices=list(cur.src_indices) + list(nxt.src_indices),
                )
            else:
                merged.append(cur)
                cur = nxt
        merged.append(cur)
    else:
        merged = items

    split_out: List[DisplayItem] = []
    for it in merged:
        t = _norm_en(it.text)
        if not split_enable or len(t) <= int(split_max_chars):
            split_out.append(it)
            continue
        sp = _best_split_point(t)
        if sp <= 3 or sp >= len(t) - 3:
            sp = max(4, min(len(t) - 4, len(t) // 2))
        a = _norm_en(t[:sp])
        b = _norm_en(t[sp:])
        if not a or not b:
            split_out.append(it)
            continue
        dur = max(float(it.end) - float(it.start), 0.001)
        wa = max(len(a), 1)
        wb = max(len(b), 1)
        cut_t = float(it.start) + dur * (wa / float(wa + wb))
        cut_t = max(float(it.start) + 0.001, min(float(it.end) - 0.001, cut_t))
        split_out.append(DisplayItem(start=float(it.start), end=cut_t, text=a, src_indices=list(it.src_indices)))
        split_out.append(DisplayItem(start=cut_t, end=float(it.end), text=b, src_indices=list(it.src_indices)))

    final_items: List[DisplayItem] = []
    for it in split_out:
        wrapped = _wrap_en(it.text, max_chars_per_line=int(max_chars_per_line), max_lines=int(max_lines))
        final_items.append(DisplayItem(start=it.start, end=it.end, text=wrapped, src_indices=list(it.src_indices)))

    mapping = []
    for i, it in enumerate(final_items, start=1):
        mapping.append(
            {
                "display_idx": i,
                "start": round(float(it.start), 3),
                "end": round(float(it.end), 3),
                "src_indices": list(it.src_indices),
                "text": (it.text or "")[:220],
            }
        )
    meta = {
        "version": 1,
        "params": {
            "max_chars_per_line": int(max_chars_per_line),
            "max_lines": int(max_lines),
            "merge_enable": bool(merge_enable),
            "merge_max_gap_s": float(merge_max_gap_s),
            "merge_max_chars": int(merge_max_chars),
            "split_enable": bool(split_enable),
            "split_max_chars": int(split_max_chars),
        },
        "display_items": len(final_items),
        "mapping": mapping,
    }
    return final_items, meta

