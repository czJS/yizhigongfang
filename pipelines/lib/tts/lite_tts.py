from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import List, Optional

try:
    from pydub import AudioSegment  # type: ignore
except ImportError:  # pragma: no cover - optional runtime dependency
    AudioSegment = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    np = None  # type: ignore

from pipelines.lib.asr.lite_asr import Segment
from pipelines.lib.utils.exec_utils import find_espeak_data_dir, prepare_piper_bin, run_cmd


def clean_tts_text(text: str) -> str:
    """Lightweight cleaning to avoid TTS报错/异常发音。"""
    # Remove common numbering prefixes like "1.", "2)" that may come from LLM output.
    text = re.sub(r"^\s*\d+\s*[\.\)]\s*", "", text.strip())
    # Normalize ampersand for better pronunciation (many TTS read '&' oddly or skip it).
    text = text.replace("&", " and ")
    # Remove CJK characters + fullwidth punctuation to avoid feeding non-English text
    # into English-only TTS models (Coqui/Piper).
    text = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", " ", text)
    chars_to_remove = ["&", "®", "™", "©"]
    for ch in chars_to_remove:
        text = text.replace(ch, "")
    # collapse spaces and strip
    text = re.sub(r"\s+", " ", text).strip()
    # If only punctuation/symbols remain (e.g. "."), treat as empty so caller can insert silence.
    if not re.sub(r"[\W_]+", "", text, flags=re.UNICODE):
        return ""
    return text


def split_for_tts(text: str, max_len: int = 80) -> List[str]:
    """
    Split long English text into smaller pieces for TTS stability.
    Uses punctuation-first, then whitespace fallback.
    """
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return [text]
    soft_whole_limit = max(max_len + 24, int(max_len * 1.3))
    punct_n = len(re.findall(r"[.!?;:]", text))
    comma_n = len(re.findall(r"[,，、]", text))
    if len(text) <= soft_whole_limit and punct_n <= 2 and comma_n <= 3:
        return [text]
    parts: List[str] = []
    buf: List[str] = []
    for token in re.split(r"(\.|\?|!|,|;|:)\s*", text):
        if not token:
            continue
        buf.append(token)
        joined = "".join(buf).strip()
        if len(joined) >= max_len or (buf and buf[-1] in {".", "?", "!", ";", ":"}):
            parts.append(joined)
            buf = []
    if buf:
        parts.append("".join(buf).strip())
    final_parts: List[str] = []
    for p in parts:
        if len(p) <= max_len:
            final_parts.append(p)
            continue
        words = p.split()
        cur: List[str] = []
        cur_len = 0
        for w in words:
            if cur_len + len(w) + 1 > max_len and cur:
                final_parts.append(" ".join(cur))
                cur = []
                cur_len = 0
            cur.append(w)
            cur_len += len(w) + 1
        if cur:
            final_parts.append(" ".join(cur))
    normalized = [p.strip() for p in final_parts if p.strip()]
    if len(normalized) <= 1:
        return normalized
    preferred_len = max(32, min(int(max_len * 0.84), max_len - 4))
    merged: List[str] = []
    for part in normalized:
        if not merged:
            merged.append(part)
            continue
        prev = merged[-1].strip()
        candidate = f"{prev} {part}".strip()
        if len(prev) < preferred_len and len(candidate) <= max_len:
            merged[-1] = candidate
        else:
            merged.append(part)
    return [p for p in merged if p.strip()]


def _audiosegment_from_waveform(waveform, frame_rate: int) -> "AudioSegment":
    if AudioSegment is None:
        raise SystemExit("pydub is required for TTS post-processing. Please install pydub.")
    sr = max(1, int(frame_rate or 22050))
    if np is not None:
        arr = np.asarray(waveform, dtype=np.float32).reshape(-1)
        arr = np.clip(arr, -1.0, 1.0)
        pcm = (arr * 32767.0).astype(np.int16).tobytes()
    else:
        vals = [max(-32768, min(32767, int(float(x) * 32767.0))) for x in (waveform or [])]
        pcm = b"".join(int(v).to_bytes(2, byteorder="little", signed=True) for v in vals)
    return AudioSegment(data=pcm, sample_width=2, frame_rate=sr, channels=1)


def synthesize_with_piper(
    text: str,
    model_path: Path,
    output_wav: Path,
    piper_bin: str = "piper",
) -> None:
    # 调用 piper CLI 生成单段 wav，不做长度对齐（后续处理）
    text = text.replace("\n", " ").strip()
    piper_bin = prepare_piper_bin(piper_bin)
    env = os.environ.copy()
    # Ensure piper can find its sibling helper (piper_phonemize) after relocation to /tmp
    try:
        pb = Path(piper_bin)
        if pb.is_absolute():
            env["PATH"] = str(pb.parent) + os.pathsep + env.get("PATH", "")
    except Exception:
        pass
    espeak_dir = find_espeak_data_dir(piper_bin)
    cmd = [
        piper_bin,
        "--model",
        str(model_path),
        "--output_file",
        str(output_wav),
    ]
    if espeak_dir is not None:
        cmd.extend(["--espeak_data", str(espeak_dir)])
    # Note: piper reads text from STDIN. Some builds do NOT support a --text argument.
    run_cmd(cmd, env=env, input_text=text + "\n")


