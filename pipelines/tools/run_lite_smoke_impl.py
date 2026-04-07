#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from pipelines.lib.lite_artifacts import BASE_REQUIRED_LITE_ARTIFACTS, FULL_REQUIRED_LITE_ARTIFACTS


def _read_json(path: Path) -> Dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "{}")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _safe_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _guess_failed_stage(missing: List[str]) -> str | None:
    missing_set = set(missing)
    if "audio.json" in missing_set or "chs.srt" in missing_set:
        return "asr"
    if "eng.srt" in missing_set:
        return "mt"
    if "tts_plan.json" in missing_set or "tts_full.wav" in missing_set:
        return "tts"
    if "output_en.mp4" in missing_set or "output_en_sub.mp4" in missing_set:
        return "mux"
    return None


def _diagnose_failure(
    *,
    missing: List[str],
    run_return_code,
    timed_out: bool,
    require_quality_report: bool,
    report_exists: bool,
    report_passed,
    quality_report_error_exists: bool,
) -> Dict[str, object]:
    if not missing and (not require_quality_report or report_exists):
        if report_exists and report_passed is False:
            return {
                "failure_category": "quality_gate_failed",
                "failure_reasons": ["quality_report_failed"],
                "failed_stage_guess": None,
            }
        if not quality_report_error_exists and (run_return_code in {None, 0}):
            return {"failure_category": None, "failure_reasons": [], "failed_stage_guess": None}

    reasons: List[str] = []
    category: str = "contract_check_failed"
    failed_stage_guess = _guess_failed_stage(missing)

    if timed_out:
        category = "timeout"
        reasons.append("max_runtime_exceeded")
    elif run_return_code not in {None, 0}:
        category = "runtime_error"
        reasons.append(f"run_return_code={run_return_code}")

    if missing:
        if category == "contract_check_failed":
            category = "missing_required_artifacts"
        reasons.append("missing_required_artifacts")
    if require_quality_report and not report_exists:
        if category == "contract_check_failed":
            category = "missing_quality_report"
        reasons.append("quality_report_missing")
    if quality_report_error_exists:
        if category == "contract_check_failed":
            category = "quality_report_error"
        reasons.append("quality_report_generation_failed")
    if report_exists and report_passed is False:
        reasons.append("quality_report_failed")

    return {
        "failure_category": category,
        "failure_reasons": reasons,
        "failed_stage_guess": failed_stage_guess,
    }


def collect_missing_artifacts(work_dir: Path, *, skip_tts: bool) -> List[str]:
    required = list(BASE_REQUIRED_LITE_ARTIFACTS)
    if not skip_tts:
        required.extend(FULL_REQUIRED_LITE_ARTIFACTS)
    return [name for name in required if not (work_dir / name).exists()]


def summarize_lite_smoke(work_dir: Path, *, skip_tts: bool, require_quality_report: bool) -> Dict:
    missing = collect_missing_artifacts(work_dir, skip_tts=skip_tts)
    run_meta = _read_json(work_dir / "lite_run_meta.json")
    report_path = work_dir / "quality_report.json"
    quality_report_error_path = work_dir / "quality_report_error.txt"
    report_exists = report_path.exists()
    report = _read_json(report_path) if report_exists else {}
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    run_elapsed_s = _safe_float(run_meta.get("elapsed_s"))
    source_duration_s = _safe_float(metrics.get("source_duration_s"))
    timed_out = _safe_bool(run_meta.get("timed_out"))
    report_passed = report.get("passed") if report_exists else False
    summary: Dict[str, object] = {
        "work_dir": str(work_dir),
        "skip_tts": bool(skip_tts),
        "required_artifacts": BASE_REQUIRED_LITE_ARTIFACTS
        if skip_tts
        else BASE_REQUIRED_LITE_ARTIFACTS + FULL_REQUIRED_LITE_ARTIFACTS,
        "missing_artifacts": missing,
        "ok": len(missing) == 0,
        "run_meta_exists": bool(run_meta),
        "run_return_code": run_meta.get("return_code"),
        "run_elapsed_s": run_elapsed_s,
        "run_timed_out": timed_out,
        "source_duration_s": source_duration_s,
        "runtime_ratio_vs_source": round(run_elapsed_s / source_duration_s, 4)
        if run_elapsed_s is not None and source_duration_s and source_duration_s > 0
        else None,
        "quality_report_error_exists": quality_report_error_path.exists(),
    }

    if require_quality_report:
        summary["quality_report_exists"] = report_exists
        summary["quality_report_passed"] = report_passed
        summary["ok"] = bool(summary["ok"]) and report_exists
    elif report_exists:
        summary["quality_report_passed"] = report_passed

    summary.update(
        _diagnose_failure(
            missing=missing,
            run_return_code=summary.get("run_return_code"),
            timed_out=timed_out,
            require_quality_report=require_quality_report,
            report_exists=report_exists,
            report_passed=report_passed,
            quality_report_error_exists=bool(summary.get("quality_report_error_exists")),
        )
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate lite mode output contract for smoke checks.")
    ap.add_argument("--work-dir", type=Path, required=True, help="Lite run output directory to validate")
    ap.add_argument("--skip-tts", action="store_true", help="Validate subtitle-only artifact contract")
    ap.add_argument("--require-quality-report", action="store_true", help="Also require quality_report.json to exist")
    args = ap.parse_args()

    summary = summarize_lite_smoke(
        args.work_dir,
        skip_tts=bool(args.skip_tts),
        require_quality_report=bool(args.require_quality_report),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not bool(summary.get("ok")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
