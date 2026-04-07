#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def parse_srt_texts(raw: str) -> List[str]:
    lines = [ln.rstrip("\n\r") for ln in (raw or "").splitlines()]
    out: List[str] = []
    i = 0
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        i += 1  # index
        if i >= len(lines):
            break
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


_PUNCT = re.compile(r"[\s\u3000，。！？；：、】【、,.!?;:（）()“”\"'·—…\-]+")


def norm_zh(s: str) -> str:
    t = str(s or "")
    t = _PUNCT.sub("", t)
    return t.strip()


def concat_norm(lines: List[str]) -> str:
    return "".join([norm_zh(x) for x in (lines or []) if norm_zh(x)])


def levenshtein(a: str, b: str) -> int:
    # DP edit distance, optimized for medium strings
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


def compare_zh(golden_lines: List[str], cand_lines: List[str]) -> Dict[str, Any]:
    g = concat_norm(golden_lines)
    c = concat_norm(cand_lines)
    dist = levenshtein(g, c)
    cer = dist / max(1, len(g))
    ratio = SequenceMatcher(None, g, c).ratio() if g and c else 0.0
    return {
        "golden_lines": len(golden_lines),
        "cand_lines": len(cand_lines),
        "golden_chars": len(g),
        "cand_chars": len(c),
        "edit_distance": dist,
        "cer": round(cer, 4),
        "similarity": round(1.0 - cer, 4),
        "seq_ratio": round(float(ratio), 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare Chinese chs.srt between runs and a golden baseline.")
    ap.add_argument("--golden", type=Path, required=True, help="golden chs.srt path")
    ap.add_argument("--base", type=Path, required=True, help="base run chs.srt path")
    ap.add_argument("--cand", type=Path, required=True, help="candidate run chs.srt path")
    ap.add_argument("--out", type=Path, required=True, help="output json path")
    args = ap.parse_args()

    g_lines = parse_srt_texts(read_text(args.golden) if args.golden.exists() else "")
    b_lines = parse_srt_texts(read_text(args.base) if args.base.exists() else "")
    c_lines = parse_srt_texts(read_text(args.cand) if args.cand.exists() else "")
    rep = {
        "paths": {"golden": str(args.golden), "base": str(args.base), "cand": str(args.cand)},
        "base_vs_golden": compare_zh(g_lines, b_lines),
        "cand_vs_golden": compare_zh(g_lines, c_lines),
        "cand_vs_base": compare_zh(b_lines, c_lines),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


