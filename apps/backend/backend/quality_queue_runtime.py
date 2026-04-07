from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from backend.task_launch_runtime import PreparedTaskLaunch, QualityQueueItem


def build_quality_queue_state(
    launch: PreparedTaskLaunch,
    *,
    status_factory: Callable[..., Any],
) -> Tuple[QualityQueueItem, Any]:
    queue_item = QualityQueueItem(
        task_id=launch.task_id,
        video_path=launch.video_path,
        work_dir=launch.work_dir,
        log_path=launch.log_path,
        cmd=launch.cmd,
        effective=launch.effective,
        mode=launch.mode,
        replace_existing=launch.replace_existing,
    )
    status = status_factory(
        id=launch.task_id,
        video=launch.video_path,
        work_dir=launch.work_dir,
        log_path=launch.log_path,
        mode=launch.mode,
    )
    status.state = "queued"
    status.stage = 0
    status.progress = 0.0
    status.message = "Queued (waiting for available worker)"
    return queue_item, status


def take_next_quality_queue_item(
    *,
    quality_queue: List[QualityQueueItem],
    quality_running: bool,
) -> Tuple[Optional[QualityQueueItem], bool]:
    if quality_running or not quality_queue:
        return None, quality_running
    item = quality_queue.pop(0)
    return item, True


def activate_queued_quality_task(
    status: Any,
    *,
    stage_names: Mapping[object, str],
    now: float,
) -> bool:
    if not status or status.state == "cancelled":
        return False
    status.state = "running"
    if status.stage in {None, 0}:
        status.stage = 1
    status.message = stage_names.get(status.stage, "Running")
    status.started_at = now
    return True


def sweep_quality_runtime_state(
    *,
    tasks: List[Any],
    write_snapshot: Callable[[Any], None],
    now: float,
) -> bool:
    running_quality = False
    for st in list(tasks):
        if not st or str(st.mode or "") != "quality":
            continue
        proc = st.proc
        if proc is not None:
            try:
                rc = proc.poll()
            except Exception:
                rc = None
            if rc is not None and st.state == "running":
                st.return_code = rc
                if st.ended_at is not None and "pause" in str(st.message or "").lower():
                    st.state = "paused"
                else:
                    st.state = "failed" if int(rc or 0) != 0 else "completed"
                st.message = "Paused (awaiting user action)" if st.state == "paused" else ("Done" if st.state == "completed" else f"Exited with {rc}")
                if st.ended_at is None:
                    st.ended_at = now
                write_snapshot(st)
                try:
                    st.proc = None
                except Exception:
                    pass
                continue
        if st.state == "running":
            if st.ended_at is not None:
                if "pause" in str(st.message or "").lower():
                    st.state = "paused"
                elif "cancel" in str(st.message or "").lower():
                    st.state = "cancelled"
                else:
                    st.state = "unknown"
                write_snapshot(st)
                continue
            running_quality = True
    return running_quality


def handle_quality_spawn_failure(
    *,
    status: Any,
    exc: Exception,
    write_snapshot: Callable[[Any], None],
    now: float,
) -> Any:
    if status and status.state != "cancelled":
        status.state = "failed"
        status.message = f"Failed to start: {type(exc).__name__}"
        status.ended_at = now
        status.return_code = 127
        write_snapshot(status)
    return status


def reconcile_quality_spawned_task(
    *,
    status: Any,
    write_snapshot: Callable[[Any], None],
    now: float,
) -> Tuple[bool, Any]:
    if not status or status.state != "running":
        return False, status
    try:
        if status.proc is not None and status.proc.poll() is not None:
            status.return_code = status.proc.returncode
            status.state = "failed" if (status.proc.returncode or 0) != 0 else "completed"
            status.message = "Done" if status.state == "completed" else f"Exited with {status.proc.returncode}"
            status.ended_at = now
            write_snapshot(status)
            return False, status
    except Exception:
        pass
    started_at = status.started_at if getattr(status, "started_at", None) is not None else now
    if status.proc is None and (now - started_at) > 10.0:
        status.state = "failed"
        status.message = "Failed to start (missing process handle)"
        status.ended_at = now
        status.return_code = 127
        write_snapshot(status)
        return False, status
    return True, status
