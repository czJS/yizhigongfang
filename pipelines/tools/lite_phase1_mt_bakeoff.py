#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from pipelines.lib.asr.lite_asr import Segment, write_srt
from pipelines.lib.lite_translate import translate_segments
from pipelines.lib.mt.mt import build_translator


DEFAULT_CANDIDATES: List[Dict[str, str]] = [
    {"id": "marian_opus_mt", "label": "Marian opus-mt-zh-en"},
    {"id": "nllb_600m", "label": "NLLB-200-distilled-600M"},
    {"id": "m2m100_418m", "label": "M2M100-418M"},
]

SIDECAR_LLM = {"id": "qwen2_5_1_5b", "label": "Qwen2.5-1.5B-Instruct (sidecar only)"}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_segments(case: Dict[str, Any]) -> List[Segment]:
    audio_json = Path(str(case.get("audio_json") or "")).expanduser()
    if audio_json.exists():
        data = json.loads(audio_json.read_text(encoding="utf-8", errors="ignore") or "[]")
        return [Segment(**item) for item in data if isinstance(item, dict)]
    raise FileNotFoundError(f"audio_json missing for case: {case.get('id')} -> {audio_json}")


def _candidate_model_id(candidate_id: str, args: argparse.Namespace) -> str:
    if candidate_id == "marian_opus_mt":
        return str(args.marian_model)
    if candidate_id == "nllb_600m":
        return str(args.nllb_model)
    if candidate_id == "m2m100_418m":
        return str(args.m2m100_model)
    raise RuntimeError(f"unknown candidate: {candidate_id}")


def _select_candidates(candidate_ids_raw: str) -> List[Dict[str, str]]:
    raw = [part.strip() for part in str(candidate_ids_raw or "").split(",") if part.strip()]
    if not raw:
        return list(DEFAULT_CANDIDATES)
    want = set(raw)
    selected = [item for item in DEFAULT_CANDIDATES if item["id"] in want]
    if not selected:
        raise SystemExit(f"no candidate selected from: {sorted(want)}")
    return selected


def build_manifest(
    cases_path: Path,
    cases_count: int,
    run_id: str,
    hardware_tier: str,
    notes: str,
    candidates: List[Dict[str, str]],
) -> Dict[str, Any]:
    return {
        "phase": "lite_phase1",
        "capability": "mt",
        "run_id": run_id,
        "hardware_tier": hardware_tier,
        "cases_path": str(cases_path),
        "cases_count": cases_count,
        "notes": notes,
        "baseline_candidate": "marian_opus_mt",
        "candidates": candidates,
        "decision_rules": {
            "hard_metrics": ["elapsed_s_mean", "passed_rate", "fail_rate", "artifacts_ok_rate"],
            "allow_replace_only_if": [
                "passed_rate_not_worse_than_baseline",
                "fail_rate_not_worse_than_baseline",
                "artifacts_ok_rate_not_worse_than_baseline",
            ],
        },
        "sidecar_llm_policy": {
            "phase1_default": "not_in_main_decision",
            "optional_probe_candidate": SIDECAR_LLM,
        },
    }


def build_results_template(run_id: str, hardware_tier: str, candidates: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "phase": "lite_phase1",
        "capability": "mt",
        "run_id": run_id,
        "hardware_tier": hardware_tier,
        "baseline_candidate": "marian_opus_mt",
        "candidates": [
            {
                "id": item["id"],
                "label": item["label"],
                "metrics": {
                    "elapsed_s_mean": None,
                    "passed_rate": None,
                    "fail_rate": None,
                    "artifacts_ok_rate": None,
                    "added_cost_vs_baseline": None,
                    "subjective_score": None,
                    "replace_recommendation": None,
                },
                "worst_samples": [],
                "case_results": [],
                "notes": "",
            }
            for item in candidates
        ],
        "sidecar_llm_experiment": {
            "tested": False,
            "candidate": SIDECAR_LLM["id"],
            "model_label": SIDECAR_LLM["label"],
            "metrics": {
                "added_latency_s": None,
                "candidate_lines": None,
                "accepted_lines": None,
                "phase2_recommendation": None,
            },
            "notes": "",
        },
    }


def build_readme(run_id: str, hardware_tier: str, cases_count: int, candidates: List[Dict[str, str]]) -> str:
    candidate_lines = "\n".join(f"- `{item['id']}`" for item in candidates)
    return f"""# lite_phase1 MT bakeoff

run_id: `{run_id}`
hardware_tier: `{hardware_tier}`
cases_count: `{cases_count}`

This folder is generated by `lite_phase1_mt_bakeoff.py`.

Files:
- `manifest.json`: static experiment definition
- `results_template.json`: measured results

Phase1 offline MT candidates:
{candidate_lines}

Shared evaluation policy:
- same Chinese source subtitles for all candidates
- same sentence-unit merge rules
- same offline cache policy
- multilingual models use explicit src/target language codes
"""


