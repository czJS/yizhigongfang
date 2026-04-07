import json
import hashlib
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.config import load_defaults, load_config_stack
from backend.task_launch_runtime import (
    PreparedTaskBundle,
    PreparedTaskLaunch,
    QualityQueueItem,
    build_bundle_task_meta,
    build_prepared_task_bundle,
    build_prepared_task_launch,
)
from backend.quality_queue_runtime import (
    activate_queued_quality_task,
    build_quality_queue_state,
    handle_quality_spawn_failure,
    reconcile_quality_spawned_task,
    sweep_quality_runtime_state,
    take_next_quality_queue_item,
)
from backend.task_process_runtime import (
    apply_exit_to_task_status,
    write_quality_report_best_effort,
    write_task_log_footer,
    write_task_log_header,
)
from backend.task_files_runtime import (
    prepare_log_path,
    read_json_best_effort,
    read_log_chunk_for_task,
    serialize_task_status,
    task_meta_path,
    task_state_path,
    write_json_best_effort,
)
from backend.lite_command_builder import build_lite_command
from backend.lite_policies import apply_lite_fixed_policies
from backend.quality_report import generate_quality_report, write_quality_report
from backend.ruleset_runtime import extract_rules_inputs, materialize_effective_rules
from backend.task_status_runtime import build_task_status_response, recover_task_status_from_disk
from pipelines.lib.lite_artifacts import collect_lite_cleanup_targets, list_existing_lite_artifacts
from pipelines.lib.lite_resume import collect_missing_lite_resume_artifacts, normalize_lite_resume_from
from backend.ruleset_store import default_doc as ruleset_default_doc
from core.runtime_paths import detect_repo_root, pick_config_dir


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


def apply_quality_fixed_policies(params: Dict[str, Any], *, review_resume: bool = False) -> Dict[str, Any]:
    """
    Single source of truth for product-fixed quality-mode policies.

    Keep these decisions consistent across:
    - task normalization / task_meta.params
    - review resume flows
    - automation contract checks
    """
    p: Dict[str, Any] = dict(params or {})
    p["denoise"] = True
    p["subtitle_postprocess_enable"] = True
    p["subtitle_wrap_enable"] = True
    p["display_srt_enable"] = True
    p["display_use_for_embed"] = True
    p["display_merge_enable"] = True
    p["display_split_enable"] = True
    p["tts_plan_enable"] = True
    p["tts_fit_enable"] = False
    # Quality main path always keeps zh_polish enabled as part of the
    # user-facing workflow, regardless of legacy UI toggles.
    p["zh_phrase_enable"] = True
    p["zh_post_polish_enable"] = True
    p["zh_gate_min_high_risk"] = max(1, int(p.get("zh_gate_min_high_risk", 1) or 1))
    p["mt_long_fallback_enable"] = False
    p["mt_compact_enable"] = False
    p["mt_two_pass_disable"] = True
    p["mt_reasoning_effort"] = "none"
    if review_resume:
        # Resume after review should be even more conservative on weak hardware.
        p["review_enabled"] = False
        p["stop_after"] = ""
        p["mt_request_timeout_s"] = max(1200, int(p.get("mt_request_timeout_s", 1200) or 1200))
        p["mt_request_retries"] = max(4, int(p.get("mt_request_retries", 4) or 4))
    return p


def _log_has_hw_limit(log_path: Path) -> bool:
    if not log_path.exists():
        return False
    try:
        tail = log_path.read_text(encoding="utf-8", errors="ignore")[-8000:]
    except Exception:
        return False
    return any(p.search(tail) for p in _HW_LIMIT_PATTERNS)


StageNames = {
    0: "排队中",
    1: "音频提取",
    2: "ASR（WhisperX / faster-whisper）",
    3: "中文优化与统一校审（zh_polish）",
    4: "翻译（MT）",
    5: "配音合成（TTS）",
    6: "视频复合（Mux）",
    7: "字幕封装/展示（SRT）",
    8: "收尾/完成",
}