def stretch_or_pad(
    audio: "AudioSegment",
    target_ms: float,
    allow_speed_change: bool = True,
    max_speed: float = 1.08,
    align_mode: str = "resample",
) -> "AudioSegment":
    """
    若语音短于目标时长则补静音；超长则可微调倍速或截断，尽量贴合字幕时间。
    速度上限 max_speed（默认 1.2x），避免合成端被过度提速导致“飙语速”。
    """
    if AudioSegment is None:
        raise SystemExit("pydub is required for TTS post-processing. Please install pydub.")

    current = len(audio)
    delta = target_ms - current
    if delta >= 0:
        return audio + AudioSegment.silent(duration=delta)
    # audio is longer than target
    small_overshoot_ms = min(120.0, max(float(target_ms) * 0.05, 45.0))
    if abs(delta) <= small_overshoot_ms:
        return audio[: int(max(target_ms, 0))]
    if not allow_speed_change or target_ms <= 0:
        return audio[: int(max(target_ms, 0))]
    speed = min(current / max(target_ms, 1), max_speed)
    mode = (align_mode or "resample").strip().lower()

    # Prefer pitch-preserving time-stretch for better "same speaker" perception.
    if mode == "atempo":
        try:
            import tempfile

            def _atempo_chain(s: float) -> str:
                # ffmpeg atempo supports 0.5..2.0 per filter; chain if needed (we normally don't).
                if s <= 0:
                    return "atempo=1.0"
                parts = []
                x = float(s)
                while x > 2.0:
                    parts.append("atempo=2.0")
                    x /= 2.0
                while x < 0.5:
                    parts.append("atempo=0.5")
                    x /= 0.5
                parts.append(f"atempo={x:.6f}")
                return ",".join(parts)

            with tempfile.TemporaryDirectory(prefix="tts_atempo_") as td:
                tin = Path(td) / "in.wav"
                tout = Path(td) / "out.wav"
                audio.export(tin, format="wav")
                run_cmd(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(tin),
                        "-filter:a",
                        _atempo_chain(speed),
                        str(tout),
                    ],
                    check=True,
                )
                sped = AudioSegment.from_file(tout)
                if len(sped) > target_ms:
                    sped = sped[: int(target_ms)]
                return sped
        except Exception:
            # fallback to resample below
            pass

    # Fallback: Increase speed by changing frame rate (faster, but pitch rises)
    sped = audio._spawn(audio.raw_data, overrides={"frame_rate": int(audio.frame_rate * speed)})
    sped = sped.set_frame_rate(audio.frame_rate)
    if len(sped) > target_ms:
        sped = sped[: int(target_ms)]
    return sped


