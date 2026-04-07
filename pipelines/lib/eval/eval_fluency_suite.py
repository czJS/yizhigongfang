#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


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


_WS = re.compile(r"\s+")


def _norm_en(s: str) -> str:
    t = str(s or "").replace("\r", "").strip()
    t = t.replace("\n", " ")
    t = _WS.sub(" ", t).strip()
    return t


def _tokens(s: str) -> List[str]:
    t = _norm_en(s).lower()
    if not t:
        return []
    return [x for x in re.split(r"[^a-z0-9']+", t) if x]


def _ngrams(xs: List[str], n: int) -> List[Tuple[str, ...]]:
    if n <= 0:
        return []
    if len(xs) < n:
        return []
    return [tuple(xs[i : i + n]) for i in range(len(xs) - n + 1)]


def bleu4(reference: str, hypothesis: str, *, smooth: float = 1.0) -> float:
    """
    Tiny BLEU-4 (token-based) with add-k smoothing (k=smooth).
    Returns 0..1.
    """
    ref = _tokens(reference)
    hyp = _tokens(hypothesis)
    if not ref or not hyp:
        return 0.0
    precisions: List[float] = []
    for n in (1, 2, 3, 4):
        ref_ngr = _ngrams(ref, n)
        hyp_ngr = _ngrams(hyp, n)
        if not hyp_ngr:
            precisions.append(0.0)
            continue
        ref_counts: Dict[Tuple[str, ...], int] = {}
        for g in ref_ngr:
            ref_counts[g] = ref_counts.get(g, 0) + 1
        match = 0
        hyp_counts: Dict[Tuple[str, ...], int] = {}
        for g in hyp_ngr:
            hyp_counts[g] = hyp_counts.get(g, 0) + 1
        for g, c in hyp_counts.items():
            match += min(c, ref_counts.get(g, 0))
        p = (match + smooth) / (len(hyp_ngr) + smooth)
        precisions.append(p)
    # brevity penalty
    r = len(ref)
    c = len(hyp)
    bp = 1.0 if c > r else math.exp(1.0 - float(r) / max(1.0, float(c)))
    score = bp * math.exp(sum(math.log(max(1e-12, p)) for p in precisions) / 4.0)
    return float(max(0.0, min(1.0, score)))


def chrf(reference: str, hypothesis: str, *, n: int = 6, beta: float = 2.0) -> float:
    """
    chrF (character n-gram F-score). Returns 0..1.
    Minimal implementation: average over n=1..N.
    """
    ref = _norm_en(reference)
    hyp = _norm_en(hypothesis)
    if not ref or not hyp:
        return 0.0

    def _char_ngrams(s: str, k: int) -> List[str]:
        if len(s) < k:
            return []
        return [s[i : i + k] for i in range(len(s) - k + 1)]

    scores: List[float] = []
    for k in range(1, int(n) + 1):
        ref_ng = _char_ngrams(ref, k)
        hyp_ng = _char_ngrams(hyp, k)
        if not ref_ng or not hyp_ng:
            continue
        ref_counts: Dict[str, int] = {}
        for g in ref_ng:
            ref_counts[g] = ref_counts.get(g, 0) + 1
        hyp_counts: Dict[str, int] = {}
        for g in hyp_ng:
            hyp_counts[g] = hyp_counts.get(g, 0) + 1
        match = 0
        for g, c in hyp_counts.items():
            match += min(c, ref_counts.get(g, 0))
        prec = match / max(1, len(hyp_ng))
        rec = match / max(1, len(ref_ng))
        if prec == 0.0 and rec == 0.0:
            f = 0.0
        else:
            b2 = beta * beta
            f = (1 + b2) * prec * rec / max(1e-12, (b2 * prec + rec))
        scores.append(f)
    if not scores:
        return 0.0
    return float(sum(scores) / len(scores))


def repetition_ratio(text: str, *, n: int = 3) -> float:
    toks = _tokens(text)
    ng = _ngrams(toks, n)
    if len(ng) <= 1:
        return 0.0
    uniq = len(set(ng))
    return float(1.0 - (uniq / max(1, len(ng))))


_END_PUNCT = re.compile(r"[.!?]$")
_BAD_CHARS = re.compile(r"[^ -~]")  # non-ascii printable (rough)


