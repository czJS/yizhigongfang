#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


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


def _dash(s: str) -> str:
    return str(s).replace("_", "-")


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

    # --- ASR normalize
    add_bool("asr_normalize_enable", "--asr-normalize-enable")
    if effective.get("asr_normalize_dict"):
        args.extend(["--asr-normalize-dict", _resolve_path(repo_root, str(effective["asr_normalize_dict"]))])

    # --- MT quality-lite options
    add_bool("sentence_unit_enable", "--sentence-unit-enable")
    add_val("sentence_unit_min_chars", "--sentence-unit-min-chars")
    add_val("sentence_unit_max_chars", "--sentence-unit-max-chars")
    add_val("sentence_unit_max_segs", "--sentence-unit-max-segs")
    add_val("sentence_unit_max_gap_s", "--sentence-unit-max-gap-s")
    add_val("sentence_unit_boundary_punct", "--sentence-unit-boundary-punct")
    # list[str] or str
    bw = effective.get("sentence_unit_break_words")
    if isinstance(bw, list) and bw:
        args.extend(["--sentence-unit-break-words", ",".join(str(x) for x in bw if str(x).strip())])
    elif isinstance(bw, str) and bw.strip():
        args.extend(["--sentence-unit-break-words", bw.strip()])

    add_bool("entity_protect_enable", "--entity-protect-enable")
    add_val("entity_protect_min_len", "--entity-protect-min-len")
    add_val("entity_protect_max_len", "--entity-protect-max-len")
    add_val("entity_protect_min_freq", "--entity-protect-min-freq")
    add_val("entity_protect_max_items", "--entity-protect-max-items")

    # --- workflow / outputs
    add_bool("offline", "--offline")
    add_bool("bilingual_srt", "--bilingual-srt")
    add_bool("skip_tts", "--skip-tts")
    add_val("min_sub_duration", "--min-sub-dur")
    add_val("tts_split_len", "--tts-split-len")
    add_val("tts_speed_max", "--tts-speed-max")
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
    tts_backend = str(effective.get("tts_backend") or "piper").strip().lower()
    if tts_backend not in {"piper", "coqui"}:
        tts_backend = "piper"
    args.extend(["--tts-backend", tts_backend])
    if tts_backend == "piper":
        if effective.get("piper_model"):
            args.extend(["--piper-model", _resolve_path(repo_root, str(effective["piper_model"]))])
        if effective.get("piper_bin"):
            args.extend(["--piper-bin", str(effective["piper_bin"])])
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
    from backend.quality_report import generate_quality_report, write_quality_report  # local import

    rep = generate_quality_report(task_id=task_id, mode=mode, work_dir=work_dir, source_video=source_video, cfg=cfg)
    write_quality_report(work_dir / "quality_report.json", rep)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one lite E2E (asr_translate_tts) and generate quality_report.json")
    ap.add_argument("--video", type=str, required=True, help="Input video file path")
    ap.add_argument("--output-dir", type=str, required=True, help="Work dir for this run")
    ap.add_argument("--config", type=str, default="config/defaults.yaml", help="Base config YAML (defaults.yaml)")
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

    # Build core paths (best-effort, similar to backend TaskManager)
    whisper_bin = _pick_existing(
        _resolve_path(repo_root, str(effective.get("whispercpp_bin") or paths.get("whispercpp_bin") or "")),
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

    # Piper defaults
    if not effective.get("piper_model"):
        # repo includes a bundled ONNX under assets/models
        if (repo_root / "assets" / "models" / "en_US-amy-low.onnx").exists():
            effective["piper_model"] = "assets/models/en_US-amy-low.onnx"
    if not effective.get("piper_bin"):
        # In docker, the runnable piper is typically under /app/local_bin/piper/piper or available on PATH.
        effective["piper_bin"] = _pick_existing(
            "/app/local_bin/piper/piper",
            "/app/bin/piper/piper",
            "piper",
        )

    cmd: List[str] = [
        sys.executable,
        str((repo_root / "scripts" / "asr_translate_tts.py").resolve()),
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

    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
    try:
        if args.max_runtime_s and int(args.max_runtime_s) > 0:
            proc.wait(timeout=int(args.max_runtime_s))
        else:
            proc.wait()
    except subprocess.TimeoutExpired:
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
        json.dumps({"return_code": rc, "elapsed_s": round(time.time() - t0, 3)}, ensure_ascii=False, indent=2),
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