def synthesize_segments(
    segments: List[Segment],
    model_path: Path,
    work_dir: Path,
    piper_bin: str = "piper",
    allow_speed_change: bool = True,
    split_len: int = 80,
    max_speed: float = 1.08,
    align_mode: str = "resample",
    pad_to_ms: Optional[float] = None,
) -> "AudioSegment":
    # 循环合成每一段英文音频 -> 长度校正 -> 按时间轴拼接（保留开头/段间静音，避免压缩时间线）
    if AudioSegment is None:
        raise SystemExit("pydub is required for TTS post-processing. Please install pydub.")

    work_dir.mkdir(parents=True, exist_ok=True)
    audio_chunks: List[AudioSegment] = []
    cursor_ms: float = 0.0
    missing_tr = 0
    for idx, seg in enumerate(segments, 1):
        # Translation may be empty for some segments (LLM/MT output quirks or aggressive cleaning).
        # For robustness, treat it as silence instead of failing the whole pipeline.
        # Prefer an explicit TTS script field when present (quality mode may generate it).
        raw_tts = getattr(seg, "tts", None)
        if raw_tts is None:
            raw_tts = seg.translation
        if raw_tts is None:
            raw_tts = ""
        text_clean = clean_tts_text(str(raw_tts))
        target_ms = max((seg.end - seg.start) * 1000.0, 300.0)
        # Preserve timeline gaps (including leading silence before first segment).
        gap_ms = max(seg.start * 1000.0 - cursor_ms, 0.0)
        if gap_ms >= 5.0:
            audio_chunks.append(AudioSegment.silent(duration=int(round(gap_ms))))
            cursor_ms += gap_ms
        if not text_clean:
            if str(raw_tts).strip() == "":
                missing_tr += 1
            # Fully stripped (e.g. non-English). Produce silence instead of synthesizing junk like "."
            audio_chunks.append(AudioSegment.silent(duration=target_ms))
            cursor_ms = max(cursor_ms, seg.end * 1000.0)
            continue
        parts = split_for_tts(text_clean, max_len=split_len)
        total_len = sum(len(p) for p in parts) or 1
        part_chunks: List[AudioSegment] = []

        for j, part in enumerate(parts):
            part_ms = max(target_ms * len(part) / total_len, 200.0)
            part_clean = clean_tts_text(part)
            if not part_clean:
                part_chunks.append(AudioSegment.silent(duration=part_ms))
                continue
            seg_wav = work_dir / f"seg_{idx:04d}_p{j}.wav"
            synthesize_with_piper(part_clean, model_path=model_path, output_wav=seg_wav, piper_bin=piper_bin)
            wav = AudioSegment.from_file(seg_wav)
            wav_aligned = stretch_or_pad(
                wav,
                target_ms=part_ms,
                allow_speed_change=allow_speed_change,
                max_speed=max_speed,
                align_mode=align_mode,
            )
            part_chunks.append(wav_aligned)

        if not part_chunks:
            raise ValueError("No audio chunks synthesized.")
        combined_part = sum(part_chunks[1:], part_chunks[0])
        combined_part = stretch_or_pad(
            combined_part,
            target_ms=target_ms,
            allow_speed_change=allow_speed_change,
            max_speed=max_speed,
            align_mode=align_mode,
        )
        audio_chunks.append(combined_part)
        cursor_ms = max(cursor_ms, seg.end * 1000.0)
    if not audio_chunks:
        raise ValueError("No audio chunks synthesized.")
    # Optional tail padding to match original audio duration (prevents ffmpeg -shortest from truncating video).
    if pad_to_ms is not None and pad_to_ms > cursor_ms:
        audio_chunks.append(AudioSegment.silent(duration=int(round(pad_to_ms - cursor_ms))))
    combined = sum(audio_chunks[1:], audio_chunks[0])
    if missing_tr:
        print(f"[warn] TTS: {missing_tr} segments had empty translation; used silence for those segments.")
    return combined


def save_audio(audio: "AudioSegment", path: Path, sample_rate: int = 22050) -> None:
    # 输出统一采样率的最终配音文件
    if AudioSegment is None:
        raise SystemExit("pydub is required for TTS post-processing. Please install pydub.")
    path.parent.mkdir(parents=True, exist_ok=True)
    audio.export(path, format="wav", parameters=["-ar", str(sample_rate)])


def _get_segment_tts_text(seg: Segment) -> str:
    raw_tts = getattr(seg, "tts", None)
    if raw_tts is None:
        raw_tts = seg.translation
    if raw_tts is None:
        raw_tts = ""
    return clean_tts_text(str(raw_tts))


def _merge_short_tts_segments(segments: List[Segment]) -> tuple[List[Segment], dict]:
    """
    Merge adjacent short subtitle blocks into a single synthesis unit.

    This is a synthesis-only optimization for XTTS: fewer calls usually help more
    than shaving a tiny amount off each call.
    """
    if not segments:
        return [], {"source_segments": 0, "synth_segments": 0, "merged_groups": 0, "merged_source_segments": 0}

    max_gap_s = 0.12
    max_group_size = 3
    short_chars = 42
    short_dur_s = 1.9
    max_group_chars = 96
    max_group_span_s = 5.2

    def _seg_text(seg: Segment) -> str:
        return _get_segment_tts_text(seg)

    def _is_short(seg: Segment, text: str) -> bool:
        dur_s = max(float(seg.end) - float(seg.start), 0.0)
        return len(text) <= short_chars or dur_s <= short_dur_s

    merged: List[Segment] = []
    i = 0
    merged_groups = 0
    merged_source_segments = 0
    while i < len(segments):
        base = segments[i]
        base_text = _seg_text(base)
        group = [base]
        texts = [base_text] if base_text else []
        j = i + 1
        while j < len(segments) and len(group) < max_group_size:
            nxt = segments[j]
            nxt_text = _seg_text(nxt)
            if not nxt_text:
                break
            prev = group[-1]
            gap_s = max(float(nxt.start) - float(prev.end), 0.0)
            span_s = max(float(nxt.end) - float(group[0].start), 0.0)
            joined_text = " ".join([t for t in [*texts, nxt_text] if t]).strip()
            if gap_s > max_gap_s:
                break
            if span_s > max_group_span_s:
                break
            if len(joined_text) > max_group_chars:
                break
            if not (_is_short(prev, texts[-1] if texts else "") and _is_short(nxt, nxt_text)):
                break
            group.append(nxt)
            texts.append(nxt_text)
            j += 1
        if len(group) == 1:
            merged.append(base)
            i += 1
            continue
        merged_text = " ".join(t for t in texts if t).strip()
        merged_seg = Segment(
            start=group[0].start,
            end=group[-1].end,
            text=group[0].text,
            translation=merged_text,
        )
        merged.append(merged_seg)
        merged_groups += 1
        merged_source_segments += len(group)
        i += len(group)
    meta = {
        "source_segments": len(segments),
        "synth_segments": len(merged),
        "merged_groups": merged_groups,
        "merged_source_segments": merged_source_segments,
    }
    return merged, meta


