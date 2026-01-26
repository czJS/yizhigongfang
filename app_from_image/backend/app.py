import json
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import uuid

from backend.config import load_defaults
from backend.hardware import detect_hardware, recommended_presets
from backend.task_manager import TaskManager


def create_app(config_path: Path) -> Flask:
    cfg = load_defaults(config_path)
    app = Flask(__name__)
    CORS(app)

    manager = TaskManager(cfg)
    repo_root = Path(__file__).resolve().parents[1]

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/hardware")
    def hardware():
        info = detect_hardware()
        return {
            "cpu_cores": info.cpu_cores,
            "memory_gb": info.memory_gb,
            "gpu_name": info.gpu_name,
            "gpu_vram_gb": info.gpu_vram_gb,
            "tier": info.tier,
            "presets": recommended_presets(),
        }

    @app.get("/api/presets")
    def presets():
        return cfg.get("presets", {})

    @app.post("/api/tasks/start")
    def start_task():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        video = payload.get("video")
        params = payload.get("params") or {}
        preset = payload.get("preset")
        if not video:
            return jsonify({"error": "video is required"}), 400
        video_path = Path(video)
        if not video_path.exists():
            return jsonify({"error": f"video not found: {video}"}), 400
        task_id = manager.start_task(str(video_path), params, preset)
        return {"task_id": task_id}

    @app.get("/api/tasks/<task_id>/status")
    def task_status(task_id: str):
        status = manager.get_status(task_id)
        if not status:
            return jsonify({"error": "not found"}), 404
        return status

    @app.post("/api/tasks/<task_id>/cancel")
    def cancel_task(task_id: str):
        ok = manager.cancel(task_id)
        if not ok:
            return jsonify({"error": "not found or not running"}), 404
        return {"status": "cancelled"}

    @app.get("/api/tasks/<task_id>/log")
    def task_log(task_id: str):
        try:
            offset = int(request.args.get("offset", 0))
        except Exception:
            offset = 0
        data = manager.read_log(task_id, offset=offset)
        return {"content": data, "next_offset": offset + len(data)}

    @app.get("/api/tasks/<task_id>/artifacts")
    def artifacts(task_id: str):
        files = manager.list_artifacts(task_id)
        return {"files": files}

    @app.get("/api/tasks/<task_id>/download")
    def download(task_id: str):
        path_str = request.args.get("path")
        if not path_str:
            return jsonify({"error": "path is required"}), 400
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "file not found"}), 404
        return send_file(path, as_attachment=True)

    @app.get("/api/config")
    def config():
        return cfg

    @app.post("/api/upload")
    def upload():
        """接收前端拖拽的文件，保存到 outputs/uploads，下发容器内可用的路径。"""
        if "file" not in request.files:
            return jsonify({"error": "file is required"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "empty filename"}), 400
        uploads_dir = manager.outputs_root / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        filename = secure_filename(f.filename)
        if not filename:
            filename = uuid.uuid4().hex
        dest = uploads_dir / filename
        f.save(dest)
        return {"path": str(dest)}

    return app


def main():
    root = Path(__file__).resolve().parents[1]
    config_dir = (root / "configs") if (root / "configs").exists() else (root / "config")
    config_path = config_dir / "defaults.yaml"
    app = create_app(config_path)
    # 绑定到 0.0.0.0 以便容器端口映射可从宿主访问
    app.run(host="0.0.0.0", port=5175, debug=False)


if __name__ == "__main__":
    main()


