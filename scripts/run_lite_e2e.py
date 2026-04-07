#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


BASE_REQUIRED_LITE_ARTIFACTS = ("audio.json", "chs.srt", "eng.srt")
FULL_REQUIRED_LITE_ARTIFACTS = ("tts_plan.json", "tts_full.wav", "output_en.mp4", "output_en_sub.mp4")
TIMEOUT_GRACE_FULL_MILESTONE_ARTIFACTS = ("tts_plan.json", "tts_full.wav", "output_en.mp4")
TIMEOUT_COMPLETION_GRACE_S = 15
LITE_DYNAMIC_TIMEOUT_RATIO = 4.3
LITE_DYNAMIC_TIMEOUT_BUFFER_S = 15
LITE_DEFAULT_TTS_PLAN_SAFETY_MARGIN = 0.02
LITE_DEFAULT_SUBTITLE_MAX_CPS = 20.0
LITE_DEFAULT_SUBTITLE_MAX_CHARS_PER_LINE = 42
LITE_DEFAULT_SUBTITLE_MAX_LINES = 2


def _load_yaml(p: Path) -> Dict[str, Any]:
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _resolve_path(repo_root: Path, p: str) -> str:
    s = str(p or "").strip()
    if not s:
        return s
    pp = Path(s)
    if pp.is_absolute():
        return str(pp)
    return str((repo_root / pp).resolve())


