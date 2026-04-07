from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


PathResolver = Callable[[str], Path]
ExecPicker = Callable[[str, List[str]], str]


def _get_quality_gate(cfg: Dict[str, Any], key: str, default: Any) -> Any:
    gates = cfg.get("quality_gates")
    if not isinstance(gates, dict):
        return default
    return gates.get(key, default)


def _resolve_lite_subtitle_policy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    base_chars = 42
    base_cps = 20.0
    try:
        gate_chars = int(_get_quality_gate(cfg, "max_chars_per_line", base_chars) or base_chars)
    except Exception:
        gate_chars = base_chars
    try:
        gate_cps = float(_get_quality_gate(cfg, "max_cps", base_cps) or base_cps)
    except Exception:
        gate_cps = base_cps
    return {
        "subtitle_max_chars_per_line": max(16, min(base_chars, gate_chars)),
        "subtitle_max_cps": max(8.0, min(base_cps, gate_cps)),
        "subtitle_max_lines": 2,
    }


def _resolve_lite_runtime_python(
    cfg: Dict[str, Any],
    paths: Dict[str, Any],
    *,
    resolve_path: PathResolver,
    env: Dict[str, str],
) -> Optional[Path]:
    lite_runtime_python_raw = (
        cfg.get("lite_runtime_python")
        or paths.get("lite_runtime_python")
        or env.get("YGF_LITE_RUNTIME_PYTHON")
        or ""
    )
    return resolve_path(lite_runtime_python_raw) if str(lite_runtime_python_raw).strip() else None


def _resolve_whisper_bin(
    cfg: Dict[str, Any],
    paths: Dict[str, Any],
    *,
    resolve_path: PathResolver,
) -> Path:
    whisper_candidate = cfg.get("whispercpp_bin") or paths.get("whispercpp_bin") or "bin/whisper-cli"
    whisper_bin = resolve_path(whisper_candidate)
    if whisper_bin.exists():
        return whisper_bin
    for fallback in ("/usr/local/bin/whisper-cli", "/opt/homebrew/bin/whisper-cli", "bin/main"):
        candidate = resolve_path(fallback)
        if candidate.exists():
            return candidate
    return whisper_bin


def _resolve_asr_model(
    cfg: Dict[str, Any],
    paths: Dict[str, Any],
    *,
    resolve_path: PathResolver,
) -> Path:
    asr_model = resolve_path(
        cfg.get("asr_model")
        or cfg.get("whispercpp_model")
        or paths.get("whispercpp_model", "assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin")
    )
    if asr_model.exists():
        return asr_model
    for fallback in (
        "assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin",
        "assets/models/lite_asr_whispercpp/ggml-tiny-q5_1.bin",
    ):
        candidate = resolve_path(fallback)
        if candidate.exists():
            return candidate
    return asr_model


def _resolve_tts_runtime(
    cfg: Dict[str, Any],
    paths: Dict[str, Any],
    *,
    resolve_path: PathResolver,
    pick_executable: ExecPicker,
) -> Dict[str, Any]:
    tts_backend = str(cfg.get("tts_backend") or "kokoro_onnx").strip().lower() or "kokoro_onnx"
    kokoro_model = cfg.get("kokoro_model") or paths.get("kokoro_model") or "assets/models/lite_tts_kokoro_onnx/kokoro-v1.0.onnx"
    kokoro_voices = cfg.get("kokoro_voices") or paths.get("kokoro_voices") or "assets/models/lite_tts_kokoro_onnx/voices-v1.0.bin"
    kokoro_model_path = resolve_path(kokoro_model)
    kokoro_voices_path = resolve_path(kokoro_voices)
    kokoro_available = kokoro_model_path.exists() and kokoro_voices_path.exists()

    if tts_backend not in {"kokoro_onnx", "coqui"}:
        tts_backend = "kokoro_onnx"
    if tts_backend == "kokoro_onnx" and not kokoro_available:
        tts_backend = "coqui"

    return {
        "tts_backend": tts_backend,
        "kokoro_model_path": kokoro_model_path,
        "kokoro_voices_path": kokoro_voices_path,
    }