def build_kokoro_tts(model_path: Path, voices_path: Path):
    try:
        from kokoro_onnx import Kokoro  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime guard
        raise SystemExit("Kokoro ONNX not installed. Please `pip install kokoro-onnx`.") from exc
    if not model_path.exists():
        raise SystemExit(f"Kokoro model missing: {model_path}")
    if not voices_path.exists():
        raise SystemExit(f"Kokoro voices missing: {voices_path}")
    return Kokoro(str(model_path), str(voices_path))


def synthesize_segments_kokoro(
    segments: List[Segment],
    kokoro,
    work_dir: Path,
    sample_rate: int,
    voice: str = "af_sarah",
    language: str = "en-us",
    speed: float = 1.0,
    split_len: int = 100,
    max_speed: float = 1.08,
    align_mode: str = "resample",
    pad_to_ms: Optional[float] = None,
) -> "AudioSegment":
    if AudioSegment is None:
        raise SystemExit("pydub is required for TTS post-processing. Please install pydub.")
    work_dir.mkdir(parents=True, exist_ok=True)
    synth_segments, merge_meta = _merge_short_tts_segments(segments)
    if merge_meta["merged_groups"] > 0:
        print(
            f"[tts] merge_short: source_segments={merge_meta['source_segments']} "
            f"synth_segments={merge_meta['synth_segments']} merged_groups={merge_meta['merged_groups']} "
            f"merged_source_segments={merge_meta['merged_source_segments']}"
        )
    else:
        print(f"[tts] merge_short: source_segments={len(segments)} synth_segments={len(synth_segments)} merged_groups=0")

    audio_chunks: List[AudioSegment] = []
    cursor_ms: float = 0.0
    missing_tr = 0
    total_parts = 0
    total_synth_ms = 0.0
    total_align_ms = 0.0

    for idx, seg in enumerate(synth_segments, 1):
        seg_start_t = time.perf_counter()
        text_clean = _get_segment_tts_text(seg)
        target_ms = max((seg.end - seg.start) * 1000.0, 300.0)
        gap_ms = max(seg.start * 1000.0 - cursor_ms, 0.0)
        if gap_ms >= 5.0:
            audio_chunks.append(AudioSegment.silent(duration=int(round(gap_ms))).set_frame_rate(sample_rate))
            cursor_ms += gap_ms
        if not text_clean:
            missing_tr += 1
            audio_chunks.append(AudioSegment.silent(duration=target_ms).set_frame_rate(sample_rate))
            cursor_ms = max(cursor_ms, seg.end * 1000.0)
            print(f"[tts] seg={idx} empty_text=true target_ms={target_ms:.0f} gap_ms={gap_ms:.0f}")
            continue

        parts = split_for_tts(text_clean, max_len=split_len)
        part_chunks: List[AudioSegment] = []
        seg_synth_ms = 0.0
        seg_align_ms = 0.0
        for part in parts:
            part_clean = part.strip()
            if not part_clean:
                part_chunks.append(AudioSegment.silent(duration=200).set_frame_rate(sample_rate))
                continue
            t_part = time.perf_counter()
            wav, sr = kokoro.create(part_clean, voice=voice, speed=speed, lang=language)
            seg_synth_ms += (time.perf_counter() - t_part) * 1000.0
            part_chunks.append(_audiosegment_from_waveform(wav, sr).set_frame_rate(sample_rate))
        if not part_chunks:
            raise ValueError("No Kokoro audio chunks synthesized for segment.")
        combined_part = sum(part_chunks[1:], part_chunks[0])
        t_align = time.perf_counter()
        combined_part = stretch_or_pad(
            combined_part,
            target_ms=target_ms,
            allow_speed_change=True,
            max_speed=max_speed,
            align_mode=align_mode,
        )
        seg_align_ms += (time.perf_counter() - t_align) * 1000.0
        combined_part = combined_part.set_frame_rate(sample_rate)
        audio_chunks.append(combined_part)
        cursor_ms = max(cursor_ms, seg.end * 1000.0)
        total_parts += len(parts)
        total_synth_ms += seg_synth_ms
        total_align_ms += seg_align_ms
        print(
            f"[tts] seg={idx} parts={len(parts)} voice={voice} "
            f"text_chars={len(text_clean)} target_ms={target_ms:.0f} gap_ms={gap_ms:.0f} "
            f"synth_ms={seg_synth_ms:.1f} align_ms={seg_align_ms:.1f} total_ms={(time.perf_counter()-seg_start_t)*1000.0:.1f}"
        )

    if not audio_chunks:
        raise ValueError("No audio chunks synthesized.")
    if pad_to_ms is not None and pad_to_ms > cursor_ms:
        audio_chunks.append(AudioSegment.silent(duration=int(round(pad_to_ms - cursor_ms))).set_frame_rate(sample_rate))
    combined = sum(audio_chunks[1:], audio_chunks[0])
    print(
        f"[tts] summary: source_segments={len(segments)} synth_segments={len(synth_segments)} "
        f"parts={total_parts} synth_ms={total_synth_ms:.1f} align_ms={total_align_ms:.1f}"
    )
    if missing_tr:
        print(f"[warn] TTS: {missing_tr} segments had empty translation; used silence for those segments.")
    return combined