@dataclass
class TaskStatus:
    id: str
    video: str
    state: str = "running"  # queued | running | completed | failed | cancelled | paused | unknown
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
        # Quality mode is CPU/GPU heavy; run sequentially to avoid oversubscription stalls.
        self._quality_queue: List[QualityQueueItem] = []
        self._quality_running: bool = False
        self._quality_worker = threading.Thread(target=self._quality_queue_worker, daemon=True)
        self._quality_worker.start()
        # In packaged builds (PyInstaller), __file__ is under a temp extraction dir.
        # Allow the host app (Electron) to override repo_root to a stable resources directory.
        self.repo_root = detect_repo_root()
        self._config_dir = pick_config_dir(self.repo_root)
        # Track config file mtimes to allow hot-reload without restarting the backend container.
        self._active_config_path: Optional[Path] = None
        self._active_config_mtime: Optional[float] = None
        self._defaults_config_path: Path = self._config_dir / "defaults.yaml"
        self._defaults_config_mtime: Optional[float] = None
        self._config_stack_meta: Dict[str, Any] = dict(config.get("_config_stack") or {})
        self._init_config_paths()
        paths = config.get("paths", {})
        outputs_root = Path(paths.get("outputs_root", "outputs"))
        self.outputs_root = outputs_root if outputs_root.is_absolute() else self.repo_root / outputs_root
        self.outputs_root.mkdir(parents=True, exist_ok=True)
        self._ruleset_global_path = self.repo_root / "assets" / "rules" / "ruleset.global.json"
        self._ruleset_seed_path = self.repo_root / "assets" / "rules" / "ruleset.seed.json"
        self._ruleset_templates_dir = self.repo_root / "assets" / "rules" / "templates"

    def _ensure_quality_worker(self) -> None:
        """
        Ensure the sequential quality queue worker thread is alive.

        Why: if the worker thread crashes due to an unhandled exception, newly enqueued
        quality tasks will stay in `queued` forever (0% progress, empty run.log).
        """
        try:
            if self._quality_worker is not None and self._quality_worker.is_alive():
                return
        except Exception:
            # Fall through to restart.
            pass
        try:
            self._quality_worker = threading.Thread(target=self._quality_queue_worker, daemon=True)
            self._quality_worker.start()
            print("[warn] quality queue worker thread restarted")
        except Exception as exc:
            # Best-effort; if we fail to restart here, tasks will remain queued.
            print(f"[error] failed to restart quality queue worker: {exc}")

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

        # Load defaults.yaml + active config + user override dir as a single stack.
        try:
            merged, meta = load_config_stack(active, defaults_path=self._defaults_config_path, repo_root=self.repo_root)
            self.config = merged
            self._config_stack_meta = meta
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

        cleaned_params, rules_disable_global, rules_template_id, rules_override = extract_rules_inputs(params)
        bundle = self._prepare_task_bundle(
            task_id,
            video_path,
            work_dir,
            log_path,
            cleaned_params,
            preset,
            mode=mode,
            rules_disable_global=bool(rules_disable_global),
            rules_template_id=rules_template_id,
            rules_override=rules_override,
        )
        self._write_bundle_task_meta(bundle, created_at=time.time())
        self._dispatch_prepared_task(bundle.launch)
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
        resume_from = normalize_lite_resume_from(resume_from) or ""

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
        # v2: global rules are always enabled (no disable_global concept).
        base_rules_disable_global = False
        base_rules_template_id = meta.get("ruleset_template_id") if isinstance(meta.get("ruleset_template_id"), str) else None
        base_rules_override = meta.get("ruleset_override") if isinstance(meta.get("ruleset_override"), dict) else None
        merged_params = dict(base_params)
        merged_params.update(params_overrides or {})
        # Allow overriding ruleset on resume
        cleaned_params, rules_disable_global, rules_template_id, rules_override = extract_rules_inputs(merged_params)
        if rules_disable_global is None:
            rules_disable_global = base_rules_disable_global
        if rules_template_id is None:
            rules_template_id = base_rules_template_id
        if rules_override is None:
            rules_override = base_rules_override
        # Resuming from TTS/mux implies we want to actually generate audio/video even if the previous run was subtitle-only.
        if resume_from in {"tts", "mux"}:
            cleaned_params["skip_tts"] = False
        mode = str(mode or "lite")
        if mode == "lite":
            missing = collect_missing_lite_resume_artifacts(work_dir, resume_from)
            if missing:
                missing_desc = ", ".join(missing)
                raise ValueError(f"cannot resume lite task from {resume_from}: missing artifacts: {missing_desc}")

        if not video_path:
            raise ValueError("cannot resume: missing video path (task_meta.json not found or incomplete)")

        log_path = self._prepare_log_path(work_dir)
        bundle = self._prepare_task_bundle(
            task_id,
            str(video_path),
            work_dir,
            log_path,
            cleaned_params,
            base_preset,
            mode=mode,
            rules_disable_global=bool(rules_disable_global),
            rules_template_id=rules_template_id,
            rules_override=rules_override,
            resume_from=resume_from,
            replace_existing=True,
        )
        self._write_bundle_task_meta(bundle, resumed_at=time.time())
        self._dispatch_prepared_task(bundle.launch)
        return task_id

    def _enqueue_quality(self, launch: PreparedTaskLaunch) -> None:
        queue_item, status = build_quality_queue_state(
            launch,
            status_factory=TaskStatus,
        )
        with self.lock:
            self.tasks[launch.task_id] = status
            self._quality_queue.append(queue_item)
        self._write_task_state_snapshot(status)
        # Defensive: make sure the worker exists (it can die if an exception bubbles out).
        self._ensure_quality_worker()

    def _quality_queue_worker(self) -> None:
        while True:
            try:
                time.sleep(1.0)
                item = self._take_next_quality_queue_item()
                if item is None:
                    continue
                if not self._activate_queued_quality_task(item):
                    continue

                try:
                    self._spawn_task(
                        item.task_id,
                        item.video_path,
                        item.work_dir,
                        item.log_path,
                        item.cmd,
                        item.effective,
                        mode=item.mode,
                        replace_existing=item.replace_existing,
                    )
                except Exception as exc:
                    # Do NOT let an exception kill the worker thread; mark task failed and continue.
                    try:
                        item.log_path.parent.mkdir(parents=True, exist_ok=True)
                        with item.log_path.open("a", encoding="utf-8") as f:
                            f.write(f"[task {item.task_id}] failed to spawn process: {type(exc).__name__}: {exc}\n")
                    except Exception:
                        pass
                    with self.lock:
                        st = self.tasks.get(item.task_id)
                        if st:
                            handle_quality_spawn_failure(
                                status=st,
                                exc=exc,
                                write_snapshot=self._write_task_state_snapshot,
                                now=time.time(),
                            )
                    self._release_quality_running_slot()
                    continue

                # _spawn_task launches watcher thread; mark running slot free when watcher ends.
                # We hook it by polling task state and a few defensive conditions.
                while True:
                    time.sleep(2.0)
                    with self.lock:
                        st = self.tasks.get(item.task_id)
                        if not st or st.state != "running":
                            self._quality_running = False
                            break
                        keep_running, _ = reconcile_quality_spawned_task(
                            status=st,
                            write_snapshot=self._write_task_state_snapshot,
                            now=time.time(),
                        )
                        if not keep_running:
                            self._quality_running = False
                            break
            except Exception as exc:
                # Absolute last line of defense: never let the thread die.
                try:
                    print(f"[error] quality queue worker crashed: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
                self._release_quality_running_slot()
                time.sleep(1.0)
                continue

    def _normalize_task_params(self, params: Dict[str, Any], *, mode: str) -> Dict[str, Any]:
        """
        Normalize task params before merging with YAML config and building pipeline CLI.

        Goals:
        - Keep backend as the single source of truth for "quality UX toggles" mapping.
        - Ignore removed/legacy UX fields without breaking old clients or old saved batches.
        - Avoid leaking unknown UX-only keys into pipeline CLI.
        """
        p: Dict[str, Any] = dict(params or {})

        # Legacy UX-only toggles (quality wizard) - no longer user-facing.
        # Keep backward compatibility: accept but ignore/popup to prevent leaking to pipeline CLI.
        p.pop("ux_subtitle_readable", None)
        p.pop("ux_tts_natural", None)

        mode_name = str(mode or "").lower()
        if mode_name == "quality":
            p = apply_quality_fixed_policies(p)
        elif mode_name == "lite":
            p = apply_lite_fixed_policies(p)

        return p

    def _sweep_quality_runtime_state_locked(self) -> None:
        """
        Best-effort self-healing for sequential quality queue state.

        Why:
        - cancelled / paused tasks can occasionally leave a stale child process behind
        - watcher timing may leave `_quality_running` stuck even after a task effectively ended
        """
        self._quality_running = sweep_quality_runtime_state(
            tasks=list(self.tasks.values()),
            write_snapshot=self._write_task_state_snapshot,
            now=time.time(),
        )

    def _prepare_log_path(self, work_dir: Path) -> Path:
        return prepare_log_path(work_dir)

    def _write_task_meta(self, work_dir: Path, meta: Dict[str, Any]) -> None:
        write_json_best_effort(task_meta_path(work_dir), meta)

    def _read_task_meta(self, work_dir: Path) -> Optional[Dict[str, Any]]:
        return read_json_best_effort(task_meta_path(work_dir))

    def _task_state_path(self, work_dir: Path) -> Path:
        return task_state_path(work_dir)

    def _serialize_task_status(self, status: TaskStatus) -> Dict[str, Any]:
        return serialize_task_status(status, updated_at=time.time())

    def _write_task_state_snapshot(self, status: TaskStatus) -> None:
        write_json_best_effort(self._task_state_path(status.work_dir), self._serialize_task_status(status))

    def _read_task_state_snapshot(self, work_dir: Path) -> Optional[Dict[str, Any]]:
        return read_json_best_effort(self._task_state_path(work_dir))

    def _write_effective_config_snapshot(self, work_dir: Path, effective: Dict[str, Any]) -> Optional[str]:
        try:
            payload = json.dumps(effective, ensure_ascii=False, indent=2, sort_keys=True)
            (work_dir / "effective_config.json").write_text(payload, encoding="utf-8")
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()
        except Exception:
            return None

    def _apply_derived_rule_paths(self, effective: Dict[str, Any], derived: Dict[str, Path]) -> None:
        # Point pipeline to task-scoped derived files so runs are reproducible from task_meta.
        if derived.get("glossary_path"):
            effective["glossary"] = str(derived["glossary_path"])
        if derived.get("asr_dict_path"):
            effective["asr_normalize_dict"] = str(derived["asr_dict_path"])
        if derived.get("en_dict_path"):
            effective["en_replace_dict"] = str(derived["en_dict_path"])

    def _build_prepared_task_launch(
        self,
        task_id: str,
        video_path: str,
        work_dir: Path,
        log_path: Path,
        effective: Dict[str, Any],
        *,
        mode: str,
        resume_from: Optional[str] = None,
        replace_existing: bool = False,
    ) -> PreparedTaskLaunch:
        cmd = self._build_command(video_path, work_dir, effective, mode=mode, resume_from=resume_from)
        return build_prepared_task_launch(
            task_id=task_id,
            video_path=video_path,
            work_dir=work_dir,
            log_path=log_path,
            cmd=cmd,
            effective=effective,
            mode=mode,
            replace_existing=replace_existing,
        )

    def _prepare_task_bundle(
        self,
        task_id: str,
        video_path: str,
        work_dir: Path,
        log_path: Path,
        params: Dict[str, Any],
        preset: Optional[str],
        *,
        mode: str,
        rules_disable_global: bool,
        rules_template_id: Optional[str],
        rules_override: Optional[Dict[str, Any]],
        resume_from: Optional[str] = None,
        replace_existing: bool = False,
    ) -> PreparedTaskBundle:
        cleaned_params = self._normalize_task_params(params, mode=mode)
        effective = self._merge_config(cleaned_params, preset)
        effective_rules, derived = materialize_effective_rules(
            work_dir,
            rules_override=rules_override,
            ruleset_seed_path=self._ruleset_seed_path,
            ruleset_global_path=self._ruleset_global_path,
            ruleset_templates_dir=self._ruleset_templates_dir,
            disable_global=rules_disable_global,
            template_id=rules_template_id,
        )
        self._apply_derived_rule_paths(effective, derived)
        launch = self._build_prepared_task_launch(
            task_id,
            video_path,
            work_dir,
            log_path,
            effective,
            mode=mode,
            resume_from=resume_from,
            replace_existing=replace_existing,
        )
        return build_prepared_task_bundle(
            launch=launch,
            preset=preset,
            cleaned_params=cleaned_params,
            rules_disable_global=rules_disable_global,
            rules_template_id=rules_template_id,
            rules_override=rules_override,
            effective_rules=effective_rules,
            derived=derived,
            resume_from=resume_from,
        )

    def _write_bundle_task_meta(self, bundle: PreparedTaskBundle, *, created_at: Optional[float] = None, resumed_at: Optional[float] = None) -> None:
        effective_hash = self._write_effective_config_snapshot(bundle.launch.work_dir, bundle.launch.effective)
        meta = build_bundle_task_meta(
            bundle,
            config_stack_meta=self._config_stack_meta,
            effective_hash=effective_hash or "",
            created_at=created_at,
            resumed_at=resumed_at,
        )
        self._write_task_meta(bundle.launch.work_dir, meta)

    def _dispatch_prepared_task(self, launch: PreparedTaskLaunch) -> None:
        # Quality runs through the sequential queue; other modes spawn immediately.
        if launch.mode == "quality":
            self._enqueue_quality(launch)
            return
        self._spawn_task(
            launch.task_id,
            launch.video_path,
            launch.work_dir,
            launch.log_path,
            launch.cmd,
            launch.effective,
            mode=launch.mode,
            replace_existing=launch.replace_existing,
        )

    def _take_next_quality_queue_item(self) -> Optional[QualityQueueItem]:
        with self.lock:
            self._sweep_quality_runtime_state_locked()
            item, running = take_next_quality_queue_item(
                quality_queue=self._quality_queue,
                quality_running=self._quality_running,
            )
            self._quality_running = running
            return item

    def _release_quality_running_slot(self) -> None:
        with self.lock:
            self._quality_running = False

    def _activate_queued_quality_task(self, item: QualityQueueItem) -> bool:
        # If the task was cancelled while queued, skip it.
        with self.lock:
            st = self.tasks.get(item.task_id)
            ok = activate_queued_quality_task(
                st,
                stage_names=StageNames,
                now=time.time(),
            )
            if not ok:
                self._quality_running = False
                return False
            self._write_task_state_snapshot(st)
            return True

    def _build_env(self, effective: Dict) -> Dict[str, str]:
        env = os.environ.copy()
        repo_root = str(self.repo_root)
        backend_app = str(self.repo_root / "apps" / "backend")
        py_parts = [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep) if p]
        for p in (repo_root, backend_app):
            if p not in py_parts:
                py_parts.insert(0, p)
        env["PYTHONPATH"] = os.pathsep.join(py_parts)
        tts_home = self.config.get("paths", {}).get("tts_home")
        if tts_home:
            env["TTS_HOME"] = str(self._resolve_path(tts_home))

        # Ensure bundled shared libs (whisper.cpp / piper) can be resolved at runtime.
        ld_parts = [p for p in (env.get("LD_LIBRARY_PATH") or "").split(os.pathsep) if p]
        # v2+: prefer image-bundled /app/bin (and /app/bin/piper) for shared libs.
        for rel in ("bin", "bin/piper"):
            p = str(self._resolve_path(rel))
            if p not in ld_parts:
                ld_parts.insert(0, p)
        env["LD_LIBRARY_PATH"] = os.pathsep.join(ld_parts)

        # 全局离线：运行期禁止任何联网下载（HuggingFace/Transformers/Datasets 等）
        if effective.get("offline", False):
            hf_cache_rel = self.config.get("paths", {}).get("hf_cache") or "assets/models/common_cache_hf"
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

    def _build_spawn_options(self, effective: Dict) -> Tuple[Dict[str, str], Any, int, Any]:
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
        return env, stdin, creationflags, startupinfo

    def _launch_subprocess(self, cmd: List[str], effective: Dict) -> subprocess.Popen:
        env, stdin, creationflags, startupinfo = self._build_spawn_options(effective)
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            stdin=stdin,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )

    def _upsert_running_task_status(
        self,
        task_id: str,
        video_path: str,
        work_dir: Path,
        log_path: Path,
        proc: subprocess.Popen,
        *,
        mode: str,
        replace_existing: bool = False,
    ) -> None:
        # Reuse any existing TaskStatus (e.g. queued quality tasks) to preserve state/message.
        with self.lock:
            status = None if replace_existing else self.tasks.get(task_id)
        if not status:
            status = TaskStatus(id=task_id, video=video_path, work_dir=work_dir, log_path=log_path, mode=mode)
        status.video = video_path
        status.work_dir = work_dir
        status.log_path = log_path
        status.mode = mode
        status.proc = proc
        status.state = "running"
        if status.started_at is None:
            status.started_at = time.time()
        with self.lock:
            self.tasks[task_id] = status
        self._write_task_state_snapshot(status)

    def _start_task_watcher(self, task_id: str, proc: subprocess.Popen, cmd: List[str]) -> None:
        watcher = threading.Thread(target=self._watch_process, args=(task_id, proc, cmd), daemon=True)
        watcher.start()

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
        proc = self._launch_subprocess(cmd, effective)
        self._upsert_running_task_status(
            task_id,
            video_path,
            work_dir,
            log_path,
            proc,
            mode=mode,
            replace_existing=replace_existing,
        )
        self._start_task_watcher(task_id, proc, cmd)

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
            return self._resolve_path(paths.get("script_lite", "pipelines/lite_pipeline.py"))
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
        return build_lite_command(
            video_path=video_path,
            work_dir=work_dir,
            cfg=cfg,
            paths=paths,
            script=script,
            resume_from=resume_from,
            resolve_path=self._resolve_path,
            pick_executable=self._pick_executable,
            packaged_exe=self._is_packaged_exe(),
            sys_executable=sys.executable,
            env=dict(os.environ),
        )

    def _resolve_quality_runtime_python(self, cfg: Dict) -> Optional[Path]:
        paths = self.config.get("paths", {})
        raw = (
            cfg.get("quality_runtime_python")
            or paths.get("quality_runtime_python")
            or os.environ.get("YGF_QUALITY_RUNTIME_PYTHON")
            or ""
        )
        text = str(raw or "").strip()
        if not text:
            return None
        candidate = self._resolve_path(text)
        return candidate if candidate.exists() else None

    def _resolve_quality_llm_endpoints(self, cfg: Dict) -> tuple[str, str]:
        llm_endpoint = os.environ.get("YGF_LLM_ENDPOINT") or cfg.get("llm_endpoint") or "http://127.0.0.1:11434/v1"
        phrase_endpoint = (
            os.environ.get("YGF_PHRASE_LLM_ENDPOINT")
            or cfg.get("zh_phrase_llm_endpoint")
            or os.environ.get("YGF_LLM_ENDPOINT")
            or cfg.get("llm_endpoint")
            or ""
        )
        return str(llm_endpoint), str(phrase_endpoint)

    def _append_quality_mt_args(self, cmd: List[str], cfg: Dict) -> None:
        # Prompt-level MT/LLM quality knobs (general, safe defaults)
        if int(cfg.get("mt_context_window", 0) or 0) > 0:
            cmd += ["--mt-context-window", str(int(cfg.get("mt_context_window", 0) or 0))]
        # MT style + subtitle concision controls (best-practice prompt tuning; fallback-safe)
        if str(cfg.get("mt_style", "") or "").strip():
            cmd += ["--mt-style", str(cfg.get("mt_style", "")).strip()]
        if cfg.get("mt_max_words_per_line") is not None:
            cmd += ["--mt-max-words-per-line", str(int(cfg.get("mt_max_words_per_line", 0) or 0))]
        # Two-stage prompt (best practice): short prompt by default, long prompt only for problematic lines.
        if str(cfg.get("mt_prompt_mode", "") or "").strip():
            cmd += ["--mt-prompt-mode", str(cfg.get("mt_prompt_mode")).strip()]
        if str(cfg.get("mt_prompt_profile", "") or "").strip():
            cmd += ["--mt-prompt-profile", str(cfg.get("mt_prompt_profile")).strip()]
        if cfg.get("mt_two_pass_disable"):
            cmd.append("--mt-two-pass-disable")
        if str(cfg.get("mt_reasoning_effort", "") or "").strip():
            cmd += ["--mt-reasoning-effort", str(cfg.get("mt_reasoning_effort")).strip()]
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
        if cfg.get("mt_request_timeout_s") is not None:
            cmd += ["--mt-request-timeout-s", str(int(cfg.get("mt_request_timeout_s", 120)))]
        if cfg.get("mt_request_retries") is not None:
            cmd += ["--mt-request-retries", str(int(cfg.get("mt_request_retries", 2)))]
        if cfg.get("mt_long_zh_chars") is not None:
            cmd += ["--mt-long-zh-chars", str(int(cfg.get("mt_long_zh_chars", 60)))]
        if cfg.get("mt_long_en_words") is not None:
            cmd += ["--mt-long-en-words", str(int(cfg.get("mt_long_en_words", 22)))]
        if cfg.get("mt_long_target_words") is not None:
            cmd += ["--mt-long-target-words", str(int(cfg.get("mt_long_target_words", 18)))]
        # High-risk line self-check (default-on in pipeline; pass caps/disable explicitly for product control).
        if cfg.get("llm_selfcheck_enable"):
            cmd.append("--llm-selfcheck-enable")
        if cfg.get("llm_selfcheck_disable"):
            cmd.append("--llm-selfcheck-disable")
        if cfg.get("llm_selfcheck_max_lines") is not None:
            cmd += ["--llm-selfcheck-max-lines", str(int(cfg.get("llm_selfcheck_max_lines", 10) or 10))]
        if cfg.get("llm_selfcheck_max_ratio") is not None:
            cmd += ["--llm-selfcheck-max-ratio", str(float(cfg.get("llm_selfcheck_max_ratio", 0.25) or 0.25))]

    def _append_quality_review_runtime_args(self, cmd: List[str], cfg: Dict, resume_from: Optional[str] = None) -> None:
        # ----------------------------
        # Review gate (zh_polish)
        # ----------------------------
        if cfg.get("zh_phrase_enable"):
            cmd.append("--zh-phrase-enable")
        if cfg.get("review_enabled"):
            cmd.append("--review-enabled")
        stop_after = str(cfg.get("stop_after") or "").strip()
        if stop_after in {"zh_polish", "mt", "tts", "mux"}:
            cmd += ["--stop-after", stop_after]
        if cfg.get("zh_post_polish_enable"):
            cmd.append("--zh-post-polish-enable")
        if cfg.get("zh_post_polish_max_lines") is not None:
            cmd += ["--zh-post-polish-max-lines", str(int(cfg.get("zh_post_polish_max_lines", 6) or 6))]
        if cfg.get("zh_opt_request_timeout_s") is not None:
            cmd += ["--zh-opt-request-timeout-s", str(int(cfg.get("zh_opt_request_timeout_s", 90)))]
        if cfg.get("zh_opt_request_retries") is not None:
            cmd += ["--zh-opt-request-retries", str(int(cfg.get("zh_opt_request_retries", 1)))]
        if cfg.get("zh_phrase_max_spans") is not None:
            cmd += ["--zh-phrase-max-spans", str(int(cfg.get("zh_phrase_max_spans", 3) or 3))]
        if cfg.get("zh_phrase_max_total") is not None:
            cmd += ["--zh-phrase-max-total", str(int(cfg.get("zh_phrase_max_total", 30) or 30))]
        if cfg.get("zh_phrase_chunk_lines") is not None:
            cmd += ["--zh-phrase-chunk-lines", str(int(cfg.get("zh_phrase_chunk_lines", 20) or 20))]
        if cfg.get("zh_phrase_candidate_max_lines") is not None:
            cmd += ["--zh-phrase-candidate-max-lines", str(int(cfg.get("zh_phrase_candidate_max_lines", 0) or 0))]
        if cfg.get("zh_phrase_force_one_per_line"):
            cmd.append("--zh-phrase-force-one-per-line")
        if not cfg.get("zh_phrase_second_pass_enable", True):
            cmd.append("--no-zh-phrase-second-pass")
        if cfg.get("zh_phrase_idiom_enable"):
            cmd.append("--zh-phrase-idiom-enable")
        if str(cfg.get("zh_phrase_idiom_path") or "").strip():
            cmd += ["--zh-phrase-idiom-path", str(cfg.get("zh_phrase_idiom_path")).strip()]
        if str(cfg.get("zh_phrase_same_pinyin_path") or "").strip():
            cmd += ["--zh-phrase-same-pinyin-path", str(cfg.get("zh_phrase_same_pinyin_path")).strip()]
        if str(cfg.get("zh_phrase_same_stroke_path") or "").strip():
            cmd += ["--zh-phrase-same-stroke-path", str(cfg.get("zh_phrase_same_stroke_path")).strip()]
        if str(cfg.get("zh_repair_lexicon_path") or "").strip():
            cmd += ["--zh-repair-lexicon-path", str(cfg.get("zh_repair_lexicon_path")).strip()]
        if str(cfg.get("zh_repair_proper_nouns_path") or "").strip():
            cmd += ["--zh-repair-proper-nouns-path", str(cfg.get("zh_repair_proper_nouns_path")).strip()]
        if str(cfg.get("asr_project_confusions_path") or "").strip():
            cmd += ["--asr-project-confusions-path", str(cfg.get("asr_project_confusions_path")).strip()]
        if cfg.get("zh_gate_min_high_risk") is not None:
            cmd += ["--zh-gate-min-high-risk", str(int(cfg.get("zh_gate_min_high_risk", 1) or 0))]
        if cfg.get("zh_gate_min_total_suspects") is not None:
            cmd += ["--zh-gate-min-total-suspects", str(int(cfg.get("zh_gate_min_total_suspects", 6) or 0))]
        if cfg.get("zh_gate_on_phrase_error"):
            cmd.append("--zh-gate-on-phrase-error")
        # Product decision: QE/TRA and other heavy evaluators are not part of the default user-facing quality workflow.
        # Keep backward-compat config keys readable, but do NOT pass them to pipeline here (compat period).
        # Audio denoise (safe fallback in scripts: arnndn w/o model -> anlmdn)
        if cfg.get("denoise"):
            cmd.append("--denoise")
            if cfg.get("denoise_model"):
                cmd += ["--denoise-model", str(self._resolve_path(cfg["denoise_model"]))]
        # ASR alignment (WhisperX wav2vec2) - optional and expensive
        if cfg.get("asr_align_enable"):
            cmd.append("--asr-align-enable")
        if resume_from:
            cmd += ["--resume-from", resume_from]
        if cfg.get("diarization"):
            cmd.append("--diarization")
        if cfg.get("llm_api_key"):
            cmd += ["--llm-api-key", cfg["llm_api_key"]]

    def _append_quality_postprocess_args(self, cmd: List[str], cfg: Dict) -> None:
        # ----------------------------
        # P0: subtitle post-process
        # ----------------------------
        if cfg.get("subtitle_postprocess_enable"):
            cmd.append("--subtitle-postprocess-enable")
        if cfg.get("subtitle_wrap_enable"):
            cmd.append("--subtitle-wrap-enable")
            cmd += ["--subtitle-wrap-max-lines", str(cfg.get("subtitle_wrap_max_lines", 2))]
        # P0: display subtitles (may change line count)
        if cfg.get("display_srt_enable"):
            cmd.append("--display-srt-enable")
        if cfg.get("display_use_for_embed"):
            cmd.append("--display-use-for-embed")
        if cfg.get("display_max_chars_per_line") is not None:
            cmd += ["--display-max-chars-per-line", str(int(cfg.get("display_max_chars_per_line", 46) or 46))]
        if cfg.get("display_max_lines") is not None:
            cmd += ["--display-max-lines", str(int(cfg.get("display_max_lines", 2) or 2))]
        if cfg.get("display_merge_enable"):
            cmd.append("--display-merge-enable")
            cmd += ["--display-merge-max-gap-s", str(float(cfg.get("display_merge_max_gap_s", 0.35) or 0.35))]
            cmd += ["--display-merge-max-chars", str(int(cfg.get("display_merge_max_chars", 96) or 96))]
        if cfg.get("display_split_enable"):
            cmd.append("--display-split-enable")
            cmd += ["--display-split-max-chars", str(int(cfg.get("display_split_max_chars", 96) or 96))]
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
        # P1: TTS fitting (word trimming) is deprecated in quality mode:
        # it can drop critical facts and makes subtitles inconsistent with dubbing.
        # Keep the pipeline flag for backward compatibility, but do NOT enable it here.
        # P1-2: per-segment TTS planning (speed cap + pause audit)
        if cfg.get("tts_plan_enable"):
            cmd.append("--tts-plan-enable")
            cmd += ["--tts-plan-safety-margin", str(float(cfg.get("tts_plan_safety_margin", 0.02) or 0.02))]
            cmd += ["--tts-plan-min-cap", str(float(cfg.get("tts_plan_min_cap", 1.0) or 1.0))]

    def _build_cmd_quality(self, video_path: str, work_dir: Path, cfg: Dict, resume_from: Optional[str] = None) -> List[str]:
        script = self._select_script("quality", cfg)
        paths = self.config.get("paths", {})
        gates = self.config.get("quality_gates", {}) or {}
        quality_runtime_python = self._resolve_quality_runtime_python(cfg)
        whisperx_model = cfg.get("whisperx_model", "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
        asr_engine = str(cfg.get("asr_engine", "faster-whisper") or "faster-whisper").strip().lower() or "faster-whisper"
        asr_experiment_profile = str(cfg.get("asr_experiment_profile", "") or "").strip().lower()
        allow_gpu = bool(cfg.get("allow_gpu", True))
        whisperx_device = "auto" if allow_gpu else "cpu"
        # Prefer paths.whisperx_model_dir (may be overridden by app via YGF_MODELS_ROOT -> user_data/models/quality_asr_whisperx).
        whisperx_dir = self._resolve_path(
            paths.get("whisperx_model_dir") or cfg.get("whisperx_model_dir") or "assets/models/quality_asr_whisperx"
        )
        sensevoice_dir = self._resolve_path(
            paths.get("sensevoice_model_dir") or cfg.get("sensevoice_model_dir") or str(whisperx_dir.parent / "common_cache_hf")
        )
        sensevoice_model = str(cfg.get("sensevoice_model", "") or "FunAudioLLM/SenseVoiceSmall").strip() or "FunAudioLLM/SenseVoiceSmall"
        if asr_experiment_profile in {"large-v3-turbo", "large_v3_turbo", "faster-whisper-large-v3-turbo"}:
            asr_engine = "faster-whisper"
            whisperx_model = str(
                cfg.get("asr_experiment_whisperx_model", "") or "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
            ).strip()
        elif asr_experiment_profile in {"sensevoice-small", "sensevoice_small", "sensevoice"}:
            asr_engine = "sensevoice"
            sensevoice_model = str(cfg.get("sensevoice_model", "") or "FunAudioLLM/SenseVoiceSmall").strip() or "FunAudioLLM/SenseVoiceSmall"
        # Product decision: coqui-only (do not expose / run piper via backend API).
        coqui_model = cfg.get("coqui_model") or "tts_models/multilingual/multi-dataset/xtts_v2"
        coqui_device = str(cfg.get("coqui_device", "auto") or "auto").strip().lower() or "auto"
        if not allow_gpu:
            coqui_device = "cpu"
        elif coqui_device not in {"auto", "cpu", "cuda"}:
            coqui_device = "auto"

        llm_endpoint, phrase_endpoint = self._resolve_quality_llm_endpoints(cfg)

        runner_python = str(quality_runtime_python) if quality_runtime_python else sys.executable
        cmd: List[str] = [
            runner_python,
            str(script),
            "--video",
            str(video_path),
            "--output-dir",
            str(work_dir),
            "--glossary",
            str(self._resolve_path(cfg.get("glossary") or paths.get("glossary", "assets/glossary/glossary.json"))),
            "--asr-engine",
            asr_engine,
            "--whisperx-model",
            whisperx_model,
            "--whisperx-model-dir",
            str(whisperx_dir),
            "--whisperx-device",
            whisperx_device,
            "--sensevoice-model",
            sensevoice_model,
            "--sensevoice-model-dir",
            str(sensevoice_dir),
            # LLM endpoints:
            # - In Docker, the correct endpoint is usually a service DNS name (e.g. http://ollama:11434/v1),
            #   and 127.0.0.1 inside the container will NOT reach host Ollama.
            # - In packaged/local runs, env defaults to localhost and works as expected.
            "--llm-endpoint",
            llm_endpoint,
            "--zh-phrase-llm-endpoint",
            phrase_endpoint,
            "--llm-model",
            cfg.get("llm_model", "qwen3.5:9b"),
            "--zh-phrase-llm-model",
            cfg.get("zh_phrase_llm_model", "") or cfg.get("llm_model", "qwen3.5:9b"),
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
            str(cfg.get("tts_speed_max", 1.12)),
            # P0: use the same thresholds as quality gates for post-process (no hidden rules)
            "--subtitle-max-cps",
            str(gates.get("max_cps", 20.0)),
            "--subtitle-max-chars-per-line",
            str(gates.get("max_chars_per_line", 80)),
        ]
        if cfg.get("en_replace_dict"):
            try:
                cmd += ["--en-replace-dict", str(self._resolve_path(cfg["en_replace_dict"]))]
            except Exception:
                pass
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
        self._append_quality_mt_args(cmd, cfg)
        self._append_quality_review_runtime_args(cmd, cfg, resume_from=resume_from)

        # TTS (coqui-only)
        cmd += ["--tts-backend", "coqui"]
        cmd += [
            "--coqui-model",
            coqui_model,
            "--coqui-device",
            coqui_device,
        ]
        # Allow subtitle-only runs in quality mode too (useful for fast review/debug).
        if cfg.get("skip_tts"):
            cmd.append("--skip-tts")
        # ASR normalization (low-risk). Pass through only when enabled.
        if cfg.get("asr_normalize_enable"):
            cmd.append("--asr-normalize-enable")
            if cfg.get("asr_normalize_dict"):
                cmd += ["--asr-normalize-dict", str(self._resolve_path(cfg["asr_normalize_dict"]))]
        if cfg.get("en_replace_dict"):
            try:
                cmd += ["--en-replace-dict", str(self._resolve_path(cfg["en_replace_dict"]))]
            except Exception:
                pass

        self._append_quality_postprocess_args(cmd, cfg)
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
            str(cfg.get("tts_speed_max", 1.12)),
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

    def _finalize_task_status_from_exit(self, task_id: str, proc: subprocess.Popen, log_path: Path) -> Optional[TaskStatus]:
        with self.lock:
            st = self.tasks.get(task_id)
            if not st:
                return None
            st = apply_exit_to_task_status(
                st,
                return_code=int(proc.returncode or 0),
                log_path=log_path,
                log_has_hw_limit=_log_has_hw_limit,
            )
            self._write_task_state_snapshot(st)
            return st

    def _maybe_write_quality_report(self, task_id: str, status: TaskStatus) -> None:
        write_quality_report_best_effort(
            task_id=task_id,
            status=status,
            cfg=self.config,
            generate_quality_report=generate_quality_report,
            write_quality_report=write_quality_report,
        )

    def _watch_process(self, task_id: str, proc: subprocess.Popen, cmd: List[str]) -> None:
        status = self.tasks[task_id]
        log_path = status.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with log_path.open("w", encoding="utf-8") as log_file:
            write_task_log_header(log_file, task_id, status, cmd)
            if proc.stdout is None:
                proc.wait()
                status.ended_at = time.time()
                self._write_task_state_snapshot(status)
                write_task_log_footer(log_file, task_id, status)
                return
            for line in proc.stdout:
                # Prefix every pipeline log line with task id for easier debugging in UI.
                try:
                    log_file.write(f"[task {task_id}] {line}")
                except Exception:
                    log_file.write(line)
                log_file.flush()
                # Keep progress parsing on the raw, un-prefixed line.
                self._update_progress(task_id, line)
            proc.wait()
            finalized = self._finalize_task_status_from_exit(task_id, proc, log_path)
            if finalized is None:
                return
            write_task_log_footer(log_file, task_id, finalized)

        self._maybe_write_quality_report(task_id, finalized)

    def _update_progress(self, task_id: str, line: str) -> None:
        # Match the outer stage marker at line start, e.g.:
        # [2/8] ... or "  [2/8][1/4] ..."
        match = re.search(r"^\s*\[(\d+)/(\d+)\]", line)
        with self.lock:
            st = self.tasks.get(task_id)
            if not st:
                return
            if match:
                num = int(match.group(1))
                den = int(match.group(2)) if match.group(2) else 0
                st.stage = num
                if den > 0:
                    st.progress = round(num / den * 100, 1)
                else:
                    st.progress = round(num / 100 * 100, 1)
                st.message = StageNames.get(num, st.message or "")
            else:
                st.message = line.strip() or st.message
            self._write_task_state_snapshot(st)

    def get_status(self, task_id: str) -> Optional[Dict]:
        # 1) In-memory fast path
        with self.lock:
            st = self.tasks.get(task_id)
        if st:
            meta = self._read_task_meta(st.work_dir) or {}
            self._write_task_state_snapshot(st)
            return build_task_status_response(st, meta, StageNames)

        # 2) Disk recovery path (backend restart clears tasks dict but outputs remain)
        wd = self.resolve_work_dir(task_id)
        if not wd:
            return None
        meta = self._read_task_meta(wd) or {}
        state_snapshot = self._read_task_state_snapshot(wd) or {}
        return recover_task_status_from_disk(
            task_id=task_id,
            work_dir=wd,
            meta=meta,
            state_snapshot=state_snapshot,
            stage_names=StageNames,
        )

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
        proc = None
        with self.lock:
            st = self.tasks.get(task_id)
            if not st:
                return False

            # Support cancelling queued quality tasks to avoid queue pollution
            # (useful for automation/preview flows and user cancellations before work starts).
            if st.state == "queued":
                # Remove from quality queue if present
                try:
                    self._quality_queue = [it for it in (self._quality_queue or []) if (it and it.task_id != task_id)]
                except Exception:
                    pass
                st.state = "cancelled"
                st.message = "Cancelled"
                st.ended_at = time.time()
                self._write_task_state_snapshot(st)
                return True

            if st.state not in {"running"}:
                return False
            st.state = "cancelled"
            st.message = "Cancelled"
            st.ended_at = time.time()
            proc = st.proc
            self._write_task_state_snapshot(st)

        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self.lock:
            self._sweep_quality_runtime_state_locked()
        return True

    def stop_process(self, task_id: str) -> None:
        # Placeholder for future pid tracking; currently relies on external orchestration
        with self.lock:
            st = self.tasks.get(task_id)
            if st:
                st.state = "cancelled"
                self._write_task_state_snapshot(st)

    def read_log_chunk(self, task_id: str, offset: int = 0, limit: int = 8_000) -> Tuple[str, int]:
        """
        Read a slice of run.log and return (content, next_offset).

        Offset semantics:
        - offset >= 0: read from that byte index forward (classic "append" mode).
        - offset < 0:  treat as "tail" (read last N bytes). In this case next_offset is the
          full length of the log, so callers can switch back to append mode safely.
        """
        with self.lock:
            st = self.tasks.get(task_id)
        return read_log_chunk_for_task(
            task_id=task_id,
            task_status=st,
            work_dir=self.resolve_work_dir(task_id),
            offset=offset,
            limit=limit,
        )

    def read_log(self, task_id: str, offset: int = 0, limit: int = 8_000) -> str:
        # Backward compatible: return only content.
        return self.read_log_chunk(task_id, offset=offset, limit=limit)[0]

    def list_artifacts(self, task_id: str) -> List[Dict]:
        with self.lock:
            st = self.tasks.get(task_id)
        wd = st.work_dir if st else self.resolve_work_dir(task_id)
        if not wd:
            return []
        return list_existing_lite_artifacts(wd)

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

        to_remove = collect_lite_cleanup_targets(
            include_resume=include_resume,
            include_review=include_review,
            include_diagnostics=include_diagnostics,
        )

        removed: List[str] = []
        missing: List[str] = []
        errors: List[str] = []
        for name in to_remove:
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



