from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List


def format_task_timestamp(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds).astimezone().isoformat(timespec="seconds")


def write_task_log_header(log_file: Any, task_id: str, status: Any, cmd: List[str]) -> None:
    log_file.write(f"[task {task_id}] started_at: {format_task_timestamp(status.started_at)}\n")
    try:
        log_file.write(f"[task {task_id}] cmd: {' '.join([str(x) for x in (cmd or [])])}\n")
    except Exception:
        pass
    log_file.flush()


def write_task_log_footer(log_file: Any, task_id: str, status: Any) -> None:
    ended_at = status.ended_at or time.time()
    log_file.write(f"[task {task_id}] ended_at:   {format_task_timestamp(ended_at)}\n")
    log_file.write(f"[task {task_id}] duration_s: {ended_at - status.started_at:.3f}\n")
    log_file.flush()


def apply_exit_to_task_status(status: Any, *, return_code: int, log_path: Path, log_has_hw_limit: Callable[[Path], bool]) -> Any:
    status.return_code = return_code
    status.ended_at = time.time()
    if status.state != "cancelled":
        if return_code == 3:
            status.state = "paused"
            status.message = "Paused (awaiting user action)"
        else:
            status.state = "completed" if return_code == 0 else "failed"
            status.message = "Done" if return_code == 0 else f"Exited with {return_code}"
            if return_code == 0:
                status.stage = max(int(status.stage or 0), 8)
                status.progress = 100.0
            if status.state == "failed" and (return_code in {9, 137} or log_has_hw_limit(log_path)):
                status.message = "硬件性能不足导致任务失败，请使用轻量模式（lite）重试。"
    return status


def write_quality_report_best_effort(
    *,
    task_id: str,
    status: Any,
    cfg: Dict[str, Any],
    generate_quality_report: Callable[..., Dict[str, Any]],
    write_quality_report: Callable[[Path, Dict[str, Any]], None],
) -> None:
    try:
        if status.state in {"completed", "failed"}:
            report_path = status.work_dir / "quality_report.json"
            report = generate_quality_report(
                task_id=task_id,
                mode=status.mode,
                work_dir=status.work_dir,
                source_video=Path(status.video) if status.video else None,
                cfg=cfg,
            )
            write_quality_report(report_path, report)
    except Exception as exc:
        try:
            (status.work_dir / "quality_report.error.txt").write_text(str(exc), encoding="utf-8")
        except Exception:
            pass
