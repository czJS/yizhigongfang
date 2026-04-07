from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation.scripts import lite_api_regress


class LiteApiRegressTest(unittest.TestCase):
    def test_merge_overrides_json_injects_skip_tts(self) -> None:
        raw = lite_api_regress._merge_overrides_json('{"foo": 1}', {"skip_tts": True})
        doc = json.loads(raw)
        self.assertEqual(doc["foo"], 1)
        self.assertTrue(doc["skip_tts"])

    def test_build_smoke_cmd_uses_manifest_selector_and_skip_tts(self) -> None:
        repo_root = ROOT
        args = lite_api_regress.argparse.Namespace(
            config="configs/defaults.yaml",
            preset="normal",
            video="",
            cases_manifest="reports/cases.jsonl",
            case_id="golden20_001",
            smoke_max_runtime_s=180,
            log_max_kb=256,
            smoke_overrides_json="",
            smoke_skip_tts=True,
            smoke_require_quality_report=True,
            cleanup_artifacts=False,
        )
        cmd, summary_path = lite_api_regress._build_smoke_cmd(repo_root, args, Path("/tmp/out"))
        self.assertIn("--cases-manifest", cmd)
        self.assertIn("--case-id", cmd)
        self.assertIn("--skip-tts", cmd)
        self.assertIn("--require-quality-report", cmd)
        overrides_json = cmd[cmd.index("--overrides-json") + 1]
        self.assertTrue(json.loads(overrides_json)["skip_tts"])
        self.assertEqual(summary_path, Path("/tmp/out/lite_smoke_summary.json"))

    def test_build_golden20_cmd_forces_explicit_empty_overrides(self) -> None:
        repo_root = ROOT
        args = lite_api_regress.argparse.Namespace(
            golden20_manifest="reports/cases.jsonl",
            config="configs/defaults.yaml",
            preset="normal",
            golden20_max_runtime_s=240,
            log_max_kb=256,
            golden20_overrides_json="",
            golden20_skip_tts=False,
            golden20_require_quality_report=True,
            cleanup_artifacts=False,
            golden20_case_id=["golden20_001"],
        )
        cmd, summary_path = lite_api_regress._build_golden20_cmd(repo_root, args, Path("/tmp/golden"))
        self.assertIn("--overrides-json", cmd)
        self.assertEqual(cmd[cmd.index("--overrides-json") + 1], "{}")
        self.assertEqual(summary_path, Path("/tmp/golden/golden20_smoke_suite_summary.json"))

    def test_desktop_test_cmd_runs_vitest_logic_suite(self) -> None:
        self.assertEqual(lite_api_regress._desktop_test_cmd(), ["npm", "run", "test:logic"])

    def test_default_test_targets_include_hard_erase_contract_tests(self) -> None:
        targets = lite_api_regress._default_test_targets()
        self.assertIn("tests/test_lite_command_builder.py", targets)
        self.assertIn("tests/test_lite_pipeline_impl.py", targets)

    def test_auth_gate_cmd_builds_expected_flags(self) -> None:
        cmd = lite_api_regress._auth_gate_cmd(ROOT, "release-gate", "https://auth.example.com")
        self.assertIn("auth_api_regress.py", cmd[1])
        self.assertIn("--release-gate", cmd)
        self.assertEqual(cmd[-2:], ["--base-url", "https://auth.example.com"])

    def test_require_video_selector_rejects_missing_inputs(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "需要提供 --video"):
            lite_api_regress._require_video_selector("", "", "")

    def test_main_runs_profile_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report_dir = root / "reports"
            video = root / "demo.mp4"
            video.write_text("", encoding="utf-8")

            def fake_run(cmd, cwd, stdout, stderr, text, timeout, check):
                if "compileall" in cmd:
                    return type("Proc", (), {"returncode": 0, "stdout": "compileall ok"})()
                if "pytest" in cmd:
                    return type("Proc", (), {"returncode": 0, "stdout": "pytest ok"})()
                if cmd[:3] == ["npm", "run", "test:logic"]:
                    return type("Proc", (), {"returncode": 0, "stdout": "desktop ok"})()
                if "auth_api_regress.py" in " ".join(cmd):
                    return type("Proc", (), {"returncode": 0, "stdout": "auth ok"})()

                out_idx = cmd.index("--output-dir") + 1 if "--output-dir" in cmd else cmd.index("--out-root") + 1
                out_dir = Path(cmd[out_idx])
                out_dir.mkdir(parents=True, exist_ok=True)
                if "run_lite_pipeline_smoke_impl.py" in cmd[1]:
                    (out_dir / "lite_smoke_summary.json").write_text(
                        json.dumps({"ok": True, "runtime_ratio_vs_source": 1.5}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                elif "run_lite_resume_smoke_impl.py" in cmd[1]:
                    (out_dir / "lite_resume_smoke_summary.json").write_text(
                        json.dumps({"ok": True, "resume_from": "tts"}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                elif "run_lite_golden20_smoke_suite_impl.py" in cmd[1]:
                    (out_dir / "golden20_smoke_suite_summary.json").write_text(
                        json.dumps({"all_ok": True, "passed_cases": 3}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                return type("Proc", (), {"returncode": 0, "stdout": "ok"})()

            argv = [
                "lite_api_regress.py",
                "--regression-gate",
                "--video",
                str(video),
                "--report-dir",
                str(report_dir),
                "--golden20-manifest",
                str(root / "cases.jsonl"),
            ]
            with patch("automation.scripts.lite_api_regress.subprocess.run", side_effect=fake_run), patch(
                "automation.scripts.lite_api_regress.importlib.util.find_spec", return_value=object()
            ), patch.object(sys, "argv", argv):
                exit_code = lite_api_regress.main()

            self.assertEqual(exit_code, 0)
            json_reports = sorted(report_dir.glob("lite_api_regress_*.json"))
            self.assertEqual(len(json_reports), 1)
            payload = json.loads(json_reports[0].read_text(encoding="utf-8"))
            self.assertTrue(payload["summary"]["passed"])
            self.assertEqual(payload["summary"]["total"], 7)
            self.assertTrue(any(item["name"] == "A-2 desktop vitest" for item in payload["items"]))
            self.assertTrue(any("auth gate" in str(item["name"]) for item in payload["items"]))

    def test_main_falls_back_to_direct_python_tests_when_pytest_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report_dir = root / "reports"
            video = root / "demo.mp4"
            video.write_text("", encoding="utf-8")

            def fake_run(cmd, cwd, stdout, stderr, text, timeout, check):
                return type("Proc", (), {"returncode": 0, "stdout": "ok"})()

            argv = [
                "lite_api_regress.py",
                "--dev-smoke",
                "--video",
                str(video),
                "--report-dir",
                str(report_dir),
            ]
            with patch("automation.scripts.lite_api_regress.subprocess.run", side_effect=fake_run), patch(
                "automation.scripts.lite_api_regress.importlib.util.find_spec", return_value=None
            ), patch.object(sys, "argv", argv):
                exit_code = lite_api_regress.main()

            self.assertEqual(exit_code, 0)
            payload = json.loads(sorted(report_dir.glob("lite_api_regress_*.json"))[0].read_text(encoding="utf-8"))
            self.assertTrue(payload["summary"]["passed"])
            self.assertTrue(any("fallback" in str(item["name"]) for item in payload["items"]))


if __name__ == "__main__":
    unittest.main()
