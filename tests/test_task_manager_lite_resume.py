from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.task_manager import TaskManager


class TaskManagerLiteResumeTest(unittest.TestCase):
    def test_resume_task_fails_fast_when_lite_resume_artifacts_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs_root = Path(tmpdir)
            task_id = "job_resume_missing"
            work_dir = outputs_root / task_id
            work_dir.mkdir(parents=True, exist_ok=True)
            (work_dir / "audio.wav").write_bytes(b"wav")
            (work_dir / "audio.json").write_text("[]", encoding="utf-8")
            (work_dir / "task_meta.json").write_text(
                json.dumps({"video": "demo.mp4", "mode": "lite", "params": {}, "preset": None}, ensure_ascii=False),
                encoding="utf-8",
            )

            manager = TaskManager({"paths": {"outputs_root": str(outputs_root)}})

            with self.assertRaises(ValueError) as ctx:
                manager.resume_task(task_id, "tts")

            self.assertIn("cannot resume lite task from tts", str(ctx.exception))
            self.assertIn("eng.srt", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
