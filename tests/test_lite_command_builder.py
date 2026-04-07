from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.lite_command_builder import build_lite_command


class LiteCommandBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.script = self.root / "pipelines" / "lite_pipeline.py"
        self.script.parent.mkdir(parents=True, exist_ok=True)
        self.script.write_text("print('lite')\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _resolve_path(self, value: str) -> Path:
        raw = str(value)
        path = Path(raw)
        return path if path.is_absolute() else (self.root / path)

    def _pick_executable(self, configured: str, _fallbacks: list[str]) -> str:
        return str(self._resolve_path(configured))

    def test_build_lite_command_keeps_kokoro_when_assets_exist(self) -> None:
        kokoro_dir = self.root / "assets" / "models" / "lite_tts_kokoro_onnx"
        kokoro_dir.mkdir(parents=True, exist_ok=True)
        (kokoro_dir / "kokoro-v1.0.onnx").write_text("onnx", encoding="utf-8")
        (kokoro_dir / "voices-v1.0.bin").write_text("bin", encoding="utf-8")

        cmd = build_lite_command(
            video_path="video.mp4",
            work_dir=self.root / "outputs" / "job1",
            cfg={
                "tts_backend": "kokoro_onnx",
                "sample_rate": 16000,
                "mt_batch_enable": True,
            },
            paths={},
            script=self.script,
            resume_from=None,
            resolve_path=self._resolve_path,
            pick_executable=self._pick_executable,
            packaged_exe=False,
            sys_executable="/usr/bin/python3",
            env={},
        )

        self.assertEqual(cmd[0], "/usr/bin/python3")
        self.assertEqual(cmd[1], str(self.script))
        self.assertIn("--offline", cmd)
        self.assertIn("--asr-normalize-enable", cmd)
        self.assertIn("--tts-backend", cmd)
        self.assertEqual(cmd[cmd.index("--tts-backend") + 1], "kokoro_onnx")
        self.assertIn("--mt-batch-enable", cmd)

    def test_build_lite_command_normalizes_removed_piper_backend_to_kokoro(self) -> None:
        kokoro_dir = self.root / "assets" / "models" / "lite_tts_kokoro_onnx"
        kokoro_dir.mkdir(parents=True, exist_ok=True)
        (kokoro_dir / "kokoro-v1.0.onnx").write_text("onnx", encoding="utf-8")
        (kokoro_dir / "voices-v1.0.bin").write_text("bin", encoding="utf-8")

        cmd = build_lite_command(
            video_path="video.mp4",
            work_dir=self.root / "outputs" / "job_piper_removed",
            cfg={
                "tts_backend": "piper",
                "sample_rate": 16000,
            },
            paths={},
            script=self.script,
            resume_from=None,
            resolve_path=self._resolve_path,
            pick_executable=self._pick_executable,
            packaged_exe=False,
            sys_executable="/usr/bin/python3",
            env={},
        )

        self.assertEqual(cmd[cmd.index("--tts-backend") + 1], "kokoro_onnx")
        self.assertNotIn("--piper-model", cmd)
        self.assertNotIn("--piper-bin", cmd)

    def test_build_lite_command_aligns_subtitle_policy_with_tighter_quality_gates(self) -> None:
        cmd = build_lite_command(
            video_path="video.mp4",
            work_dir=self.root / "outputs" / "job_subtitle_policy",
            cfg={
                "sample_rate": 16000,
                "quality_gates": {"max_chars_per_line": 36, "max_cps": 18.0},
            },
            paths={},
            script=self.script,
            resume_from=None,
            resolve_path=self._resolve_path,
            pick_executable=self._pick_executable,
            packaged_exe=False,
            sys_executable="/usr/bin/python3",
            env={},
        )

        self.assertIn("--subtitle-max-chars-per-line", cmd)
        self.assertEqual(cmd[cmd.index("--subtitle-max-chars-per-line") + 1], "36")
        self.assertIn("--subtitle-max-cps", cmd)
        self.assertEqual(cmd[cmd.index("--subtitle-max-cps") + 1], "18.0")
        self.assertIn("--subtitle-max-lines", cmd)
        self.assertEqual(cmd[cmd.index("--subtitle-max-lines") + 1], "2")

    def test_build_lite_command_passes_p2_2_delivery_controls(self) -> None:
        cmd = build_lite_command(
            video_path="video.mp4",
            work_dir=self.root / "outputs" / "job_delivery",
            cfg={
                "sample_rate": 16000,
                "tts_plan_safety_margin": 0.12,
            },
            paths={},
            script=self.script,
            resume_from=None,
            resolve_path=self._resolve_path,
            pick_executable=self._pick_executable,
            packaged_exe=False,
            sys_executable="/usr/bin/python3",
            env={},
        )

        self.assertIn("--tts-plan-safety-margin", cmd)
        self.assertEqual(cmd[cmd.index("--tts-plan-safety-margin") + 1], "0.12")
        self.assertNotIn("--mux-tail-pad-max-s", cmd)

    def test_build_lite_command_passes_erase_and_subtitle_place_controls(self) -> None:
        cmd = build_lite_command(
            video_path="video.mp4",
            work_dir=self.root / "outputs" / "job_erase",
            cfg={
                "sample_rate": 16000,
                "erase_subtitle_enable": True,
                "erase_subtitle_method": "fill",
                "erase_subtitle_coord_mode": "ratio",
                "erase_subtitle_x": 0.11,
                "erase_subtitle_y": 0.72,
                "erase_subtitle_w": 0.77,
                "erase_subtitle_h": 0.16,
                "erase_subtitle_blur_radius": 9,
                "sub_place_enable": True,
                "sub_place_coord_mode": "ratio",
                "sub_place_x": 0.12,
                "sub_place_y": 0.73,
                "sub_place_w": 0.78,
                "sub_place_h": 0.17,
            },
            paths={},
            script=self.script,
            resume_from=None,
            resolve_path=self._resolve_path,
            pick_executable=self._pick_executable,
            packaged_exe=False,
            sys_executable="/usr/bin/python3",
            env={},
        )

        self.assertIn("--erase-subtitle-enable", cmd)
        self.assertEqual(cmd[cmd.index("--erase-subtitle-method") + 1], "fill")
        self.assertEqual(cmd[cmd.index("--erase-subtitle-x") + 1], "0.11")
        self.assertEqual(cmd[cmd.index("--erase-subtitle-h") + 1], "0.16")
        self.assertEqual(cmd[cmd.index("--erase-subtitle-blur-radius") + 1], "9")
        self.assertIn("--sub-place-enable", cmd)
        self.assertEqual(cmd[cmd.index("--sub-place-x") + 1], "0.12")
        self.assertEqual(cmd[cmd.index("--sub-place-h") + 1], "0.17")

    def test_build_lite_command_passes_subtitle_style_controls(self) -> None:
        cmd = build_lite_command(
            video_path="video.mp4",
            work_dir=self.root / "outputs" / "job_style",
            cfg={
                "sample_rate": 16000,
                "sub_font_name": "Arial",
                "sub_font_size": 36,
                "sub_outline": 2,
                "sub_shadow": 1,
                "sub_margin_v": 30,
                "sub_alignment": 5,
            },
            paths={},
            script=self.script,
            resume_from=None,
            resolve_path=self._resolve_path,
            pick_executable=self._pick_executable,
            packaged_exe=False,
            sys_executable="/usr/bin/python3",
            env={},
        )

        self.assertEqual(cmd[cmd.index("--sub-font-name") + 1], "Arial")
        self.assertEqual(cmd[cmd.index("--sub-font-size") + 1], "36")
        self.assertEqual(cmd[cmd.index("--sub-outline") + 1], "2")
        self.assertEqual(cmd[cmd.index("--sub-shadow") + 1], "1")
        self.assertEqual(cmd[cmd.index("--sub-margin-v") + 1], "30")
        self.assertEqual(cmd[cmd.index("--sub-alignment") + 1], "5")

    def test_build_lite_command_uses_packaged_entry_when_requested(self) -> None:
        cmd = build_lite_command(
            video_path="video.mp4",
            work_dir=self.root / "outputs" / "job2",
            cfg={"sample_rate": 16000},
            paths={},
            script=self.script,
            resume_from="mt",
            resolve_path=self._resolve_path,
            pick_executable=self._pick_executable,
            packaged_exe=True,
            sys_executable="/tmp/backend_server.exe",
            env={},
        )

        self.assertEqual(cmd[:3], ["/tmp/backend_server.exe", "--run-pipeline", "lite"])
        self.assertIn("--resume-from", cmd)
        self.assertEqual(cmd[cmd.index("--resume-from") + 1], "mt")

    def test_build_lite_command_prefers_runtime_python_when_present(self) -> None:
        runtime_python = self.root / "runtime" / "python3"
        runtime_python.parent.mkdir(parents=True, exist_ok=True)
        runtime_python.write_text("", encoding="utf-8")

        cmd = build_lite_command(
            video_path="video.mp4",
            work_dir=self.root / "outputs" / "job3",
            cfg={
                "sample_rate": 16000,
                "lite_runtime_python": "runtime/python3",
            },
            paths={},
            script=self.script,
            resume_from=None,
            resolve_path=self._resolve_path,
            pick_executable=self._pick_executable,
            packaged_exe=True,
            sys_executable="/tmp/backend_server.exe",
            env={},
        )

        self.assertEqual(cmd[0], str(runtime_python))
        self.assertEqual(cmd[1], str(self.script))


if __name__ == "__main__":
    unittest.main()
