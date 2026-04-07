from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def prepare_log_path(work_dir: Path) -> Path:
    """
    Keep `run.log` as the latest run; rotate existing logs to `run.<ts>.log`.
    """
    log_path = work_dir / "run.log"
    if log_path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        rotated = work_dir / f"run.{ts}.log"
        try:
            log_path.rename(rotated)
        except Exception:
            try:
                log_path.write_text("", encoding="utf-8")
            except Exception:
                pass
    return work_dir / "run.log"


def write_json_best_effort(path: Path, payload: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def read_json_best_effort(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "{}")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return None


def task_state_path(work_dir: Path) -> Path:
    return work_dir / "task_state.json"


def task_meta_path(work_dir: Path) -> Path:
    return work_dir / "task_meta.json"


def serialize_task_status(status: Any, *, updated_at: float) -> Dict[str, Any]:
    return {
        "id": status.id,
        "video": status.video,
        "state": status.state,
        "stage": status.stage,
        "progress": status.progress,
        "message": status.message,
        "started_at": status.started_at,
        "ended_at": status.ended_at,
        "work_dir": str(status.work_dir),
        "log_path": str(status.log_path),
        "mode": status.mode,
        "return_code": status.return_code,
        "updated_at": updated_at,
    }


def read_log_chunk_for_task(
    *,
    task_id: str,
    task_status: Any,
    work_dir: Optional[Path],
    offset: int,
    limit: int,
) -> Tuple[str, int]:
    if task_status is not None and task_status.state == "queued" and not task_status.log_path.exists():
        msg = task_status.message or "Queued"
        data = f"[task {task_id}] {msg}\n"
    else:
        log_path = task_status.log_path if (task_status and task_status.log_path and task_status.log_path.exists()) else None
        if log_path is None:
            if not work_dir:
                return "", 0
            log_path = work_dir / "run.log"
            if not log_path.exists():
                return "", 0
        data = log_path.read_text(encoding="utf-8", errors="ignore")

    total = len(data)
    if offset < 0:
        start = max(0, total + offset)
        chunk = data[start:total]
        if len(chunk) > limit:
            chunk = chunk[-limit:]
        return chunk, total

    chunk = data[offset : offset + limit]
    return chunk, min(total, offset + len(chunk))
