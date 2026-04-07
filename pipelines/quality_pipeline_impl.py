#!/usr/bin/env python3
"""
Quality pipeline: WhisperX + local LLM + Coqui/Piper TTS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
import time
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# Ensure prints are flushed when stdout/stderr are piped (e.g. spawned by backend).
# Without this, progress/log streaming can appear stuck at 0% until the process exits.
try:  # pragma: no cover
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except Exception:
    try:
        os.environ["PYTHONUNBUFFERED"] = "1"
    except Exception:
        pass

try:
    import torch  # type: ignore
except Exception:  # pragma: no cover
    torch = None  # type: ignore

# Ensure project root (/app) is on sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipelines.lib.text.asr_normalize import load_asr_dict, normalize_asr_zh_text
from pipelines.lib.text.zh_convert import zh_to_simplified
from pipelines.lib.text.en_replace import apply_en_replacements, load_en_dict
from pipelines.lib.glossary.entity_protect import restore_entities
from pipelines.lib.media.ffmpeg_mux import mux_video_audio
from pipelines.lib.glossary.glossary import apply_glossary_to_segments, load_glossary
from pipelines.lib.asr.lite_asr import extract_audio, write_srt
from pipelines.lib.tts.lite_tts import (
    build_coqui_tts,
    clean_tts_text,
    save_audio,
    synthesize_segments,
    synthesize_segments_coqui,
)
from pipelines.lib.mt.mt_split import split_translation_by_src_lengths
from pipelines.lib.text.srt_io import read_srt_texts
from pipelines.lib.media.subtitle_display import build_display_items  # screen-friendly subtitle track
from pipelines.lib.media.media_probe import ffprobe_display_wh
from pipelines.lib.media.subtitles_burn import burn_subtitles
from pipelines.lib.text.zh_text import clean_zh_text

try:  # pragma: no cover
    from pydub import AudioSegment  # type: ignore
except Exception:  # pragma: no cover
    AudioSegment = None  # type: ignore


# ----------------------
# Data structures
# ----------------------
@dataclass
class Segment:
    start: float
    end: float
    text: str
    translation: Optional[str] = None
    # Optional TTS script (separate from subtitle translation). When present, TTS should prefer this.
    tts: Optional[str] = None


@dataclass
class ZhPolishStageOptions:
    resume_from: Optional[str]
    allow_zh_polish: bool
    review_enabled: bool
    stop_after: Optional[str]
    zh_phrase_enable: bool
    zh_post_polish_requested: bool
    zh_gate_min_high_risk: int
    zh_gate_min_total_suspects: int
    zh_gate_on_phrase_error: bool


@dataclass
class ZhPolishArtifacts:
    phrase_items: List[Dict[str, Any]]
    suspects: List[Dict[str, Any]]
    gate_summary: Dict[str, Any]


_ASR_TAIL_PROMO_RE = re.compile(
    r"(点赞|订阅|关注|打赏|支持|转发|收藏|评论|感谢观看|谢谢观看|下期再见|关注我们|频道|栏目"
    r"|thanks\s+for\s+watching|thank\s+you\s+for\s+watching|like\s+and\s+subscribe)",
    re.IGNORECASE,
)


def _normalize_asr_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", "", text).lower()


def _sanitize_asr_segments(segments_raw: List[Dict[str, Any]], audio_total_s: Optional[float]) -> List[Dict[str, Any]]:
    if not segments_raw:
        return []
    if audio_total_s is None or audio_total_s <= 0:
        return segments_raw

    cleaned: List[Dict[str, Any]] = []
    clamped = 0
    dropped = 0

    for idx, seg in enumerate(segments_raw, start=1):
        start = float(seg.get("start", 0.0) or 0.0)
        end = float(seg.get("end", start) or start)
        text = str(seg.get("text", "") or "").strip()
        overflow_s = max(0.0, end - audio_total_s)
        remaining_s = max(0.0, audio_total_s - start)
        seg_duration_s = max(0.0, end - start)
        tail_window = start >= max(0.0, audio_total_s - 20.0)
        text_norm = _normalize_asr_text(text)
        text_chars = len(re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text_norm))
        looks_like_promo = bool(_ASR_TAIL_PROMO_RE.search(text_norm))
        severe_overflow = overflow_s > max(1.2, min(8.0, remaining_s * 0.35 + 1.0))
        very_long_tail = seg_duration_s > max(12.0, remaining_s + 4.0) and text_chars >= 16

        if tail_window and overflow_s > 0.8 and (looks_like_promo or (severe_overflow and very_long_tail)):
            dropped += 1
            preview = text[:48] + ("..." if len(text) > 48 else "")
            print(
                f"  [warn] 丢弃疑似尾部幻听片段 idx={idx} "
                f"start={start:.2f}s end={end:.2f}s overflow={overflow_s:.2f}s text={preview!r}"
            )
            continue

        if end > audio_total_s:
            end = audio_total_s
            clamped += 1

        cleaned.append(
            {
                "start": max(0.0, start),
                "end": max(start, end),
                "text": text,
            }
        )

    if clamped or dropped:
        print(
            f"  [info] ASR 尾段清洗完成：clamped={clamped} dropped={dropped} "
            f"audio_dur={audio_total_s:.2f}s"
        )
    return cleaned


@dataclass
class ZhPostPolishArtifacts:
    llm_lines_by_idx: Dict[int, str]
    llm_meta_items: List[Dict[str, Any]]


@dataclass
class MtSourcePreparation:
    override_path: Optional[Path]
    context_src_lines: List[str]


# Per-run LLM contract telemetry. Kept in-memory and periodically flushed to disk so
# short stop_after runs can still expose what happened before the pipeline paused.
def _new_contract_stats_template() -> Dict[str, Dict[str, Any]]:
    return {
        "mt": {
            "requests": 0,
            "contract_retry": 0,
            "adaptive_splits": 0,
            "fallback_legacy_format": 0,
            "contract_invalid": 0,
            "success_chunks": 0,
            "syntactic_repair": 0,
        },
        "zh_opt": {
            "requests": 0,
            "contract_retry": 0,
            "adaptive_splits": 0,
            "fallback_legacy_format": 0,
            "contract_invalid": 0,
            "success_chunks": 0,
            "syntactic_repair": 0,
        },
    }


_LLM_CONTRACT_STATS: Dict[str, Dict[str, Any]] = _new_contract_stats_template()
_STRUCTURED_OUTPUT_CAP_CACHE: Dict[str, bool] = {}


# ----------------------
# Helpers
# ----------------------
def _bump_contract_stat(stage: str, key: str, delta: int = 1) -> None:
    try:
        bucket = _LLM_CONTRACT_STATS.setdefault(stage, {})
        bucket[key] = int(bucket.get(key, 0) or 0) + int(delta or 0)
    except Exception:
        pass


def _contract_stats_snapshot() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for stage, data in (_LLM_CONTRACT_STATS or {}).items():
        if isinstance(data, dict):
            out[str(stage)] = {str(k): int(v) if isinstance(v, (bool, int, float)) else v for k, v in data.items()}
    return out


def _load_contract_stats_seed(output_dir: Path) -> Dict[str, Dict[str, Any]]:
    try:
        p = Path(output_dir) / "llm_contract_metrics.json"
        if not p.exists():
            return {}
        obj = json.loads(p.read_text(encoding="utf-8", errors="ignore") or "{}")
        stats = obj.get("stats") if isinstance(obj, dict) else None
        return stats if isinstance(stats, dict) else {}
    except Exception:
        return {}


def _reset_contract_stats(seed: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
    global _LLM_CONTRACT_STATS
    merged = _new_contract_stats_template()
    if isinstance(seed, dict):
        for stage, data in seed.items():
            if not isinstance(data, dict):
                continue
            bucket = merged.setdefault(str(stage), {})
            for key, value in data.items():
                try:
                    bucket[str(key)] = int(value or 0)
                except Exception:
                    bucket[str(key)] = value
    _LLM_CONTRACT_STATS = merged


def _write_contract_stats(output_dir: Path, *, stage: str = "") -> None:
    try:
        payload = {
            "version": 1,
            "updated_at": round(time.time(), 3),
            "stage": str(stage or ""),
            "stats": _contract_stats_snapshot(),
        }
        (Path(output_dir) / "llm_contract_metrics.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _strip_chs_review_markers(s: str) -> str:
    """
    Defensive cleanup for user-review SRT text.

    Some UI/tooling may accidentally append audit markers (e.g. "【已校审】") into subtitle text.
    These markers must NEVER enter MT, otherwise they pollute translations and downstream TTS.
    """
    t = str(s or "")
    # Common audit markers (exact tokens)
    for tok in ("【已校审】", "[已校审]", "(已校审)", "（已校审）"):
        if tok in t:
            t = t.replace(tok, "")
    # Collapse whitespace introduced by removals
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _enforce_min_duration_inplace(segments: List[Any], min_duration: float, safety_gap: float = 0.2) -> List[Any]:
    """
    Ensure each segment has at least `min_duration` seconds by extending `end` into available gaps.

    This preserves the original segment objects (and any extra fields like `tts`).
    """
    if not segments:
        return segments
    for i, seg in enumerate(segments):
        try:
            start = float(getattr(seg, "start"))
            end = float(getattr(seg, "end"))
        except Exception:
            continue
        duration = end - start
        if i < len(segments) - 1:
            try:
                next_start = float(getattr(segments[i + 1], "start"))
            except Exception:
                next_start = end + min_duration
            headroom = max(0.0, next_start - safety_gap - end)
        else:
            headroom = min_duration
        if duration < min_duration:
            need = min_duration - duration
            extend_by = min(need, headroom)
            try:
                setattr(seg, "end", end + extend_by)
            except Exception:
                pass
    return segments


def check_dep(args=None):
    missing = []
    tts_backend = ""
    try:
        tts_backend = str(getattr(args, "tts_backend", "") or "").strip().lower()
    except Exception:
        tts_backend = ""
    asr_engine = ""
    try:
        asr_engine = str(getattr(args, "asr_engine", "faster-whisper") or "faster-whisper").strip().lower()
    except Exception:
        asr_engine = "faster-whisper"

    # torch is required for the Coqui TTS backend (and some other ML components).
    # Piper/CLI backends do not require torch.
    if tts_backend in {"coqui", "coqui-tts", "xtts", "vits"}:
        if torch is None:
            missing.append("torch 缺失（Coqui TTS 需要）：pip install torch")

    if asr_engine in {"sensevoice", "sensevoice-small", "sensevoice_small"}:
        try:
            from funasr import AutoModel  # type: ignore  # noqa: F401
        except Exception as exc:  # pragma: no cover
            missing.append(f"funasr 缺失或导入失败（SenseVoiceSmall 需要）：{exc}")
    else:
        try:
            from faster_whisper import WhisperModel  # type: ignore  # noqa: F401
        except Exception as exc:  # pragma: no cover
            missing.append(f"faster-whisper 缺失或导入失败（ASR 需要）：{exc}")

    # WhisperX is only required when alignment is enabled on faster-whisper path.
    align_enable = False
    try:
        align_enable = bool(getattr(args, "asr_align_enable", False))
    except Exception:
        align_enable = False
    if align_enable and asr_engine not in {"sensevoice", "sensevoice-small", "sensevoice_small"}:
        try:
            import whisperx  # type: ignore  # noqa: F401
        except Exception as exc:  # pragma: no cover
            missing.append(f"whisperx 缺失或导入失败（用于对齐）：{exc}")
    return missing


def _apply_zh_glossary_inplace(segments: List[Segment], glossary: List[Dict[str, Any]] | None) -> int:
    """
    Apply ZH->ZH glossary corrections (rules center asr_fixes) directly onto seg.text.
    This is intended to run BEFORE zh phrase extraction so spans are extracted from the corrected text.
    """
    if not segments or not glossary:
        return 0
    pairs: List[tuple[str, str]] = []
    for term in glossary:
        if not isinstance(term, dict):
            continue
        src = str((term or {}).get("src") or "").strip()
        tgt = str((term or {}).get("tgt") or "").strip()
        if not src or not tgt:
            continue
        # Heuristic: treat CJK targets as "Chinese correction" terms.
        if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", tgt):
            pairs.append((src, tgt))
    if not pairs:
        return 0
    hits = 0
    for seg in segments:
        z = str(getattr(seg, "text", "") or "")
        if not z:
            continue
        z2 = z
        for src, tgt in pairs:
            if src in z2:
                z2 = z2.replace(src, tgt)
        if z2 != z:
            hits += 1
            seg.text = z2
    return hits


def _build_zh_polish_stage_options(args: argparse.Namespace) -> ZhPolishStageOptions:
    resume_from = getattr(args, "resume_from", None)
    allow_zh_polish = resume_from in {None, "asr"}
    review_enabled = bool(getattr(args, "review_enabled", False)) and allow_zh_polish
    stop_after = getattr(args, "stop_after", None)
    zh_phrase_enable = bool(
        allow_zh_polish and (getattr(args, "zh_phrase_enable", False) or review_enabled or stop_after == "zh_polish")
    )
    # Review gate and zh_post_polish are separate concerns:
    # - review_enabled: decide whether the pipeline may pause before MT
    # - zh_post_polish_enable: whether to spend extra LLM calls rewriting suspect Chinese lines
    # Gate/smoke flows often want the former without paying for the latter.
    zh_post_polish_requested = bool(allow_zh_polish and getattr(args, "zh_post_polish_enable", False))
    return ZhPolishStageOptions(
        resume_from=resume_from,
        allow_zh_polish=allow_zh_polish,
        review_enabled=review_enabled,
        stop_after=stop_after,
        zh_phrase_enable=zh_phrase_enable,
        zh_post_polish_requested=zh_post_polish_requested,
        zh_gate_min_high_risk=max(0, int(getattr(args, "zh_gate_min_high_risk", 1) or 0)),
        zh_gate_min_total_suspects=max(0, int(getattr(args, "zh_gate_min_total_suspects", 6) or 0)),
        zh_gate_on_phrase_error=bool(getattr(args, "zh_gate_on_phrase_error", False)),
    )


def _print_zh_polish_stage_banner(opts: ZhPolishStageOptions, pipe_total: int) -> None:
    print(
        f"[3/{pipe_total}] zh_polish: 短语/span 检测 + 受约束中文修复 + 审核门禁 "
        f"(zh_phrase={'on' if opts.zh_phrase_enable else 'off'}, review_gate={'on' if opts.review_enabled else 'off'}, "
        f"resume_from={opts.resume_from or 'full'})"
    )


def _run_asr_stage(
    args: argparse.Namespace,
    *,
    pipe_total: int,
    audio_pcm: Path,
    audio_json: Path,
    chs_srt: Path,
    chs_norm_srt: Path,
    glossary: List[Dict[str, Any]] | None,
) -> List[Segment]:
    print(f"[1/{pipe_total}] Extracting audio...")
    # Quality 模式支持去噪：沿用共享 extract_audio 的安全逻辑（若未提供 arnndn 模型则回退 anlmdn）
    extract_audio(
        args.video,
        audio_pcm,
        sample_rate=args.sample_rate,
        denoise=bool(getattr(args, "denoise", False)),
        denoise_model=getattr(args, "denoise_model", None),
    )
    audio_total_ms = None
    try:
        if AudioSegment is not None:
            audio_total_ms = float(len(AudioSegment.from_file(audio_pcm)))
    except Exception:
        audio_total_ms = None

    asr_engine = str(getattr(args, "asr_engine", "faster-whisper") or "faster-whisper").strip().lower()
    print(f"[2/{pipe_total}] Running ASR ({asr_engine})...")
    audio_total_s = (audio_total_ms / 1000.0) if isinstance(audio_total_ms, (int, float)) and audio_total_ms else None
    if asr_engine in {"sensevoice", "sensevoice-small", "sensevoice_small"}:
        if bool(getattr(args, "asr_align_enable", False)):
            print("  [warn] SenseVoiceSmall 实验档暂不走 WhisperX 对齐，已忽略 asr_align_enable。")
        segments = run_sensevoice_asr(
            audio_path=audio_pcm,
            model_id=str(getattr(args, "sensevoice_model", "") or "FunAudioLLM/SenseVoiceSmall"),
            device=getattr(args, "whisperx_device", "auto"),
            model_dir=getattr(args, "sensevoice_model_dir", None),
            audio_total_s=audio_total_s,
        )
    else:
        segments = run_whisperx(
            audio_path=audio_pcm,
            model_id=args.whisperx_model,
            device=getattr(args, "whisperx_device", "auto"),
            model_dir=args.whisperx_model_dir,
            diarization=args.diarization,
            align_enable=bool(getattr(args, "asr_align_enable", False)),
            audio_total_s=audio_total_s,
        )
    # Low-risk ASR normalization (best-effort). This runs on ASR output only (not on review overrides).
    asr_dict = load_asr_dict(getattr(args, "asr_normalize_dict", None)) if getattr(args, "asr_normalize_enable", False) else {}
    for seg in segments:
        seg.text = normalize_asr_zh_text(seg.text, to_simplified_fn=zh_to_simplified, asr_dict=asr_dict)
    # Apply rules center ZH->ZH fixes BEFORE splitting/phrase extraction so downstream sees corrected Chinese.
    _apply_zh_glossary_inplace(segments, glossary)
    # Split overly long ASR segments for better subtitles and more reliable translation.
    segments = split_segments_for_subtitles(segments, max_chars=args.max_sentence_len)
    print(f"[2/{pipe_total}] Subtitle segments after split: {len(segments)} (max_sentence_len={args.max_sentence_len})")
    segments = _enforce_min_duration_inplace(segments, min_duration=args.min_sub_dur)
    audio_json.write_text(json.dumps([seg.__dict__ for seg in segments], ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(chs_srt, segments, text_attr="text")
    # P0: deterministic zh normalization artifact (same as current seg.text at this point)
    try:
        write_srt(chs_norm_srt, segments, text_attr="text")
    except Exception:
        pass
    return segments


def _load_segments_for_resume(
    args: argparse.Namespace,
    *,
    audio_json: Path,
    chs_srt: Path,
    chs_norm_srt: Path,
    glossary: List[Dict[str, Any]] | None,
) -> List[Segment]:
    def _parse_srt_segments(path: Path) -> List[Segment]:
        raw = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
        blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
        items: List[Segment] = []

        def _ts_to_seconds(ts: str) -> float:
            ts = str(ts or "").strip()
            if "," in ts:
                hhmmss, ms = ts.split(",", 1)
            else:
                hhmmss, ms = ts, "0"
            hh, mm, ss = [int(x) for x in hhmmss.split(":")]
            millis = int(ms[:3].ljust(3, "0"))
            return float(hh * 3600 + mm * 60 + ss) + (float(millis) / 1000.0)

        for block in blocks:
            lines = [ln.rstrip() for ln in block.split("\n")]
            if len(lines) < 2:
                continue
            time_line = lines[1] if "-->" in lines[1] else (lines[0] if "-->" in lines[0] else "")
            if "-->" not in time_line:
                continue
            left, right = [part.strip() for part in time_line.split("-->", 1)]
            start_s = _ts_to_seconds(left)
            end_s = _ts_to_seconds(right.split(" ", 1)[0].strip())
            text_lines = lines[2:] if time_line == lines[1] else lines[1:]
            text = "\n".join(x.strip() for x in text_lines if x.strip()).strip()
            if not text:
                continue
            items.append(Segment(start=start_s, end=end_s, text=text))
        return items

    if audio_json.exists():
        data = json.loads(audio_json.read_text(encoding="utf-8"))
        segments = [Segment(**item) for item in data]
    elif chs_srt.exists():
        print("[resume] audio.json missing; rebuilding resume segments from chs.srt")
        segments = _parse_srt_segments(chs_srt)
        if not segments:
            sys.exit("resume_from=mt 但缺少可用的 audio.json，且无法从 chs.srt 重建分段")
        try:
            audio_json.write_text(json.dumps([seg.__dict__ for seg in segments], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    else:
        sys.exit("resume_from=mt 但缺少 audio.json")
    asr_dict = load_asr_dict(getattr(args, "asr_normalize_dict", None)) if getattr(args, "asr_normalize_enable", False) else {}
    for seg in segments:
        seg.text = normalize_asr_zh_text(seg.text, to_simplified_fn=zh_to_simplified, asr_dict=asr_dict)
    # Apply rules center ZH->ZH fixes BEFORE phrase extraction in resume flows too.
    _apply_zh_glossary_inplace(segments, glossary)
    if not chs_srt.exists():
        write_srt(chs_srt, segments, text_attr="text")
    # Keep chs.norm.srt in sync for review workflows.
    if not chs_norm_srt.exists():
        try:
            write_srt(chs_norm_srt, segments, text_attr="text")
        except Exception:
            pass
    return segments


def _prepare_initial_segments(
    args: argparse.Namespace,
    *,
    pipe_total: int,
    audio_pcm: Path,
    audio_json: Path,
    chs_srt: Path,
    chs_norm_srt: Path,
    glossary: List[Dict[str, Any]] | None,
) -> List[Segment]:
    if args.resume_from is None or args.resume_from == "asr":
        return _run_asr_stage(
            args,
            pipe_total=pipe_total,
            audio_pcm=audio_pcm,
            audio_json=audio_json,
            chs_srt=chs_srt,
            chs_norm_srt=chs_norm_srt,
            glossary=glossary,
        )
    return _load_segments_for_resume(
        args,
        audio_json=audio_json,
        chs_srt=chs_srt,
        chs_norm_srt=chs_norm_srt,
        glossary=glossary,
    )


def _maybe_pause_after_zh_polish(
    opts: ZhPolishStageOptions,
    *,
    gate_summary: Dict[str, Any],
    zh_phrase_error: str,
    suspects_n: int,
) -> None:
    # Product policy:
    # - Once review is enabled, we ALWAYS pause before MT after zh_polish finishes.
    # - Risk thresholds still matter for telemetry/UI severity, but not for whether we pause.
    if opts.review_enabled:
        if zh_phrase_error:
            print(f"[warn] zh_gate: phrase extraction error (will still pause for review): {zh_phrase_error}")
        print(
            "[gate] zh_gate summary: "
            f"total={gate_summary.get('total_suspects')} "
            f"high={gate_summary.get('high_risk_suspects')} "
            f"medium={gate_summary.get('medium_risk_suspects')} "
            f"pause_reasons={gate_summary.get('pause_reasons') or []}"
        )
        print("[gate] zh_gate: paused before MT (review_enabled=true)")
        sys.exit(3)
    # Batch barrier: stop after zh_polish even when no suspects (front-end will decide resume).
    if opts.stop_after == "zh_polish":
        print(f"[gate] stop_after=zh_polish -> paused (suspects={suspects_n})")
        sys.exit(3)


def _apply_review_gate_policy(gate_summary: Dict[str, Any], *, review_enabled: bool) -> Dict[str, Any]:
    out = dict(gate_summary or {})
    reasons = [str(x).strip() for x in (out.get("pause_reasons") or []) if str(x).strip()]
    if review_enabled:
        if "review_enabled" not in reasons:
            reasons.insert(0, "review_enabled")
        out["should_pause"] = True
    out["pause_reasons"] = reasons
    return out


def _collect_zh_polish_artifacts(
    segments: List[Segment],
    *,
    spans_by_idx: Dict[int, List[Dict[str, Any]]],
    rule_reasons_by_idx: Dict[int, List[str]],
    zh_phrase_error: str,
    zh_phrase_enable: bool,
    min_high_risk: int,
    min_total_suspects: int,
    pause_on_phrase_error: bool,
) -> ZhPolishArtifacts:
    phrase_items: List[Dict[str, Any]] = []
    suspects: List[Dict[str, Any]] = []
    for i, seg in enumerate(segments, 1):
        idx = int(i)
        line = str(seg.text or "")
        spans = spans_by_idx.get(idx, [])
        rule_rr = rule_reasons_by_idx.get(idx, [])
        if spans:
            phrase_items.append({"idx": idx, "text": line, "spans": spans})
        # suspects if: any spans, or rule reasons
        if spans or rule_rr:
            suspects.append({"idx": idx, "text": line, "spans": spans, "rule_reasons": rule_rr})
    gate_summary = _build_zh_gate_summary(
        suspects,
        phrase_error=zh_phrase_error,
        min_high_risk=min_high_risk,
        min_total_suspects=min_total_suspects,
        pause_on_phrase_error=pause_on_phrase_error,
    )
    return ZhPolishArtifacts(
        phrase_items=phrase_items,
        suspects=suspects,
        gate_summary=gate_summary,
    )


def _write_zh_polish_artifacts(
    *,
    chs_phrases_json: Path,
    chs_suspects_json: Path,
    artifacts: ZhPolishArtifacts,
    zh_phrase_error: str,
    zh_polish_enabled: bool,
    review_gate_enabled: bool,
    zh_opt_enabled: bool,
) -> None:
    try:
        chs_phrases_json.write_text(
            json.dumps(
                {
                    "items": artifacts.phrase_items,
                    "meta": {
                        "phrase_extraction_error": zh_phrase_error,
                        "optimization_error": zh_phrase_error,
                        "zh_polish_enabled": zh_polish_enabled,
                        "review_gate_enabled": review_gate_enabled,
                        "zh_opt_enabled": zh_opt_enabled,
                        # Legacy compatibility: keep the old key aligned with the real stage state.
                        "zh_phrase_enable": zh_polish_enabled,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    try:
        chs_suspects_json.write_text(
            json.dumps(
                {
                    "items": artifacts.suspects,
                    "meta": {
                        "phrase_extraction_error": zh_phrase_error,
                        "optimization_error": zh_phrase_error,
                        "zh_polish_enabled": zh_polish_enabled,
                        "review_gate_enabled": review_gate_enabled,
                        "zh_opt_enabled": zh_opt_enabled,
                        "zh_phrase_enable": zh_polish_enabled,
                        "zh_gate_summary": artifacts.gate_summary,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _collect_polish_target_indexes(suspects: List[Dict[str, Any]], *, max_lines: int = 6) -> Set[int]:
    """
    Select a SMALL subset of suspects for P2 constrained zh polish.

    Product intent:
    - P1 phrase/span extraction can stay broad for review visibility.
    - P2 local LLM rewrite must stay narrow on weak hardware.
    - Prefer obviously high-value cases:
      1) high-risk suspects
      2) rule-based anomalies
      3) deterministic dict hits
    """
    cap = max(0, int(max_lines or 0))
    if cap <= 0:
        return set()

    high_rule_reasons = {
        "乱码/异常字符",
        "疑似ASR脏词/生造词",
        "疑似不通顺搭配",
        "疑似动宾搭配异常",
        "疑似动词缺失/错置",
        "短句但含异常词",
    }
    repeated_llm_texts: Dict[str, int] = {}
    for it in suspects or []:
        line = str(it.get("text") or "")
        for sp in (it.get("spans") or []):
            if not isinstance(sp, dict):
                continue
            txt = str(sp.get("text") or "").strip()
            src = str(sp.get("source") or "")
            if src not in {"llm", "spell"} or len(txt) < 2 or len(txt) > 8:
                continue
            if txt in line:
                repeated_llm_texts[txt] = repeated_llm_texts.get(txt, 0) + line.count(txt)
    repeated_llm_texts = {k: v for k, v in repeated_llm_texts.items() if int(v) >= 2}

    ranked: List[tuple[tuple[int, int, int, int, int], int]] = []
    for it in suspects or []:
        try:
            idx = int(it.get("idx"))
        except Exception:
            continue
        if idx <= 0:
            continue
        spans = [sp for sp in (it.get("spans") or []) if isinstance(sp, dict)]
        rule_rr = [str(x or "").strip() for x in (it.get("rule_reasons") or []) if str(x or "").strip()]
        severity = _suspect_severity(it)
        is_high = 1 if severity == "high" else 0
        has_rule_anomaly = 1 if any(rr in high_rule_reasons for rr in rule_rr) else 0
        has_any_rule = 1 if rule_rr else 0
        has_repeated_llm = 1 if any(str(sp.get("text") or "").strip() in repeated_llm_texts for sp in spans if isinstance(sp, dict)) else 0
        has_llm_span = 1 if any(str(sp.get("source") or "") in {"llm", "spell"} for sp in spans) else 0
        has_non_idiom_dict = 1 if any(
            str(sp.get("source") or "") == "dict" and "idiom_dict" not in [str(x or "") for x in (sp.get("reasons") or [])]
            for sp in spans
            if isinstance(sp, dict)
        ) else 0
        has_high_rewrite_span = 1 if any(
            str(sp.get("risk") or "").lower().startswith("h")
            and not (
                str(sp.get("source") or "") == "dict"
                and "idiom_dict" in [str(x or "") for x in (sp.get("reasons") or [])]
            )
            for sp in spans
            if isinstance(sp, dict)
        ) else 0
        high_span_n = sum(1 for sp in spans if str(sp.get("risk") or "").lower().startswith("h"))
        total_span_n = len(spans)

        # Skip pure idiom-only prompt noise. Keep P2 focused on source repair / consistency value.
        if not (has_rule_anomaly or has_any_rule or has_repeated_llm or has_high_rewrite_span or has_non_idiom_dict or has_llm_span):
            continue

        score = (
            has_rule_anomaly,
            has_repeated_llm,
            has_high_rewrite_span,
            has_non_idiom_dict,
            has_llm_span,
            min(high_span_n, 9),
            min(total_span_n, 9),
            is_high,
        )
        # Prefer earlier subtitle rows when score ties: they are usually more user-visible in short clips.
        ranked.append((score, idx))

    ranked.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2], -x[0][3], -x[0][4], -x[0][5], -x[0][6], x[1]))
    return {idx for _score, idx in ranked[:cap]}


def _request_zh_post_polish_lines(
    args: argparse.Namespace,
    *,
    segments: List[Segment],
    spans_by_idx: Dict[int, List[Dict[str, Any]]],
    rule_reasons_by_idx: Dict[int, List[str]],
    polish_idxs: Set[int],
    zh_post_polish_enable: bool,
) -> Dict[int, str]:
    llm_lines_by_idx: Dict[int, str] = {}
    if not zh_post_polish_enable:
        return llm_lines_by_idx
    try:
        # Prefer cheap local repairs first; unresolved lines fall through to the LLM.
        locked_inputs: List[tuple[int, str]] = []
        notes_by_idx: Dict[int, str] = {}
        hints_by_idx: Dict[int, List[str]] = {}
        same_pinyin_path = str(getattr(args, "zh_phrase_same_pinyin_path", "") or "")
        lexicon_path = str(getattr(args, "zh_repair_lexicon_path", "") or "")
        proper_nouns_path = str(getattr(args, "zh_repair_proper_nouns_path", "") or "")
        for i, seg in enumerate(segments, 1):
            idx = int(i)
            if idx not in polish_idxs:
                continue
            line = str(seg.text or "")
            auto_opt, hints = _pick_local_zh_repair(
                line=line,
                spans=spans_by_idx.get(idx, []),
                rule_reasons=rule_reasons_by_idx.get(idx, []),
                same_pinyin_path=same_pinyin_path,
                lexicon_path=lexicon_path,
                proper_nouns_path=proper_nouns_path,
            )
            if auto_opt and auto_opt.strip() != line.strip():
                llm_lines_by_idx[idx] = auto_opt.strip()
                continue
            note_parts: List[str] = []
            reasons = [str(x or "").strip() for x in (rule_reasons_by_idx.get(idx, []) or []) if str(x or "").strip()]
            if hints:
                hints_by_idx[idx] = list(hints)
            if reasons:
                note_parts.append("reasons=" + "/".join(reasons[:3]))
            if hints:
                note_parts.append("local_hints=" + " ; ".join(hints[:3]))
            if note_parts:
                notes_by_idx[idx] = " | ".join(note_parts)
            locked_inputs.append((idx, line))
        # chunk polish requests
        chunk_n = int(getattr(args, "zh_phrase_chunk_lines", 20) or 20)
        for j in range(0, len(locked_inputs), max(1, chunk_n)):
            chunk = locked_inputs[j : j + max(1, chunk_n)]
            try:
                got = _constrained_zh_polish_llm(
                    endpoint=args.llm_endpoint,
                    model=args.llm_model,
                    api_key=args.llm_api_key,
                    items=chunk,
                    notes_by_idx=notes_by_idx,
                    request_timeout_s=max(30, int(getattr(args, "zh_opt_request_timeout_s", 180) or 180)),
                    request_retries=max(0, int(getattr(args, "zh_opt_request_retries", 2) or 2)),
                )
                for idx, opt in (got or {}).items():
                    idx_i = int(idx)
                    opt_s = str(opt or "").strip()
                    base_line = str(segments[idx_i - 1].text or "") if 1 <= idx_i <= len(segments) else ""
                    if not _should_accept_llm_polish(
                        base=base_line,
                        opt=opt_s,
                        rule_reasons=rule_reasons_by_idx.get(idx_i, []),
                        local_hints=hints_by_idx.get(idx_i, []),
                        same_pinyin_path=same_pinyin_path,
                        lexicon_path=lexicon_path,
                        proper_nouns_path=proper_nouns_path,
                    ):
                        continue
                    llm_lines_by_idx[idx_i] = opt_s
            except Exception as exc:
                print(f"[warn] zh post-polish chunk failed (lines={len(chunk)}), retrying line-by-line: {exc}")
                for one in chunk:
                    try:
                        got1 = _constrained_zh_polish_llm(
                            endpoint=args.llm_endpoint,
                            model=args.llm_model,
                            api_key=args.llm_api_key,
                            items=[one],
                            notes_by_idx=notes_by_idx,
                            request_timeout_s=max(30, int(getattr(args, "zh_opt_request_timeout_s", 180) or 180)),
                            request_retries=max(0, int(getattr(args, "zh_opt_request_retries", 2) or 2)),
                        )
                        for idx, opt in (got1 or {}).items():
                            idx_i = int(idx)
                            opt_s = str(opt or "").strip()
                            base_line = str(segments[idx_i - 1].text or "") if 1 <= idx_i <= len(segments) else ""
                            if not _should_accept_llm_polish(
                                base=base_line,
                                opt=opt_s,
                                rule_reasons=rule_reasons_by_idx.get(idx_i, []),
                                local_hints=hints_by_idx.get(idx_i, []),
                                same_pinyin_path=same_pinyin_path,
                                lexicon_path=lexicon_path,
                                proper_nouns_path=proper_nouns_path,
                            ):
                                continue
                            llm_lines_by_idx[idx_i] = opt_s
                    except Exception as exc_one:
                        print(f"[warn] zh post-polish single-line retry failed (idx={int(one[0])}): {exc_one}")
    except Exception as exc:
        print(f"[warn] zh post-polish failed, falling back to norm: {exc}")
    return llm_lines_by_idx


def _apply_zh_post_polish_results(
    *,
    segments: List[Segment],
    spans_by_idx: Dict[int, List[Dict[str, Any]]],
    rule_reasons_by_idx: Dict[int, List[str]],
    polish_idxs: Set[int],
    llm_lines_by_idx: Dict[int, str],
    zh_post_polish_enable: bool,
) -> ZhPostPolishArtifacts:
    llm_meta_items: List[Dict[str, Any]] = []
    # Materialize chs.llm.srt and update in-memory segments.text for downstream MT.

    def _change_kind(base: str, opt: str, spans: List[Dict[str, Any]], rule_reasons: List[str]) -> str:
        base_s = str(base or "").strip()
        opt_s = str(opt or "").strip()
        if base_s == opt_s:
            return "none"
        base_no_punct = re.sub(r"[，。！？；：、,.!?;:\s]", "", base_s)
        opt_no_punct = re.sub(r"[，。！？；：、,.!?;:\s]", "", opt_s)
        if base_no_punct == opt_no_punct:
            return "punctuation_fix"
        if any(str(rr or "").strip() in {"疑似专名/称谓一致性"} for rr in (rule_reasons or [])):
            return "consistency_fix"
        if any(bool((sp.get("meta") or {}).get("repeated_occurrence")) for sp in (spans or []) if isinstance(sp, dict)):
            return "consistency_fix"
        if any(
            str(rr or "").strip()
            in {"乱码/异常字符", "疑似ASR脏词/生造词", "疑似不通顺搭配", "疑似动宾搭配异常", "疑似动词缺失/错置", "短句但含异常词"}
            for rr in (rule_reasons or [])
        ):
            return "asr_fix"
        return "rewrite"

    for i, seg in enumerate(segments, 1):
        idx = int(i)
        base = str(seg.text or "")
        opt_raw = llm_lines_by_idx.get(idx, "")
        opt = opt_raw
        if opt:
            # strip lock tags if any (legacy compatibility; newer calls pass raw lines)
            opt2 = re.sub(r"<<LOCK\d+>>", "", opt)
            opt2 = re.sub(r"<</LOCK\d+>>", "", opt2)
            opt2 = re.sub(r"\s+", " ", opt2).strip()
            final = opt2 or base
            seg.text = final
            changed = str(final).strip() != str(base).strip()
            change_kind = _change_kind(base, final, spans_by_idx.get(idx, []), rule_reasons_by_idx.get(idx, []))
            llm_meta_items.append(
                {
                    "idx": idx,
                    "base": base,
                    "opt": final,
                    "spans": spans_by_idx.get(idx, []),
                    "need_review": bool(spans_by_idx.get(idx) or rule_reasons_by_idx.get(idx)),
                    "rule_reasons": rule_reasons_by_idx.get(idx, []),
                    "confidence": None,
                    "changed": changed,
                    "polished": bool(zh_post_polish_enable and idx in polish_idxs and changed),
                    "polish_attempted": bool(zh_post_polish_enable and idx in polish_idxs),
                    "change_kind": change_kind,
                }
            )
        else:
            seg.text = base
            llm_meta_items.append(
                {
                    "idx": idx,
                    "base": base,
                    "opt": base,
                    "spans": spans_by_idx.get(idx, []),
                    "need_review": bool(spans_by_idx.get(idx) or rule_reasons_by_idx.get(idx)),
                    "rule_reasons": rule_reasons_by_idx.get(idx, []),
                    "confidence": None,
                    "changed": False,
                    "skipped": True,
                    "polished": False,
                    "polish_attempted": bool(zh_post_polish_enable and idx in polish_idxs),
                    "change_kind": "none",
                }
            )
    return ZhPostPolishArtifacts(
        llm_lines_by_idx=llm_lines_by_idx,
        llm_meta_items=llm_meta_items,
    )


def _extract_zh_phrase_spans_for_segments(
    args: argparse.Namespace,
    *,
    segments: List[Segment],
    rule_reasons_by_idx: Dict[int, List[str]],
    pipe_total: int,
) -> tuple[Dict[int, List[Dict[str, Any]]], str]:
    spans_by_idx: Dict[int, List[Dict[str, Any]]] = {}
    zh_phrase_error = ""
    items_all: List[tuple[int, str]] = [(i + 1, clean_zh_text(str(segments[i].text or ""))) for i in range(len(segments))]
    candidate_max = max(0, int(getattr(args, "zh_phrase_candidate_max_lines", 0) or 0))
    include_idxs = list(rule_reasons_by_idx.keys())
    items_pick = (
        _pick_phrase_candidate_items(items_all, max_lines=candidate_max, include_idxs=include_idxs)
        if candidate_max > 0
        else items_all
    )
    phrase_model = str(getattr(args, "zh_phrase_llm_model", "") or "").strip() or str(getattr(args, "llm_model", "") or "").strip()
    phrase_endpoint = str(getattr(args, "zh_phrase_llm_endpoint", "") or "").strip() or str(getattr(args, "llm_endpoint", "") or "").strip()
    llm_api_key = str(getattr(args, "llm_api_key", "") or "")
    max_spans = max(1, int(getattr(args, "zh_phrase_max_spans", 3) or 3))
    max_total = max(1, int(getattr(args, "zh_phrase_max_total", 30) or 30))
    chunk_n = max(1, int(getattr(args, "zh_phrase_chunk_lines", 8) or 8))
    second_pass = not bool(getattr(args, "no_zh_phrase_second_pass", False))

    if items_pick:
        print(
            f"  [3/{pipe_total}][P1] zh_phrase_extract: model={phrase_model or getattr(args, 'llm_model', '')} "
            f"candidates={len(items_pick)}/{len(items_all)} chunk_lines={chunk_n} max_spans={max_spans}"
        )
    try:
        for j in range(0, len(items_pick), chunk_n):
            chunk = items_pick[j : j + chunk_n]
            if not chunk:
                continue
            got = _extract_zh_risky_spans_llm_two_pass(
                endpoint=phrase_endpoint,
                model=phrase_model,
                api_key=llm_api_key,
                items=chunk,
                max_spans_per_line=max_spans,
                max_total_spans=max_total,
                second_pass=second_pass,
                second_pass_max_lines=min(5, max(2, chunk_n // 3 or 2)),
                second_pass_trigger_min_spans=1,
                log_enabled=True,
                log_prefix=f"  [3/{pipe_total}][P1]",
            )
            for k, v in (got or {}).items():
                if not v:
                    continue
                idx = int(k)
                line = str(items_all[idx - 1][1]) if 1 <= idx <= len(items_all) else ""
                vv: List[Dict[str, Any]] = []
                for sp in (v or []):
                    if not isinstance(sp, dict):
                        continue
                    sp2 = dict(sp)
                    sp2.setdefault("source", "llm")
                    sp2.setdefault("reasons", ["llm_extract"])
                    sp2.setdefault(
                        "confidence",
                        0.7 if str(sp2.get("risk") or "").lower().startswith("h") else 0.6,
                    )
                    vv.append(sp2)
                if vv:
                    spans_by_idx[idx] = _merge_dedupe_spans_same_line(line, vv, max_spans=max_spans)
    except Exception as exc:
        zh_phrase_error = f"{type(exc).__name__}: {exc}"

    idiom_enable = bool(getattr(args, "zh_phrase_idiom_enable", False))
    idioms4 = _load_idioms_4char(str(getattr(args, "zh_phrase_idiom_path", "") or "")) if idiom_enable else set()
    homo_map = _load_same_pinyin_char_map(str(getattr(args, "zh_phrase_same_pinyin_path", "") or "")) if idiom_enable else {}
    for i, seg in enumerate(segments, 1):
        idx = int(i)
        line = str(getattr(seg, "text", "") or "")
        llm_spans = [dict(x) for x in (spans_by_idx.get(idx, []) or [])]
        dict_spans: List[Dict[str, Any]] = []
        if idioms4:
            dict_spans += _idiom_spans_from_line(line, idioms4)
            if homo_map:
                dict_spans += _idiom_spans_from_line_fuzzy(line, idioms4=idioms4, homo_map=homo_map)
        pattern_spans = _pattern_spans_from_line(line) if not llm_spans else []
        merged = llm_spans + dict_spans + pattern_spans
        if merged:
            spans_by_idx[idx] = _merge_dedupe_spans_same_line(line, merged, max_spans=max_spans)

    spans_by_idx = _diffuse_repeated_llm_spans_across_segments(segments, spans_by_idx)
    spans_by_idx = _cap_repeated_spans_by_text(spans_by_idx, max_occ=1, sources={"pattern"})

    if bool(getattr(args, "zh_phrase_force_one_per_line", False)):
        forced_n = 0
        for i, seg in enumerate(segments, 1):
            idx = int(i)
            if spans_by_idx.get(idx):
                continue
            sp = _force_span_from_line(str(getattr(seg, "text", "") or ""))
            if sp:
                spans_by_idx[idx] = [sp]
                forced_n += 1
        print(f"  [3/{pipe_total}][P1] force_one_per_line: forced_lines={forced_n}/{len(segments)}")
    return spans_by_idx, zh_phrase_error


def _apply_zh_optimize_results(
    *,
    segments: List[Segment],
    rule_reasons_by_idx: Dict[int, List[str]],
    llm_items_by_idx: Dict[int, Dict[str, Any]],
    optimization_enabled: bool,
) -> ZhPostPolishArtifacts:
    llm_lines_by_idx: Dict[int, str] = {}
    llm_meta_items: List[Dict[str, Any]] = []
    for i, seg in enumerate(segments, 1):
        idx = int(i)
        base = clean_zh_text(str(seg.text or ""))
        item = dict(llm_items_by_idx.get(idx) or {})
        has_item = bool(item)
        opt = clean_zh_text(str(item.get("opt") or "").strip()) or base
        changed = bool(item.get("changed")) and opt != base
        if changed and optimization_enabled:
            seg.text = opt
            llm_lines_by_idx[idx] = opt
        else:
            seg.text = base
            opt = base
            changed = False
        risk = _normalize_zh_opt_risk(item.get("risk")) if has_item else "low"
        reasons = _normalize_zh_opt_reasons(item.get("reasons"))
        need_review = (bool(item.get("need_review")) if has_item else False) or risk == "high"
        try:
            confidence = float(item.get("confidence")) if has_item else 1.0
        except Exception:
            confidence = 0.0
        llm_meta_items.append(
            {
                "idx": idx,
                "base": base,
                "opt": opt,
                "changed": changed,
                "risk": risk,
                "need_review": need_review,
                "reasons": reasons,
                "rule_reasons": list(rule_reasons_by_idx.get(idx, []) or []),
                "confidence": confidence,
                "polished": bool(optimization_enabled and changed),
            }
        )
    return ZhPostPolishArtifacts(llm_lines_by_idx=llm_lines_by_idx, llm_meta_items=llm_meta_items)


def _write_zh_post_polish_artifacts(
    *,
    chs_llm_srt: Path,
    chs_llm_json: Path,
    segments: List[Segment],
    artifacts: ZhPostPolishArtifacts,
) -> None:
    try:
        write_srt(chs_llm_srt, segments, text_attr="text")
    except Exception:
        pass
    try:
        chs_llm_json.write_text(json.dumps({"items": artifacts.llm_meta_items}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _should_run_mt_stage(args: argparse.Namespace) -> bool:
    return args.resume_from is None or args.resume_from in {"asr", "mt"}


def _resolve_chs_mt_override_path(args: argparse.Namespace, output_dir: Path) -> Optional[Path]:
    raw_ov = str(getattr(args, "chs_override_srt", "") or "").strip()
    if raw_ov:
        p = Path(raw_ov)
        if p.exists():
            return p
    # Default location produced by the unified review UI.
    review_path = output_dir / "chs.review.srt"
    if review_path.exists():
        return review_path
    return None


def _prepare_mt_source_segments(
    args: argparse.Namespace,
    *,
    segments: List[Segment],
    output_dir: Path,
    chs_srt: Path,
) -> MtSourcePreparation:
    # Highest-priority MT input:
    # Use the user-reviewed Chinese subtitles as the final ASR result and feed them to MT.
    # This is NOT "extra hints" to the LLM; it replaces seg.text (the MT source).
    override_path = _resolve_chs_mt_override_path(args, output_dir)
    if override_path is not None:
        try:
            print(f"[mt] using chs override srt as MT source: {override_path}")
        except Exception:
            pass
        try:
            texts = read_srt_texts(override_path)
        except Exception:
            texts = []
        if texts:
            for i, seg in enumerate(segments):
                if i < len(texts):
                    t0 = _strip_chs_review_markers(texts[i])
                    if t0:
                        # Manual review override is the single source of truth for *this task*.
                        # Do NOT apply zh->zh replacement rules on top of it.
                        seg.text = zh_to_simplified(t0)
            # Keep chs.srt in sync with the actual MT source for later review/debug.
            # IMPORTANT: write sanitized texts (do NOT copy raw review file).
            try:
                write_srt(chs_srt, segments, text_attr="text")
            except Exception:
                pass
    return MtSourcePreparation(
        override_path=override_path,
        context_src_lines=[s.text for s in segments],
    )


def _restore_resume_translations(
    args: argparse.Namespace,
    *,
    segments: List[Segment],
    eng_srt: Path,
    en_dict: Optional[Dict[str, str]],
) -> List[Segment]:
    seg_en = segments
    # Restore translations for TTS when resuming from tts/mux.
    override = getattr(args, "eng_override_srt", None)
    eng_path = Path(override) if override else eng_srt
    if getattr(args, "resume_from", None) in {"tts", "mux"} and not eng_path.exists():
        raise RuntimeError(
            f"resume_from={getattr(args, 'resume_from', None)} requires an existing English subtitle file, "
            f"but not found: {eng_path}"
        )
    if eng_path.exists():
        texts = read_srt_texts(eng_path)
        for i, seg in enumerate(seg_en):
            if i < len(texts):
                seg.translation = texts[i]
            else:
                seg.translation = seg.translation or ""
        if en_dict:
            for seg in seg_en:
                seg.translation = apply_en_replacements(getattr(seg, "translation", "") or "", en_dict)
        # For manual ENG override (eng.review.srt), do NOT apply guardrail (respect user edits).
        # For base eng.srt resume, guardrail is safe and idempotent.
        if not override:
            for seg in seg_en:
                seg.translation = _final_en_guardrail(seg.translation or "", zh=str(getattr(seg, "text", "") or ""))
        # Keep eng.srt in sync for later embed
        try:
            if eng_path != eng_srt:
                eng_srt.write_text(eng_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass
    # P0: regenerate TTS script from the loaded subtitles (base or review override)
    for seg in seg_en:
        seg.tts = _build_tts_script(seg.translation or "")
    return seg_en


def _maybe_build_display_subtitles(
    args: argparse.Namespace,
    *,
    seg_en: List[Segment],
    display_srt: Path,
    display_meta_json: Path,
) -> None:
    if not (getattr(args, "display_srt_enable", False) or getattr(args, "display_use_for_embed", False)):
        return
    try:
        max_chars_per_line = int(getattr(args, "display_max_chars_per_line", 42) or 42)
        if bool(getattr(args, "display_use_for_embed", False)):
            box_w = None
            if bool(getattr(args, "sub_place_enable", False)):
                box_w = float(getattr(args, "sub_place_w", 0.0) or 0.0)
            elif bool(getattr(args, "erase_subtitle_enable", False)):
                box_w = float(getattr(args, "erase_subtitle_w", 0.0) or 0.0)
            wh = ffprobe_display_wh(Path(str(getattr(args, "video", "") or "")))
            font_size = max(10, int(getattr(args, "sub_font_size", 18) or 18))
            if wh and box_w and box_w > 0:
                play_w = int(wh[0])
                usable_w_px = max(120.0, float(play_w) * float(box_w) - font_size * 1.2)
                approx_char_px = max(9.0, font_size * 0.90)
                estimated_chars = int(max(16, min(max_chars_per_line, usable_w_px // approx_char_px)))
                if estimated_chars < max_chars_per_line:
                    print(
                        f"[p0] display_srt_layout: max_chars_per_line {max_chars_per_line} -> {estimated_chars} "
                        f"(play_w={play_w} box_w={float(box_w):.3f} font={font_size})"
                    )
                    max_chars_per_line = estimated_chars
        src = [(float(s.start), float(s.end), str(s.translation or "")) for s in seg_en]
        items, meta = build_display_items(
            src=src,
            max_chars_per_line=max_chars_per_line,
            max_lines=int(getattr(args, "display_max_lines", 2) or 2),
            merge_enable=bool(getattr(args, "display_merge_enable", False)),
            merge_max_gap_s=float(getattr(args, "display_merge_max_gap_s", 0.25) or 0.25),
            merge_max_chars=int(getattr(args, "display_merge_max_chars", 80) or 80),
            split_enable=bool(getattr(args, "display_split_enable", False)),
            split_max_chars=int(getattr(args, "display_split_max_chars", 86) or 86),
        )
        disp_segs: List[Segment] = [Segment(start=it.start, end=it.end, text="", translation=it.text) for it in items]
        write_srt(display_srt, disp_segs, text_attr="translation")
        try:
            display_meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        print(f"[p0] display_srt: enabled, items={len(disp_segs)}, out={display_srt.name}")
    except Exception as exc:
        print(f"[warn] display_srt generation failed, continue without it: {exc}")


def _resolve_audio_total_ms(audio_pcm: Path) -> Optional[float]:
    try:
        if AudioSegment is not None and audio_pcm.exists():
            return float(len(AudioSegment.from_file(audio_pcm)))
    except Exception:
        return None
    return None


def _run_tts_stage(
    args: argparse.Namespace,
    *,
    seg_en: List[Segment],
    work_tts: Path,
    tts_wav: Path,
    audio_total_ms: Optional[float],
    pipe_total: int,
) -> None:
    stage_t0 = time.perf_counter()
    seg_count = len(seg_en or [])
    text_chars = sum(len(str(getattr(seg, "tts", None) or seg.translation or "")) for seg in (seg_en or []))
    print(
        f"[5/{pipe_total}] Synthesizing TTS with {args.tts_backend}..."
        f" coqui_device={getattr(args, 'coqui_device', 'auto')}"
    )
    # Robustness: if ASR/MT produced no segments (e.g. silent/tonal test media),
    # do NOT fail the whole pipeline. Generate silence and still produce deliverables.
    if not seg_en:
        if AudioSegment is None:
            raise RuntimeError("No subtitle segments and pydub unavailable; cannot generate silent TTS.")
        dur_ms = int(round(float(audio_total_ms) if audio_total_ms is not None else 1000.0))
        dur_ms = max(dur_ms, 300)
        silent_t0 = time.perf_counter()
        combined_audio = AudioSegment.silent(duration=dur_ms).set_frame_rate(int(args.sample_rate or 16000))
        save_t0 = time.perf_counter()
        save_audio(combined_audio, tts_wav, sample_rate=args.sample_rate)
        print(
            f"[5/{pipe_total}] tts_timing: mode=silence segs=0 audio_ms={dur_ms} "
            f"build_s=0.000 synth_s={(save_t0 - silent_t0):.3f} save_s={(time.perf_counter() - save_t0):.3f} "
            f"total_s={(time.perf_counter() - stage_t0):.3f}"
        )
        return
    if args.tts_backend == "piper":
        synth_t0 = time.perf_counter()
        combined_audio = synthesize_segments(
            seg_en,
            model_path=args.piper_model,
            work_dir=work_tts,
            piper_bin=args.piper_bin,
            allow_speed_change=True,
            split_len=args.tts_split_len,
            max_speed=args.tts_speed_max,
            align_mode=str(getattr(args, "tts_align_mode", "resample") or "resample"),
            pad_to_ms=audio_total_ms,
        )
        build_s = 0.0
        synth_s = time.perf_counter() - synth_t0
    else:
        build_t0 = time.perf_counter()
        tts = build_coqui_tts(model_name=args.coqui_model, device=args.coqui_device)
        build_s = time.perf_counter() - build_t0
        speaker = None
        language = None
        speaker_wav = None
        try:
            m = str(getattr(args, "coqui_model", "") or "")
            if "xtts" in m.lower():
                language = "en"
                speaker_wav = getattr(tts, "_ygf_default_speaker_wav", None) or None
        except Exception:
            pass
        synth_t0 = time.perf_counter()
        combined_audio = synthesize_segments_coqui(
            seg_en,
            tts=tts,
            work_dir=work_tts,
            sample_rate=args.sample_rate,
            speaker=speaker,
            speaker_wav=speaker_wav,
            language=language,
            split_len=args.tts_split_len,
            max_speed=args.tts_speed_max,
            align_mode=str(getattr(args, "tts_align_mode", "resample") or "resample"),
            pad_to_ms=audio_total_ms,
        )
        synth_s = time.perf_counter() - synth_t0
    save_t0 = time.perf_counter()
    save_audio(combined_audio, tts_wav, sample_rate=args.sample_rate)
    save_s = time.perf_counter() - save_t0
    total_s = time.perf_counter() - stage_t0
    print(
        f"[5/{pipe_total}] tts_timing: backend={args.tts_backend} segs={seg_count} text_chars={text_chars} "
        f"build_s={build_s:.3f} synth_s={synth_s:.3f} save_s={save_s:.3f} total_s={total_s:.3f}"
    )


def _run_mux_stage(
    args: argparse.Namespace,
    *,
    tts_wav: Path,
    video_dub: Path,
    pipe_total: int,
) -> None:
    stage_t0 = time.perf_counter()
    print(f"[6/{pipe_total}] Muxing video with new audio...")
    erase_enable = bool(getattr(args, "erase_subtitle_enable", False))
    erase_w = float(getattr(args, "erase_subtitle_w", 1.0) or 0.0)
    erase_h = float(getattr(args, "erase_subtitle_h", 0.22) or 0.0)

    mux_video_audio(
        args.video,
        tts_wav,
        video_dub,
        sync_strategy=str(getattr(args, "mux_sync_strategy", "slow") or "slow"),
        slow_max_ratio=float(getattr(args, "mux_slow_max_ratio", 1.08) or 1.08),
        threshold_s=float(getattr(args, "mux_slow_threshold_s", 0.05) or 0.05),
        erase_subtitle_enable=erase_enable,
        erase_subtitle_method=str(getattr(args, "erase_subtitle_method", "delogo") or "delogo"),
        erase_subtitle_coord_mode=str(getattr(args, "erase_subtitle_coord_mode", "ratio") or "ratio"),
        erase_subtitle_x=float(getattr(args, "erase_subtitle_x", 0.0) or 0.0),
        erase_subtitle_y=float(getattr(args, "erase_subtitle_y", 0.78) or 0.78),
        erase_subtitle_w=erase_w if erase_w else float(getattr(args, "erase_subtitle_w", 1.0) or 1.0),
        erase_subtitle_h=erase_h if erase_h else float(getattr(args, "erase_subtitle_h", 0.22) or 0.22),
        erase_subtitle_blur_radius=int(getattr(args, "erase_subtitle_blur_radius", 12) or 12),
    )
    if not video_dub.exists():
        raise RuntimeError(f"mux failed: {video_dub} not created")
    print(f"[6/{pipe_total}] mux_timing: total_s={(time.perf_counter() - stage_t0):.3f} out={video_dub.name}")


def _run_embed_stage(
    args: argparse.Namespace,
    *,
    eng_srt: Path,
    display_srt: Path,
    video_dub: Path,
    video_sub: Path,
    pipe_total: int,
) -> bool:
    stage_t0 = time.perf_counter()
    print(f"[7/{pipe_total}] Embedding subtitles...")
    print(f"[8/{pipe_total}] Done.")
    srt_to_burn = display_srt if (getattr(args, "display_use_for_embed", False) and display_srt.exists()) else eng_srt
    # Robustness: when there is nothing to burn (empty SRT), keep a valid "subbed" deliverable
    # by copying the dubbed video as-is.
    try:
        burn_texts = read_srt_texts(srt_to_burn) if srt_to_burn.exists() else []
    except Exception:
        burn_texts = []
    if not burn_texts:
        try:
            copy_t0 = time.perf_counter()
            shutil.copyfile(video_dub, video_sub)
            print(f"[warn] embed skipped (empty srt): copied {video_dub.name} -> {video_sub.name}")
            print(
                f"[7/{pipe_total}] embed_timing: mode=copy empty_srt=1 "
                f"copy_s={(time.perf_counter() - copy_t0):.3f} total_s={(time.perf_counter() - stage_t0):.3f}"
            )
        except Exception as exc:
            raise RuntimeError(f"embed skipped but copy failed: {exc}") from exc
        return False
    # Placement precedence:
    # 1) If user explicitly enabled subtitle placement, respect that rectangle.
    # 2) Otherwise, when erase is enabled, mirror the erase rectangle so the new subtitle
    #    lands in the cleared area.
    place_enable = bool(getattr(args, "sub_place_enable", False))
    place_coord_mode = str(getattr(args, "sub_place_coord_mode", "ratio") or "ratio")
    place_x = float(getattr(args, "sub_place_x", 0.0) or 0.0)
    place_y = float(getattr(args, "sub_place_y", 0.78) or 0.78)
    place_w = float(getattr(args, "sub_place_w", 1.0) or 1.0)
    place_h = float(getattr(args, "sub_place_h", 0.22) or 0.22)
    if not place_enable and bool(getattr(args, "erase_subtitle_enable", False)):
        place_enable = True
        place_coord_mode = str(getattr(args, "erase_subtitle_coord_mode", "ratio") or "ratio")
        place_x = float(getattr(args, "erase_subtitle_x", 0.0) or 0.0)
        place_y = float(getattr(args, "erase_subtitle_y", 0.78) or 0.78)
        place_w = float(getattr(args, "erase_subtitle_w", 1.0) or 1.0)
        place_h = float(getattr(args, "erase_subtitle_h", 0.22) or 0.22)
    print(
        f"[7/{pipe_total}] subtitle_burn_layout: "
        f"source={'sub_place' if bool(getattr(args, 'sub_place_enable', False)) else ('erase_rect' if bool(getattr(args, 'erase_subtitle_enable', False)) else 'default')} "
        f"place_enable={place_enable} coord={place_coord_mode} x={place_x} y={place_y} w={place_w} h={place_h} "
        f"font={int(getattr(args, 'sub_font_size', 18) or 18)}"
    )
    burn_t0 = time.perf_counter()
    burn_subtitles(
        video_dub,
        srt_to_burn,
        video_sub,
        font_name=str(getattr(args, "sub_font_name", "Arial") or "Arial"),
        font_size=int(getattr(args, "sub_font_size", 18) or 18),
        outline=int(getattr(args, "sub_outline", 1) or 1),
        shadow=int(getattr(args, "sub_shadow", 0) or 0),
        margin_v=int(getattr(args, "sub_margin_v", 24) or 24),
        alignment=int(getattr(args, "sub_alignment", 2) or 2),
        place_enable=place_enable,
        place_coord_mode=place_coord_mode,
        place_x=place_x,
        place_y=place_y,
        place_w=place_w,
        place_h=place_h,
    )
    print(
        f"[7/{pipe_total}] embed_timing: mode=burn srt={srt_to_burn.name} items={len(burn_texts)} "
        f"burn_s={(time.perf_counter() - burn_t0):.3f} total_s={(time.perf_counter() - stage_t0):.3f}"
    )
    return True


def _print_final_outputs(
    *,
    output_dir: Path,
    audio_json: Path,
    chs_srt: Path,
    eng_srt: Path,
    bi_srt: Path,
    tts_wav: Path,
    video_dub: Path,
    video_sub: Path,
) -> None:
    print("Done.")
    print(f"Outputs in: {output_dir}")
    print(f"- ASR JSON:   {audio_json}")
    print(f"- CHS SRT:    {chs_srt}")
    print(f"- ENG SRT:    {eng_srt}")
    print(f"- BI  SRT:    {bi_srt}")
    print(f"- TTS audio:  {tts_wav}")
    print(f"- Video dub:  {video_dub}")
    print(f"- Video+sub:  {video_sub}")


def translate_segments_llm(
    segments: List[Segment],
    endpoint: str,
    model: str,
    api_key: str,
    chunk_size: int = 2,
    *,
    context_window: int = 0,
    style_hint: str = "",
    max_words_per_line: int = 0,
    compact_enable: bool = False,
    compact_aggressive: bool = False,
    compact_temperature: float = 0.1,
    compact_max_tokens: int = 96,
    compact_timeout_s: int = 120,
    long_zh_chars: int = 60,
    long_en_words: int = 22,
    long_target_words: int = 18,
    prompt_mode: str = "short",  # short|long
    prompt_profile: str = "",
    two_pass_enable: bool = True,
    long_fallback_enable: bool = True,
    long_fallback_max_lines: int = 0,
    long_fallback_max_ratio: float = 0.0,
    long_examples_enable: bool = True,
    glossary: Optional[List[Dict[str, Any]]] = None,
    selfcheck_enable: bool = False,
    selfcheck_max_lines: int = 10,
    selfcheck_max_ratio: float = 0.25,
    context_src_lines: Optional[List[str]] = None,
    mt_reasoning_effort: str = "",
    request_timeout_s: int = 120,
    request_retries: int = 2,
) -> List[Segment]:
    """Chunked translation using OpenAI-compatible /v1/chat/completions."""
    if not segments:
        return segments
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Apply rules center ZH->ZH fixes BEFORE translation so downstream sees corrected Chinese.
    try:
        _apply_zh_glossary_inplace(segments, glossary)
    except Exception:
        pass

    # MT-only pre-normalization (low-risk):
    # - Replace common Chinese nickname patterns ("X哥/姐/爷...") with neutral references to prevent
    #   transliteration outputs like "Bro Cong"/"Sung Ge".
    def _neutralize_zh_nicknames(s: str) -> str:
        t = str(s or "")
        if not t:
            return t
        # Replace common short nickname tokens ("X哥/姐/总/哥们") even when followed by CJK text.
        # Avoid kinship words like 哥哥/姐姐.
        pat = re.compile(r"([\u4e00-\u9fff]{1,4})(哥们|哥|姐|爷|叔|婶|妹|弟|总)")

        def repl(m: re.Match) -> str:
            suf = str(m.group(2) or "")
            end = int(m.end())
            nxt = t[end : end + 1]
            if suf == "哥" and nxt == "哥":
                return m.group(0)
            if suf == "姐" and nxt == "姐":
                return m.group(0)
            return "那个人"

        return pat.sub(repl, t)

    try:
        for seg in segments:
            seg.text = _neutralize_zh_nicknames(str(getattr(seg, "text", "") or ""))
    except Exception:
        pass

    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i : i + n]

    out: List[Segment] = []

    def list_models() -> List[str]:
        try:
            r = requests.get(f"{endpoint}/models", headers=headers, timeout=30)
            if r.status_code != 200:
                return []
            data = r.json() or {}
            items = data.get("data") or []
            ids = []
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    ids.append(str(it["id"]))
            return ids
        except Exception:
            return []

    def post_chat(body: dict) -> requests.Response:
        """
        Ollama runners can occasionally crash under memory pressure (500 + EOF).
        We retry with backoff to allow the server to restart the runner.
        """
        openai_timeout_s = max(30, int(request_timeout_s or 120))
        openai_retries = max(1, int(request_retries or 2))

        last_exc: Exception | None = None
        for attempt in range(openai_retries):
            try:
                resp = requests.post(f"{endpoint}/chat/completions", json=body, headers=headers, timeout=openai_timeout_s)
                # 500 with runner crash is often transient; allow retry
                if resp.status_code == 500 and "runner" in (resp.text or "").lower():
                    raise RuntimeError(resp.text)
                # Thinking-capable models (e.g. Qwen3/Qwen3.5) may spend most of a small token budget
                # on the reasoning trace and return an empty final content. If that happens, retry once
                # with a larger max_tokens to ensure we get the final answer in message.content.
                if resp.status_code == 200:
                    try:
                        data = resp.json() or {}
                        msg = (data.get("choices") or [{}])[0].get("message") or {}
                        content = (msg.get("content") or "").strip()
                        reasoning = (msg.get("reasoning") or msg.get("thinking") or "").strip()
                        mt = int(body.get("max_tokens") or 0)
                        if (not content) and reasoning and mt and mt < 512:
                            body2 = dict(body)
                            body2["max_tokens"] = max(512, mt * 4)
                            resp2 = requests.post(f"{endpoint}/chat/completions", json=body2, headers=headers, timeout=openai_timeout_s)
                            if resp2.status_code == 200:
                                d2 = resp2.json() or {}
                                m2 = (d2.get("choices") or [{}])[0].get("message") or {}
                                if (m2.get("content") or "").strip():
                                    return resp2
                    except Exception:
                        pass
                return resp
            except Exception as exc:
                last_exc = exc
                sleep_s = 1.0 * (2**attempt)
                print(
                    f"[warn] LLM request failed (attempt {attempt+1}/{openai_retries}, "
                    f"timeout={openai_timeout_s}s): {exc}. Retrying in {sleep_s:.1f}s"
                )
                time.sleep(sleep_s)
        raise RuntimeError(
            "LLM request failed repeatedly.\n"
            "This can happen when the local Ollama service is not started yet, or the runner crashes under memory pressure.\n"
            f"- endpoint: {endpoint}\n- model: {body.get('model')}\n"
            "Standalone app note:\n"
            "- In this project, Ollama may be loaded/started manually via the desktop app UI.\n"
            "- Please ensure the Ollama service is running and the model is loaded, then verify:\n"
            f"  curl.exe {endpoint}/models\n"
            f"Last error: {last_exc}"
        )

    def _build_mt_format_schema(expected_count: int) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "array",
                    "minItems": int(expected_count),
                    "maxItems": int(expected_count),
                    "items": {"type": "string"},
                }
            },
            "required": ["lines"],
            "additionalProperties": False,
        }

    def _parse_mt_contract(content: Any, expected_count: int) -> Optional[List[str]]:
        raw = _strip_json_fence(str(content or ""))
        if not raw:
            return None
        data = _load_json_candidate(raw, allow_repair=True)
        # Some OpenAI-compatible servers occasionally return a JSON string that itself contains
        # the actual contract object. Accept that double-encoded form as long as the inner object matches.
        if isinstance(data, str):
            data = _load_json_candidate(data, allow_repair=True)
        if not isinstance(data, dict):
            return None
        items = data.get("lines")
        if not isinstance(items, list) or len(items) != int(expected_count):
            return None
        # Preferred contract: {"lines":["...", "..."]}. Keep backward-compat parsing for
        # legacy object arrays so older cached runners do not hard-fail mid-transition.
        if all(isinstance(item, str) for item in items):
            return [re.sub(r"\s+", " ", str(item or "").strip()).strip() for item in items]
        mapped = [""] * int(expected_count)
        seen: set[int] = set()
        for item in items:
            if not isinstance(item, dict):
                return None
            try:
                idx = int(item.get("id"))
            except Exception:
                return None
            if idx < 1 or idx > int(expected_count) or idx in seen:
                return None
            seen.add(idx)
            en = re.sub(r"\s+", " ", str(item.get("en") or "").strip()).strip()
            mapped[idx - 1] = en
        if seen != set(range(1, int(expected_count) + 1)):
            return None
        return mapped

    def _ensure_mt_success(resp: requests.Response, body: Dict[str, Any]) -> requests.Response:
        if resp.status_code == 200:
            return resp
        if resp.status_code == 404 and "not found" in (resp.text or "").lower():
            available = list_models()
            raise RuntimeError(
                f"LLM translation failed: {resp.status_code} {resp.text}\n"
                f"- configured: {body.get('model')}\n"
                f"- available: {available if available else '(failed to list models)'}"
            )
        raise RuntimeError(f"LLM translation failed: {resp.status_code} {resp.text}")

    def _selfcheck_lines(zh_lines: List[str], en_lines: List[str], *, ctx_blocks: Optional[List[str]] = None) -> List[str]:
        """
        LLM-only self-check for a very small set of user-visible deal-breakers.
        Implemented per-line to reduce cross-line contamination; if a request fails, keep the original line.
        """
        if not zh_lines or not en_lines:
            return en_lines
        fixed: List[str] = []
        ctx_blocks = ctx_blocks or []
        _taboo_re = re.compile(r"\b(hemorrhoid|hemorrhoids|anus|anal|penis|vagina)\b", re.IGNORECASE)
        for i, (zh, en) in enumerate(zip(zh_lines, en_lines)):
            zh = (zh or "").strip()
            en0 = (en or "").strip()
            if not zh or not en0:
                fixed.append(en0)
                continue
            ctx = (ctx_blocks[i] if i < len(ctx_blocks) else "").strip()
            prompt_parts = [
                "You are reviewing ONE subtitle translation.",
                "Only rewrite if the English line has ONE of these critical issues:",
                "1) fragment / incomplete sentence,",
                "2) POV drift (I/we/me/our/us not supported by the Chinese),",
                "3) wrong spatial relation (on/in/into/inside flipped),",
                "4) explicit vulgar/body-humor literal that must be softened.",
                "If it's OK, output the EXACT SAME line as provided.",
                "Rules:",
                "- ENGLISH ONLY.",
                "- ONE LINE ONLY.",
                "- No numbering/bullets/extra commentary.",
                "- Avoid switching POV (I/we) unless explicitly present in Chinese.",
                "- Keep spatial relations faithful (for example: on/in/into/inside must not be flipped).",
                "- Output a COMPLETE sentence (not a fragment).",
                "- Do NOT rewrite for style only.",
                "- For exaggerated/comic narration or vulgar slang, rewrite into CLEAN comedic narration (PG-13).",
                "- Do NOT use explicit sexual/bodily terms (hemorrhoid/anus/anal/penis/vagina). Rewrite to clean narration.",
            ]
            if ctx:
                prompt_parts += [f"[context]", ctx, "[/context]"]
            prompt_parts += [f"ZH: {zh}", f"EN: {en0}"]
            prompt = "\n".join(prompt_parts) + "\n"
            body = {
                "model": model,
                "messages": [{"role": "system", "content": "Subtitle translation quality reviewer."}, {"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 128,
                "options": _build_llm_options(),
            }
            try:
                resp = post_chat(body)
                if resp.status_code != 200:
                    fixed.append(en0)
                    continue
                content = (resp.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                s = str(content).strip()
                lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
                s = lines[0] if lines else ""
                s = re.sub(r"^\s*[-–•]+\s*", "", s)
                s = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s)
                s = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s).strip()
                s = re.sub(r"\s+", " ", s).strip()
                s2 = s or en0
                # If the model still outputs taboo/body-humor literals, do ONE extra cleanup pass.
                # This is rare and bounded by selfcheck caps, so the cost stays controlled.
                if _taboo_re.search(s2):
                    prompt2_parts = [
                        "Rewrite the English subtitle into CLEAN comedic narration (PG-13).",
                        "Hard rules:",
                        "- ENGLISH ONLY.",
                        "- ONE LINE ONLY.",
                        "- Do NOT use explicit sexual/bodily terms (including: hemorrhoid/anus/anal/penis/vagina).",
                        "- Keep POV and spatial relations faithful to the Chinese.",
                        "- Keep the meaning and comedic intent, but soften vulgar slang.",
                    ]
                    if ctx:
                        prompt2_parts += ["[context]", ctx, "[/context]"]
                    prompt2_parts += [f"ZH: {zh}", f"EN_BAD: {s2}", "EN_CLEAN:"]
                    body2 = {
                        "model": model,
                        "messages": [{"role": "system", "content": "Subtitle translation cleaner."}, {"role": "user", "content": "\n".join(prompt2_parts) + "\n"}],
                        "temperature": 0.0,
                        "max_tokens": 96,
                        "options": _build_llm_options(),
                    }
                    try:
                        resp2 = post_chat(body2)
                        if resp2.status_code == 200:
                            c2 = (resp2.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                            t2 = str(c2).strip()
                            ln2 = [ln.strip() for ln in t2.split("\n") if ln.strip()]
                            t2 = ln2[0] if ln2 else ""
                            t2 = re.sub(r"^\s*[-–•]+\s*", "", t2)
                            t2 = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", t2)
                            t2 = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", t2).strip()
                            t2 = re.sub(r"\s+", " ", t2).strip()
                            if t2 and (not _taboo_re.search(t2)):
                                s2 = t2
                    except Exception:
                        pass
                fixed.append(s2)
            except Exception:
                fixed.append(en0)
        return fixed

    def _needs_selfcheck(zh: str, en: str) -> bool:
        """
        Lightweight trigger for the bounded self-check pass.
        Only cover a tiny set of deal-breakers; do not widen this into a style-polish stage.
        """
        z = (zh or "").strip()
        e = (en or "").strip()
        if not z or not e:
            return False
        low = e.lower()
        # POV drift: I/we without explicit ZH signal.
        if re.search(r"\b(i|me|my|we|our|us)\b", low) and not re.search(r"[我咱们我们俺本人]", z):
            return True
        # Fragment-like openings (gerunds/subordinate clauses).
        if re.match(r"^(watching|seeing|looking|while|when|because|although|if|as)\b", low):
            return True
        # Explicit literal / body-humor artifacts that should be cleaned.
        if re.search(r"\b(hemorrhoid|hemorrhoids|anus|anal|penis|vagina)\b", low):
            return True
        # Concrete spatial relation flips (conservative).
        try:
            if re.search(r"(车上|桌上|墙上|门上|身上|头上|手上|背上|上面|上边|上方)", z) and re.search(r"\b(in|inside|into)\b", low) and (not re.search(r"\b(on|onto|atop)\b", low)):
                return True
            if re.search(r"(车里|屋里|包里|口袋里|里面|内侧|内部)", z) and re.search(r"\b(on|onto|atop)\b", low) and (not re.search(r"\b(in|inside|into)\b", low)):
                return True
        except Exception:
            pass
        # incomplete fragments (common failure mode)
        if e.endswith((",", ";", ":")):
            return True
        if re.search(r"\b(and|or|but|to|of|with|for)$", low):
            return True
        return False

    def _selfcheck_score(zh: str, en: str) -> int:
        """
        Rank which lines deserve self-check under a tight cap.
        Goal: prioritize four deal-breakers only:
        POV drift, fragments, spatial relation flips, and explicit body-humor literals.
        """
        z = (zh or "").strip()
        e = (en or "").strip()
        if not z or not e:
            return 0
        low = e.lower()
        score = 0
        # Highest priority: taboo / body-humor literals (very user-visible).
        if re.search(r"\b(hemorrhoid|hemorrhoids|anus|anal|penis|vagina)\b", low):
            score += 100
        # POV drift: I/we without explicit Chinese signal.
        if re.search(r"\b(i|me|my|we|our|us)\b", low) and not re.search(r"[我咱们我们俺本人]", z):
            score += 80
        # Spatial relation flips are factual and easy to get wrong.
        try:
            if re.search(r"(车上|桌上|墙上|门上|身上|头上|手上|背上|上面|上边|上方)", z) and re.search(r"\b(in|inside|into)\b", low) and (not re.search(r"\b(on|onto|atop)\b", low)):
                score += 60
            if re.search(r"(车里|屋里|包里|口袋里|里面|内侧|内部)", z) and re.search(r"\b(on|onto|atop)\b", low) and (not re.search(r"\b(in|inside|into)\b", low)):
                score += 60
        except Exception:
            pass
        # Fragment-like openings / dangling endings.
        if re.match(r"^(watching|seeing|looking|while|when|because|although|if|as)\b", low):
            score += 35
        if e.endswith((",", ";", ":")) or re.search(r"\b(and|or|but|to|of|with|for)$", low):
            score += 30
        return score

    def _ok_en_line(s: str) -> bool:
        t = (s or "").strip()
        if not t:
            return False
        if "\n" in t or "\r" in t:
            return False
        if len(t) > 180:
            return False
        low = t.lower()
        # common dangling function words / fragments
        if re.search(r"\b(and|or|but|to|of|with|for|on|in|at|into|from|that|which|because|though)$", low):
            return False
        # dangling articles / auxiliaries (very common truncation patterns)
        if re.search(r"\b(a|an|the|is|are|was|were|do|does|did|can|could|will|would|should|may|might)$", low.rstrip(".!?")):
            return False
        # one-word stubs like "In."
        words = [w for w in re.split(r"\s+", re.sub(r"[^A-Za-z\s']+", " ", t)) if w]
        if len(words) <= 1:
            return False
        return True

    # make context lines available (prefer original zh lines)
    ctx_lines = context_src_lines if (context_src_lines and len(context_src_lines) == len(segments)) else [s.text for s in segments]

    # Stability vs performance:
    # - If context_window > 0, we translate per-line (easier to provide per-line context blocks and reduces cross-line contamination).
    # - Self-check itself does NOT require per-line translation; we only self-check suspicious lines afterwards (bounded by caps).
    effective_chunk_size = 1 if (max(0, int(context_window or 0)) > 0) else max(1, int(chunk_size or 1))
    total_groups = (len(segments) + effective_chunk_size - 1) // effective_chunk_size if segments else 0

    for gi, group in enumerate(chunks(list(enumerate(segments)), effective_chunk_size), start=1):
        idxs = [i for i, _ in group]
        segs = [s for _, s in group]
        src_lines = [s.text.strip() for s in segs]
        cw = max(0, int(context_window or 0))
        ctx_blocks: List[str] = []
        ctx_blocks_sc: List[str] = []  # context blocks for selfcheck (cheap; does not affect chunking)
        if cw > 0:
            for i in idxs:
                prev = ctx_lines[i - 1].strip() if i - 1 >= 0 else ""
                nxt = ctx_lines[i + 1].strip() if i + 1 < len(ctx_lines) else ""
                ctx_blocks.append(f"prev: {prev}\nnext: {nxt}")
            ctx_blocks_sc = list(ctx_blocks)
        else:
            ctx_blocks = ["" for _ in idxs]
            # Still provide a minimal prev/next context for the selfcheck stage (no extra MT calls).
            for i in idxs:
                prev = ctx_lines[i - 1].strip() if i - 1 >= 0 else ""
                nxt = ctx_lines[i + 1].strip() if i + 1 < len(ctx_lines) else ""
                ctx_blocks_sc.append(f"prev: {prev}\nnext: {nxt}")

        cleaned: List[str] = []
        style = (style_hint or "").strip()
        reasoning_effort = str(mt_reasoning_effort or "").strip()
        pm = (prompt_mode or "short").strip().lower()
        pm = "long" if pm == "long" else "short"
        long_fb = bool(long_fallback_enable)

        def _build_llm_options() -> Dict[str, Any]:
            opts: Dict[str, Any] = {
                "num_ctx": 2048,
                "num_batch": 128,
            }
            return opts

        def _load_prompt_asset(profile: str, variant: str) -> str:
            """
            Load MT prompt template from assets (best-effort).
            Returns empty string on any failure so we can fall back to the in-code prompt.
            """
            p0 = str(profile or "").strip()
            v0 = str(variant or "").strip()
            if not p0 or not v0:
                return ""
            try:
                path = _ROOT / "assets" / "prompts" / "mt" / f"{p0}.{v0}.txt"
                if not path.exists():
                    return ""
                return path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return ""

        def _render_prompt_template(tpl: str) -> str:
            t = str(tpl or "")
            style_line = f"- {style}\n" if style else ""
            t = t.replace("{{STYLE_HINT}}", style_line)
            return t.strip()

        def _build_style_block(*, long: bool) -> str:
            """
            Two-stage prompt strategy:
            - short: minimal constraints (fast path)
            - long: full best-practice rules + self-check + optional examples (fallback path)
            """
            if not long:
                return (
                    "\nStyle:\n"
                    + (f"- {style}\n" if style else "")
                    + "- Neutral, natural subtitle English.\n"
                    + "- Clear and faithful; avoid slang and meme-y wording.\n"
                    + "- Prefer simple phrasing over flashy expressions.\n"
                    + "- Preserve tone (question/negation/command/exclamation).\n"
                    + "- NEVER add new facts, names, or pop-culture references.\n"
                    + "- DO NOT invent person names or nicknames.\n"
                    + "- Do NOT invent identity attributes: gender, number, relationships, or narrator POV.\n"
                    + "- Avoid gendered pronouns (he/she) unless the Chinese clearly implies gender.\n"
                    + "- Avoid switching POV (I/we) unless explicitly present in Chinese.\n"
                    + "- Prefer explicit role nouns (the man/the killer/the mosquito/the person) when the subject is clear; otherwise use neutral phrasing.\n"
                    + "- DO NOT output pinyin/transliteration for slang/nicknames (e.g., 'Huaizi', 'Scong'). Translate the meaning (e.g., 'a cigarette') or paraphrase neutrally.\n"
                    + "- Avoid unnatural collocations. Example: say 'swat at the mosquito' (NOT 'swat away the mosquito bites').\n"
                    + "- For exaggerated/comic narration or vulgar slang, rewrite into CLEAN comedic narration (PG-13): do NOT output explicit sexual/bodily terms.\n"
                    + "- If the Chinese is a fragment (missing subject/object), you MUST add minimal implied pronouns (he/they/it/this/that) to make a complete English sentence.\n"
                )
            blk = (
                "\nWorkflow (do NOT output intermediate steps):\n"
                "- Step 1: Rewrite the Chinese subtitle in your head with the SAME meaning. Do NOT add or remove facts.\n"
                "- Step 2: Translate the rewritten version into neutral, natural English subtitles.\n"
                "\nStyle requirements:\n"
                + (f"- {style}\n" if style else "")
                + "- Neutral, natural subtitle English.\n"
                + "- Clear and faithful; avoid slang and meme-y wording.\n"
                + "- Prefer simple phrasing over flashy expressions.\n"
                + "- Keep it readable; avoid long clauses when possible.\n"
                + "- Preserve the original tone: questions/negation/commands/exclamations.\n"
                + "- Use idiomatic collocations. Avoid literal-but-weird word combinations.\n"
                + "- For exaggerated/comic narration: keep it like a clean voice-over story (PG-13), not a literal crude translation.\n"
                + "  If the source uses vulgar/body-humor slang, soften it while preserving the comedic intent.\n"
                "\nProhibited (VERY IMPORTANT):\n"
                + "- Do NOT add new information (reasons, background, opinions) not in the source.\n"
                + "- Do NOT change facts, time order, causality, person reference, or negation.\n"
                + "- Do NOT introduce new named entities / pop culture references unless explicitly present in the Chinese.\n"
                + "- Do NOT invent person names or nicknames. If the source doesn't explicitly provide a name, DO NOT create one.\n"
                + "- Do NOT invent identity attributes: gender, number, relationships, or narrator POV.\n"
                + "- Avoid gendered pronouns (he/she) unless the Chinese clearly implies gender.\n"
                + "- Avoid switching POV (I/we) unless explicitly present in Chinese.\n"
                + "- Prefer explicit role nouns (the man/the killer/the mosquito/the person) when the subject is clear; otherwise use neutral phrasing.\n"
                + "- Do NOT output pinyin/transliteration for Chinese slang/nicknames/brands unless the source already contains that exact Latin spelling.\n"
                + "  Example: 老板 -> the boss (NOT lao ban). 香烟 -> a cigarette (NOT transliterated slang).\n"
                + "- Do NOT output sentence fragments. Each output line must be a complete, standalone English sentence.\n"
                + "- Avoid dangling endings like: to/with/of/on/at/that/which/because/even though/but/and.\n"
                + "- If the Chinese is a fragment (missing subject/object), you MUST add minimal implied pronouns (he/they/it/this/that) to make a grammatical sentence.\n"
                + "  This is allowed and is NOT considered 'adding new facts'.\n"
                + "- If uncertain, be conservative and literal rather than making things up.\n"
                "\nSelf-check (do NOT output):\n"
                + "- Did you add/remove facts?\n"
                + "- Is it conversational and concise?\n"
                + "- Does each output line align 1:1 with the input line?\n"
                + "- Did you accidentally introduce she/her or a new named person?\n"
                + "- Did you create an awkward collocation? If yes, rewrite to a natural phrasing.\n"
            )
            if bool(long_examples_enable):
                blk += (
                    "\nExamples:\n"
                    "ZH: 你别装了，我都看见了。\nEN: Stop pretending. I saw it.\n"
                    "ZH: 现在不是吵架的时候，我们得先走。\nEN: This isn’t the time to argue. We need to go.\n"
                    "ZH: 你到底想说什么？别绕弯子。\nEN: What are you trying to say? Get to the point.\n"
                    "ZH: 此刻他想抽根烟。\nEN: At this moment, he wants a cigarette.\n"
                    "ZH: 下一秒。\nEN: In the next second.\n"
                    "ZH: 他们决定正面应对。\nEN: They decided to face it head-on.\n"
                )
            return blk

        def _get_style_block(*, long: bool) -> str:
            prof = str(prompt_profile or "").strip()
            if prof:
                raw = _load_prompt_asset(prof, "long" if long else "short")
                if raw.strip():
                    return "\n" + _render_prompt_template(raw) + "\n"
            return _build_style_block(long=long)

        def _build_mt_prompt(local_src_lines: List[str], local_ctx_blocks: List[str], *, strict_json: bool) -> str:
            style_block = _get_style_block(long=(pm == "long"))
            return (
                "You are a professional subtitle translator.\n"
                "Translate the following Chinese subtitle lines to neutral, natural English.\n"
                "Rules:\n"
                "- Output ENGLISH ONLY (no Chinese characters).\n"
                "- Return ONLY one JSON object, with no markdown fences and no commentary.\n"
                "- JSON shape: {\"lines\":[\"...\", \"...\"]}.\n"
                f"- The lines array must contain exactly {len(local_src_lines)} items and keep the SAME input order.\n"
                "- Preserve any placeholder tokens like @@ENTAA@@ verbatim.\n"
                + (
                    f"- Soft budget: try to keep each output line <= {int(max_words_per_line)} words, but NEVER output fragments or drop key facts.\n"
                    if int(max_words_per_line or 0) > 0
                    else ""
                )
                + "- Do NOT add information not present in the source.\n"
                + "- Do NOT change person/number/negation/causality.\n"
                + "- Each output line MUST be a complete, standalone English sentence (no fragments).\n"
                + "- Do NOT introduce new named entities or pop-culture references unless explicitly present in the Chinese.\n"
                + "- Do NOT output pinyin/transliteration for slang/nicknames. Translate the meaning or paraphrase neutrally.\n"
                + "- Do NOT invent person names/nicknames. Use neutral references unless the source explicitly names someone.\n"
                + ("- Context may be provided (prev/next lines). Use it ONLY for disambiguation.\n" if cw > 0 else "")
                + ("- STRICT MODE: output only valid JSON matching the schema exactly.\n" if strict_json else "")
                + style_block
                + "\n".join(
                    f"{k+1}. {local_src_lines[k]}"
                    + (f"\n[context]\n{local_ctx_blocks[k]}\n[/context]" if cw > 0 else "")
                    for k in range(len(local_src_lines))
                )
            )

        def _request_mt_cleaned(
            local_idxs: List[int],
            local_segs: List[Segment],
            local_src_lines: List[str],
            local_ctx_blocks: List[str],
            *,
            label: str,
            strict_json: bool,
        ) -> List[str]:
            prompt = _build_mt_prompt(local_src_lines, local_ctx_blocks, strict_json=strict_json)
            body: Dict[str, Any] = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Translate Chinese to English, keep meaning concise and follow the JSON contract exactly.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": min(768, max(192, 80 * max(1, len(local_src_lines)))),
                "options": _build_llm_options(),
            }
            if reasoning_effort:
                body["reasoning_effort"] = reasoning_effort
            try:
                idx_range = f"{(local_idxs[0] + 1) if local_idxs else '?'}-{(local_idxs[-1] + 1) if local_idxs else '?'}"
                ep0 = str(endpoint or "").strip()
                ep_show = ep0 if len(ep0) <= 96 else (ep0[:96] + "…")
                print(
                    f"[mt] request {label} lines={len(local_src_lines)} idx={idx_range} "
                    f"model={model} strict={strict_json} endpoint={ep_show}"
                )
            except Exception:
                pass
            t_req = time.time()
            _bump_contract_stat("mt", "requests")
            data, mode = _llm_post_chat_structured(
                endpoint,
                headers=headers,
                body=body,
                schema_name="mt_lines",
                schema=_build_mt_format_schema(len(local_src_lines)),
                timeout_s=max(30, int(request_timeout_s or 120)),
                retries=max(1, int(request_retries or 2)),
                stage_name="mt",
            )
            try:
                print(
                    f"[mt] response {label} mode={mode} elapsed={time.time()-t_req:.1f}s"
                )
            except Exception:
                pass
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            payload_candidates = _extract_message_payload_candidates(msg)
            content = payload_candidates[0] if payload_candidates else (msg.get("content", "") or "")
            cleaned0 = None
            repaired_used = False
            for candidate in payload_candidates or [content]:
                candidate_text = str(candidate or "")
                cleaned0 = _parse_mt_contract(candidate, len(local_segs))
                if cleaned0 is not None:
                    repaired_text = _repair_json_candidate_text(candidate_text)
                    repaired_used = bool(repaired_text) and repaired_text != _normalize_json_candidate_text(candidate_text)
                    break
            if cleaned0 is None:
                _bump_contract_stat("mt", "contract_invalid")
                raw_head = str(content or "").replace("\n", " ")[:500]
                raise RuntimeError(
                    f"mt_contract_invalid lines={len(local_segs)} strict={strict_json} raw={raw_head!r}"
                )
            if repaired_used:
                _bump_contract_stat("mt", "syntactic_repair")
            _bump_contract_stat("mt", "success_chunks")
            return cleaned0

        def _translate_group_adaptive(
            local_idxs: List[int],
            local_segs: List[Segment],
            local_src_lines: List[str],
            local_ctx_blocks: List[str],
            *,
            label: str,
        ) -> List[str]:
            try:
                return _request_mt_cleaned(
                    local_idxs,
                    local_segs,
                    local_src_lines,
                    local_ctx_blocks,
                    label=label,
                    strict_json=False,
                )
            except Exception as exc1:
                _bump_contract_stat("mt", "contract_retry")
                print(f"[warn] MT primary chunk failed for {label}; retrying once with stricter JSON-only prompt: {exc1}")
                try:
                    return _request_mt_cleaned(
                        local_idxs,
                        local_segs,
                        local_src_lines,
                        local_ctx_blocks,
                        label=f"{label}:retry",
                        strict_json=True,
                    )
                except Exception as exc2:
                    if len(local_segs) <= 1:
                        raise RuntimeError(f"LLM translation contract violation at {label}: {exc2}") from exc2
                    mid = max(1, len(local_segs) // 2)
                    _bump_contract_stat("mt", "adaptive_splits")
                    print(
                        f"[warn] MT chunk still unstable for {label}; splitting {len(local_segs)} -> "
                        f"{mid}+{len(local_segs)-mid}"
                    )
                    left = _translate_group_adaptive(
                        local_idxs[:mid],
                        local_segs[:mid],
                        local_src_lines[:mid],
                        local_ctx_blocks[:mid],
                        label=f"{label}L",
                    )
                    right = _translate_group_adaptive(
                        local_idxs[mid:],
                        local_segs[mid:],
                        local_src_lines[mid:],
                        local_ctx_blocks[mid:],
                        label=f"{label}R",
                    )
                    return left + right

        cleaned = _translate_group_adaptive(
            idxs,
            segs,
            src_lines,
            ctx_blocks,
            label=f"{gi}/{max(1,total_groups)}",
        )
        # enforce english-only (strip any CJK/fullwidth chars that may leak from LLM)
        cleaned = [re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s).strip() for s in cleaned]
        # align lengths
        while len(cleaned) < len(segs):
            cleaned.append("")
        cleaned = cleaned[: len(segs)]

        def _fix_bad_en(s: str) -> str:
            """
            Extremely low-risk English post-fix (non-LLM):
            - collapse duplicated short function words (e.g. 'the the', 'a a')
            - remove 'a the' / 'the a' typos
            - collapse whitespace
            """
            t = str(s or "")
            t = re.sub(r"\s+", " ", t).strip()
            if not t:
                return t
            # Common duplicated function words
            t = re.sub(r"\b(the|a|an|to|of|in|on|and|but|or)\s+\1\b", r"\1", t, flags=re.IGNORECASE)
            # Common article bigram typos
            t = re.sub(r"\b(a|an)\s+the\b", "the", t, flags=re.IGNORECASE)
            t = re.sub(r"\bthe\s+(a|an)\b", "the", t, flags=re.IGNORECASE)
            t = re.sub(r"\s+", " ", t).strip()
            return t

        cleaned = [_fix_bad_en(s) for s in cleaned]

        def _is_preposition_relation_risky(zh: str, en: str) -> bool:
            """
            Generic fact-risk heuristic: avoid flipping concrete spatial relations
            such as "on" vs "in" when Chinese clearly indicates it.
            This is intentionally conservative and only triggers on common, unambiguous patterns.
            """
            z = str(zh or "").strip()
            e = " " + re.sub(r"\s+", " ", str(en or "").lower()).strip() + " "
            if not z or not e.strip():
                return False
            # On / attached / on top of ...
            on_mark = bool(re.search(r"(车上|桌上|墙上|门上|身上|头上|手上|背上|上面|上边|上方)", z))
            # In / inside ...
            in_mark = bool(re.search(r"(车里|屋里|包里|口袋里|里面|内侧|内部)", z))
            has_on = (" on " in e) or (" onto " in e) or (" atop " in e)
            has_in = (" in " in e) or (" inside " in e) or (" into " in e)
            if on_mark and has_in and (not has_on):
                return True
            if in_mark and has_on and (not has_in):
                return True
            return False

        # Product rule:
        # MT main path is strict JSON batch translation + optional bounded self-check only.
        # Do NOT perform per-line long-prompt retranslation or word-budget rewriting here.

        # Robustness: retry once for obviously broken English lines (common LLM failure modes).
        def _is_bad_line(src_zh: str, en: str) -> bool:
            s = (en or "").strip()
            if not s:
                return True
            # non-ascii often indicates pinyin/diacritics artifacts (e.g., Sóng)
            try:
                if any(ord(ch) > 127 for ch in s):
                    return True
            except Exception:
                pass
            low = s.lower()
            low2 = re.sub(r"\s+", " ", low).strip().rstrip(".!?")
            # obvious fragments / dangling stubs
            if low2 in {"even though", "but", "and", "because", "so", "then"}:
                return True
            if re.search(r"\b(and|or|but|to|of|with|for|on|in|at|into|from|that|which|because|though)$", low2):
                return True
            if re.search(r"\b(a|an|the|is|are|was|were|do|does|did|can|could|will|would|should|may|might)$", low2):
                return True
            # duplicated function words / obvious article errors
            if re.search(r"\b(the|a|an|to|of|in|on|and|but|or)\s+\1\b", low):
                return True
            if " a the " in f" {low} " or " the a " in f" {low} ":
                return True
            # very short outputs for long Chinese inputs (likely truncation)
            if len(src_zh.strip()) >= 12 and len(s) <= 12:
                return True
            # too few words for a non-trivial Chinese line
            if len(src_zh.strip()) >= 10 and len([w for w in re.split(r"\s+", s) if w]) <= 2:
                return True
            # fragment ending with comma/semicolon (often incomplete)
            if s.endswith((",", ";", ":")):
                return True
            # dangling conjunctions / prepositions (often incomplete)
            if re.search(r"\b(and|or|but|to|of|with|for)$", low):
                return True
            # nickname transliteration like "Bro X" from X哥/X姐 etc
            try:
                if re.search(r"[\u4e00-\u9fff]{1,6}(哥们|哥|姐|爷|叔|婶|妹|弟|总)", str(src_zh or "")) and re.search(r"\b(bro|cong)\b", low):
                    return True
            except Exception:
                pass
            return False

        if selfcheck_enable:
            # Only self-check suspicious lines to keep cost low and avoid unnecessary rewrites.
            zh_for_hint = list(src_lines)
            max_lines = max(0, int(selfcheck_max_lines or 0))
            max_ratio = float(selfcheck_max_ratio or 0.0)
            max_ratio = max(0.0, min(max_ratio, 1.0))
            cap = max_lines
            if max_ratio > 0:
                cap = min(cap, int(max(1, round(len(cleaned) * max_ratio))))
            idxs_need = [j for j, (zhj, enj) in enumerate(zip(zh_for_hint, cleaned)) if _needs_selfcheck(zhj, enj)]
            if cap and idxs_need:
                # Prioritize high-impact problems under tight caps (avoid "headache-fix" randomness).
                idxs_need_sorted = sorted(
                    idxs_need,
                    key=lambda j: (_selfcheck_score(zh_for_hint[j], cleaned[j]), -j),
                    reverse=True,
                )
                idxs_pick = idxs_need_sorted[:cap]
                fixed = list(cleaned)
                for j in idxs_pick:
                    ctx = ctx_blocks_sc[j] if (j < len(ctx_blocks_sc)) else ""
                    fixed[j] = _selfcheck_lines([zh_for_hint[j]], [fixed[j]], ctx_blocks=[ctx])[0]
                cleaned = [_fix_bad_en(s) for s in fixed]

        for seg, tr in zip(segs, cleaned):
            out.append(Segment(start=seg.start, end=seg.end, text=seg.text, translation=tr))
    return out


def _llm_post_chat(
    endpoint: str,
    *,
    headers: Dict[str, str],
    body: Dict[str, Any],
    timeout_s: int = 180,
    retries: int = 4,
) -> Dict[str, Any]:
    """OpenAI-compatible chat helper with simple retry."""
    endpoint0 = str(endpoint or "").strip()
    url = f"{endpoint0.rstrip('/')}/chat/completions"
    last_exc: Exception | None = None

    def _is_transient_ollama_error(msg: str) -> bool:
        low = str(msg or "").lower()
        return any(
            token in low
            for token in (
                "llm server loading model",
                "llm server not responding",
                "runner process no longer running",
                "unexpected server status",
                "model failed to load",
                "context canceled",
            )
        )

    for attempt in range(max(1, int(retries))):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=(10, timeout_s))
            if resp.status_code != 200:
                msg = f"http {resp.status_code}: {(resp.text or '').strip()[:1000]}"
                if _is_transient_ollama_error(msg):
                    raise RuntimeError(f"transient_ollama_error: {msg}")
                raise RuntimeError(msg)
            data = resp.json() or {}
            if not isinstance(data, dict):
                raise RuntimeError("invalid llm response (not dict)")
            return data
        except Exception as exc:
            last_exc = exc
            # Self-heal common Docker misconfig:
            # - inside container, endpoint mistakenly set to localhost (127.0.0.1:11434)
            # - Ollama runs in a sibling service named "ollama"
            try:
                if (
                    attempt == 0
                    and ("/v1/" in endpoint0 or endpoint0.endswith(":11434") or ":11434" in endpoint0)
                    and ("127.0.0.1" in endpoint0 or "localhost" in endpoint0)
                    and Path("/.dockerenv").exists()
                ):
                    endpoint1 = re.sub(r"(127\.0\.0\.1|localhost)", "ollama", endpoint0)
                    url = f"{endpoint1.rstrip('/')}/chat/completions"
                    print(f"[warn] LLM endpoint looks like localhost inside Docker; retrying with {endpoint1}")
            except Exception:
                pass
            sleep_s = min(20.0, 2.0 * (2**attempt))
            if _is_transient_ollama_error(str(exc)):
                sleep_s = max(sleep_s, 8.0)
            print(f"[warn] LLM request failed (attempt {attempt+1}/{max(1,int(retries))}): {exc}. Retrying in {sleep_s:.1f}s")
            time.sleep(sleep_s)
    raise RuntimeError(f"LLM request failed repeatedly: {last_exc}")


def _strip_json_fence(s: str) -> str:
    t = str(s or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^\s*```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _normalize_json_candidate_text(raw0: Any) -> str:
    t = _strip_json_fence(str(raw0 or ""))
    if not t:
        return ""
    t = t.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    return t.strip()


def _iter_json_candidate_strings(raw0: Any) -> List[str]:
    raw1 = _normalize_json_candidate_text(raw0)
    if not raw1:
        return []
    out: List[str] = [raw1]
    i0_obj = raw1.find("{")
    i1_obj = raw1.rfind("}")
    if i0_obj >= 0:
        if i1_obj > i0_obj:
            out.append(raw1[i0_obj : i1_obj + 1])
        else:
            out.append(raw1[i0_obj:])
    i0_arr = raw1.find("[")
    i1_arr = raw1.rfind("]")
    if i0_arr >= 0:
        if i1_arr > i0_arr:
            out.append(raw1[i0_arr : i1_arr + 1])
        else:
            out.append(raw1[i0_arr:])
    dedup: List[str] = []
    seen: set[str] = set()
    for item in out:
        s = str(item or "").strip()
        if s and s not in seen:
            seen.add(s)
            dedup.append(s)
    return dedup


def _balance_json_candidate_text(raw: str) -> str:
    s = str(raw or "")
    if not s:
        return s
    out_chars: List[str] = []
    stack: List[str] = []
    in_str = False
    escape = False
    for ch in s:
        if in_str:
            out_chars.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            out_chars.append(ch)
            in_str = True
        elif ch in "{[":
            out_chars.append(ch)
            stack.append(ch)
        elif ch in "}]":
            want = "{" if ch == "}" else "["
            if stack and stack[-1] == want:
                out_chars.append(ch)
                stack.pop()
                continue
            if want in stack:
                while stack and stack[-1] != want:
                    opener = stack.pop()
                    out_chars.append("}" if opener == "{" else "]")
                if stack and stack[-1] == want:
                    out_chars.append(ch)
                    stack.pop()
                continue
            # Drop stray unmatched closing delimiters instead of hard-failing the whole payload.
            continue
        else:
            out_chars.append(ch)
    if in_str:
        out_chars.append('"')
    for opener in reversed(stack):
        out_chars.append("}" if opener == "{" else "]")
    return "".join(out_chars)


def _repair_json_candidate_text(raw0: Any) -> str:
    s = _normalize_json_candidate_text(raw0)
    if not s:
        return ""
    s = re.sub(r",\s*([\]}])", r"\1", s)
    s = re.sub(r'""+(?=\s*[\]}])', '"', s)
    s = re.sub(r'"\s*"\s*(?=[\]}])', '"', s)
    s = _balance_json_candidate_text(s)
    s = re.sub(r",\s*([\]}])", r"\1", s)
    return s.strip()


def _try_load_json_text(raw: str) -> Any:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        try:
            decoder = json.JSONDecoder()
            obj, idx = decoder.raw_decode(s)
            rest = s[idx:].strip()
            if not rest or rest in {"```", "```json"}:
                return obj
        except Exception:
            pass
        return None


def _load_json_candidate(raw0: Any, *, allow_repair: bool = False) -> Any:
    if isinstance(raw0, (dict, list)):
        return raw0
    candidates = _iter_json_candidate_strings(raw0)
    if allow_repair:
        repaired = [_repair_json_candidate_text(item) for item in candidates]
        for item in repaired:
            if item and item not in candidates:
                candidates.append(item)
    for cand in candidates:
        obj = _try_load_json_text(cand)
        if obj is not None:
            return obj
    return None


def _extract_message_payload_candidates(msg: Any) -> List[Any]:
    if not isinstance(msg, dict):
        return []
    candidates: List[Any] = []
    parsed = msg.get("parsed")
    if parsed not in (None, "", []):
        candidates.append(parsed)
    content = msg.get("content")
    if isinstance(content, list):
        text_parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                txt = part.get("text")
                if txt in (None, ""):
                    txt = part.get("content")
                if txt in (None, ""):
                    txt = part.get("value")
                if isinstance(txt, str) and txt.strip():
                    text_parts.append(txt)
        if text_parts:
            candidates.append("\n".join(text_parts))
    elif content not in (None, ""):
        candidates.append(content)
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        args = fn.get("arguments")
        if args not in (None, ""):
            candidates.append(args)
    dedup: List[Any] = []
    seen: set[str] = set()
    for item in candidates:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key not in seen:
            seen.add(key)
            dedup.append(item)
    return dedup


def _build_response_format(schema_name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": str(schema_name or "structured_output"),
            "strict": True,
            "schema": schema,
        },
    }


def _structured_output_cap_cache_key(endpoint: str, model: str) -> str:
    return f"{str(endpoint or '').strip()}::{str(model or '').strip()}"


def _is_response_format_unsupported_error(msg: str) -> bool:
    low = str(msg or "").lower()
    return "response_format" in low and any(
        token in low
        for token in (
            "unknown field",
            "unknown parameter",
            "unsupported",
            "not supported",
            "invalid field",
            "extra inputs are not permitted",
        )
    )


def _llm_post_chat_structured(
    endpoint: str,
    *,
    headers: Dict[str, str],
    body: Dict[str, Any],
    schema_name: str,
    schema: Dict[str, Any],
    timeout_s: int,
    retries: int,
    stage_name: str,
) -> Tuple[Dict[str, Any], str]:
    key = _structured_output_cap_cache_key(endpoint, str(body.get("model") or ""))
    prefer_response_format = _STRUCTURED_OUTPUT_CAP_CACHE.get(key, True)
    modes = ["response_format", "format"] if prefer_response_format else ["format"]
    last_exc: Exception | None = None
    for mode in modes:
        body0 = dict(body)
        body0.pop("response_format", None)
        body0.pop("format", None)
        if mode == "response_format":
            body0["response_format"] = _build_response_format(schema_name, schema)
        else:
            body0["format"] = schema
        try:
            data = _llm_post_chat(
                endpoint,
                headers=headers,
                body=body0,
                timeout_s=timeout_s,
                retries=retries,
            )
            _STRUCTURED_OUTPUT_CAP_CACHE[key] = mode == "response_format"
            if mode != "response_format":
                _bump_contract_stat(stage_name, "fallback_legacy_format")
            return data, mode
        except Exception as exc:
            last_exc = exc
            if mode == "response_format" and _is_response_format_unsupported_error(str(exc)):
                print(f"[warn] {stage_name}: response_format unsupported, falling back to legacy format")
                _STRUCTURED_OUTPUT_CAP_CACHE[key] = False
                continue
            raise
    raise RuntimeError(f"{stage_name} structured request failed: {last_exc}")

def _extract_zh_risky_spans_llm(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    items: List[tuple[int, str]],
    max_spans_per_line: int = 3,
    max_total_spans: int = 30,
) -> Dict[int, List[Dict[str, Any]]]:
    """
    P1: Extract risky phrase spans (no paraphrase).
    Returns idx -> spans[].
    Span schema (minimal): {start,end,text,risk}.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Build input block
    lines = []
    for idx, text in items:
        t = str(text or "").replace("\n", " ").strip()
        lines.append(f"{int(idx)}: {t}")
    idx_text_block = "\n".join(lines)

    def _is_junk_span(s0: str) -> bool:
        """
        Hard filters to avoid meaningless spans such as single characters ("秒","纠") or pure units/connectives.
        This is intentionally conservative: allow slang/nicknames/idioms; block function words and measurement/time units.
        """
        s = str(s0 or "").strip()
        if not s:
            return True
        compact = re.sub(r"\s+", "", s)
        # Minimum 2 chars: single-character spans are almost always noise for "hard-to-translate" goals.
        if len(compact) < 2:
            return True
        # Pure punctuation / digits / ascii noise
        if re.fullmatch(r"[0-9]+", compact):
            return True
        if re.fullmatch(r"[\W_]+", compact, flags=re.UNICODE):
            return True
        if re.fullmatch(r"[A-Za-z]+", compact):
            return True
        # Common units / quantifiers (often meaningless alone)
        units = {
            "秒",
            "分",
            "分钟",
            "小时",
            "天",
            "月",
            "年",
            "次",
            "个",
            "把",
            "只",
            "条",
            "位",
            "名",
            "块",
            "元",
            "米",
            "公里",
            "度",
            "斤",
            "两",
            "岁",
        }
        if compact in units:
            return True
        # Time/countdown/duration expressions (very common; usually low review value).
        # e.g. "三十秒" "10分钟" "两小时"
        if re.fullmatch(
            r"(?:[0-9]+|[零〇一二三四五六七八九十百千万两]+)(?:秒|分钟|分|小时|天|月|年)",
            compact,
        ):
            return True
        # High-frequency connectives / filler phrases that are rarely "hard-to-translate" targets.
        fillers = {
            "此时",
            "这时",
            "然后",
            "于是",
            "而且",
            "但是",
            "因为",
            "所以",
            "不过",
            "只是",
            "还是",
            "就是",
            "一个",
            "这种",
            "那种",
            "这样",
            "那样",
            "这里",
            "那里",
            "一下",
            "一点",
            "一些",
            "一起",
            "继续",
        }
        if compact in fillers:
            return True
        return False

    # 轻文本协议：不再要求 JSON，由 LLM 输出简单行格式后在本地解析。
    # 协议示例：
    #   idx=1: 太秀了|medium
    #   idx=2: 浑身解数|medium; 正面硬刚|medium
    #   idx=3: 智窗|high
    # 其中 risk 只能是 high/medium，省略时默认 medium。
    prompt = (
        "### Role / 角色\n"
        "You are a phrase risk extractor for Chinese subtitles in a Chinese-to-English dubbing pipeline.\n"
        "你是“中文字幕短语风险抽取助手”，结果只给人工审核使用，用来发现：ASR 同音错字、难翻的表达、网络用语/梗等。\n"
        "Your priority is phrase-like spans (NOT full clauses/sentences). Prefer the smallest meaningful phrase.\n"
        "优先抽取“短语/词组”，不要输出整句/分句；宁可短一点，也不要把整句复制出来。\n"
        "\n"
        "### Task / 任务\n"
        "You will receive multiple Chinese subtitle lines, each with an integer idx.\n"
        "下面会给出多行中文字幕，每行都有一个 idx。\n"
        f"For each line, extract 0–N spans (2–8 Chinese characters) that deserve human review, up to {int(max_spans_per_line)} spans per line, and at most {int(max_total_spans)} spans in total.\n"
        "Each span MUST be an exact contiguous substring of the line (copy from the line, do not invent or rewrite).\n"
        "Use 0-based character indices for start (inclusive) and end (exclusive). risk MUST be \"high\" or \"medium\".\n"
        "\n"
        "Hard constraints / 硬性约束（必须遵守）\n"
        "- Length: 2–8 Chinese characters (CJK). Do NOT output longer spans.\n"
        "- Do NOT include punctuation. Do NOT output whole sentences.\n"
        "- Do NOT output generic fillers (例如：然后、就是、一下、这个、那种...).\n"
        "\n"
        "### What to extract (include when in doubt; use risk=\"medium\") / 优先抽取\n"
        "1) ASR / homophone errors / 同音错误：在语境里看起来不通顺、像是把发音相近的字识别错了的那一小段。\n"
        "   Common confusion types: -n/-ng, 的/得/地, 是/事, sound-alike words, etc.\n"
        "   Extract ONLY the suspicious part as it appears in the line (do NOT output the corrected form).\n"
        "2) Hard-to-translate or meaning-heavy expressions / 难翻译、语义很重的表达：成语、固定搭配、比喻、文化色彩很强的说法等。\n"
        "   Phrases where a naive word-by-word English translation would likely be awkward or wrong.\n"
        "3) Slang / internet / memes / nicknames / playful or non-literal wording / 网络用语、梗、外号等。\n"
        "   Internet buzzwords, jokes, character nicknames, gaming/ACG slang, etc.\n"
        "If a line does NOT contain any such content, output nothing for that idx (leave it blank after ':').\n"
        "\n"
        "### Output format / 输出格式（只允许这一种）\n"
        "- Output plain text lines, one line per subtitle idx.\n"
        "- Line format: idx=<number>: phrase1|risk1; phrase2|risk2; ...\n"
        "- risk MUST be \"high\" or \"medium\"; when omitted, treat as \"medium\".\n"
        "- Do NOT output JSON. Do NOT output explanations, thoughts, or markdown. Do NOT output <think>.\n"
        "\n"
        "Example:\n"
        "Input lines:\n"
        "1: 这波操作太秀了\n"
        "2: 他使出浑身解数正面硬刚对手\n"
        "3: 医生说他得了智窗这种病（ASR 把“痔疮”听错）\n"
        "\n"
        "Output:\n"
        "idx=1: 太秀了|medium\n"
        "idx=2: 浑身解数|medium; 正面硬刚|medium\n"
        "idx=3: 智窗|high\n"
        "\n"
        "### Actual input lines / 实际输入\n"
        "Below are the real subtitle lines. Follow ALL rules above and output ONLY the idx=... lines:\n"
        + idx_text_block
        + "\n"
    )

    def _is_sentencey_span(s0: str) -> bool:
        """
        Filters spans that look like clauses/sentences rather than phrases.
        Keep strict: sentence-level review is done via CHS editor, not phrase precipitation.
        """
        s = str(s0 or "").strip()
        if not s:
            return True
        compact = re.sub(r"\s+", "", s)
        if re.search(r"[，。！？；：、,.!?…\(\)（）\[\]【】《》“”\"']", compact):
            return True
        cjk_n = len(re.findall(r"[\u4e00-\u9fff]", compact))
        if cjk_n > 8:
            return True
        return False

    # 简单 chat 调用：不再依赖 JSON mode 或 structured outputs。
    headers = dict(headers)  # shallow copy
    messages = [
        {
            "role": "system",
            "content": (
                "You extract phrase spans for human review in a Chinese-to-English dubbing pipeline. "
                "Your ONLY job is to output plain text lines in the exact format described by the user. "
                "Do NOT output explanations, thoughts, markdown, JSON, or <think>; output the lines only."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.15,
        "max_tokens": 600,
        "stream": False,
        "reasoning_effort": "none",
    }
    # Debug: print request target (helps diagnose localhost-vs-docker mistakes).
    try:
        print(f"[debug] zh_phrase: request endpoint={str(endpoint).strip()!r} model={model!r} max_tokens={body.get('max_tokens')}")
    except Exception:
        pass

    content = ""
    reasoning = ""
    # Phrase extraction is a best-effort hinting stage. Keep requests bounded so one
    # stuck local LLM call cannot block the entire quality pipeline or regression gate.
    data = _llm_post_chat(endpoint, headers=headers, body=body, timeout_s=75, retries=2)
    msg0 = (data.get("choices") or [{}])[0].get("message", {}) or {}
    content = msg0.get("content", "") or ""
    reasoning = msg0.get("reasoning", "") or msg0.get("thinking", "") or ""
    try:
        print(f"[debug] zh_phrase: openai_compat content_len={len(str(content or ''))} reasoning_len={len(str(reasoning or ''))}")
    except Exception:
        pass
    # Qwen3/Qwen3.5 sometimes spends the budget on reasoning and returns empty content.
    # If that happens, retry once with a larger token budget.
    if (not str(content).strip()) and str(reasoning).strip() and int(body.get("max_tokens") or 0) < 1200:
        body2 = dict(body)
        body2["max_tokens"] = 1200
        try:
            print("[warn] zh_phrase: empty content with non-empty reasoning; retrying with max_tokens=1200")
        except Exception:
            pass
        data = _llm_post_chat(endpoint, headers=headers, body=body2, timeout_s=75, retries=1)
        msg0 = (data.get("choices") or [{}])[0].get("message", {}) or {}
        content = msg0.get("content", "") or ""
        reasoning = msg0.get("reasoning", "") or msg0.get("thinking", "") or ""
        try:
            print(f"[debug] zh_phrase: openai_compat(retry) content_len={len(str(content or ''))} reasoning_len={len(str(reasoning or ''))}")
        except Exception:
            pass
    s = str(content).strip()
    # Debug 小模型原始输出头部，便于诊断“完全不说话”的情况。
    try:
        head = s[:240].replace("\n", " ")
        print(f"[debug] zh_phrase: llm_raw_head={head!r}")
    except Exception:
        pass

    # 允许空或无效输出：直接视为无短语（by_idx 为空），避免中断整条流水线。
    if not s:
        return {}

    # 解析轻文本协议。
    by_idx: Dict[int, List[Dict[str, Any]]] = {}
    total_spans = 0
    text_by_idx = {int(i): str(t or "") for i, t in items}

    # 简单去掉可能的 <think> 包裹（若模型仍然偷偷输出）。
    # 例如: "<think>...</think>\nidx=1: ..."
    s2 = re.sub(r"<think>.*?</think>", "", s, flags=re.IGNORECASE | re.DOTALL).strip()
    lines_out = [ln.strip() for ln in s2.splitlines() if ln.strip()]

    for ln in lines_out:
        # 期望格式: idx=1: 短语1|risk1; 短语2|risk2
        m = re.match(r"^idx\s*=\s*(\d+)\s*:\s*(.*)$", ln)
        if not m:
            continue
        try:
            idx = int(m.group(1))
        except Exception:
            continue
        tail = m.group(2).strip()
        if not tail:
            continue

        line_text = text_by_idx.get(idx, "")
        if not line_text:
            continue

        parts = [p.strip() for p in tail.split(";") if p.strip()]
        if not parts:
            continue

        out_spans: List[Dict[str, Any]] = []
        for part in parts[: max(0, int(max_spans_per_line))]:
            if "|" in part:
                text_raw, risk_raw = part.split("|", 1)
            else:
                text_raw, risk_raw = part, "medium"
            text_raw = str(text_raw or "").strip()
            risk_raw = str(risk_raw or "").strip().lower() or "medium"
            if not text_raw:
                continue
            # Enforce phrase-like constraint even if model ignores the prompt.
            if _is_sentencey_span(text_raw):
                continue

            # 在原字幕行中定位子串；若出现多次，则取第一次且要求唯一匹配。
            pos = line_text.find(text_raw)
            if pos < 0:
                continue
            if line_text.find(text_raw, pos + 1) >= 0:
                # 多次出现时为避免歧义，暂时跳过。
                continue
            start = pos
            end = pos + len(text_raw)
            if start < 0 or end <= start or end > len(line_text):
                continue
            sub = line_text[start:end]
            if _is_junk_span(sub):
                continue
            # Final guardrails on the actual located substring.
            sub_compact = re.sub(r"\s+", "", sub)
            sub_cjk_n = len(re.findall(r"[\u4e00-\u9fff]", sub_compact))
            if sub_cjk_n < 2 or sub_cjk_n > 8:
                continue
            if re.search(r"[，。！？；：、,.!?…\(\)（）\[\]【】《》“”\"']", sub_compact):
                continue

            span = {
                "start": int(start),
                "end": int(end),
                "text": sub,
                "type": "other",
                "risk": "high" if risk_raw.startswith("h") else "medium",
                "reasons": [],
                "confidence": 0.7 if risk_raw.startswith("h") else 0.6,
            }
            out_spans.append(span)
            total_spans += 1
            if total_spans >= int(max_total_spans):
                break

        if out_spans:
            by_idx[idx] = out_spans
        if total_spans >= int(max_total_spans):
            break

    try:
        print(f"[debug] zh_phrase: parsed_items={len(by_idx)} total_spans={total_spans}")
    except Exception:
        pass
    return by_idx


def _phrase_candidate_score(text: str) -> float:
    """
    Heuristic score for selecting lines to send to LLM phrase extraction.
    Goal: prioritize lines that are more likely to contain idioms/slang/metaphors/nicknames or condensed meaning.
    This must be cheap and language-agnostic; it only drives *which lines* we ask the LLM about.
    """
    t = str(text or "").strip()
    compact = re.sub(r"\s+", "", t)
    if not compact:
        return 0.0
    # Very short lines are rarely useful unless they contain a proper noun / slang; we keep score low.
    L = len(compact)
    base = min(max(L, 0), 24) / 24.0
    # 4-char chunk density (common idiom length); not a guarantee, but a useful prior.
    has4 = 1.0 if re.search(r"[\u4e00-\u9fff]{4}", compact) else 0.0
    # Mixed scripts can indicate names/terms (e.g., "DJ", "iPhone") or stylized slang.
    mixed = 1.0 if re.search(r"[A-Za-z0-9]", compact) and re.search(r"[\u4e00-\u9fff]", compact) else 0.0
    # Quoted / emphasized fragments tend to be terms or special phrases.
    quoted = 1.0 if any(x in t for x in ["“", "”", "\"", "「", "」", "『", "』"]) else 0.0
    # Exclamations/interjections often carry implied meaning.
    emph = 1.0 if re.search(r"[！？!?.…]", t) else 0.0
    # Colloquial nickname/slang patterns (generic, not video-specific), e.g. X哥/X姐/X爷.
    nick = 1.0 if re.search(r"[\u4e00-\u9fff]{1,3}(哥|姐|爷|叔|婶|妹|弟|总|哥们)$", compact) else 0.0
    return 0.52 * base + 0.25 * has4 + 0.10 * mixed + 0.06 * quoted + 0.03 * emph + 0.04 * nick


def _force_span_from_line(line: str) -> Dict[str, Any] | None:
    """
    Low-cost fallback to ensure coverage (>=1 span per line) when desired.
    Best practice: keep it simple, deterministic, and avoid obvious junk.
    """
    s = str(line or "").replace("\n", " ").strip()
    if not s:
        return None
    # Quick reject: no CJK at all
    if not re.search(r"[\u4e00-\u9fff]", s):
        return None

    def is_junk(t0: str) -> bool:
        t = str(t0 or "").strip()
        if not t:
            return True
        c = re.sub(r"\s+", "", t)
        if len(c) < 2:
            return True
        if re.fullmatch(r"[0-9]+", c):
            return True
        if re.fullmatch(r"[\W_]+", c, flags=re.UNICODE):
            return True
        if re.fullmatch(r"[A-Za-z]+", c):
            return True
        units = {
            "秒", "分", "分钟", "小时", "天", "月", "年", "次", "个", "把", "只", "条", "位", "名", "块", "元", "米", "公里", "度", "斤", "两", "岁",
        }
        if c in units:
            return True
        fillers = {
            "此时", "这时", "然后", "于是", "而且", "但是", "因为", "所以", "不过", "只是", "还是", "就是",
            "一个", "这种", "那种", "这样", "那样", "这里", "那里", "一下", "一点", "一些", "一起", "继续",
        }
        if c in fillers:
            return True
        return False

    # 1) Quoted term (often a slang/term/nickname)
    quote_pairs = [("“", "”"), ("「", "」"), ("『", "』"), ('"', '"')]
    for lq, rq in quote_pairs:
        if lq in s and rq in s:
            i0 = s.find(lq)
            i1 = s.find(rq, i0 + 1)
            if i0 >= 0 and i1 > i0 + 1:
                inside = s[i0 + 1 : i1].strip()
                inside2 = re.sub(r"\s+", "", inside)
                if 2 <= len(inside2) <= 10 and not is_junk(inside2) and re.search(r"[\u4e00-\u9fff]{2,}", inside2):
                    start = i0 + 1
                    end = i1
                    return {
                        "start": int(start),
                        "end": int(end),
                        "text": s[start:end],
                        "risk": "medium",
                        "source": "forced",
                        "reasons": ["min_coverage"],
                        "confidence": 0.3,
                    }

    # 2) Common nickname/slang suffixes (通用，不依赖具体视频)
    m = re.search(r"[\u4e00-\u9fff]{1,3}(哥|姐|爷|叔|婶|妹|弟|总|哥们)", s)
    if m:
        t = m.group(0)
        if not is_junk(t):
            return {
                "start": int(m.start()),
                "end": int(m.end()),
                "text": t,
                "risk": "medium",
                "source": "forced",
                "reasons": ["min_coverage"],
                "confidence": 0.3,
            }

    # 3) Idiom-like 4-char chunk (high prior)
    m4 = re.search(r"[\u4e00-\u9fff]{4}", s)
    if m4:
        t = m4.group(0)
        if not is_junk(t):
            return {
                "start": int(m4.start()),
                "end": int(m4.end()),
                "text": t,
                "risk": "medium",
                "source": "forced",
                "reasons": ["min_coverage"],
                "confidence": 0.3,
            }

    # 4) Fallback: pick the best 2-6 CJK substring window
    best: tuple[float, int, int] | None = None
    for L in [6, 5, 4, 3, 2]:
        for i in range(0, max(0, len(s) - L + 1)):
            sub = s[i : i + L]
            if not re.fullmatch(r"[\u4e00-\u9fff]{%d}" % L, sub):
                continue
            if is_junk(sub):
                continue
            # score: prefer 4-char and slightly longer windows
            score = float(L) + (1.0 if L == 4 else 0.0)
            if best is None or score > best[0]:
                best = (score, i, i + L)
        if best is not None:
            break
    if best is None:
        # last resort: first 2 Chinese chars
        m2 = re.search(r"[\u4e00-\u9fff]{2}", s)
        if not m2:
            return None
        i0, i1 = m2.start(), m2.end()
    else:
        _, i0, i1 = best
    text = s[i0:i1]
    if is_junk(text):
        return None
    return {
        "start": int(i0),
        "end": int(i1),
        "text": text,
        "risk": "medium",
        "source": "forced",
        "reasons": ["min_coverage"],
        "confidence": 0.3,
    }


_IDIOM_SET_CACHE: Dict[str, set[str]] = {}


def _resolve_data_path(p0: str) -> Path | None:
    p = str(p0 or "").strip()
    if not p:
        return None
    try:
        pp = Path(p)
        if pp.is_absolute() and pp.exists():
            return pp
    except Exception:
        pass

    # Try cwd first (dev usage)
    try:
        cand = Path.cwd() / p
        if cand.exists():
            return cand
    except Exception:
        pass

    # Try repo root relative to this file (works for source tree and for docker /app)
    try:
        here = Path(__file__).resolve()
        repo_root = here.parent.parent
        cand = repo_root / p
        if cand.exists():
            return cand
    except Exception:
        pass

    # PyInstaller onefile extraction root
    try:
        base = Path(getattr(sys, "_MEIPASS", "") or "")
        if base and base.exists():
            cand = base / p
            if cand.exists():
                return cand
    except Exception:
        pass

    # Next to executable (some deployments keep assets near exe)
    try:
        exe_dir = Path(sys.executable).resolve().parent
        cand = exe_dir / p
        if cand.exists():
            return cand
    except Exception:
        pass
    return None


def _load_idioms_4char(path: str) -> set[str]:
    p = str(path or "").strip()
    if not p:
        return set()
    if p in _IDIOM_SET_CACHE:
        return _IDIOM_SET_CACHE[p]
    out: set[str] = set()
    try:
        rp = _resolve_data_path(p)
        if not rp:
            _IDIOM_SET_CACHE[p] = set()
            return set()
        for ln in rp.read_text(encoding="utf-8", errors="ignore").splitlines():
            w = (ln or "").strip()
            if len(w) == 4:
                out.add(w)
        # Optional local supplement list next to the main idiom file.
        # This allows adding widely-used idioms that upstream lists may miss,
        # without modifying the derived upstream file.
        try:
            extra = rp.parent / "idioms_extra.txt"
            if extra.exists():
                for ln in extra.read_text(encoding="utf-8", errors="ignore").splitlines():
                    w = (ln or "").strip()
                    if len(w) == 4:
                        out.add(w)
        except Exception:
            pass
    except Exception:
        out = set()
    _IDIOM_SET_CACHE[p] = out
    return out


_HOMO_CHAR_CACHE: Dict[str, Dict[str, List[str]]] = {}
_SHAPE_CHAR_CACHE: Dict[str, Dict[str, List[str]]] = {}
_PROJECT_CONFUSION_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_ZH_WORD_SET_CACHE: Dict[tuple[str, int, int], Set[str]] = {}
_ZH_WORD_MASK_CACHE: Dict[tuple[str, int], Dict[str, List[str]]] = {}


def _load_same_pinyin_char_map(path: str) -> Dict[str, List[str]]:
    """
    Load a lightweight char->homophones map from pycorrector's `same_pinyin.txt`.
    File format: `<char> <same_tone_chars> <diff_tone_chars>` (whitespace-separated).
    We treat both groups as homophones for risk-detection only (no auto-correction).
    """
    p = str(path or "").strip()
    if not p:
        return {}
    if p in _HOMO_CHAR_CACHE:
        return _HOMO_CHAR_CACHE[p]
    mp: Dict[str, List[str]] = {}
    try:
        rp = _resolve_data_path(p)
        if not rp:
            _HOMO_CHAR_CACHE[p] = {}
            return {}
        raw = rp.read_text(encoding="utf-8", errors="ignore")
        for ln in raw.splitlines():
            s = (ln or "").strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            key = parts[0].strip()
            if len(key) != 1:
                continue
            chars: List[str] = []
            for tok in parts[1:]:
                for ch in tok.strip():
                    if ch and ch != key:
                        chars.append(ch)
            # de-dup preserving order
            seen = set()
            chars2: List[str] = []
            for ch in chars:
                if ch in seen:
                    continue
                seen.add(ch)
                chars2.append(ch)
            if chars2:
                mp[key] = chars2
    except Exception:
        mp = {}
    _HOMO_CHAR_CACHE[p] = mp
    return mp


def _load_same_stroke_char_map(path: str) -> Dict[str, List[str]]:
    p = str(path or "").strip()
    if not p:
        return {}
    if p in _SHAPE_CHAR_CACHE:
        return _SHAPE_CHAR_CACHE[p]
    mp: Dict[str, List[str]] = {}
    try:
        rp = _resolve_data_path(p)
        if not rp:
            _SHAPE_CHAR_CACHE[p] = {}
            return {}
        for ln in rp.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = (ln or "").strip().lstrip("\ufeff")
            if not s or s.startswith("#"):
                continue
            chars: List[str] = []
            for tok in re.split(r"[\t ]+", s):
                for ch in tok.strip():
                    if ch and re.fullmatch(r"[\u4e00-\u9fff]", ch):
                        chars.append(ch)
            uniq: List[str] = []
            seen: Set[str] = set()
            for ch in chars:
                if ch in seen:
                    continue
                seen.add(ch)
                uniq.append(ch)
            for ch in uniq:
                bucket = mp.setdefault(ch, [])
                for other in uniq:
                    if other != ch and other not in bucket:
                        bucket.append(other)
    except Exception:
        mp = {}
    _SHAPE_CHAR_CACHE[p] = mp
    return mp


def _load_project_confusions(path: str) -> List[Dict[str, Any]]:
    p = str(path or "").strip()
    if not p:
        return []
    if p in _PROJECT_CONFUSION_CACHE:
        return _PROJECT_CONFUSION_CACHE[p]
    out: List[Dict[str, Any]] = []
    try:
        rp = _resolve_data_path(p)
        if not rp:
            _PROJECT_CONFUSION_CACHE[p] = []
            return []
        raw = json.loads(rp.read_text(encoding="utf-8", errors="ignore") or "[]")
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            wrong = str(it.get("wrong") or "").strip()
            candidates = [str(x).strip() for x in (it.get("candidates") or []) if str(x).strip()]
            if not wrong or not candidates:
                continue
            out.append(
                {
                    "wrong": wrong,
                    "candidates": candidates,
                    "type": str(it.get("type") or "").strip(),
                    "evidence_count": int(it.get("evidence_count") or 0),
                    "sources": [str(x).strip() for x in (it.get("sources") or []) if str(x).strip()],
                    "requires_high_risk": bool(it.get("requires_high_risk", True)),
                    "max_edit_distance": max(1, int(it.get("max_edit_distance") or 2)),
                    "notes": str(it.get("notes") or "").strip(),
                }
            )
    except Exception:
        out = []
    _PROJECT_CONFUSION_CACHE[p] = out
    return out


def _project_confusion_hits(text: str, *, min_evidence_count: int = 2) -> List[Dict[str, Any]]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    out: List[Dict[str, Any]] = []
    for item in _load_project_confusions(_DEFAULT_PROJECT_CONFUSIONS_PATH):
        wrong = str(item.get("wrong") or "").strip()
        if not wrong or len(wrong) > 8:
            continue
        if int(item.get("evidence_count") or 0) < int(min_evidence_count):
            continue
        if wrong in compact:
            out.append(item)
    return out


def _load_zh_word_set(path: str, *, min_len: int = 2, max_len: int = 4) -> Set[str]:
    key = (str(path or "").strip(), int(min_len), int(max_len))
    if key in _ZH_WORD_SET_CACHE:
        return _ZH_WORD_SET_CACHE[key]
    out: Set[str] = set()
    try:
        rp = _resolve_data_path(key[0])
        if not rp:
            _ZH_WORD_SET_CACHE[key] = set()
            return set()
        for ln in rp.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = (ln or "").strip().lstrip("\ufeff")
            if not s or s.startswith("#"):
                continue
            word = re.split(r"[\t ]+", s, maxsplit=1)[0].strip()
            if not (min_len <= len(word) <= max_len):
                continue
            if not re.fullmatch(r"[\u4e00-\u9fff]+", word):
                continue
            out.add(word)
    except Exception:
        out = set()
    _ZH_WORD_SET_CACHE[key] = out
    return out


def _load_zh_word_mask_index(path: str, *, word_len: int) -> Dict[str, List[str]]:
    key = (str(path or "").strip(), int(word_len))
    if key in _ZH_WORD_MASK_CACHE:
        return _ZH_WORD_MASK_CACHE[key]
    out: Dict[str, List[str]] = {}
    if word_len <= 0:
        _ZH_WORD_MASK_CACHE[key] = out
        return out
    words = _load_zh_word_set(str(path or "").strip(), min_len=word_len, max_len=word_len)
    try:
        for word in sorted(words):
            for pos in range(word_len):
                mask = word[:pos] + "*" + word[pos + 1 :]
                bucket = out.setdefault(mask, [])
                bucket.append(word)
    except Exception:
        out = {}
    _ZH_WORD_MASK_CACHE[key] = out
    return out


def _idiom_spans_from_line(line: str, idioms4: set[str]) -> List[Dict[str, Any]]:
    s = str(line or "").replace("\n", " ").strip()
    if not s or not idioms4:
        return []
    out: List[Dict[str, Any]] = []
    # 4-char scan is cheap and stable (subtitle lines are short).
    for i in range(0, max(0, len(s) - 4 + 1)):
        sub = s[i : i + 4]
        if sub in idioms4:
            out.append(
                {
                    "start": int(i),
                    "end": int(i + 4),
                    "text": sub,
                    "risk": "high",
                    "source": "dict",
                    "reasons": ["idiom_dict"],
                    "confidence": 0.85,
                }
            )
    return out


def _idiom_spans_from_line_fuzzy(
    line: str,
    *,
    idioms4: set[str],
    homo_map: Dict[str, List[str]],
    max_hits: int = 4,
) -> List[Dict[str, Any]]:
    """
    Idiom near-match (generic, low false-positive):
    - Scan 4-char windows.
    - If the window is NOT an idiom, try replacing 1 char with a same-pinyin confusable.
    - If the substituted 4-char string is a known idiom, mark the ORIGINAL window as an idiom-risk span,
      and attach the suggested idiom in meta.

    This catches 1-char same-pinyin near-matches to a known idiom.
    """
    if not idioms4 or not homo_map:
        return []
    s = str(line or "").replace("\n", " ").strip()
    if not s:
        return []
    out: List[Dict[str, Any]] = []
    hits = 0
    for i in range(0, max(0, len(s) - 4 + 1)):
        sub = s[i : i + 4]
        if sub in idioms4:
            continue
        # Only consider pure CJK 4-char windows.
        if not re.fullmatch(r"[\u4e00-\u9fff]{4}", sub):
            continue
        # One-char homophone substitution to a known idiom.
        for k in range(4):
            alts = homo_map.get(sub[k]) or []
            for alt in alts[:8]:
                if not alt or alt == sub[k]:
                    continue
                cand = sub[:k] + alt + sub[k + 1 :]
                if cand in idioms4:
                    out.append(
                        {
                            "start": int(i),
                            "end": int(i + 4),
                            "text": sub,
                            "risk": "high",
                            "source": "dict",
                            "reasons": ["idiom_dict_fuzzy"],
                            "confidence": 0.75,
                            "meta": {"suggest": cand, "mode": "idiom_homophone_1"},
                        }
                    )
                    hits += 1
                    break
            if hits >= int(max_hits):
                break
            if hits and out and out[-1].get("start") == int(i):
                # already hit this window; don't add multiple suggestions for same 4-chunk
                break
        if hits >= int(max_hits):
            break
    return out


def _pattern_spans_from_line(line: str) -> List[Dict[str, Any]]:
    s = str(line or "").replace("\n", " ").strip()
    if not s:
        return []
    out: List[Dict[str, Any]] = []
    # Nickname/slang suffix patterns, e.g. X哥/X姐/…; prefer a short core to reduce noise.
    # Excellent-practice tradeoff: higher precision, lower recall; users can still add phrases manually.
    for m in re.finditer(r"(?P<name>[\u4e00-\u9fff]{1,6})(?P<suf>哥们|哥|姐|爷|叔|婶|妹|弟|总)", s):
        name = str(m.group("name") or "")
        suf = str(m.group("suf") or "")
        if not suf:
            continue
        name2 = re.sub(r"\s+", "", name)
        if not name2:
            continue
        # Prefer "X哥" (2 chars) or "X哥们" (3 chars)
        keep_name_len = 1 if suf != "哥们" else 1
        # If name ends with 的/地/得, drop it to keep the nickname core compact.
        if name2.endswith(("的", "地", "得")) and len(name2) >= 2:
            name2 = name2[:-1]
        core = (name2[-keep_name_len:] + suf) if name2 else suf
        # Guard: core must be pure CJK and short
        core2 = re.sub(r"\s+", "", core)
        if suf == "哥们":
            if len(core2) < 3:
                continue
            core2 = core2[-3:]
        else:
            if len(core2) < 2:
                continue
            core2 = core2[-2:]
        if not re.fullmatch(r"[\u4e00-\u9fff]{%d}" % len(core2), core2):
            continue

        span_text = s[m.start() : m.end()]
        k = span_text.rfind(core2)
        if k < 0:
            continue
        st = int(m.start() + k)
        ed = int(st + len(core2))
        out.append(
            {
                "start": st,
                "end": ed,
                "text": s[st:ed],
                "risk": "medium",
                "source": "pattern",
                "reasons": ["nickname_pattern"],
                "confidence": 0.7,
            }
        )
    # Quoted fragments are often terms/slang.
    quote_pairs = [("“", "”"), ("「", "」"), ("『", "』"), ('"', '"')]
    for lq, rq in quote_pairs:
        if lq not in s or rq not in s:
            continue
        i0 = 0
        while True:
            a = s.find(lq, i0)
            if a < 0:
                break
            b = s.find(rq, a + 1)
            if b < 0:
                break
            inside = s[a + 1 : b].strip()
            inside2 = re.sub(r"\s+", "", inside)
            if 2 <= len(inside2) <= 10 and re.search(r"[\u4e00-\u9fff]{2,}", inside2):
                out.append(
                    {
                        "start": int(a + 1),
                        "end": int(b),
                        "text": s[a + 1 : b],
                        "risk": "medium",
                        "source": "pattern",
                        "reasons": ["quoted_term"],
                        "confidence": 0.55,
                    }
                )
            i0 = b + 1
    return out


def _merge_dedupe_limit_spans(spans: List[Dict[str, Any]], *, max_spans: int) -> List[Dict[str, Any]]:
    if not spans:
        return []
    # de-dup by (start,end,text)
    seen: set[tuple[int, int, str]] = set()
    spans2: List[Dict[str, Any]] = []
    for sp in spans:
        try:
            key = (int(sp.get("start")), int(sp.get("end")), str(sp.get("text") or ""))
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        spans2.append(sp)

    def pri(sp: Dict[str, Any]) -> tuple:
        risk = str(sp.get("risk") or "").lower()
        high = 1 if risk.startswith("h") else 0
        src = str(sp.get("source") or "")
        forced = 1 if src == "forced" else 0
        try:
            conf = float(sp.get("confidence") or 0.0)
        except Exception:
            conf = 0.0
        L = len(str(sp.get("text") or ""))
        # Prefer high risk, non-forced, higher confidence, longer spans.
        return (-high, forced, -conf, -L)

    spans3 = sorted(spans2, key=pri)
    return spans3[: max(0, int(max_spans))]


def _merge_dedupe_spans_same_line(line: str, spans: List[Dict[str, Any]], *, max_spans: int) -> List[Dict[str, Any]]:
    """
    Phrase post-process (same-line merge + dedupe) to reduce UI noise:
    - Drop invalid spans.
    - Merge overlapping/containing spans into a single contiguous span.
    - If the same span text appears multiple times on the same line (common for nickname_pattern),
      keep only the best one by risk/confidence/length, to avoid duplicate tags for the same phrase.
    - Finally apply the existing priority+limit selection.

    This never invents new text: merged span text is always a contiguous substring of `line`.
    """
    if not spans:
        return []
    s = str(line or "")

    # 1) sanitize / clamp to line boundaries
    norm: List[Dict[str, Any]] = []
    for sp in spans:
        if not isinstance(sp, dict):
            continue
        try:
            st = int(sp.get("start"))
            ed = int(sp.get("end"))
        except Exception:
            continue
        if st < 0 or ed <= st or st >= len(s):
            continue
        ed = min(ed, len(s))
        txt = str(sp.get("text") or "")
        # ensure text matches the line substring; if not, overwrite with actual substring
        sub = s[st:ed]
        if not sub:
            continue
        sp2 = dict(sp)
        sp2["start"] = st
        sp2["end"] = ed
        sp2["text"] = sub
        norm.append(sp2)

    if not norm:
        return []

    # helper: span priority (reuse logic from _merge_dedupe_limit_spans)
    def pri(sp: Dict[str, Any]) -> tuple:
        risk = str(sp.get("risk") or "").lower()
        high = 1 if risk.startswith("h") else 0
        src = str(sp.get("source") or "")
        forced = 1 if src == "forced" else 0
        try:
            conf = float(sp.get("confidence") or 0.0)
        except Exception:
            conf = 0.0
        L = len(str(sp.get("text") or ""))
        return (-high, forced, -conf, -L)

    # 2) merge overlaps/containment into contiguous spans
    # sort by start asc, then end desc (so containers come first)
    norm_sorted = sorted(norm, key=lambda x: (int(x.get("start", 0)), -int(x.get("end", 0))))
    merged: List[Dict[str, Any]] = []
    for sp in norm_sorted:
        if not merged:
            merged.append(sp)
            continue
        last = merged[-1]
        a0, a1 = int(last.get("start")), int(last.get("end"))
        b0, b1 = int(sp.get("start")), int(sp.get("end"))
        # overlap or touch
        if b0 <= a1:
            st = min(a0, b0)
            ed = max(a1, b1)
            sub = s[st:ed]
            # combine metadata conservatively
            out = dict(last)
            out["start"] = st
            out["end"] = ed
            out["text"] = sub
            # risk: high wins
            r0 = str(last.get("risk") or "").lower()
            r1 = str(sp.get("risk") or "").lower()
            out["risk"] = "high" if (r0.startswith("h") or r1.startswith("h")) else "medium"
            # reasons: union
            rr = []
            for x in (last.get("reasons") or []):
                if isinstance(x, str) and x and x not in rr:
                    rr.append(x)
            for x in (sp.get("reasons") or []):
                if isinstance(x, str) and x and x not in rr:
                    rr.append(x)
            if rr:
                out["reasons"] = rr
            # confidence: max
            try:
                c0 = float(last.get("confidence") or 0.0)
            except Exception:
                c0 = 0.0
            try:
                c1 = float(sp.get("confidence") or 0.0)
            except Exception:
                c1 = 0.0
            out["confidence"] = max(c0, c1)
            # source: keep last unless sp is dict/forced (more trustworthy)
            src_last = str(last.get("source") or "")
            src_new = str(sp.get("source") or "")
            if src_last != src_new and src_new in {"dict", "forced"}:
                out["source"] = src_new
            merged[-1] = out
        else:
            merged.append(sp)

    # 3) same-text dedupe within the same line: keep best span per text
    best_by_text: Dict[str, Dict[str, Any]] = {}
    for sp in merged:
        txt = str(sp.get("text") or "").strip()
        if not txt:
            continue
        cur = best_by_text.get(txt)
        if cur is None or pri(sp) < pri(cur):
            best_by_text[txt] = sp
    merged2 = list(best_by_text.values())

    # 3.5) Drop sentence-level spans that cover the whole line (too noisy for "phrase precipitation").
    # Keep:
    # - very short lines (<=6 chars)
    # - high-risk spans
    # - dict/forced spans
    filtered: List[Dict[str, Any]] = []
    line2 = re.sub(r"\s+", "", s)
    for sp in merged2:
        txt = str(sp.get("text") or "").strip()
        txt2 = re.sub(r"\s+", "", txt)
        if txt2 and line2 and txt2 == line2 and len(txt2) >= 8:
            risk = str(sp.get("risk") or "").lower()
            src = str(sp.get("source") or "")
            if (not risk.startswith("h")) and src not in {"dict", "forced"}:
                continue
        filtered.append(sp)

    # 4) final limit using existing logic (dedupe by start/end/text again + priority + max_spans)
    return _merge_dedupe_limit_spans(filtered, max_spans=max_spans)


def _spans_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    try:
        a0, a1 = int(a.get("start")), int(a.get("end"))
        b0, b1 = int(b.get("start")), int(b.get("end"))
    except Exception:
        return False
    return a0 < b1 and b0 < a1


def _any_overlap(sp: Dict[str, Any], others: List[Dict[str, Any]]) -> bool:
    for o in others or []:
        if _spans_overlap(sp, o):
            return True
    return False


def _cap_repeated_spans_by_text(
    spans_by_idx: Dict[int, List[Dict[str, Any]]],
    *,
    max_occ: int = 1,
    sources: set[str] | None = None,
) -> Dict[int, List[Dict[str, Any]]]:
    """
    Reduce repeated spans across the whole subtitle set (global dedupe), mainly to remove noise like
    nickname_pattern firing on many lines.

    - Only applies to spans whose `source` is in `sources` (default: {"pattern"}).
    - Keeps up to `max_occ` occurrences per identical `text` across all lines, prioritizing higher risk/confidence/length.
    """
    if not spans_by_idx:
        return spans_by_idx
    max_occ = max(0, int(max_occ or 0))
    if max_occ <= 0:
        return {k: [] for k in spans_by_idx.keys()}
    src_allow = sources or {"pattern"}

    def pri(sp: Dict[str, Any]) -> tuple:
        risk = str(sp.get("risk") or "").lower()
        high = 1 if risk.startswith("h") else 0
        src = str(sp.get("source") or "")
        forced = 1 if src == "forced" else 0
        try:
            conf = float(sp.get("confidence") or 0.0)
        except Exception:
            conf = 0.0
        L = len(str(sp.get("text") or ""))
        return (-high, forced, -conf, -L)

    # collect candidates
    all_occ: List[tuple[str, int, Dict[str, Any]]] = []
    for idx, spans in (spans_by_idx or {}).items():
        for sp in spans or []:
            if str(sp.get("source") or "") not in src_allow:
                continue
            txt = str(sp.get("text") or "").strip()
            if not txt:
                continue
            all_occ.append((txt, int(idx), sp))

    if not all_occ:
        return spans_by_idx

    # rank occurrences per text
    by_text: Dict[str, List[tuple[int, Dict[str, Any]]]] = {}
    for txt, idx, sp in all_occ:
        by_text.setdefault(txt, []).append((idx, sp))

    keep: set[tuple[str, int]] = set()
    for txt, occ in by_text.items():
        occ2 = sorted(occ, key=lambda x: pri(x[1]))
        for idx, _sp in occ2[:max_occ]:
            keep.add((txt, int(idx)))

    # rebuild spans_by_idx
    out: Dict[int, List[Dict[str, Any]]] = {}
    for idx, spans in spans_by_idx.items():
        kept: List[Dict[str, Any]] = []
        for sp in spans or []:
            txt = str(sp.get("text") or "").strip()
            if str(sp.get("source") or "") in src_allow and txt:
                if (txt, int(idx)) not in keep:
                    continue
            kept.append(sp)
        out[int(idx)] = kept
    return out


def _diffuse_repeated_llm_spans_across_segments(
    segments: List[Segment],
    spans_by_idx: Dict[int, List[Dict[str, Any]]],
    *,
    min_occ: int = 2,
    min_len: int = 2,
    max_len: int = 8,
    sources: set[str] | None = None,
) -> Dict[int, List[Dict[str, Any]]]:
    """
    Diffuse repeated short LLM-detected terms across the whole clip.

    Product intent:
    - If one occurrence of a likely proper noun / term is detected (e.g. 羽人), all same-text
      occurrences in the current task should also be visible in review.
    - Keep this narrow to weak-hardware-friendly, review-useful terms only.
    """
    if not segments or not spans_by_idx:
        return spans_by_idx
    src_allow = sources or {"llm", "spell"}

    def _count_occ(line: str, needle: str) -> int:
        if not line or not needle:
            return 0
        n = 0
        pos = 0
        while True:
            j = line.find(needle, pos)
            if j < 0:
                break
            n += 1
            pos = j + len(needle)
        return n

    def _find_all(line: str, needle: str) -> List[int]:
        out: List[int] = []
        if not line or not needle:
            return out
        pos = 0
        while True:
            j = line.find(needle, pos)
            if j < 0:
                break
            out.append(j)
            pos = j + len(needle)
        return out

    proto_by_text: Dict[str, Dict[str, Any]] = {}
    candidate_texts: set[str] = set()
    for idx, spans in (spans_by_idx or {}).items():
        for sp in spans or []:
            src = str(sp.get("source") or "")
            txt = str(sp.get("text") or "").strip()
            if src not in src_allow or len(txt) < min_len or len(txt) > max_len:
                continue
            if not proto_by_text.get(txt):
                proto_by_text[txt] = dict(sp)
            candidate_texts.add(txt)

    occ_by_text: Dict[str, int] = {}
    for txt in candidate_texts:
        occ_by_text[txt] = sum(_count_occ(str(getattr(seg, "text", "") or ""), txt) for seg in (segments or []))

    repeated_texts = {txt for txt, n in occ_by_text.items() if int(n) >= int(min_occ)}
    if not repeated_texts:
        return spans_by_idx

    out: Dict[int, List[Dict[str, Any]]] = {int(idx): [dict(sp) for sp in (spans or [])] for idx, spans in (spans_by_idx or {}).items()}
    for i, seg in enumerate(segments, 1):
        line = str(getattr(seg, "text", "") or "")
        cur = [dict(sp) for sp in (out.get(i, []) or [])]
        existing = {
            (int(sp.get("start", -1)), int(sp.get("end", -1)), str(sp.get("text") or "").strip())
            for sp in cur
            if isinstance(sp, dict)
        }
        for txt in repeated_texts:
            proto = proto_by_text.get(txt)
            if not proto:
                continue
            for start in _find_all(line, txt):
                end = start + len(txt)
                key = (start, end, txt)
                if key in existing:
                    continue
                existing.add(key)
                sp2 = dict(proto)
                sp2["start"] = start
                sp2["end"] = end
                sp2["text"] = txt
                meta = dict(sp2.get("meta") or {})
                meta["repeated_occurrence"] = True
                sp2["meta"] = meta
                cur.append(sp2)
        if cur:
            out[i] = _merge_dedupe_spans_same_line(line, cur, max_spans=max(3, len(cur)))
    return out


def _pick_phrase_candidate_items(
    items_all: List[tuple[int, str]],
    *,
    max_lines: int,
    include_idxs: List[int] | None = None,
) -> List[tuple[int, str]]:
    """
    Pick a bounded subset of lines for LLM phrase extraction.
    Always include `include_idxs` (e.g., rule-based suspects) when present.
    """
    if max_lines <= 0 or len(items_all) <= max_lines:
        return items_all
    include_set = set(int(x) for x in (include_idxs or []) if int(x) > 0)
    pinned: List[tuple[int, str]] = []
    rest: List[tuple[int, str]] = []
    for idx, txt in items_all:
        if int(idx) in include_set:
            pinned.append((int(idx), txt))
        else:
            rest.append((int(idx), txt))
    # If pinned already exceeds budget, keep them all. These are precisely the lines
    # product logic decided must not be dropped from zh_polish coverage.
    budget = max(1, int(max_lines))
    if len(pinned) >= budget:
        return sorted(pinned, key=lambda x: int(x[0]))
    scored = sorted(rest, key=lambda it: _phrase_candidate_score(it[1]), reverse=True)
    picked = pinned + scored[: max(0, budget - len(pinned))]
    # Stable order by idx for readability/debugging.
    picked2 = sorted(picked, key=lambda x: int(x[0]))
    return picked2


def _normalize_zh_opt_risk(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in {"high", "medium", "low"}:
        return s
    return "medium"


def _normalize_zh_opt_reasons(raw: Any) -> List[str]:
    allowed = {
        "asr_like",
        "hard_to_translate",
        "internet_slang",
        "ambiguity",
        "meaning_shift_risk",
        "rule_based_anomaly",
        "low_confidence",
        "contract_fallback",
    }
    out: List[str] = []
    vals = raw if isinstance(raw, list) else []
    for it in vals:
        s = str(it or "").strip().lower()
        if s and s in allowed and s not in out:
            out.append(s)
    return out


def _optimize_zh_lines_with_risk_llm(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    items: List[tuple[int, str]],
    request_timeout_s: int = 180,
    request_retries: int = 2,
) -> Dict[int, Dict[str, Any]]:
    """
    Product main path for zh_polish:
    do ONE structured LLM pass that returns both optimized Chinese and risk labels.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    lines = []
    for idx, text in items:
        t = clean_zh_text(str(text or "").replace("\n", " ").strip())
        lines.append(f"{int(idx)}: {t}")
    idx_block = "\n".join(lines)
    prompt = (
        "You are optimizing Chinese subtitle lines BEFORE translation.\n"
        "For each line, lightly rewrite only when it materially improves translation stability.\n"
        "\n"
        "Hard rules:\n"
        "- Keep the SAME meaning. Do NOT add/remove facts.\n"
        "- Do NOT change who did what, time order, causality, negation, or concrete spatial relations.\n"
        "- Keep natural spoken Chinese.\n"
        "- Remove only obvious fillers/repetitions conservatively.\n"
        "- If the line is already good, keep it unchanged.\n"
        "- Risk is about delivery confidence after optimization, not style preference.\n"
        "\n"
        "Use these risk levels only:\n"
        "- low: safe to continue automatically.\n"
        "- medium: understandable but may deserve a quick human glance.\n"
        "- high: likely needs human review before MT.\n"
        "\n"
        "Allowed reasons only:\n"
        "[\"asr_like\", \"hard_to_translate\", \"internet_slang\", \"ambiguity\", \"meaning_shift_risk\", \"rule_based_anomaly\", \"low_confidence\"]\n"
        "\n"
        "Output STRICT JSON only with schema:\n"
        "{\"items\":[{\"idx\":1,\"opt\":\"...\",\"changed\":false,\"risk\":\"low\",\"need_review\":false,\"reasons\":[],\"confidence\":0.0}]}\n"
        "\n"
        "Input lines:\n"
        f"{idx_block}\n"
    )
    max_tokens = min(1200, max(320, 120 * max(1, len(items))))
    format_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "idx": {"type": "integer"},
                        "opt": {"type": "string"},
                        "changed": {"type": "boolean"},
                        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                        "need_review": {"type": "boolean"},
                        "reasons": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number"},
                    },
                    "required": ["idx", "opt", "risk", "need_review"],
                    "additionalProperties": True,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You optimize Chinese subtitles before translation. Return STRICT JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": int(max_tokens),
        "options": {"num_ctx": 2048, "num_batch": 128},
        "reasoning_effort": "none",
    }
    _bump_contract_stat("zh_opt", "requests")
    data, _mode = _llm_post_chat_structured(
        endpoint,
        headers=headers,
        body=body,
        schema_name="zh_opt_items",
        schema=format_schema,
        timeout_s=max(30, int(request_timeout_s or 180)),
        retries=max(0, int(request_retries or 0)),
        stage_name="zh_opt",
    )
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    payload_candidates = _extract_message_payload_candidates(msg)
    content = payload_candidates[0] if payload_candidates else (msg.get("content", "") or "")
    s = str(content).strip()
    if not s and not payload_candidates:
        raise ValueError("zh optimize empty response")
    obj = None
    repaired_used = False
    for candidate in payload_candidates or [s]:
        try:
            obj = _load_json_candidate(candidate, allow_repair=True)
        except Exception:
            obj = None
        if isinstance(obj, str):
            obj = _load_json_candidate(obj, allow_repair=True)
        if obj is not None:
            repaired_text = _repair_json_candidate_text(candidate)
            repaired_used = bool(repaired_text) and repaired_text != _normalize_json_candidate_text(candidate)
            break
    if isinstance(obj, str):
        obj = _load_json_candidate(obj, allow_repair=True)
    if obj is None:
        _bump_contract_stat("zh_opt", "contract_invalid")
        raise ValueError("zh optimize invalid json")
    if repaired_used:
        _bump_contract_stat("zh_opt", "syntactic_repair")
    arr = obj.get("items") if isinstance(obj, dict) else None
    if not isinstance(arr, list):
        _bump_contract_stat("zh_opt", "contract_invalid")
        raise ValueError("zh optimize items not a list")
    src_by_idx = {int(idx): clean_zh_text(str(text or "").strip()) for idx, text in items}
    out: Dict[int, Dict[str, Any]] = {}
    for it in arr:
        if not isinstance(it, dict):
            continue
        try:
            idx = int(it.get("idx"))
        except Exception:
            continue
        if idx not in src_by_idx:
            continue
        src = src_by_idx[idx]
        opt = clean_zh_text(str(it.get("opt") or "").strip()) or src
        changed = bool(it.get("changed"))
        if opt == src:
            changed = False
        risk = _normalize_zh_opt_risk(it.get("risk"))
        need_review = bool(it.get("need_review"))
        reasons = _normalize_zh_opt_reasons(it.get("reasons"))
        try:
            confidence = float(it.get("confidence"))
        except Exception:
            confidence = 0.0
        if changed:
            len_ratio = abs(len(opt) - len(src)) / max(1, len(src))
            if len_ratio >= 0.6 and risk == "low":
                risk = "medium"
                if "meaning_shift_risk" not in reasons:
                    reasons.append("meaning_shift_risk")
        if risk == "high":
            need_review = True
        out[idx] = {
            "idx": idx,
            "base": src,
            "opt": opt,
            "changed": changed,
            "risk": risk,
            "need_review": need_review,
            "reasons": reasons,
            "confidence": confidence,
        }
    _bump_contract_stat("zh_opt", "success_chunks")
    return out


def _optimize_zh_lines_with_risk_llm_adaptive(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    items: List[tuple[int, str]],
    request_timeout_s: int = 180,
    request_retries: int = 2,
    label: str = "zh_opt",
) -> Dict[int, Dict[str, Any]]:
    if not items:
        return {}
    try:
        return _optimize_zh_lines_with_risk_llm(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            items=items,
            request_timeout_s=request_timeout_s,
            request_retries=request_retries,
        )
    except Exception as exc1:
        _bump_contract_stat("zh_opt", "contract_retry")
        print(f"[warn] zh_opt chunk failed for {label}; retrying once with same chunk: {exc1}")
        try:
            return _optimize_zh_lines_with_risk_llm(
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                items=items,
                request_timeout_s=request_timeout_s,
                request_retries=request_retries,
            )
        except Exception as exc2:
            if len(items) <= 1:
                raise RuntimeError(f"zh_opt contract violation at {label}: {exc2}") from exc2
            mid = max(1, len(items) // 2)
            _bump_contract_stat("zh_opt", "adaptive_splits")
            print(f"[warn] zh_opt chunk still unstable for {label}; splitting {len(items)} -> {mid}+{len(items)-mid}")
            left = _optimize_zh_lines_with_risk_llm_adaptive(
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                items=items[:mid],
                request_timeout_s=request_timeout_s,
                request_retries=request_retries,
                label=f"{label}L",
            )
            right = _optimize_zh_lines_with_risk_llm_adaptive(
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                items=items[mid:],
                request_timeout_s=request_timeout_s,
                request_retries=request_retries,
                label=f"{label}R",
            )
            merged = dict(left)
            merged.update(right)
            return merged


def _extract_zh_risky_spans_llm_two_pass(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    items: List[tuple[int, str]],
    max_spans_per_line: int,
    max_total_spans: int,
    # Second-pass controls (cautious by default)
    second_pass: bool = True,
    second_pass_max_lines: int = 8,
    second_pass_trigger_min_spans: int = 2,
    log_enabled: bool = False,
    log_prefix: str = "",
) -> Dict[int, List[Dict[str, Any]]]:
    """
    Two-pass recall strategy with bounded cost.
    - Pass1: normal extraction.
    - Pass2: only when Pass1 spans_total is very low, run extraction on a small subset of high-score lines
      that currently have no spans. This improves recall without blowing up compute.
    """
    try:
        got1 = _extract_zh_risky_spans_llm(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            items=items,
            max_spans_per_line=max_spans_per_line,
            max_total_spans=max_total_spans,
        )
    except Exception as exc:
        if log_enabled:
            try:
                print(f"{log_prefix} phrase_extract pass1 skipped: {exc}")
            except Exception:
                pass
        got1 = {}
    by_idx: Dict[int, List[Dict[str, Any]]] = dict(got1 or {})
    try:
        spans_total = sum(len(v or []) for v in by_idx.values())
    except Exception:
        spans_total = 0
    if not second_pass:
        return by_idx
    if spans_total >= int(second_pass_trigger_min_spans):
        return by_idx
    # Candidate lines: those without spans yet.
    cand: List[tuple[int, str]] = []
    for idx, txt in items:
        if int(idx) in by_idx:
            continue
        t = str(txt or "").strip()
        if not t:
            continue
        cand.append((int(idx), t))
    if not cand:
        return by_idx
    picked = sorted(cand, key=lambda it: _phrase_candidate_score(it[1]), reverse=True)[: max(1, int(second_pass_max_lines))]
    if log_enabled:
        try:
            print(f"{log_prefix} phrase_extract second-pass: pass1_spans={spans_total} boost_lines={len(picked)}/{len(items)}")
        except Exception:
            pass
    try:
        got2 = _extract_zh_risky_spans_llm(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            items=picked,
            # Keep bounded; allow a bit more per line but cap totals.
            max_spans_per_line=max(2, int(max_spans_per_line)),
            max_total_spans=min(int(max_total_spans), 18),
        )
    except Exception as exc:
        if log_enabled:
            try:
                print(f"{log_prefix} phrase_extract second-pass skipped: {exc}")
            except Exception:
                pass
        got2 = {}
    for k, v in (got2 or {}).items():
        if v:
            by_idx[int(k)] = v
    return by_idx


def _detect_asr_dirty_sentence_signals(text: str) -> List[str]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    reasons: List[str] = []
    confusable_terms = _generic_confusable_terms(compact)
    reaction_windows = _generic_reaction_windows(compact)
    project_hits = _project_confusion_hits(compact)
    if confusable_terms:
        reasons.append("疑似ASR脏词/生造词")
    if _has_confusable_after_function_word(compact, confusable_terms):
        reasons.append("疑似不通顺搭配")
    if reaction_windows:
        reasons.append("疑似动宾搭配异常")
    if _has_generic_dangling_tail(compact):
        reasons.append("疑似动词缺失/错置")
    if project_hits:
        reasons.append("疑似项目高频混淆")
    if 3 <= len(compact) <= 16 and (confusable_terms or _has_confusable_after_function_word(compact, confusable_terms) or reaction_windows):
        reasons.append("短句但含异常词")
    return reasons[:4]


def _detect_repair_candidate_reason(text: str) -> List[str]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    reasons: List[str] = []
    if re.search(r"(羽人|公主|西域|中原|四大天王|女巫手下|小土豆|杂兵)", compact):
        reasons.append("疑似专名/称谓一致性")
    if _project_confusion_hits(compact):
        reasons.append("疑似项目高频混淆")
    return reasons[:2]


def _rule_based_suspect(seg: Segment) -> List[str]:
    """
    Cheap, stable suspects for zh gate (works even when LLM is unavailable).
    Returns reasons[] (empty means not a suspect by rules).
    """
    t = str(getattr(seg, "text", "") or "")
    dur = max(float(seg.end) - float(seg.start), 0.0)
    compact = re.sub(r"\s+", "", clean_zh_text(t))
    reasons: List[str] = []
    if "\uFFFD" in t or "�" in t:
        reasons.append("乱码/异常字符")
    if re.search(r"([！？。；，])\1\1+", t):
        reasons.append("重复标点")
    if len(compact) <= 2 and dur >= 3.0:
        reasons.append("文本极短但时长较长")
    if len(compact) >= 28 and dur <= 0.8:
        reasons.append("文本较长但时长较短")
    reasons.extend(_detect_asr_dirty_sentence_signals(compact))
    reasons.extend(_detect_repair_candidate_reason(compact))
    # preserve order while deduping
    out: List[str] = []
    for r in reasons:
        rr = str(r or "").strip()
        if rr and rr not in out:
            out.append(rr)
    return out[:4]


_DEFAULT_SAME_PINYIN_PATH = "assets/zh_phrase/pycorrector_same_pinyin.txt"
_DEFAULT_SAME_STROKE_PATH = "assets/zh_phrase/pycorrector_same_stroke.txt"
_DEFAULT_REPAIR_LEXICON_PATH = "assets/zh_phrase/chinese_xinhua_ci_2to4.txt"
_DEFAULT_REPAIR_PROPER_NOUNS_PATH = "assets/zh_phrase/thuocl_proper_nouns.txt"
_DEFAULT_PROJECT_CONFUSIONS_PATH = "assets/zh_phrase/asr_project_confusions.json"
_GENERIC_FUNCTION_WORDS = ("于", "把", "被", "给", "向", "对", "跟", "从", "在", "为", "往")
_GENERIC_DANGLING_TAILS = ("重新", "继续", "开始", "准备", "正在")
_GENERIC_REACTION_PAT = re.compile(r"(连连|不断|纷纷)(叫|喊|说|哭|笑|骂|打|跑|走|飞|跳)[\u4e00-\u9fff]{0,1}")
_GENERIC_VALID_REACTION_TAILS = {"叫苦", "叫喊", "叫唤", "叫嚷", "叫屈", "叫冤", "哭喊", "哭叫", "喊叫"}
_GENERIC_SHORT_TOKEN_SKIP = {
    "我们",
    "你们",
    "他们",
    "一个",
    "这个",
    "那个",
    "这里",
    "那里",
    "这样",
    "那样",
    "然后",
    "但是",
    "因为",
    "所以",
}


def _generic_confusable_terms(text: str, *, max_terms: int = 4) -> List[str]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    lexicon_words = _load_zh_word_set(_DEFAULT_REPAIR_LEXICON_PATH, min_len=2, max_len=4)
    proper_nouns = _load_zh_word_set(_DEFAULT_REPAIR_PROPER_NOUNS_PATH, min_len=2, max_len=8)
    homo_map = _load_same_pinyin_char_map(_DEFAULT_SAME_PINYIN_PATH)
    shape_map = _load_same_stroke_char_map(_DEFAULT_SAME_STROKE_PATH)
    out: List[str] = []
    seen: Set[str] = set()
    for length in (3, 2):
        mask_index = _load_zh_word_mask_index(_DEFAULT_REPAIR_LEXICON_PATH, word_len=length)
        for i in range(0, max(0, len(compact) - length + 1)):
            sub = compact[i : i + length]
            if sub in seen or sub in _GENERIC_SHORT_TOKEN_SKIP:
                continue
            if not re.fullmatch(r"[\u4e00-\u9fff]+", sub):
                continue
            if sub in lexicon_words or sub in proper_nouns:
                continue
            has_confusable = False
            for pos in range(length):
                mask = sub[:pos] + "*" + sub[pos + 1 :]
                for cand in (mask_index.get(mask) or []):
                    if cand == sub:
                        continue
                    changed_chars = sum(1 for a, b in zip(sub, cand) if a != b)
                    if changed_chars <= 0:
                        continue
                    if max(_same_pinyin_change_count(sub, cand, homo_map), _same_pinyin_change_count(sub, cand, shape_map)) == changed_chars:
                        has_confusable = True
                        break
                if has_confusable:
                    break
            if not has_confusable:
                continue
            seen.add(sub)
            out.append(sub)
            if len(out) >= int(max_terms):
                return out
    return out


def _has_confusable_after_function_word(compact: str, confusable_terms: List[str]) -> bool:
    for fw in _GENERIC_FUNCTION_WORDS:
        for term in confusable_terms:
            if fw + term in compact:
                return True
    return False


def _has_generic_dangling_tail(compact: str) -> bool:
    return any(compact.endswith(tok) for tok in _GENERIC_DANGLING_TAILS)


def _generic_reaction_windows(text: str) -> List[str]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    out: List[str] = []
    for m in _GENERIC_REACTION_PAT.finditer(compact):
        frag = str(m.group(0) or "")
        if len(frag) >= 2:
            tail = frag[-2:]
            if re.fullmatch(r"[\u4e00-\u9fff]{2}", tail) and tail not in _GENERIC_VALID_REACTION_TAILS and tail not in out:
                out.append(tail)
    return out


def _zh_repair_line_badness(text: str) -> float:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return 0.0
    score = 0.0
    confusable_terms = _generic_confusable_terms(compact)
    project_hits = _project_confusion_hits(compact)
    score += 4.0 * len(_detect_asr_dirty_sentence_signals(compact))
    if confusable_terms:
        score += 2.5 + min(2.0, 0.7 * len(confusable_terms))
    if project_hits:
        score += 2.8 + min(2.0, 0.8 * len(project_hits))
    if _has_confusable_after_function_word(compact, confusable_terms):
        score += 2.2
    if _generic_reaction_windows(compact):
        score += 1.8
    if _has_generic_dangling_tail(compact):
        score += 2.0
    return score


def _same_pinyin_change_count(src: str, tgt: str, homo_map: Dict[str, List[str]]) -> int:
    if len(src) != len(tgt):
        return 0
    count = 0
    for a, b in zip(src, tgt):
        if a == b:
            continue
        if b in (homo_map.get(a) or []):
            count += 1
    return count


def _contextual_repair_bonus(line: str, src: str, cand: str) -> float:
    compact = re.sub(r"\s+", "", clean_zh_text(str(line or "")))
    if not compact or src == cand:
        return 0.0
    bonus = 0.0
    if src.startswith("叫") and cand.startswith("叫") and re.search(r"(连连|百姓|众人|村民|路人)", compact):
        reaction_bonus = {
            "叫苦": 3.4,
            "叫喊": 3.0,
            "叫唤": 2.9,
            "叫嚷": 2.8,
            "叫屈": 2.7,
            "叫冤": 2.7,
        }
        if cand in reaction_bonus:
            bonus += reaction_bonus[cand]
        elif cand.endswith(("跑", "冲", "跳", "飞")):
            bonus -= 1.8
    return bonus


def _collect_local_repair_windows(line: str, spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    s = str(line or "")
    if not s:
        return []
    out: List[Dict[str, Any]] = []
    seen: Set[Tuple[int, int, str]] = set()

    def _append_window(start: int, end: int, text: str, *, explicit: Optional[List[str]] = None) -> None:
        key = (int(start), int(end), str(text or ""))
        if start < 0 or end <= start or not text:
            return
        if key in seen:
            for item in out:
                if (item["start"], item["end"], item["text"]) != key:
                    continue
                cur = item.setdefault("explicit", [])
                for cand in explicit or []:
                    c = str(cand or "").strip()
                    if c and c not in cur:
                        cur.append(c)
                break
            return
        seen.add(key)
        out.append(
            {
                "start": int(start),
                "end": int(end),
                "text": str(text or ""),
                "explicit": [str(x).strip() for x in (explicit or []) if str(x).strip()],
            }
        )

    for sp in spans or []:
        if not isinstance(sp, dict):
            continue
        text = str(sp.get("text") or "").strip()
        if not text or len(text) < 2:
            continue
        try:
            start = int(sp.get("start"))
            end = int(sp.get("end"))
        except Exception:
            start = s.find(text)
            end = start + len(text) if start >= 0 else -1
        if not (0 <= start < end <= len(s)) or s[start:end] != text:
            start = s.find(text)
            end = start + len(text) if start >= 0 else -1
        if start < 0 or end <= start:
            continue
        explicit: List[str] = []
        meta = sp.get("meta") or {}
        suggest = str(meta.get("suggest") or "").strip() if isinstance(meta, dict) else ""
        if suggest and len(suggest) == len(text):
            explicit.append(suggest)
        _append_window(start, end, text, explicit=explicit)

    for term in _generic_confusable_terms(s):
        start = 0
        while start < len(s):
            at = s.find(term, start)
            if at < 0:
                break
            _append_window(int(at), int(at + len(term)), term)
            start = at + len(term)
    for term in _generic_reaction_windows(s):
        start = 0
        while start < len(s):
            at = s.find(term, start)
            if at < 0:
                break
            _append_window(int(at), int(at + len(term)), term)
            start = at + len(term)
    for item in _project_confusion_hits(s):
        wrong = str(item.get("wrong") or "").strip()
        explicit = [str(x).strip() for x in (item.get("candidates") or []) if str(x).strip()]
        if not wrong or not explicit:
            continue
        start = 0
        while start < len(s):
            at = s.find(wrong, start)
            if at < 0:
                break
            _append_window(int(at), int(at + len(wrong)), wrong, explicit=explicit)
            start = at + len(wrong)
    return out


def _pick_local_zh_repair(
    *,
    line: str,
    spans: List[Dict[str, Any]],
    rule_reasons: List[str],
    same_pinyin_path: str,
    lexicon_path: str,
    proper_nouns_path: str,
) -> Tuple[str, List[str]]:
    s = str(line or "").strip()
    if not s:
        return "", []
    base_badness = _zh_repair_line_badness(s)
    if base_badness <= 0 and not rule_reasons:
        return "", []

    homo_map = _load_same_pinyin_char_map(same_pinyin_path)
    lexicon_words = _load_zh_word_set(lexicon_path, min_len=2, max_len=4)
    proper_nouns = _load_zh_word_set(proper_nouns_path, min_len=2, max_len=8)
    windows = _collect_local_repair_windows(s, spans)
    if not windows:
        return "", []

    scored: List[Tuple[float, str, str]] = []
    for win in windows:
        start = int(win.get("start", -1))
        end = int(win.get("end", -1))
        src = str(win.get("text") or "")
        if not (0 <= start < end <= len(s)) or s[start:end] != src:
            continue
        explicit = [str(x).strip() for x in (win.get("explicit") or []) if str(x).strip()]
        candidates: List[str] = []
        for cand in explicit:
            if cand not in candidates:
                candidates.append(cand)
        if 2 <= len(src) <= 3 and re.fullmatch(r"[\u4e00-\u9fff]+", src):
            mask_index = _load_zh_word_mask_index(lexicon_path, word_len=len(src))
            for pos in range(len(src)):
                mask = src[:pos] + "*" + src[pos + 1 :]
                for cand in (mask_index.get(mask) or []):
                    if cand != src and cand not in candidates:
                        candidates.append(cand)
        for cand in candidates:
            if len(cand) != len(src) or cand == src:
                continue
            if cand in proper_nouns and "疑似专名/称谓一致性" not in (rule_reasons or []):
                continue
            changed_chars = sum(1 for a, b in zip(src, cand) if a != b)
            if changed_chars <= 0:
                continue
            cand_line = s[:start] + cand + s[end:]
            cand_badness = _zh_repair_line_badness(cand_line)
            same_pinyin_changes = _same_pinyin_change_count(src, cand, homo_map)
            context_bonus = _contextual_repair_bonus(s, src, cand)
            if cand not in explicit and len(src) <= 3:
                short_candidate_ok = False
                if "疑似专名/称谓一致性" in (rule_reasons or []) and cand in proper_nouns:
                    short_candidate_ok = True
                elif same_pinyin_changes == changed_chars and changed_chars > 0 and cand in lexicon_words and context_bonus >= 2.8:
                    short_candidate_ok = True
                elif cand in lexicon_words and context_bonus >= 2.8:
                    short_candidate_ok = True
                if not short_candidate_ok:
                    continue
            score = (base_badness - cand_badness) * 2.8
            if src not in lexicon_words and cand in lexicon_words:
                score += 1.8
            if cand in lexicon_words:
                score += 0.6
            if same_pinyin_changes == changed_chars and changed_chars > 0:
                score += 1.4
            elif same_pinyin_changes > 0:
                score += 0.7
            if changed_chars == 1:
                score += 0.8
            if cand in explicit:
                score += 2.2
            score += context_bonus
            if cand_badness >= base_badness and cand not in explicit and same_pinyin_changes == 0:
                score -= 2.5
            if score <= 0:
                continue
            scored.append((score, cand_line, f"{src}->{cand}"))

    if not scored:
        return "", []
    scored.sort(key=lambda it: (-it[0], len(it[1]), it[2]))
    best_score, best_line, _best_hint = scored[0]
    hints: List[str] = []
    for _score, _line, hint in scored[:3]:
        if hint not in hints:
            hints.append(hint)
    if best_score >= 5.5 and _zh_repair_line_badness(best_line) < base_badness:
        return best_line, hints
    return "", hints


def _extract_single_replacement(base: str, opt: str) -> Tuple[str, str]:
    base_s = str(base or "").strip()
    opt_s = str(opt or "").strip()
    if base_s == opt_s:
        return "", ""
    prefix = 0
    while prefix < len(base_s) and prefix < len(opt_s) and base_s[prefix] == opt_s[prefix]:
        prefix += 1
    suffix = 0
    max_suffix = min(len(base_s) - prefix, len(opt_s) - prefix)
    while suffix < max_suffix and base_s[len(base_s) - 1 - suffix] == opt_s[len(opt_s) - 1 - suffix]:
        suffix += 1
    base_mid = base_s[prefix : len(base_s) - suffix if suffix else len(base_s)]
    opt_mid = opt_s[prefix : len(opt_s) - suffix if suffix else len(opt_s)]
    return base_mid, opt_mid


def _hint_targets_from_pairs(hints: List[str]) -> Set[str]:
    out: Set[str] = set()
    for hint in hints or []:
        s = str(hint or "").strip()
        if "->" not in s:
            continue
        _src, tgt = s.split("->", 1)
        tgt2 = str(tgt or "").strip()
        if tgt2:
            out.add(tgt2)
    return out


def _should_accept_llm_polish(
    *,
    base: str,
    opt: str,
    rule_reasons: List[str],
    local_hints: List[str],
    same_pinyin_path: str,
    lexicon_path: str,
    proper_nouns_path: str,
) -> bool:
    base_s = str(base or "").strip()
    opt_s = str(opt or "").strip()
    if not opt_s or base_s == opt_s:
        return False
    if _zh_repair_line_badness(opt_s) >= _zh_repair_line_badness(base_s):
        return False
    base_mid, opt_mid = _extract_single_replacement(base_s, opt_s)
    if not base_mid and not opt_mid:
        return False
    if re.sub(r"[，。！？；：、,.!?;:\s]", "", base_s) == re.sub(r"[，。！？；：、,.!?;:\s]", "", opt_s):
        return True

    homo_map = _load_same_pinyin_char_map(same_pinyin_path)
    lexicon_words = _load_zh_word_set(lexicon_path, min_len=2, max_len=4)
    proper_nouns = _load_zh_word_set(proper_nouns_path, min_len=2, max_len=8)
    hint_targets = _hint_targets_from_pairs(local_hints)
    changed_chars = sum(1 for a, b in zip(base_mid, opt_mid) if a != b) if len(base_mid) == len(opt_mid) else max(len(base_mid), len(opt_mid))

    if opt_mid in hint_targets or any(tgt and tgt in opt_s for tgt in hint_targets):
        return True
    if "疑似专名/称谓一致性" in (rule_reasons or []) and opt_mid in proper_nouns and len(opt_mid) <= 8:
        return True
    if len(base_mid) == len(opt_mid) and 1 <= len(base_mid) <= 3:
        same_pinyin_changes = _same_pinyin_change_count(base_mid, opt_mid, homo_map)
        context_bonus = _contextual_repair_bonus(base_s, base_mid, opt_mid)
        if same_pinyin_changes == changed_chars and changed_chars > 0 and opt_mid in lexicon_words and context_bonus >= 2.8:
            return True
        if opt_mid in lexicon_words and context_bonus >= 2.8:
            return True
        return False
    if len(base_mid) == len(opt_mid) and changed_chars <= 2 and changed_chars > 0 and opt_mid in lexicon_words:
        return _same_pinyin_change_count(base_mid, opt_mid, homo_map) >= 1
    if abs(len(base_mid) - len(opt_mid)) <= 1 and max(len(base_mid), len(opt_mid)) <= 4:
        function_particles = {"了", "着", "的", "地", "得"}
        if any(ch in function_particles for ch in (base_mid + opt_mid)):
            return True
    return False


def _zh_opt_candidate_score(text: str, rule_reasons: Optional[List[str]] = None) -> int:
    t = clean_zh_text(str(text or ""))
    compact = re.sub(r"\s+", "", t)
    if not compact:
        return -999
    score = 0
    rr = list(rule_reasons or [])
    if rr:
        score += 100 + min(20, 4 * len(rr))
    n = len(compact)
    clause_marks = len(re.findall(r"[，、；：…]", compact))
    filler_hits = len(re.findall(r"[啊呀吧呢嘛呗欸诶哈啦咯哇哦噢]", compact))
    confusable_terms = _generic_confusable_terms(compact)
    project_hits = _project_confusion_hits(compact)
    if n >= 22:
        score += 5
    elif n >= 14:
        score += 2
    score += min(6, clause_marks * 2)
    score += min(3, filler_hits)
    if re.search(r"(就是|那个|然后|所以|其实|你知道|怎么说|相当于|这玩意|那玩意)", compact):
        score += 2
    if re.search(r"[A-Za-z0-9]{4,}", t):
        score += 2
    if re.search(r"[“”\"'《》【】（）()]", t):
        score += 1
    if n >= 10 and not re.search(r"[。！？!?]$", compact):
        score += 2
    if re.search(r"[，、；：]$", compact):
        score += 3
    if confusable_terms:
        score += 8 + min(4, 2 * len(confusable_terms))
    if project_hits:
        score += 7 + min(4, 2 * len(project_hits))
    if _has_confusable_after_function_word(compact, confusable_terms):
        score += 6
    if _generic_reaction_windows(compact):
        score += 4
    if _has_generic_dangling_tail(compact):
        score += 5
    if 4 <= n <= 18 and (project_hits or confusable_terms or _has_confusable_after_function_word(compact, confusable_terms)):
        score += 6
    return score


def _is_zh_opt_candidate(text: str, rule_reasons: Optional[List[str]] = None) -> bool:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return False
    rr = list(rule_reasons or [])
    if rr:
        return True
    end_ok = bool(re.search(r"[。！？!?]$", compact))
    clause_marks = len(re.findall(r"[，、；：…]", compact))
    filler_like = bool(re.search(r"(就是|那个|然后|所以|其实|你知道|怎么说|相当于)", compact))
    mixed_token = bool(re.search(r"[A-Za-z0-9]{4,}", compact))
    quoted_like = bool(re.search(r"[“”\"'《》【】（）()]", compact))
    confusable_terms = _generic_confusable_terms(compact)
    project_hits = _project_confusion_hits(compact)
    anomaly_hint = bool(
        project_hits
        or
        confusable_terms
        or _has_confusable_after_function_word(compact, confusable_terms)
        or _generic_reaction_windows(compact)
        or _has_generic_dangling_tail(compact)
    )
    score = _zh_opt_candidate_score(compact, rr)
    if anomaly_hint:
        return True
    if len(compact) <= 16 and not end_ok and score >= 4:
        return True
    if len(compact) <= 8 and end_ok and clause_marks == 0 and not filler_like and not mixed_token:
        return False
    if len(compact) <= 16 and end_ok and clause_marks <= 1 and not filler_like and not mixed_token and not quoted_like:
        return False
    if len(compact) <= 24 and end_ok and clause_marks <= 1 and not filler_like and not mixed_token and not quoted_like:
        return False
    if len(compact) <= 28 and end_ok and clause_marks <= 1 and not filler_like and not mixed_token and score < 5:
        return False
    return score >= 5


def _zh_opt_effective_max_lines(total_items: int, configured_max: int) -> int:
    """
    Keep short videos from spending too much time polishing low-value lines.
    Rule-based anomalies are still pinned later by `_pick_phrase_candidate_items`.
    """
    total = max(0, int(total_items or 0))
    budget = int(configured_max or 0)
    if total <= 0 or budget <= 0:
        return budget
    if total <= 8:
        return min(budget, 6)
    if total <= 16:
        return min(budget, 8)
    if total <= 32:
        return min(budget, 10)
    if total <= 64:
        return min(budget, 14)
    return budget


def _select_zh_opt_candidate_items(
    items_all: List[tuple[int, str]],
    *,
    rule_reasons_by_idx: Dict[int, List[str]],
    max_lines: int,
) -> List[tuple[int, str]]:
    filtered: List[tuple[int, str]] = []
    for idx, text in items_all:
        rr = rule_reasons_by_idx.get(int(idx), [])
        if _is_zh_opt_candidate(text, rr):
            filtered.append((int(idx), text))
    if not filtered:
        return []
    if max_lines <= 0 or len(filtered) <= max_lines:
        return filtered
    include_idxs = list(rule_reasons_by_idx.keys())
    if include_idxs and len(include_idxs) >= max_lines:
        include_set = set(int(x) for x in include_idxs if int(x) > 0)
        return [it for it in filtered if int(it[0]) in include_set]
    return _pick_phrase_candidate_items(filtered, max_lines=max_lines, include_idxs=include_idxs)


def _build_zh_opt_fallback_item(idx: int, text: str, rule_reasons: Optional[List[str]] = None) -> Dict[str, Any]:
    base = clean_zh_text(str(text or ""))
    compact = re.sub(r"\s+", "", base)
    rule_rr = list(rule_reasons or [])
    reasons: List[str] = ["contract_fallback"]
    risk = "medium"
    need_review = False
    if rule_rr:
        need_review = True
        if "rule_based_anomaly" not in reasons:
            reasons.append("rule_based_anomaly")
    if any(
        str(rr) in {
            "乱码/异常字符",
            "重复标点",
            "疑似ASR脏词/生造词",
            "疑似不通顺搭配",
            "疑似动宾搭配异常",
            "疑似动词缺失/错置",
            "短句但含异常词",
        }
        for rr in rule_rr
    ) or re.search(r"[�\uFFFD]", base):
        risk = "high"
        need_review = True
    elif re.search(r"[，、；：]$", compact) or (len(compact) >= 24 and not re.search(r"[。！？!?]$", compact)):
        risk = "high"
        need_review = True
        if "meaning_shift_risk" not in reasons:
            reasons.append("meaning_shift_risk")
    return {
        "idx": int(idx),
        "base": base,
        "opt": base,
        "changed": False,
        "risk": risk,
        "need_review": need_review,
        "reasons": reasons,
        "confidence": 0.0,
    }


def _suspect_severity(item: Dict[str, Any]) -> str:
    risk = str(item.get("risk") or "").strip().lower()
    if risk in {"high", "medium", "low"}:
        return "high" if risk == "high" else "medium"
    spans = item.get("spans") or []
    for sp in spans:
        if isinstance(sp, dict) and str(sp.get("risk") or "").lower().startswith("h"):
            return "high"
    reasons = item.get("rule_reasons") or []
    if any(
        str(rr or "").strip()
        in {"乱码/异常字符", "疑似ASR脏词/生造词", "疑似不通顺搭配", "疑似动宾搭配异常", "疑似动词缺失/错置", "短句但含异常词"}
        for rr in reasons
    ):
        return "high"
    return "medium"


def _build_zh_gate_summary(
    suspects: List[Dict[str, Any]],
    *,
    phrase_error: str,
    min_high_risk: int,
    min_total_suspects: int,
    pause_on_phrase_error: bool,
) -> Dict[str, Any]:
    total = len(suspects)
    high_risk = 0
    medium_risk = 0
    for item in suspects:
        if _suspect_severity(item) == "high":
            high_risk += 1
        else:
            medium_risk += 1
    reasons: List[str] = []
    should_pause = False
    if pause_on_phrase_error and str(phrase_error or "").strip():
        should_pause = True
        reasons.append("phrase_error")
    if int(min_high_risk or 0) > 0 and high_risk >= int(min_high_risk or 0):
        should_pause = True
        reasons.append(f"high_risk>={int(min_high_risk or 0)}")
    if int(min_total_suspects or 0) > 0 and total >= int(min_total_suspects or 0):
        should_pause = True
        reasons.append(f"total_suspects>={int(min_total_suspects or 0)}")
    return {
        "total_suspects": total,
        "high_risk_suspects": high_risk,
        "medium_risk_suspects": medium_risk,
        "phrase_error": str(phrase_error or ""),
        "pause_reasons": reasons,
        "should_pause": should_pause,
    }


def _lock_line_by_spans(line: str, spans: List[Dict[str, Any]]) -> tuple[str, List[str]]:
    """
    Wrap spans with <<LOCKk>>...<</LOCKk>>. Spans must be non-overlapping and within line.
    Returns (locked_line, locked_texts).
    """
    if not spans:
        return line, []
    # sort by start asc, then length desc (avoid nesting surprises)
    ss = sorted(spans, key=lambda x: (int(x.get("start", 0)), -int(x.get("end", 0))))
    out = ""
    locked_texts: List[str] = []
    cur = 0
    k = 0
    for sp in ss:
        try:
            st = int(sp.get("start"))
            ed = int(sp.get("end"))
        except Exception:
            continue
        if st < cur or ed <= st or ed > len(line):
            continue
        out += line[cur:st]
        token_l = f"<<LOCK{k}>>"
        token_r = f"<</LOCK{k}>>"
        frag = line[st:ed]
        out += token_l + frag + token_r
        locked_texts.append(frag)
        cur = ed
        k += 1
    out += line[cur:]
    return out, locked_texts


def _constrained_zh_polish_llm(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    items: List[tuple[int, str]],
    notes_by_idx: Optional[Dict[int, str]] = None,
    request_timeout_s: int = 180,
    request_retries: int = 2,
) -> Dict[int, str]:
    """
    P2: constrained zh polish. Input lines may contain <<LOCKk>>...<</LOCKk>> blocks.
    Returns idx -> opt line (may still include locks; caller should strip).
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    lines = []
    for idx, text in items:
        t = str(text or "").replace("\n", " ").strip()
        note = str((notes_by_idx or {}).get(int(idx)) or "").strip()
        if note:
            lines.append(f"{int(idx)}: {t} || {note}")
        else:
            lines.append(f"{int(idx)}: {t}")
    idx_block = "\n".join(lines)
    prompt = (
        "You are repairing Chinese subtitle source text BEFORE translation.\n"
        "Only rewrite when there is a clear source-side benefit. If a line is obviously awkward or corrupted, prefer the smallest natural fix instead of leaving it broken.\n"
        "\n"
        "Priorities (highest to lowest):\n"
        "1) obvious ASR mistakes / malformed wording / broken collocations\n"
        "2) stable proper noun or title consistency inside the clip\n"
        "3) very small punctuation cleanup only when it materially improves readability\n"
        "\n"
        "Hard constraints:\n"
        "- Keep the SAME meaning. Do NOT add/remove facts.\n"
        "- Do NOT change who did what, time order, causality, negation, or concrete relations.\n"
        "- Change as few characters as possible.\n"
        "- Do NOT merge or split lines; output one opt per input idx.\n"
        "- Do NOT rewrite a line only because an idiom / literary phrase may be hard to translate.\n"
        "- Do NOT make cosmetic rewrites that keep the same wording.\n"
        "- When local_hints are provided, treat them only as candidate directions, not mandatory answers.\n"
        "\n"
        "Output JSON only, schema:\n"
        "{\"items\":[{\"idx\":1,\"opt\":\"...\",\"need_review\":false,\"reasons\":[],\"confidence\":0.0}]}\n"
        "\n"
        "Input lines (idx: text_with_notes):\n"
        + idx_block
        + "\n"
    )
    # Cap token budget to avoid slow/hanging models; output is short JSON lines.
    max_tokens = min(900, max(240, 80 * max(1, len(items))))
    format_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "idx": {"type": "integer"},
                        "opt": {"type": "string"},
                        "need_review": {"type": "boolean"},
                        "reasons": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number"},
                    },
                    "required": ["idx", "opt"],
                    "additionalProperties": True,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are polishing Chinese subtitles BEFORE translation. Return STRICT JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": int(max_tokens),
        "options": {"num_ctx": 2048, "num_batch": 128},
        "format": format_schema,
        "reasoning_effort": "none",
    }
    data = _llm_post_chat(
        endpoint,
        headers=headers,
        body=body,
        timeout_s=max(30, int(request_timeout_s or 180)),
        retries=max(0, int(request_retries or 0)),
    )
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    s = str(content).strip()
    if not s:
        raise ValueError("zh polish empty response")
    try:
        obj = json.loads(s)
    except Exception:
        # Best-effort: some models may wrap JSON with stray text.
        i0 = s.find("{")
        i1 = s.rfind("}")
        if i0 >= 0 and i1 > i0:
            obj = json.loads(s[i0 : i1 + 1])
        else:
            raise
    if not isinstance(obj, dict):
        raise ValueError("zh polish json not an object")
    arr = obj.get("items") or []
    if not isinstance(arr, list):
        raise ValueError("zh polish items not a list")
    out: Dict[int, str] = {}
    for it in arr:
        if not isinstance(it, dict):
            continue
        try:
            idx = int(it.get("idx"))
        except Exception:
            continue
        opt = str(it.get("opt") or "").strip()
        if opt:
            out[idx] = opt
    return out

def split_segments_for_subtitles(segments: List[Segment], max_chars: int = 50) -> List[Segment]:
    """
    Split long segments into smaller ones for better subtitles/translation.
    Uses punctuation-first, then hard split by character length.
    """
    if not segments:
        return segments

    def split_text(text: str) -> List[str]:
        t = re.sub(r"\s+", " ", text).strip()
        if not t:
            return [""]
        if len(t) <= max_chars:
            return [t]
        # split by common Chinese/English punctuation
        parts = [p.strip() for p in re.split(r"(?<=[。！？；，,\.!?;])\s*", t) if p.strip()]
        out: List[str] = []
        for p in parts:
            if len(p) <= max_chars:
                out.append(p)
            else:
                # hard split long chunk
                for i in range(0, len(p), max_chars):
                    chunk = p[i : i + max_chars].strip()
                    if chunk:
                        out.append(chunk)
        return out or [t]

    out_segments: List[Segment] = []
    for seg in segments:
        pieces = split_text(seg.text)
        if len(pieces) <= 1:
            out_segments.append(seg)
            continue
        total = sum(max(len(p), 1) for p in pieces)
        dur = max(seg.end - seg.start, 0.001)
        cursor = seg.start
        for i, p in enumerate(pieces):
            frac = max(len(p), 1) / total
            piece_dur = dur * frac
            # last piece ends exactly at seg.end
            end = seg.end if i == len(pieces) - 1 else cursor + piece_dur
            out_segments.append(Segment(start=float(cursor), end=float(end), text=p))
            cursor = end
    return out_segments

# ----------------------
# P0: Subtitle post-process + TTS script separation
# ----------------------
_WS_RE = re.compile(r"\s+")
_BRACKET_RE = re.compile(r"(\([^)]*\)|\[[^\]]*\]|\{[^}]*\}|（[^）]*）|【[^】]*】|《[^》]*》)")


def _normalize_en_line(s: str) -> str:
    # collapse whitespace/newlines
    return _WS_RE.sub(" ", (s or "").replace("\n", " ")).strip()


def _wrap_en_for_subtitle(s: str, *, max_chars_per_line: int, max_lines: int = 2) -> str:
    """
    Soft-wrap English into <= max_lines lines to reduce long-line warnings.
    NOTE: CPS is computed with newlines replaced by spaces, so wrapping does NOT reduce CPS.
    """
    t = _normalize_en_line(s)
    if not t or max_lines <= 1 or len(t) <= max_chars_per_line:
        return t
    words = t.split(" ")
    if len(words) <= 1:
        # hard wrap
        return "\n".join([t[i : i + max_chars_per_line] for i in range(0, min(len(t), max_chars_per_line * max_lines), max_chars_per_line)])
    # Greedy pack words into lines
    lines: List[str] = []
    cur: List[str] = []
    for w in words:
        if not cur:
            cur = [w]
            continue
        cand = (" ".join(cur + [w])).strip()
        # If we already have max_lines-1 finished lines, keep everything in the last line
        # (we will clamp it later). This avoids creating more lines than allowed.
        if len(lines) >= max_lines - 1:
            cur.append(w)
            continue
        if len(cand) <= max_chars_per_line:
            cur.append(w)
        else:
            lines.append(" ".join(cur).strip())
            cur = [w]
    if cur:
        lines.append(" ".join(cur).strip())
    # Trim to max_lines and max_chars_per_line per line (hard clamp, best-effort)
    lines = [ln.strip() for ln in lines if ln.strip()][:max_lines]
    clamped: List[str] = []
    for ln in lines:
        if len(ln) <= max_chars_per_line:
            clamped.append(ln)
        else:
            clamped.append(ln[: max_chars_per_line - 1].rstrip() + "…")
    return "\n".join(clamped).strip()

def _build_tts_script(en: str) -> str:
    """
    Minimal "TTS稿" generation (P0):
    - strip bracketed asides
    - normalize whitespace
    - keep punctuation (for pauses)
    - ensure it isn't empty after cleaning
    """
    t = str(en or "")
    t = t.replace("&", " and ")
    t = _BRACKET_RE.sub(" ", t)
    t = _normalize_en_line(t)
    # keep sentence ending punctuation for better prosody
    if t and not re.search(r"[.!?]$", t):
        t = t + "."
    # final cleanup using the shared lite cleaner (removes CJK/fullwidth and junk)
    try:
        t = clean_tts_text(t)
    except Exception:
        t = _normalize_en_line(t)
    return t


def _final_en_guardrail(en: str, *, zh: str = "") -> str:
    """
    Final deterministic guardrail for *auto-generated* English subtitles.
    Design goals:
    - Keep meaning broadly consistent, but prioritize user comprehension and non-cringe output.
    - Never rely on an extra LLM call (must be fast and stable).
    - Run AFTER rules center en_fixes (so user can intentionally override), but still prevent obvious deal-breakers:
      POV drift (I/we), explicit sexual/bodily terms, and common fragment tails.
    """
    z = str(zh or "").strip()
    s = _normalize_en_line(str(en or ""))
    if not s:
        return s

    # 1) Remove/soften explicit sexual/bodily terms (PG-13).
    taboo_re = re.compile(r"\b(hemorrhoid|hemorrhoids|anus|anal|penis|vagina)\b", re.IGNORECASE)
    if taboo_re.search(s):
        s = taboo_re.sub("rear end", s)
        s = re.sub(r"\s+", " ", s).strip()

    # 2) POV drift guardrail: avoid I/we unless clearly present in Chinese.
    has_1p_zh = bool(re.search(r"[我咱们我们俺本人]", z))
    if (not has_1p_zh) and re.search(r"\b(i|me|my|we|our|us)\b", s, re.IGNORECASE):
        # pick a neutral subject based on Chinese cues
        subj = "That person"
        if re.search(r"(她|女人|女孩|姑娘|女士|妻|老婆|女友)", z):
            subj = "She"
        elif re.search(r"(他|男人|男的|小伙|先生|丈夫|老公|男友)", z):
            subj = "He"
        elif re.search(r"(那人|那个人|对方|此人)", z):
            subj = "That person"
        else:
            subj = "They"

        # Only do safe rewrites for the *leading* clause; avoid over-editing mid-sentence.
        s0 = s
        s = re.sub(r"^\s*I\s+", f"{subj} ", s, flags=re.IGNORECASE)
        s = re.sub(r"^\s*I'm\s+", f"{subj} is ", s, flags=re.IGNORECASE)
        s = re.sub(r"^\s*I've\s+", f"{subj} has ", s, flags=re.IGNORECASE)
        s = re.sub(r"^\s*I'd\s+", f"{subj} would ", s, flags=re.IGNORECASE)
        s = re.sub(r"^\s*We\s+", f"{subj} ", s, flags=re.IGNORECASE)
        s = re.sub(r"^\s*We're\s+", f"{subj} are ", s, flags=re.IGNORECASE)
        s = re.sub(r"^\s*We've\s+", f"{subj} have ", s, flags=re.IGNORECASE)
        s = re.sub(r"^\s*Our\s+", f"{subj}'s ", s, flags=re.IGNORECASE)
        # If nothing changed (e.g. I/we in the middle), do a minimal neutralization.
        if s == s0:
            s = re.sub(r"\b(my)\b", "the", s, flags=re.IGNORECASE)

    # 3) Fragment tails (common truncation patterns) -> make it a complete thought.
    if re.search(r"\b(and|or|but|to|of|with|for|on|in|at|into|from|because)$", s, re.IGNORECASE):
        s = re.sub(r"\s+\b(and|or|but|to|of|with|for|on|in|at|into|from|because)$", "", s, flags=re.IGNORECASE).strip()
    if re.search(r"\b(a|an|the)$", s, re.IGNORECASE):
        s = re.sub(r"\s+\b(a|an|the)$", " it", s, flags=re.IGNORECASE).strip()

    return _normalize_en_line(s)


def _estimate_en_seconds(text: str, *, wps: float = 2.6) -> float:
    """
    Extremely lightweight speaking-time estimator for English:
    - base: words / wps
    - pauses: commas/semicolons/colons add 0.12s; sentence end punctuation adds 0.22s
    This is intentionally conservative & stable (no ML/LLM).
    """
    t = _normalize_en_line(text)
    if not t:
        return 0.0
    words = [w for w in re.split(r"\s+", t) if w]
    base = (len(words) / max(float(wps), 0.5)) if words else 0.0
    pauses = 0.12 * len(re.findall(r"[,;:]", t)) + 0.22 * len(re.findall(r"[.!?]", t))
    return float(base + pauses)


def _tts_plan_floor_duration(text: str, *, cps_need: float, min_dur: float, hard_min: float = 0.35) -> float:
    """
    A readable minimum duration floor used only when we must compress the whole planned
    timeline under a hard cap. This is intentionally lower than the normal planning
    target but much higher than the previous 0.2s emergency floor.
    """
    txt = _normalize_en_line(text)
    if not txt:
        return max(float(hard_min), 0.2)
    words = [w for w in re.split(r"\s+", txt) if w]
    chars = len(txt.replace("\n", " "))
    floor = max(float(hard_min), min(float(min_dur), max(0.45, float(cps_need) * 0.55)))
    if words and len(words) >= 12:
        floor = max(floor, 0.85)
    elif words and len(words) >= 8:
        floor = max(floor, 0.7)
    if chars >= 120:
        floor = max(floor, 1.1)
    elif chars >= 80:
        floor = max(floor, 0.9)
    elif chars >= 40:
        floor = max(floor, 0.7)
    return float(floor)


def _rebalance_tts_plan_under_cap(
    segs: List[Segment],
    *,
    cap_end: float,
    min_gap: float,
    min_dur: float,
    max_cps: float,
) -> bool:
    """
    Redistribute time-budget pressure across all planned segments instead of collapsing
    the tail into a single unreadable 0.2s subtitle.
    """
    if not segs:
        return False
    first_start = float(segs[0].start)
    total_budget = max(0.0, float(cap_end) - float(first_start))
    if total_budget <= 0:
        return False
    gaps_budget = max(0.0, float(min_gap) * float(max(0, len(segs) - 1)))
    total_dur_budget = total_budget - gaps_budget
    if total_dur_budget <= 0:
        return False

    desired: List[float] = []
    floors: List[float] = []
    for seg in segs:
        txt = str(getattr(seg, "translation", "") or "")
        dur = max(float(seg.end) - float(seg.start), 0.001)
        cps_need = (len(txt.replace("\n", " ")) / max(float(max_cps), 1.0)) if txt else 0.0
        desired.append(dur)
        floors.append(_tts_plan_floor_duration(txt, cps_need=cps_need, min_dur=min_dur))

    total_desired = sum(desired)
    if total_desired <= total_dur_budget + 1e-6:
        return False

    # If even the readability floors cannot fit, scale those floors down proportionally
    # but keep a much safer absolute floor than the previous 0.2s behavior.
    total_floor = sum(floors)
    if total_floor > total_dur_budget and total_floor > 1e-6:
        scale = max(0.5, float(total_dur_budget) / float(total_floor))
        floors = [max(0.35, f * scale) for f in floors]
        total_floor = sum(floors)

    if total_floor >= total_dur_budget - 1e-6:
        new_durs = list(floors)
    else:
        slack = [max(0.0, d - f) for d, f in zip(desired, floors)]
        total_slack = sum(slack)
        need_reduce = total_desired - total_dur_budget
        if total_slack <= 1e-6:
            new_durs = list(floors)
        else:
            ratio = min(1.0, max(0.0, need_reduce / total_slack))
            new_durs = [max(f, d - s * ratio) for d, f, s in zip(desired, floors, slack)]

    cursor = float(first_start)
    for i, seg in enumerate(segs):
        seg.start = float(cursor)
        seg.end = float(cursor + max(new_durs[i], 0.35))
        cursor = float(seg.end) + (float(min_gap) if i < len(segs) - 1 else 0.0)

    if float(segs[-1].end) > float(cap_end):
        overflow = float(segs[-1].end) - float(cap_end)
        segs[-1].end = max(float(segs[-1].start) + 0.35, float(segs[-1].end) - overflow)
    return True


def _trim_en_to_word_budget(text: str, *, max_words: int, min_words: int = 3) -> str:
    """
    Rule-based trimming that tries to keep the beginning intact and cut at a nearby punctuation boundary.
    Output is cleaned via clean_tts_text to avoid feeding junk to TTS.
    """
    t = _normalize_en_line(text)
    if not t:
        return ""
    tokens = [w for w in t.split(" ") if w]
    if len(tokens) <= int(max_words):
        return t
    keep = max(int(min_words), min(int(max_words), len(tokens)))
    cut = keep
    window = 6
    for j in range(max(1, keep - window), keep + 1):
        if j <= 1 or j >= len(tokens):
            continue
        if re.search(r"[.!?]$", tokens[j - 1]) or re.search(r"[,;:]$", tokens[j - 1]):
            cut = j
    # Avoid trimming to a dangling ending like "the.", "to.", "because." etc.
    banned = {
        "a",
        "an",
        "the",
        "to",
        "of",
        "with",
        "for",
        "and",
        "or",
        "but",
        "because",
        "that",
        "which",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "can",
        "could",
        "will",
        "would",
        "should",
        "may",
        "might",
        "into",
        "from",
        "in",
        "on",
        "at",
        "as",
    }

    def _last_word(tok: str) -> str:
        w = re.sub(r"[^A-Za-z']+", "", str(tok or "")).strip().lower()
        return w

    cut2 = int(cut)
    while cut2 > int(min_words):
        w = _last_word(tokens[cut2 - 1])
        if w and w in banned:
            cut2 -= 1
            continue
        break
    cut = max(int(min_words), min(int(cut2), len(tokens)))

    out = " ".join(tokens[:cut]).strip()
    # drop trailing punctuation that looks like an unfinished clause
    out = re.sub(r"[,:;]+$", "", out).strip()
    # if we still end with a banned word after cleanup, drop one more token if possible
    while out and cut > int(min_words):
        last = _last_word(out.split(" ")[-1])
        if last and last in banned:
            cut -= 1
            out = " ".join(tokens[:cut]).strip()
            out = re.sub(r"[,:;]+$", "", out).strip()
        else:
            break
    if out and not re.search(r"[.!?]$", out):
        out = out + "."
    try:
        out = clean_tts_text(out)
    except Exception:
        out = _normalize_en_line(out)
    return out


def _clean_en_one_line(content: str) -> str:
    """
    Best-effort cleanup for LLM outputs:
    - take first non-empty line
    - strip bullets/numbering
    - remove CJK/fullwidth characters
    - normalize whitespace
    """
    s = str(content or "").strip()
    if not s:
        return ""
    lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
    s = lines[0] if lines else ""
    s = re.sub(r"^\s*[-–•]+\s*", "", s)
    s = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s)
    s = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _rewrite_en_to_budget_llm(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    zh: str,
    en: str,
    max_words: int,
    aggressive: bool,
    temperature: float = 0.1,
    max_tokens: int = 96,
    timeout_s: int = 120,
) -> str:
    """
    Use local LLM (OpenAI-compatible /v1/chat/completions) to rewrite an English line to fit within max_words.
    This is used as a friendlier alternative to hard word trimming when a line is over-budget.
    Fallback-safe: caller should verify constraints and fall back to rule-based trimming if needed.
    """
    try:
        import requests  # type: ignore
    except Exception:
        return ""
    max_words = max(int(max_words), 1)
    en0 = _normalize_en_line(en)
    zh0 = (zh or "").strip()
    if not en0:
        return ""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    sys_prompt = "You rewrite English subtitles to be shorter while preserving meaning and natural speech."
    user_parts: List[str] = [
        f"Rewrite the English subtitle to fit within {max_words} words.",
        "Rules:",
        "- Output ENGLISH ONLY.",
        "- Output ONE LINE ONLY.",
        f"- Word count MUST be <= {max_words}.",
        "- Preserve numbers, names, and negation.",
        "- Preserve intent: question/command/emphasis.",
        "- Remove fillers, redundancies, and side comments.",
        "- Do NOT add new information.",
        "- Make it a COMPLETE sentence (avoid dangling fragments like 'and/to/of').",
        "- Prefer conversational subtitle style: short, natural, spoken.",
        "- Avoid formal connectors (e.g., moreover/therefore); prefer so/then/actually/just.",
    ]
    if aggressive:
        user_parts += [
            "- Aggressive mode: you MAY drop secondary details, but keep the main event and result.",
        ]
    else:
        user_parts += [
            "- Prefer minimal rewriting; preserve details unless necessary to fit the limit.",
        ]
    if zh0:
        user_parts += [
            "",
            "Chinese meaning reference (do NOT output Chinese):",
            f"SRC_ZH: {zh0}",
        ]
    user_parts += [
        "",
        f"ORIGINAL_EN: {en0}",
        "OUTPUT_EN:",
    ]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": "\n".join(user_parts)}],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "options": {"num_ctx": 2048, "num_batch": 128},
    }

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(f"{endpoint}/chat/completions", json=body, headers=headers, timeout=int(timeout_s))
            if resp.status_code != 200:
                raise RuntimeError(resp.text)
            content = (resp.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            s = _clean_en_one_line(str(content))
            if not s:
                return ""
            # Enforce budget strictly.
            words = [w for w in s.split(" ") if w]
            if len(words) > max_words:
                return ""
            return s
        except Exception as exc:
            last_exc = exc
            time.sleep(1.0 * (2**attempt))
    _ = last_exc
    return ""


def _en_line_is_fragment(s: str) -> bool:
    """
    Detect common truncated English subtitle lines (often created by trimming / time-budgeting).
    Keep it conservative: only flag very likely fragments.
    """
    t = _normalize_en_line(s or "")
    if not t:
        return True
    low = t.lower().strip()
    low2 = low.rstrip(".!?")
    # single word like "The."
    words = [w for w in re.split(r"\s+", re.sub(r"[^A-Za-z\s']+", " ", t)) if w]
    if len(words) <= 1:
        return True
    # obvious dangling endings
    if re.search(
        r"\b(a|an|the|to|of|with|for|and|or|but|because|that|which|is|are|was|were|do|does|did|can|could|will|would|should|may|might|into|from|in|on|at|about)$",
        low2,
    ):
        return True
    # dangling possessives/pronouns
    if re.search(r"\b(my|your|his|her|its|our|their|this|that|these|those|every)\.?$", low):
        return True
    # non-ascii often indicates pinyin/diacritics artifacts
    try:
        if any(ord(ch) > 127 for ch in t):
            return True
    except Exception:
        pass
    return False


def _repair_fragment_en_deterministic(zh: str, en: str) -> str:
    """
    Low-risk deterministic repair for common cases:
    - drop trailing dangling tokens
    - handle a few ultra-common short Chinese patterns
    """
    z = str(zh or "").strip()
    e = _normalize_en_line(en or "")
    if not e:
        e = ""
    # ultra-common patterns
    if z == "下一秒":
        return "In the next second."

    # drop trailing dangling words
    banned = {
        "a",
        "an",
        "the",
        "to",
        "of",
        "with",
        "for",
        "and",
        "or",
        "but",
        "because",
        "that",
        "which",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "can",
        "could",
        "will",
        "would",
        "should",
        "may",
        "might",
        "into",
        "from",
        "in",
        "on",
        "at",
        "about",
        "my",
        "your",
        "his",
        "her",
        "its",
        "our",
        "their",
        "this",
        "that",
        "these",
        "those",
        "every",
    }
    toks = [w for w in e.split(" ") if w]
    while len(toks) > 2:
        last = re.sub(r"[^A-Za-z']+", "", toks[-1]).lower()
        last2 = re.sub(r"[^A-Za-z']+", "", toks[-1].rstrip(".!?")).lower()
        if last in banned or last2 in banned:
            toks.pop()
            continue
        break
    out = " ".join(toks).strip()
    out = re.sub(r"[,:;]+$", "", out).strip()
    if out and not re.search(r"[.!?]$", out):
        out += "."
    return out


def _repair_fragment_en_llm(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    zh: str,
    en_bad: str,
    ctx_prev: str = "",
    ctx_next: str = "",
) -> str:
    """
    LLM repair pass for a single fragmented English line.
    Small, deterministic, and only used for a few flagged lines.
    """
    try:
        import requests  # type: ignore
    except Exception:
        return ""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    prompt = (
        "Fix ONE subtitle line.\n"
        "Rewrite the English into a complete, standalone sentence faithful to the Chinese.\n"
        "Hard rules:\n"
        "- ENGLISH ONLY.\n"
        "- ONE LINE ONLY.\n"
        "- Do NOT output transliterations or invented names (never 'Bro X', never pinyin).\n"
        "- You MAY add implied pronouns/objects (he/they/it/this/that) to make grammar complete.\n"
        "- Do NOT end the line with: a/an/the/is/are/was/were/to/of/with/for/and/or/but/about.\n"
        + (f"\n[context]\nprev: {ctx_prev.strip()}\nnext: {ctx_next.strip()}\n[/context]\n" if (ctx_prev or ctx_next) else "")
        + f"\nZH: {str(zh or '').strip()}\n"
        + f"BAD_EN: {str(en_bad or '').strip()}\n"
        + "FIXED_EN:"
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": "Subtitle translation fixer."}, {"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 96,
        "options": {"num_ctx": 2048, "num_batch": 128},
    }
    try:
        r = requests.post(f"{str(endpoint).rstrip('/')}/chat/completions", json=body, headers=headers, timeout=300)
        if r.status_code != 200:
            return ""
        content = (r.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        s = _clean_en_one_line(str(content))
        s = re.sub(r"\s+", " ", s).strip()
        return s
    except Exception:
        return ""

def run_whisperx(
    audio_path: Path,
    model_id: str,
    device: str = "auto",
    model_dir: Optional[Path] = None,
    diarization: bool = False,
    align_enable: bool = False,
    audio_total_s: Optional[float] = None,
) -> List[Segment]:
    whisperx = None
    if align_enable:
        # Import WhisperX lazily to avoid importing whisperx.asr/vads (pyannote/speechbrain) at module import time.
        # We only use WhisperX for alignment (load_align_model + align).
        try:
            import whisperx as whisperx_mod  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"whisperx 未安装或导入失败（用于对齐）：{exc}") from exc
        whisperx = whisperx_mod
    # PyTorch 2.6+ defaults torch.load(weights_only=True). Pyannote's bundled VAD checkpoint
    # (whisperx/assets/pytorch_model.bin) contains OmegaConf objects, which are blocked by default
    # and cause `_pickle.UnpicklingError: Unsupported global ... omegaconf.*Config`.
    # We ship this checkpoint with the app, so it's a trusted source: allowlist the needed classes.
    try:
        if torch is not None:
            import typing
            import collections

            ser = getattr(torch, "serialization", None)
            add = getattr(ser, "add_safe_globals", None) if ser is not None else None
            if callable(add):
                # Pyannote checkpoints may embed OmegaConf metadata objects.
                # With PyTorch 2.6+, torch.load defaults to weights_only=True and blocks unpickling
                # of non-allowlisted globals, raising `_pickle.UnpicklingError`.
                from omegaconf import DictConfig, ListConfig  # type: ignore
                from omegaconf.base import ContainerMetadata  # type: ignore

                # Also allowlist common builtins/types seen in pyannote checkpoints.
                # - typing.Any: seen in some checkpoints
                # - list: some checkpoints reference bare `list` and get blocked under weights_only=True
                add([DictConfig, ListConfig, ContainerMetadata, typing.Any, list, collections.defaultdict])
    except Exception:
        # Best-effort: if this fails, torch/pyannote will surface the original error.
        pass
    # Treat env offline flags as authoritative (TaskManager sets these in fully-local mode).
    env_offline = os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    def resolve_local_snapshot(root: Optional[Path], repo_id: str, required_files: Optional[List[str]] = None) -> Optional[Path]:
        """
        Resolve a local HuggingFace cache snapshot folder under `root`.

        Example repo_id: "Systran/faster-whisper-medium"
        Expected layout:
          root/models--Systran--faster-whisper-medium/snapshots/<hash>/(model.bin, config.json, ...)
        """
        if not root:
            return None
        repo_dir = root / ("models--" + repo_id.replace("/", "--"))
        # Compatibility:
        # Some packs store the CTranslate2 model directly under repo_dir
        # (repo_dir/model.bin + repo_dir/config.json) without HF-style snapshots/.
        # In that case, repo_dir itself is a valid local model folder for whisperx.load_model(...).
        required = required_files or ["model.bin", "config.json"]
        try:
            if repo_dir.exists() and all((repo_dir / f).exists() for f in required):
                return repo_dir
        except Exception:
            pass
        snap_root = repo_dir / "snapshots"
        if not snap_root.exists():
            return None
        candidates: list[Path] = []
        for snap in sorted(snap_root.iterdir()):
            if not snap.is_dir():
                continue
            if all((snap / f).exists() for f in required):
                candidates.append(snap)
        return candidates[-1] if candidates else None

    def has_local_hf_snapshot(root: Optional[Path], repo_id: str, required_files: List[str]) -> bool:
        """Check whether a HF-style cached snapshot exists locally with required files."""
        snap = resolve_local_snapshot(root, repo_id, required_files=required_files)
        if not snap:
            return False
        return all((snap / f).exists() for f in required_files)

    if device == "auto":
        device = "cuda" if torch and torch.cuda.is_available() else "cpu"
    device = "cuda" if device == "cuda" and torch and torch.cuda.is_available() else "cpu"
    # CPU default: int8 is dramatically faster (especially under amd64-on-arm64 emulation).
    # CUDA default: float16.
    compute_type = "float16" if device == "cuda" else "int8"

    PIPE_TOTAL = 8
    print(f"  [2/{PIPE_TOTAL}][1/4] Loading ASR model (faster-whisper)... device={device} compute_type={compute_type}")
    # Prefer loading from local snapshot folder to avoid any online HF lookup.
    model_to_load = model_id
    local_files_only = False
    offline_mode = False
    candidate_model_roots: List[Path] = []
    if model_dir:
        candidate_model_roots.append(model_dir)
        try:
            sibling_common = model_dir.parent / "common_cache_hf"
            if sibling_common != model_dir:
                candidate_model_roots.append(sibling_common)
        except Exception:
            pass
    for env_key in ["HUGGINGFACE_HUB_CACHE", "HF_HOME"]:
        raw = str(os.environ.get(env_key, "") or "").strip()
        if not raw:
            continue
        try:
            p = Path(raw)
        except Exception:
            continue
        if p not in candidate_model_roots:
            candidate_model_roots.append(p)
        try:
            hub_p = p / "hub"
            if hub_p not in candidate_model_roots:
                candidate_model_roots.append(hub_p)
        except Exception:
            pass
    try:
        default_hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
        if default_hf_hub not in candidate_model_roots:
            candidate_model_roots.append(default_hf_hub)
    except Exception:
        pass
    if model_dir:
        # Default preference remains the configured model dir; if the exact model is absent there,
        # we may fall back to a shared HF cache (e.g. Docker dev bind mount).
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(model_dir)
        # HF_HOME is used by transformers/huggingface_hub as a base for caches/configs.
        os.environ["HF_HOME"] = str(model_dir)
    if model_dir:
        # `whisperx.load_model()` accepts either:
        # - repo id, e.g. "Systran/faster-whisper-medium"
        # - shorthand, e.g. "medium" (internally maps to Systran/faster-whisper-medium)
        # - local folder path to a CT2 model snapshot directory
        repo_candidates: List[str] = []
        if "/" in model_id:
            repo_candidates.append(model_id)
        else:
            # Map shorthand to the upstream faster-whisper repo id.
            if model_id.startswith("faster-whisper-"):
                repo_candidates.append(f"Systran/{model_id}")
            else:
                repo_candidates.append(f"Systran/faster-whisper-{model_id}")

        local_snap = None
        local_snap_root: Optional[Path] = None
        for repo_id in repo_candidates:
            for root in candidate_model_roots:
                local_snap = resolve_local_snapshot(root, repo_id, required_files=["model.bin", "config.json"])
                if local_snap:
                    local_snap_root = root
                    break
            if local_snap:
                break

        if local_snap:
            model_to_load = str(local_snap)
            local_files_only = True
            if local_snap_root is not None:
                os.environ["HUGGINGFACE_HUB_CACHE"] = str(local_snap_root)
                os.environ["HF_HOME"] = str(local_snap_root)
            # We already have all files locally; keep HF fully offline to avoid DNS stalls.
            os.environ["HF_HUB_OFFLINE"] = "1"
            # Transformers also needs its own offline flag, otherwise it may still retry HF requests.
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            os.environ["HF_DATASETS_OFFLINE"] = "1"
            offline_mode = True
            if local_snap_root is not None and model_dir and local_snap_root != model_dir:
                print(f"        在备用模型缓存找到 WhisperX 模型：{local_snap_root}")
        else:
            # Helpful hint about where we looked.
            for repo_id in repo_candidates:
                print(f"  [warn] 未在本地找到模型缓存：{repo_id}")
                for root in candidate_model_roots or ([model_dir] if model_dir else []):
                    try:
                        print(f"        已检查目录: {root / ('models--' + repo_id.replace('/', '--'))}")
                    except Exception:
                        pass
            if env_offline:
                raise RuntimeError(
                    "当前为全离线模式（HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE=1），且本地缺少 WhisperX 模型缓存。\n"
                    "请先把模型放入 assets/models/quality_asr_whisperx 对应的 models--<repo>/snapshots/<hash>/ 目录。"
                )
            print("        将尝试在线下载（若当前环境无法联网会失败）。")
    if model_dir:
        print(f"        whisperx_model_dir: {model_dir}")
    if local_files_only:
        print(f"        使用本地 WhisperX 模型快照：{model_to_load}")
    else:
        print(f"        使用 WhisperX 模型ID：{model_to_load}")
    t0 = time.time()
    try:
        from faster_whisper import WhisperModel  # type: ignore

        # NOTE:
        # We intentionally avoid whisperx.load_model() here because whisperx.asr imports `whisperx.vads`
        # at import time, which pulls in pyannote + speechbrain. That stack is brittle in PyInstaller(onefile)
        # and can fail with FileNotFoundError / recursion within speechbrain importutils.
        #
        # For ASR we can use faster-whisper directly (and still run WhisperX alignment later via
        # whisperx.load_align_model + whisperx.align).
        # NOTE:
        # CPU decoding can appear "stuck" when multiple tasks run concurrently and CTranslate2
        # over-subscribes threads. Keep CPU defaults conservative for responsiveness.
        cpu_threads = None
        num_workers = None
        if device == "cpu":
            try:
                import multiprocessing

                ncpu = int(multiprocessing.cpu_count() or 1)
            except Exception:
                ncpu = 1
            # Bound CPU threads to reduce contention (especially when multiple tasks run).
            cpu_threads = max(1, min(4, ncpu))
            num_workers = 1

        inner = WhisperModel(
            str(model_to_load),
            device=device if device in ["cuda", "cpu"] else "auto",
            compute_type=compute_type,
            download_root=str(model_dir) if model_dir else None,
            local_files_only=local_files_only,
            cpu_threads=cpu_threads,
            num_workers=num_workers,
        )
    except Exception as exc:
        msg = str(exc)
        if "model.bin" in msg or "config.json" in msg:
            raise RuntimeError(
                f"faster-whisper 模型缺失：{msg}\n"
                f"请确认 {model_dir or '默认缓存目录'} 下存在对应的 model.bin/config.json（CT2 模型快照），或联网后重试。"
            ) from exc
        raise
    print(f"  [2/{PIPE_TOTAL}][1/4] 模型加载完成，用时 {time.time() - t0:.1f}s")

    # Duration hint (helps distinguish "stuck" vs "just slow").
    if audio_total_s is None:
        try:
            import wave

            with wave.open(str(audio_path), "rb") as w:
                frames = float(w.getnframes())
                rate = float(w.getframerate() or 1.0)
                audio_total_s = max(0.0, frames / rate)
        except Exception:
            audio_total_s = None

    if audio_total_s is not None and audio_total_s > 0:
        print(f"  [2/{PIPE_TOTAL}][2/4] 转录中... audio_dur={audio_total_s:.1f}s")
    else:
        print(f"  [2/{PIPE_TOTAL}][2/4] 转录中...")
    t1 = time.time()
    # Whisper-family models are prone to hallucinating common outro/promo text on silence.
    # Use conservative VAD + word-level hallucination skipping first, then keep a tail cleanup
    # pass below as a safety net for impossible timestamps.
    vad_filter = True
    vad_options: Optional[Dict[str, Any]] = {
        "min_silence_duration_ms": 2000,
        "speech_pad_ms": 320,
    }
    hallucination_silence_threshold = 2.0 if device == "cpu" else 1.5
    transcribe_mode = "guarded"

    # Heartbeat thread: if decoding is slow before the first segment is yielded,
    # the main thread may appear "stuck" with no logs. This keeps logs alive.
    import threading

    progress = {"segs": 0, "last_end_s": 0.0, "done": False, "phase": "calling_transcribe"}

    def _heartbeat() -> None:
        while True:
            time.sleep(15.0)
            if progress.get("done"):
                return
            now = time.time()
            segs = int(progress.get("segs") or 0)
            last_end = float(progress.get("last_end_s") or 0.0)
            phase = str(progress.get("phase") or "transcribing")
            extra = f" phase={phase}"
            if audio_total_s is not None and audio_total_s > 0 and last_end > 0:
                pct = max(0.0, min(99.9, 100.0 * (last_end / audio_total_s)))
                extra += f" last_end={last_end:.1f}s ({pct:.1f}%)"
            elif last_end > 0:
                extra += f" last_end={last_end:.1f}s"
            elif audio_total_s is not None and audio_total_s > 0:
                extra += f" audio_dur={audio_total_s:.1f}s"
            if segs == 0:
                extra += " waiting_first_segment=1"
            print(f"  [2/{PIPE_TOTAL}][2/4] 转录进行中... segs={segs}{extra} elapsed={now - t1:.1f}s", flush=True)

    hb = threading.Thread(target=_heartbeat, name="asr-heartbeat", daemon=True)
    hb.start()

    transcribe_attempts: List[Tuple[str, Dict[str, Any]]] = [
        (
            "guarded",
            {
                "language": "zh",
                "task": "transcribe",
                # CPU speed/robustness defaults:
                # - smaller beam reduces worst-case decode stalls
                # - fixed chunk length yields segments more frequently (less "looks stuck")
                "beam_size": 1 if device == "cpu" else 5,
                "best_of": 1 if device == "cpu" else 5,
                "condition_on_previous_text": False,
                "chunk_length": 20 if device == "cpu" else 30,
                "compression_ratio_threshold": 2.4,
                "log_prob_threshold": -1.0,
                "no_speech_threshold": 0.6,
                "word_timestamps": True,
                "hallucination_silence_threshold": hallucination_silence_threshold,
                "vad_filter": vad_filter,
                "vad_parameters": vad_options,
            },
        ),
        (
            "compat_no_hall_skip",
            {
                "language": "zh",
                "task": "transcribe",
                "beam_size": 1 if device == "cpu" else 5,
                "best_of": 1 if device == "cpu" else 5,
                "condition_on_previous_text": False,
                "chunk_length": 20 if device == "cpu" else 30,
                "compression_ratio_threshold": 2.4,
                "log_prob_threshold": -1.0,
                "no_speech_threshold": 0.6,
                "word_timestamps": True,
                "vad_filter": vad_filter,
                "vad_parameters": vad_options,
            },
        ),
        (
            "compat_no_vad",
            {
                "language": "zh",
                "task": "transcribe",
                "beam_size": 1 if device == "cpu" else 5,
                "best_of": 1 if device == "cpu" else 5,
                "condition_on_previous_text": False,
                "chunk_length": 20 if device == "cpu" else 30,
                "compression_ratio_threshold": 2.4,
                "log_prob_threshold": -1.0,
                "no_speech_threshold": 0.6,
                "word_timestamps": True,
                "vad_filter": False,
                "vad_parameters": None,
            },
        ),
    ]

    print(
        f"  [info] ASR 幻听防护：vad=on min_silence_ms={vad_options['min_silence_duration_ms']} "
        f"word_timestamps=on hall_silence={hallucination_silence_threshold:.1f}s"
    )

    info = None
    last_exc: Optional[Exception] = None
    try:
        for attempt_name, attempt_kwargs in transcribe_attempts:
            try:
                transcribe_mode = attempt_name
                progress["phase"] = f"calling_transcribe:{attempt_name}"
                seg_iter, info = inner.transcribe(str(audio_path), **attempt_kwargs)  # type: ignore[attr-defined]
                break
            except TypeError as exc:
                last_exc = exc
                if attempt_name == "guarded" and "hallucination_silence_threshold" in str(exc):
                    print(f"  [warn] 当前 faster-whisper 版本不支持 hallucination_silence_threshold，回退到兼容模式：{exc}")
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                if attempt_name != "compat_no_vad" and ("onnxruntime" in msg or "vad" in msg):
                    print(f"  [warn] ASR 防护模式依赖不可用，将回退到较弱兼容模式：{exc}")
                    continue
                raise
        else:
            raise RuntimeError(f"ASR 转录启动失败：{last_exc}") from last_exc

        progress["phase"] = "iterating_segments"
        segments_raw = []
        last_end_s = 0.0
        for s in seg_iter:
            try:
                last_end_s = float(getattr(s, "end", last_end_s) or last_end_s)
            except Exception:
                pass
            segments_raw.append(
                {
                    "start": float(getattr(s, "start", 0.0)),
                    "end": float(getattr(s, "end", 0.0)),
                    "text": str(getattr(s, "text", "")).strip(),
                }
            )
            progress["segs"] = len(segments_raw)
            progress["last_end_s"] = last_end_s
    finally:
        progress["done"] = True
    result = {"segments": segments_raw, "language": getattr(info, "language", "zh")}
    print(f"  [2/{PIPE_TOTAL}][2/4] 原始分段数：{len(segments_raw)} (mode={transcribe_mode})")
    print(f"  [2/{PIPE_TOTAL}][2/4] 转录完成，用时 {time.time() - t1:.1f}s")

    segments_raw = _sanitize_asr_segments(result["segments"], audio_total_s)

    if not align_enable:
        print(f"  [2/{PIPE_TOTAL}][3/4] 跳过对齐（asr_align_enable=off）")
        segments_out: List[Segment] = []
        for seg in segments_raw:
            segments_out.append(
                Segment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=str(seg.get("text", "")).strip(),
                )
            )
        return segments_out

    print(f"  [2/{PIPE_TOTAL}][3/4] 加载对齐模型...")
    # Alignment model is typically downloaded from HuggingFace (e.g. wav2vec2). In offline setups,
    # trying to load it causes long HF retry loops. We skip alignment entirely when offline.
    is_offline = offline_mode or env_offline
    align_model_name: Optional[str] = None
    if is_offline:
        # If user has manually cached the align model locally, we can still run alignment offline.
        lang = str(result.get("language", "zh"))
        align_repo: Optional[str] = None
        align_required: List[str] = []
        if lang == "zh":
            align_repo = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"
            # Minimum set needed by Transformers to run Wav2Vec2Processor + model weights.
            align_required = [
                "config.json",
                "preprocessor_config.json",
                # tokenizer_config.json is optional for some Wav2Vec2 checkpoints
                "pytorch_model.bin",
                "vocab.json",
                "special_tokens_map.json",
            ]

        if align_repo and model_dir and has_local_hf_snapshot(model_dir, align_repo, align_required):
            # IMPORTANT: Use the local snapshot folder path as model_name, so transformers will not
            # try any network HEAD/resolve calls for repo files.
            snap = resolve_local_snapshot(model_dir, align_repo, required_files=align_required)
            align_model_name = str(snap) if snap else None
            print(f"  [info] 检测到本地对齐模型缓存：{align_repo}，将继续执行对齐（离线）。")
            if align_model_name:
                print(f"  [info] 使用本地对齐模型快照路径：{align_model_name}")
        else:
            print("  [warn] 当前为离线模式，且未检测到本地对齐模型缓存，跳过对齐步骤（避免 HuggingFace 重试）。")
            segments_out: List[Segment] = []
            for seg in segments_raw:
                segments_out.append(
                    Segment(
                        start=float(seg.get("start", 0.0)),
                        end=float(seg.get("end", 0.0)),
                        text=str(seg.get("text", "")).strip(),
                    )
                )
            return segments_out
    try:
        model_a, metadata = whisperx.load_align_model(
            language_code=result.get("language", "zh"),
            device=device,
            model_name=align_model_name,
            model_dir=str(model_dir) if model_dir else None,
        )
    except Exception as exc:  # pragma: no cover
        # Alignment model often requires extra downloads; in offline setups we gracefully degrade.
        print(f"  [warn] 对齐模型加载失败，将跳过对齐，直接使用原始分段：{exc}")
        segments_out: List[Segment] = []
        for seg in segments_raw:
            segments_out.append(
                Segment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=str(seg.get("text", "")).strip(),
                )
            )
        return segments_out

    print(f"  [2/{PIPE_TOTAL}][4/4] 对齐中...")
    try:
        result_aligned = whisperx.align(segments_raw, model_a, metadata, str(audio_path), device=device)
    except Exception as exc:  # pragma: no cover
        print(f"  [warn] 对齐失败，将跳过对齐，直接使用原始分段：{exc}")
        segments_out: List[Segment] = []
        for seg in segments_raw:
            segments_out.append(
                Segment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=str(seg.get("text", "")).strip(),
                )
            )
        return segments_out
    print(f"  [2/{PIPE_TOTAL}] ASR 对齐后片段数：{len(result_aligned['segments'])}")
    segments_out: List[Segment] = []
    for seg in result_aligned["segments"]:
        segments_out.append(Segment(start=float(seg["start"]), end=float(seg["end"]), text=str(seg.get("text", "")).strip()))
    return segments_out


