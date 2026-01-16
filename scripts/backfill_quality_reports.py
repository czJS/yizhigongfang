#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # type: ignore

# Ensure repo root is importable
_ROOT = Path(__file__).resolve().parents[1]
import sys

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.quality_report import generate_quality_report, write_quality_report


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


def _load_cfg(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8", errors="ignore")) or {}
    return doc if isinstance(doc, dict) else {}


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill missing quality_report.json for existing E2E run directories.")
    ap.add_argument("--segments", type=Path, required=True, help="segments jsonl (id/video/meta)")
    ap.add_argument("--run-dir", type=Path, required=True, help="run dir containing <seg_id>/ artifacts")
    ap.add_argument("--config", type=Path, default=None, help="config yaml (default: env CONFIG_PATH)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing quality_report.json")
    args = ap.parse_args()

    cfg_path = args.config or Path(os.getenv("CONFIG_PATH", "") or "")
    cfg = _load_cfg(Path(cfg_path)) if cfg_path else {}

    segs = _read_jsonl(Path(args.segments))
    by_id: Dict[str, Dict[str, Any]] = {str(s.get("id") or "").strip(): s for s in segs if str(s.get("id") or "").strip()}

    done = 0
    skipped = 0
    missing_dir = 0
    for sid, s in by_id.items():
        seg_dir = Path(args.run_dir) / sid
        if not seg_dir.exists():
            missing_dir += 1
            continue
        out_p = seg_dir / "quality_report.json"
        if out_p.exists() and not args.overwrite:
            skipped += 1
            continue
        video = str(s.get("video") or "").strip()
        src = Path(video) if video else None
        rep = generate_quality_report(
            task_id=str(sid),
            mode="quality",
            work_dir=seg_dir,
            source_video=src if (src and src.exists()) else None,
            cfg=cfg,
        )
        write_quality_report(out_p, rep)
        done += 1
        print(f"[ok] {sid} -> {out_p} passed={bool(rep.get('passed'))}", flush=True)

    print(f"[summary] wrote={done} skipped={skipped} missing_dir={missing_dir}")


if __name__ == "__main__":
    main()


