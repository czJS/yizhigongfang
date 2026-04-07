from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


def build_task_status_response(status: Any, meta: Dict[str, Any], stage_names: Mapping[object, str]) -> Dict[str, Any]:
    return {
        "id": status.id,
        "video": status.video,
        "state": status.state,
        "stage": status.stage,
        "stage_name": stage_names.get(status.stage, ""),
        "progress": status.progress,
        "message": status.message,
        "started_at": status.started_at,
        "ended_at": status.ended_at,
        "work_dir": str(status.work_dir),
        "mode": status.mode,
        "resume_from": meta.get("resume_from"),
        "created_at": meta.get("created_at"),
        "resumed_at": meta.get("resumed_at"),
    }


def recover_task_status_from_disk(
    *,
    task_id: str,
    work_dir: Path,
    meta: Dict[str, Any],
    state_snapshot: Dict[str, Any],
    stage_names: Mapping[object, str],
) -> Dict[str, Any]:
    mode = str(meta.get("mode") or "lite")
    video = str(state_snapshot.get("video") or meta.get("video") or "")

    log_path = work_dir / "run.log"
    stage: Optional[int] = state_snapshot.get("stage") if isinstance(state_snapshot.get("stage"), int) else None
    started_at: Optional[float] = state_snapshot.get("started_at") if isinstance(state_snapshot.get("started_at"), (int, float)) else None
    ended_at: Optional[float] = state_snapshot.get("ended_at") if isinstance(state_snapshot.get("ended_at"), (int, float)) else None
    message = str(state_snapshot.get("message") or "")
    state = str(
        state_snapshot.get("state")
        or ("completed" if (work_dir / "output_en.mp4").exists() or (work_dir / "output_en_sub.mp4").exists() else "unknown")
    )

    try:
        if log_path.exists():
            txt = log_path.read_text(encoding="utf-8", errors="ignore")
            matches = re.findall(r"^\s*(?:\[task [^\]]+\]\s+)?\[(\d+)/(\d+)\]", txt, flags=re.MULTILINE)
            if matches:
                stage = int(matches[-1][0])
                message = stage_names.get(stage, "")

            started_match = re.search(r"^\[task(?: [^\]]+)?\]\s+started_at:\s*(.+)$", txt, flags=re.MULTILINE)
            if started_match:
                try:
                    started_at = datetime.fromisoformat(started_match.group(1).strip().replace("Z", "+00:00")).timestamp()
                except Exception:
                    started_at = None

            ended_match = re.search(r"^\[task(?: [^\]]+)?\]\s+ended_at:\s*(.+)$", txt, flags=re.MULTILINE)
            if ended_match:
                try:
                    ended_at = datetime.fromisoformat(ended_match.group(1).strip().replace("Z", "+00:00")).timestamp()
                except Exception:
                    ended_at = None

            tail = txt[-5000:]
            if "Paused" in tail or "paused" in tail:
                state = "paused"
            elif "Traceback" in tail or "RuntimeError:" in tail or "Error opening output" in tail or "Error applying option" in tail:
                state = "failed"
            elif ended_at is not None:
                state = "completed"
            else:
                state = "unknown"
    except Exception:
        pass

    progress_snapshot = state_snapshot.get("progress")
    progress = (
        float(progress_snapshot)
        if isinstance(progress_snapshot, (int, float))
        else (round((int(stage) / 8 * 100), 1) if stage else 0.0)
    )
    return {
        "id": task_id,
        "video": video,
        "state": state,
        "stage": stage,
        "stage_name": stage_names.get(stage, ""),
        "progress": progress,
        "message": message or state,
        "started_at": started_at,
        "ended_at": ended_at,
        "work_dir": str(work_dir),
        "mode": mode,
        "resume_from": meta.get("resume_from"),
        "created_at": meta.get("created_at"),
        "resumed_at": meta.get("resumed_at"),
    }
