#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scripts.asr_normalize import normalize_asr_zh_text
from scripts.eval_fluency_suite import bleu4, chrf, ref_free_readability_score, repetition_ratio, quality_score
from scripts.eval_quality_e2e_suite import e2e_score_from_quality_report, bootstrap_prob_improve


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


_TS = re.compile(r"(?P<s>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<e>\d{2}:\d{2}:\d{2},\d{3})")


def _parse_srt_time(ts: str) -> float:
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


@dataclass
class Cue:
    start_s: float
    end_s: float
    text: str

    @property
    def dur_s(self) -> float:
        return max(0.0, float(self.end_s) - float(self.start_s))


def parse_srt_cues(raw: str) -> List[Cue]:
    """
    Minimal SRT parser (timestamp-aware). Tolerant to non-standard index lines.
    """
    lines = [ln.rstrip("\n\r") for ln in (raw or "").splitlines()]
    cues: List[Cue] = []
    i = 0
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        # optional index
        if lines[i].strip().isdigit():
            i += 1
        if i >= len(lines):
            break
        m = _TS.search(lines[i] or "")
        if not m:
            i += 1
            continue
        s = _parse_srt_time(m.group("s"))
        e = _parse_srt_time(m.group("e"))
        i += 1
        text_lines: List[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        txt = "\n".join(text_lines).strip()
        cues.append(Cue(start_s=float(s), end_s=float(e), text=txt))
    cues.sort(key=lambda c: (c.start_s, c.end_s))
    return cues


def read_srt_like(path: Path) -> List[Cue]:
    """
    Read SRT cues from:
    - normal *.srt files
    - non-standard filenames like *.srt_en (content contains SRT timestamps)
    """
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if not _TS.search(raw or ""):
        return []
    return parse_srt_cues(raw)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def align_by_time(golden: List[Cue], pred: List[Cue], *, min_overlap_ratio: float = 0.2) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """
    For each golden cue, pick the predicted cue with maximum time overlap.
    Returns aligned text lists (same length), plus alignment stats.
    """
    if not golden:
        return [], [], {"golden_n": 0, "pred_n": len(pred), "matched_n": 0, "matched_ratio": 0.0}
    if not pred:
        return [c.text for c in golden], [""] * len(golden), {"golden_n": len(golden), "pred_n": 0, "matched_n": 0, "matched_ratio": 0.0}

    g_txt: List[str] = []
    p_txt: List[str] = []
    matched = 0
    ratios: List[float] = []

    j = 0
    for g in golden:
        # advance j so pred[j] ends after g.start
        while j < len(pred) and pred[j].end_s <= g.start_s:
            j += 1
        best_k: Optional[int] = None
        best_ratio = 0.0
        k = j
        while k < len(pred) and pred[k].start_s < g.end_s:
            p = pred[k]
            ov = _overlap(g.start_s, g.end_s, p.start_s, p.end_s)
            denom = max(1e-6, min(g.dur_s, p.dur_s))
            ratio = float(ov / denom) if denom > 0 else 0.0
            if ratio > best_ratio:
                best_ratio = ratio
                best_k = k
            k += 1
        g_txt.append(g.text)
        if best_k is not None and best_ratio >= float(min_overlap_ratio):
            p_txt.append(pred[best_k].text)
            matched += 1
            ratios.append(best_ratio)
        else:
            p_txt.append("")
    return g_txt, p_txt, {
        "golden_n": len(golden),
        "pred_n": len(pred),
        "matched_n": int(matched),
        "matched_ratio": round(matched / float(max(1, len(golden))), 4),
        "overlap_ratio_mean": round(sum(ratios) / float(max(1, len(ratios))), 4) if ratios else 0.0,
        "min_overlap_ratio": float(min_overlap_ratio),
    }


def _levenshtein(a: List[str], b: List[str]) -> int:
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
    t = (s or "").replace(" ", "").strip()
    return list(t)


def cer(ref_zh: str, pred_zh: str) -> Tuple[float, int, int]:
    r = normalize_asr_zh_text(ref_zh)
    p = normalize_asr_zh_text(pred_zh)
    rc = _chars_zh(r)
    pc = _chars_zh(p)
    edits = _levenshtein(rc, pc)
    denom = max(1, len(rc))
    return float(edits / denom), int(edits), int(len(rc))


def _join_lines(lines: List[str]) -> str:
    return "\n".join([str(x or "").strip() for x in lines if str(x or "").strip()]).strip()


def mt_metrics(ref_en: str, pred_en: str) -> Dict[str, Any]:
    b = bleu4(ref_en, pred_en)
    c = chrf(ref_en, pred_en)
    # token length ratio (same logic as eval_fluency_suite)
    # approximate tokens: split by whitespace after normalization inside bleu4/chrf; use simple count here
    ref_toks = [x for x in re.split(r"\\s+", (ref_en or "").strip()) if x]
    pred_toks = [x for x in re.split(r"\\s+", (pred_en or "").strip()) if x]
    lr = float(len(pred_toks) / max(1, len(ref_toks)))
    rep3 = repetition_ratio(pred_en, n=3)
    qs = quality_score(b, c, lr, rep3)
    rf = ref_free_readability_score(pred_en)
    mix = 0.6 * float(qs) + 0.4 * float(rf)
    return {
        "bleu": round(float(b), 6),
        "chrf": round(float(c), 6),
        "len_ratio": round(float(lr), 6),
        "rep3": round(float(rep3), 6),
        "quality_score": round(float(qs), 6),
        "ref_free_score": round(float(rf), 6),
        "mix": float(mix),  # for baseline-anchored final score mapping
    }


def _final_score_100(mix: float, *, base_mix: float) -> float:
    # match eval_fluency_suite: baseline anchored at 70, headroom scaled to 100
    denom = max(1e-6, 1.0 - float(base_mix))
    val = 70.0 + 30.0 * ((float(mix) - float(base_mix)) / denom)
    return float(max(0.0, min(100.0, val)))


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def eval_one_segment_with_golden(seg: Dict[str, Any], *, run_seg_dir: Path, min_overlap_ratio: float) -> Dict[str, Any]:
    sid = str(seg.get("id") or "").strip()
    meta = seg.get("meta") if isinstance(seg.get("meta"), dict) else {}
    gold_chs = Path(str(meta.get("gold_chs_srt") or ""))
    gold_eng = Path(str(meta.get("gold_eng_srt") or ""))
    pred_chs = run_seg_dir / "chs.srt"
    pred_eng = run_seg_dir / "eng.srt"

    out: Dict[str, Any] = {"id": sid, "golden": {"chs_srt": str(gold_chs), "eng_srt": str(gold_eng)}}
    if not (gold_chs.exists() and gold_eng.exists()):
        out["golden_present"] = False
        return out

    out["golden_present"] = True
    g_chs = read_srt_like(gold_chs)
    g_eng = read_srt_like(gold_eng)
    p_chs = read_srt_like(pred_chs) if pred_chs.exists() else []
    p_eng = read_srt_like(pred_eng) if pred_eng.exists() else []

    # Align by time (golden as reference grid)
    g_zh, p_zh, asr_align = align_by_time(g_chs, p_chs, min_overlap_ratio=float(min_overlap_ratio))
    g_en, p_en, mt_align = align_by_time(g_eng, p_eng, min_overlap_ratio=float(min_overlap_ratio))

    ref_zh = _join_lines(g_zh)
    pred_zh = _join_lines(p_zh)
    ref_en = _join_lines(g_en)
    pred_en = _join_lines(p_en)

    c, edits, ref_n = cer(ref_zh, pred_zh)
    mt = mt_metrics(ref_en, pred_en)

    out["asr"] = {
        "cer": round(float(c), 6),
        "ref_chars": int(ref_n),
        "edits": int(edits),
        "align": asr_align,
    }
    out["mt"] = {
        **{k: v for k, v in mt.items() if k != "mix"},
        "align": mt_align,
    }
    # keep raw texts only when needed (avoid huge report)
    out["debug"] = {
        "ref_zh_len": len(ref_zh),
        "pred_zh_len": len(pred_zh),
        "ref_en_len": len(ref_en),
        "pred_en_len": len(pred_en),
    }
    return out


def _md_table(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    # basic markdown table
    head = rows[0]
    body = rows[1:]
    out = []
    out.append("| " + " | ".join(head) + " |")
    out.append("| " + " | ".join(["---"] * len(head)) + " |")
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate E2E runs + golden SRT absolute quality (ASR CER + MT metrics) with bootstrap confidence.")
    ap.add_argument("--segments", type=Path, required=True, help="segments jsonl (id/video/meta); meta should include gold_chs_srt/gold_eng_srt")
    ap.add_argument("--baseline", type=Path, required=True, help="baseline run dir (contains <seg_id>/quality_report.json)")
    ap.add_argument("--runs", nargs="*", default=[], help="extra runs: name=dir (repeatable)")
    ap.add_argument("--bootstrap-iters", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-overlap-ratio", type=float, default=0.2, help="Minimum time-overlap ratio to match a predicted cue to a golden cue")
    ap.add_argument("--out-json", type=Path, required=True, help="Output report.json path")
    ap.add_argument("--out-md", type=Path, default=None, help="Optional output report.md path (readable)")
    args = ap.parse_args()

    segments = _read_jsonl(Path(args.segments))
    segments = [s for s in segments if isinstance(s, dict) and str(s.get("id") or "").strip()]
    if not segments:
        raise SystemExit("segments is empty")

    rng = random.Random(int(args.seed))
    _ = rng  # keep for future use; bootstrap uses imported function

    # ---- Baseline ----
    base_scores: List[float] = []
    base_rows: List[Dict[str, Any]] = []
    base_asr_score100: List[float] = []
    base_mt_mix: List[float] = []
    base_mt_final100: List[float] = []

    for s in segments:
        sid = str(s.get("id") or "").strip()
        wd = Path(args.baseline) / sid
        rep_p = wd / "quality_report.json"
        rep = _read_json(rep_p) if rep_p.exists() else {"passed": False, "errors": [f"missing {rep_p}"], "checks": {"required_artifacts": {"missing": ["quality_report.json"]}}}
        e2e = e2e_score_from_quality_report(rep)
        base_scores.append(float(e2e["e2e_score_100"]))

        gold = eval_one_segment_with_golden(s, run_seg_dir=wd, min_overlap_ratio=float(args.min_overlap_ratio))
        # asr score_100 is a convenience mapping: 100*(1-cer)
        if gold.get("golden_present") and isinstance(gold.get("asr"), dict):
            cer_v = _safe_float(gold["asr"].get("cer"), 1.0)
            base_asr_score100.append(float(max(0.0, min(100.0, (1.0 - cer_v) * 100.0))))
        if gold.get("golden_present") and isinstance(gold.get("mt"), dict):
            # recompute mix from stored mt fields (quality_score/ref_free_score) to avoid storing 'mix' in report
            qs = _safe_float(gold["mt"].get("quality_score"), 0.0)
            rf = _safe_float(gold["mt"].get("ref_free_score"), 0.0)
            base_mt_mix.append(0.6 * qs + 0.4 * rf)
        base_rows.append({"id": sid, "work_dir": str(wd), **e2e, "golden_eval": gold})

    base_mix = float(sum(base_mt_mix) / max(1, len(base_mt_mix))) if base_mt_mix else 0.0
    for mix in base_mt_mix:
        base_mt_final100.append(_final_score_100(mix, base_mix=base_mix))

    def _mean(xs: List[float]) -> float:
        return float(sum(xs) / max(1, len(xs)))

    def _rate_pass(rows: List[Dict[str, Any]]) -> float:
        return round(sum(1 for r in rows if r.get("passed")) / float(max(1, len(rows))), 4)

    report: Dict[str, Any] = {
        "说明_cn": {
            "用途": "质量模式 E2E（交付体验）+ 金标绝对质量（ASR/翻译）评测。用于评估单开关/组合开关是否值得默认开启。",
            "三类指标": {
                "E2E": "不依赖参考译文：基于 quality_report.json 的门禁/交付体验评分 e2e_score_100 与 passed_rate。",
                "ASR": "依赖中文金标 chs.srt：用时间戳对齐后计算 CER（越低越好），并提供 asr_score_100=100*(1-CER) 便于对比。",
                "翻译": "依赖英文金标 eng.srt：用时间戳对齐后计算 chrF/BLEU/quality_score/ref_free_score，并给出 final_score_100（baseline=70 锚点）。",
            },
            "时间戳对齐": "对每个金标字幕块，按时间重叠选取最匹配的预测字幕块（min_overlap_ratio 可调），再做文本指标计算；这能避免仅按行号对齐导致的错位。",
            "置信度": "bootstrap：对每段视频抽样，统计候选 run 相对 baseline 的提升概率 p_improve。",
        },
        "segments": str(Path(args.segments)),
        "baseline": str(Path(args.baseline)),
        "params": {"min_overlap_ratio": float(args.min_overlap_ratio), "bootstrap_iters": int(args.bootstrap_iters), "seed": int(args.seed)},
        "baseline_summary": {
            "n": len(segments),
            "passed_rate": _rate_pass(base_rows),
            "e2e_score_100_mean": round(_mean(base_scores), 2),
            "asr_score_100_mean": round(_mean(base_asr_score100), 2) if base_asr_score100 else None,
            "mt_final_score_100_mean": round(_mean(base_mt_final100), 2) if base_mt_final100 else None,
            "golden_covered_n": sum(1 for r in base_rows if (r.get("golden_eval") or {}).get("golden_present")),
        },
        "runs": {},
    }

    # ---- Candidate runs ----
    for spec in list(args.runs or []):
        if "=" not in spec:
            continue
        name, p = spec.split("=", 1)
        name = name.strip()
        run_dir = Path(p.strip())
        scores: List[float] = []
        rows: List[Dict[str, Any]] = []
        asr_score100: List[float] = []
        mt_mix: List[float] = []

        # baseline-anchored final score uses baseline's base_mix
        mt_final100: List[float] = []

        for s in segments:
            sid = str(s.get("id") or "").strip()
            wd = run_dir / sid
            rep_p = wd / "quality_report.json"
            rep = _read_json(rep_p) if rep_p.exists() else {"passed": False, "errors": [f"missing {rep_p}"], "checks": {"required_artifacts": {"missing": ["quality_report.json"]}}}
            e2e = e2e_score_from_quality_report(rep)
            scores.append(float(e2e["e2e_score_100"]))

            gold = eval_one_segment_with_golden(s, run_seg_dir=wd, min_overlap_ratio=float(args.min_overlap_ratio))
            if gold.get("golden_present") and isinstance(gold.get("asr"), dict):
                cer_v = _safe_float(gold["asr"].get("cer"), 1.0)
                asr_score100.append(float(max(0.0, min(100.0, (1.0 - cer_v) * 100.0))))
            if gold.get("golden_present") and isinstance(gold.get("mt"), dict):
                qs = _safe_float(gold["mt"].get("quality_score"), 0.0)
                rf = _safe_float(gold["mt"].get("ref_free_score"), 0.0)
                mt_mix.append(0.6 * qs + 0.4 * rf)
            rows.append({"id": sid, "work_dir": str(wd), **e2e, "golden_eval": gold})

        for mix in mt_mix:
            mt_final100.append(_final_score_100(mix, base_mix=base_mix))

        # bootstrap: e2e_score_100 (higher better)
        e2e_boot = bootstrap_prob_improve(base_scores, scores, iters=int(args.bootstrap_iters), seed=int(args.seed))
        # bootstrap: MT final score (higher better), if present
        mt_boot = None
        if base_mt_final100 and mt_final100 and (len(base_mt_final100) == len(mt_final100)):
            mt_boot = bootstrap_prob_improve(base_mt_final100, mt_final100, iters=int(args.bootstrap_iters), seed=int(args.seed))
        # bootstrap: ASR score_100 (higher better), if present
        asr_boot = None
        if base_asr_score100 and asr_score100 and (len(base_asr_score100) == len(asr_score100)):
            asr_boot = bootstrap_prob_improve(base_asr_score100, asr_score100, iters=int(args.bootstrap_iters), seed=int(args.seed))

        report["runs"][name] = {
            "n": len(scores),
            "passed_rate": _rate_pass(rows),
            "e2e_score_100_mean": round(_mean(scores), 2),
            "asr_score_100_mean": round(_mean(asr_score100), 2) if asr_score100 else None,
            "mt_final_score_100_mean": round(_mean(mt_final100), 2) if mt_final100 else None,
            "bootstrap": {"e2e": e2e_boot, "asr_score_100": asr_boot, "mt_final_score_100": mt_boot},
            "per_segment": rows,
        }

    # ---- Write JSON ----
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- Optional Markdown (readable) ----
    out_md = args.out_md
    if out_md is None:
        out_md = Path(str(args.out_json).replace(".json", ".md"))
    rows_md: List[List[str]] = [
        ["run", "passed_rate", "e2e_mean", "ASR(100)", "MT(final100)", "p_improve(e2e)", "p_improve(ASR)", "p_improve(MT)"]
    ]
    base_s = report["baseline_summary"]
    rows_md.append(
        [
            "baseline",
            str(base_s.get("passed_rate")),
            str(base_s.get("e2e_score_100_mean")),
            str(base_s.get("asr_score_100_mean") if base_s.get("asr_score_100_mean") is not None else "-"),
            str(base_s.get("mt_final_score_100_mean") if base_s.get("mt_final_score_100_mean") is not None else "-"),
            "-",
            "-",
            "-",
        ]
    )
    for name, r in (report.get("runs") or {}).items():
        b = (r.get("bootstrap") or {}) if isinstance(r.get("bootstrap"), dict) else {}
        rows_md.append(
            [
                str(name),
                str(r.get("passed_rate")),
                str(r.get("e2e_score_100_mean")),
                str(r.get("asr_score_100_mean") if r.get("asr_score_100_mean") is not None else "-"),
                str(r.get("mt_final_score_100_mean") if r.get("mt_final_score_100_mean") is not None else "-"),
                str(((b.get("e2e") or {}) if isinstance(b.get("e2e"), dict) else {}).get("p_improve", "-")),
                str(((b.get("asr_score_100") or {}) if isinstance(b.get("asr_score_100"), dict) else {}).get("p_improve", "-")),
                str(((b.get("mt_final_score_100") or {}) if isinstance(b.get("mt_final_score_100"), dict) else {}).get("p_improve", "-")),
            ]
        )
    md = []
    md.append("# 质量模式 E2E + 金标绝对分 评测报告\n")
    md.append("## 一句话结论怎么读\n")
    md.append("- **先看 passed_rate**：是否能稳定交付；掉通过率的开关不建议默认开。\n")
    md.append("- **再看 e2e_mean**：交付体验是否变好（门禁/截断/字幕工程/TTS风险综合）。\n")
    md.append("- **最后看 ASR(100)/MT(final100)**：有金标时，确认“识别/翻译”是否真的更准（更可解释）。\n")
    md.append("\n## 汇总表\n")
    md.append(_md_table(rows_md))
    md.append("\n\n## 配置与参数\n")
    md.append(f"- segments: `{report.get('segments')}`\n")
    md.append(f"- baseline: `{report.get('baseline')}`\n")
    md.append(f"- min_overlap_ratio: `{report.get('params', {}).get('min_overlap_ratio')}`\n")
    md.append(f"- bootstrap_iters: `{report.get('params', {}).get('bootstrap_iters')}`\n")
    md.append("\n## 说明（中文）\n")
    md.append(json.dumps(report.get('说明_cn', {}), ensure_ascii=False, indent=2))
    md.append("\n")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md), encoding="utf-8")

    print(f"[ok] wrote {args.out_json}")
    print(f"[ok] wrote {out_md}")


if __name__ == "__main__":
    main()


