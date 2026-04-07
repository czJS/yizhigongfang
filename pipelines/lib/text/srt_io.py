from __future__ import annotations

from pathlib import Path
from typing import List


def read_srt_texts(path: Path) -> List[str]:
    """
    Minimal SRT reader that returns text blocks in order.
    Used for resume-from flows to restore translations from eng.srt.
    """
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.rstrip("\n\r") for ln in raw.splitlines()]
    out: List[str] = []
    i = 0
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        # index line
        i += 1
        if i >= len(lines):
            break
        # timing line
        if "-->" in (lines[i] or ""):
            i += 1
        else:
            i += 1
            continue
        text_lines: List[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        out.append("\n".join(text_lines).strip())
    return out


def read_srt_texts_ordered(path: Path) -> List[str]:
    """
    Read SRT texts in order (block order), preserving multi-line blocks joined with '\n'.
    This is used for review overrides where we assume 1:1 ordering with existing segments.
    """
    return read_srt_texts(path)

