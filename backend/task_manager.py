import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import load_defaults
from backend.quality_report import generate_quality_report, write_quality_report
from backend.runtime_paths import detect_repo_root, pick_config_dir


_HW_LIMIT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"out of memory",
        r"cuda out of memory",
        r"memoryerror",
        r"\boom\b",
        r"killed",
        r"requires more system memory",
        r"cublas.*alloc",
        r"cuda error: out of memory",
    ]
]


def _log_has_hw_limit(log_path: Path) -> bool:
    if not log_path.exists():
        return False
    try:
        tail = log_path.read_text(encoding="utf-8", errors="ignore")[-8000:]
    except Exception:
        return False
    return any(p.search(tail) for p in _HW_LIMIT_PATTERNS)


StageNames = {
    1: "音频提取",
    2: "ASR (whisper.cpp)",
    3: "加载翻译/润色",
    4: "翻译",
    5: "TTS 合成",
    6: "视频复合",
    7: "字幕封装",
}


@dataclass
class TaskStatus:
    id: str
    video: str
    state: str = "running"  # running | completed | failed | cancelled | paused
    stage: Optional[int] = None
    progress: float = 0.0
    message: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    work_dir: Path = field(default_factory=Path)
    log_path: Path = field(default_factory=Path)
    return_code: Optional[int] = None
    proc: Optional[subprocess.Popen] = None
    mode: str = "lite"  # lite | quality | online