def _sensevoice_ts_to_seconds(value: Any) -> Optional[float]:
    try:
        n = float(value)
    except Exception:
        return None
    return n / 1000.0 if abs(n) > 1000.0 else n


def _sensevoice_bounds_from_obj(obj: Any) -> Tuple[Optional[float], Optional[float]]:
    if isinstance(obj, dict):
        start = obj.get("start", obj.get("begin"))
        end = obj.get("end", obj.get("stop"))
        s = _sensevoice_ts_to_seconds(start)
        e = _sensevoice_ts_to_seconds(end)
        if s is not None and e is not None:
            return s, e
    if isinstance(obj, (list, tuple)):
        pairs: List[Tuple[float, float]] = []
        for item in obj:
            if isinstance(item, dict):
                start = _sensevoice_ts_to_seconds(item.get("start", item.get("begin")))
                end = _sensevoice_ts_to_seconds(item.get("end", item.get("stop")))
                if start is not None and end is not None:
                    pairs.append((start, end))
                    continue
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                start = _sensevoice_ts_to_seconds(item[0])
                end = _sensevoice_ts_to_seconds(item[1])
                if start is not None and end is not None:
                    pairs.append((start, end))
        if pairs:
            return pairs[0][0], pairs[-1][1]
    return None, None


def _normalize_sensevoice_text(text: Any, postprocess_fn: Optional[Any] = None) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    if callable(postprocess_fn):
        try:
            s = str(postprocess_fn(s) or "").strip()
        except Exception:
            s = str(text or "").strip()
    return s