def _execute(args: argparse.Namespace, rows: List[Dict[str, Any]], results: Dict[str, Any]) -> None:
    selected_candidates = _select_candidates(args.candidate_ids)
    out_dir = Path(args.out_dir)
    baseline_elapsed: Optional[float] = None
    for cand in results["candidates"]:
        cand_id = str(cand["id"])
        model_id = _candidate_model_id(cand_id, args)
        translate_fn = build_translator(
            model_id=model_id,
            device=args.mt_device,
            cache_dir=str(args.mt_cache_dir) if args.mt_cache_dir else None,
            offline=True,
        )
        case_results: List[Dict[str, Any]] = []
        elapsed_list: List[float] = []
        fail_count = 0
        artifact_ok_count = 0
        for row in rows[: args.limit if args.limit and args.limit > 0 else None]:
            case_id = str(row.get("id") or "")
            case_dir = out_dir / "runs" / cand_id / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            try:
                segments = _load_segments(row)
                t0 = time.time()
                seg_en = translate_segments(
                    segments=segments,
                    translate_fn=translate_fn,
                    sentence_unit_enable=True,
                    sentence_unit_min_chars=12,
                    sentence_unit_max_chars=60,
                    sentence_unit_max_segs=3,
                    sentence_unit_max_gap_s=0.6,
                    sentence_unit_boundary_punct="。！？!?.,",
                    sentence_unit_break_words=["但", "而", "于是", "然后", "忽然", "突然", "不过", "结果", "同时"],
                )
                elapsed_s = round(time.time() - t0, 4)
                eng_srt = case_dir / "eng.srt"
                eng_json = case_dir / "eng.json"
                write_srt(eng_srt, seg_en, text_attr="translation")
                eng_json.write_text(
                    json.dumps([seg.__dict__ for seg in seg_en], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                pred_lines = [str(getattr(seg, "translation", "") or "").strip() for seg in seg_en]
                pred_preview = "\n".join([line for line in pred_lines if line][:4]).strip()
                source_preview = "\n".join([str(seg.text).strip() for seg in segments if str(seg.text).strip()][:4]).strip()
                artifact_ok = eng_srt.exists() and bool(pred_preview)
                status = "ok" if artifact_ok else "artifact_missing"
                if artifact_ok:
                    artifact_ok_count += 1
                else:
                    fail_count += 1
                elapsed_list.append(elapsed_s)
                case_results.append(
                    {
                        "id": case_id,
                        "status": status,
                        "elapsed_s": elapsed_s,
                        "artifact_ok": artifact_ok,
                        "error": "",
                        "video": str(row.get("video") or ""),
                        "source_chs_srt": str(row.get("chs_srt") or ""),
                        "pred_eng_srt": str(eng_srt),
                        "source_preview": source_preview,
                        "pred_preview": pred_preview,
                    }
                )
            except Exception as exc:
                fail_count += 1
                case_results.append(
                    {
                        "id": case_id,
                        "status": "error",
                        "elapsed_s": None,
                        "artifact_ok": False,
                        "error": str(exc),
                        "video": str(row.get("video") or ""),
                        "source_chs_srt": str(row.get("chs_srt") or ""),
                        "pred_eng_srt": "",
                        "source_preview": "",
                        "pred_preview": "",
                    }
                )
        total = len(case_results) or 1
        elapsed_mean = round(statistics.mean(elapsed_list), 4) if elapsed_list else None
        if cand_id == "marian_opus_mt":
            baseline_elapsed = elapsed_mean
        cand["case_results"] = case_results
        cand["metrics"]["elapsed_s_mean"] = elapsed_mean
        cand["metrics"]["passed_rate"] = round((artifact_ok_count / total), 4)
        cand["metrics"]["fail_rate"] = round((fail_count / total), 4)
        cand["metrics"]["artifacts_ok_rate"] = round((artifact_ok_count / total), 4)
        cand["metrics"]["added_cost_vs_baseline"] = (
            round(float(elapsed_mean - baseline_elapsed), 4)
            if elapsed_mean is not None and baseline_elapsed is not None
            else None
        )
        cand["notes"] = "all cases completed" if fail_count == 0 else f"{fail_count} cases failed"


def main() -> None:
    ap = argparse.ArgumentParser(description="Run phase1 MT bakeoff with real offline translation execution.")
    ap.add_argument("--cases", type=Path, required=True, help="Input cases jsonl")
    ap.add_argument("--out-dir", type=Path, required=True, help="Output directory")
    ap.add_argument("--hardware-tier", type=str, default="normal", choices=["normal", "mid", "high"])
    ap.add_argument("--run-id", type=str, required=True, help="Run identifier")
    ap.add_argument("--notes", type=str, default="", help="Optional note")
    ap.add_argument("--execute", action="store_true", help="Run real translation instead of only generating templates")
    ap.add_argument("--limit", type=int, default=0, help="Optional case limit for debugging")
    ap.add_argument("--candidate-ids", type=str, default="", help="Comma separated candidate ids to run; empty means all")
    ap.add_argument("--mt-device", type=str, default="cpu", help="MT device: cpu/cuda/auto")
    ap.add_argument("--mt-cache-dir", type=Path, default=Path("assets/models/common_cache_hf"), help="HF cache dir for multilingual MT models")
    ap.add_argument("--marian-model", type=Path, default=Path("assets/models/lite_mt_marian_opus_mt_zh_en"))
    ap.add_argument("--nllb-model", type=str, default="facebook/nllb-200-distilled-600M")
    ap.add_argument("--m2m100-model", type=str, default="facebook/m2m100_418M")
    args = ap.parse_args()

    rows = _read_jsonl(args.cases)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_candidates = _select_candidates(args.candidate_ids)

    manifest = build_manifest(
        args.cases,
        len(rows[: args.limit if args.limit and args.limit > 0 else None]),
        args.run_id,
        args.hardware_tier,
        args.notes,
        selected_candidates,
    )
    results = build_results_template(args.run_id, args.hardware_tier, selected_candidates)

    if args.execute:
        _execute(args=args, rows=rows, results=results)

    _write_json(out_dir / "manifest.json", manifest)
    _write_json(out_dir / "results_template.json", results)
    (out_dir / "README.md").write_text(build_readme(args.run_id, args.hardware_tier, len(rows), selected_candidates), encoding="utf-8")

    print(str(out_dir / "manifest.json"))
    print(str(out_dir / "results_template.json"))


if __name__ == "__main__":
    main()
