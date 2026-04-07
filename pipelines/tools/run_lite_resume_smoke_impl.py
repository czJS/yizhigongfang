#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


ROOT = _repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.lib.lite_resume import collect_missing_lite_resume_artifacts, normalize_lite_resume_from
from pipelines.tools.run_lite_pipeline_smoke_impl import _resolve_video, run_lite_pipeline_smoke


def _parse_json_dict(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _build_resume_overrides(
    base_overrides_json: str,
    resume_from: str,
    resume_overrides_json: str,
    *,
    skip_tts: bool,
) -> str:
    merged = _parse_json_dict(base_overrides_json)
    merged.update(_parse_json_dict(resume_overrides_json))
    merged["resume_from"] = str(resume_from)
    if str(resume_from) in {"tts", "mux"} and not bool(skip_tts):
        merged["skip_tts"] = False
    return json.dumps(merged, ensure_ascii=False)


def run_lite_resume_smoke(
    *,
    repo_root: Path,
    video: Path,
    output_dir: Path,
    config: str,
    preset: str,
    base_overrides_json: str,
    resume_from: str,
    resume_overrides_json: str,
    max_runtime_s: int,
    skip_tts: bool,
    require_quality_report: bool,
    cleanup_artifacts: bool,
    log_max_kb: int,
    prepare_base: bool,
) -> Dict[str, Any]:
    normalized_resume = normalize_lite_resume_from(resume_from)
    if normalized_resume is None:
        raise SystemExit("resume smoke requires a non-empty --resume-from")

    base_summary: Dict[str, Any] | None = None
    if prepare_base:
        base_summary = run_lite_pipeline_smoke(
            repo_root=repo_root,
            video=video,
            output_dir=output_dir,
            config=config,
            preset=preset,
            overrides_json=base_overrides_json,
            max_runtime_s=max_runtime_s,
            skip_tts=bool(_parse_json_dict(base_overrides_json).get("skip_tts", skip_tts)),
            require_quality_report=require_quality_report,
            cleanup_artifacts=cleanup_artifacts,
            log_max_kb=log_max_kb,
        )
        if not bool(base_summary.get("ok")):
            return {
                "ok": False,
                "prepare_base": True,
                "resume_from": normalized_resume,
                "base_summary": base_summary,
                "precheck_missing_artifacts": [],
                "resume_summary": None,
                "failure_category": "base_smoke_failed",
            }

    precheck_missing = collect_missing_lite_resume_artifacts(output_dir, normalized_resume)
    if precheck_missing:
        return {
            "ok": False,
            "prepare_base": bool(prepare_base),
            "resume_from": normalized_resume,
            "base_summary": base_summary,
            "precheck_missing_artifacts": precheck_missing,
            "resume_summary": None,
            "failure_category": "resume_precheck_failed",
            "failed_stage_guess": normalized_resume,
        }

    resume_summary = run_lite_pipeline_smoke(
        repo_root=repo_root,
        video=video,
        output_dir=output_dir,
        config=config,
        preset=preset,
        overrides_json=_build_resume_overrides(
            base_overrides_json,
            normalized_resume,
            resume_overrides_json,
            skip_tts=bool(skip_tts),
        ),
        max_runtime_s=max_runtime_s,
        skip_tts=skip_tts,
        require_quality_report=require_quality_report,
        cleanup_artifacts=cleanup_artifacts,
        log_max_kb=log_max_kb,
    )
    return {
        "ok": bool(resume_summary.get("ok")),
        "prepare_base": bool(prepare_base),
        "resume_from": normalized_resume,
        "base_summary": base_summary,
        "precheck_missing_artifacts": precheck_missing,
        "resume_summary": resume_summary,
        "failure_category": None if bool(resume_summary.get("ok")) else resume_summary.get("failure_category"),
        "failed_stage_guess": resume_summary.get("failed_stage_guess"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Run lite resume_from smoke with optional base preparation.")
    ap.add_argument("--video", type=str, default="", help="Input video path")
    ap.add_argument("--cases-manifest", type=str, default="", help="Optional JSONL manifest with id/video fields")
    ap.add_argument("--case-id", type=str, default="", help="Case id in manifest")
    ap.add_argument("--output-dir", type=Path, required=True, help="Resume smoke output dir")
    ap.add_argument("--config", type=str, default="configs/defaults.yaml", help="Base config YAML")
    ap.add_argument("--preset", type=str, default="normal", help="Preset key")
    ap.add_argument("--base-overrides-json", type=str, default="", help="Optional JSON overrides for base run")
    ap.add_argument("--resume-from", type=str, required=True, help="Resume stage: asr/mt/tts/mux")
    ap.add_argument("--resume-overrides-json", type=str, default="", help="Optional JSON overrides for resume run")
    ap.add_argument("--max-runtime-s", type=int, default=0, help="Hard timeout for each lite run")
    ap.add_argument("--skip-tts", action="store_true", help="Validate subtitle-only contract on resume run")
    ap.add_argument("--require-quality-report", action="store_true", help="Require quality_report.json in validation")
    ap.add_argument("--cleanup-artifacts", action="store_true", help="Forward cleanup flag to run_lite_e2e.py")
    ap.add_argument("--log-max-kb", type=int, default=256, help="Forward log cap to run_lite_e2e.py")
    ap.add_argument("--no-prepare-base", action="store_true", help="Reuse an existing output-dir instead of running a base smoke first")
    args = ap.parse_args()
    video = _resolve_video(args.video, args.cases_manifest, args.case_id)

    summary = run_lite_resume_smoke(
        repo_root=_repo_root(),
        video=video,
        output_dir=args.output_dir.expanduser().resolve(),
        config=str(args.config),
        preset=str(args.preset),
        base_overrides_json=str(args.base_overrides_json or ""),
        resume_from=str(args.resume_from or ""),
        resume_overrides_json=str(args.resume_overrides_json or ""),
        max_runtime_s=int(args.max_runtime_s or 0),
        skip_tts=bool(args.skip_tts),
        require_quality_report=bool(args.require_quality_report),
        cleanup_artifacts=bool(args.cleanup_artifacts),
        log_max_kb=int(args.log_max_kb),
        prepare_base=not bool(args.no_prepare_base),
    )
    (args.output_dir.expanduser().resolve() / "lite_resume_smoke_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not bool(summary.get("ok")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