def ref_free_readability_score(text: str) -> float:
    """
    Reference-free readability/naturalness heuristic score for subtitle-like English (0..1).
    This is intentionally lightweight (no extra model download).
    """
    t = _norm_en(text)
    toks = _tokens(t)
    n = len(toks)
    if n == 0:
        return 0.0
    # length window: best around 5..14; tolerate 2..18
    if n < 2:
        len_s = 0.3
    elif n <= 18:
        # triangular peak at 10
        len_s = 1.0 - min(1.0, abs(n - 10) / 12.0)
    else:
        len_s = max(0.0, 1.0 - (n - 18) / 20.0)
    # non-ascii penalty
    bad = len(_BAD_CHARS.findall(t))
    bad_ratio = bad / max(1, len(t))
    bad_s = max(0.0, 1.0 - min(1.0, bad_ratio * 8.0))
    # end punctuation bonus (soft)
    end_s = 1.0 if _END_PUNCT.search(t) else 0.85
    # repetition penalty
    rep = repetition_ratio(t, n=3)
    rep_s = max(0.0, 1.0 - min(0.5, rep) * 1.5)
    score = 0.45 * len_s + 0.25 * bad_s + 0.15 * end_s + 0.15 * rep_s
    return float(max(0.0, min(1.0, score)))


def quality_score(bleu: float, chrf: float, len_ratio: float, rep3: float) -> float:
    """
    A simple, reproducible "quality score" for subtitle-like English:
    - base quality: 0.75*chrF + 0.25*BLEU
    - penalize abnormal length drift from reference
    - penalize repetition
    """
    base = 0.75 * float(chrf) + 0.25 * float(bleu)
    lr = float(len_ratio)
    len_pen = max(0.0, 1.0 - 0.25 * abs(lr - 1.0))  # 1.0 drift -> 0.75
    rep_pen = max(0.0, 1.0 - min(0.2, float(rep3)) * 2.0)  # cap penalty at 40%
    return float(max(0.0, min(1.0, base * len_pen * rep_pen)))


@dataclass
class EvalRow:
    id: str
    ref_en: str
    pred_en: str
    bleu: float
    chrf: float
    len_ratio: float
    rep3: float
    score: float
    ref_free: float


def eval_one(ref_en: str, pred_en: str) -> EvalRow:
    ref = _norm_en(ref_en)
    pred = _norm_en(pred_en)
    b = bleu4(ref, pred)
    c = chrf(ref, pred)
    rlen = len(_tokens(ref))
    plen = len(_tokens(pred))
    lr = float(plen / max(1, rlen))
    rep = repetition_ratio(pred, n=3)
    sc = quality_score(b, c, lr, rep)
    rf = ref_free_readability_score(pred)
    return EvalRow(id="", ref_en=ref, pred_en=pred, bleu=b, chrf=c, len_ratio=lr, rep3=rep, score=sc, ref_free=rf)


def mean(xs: List[float]) -> float:
    return float(sum(xs) / max(1, len(xs)))


