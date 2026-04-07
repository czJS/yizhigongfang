from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.quality_queue_runtime import (
    activate_queued_quality_task,
    build_quality_queue_state,
    handle_quality_spawn_failure,
    reconcile_quality_spawned_task,
    sweep_quality_runtime_state,
    take_next_quality_queue_item,
)
from backend.task_launch_runtime import PreparedTaskLaunch


STAGE_NAMES = {1: "音频提取"}


class QualityQueueRuntimeTest(unittest.TestCase):
    def test_build_quality_queue_state_creates_queued_status_and_item(self) -> None:
        launch = PreparedTaskLaunch(
            task_id="job1",
            video_path="demo.mp4",
            work_dir=Path("/tmp/job1"),
            log_path=Path("/tmp/job1/run.log"),
            cmd=["python3"],
            effective={},
            mode="quality",
        )

        item, status = build_quality_queue_state(launch, status_factory=SimpleNamespace)

        self.assertEqual(item.task_id, "job1")
        self.assertEqual(status.state, "queued")
        self.assertEqual(status.stage, 0)

    def test_take_next_quality_queue_item_updates_running_flag(self) -> None:
        item = SimpleNamespace(task_id="job2")
        queue = [item]

        next_item, running = take_next_quality_queue_item(quality_queue=queue, quality_running=False)

        self.assertIs(next_item, item)
        self.assertTrue(running)
        self.assertEqual(queue, [])

    def test_activate_queued_quality_task_moves_status_to_running(self) -> None:
        status = SimpleNamespace(state="queued", stage=0, message="", started_at=0.0)

        ok = activate_queued_quality_task(status, stage_names=STAGE_NAMES, now=12.0)

        self.assertTrue(ok)
        self.assertEqual(status.state, "running")
        self.assertEqual(status.stage, 1)
        self.assertEqual(status.message, "音频提取")
        self.assertEqual(status.started_at, 12.0)

    def test_sweep_quality_runtime_state_marks_finished_process(self) -> None:
        writes = []
        proc = SimpleNamespace(poll=lambda: 0)
        status = SimpleNamespace(mode="quality", proc=proc, state="running", ended_at=None, message="", return_code=None)

        running = sweep_quality_runtime_state(
            tasks=[status],
            write_snapshot=lambda st: writes.append(st.state),
            now=5.0,
        )

        self.assertFalse(running)
        self.assertEqual(status.state, "completed")
        self.assertEqual(status.return_code, 0)
        self.assertIn("completed", writes)

    def test_handle_quality_spawn_failure_marks_status_failed(self) -> None:
        writes = []
        status = SimpleNamespace(state="queued", message="", ended_at=None, return_code=None)

        handle_quality_spawn_failure(
            status=status,
            exc=RuntimeError("boom"),
            write_snapshot=lambda st: writes.append(st.message),
            now=9.0,
        )

        self.assertEqual(status.state, "failed")
        self.assertEqual(status.return_code, 127)
        self.assertTrue(writes)

    def test_reconcile_quality_spawned_task_fails_missing_handle(self) -> None:
        writes = []
        status = SimpleNamespace(state="running", proc=None, started_at=0.0, ended_at=None, return_code=None, message="")

        keep_running, _ = reconcile_quality_spawned_task(
            status=status,
            write_snapshot=lambda st: writes.append(st.state),
            now=15.0,
        )

        self.assertFalse(keep_running)
        self.assertEqual(status.state, "failed")
        self.assertEqual(status.return_code, 127)
        self.assertIn("failed", writes)


if __name__ == "__main__":
    unittest.main()
