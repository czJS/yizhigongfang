#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scripts.eval_quality_e2e_suite import bootstrap_prob_improve, e2e_score_from_quality_report


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


def mean(xs: List[float]) -> float:
    return float(sum(xs) / max(len(xs), 1))


@dataclass
class RunResult:
    name: str
    n: int
    passed_rate: float
    e2e_mean: float
    bootstrap: Dict[str, Any]
    per_segment: List[Dict[str, Any]]

_FILES_FOR_RESUME = {
    # Minimal artifacts needed for resume flows in scripts/asr_translate_tts.py
    "mt": ["audio.wav", "audio.json", "chs.srt"],
    "tts": ["audio.wav", "audio.json", "chs.srt", "eng.srt"],
}


def _needs_resume_stage(overrides: Dict[str, Any]) -> Optional[str]:
    """
    Decide whether an experiment can reuse baseline artifacts.

    IMPORTANT: Some "flags" are store_true in the lite script, so toggling them OFF after baseline
    is not always possible via resume, because baseline artifacts already baked in the effect.
    We intentionally keep this conservative.
    """
    ov = overrides or {}
    if not ov:
        return "tts"  # baseline-like run can reuse if we copied artifacts (not used for baseline itself)

    # Any change that affects ASR outputs saved into audio.json must re-run from scratch.
    asr_affecting = {
        "sample_rate",
        "denoise",
        "denoise_model",
        "whispercpp_threads",
        "whispercpp_bin",
        "whispercpp_model",
        "asr_model",
        "vad_enable",
        "vad_threshold",
        "vad_min_dur",
        "vad_model",
        "asr_normalize_enable",
        "asr_normalize_dict",
        # enforce_min_duration happens only in ASR stage; changing it requires rerun
        "min_sub_duration",
    }
    if any(k in ov for k in asr_affecting):
        return None

    # MT-stage affecting
    mt_affecting = {
        "sentence_unit_enable",
        "sentence_unit_min_chars",
        "sentence_unit_max_chars",
        "sentence_unit_max_segs",
        "sentence_unit_max_gap_s",
        "sentence_unit_boundary_punct",
        "sentence_unit_break_words",
        "entity_protect_enable",
        "entity_protect_min_len",
        "entity_protect_max_len",
        "entity_protect_min_freq",
        "entity_protect_max_items",
        "mt_model",
        "mt_device",
        "mt_cache_dir",
        "offline",
        "bilingual_srt",
        "en_polish_model",
        "en_polish_device",
        "lt_enable",
        "replacements",
        "glossary",
        "chs_override_srt",
    }
    if any(k in ov for k in mt_affecting):
        return "mt"

    # Otherwise assume TTS-stage affecting (or mux/embed only)
    return "tts"