def bootstrap_prob_improve(
    base_scores: List[float],
    cand_scores: List[float],
    *,
    iters: int = 2000,
    seed: int = 42,
) -> Dict[str, Any]:
    rng = random.Random(int(seed))
    n = min(len(base_scores), len(cand_scores))
    if n <= 1:
        return {"iters": 0, "p_improve": 0.0, "delta_mean": 0.0, "ci95": [0.0, 0.0]}
    deltas: List[float] = []
    improve = 0
    for _ in range(int(iters)):
        idxs = [rng.randrange(0, n) for _ in range(n)]
        mb = mean([base_scores[i] for i in idxs])
        mc = mean([cand_scores[i] for i in idxs])
        d = mc - mb
        deltas.append(d)
        if d > 0:
            improve += 1
    deltas.sort()
    lo = deltas[int(0.025 * len(deltas))]
    hi = deltas[int(0.975 * len(deltas)) - 1]
    return {
        "iters": int(iters),
        "p_improve": round(float(improve / max(1, iters)), 4),
        "delta_mean": round(float(mean(deltas)), 6),
        "ci95": [round(float(lo), 6), round(float(hi), 6)],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate English fluency runs vs references with bootstrap confidence.")
    ap.add_argument("--cases", type=Path, required=True, help="cases.jsonl path (must include ref_en)")
    ap.add_argument("--baseline", type=Path, required=True, help="baseline preds.jsonl path")
    ap.add_argument("--runs", type=str, nargs="*", default=[], help="extra runs: name=path (repeatable)")
    ap.add_argument("--out", type=Path, required=True, help="output report.json path")
    ap.add_argument("--bootstrap-iters", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--primary-metric", type=str, default="chrf", choices=["chrf", "bleu"], help="metric used for p_improve")
    args = ap.parse_args()

    cases = _read_jsonl(args.cases)
    case_by_id: Dict[str, Dict[str, Any]] = {str(x.get("id")): x for x in cases if str(x.get("id") or "").strip()}

    def _load_preds(p: Path) -> Dict[str, str]:
        rows = _read_jsonl(p)
        out: Dict[str, str] = {}
        for r in rows:
            cid = str(r.get("id") or "").strip()
            if not cid:
                continue
            out[cid] = str(r.get("pred_en") or "")
        return out

    base_preds = _load_preds(args.baseline)

    run_paths: Dict[str, Path] = {}
    for item in args.runs:
        if "=" not in item:
            raise SystemExit(f"bad --runs item: {item} (expected name=path)")
        name, path = item.split("=", 1)
        name = name.strip()
        path = path.strip()
        if not name or not path:
            raise SystemExit(f"bad --runs item: {item}")
        run_paths[name] = Path(path)

    def _score(preds: Dict[str, str]) -> Dict[str, Any]:
        bleus: List[float] = []
        chrfs: List[float] = []
        lenrs: List[float] = []
        rep3s: List[float] = []
        scores: List[float] = []
        ref_free_scores: List[float] = []
        missing = 0
        for cid, c in case_by_id.items():
            ref = str(c.get("ref_en") or "")
            if not ref.strip():
                continue
            pred = preds.get(cid, "")
            if not str(pred).strip():
                missing += 1
            r = eval_one(ref, pred)
            bleus.append(r.bleu)
            chrfs.append(r.chrf)
            lenrs.append(r.len_ratio)
            rep3s.append(r.rep3)
            scores.append(r.score)
            ref_free_scores.append(r.ref_free)
        return {
            "n": len(bleus),
            "missing_pred": missing,
            "bleu": round(mean(bleus), 6),
            "chrf": round(mean(chrfs), 6),
            "len_ratio": round(mean(lenrs), 6),
            "rep3": round(mean(rep3s), 6),
            "quality_score": round(mean(scores), 6),
            "ref_free_score": round(mean(ref_free_scores), 6),
            "_per_item": {"bleu": bleus, "chrf": chrfs},
        }

    base = _score(base_preds)
    base_metric_list = base["_per_item"][args.primary_metric]

    runs_rep: Dict[str, Any] = {}
    for name, path in run_paths.items():
        preds = _load_preds(path)
        rep = _score(preds)
        metric_list = rep["_per_item"][args.primary_metric]
        rep["bootstrap_primary"] = bootstrap_prob_improve(
            base_metric_list,
            metric_list,
            iters=int(args.bootstrap_iters),
            seed=int(args.seed),
        )
        runs_rep[name] = rep

    def _strip(x: Dict[str, Any]) -> Dict[str, Any]:
        y = dict(x)
        y.pop("_per_item", None)
        return y

    report = {
        "cases": str(args.cases),
        "baseline": str(args.baseline),
        "primary_metric": args.primary_metric,
        "baseline_summary": _strip(base),
        "runs": {k: _strip(v) for k, v in runs_rep.items()},
    }
    report["baseline_summary"]["quality_score_100"] = round(float(report["baseline_summary"].get("quality_score", 0.0)) * 100.0, 2)
    report["baseline_summary"]["ref_free_score_100"] = round(float(report["baseline_summary"].get("ref_free_score", 0.0)) * 100.0, 2)
    for k, v in report["runs"].items():
        v["quality_score_100"] = round(float(v.get("quality_score", 0.0)) * 100.0, 2)
        v["ref_free_score_100"] = round(float(v.get("ref_free_score", 0.0)) * 100.0, 2)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

