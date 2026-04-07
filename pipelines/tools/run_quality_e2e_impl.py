#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import os
import selectors
import subprocess
import sys
import time
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml


def _read_jsonl(p: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        obj = json.loads(ln)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _load_yaml(p: Path) -> Dict[str, Any]:
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _dash(s: str) -> str:
    return str(s).replace("_", "-")


# config key -> quality_pipeline cli key (without leading --)
_KEY_MAP: Dict[str, str] = {
    # config uses *_duration but CLI uses *_dur
    "min_sub_duration": "min_sub_dur",
    # whisperx VAD threshold arg name is historical
    "vad_threshold": "vad_thold",
}


def _cli_key(k: str) -> str:
    return _KEY_MAP.get(k, k)


# Keep this tool in sync with `pipelines/quality_pipeline_impl.py` argparse.
# Only keys in this allowlist are converted into CLI arguments.
_SUPPORTED_KEYS = {
    # I/O and review workflow
    "glossary",
    "en_replace_dict",
    "chs_override_srt",
    "eng_override_srt",
    "resume_from",
    "skip_tts",
    # ASR / segmentation
    "whisperx_model",
    "whisperx_model_dir",
    "diarization",
    "vad_enable",
    "vad_threshold",
    "vad_min_dur",
    "max_sentence_len",
    "min_sub_duration",
    "sample_rate",
    "denoise",
    "denoise_model",
    # MT / LLM
    "llm_endpoint",
    "llm_model",
    "llm_api_key",
    "llm_chunk_size",
    "mt_context_window",
    "mt_style",
    "mt_max_words_per_line",
    "mt_prompt_mode",
    "mt_long_fallback_enable",
    "mt_long_examples_enable",
    "mt_compact_enable",
    "mt_compact_aggressive",
    "mt_compact_temperature",
    "mt_compact_max_tokens",
    "mt_compact_timeout_s",
    "mt_long_zh_chars",
    "mt_long_en_words",
    "mt_long_target_words",
    # Text normalize
    "asr_normalize_enable",
    "asr_normalize_dict",
    # Subtitle post-process / display subtitles
    "subtitle_postprocess_enable",
    "subtitle_wrap_enable",
    "subtitle_wrap_max_lines",
    "subtitle_max_chars_per_line",
    "subtitle_max_cps",
    "display_srt_enable",
    "display_use_for_embed",
    "display_max_chars_per_line",
    "display_max_lines",
    "display_merge_enable",
    "display_merge_max_gap_s",
    "display_merge_max_chars",
    "display_split_enable",
    "display_split_max_chars",
    # Subtitle erase (feature)
    "erase_subtitle_enable",
    "erase_subtitle_method",
    "erase_subtitle_coord_mode",
    "erase_subtitle_x",
    "erase_subtitle_y",
    "erase_subtitle_w",
    "erase_subtitle_h",
    "erase_subtitle_blur_radius",
    # TTS
    "tts_backend",
    "piper_model",
    "piper_bin",
    "coqui_model",
    "coqui_device",
    "tts_split_len",
    "tts_speed_max",
    "tts_align_mode",
    "tts_fit_enable",
    "tts_fit_wps",
    "tts_fit_min_words",
    "tts_fit_save_raw",
    "tts_plan_enable",
    "tts_plan_safety_margin",
    "tts_plan_min_cap",
    # Hard-sub styles and placement
    "sub_font_name",
    "sub_font_size",
    "sub_outline",
    "sub_shadow",
    "sub_margin_v",
    "sub_alignment",
    "sub_place_enable",
    "sub_place_coord_mode",
    "sub_place_x",
    "sub_place_y",
    "sub_place_w",
    "sub_place_h",
    # Mux sync
    "mux_sync_strategy",
    "mux_slow_max_ratio",
    "mux_slow_threshold_s",
}


def _overrides_to_args(overrides: Dict[str, Any]) -> List[str]:
    """
    Convert config-like overrides into quality_pipeline CLI args.
    Convention:
    - bool True  -> add flag --kebab
    - bool False -> omit flag (use effective config merge to disable)
    - number/str -> --kebab value
    - list[str]  -> comma-join (for sentence_unit_break_words etc.)
    """
    args: List[str] = []
    for k, v in (overrides or {}).items():
        if str(k) not in _SUPPORTED_KEYS:
            continue
        key = "--" + _dash(_cli_key(str(k)))
        if isinstance(v, bool):
            if v is True:
                args.append(key)
            continue
        if isinstance(v, (int, float, str)):
            args.extend([key, str(v)])
            continue
        if isinstance(v, list):
            # only support list[str|int|float] by joining
            args.extend([key, ",".join(str(x) for x in v)])
            continue
        # ignore unknown complex values
    return args


def _read_proc_cpu_jiffies(pid: int) -> int | None:
    """
    Best-effort CPU time counter for stall detection.
    Linux only (/proc). Returns (utime+stime) jiffies, or None if not available.
    """
    try:
        stat_p = Path(f"/proc/{int(pid)}/stat")
        if not stat_p.exists():
            return None
        parts = stat_p.read_text(encoding="utf-8", errors="ignore").strip().split()
        # fields 14,15 are utime, stime (1-indexed). Here 0-indexed => 13,14
        if len(parts) >= 15:
            return int(parts[13]) + int(parts[14])
    except Exception:
        return None
    return None


def _latest_file_mtime(p: Path) -> float | None:
    """
    Best-effort latest mtime under a directory (non-recursive, files only).
    We keep it cheap: it's only used as one of the 'progress signals'.
    """
    try:
        if not p.exists():
            return None
        mt = None
        for it in p.iterdir():
            if it.is_file():
                try:
                    t = float(it.stat().st_mtime)
                    mt = t if mt is None else max(mt, t)
                except Exception:
                    continue
        return mt
    except Exception:
        return None


def _run_one(
    video: Path,
    out_dir: Path,
    base_cfg: Dict[str, Any],
    overrides: Dict[str, Any],
    *,
    stall_timeout_s: int = 0,
    stall_check_s: int = 10,
    stall_cpu_min_jiffies: int = 5,
    max_runtime_s: int = 0,
) -> Tuple[int, float, str]:
    """
    Run pipelines/quality_pipeline.py for one segment.
    """
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Base args from base config (quality.yaml) defaults
    defaults = (base_cfg.get("defaults") or {}) if isinstance(base_cfg.get("defaults"), dict) else {}
    paths = (base_cfg.get("paths") or {}) if isinstance(base_cfg.get("paths"), dict) else {}

    # Merge defaults + overrides into one "effective config", then convert once.
    # This allows bool False to disable a default-on flag without relying on `--no-xxx` arguments.
    effective: Dict[str, Any] = dict(defaults)
    effective.update(dict(overrides or {}))
    # paths -> cli (only when not already specified by overrides/defaults)
    if "whisperx_model_dir" in paths and "whisperx_model_dir" not in effective:
        effective["whisperx_model_dir"] = paths.get("whisperx_model_dir")
    if "glossary" in paths and "glossary" not in effective:
        effective["glossary"] = paths.get("glossary")
    base_args = _overrides_to_args(effective)
    exp_args: List[str] = []

    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "quality_pipeline.py"),
        "--video",
        str(video),
        "--output-dir",
        str(out_dir),
    ] + base_args + exp_args

    # Ensure consistent env (LLM endpoint inside docker by service name)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    # Stream logs live so:
    # - we can detect stalls (no output + CPU idle)
    # - run_round1_onejob can detect active runs via e2e_run.log mtime
    log_p = out_dir / "e2e_run.log"
    log_p.parent.mkdir(parents=True, exist_ok=True)
    with log_p.open("a", encoding="utf-8") as lf:
        lf.write(f"\n[{time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime())}] CMD: {' '.join(cmd)}\n")
        lf.flush()

        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
        assert p.stdout is not None

        sel = selectors.DefaultSelector()
        sel.register(p.stdout, selectors.EVENT_READ)

        tail_lines: collections.deque[str] = collections.deque(maxlen=200)

        last_output_ts = time.time()
        last_file_mtime = _latest_file_mtime(out_dir)
        last_progress_ts = last_output_ts

        last_cpu_j = _read_proc_cpu_jiffies(p.pid)
        last_cpu_check_ts = time.time()
        idle_cpu_s = 0.0

        stall_timeout_s = max(0, int(stall_timeout_s or 0))
        stall_check_s = max(1, int(stall_check_s or 1))
        stall_cpu_min_jiffies = max(0, int(stall_cpu_min_jiffies or 0))
        max_runtime_s = max(0, int(max_runtime_s or 0))

        while True:
            # Read available output lines without blocking forever.
            events = sel.select(timeout=float(stall_check_s))
            now = time.time()
            for key, _mask in events:
                try:
                    line = key.fileobj.readline()
                except Exception:
                    line = ""
                if not line:
                    continue
                last_output_ts = now
                last_progress_ts = now
                tail_lines.append(line)
                # tee to stdout + file
                sys.stdout.write(line)
                sys.stdout.flush()
                lf.write(line)
                lf.flush()

            # File progress signal (cheap): any file mtime updated.
            mt = _latest_file_mtime(out_dir)
            if mt is not None and (last_file_mtime is None or mt > last_file_mtime + 1e-6):
                last_file_mtime = mt
                last_progress_ts = now

            rc = p.poll()
            if rc is not None:
                # Drain remaining output if any (best-effort)
                try:
                    for line in p.stdout:
                        if not line:
                            continue
                        tail_lines.append(line)
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        lf.write(line)
                        lf.flush()
                except Exception:
                    pass
                return int(rc), time.time() - t0, ("".join(tail_lines))[-2000:]

            # Watchdog: only triggers when BOTH:
            # - no progress signal for stall_timeout_s
            # - CPU time is not increasing (likely stuck waiting on network)
            if stall_timeout_s > 0:
                cpu_j = _read_proc_cpu_jiffies(p.pid)
                if cpu_j is not None and last_cpu_j is not None:
                    dt = max(0.0, now - last_cpu_check_ts)
                    dj = cpu_j - last_cpu_j
                    if dj <= stall_cpu_min_jiffies:
                        idle_cpu_s += dt
                    else:
                        idle_cpu_s = 0.0
                    last_cpu_j = cpu_j
                    last_cpu_check_ts = now

                stalled_by_time = (now - last_progress_ts) >= float(stall_timeout_s)
                stalled_by_cpu = (idle_cpu_s >= float(stall_timeout_s)) if (cpu_j is not None and last_cpu_j is not None) else False

                if stalled_by_time and stalled_by_cpu:
                    lf.write(f"\n[watchdog] stall detected: no progress for {stall_timeout_s}s and CPU idle. terminating pid={p.pid}\n")
                    lf.flush()
                    try:
                        p.terminate()
                    except Exception:
                        pass
                    try:
                        p.wait(timeout=15)
                    except Exception:
                        try:
                            p.kill()
                        except Exception:
                            pass
                    rc2 = p.poll()
                    return int(rc2 if rc2 is not None else -15), time.time() - t0, ("".join(tail_lines))[-2000:]

            if max_runtime_s > 0 and (now - t0) >= float(max_runtime_s):
                lf.write(f"\n[watchdog] max runtime reached: {max_runtime_s}s. terminating pid={p.pid}\n")
                lf.flush()
                try:
                    p.terminate()
                except Exception:
                    pass
                try:
                    p.wait(timeout=15)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
                rc2 = p.poll()
                return int(rc2 if rc2 is not None else -15), time.time() - t0, ("".join(tail_lines))[-2000:]