def run_sensevoice_asr(
    audio_path: Path,
    model_id: str,
    device: str = "auto",
    model_dir: Optional[Path] = None,
    audio_total_s: Optional[float] = None,
) -> List[Segment]:
    try:
        from funasr import AutoModel  # type: ignore
        from funasr.utils.postprocess_utils import rich_transcription_postprocess  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"funasr 未安装或导入失败（SenseVoiceSmall 需要）：{exc}") from exc

    if model_dir:
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(model_dir)
        os.environ["HF_HOME"] = str(model_dir)

    if device == "auto":
        device = "cuda" if torch and torch.cuda.is_available() else "cpu"
    device = "cuda" if device == "cuda" and torch and torch.cuda.is_available() else "cpu"
    device_arg = "cuda:0" if device == "cuda" else "cpu"

    PIPE_TOTAL = 8
    print(f"  [2/{PIPE_TOTAL}][1/3] Loading ASR model (SenseVoiceSmall)... device={device_arg}")
    t0 = time.time()
    model = AutoModel(
        model=model_id,
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device=device_arg,
        hub="hf",
    )
    print(f"  [2/{PIPE_TOTAL}][1/3] 模型加载完成，用时 {time.time() - t0:.1f}s")

    if audio_total_s is not None and audio_total_s > 0:
        print(f"  [2/{PIPE_TOTAL}][2/3] 转录中... audio_dur={audio_total_s:.1f}s")
    else:
        print(f"  [2/{PIPE_TOTAL}][2/3] 转录中...")
    t1 = time.time()
    res = model.generate(
        input=str(audio_path),
        cache={},
        language="auto",
        use_itn=True,
        batch_size_s=60 if device == "cuda" else 20,
        merge_vad=True,
        merge_length_s=15,
        output_timestamp=True,
    )
    print(f"  [2/{PIPE_TOTAL}][2/3] 转录完成，用时 {time.time() - t1:.1f}s")

    items = res if isinstance(res, list) else [res]
    segments_out: List[Segment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sentence_info = item.get("sentence_info") or item.get("sentences") or []
        if isinstance(sentence_info, list) and sentence_info:
            for sent in sentence_info:
                if not isinstance(sent, dict):
                    continue
                text = _normalize_sensevoice_text(
                    sent.get("text") or sent.get("sentence") or sent.get("raw_text"),
                    rich_transcription_postprocess,
                )
                if not text:
                    continue
                start, end = _sensevoice_bounds_from_obj(
                    sent.get("timestamp") if sent.get("timestamp") is not None else sent
                )
                if start is None:
                    start = _sensevoice_ts_to_seconds(sent.get("start"))
                if end is None:
                    end = _sensevoice_ts_to_seconds(sent.get("end"))
                if start is None or end is None or end <= start:
                    continue
                segments_out.append(Segment(start=float(start), end=float(end), text=text))
            continue

        text = _normalize_sensevoice_text(item.get("text"), rich_transcription_postprocess)
        if not text:
            continue
        start, end = _sensevoice_bounds_from_obj(item.get("timestamp"))
        if start is None:
            start = 0.0
        if end is None:
            end = float(audio_total_s or max(start + 3.0, 3.0))
        if end <= start:
            end = float(audio_total_s or max(start + 3.0, start + 0.1))
        segments_out.append(Segment(start=float(start), end=float(end), text=text))

    if not segments_out:
        raise RuntimeError("SenseVoiceSmall 未返回可用分段（缺少 sentence_info/timestamp）")
    print(f"  [2/{PIPE_TOTAL}][3/3] 原始分段数：{len(segments_out)}")
    return segments_out


# ----------------------
# Pipeline
# ----------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Quality pipeline (WhisperX + LLM + SoVITS placeholder)")
    p.add_argument("--video", type=Path, required=True, help="Input video file")
    p.add_argument("--output-dir", type=Path, default=Path("outputs"), help="Directory for outputs")
    p.add_argument("--glossary", type=Path, default=Path("assets/glossary/glossary.json"), help="Glossary JSON path (optional)")
    p.add_argument("--en-replace-dict", type=Path, default=None, help="Optional JSON dictionary for English replacements (word-level)")
    p.add_argument("--chs-override-srt", type=Path, default=None, help="Override chs.srt content when rerunning MT (review workflow)")
    p.add_argument("--eng-override-srt", type=Path, default=None, help="Override eng.srt content when rerunning TTS (review workflow)")
    p.add_argument("--asr-engine", choices=["faster-whisper", "sensevoice"], default="faster-whisper", help="ASR engine: faster-whisper (default) or sensevoice")
    p.add_argument("--whisperx-model", default="mobiuslabsgmbh/faster-whisper-large-v3-turbo", help="WhisperX/faster-whisper model id")
    p.add_argument("--whisperx-model-dir", type=Path, default=Path("assets/models/quality_asr_whisperx"), help="WhisperX model cache dir")
    p.add_argument("--whisperx-device", choices=["auto", "cpu", "cuda"], default="auto", help="ASR device for faster-whisper: auto/cpu/cuda")
    p.add_argument("--sensevoice-model", default="FunAudioLLM/SenseVoiceSmall", help="SenseVoiceSmall model id (experimental)")
    p.add_argument("--sensevoice-model-dir", type=Path, default=Path("assets/models/common_cache_hf"), help="SenseVoiceSmall/HF cache dir")
    p.add_argument("--asr-align-enable", action="store_true", help="Enable WhisperX alignment (wav2vec2) after ASR")
    p.add_argument("--diarization", action="store_true", help="Enable diarization (if supported)")
    p.add_argument("--llm-endpoint", default="http://127.0.0.1:11434/v1", help="LLM endpoint for MT (OpenAI-compatible /v1, e.g., Ollama)")
    p.add_argument("--zh-phrase-llm-endpoint", default="", help="Optional endpoint for phrase extraction (empty -> use --llm-endpoint)")
    p.add_argument("--llm-model", default="qwen3.5:9b", help="LLM model name (served model id for MT)")
    p.add_argument("--llm-api-key", default="", help="LLM API key if required")
    p.add_argument("--llm-chunk-size", type=int, default=2, help="How many ASR segments per LLM call (lower is more stable)")
    # MT quality levers (prompt-level, general)
    p.add_argument("--mt-context-window", type=int, default=0, help="Include prev/next Chinese lines as context (0=off, 1=prev+next)")
    p.add_argument("--mt-style", type=str, default="", help="Translation style hint (e.g., American English daily dialogue, concise)")
    p.add_argument("--mt-max-words-per-line", type=int, default=0, help="Max words per translated line (0=off). Used for subtitle concision.")
    p.add_argument(
        "--mt-terms-max-items",
        type=int,
        default=0,
        help="(deprecated) ZH->EN terms chain removed in v2; kept only for CLI compatibility (ignored).",
    )
    p.add_argument(
        "--mt-terms-max-chars",
        type=int,
        default=0,
        help="(deprecated) ZH->EN terms chain removed in v2; kept only for CLI compatibility (ignored).",
    )
    p.add_argument("--mt-compact-enable", action="store_true", help="If a translated line exceeds max words, use local LLM to rewrite within budget")
    p.add_argument("--mt-compact-aggressive", action="store_true", help="Allow more aggressive compression when compacting over-long lines")
    p.add_argument("--mt-compact-temperature", type=float, default=0.1, help="Temperature for compact rewrite (lower is more stable)")
    p.add_argument("--mt-compact-max-tokens", type=int, default=96, help="Max tokens for compact rewrite response")
    p.add_argument("--mt-compact-timeout-s", type=int, default=480, help="Timeout seconds for compact rewrite request")
    p.add_argument("--mt-request-timeout-s", type=int, default=1200, help="Timeout seconds for each MT request")
    p.add_argument("--mt-request-retries", type=int, default=4, help="Retry count for MT OpenAI-compatible requests")
    p.add_argument("--mt-long-zh-chars", type=int, default=60, help="Trigger long-line compression when zh chars >= this")
    p.add_argument("--mt-long-en-words", type=int, default=22, help="Trigger long-line compression when en words >= this")
    p.add_argument("--mt-long-target-words", type=int, default=18, help="Target max words after long-line compression")
    # Two-stage prompt strategy (best practice): short prompt by default, long prompt only for bad lines.
    p.add_argument("--mt-prompt-mode", choices=["short", "long"], default="short", help="Prompt mode for MT: short or long (short recommended)")
    p.add_argument("--mt-long-fallback-enable", action="store_true", help="When mt-prompt-mode=short, retry bad lines with a longer prompt")
    p.add_argument("--mt-long-fallback-max-lines", type=int, default=10, help="Max number of lines to retry with long prompt (0=unlimited)")
    p.add_argument("--mt-long-fallback-max-ratio", type=float, default=0.25, help="Max fraction of lines to retry with long prompt (0=unlimited)")
    p.add_argument("--mt-long-examples-enable", action="store_true", help="Include examples in the long prompt (better quality, slower)")
    p.add_argument("--mt-prompt-profile", type=str, default="subtitle_clean_v1", help="MT prompt profile name under assets/prompts/mt (empty=use built-in prompt)")
    p.add_argument("--mt-two-pass-disable", action="store_true", help="Disable two-pass MT (short then long fallback). Useful for benchmarking.")
    p.add_argument(
        "--mt-reasoning-effort",
        type=str,
        default="",
        help="Best-effort runtime knob for the OpenAI-compatible endpoint. Use 'none' to suppress reasoning where supported.",
    )
    # High-risk line self-check:
    # - Default ON in quality mode (product decision: prefer stable, complete lines; bounded by caps below).
    # - Allow explicit disable for benchmarking or emergency rollback.
    p.add_argument("--llm-selfcheck-enable", action="store_true", help="(legacy) Enable LLM self-check pass (now default-on; kept for CLI compatibility)")
    p.add_argument("--llm-selfcheck-disable", action="store_true", help="Disable LLM self-check pass (benchmark/rollback)")
    p.add_argument("--llm-selfcheck-max-lines", type=int, default=10, help="Max number of lines to self-check (cost guardrail)")
    p.add_argument("--llm-selfcheck-max-ratio", type=float, default=0.25, help="Max fraction of lines to self-check (0-1, cost guardrail)")
    p.add_argument("--tts-backend", choices=["coqui", "piper"], default="coqui", help="TTS backend")
    p.add_argument("--piper-model", type=Path, default=Path("assets/models/lite_tts_piper/en_US-amy-low.onnx"), help="Piper ONNX model path")
    p.add_argument("--piper-bin", default="piper", help="Path to piper executable")
    p.add_argument("--coqui-model", default="tts_models/multilingual/multi-dataset/xtts_v2", help="Coqui TTS model name")
    p.add_argument("--coqui-device", default="auto", help="Coqui TTS device: auto/cpu/cuda")
    p.add_argument("--sample-rate", type=int, default=16000, help="Sample rate for extraction and TTS export")
    # Denoise during audio extraction (safe fallback: arnndn without model -> anlmdn)
    p.add_argument("--denoise", action="store_true", help="Enable simple denoise during audio extraction (ffmpeg)")
    p.add_argument("--denoise-model", type=Path, default=None, help="Optional ffmpeg arnndn model file path")
    p.add_argument("--max-sentence-len", type=int, default=50, help="Max characters per subtitle segment before splitting")
    p.add_argument("--min-sub-dur", type=float, default=1.8, help="Minimum subtitle duration (seconds)")
    p.add_argument("--tts-split-len", type=int, default=80, help="Max characters per TTS chunk")
    p.add_argument("--tts-speed-max", type=float, default=1.08, help="Max speed-up factor when aligning audio")
    p.add_argument("--tts-align-mode", choices=["atempo", "resample"], default="resample", help="Time-stretch mode for TTS alignment (atempo preserves pitch)")
    # Hard subtitle erase (burned-in subtitles on video frames) - applied during mux (before burn_subtitles).
    p.add_argument("--erase-subtitle-enable", action="store_true", help="Enable burned-in subtitle erase/obscure on source video frames")
    p.add_argument("--erase-subtitle-method", default="delogo", help="Erase method (currently delogo)")
    p.add_argument("--erase-subtitle-coord-mode", default="ratio", choices=["ratio", "px"], help="Coordinate mode for erase region")
    p.add_argument("--erase-subtitle-x", type=float, default=0.0, help="Erase region X (ratio or px)")
    p.add_argument("--erase-subtitle-y", type=float, default=0.78, help="Erase region Y (ratio or px)")
    p.add_argument("--erase-subtitle-w", type=float, default=1.0, help="Erase region width (ratio or px)")
    p.add_argument("--erase-subtitle-h", type=float, default=0.22, help="Erase region height (ratio or px)")
    p.add_argument("--erase-subtitle-blur-radius", type=int, default=12, help="Aggressiveness (mapped to delogo band)")
    p.add_argument("--mode", default="quality", help="Mode flag (quality)")
    p.add_argument("--resume-from", choices=["asr", "mt", "tts", "mux"], default=None, help="Resume from a specific stage")
    # Review gate (zh_polish) - used by desktop app workflow
    p.add_argument("--review-enabled", action="store_true", help="Enable zh_gate before MT (pause only for high-risk zh_polish results)")
    p.add_argument(
        "--stop-after",
        choices=["zh_polish", "mt", "tts", "mux"],
        default=None,
        help="Stop early after a stage (used for batch barrier; exits with code 3 as paused)",
    )
    # P1: phrase/span extraction (LLM; extract only, no paraphrase)
    p.add_argument("--zh-phrase-enable", action="store_true", help="Enable zh_polish phrase extraction even when review gate is off")
    p.add_argument(
        "--zh-phrase-llm-model",
        default="qwen3-4b-instruct",
        help="Optional dedicated LLM model for zh phrase extraction (empty -> reuse --llm-model)",
    )
    p.add_argument("--zh-phrase-max-spans", type=int, default=3, help="Max spans per line for phrase extraction")
    p.add_argument("--zh-phrase-max-total", type=int, default=30, help="Max total spans per request group")
    p.add_argument("--zh-phrase-candidate-max-lines", type=int, default=0, help="Max candidate lines sent to phrase extractor (0=all)")
    # Keep small by default to avoid local inference timeouts and truncation.
    p.add_argument("--zh-phrase-chunk-lines", type=int, default=8, help="How many subtitle lines per extraction request")
    p.add_argument("--zh-phrase-idiom-enable", action="store_true", help="Enable deterministic idiom dictionary spans")
    p.add_argument("--zh-phrase-idiom-path", default="assets/zh_phrase/idioms_4char.txt", help="Path to 4-char idiom list (txt, one per line)")
    p.add_argument(
        "--zh-phrase-same-pinyin-path",
        default="assets/zh_phrase/pycorrector_same_pinyin.txt",
        help="Path to same-pinyin confusion table (pycorrector same_pinyin.txt)",
    )
    p.add_argument(
        "--zh-phrase-same-stroke-path",
        default="assets/zh_phrase/pycorrector_same_stroke.txt",
        help="Path to same-stroke confusion table (pycorrector same_stroke.txt)",
    )
    p.add_argument(
        "--asr-project-confusions-path",
        default="assets/zh_phrase/asr_project_confusions.json",
        help="Shared ASR project confusion asset used by review gate and local repair hints",
    )
    p.add_argument(
        "--zh-phrase-force-one-per-line",
        action="store_true",
        help="Force at least one span per subtitle line in review mode (adds low-cost fallback spans; increases noise/workload)",
    )
    p.add_argument(
        "--no-zh-phrase-second-pass",
        action="store_true",
        help="Disable second-pass phrase extraction (faster on CPU; lower recall when pass1 returns few spans)",
    )
    p.add_argument("--zh-gate-min-high-risk", type=int, default=1, help="Pause zh_gate when high-risk suspects reach this count (0=disable)")
    p.add_argument("--zh-gate-min-total-suspects", type=int, default=6, help="Pause zh_gate when total suspects reach this count (0=disable)")
    p.add_argument("--zh-gate-on-phrase-error", action="store_true", help="Pause zh_gate when phrase extraction errors out")
    # P2: constrained zh polish (LLM; optional)
    p.add_argument(
        "--zh-post-polish-enable",
        action="store_true",
        help="Enable constrained Chinese polish for suspect lines before MT",
    )
    p.add_argument("--zh-post-polish-max-lines", type=int, default=6, help="Max suspect lines sent to constrained zh post-polish")
    p.add_argument("--zh-opt-request-timeout-s", type=int, default=360, help="Timeout seconds for each zh optimization LLM request")
    p.add_argument("--zh-opt-request-retries", type=int, default=3, help="Retry count for zh optimization LLM requests")
    p.add_argument("--zh-repair-lexicon-path", default="assets/zh_phrase/chinese_xinhua_ci_2to4.txt", help="Path to local Chinese lexicon used for lightweight one-char repair candidates")
    p.add_argument("--zh-repair-proper-nouns-path", default="assets/zh_phrase/thuocl_proper_nouns.txt", help="Path to proper-noun lexicon used to avoid reckless local rewrites")
    p.add_argument("--skip-tts", action="store_true", help="Skip TTS (for ASR/MT only)")
    # P0: subtitle post-process (safe defaults: off)
    p.add_argument("--subtitle-postprocess-enable", action="store_true", help="Enable P0 subtitle post-process (normalize + optional wrap)")
    p.add_argument("--subtitle-wrap-enable", action="store_true", help="Enable soft wrap for long English subtitle lines")
    p.add_argument("--subtitle-wrap-max-lines", type=int, default=2, help="Max wrapped lines per subtitle block (when wrap enabled)")
    p.add_argument("--subtitle-max-chars-per-line", type=int, default=80, help="Max chars per line for wrapping (best-effort)")
    p.add_argument("--subtitle-max-cps", type=float, default=20.0, help="Max readability CPS gate (used by planning; does not run timeline CPS-fix)")

    # P0: display subtitles (extra deliverable; readability-oriented)
    p.add_argument("--display-srt-enable", action="store_true", help="Generate display subtitle srt (readability-oriented)")
    p.add_argument("--display-use-for-embed", action="store_true", help="Use display subtitle for embedding into video")
    p.add_argument("--display-max-chars-per-line", type=int, default=42, help="Display subtitle max chars per line")
    p.add_argument("--display-max-lines", type=int, default=2, help="Display subtitle max lines per block")
    p.add_argument("--display-merge-enable", action="store_true", help="Merge adjacent short blocks for display subtitle")
    p.add_argument("--display-merge-max-gap-s", type=float, default=0.25, help="Max gap seconds to merge for display subtitle")
    p.add_argument("--display-merge-max-chars", type=int, default=80, help="Max merged chars for display subtitle")
    p.add_argument("--display-split-enable", action="store_true", help="Split overly long blocks for display subtitle")
    p.add_argument("--display-split-max-chars", type=int, default=86, help="Split threshold chars for display subtitle")
    # Subtitle burn-in style (hard-sub)
    p.add_argument("--sub-font-name", default="Arial", help="Subtitle font name for hard-burn (best-effort)")
    p.add_argument("--sub-font-size", type=int, default=18, help="Subtitle font size for hard-burn")
    p.add_argument("--sub-outline", type=int, default=1, help="Subtitle outline thickness")
    p.add_argument("--sub-shadow", type=int, default=0, help="Subtitle shadow")
    p.add_argument("--sub-margin-v", type=int, default=24, help="Subtitle vertical margin (pixels)")
    p.add_argument("--sub-alignment", type=int, default=2, help="ASS Alignment (2=bottom-center)")
    # Subtitle placement box (optional): when enabled, subtitles are forced to the center of this box.
    # This takes precedence over alignment/margins.
    p.add_argument("--sub-place-enable", action="store_true", help="Force subtitle position to the center of a user-defined box")
    p.add_argument("--sub-place-coord-mode", default="ratio", choices=["ratio", "px"], help="Coordinate mode for subtitle box")
    p.add_argument("--sub-place-x", type=float, default=0.0, help="Subtitle box X (ratio or px)")
    p.add_argument("--sub-place-y", type=float, default=0.78, help="Subtitle box Y (ratio or px)")
    p.add_argument("--sub-place-w", type=float, default=1.0, help="Subtitle box width (ratio or px)")
    p.add_argument("--sub-place-h", type=float, default=0.22, help="Subtitle box height (ratio or px)")
    # Mux sync (hearing-first): when audio is longer than video
    p.add_argument("--mux-sync-strategy", choices=["slow", "freeze"], default="slow", help="When audio is longer: slow video or freeze last frame")
    p.add_argument("--mux-slow-max-ratio", type=float, default=1.10, help="Max slow-down ratio for whole video (e.g. 1.10 = 10% slower)")
    p.add_argument("--mux-slow-threshold-s", type=float, default=0.05, help="Trigger threshold seconds for applying sync strategy")
    # Deprecated (kept for CLI backward compatibility only; ignored in runtime):
    # Historically used to trim TTS script to fit time budget. This caused fact loss (unacceptable for delivery).
    p.add_argument("--tts-fit-enable", action="store_true", help="(deprecated) Ignored. TTS fitting by trimming is removed.")
    p.add_argument("--tts-fit-wps", type=float, default=2.6, help="(deprecated) Ignored.")
    p.add_argument("--tts-fit-min-words", type=int, default=3, help="(deprecated) Ignored.")
    p.add_argument("--tts-fit-save-raw", action="store_true", help="(deprecated) Ignored.")
    # P1-2: per-segment TTS planning (hearing-first)
    p.add_argument("--tts-plan-enable", action="store_true", help="Enable P1-2 TTS time-budget planning (hearing-first)")
    p.add_argument("--tts-plan-safety-margin", type=float, default=0.05, help="Reserved tail margin seconds (planning only)")
    p.add_argument("--tts-plan-min-cap", type=float, default=1.05, help="Minimum speed cap used by planner (planning only)")
    # ASR text normalization (extremely low-risk). Enabled by config; can be disabled for debugging.
    p.add_argument("--asr-normalize-enable", action="store_true", help="Enable low-risk Chinese ASR text normalization")
    p.add_argument(
        "--asr-normalize-dict",
        type=Path,
        default=Path("assets/asr_normalize/asr_zh_dict.json"),
        help="Optional JSON dictionary for known ASR typos (defaults to an empty dict file)",
    )
    args, unknown = p.parse_known_args()
    if unknown:
        print(f"[quality] Ignoring unknown args: {unknown}")
    return args


def main() -> None:
    args = parse_args()
    global _DEFAULT_SAME_PINYIN_PATH, _DEFAULT_SAME_STROKE_PATH, _DEFAULT_REPAIR_LEXICON_PATH
    global _DEFAULT_REPAIR_PROPER_NOUNS_PATH, _DEFAULT_PROJECT_CONFUSIONS_PATH
    _DEFAULT_SAME_PINYIN_PATH = str(getattr(args, "zh_phrase_same_pinyin_path", _DEFAULT_SAME_PINYIN_PATH) or _DEFAULT_SAME_PINYIN_PATH)
    _DEFAULT_SAME_STROKE_PATH = str(getattr(args, "zh_phrase_same_stroke_path", _DEFAULT_SAME_STROKE_PATH) or _DEFAULT_SAME_STROKE_PATH)
    _DEFAULT_REPAIR_LEXICON_PATH = str(getattr(args, "zh_repair_lexicon_path", _DEFAULT_REPAIR_LEXICON_PATH) or _DEFAULT_REPAIR_LEXICON_PATH)
    _DEFAULT_REPAIR_PROPER_NOUNS_PATH = str(getattr(args, "zh_repair_proper_nouns_path", _DEFAULT_REPAIR_PROPER_NOUNS_PATH) or _DEFAULT_REPAIR_PROPER_NOUNS_PATH)
    _DEFAULT_PROJECT_CONFUSIONS_PATH = str(getattr(args, "asr_project_confusions_path", _DEFAULT_PROJECT_CONFUSIONS_PATH) or _DEFAULT_PROJECT_CONFUSIONS_PATH)
    try:
        import torch  # type: ignore

        cuda_available = bool(torch.cuda.is_available())
        gpu_name = torch.cuda.get_device_name(0) if cuda_available else ""
    except Exception:
        cuda_available = False
        gpu_name = ""
    print(
        "[runtime] local_device_policy:"
        f" whisperx_device={getattr(args, 'whisperx_device', 'auto')}"
        f" coqui_device={getattr(args, 'coqui_device', 'auto')}"
        f" torch_cuda_available={cuda_available}"
        f"{f' gpu={gpu_name}' if gpu_name else ''}"
    )
    print(
        "[runtime] llm_device_policy:"
        f" endpoint={getattr(args, 'llm_endpoint', '')}"
        f" model={getattr(args, 'llm_model', '')}"
        " runtime=host_ollama_auto_gpu_if_available"
    )

    missing = check_dep(args)
    if missing:
        sys.exit("质量模式依赖未满足：\n- " + "\n- ".join(missing))

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    work_tts = output_dir / "tts_segments"
    work_asr_prefix = output_dir / "asr_whisperx"

    audio_pcm = output_dir / "audio.wav"
    audio_json = output_dir / "audio.json"
    chs_srt = output_dir / "chs.srt"
    chs_norm_srt = output_dir / "chs.norm.srt"
    chs_phrases_json = output_dir / "chs.phrases.json"
    chs_suspects_json = output_dir / "chs.suspects.json"
    chs_llm_srt = output_dir / "chs.llm.srt"
    chs_llm_json = output_dir / "chs.llm.json"
    eng_srt = output_dir / "eng.srt"
    en_dict = load_en_dict(getattr(args, "en_replace_dict", None))
    glossary = load_glossary(getattr(args, "glossary", None))
    bi_srt = output_dir / "bilingual.srt"
    display_srt = output_dir / "display.srt"
    display_meta_json = output_dir / "display_meta.json"
    # v2 product decision: single source of truth for EN subtitles and dubbing script.
    # - TTS reads from eng.srt (seg.translation) directly.
    # - Do NOT generate separate eng_tts*.srt or tts_fit.json (word trimming is removed to prevent fact loss).
    tts_wav = output_dir / "tts_full.wav"
    video_dub = output_dir / "output_en.mp4"
    video_sub = output_dir / "output_en_sub.mp4"
    tts_plan_json = output_dir / "tts_plan.json"

    # Pipeline stage count used in log prefixes. Must be defined for all resume modes.
    # (resume_from=mt/tts/mux skips earlier steps but still shares the same stage numbering.)
    PIPE_TOTAL = 8

    segments = _prepare_initial_segments(
        args,
        pipe_total=PIPE_TOTAL,
        audio_pcm=audio_pcm,
        audio_json=audio_json,
        chs_srt=chs_srt,
        chs_norm_srt=chs_norm_srt,
        glossary=glossary,
    )

    # ----------------------------
    # zh_polish (P0/P1/P2) + review gate (before MT)
    # ----------------------------
    zh_opts = _build_zh_polish_stage_options(args)
    resume_from = zh_opts.resume_from
    review_enabled = zh_opts.review_enabled
    stop_after = zh_opts.stop_after
    zh_phrase_enable = zh_opts.zh_phrase_enable
    zh_post_polish_requested = zh_opts.zh_post_polish_requested
    zh_gate_min_high_risk = zh_opts.zh_gate_min_high_risk
    zh_gate_min_total_suspects = zh_opts.zh_gate_min_total_suspects
    zh_gate_on_phrase_error = zh_opts.zh_gate_on_phrase_error
    _print_zh_polish_stage_banner(zh_opts, PIPE_TOTAL)
    _reset_contract_stats(
        _load_contract_stats_seed(output_dir)
        if str(resume_from or "full").strip().lower() != "full"
        else None
    )

    # Product main path:
    # zh_polish = deterministic cleanup -> phrase/span detection -> bounded local zh repair.
    # When review is enabled, we always stop before MT so the user can review the Chinese source.
    if not zh_opts.allow_zh_polish:
        print(f"  [3/{PIPE_TOTAL}] zh_polish skipped: resume_from={resume_from}, preserving existing zh artifacts")
    else:
        rule_reasons_by_idx: Dict[int, List[str]] = {}
        for i, seg in enumerate(segments, 1):
            rr = _rule_based_suspect(seg)
            if rr:
                rule_reasons_by_idx[int(i)] = rr
        spans_by_idx, zh_phrase_error = _extract_zh_phrase_spans_for_segments(
            args,
            segments=segments,
            rule_reasons_by_idx=rule_reasons_by_idx,
            pipe_total=PIPE_TOTAL,
        )
        zh_artifacts = _collect_zh_polish_artifacts(
            segments,
            spans_by_idx=spans_by_idx,
            rule_reasons_by_idx=rule_reasons_by_idx,
            zh_phrase_error=zh_phrase_error,
            zh_phrase_enable=bool(zh_phrase_enable),
            min_high_risk=zh_gate_min_high_risk,
            min_total_suspects=zh_gate_min_total_suspects,
            pause_on_phrase_error=zh_gate_on_phrase_error,
        )
        zh_artifacts = ZhPolishArtifacts(
            phrase_items=zh_artifacts.phrase_items,
            suspects=zh_artifacts.suspects,
            gate_summary=_apply_review_gate_policy(zh_artifacts.gate_summary, review_enabled=bool(review_enabled)),
        )

        zh_post_artifacts = ZhPostPolishArtifacts(llm_lines_by_idx={}, llm_meta_items=[])
        polish_idxs = _collect_polish_target_indexes(
            zh_artifacts.suspects,
            max_lines=max(0, int(getattr(args, "zh_post_polish_max_lines", 6) or 6)),
        )
        zh_opt_enable = bool(zh_post_polish_requested)
        if zh_opt_enable and polish_idxs:
            print(
                f"  [3/{PIPE_TOTAL}][P2] zh_post_polish: model={args.llm_model} suspect_lines={len(polish_idxs)}"
            )
            llm_lines_by_idx = _request_zh_post_polish_lines(
                args,
                segments=segments,
                spans_by_idx=spans_by_idx,
                rule_reasons_by_idx=rule_reasons_by_idx,
                polish_idxs=polish_idxs,
                zh_post_polish_enable=True,
            )
            zh_post_artifacts = _apply_zh_post_polish_results(
                segments=segments,
                spans_by_idx=spans_by_idx,
                rule_reasons_by_idx=rule_reasons_by_idx,
                polish_idxs=polish_idxs,
                llm_lines_by_idx=llm_lines_by_idx,
                zh_post_polish_enable=True,
            )
        else:
            zh_post_artifacts = _apply_zh_post_polish_results(
                segments=segments,
                spans_by_idx=spans_by_idx,
                rule_reasons_by_idx=rule_reasons_by_idx,
                polish_idxs=set(),
                llm_lines_by_idx={},
                zh_post_polish_enable=False,
            )
        _write_zh_post_polish_artifacts(
            chs_llm_srt=chs_llm_srt,
            chs_llm_json=chs_llm_json,
            segments=segments,
            artifacts=zh_post_artifacts,
        )

        polish_meta_by_idx = {
            int(it.get("idx")): it
            for it in (zh_post_artifacts.llm_meta_items or [])
            if isinstance(it, dict) and int(it.get("idx") or 0) > 0
        }
        suspects2: List[Dict[str, Any]] = []
        for it in zh_artifacts.suspects:
            idx = int(it.get("idx") or 0)
            meta = dict(polish_meta_by_idx.get(idx) or {})
            row = dict(it)
            if meta:
                row["base"] = str(meta.get("base") or row.get("text") or "")
                row["opt"] = str(meta.get("opt") or row.get("text") or "")
                row["changed"] = str(row.get("base") or "").strip() != str(row.get("opt") or "").strip()
                row["text"] = str(row.get("opt") or row.get("text") or "")
                row["polished"] = bool(meta.get("polished"))
                row["polish_attempted"] = bool(meta.get("polish_attempted"))
                row["change_kind"] = str(meta.get("change_kind") or "none")
            suspects2.append(row)
        zh_artifacts = ZhPolishArtifacts(
            phrase_items=zh_artifacts.phrase_items,
            suspects=suspects2,
            gate_summary=zh_artifacts.gate_summary,
        )
        _write_zh_polish_artifacts(
            chs_phrases_json=chs_phrases_json,
            chs_suspects_json=chs_suspects_json,
            artifacts=zh_artifacts,
            zh_phrase_error=zh_phrase_error,
            zh_polish_enabled=bool(zh_phrase_enable or zh_post_polish_requested or review_enabled),
            review_gate_enabled=bool(review_enabled),
            zh_opt_enabled=bool(zh_opt_enable),
        )
        _write_contract_stats(output_dir, stage="zh_polish")

        _maybe_pause_after_zh_polish(
            zh_opts,
            gate_summary=zh_artifacts.gate_summary,
            zh_phrase_error=zh_phrase_error,
            suspects_n=len(zh_artifacts.suspects),
        )


    if _should_run_mt_stage(args):
        print(f"[4/{PIPE_TOTAL}] Translating with local LLM...")
        mt_context_window = int(getattr(args, "mt_context_window", 0) or 0)
        mt_style = str(getattr(args, "mt_style", "") or "").strip()
        mt_max_words_per_line = int(getattr(args, "mt_max_words_per_line", 0) or 0)
        mt_compact_enable = bool(getattr(args, "mt_compact_enable", False))
        mt_compact_aggressive = bool(getattr(args, "mt_compact_aggressive", False))
        mt_compact_temperature = float(getattr(args, "mt_compact_temperature", 0.1) or 0.1)
        mt_compact_max_tokens = int(getattr(args, "mt_compact_max_tokens", 96) or 96)
        mt_compact_timeout_s = int(getattr(args, "mt_compact_timeout_s", 120) or 120)
        mt_request_timeout_s = int(getattr(args, "mt_request_timeout_s", 120) or 120)
        mt_request_retries = int(getattr(args, "mt_request_retries", 2) or 2)
        mt_long_zh_chars = int(getattr(args, "mt_long_zh_chars", 60) or 60)
        mt_long_en_words = int(getattr(args, "mt_long_en_words", 22) or 22)
        mt_long_target_words = int(getattr(args, "mt_long_target_words", 18) or 18)
        mt_prompt_mode = str(getattr(args, "mt_prompt_mode", "short") or "short").strip().lower()
        mt_prompt_mode = "long" if mt_prompt_mode == "long" else "short"
        mt_long_fallback_enable = bool(getattr(args, "mt_long_fallback_enable", False))
        mt_long_fallback_max_lines = int(getattr(args, "mt_long_fallback_max_lines", 10) or 10)
        mt_long_fallback_max_ratio = float(getattr(args, "mt_long_fallback_max_ratio", 0.25) or 0.25)
        mt_long_examples_enable = bool(getattr(args, "mt_long_examples_enable", False))
        mt_prompt_profile = str(getattr(args, "mt_prompt_profile", "") or "").strip()
        mt_two_pass_enable = not bool(getattr(args, "mt_two_pass_disable", False))
        mt_reasoning_effort = str(getattr(args, "mt_reasoning_effort", "") or "").strip()
        selfcheck_enable = not bool(getattr(args, "llm_selfcheck_disable", False))
        mt_source = _prepare_mt_source_segments(
            args,
            segments=segments,
            output_dir=output_dir,
            chs_srt=chs_srt,
        )
        try:
            seg_en = translate_segments_llm(
                segments,
                endpoint=args.llm_endpoint,
                model=args.llm_model,
                api_key=args.llm_api_key,
                chunk_size=max(1, int(getattr(args, "llm_chunk_size", 2) or 2)),
                context_window=mt_context_window,
                style_hint=mt_style,
                max_words_per_line=mt_max_words_per_line,
                compact_enable=mt_compact_enable,
                compact_aggressive=mt_compact_aggressive,
                compact_temperature=mt_compact_temperature,
                compact_max_tokens=mt_compact_max_tokens,
                compact_timeout_s=mt_compact_timeout_s,
                long_zh_chars=mt_long_zh_chars,
                long_en_words=mt_long_en_words,
                long_target_words=mt_long_target_words,
                prompt_mode=mt_prompt_mode,
                prompt_profile=mt_prompt_profile,
                two_pass_enable=mt_two_pass_enable,
                long_fallback_enable=mt_long_fallback_enable,
                long_fallback_max_lines=mt_long_fallback_max_lines,
                long_fallback_max_ratio=mt_long_fallback_max_ratio,
                long_examples_enable=mt_long_examples_enable,
                glossary=glossary,
                selfcheck_enable=selfcheck_enable,
                selfcheck_max_lines=max(0, int(getattr(args, "llm_selfcheck_max_lines", 10) or 10)),
                selfcheck_max_ratio=float(getattr(args, "llm_selfcheck_max_ratio", 0.25) or 0.25),
                context_src_lines=mt_source.context_src_lines,
                mt_reasoning_effort=mt_reasoning_effort,
                request_timeout_s=mt_request_timeout_s,
                request_retries=mt_request_retries,
            )
        finally:
            _write_contract_stats(output_dir, stage="mt")
        if glossary:
            stats = apply_glossary_to_segments(seg_en, glossary)
            print(f"[3b] Glossary applied: {stats}")
        # Optional English replacements dict (cautious, whole-word)
        if en_dict:
            for seg in seg_en:
                seg.translation = apply_en_replacements(getattr(seg, "translation", "") or "", en_dict)
        # Final deterministic guardrail (after en_fixes) for auto-generated subtitles only.
        for seg in seg_en:
            seg.translation = _final_en_guardrail(seg.translation or "", zh=str(getattr(seg, "text", "") or ""))
        write_srt(eng_srt, seg_en, text_attr="translation")
        # P0: build a deterministic TTS script for synthesis (not user-configurable).
        for seg in seg_en:
            seg.tts = _build_tts_script(seg.translation or "")
    else:
        seg_en = _restore_resume_translations(
            args,
            segments=segments,
            eng_srt=eng_srt,
            en_dict=en_dict,
        )

    # NOTE: tts_fit_enable is intentionally ignored (deprecated). We no longer trim text for duration fitting.

    # ----------------------------
    # P0: subtitle post-process
    # ----------------------------
    if getattr(args, "subtitle_postprocess_enable", False):
        # Normalize translations to a single line first (reduces formatting pollution).
        for seg in seg_en:
            seg.translation = _normalize_en_line(seg.translation or "")
        # Optional soft wrap to reduce "long line" warnings.
        if getattr(args, "subtitle_wrap_enable", False):
            max_chars = int(getattr(args, "subtitle_max_chars_per_line", 80) or 80)
            max_lines = int(getattr(args, "subtitle_wrap_max_lines", 2) or 2)
            wrapped = 0
            for seg in seg_en:
                before = seg.translation or ""
                after = _wrap_en_for_subtitle(before, max_chars_per_line=max_chars, max_lines=max_lines)
                if after != before:
                    seg.translation = after
                    wrapped += 1
            print(f"[p0] subtitle_wrap: enabled, wrapped={wrapped}, max_chars_per_line={max_chars}, max_lines={max_lines}")

    # ----------------------------
    # P1-2: TTS time-budget planning (hearing-first; no eng_tts.srt required)
    # - Keep required speed <= tts_speed_max (e.g., 1.15)
    # - Borrow time by shifting segment timeline forward (subtitles follow audio)
    # - Last resort: if we hit the source end cap, lightly trim the tail segments deterministically
    # ----------------------------
    if getattr(args, "tts_plan_enable", False):
        try:
            wps = float(getattr(args, "tts_fit_wps", 2.6) or 2.6)
            max_speed = float(getattr(args, "tts_speed_max", 1.15) or 1.15)
            # Planning min duration: keep short lines from inflating too much.
            # We still respect CPS & max_speed constraints; this is just a floor to avoid 0.2s segments.
            min_dur = float(getattr(args, "min_sub_dur", 1.8) or 1.8)
            min_gap = 0.04  # keep tiny gap to avoid overlaps
            max_cps = float(getattr(args, "subtitle_max_cps", 20.0) or 20.0)
            # Hard cap total duration to avoid “补帧太多”.
            # Use source video duration * mux_slow_max_ratio as upper bound (hearing-first but bounded).
            cap_ratio = float(getattr(args, "mux_slow_max_ratio", 1.08) or 1.08)
            cap_ratio = max(1.0, min(cap_ratio, 1.30))
            tail_margin = float(getattr(args, "tts_plan_safety_margin", 0.05) or 0.05)
            min_words = int(getattr(args, "tts_fit_min_words", 1) or 1)
            src_dur_s = None
            try:
                from pathlib import Path as _Path

                from pipelines.lib.media.media_probe import probe_duration_s as _probe_duration_s

                src_dur_s = _probe_duration_s(_Path(str(args.video)))
            except Exception:
                src_dur_s = None
            cap_end = None
            if src_dur_s and src_dur_s > 0:
                cap_end = max(float(src_dur_s) * cap_ratio - tail_margin, 0.5)

            def _word_budget_for_duration(text: str, *, dur_s: float) -> int:
                """
                Compute a deterministic word budget to fit within dur_s at max_speed.
                We subtract simple punctuation pauses so the remaining time is for words.
                """
                if not text:
                    return 0
                d = max(float(dur_s), 0.05)
                pauses = 0.12 * len(re.findall(r"[,;:]", text)) + 0.22 * len(re.findall(r"[.!?]", text))
                budget_words = int(max(1, (max(d * max_speed - pauses, 0.15) * wps)))
                return budget_words
            min_words_default = int(getattr(args, "tts_fit_min_words", 3) or 3)

            plans = []
            prev_end = None
            for i, seg in enumerate(seg_en):
                aggressive_line = False
                st0 = float(seg.start)
                ed0 = float(seg.end)
                dur0 = max(ed0 - st0, 0.001)
                txt = str(seg.translation or "").strip()
                est = _estimate_en_seconds(txt, wps=wps) if txt else 0.0
                # Minimal duration from CPS. We keep a tiny floor so 0.2s segments don't become unreadable,
                # but we now prefer trimming text (global) over extending the whole timeline.
                cps_need = (len(txt) / max(max_cps, 1.0)) if txt else 0.0
                floor = min(min_dur, max(0.25, cps_need)) if txt else min_dur
                # "Aggressive" mode for very short segments: do NOT inflate to cps_need; trim instead.
                if dur0 < 0.8 and txt:
                    base_dur = max(dur0, 0.25)
                else:
                    base_dur = max(dur0, floor, cps_need)

                # Do NOT trim translation text for planning. Single source of truth: keep subtitles/dubbing faithful.

                need_dur = float(base_dur)
                st = st0
                if prev_end is not None:
                    st = max(st, float(prev_end) + float(min_gap))
                ed = st + need_dur
                plans.append(
                    {
                        "idx": i + 1,
                        "text": txt[:180],
                        "orig": {"start": round(st0, 3), "end": round(ed0, 3), "dur": round(dur0, 3)},
                        "planned": {"start": round(st, 3), "end": round(ed, 3), "dur": round(need_dur, 3)},
                        "est_s": round(float(est), 3),
                        "required_speed": round((float(est) / float(need_dur)) if need_dur > 0 else 0.0, 3),
                        "trim": {"mode": "rule", "aggressive": bool(aggressive_line)}
                        if txt
                        else None,
                    }
                )
                seg.start = float(st)
                seg.end = float(ed)
                prev_end = float(ed)

            # Cap to bounded max end; do NOT trim text (single source of truth)
            if cap_end is not None and seg_en:
                severe_overlong_pressure = any(
                    (float(p.get("required_speed") or 0.0) > float(max_speed) * 1.18)
                    or (float((p.get("planned") or {}).get("dur") or 0.0) < 0.5 and len(str(p.get("text") or "")) >= 32)
                    for p in plans
                )
                if severe_overlong_pressure and src_dur_s and src_dur_s > 0:
                    soft_ratio = min(1.26, cap_ratio + 0.06)
                    cap_end = max(cap_end, max(float(src_dur_s) * soft_ratio - tail_margin, 0.5))

                # Walk backwards: if the tail exceeds cap_end, cap & trim that segment to fit under max_speed
                rebalanced = _rebalance_tts_plan_under_cap(
                    seg_en,
                    cap_end=float(cap_end),
                    min_gap=float(min_gap),
                    min_dur=float(min_dur),
                    max_cps=float(max_cps),
                )
                if rebalanced:
                    for p, seg in zip(plans, seg_en):
                        p["planned"] = {
                            "start": round(float(seg.start), 3),
                            "end": round(float(seg.end), 3),
                            "dur": round(max(float(seg.end) - float(seg.start), 0.0), 3),
                        }
                        need = max(float(seg.end) - float(seg.start), 0.001)
                        p["required_speed"] = round((float(p.get("est_s") or 0.0) / need) if need > 0 else 0.0, 3)
                    print(f"[p1.2] tts_plan: redistributed under cap_end={float(cap_end):.3f}")
                else:
                    for i in range(len(seg_en) - 1, -1, -1):
                        seg = seg_en[i]
                        if float(seg.end) <= cap_end:
                            cap_end = float(seg.start) - float(min_gap)
                            continue
                        seg.end = max(float(seg.start) + 0.35, cap_end)
                        cap_end = float(seg.start) - float(min_gap)
                        # no text trimming here

            try:
                tts_plan_json.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "enabled": True,
                            "params": {
                                "wps": wps,
                                "max_speed": max_speed,
                                "min_sub_dur": min_dur,
                                "min_gap": min_gap,
                                "cap_ratio": cap_ratio,
                                "cap_end": cap_end,
                                "min_words_default": min_words_default,
                            },
                            "plans": plans[:400],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            print(f"[p1.2] tts_plan: enabled, max_speed={max_speed}, wps={wps}, min_sub_dur={min_dur}")
        except Exception as exc:
            print(f"[warn] tts_plan failed, continuing without it: {exc}")

    # ----------------------------
    # P1-3: Repair truncated EN lines (post time-budget trims)
    # ----------------------------
    try:
        bad_idxs = [i for i, seg in enumerate(seg_en) if _en_line_is_fragment(getattr(seg, "translation", "") or "")]
        if bad_idxs:
            fixed_n = 0
            for i in bad_idxs:
                seg = seg_en[i]
                zh = str(getattr(seg, "text", "") or "")
                en0 = str(getattr(seg, "translation", "") or "")
                en1 = _repair_fragment_en_deterministic(zh, en0)
                # If still bad, use a tiny LLM repair (context: prev/next zh).
                if _en_line_is_fragment(en1):
                    prev = str(getattr(seg_en[i - 1], "text", "") or "") if i - 1 >= 0 else ""
                    nxt = str(getattr(seg_en[i + 1], "text", "") or "") if i + 1 < len(seg_en) else ""
                    en2 = _repair_fragment_en_llm(
                        endpoint=str(getattr(args, "llm_endpoint", "") or ""),
                        model=str(getattr(args, "llm_model", "") or ""),
                        api_key=str(getattr(args, "llm_api_key", "") or ""),
                        zh=zh,
                        en_bad=en1 or en0,
                        ctx_prev=prev,
                        ctx_next=nxt,
                    )
                    if en2:
                        en1 = en2
                if en1 and en1 != en0 and not _en_line_is_fragment(en1):
                    seg.translation = en1
                    fixed_n += 1
            if fixed_n:
                print(f"[p1.3] en_fragment_repair: fixed={fixed_n}/{len(bad_idxs)}")
    except Exception as exc:
        print(f"[warn] en fragment repair failed, continuing: {exc}")

    # Re-apply subtitle wrapping because fragment repair may rewrite the text and
    # remove earlier line breaks, causing long single-line subtitles to come back.
    if getattr(args, "subtitle_wrap_enable", False):
        try:
            max_chars = int(getattr(args, "subtitle_max_chars_per_line", 42) or 42)
            max_lines = int(getattr(args, "subtitle_max_lines", 2) or 2)
            rewrapped = 0
            for seg in seg_en:
                before = str(getattr(seg, "translation", "") or "")
                after = _wrap_en_for_subtitle(before, max_chars_per_line=max_chars, max_lines=max_lines)
                if after != before:
                    seg.translation = after
                    rewrapped += 1
            if rewrapped:
                print(f"[p1.3] subtitle_wrap_reapply: updated={rewrapped}")
        except Exception as exc:
            print(f"[warn] subtitle wrap reapply failed, continuing: {exc}")

    # Always rewrite subtitle artifacts from current in-memory segments to keep timestamps/text consistent.
    # (This is important when planning adjusted timestamps or when review overrides were loaded.)
    write_srt(chs_srt, seg_en, text_attr="text")
    write_srt(eng_srt, seg_en, text_attr="translation")
    bilingual_enabled = getattr(args, "bilingual_srt", True)
    if bilingual_enabled:
        bilingual_segments = []
        for seg in seg_en:
            bilingual_text = f"{seg.text}\n{seg.translation}"
            bilingual_segments.append(Segment(start=seg.start, end=seg.end, text=bilingual_text, translation=seg.translation))
        write_srt(bi_srt, bilingual_segments, text_attr="text")

    # ----------------------------
    # P0: display subtitles (screen-friendly)
    # ----------------------------
    _maybe_build_display_subtitles(
        args,
        seg_en=seg_en,
        display_srt=display_srt,
        display_meta_json=display_meta_json,
    )

    # Persist updated timings/fields for resume flows.
    try:
        audio_json.write_text(json.dumps([seg.__dict__ for seg in seg_en], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    if args.skip_tts:
        print("Skip TTS enabled; generated subtitles only.")
        return

    # If we didn't just extract audio (resume), still try to compute total duration for padding.
    if "audio_total_ms" not in locals():
        audio_total_ms = _resolve_audio_total_ms(audio_pcm)

    if args.resume_from is None or args.resume_from in {"asr", "mt", "tts"}:
        _run_tts_stage(
            args,
            seg_en=seg_en,
            work_tts=work_tts,
            tts_wav=tts_wav,
            audio_total_ms=audio_total_ms,
            pipe_total=PIPE_TOTAL,
        )

    if args.resume_from == "mux" and not tts_wav.exists():
        raise RuntimeError(f"resume_from=mux requires existing TTS audio, but not found: {tts_wav}")

    _run_mux_stage(
        args,
        tts_wav=tts_wav,
        video_dub=video_dub,
        pipe_total=PIPE_TOTAL,
    )
    embed_completed = _run_embed_stage(
        args,
        eng_srt=eng_srt,
        display_srt=display_srt,
        video_dub=video_dub,
        video_sub=video_sub,
        pipe_total=PIPE_TOTAL,
    )
    if not embed_completed:
        return
    _write_contract_stats(output_dir, stage="done")
    _print_final_outputs(
        output_dir=output_dir,
        audio_json=audio_json,
        chs_srt=chs_srt,
        eng_srt=eng_srt,
        bi_srt=bi_srt,
        tts_wav=tts_wav,
        video_dub=video_dub,
        video_sub=video_sub,
    )


if __name__ == "__main__":
    main()