def _copy_if_missing(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.write_bytes(src.read_bytes())
    except Exception:
        # fallback: skip silently
        pass


def _prime_from_baseline(*, baseline_seg_dir: Path, exp_seg_dir: Path, stage: str) -> None:
    files = _FILES_FOR_RESUME.get(stage) or []
    for name in files:
        s = baseline_seg_dir / name
        d = exp_seg_dir / name
        if s.exists():
            _copy_if_missing(s, d)


def _run_one_seg(
    *,
    repo_root: Path,
    video: str,
    out_dir: Path,
    config: str,
    preset: str,
    overrides: Dict[str, Any],
    max_runtime_s: int,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str((repo_root / "scripts" / "run_lite_e2e.py").resolve()),
        "--video",
        video,
        "--output-dir",
        str(out_dir),
        "--config",
        config,
        "--preset",
        preset,
        "--overrides-json",
        json.dumps(overrides, ensure_ascii=False),
        "--cleanup-artifacts",
        "--log-max-kb",
        "256",
    ]
    if max_runtime_s and max_runtime_s > 0:
        cmd += ["--max-runtime-s", str(int(max_runtime_s))]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = proc.stdout or ""
    # keep runner log small; never let logs be the reason we run out of disk
    if len(out) > 256 * 1024:
        out = out[: 256 * 1024] + "\n...[truncated]...\n"
    (out_dir / "_runner.log").write_text(out, encoding="utf-8")
    return int(proc.returncode or 0)


def _eval_run(run_dir: Path, seg_ids: List[str]) -> RunResult:
    per: List[Dict[str, Any]] = []
    scores: List[float] = []
    passed_n = 0
    for sid in seg_ids:
        w = run_dir / sid
        rep_p = w / "quality_report.json"
        rep = _read_json(rep_p) if rep_p.exists() else {}
        m = e2e_score_from_quality_report(rep)
        ok = bool(m.get("passed", False))
        passed_n += 1 if ok else 0
        scores.append(float(m.get("e2e_score_100", 0.0) or 0.0))
        per.append(
            {
                "id": sid,
                "work_dir": str(w),
                "e2e_score_100": float(m.get("e2e_score_100", 0.0) or 0.0),
                "passed": ok,
                "missing_artifacts_n": int(m.get("missing_artifacts_n", 0) or 0),
                "cjk_hits_n": int(m.get("cjk_hits_n", 0) or 0),
                "long_line_hits_n": int(m.get("long_line_hits_n", 0) or 0),
                "cps_hits_n": int(m.get("cps_hits_n", 0) or 0),
                "overlap_n": int(m.get("overlap_n", 0) or 0),
                "negdur_n": int(m.get("negdur_n", 0) or 0),
                "tts_risk_hits_n": int(m.get("tts_risk_hits_n", 0) or 0),
                "term_missing_n": int(m.get("term_missing_n", 0) or 0),
                "term_forbidden_n": int(m.get("term_forbidden_n", 0) or 0),
                "tts_clip_ratio": float(m.get("tts_clip_ratio", 0.0) or 0.0),
                "errors_n": int(m.get("errors_n", 0) or 0),
                "warnings_n": int(m.get("warnings_n", 0) or 0),
            }
        )
    return RunResult(
        name=run_dir.name,
        n=len(seg_ids),
        passed_rate=round(float(passed_n) / max(len(seg_ids), 1), 4),
        e2e_mean=round(mean(scores), 2),
        bootstrap={},
        per_segment=per,
    )


def _write_md(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Lite mode evaluation flow (Round1/1.5/2) for short datasets")
    ap.add_argument("--segments", default="eval/e2e_quality/segments_short3.docker.jsonl", help="JSONL segments file")
    ap.add_argument("--dataset-name", default="short3", help="Name used in output paths")
    ap.add_argument("--config", default="config/defaults.yaml", help="Base YAML (defaults.yaml)")
    ap.add_argument("--preset", default="normal", help="Preset key: normal/mid/high")
    ap.add_argument("--out-root", default="outputs/eval/e2e_lite_flow", help="Output root under repo")
    ap.add_argument("--bootstrap-iters", type=int, default=2000)
    ap.add_argument("--max-runtime-s", type=int, default=0, help="Hard timeout per segment run (0=disable)")
    ap.add_argument("--skip-round15", action="store_true")
    ap.add_argument("--skip-round2", action="store_true")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    segs = _read_jsonl(repo_root / args.segments)
    seg_ids = [str(s.get("id") or "") for s in segs if str(s.get("id") or "").strip()]
    videos = {str(s.get("id")): str(s.get("video")) for s in segs if str(s.get("id") or "").strip()}

    out_root = (repo_root / args.out_root / args.dataset_name / f"lite_{args.preset}").resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    progress_path = out_root / "lite_test_flow_progress.json"

    def write_progress(stage: str, exp: str, sid: str, msg: str) -> None:
        try:
            payload = {
                "ts": int(time.time()),
                "stage": stage,
                "exp": exp,
                "segment_id": sid,
                "message": msg,
            }
            progress_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # -------------------------
    # Round1: single switches
    # -------------------------
    round1: Dict[str, Dict[str, Any]] = {
        "baseline": {},
        # OFF ablations for default-on lite items
        "asr_normalize_off": {"asr_normalize_enable": False},
        "bilingual_srt_off": {"bilingual_srt": False},
        # ON toggles
        "denoise_on": {"denoise": True},
        # VAD (note: only effective when vad_model exists; still run for regression)
        "vad_on": {"vad_enable": True},
        "sentence_unit_on": {"sentence_unit_enable": True},
        "entity_protect_on": {"entity_protect_enable": True},
        # skip_tts is excluded from score comparison because quality_report requires video/audio artifacts
    }

    def run_group(group_name: str, exp_overrides: Dict[str, Dict[str, Any]]) -> Dict[str, RunResult]:
        results: Dict[str, RunResult] = {}
        baseline_dir = out_root / group_name / "baseline"
        for exp, ov in exp_overrides.items():
            run_dir = out_root / group_name / exp
            for sid in seg_ids:
                video = videos.get(sid)
                if not video:
                    continue
                w = run_dir / sid
                # Skip if already done and has a report
                if (w / "quality_report.json").exists():
                    continue
                print(f"[{group_name}] exp={exp} seg={sid} start")
                write_progress(group_name, exp, sid, "start")
                # Conservative reuse: if baseline exists for this group and override doesn't affect ASR,
                # copy minimal artifacts and resume from mt/tts.
                stage = None
                if exp != "baseline":
                    stage = _needs_resume_stage(ov)
                base_seg_dir = baseline_dir / sid
                effective_overrides = dict(ov or {})
                if stage and base_seg_dir.exists():
                    _prime_from_baseline(baseline_seg_dir=base_seg_dir, exp_seg_dir=w, stage=stage)
                    effective_overrides["resume_from"] = stage

                _run_one_seg(
                    repo_root=repo_root,
                    video=video,
                    out_dir=w,
                    config=args.config,
                    preset=args.preset,
                    overrides=effective_overrides,
                    max_runtime_s=int(args.max_runtime_s or 0),
                )
                print(f"[{group_name}] exp={exp} seg={sid} done")
                write_progress(group_name, exp, sid, "done")
            results[exp] = _eval_run(run_dir, seg_ids)
        return results

    round1_results = run_group("round1", round1)

    # bootstrap p_improve vs baseline (e2e only; lite has no golden ASR/MT absolute score in this flow)
    baseline_scores = [float(it["e2e_score_100"]) for it in round1_results["baseline"].per_segment]
    for exp, rr in round1_results.items():
        if exp == "baseline":
            continue
        scores = [float(it["e2e_score_100"]) for it in rr.per_segment]
        rr.bootstrap = {
            "e2e": bootstrap_prob_improve(baseline_scores, scores, iters=int(args.bootstrap_iters), seed=42),
        }

    # -------------------------
    # Round1.5: param sweeps
    # -------------------------
    round15_results: Dict[str, RunResult] = {}
    round15_plan: Dict[str, List[Any]] = {}
    if not args.skip_round15:
        # 3-point sweep candidates (P/D/A) from docs/轻量模式配置项测试流程.md
        round15_plan = {
            "vad_threshold": [0.5, 0.6, 0.7],
            "vad_min_dur": [0.8, 1.5, 2.0],
            "min_sub_duration": [1.0, 1.5, 2.0],
            "tts_split_len": [60, 80, 120],
            "tts_speed_max": [1.05, 1.10, 1.20],
        }
        sweep_exps: Dict[str, Dict[str, Any]] = {"baseline": {}}
        for k, pts in round15_plan.items():
            for v in pts:
                sweep_exps[f"{k}={v}"] = {k: v}
        round15_results = run_group("round15", sweep_exps)

        base_scores = [float(it["e2e_score_100"]) for it in round15_results["baseline"].per_segment]
        for exp, rr in round15_results.items():
            if exp == "baseline":
                continue
            scores = [float(it["e2e_score_100"]) for it in rr.per_segment]
            rr.bootstrap = {
                "e2e": bootstrap_prob_improve(base_scores, scores, iters=int(args.bootstrap_iters), seed=42),
            }

    # -------------------------
    # Round2: combos
    # -------------------------
    round2_results: Dict[str, RunResult] = {}
    if not args.skip_round2:
        combos: Dict[str, Dict[str, Any]] = {
            "combo_lite_baseline": {},
            "combo_lite_stable_plus": {
                "asr_normalize_enable": True,
                "bilingual_srt": True,
                # keep min_sub_duration/tts params as defaults unless you decide otherwise
            },
            "combo_lite_quality_plus": {
                "sentence_unit_enable": True,
                "entity_protect_enable": True,
                # vad_enable only makes sense when vad_model exists in deployment
                "vad_enable": True,
            },
        }
        round2_results = run_group("round2", combos)

        base_scores = [float(it["e2e_score_100"]) for it in round2_results["combo_lite_baseline"].per_segment]
        for exp, rr in round2_results.items():
            if exp == "combo_lite_baseline":
                continue
            scores = [float(it["e2e_score_100"]) for it in rr.per_segment]
            rr.bootstrap = {
                "e2e": bootstrap_prob_improve(base_scores, scores, iters=int(args.bootstrap_iters), seed=42),
            }

    # -------------------------
    # Write reports
    # -------------------------
    report_dir = (repo_root / "reports" / "lite").resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_json = report_dir / f"report_{args.dataset_name}_lite_flow_{ts}.json"
    out_md = report_dir / f"report_{args.dataset_name}_lite_flow_{ts}.md"

    def rr_to_dict(rr: RunResult) -> Dict[str, Any]:
        return {
            "n": rr.n,
            "passed_rate": rr.passed_rate,
            "e2e_score_100_mean": rr.e2e_mean,
            "bootstrap": rr.bootstrap,
            "per_segment": rr.per_segment,
        }

    payload = {
        "dataset": args.dataset_name,
        "segments": str(args.segments),
        "preset": args.preset,
        "out_root": str(out_root),
        "params": {"bootstrap_iters": int(args.bootstrap_iters)},
        "round1": {k: rr_to_dict(v) for k, v in round1_results.items()},
        "round15": {"plan": round15_plan, "runs": {k: rr_to_dict(v) for k, v in round15_results.items()}},
        "round2": {k: rr_to_dict(v) for k, v in round2_results.items()},
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # A concise, product-friendly overview (to be copied into docs/轻量测评纵览.md by caller)
    lines: List[str] = []
    lines.append("# 轻量模式测评纵览（Round1 / Round1.5 / Round2）")
    lines.append("")
    lines.append(f"- 数据集：`{args.dataset_name}`（n={len(seg_ids)}）")
    lines.append(f"- 预设：`{args.preset}`")
    lines.append(f"- 产物目录：`{out_root}`")
    lines.append(f"- 原始报告：`{out_json}`")
    lines.append("")

    def add_table(title: str, items: Dict[str, RunResult], baseline_key: str) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| run | passed_rate | e2e_mean | p_improve(e2e) |")
        lines.append("| --- | --- | --- | --- |")
        for k, rr in items.items():
            p = "-"
            if rr.bootstrap.get("e2e") and isinstance(rr.bootstrap["e2e"], dict):
                p = str(rr.bootstrap["e2e"].get("p_improve"))
            lines.append(f"| {k} | {rr.passed_rate} | {rr.e2e_mean} | {p} |")
        lines.append("")
        lines.append(f"> 判读建议：优先看 passed_rate 是否下降；再看 e2e_mean 与 p_improve（一般 p_improve≥0.9 才考虑默认开）。基线为 `{baseline_key}`。")
        lines.append("")

    add_table("Round1：单开关（Lite）", round1_results, "baseline")
    if round15_results:
        add_table("Round1.5：三点扫参（Lite，short3 快筛）", round15_results, "baseline")
    if round2_results:
        add_table("Round2：组合方案（Lite）", round2_results, "combo_lite_baseline")

    _write_md(out_md, "\n".join(lines) + "\n")

    print(str(out_md))


if __name__ == "__main__":
    main()


