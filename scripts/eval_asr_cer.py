#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from scripts.asr_normalize import normalize_asr_zh_text


def _read_jsonl(p: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        obj = json.loads(ln)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _levenshtein(a: List[str], b: List[str]) -> int:
    """
    Levenshtein edit distance (O(n*m) DP). For ASR eval sizes (test sets) this is fine.
    """
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    cur = [0] * (m + 1)
    for i in range(1, n + 1):
        cur[0] = i
        ai = a[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev, cur = cur, prev
    return prev[m]


def _chars_zh(s: str) -> List[str]:
    # Character-level CER: keep every codepoint except spaces.
    t = (s or "").replace(" ", "").strip()
    return list(t)


@dataclass
class Row:
    id: str
    ref: str
    pred: str
    ref_n: int
    edits: int
    cer: float
    meta: Dict[str, Any]


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate Chinese ASR with CER on JSONL pairs (id/ref_zh/pred_zh).")
    ap.add_argument("--in-jsonl", type=Path, required=True, help="Input JSONL: {id, ref_zh, pred_zh, meta?}")
    ap.add_argument("--out", type=Path, required=True, help="Output report.json path")
    ap.add_argument("--topk", type=int, default=30, help="Include top-K worst samples")
    args = ap.parse_args()

    rows_in = _read_jsonl(Path(args.in_jsonl))
    rows: List[Row] = []
    total_edits = 0
    total_ref = 0

    for obj in rows_in:
        cid = str(obj.get("id") or "").strip()
        ref = str(obj.get("ref_zh") or "")
        pred = str(obj.get("pred_zh") or "")
        meta = dict(obj.get("meta") or {}) if isinstance(obj.get("meta"), dict) else {}
        if not cid:
            continue
        ref_n = normalize_asr_zh_text(ref)
        pred_n = normalize_asr_zh_text(pred)
        ref_chars = _chars_zh(ref_n)
        pred_chars = _chars_zh(pred_n)
        edits = _levenshtein(ref_chars, pred_chars)
        denom = max(1, len(ref_chars))
        cer = float(edits / denom)
        rows.append(Row(id=cid, ref=ref_n, pred=pred_n, ref_n=len(ref_chars), edits=edits, cer=cer, meta=meta))
        total_edits += edits
        total_ref += len(ref_chars)

    overall_cer = float(total_edits / max(1, total_ref))
    rows_sorted = sorted(rows, key=lambda r: (r.cer, r.edits, r.ref_n), reverse=True)
    worst = [
        {
            "id": r.id,
            "cer": round(r.cer, 6),
            "edits": r.edits,
            "ref_len": r.ref_n,
            "ref_zh_norm": r.ref,
            "pred_zh_norm": r.pred,
            "meta": r.meta,
        }
        for r in rows_sorted[: int(args.topk)]
    ]

    out_obj = {
        "task": "asr_zh_cer",
        "in_jsonl": str(Path(args.in_jsonl)),
        "n": len(rows),
        "total_ref_chars": total_ref,
        "total_edits": total_edits,
        "cer": round(overall_cer, 6),
        "worst_samples": worst,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {args.out} (n={len(rows)}, cer={overall_cer:.4f})")


if __name__ == "__main__":
    main()


