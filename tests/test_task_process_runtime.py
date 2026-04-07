from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.task_process_runtime import (
    apply_exit_to_task_status,
    format_task_timestamp,
    write_quality_report_best_effort,
    write_task_log_footer,
    write_task_log_header,
)


class TaskProcessRuntimeTest(unittest.TestCase):
    def test_write_task_log_header_and_footer_emit_metadata(self) -> None:
        buf = io.StringIO()
        status = SimpleNamespace(started_at=100.0, ended_at=110.0)

        write_task_log_header(buf, "job1", status, ["python3", "run.py"])
        write_task_log_footer(buf, "job1", status)

        text = buf.getvalue()
        self.assertIn("[task job1] started_at:", text)
        self.assertIn("[task job1] cmd: python3 run.py", text)
        self.assertIn("[task job1] ended_at:", text)
        self.assertIn("[task job1] duration_s:", text)

    def test_apply_exit_to_task_status_marks_hw_limited_failure(self) -> None:
        status = SimpleNamespace(
            state="running",
            message="",
            stage=2,
            progress=40.0,
            return_code=None,
            ended_at=None,
        )

        actual = apply_exit_to_task_status(
            status,
            return_code=137,
            log_path=Path("/tmp/run.log"),
            log_has_hw_limit=lambda _p: False,
        )

        self.assertEqual(actual.state, "failed")
        self.assertIn("硬件性能不足", actual.message)

    def test_apply_exit_to_task_status_marks_completed_and_finishes_progress(self) -> None:
        status = SimpleNamespace(
            state="running",
            message="",
            stage=6,
            progress=75.0,
            return_code=None,
            ended_at=None,
        )

        actual = apply_exit_to_task_status(
            status,
            return_code=0,
            log_path=Path("/tmp/run.log"),
            log_has_hw_limit=lambda _p: False,
        )

        self.assertEqual(actual.state, "completed")
        self.assertEqual(actual.stage, 8)
        self.assertEqual(actual.progress, 100.0)

    def test_write_quality_report_best_effort_writes_error_marker_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            status = SimpleNamespace(state="failed", work_dir=work_dir, mode="lite", video="demo.mp4")

            def _raise_report(**_kwargs):
                raise RuntimeError("boom")

            write_quality_report_best_effort(
                task_id="job2",
                status=status,
                cfg={},
                generate_quality_report=_raise_report,
                write_quality_report=lambda _path, _doc: None,
            )

            self.assertTrue((work_dir / "quality_report.error.txt").exists())

    def test_format_task_timestamp_returns_iso_like_value(self) -> None:
        value = format_task_timestamp(100.0)
        self.assertIn("T", value)


if __name__ == "__main__":
    unittest.main()
