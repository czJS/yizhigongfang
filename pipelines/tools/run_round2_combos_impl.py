#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(p.read_text(encoding="utf-8", errors="ignore") or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _run(cmd: list[str]) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    p = subprocess.run(cmd, env=env)
    return int(p.returncode)


def main() -> None:
    ap = argparse.ArgumentParser(description="Round2: 3 combos full delivery on golden9.")
    ap.add_argument("--wait-progress", type=Path, default=Path("/app/outputs/eval/round1_onejob_progress.json"))
    ap.add_argument("--poll-s", type=int, default=120)
    ap.add_argument("--no-wait", action="store_true", help="Do not wait for round1 progress file to reach all_done.")
    ap.add_argument("--done-marker", type=Path, default=Path("/app/outputs/eval/round2_combos_done.json"))
    ap.add_argument("--out-root", type=Path, default=Path("/app/outputs/eval/e2e_quality_golden9_round2_combos"))
    ap.add_argument("--segments", type=Path, default=Path("/app/eval/suites/e2e_quality/datasets/segments_golden_9.docker.jsonl"))
    ap.add_argument("--experiments", type=Path, default=Path("/app/eval/suites/e2e_quality/experiments/experiments.round2_combos.yaml"))
    ap.add_argument("--base-config", type=Path, default=Path("/app/configs/quality.yaml"))
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--bootstrap-iters", type=int, default=2000)
    ap.add_argument("--report-json", type=Path, default=Path("/app/eval/reports/e2e_quality/report_golden9_round2_combos.json"))
    ap.add_argument("--report-md", type=Path, default=Path("/app/eval/reports/e2e_quality/report_golden9_round2_combos.md"))
    args = ap.parse_args()

    # If already done, stay idle to avoid restart loops (compose restart unless-stopped).
    if args.done_marker.exists():
        print(f"[round2] done marker exists: {args.done_marker}. idle.", flush=True)
        while True:
            time.sleep(3600)

    # Wait for round1 onejob completion (best-effort). If the progress file is missing, we still run.
    if not bool(args.no_wait):
        while args.wait_progress.exists():
            doc = _read_json(args.wait_progress)
            if doc.get("status") == "all_done":
                break
            print(f"[round2] waiting for round1 onejob... ts={doc.get('ts')} stage={doc.get('stage')} status={doc.get('status')}", flush=True)
            time.sleep(max(10, int(args.poll_s)))

    print("[round2] starting combos...", flush=True)
    args.out_root.mkdir(parents=True, exist_ok=True)

    rc = _run(
        [
            sys.executable,
            "/app/pipelines/tools/run_quality_e2e_impl.py",
            "--segments",
            str(args.segments),
            "--experiments",
            str(args.experiments),
            "--base-config",
            str(args.base_config),
            "--out-root",
            str(args.out_root),
            "--jobs",
            str(int(args.jobs)),
        ]
    )
    print(f"[round2] run_quality_e2e done rc={rc}", flush=True)

    # Evaluate (best-effort).
    _ = _run(
        [
            sys.executable,
            "/app/pipelines/tools/eval_quality_e2e_golden_suite_impl.py",
            "--segments",
            str(args.segments),
            "--baseline",
            str(args.out_root / "baseline"),
            "--runs",
            f"super_quality_v1={args.out_root}/super_quality_v1",
            f"super_quality_v2={args.out_root}/super_quality_v2",
            f"super_quality_v3={args.out_root}/super_quality_v3",
            "--bootstrap-iters",
            str(int(args.bootstrap_iters)),
            "--out-json",
            str(args.report_json),
            "--out-md",
            str(args.report_md),
        ]
    )

    args.done_marker.write_text(
        json.dumps({"ts": _now_iso(), "rc": rc, "out_root": str(args.out_root)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[round2] done. report: {args.report_md}", flush=True)

    # idle
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()