def synthesize_segments_coqui(
    segments: List[Segment],
    tts,
    work_dir: Path,
    sample_rate: int,
    speaker: Optional[str] = None,
    speaker_wav: Optional[str] = None,
    language: Optional[str] = None,
    split_len: int = 80,
    max_speed: float = 1.08,
    align_mode: str = "resample",
    pad_to_ms: Optional[float] = None,
) -> "AudioSegment":
    """
    使用 Coqui TTS（纯 Python，无外部 dylib 依赖）分段合成。
    """
    if AudioSegment is None:
        raise SystemExit("pydub is required for TTS post-processing. Please install pydub.")
    work_dir.mkdir(parents=True, exist_ok=True)
    synth_segments, merge_meta = _merge_short_tts_segments(segments)
    if merge_meta["merged_groups"] > 0:
        print(
            f"[tts] merge_short: source_segments={merge_meta['source_segments']} "
            f"synth_segments={merge_meta['synth_segments']} merged_groups={merge_meta['merged_groups']} "
            f"merged_source_segments={merge_meta['merged_source_segments']}"
        )
    else:
        print(f"[tts] merge_short: source_segments={len(segments)} synth_segments={len(synth_segments)} merged_groups=0")

    def _audiosegment_from_waveform(waveform, frame_rate: int) -> "AudioSegment":
        sr = max(1, int(frame_rate or sample_rate or 22050))
        if np is not None:
            arr = np.asarray(waveform, dtype=np.float32).reshape(-1)
            arr = np.clip(arr, -1.0, 1.0)
            pcm = (arr * 32767.0).astype(np.int16).tobytes()
        else:
            vals = [max(-32768, min(32767, int(float(x) * 32767.0))) for x in (waveform or [])]
            pcm = b"".join(int(v).to_bytes(2, byteorder="little", signed=True) for v in vals)
        return AudioSegment(data=pcm, sample_width=2, frame_rate=sr, channels=1)

    def _coqui_output_sample_rate() -> int:
        synth = getattr(tts, "synthesizer", None)
        sr = getattr(synth, "output_sample_rate", None)
        if sr:
            return int(sr)
        return int(sample_rate or 22050)

    def _resolve_speaker_wav() -> Optional[str]:
        raw = str(speaker_wav or getattr(tts, "_ygf_default_speaker_wav", "") or "").strip()
        return raw or None

    def _synthesize_part_audio(part_text: str) -> tuple["AudioSegment", bool]:
        synth = getattr(tts, "synthesizer", None)
        model = getattr(synth, "tts_model", None)
        is_xtts = bool(getattr(tts, "_ygf_is_xtts", False))
        ref = _resolve_speaker_wav()
        lang = str(language or "en").strip() or "en"
        if is_xtts and model is not None and hasattr(model, "get_conditioning_latents") and hasattr(model, "inference") and ref:
            cache = getattr(tts, "_ygf_xtts_conditioning_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                setattr(tts, "_ygf_xtts_conditioning_cache", cache)
            ref_key = str(Path(ref).resolve()) if Path(ref).exists() else ref
            cache_hit = ref_key in cache
            if not cache_hit:
                gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(audio_path=ref)
                cache[ref_key] = (gpt_cond_latent, speaker_embedding)
            else:
                gpt_cond_latent, speaker_embedding = cache[ref_key]
            out = model.inference(
                text=part_text,
                language=lang,
                gpt_cond_latent=gpt_cond_latent,
                speaker_embedding=speaker_embedding,
                speed=1.0,
                enable_text_splitting=False,
            )
            wav = out.get("wav") if isinstance(out, dict) else out
            return _audiosegment_from_waveform(wav, _coqui_output_sample_rate()), cache_hit
        # Fallback: use the generic API and keep everything in memory.
        wav = tts.tts(
            text=part_text,
            speaker=speaker,
            speaker_wav=ref,
            language=lang,
            speed=1.0,
            split_sentences=False,
        )
        return _audiosegment_from_waveform(wav, _coqui_output_sample_rate()), False

    audio_chunks: List[AudioSegment] = []
    cursor_ms: float = 0.0

    missing_tr = 0
    total_synth_ms = 0.0
    total_align_ms = 0.0
    total_parts = 0
    total_cache_hits = 0
    for idx, seg in enumerate(synth_segments, 1):
        seg_start_t = time.perf_counter()
        text_clean = _get_segment_tts_text(seg)
        target_ms = max((seg.end - seg.start) * 1000.0, 300.0)  # 最短 300ms，避免过短导致非自然
        gap_ms = max(seg.start * 1000.0 - cursor_ms, 0.0)
        if gap_ms >= 5.0:
            audio_chunks.append(AudioSegment.silent(duration=int(round(gap_ms))).set_frame_rate(sample_rate))
            cursor_ms += gap_ms
        if not text_clean:
            missing_tr += 1
            # Fully stripped (e.g. non-English). Produce silence instead of synthesizing junk.
            audio_chunks.append(AudioSegment.silent(duration=target_ms).set_frame_rate(sample_rate))
            cursor_ms = max(cursor_ms, seg.end * 1000.0)
            print(f"[tts] seg={idx} empty_text=true target_ms={target_ms:.0f} gap_ms={gap_ms:.0f}")
            continue

        # Long text may still be split for XTTS stability, but keep splitting conservative to reduce per-call overhead.
        parts = split_for_tts(text_clean, max_len=split_len)
        part_chunks: List[AudioSegment] = []
        seg_synth_ms = 0.0
        seg_align_ms = 0.0
        seg_cache_hits = 0
        for j, part in enumerate(parts):
            part_clean = part.strip()
            if not part_clean:
                part_chunks.append(AudioSegment.silent(duration=200).set_frame_rate(sample_rate))
                continue
            t_part = time.perf_counter()
            wav, cache_hit = _synthesize_part_audio(part_clean)
            seg_synth_ms += (time.perf_counter() - t_part) * 1000.0
            if cache_hit:
                seg_cache_hits += 1
            part_chunks.append(wav.set_frame_rate(sample_rate))
        if not part_chunks:
            raise ValueError("No audio chunks synthesized for segment.")
        combined_part = sum(part_chunks[1:], part_chunks[0])
        t_align = time.perf_counter()
        combined_part = stretch_or_pad(
            combined_part,
            target_ms=target_ms,
            allow_speed_change=True,
            max_speed=max_speed,
            align_mode=align_mode,
        )
        seg_align_ms += (time.perf_counter() - t_align) * 1000.0
        combined_part = combined_part.set_frame_rate(sample_rate)
        audio_chunks.append(combined_part)
        cursor_ms = max(cursor_ms, seg.end * 1000.0)
        total_parts += len(parts)
        total_cache_hits += seg_cache_hits
        total_synth_ms += seg_synth_ms
        total_align_ms += seg_align_ms
        print(
            f"[tts] seg={idx} parts={len(parts)} cache_hits={seg_cache_hits}/{len(parts)} "
            f"text_chars={len(text_clean)} target_ms={target_ms:.0f} gap_ms={gap_ms:.0f} "
            f"synth_ms={seg_synth_ms:.1f} align_ms={seg_align_ms:.1f} total_ms={(time.perf_counter()-seg_start_t)*1000.0:.1f}"
        )
    if not audio_chunks:
        raise ValueError("No audio chunks synthesized.")
    if pad_to_ms is not None and pad_to_ms > cursor_ms:
        audio_chunks.append(AudioSegment.silent(duration=int(round(pad_to_ms - cursor_ms))).set_frame_rate(sample_rate))
    combined = sum(audio_chunks[1:], audio_chunks[0])
    print(
        f"[tts] summary: source_segments={len(segments)} synth_segments={len(synth_segments)} "
        f"parts={total_parts} cache_hits={total_cache_hits} "
        f"synth_ms={total_synth_ms:.1f} align_ms={total_align_ms:.1f}"
    )
    return combined


def build_coqui_tts(model_name: str, device: str = "auto"):
    """构建 Coqui TTS 接口，按需启用 GPU。"""
    try:
        # In PyInstaller(onefile), Python sources are packed into an archive and TorchScript can fail
        # when it tries to inspect source code (inspect.getsourcelines) for scripting.
        # Disable JIT to avoid:
        #   OSError: TorchScript requires source access... make sure original .py files are available.
        os.environ.setdefault("PYTORCH_JIT", "0")
        os.environ.setdefault("TORCH_JIT", "0")
        try:
            import torch  # type: ignore

            try:
                torch.jit._state.disable()  # type: ignore[attr-defined]
            except Exception:
                pass

            # PyTorch 2.6+ changed torch.load default `weights_only=True`.
            # Coqui XTTS 0.22 still expects to unpickle trusted local checkpoint metadata
            # (for example `XttsConfig`) when loading from our packaged local model dir.
            # Force the legacy default for this process unless a caller explicitly overrides it.
            try:
                _orig_torch_load = getattr(torch, "load", None)
                if callable(_orig_torch_load) and not getattr(torch, "_ygf_torch_load_patched", False):
                    def _ygf_torch_load(*args, **kwargs):  # type: ignore[no-untyped-def]
                        if "weights_only" not in kwargs:
                            kwargs["weights_only"] = False
                        return _orig_torch_load(*args, **kwargs)

                    setattr(torch, "load", _ygf_torch_load)
                    setattr(torch, "_ygf_torch_load_patched", True)
            except Exception:
                pass
        except Exception:
            pass

        # In some PyInstaller(onefile) builds, `gruut` is present but its `VERSION` data file
        # is missing from the bundle, causing:
        #   FileNotFoundError: ...\\_MEIxxxx\\gruut\\VERSION
        # Create it proactively so Coqui TTS import can proceed.
        try:
            import sys

            mei = getattr(sys, "_MEIPASS", None)
            if mei:
                vp = Path(mei) / "gruut" / "VERSION"
                if not vp.exists():
                    vp.parent.mkdir(parents=True, exist_ok=True)
                    vp.write_text("0.0.0", encoding="utf-8")
        except Exception:
            pass

        # Coqui's text normalization stack pulls in `inflect`, which (newer versions) uses `typeguard`
        # decorators that instrument functions by calling `inspect.getsource()`. In PyInstaller(onefile),
        # source code may be unavailable, causing:
        #   OSError: could not get source code
        # Workaround: monkey-patch typeguard's decorator to a no-op before importing TTS/inflect.
        try:
            def _no_typechecked(*args, **kwargs):  # type: ignore[no-untyped-def]
                # supports both @typechecked and @typechecked(...)
                if args and callable(args[0]) and len(args) == 1 and not kwargs:
                    return args[0]

                def _deco(fn):  # type: ignore[no-untyped-def]
                    return fn

                return _deco

            try:
                import typeguard  # type: ignore

                setattr(typeguard, "typechecked", _no_typechecked)
            except Exception:
                pass
            try:
                import typeguard._decorators as _tg_decorators  # type: ignore

                setattr(_tg_decorators, "typechecked", _no_typechecked)
            except Exception:
                pass
        except Exception:
            pass

        from TTS.api import TTS as CoquiTTS  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime guard
        raise SystemExit("Coqui TTS not installed. Please `pip install TTS`.") from exc

    use_gpu = False
    if device == "auto":
        try:
            import torch  # type: ignore

            use_gpu = torch.cuda.is_available()
        except Exception:
            use_gpu = False
    elif device == "cuda":
        use_gpu = True
    # ---------------------------------------------------------
    # Offline-only / local-only policy:
    # We must NOT download models at runtime in quality mode.
    #
    # Canonical on-disk location (repo-relative, mounted into docker as /app/assets):
    #   assets/models/quality_tts_coqui/
    #     tts_models--en--ljspeech--tacotron2-DDC/
    #       config.json
    #       model_file.pth
    #     vocoder_models--en--ljspeech--hifigan_v2/
    #       config.json
    #       model_file.pth
    #
    # `model_name` keeps the familiar Coqui id (e.g. tts_models/en/ljspeech/tacotron2-DDC),
    # but we always resolve it to local files and load via (model_path/config_path/...),
    # which bypasses Coqui's downloader logic.
    # ---------------------------------------------------------
    repo_root = Path(__file__).resolve().parents[3]
    base = Path(os.environ.get("YGF_COQUI_TTS_DIR") or (repo_root / "assets" / "models" / "quality_tts_coqui"))
    if not base.is_absolute():
        base = (repo_root / base).resolve()

    # Normalize Coqui model id:
    # - user typically passes `tts_models/en/ljspeech/tacotron2-DDC`
    # - on disk we store `tts_models--en--ljspeech--tacotron2-DDC`
    mid = str(model_name).strip()
    if mid.startswith("tts_models--"):
        slug = mid
    else:
        if mid.startswith("tts_models/"):
            mid = mid[len("tts_models/") :]
        slug = "tts_models--" + mid.replace("/", "--")
    tts_dir = base / slug

    is_xtts = ("xtts" in slug.lower()) or ("multilingual--multi-dataset--xtts" in slug.lower())

    # Resolve model paths. XTTS-v2 uses different filenames and does not require an external vocoder directory.
    config_path = tts_dir / "config.json"
    model_path = tts_dir / ("model.pth" if (tts_dir / "model.pth").exists() else "model_file.pth")

    voc_dir = None
    vocoder_path = None
    vocoder_config_path = None
    if not is_xtts:
        # Pick vocoder directory:
        # - Prefer explicit env override
        # - Else if exactly one vocoder dir exists, use it
        # - Else default to ljspeech hifigan_v2 (matches our default coqui model)
        voc_env = os.environ.get("YGF_COQUI_VOCODER_DIR", "").strip()
        if voc_env:
            voc_dir = Path(voc_env)
            if not voc_dir.is_absolute():
                voc_dir = (repo_root / voc_dir).resolve()
        else:
            try:
                vocs = [p for p in base.iterdir() if p.is_dir() and p.name.startswith("vocoder_models--")]
            except Exception:
                vocs = []
            if len(vocs) == 1:
                voc_dir = vocs[0]
            else:
                voc_dir = base / "vocoder_models--en--ljspeech--hifigan_v2"
        vocoder_path = voc_dir / "model_file.pth"
        vocoder_config_path = voc_dir / "config.json"

    missing = []
    if not config_path.exists():
        missing.append(str(config_path))
    if not model_path.exists():
        missing.append(str(model_path))
    if (not is_xtts) and vocoder_path and vocoder_config_path:
        if not vocoder_path.exists():
            missing.append(str(vocoder_path))
        if not vocoder_config_path.exists():
            missing.append(str(vocoder_config_path))
    if missing:
        msg = (
            "Coqui TTS 仅允许使用本地模型（禁止运行时下载）。\n"
            f"- 期望 Coqui 模型目录: {tts_dir}\n"
            + (f"- 期望 Vocoder 目录: {voc_dir}\n" if voc_dir else "")
            + "- 缺少以下文件:\n  - "
            + "\n  - ".join(missing)
            + "\n\n"
            "请把模型文件放到 `assets/models/quality_tts_coqui/` 下（docker 中是 `/app/assets/models/quality_tts_coqui/`），"
            "并确保包含 `config.json` 与模型权重文件。"
        )
        raise SystemExit(msg)

    # IMPORTANT:
    # Passing (model_path/config_path/...) to TTS(...) can leave some model capability flags unset
    # in certain TTS versions (e.g. `is_multi_lingual`), which later crashes at inference time.
    #
    # So we keep Coqui's normal initialization path (model_name -> Synthesizer),
    # but we *replace* its download routine with a strict local resolver.
    _orig_download = getattr(CoquiTTS, "download_model_by_name", None)

    def _local_only_download_model_by_name(self, _model_name: str):  # type: ignore[no-untyped-def]
        # Return paths in the exact shape expected by `load_tts_model_by_name`.
        # XTTS:
        # - expects a checkpoint *directory* (it will resolve model.pth inside)
        # - does not require an external vocoder
        if is_xtts:
            return (str(tts_dir), str(config_path), None, None, None)
        return (str(model_path), str(config_path), str(vocoder_path), str(vocoder_config_path), None)

    try:
        setattr(CoquiTTS, "download_model_by_name", _local_only_download_model_by_name)
        # XTTS benefits from staying on the native codepath (model_name) so speaker/language capabilities
        # are detected correctly, but we still hard-block runtime downloads via the patch above.
        tts = CoquiTTS(model_name=model_name, progress_bar=False, gpu=use_gpu)
        if is_xtts:
            # XTTS requires a reference audio for conditioning; use a stable built-in sample by default.
            # Allow override via env (packaged app can point to a user-provided voice).
            env_ref = os.environ.get("YGF_XTTS_SPEAKER_WAV", "").strip()
            ref = None
            if env_ref:
                p = Path(env_ref)
                if not p.is_absolute():
                    p = (repo_root / p).resolve()
                if p.exists():
                    ref = str(p)
            if not ref:
                cand = tts_dir / "samples" / "en_sample.wav"
                if cand.exists():
                    ref = str(cand)
                else:
                    # best-effort: pick any wav under samples/
                    try:
                        wavs = sorted((tts_dir / "samples").glob("*.wav"))
                        if wavs:
                            ref = str(wavs[0])
                    except Exception:
                        ref = None
            try:
                setattr(tts, "_ygf_is_xtts", True)
                setattr(tts, "_ygf_default_speaker_wav", ref)
            except Exception:
                pass
        return tts
    finally:
        # Restore to avoid surprising other code paths.
        if _orig_download is not None:
            try:
                setattr(CoquiTTS, "download_model_by_name", _orig_download)
            except Exception:
                pass

