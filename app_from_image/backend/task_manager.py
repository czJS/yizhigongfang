import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


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
    state: str = "running"  # running | completed | failed | cancelled
    stage: Optional[int] = None
    progress: float = 0.0
    message: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    work_dir: Path = field(default_factory=Path)
    log_path: Path = field(default_factory=Path)
    return_code: Optional[int] = None
    proc: Optional[subprocess.Popen] = None


class TaskManager:
    def __init__(self, config: Dict):
        self.config = config
        self.tasks: Dict[str, TaskStatus] = {}
        self.lock = threading.Lock()
        self.repo_root = Path(__file__).resolve().parents[1]
        paths = config.get("paths", {})
        outputs_root = Path(paths.get("outputs_root", "outputs"))
        self.outputs_root = outputs_root if outputs_root.is_absolute() else self.repo_root / outputs_root
        self.outputs_root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, rel_or_abs) -> Path:
        # Py3.9 兼容：不使用 union 运算符
        p = Path(rel_or_abs)
        return p if p.is_absolute() else self.repo_root / p

    def start_task(self, video_path: str, params: Dict, preset: Optional[str] = None) -> str:
        task_id = uuid.uuid4().hex[:12]
        work_dir = self.outputs_root / task_id
        work_dir.mkdir(parents=True, exist_ok=True)
        log_path = work_dir / "run.log"

        effective = self._merge_config(params, preset)
        cmd = self._build_command(video_path, work_dir, effective)
        env = os.environ.copy()
        tts_home = self.config.get("paths", {}).get("tts_home")
        if tts_home:
            env["TTS_HOME"] = str(self._resolve_path(tts_home))
        hf_endpoint = effective.get("hf_endpoint")
        if hf_endpoint:
            env["HF_ENDPOINT"] = hf_endpoint

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

        status = TaskStatus(id=task_id, video=video_path, work_dir=work_dir, log_path=log_path)
        status.proc = proc
        with self.lock:
            self.tasks[task_id] = status

        t = threading.Thread(target=self._watch_process, args=(task_id, proc), daemon=True)
        t.start()
        return task_id

    def _merge_config(self, params: Dict, preset: Optional[str]) -> Dict:
        defaults = self.config.get("defaults", {})
        preset_cfg = self.config.get("presets", {}).get(preset or "", {})
        merged = {**defaults, **preset_cfg}
        merged.update({k: v for k, v in params.items() if v is not None})
        return merged

    def _build_command(self, video_path: str, work_dir: Path, cfg: Dict) -> List[str]:
        paths = self.config.get("paths", {})
        script = self._resolve_path(paths.get("script", "pipelines/asr_translate_tts.py"))
        whisper_bin = self._resolve_path(cfg.get("whispercpp_bin") or paths.get("whispercpp_bin", "bin/main"))
        asr_model = self._resolve_path(cfg.get("asr_model") or cfg.get("whispercpp_model") or paths.get("whispercpp_model", "assets/models/ggml-small-q5_0.bin"))
        piper_bin = cfg.get("piper_bin") or "piper"
        piper_model = self._resolve_path(cfg.get("piper_model") or "assets/models/en_US-amy-low.onnx")
        mt_model = cfg.get("mt_model", "Helsinki-NLP/opus-mt-zh-en")
        coqui_model = cfg.get("coqui_model") or cfg.get("tts_model") or "tts_models/en/ljspeech/tacotron2-DDC"

        cmd: List[str] = [
            sys.executable,
            str(script),
            "--video",
            str(video_path),
            "--output-dir",
            str(work_dir),
            "--whispercpp-bin",
            str(whisper_bin),
            "--whispercpp-model",
            str(asr_model),
            "--mt-model",
            mt_model,
            "--mt-device",
            cfg.get("mt_device", "auto"),
            "--sample-rate",
            str(cfg.get("sample_rate", 16000)),
        ]
        if cfg.get("whispercpp_threads"):
            cmd += ["--whispercpp-threads", str(cfg["whispercpp_threads"])]
        if cfg.get("vad_enable"):
            cmd.append("--vad-enable")
            if cfg.get("vad_threshold"):
                cmd += ["--vad-thold", str(cfg["vad_threshold"])]
            if cfg.get("vad_min_dur"):
                cmd += ["--vad-min-dur", str(cfg["vad_min_dur"])]
        if cfg.get("denoise"):
            cmd.append("--denoise")
            if cfg.get("denoise_model"):
                cmd += ["--denoise-model", str(self._resolve_path(cfg["denoise_model"]))]
        if cfg.get("bilingual_srt"):
            cmd.append("--bilingual-srt")

        tts_backend = cfg.get("tts_backend", "coqui")
        cmd += ["--tts-backend", tts_backend]
        if tts_backend == "piper":
            cmd += ["--piper-bin", piper_bin, "--piper-model", str(piper_model)]
        else:
            cmd += [
                "--coqui-model",
                coqui_model,
                "--coqui-device",
                cfg.get("tts_device", cfg.get("coqui_device", "auto")),
            ]
            if cfg.get("coqui_speaker"):
                cmd += ["--coqui-speaker", cfg["coqui_speaker"]]
            if cfg.get("coqui_language"):
                cmd += ["--coqui-language", cfg["coqui_language"]]

        if cfg.get("en_polish_model"):
            cmd += ["--en-polish-model", cfg["en_polish_model"], "--en-polish-device", cfg.get("en_polish_device", "auto")]
        if cfg.get("lt_enable"):
            cmd.append("--lt-enable")
        if cfg.get("replacements"):
            cmd += ["--replacements", str(self._resolve_path(cfg["replacements"]))]
        if cfg.get("skip_tts"):
            cmd.append("--skip-tts")
        return cmd

    def _watch_process(self, task_id: str, proc: subprocess.Popen) -> None:
        status = self.tasks[task_id]
        log_path = status.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log_file:
            if proc.stdout is None:
                proc.wait()
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
                    st.state = "completed" if proc.returncode == 0 else "failed"
                    st.message = "Done" if proc.returncode == 0 else f"Exited with {proc.returncode}"

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
        with self.lock:
            st = self.tasks.get(task_id)
            if not st:
                return None
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
            }

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
        if not st or not st.log_path.exists():
            return ""
        data = st.log_path.read_text(encoding="utf-8", errors="ignore")
        return data[offset : offset + limit]

    def list_artifacts(self, task_id: str) -> List[Dict]:
        with self.lock:
            st = self.tasks.get(task_id)
        if not st:
            return []
        files = [
            "asr_whispercpp.json",
            "audio.json",
            "audio.wav",
            "chs.srt",
            "eng.srt",
            "bilingual.srt",
            "tts_full.wav",
            "output_en.mp4",
            "output_en_sub.mp4",
        ]
        found = []
        for name in files:
            p = st.work_dir / name
            if p.exists():
                found.append({"name": name, "path": str(p), "size": p.stat().st_size})
        return found