def _resolve_runtime_python_path(repo_root: Path, raw: str) -> Optional[Path]:
    text = str(raw or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate if candidate.exists() else None


def _pick_existing(*cands: str) -> str:
    for s in cands:
        if not s:
            continue
        try:
            if Path(s).exists():
                return s
        except Exception:
            continue
    return str(cands[0]) if cands else ""


def _hydrate_effective_runtime_paths(effective: Dict[str, Any], paths: Dict[str, Any]) -> Dict[str, Any]:
    hydrated = dict(effective or {})
    if bool(hydrated.get("vad_enable")) and not str(hydrated.get("vad_model") or "").strip():
        vad_model = paths.get("vad_model")
        if str(vad_model or "").strip():
            hydrated["vad_model"] = str(vad_model)
    return hydrated


def _dash(s: str) -> str:
    return str(s).replace("_", "-")


def _get_quality_gate(cfg: Dict[str, Any], key: str, default: Any) -> Any:
    gates = (cfg or {}).get("quality_gates")
    if not isinstance(gates, dict):
        return default
    return gates.get(key, default)


def _probe_duration_seconds(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return None
        raw = str(proc.stdout or "").strip()
        return float(raw) if raw else None
    except Exception:
        return None


def _compute_effective_timeout_s(requested_timeout_s: int, *, source_video: Path) -> tuple[int, Optional[float], str]:
    requested = max(int(requested_timeout_s or 0), 0)
    if requested <= 0:
        return 0, None, "disabled"
    source_duration_s = _probe_duration_seconds(source_video)
    if source_duration_s is None or source_duration_s <= 0:
        return requested, source_duration_s, "fixed"
    dynamic_timeout = int(math.ceil(float(source_duration_s) * float(LITE_DYNAMIC_TIMEOUT_RATIO) + float(LITE_DYNAMIC_TIMEOUT_BUFFER_S)))
    effective_timeout = max(requested, dynamic_timeout)
    mode = "dynamic" if effective_timeout > requested else "fixed"
    return effective_timeout, float(source_duration_s), mode


def _align_lite_subtitle_policy(effective: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    aligned = dict(effective or {})
    base_chars = int(aligned.get("subtitle_max_chars_per_line") or LITE_DEFAULT_SUBTITLE_MAX_CHARS_PER_LINE)
    base_cps = float(aligned.get("subtitle_max_cps") or LITE_DEFAULT_SUBTITLE_MAX_CPS)
    try:
        gate_chars = int(_get_quality_gate(cfg, "max_chars_per_line", base_chars) or base_chars)
    except Exception:
        gate_chars = base_chars
    try:
        gate_cps = float(_get_quality_gate(cfg, "max_cps", base_cps) or base_cps)
    except Exception:
        gate_cps = base_cps
    aligned["subtitle_max_chars_per_line"] = max(16, min(base_chars, gate_chars))
    aligned["subtitle_max_cps"] = max(8.0, min(base_cps, gate_cps))
    aligned["subtitle_wrap_max_lines"] = int(aligned.get("subtitle_wrap_max_lines") or LITE_DEFAULT_SUBTITLE_MAX_LINES)
    aligned["tts_plan_safety_margin"] = float(aligned.get("tts_plan_safety_margin") or LITE_DEFAULT_TTS_PLAN_SAFETY_MARGIN)
    return aligned


def _expected_timeout_grace_artifacts(effective: Dict[str, Any]) -> List[str]:
    required = list(BASE_REQUIRED_LITE_ARTIFACTS)
    if not bool(effective.get("skip_tts", False)):
        required.extend(TIMEOUT_GRACE_FULL_MILESTONE_ARTIFACTS)
    return required


def _has_timeout_grace_artifacts(out_dir: Path, effective: Dict[str, Any]) -> bool:
    return all((out_dir / name).exists() for name in _expected_timeout_grace_artifacts(effective))


def _to_cli_args(effective: Dict[str, Any], repo_root: Path) -> List[str]:
    """
    Render lite-effective config to scripts/asr_translate_tts.py CLI args.
    Note: that script mostly uses store_true flags for booleans, so we build from the merged config
    instead of emitting --no-xxx flags.
    """
    args: List[str] = []

    def add_bool(k: str, flag: str):
        if bool(effective.get(k, False)):
            args.append(flag)

    def add_val(k: str, flag: str):
        v = effective.get(k, None)
        if v is None:
            return
        if isinstance(v, bool):
            return
        if isinstance(v, list):
            args.extend([flag, ",".join(str(x) for x in v if str(x).strip())])
            return
        args.extend([flag, str(v)])

    # --- ASR / Audio
    add_val("sample_rate", "--sample-rate")
    add_bool("denoise", "--denoise")
    if effective.get("denoise_model"):
        args.extend(["--denoise-model", _resolve_path(repo_root, str(effective["denoise_model"]))])

    add_bool("vad_enable", "--vad-enable")
    if effective.get("vad_model"):
        args.extend(["--vad-model", _resolve_path(repo_root, str(effective["vad_model"]))])
    # whisper.cpp flag names
    add_val("vad_threshold", "--vad-thold")
    add_val("vad_min_dur", "--vad-min-dur")
    add_val("whispercpp_threads", "--whispercpp-threads")
    add_val("whispercpp_beam_size", "--whispercpp-beam-size")

    # --- ASR normalize
    add_bool("asr_normalize_enable", "--asr-normalize-enable")
    if effective.get("asr_normalize_dict"):
        args.extend(["--asr-normalize-dict", _resolve_path(repo_root, str(effective["asr_normalize_dict"]))])
    add_bool("asr_glossary_fix_enable", "--asr-glossary-fix-enable")
    add_bool("asr_low_cost_clean_enable", "--asr-low-cost-clean-enable")
    add_bool("asr_badline_detect_enable", "--asr-badline-detect-enable")
    if effective.get("asr_same_pinyin_path"):
        args.extend(["--asr-same-pinyin-path", _resolve_path(repo_root, str(effective["asr_same_pinyin_path"]))])
    if effective.get("asr_same_stroke_path"):
        args.extend(["--asr-same-stroke-path", _resolve_path(repo_root, str(effective["asr_same_stroke_path"]))])
    if effective.get("asr_project_confusions_path"):
        args.extend(["--asr-project-confusions-path", _resolve_path(repo_root, str(effective["asr_project_confusions_path"]))])
    if effective.get("asr_lexicon_path"):
        args.extend(["--asr-lexicon-path", _resolve_path(repo_root, str(effective["asr_lexicon_path"]))])
    if effective.get("asr_proper_nouns_path"):
        args.extend(["--asr-proper-nouns-path", _resolve_path(repo_root, str(effective["asr_proper_nouns_path"]))])
    # --- workflow / outputs
    add_bool("offline", "--offline")
    add_bool("mt_batch_enable", "--mt-batch-enable")
    add_val("mt_batch_size", "--mt-batch-size")
    add_bool("bilingual_srt", "--bilingual-srt")
    add_bool("skip_tts", "--skip-tts")
    add_val("min_sub_duration", "--min-sub-dur")
    add_val("tts_split_len", "--tts-split-len")
    add_val("tts_speed_max", "--tts-speed-max")
    add_val("tts_plan_safety_margin", "--tts-plan-safety-margin")
    add_val("subtitle_max_cps", "--subtitle-max-cps")
    add_val("subtitle_max_chars_per_line", "--subtitle-max-chars-per-line")
    add_val("subtitle_wrap_max_lines", "--subtitle-max-lines")
    add_val("resume_from", "--resume-from")
    if effective.get("chs_override_srt"):
        args.extend(["--chs-override-srt", _resolve_path(repo_root, str(effective["chs_override_srt"]))])
    if effective.get("eng_override_srt"):
        args.extend(["--eng-override-srt", _resolve_path(repo_root, str(effective["eng_override_srt"]))])

    # --- optional English polishing
    if str(effective.get("en_polish_model") or "").strip():
        args.extend(["--en-polish-model", str(effective["en_polish_model"]).strip()])
        if str(effective.get("en_polish_device") or "").strip():
            args.extend(["--en-polish-device", str(effective["en_polish_device"]).strip()])
    add_bool("lt_enable", "--lt-enable")
    if effective.get("replacements"):
        args.extend(["--replacements", _resolve_path(repo_root, str(effective["replacements"]))])

    # --- TTS backend
    tts_backend = str(effective.get("tts_backend") or "kokoro_onnx").strip().lower()
    if tts_backend not in {"coqui", "kokoro_onnx"}:
        tts_backend = "kokoro_onnx"
    args.extend(["--tts-backend", tts_backend])
    if tts_backend == "kokoro_onnx":
        if effective.get("kokoro_model"):
            args.extend(["--kokoro-model", _resolve_path(repo_root, str(effective["kokoro_model"]))])
        if effective.get("kokoro_voices"):
            args.extend(["--kokoro-voices", _resolve_path(repo_root, str(effective["kokoro_voices"]))])
        if effective.get("kokoro_voice"):
            args.extend(["--kokoro-voice", str(effective["kokoro_voice"])])
        if effective.get("kokoro_language"):
            args.extend(["--kokoro-language", str(effective["kokoro_language"])])
        if effective.get("kokoro_speed") is not None:
            args.extend(["--kokoro-speed", str(effective["kokoro_speed"])])
    else:
        if effective.get("coqui_model"):
            args.extend(["--coqui-model", str(effective["coqui_model"])])
        if effective.get("coqui_device") or effective.get("tts_device"):
            args.extend(["--coqui-device", str(effective.get("tts_device") or effective.get("coqui_device") or "auto")])
        if effective.get("coqui_speaker"):
            args.extend(["--coqui-speaker", str(effective["coqui_speaker"])])
        if effective.get("coqui_language"):
            args.extend(["--coqui-language", str(effective["coqui_language"])])

    return args


def _write_quality_report(*, mode: str, work_dir: Path, cfg: Dict[str, Any], source_video: Optional[Path], task_id: str) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    backend_app = repo_root / "apps" / "backend"
    for p in (str(repo_root), str(backend_app)):
        if p not in sys.path:
            sys.path.insert(0, p)
    from backend.quality_report import generate_quality_report, write_quality_report  # local import

    rep = generate_quality_report(task_id=task_id, mode=mode, work_dir=work_dir, source_video=source_video, cfg=cfg)
    write_quality_report(work_dir / "quality_report.json", rep)


def _pick_runtime_python(repo_root: Path, cfg: Dict[str, Any], effective: Dict[str, Any]) -> str:
    paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}
    raw = (
        str(effective.get("lite_runtime_python") or "").strip()
        or str(paths.get("lite_runtime_python") or "").strip()
        or str(os.environ.get("YGF_LITE_RUNTIME_PYTHON") or "").strip()
    )
    if raw:
        p = _resolve_runtime_python_path(repo_root, raw)
        if p is not None:
            return str(p)
    return sys.executable


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one lite E2E (asr_translate_tts) and generate quality_report.json")
    ap.add_argument("--video", type=str, required=True, help="Input video file path")
    ap.add_argument("--output-dir", type=str, required=True, help="Work dir for this run")
    ap.add_argument("--config", type=str, default="configs/defaults.yaml", help="Base config YAML (defaults.yaml)")
    ap.add_argument("--preset", type=str, default="normal", help="Preset key in config (normal/mid/high)")
    ap.add_argument("--mode", type=str, default="lite", choices=["lite"], help="Mode label written into report")
    ap.add_argument("--overrides-json", type=str, default="", help="Overrides as JSON dict (config-like keys)")
    ap.add_argument("--max-runtime-s", type=int, default=0, help="Hard timeout for the whole run (0=disable)")
    ap.add_argument(
        "--cleanup-artifacts",
        action="store_true",
        help="After generating quality_report.json, delete large artifacts (mp4/wav/segment wavs) to save disk.",
    )
    ap.add_argument("--log-max-kb", type=int, default=512, help="Max KB to keep for lite_run.log/_runner.log (0=keep all)")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    cfg = _load_yaml(Path(_resolve_path(repo_root, args.config)))
    defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}
    presets = cfg.get("presets") if isinstance(cfg.get("presets"), dict) else {}
    preset_cfg = presets.get(args.preset) if isinstance(presets.get(args.preset), dict) else {}
    paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}

    overrides: Dict[str, Any] = {}
    if str(args.overrides_json or "").strip():
        try:
            obj = json.loads(args.overrides_json)
            if isinstance(obj, dict):
                overrides = obj
        except Exception:
            overrides = {}

    effective: Dict[str, Any] = {}
    effective.update(defaults or {})
    effective.update(preset_cfg or {})
    effective.update(overrides or {})
    effective = _hydrate_effective_runtime_paths(effective, paths)
    effective = _align_lite_subtitle_policy(effective, cfg)

    # Build core paths (best-effort, similar to backend TaskManager)
    whisper_bin = _pick_existing(
        _resolve_path(repo_root, str(effective.get("whispercpp_bin") or paths.get("whispercpp_bin") or "")),
        "/usr/local/bin/whisper-cli",
        "/opt/homebrew/bin/whisper-cli",
        str((repo_root / "bin" / "whisper-cli").resolve()),
        str((repo_root / "bin" / "main").resolve()),
        "/app/bin/whisper-cli",
        "/app/bin/main",
        "/app/local_bin/whisper-cli",
        "/app/local_bin/main",
    )
    whisper_model = _pick_existing(
        _resolve_path(repo_root, str(effective.get("asr_model") or effective.get("whispercpp_model") or paths.get("whispercpp_model") or "")),
        _resolve_path(repo_root, "assets/models/ggml-small-q5_0.bin"),
        "/app/assets/models/ggml-small-q5_0.bin",
    )

    glossary_path = _pick_existing(
        _resolve_path(repo_root, str(effective.get("glossary") or paths.get("glossary") or "assets/glossary/glossary.json")),
        _resolve_path(repo_root, "assets/glossary/glossary.json"),
    )

    mt_model = str(effective.get("mt_model") or "Helsinki-NLP/opus-mt-zh-en")
    mt_device = str(effective.get("mt_device") or "auto")
    mt_cache_dir = str(effective.get("mt_cache_dir") or paths.get("hf_cache") or "assets/models/hf")
    mt_cache_dir = _resolve_path(repo_root, mt_cache_dir)

    runtime_python = _pick_runtime_python(repo_root, cfg, effective)

    cmd: List[str] = [
        runtime_python,
        str((repo_root / "pipelines" / "lite_pipeline.py").resolve()),
        "--video",
        str(args.video),
        "--output-dir",
        str(args.output_dir),
        "--glossary",
        str(glossary_path),
        "--whispercpp-bin",
        str(whisper_bin),
        "--whispercpp-model",
        str(whisper_model),
        "--mt-model",
        mt_model,
        "--mt-device",
        mt_device,
        "--mt-cache-dir",
        str(mt_cache_dir),
    ]
    cmd.extend(_to_cli_args(effective, repo_root))

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "lite_effective_config.json").write_text(json.dumps(effective, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "lite_cmd.txt").write_text(" ".join(cmd) + "\n", encoding="utf-8")

    effective_timeout_s, source_duration_s, timeout_mode = _compute_effective_timeout_s(
        int(args.max_runtime_s or 0),
        source_video=Path(args.video),
    )

    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
    timed_out = False
    timeout_grace_used_s = 0
    try:
        if effective_timeout_s > 0:
            proc.wait(timeout=int(effective_timeout_s))
        else:
            proc.wait()
    except subprocess.TimeoutExpired:
        if effective_timeout_s > 0 and _has_timeout_grace_artifacts(out_dir, effective):
            try:
                # If delivery artifacts are already complete, give the runner a short
                # grace window to exit cleanly instead of failing at the finish line.
                proc.wait(timeout=TIMEOUT_COMPLETION_GRACE_S)
                timeout_grace_used_s = TIMEOUT_COMPLETION_GRACE_S
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait(timeout=10)
        else:
            timed_out = True
            proc.kill()
            proc.wait(timeout=10)
    finally:
        # Best-effort drain output
        try:
            out = ""
            if proc.stdout is not None:
                out = proc.stdout.read() or ""
            # truncate logs to avoid filling disk on long runs
            if args.log_max_kb and int(args.log_max_kb) > 0:
                max_chars = int(args.log_max_kb) * 1024
                if len(out) > max_chars:
                    out = out[:max_chars] + "\n...[truncated]...\n"
            (out_dir / "lite_run.log").write_text(out, encoding="utf-8")
        except Exception:
            pass

    rc = int(proc.returncode or 0)
    (out_dir / "lite_run_meta.json").write_text(
        json.dumps(
            {
                "return_code": rc,
                "elapsed_s": round(time.time() - t0, 3),
                "timed_out": bool(timed_out),
                "requested_max_runtime_s": int(args.max_runtime_s or 0),
                "effective_timeout_s": int(effective_timeout_s or 0),
                "timeout_mode": timeout_mode,
                "source_video_duration_s": round(float(source_duration_s), 3) if source_duration_s else None,
                "timeout_grace_used_s": int(timeout_grace_used_s),
                "terminated_by_signal": abs(rc) if rc < 0 else None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Generate quality_report.json even on failures (so the evaluator has something to read).
    try:
        _write_quality_report(
            mode=args.mode,
            work_dir=out_dir,
            cfg=cfg,
            source_video=Path(args.video) if args.video else None,
            task_id=f"lite:{args.preset}:{out_dir.name}",
        )
    except Exception as exc:
        (out_dir / "quality_report_error.txt").write_text(str(exc), encoding="utf-8")

    if args.cleanup_artifacts:
        # Keep: quality_report.json, *.srt, audio.json, lite_effective_config.json, lite_cmd.txt, logs/meta.
        # Remove: heavy media files and per-seg wav caches (tts_segments/*).
        try:
            for name in ["output_en.mp4", "output_en_sub.mp4", "tts_full.wav", "audio.wav"]:
                p = out_dir / name
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
            tts_dir = out_dir / "tts_segments"
            if tts_dir.exists() and tts_dir.is_dir():
                for it in tts_dir.glob("*.wav"):
                    try:
                        it.unlink()
                    except Exception:
                        pass
                # best-effort remove dir if empty
                try:
                    if not any(tts_dir.iterdir()):
                        tts_dir.rmdir()
                except Exception:
                    pass
        except Exception:
            pass

    sys.exit(rc)


if __name__ == "__main__":
    main()


