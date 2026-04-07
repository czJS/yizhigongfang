#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


ROOT = _repo_root()
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.tools.run_lite_pipeline_smoke_impl import _resolve_video, run_lite_pipeline_smoke


DEFAULT_CASES_MANIFEST = "reports/lite_phase1/golden20_lite_1min/cases.jsonl"
REPRESENTATIVE_CATEGORIES = ["narration", "explanatory", "movie_explain"]


def _load_manifest_rows(cases_manifest: str) -> List[Dict[str, Any]]:
    manifest = Path(cases_manifest).expanduser().resolve()
    rows: List[Dict[str, Any]] = []
    for line in manifest.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def select_case_ids(cases_manifest: str, requested_case_ids: List[str]) -> List[str]:
    if requested_case_ids:
        return [str(case_id).strip() for case_id in requested_case_ids if str(case_id).strip()]

    rows = _load_manifest_rows(cases_manifest)
    selected: List[str] = []
    seen = set()
    for category in REPRESENTATIVE_CATEGORIES:
        for row in rows:
            if str((row.get("meta") or {}).get("category") or "").strip() != category:
                continue
            case_id = str(row.get("id") or "").strip()
            if case_id and case_id not in seen:
                selected.append(case_id)
                seen.add(case_id)
                break
    return selected


def summarize_suite(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if bool(item.get("ok")))
    runtime_ratios = [
        float(item["runtime_ratio_vs_source"])
        for item in results
        if isinstance(item.get("runtime_ratio_vs_source"), (int, float))
    ]
    return {
        "total_cases": total,
        "passed_cases": passed,
        "passed_rate": round(float(passed) / max(total, 1), 4),
        "all_ok": passed == total,
        "timed_out_cases": [str(item.get("case_id") or "") for item in results if bool(item.get("timed_out"))],
        "avg_runtime_ratio_vs_source": round(sum(runtime_ratios) / len(runtime_ratios), 4) if runtime_ratios else None,
        "max_runtime_ratio_vs_source": round(max(runtime_ratios), 4) if runtime_ratios else None,
        "case_ids": [str(item.get("case_id") or "") for item in results],
        "results": results,
    }


def run_golden20_smoke_suite(
    *,
    repo_root: Path,
    cases_manifest: str,
    case_ids: List[str],
    out_root: Path,
    config: str,
    preset: str,
    overrides_json: str,
    max_runtime_s: int,
    skip_tts: bool,
    require_quality_report: bool,
    cleanup_artifacts: bool,
    log_max_kb: int,
) -> Dict[str, Any]:
    out_root.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []
    for case_id in case_ids:
        video = _resolve_video("", cases_manifest, case_id)
        work_dir = out_root / case_id
        summary = run_lite_pipeline_smoke(
            repo_root=repo_root,
            video=video,
            output_dir=work_dir,
            config=config,
            preset=preset,
            overrides_json=overrides_json,
            max_runtime_s=max_runtime_s,
            skip_tts=skip_tts,
            require_quality_report=require_quality_report,
            cleanup_artifacts=cleanup_artifacts,
            log_max_kb=log_max_kb,
        )
        summary["case_id"] = case_id
        summary["video"] = str(video)
        results.append(summary)
    return summarize_suite(results)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run representative golden20 lite smoke suite.")
    ap.add_argument("--cases-manifest", type=str, default=DEFAULT_CASES_MANIFEST, help="golden20 cases manifest")
    ap.add_argument("--case-id", action="append", default=[], help="Optional case id(s); repeatable")
    ap.add_argument("--out-root", type=Path, required=True, help="Output root for suite run")
    ap.add_argument("--config", type=str, default="configs/defaults.yaml", help="Base config YAML")
    ap.add_argument("--preset", type=str, default="normal", help="Preset key")
    ap.add_argument("--overrides-json", type=str, default='{"skip_tts": true}', help="Optional lite overrides JSON")
    ap.add_argument("--max-runtime-s", type=int, default=240, help="Hard timeout per case")
    ap.add_argument("--skip-tts", action="store_true", help="Validate subtitle-only contract")
    ap.add_argument("--require-quality-report", action="store_true", help="Require quality_report.json")
    ap.add_argument("--cleanup-artifacts", action="store_true", help="Forward cleanup flag")
    ap.add_argument("--log-max-kb", type=int, default=256, help="Forward log cap")
    args = ap.parse_args()

    case_ids = select_case_ids(args.cases_manifest, list(args.case_id or []))
    if not case_ids:
        raise SystemExit("未找到可执行的 golden20 case 列表")

    summary = run_golden20_smoke_suite(
        repo_root=_repo_root(),
        cases_manifest=args.cases_manifest,
        case_ids=case_ids,
        out_root=args.out_root.expanduser().resolve(),
        config=str(args.config),
        preset=str(args.preset),
        overrides_json=str(args.overrides_json or ""),
        max_runtime_s=int(args.max_runtime_s or 0),
        skip_tts=bool(args.skip_tts),
        require_quality_report=bool(args.require_quality_report),
        cleanup_artifacts=bool(args.cleanup_artifacts),
        log_max_kb=int(args.log_max_kb),
    )
    summary_path = args.out_root.expanduser().resolve() / "golden20_smoke_suite_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not bool(summary.get("all_ok")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
