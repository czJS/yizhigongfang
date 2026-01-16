#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def _parse_srt_blocks(raw: str) -> List[str]:
    """
    Minimal SRT reader: returns text blocks in order, joining multi-line blocks by '\n'.
    """
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


_WS = re.compile(r"\s+")


def _norm_en(s: str) -> str:
    t = str(s or "").replace("\r", "").strip()
    t = _WS.sub(" ", t.replace("\n", " ")).strip()
    return t


def _word_count_en(s: str) -> int:
    t = _norm_en(s)
    if not t:
        return 0
    return len([x for x in t.split(" ") if x])


@dataclass
class Case:
    id: str
    zh: str
    ref_en: str
    source: str
    meta: Dict[str, Any]

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "zh": self.zh, "ref_en": self.ref_en, "source": self.source, "meta": self.meta}


def _load_public_pairs_jsonl(p: Path) -> List[Case]:
    if not p.exists():
        raise FileNotFoundError(f"public pairs not found: {p}")
    out: List[Case] = []
    for ln in _read_text(p).splitlines():
        ln = ln.strip()
        if not ln:
            continue
        obj = json.loads(ln)
        if not isinstance(obj, dict):
            continue
        cid = str(obj.get("id") or "").strip()
        zh = str(obj.get("zh") or "")
        ref_en = str(obj.get("ref_en") or "")
        if not cid or not _norm_en(ref_en) or not str(zh or "").strip():
            continue
        out.append(
            Case(
                id=cid,
                zh=zh,
                ref_en=ref_en,
                source=str(obj.get("source") or "public"),
                meta=dict(obj.get("meta") or {}),
            )
        )
    return out


def _load_golden_srt_dir(golden_dir: Path) -> List[Case]:
    eng = golden_dir / "eng.srt"
    if not eng.exists():
        raise FileNotFoundError(f"golden eng.srt not found in {golden_dir}")
    chs = golden_dir / "chs.srt"
    en_lines = _parse_srt_blocks(_read_text(eng))
    zh_lines = _parse_srt_blocks(_read_text(chs)) if chs.exists() else []
    n = len(en_lines)
    out: List[Case] = []
    for i in range(n):
        ref_en = en_lines[i]
        if not _norm_en(ref_en):
            continue
        zh = zh_lines[i] if i < len(zh_lines) else ""
        out.append(
            Case(
                id=f"golden-{i+1:05d}",
                zh=zh,
                ref_en=ref_en,
                source="golden_srt",
                meta={"idx": i + 1},
            )
        )
    return out


def _bin_key(ref_en: str) -> str:
    wc = _word_count_en(ref_en)
    if wc <= 5:
        return "len<=5"
    if wc <= 10:
        return "len6-10"
    if wc <= 18:
        return "len11-18"
    return "len19+"


def _stratified_sample(items: List[Case], n: int, rng: random.Random) -> List[Case]:
    if n <= 0:
        return []
    if len(items) <= n:
        rng.shuffle(items)
        return items
    bins: Dict[str, List[Case]] = {}
    for it in items:
        bins.setdefault(_bin_key(it.ref_en), []).append(it)
    for k in list(bins.keys()):
        rng.shuffle(bins[k])
    keys = sorted(bins.keys())
    # proportional allocation + round-robin fill
    total = sum(len(v) for v in bins.values()) or 1
    alloc: Dict[str, int] = {k: max(1, int(round(n * (len(bins[k]) / total)))) for k in keys}
    picked: List[Case] = []
    # first pass
    for k in keys:
        take = min(alloc[k], len(bins[k]))
        picked.extend(bins[k][:take])
        bins[k] = bins[k][take:]
    # round-robin fill
    i = 0
    while len(picked) < n:
        k = keys[i % len(keys)]
        if bins[k]:
            picked.append(bins[k].pop())
        else:
            # find any non-empty
            any_left = None
            for kk in keys:
                if bins[kk]:
                    any_left = kk
                    break
            if any_left is None:
                break
            picked.append(bins[any_left].pop())
        i += 1
    rng.shuffle(picked)
    return picked[:n]


def main() -> None:
    ap = argparse.ArgumentParser(description="Build English-fluency eval set (cases.jsonl) from public pairs and/or golden SRT.")
    ap.add_argument("--out", type=Path, required=True, help="Output cases.jsonl path")
    ap.add_argument("--n", type=int, default=200, help="Number of cases (default: 200)")
    ap.add_argument("--seed", type=int, default=42, help="Random seed")
    ap.add_argument("--public-only", action="store_true", help="Only use public pairs, ignore golden even if provided")
    ap.add_argument("--public-pairs", type=Path, default=Path("eval/fluency_en/public/pairs.jsonl"), help="Public pairs.jsonl path")
    ap.add_argument("--golden-dir", type=Path, default=None, help="Golden dir containing eng.srt (and optional chs.srt)")
    ap.add_argument("--mix-golden-ratio", type=float, default=0.5, help="When mixing public+golden, golden ratio (0..1)")
    args = ap.parse_args()

    rng = random.Random(int(args.seed))
    cases: List[Case] = []

    public_items: List[Case] = []
    if args.public_pairs and Path(args.public_pairs).exists():
        public_items = _load_public_pairs_jsonl(Path(args.public_pairs))

    golden_items: List[Case] = []
    if (not args.public_only) and args.golden_dir:
        golden_items = _load_golden_srt_dir(Path(args.golden_dir))

    if args.public_only:
        if not public_items:
            raise SystemExit("public-only 但 public-pairs 为空/不存在；请提供 eval/fluency_en/public/pairs.jsonl")
        cases = _stratified_sample(public_items, int(args.n), rng)
    else:
        # prefer golden if exists; otherwise public
        if golden_items and public_items:
            gr = max(0.0, min(1.0, float(args.mix_golden_ratio)))
            n_g = int(round(int(args.n) * gr))
            n_p = int(args.n) - n_g
            a = _stratified_sample(golden_items, n_g, rng)
            b = _stratified_sample(public_items, n_p, rng)
            cases = a + b
            rng.shuffle(cases)
        elif golden_items:
            cases = _stratified_sample(golden_items, int(args.n), rng)
        elif public_items:
            cases = _stratified_sample(public_items, int(args.n), rng)
        else:
            raise SystemExit("未找到任何输入：请提供 public-pairs 或 golden-dir")

    # de-dup by id (keep first)
    seen: set[str] = set()
    uniq: List[Case] = []
    for c in cases:
        if c.id in seen:
            continue
        seen.add(c.id)
        uniq.append(c)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for c in uniq:
            f.write(json.dumps(c.to_json(), ensure_ascii=False) + "\n")
    print(f"[ok] wrote {args.out} (n={len(uniq)})")


if __name__ == "__main__":
    main()


