import shutil
import subprocess
import os
import json
import re
import time
import sys
import runpy
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse
import urllib.error
import urllib.request

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import uuid

from backend.config import load_defaults, load_config_stack
from backend.hardware import detect_hardware, recommended_presets
from backend.task_manager import TaskManager, apply_quality_fixed_policies
from backend.glossary_store import load_glossary, save_glossary
from backend.ruleset_store import (
    default_doc as ruleset_default_doc,
    glossary_doc_to_ruleset_asr_fixes,
    load_ruleset,
    ruleset_to_glossary_doc,
    save_ruleset,
    validate_doc as validate_ruleset_doc,
)
from backend.ruleset_template_store import (
    create_template as create_ruleset_template,
    delete_template as delete_ruleset_template,
    import_template_from_json as import_ruleset_template_from_json,
    list_templates as list_ruleset_templates,
    load_template as load_ruleset_template,
    update_template as update_ruleset_template,
)
from backend.review_workflow import read_text, write_text, unified_diff, mux_video_audio, embed_subtitles, regenerate_quality_report
from core.runtime_paths import detect_repo_root, pick_config_dir, pick_pipelines_dir
from core.runtime_manifest import build_quality_runtime_resource_check


def create_app(config_path: Path) -> Flask:
    # In PyInstaller builds, __file__ points to a temp extraction directory (often on C:).
    # Allow the Electron app to override the effective "repo root" so relative paths resolve
    # to the packaged resources directory (process.resourcesPath).
    repo_root = detect_repo_root()
    defaults_path = pick_config_dir(repo_root) / "defaults.yaml"
    cfg = None
    config_stack_meta: Dict[str, Any] = {}
    last_exc: Optional[Exception] = None
    # Docker Desktop bind mounts sometimes present a temporarily inconsistent view.
    # Retry briefly before falling back.
    for _ in range(30):  # ~6s total
        try:
            cfg = load_defaults(config_path)
            break
        except Exception as exc:
            last_exc = exc
        time.sleep(0.2)
    if cfg is None:
        msg = f"[warn] Failed to load config: {config_path}"
        if last_exc:
            msg += f" ({last_exc})"
        msg += f". Falling back to {defaults_path}."
        print(msg)
        cfg = load_defaults(defaults_path)
    else:
        # Merge defaults.yaml + active config + user override dir as a single source of truth.
        try:
            cfg, config_stack_meta = load_config_stack(config_path, defaults_path=defaults_path, repo_root=repo_root)
        except Exception as exc:
            print(f"[warn] Failed to merge base defaults.yaml: {exc}. Proceeding with {config_path} only.")
    # Allow app to override outputs root (avoid writing into installed Program Files/resources).
    outputs_root_env = os.environ.get("YGF_OUTPUTS_ROOT", "").strip()
    if outputs_root_env:
        paths = cfg.setdefault("paths", {})
        paths["outputs_root"] = outputs_root_env

    # Allow app to override models root (e.g. userData\models in packaged app)
    models_root = os.environ.get("YGF_MODELS_ROOT", "").strip()
    if models_root:
        paths = cfg.setdefault("paths", {})
        paths["models_root"] = models_root
        mr = Path(models_root)
        # v2 model layout (mode_stage_engine)
        paths["tts_home"] = str(mr / "quality_tts_coqui")
        paths["hf_cache"] = str(mr / "common_cache_hf")
        # Quality mode specific overrides
        paths["whisperx_model_dir"] = str(mr / "quality_asr_whisperx")

        # IMPORTANT (packaged app):
        # configs/*.yaml in resources may still contain repo-relative paths like:
        #   assets/models/lite_asr_whispercpp/...
        # but in installed app, models are extracted to YGF_MODELS_ROOT.
        # Rewrite those paths in-memory so TaskManager/_resolve_path works without special-casing.
        def _rewrite_assets_models(obj):  # type: ignore[no-untyped-def]
            try:
                if isinstance(obj, str):
                    s = obj.strip()
                    prefix = "assets/models/"
                    if s.startswith(prefix):
                        return str(mr / s[len(prefix) :])
                    return obj
                if isinstance(obj, list):
                    return [_rewrite_assets_models(x) for x in obj]
                if isinstance(obj, dict):
                    return {k: _rewrite_assets_models(v) for k, v in obj.items()}
            except Exception:
                return obj
            return obj

        cfg = _rewrite_assets_models(cfg)

    # Reduce noisy warnings in packaged app (does not affect functionality):
    # - HF symlink warning on Windows (caching still works without symlinks).
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    # Keep Matplotlib cache off C: and stable across runs.
    # Electron sets TEMP/TMP to user_data/tmp for packaged runs; reuse it when available.
    try:
        base_tmp = os.environ.get("TMP") or os.environ.get("TEMP") or ""
        if base_tmp:
            mpl_dir = Path(base_tmp) / "matplotlib"
            mpl_dir.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))
    except Exception:
        pass

    # Allow packaged app / Docker to override LLM endpoints without modifying quality.yaml.
    defaults = cfg.setdefault("defaults", {})
    llm_endpoint_env = os.environ.get("YGF_LLM_ENDPOINT", "").strip()
    if llm_endpoint_env:
        defaults["llm_endpoint"] = llm_endpoint_env
    phrase_endpoint_env = os.environ.get("YGF_PHRASE_LLM_ENDPOINT", "").strip()
    if phrase_endpoint_env:
        defaults["zh_phrase_llm_endpoint"] = phrase_endpoint_env

    app = Flask(__name__)
    try:
        is_packaged_backend_runtime = bool(getattr(sys, "frozen", False)) or Path(sys.executable).name.lower() == "backend_server.exe"
    except Exception:
        is_packaged_backend_runtime = False

    def _is_local_llm_endpoint(url: str) -> bool:
        raw = str(url or "").strip()
        if not raw:
            return True
        try:
            host = (urlparse(raw).hostname or "").strip().lower()
        except Exception:
            host = ""
        return host in {"127.0.0.1", "localhost", "::1", "host.docker.internal"}

    def _enforce_offline_llm_endpoints(defaults_cfg: Dict[str, Any]) -> None:
        if not bool(defaults_cfg.get("offline")):
            return
        fallback = "http://127.0.0.1:11434/v1"
        adjustments = []
        llm_endpoint = str(defaults_cfg.get("llm_endpoint") or "").strip()
        if llm_endpoint and not _is_local_llm_endpoint(llm_endpoint):
            defaults_cfg["llm_endpoint"] = fallback
            adjustments.append({"key": "llm_endpoint", "from": llm_endpoint, "to": fallback})
        phrase_endpoint = str(defaults_cfg.get("zh_phrase_llm_endpoint") or "").strip()
        if phrase_endpoint and not _is_local_llm_endpoint(phrase_endpoint):
            defaults_cfg["zh_phrase_llm_endpoint"] = fallback
            adjustments.append({"key": "zh_phrase_llm_endpoint", "from": phrase_endpoint, "to": fallback})
        if adjustments:
            defaults_cfg["_offline_endpoint_adjustments"] = adjustments

    _enforce_offline_llm_endpoints(defaults)
    cfg["_config_stack"] = config_stack_meta

    # CORS policy:
    # - Default: only allow local dev UI origins (localhost/127.0.0.1).
    # - Electron file:// often sends Origin: null; allow it only for packaged product runs.
    # - Override with YGF_CORS_ALLOW_ORIGINS="*" for dev-only scenarios.
    raw_cors = (os.environ.get("YGF_CORS_ALLOW_ORIGINS") or "").strip()
    if raw_cors == "*":
        CORS(app)
    else:
        allow_origins = [
            r"http://127\.0\.0\.1(:\d+)?",
            r"http://localhost(:\d+)?",
        ]
        if is_packaged_backend_runtime:
            allow_origins.append(r"null")
        # Only send CORS headers when the request includes Origin.
        # Must allow our local token header so browser preflight passes in dev (Vite origin -> backend).
        CORS(
            app,
            resources={r"/api/*": {"origins": allow_origins}},
            always_send=False,
            allow_headers=["Content-Type", "X-YGF-Token", "X-YGF-Cloud-Token"],
        )

    # Optional API token guard (off by default).
    # When enabled, it prevents random web pages on the same machine from calling the local backend APIs.
    # Enable by setting YGF_API_TOKEN in the backend process environment.
    api_token = (os.environ.get("YGF_API_TOKEN") or "").strip()
    cloud_license_required = (os.environ.get("YGF_REQUIRE_CLOUD_LICENSE") or "").strip().lower() in {"1", "true", "yes", "on"}
    cloud_auth_api_base = (os.environ.get("YGF_AUTH_API_BASE") or "https://auth.miaoyichuhai.com").strip().rstrip("/")
    cloud_product_edition = (os.environ.get("YGF_PRODUCT_EDITION") or "lite").strip().lower() or "lite"
    cloud_auth_timeout_s = max(int((os.environ.get("YGF_AUTH_TIMEOUT_S") or "15").strip() or "15"), 3)
    cloud_auth_cache_ttl_s = max(int((os.environ.get("YGF_AUTH_CACHE_TTL_S") or "15").strip() or "15"), 1)
    cloud_auth_cache: Dict[str, Dict[str, Any]] = {}
    cloud_guard_patterns = [
        ("POST", re.compile(r"^/api/tasks/start$")),
        ("POST", re.compile(r"^/api/tasks/[^/]+/resume$")),
        ("POST", re.compile(r"^/api/tasks/[^/]+/review/(run|apply|reextract_zh_phrases|upload_chs_srt|upload_eng_srt)$")),
        ("PUT", re.compile(r"^/api/tasks/[^/]+/review/(chs_srt|eng_srt)$")),
    ]

    def _needs_active_cloud_license(path: str, method: str) -> bool:
        if not cloud_license_required:
            return False
        normalized_method = str(method or "").upper()
        normalized_path = str(path or "")
        for expected_method, pattern in cloud_guard_patterns:
            if normalized_method == expected_method and pattern.match(normalized_path):
                return True
        return False

    def _fetch_cloud_auth_state(cloud_token: str) -> Dict[str, Any]:
        now = time.time()
        cached = cloud_auth_cache.get(cloud_token)
        if cached and float(cached.get("expires_at", 0)) > now:
            payload = cached.get("payload")
            if isinstance(payload, dict):
                return payload

        req = urllib.request.Request(
            f"{cloud_auth_api_base}/api/auth/me",
            headers={"Authorization": f"Bearer {cloud_token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=cloud_auth_timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("invalid auth response")
        cloud_auth_cache[cloud_token] = {"expires_at": now + float(cloud_auth_cache_ttl_s), "payload": payload}
        return payload

    @app.before_request
    def _ygf_auth_guard():  # type: ignore[no-untyped-def]
        # Only guard API endpoints.
        if not request.path.startswith("/api/"):
            return None
        # Allow CORS preflight requests.
        if request.method == "OPTIONS":
            return None
        # Allow health checks to be called without token if explicitly requested.
        if request.path == "/api/health" and (os.environ.get("YGF_API_TOKEN_ALLOW_HEALTH") or "").strip() in {"1", "true", "yes", "on"}:
            return None
        # Header-based token (simple and robust for Electron/axios).
        if api_token:
            got = (request.headers.get("X-YGF-Token") or "").strip()
            if got != api_token:
                return jsonify({"error": "unauthorized"}), 401
        if not _needs_active_cloud_license(request.path, request.method):
            return None
        cloud_token = (request.headers.get("X-YGF-Cloud-Token") or "").strip()
        if not cloud_token:
            return jsonify({"error": "当前云端登录态缺失，请重新登录后再试。"}), 401
        if not cloud_auth_api_base:
            return jsonify({"error": "本地后端未配置云端认证地址，无法校验授权。"}), 503
        try:
            payload = _fetch_cloud_auth_state(cloud_token)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return jsonify({"error": "云端登录态已失效，请重新登录后再试。"}), 401
            return jsonify({"error": f"云端授权校验失败（HTTP {exc.code}）。"}), 503
        except Exception:
            return jsonify({"error": "云端授权校验失败，请检查网络或认证服务状态后重试。"}), 503
        license_payload = payload.get("license") if isinstance(payload, dict) else {}
        if not isinstance(license_payload, dict):
            return jsonify({"error": "云端授权校验结果无效，请重新登录后重试。"}), 503
        status = str(license_payload.get("status") or "").strip().lower()
        active = bool(license_payload.get("active"))
        license_product_edition = str(license_payload.get("product_edition") or "").strip().lower()
        if active and license_product_edition and license_product_edition not in {"universal", cloud_product_edition}:
            return (
                jsonify({"error": f"当前授权属于{license_product_edition}版，不能在当前{cloud_product_edition}版安装包中继续处理任务。"}),
                403,
            )
        if active and status not in {"expired", "frozen", "none"}:
            return None
        if status == "expired":
            return jsonify({"error": "当前授权已到期，不能继续处理任务。请输入新的激活码后再试。"}), 403
        if status == "frozen":
            return jsonify({"error": "当前授权已被冻结，不能继续处理任务。请联系管理员处理。"}), 403
        return jsonify({"error": "当前账号尚未激活，不能继续处理任务。"}), 403
        return None

    manager = TaskManager(cfg)
    glossary_path = repo_root / "assets" / "glossary" / "glossary.json"
    ruleset_global_path = repo_root / "assets" / "rules" / "ruleset.global.json"

    def _strip_chs_review_markers(raw: str) -> str:
        # Must be extremely low-risk: only remove known audit tokens.
        s = str(raw or "")
        for tok in ("【已校审】", "[已校审]", "(已校审)", "（已校审）"):
            if tok in s:
                s = s.replace(tok, "")
        return s

    def _sync_legacy_glossary_from_ruleset(ruleset_doc: Dict[str, Any]) -> None:
        """
        Compatibility bridge:
        - Pipelines/quality checks historically read assets/glossary/glossary.json.
        - New source of truth is ruleset.global.json (terms section).
        Keep the legacy glossary file updated best-effort so old codepaths still work.
        """
        try:
            gdoc = ruleset_to_glossary_doc(ruleset_doc)
            save_glossary(glossary_path, gdoc)
        except Exception:
            pass

    def _runtime_info(include_sensitive: bool = False) -> Dict[str, Any]:
        # Keep this minimal and safe: helps frontend/main process verify it is talking to
        # the correct backend instance (not a stale one still bound to 5175).
        try:
            exe_name = Path(sys.executable).name
        except Exception:
            exe_name = str(sys.executable)
        payload = {
            "config_path": str(config_path),
            "sys_executable_name": exe_name,
            "is_frozen": bool(getattr(sys, "frozen", False)),
            "YGF_APP_ROOT": os.environ.get("YGF_APP_ROOT", ""),
            "CONFIG_PATH": os.environ.get("CONFIG_PATH", ""),
            "cloud_license_enforced": cloud_license_required,
            "cloud_auth_api_base": cloud_auth_api_base,
            "product_edition": cloud_product_edition,
        }
        if include_sensitive:
            payload.update(
                {
                    "pid": os.getpid(),
                    "cwd": str(Path.cwd()),
                    "repo_root": str(repo_root),
                    "sys_executable": str(sys.executable),
                }
            )
        return payload

    def _is_media_file(path: Path) -> tuple[bool, str]:
        """
        Validate the input is a readable audio/video file.
        We reject obvious non-media uploads (e.g. .dmg) early to avoid ffmpeg failures later.
        """
        if not path.exists() or not path.is_file():
            return False, f"file not found: {path}"
        # Quick extension filter to catch common mistakes early
        allowed_ext = {
            ".mp4",
            ".mkv",
            ".mov",
            ".m4v",
            ".avi",
            ".webm",
            ".flv",
            ".ts",
            ".mp3",
            ".wav",
            ".m4a",
            ".aac",
            ".flac",
            ".ogg",
        }
        ext = path.suffix.lower()
        if ext and ext not in allowed_ext:
            return (
                False,
                f"unsupported file type: {ext}. Please upload a video/audio file (e.g. mp4/mkv/mov/mp3/wav).",
            )

        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            # If ffprobe is not available, fall back to extension-only check.
            return True, ""
        try:
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_streams",
                    "-show_format",
                    str(path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            if proc.returncode != 0:
                return False, f"ffprobe failed: {proc.stderr.strip() or 'invalid media file'}"
            info = json.loads(proc.stdout or "{}")
            streams = info.get("streams") or []
            has_av = any(s.get("codec_type") in {"audio", "video"} for s in streams if isinstance(s, dict))
            if not has_av:
                return False, "no audio/video streams found (not a media file)"
            return True, ""
        except Exception as exc:
            return False, f"failed to validate media file: {exc}"

    def available_modes() -> list[str]:
        """
        Compute which modes are usable in the *current runtime*.

        Packaged Windows builds rely on real files under `process.resourcesPath`
        (Electron sets YGF_APP_ROOT to that path). If a previous version is still
        running during install, the installer may fail to overwrite some locked
        files, causing mode resources to be missing. We therefore:
        - check multiple plausible locations (resources/pipelines and app.asar.unpacked/pipelines)
        - provide structured reasons via /api/config so UI can explain "why not quality".
        """

        # Lite is always available (minimal deps).
        return [m for m, d in available_modes_detail().items() if d.get("available")]

    def available_modes_detail() -> Dict[str, Dict[str, Any]]:
        """
        Return per-mode availability and reasons.
        Schema:
          {
            "<mode>": {
              "available": bool,
              "reasons": [str, ...],   # non-empty when unavailable
              "paths_checked": { "<name>": "<path>", ... },
            },
            ...
          }
        """
        details: Dict[str, Dict[str, Any]] = {}

        def _exists_any(paths: list[Path]) -> Optional[Path]:
            for p in paths:
                try:
                    if p.exists():
                        return p
                except Exception:
                    continue
            return None

        # Detect packaged backend exe (PyInstaller onefile / installed app).
        is_packaged_backend = is_packaged_backend_runtime

        quality_only = bool((cfg or {}).get("ui", {}).get("quality_only"))

        # lite
        if quality_only:
            details["lite"] = {
                "available": False,
                "reasons": ["此版本仅支持质量模式（quality_only=true）"],
                "paths_checked": {},
            }
        else:
            details["lite"] = {
                "available": True,
                "reasons": [],
                "paths_checked": {},
            }

        # quality
        q_script_candidates = [
            repo_root / "pipelines" / "quality_pipeline.py",
            repo_root / "app.asar.unpacked" / "pipelines" / "quality_pipeline.py",
        ]
        q_script = _exists_any(q_script_candidates)
        q_worker_candidates: list[Path] = []
        try:
            exe = Path(sys.executable).resolve()
            q_worker_candidates = [
                exe.with_name("quality_worker.exe"),
                repo_root / "quality_worker.exe",
                repo_root / "app.asar.unpacked" / "quality_worker.exe",
            ]
        except Exception:
            q_worker_candidates = [repo_root / "quality_worker.exe"]
        q_worker = _exists_any(q_worker_candidates)

        q_reasons: list[str] = []
        if not q_script:
            q_reasons.append("缺少质量模式脚本：pipelines/quality_pipeline.py（可能是安装时程序未退出导致资源未更新）")
        if is_packaged_backend and not q_worker:
            q_reasons.append("缺少质量模式 worker：quality_worker.exe（安装包应包含该文件）")
        details["quality"] = {
            "available": (len(q_reasons) == 0),
            "reasons": q_reasons,
            "paths_checked": {
                "quality_script_candidates": "; ".join([str(p) for p in q_script_candidates]),
                "quality_worker_candidates": "; ".join([str(p) for p in q_worker_candidates]),
                "quality_script_found": str(q_script) if q_script else "",
                "quality_worker_found": str(q_worker) if q_worker else "",
                "is_packaged_backend": str(bool(is_packaged_backend)),
                "repo_root": str(repo_root),
                "sys_executable": str(sys.executable),
            },
        }

        # online
        o_script_candidates = [
            repo_root / "pipelines" / "online_pipeline.py",
            repo_root / "app.asar.unpacked" / "pipelines" / "online_pipeline.py",
        ]
        o_script = _exists_any(o_script_candidates)
        o_reasons: list[str] = []
        if quality_only:
            o_reasons.append("此版本仅支持质量模式（quality_only=true）")
        if not o_script:
            o_reasons.append("缺少在线模式脚本：pipelines/online_pipeline.py")
        details["online"] = {
            "available": (len(o_reasons) == 0),
            "reasons": o_reasons,
            "paths_checked": {
                "online_script_candidates": "; ".join([str(p) for p in o_script_candidates]),
                "online_script_found": str(o_script) if o_script else "",
            },
        }

        return details

    @app.get("/api/health")
    def health():
        # Explicit jsonify to ensure Content-Type=application/json (PowerShell Invoke-RestMethod parsing).
        return jsonify({"status": "ok", "runtime": _runtime_info(include_sensitive=False)})

    # -----------------------
    # Video helpers (preview/probe) for "hard subtitle erase" UX
    # -----------------------
    @app.post("/api/video/probe")
    def video_probe():
        """
        Body: { path: string }
        Returns: { width, height, duration_s }
        """
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        p = payload.get("path")
        if not p:
            return jsonify({"error": "path is required"}), 400
        path = Path(str(p))
        if not path.exists():
            return jsonify({"error": f"file not found: {path}"}), 400
        ok, reason = _is_media_file(path)
        if not ok:
            return jsonify({"error": f"invalid media file: {reason}"}), 400
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return jsonify({"error": "ffprobe not found"}), 500
        try:
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-select_streams",
                    "v:0",
                    "-show_streams",
                    "-show_format",
                    str(path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            if proc.returncode != 0:
                return jsonify({"error": f"ffprobe failed: {proc.stderr.strip()}"}), 500
            info = json.loads(proc.stdout or "{}")
            streams = info.get("streams") or []
            v0 = streams[0] if streams and isinstance(streams[0], dict) else {}
            width = int(v0.get("width") or 0)
            height = int(v0.get("height") or 0)
            dur = None
            fmt = info.get("format") if isinstance(info.get("format"), dict) else {}
            try:
                dur = float(fmt.get("duration")) if fmt and fmt.get("duration") is not None else None
            except Exception:
                dur = None
            if width <= 0 or height <= 0:
                return jsonify({"error": "failed to read video width/height"}), 500
            return {"width": width, "height": height, "duration_s": dur}
        except Exception as exc:
            return jsonify({"error": f"probe failed: {exc}"}), 500

    @app.post("/api/video/frame")
    def video_frame():
        """
        Body: { path: string, t?: number, max_width?: number }
        Returns: image/png
        """
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        p = payload.get("path")
        if not p:
            return jsonify({"error": "path is required"}), 400
        path = Path(str(p))
        if not path.exists():
            return jsonify({"error": f"file not found: {path}"}), 400
        ok, reason = _is_media_file(path)
        if not ok:
            return jsonify({"error": f"invalid media file: {reason}"}), 400
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return jsonify({"error": "ffmpeg not found"}), 500
        try:
            t = float(payload.get("t") or 0.0)
        except Exception:
            t = 0.0
        try:
            max_width = int(payload.get("max_width") or 960)
        except Exception:
            max_width = 960
        max_width = max(240, min(max_width, 1920))
        preview_dir = manager.outputs_root / "_preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        out = preview_dir / f"{uuid.uuid4().hex}.png"
        # Extract one frame. Use -ss before -i for speed; scale down for responsiveness.
        vf = f"scale='min({max_width},iw)':-1"
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            str(max(t, 0.0)),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            vf,
            "-q:v",
            "2",
            str(out),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if proc.returncode != 0 or not out.exists():
            return jsonify({"error": f"ffmpeg frame failed: {(proc.stderr or proc.stdout or '').strip()[:500]}"}), 500
        return send_file(out, mimetype="image/png", as_attachment=False, download_name="frame.png")

    @app.get("/api/hardware")
    def hardware():
        info = detect_hardware()
        quality_defaults = cfg.get("defaults", {}) or {}
        allow_gpu = bool(quality_defaults.get("allow_gpu", True))
        asr_device = "auto" if allow_gpu else "cpu"
        tts_device = str(quality_defaults.get("coqui_device", "auto") or "auto").strip().lower() or "auto"
        if not allow_gpu:
            tts_device = "cpu"
        elif tts_device not in {"auto", "cpu", "cuda"}:
            tts_device = "auto"
        return {
            "cpu_cores": info.cpu_cores,
            "memory_gb": info.memory_gb,
            "gpu_name": info.gpu_name,
            "gpu_vram_gb": info.gpu_vram_gb,
            "cuda_available": bool(info.gpu_name),
            "tier": info.tier,
            "device_policy": {
                "quality_allow_gpu": allow_gpu,
                "asr_device": asr_device,
                "tts_device": tts_device,
                "llm_runtime": "host_ollama_auto_gpu_if_available",
                "gpu_effective": bool(allow_gpu and info.gpu_name),
            },
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
        default_mode = cfg.get("default_mode") or cfg.get("defaults", {}).get("default_mode") or "lite"
        mode = payload.get("mode") or default_mode
        # Use runtime-derived availability (not YAML), so UI and backend agree.
        available = available_modes()
        if mode not in available:
            # Do NOT silently downgrade; return a clear reason so the user can fix installation/resources.
            detail = available_modes_detail().get(str(mode), {})
            reasons = detail.get("reasons") or []
            return (
                jsonify(
                    {
                        "error": f"mode not available: {mode}",
                        "mode": mode,
                        "available_modes": available,
                        "reasons": reasons,
                        "paths_checked": detail.get("paths_checked") or {},
                        "hint": "若你确认安装包是“质量包”，请先完全退出程序后重新安装（安装器检测到正在运行会导致资源未更新）。",
                    }
                ),
                400,
            )
        # 尊重前端显式选择的 mode；不要因为默认是 quality 就强制覆盖用户选择。

        # 质量模式：剥离轻量参数，避免传递 whispercpp 等无效参数
        if mode == "quality":
            lite_keys = {
                "whispercpp_threads",
                "whispercpp_model",
                "whispercpp_bin",
                "vad_enable",
                "vad_threshold",
                "vad_min_dur",
                "denoise",
                "bilingual_srt",
                "dedupe",
                "asr_model",
                "mt_model",
                "mt_device",
                # 产品策略：后端固定使用 Coqui（不接受前端切换 Piper）
                "tts_backend",
                "piper_model",
                "piper_bin",
            }
            for k in list(params.keys()):
                if k in lite_keys:
                    params.pop(k, None)
        if mode == "online":
            # online 也不需要本地 ASR/MT/TTS 配置
            lite_keys = {
                "whispercpp_threads",
                "whispercpp_model",
                "whispercpp_bin",
                "vad_enable",
                "vad_threshold",
                "vad_min_dur",
                "denoise",
                "bilingual_srt",
                "dedupe",
                "asr_model",
                "mt_model",
                "mt_device",
                "piper_model",
                "piper_bin",
                "coqui_model",
                "coqui_device",
            }
            for k in list(params.keys()):
                if k in lite_keys:
                    params.pop(k, None)
        if not video:
            return jsonify({"error": "video is required"}), 400
        video_path = Path(video)
        if not video_path.exists():
            return jsonify({"error": f"video not found: {video}"}), 400
        ok, reason = _is_media_file(video_path)
        if not ok:
            return jsonify({"error": f"invalid input file: {reason}"}), 400
        try:
            task_id = manager.start_task(str(video_path), params, preset, mode=mode)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
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

    @app.post("/api/tasks/<task_id>/resume")
    def resume_task(task_id: str):
        """
        Resume an existing task in-place (same task_id/work_dir) from a later stage.
        Body:
          - resume_from: one of ["asr","mt","tts","mux"] (required)
          - params: optional overrides (optional)
          - preset: optional preset key (optional)
        """
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        resume_from = payload.get("resume_from")
        params = payload.get("params") or {}
        preset = payload.get("preset")
        if not resume_from:
            return jsonify({"error": "resume_from is required"}), 400
        try:
            rid = manager.resume_task(task_id, str(resume_from), params_overrides=params, preset=preset)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return {"task_id": rid}

    @app.get("/api/tasks/<task_id>/log")
    def task_log(task_id: str):
        try:
            offset = int(request.args.get("offset", 0))
        except Exception:
            offset = 0
        content, next_offset = manager.read_log_chunk(task_id, offset=offset)
        return {"content": content, "next_offset": next_offset}

    @app.get("/api/tasks/<task_id>/artifacts")
    def artifacts(task_id: str):
        files = manager.list_artifacts(task_id)
        return {"files": files}

    @app.post("/api/tasks/<task_id>/cleanup")
    def cleanup_artifacts(task_id: str):
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        include_resume = bool(payload.get("include_resume"))
        include_review = bool(payload.get("include_review"))
        include_diagnostics = bool(payload.get("include_diagnostics", True))
        try:
            res = manager.cleanup_artifacts(
                task_id,
                include_resume=include_resume,
                include_review=include_review,
                include_diagnostics=include_diagnostics,
            )
            return res
        except ValueError:
            return jsonify({"error": "task not found"}), 404

    @app.get("/api/tasks/<task_id>/quality_report")
    def quality_report(task_id: str):
        status = manager.get_status(task_id)
        if not status:
            return jsonify({"error": "not found"}), 404
        work_dir = Path(status["work_dir"])
        report_path = work_dir / "quality_report.json"
        if not report_path.exists():
            return jsonify({"pending": True}), 200
        try:
            data = json.loads(report_path.read_text(encoding="utf-8", errors="ignore") or "{}")
        except Exception as exc:
            return jsonify({"error": f"failed to read quality_report: {exc}"}), 500
        # Optional: regenerate report using latest code (useful after upgrading quality_report.py).
        # - regen=1 forces regeneration.
        # - Also auto-regenerate when we detect an older schema.
        try:
            regen = str(request.args.get("regen") or "").strip() in {"1", "true", "yes", "on"}
            old_schema = False
            ra = (data.get("checks") or {}).get("required_artifacts") if isinstance(data, dict) else None
            if isinstance(ra, dict) and "missing" in ra:
                old_schema = True
            if regen or old_schema:
                work_dir2 = manager.resolve_work_dir(task_id)
                mode = manager.resolve_mode(task_id)
                src = manager.resolve_video_path(task_id)
                if work_dir2:
                    regenerate_quality_report(task_id, mode, work_dir2, src, manager.config)
                    data = json.loads(report_path.read_text(encoding="utf-8", errors="ignore") or "{}")
        except Exception:
            # Never fail the endpoint due to regen issues; return existing data.
            pass
        return data

    # -----------------------
    # Review workflow (P1-3)
    # -----------------------
    @app.get("/api/tasks/<task_id>/terminology")
    def terminology_get(task_id: str):
        """
        Read per-task terminology.json (P0-4).
        This is used by the 'pause-before-translate' workflow to let users edit forced terms.
        """
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        p = work_dir / "terminology.json"
        if not p.exists():
            return jsonify({"error": "terminology.json not found"}), 404
        return {"name": p.name, "content": read_text(p)}

    @app.put("/api/tasks/<task_id>/terminology")
    def terminology_put(task_id: str):
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        content = payload.get("content")
        if content is None:
            return jsonify({"error": "content is required"}), 400
        p = work_dir / "terminology.json"
        write_text(p, str(content))
        return {"status": "ok", "path": str(p)}

    @app.get("/api/tasks/<task_id>/review/eng_srt")
    def review_get_eng_srt(task_id: str):
        # Frontend historically used `which`, while some earlier drafts used `use`.
        # Accept both to keep the API backward/forward compatible.
        which = (request.args.get("which") or request.args.get("use") or "base").strip()  # base|review
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        base_path = work_dir / "eng.srt"
        review_path = work_dir / "eng.review.srt"
        path = review_path if which == "review" else base_path
        if not path.exists() and which == "review" and base_path.exists():
            # UX: allow fetching review content before the user ever saved it (fallback to base).
            path = base_path
            return {"name": path.name, "content": read_text(path), "which_used": "base", "requested": "review"}
        if not path.exists():
            return jsonify({"error": "file not found"}), 404
        return {"name": path.name, "content": read_text(path), "which_used": which}

    @app.get("/api/tasks/<task_id>/review/chs_srt")
    def review_get_chs_srt(task_id: str):
        which = (request.args.get("which") or request.args.get("use") or "base").strip()  # base|review
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        base_path = work_dir / "chs.srt"
        llm_path = work_dir / "chs.llm.srt"
        review_path = work_dir / "chs.review.srt"
        if which == "review":
            if review_path.exists():
                path = review_path
                which_used = "review"
            elif llm_path.exists():
                path = llm_path
                which_used = "llm"
            else:
                path = base_path
                which_used = "base"
        else:
            path = base_path
            which_used = "base"
        if not path.exists():
            return jsonify({"error": "file not found"}), 404
        return {"name": path.name, "content": read_text(path), "which_used": which_used, "requested": which}

    @app.put("/api/tasks/<task_id>/review/chs_srt")
    def review_put_chs_srt(task_id: str):
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        content = payload.get("content")
        if content is None:
            return jsonify({"error": "content is required"}), 400
        # Defensive: never persist audit markers into the review SRT.
        write_text(work_dir / "chs.review.srt", _strip_chs_review_markers(str(content)))
        return {"status": "ok", "path": str(work_dir / "chs.review.srt")}

    @app.post("/api/tasks/<task_id>/review/upload_chs_srt")
    def review_upload_chs_srt(task_id: str):
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        if "file" not in request.files:
            return jsonify({"error": "file is required"}), 400
        f = request.files["file"]
        raw = f.read().decode("utf-8", errors="ignore")
        write_text(work_dir / "chs.review.srt", _strip_chs_review_markers(raw))
        return {"status": "ok", "path": str(work_dir / "chs.review.srt")}

    @app.put("/api/tasks/<task_id>/review/eng_srt")
    def review_put_eng_srt(task_id: str):
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        content = payload.get("content")
        if content is None:
            return jsonify({"error": "content is required"}), 400
        write_text(work_dir / "eng.review.srt", str(content))
        return {"status": "ok", "path": str(work_dir / "eng.review.srt")}

    @app.post("/api/tasks/<task_id>/review/upload_eng_srt")
    def review_upload_eng_srt(task_id: str):
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        if "file" not in request.files:
            return jsonify({"error": "file is required"}), 400
        f = request.files["file"]
        raw = f.read().decode("utf-8", errors="ignore")
        write_text(work_dir / "eng.review.srt", raw)
        return {"status": "ok", "path": str(work_dir / "eng.review.srt")}

    @app.post("/api/tasks/<task_id>/review/run")
    def review_run(task_id: str):
        """
        Run downstream pipeline based on reviewed edits.
        Body:
          - lang: "chs" | "eng"
        Behavior:
          - chs: rerun MT + TTS + mux + embed (resume_from=mt)
          - eng: rerun TTS + mux + embed (resume_from=tts), skipping MT
        """
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        lang = payload.get("lang")
        if lang not in {"chs", "eng"}:
            return jsonify({"error": "lang must be chs|eng"}), 400

        if lang == "chs":
            p = work_dir / "chs.review.srt"
            if not p.exists():
                return jsonify({"error": "chs.review.srt not found; please save review first"}), 400
            # After user review, we should NOT re-enter zh_gate again.
            # The reviewed Chinese subtitles become the single source of truth for MT.
            overrides = apply_quality_fixed_policies({
                "chs_override_srt": str(p),
                # MT tuning defaults after review:
                # keep the main path fast/stable, then allow only tiny bounded self-check for deal-breaker errors.
                "mt_style": "Neutral, natural English subtitles; clear and faithful; avoid slang/memes; concise but complete",
                # Product main path: short prompt batch translation only.
                "mt_prompt_mode": "short",
                "mt_long_fallback_max_lines": 0,
                "mt_long_fallback_max_ratio": 0.0,
                "mt_context_window": 0,
                "mt_prompt_profile": "subtitle_clean_v1",
                # qwen3.5:9b on local/offline hosts can have substantial cold-start latency after
                # review pause / resume_from=mt. Give the main MT request even more budget on weak hardware.
                "mt_request_timeout_s": 1200,
                "mt_request_retries": 4,
                # Keep word budget as a soft prompt hint only; no secondary compression rewrite.
                "mt_max_words_per_line": 18,
                "mt_long_zh_chars": 0,
                "mt_long_en_words": 0,
                "mt_long_target_words": 0,
                "llm_selfcheck_max_lines": 2,
                "llm_selfcheck_max_ratio": 0.05,
            }, review_resume=True)
            # If user also edited English, keep it as an override too.
            # Quality mode always resumes from MT here, but the pipeline can still choose to apply
            # eng.review.srt after translation (useful for manual fixes).
            p_en = work_dir / "eng.review.srt"
            if p_en.exists():
                overrides["eng_override_srt"] = str(p_en)
            resume_from = "mt"
        else:
            p = work_dir / "eng.review.srt"
            if not p.exists():
                return jsonify({"error": "eng.review.srt not found; please save review first"}), 400
            overrides = {"eng_override_srt": str(p), "review_enabled": False, "stop_after": ""}
            resume_from = "tts"

        try:
            rid = manager.resume_task(task_id, resume_from, params_overrides=overrides, preset=None)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return {"task_id": rid, "resume_from": resume_from, "lang": lang}

    @app.post("/api/tasks/<task_id>/review/reextract_zh_phrases")
    def review_reextract_zh_phrases(task_id: str):
        """
        Developer-only utility (Docker dev):
        Re-run zh phrase extraction on existing chs.srt/chs.review.srt and refresh:
          - chs.phrases.json
          - chs.suspects.json

        This does NOT resume the pipeline; it only refreshes review artifacts for the UI.
        """
        # Only expose this in Docker dev mode to avoid shipping/maintaining it as a public feature.
        try:
            is_docker_dev = (
                str(repo_root) == "/app"
                or str(Path.cwd()) == "/app"
                or str(os.environ.get("YGF_APP_ROOT", "") or "") == "/app"
                or str(os.environ.get("CONFIG_PATH", "") or "").startswith("/app/")
            )
        except Exception:
            is_docker_dev = False
        if not is_docker_dev:
            return jsonify({"error": "reextract is available only in Docker dev mode"}), 403

        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404

        # Read LLM settings from config (quality.yaml: defaults section, same as TaskManager).
        def_cfg = manager.config.get("defaults") or {}
        llm_endpoint = str(def_cfg.get("llm_endpoint") or os.environ.get("YGF_LLM_ENDPOINT") or "http://127.0.0.1:11434/v1")
        phrase_endpoint = str(def_cfg.get("zh_phrase_llm_endpoint") or def_cfg.get("llm_endpoint") or llm_endpoint)
        phrase_model = str(def_cfg.get("zh_phrase_llm_model") or def_cfg.get("llm_model") or "qwen3.5:9b")
        fallback_model = str(def_cfg.get("llm_model") or "qwen3.5:9b")
        llm_api_key = str(def_cfg.get("llm_api_key") or "")
        max_spans = int(def_cfg.get("zh_phrase_max_spans") or 2)
        max_total = int(def_cfg.get("zh_phrase_max_total") or 16)
        cand_max = int(def_cfg.get("zh_phrase_candidate_max_lines") or 40)
        force_one = bool(def_cfg.get("zh_phrase_force_one_per_line") or False)
        idiom_enable = bool(def_cfg.get("zh_phrase_idiom_enable") or False)
        idiom_path = str(def_cfg.get("zh_phrase_idiom_path") or "").strip()
        same_pinyin_path = str(def_cfg.get("zh_phrase_same_pinyin_path") or "").strip()

        def run_reextract(llm_model: str):
            return subprocess.run(
                [
                    sys.executable,
                    str(tool),
                    "--output-dir",
                    str(work_dir),
                    "--llm-endpoint",
                    phrase_endpoint,
                    "--llm-model",
                    llm_model,
                    "--llm-api-key",
                    llm_api_key,
                    "--max-spans",
                    str(max_spans),
                    "--max-total",
                    str(max_total),
                    "--candidate-max-lines",
                    str(cand_max),
                    *(
                        ["--force-one-per-line"]
                        if force_one
                        else []
                    ),
                    *(
                        ["--idiom-enable"]
                        if idiom_enable
                        else []
                    ),
                    *(
                        ["--idiom-path", idiom_path]
                        if idiom_path
                        else []
                    ),
                    *(
                        ["--same-pinyin-path", same_pinyin_path]
                        if same_pinyin_path
                        else []
                    ),
                ],
                capture_output=True,
                text=True,
                # Phrase extraction can be slow on CPU or when the local LLM is cold-starting.
                # Keep this generous so the dev-only endpoint doesn't flake under load.
                timeout=1200,
            )

        tool = repo_root / "pipelines" / "tools" / "reextract_zh_phrases.py"
        if not tool.exists():
            return jsonify({"error": f"tool missing: {tool}"}), 500
        try:
            proc = run_reextract(phrase_model)
            if proc.returncode != 0:
                combined = (proc.stderr or "") + (proc.stdout or "")
                if ("not found" in combined and "404" in combined) or ("model '" in combined and "' not found" in combined):
                    if phrase_model != fallback_model:
                        proc = run_reextract(fallback_model)
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "")[-2000:]
                return jsonify({"error": f"reextract failed (code={proc.returncode})", "detail": tail}), 500
            out = (proc.stdout or "").strip()
            # Tool prints a single JSON line at the end.
            try:
                last = out.splitlines()[-1] if out else "{}"
                data = json.loads(last)
            except Exception:
                data = {"status": "ok", "raw": out[-2000:]}
            return jsonify(data)
        except subprocess.TimeoutExpired:
            return jsonify({"error": "reextract timeout"}), 504
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.get("/api/tasks/<task_id>/review/diff")
    def review_diff(task_id: str):
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        lang = request.args.get("lang") or "eng"  # eng|chs
        if lang == "chs":
            base_path = work_dir / "chs.srt"
            rev_path = work_dir / "chs.review.srt"
        else:
            base_path = work_dir / "eng.srt"
            rev_path = work_dir / "eng.review.srt"
        base = read_text(base_path)
        rev = read_text(rev_path)
        diff = unified_diff(base, rev, base_path.name, rev_path.name)
        return {"diff": diff}

    @app.post("/api/tasks/<task_id>/review/apply")
    def review_apply(task_id: str):
        """
        Apply reviewed subtitles to regenerate deliverables without re-running ASR/MT.
        Body:
          - action: one of ["mux","embed","mux_embed"] (required)
          - use: "review"|"base" (optional, default "review")
        """
        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "not found"}), 404
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        action = payload.get("action")
        use = payload.get("use") or "review"
        if action not in {"mux", "embed", "mux_embed"}:
            return jsonify({"error": "action must be mux|embed|mux_embed"}), 400

        # Prefer review SRT if present, but gracefully fall back to base SRT when review doesn't exist yet.
        srt_path = work_dir / ("eng.review.srt" if use == "review" else "eng.srt")
        effective_use = use
        if not srt_path.exists() and use == "review":
            base_path = work_dir / "eng.srt"
            if base_path.exists():
                srt_path = base_path
                effective_use = "base"
        if not srt_path.exists():
            return jsonify({"error": f"srt not found: {srt_path.name}"}), 400

        source_video = manager.resolve_video_path(task_id)
        if not source_video or not source_video.exists():
            return jsonify({"error": "source video path not found for this task"}), 400

        tts_wav = work_dir / "tts_full.wav"
        out_mp4 = work_dir / "output_en.mp4"
        out_sub = work_dir / "output_en_sub.mp4"

        # Allow passing params from UI to ensure regen respects latest settings.
        req = request.get_json(silent=True) or {}
        req_params = req.get("params") if isinstance(req, dict) else None
        logs = []
        if action in {"mux", "mux_embed"}:
            if not tts_wav.exists():
                return jsonify({"error": "tts_full.wav missing; cannot mux"}), 400
            # Respect per-task mux sync settings if present in task_meta.json (hearing-first).
            meta = {}
            try:
                meta = json.loads((work_dir / "task_meta.json").read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            params = req_params if isinstance(req_params, dict) else ((meta.get("params") or {}) if isinstance(meta, dict) else {})
            rc, out = mux_video_audio(
                source_video,
                tts_wav,
                out_mp4,
                sync_strategy=str(params.get("mux_sync_strategy", "slow") or "slow"),
                slow_max_ratio=float(params.get("mux_slow_max_ratio", 1.18) or 1.18),
                threshold_s=float(params.get("mux_slow_threshold_s", 0.05) or 0.05),
            )
            logs.append(out)
            if rc != 0:
                return jsonify({"error": "mux failed", "log": out}), 500
        if action in {"embed", "mux_embed"}:
            if not out_mp4.exists():
                return jsonify({"error": "output_en.mp4 missing; cannot embed"}), 400
            # Use effective params from task_meta.json so subtitle style/placement matches pipeline UI.
            meta = {}
            try:
                meta = json.loads((work_dir / "task_meta.json").read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            params = req_params if isinstance(req_params, dict) else ((meta.get("params") or {}) if isinstance(meta, dict) else {})
            # Match pipeline behavior: when using base subtitles and display_use_for_embed is enabled, prefer display.srt.
            srt_to_burn = srt_path
            try:
                if effective_use == "base" and bool(params.get("display_use_for_embed", False)):
                    ds = work_dir / "display.srt"
                    if ds.exists():
                        srt_to_burn = ds
            except Exception:
                srt_to_burn = srt_path

            # Placement precedence matches the main pipeline:
            # explicit subtitle placement wins; erase box is only used as a fallback.
            place_enable = bool(params.get("sub_place_enable", False))
            place_coord_mode = str(params.get("sub_place_coord_mode", "ratio") or "ratio")
            place_x = float(params.get("sub_place_x", 0.0) or 0.0)
            place_y = float(params.get("sub_place_y", 0.78) or 0.78)
            place_w = float(params.get("sub_place_w", 1.0) or 1.0)
            place_h = float(params.get("sub_place_h", 0.22) or 0.22)
            if not place_enable and bool(params.get("erase_subtitle_enable", False)):
                place_enable = True
                place_coord_mode = str(params.get("erase_subtitle_coord_mode", "ratio") or "ratio")
                place_x = float(params.get("erase_subtitle_x", 0.0) or 0.0)
                place_y = float(params.get("erase_subtitle_y", 0.78) or 0.78)
                place_w = float(params.get("erase_subtitle_w", 1.0) or 1.0)
                place_h = float(params.get("erase_subtitle_h", 0.22) or 0.22)
            logs.append(
                "subtitle_burn_layout: "
                f"source={'sub_place' if bool(params.get('sub_place_enable', False)) else ('erase_rect' if bool(params.get('erase_subtitle_enable', False)) else 'default')} "
                f"place_enable={place_enable} coord={place_coord_mode} x={place_x} y={place_y} w={place_w} h={place_h} "
                f"font={int(params.get('sub_font_size', 18) or 18)}"
            )

            rc, out = embed_subtitles(
                out_mp4,
                srt_to_burn,
                out_sub,
                font_name=str(params.get("sub_font_name", "Arial") or "Arial"),
                font_size=int(params.get("sub_font_size", 18) or 18),
                outline=int(params.get("sub_outline", 1) or 1),
                shadow=int(params.get("sub_shadow", 0) or 0),
                margin_v=int(params.get("sub_margin_v", 24) or 24),
                alignment=int(params.get("sub_alignment", 2) or 2),
                place_enable=place_enable,
                place_coord_mode=place_coord_mode,
                place_x=place_x,
                place_y=place_y,
                place_w=place_w,
                place_h=place_h,
            )
            logs.append(out)
            if rc != 0:
                return jsonify({"error": "embed failed", "log": out}), 500

        # Re-generate quality report after review apply.
        try:
            mode = manager.resolve_mode(task_id)
            regenerate_quality_report(task_id, mode, work_dir, source_video, manager.config)
        except Exception:
            pass

        # Append a small audit log
        try:
            audit = {
                "task_id": task_id,
                "action": action,
                "use": use,
                "time": int(time.time()),
            }
            (work_dir / "review_audit.jsonl").open("a", encoding="utf-8").write(json.dumps(audit, ensure_ascii=False) + "\n")
        except Exception:
            pass

        return {"status": "ok", "action": action, "use": effective_use, "srt": srt_path.name}

    @app.get("/api/tasks/<task_id>/download")
    def download(task_id: str):
        """
        Download a task artifact.

        Security:
        - Only allow files under this task's work_dir (outputs/<task_id>/...).
        - Accept both absolute paths (legacy frontend behavior) and relative paths.
        """
        path_str = request.args.get("path")
        if not path_str:
            return jsonify({"error": "path is required"}), 400

        work_dir = manager.resolve_work_dir(task_id)
        if not work_dir:
            return jsonify({"error": "task not found"}), 404

        raw = str(path_str)
        # Support relative path requests (treat as work_dir-relative).
        p = Path(raw)
        try:
            candidate = (work_dir / p) if not p.is_absolute() else p
            # Resolve without requiring existence (strict=False) to prevent traversal tricks.
            candidate_abs = candidate.expanduser().resolve(strict=False)
            work_abs = work_dir.resolve(strict=False)
            # Enforce: candidate must be within work_dir.
            try:
                candidate_abs.relative_to(work_abs)
            except Exception:
                return jsonify({"error": "invalid path: must be under task work_dir"}), 400
            if not candidate_abs.exists() or not candidate_abs.is_file():
                return jsonify({"error": "file not found"}), 404
        except Exception as exc:
            return jsonify({"error": f"invalid path: {exc}"}), 400

        return send_file(candidate_abs, as_attachment=True)

    @app.get("/api/config")
    def config():
        # Ensure UI always receives the latest YAML (hot reload) even before any task starts.
        # Previously reload only happened when starting/resuming tasks, which made the UI show stale defaults.
        try:
            manager._reload_config_if_changed()  # type: ignore[attr-defined]
        except Exception:
            pass
        # Return the TaskManager's live config so hot-reloaded YAML changes are reflected in UI.
        cfg_with_modes = dict(manager.config)
        modes_detail = available_modes_detail()
        cfg_with_modes["available_modes"] = [m for m, d in modes_detail.items() if d.get("available")]
        cfg_with_modes["available_modes_detail"] = modes_detail
        cfg_with_modes["config_stack"] = manager.config.get("_config_stack") or {}
        cfg_with_modes["runtime_resource_check"] = build_quality_runtime_resource_check(repo_root, manager.config)
        cfg_with_modes["runtime"] = _runtime_info(include_sensitive=True)
        # Explicit jsonify to ensure Content-Type=application/json (PowerShell Invoke-RestMethod parsing).
        return jsonify(cfg_with_modes)

    # -----------------------
    # Glossary (P1-1 公共能力)
    # -----------------------
    @app.get("/api/glossary")
    def get_glossary():
        rs = load_ruleset(ruleset_global_path)
        return ruleset_to_glossary_doc(rs)

    @app.put("/api/glossary")
    def put_glossary():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        try:
            base = load_ruleset(ruleset_global_path)
            next_doc = dict(base or ruleset_default_doc())
            next_doc["asr_fixes"] = glossary_doc_to_ruleset_asr_fixes(payload)
            saved = save_ruleset(ruleset_global_path, next_doc)
            _sync_legacy_glossary_from_ruleset(saved)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return ruleset_to_glossary_doc(saved)

    @app.post("/api/glossary/upload")
    def upload_glossary():
        if "file" not in request.files:
            return jsonify({"error": "file is required"}), 400
        f = request.files["file"]
        raw = f.read().decode("utf-8", errors="ignore")
        try:
            doc = json.loads(raw or "{}")
            base = load_ruleset(ruleset_global_path)
            next_doc = dict(base or ruleset_default_doc())
            # Accept either legacy GlossaryDoc (items) or a full ruleset doc.
            if isinstance(doc, dict) and isinstance(doc.get("items"), list):
                next_doc["asr_fixes"] = glossary_doc_to_ruleset_asr_fixes(doc)
                saved = save_ruleset(ruleset_global_path, next_doc)
            else:
                saved = save_ruleset(ruleset_global_path, doc if isinstance(doc, dict) else {})
            _sync_legacy_glossary_from_ruleset(saved)
        except Exception as exc:
            return jsonify({"error": f"invalid glossary json: {exc}"}), 400
        return ruleset_to_glossary_doc(saved)

    # -----------------------
    # Ruleset (统一规则中心)
    # -----------------------
    ruleset_templates_dir = repo_root / "assets" / "rules" / "templates"

    @app.get("/api/rulesets/global")
    def ruleset_global_get():
        return load_ruleset(ruleset_global_path)

    @app.put("/api/rulesets/global")
    def ruleset_global_put():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        try:
            saved = save_ruleset(ruleset_global_path, payload)
            _sync_legacy_glossary_from_ruleset(saved)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return saved

    @app.post("/api/rulesets/upload")
    def ruleset_upload():
        if "file" not in request.files:
            return jsonify({"error": "file is required"}), 400
        f = request.files["file"]
        raw = f.read().decode("utf-8", errors="ignore")
        try:
            doc = json.loads(raw or "{}")
            if not isinstance(doc, dict):
                raise ValueError("ruleset json must be an object")
            # validate early to return friendly error
            _ = validate_ruleset_doc(doc)
            saved = save_ruleset(ruleset_global_path, doc)
            _sync_legacy_glossary_from_ruleset(saved)
        except Exception as exc:
            return jsonify({"error": f"invalid ruleset json: {exc}"}), 400
        return saved

    # -----------------------
    # Ruleset Templates (可命名/可复用)
    # -----------------------
    @app.get("/api/rulesets/templates")
    def ruleset_templates_list():
        return {"items": list_ruleset_templates(ruleset_templates_dir)}

    @app.post("/api/rulesets/templates")
    def ruleset_templates_create():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        name = str(payload.get("name") or "").strip()
        doc = payload.get("doc") if isinstance(payload.get("doc"), dict) else None
        try:
            saved = create_ruleset_template(ruleset_templates_dir, name=name or "新模板", doc=doc)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return saved

    @app.get("/api/rulesets/templates/<template_id>")
    def ruleset_templates_get(template_id: str):
        try:
            return load_ruleset_template(ruleset_templates_dir, template_id)
        except FileNotFoundError:
            return jsonify({"error": "template not found"}), 404
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.put("/api/rulesets/templates/<template_id>")
    def ruleset_templates_put(template_id: str):
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        name = payload.get("name")
        doc = payload.get("doc")
        try:
            saved = update_ruleset_template(
                ruleset_templates_dir,
                template_id,
                name=str(name) if name is not None else None,
                doc=doc if isinstance(doc, dict) else None,
            )
        except FileNotFoundError:
            return jsonify({"error": "template not found"}), 404
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return saved

    @app.delete("/api/rulesets/templates/<template_id>")
    def ruleset_templates_delete(template_id: str):
        try:
            delete_ruleset_template(ruleset_templates_dir, template_id)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return {"ok": True}

    @app.post("/api/rulesets/templates/upload")
    def ruleset_templates_upload():
        if "file" not in request.files:
            return jsonify({"error": "file is required"}), 400
        f = request.files["file"]
        raw = f.read().decode("utf-8", errors="ignore")
        try:
            doc = json.loads(raw or "{}")
            saved = import_ruleset_template_from_json(
                ruleset_templates_dir,
                doc if isinstance(doc, dict) else {},
                name_hint=secure_filename(f.filename or "") or "导入模板",
            )
        except Exception as exc:
            return jsonify({"error": f"invalid template json: {exc}"}), 400
        return saved

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
    root = detect_repo_root()
    cfg_dir = pick_config_dir(root)

    # ---------------------------------------------------------
    # Self-check mode (packaging/smoke test)
    # ---------------------------------------------------------
    # Usage (packaged):
    #   set YGF_APP_ROOT=<resources>
    #   set CONFIG_PATH=<resources>\configs\quality.yaml
    #   backend_server.exe --self-check
    #
    # This provides a fast signal before building an installer.
    if "--self-check" in sys.argv:
        try:
            pipelines_dir = pick_pipelines_dir(root)
            ffmpeg_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            # Minimal filesystem checks
            required = {
                f"{pipelines_dir.name}/lite_pipeline.py": pipelines_dir / "lite_pipeline.py",
                f"{pipelines_dir.name}/quality_pipeline.py": pipelines_dir / "quality_pipeline.py",
                "configs/quality.yaml": cfg_dir / "quality.yaml",
                f"bin/{ffmpeg_name}": root / "bin" / ffmpeg_name,
            }
            missing = [k for k, p in required.items() if not p.exists()]
            # Packaged Windows best-practice: quality_worker should exist alongside backend_server.exe
            try:
                is_packaged = bool(getattr(sys, "frozen", False)) or Path(sys.executable).name.lower() == "backend_server.exe"
            except Exception:
                is_packaged = False
            if is_packaged:
                worker = Path(sys.executable).resolve().with_name("quality_worker.exe")
                if not worker.exists():
                    missing.append("quality_worker.exe")

            payload = {
                "ok": len(missing) == 0,
                "missing": missing,
                "root": str(root),
                "sys_executable": str(sys.executable),
                "is_packaged": bool(is_packaged),
                "YGF_APP_ROOT": os.environ.get("YGF_APP_ROOT", ""),
                "CONFIG_PATH": os.environ.get("CONFIG_PATH", ""),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            raise SystemExit(0 if payload["ok"] else 2)
        except SystemExit:
            raise
        except Exception as exc:
            try:
                print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            finally:
                raise SystemExit(3)

    # ---------------------------------------------------------
    # Packaged-task runner mode (PyInstaller-friendly)
    # ---------------------------------------------------------
    # In packaged builds, TaskManager spawns pipelines using `sys.executable`.
    # Under PyInstaller, `sys.executable` is backend_server.exe, so attempting to run
    # `sys.executable pipelines/xxx.py ...` will just start another Flask server, causing
    # tasks to "stall" at 0% with only Flask startup logs.
    #
    # We provide a dedicated entry to run the pipeline scripts inside a separate process:
    #   backend_server.exe --run-pipeline <lite|quality|online> <script-args...>
    if "--run-pipeline" in sys.argv:
        try:
            idx = sys.argv.index("--run-pipeline")
            mode = (sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "").strip() or "lite"
            forwarded = sys.argv[idx + 2 :]
        except Exception:
            mode = "lite"
            forwarded = []

        # Best practice: in packaged Windows builds, the "quality" pipeline is executed by a
        # separate worker executable (dependency isolation). If present, delegate immediately.
        if mode == "quality":
            try:
                worker = Path(sys.executable).resolve().with_name("quality_worker.exe")
                if worker.exists():
                    proc = subprocess.run([str(worker), "--run-pipeline", "quality", *forwarded])
                    raise SystemExit(proc.returncode)
            except SystemExit:
                raise
            except Exception:
                # Fallback to legacy in-process runner (may fail if deps are missing).
                pass

        pipelines_dir = pick_pipelines_dir(root)
        # Ensure bundled binaries are discoverable (ffmpeg/whisper-cli, etc.).
        # Pipeline scripts often call "ffmpeg" via PATH (not an absolute path).
        bin_dir = root / "bin"
        if bin_dir.exists():
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            # Some subprocess resolution on Windows depends on PATHEXT.
            # In certain packaged environments it can be missing, making `ffmpeg` fail to resolve to `ffmpeg.exe`.
            if os.name == "nt" and not os.environ.get("PATHEXT"):
                os.environ["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"
            # Help libs that probe ffmpeg at import-time (e.g. pydub).
            ffmpeg_exe = bin_dir / "ffmpeg.exe"
            if ffmpeg_exe.exists():
                os.environ.setdefault("FFMPEG_BINARY", str(ffmpeg_exe))

        script_map = {
            "lite": pipelines_dir / "lite_pipeline.py",
            "quality": pipelines_dir / "quality_pipeline.py",
            "online": pipelines_dir / "online_pipeline.py",
        }
        script_path = script_map.get(mode, script_map["lite"])
        if not script_path.exists():
            raise FileNotFoundError(f"pipeline script not found for mode={mode}: {script_path}")

        # Ensure repo root is importable for `from pipelines import ...` imports
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        # Run the script as __main__, forwarding the original CLI args.
        sys.argv = [str(script_path), *forwarded]
        try:
            runpy.run_path(str(script_path), run_name="__main__")
        except SystemExit as exc:
            # Preserve script failures.
            # - sys.exit(int): keep the code
            # - sys.exit(str): print message to stderr and return non-zero
            if isinstance(exc.code, int):
                raise SystemExit(exc.code)
            if exc.code is None:
                raise SystemExit(0)
            try:
                print(str(exc.code), file=sys.stderr)
            finally:
                raise SystemExit(1)
        return

    # Allow selecting config file via env var (helps fully-local + docker setups).
    # Examples:
    # - CONFIG_PATH=/app/configs/quality.yaml
    # - CONFIG_PATH=/app/configs/defaults.yaml
    raw = os.environ.get("CONFIG_PATH")
    candidates = []
    if raw:
        candidates.append(Path(raw))
    # sensible fallbacks (both in-container paths)
    candidates.append(cfg_dir / "quality.yaml")
    candidates.append(cfg_dir / "defaults.yaml")

    config_path = None
    for p in candidates:
        try:
            if p.exists():
                config_path = p
                break
        except Exception:
            continue
    if config_path is None:
        raise FileNotFoundError(f"Config not found. Tried: {[str(p) for p in candidates]}")
    if raw and str(config_path) != raw:
        print(f"[warn] CONFIG_PATH={raw} not found; fallback to {config_path}")
    app = create_app(config_path)
    # Default bind:
    # - Packaged app / local dev: bind to localhost only (safer; prevents other LAN devices/web pages from reaching it).
    # - Docker: bind to 0.0.0.0 so port mapping works.
    #
    # Override with env:
    # - YGF_BIND_HOST=0.0.0.0 (explicitly expose)
    # - YGF_PORT=5175 (optional)
    bind_host = (os.environ.get("YGF_BIND_HOST") or os.environ.get("BIND_HOST") or "").strip()
    if not bind_host:
        try:
            in_docker = Path("/.dockerenv").exists()
        except Exception:
            in_docker = False
        bind_host = "0.0.0.0" if in_docker else "127.0.0.1"
    try:
        port = int((os.environ.get("YGF_PORT") or os.environ.get("PORT") or "5175").strip())
    except Exception:
        port = 5175
    app.run(host=bind_host, port=port, debug=False)


if __name__ == "__main__":
    main()


