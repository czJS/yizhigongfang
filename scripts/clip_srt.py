#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clip an SRT file to a time window [start_s, start_s + dur_s] and shift timestamps to start at 0.

This is used to prepare short3 clips derived from golden videos, keeping subtitles aligned.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def _ts_to_ms(ts: str) -> int:
    m = _TS_RE.match(ts.strip())
    if not m:
        raise ValueError(f"bad timestamp: {ts!r}")
    hh, mm, ss, ms = (int(x) for x in m.groups())
    return (((hh * 60 + mm) * 60) + ss) * 1000 + ms


def _ms_to_ts(ms: int) -> str:
    ms = max(0, int(ms))
    ss, msec = divmod(ms, 1000)
    mm, sec = divmod(ss, 60)
    hh, minu = divmod(mm, 60)
    return f"{hh:02d}:{minu:02d}:{sec:02d},{msec:03d}"


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    text: str


def parse_srt(text: str) -> List[Cue]:
    # Minimal SRT parser (good enough for our golden files).
    blocks = re.split(r"\n\s*\n", text.strip(), flags=re.M)
    out: List[Cue] = []
    for b in blocks:
        ls = [ln.rstrip("\r") for ln in b.splitlines()]
        if not ls:
            continue
        # Optional numeric index
        if len(ls) >= 2 and ls[0].strip().isdigit():
            ls = ls[1:]
        if not ls:
            continue
        if "-->" not in ls[0]:
            # malformed block
            continue
        t0, t1 = [x.strip() for x in ls[0].split("-->", 1)]
        try:
            s0 = _ts_to_ms(t0)
            s1 = _ts_to_ms(t1)
        except Exception:
            continue
        body = "\n".join(ls[1:]).strip()
        out.append(Cue(start_ms=s0, end_ms=s1, text=body))
    return out


def clip_cues(
    cues: List[Cue],
    *,
    clip_start_s: float,
    clip_dur_s: float,
) -> List[Cue]:
    start_ms = int(round(float(clip_start_s) * 1000))
    end_ms = int(round((float(clip_start_s) + float(clip_dur_s)) * 1000))
    out: List[Cue] = []
    for c in cues:
        # overlap
        s = max(c.start_ms, start_ms)
        e = min(c.end_ms, end_ms)
        if e <= s:
            continue
        out.append(Cue(start_ms=s - start_ms, end_ms=e - start_ms, text=c.text))
    return out


def dump_srt(cues: List[Cue]) -> str:
    parts: List[str] = []
    for i, c in enumerate(cues, start=1):
        parts.append(str(i))
        parts.append(f"{_ms_to_ts(c.start_ms)} --> {_ms_to_ts(c.end_ms)}")
        parts.append(c.text.strip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Clip SRT to a time window and shift timestamps.")
    ap.add_argument("--in-srt", type=Path, required=True)
    ap.add_argument("--out-srt", type=Path, required=True)
    ap.add_argument("--clip-start-s", type=float, required=True)
    ap.add_argument("--clip-dur-s", type=float, required=True)
    args = ap.parse_args()

    raw = Path(args.in_srt).read_text(encoding="utf-8", errors="ignore")
    cues = parse_srt(raw)
    clipped = clip_cues(cues, clip_start_s=float(args.clip_start_s), clip_dur_s=float(args.clip_dur_s))
    Path(args.out_srt).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_srt).write_text(dump_srt(clipped), encoding="utf-8")
    print(f"[ok] wrote {args.out_srt} cues={len(clipped)}")


if __name__ == "__main__":
    main()