def build_lite_command(
    *,
    video_path: str,
    work_dir: Path,
    cfg: Dict[str, Any],
    paths: Dict[str, Any],
    script: Path,
    resume_from: Optional[str],
    resolve_path: PathResolver,
    pick_executable: ExecPicker,
    packaged_exe: bool,
    sys_executable: str,
    env: Dict[str, str],
) -> List[str]:
    lite_runtime_python = _resolve_lite_runtime_python(cfg, paths, resolve_path=resolve_path, env=env)
    whisper_bin = _resolve_whisper_bin(cfg, paths, resolve_path=resolve_path)
    asr_model = _resolve_asr_model(cfg, paths, resolve_path=resolve_path)
    vad_model_raw = cfg.get("vad_model") or paths.get("vad_model") or "assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin"
    vad_model = resolve_path(vad_model_raw)
    vad_enabled = bool(cfg.get("vad_enable")) and vad_model.exists()

    mt_model = "assets/models/lite_mt_marian_opus_mt_zh_en"
    mt_device = "auto"
    coqui_model = str(cfg.get("coqui_model") or "tts_models/multilingual/multi-dataset/xtts_v2")
    coqui_device = str(cfg.get("coqui_device") or "auto")
    kokoro_voice = str(cfg.get("kokoro_voice") or "af_bella")
    kokoro_language = str(cfg.get("kokoro_language") or "en-us")
    kokoro_speed = float(cfg.get("kokoro_speed", 1.0) or 1.0)
    tts_runtime = _resolve_tts_runtime(cfg, paths, resolve_path=resolve_path, pick_executable=pick_executable)

    hf_cache_rel = paths.get("hf_cache") or "assets/models/common_cache_hf"
    mt_cache_dir = resolve_path(hf_cache_rel)

    args: List[str] = [
        "--video",
        str(video_path),
        "--output-dir",
        str(work_dir),
        "--glossary",
        str(resolve_path(cfg.get("glossary") or paths.get("glossary", "assets/glossary/glossary.json"))),
        "--whispercpp-bin",
        str(whisper_bin),
        "--whispercpp-model",
        str(asr_model),
        "--mt-model",
        mt_model,
        "--mt-device",
        mt_device,
        "--mt-cache-dir",
        str(mt_cache_dir),
        "--sample-rate",
        str(cfg.get("sample_rate", 16000)),
    ]
    if cfg.get("mt_batch_enable"):
        args.append("--mt-batch-enable")
    if cfg.get("mt_batch_size") is not None:
        args += ["--mt-batch-size", str(cfg.get("mt_batch_size"))]
    if resume_from:
        args += ["--resume-from", resume_from]
    if cfg.get("chs_override_srt"):
        args += ["--chs-override-srt", str(cfg["chs_override_srt"])]
    if cfg.get("eng_override_srt"):
        args += ["--eng-override-srt", str(cfg["eng_override_srt"])]
    args.append("--offline")
    if cfg.get("whispercpp_threads"):
        args += ["--whispercpp-threads", str(cfg["whispercpp_threads"])]
    if cfg.get("whispercpp_beam_size") is not None:
        args += ["--whispercpp-beam-size", str(cfg["whispercpp_beam_size"])]
    if vad_enabled:
        args += ["--vad-enable", "--vad-model", str(vad_model)]
        if cfg.get("vad_threshold") is not None:
            args += ["--vad-thold", str(cfg["vad_threshold"])]
        if cfg.get("vad_min_dur") is not None:
            args += ["--vad-min-dur", str(cfg["vad_min_dur"])]

    args += ["--tts-backend", str(tts_runtime["tts_backend"])]
    if tts_runtime["tts_backend"] == "kokoro_onnx":
        args += ["--kokoro-model", str(tts_runtime["kokoro_model_path"])]
        args += ["--kokoro-voices", str(tts_runtime["kokoro_voices_path"])]
        args += ["--kokoro-voice", kokoro_voice]
        args += ["--kokoro-language", kokoro_language]
        args += ["--kokoro-speed", str(kokoro_speed)]
    else:
        args += ["--coqui-model", coqui_model]
        args += ["--coqui-device", coqui_device]
    if cfg.get("skip_tts"):
        args.append("--skip-tts")
    if cfg.get("min_sub_duration"):
        args += ["--min-sub-dur", str(cfg["min_sub_duration"])]
    if cfg.get("tts_split_len"):
        args += ["--tts-split-len", str(cfg["tts_split_len"])]
    if cfg.get("tts_speed_max"):
        args += ["--tts-speed-max", str(cfg["tts_speed_max"])]
    if cfg.get("tts_sample_rate"):
        args += ["--tts-sample-rate", str(cfg["tts_sample_rate"])]
    args += ["--tts-align-mode", str(cfg.get("tts_align_mode", "atempo") or "atempo")]
    args += ["--tts-plan-safety-margin", str(float(cfg.get("tts_plan_safety_margin", 0.02) or 0.02))]
    subtitle_policy = _resolve_lite_subtitle_policy(cfg)
    args += ["--subtitle-max-cps", str(subtitle_policy["subtitle_max_cps"])]
    args += ["--subtitle-max-chars-per-line", str(subtitle_policy["subtitle_max_chars_per_line"])]
    args += ["--subtitle-max-lines", str(subtitle_policy["subtitle_max_lines"])]
    args += ["--sub-font-name", str(cfg.get("sub_font_name", "Arial") or "Arial")]
    args += ["--sub-font-size", str(int(cfg.get("sub_font_size", 18) or 18))]
    args += ["--sub-outline", str(int(cfg.get("sub_outline", 1) or 1))]
    args += ["--sub-shadow", str(int(cfg.get("sub_shadow", 0) or 0))]
    args += ["--sub-margin-v", str(int(cfg.get("sub_margin_v", 24) or 24))]
    args += ["--sub-alignment", str(int(cfg.get("sub_alignment", 2) or 2))]
    args += ["--mux-sync-strategy", str(cfg.get("mux_sync_strategy", "slow") or "slow")]
    args += ["--mux-slow-max-ratio", str(float(cfg.get("mux_slow_max_ratio", 1.18) or 1.18))]
    args += ["--mux-slow-threshold-s", str(float(cfg.get("mux_slow_threshold_s", 0.05) or 0.05))]
    if cfg.get("erase_subtitle_enable"):
        args.append("--erase-subtitle-enable")
        args += ["--erase-subtitle-method", str(cfg.get("erase_subtitle_method", "delogo") or "delogo")]
        args += ["--erase-subtitle-coord-mode", str(cfg.get("erase_subtitle_coord_mode", "ratio") or "ratio")]
        args += ["--erase-subtitle-x", str(cfg.get("erase_subtitle_x", 0.0))]
        args += ["--erase-subtitle-y", str(cfg.get("erase_subtitle_y", 0.78))]
        args += ["--erase-subtitle-w", str(cfg.get("erase_subtitle_w", 1.0))]
        args += ["--erase-subtitle-h", str(cfg.get("erase_subtitle_h", 0.22))]
        args += ["--erase-subtitle-blur-radius", str(int(cfg.get("erase_subtitle_blur_radius", 12) or 12))]
    if cfg.get("sub_place_enable"):
        args.append("--sub-place-enable")
    if str(cfg.get("sub_place_coord_mode", "") or "").strip():
        args += ["--sub-place-coord-mode", str(cfg.get("sub_place_coord_mode")).strip()]
    if cfg.get("sub_place_x") is not None:
        args += ["--sub-place-x", str(cfg.get("sub_place_x", 0.0))]
    if cfg.get("sub_place_y") is not None:
        args += ["--sub-place-y", str(cfg.get("sub_place_y", 0.78))]
    if cfg.get("sub_place_w") is not None:
        args += ["--sub-place-w", str(cfg.get("sub_place_w", 1.0))]
    if cfg.get("sub_place_h") is not None:
        args += ["--sub-place-h", str(cfg.get("sub_place_h", 0.22))]

    args.append("--asr-normalize-enable")
    norm_dict = cfg.get("asr_normalize_dict") or paths.get("asr_normalize_dict") or "assets/asr_normalize/asr_zh_dict.json"
    args += ["--asr-normalize-dict", str(resolve_path(norm_dict))]
    if cfg.get("asr_glossary_fix_enable"):
        args.append("--asr-glossary-fix-enable")
    if cfg.get("asr_low_cost_clean_enable"):
        args.append("--asr-low-cost-clean-enable")
    if cfg.get("asr_badline_detect_enable"):
        args.append("--asr-badline-detect-enable")
    if cfg.get("asr_same_pinyin_path"):
        args += ["--asr-same-pinyin-path", str(resolve_path(cfg["asr_same_pinyin_path"]))]
    if cfg.get("asr_same_stroke_path"):
        args += ["--asr-same-stroke-path", str(resolve_path(cfg["asr_same_stroke_path"]))]
    if cfg.get("asr_project_confusions_path"):
        args += ["--asr-project-confusions-path", str(resolve_path(cfg["asr_project_confusions_path"]))]
    if cfg.get("asr_lexicon_path"):
        args += ["--asr-lexicon-path", str(resolve_path(cfg["asr_lexicon_path"]))]
    if cfg.get("asr_proper_nouns_path"):
        args += ["--asr-proper-nouns-path", str(resolve_path(cfg["asr_proper_nouns_path"]))]
    if cfg.get("en_replace_dict"):
        try:
            args += ["--en-replace-dict", str(resolve_path(cfg["en_replace_dict"]))]
        except Exception:
            pass

    if lite_runtime_python and lite_runtime_python.exists():
        return [str(lite_runtime_python), str(script), *args]
    if packaged_exe:
        return [sys_executable, "--run-pipeline", "lite", *args]
    return [sys_executable, str(script), *args]
