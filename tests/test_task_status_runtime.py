from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.task_status_runtime import build_task_status_response, recover_task_status_from_disk


STAGE_NAMES = {1: "音频提取", 2: "ASR", 8: "收尾/完成"}


class TaskStatusRuntimeTest(unittest.TestCase):
    def test_build_task_status_response_uses_runtime_and_meta_fields(self) -> None:
        status = SimpleNamespace(
            id="job1",
            video="demo.mp4",
            state="running",
            stage=2,
            progress=25.0,
            message="ASR",
            started_at=100.0,
            ended_at=None,
            work_dir=Path("/tmp/job1"),
            mode="lite",
        )
        meta = {"resume_from": "mt", "created_at": 1.0, "resumed_at": 2.0}

        actual = build_task_status_response(status, meta, STAGE_NAMES)

        self.assertEqual(actual["id"], "job1")
        self.assertEqual(actual["stage_name"], "ASR")
        self.assertEqual(actual["resume_from"], "mt")
        self.assertEqual(actual["created_at"], 1.0)
        self.assertEqual(actual["resumed_at"], 2.0)

    def test_recover_task_status_from_disk_reads_log_and_infers_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "run.log").write_text(
                "\n".join(
                    [
                        "[task job2] started_at: 2026-03-30T10:00:00+00:00",
                        "[task job2] [2/7] Running ASR",
                        "Traceback (most recent call last):",
                    ]
                ),
                encoding="utf-8",
            )

            actual = recover_task_status_from_disk(
                task_id="job2",
                work_dir=work_dir,
                meta={"mode": "lite", "video": "demo.mp4"},
                state_snapshot={"progress": 12.5},
                stage_names=STAGE_NAMES,
            )

            self.assertEqual(actual["state"], "failed")
            self.assertEqual(actual["stage"], 2)
            self.assertEqual(actual["stage_name"], "ASR")
            self.assertEqual(actual["progress"], 12.5)
            self.assertEqual(actual["message"], "ASR")

    def test_recover_task_status_from_disk_marks_completed_from_deliverable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "output_en_sub.mp4").write_text("ok", encoding="utf-8")

            actual = recover_task_status_from_disk(
                task_id="job3",
                work_dir=work_dir,
                meta={"mode": "lite", "video": "demo.mp4"},
                state_snapshot={},
                stage_names=STAGE_NAMES,
            )

            self.assertEqual(actual["state"], "completed")
            self.assertEqual(actual["progress"], 0.0)


if __name__ == "__main__":
    unittest.main()
