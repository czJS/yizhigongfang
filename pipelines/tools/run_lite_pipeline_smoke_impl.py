#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


ROOT = _repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.tools.run_lite_smoke_impl import summarize_lite_smoke


def _resolve_video(video: str, cases_manifest: str, case_id: str) -> Path:
    raw_video = str(video or "").strip()
    if raw_video:
        return Path(raw_video).expanduser().resolve()
    manifest = Path(str(cases_manifest or "")).expanduser().resolve()
    target_id = str(case_id or "").strip()
    if not manifest.exists():
        raise SystemExit(f"cases manifest not found: {manifest}")
    if not target_id:
        raise SystemExit("需要提供 --video，或同时提供 --cases-manifest 与 --case-id")
    for line in manifest.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("id") or "").strip() == target_id:
            video_path = str(row.get("video") or "").strip()
            if video_path:
                return Path(video_path).expanduser().resolve()
            break
    raise SystemExit(f"未在 manifest 中找到 case_id={target_id}")


def build_smoke_command(
    *,
    repo_root: Path,
    video: Path,
    output_dir: Path,
    config: str,
    preset: str,
    overrides_json: str,
    max_runtime_s: int,
    cleanup_artifacts: bool,
    log_max_kb: int,
) -> List[str]:
    cmd = [
        sys.executable,
        str((repo_root / "scripts" / "run_lite_e2e.py").resolve()),
        "--video",
        str(video),
        "--output-dir",
        str(output_dir),
        "--config",
        str(config),
        "--preset",
        str(preset),
    ]
    if str(overrides_json or "").strip():
        cmd += ["--overrides-json", str(overrides_json)]
    if int(max_runtime_s or 0) > 0:
        cmd += ["--max-runtime-s", str(int(max_runtime_s))]
    if cleanup_artifacts:
        cmd.append("--cleanup-artifacts")
    if int(log_max_kb or 0) >= 0:
        cmd += ["--log-max-kb", str(int(log_max_kb))]
    return cmd


def run_lite_pipeline_smoke(
    *,
    repo_root: Path,
    video: Path,
    output_dir: Path,
    config: str,
    preset: str,
    overrides_json: str,
    max_runtime_s: int,
    skip_tts: bool,
    require_quality_report: bool,
    cleanup_artifacts: bool,
    log_max_kb: int,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_smoke_command(
        repo_root=repo_root,
        video=video,
        output_dir=output_dir,
        config=config,
        preset=preset,
        overrides_json=overrides_json,
        max_runtime_s=max_runtime_s,
        cleanup_artifacts=cleanup_artifacts,
        log_max_kb=log_max_kb,
    )
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
    (output_dir / "smoke_runner.stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (output_dir / "smoke_runner.stderr.log").write_text(proc.stderr or "", encoding="utf-8")

    summary = summarize_lite_smoke(
        output_dir,
        skip_tts=bool(skip_tts),
        require_quality_report=bool(require_quality_report),
    )
    summary["command"] = cmd
    summary["max_runtime_s"] = int(max_runtime_s or 0)
    summary["return_code"] = int(proc.returncode)
    summary["runner_ok"] = int(proc.returncode) == 0
    run_elapsed_s = summary.get("run_elapsed_s")
    summary["timed_out"] = bool(
        bool(summary.get("run_timed_out"))
        or (
            int(max_runtime_s or 0) > 0
            and int(proc.returncode) != 0
            and isinstance(run_elapsed_s, (int, float))
            and float(run_elapsed_s) >= max(float(max_runtime_s) - 1.0, 0.0)
        )
    )
    summary["ok"] = bool(summary.get("ok")) and int(proc.returncode) == 0
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one lite pipeline smoke and validate output contract.")
    ap.add_argument("--video", type=str, default="", help="Input video path")
    ap.add_argument("--cases-manifest", type=str, default="", help="Optional JSONL manifest with id/video fields")
    ap.add_argument("--case-id", type=str, default="", help="Case id in manifest, e.g. golden20_001")
    ap.add_argument("--output-dir", type=Path, required=True, help="Smoke output dir")
    ap.add_argument("--config", type=str, default="configs/defaults.yaml", help="Base config YAML")
    ap.add_argument("--preset", type=str, default="normal", help="Preset key")
    ap.add_argument("--overrides-json", type=str, default="", help="Optional lite overrides JSON")
    ap.add_argument("--max-runtime-s", type=int, default=0, help="Hard timeout for the lite run")
    ap.add_argument("--skip-tts", action="store_true", help="Validate subtitle-only contract")
    ap.add_argument("--require-quality-report", action="store_true", help="Require quality_report.json in smoke validation")
    ap.add_argument("--cleanup-artifacts", action="store_true", help="Forward cleanup flag to run_lite_e2e.py")
    ap.add_argument("--log-max-kb", type=int, default=256, help="Forward log cap to run_lite_e2e.py")
    args = ap.parse_args()
    video = _resolve_video(args.video, args.cases_manifest, args.case_id)

    summary = run_lite_pipeline_smoke(
        repo_root=_repo_root(),
        video=video,
        output_dir=args.output_dir.expanduser().resolve(),
        config=str(args.config),
        preset=str(args.preset),
        overrides_json=str(args.overrides_json or ""),
        max_runtime_s=int(args.max_runtime_s or 0),
        skip_tts=bool(args.skip_tts),
        require_quality_report=bool(args.require_quality_report),
        cleanup_artifacts=bool(args.cleanup_artifacts),
        log_max_kb=int(args.log_max_kb),
    )
    (args.output_dir.expanduser().resolve() / "lite_smoke_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not bool(summary.get("ok")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
