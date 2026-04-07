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

from backend.task_files_runtime import (
    prepare_log_path,
    read_json_best_effort,
    read_log_chunk_for_task,
    serialize_task_status,
    task_meta_path,
    task_state_path,
    write_json_best_effort,
)


class TaskFilesRuntimeTest(unittest.TestCase):
    def test_prepare_log_path_rotates_existing_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            (work_dir / "run.log").write_text("old", encoding="utf-8")

            new_path = prepare_log_path(work_dir)

            self.assertEqual(new_path, work_dir / "run.log")
            rotated = list(work_dir.glob("run.*.log"))
            self.assertEqual(len(rotated), 1)

    def test_write_and_read_json_best_effort_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            meta_path = task_meta_path(work_dir)
            payload = {"foo": "bar"}

            write_json_best_effort(meta_path, payload)

            self.assertEqual(read_json_best_effort(meta_path), payload)

    def test_serialize_task_status_includes_runtime_fields(self) -> None:
        status = SimpleNamespace(
            id="job1",
            video="demo.mp4",
            state="running",
            stage=2,
            progress=50.0,
            message="ASR",
            started_at=1.0,
            ended_at=None,
            work_dir=Path("/tmp/job1"),
            log_path=Path("/tmp/job1/run.log"),
            mode="lite",
            return_code=None,
        )

        actual = serialize_task_status(status, updated_at=9.0)

        self.assertEqual(actual["id"], "job1")
        self.assertEqual(actual["stage"], 2)
        self.assertEqual(actual["updated_at"], 9.0)

    def test_read_log_chunk_for_task_returns_queued_synthetic_log(self) -> None:
        status = SimpleNamespace(state="queued", log_path=Path("/tmp/definitely_missing.log"), message="Queued now")

        chunk, next_offset = read_log_chunk_for_task(
            task_id="job2",
            task_status=status,
            work_dir=None,
            offset=0,
            limit=8000,
        )

        self.assertIn("Queued now", chunk)
        self.assertEqual(next_offset, len(chunk))

    def test_read_log_chunk_for_task_reads_tail_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            log_path = work_dir / "run.log"
            log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")

            chunk, next_offset = read_log_chunk_for_task(
                task_id="job3",
                task_status=None,
                work_dir=work_dir,
                offset=-6,
                limit=8000,
            )

            self.assertIn("ine3", chunk)
            self.assertEqual(next_offset, len(log_path.read_text(encoding="utf-8")))

    def test_task_state_path_uses_stable_name(self) -> None:
        self.assertEqual(task_state_path(Path("/tmp/job")).name, "task_state.json")


if __name__ == "__main__":
    unittest.main()
