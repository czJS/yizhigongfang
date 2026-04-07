#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import re
import urllib.request
from pathlib import Path
from typing import Dict, List, Set


_WS = re.compile(r"\s+")
_ONLY_PUNCT = re.compile(r"^[^A-Za-z0-9]+$")
_BAD = re.compile(r"(https?://|www\\.|\\bsubtitle\\b|\\bsubtitles\\b)", re.IGNORECASE)


def _norm_en(s: str) -> str:
    t = str(s or "").replace("\r", "").strip()
    t = t.replace("\u00a0", " ")
    t = _WS.sub(" ", t).strip()
    return t


def _is_good_line(s: str) -> bool:
    t = _norm_en(s)
    if not t:
        return False
    if len(t) < 3 or len(t) > 120:
        return False
    if _ONLY_PUNCT.match(t):
        return False
    if _BAD.search(t):
        return False
    # avoid bracketed SFX
    low = t.lower()
    if (low.startswith("[") and low.endswith("]")) or (low.startswith("(") and low.endswith(")")):
        return False
    # require letters
    if sum(ch.isalpha() for ch in t) < 3:
        return False
    toks = [x for x in re.split(r"[^A-Za-z0-9']+", t) if x]
    if len(toks) < 2 or len(toks) > 24:
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch English ref_en lines from OPUS OpenSubtitles (v2018) monolingual EN corpus.")
    ap.add_argument(
        "--url",
        type=str,
        default="https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2018/mono/en.txt.gz",
        help="Source .gz URL",
    )
    ap.add_argument("--n", type=int, default=200, help="How many ref_en lines to output (default: 200)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-jsonl", type=Path, required=True, help="Output JSONL: {id, ref_en, meta}")
    ap.add_argument("--max-scan-lines", type=int, default=2_000_000, help="Max lines to scan from stream")
    ap.add_argument("--pool-mult", type=int, default=200, help="Candidate pool size multiplier (pool = n*pool_mult)")
    args = ap.parse_args()

    n = max(1, int(args.n))
    pool_target = max(n * max(10, int(args.pool_mult)), n)
    rng = random.Random(int(args.seed))

    # collect a bounded pool then sample for variety (keeps memory bounded)
    pool: List[str] = []
    seen: Set[str] = set()

    with urllib.request.urlopen(str(args.url), timeout=60) as resp:
        gz = gzip.GzipFile(fileobj=resp)  # type: ignore[arg-type]
        for i, raw in enumerate(gz, 1):
            if i > int(args.max_scan_lines):
                break
            try:
                line = raw.decode("utf-8", errors="ignore")
            except Exception:
                continue
            line = _norm_en(line)
            if not _is_good_line(line):
                continue
            key = hashlib.sha1(line.lower().encode("utf-8")).hexdigest()[:16]
            if key in seen:
                continue
            seen.add(key)
            pool.append(line)
            if len(pool) >= pool_target:
                break

    if len(pool) < n:
        raise SystemExit(f"Not enough lines collected (got={len(pool)}, need={n}). Try increasing --max-scan-lines.")

    rng.shuffle(pool)
    picked = pool[:n]

    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.out_jsonl.open("w", encoding="utf-8") as f:
        for idx, t in enumerate(picked, 1):
            f.write(
                json.dumps(
                    {
                        "id": f"opensubtitles2018-en-{idx:05d}",
                        "ref_en": t,
                        "meta": {"source": "OPUS-OpenSubtitles-v2018-mono-en", "url": str(args.url), "seed": int(args.seed)},
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(f"[ok] wrote {args.out_jsonl} (n={n}, pool={len(pool)})")


if __name__ == "__main__":
    main()





