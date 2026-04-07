#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
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


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(p.read_text(encoding="utf-8", errors="ignore") or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _get(d: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = d
    for k in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def clamp(x: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, x)))


def e2e_score_from_quality_report(rep: Dict[str, Any]) -> Dict[str, Any]:
    """
    Produce a stable 0..100 E2E score from quality_report.json.
    Best practice: gate/ship-ability first, then mild linear penalties for warnings.
    """
    passed = bool(rep.get("passed", False))
    errors = list(rep.get("errors") or [])
    warnings = list(rep.get("warnings") or [])
    checks = rep.get("checks") if isinstance(rep.get("checks"), dict) else {}

    # Hard failures / non-ship
    missing = int(_get(checks, "required_artifacts.missing", []) and len(_get(checks, "required_artifacts.missing", [])) or 0)
    trunc_fail = False
    trunc_s = _get(checks, "video_truncation.truncation_s", None)
    trunc_ratio = _get(checks, "video_truncation.truncation_ratio", None)
    if isinstance(trunc_s, (int, float)) and isinstance(trunc_ratio, (int, float)) and trunc_s > 0:
        trunc_fail = trunc_s > 1.0 and trunc_ratio > 0.03

    # Soft counts
    cjk_n = int(_get(checks, "english_purity.cjk_hits_n", 0) or 0)
    long_n = int(_get(checks, "line_length.hits_n", 0) or 0)
    cps_n = int(_get(checks, "reading_speed.hits_n", 0) or 0)
    overlap_n = int(_get(checks, "timeline_sanity.overlap_n", 0) or 0)
    negdur_n = int(_get(checks, "timeline_sanity.negative_or_zero_dur_n", 0) or 0)
    tts_risk_n = int(_get(checks, "tts_risk.hits_n", 0) or 0)
    term_missing_n = int(_get(checks, "terminology.missing_n", 0) or 0)
    term_forbidden_n = int(_get(checks, "terminology.forbidden_n", 0) or 0)

    clip_ratio = _get(checks, "tts_audio_clipping.clipped_ratio", None)
    clip_ratio_f = float(clip_ratio) if isinstance(clip_ratio, (int, float)) else 0.0

    score = 100.0
    if missing > 0:
        score -= 50.0
        score -= 2.0 * min(10, missing)
    if trunc_fail:
        score -= 35.0
    if (not passed) and errors:
        score -= 15.0

    score -= min(20.0, 2.0 * cjk_n)  # cjk in eng is severe
    score -= min(15.0, 0.6 * long_n)
    score -= min(15.0, 0.6 * cps_n)
    score -= min(10.0, 0.5 * overlap_n)
    score -= min(10.0, 1.0 * negdur_n)
    score -= min(12.0, 0.25 * tts_risk_n)
    score -= min(8.0, 0.25 * term_missing_n)
    score -= min(10.0, 0.7 * term_forbidden_n)
    if clip_ratio_f > 0.002:
        score -= 5.0

    score = clamp(score, 0.0, 100.0)
    return {
        "e2e_score_100": round(score, 2),
        "passed": bool(passed),
        "missing_artifacts_n": int(missing),
        "cjk_hits_n": int(cjk_n),
        "long_line_hits_n": int(long_n),
        "cps_hits_n": int(cps_n),
        "overlap_n": int(overlap_n),
        "negdur_n": int(negdur_n),
        "tts_risk_hits_n": int(tts_risk_n),
        "term_missing_n": int(term_missing_n),
        "term_forbidden_n": int(term_forbidden_n),
        "tts_clip_ratio": round(float(clip_ratio_f), 6),
        "truncation_s": trunc_s,
        "truncation_ratio": trunc_ratio,
        "errors_n": len(errors),
        "warnings_n": len(warnings),
    }


def bootstrap_prob_improve(
    base: List[float],
    cand: List[float],
    *,
    iters: int = 2000,
    seed: int = 42,
) -> Dict[str, Any]:
    rng = random.Random(int(seed))
    n = min(len(base), len(cand))
    if n <= 1:
        return {"iters": 0, "p_improve": 0.0, "delta_mean": 0.0, "ci95": [0.0, 0.0]}
    deltas: List[float] = []
    improve = 0
    for _ in range(int(iters)):
        ds = 0.0
        for _j in range(n):
            k = rng.randrange(0, n)
            ds += (cand[k] - base[k])
        ds /= float(n)
        deltas.append(ds)
        if ds > 0:
            improve += 1
    deltas.sort()
    lo = deltas[int(0.025 * len(deltas))]
    hi = deltas[int(0.975 * len(deltas)) - 1]
    return {
        "iters": int(iters),
        "p_improve": round(improve / float(iters), 6),
        "delta_mean": round(sum(deltas) / float(len(deltas)), 6),
        "ci95": [round(lo, 6), round(hi, 6)],
    }


def load_run_scores(segments: List[Dict[str, Any]], run_dir: Path) -> Tuple[List[float], List[Dict[str, Any]]]:
    scores: List[float] = []
    per_seg: List[Dict[str, Any]] = []
    for s in segments:
        sid = str(s.get("id") or "").strip()
        wd = run_dir / sid
        rep_p = wd / "quality_report.json"
        rep = (
            _read_json(rep_p)
            if rep_p.exists()
            else {"passed": False, "errors": [f"missing {rep_p}"], "checks": {"required_artifacts": {"missing": ["quality_report.json"]}}}
        )
        m = e2e_score_from_quality_report(rep)
        scores.append(float(m["e2e_score_100"]))
        per_seg.append({"id": sid, "work_dir": str(wd), **m})
    return scores, per_seg


def mean(xs: List[float]) -> float:
    return float(sum(xs) / max(1, len(xs)))


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate quality-mode E2E runs using quality_report.json with bootstrap confidence.")
    ap.add_argument("--segments", type=Path, required=True, help="segments jsonl (id/video/meta)")
    ap.add_argument("--baseline", type=Path, required=True, help="baseline run dir (contains <seg_id>/quality_report.json)")
    ap.add_argument("--runs", nargs="*", default=[], help="extra runs: name=dir (repeatable)")
    ap.add_argument("--bootstrap-iters", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    segments = _read_jsonl(Path(args.segments))
    segments = [s for s in segments if isinstance(s, dict) and str(s.get("id") or "").strip()]
    if not segments:
        raise SystemExit("segments is empty")

    base_scores, base_rows = load_run_scores(segments, Path(args.baseline))
    out: Dict[str, Any] = {
        "task": "quality_e2e",
        "segments": str(Path(args.segments)),
        "baseline": str(Path(args.baseline)),
        "baseline_summary": {
            "n": len(base_scores),
            "passed_rate": round(sum(1 for r in base_rows if r.get("passed")) / float(max(1, len(base_rows))), 4),
            "e2e_score_100_mean": round(mean(base_scores), 2),
        },
        "runs": {},
    }

    for spec in list(args.runs or []):
        if "=" not in spec:
            continue
        name, p = spec.split("=", 1)
        name = name.strip()
        run_dir = Path(p.strip())
        scores, rows = load_run_scores(segments, run_dir)
        out["runs"][name] = {
            "n": len(scores),
            "passed_rate": round(sum(1 for r in rows if r.get("passed")) / float(max(1, len(rows))), 4),
            "e2e_score_100_mean": round(mean(scores), 2),
            "bootstrap": bootstrap_prob_improve(base_scores, scores, iters=int(args.bootstrap_iters), seed=int(args.seed)),
            "per_segment": rows,
        }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {args.out}")


if __name__ == "__main__":
    main()

