from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.task_launch_runtime import (
    build_bundle_task_meta,
    build_prepared_task_bundle,
    build_prepared_task_launch,
)


class TaskLaunchRuntimeTest(unittest.TestCase):
    def test_build_prepared_task_launch_keeps_runtime_fields(self) -> None:
        launch = build_prepared_task_launch(
            task_id="job1",
            video_path="demo.mp4",
            work_dir=Path("/tmp/job1"),
            log_path=Path("/tmp/job1/run.log"),
            cmd=["python3", "run.py"],
            effective={"offline": True},
            mode="lite",
            replace_existing=True,
        )

        self.assertEqual(launch.task_id, "job1")
        self.assertEqual(launch.mode, "lite")
        self.assertTrue(launch.replace_existing)

    def test_build_prepared_task_bundle_keeps_rules_and_resume_fields(self) -> None:
        launch = build_prepared_task_launch(
            task_id="job2",
            video_path="demo.mp4",
            work_dir=Path("/tmp/job2"),
            log_path=Path("/tmp/job2/run.log"),
            cmd=["python3"],
            effective={},
            mode="lite",
        )
        bundle = build_prepared_task_bundle(
            launch=launch,
            preset="normal",
            cleaned_params={"a": 1},
            rules_disable_global=False,
            rules_template_id="tpl1",
            rules_override={"version": 1},
            effective_rules={"version": 1},
            derived={"glossary_path": Path("/tmp/job2/.ygf_rules/glossary.json")},
            resume_from="mt",
        )

        self.assertEqual(bundle.launch.task_id, "job2")
        self.assertEqual(bundle.rules_template_id, "tpl1")
        self.assertEqual(bundle.resume_from, "mt")

    def test_build_bundle_task_meta_matches_task_meta_contract(self) -> None:
        launch = build_prepared_task_launch(
            task_id="job3",
            video_path="demo.mp4",
            work_dir=Path("/tmp/job3"),
            log_path=Path("/tmp/job3/run.log"),
            cmd=["python3", "run.py"],
            effective={"offline": True},
            mode="lite",
        )
        bundle = build_prepared_task_bundle(
            launch=launch,
            preset="normal",
            cleaned_params={"x": 1},
            rules_disable_global=False,
            rules_template_id=None,
            rules_override=None,
            effective_rules={"version": 1},
            derived={"ruleset_path": Path("/tmp/job3/.ygf_rules/ruleset_effective.json")},
            resume_from="tts",
        )

        meta = build_bundle_task_meta(
            bundle,
            config_stack_meta={"active": "defaults.yaml"},
            effective_hash="abc123",
            created_at=10.0,
            resumed_at=20.0,
        )

        self.assertEqual(meta["task_id"], "job3")
        self.assertEqual(meta["mode"], "lite")
        self.assertEqual(meta["effective_config_hash"], "abc123")
        self.assertEqual(meta["resume_from"], "tts")
        self.assertEqual(meta["created_at"], 10.0)
        self.assertEqual(meta["resumed_at"], 20.0)


if __name__ == "__main__":
    unittest.main()
