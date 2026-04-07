from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "apps" / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend import app as backend_app


class FakeTaskManager:
    instances = []
    task_work_dir = None
    task_video_path = ""
    task_mode = "lite"

    def __init__(self, cfg):
        self.config = dict(cfg or {})
        self.started = []
        self.cancel_ok = True
        self.outputs_root = Path((cfg or {}).get("paths", {}).get("outputs_root", "."))
        FakeTaskManager.instances.append(self)

    def _reload_config_if_changed(self):
        return None

    def start_task(self, video, params, preset, mode="lite"):
        self.started.append({"video": video, "params": dict(params or {}), "preset": preset, "mode": mode})
        return "lite-task-001"

    def get_status(self, task_id):
        if task_id == "missing":
            return None
        work_dir = str(FakeTaskManager.task_work_dir or (self.outputs_root / task_id))
        return {"task_id": task_id, "work_dir": work_dir, "state": "running"}

    def cancel(self, task_id):
        return self.cancel_ok and task_id != "missing"

    def resume_task(self, task_id, resume_from, params_overrides=None, preset=None):
        if not resume_from:
            raise ValueError("resume_from is required")
        return f"{task_id}-{resume_from}"

    def read_log_chunk(self, task_id, offset=0):
        return (f"log:{task_id}:{offset}", offset + 10)

    def list_artifacts(self, task_id):
        return [{"name": "eng.srt"}, {"name": "quality_report.json"}]

    def cleanup_artifacts(self, task_id, include_resume=False, include_review=False, include_diagnostics=True):
        if task_id == "missing":
            raise ValueError("task not found")
        return {
            "task_id": task_id,
            "include_resume": include_resume,
            "include_review": include_review,
            "include_diagnostics": include_diagnostics,
        }

    def resolve_work_dir(self, task_id):
        if task_id == "missing":
            return None
        return FakeTaskManager.task_work_dir or (self.outputs_root / task_id)

    def resolve_mode(self, task_id):
        return FakeTaskManager.task_mode

    def resolve_video_path(self, task_id):
        return FakeTaskManager.task_video_path


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class BackendLiteApiTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeTaskManager.instances = []

    def _build_client(self, *, extra_env=None, cloud_auth_payload=None):
        tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(tmpdir.name)
        (tmp_root / "assets" / "rules").mkdir(parents=True, exist_ok=True)
        (tmp_root / "assets" / "glossary").mkdir(parents=True, exist_ok=True)
        (tmp_root / "configs").mkdir(parents=True, exist_ok=True)
        config_path = tmp_root / "configs" / "defaults.yaml"
        config_path.write_text("defaults: {}\n", encoding="utf-8")

        cfg = {
            "paths": {"outputs_root": str(tmp_root / "outputs")},
            "defaults": {"default_mode": "lite", "offline": True},
            "available_modes": ["lite"],
            "ui": {},
            "presets": {"normal": {}},
        }
        work_dir = tmp_root / "outputs" / "task-1"
        work_dir.mkdir(parents=True, exist_ok=True)
        demo_video = tmp_root / "demo.mp4"
        demo_video.write_bytes(b"fake-video")
        FakeTaskManager.task_work_dir = work_dir
        FakeTaskManager.task_video_path = str(demo_video)
        FakeTaskManager.task_mode = "lite"

        patchers = [
            patch.object(backend_app, "detect_repo_root", return_value=tmp_root),
            patch.object(backend_app, "load_defaults", return_value=cfg),
            patch.object(backend_app, "load_config_stack", return_value=(cfg, {})),
            patch.object(backend_app, "TaskManager", FakeTaskManager),
            patch.object(
                backend_app,
                "detect_hardware",
                return_value=SimpleNamespace(cpu_cores=8, memory_gb=16, gpu_name=None, gpu_vram_gb=None, tier="normal"),
            ),
            patch.object(backend_app, "recommended_presets", return_value={"lite": ["normal"]}),
            patch.object(backend_app, "build_quality_runtime_resource_check", return_value=[]),
            patch.object(backend_app.shutil, "which", return_value=None),
        ]
        env = {"YGF_REQUIRE_CLOUD_LICENSE": "0"}
        env.update(extra_env or {})
        patchers.append(patch.dict(os.environ, env, clear=False))
        if cloud_auth_payload is not None:
            patchers.append(patch.object(backend_app.urllib.request, "urlopen", return_value=FakeHttpResponse(cloud_auth_payload)))

        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patchers)])
        self.addCleanup(tmpdir.cleanup)

        app = backend_app.create_app(config_path)
        app.config["TESTING"] = True
        return app.test_client()

    def test_start_task_accepts_lite_request_and_forwards_mode(self) -> None:
        client = self._build_client()
        with tempfile.TemporaryDirectory() as workdir:
            video = Path(workdir) / "demo.mp4"
            video.write_bytes(b"fake")

            resp = client.post(
                "/api/tasks/start",
                json={
                    "video": str(video),
                    "mode": "lite",
                    "preset": "normal",
                    "params": {"skip_tts": True, "whispercpp_threads": 4},
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["task_id"], "lite-task-001")
        self.assertEqual(FakeTaskManager.instances[0].started[0]["mode"], "lite")
        self.assertTrue(FakeTaskManager.instances[0].started[0]["params"]["skip_tts"])

    def test_config_endpoint_exposes_runtime_and_available_modes(self) -> None:
        client = self._build_client()

        resp = client.get("/api/config")

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertIn("lite", payload["available_modes"])
        self.assertIn("runtime", payload)
        self.assertIn("config_stack", payload)

    def test_rulesets_global_can_round_trip_default_doc(self) -> None:
        client = self._build_client()
        doc = backend_app.ruleset_default_doc()

        put_resp = client.put("/api/rulesets/global", json=doc)
        get_resp = client.get("/api/rulesets/global")

        self.assertEqual(put_resp.status_code, 200)
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.get_json(), put_resp.get_json())

    def test_task_status_cancel_resume_log_artifacts_and_cleanup_endpoints(self) -> None:
        client = self._build_client()

        status_resp = client.get("/api/tasks/task-1/status")
        cancel_resp = client.post("/api/tasks/task-1/cancel")
        resume_resp = client.post("/api/tasks/task-1/resume", json={"resume_from": "tts"})
        log_resp = client.get("/api/tasks/task-1/log?offset=5")
        artifacts_resp = client.get("/api/tasks/task-1/artifacts")
        cleanup_resp = client.post("/api/tasks/task-1/cleanup", json={"include_resume": True, "include_review": True})

        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.get_json()["task_id"], "task-1")
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertEqual(cancel_resp.get_json()["status"], "cancelled")
        self.assertEqual(resume_resp.status_code, 200)
        self.assertEqual(resume_resp.get_json()["task_id"], "task-1-tts")
        self.assertEqual(log_resp.status_code, 200)
        self.assertEqual(log_resp.get_json()["next_offset"], 15)
        self.assertEqual(artifacts_resp.status_code, 200)
        self.assertEqual(len(artifacts_resp.get_json()["files"]), 2)
        self.assertEqual(cleanup_resp.status_code, 200)
        self.assertTrue(cleanup_resp.get_json()["include_resume"])
        self.assertTrue(cleanup_resp.get_json()["include_review"])

    def test_health_and_hardware_endpoints_return_lite_runtime_info(self) -> None:
        client = self._build_client()

        health_resp = client.get("/api/health")
        hardware_resp = client.get("/api/hardware")

        self.assertEqual(health_resp.status_code, 200)
        self.assertEqual(health_resp.get_json()["status"], "ok")
        self.assertIn("runtime", health_resp.get_json())
        self.assertEqual(hardware_resp.status_code, 200)
        self.assertEqual(hardware_resp.get_json()["cpu_cores"], 8)
        self.assertEqual(hardware_resp.get_json()["tier"], "normal")

    def test_presets_upload_download_and_quality_report_endpoints(self) -> None:
        client = self._build_client()
        work_dir = FakeTaskManager.task_work_dir
        assert work_dir is not None
        (work_dir / "eng.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        (work_dir / "quality_report.json").write_text(json.dumps({"passed": True, "checks": {}}), encoding="utf-8")

        presets_resp = client.get("/api/presets")
        upload_resp = client.post(
            "/api/upload",
            data={"file": (io.BytesIO(b"video"), "upload-demo.mp4")},
            content_type="multipart/form-data",
        )
        download_resp = client.get("/api/tasks/task-1/download", query_string={"path": "eng.srt"})
        quality_resp = client.get("/api/tasks/task-1/quality_report")

        self.assertEqual(presets_resp.status_code, 200)
        self.assertIn("normal", presets_resp.get_json())
        self.assertEqual(upload_resp.status_code, 200)
        self.assertTrue(Path(upload_resp.get_json()["path"]).exists())
        self.assertEqual(download_resp.status_code, 200)
        self.assertIn(b"hello", download_resp.data)
        self.assertEqual(quality_resp.status_code, 200)
        self.assertTrue(quality_resp.get_json()["passed"])
        download_resp.close()

    def test_video_probe_and_frame_endpoints(self) -> None:
        client = self._build_client()
        video_path = Path(FakeTaskManager.task_video_path)

        def fake_run(cmd, stdout=None, stderr=None, text=None, timeout=None):
            exe = Path(cmd[0]).name
            if exe == "ffprobe":
                return type(
                    "Proc",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            {"streams": [{"codec_type": "video", "width": 1280, "height": 720}], "format": {"duration": "12.5"}}
                        ),
                        "stderr": "",
                    },
                )()
            out_path = Path(cmd[-1])
            out_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
            return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch.object(backend_app.shutil, "which", side_effect=lambda name: name), patch.object(
            backend_app.subprocess,
            "run",
            side_effect=fake_run,
        ):
            probe_resp = client.post("/api/video/probe", json={"path": str(video_path)})
            frame_resp = client.post("/api/video/frame", json={"path": str(video_path), "t": 1.2, "max_width": 800})

        self.assertEqual(probe_resp.status_code, 200)
        self.assertEqual(probe_resp.get_json()["width"], 1280)
        self.assertEqual(probe_resp.get_json()["height"], 720)
        self.assertEqual(frame_resp.status_code, 200)
        self.assertEqual(frame_resp.mimetype, "image/png")
        frame_resp.close()

    def test_cloud_license_guard_allows_active_start(self) -> None:
        client = self._build_client(
            extra_env={"YGF_REQUIRE_CLOUD_LICENSE": "1", "YGF_AUTH_API_BASE": "https://auth.example.com"},
            cloud_auth_payload={"user": {"id": 1}, "license": {"status": "active", "active": True}},
        )
        with tempfile.TemporaryDirectory() as workdir:
            video = Path(workdir) / "demo.mp4"
            video.write_bytes(b"fake")
            resp = client.post(
                "/api/tasks/start",
                json={"video": str(video), "mode": "lite", "params": {}},
                headers={"X-YGF-Cloud-Token": "tok-active"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["task_id"], "lite-task-001")
        self.assertEqual(len(FakeTaskManager.instances[0].started), 1)

    def test_cloud_license_guard_blocks_missing_or_expired_token(self) -> None:
        client = self._build_client(
            extra_env={"YGF_REQUIRE_CLOUD_LICENSE": "1", "YGF_AUTH_API_BASE": "https://auth.example.com"},
            cloud_auth_payload={"user": {"id": 1}, "license": {"status": "expired", "active": False}},
        )
        with tempfile.TemporaryDirectory() as workdir:
            video = Path(workdir) / "demo.mp4"
            video.write_bytes(b"fake")
            missing_resp = client.post("/api/tasks/start", json={"video": str(video), "mode": "lite", "params": {}})
            expired_resp = client.post(
                "/api/tasks/start",
                json={"video": str(video), "mode": "lite", "params": {}},
                headers={"X-YGF-Cloud-Token": "tok-expired"},
            )

        self.assertEqual(missing_resp.status_code, 401)
        self.assertIn("云端登录态缺失", missing_resp.get_json()["error"])
        self.assertEqual(expired_resp.status_code, 403)
        self.assertIn("授权已到期", expired_resp.get_json()["error"])
        self.assertEqual(len(FakeTaskManager.instances[0].started), 0)

    def test_cloud_license_guard_blocks_product_mismatch(self) -> None:
        client = self._build_client(
            extra_env={
                "YGF_REQUIRE_CLOUD_LICENSE": "1",
                "YGF_AUTH_API_BASE": "https://auth.example.com",
                "YGF_PRODUCT_EDITION": "lite",
            },
            cloud_auth_payload={
                "user": {"id": 1},
                "license": {"status": "active", "active": True, "product_edition": "quality"},
            },
        )
        with tempfile.TemporaryDirectory() as workdir:
            video = Path(workdir) / "demo.mp4"
            video.write_bytes(b"fake")
            resp = client.post(
                "/api/tasks/start",
                json={"video": str(video), "mode": "lite", "params": {}},
                headers={"X-YGF-Cloud-Token": "tok-quality"},
            )

        self.assertEqual(resp.status_code, 403)
        self.assertIn("quality", resp.get_json()["error"])
        self.assertEqual(len(FakeTaskManager.instances[0].started), 0)

    def test_cloud_license_guard_keeps_readonly_endpoints_available(self) -> None:
        client = self._build_client(
            extra_env={"YGF_REQUIRE_CLOUD_LICENSE": "1", "YGF_AUTH_API_BASE": "https://auth.example.com"},
            cloud_auth_payload={"user": {"id": 1}, "license": {"status": "expired", "active": False}},
        )

        resp = client.get("/api/tasks/task-1/status")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["task_id"], "task-1")


if __name__ == "__main__":
    unittest.main()