class TaskManager:
    def __init__(self, config: Dict):
        self.config = config
        self.tasks: Dict[str, TaskStatus] = {}
        self.lock = threading.Lock()
        # In packaged builds (PyInstaller), __file__ is under a temp extraction dir.
        # Allow the host app (Electron) to override repo_root to a stable resources directory.
        self.repo_root = detect_repo_root()
        self._config_dir = pick_config_dir(self.repo_root)
        # Track config file mtimes to allow hot-reload without restarting the backend container.
        self._active_config_path: Optional[Path] = None
        self._active_config_mtime: Optional[float] = None
        self._defaults_config_path: Path = self._config_dir / "defaults.yaml"
        self._defaults_config_mtime: Optional[float] = None
        self._init_config_paths()
        paths = config.get("paths", {})
        outputs_root = Path(paths.get("outputs_root", "outputs"))
        self.outputs_root = outputs_root if outputs_root.is_absolute() else self.repo_root / outputs_root
        self.outputs_root.mkdir(parents=True, exist_ok=True)

    def _init_config_paths(self) -> None:
        """
        Determine which config file is active in this runtime, mirroring backend/app.py main() selection.
        We rely on CONFIG_PATH when set; otherwise prefer quality.yaml then defaults.yaml.
        """
        raw = os.environ.get("CONFIG_PATH")
        candidates: List[Path] = []
        if raw:
            candidates.append(Path(raw))
        candidates.append(self._config_dir / "quality.yaml")
        candidates.append(self._config_dir / "defaults.yaml")
        # legacy fallbacks
        candidates.append(self.repo_root / "config" / "quality.yaml")
        candidates.append(self.repo_root / "config" / "defaults.yaml")
        for p in candidates:
            try:
                if p.exists():
                    self._active_config_path = p
                    self._active_config_mtime = p.stat().st_mtime
                    break
            except Exception:
                continue
        try:
            if self._defaults_config_path.exists():
                self._defaults_config_mtime = self._defaults_config_path.stat().st_mtime
        except Exception:
            self._defaults_config_mtime = None

    def _reload_config_if_changed(self) -> None:
        """
        Reload config from disk if the active config file or defaults.yaml has changed.
        This prevents stale in-memory config after editing YAML files in bind mounts.
        """
        active = self._active_config_path
        if not active:
            return
        try:
            active_m = active.stat().st_mtime
        except Exception:
            active_m = None
        try:
            defaults_m = self._defaults_config_path.stat().st_mtime if self._defaults_config_path.exists() else None
        except Exception:
            defaults_m = None

        if active_m == self._active_config_mtime and defaults_m == self._defaults_config_mtime:
            return

        # Load active config + merge defaults.yaml as base (same logic as create_app()).
        try:
            override = load_defaults(active)
            base = load_defaults(self._defaults_config_path) if self._defaults_config_path.exists() else {}

            def deep_merge(a: Dict, b: Dict) -> Dict:
                out = dict(a or {})
                for k, v in (b or {}).items():
                    if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                        out[k] = deep_merge(out[k], v)
                    else:
                        out[k] = v
                return out

            merged = deep_merge(base, override)
            self.config = merged
            self._active_config_mtime = active_m
            self._defaults_config_mtime = defaults_m
            print(f"[info] Reloaded config from {active} (mtime changed).")
        except Exception as exc:
            print(f"[warn] Failed to reload config from {active}: {exc}. Keeping existing in-memory config.")

    def _resolve_path(self, rel_or_abs) -> Path:
        # Py3.9 兼容：不使用 union 运算符
        p = Path(rel_or_abs)
        return p if p.is_absolute() else self.repo_root / p

    def _pick_executable(self, configured: str, fallbacks: List[str]) -> str:
        """
        Pick an executable path for bundled tools.
        - If configured is an absolute/relative path and exists -> use it.
        - Else try fallbacks (relative to repo root) and use the first existing one.
        - Else return configured (may rely on PATH).
        """
        try:
            p = Path(configured)
            if p.is_absolute() or (os.sep in configured) or configured.startswith("."):
                rp = self._resolve_path(p)
                if rp.exists():
                    return str(rp)
        except Exception:
            pass
        # os.uname() is not available on Windows; use platform.machine() when needed.
        try:
            host_arch = (os.uname().machine or "").lower()  # type: ignore[attr-defined]
        except Exception:
            import platform

            host_arch = (platform.machine() or "").lower()
        expected_emachine = 62 if host_arch in {"x86_64", "amd64"} else 183 if host_arch in {"aarch64", "arm64"} else None

        def _elf_emachine(path: Path) -> Optional[int]:
            try:
                b = path.read_bytes()
                if b[:4] != b"\x7fELF":
                    return None
                ei_data = b[5]
                endian = "<" if ei_data == 1 else ">"
                return int.from_bytes(b[18:20], byteorder="little" if endian == "<" else "big", signed=False)
            except Exception:
                return None

        for fb in fallbacks:
            try:
                rp = self._resolve_path(fb)
                if rp.exists():
                    if expected_emachine is not None:
                        em = _elf_emachine(rp)
                        # If we can detect ELF and it's wrong arch, skip this candidate.
                        if em is not None and em != expected_emachine:
                            continue
                    return str(rp)
            except Exception:
                continue
        return configured

    def start_task(self, video_path: str, params: Dict, preset: Optional[str] = None, mode: str = "lite") -> str:
        # Ensure we pick up latest YAML edits without requiring a backend restart.
        self._reload_config_if_changed()
        task_id = uuid.uuid4().hex[:12]
        work_dir = self.outputs_root / task_id
        work_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._prepare_log_path(work_dir)

        effective = self._merge_config(params, preset)
        cmd = self._build_command(video_path, work_dir, effective, mode=mode, resume_from=None)
        self._write_task_meta(
            work_dir,
            {
                "task_id": task_id,
                "video": video_path,
                "mode": mode,
                "preset": preset,
                "params": params,
                "cmd": cmd,
                "created_at": time.time(),
            },
        )
        self._spawn_task(task_id, video_path, work_dir, log_path, cmd, effective, mode=mode)
        return task_id

    def resume_task(
        self,
        task_id: str,
        resume_from: str,
        params_overrides: Optional[Dict] = None,
        preset: Optional[str] = None,
    ) -> str:
        """
        Resume a task in-place (same task_id/work_dir) from a later stage.
        This enables "断点续跑与步骤复用": reusing existing artifacts and skipping completed steps.
        """
        self._reload_config_if_changed()
        if resume_from not in {"asr", "mt", "tts", "mux"}:
            raise ValueError(f"invalid resume_from: {resume_from}")

        work_dir = self.outputs_root / task_id
        if not work_dir.exists():
            raise ValueError(f"task work_dir not found: {work_dir}")

        with self.lock:
            existing = self.tasks.get(task_id)
        if existing and existing.state == "running":
            raise ValueError("task is running; cannot resume")

        meta = self._read_task_meta(work_dir) or {}
        video_path = (existing.video if existing else None) or meta.get("video")
        mode = (existing.mode if existing else None) or meta.get("mode") or "lite"
        base_preset = preset if preset is not None else meta.get("preset")
        base_params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
        merged_params = dict(base_params)
        merged_params.update(params_overrides or {})
        # Resuming from TTS/mux implies we want to actually generate audio/video even if the previous run was subtitle-only.
        if resume_from in {"tts", "mux"}:
            merged_params["skip_tts"] = False

        if not video_path:
            raise ValueError("cannot resume: missing video path (task_meta.json not found or incomplete)")

        log_path = self._prepare_log_path(work_dir)
        effective = self._merge_config(merged_params, base_preset)
        cmd = self._build_command(str(video_path), work_dir, effective, mode=mode, resume_from=resume_from)
        self._write_task_meta(
            work_dir,
            {
                "task_id": task_id,
                "video": str(video_path),
                "mode": mode,
                "preset": base_preset,
                "params": merged_params,
                "cmd": cmd,
                "resume_from": resume_from,
                "resumed_at": time.time(),
            },
        )
        self._spawn_task(task_id, str(video_path), work_dir, log_path, cmd, effective, mode=mode, replace_existing=True)
        return task_id

    def _prepare_log_path(self, work_dir: Path) -> Path:
        """
        Keep `run.log` as the latest run; rotate existing logs to `run.<ts>.log`.
        """
        log_path = work_dir / "run.log"
        if log_path.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            rotated = work_dir / f"run.{ts}.log"
            try:
                log_path.rename(rotated)
            except Exception:
                # If rename fails (e.g. permission), fall back to truncating.
                try:
                    log_path.write_text("", encoding="utf-8")
                except Exception:
                    pass
        return work_dir / "run.log"

    def _write_task_meta(self, work_dir: Path, meta: Dict[str, Any]) -> None:
        try:
            (work_dir / "task_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            # best-effort; never block task start
            pass

    def _read_task_meta(self, work_dir: Path) -> Optional[Dict[str, Any]]:
        p = work_dir / "task_meta.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8", errors="ignore") or "{}")
        except Exception:
            return None

    def _build_env(self, effective: Dict) -> Dict[str, str]:
        env = os.environ.copy()
        tts_home = self.config.get("paths", {}).get("tts_home")
        if tts_home:
            env["TTS_HOME"] = str(self._resolve_path(tts_home))

        # Ensure bundled shared libs (whisper.cpp / piper) can be resolved at runtime.
        ld_parts = [p for p in (env.get("LD_LIBRARY_PATH") or "").split(os.pathsep) if p]
        for rel in ("bin", "local_bin", "local_bin/piper"):
            p = str(self._resolve_path(rel))
            if p not in ld_parts:
                ld_parts.insert(0, p)
        env["LD_LIBRARY_PATH"] = os.pathsep.join(ld_parts)

        # 全局离线：运行期禁止任何联网下载（HuggingFace/Transformers/Datasets 等）
        if effective.get("offline", False):
            hf_cache_rel = self.config.get("paths", {}).get("hf_cache") or "assets/models/hf"
            hf_cache = self._resolve_path(hf_cache_rel)
            hf_cache.mkdir(parents=True, exist_ok=True)
            env["HUGGINGFACE_HUB_CACHE"] = str(hf_cache)
            env["HF_HOME"] = str(hf_cache)
            env["HF_HUB_OFFLINE"] = "1"
            env["TRANSFORMERS_OFFLINE"] = "1"
            env["HF_DATASETS_OFFLINE"] = "1"
            env["HF_HUB_DISABLE_TELEMETRY"] = "1"
            env.pop("HF_ENDPOINT", None)
        hf_endpoint = effective.get("hf_endpoint")
        if hf_endpoint:
            env["HF_ENDPOINT"] = hf_endpoint
        return env

    def _spawn_task(
        self,
        task_id: str,
        video_path: str,
        work_dir: Path,
        log_path: Path,
        cmd: List[str],
        effective: Dict,
        *,
        mode: str,
        replace_existing: bool = False,
    ) -> None:
        env = self._build_env(effective)
        # On Windows, spawning a console-subsystem executable from a GUI (windowed) parent can
        # flash a console window briefly. Hide it to keep the UX clean in the Electron app.
        creationflags = 0
        startupinfo = None
        stdin = None
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0  # SW_HIDE
                startupinfo = si
            except Exception:
                startupinfo = None
            stdin = subprocess.DEVNULL

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            stdin=stdin,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )

        status = TaskStatus(id=task_id, video=video_path, work_dir=work_dir, log_path=log_path, mode=mode)
        status.proc = proc
        with self.lock:
            if replace_existing or task_id not in self.tasks:
                self.tasks[task_id] = status
            else:
                self.tasks[task_id] = status

        t = threading.Thread(target=self._watch_process, args=(task_id, proc, cmd), daemon=True)
        t.start()

    def _is_packaged_exe(self) -> bool:
        """
        Detect whether we are running under the packaged backend executable.
        Relying on sys.frozen is usually correct, but in some launch contexts it may be missing.
        We also check the executable name to avoid spawning another Flask server by mistake.
        """
        try:
            if getattr(sys, "frozen", False):
                return True
        except Exception:
            pass
        try:
            return Path(sys.executable).name.lower() == "backend_server.exe"
        except Exception:
            return False

    def _merge_config(self, params: Dict, preset: Optional[str]) -> Dict:
        defaults = self.config.get("defaults", {})
        preset_cfg = self.config.get("presets", {}).get(preset or "", {})
        merged = {**defaults, **preset_cfg}
        merged.update({k: v for k, v in params.items() if v is not None})
        return merged

    def _select_script(self, mode: str, cfg: Dict) -> Path:
        paths = self.config.get("paths", {})
        if mode == "lite":
            # Always use the lite pipeline script, regardless of which config file is loaded.
            return self._resolve_path(paths.get("script_lite", "pipelines/asr_translate_tts.py"))
        if mode == "quality":
            # Always use the quality pipeline script.
            return self._resolve_path(paths.get("script_quality", "pipelines/quality_pipeline.py"))
        if mode == "online":
            return self._resolve_path(paths.get("script_online", "pipelines/online_pipeline.py"))
        raise ValueError(f"未知模式: {mode}")

    def _build_command(self, video_path: str, work_dir: Path, cfg: Dict, mode: str, resume_from: Optional[str] = None) -> List[str]:
        if mode == "quality":
            return self._build_cmd_quality(video_path, work_dir, cfg, resume_from=resume_from)
        if mode == "online":
            return self._build_cmd_online(video_path, work_dir, cfg)
        return self._build_cmd_lite(video_path, work_dir, cfg, mode, resume_from=resume_from)

    def _build_cmd_lite(self, video_path: str, work_dir: Path, cfg: Dict, mode: str, resume_from: Optional[str] = None) -> List[str]:
        paths = self.config.get("paths", {})
        script = self._select_script(mode, cfg)
        whisper_candidate = cfg.get("whispercpp_bin") or paths.get("whispercpp_bin") or "bin/whisper-cli"
        whisper_bin = self._resolve_path(whisper_candidate)
        if not whisper_bin.exists():
            whisper_bin = self._resolve_path("bin/main")
        asr_model = self._resolve_path(cfg.get("asr_model") or cfg.get("whispercpp_model") or paths.get("whispercpp_model", "assets/models/ggml-small-q5_0.bin"))
        # Lite mode default: prefer Piper (fully offline, minimal deps), as configured by defaults.yaml.
        # Allow overrides via cfg when needed for engineering/experiments.
        mt_model = cfg.get("mt_model", "Helsinki-NLP/opus-mt-zh-en")
        tts_backend = str(cfg.get("tts_backend") or "piper").strip().lower()
        coqui_model = cfg.get("coqui_model") or cfg.get("tts_model") or "tts_models/en/ljspeech/tacotron2-DDC"
        piper_model = cfg.get("piper_model") or paths.get("piper_model") or "assets/models/en_US-amy-low.onnx"
        piper_bin = cfg.get("piper_bin") or paths.get("piper_bin") or "piper"
        # Transformers/HF cache dir (must match env injected by start_task)
        hf_cache_rel = paths.get("hf_cache") or "assets/models/hf"
        mt_cache_dir = self._resolve_path(hf_cache_rel)

        args: List[str] = [
            "--video",
            str(video_path),
            "--output-dir",
            str(work_dir),
            "--glossary",
            str(self._resolve_path(paths.get("glossary", "assets/glossary/glossary.json"))),
            "--whispercpp-bin",
            str(whisper_bin),
            "--whispercpp-model",
            str(asr_model),
            "--mt-model",
            mt_model,
            "--mt-device",
            cfg.get("mt_device", "auto"),
            "--mt-cache-dir",
            str(mt_cache_dir),
            "--sample-rate",
            str(cfg.get("sample_rate", 16000)),
        ]
        if resume_from:
            args += ["--resume-from", resume_from]
        if cfg.get("chs_override_srt"):
            args += ["--chs-override-srt", str(cfg["chs_override_srt"])]
        if cfg.get("eng_override_srt"):
            args += ["--eng-override-srt", str(cfg["eng_override_srt"])]
        if cfg.get("chs_override_srt"):
            args += ["--chs-override-srt", str(cfg["chs_override_srt"])]
        if cfg.get("eng_override_srt"):
            args += ["--eng-override-srt", str(cfg["eng_override_srt"])]
        if cfg.get("offline"):
            args.append("--offline")
        if cfg.get("whispercpp_threads"):
            args += ["--whispercpp-threads", str(cfg["whispercpp_threads"])]
        # whisper.cpp VAD requires a separate VAD model file; only enable when provided.
        vad_model_cfg = cfg.get("vad_model") or paths.get("vad_model")
        vad_model_path = self._resolve_path(vad_model_cfg) if vad_model_cfg else None
        if cfg.get("vad_enable") and vad_model_path and vad_model_path.exists():
            args.append("--vad-enable")
            args += ["--vad-model", str(vad_model_path)]
            if cfg.get("vad_threshold"):
                args += ["--vad-thold", str(cfg["vad_threshold"])]
            if cfg.get("vad_min_dur"):
                args += ["--vad-min-dur", str(cfg["vad_min_dur"])]
        if cfg.get("denoise"):
            args.append("--denoise")
            if cfg.get("denoise_model"):
                args += ["--denoise-model", str(self._resolve_path(cfg["denoise_model"]))]
        if cfg.get("bilingual_srt"):
            args.append("--bilingual-srt")

        # TTS (lite): default piper; allow coqui as an override.
        if tts_backend not in {"piper", "coqui"}:
            tts_backend = "piper"
        args += ["--tts-backend", tts_backend]
        if tts_backend == "piper":
            args += ["--piper-model", str(self._resolve_path(piper_model))]
            args += ["--piper-bin", str(piper_bin)]
        else:
            args += [
                "--coqui-model",
                coqui_model,
                "--coqui-device",
                cfg.get("tts_device", cfg.get("coqui_device", "auto")),
            ]
            if cfg.get("coqui_speaker"):
                args += ["--coqui-speaker", cfg["coqui_speaker"]]
            if cfg.get("coqui_language"):
                args += ["--coqui-language", cfg["coqui_language"]]

        if cfg.get("en_polish_model"):
            args += ["--en-polish-model", cfg["en_polish_model"], "--en-polish-device", cfg.get("en_polish_device", "auto")]
        if cfg.get("lt_enable"):
            args.append("--lt-enable")
        if cfg.get("replacements"):
            args += ["--replacements", str(self._resolve_path(cfg["replacements"]))]
        if cfg.get("skip_tts"):
            args.append("--skip-tts")
        if cfg.get("min_sub_duration"):
            args += ["--min-sub-dur", str(cfg["min_sub_duration"])]
        if cfg.get("tts_split_len"):
            args += ["--tts-split-len", str(cfg["tts_split_len"])]
        if cfg.get("tts_speed_max"):
            args += ["--tts-speed-max", str(cfg["tts_speed_max"])]
        if str(cfg.get("tts_align_mode", "") or "").strip():
            args += ["--tts-align-mode", str(cfg.get("tts_align_mode")).strip()]
        # ASR normalization (low-risk). Pass through only when enabled.
        if cfg.get("asr_normalize_enable"):
            args.append("--asr-normalize-enable")
            if cfg.get("asr_normalize_dict"):
                args += ["--asr-normalize-dict", str(self._resolve_path(cfg["asr_normalize_dict"]))]

        # ----------------------------
        # P2-ASR: ASR enhancements (audio preprocess / merge-short / LLM fix)
        # ----------------------------
        if cfg.get("asr_preprocess_enable"):
            args.append("--asr-preprocess-enable")
            if cfg.get("asr_preprocess_loudnorm"):
                args.append("--asr-preprocess-loudnorm")
            if cfg.get("asr_preprocess_highpass") is not None:
                args += ["--asr-preprocess-highpass", str(int(cfg.get("asr_preprocess_highpass", 80) or 80))]
            if cfg.get("asr_preprocess_lowpass") is not None:
                args += ["--asr-preprocess-lowpass", str(int(cfg.get("asr_preprocess_lowpass", 8000) or 8000))]
            if str(cfg.get("asr_preprocess_ffmpeg_extra", "") or "").strip():
                args += ["--asr-preprocess-ffmpeg-extra", str(cfg.get("asr_preprocess_ffmpeg_extra")).strip()]
        if cfg.get("asr_merge_short_enable"):
            args.append("--asr-merge-short-enable")
            args += ["--asr-merge-min-dur-s", str(float(cfg.get("asr_merge_min_dur_s", 0.8) or 0.8))]
            args += ["--asr-merge-min-chars", str(int(cfg.get("asr_merge_min_chars", 6) or 6))]
            args += ["--asr-merge-max-gap-s", str(float(cfg.get("asr_merge_max_gap_s", 0.25) or 0.25))]
            args += ["--asr-merge-max-group-chars", str(int(cfg.get("asr_merge_max_group_chars", 120) or 120))]
            if cfg.get("asr_merge_save_debug"):
                args.append("--asr-merge-save-debug")
        if cfg.get("asr_llm_fix_enable"):
            args.append("--asr-llm-fix-enable")
            if str(cfg.get("asr_llm_fix_mode", "") or "").strip():
                args += ["--asr-llm-fix-mode", str(cfg.get("asr_llm_fix_mode")).strip()]
            if cfg.get("asr_llm_fix_max_items") is not None:
                args += ["--asr-llm-fix-max-items", str(int(cfg.get("asr_llm_fix_max_items", 60) or 60))]
            if cfg.get("asr_llm_fix_min_chars") is not None:
                args += ["--asr-llm-fix-min-chars", str(int(cfg.get("asr_llm_fix_min_chars", 12) or 12))]
            if cfg.get("asr_llm_fix_save_debug"):
                args.append("--asr-llm-fix-save-debug")
            if str(cfg.get("asr_llm_fix_model", "") or "").strip():
                args += ["--asr-llm-fix-model", str(cfg.get("asr_llm_fix_model")).strip()]
        # Sentence-unit merge (min-risk). Pass through only when enabled.
        if cfg.get("sentence_unit_enable"):
            args.append("--sentence-unit-enable")
            args += ["--sentence-unit-min-chars", str(cfg.get("sentence_unit_min_chars", 12))]
            args += ["--sentence-unit-max-chars", str(cfg.get("sentence_unit_max_chars", 60))]
            args += ["--sentence-unit-max-segs", str(cfg.get("sentence_unit_max_segs", 3))]
            args += ["--sentence-unit-max-gap-s", str(cfg.get("sentence_unit_max_gap_s", 0.6))]
            args += ["--sentence-unit-boundary-punct", str(cfg.get("sentence_unit_boundary_punct", "。！？!?.,"))]
            bw = cfg.get("sentence_unit_break_words")
            if isinstance(bw, list) and bw:
                args += ["--sentence-unit-break-words", ",".join([str(x) for x in bw if str(x).strip()])]
            elif isinstance(bw, str) and bw.strip():
                args += ["--sentence-unit-break-words", bw.strip()]

        # Auto entity protection (optional)
        if cfg.get("entity_protect_enable"):
            args.append("--entity-protect-enable")
            args += ["--entity-protect-min-len", str(cfg.get("entity_protect_min_len", 2))]
            args += ["--entity-protect-max-len", str(cfg.get("entity_protect_max_len", 6))]
            args += ["--entity-protect-min-freq", str(cfg.get("entity_protect_min_freq", 2))]
            args += ["--entity-protect-max-items", str(cfg.get("entity_protect_max_items", 30))]

        # In packaged builds, sys.executable points to backend_server.exe, not python.exe.
        # Use backend_server.exe's special entry: `--run-pipeline lite` to execute the script.
        if self._is_packaged_exe():
            return [sys.executable, "--run-pipeline", "lite", *args]
        return [sys.executable, str(script), *args]

    def _build_cmd_quality(self, video_path: str, work_dir: Path, cfg: Dict, resume_from: Optional[str] = None) -> List[str]:
        script = self._select_script("quality", cfg)
        paths = self.config.get("paths", {})
        gates = self.config.get("quality_gates", {}) or {}
        whisperx_model = cfg.get("whisperx_model", "large-v3")
        # Prefer paths.whisperx_model_dir (may be overridden by app via YGF_MODELS_ROOT -> user_data/models/whisperx).
        whisperx_dir = self._resolve_path(paths.get("whisperx_model_dir") or cfg.get("whisperx_model_dir") or "assets/models/whisperx")
        # Product decision: coqui-only (do not expose / run piper via backend API).
        coqui_model = cfg.get("coqui_model") or "tts_models/en/ljspeech/tacotron2-DDC"

        cmd: List[str] = [
            sys.executable,
            str(script),
            "--video",
            str(video_path),
            "--output-dir",
            str(work_dir),
            "--glossary",
            str(self._resolve_path(paths.get("glossary", "assets/glossary/glossary.json"))),
            "--whisperx-model",
            whisperx_model,
            "--whisperx-model-dir",
            str(whisperx_dir),
            "--llm-endpoint",
            cfg.get("llm_endpoint", "http://ollama:11434/v1"),
            "--llm-model",
            cfg.get("llm_model", "qwen2.5:7b"),
            "--llm-chunk-size",
            str(cfg.get("llm_chunk_size", 2)),
            "--sample-rate",
            str(cfg.get("sample_rate", 16000)),
            "--max-sentence-len",
            str(cfg.get("max_sentence_len", 50)),
            "--min-sub-dur",
            str(cfg.get("min_sub_duration", 1.8)),
            "--tts-split-len",
            str(cfg.get("tts_split_len", 80)),
            "--tts-speed-max",
            str(cfg.get("tts_speed_max", 1.1)),
            # P0: use the same thresholds as quality gates for post-process (no hidden rules)
            "--subtitle-max-cps",
            str(gates.get("max_cps", 20.0)),
            "--subtitle-max-chars-per-line",
            str(gates.get("max_chars_per_line", 80)),
        ]
        # Subtitle burn-in style (hard-sub)
        cmd += ["--sub-font-name", str(cfg.get("sub_font_name", "Arial") or "Arial")]
        cmd += ["--sub-font-size", str(int(cfg.get("sub_font_size", 18) or 18))]
        cmd += ["--sub-outline", str(int(cfg.get("sub_outline", 1) or 1))]
        cmd += ["--sub-shadow", str(int(cfg.get("sub_shadow", 0) or 0))]
        cmd += ["--sub-margin-v", str(int(cfg.get("sub_margin_v", 24) or 24))]
        cmd += ["--sub-alignment", str(int(cfg.get("sub_alignment", 2) or 2))]
        # subtitle placement box (optional, takes precedence when enabled)
        if cfg.get("sub_place_enable"):
            cmd.append("--sub-place-enable")
        if str(cfg.get("sub_place_coord_mode", "") or "").strip():
            cmd += ["--sub-place-coord-mode", str(cfg.get("sub_place_coord_mode")).strip()]
        if cfg.get("sub_place_x") is not None:
            cmd += ["--sub-place-x", str(cfg.get("sub_place_x", 0.0))]
        if cfg.get("sub_place_y") is not None:
            cmd += ["--sub-place-y", str(cfg.get("sub_place_y", 0.78))]
        if cfg.get("sub_place_w") is not None:
            cmd += ["--sub-place-w", str(cfg.get("sub_place_w", 1.0))]
        if cfg.get("sub_place_h") is not None:
            cmd += ["--sub-place-h", str(cfg.get("sub_place_h", 0.22))]
        # Mux sync (hearing-first)
        cmd += ["--mux-sync-strategy", str(cfg.get("mux_sync_strategy", "slow") or "slow")]
        cmd += ["--mux-slow-max-ratio", str(float(cfg.get("mux_slow_max_ratio", 1.08) or 1.08))]
        cmd += ["--mux-slow-threshold-s", str(float(cfg.get("mux_slow_threshold_s", 0.05) or 0.05))]
        # Prompt-level MT/LLM quality knobs (general, safe defaults)
        if int(cfg.get("mt_context_window", 0) or 0) > 0:
            cmd += ["--mt-context-window", str(int(cfg.get("mt_context_window", 0) or 0))]
        if str(cfg.get("mt_topic", "") or "").strip():
            cmd += ["--mt-topic", str(cfg.get("mt_topic", "")).strip()]
        # MT style + subtitle concision controls (best-practice prompt tuning; fallback-safe)
        if str(cfg.get("mt_style", "") or "").strip():
            cmd += ["--mt-style", str(cfg.get("mt_style", "")).strip()]
        if cfg.get("mt_max_words_per_line") is not None:
            cmd += ["--mt-max-words-per-line", str(int(cfg.get("mt_max_words_per_line", 0) or 0))]
        # Two-stage prompt (best practice): short prompt by default, long prompt only for problematic lines.
        if str(cfg.get("mt_prompt_mode", "") or "").strip():
            cmd += ["--mt-prompt-mode", str(cfg.get("mt_prompt_mode")).strip()]
        if cfg.get("mt_long_fallback_enable"):
            cmd.append("--mt-long-fallback-enable")
        if cfg.get("mt_long_examples_enable"):
            cmd.append("--mt-long-examples-enable")
        if cfg.get("mt_compact_enable"):
            cmd.append("--mt-compact-enable")
            if cfg.get("mt_compact_aggressive"):
                cmd.append("--mt-compact-aggressive")
            if cfg.get("mt_compact_temperature") is not None:
                cmd += ["--mt-compact-temperature", str(float(cfg.get("mt_compact_temperature", 0.1) or 0.1))]
            if cfg.get("mt_compact_max_tokens") is not None:
                cmd += ["--mt-compact-max-tokens", str(int(cfg.get("mt_compact_max_tokens", 96) or 96))]
            if cfg.get("mt_compact_timeout_s") is not None:
                cmd += ["--mt-compact-timeout-s", str(int(cfg.get("mt_compact_timeout_s", 120) or 120))]
        if cfg.get("mt_long_zh_chars") is not None:
            cmd += ["--mt-long-zh-chars", str(int(cfg.get("mt_long_zh_chars", 60) or 60))]
        if cfg.get("mt_long_en_words") is not None:
            cmd += ["--mt-long-en-words", str(int(cfg.get("mt_long_en_words", 22) or 22))]
        if cfg.get("mt_long_target_words") is not None:
            cmd += ["--mt-long-target-words", str(int(cfg.get("mt_long_target_words", 18) or 18))]
        if cfg.get("glossary_prompt_enable"):
            cmd.append("--glossary-prompt-enable")
        # llm_selfcheck_enable removed: it overlaps with TRA/QE rewrite paths and increases regression risk.
        if cfg.get("mt_json_enable"):
            cmd.append("--mt-json-enable")
        if cfg.get("mt_topic_auto_enable"):
            cmd.append("--mt-topic-auto-enable")
            cmd += ["--mt-topic-auto-max-segs", str(int(cfg.get("mt_topic_auto_max_segs", 20) or 20))]
        if cfg.get("glossary_placeholder_enable"):
            cmd.append("--glossary-placeholder-enable")
            cmd += ["--glossary-placeholder-max", str(int(cfg.get("glossary_placeholder_max", 6) or 6))]
        if cfg.get("qe_enable"):
            cmd.append("--qe-enable")
            cmd += ["--qe-threshold", str(float(cfg.get("qe_threshold", 3.5) or 3.5))]
            if str(cfg.get("qe_mode", "") or "").strip():
                cmd += ["--qe-mode", str(cfg.get("qe_mode")).strip()]
            if cfg.get("qe_max_items") is not None:
                cmd += ["--qe-max-items", str(int(cfg.get("qe_max_items", 200) or 200))]
            if cfg.get("qe_save_report"):
                cmd.append("--qe-save-report")
            if str(cfg.get("qe_model", "") or "").strip():
                cmd += ["--qe-model", str(cfg.get("qe_model")).strip()]
            if cfg.get("qe_time_budget_s") is not None:
                cmd += ["--qe-time-budget-s", str(int(cfg.get("qe_time_budget_s", 180) or 180))]
            if cfg.get("qe_embed_enable"):
                cmd.append("--qe-embed-enable")
                if str(cfg.get("qe_embed_model", "") or "").strip():
                    cmd += ["--qe-embed-model", str(cfg.get("qe_embed_model")).strip()]
                if cfg.get("qe_embed_threshold") is not None:
                    cmd += ["--qe-embed-threshold", str(float(cfg.get("qe_embed_threshold", 0.55) or 0.55))]
                if cfg.get("qe_embed_max_segs") is not None:
                    cmd += ["--qe-embed-max-segs", str(int(cfg.get("qe_embed_max_segs", 2000) or 2000))]
            if cfg.get("qe_backtranslate_enable"):
                cmd.append("--qe-backtranslate-enable")
                if str(cfg.get("qe_backtranslate_model", "") or "").strip():
                    cmd += ["--qe-backtranslate-model", str(cfg.get("qe_backtranslate_model")).strip()]
                if cfg.get("qe_backtranslate_max_items") is not None:
                    cmd += ["--qe-backtranslate-max-items", str(int(cfg.get("qe_backtranslate_max_items", 60) or 60))]
                if cfg.get("qe_backtranslate_overlap_threshold") is not None:
                    cmd += [
                        "--qe-backtranslate-overlap-threshold",
                        str(float(cfg.get("qe_backtranslate_overlap_threshold", 0.35) or 0.35)),
                    ]
        # Audio denoise (safe fallback in scripts: arnndn w/o model -> anlmdn)
        if cfg.get("denoise"):
            cmd.append("--denoise")
            if cfg.get("denoise_model"):
                cmd += ["--denoise-model", str(self._resolve_path(cfg["denoise_model"]))]
        # WhisperX VAD (no external model needed)
        if cfg.get("vad_enable"):
            cmd.append("--vad-enable")
            if cfg.get("vad_threshold") is not None:
                cmd += ["--vad-thold", str(cfg.get("vad_threshold"))]
            if cfg.get("vad_min_dur") is not None:
                cmd += ["--vad-min-dur", str(cfg.get("vad_min_dur"))]
        if resume_from:
            cmd += ["--resume-from", resume_from]
        if cfg.get("diarization"):
            cmd.append("--diarization")
        if cfg.get("llm_api_key"):
            cmd += ["--llm-api-key", cfg["llm_api_key"]]

        # TTS (coqui-only)
        cmd += ["--tts-backend", "coqui"]
        cmd += [
            "--coqui-model",
            coqui_model,
            "--coqui-device",
            cfg.get("coqui_device", "auto"),
        ]
        # Sentence-unit merge (min-risk). Pass through only when enabled.
        if cfg.get("sentence_unit_enable"):
            cmd.append("--sentence-unit-enable")
            cmd += ["--sentence-unit-min-chars", str(cfg.get("sentence_unit_min_chars", 12))]
            cmd += ["--sentence-unit-max-chars", str(cfg.get("sentence_unit_max_chars", 60))]
            cmd += ["--sentence-unit-max-segs", str(cfg.get("sentence_unit_max_segs", 3))]
            cmd += ["--sentence-unit-max-gap-s", str(cfg.get("sentence_unit_max_gap_s", 0.6))]
            cmd += ["--sentence-unit-boundary-punct", str(cfg.get("sentence_unit_boundary_punct", "。！？!?.,"))]
            bw = cfg.get("sentence_unit_break_words")
            if isinstance(bw, list) and bw:
                cmd += ["--sentence-unit-break-words", ",".join([str(x) for x in bw if str(x).strip()])]
            elif isinstance(bw, str) and bw.strip():
                cmd += ["--sentence-unit-break-words", bw.strip()]

        # Auto entity protection (optional)
        if cfg.get("entity_protect_enable"):
            cmd.append("--entity-protect-enable")
            cmd += ["--entity-protect-min-len", str(cfg.get("entity_protect_min_len", 2))]
            cmd += ["--entity-protect-max-len", str(cfg.get("entity_protect_max_len", 6))]
            cmd += ["--entity-protect-min-freq", str(cfg.get("entity_protect_min_freq", 2))]
            cmd += ["--entity-protect-max-items", str(cfg.get("entity_protect_max_items", 30))]
        # ASR normalization (low-risk). Pass through only when enabled.
        if cfg.get("asr_normalize_enable"):
            cmd.append("--asr-normalize-enable")
            if cfg.get("asr_normalize_dict"):
                cmd += ["--asr-normalize-dict", str(self._resolve_path(cfg["asr_normalize_dict"]))]

        # ----------------------------
        # P0: subtitle post-process + TTS-script separation
        # ----------------------------
        if cfg.get("subtitle_postprocess_enable"):
            cmd.append("--subtitle-postprocess-enable")
        if cfg.get("subtitle_wrap_enable"):
            cmd.append("--subtitle-wrap-enable")
            cmd += ["--subtitle-wrap-max-lines", str(cfg.get("subtitle_wrap_max_lines", 2))]
        if cfg.get("subtitle_cps_fix_enable"):
            cmd.append("--subtitle-cps-fix-enable")
            cmd += ["--subtitle-cps-safety-gap", str(cfg.get("subtitle_cps_safety_gap", 0.2))]
        if cfg.get("tts_script_enable"):
            cmd.append("--tts-script-enable")
        if cfg.get("tts_script_strict_clean_enable"):
            cmd.append("--tts-script-strict-clean-enable")
        # P0: display subtitles (may change line count)
        if cfg.get("display_srt_enable"):
            cmd.append("--display-srt-enable")
        if cfg.get("display_use_for_embed"):
            cmd.append("--display-use-for-embed")
        if cfg.get("display_max_chars_per_line") is not None:
            cmd += ["--display-max-chars-per-line", str(int(cfg.get("display_max_chars_per_line", 42) or 42))]
        if cfg.get("display_max_lines") is not None:
            cmd += ["--display-max-lines", str(int(cfg.get("display_max_lines", 2) or 2))]
        if cfg.get("display_merge_enable"):
            cmd.append("--display-merge-enable")
            cmd += ["--display-merge-max-gap-s", str(float(cfg.get("display_merge_max_gap_s", 0.25) or 0.25))]
            cmd += ["--display-merge-max-chars", str(int(cfg.get("display_merge_max_chars", 80) or 80))]
        if cfg.get("display_split_enable"):
            cmd.append("--display-split-enable")
            cmd += ["--display-split-max-chars", str(int(cfg.get("display_split_max_chars", 86) or 86))]
        # P0-4: pause-before-translate terminology hook (optional)
        if cfg.get("mt_pause_before_translate"):
            cmd.append("--mt-pause-before-translate")
        # P1-1: meaning-based splitting (semantic) for overly long Chinese segments
        if cfg.get("meaning_split_enable"):
            cmd.append("--meaning-split-enable")
            cmd += ["--meaning-split-min-chars", str(int(cfg.get("meaning_split_min_chars", 90) or 90))]
            cmd += ["--meaning-split-max-parts", str(int(cfg.get("meaning_split_max_parts", 3) or 3))]
            if cfg.get("meaning_split_save_debug"):
                cmd.append("--meaning-split-save-debug")
        # Hard subtitle erase (burned-in subtitles on video frames)
        if cfg.get("erase_subtitle_enable"):
            cmd.append("--erase-subtitle-enable")
            cmd += ["--erase-subtitle-method", str(cfg.get("erase_subtitle_method", "delogo") or "delogo")]
            cmd += ["--erase-subtitle-coord-mode", str(cfg.get("erase_subtitle_coord_mode", "ratio") or "ratio")]
            cmd += ["--erase-subtitle-x", str(cfg.get("erase_subtitle_x", 0.0))]
            cmd += ["--erase-subtitle-y", str(cfg.get("erase_subtitle_y", 0.78))]
            cmd += ["--erase-subtitle-w", str(cfg.get("erase_subtitle_w", 1.0))]
            cmd += ["--erase-subtitle-h", str(cfg.get("erase_subtitle_h", 0.22))]
            cmd += ["--erase-subtitle-blur-radius", str(int(cfg.get("erase_subtitle_blur_radius", 12) or 12))]
        # P1: TTS fitting (rule-based trim). Enabled only when configured.
        if cfg.get("tts_fit_enable"):
            cmd.append("--tts-fit-enable")
            cmd += ["--tts-fit-wps", str(cfg.get("tts_fit_wps", 2.6))]
            cmd += ["--tts-fit-min-words", str(cfg.get("tts_fit_min_words", 3))]
            if cfg.get("tts_fit_save_raw"):
                cmd.append("--tts-fit-save-raw")
        # P1-2: per-segment TTS planning (speed cap + pause audit)
        if cfg.get("tts_plan_enable"):
            cmd.append("--tts-plan-enable")
            cmd += ["--tts-plan-safety-margin", str(float(cfg.get("tts_plan_safety_margin", 0.05) or 0.05))]
            cmd += ["--tts-plan-min-cap", str(float(cfg.get("tts_plan_min_cap", 1.05) or 1.05))]
        # Friendlier over-budget handling: use local LLM to rewrite too-long English lines (fallback-safe)
        if cfg.get("tts_trim_llm_enable"):
            cmd.append("--tts-trim-llm-enable")
            if cfg.get("tts_trim_llm_aggressive"):
                cmd.append("--tts-trim-llm-aggressive")
            if cfg.get("tts_trim_llm_temperature") is not None:
                cmd += ["--tts-trim-llm-temperature", str(float(cfg.get("tts_trim_llm_temperature", 0.1) or 0.1))]
            if cfg.get("tts_trim_llm_max_tokens") is not None:
                cmd += ["--tts-trim-llm-max-tokens", str(int(cfg.get("tts_trim_llm_max_tokens", 96) or 96))]
            if cfg.get("tts_trim_llm_timeout_s") is not None:
                cmd += ["--tts-trim-llm-timeout-s", str(int(cfg.get("tts_trim_llm_timeout_s", 120) or 120))]
        # P1-4: background audio mix (ducking + loudnorm)
        if cfg.get("bgm_mix_enable"):
            cmd.append("--bgm-mix-enable")
            cmd += ["--bgm-separate-method", str(cfg.get("bgm_separate_method", "none") or "none")]
            if cfg.get("bgm_duck_enable"):
                cmd.append("--bgm-duck-enable")
            cmd += ["--bgm-gain-db", str(float(cfg.get("bgm_gain_db", -10.0) or -10.0))]
            cmd += ["--tts-gain-db", str(float(cfg.get("tts_gain_db", 0.0) or 0.0))]
            if cfg.get("bgm_loudnorm_enable"):
                cmd.append("--bgm-loudnorm-enable")
            cmd += ["--bgm-sample-rate", str(int(cfg.get("bgm_sample_rate", 48000) or 48000))]
        # P1: TRA multi-step translation (optional)
        if cfg.get("tra_enable"):
            cmd.append("--tra-enable")
            if cfg.get("tra_save_debug"):
                cmd.append("--tra-save-debug")
            if cfg.get("tra_json_enable"):
                cmd.append("--tra-json-enable")
            if cfg.get("tra_auto_enable"):
                cmd.append("--tra-auto-enable")
        if self._is_packaged_exe():
            # Prefer a dedicated quality worker executable when available (dependency isolation).
            # This avoids conflicts between WhisperX heavy deps and the main backend runtime.
            try:
                worker = Path(sys.executable).resolve().with_name("quality_worker.exe")
                if worker.exists():
                    return [str(worker), "--run-pipeline", "quality", *cmd[2:]]
            except Exception:
                pass
            # Fallback: run pipelines via backend_server.exe runner entry.
            return [sys.executable, "--run-pipeline", "quality", *cmd[2:]]
        return cmd

    def _build_cmd_online(self, video_path: str, work_dir: Path, cfg: Dict) -> List[str]:
        script = self._select_script("online", cfg)
        cmd: List[str] = [
            sys.executable,
            str(script),
            "--video",
            str(video_path),
            "--output-dir",
            str(work_dir),
            "--asr-endpoint",
            cfg.get("asr_endpoint", ""),
            "--mt-endpoint",
            cfg.get("mt_endpoint", ""),
            "--mt-model",
            cfg.get("mt_model", "gpt-4o-mini"),
            "--tts-endpoint",
            cfg.get("tts_endpoint", ""),
            "--tts-voice",
            cfg.get("tts_voice", "en-US-amy"),
            "--sample-rate",
            str(cfg.get("sample_rate", 16000)),
            "--min-sub-dur",
            str(cfg.get("min_sub_duration", 1.5)),
            "--tts-split-len",
            str(cfg.get("tts_split_len", 80)),
            "--tts-speed-max",
            str(cfg.get("tts_speed_max", 1.1)),
        ]
        if cfg.get("asr_api_key"):
            cmd += ["--asr-api-key", cfg["asr_api_key"]]
        if cfg.get("mt_api_key"):
            cmd += ["--mt-api-key", cfg["mt_api_key"]]
        if cfg.get("tts_api_key"):
            cmd += ["--tts-api-key", cfg["tts_api_key"]]
        if self._is_packaged_exe():
            return [sys.executable, "--run-pipeline", "online", *cmd[2:]]
        return cmd

    def _watch_process(self, task_id: str, proc: subprocess.Popen, cmd: List[str]) -> None:
        status = self.tasks[task_id]
        log_path = status.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def _ts(epoch_seconds: float) -> str:
            # Local timezone, ISO8601 for readability
            return datetime.fromtimestamp(epoch_seconds).astimezone().isoformat(timespec="seconds")

        with log_path.open("w", encoding="utf-8") as log_file:
            log_file.write(f"[task] started_at: {_ts(status.started_at)}\n")
            try:
                log_file.write(f"[task] cmd: {' '.join([str(x) for x in (cmd or [])])}\n")
            except Exception:
                pass
            log_file.flush()
            if proc.stdout is None:
                proc.wait()
                status.ended_at = time.time()
                log_file.write(f"[task] ended_at:   {_ts(status.ended_at)}\n")
                log_file.write(f"[task] duration_s: {status.ended_at - status.started_at:.3f}\n")
                log_file.flush()
                return
            for line in proc.stdout:
                log_file.write(line)
                log_file.flush()
                self._update_progress(task_id, line)
            proc.wait()
            with self.lock:
                st = self.tasks.get(task_id)
                if not st:
                    return
                st.return_code = proc.returncode
                st.ended_at = time.time()
                if st.state != "cancelled":
                    # Special exit code: paused (e.g. pause-before-translate hook)
                    if proc.returncode == 3:
                        st.state = "paused"
                        st.message = "Paused (awaiting user action)"
                    else:
                        st.state = "completed" if proc.returncode == 0 else "failed"
                        st.message = "Done" if proc.returncode == 0 else f"Exited with {proc.returncode}"
                        if st.state == "failed":
                            if proc.returncode in {9, 137} or _log_has_hw_limit(log_path):
                                st.message = "硬件性能不足导致任务失败，请使用轻量模式（lite）重试。"
            # Append timing footer after status updated
            log_file.write(f"[task] ended_at:   {_ts(status.ended_at or time.time())}\n")
            if status.ended_at is not None:
                log_file.write(f"[task] duration_s: {status.ended_at - status.started_at:.3f}\n")
            log_file.flush()

        # Generate a rule-based quality report after task ends (best-effort).
        try:
            with self.lock:
                st = self.tasks.get(task_id)
            if st and st.state in {"completed", "failed"}:
                report_path = st.work_dir / "quality_report.json"
                report = generate_quality_report(
                    task_id=task_id,
                    mode=st.mode,
                    work_dir=st.work_dir,
                    source_video=Path(st.video) if st.video else None,
                    cfg=self.config,
                )
                write_quality_report(report_path, report)
        except Exception as exc:
            # Never fail the task due to reporting issues.
            try:
                (status.work_dir / "quality_report.error.txt").write_text(str(exc), encoding="utf-8")
            except Exception:
                pass

    def _update_progress(self, task_id: str, line: str) -> None:
        match = re.search(r"\[(\d)/7\]", line)
        with self.lock:
            st = self.tasks.get(task_id)
            if not st:
                return
            if match:
                num = int(match.group(1))
                st.stage = num
                st.progress = round(num / 7 * 100, 1)
                st.message = StageNames.get(num, "")
            else:
                st.message = line.strip() or st.message

    def get_status(self, task_id: str) -> Optional[Dict]:
        # 1) In-memory fast path
        with self.lock:
            st = self.tasks.get(task_id)
        if st:
            return {
                "id": st.id,
                "video": st.video,
                "state": st.state,
                "stage": st.stage,
                "stage_name": StageNames.get(st.stage, ""),
                "progress": st.progress,
                "message": st.message,
                "started_at": st.started_at,
                "ended_at": st.ended_at,
                "work_dir": str(st.work_dir),
                "mode": st.mode,
            }

        # 2) Disk recovery path (backend restart clears tasks dict but outputs remain)
        wd = self.resolve_work_dir(task_id)
        if not wd:
            return None
        meta = self._read_task_meta(wd) or {}
        mode = str(meta.get("mode") or "lite")
        video = str(meta.get("video") or "")

        log_path = wd / "run.log"
        stage: Optional[int] = None
        started_at: Optional[float] = None
        ended_at: Optional[float] = None
        message = ""
        state = "completed" if (wd / "output_en.mp4").exists() or (wd / "output_en_sub.mp4").exists() else "unknown"

        try:
            if log_path.exists():
                txt = log_path.read_text(encoding="utf-8", errors="ignore")
                # stage/progress
                m = re.findall(r"\[(\d)/7\]", txt)
                if m:
                    stage = int(m[-1])
                    message = StageNames.get(stage, "")
                # timestamps
                m1 = re.search(r"^\[task\]\s+started_at:\s*(.+)$", txt, flags=re.MULTILINE)
                if m1:
                    try:
                        started_at = datetime.fromisoformat(m1.group(1).strip().replace("Z", "+00:00")).timestamp()
                    except Exception:
                        started_at = None
                m2 = re.search(r"^\[task\]\s+ended_at:\s*(.+)$", txt, flags=re.MULTILINE)
                if m2:
                    try:
                        ended_at = datetime.fromisoformat(m2.group(1).strip().replace("Z", "+00:00")).timestamp()
                    except Exception:
                        ended_at = None
                # state inference from tail
                tail = txt[-5000:]
                if "Paused" in tail or "paused" in tail:
                    state = "paused"
                elif "Traceback" in tail or "RuntimeError:" in tail or "Error opening output" in tail or "Error applying option" in tail:
                    state = "failed"
                elif ended_at is not None:
                    state = "completed"
                else:
                    state = "running"
        except Exception:
            pass

        prog = round((int(stage) / 7 * 100), 1) if stage else 0.0
        return {
            "id": task_id,
            "video": video,
            "state": state,
            "stage": stage,
            "stage_name": StageNames.get(stage, ""),
            "progress": prog,
            "message": message or state,
            "started_at": started_at,
            "ended_at": ended_at,
            "work_dir": str(wd),
            "mode": mode,
        }

    def resolve_work_dir(self, task_id: str) -> Optional[Path]:
        p = self.outputs_root / task_id
        return p if p.exists() and p.is_dir() else None

    def resolve_video_path(self, task_id: str) -> Optional[Path]:
        with self.lock:
            st = self.tasks.get(task_id)
        if st and st.video:
            return Path(st.video)
        wd = self.resolve_work_dir(task_id)
        if not wd:
            return None
        meta = self._read_task_meta(wd) or {}
        v = meta.get("video")
        return Path(v) if isinstance(v, str) and v else None

    def resolve_mode(self, task_id: str) -> str:
        with self.lock:
            st = self.tasks.get(task_id)
        if st and st.mode:
            return st.mode
        wd = self.resolve_work_dir(task_id)
        if not wd:
            return "lite"
        meta = self._read_task_meta(wd) or {}
        m = meta.get("mode")
        return str(m) if m else "lite"

    def cancel(self, task_id: str) -> bool:
        with self.lock:
            st = self.tasks.get(task_id)
            if not st:
                return False
            if st.state not in {"running"}:
                return False
            st.state = "cancelled"
        proc = st.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        return True

    def stop_process(self, task_id: str) -> None:
        # Placeholder for future pid tracking; currently relies on external orchestration
        with self.lock:
            st = self.tasks.get(task_id)
            if st:
                st.state = "cancelled"

    def read_log(self, task_id: str, offset: int = 0, limit: int = 8_000) -> str:
        with self.lock:
            st = self.tasks.get(task_id)
        if st and st.log_path.exists():
            data = st.log_path.read_text(encoding="utf-8", errors="ignore")
            return data[offset : offset + limit]
        wd = self.resolve_work_dir(task_id)
        if not wd:
            return ""
        p = wd / "run.log"
        if not p.exists():
            return ""
        data = p.read_text(encoding="utf-8", errors="ignore")
        return data[offset : offset + limit]

    def list_artifacts(self, task_id: str) -> List[Dict]:
        with self.lock:
            st = self.tasks.get(task_id)
        wd = st.work_dir if st else self.resolve_work_dir(task_id)
        if not wd:
            return []
        files = [
            "asr_whispercpp.json",
            "audio.json",
            "audio.wav",
            "chs.srt",
            "chs.review.srt",
            "eng.srt",
            "eng.review.srt",
            "eng_tts.srt",
            "eng_tts_raw.srt",
            "tts_fit.json",
            "mt_topic_auto.json",
            "qe_report.json",
            "bilingual.srt",
            "quality_report.json",
            "tra_debug.json",
            "tts_full.wav",
            "output_en.mp4",
            "output_en_sub.mp4",
        ]
        found = []
        for name in files:
            p = wd / name
            if p.exists():
                found.append({"name": name, "path": str(p), "size": p.stat().st_size})
        return found

    def cleanup_artifacts(
        self,
        task_id: str,
        *,
        include_resume: bool = False,
        include_review: bool = False,
        include_diagnostics: bool = True,
    ) -> Dict[str, List[str]]:
        wd = self.resolve_work_dir(task_id)
        if not wd:
            raise ValueError("work_dir not found")

        deliverables = {"output_en_sub.mp4", "chs.srt", "eng.srt", "bilingual.srt"}
        review_files = {"chs.review.srt", "eng.review.srt", "review_audit.jsonl"}
        resume_files = {
            "asr_whispercpp.json",
            "audio.json",
            "audio.wav",
            "eng_tts.srt",
            "eng_tts_raw.srt",
            "tts_full.wav",
            "output_en.mp4",
        }
        diagnostic_files = {"tts_fit.json", "mt_topic_auto.json", "qe_report.json", "tra_debug.json", "run.log"}

        to_remove = set()
        if include_diagnostics:
            to_remove |= diagnostic_files
        if include_review:
            to_remove |= review_files
        if include_resume:
            to_remove |= resume_files
        to_remove -= deliverables

        removed: List[str] = []
        missing: List[str] = []
        errors: List[str] = []
        for name in sorted(to_remove):
            p = wd / name
            if not p.exists():
                missing.append(name)
                continue
            try:
                p.unlink()
                removed.append(name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        return {"removed": removed, "missing": missing, "errors": errors}