def _looks_like_asr_override(overrides: Dict[str, Any]) -> bool:
    """
    If an experiment changes ASR-related knobs, we should NOT reuse baseline ASR outputs.
    """
    asr_prefixes = ("asr_", "whisperx_", "vad_", "denoise", "max_sentence_len", "min_sub_duration")
    for k in (overrides or {}).keys():
        ks = str(k)
        # NOTE: diarization currently has no effect in the pipeline (placeholder), so it should not
        # force a full ASR rerun or block reuse. Keep whisperx_model as ASR-affecting.
        if ks.startswith(asr_prefixes) or ks in {"whisperx_model"}:
            return True
    return False


def _prepare_reuse_baseline_asr(baseline_seg_dir: Path, target_seg_dir: Path) -> None:
    """
    Copy baseline ASR artifacts (audio.wav + audio.json + chs.srt) and strip translation/tts fields from audio.json.
    This enables running resume_from=mt for fair MT/TTS comparisons.
    """
    target_seg_dir.mkdir(parents=True, exist_ok=True)
    # required for quality_report: audio.wav exists
    for name in ["audio.wav", "chs.srt"]:
        src = baseline_seg_dir / name
        if src.exists():
            shutil.copy2(src, target_seg_dir / name)
    # audio.json: strip translation/tts fields
    aj = baseline_seg_dir / "audio.json"
    if aj.exists():
        try:
            data = json.loads(aj.read_text(encoding="utf-8", errors="ignore") or "[]")
            if isinstance(data, list):
                for it in data:
                    if isinstance(it, dict):
                        it.pop("translation", None)
                        it.pop("tts", None)
                        it.pop("tts_max_speed", None)
                (target_seg_dir / "audio.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                shutil.copy2(aj, target_seg_dir / "audio.json")
        except Exception:
            shutil.copy2(aj, target_seg_dir / "audio.json")


def _is_done_success(out_dir: Path) -> bool:
    """
    Segment is considered done only when a quality_report.json exists AND passed=true.
    This prevents skipping failed segments on resume.
    """
    rep_p = out_dir / "quality_report.json"
    if not rep_p.exists():
        return False
    try:
        rep = json.loads(rep_p.read_text(encoding="utf-8", errors="ignore") or "{}")
    except Exception:
        return False
    if not isinstance(rep, dict):
        return False
    if rep.get("passed") is not True:
        return False
    missing = (((rep.get("checks") or {}).get("required_artifacts") or {}).get("missing") or [])
    if isinstance(missing, list) and len(missing) > 0:
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Run quality-mode E2E experiments on a segment set.")
    ap.add_argument("--segments", type=Path, required=True, help="segments jsonl: {id, video, meta?}")
    ap.add_argument("--experiments", type=Path, required=True, help="experiments.yaml")
    ap.add_argument("--base-config", type=Path, required=True, help="quality.yaml")
    ap.add_argument("--out-root", type=Path, required=True, help="Output root (e.g., outputs/eval/e2e_quality)")
    ap.add_argument("--jobs", type=int, default=1, help="Parallel jobs (currently sequential; reserved for future)")
    ap.add_argument(
        "--seg-stall-timeout-s",
        type=int,
        default=0,
        help="单段卡死止血：若连续 N 秒无输出/无文件更新 且 CPU 几乎不增长，则终止该段（0=关闭）",
    )
    ap.add_argument(
        "--seg-stall-check-s",
        type=int,
        default=10,
        help="卡死检测轮询间隔（秒）",
    )
    ap.add_argument(
        "--seg-stall-cpu-min-jiffies",
        type=int,
        default=5,
        help="判定“CPU 不增长”的最小 jiffies 增量阈值（越小越敏感）",
    )
    ap.add_argument(
        "--seg-max-runtime-s",
        type=int,
        default=0,
        help="单段硬超时（秒，0=关闭）。到点直接终止该段，防止无限拖延。",
    )
    args = ap.parse_args()

    segs = _read_jsonl(Path(args.segments))
    segs = [s for s in segs if isinstance(s, dict) and str(s.get("id") or "").strip() and str(s.get("video") or "").strip()]
    if not segs:
        raise SystemExit("segments empty or missing id/video")

    base_cfg = _load_yaml(Path(args.base_config))
    exp_cfg = _load_yaml(Path(args.experiments))
    baseline = (exp_cfg.get("baseline") or {}) if isinstance(exp_cfg.get("baseline"), dict) else {}
    exp_defs = (exp_cfg.get("experiments") or {}) if isinstance(exp_cfg.get("experiments"), dict) else {}
    reuse_cfg = (exp_cfg.get("reuse") or {}) if isinstance(exp_cfg.get("reuse"), dict) else {}
    freeze_asr = bool(reuse_cfg.get("freeze_asr_for_non_asr_experiments", False))
    reuse_method = str(reuse_cfg.get("method") or "").strip()

    # NOTE: overrides in experiments.yaml are defined as "delta relative to baseline" (see eval docs + example yaml).
    # Therefore each experiment run should inherit baseline overrides, then apply its own overrides on top.
    plan: List[Tuple[str, Dict[str, Any]]] = []
    base_name = str(baseline.get("name") or "baseline")
    base_overrides = dict(baseline.get("overrides") or {})
    plan.append((base_name, dict(base_overrides)))
    for name, spec in exp_defs.items():
        if not isinstance(spec, dict):
            continue
        merged = dict(base_overrides)
        merged.update(dict(spec.get("overrides") or {}))
        plan.append((str(name), merged))

    args.out_root.mkdir(parents=True, exist_ok=True)
    meta = {"segments": str(Path(args.segments)), "base_config": str(Path(args.base_config)), "experiments": str(Path(args.experiments))}
    (args.out_root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for exp_name, overrides in plan:
        exp_root = args.out_root / exp_name
        exp_root.mkdir(parents=True, exist_ok=True)
        (exp_root / "overrides.json").write_text(json.dumps(overrides, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[exp] {exp_name} segments={len(segs)}", flush=True)
        for s in segs:
            sid = str(s.get("id") or "").strip()
            v = Path(str(s.get("video") or ""))
            out_dir = exp_root / sid
            if _is_done_success(out_dir):
                continue
            print(f"  [seg] {sid} -> {out_dir}", flush=True)
            # Fast resume: if ASR artifacts already exist in this output directory AND this experiment
            # doesn't change ASR-related knobs, resume from MT to avoid rerunning WhisperX.
            # This is especially important for retrying failed runs where ASR already succeeded.
            overrides_eff = dict(overrides or {})
            if "resume_from" not in overrides_eff and (not _looks_like_asr_override(overrides_eff)):
                try:
                    if (out_dir / "audio.json").exists() and (out_dir / "audio.wav").exists() and (out_dir / "chs.srt").exists():
                        overrides_eff["resume_from"] = "mt"
                except Exception:
                    pass
            # Best practice: when experiment doesn't touch ASR knobs, optionally reuse baseline ASR outputs to reduce variance.
            if exp_name != base_name and freeze_asr and reuse_method == "baseline_asr" and (not _looks_like_asr_override(overrides_eff)):
                base_seg = (args.out_root / base_name / sid)
                if base_seg.exists():
                    _prepare_reuse_baseline_asr(base_seg, out_dir)
                    # quality_pipeline requires audio.json for resume_from=mt
                    # Run from MT stage (ASR frozen).
                    overrides2 = dict(overrides_eff)
                    overrides2["resume_from"] = "mt"
                    rc, dur_s, tail = _run_one(
                        v,
                        out_dir,
                        base_cfg,
                        overrides2,
                        stall_timeout_s=int(args.seg_stall_timeout_s),
                        stall_check_s=int(args.seg_stall_check_s),
                        stall_cpu_min_jiffies=int(args.seg_stall_cpu_min_jiffies),
                        max_runtime_s=int(args.seg_max_runtime_s),
                    )
                else:
                    rc, dur_s, tail = _run_one(
                        v,
                        out_dir,
                        base_cfg,
                        overrides_eff,
                        stall_timeout_s=int(args.seg_stall_timeout_s),
                        stall_check_s=int(args.seg_stall_check_s),
                        stall_cpu_min_jiffies=int(args.seg_stall_cpu_min_jiffies),
                        max_runtime_s=int(args.seg_max_runtime_s),
                    )
            else:
                rc, dur_s, tail = _run_one(
                    v,
                    out_dir,
                    base_cfg,
                    overrides_eff,
                    stall_timeout_s=int(args.seg_stall_timeout_s),
                    stall_check_s=int(args.seg_stall_check_s),
                    stall_cpu_min_jiffies=int(args.seg_stall_cpu_min_jiffies),
                    max_runtime_s=int(args.seg_max_runtime_s),
                )
            (out_dir / "e2e_status.json").write_text(
                json.dumps({"id": sid, "video": str(v), "return_code": rc, "duration_s": round(dur_s, 3)}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if rc != 0:
                (out_dir / "e2e_error_tail.txt").write_text(tail, encoding="utf-8")
                print(f"    [warn] seg failed rc={rc}", flush=True)

    print(f"[ok] done out_root={args.out_root}")


if __name__ == "__main__":
    main()


