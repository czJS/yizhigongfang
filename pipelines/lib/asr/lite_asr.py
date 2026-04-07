from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from pipelines.lib.utils.exec_utils import run_cmd, run_cmd_with_heartbeat


@dataclass
class Segment:
    # 单条语音片段的时间戳与文本（text 为中文，translation 为英文）
    start: float
    end: float
    text: str
    translation: Optional[str] = None


def format_srt_time(seconds: float) -> str:
    ms_total = int(round(seconds * 1000))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1_000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def write_srt(path: Path, segments: List[Segment], text_attr: str = "text") -> None:
    # 根据 Segment 列表输出标准 SRT 文件，可选写入中文或英文字段
    lines = []
    for idx, seg in enumerate(segments, 1):
        start = format_srt_time(seg.start)
        end = format_srt_time(seg.end)
        text = getattr(seg, text_attr, "").strip()
        lines.append(f"{idx}\n{start} --> {end}\n{text}\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def enforce_min_duration(
    segments: List[Segment],
    min_duration: float = 1.5,
    safety_gap: float = 0.2,
) -> List[Segment]:
    """
    Ensure each segment has at least min_duration seconds by extending the end time
    into available gaps (without overlapping the next segment).
    """
    if not segments:
        return segments
    adjusted: List[Segment] = []
    for i, seg in enumerate(segments):
        start = seg.start
        end = seg.end
        duration = end - start
        # compute how much headroom we have until next segment starts
        if i < len(segments) - 1:
            next_start = segments[i + 1].start
            headroom = max(0.0, next_start - safety_gap - end)
        else:
            headroom = min_duration  # last segment can extend freely
        if duration < min_duration:
            need = min_duration - duration
            extend_by = min(need, headroom)
            end = end + extend_by
        adjusted.append(Segment(start=start, end=end, text=seg.text, translation=seg.translation))
    return adjusted


def extract_audio(
    video_path: Path,
    audio_path: Path,
    sample_rate: int = 16000,
    denoise: bool = False,
    denoise_model: Optional[Path] = None,
) -> None:
    # 统一转单声道、16k 采样，提升 ASR 稳定性；可选简单去噪
    if not video_path.exists():
        raise FileNotFoundError(f"Input video not found: {video_path}")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
    ]
    if denoise:
        # ffmpeg arnndn requires an explicit model file; otherwise it fails with
        # "Error initializing filters". If no model is provided, fall back to
        # built-in spectral denoiser (anlmdn) to keep the pipeline working offline.
        if denoise_model:
            cmd.extend(["-af", f"arnndn=m={denoise_model}"])
        else:
            cmd.extend(["-af", "anlmdn"])
    cmd.append(str(audio_path))
    run_cmd(cmd)


def preprocess_audio_for_asr(
    input_audio: Path,
    output_audio: Path,
    *,
    sample_rate: int = 16000,
    loudnorm: bool = False,
    highpass_hz: Optional[int] = None,
    lowpass_hz: Optional[int] = None,
    ffmpeg_extra: str = "",
) -> None:
    """
    Lightweight generic ASR preprocess.
    Goal: improve robustness on weak/noisy inputs without introducing model-specific tuning.
    """
    if not input_audio.exists():
        raise FileNotFoundError(f"ASR preprocess input not found: {input_audio}")
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    filters: List[str] = []
    if loudnorm:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if highpass_hz and int(highpass_hz) > 0:
        filters.append(f"highpass=f={int(highpass_hz)}")
    if lowpass_hz and int(lowpass_hz) > 0:
        filters.append(f"lowpass=f={int(lowpass_hz)}")
    extra = str(ffmpeg_extra or "").strip()
    if extra:
        filters.append(extra)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_audio),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
    ]
    if filters:
        cmd.extend(["-af", ",".join(filters)])
    cmd.append(str(output_audio))
    run_cmd(cmd)


def merge_short_asr_segments(
    segments: List[Segment],
    *,
    min_duration_s: float = 0.8,
    min_chars: int = 6,
    max_gap_s: float = 0.25,
    max_group_chars: int = 120,
) -> tuple[List[Segment], dict]:
    """
    Merge extremely short adjacent ASR segments to reduce short-window guessing and
    unstable translation caused by over-fragmented subtitles.
    """
    if not segments:
        return [], {
            "source_segments": 0,
            "merged_segments": 0,
            "merged_groups": 0,
        }

    def text_len(seg: Segment) -> int:
        return len(re.sub(r"\s+", "", str(seg.text or "")))

    def should_merge(cur_group: List[Segment], nxt: Segment) -> bool:
        if not cur_group:
            return False
        cur = cur_group[-1]
        gap = max(0.0, float(nxt.start) - float(cur.end))
        if gap > max_gap_s:
            return False
        total_chars = sum(text_len(s) for s in cur_group) + text_len(nxt)
        if total_chars > max_group_chars:
            return False
        group_dur = float(cur_group[-1].end) - float(cur_group[0].start)
        group_chars = sum(text_len(s) for s in cur_group)
        cur_is_short = group_dur < min_duration_s or group_chars < min_chars
        nxt_is_short = (float(nxt.end) - float(nxt.start)) < min_duration_s or text_len(nxt) < min_chars
        return cur_is_short or nxt_is_short

    out: List[Segment] = []
    merged_groups = 0
    i = 0
    while i < len(segments):
        group = [segments[i]]
        j = i + 1
        while j < len(segments) and should_merge(group, segments[j]):
            group.append(segments[j])
            j += 1
        if len(group) == 1:
            out.append(group[0])
        else:
            merged_groups += 1
            merged_text = "".join(str(seg.text or "").strip() for seg in group)
            out.append(
                Segment(
                    start=float(group[0].start),
                    end=float(group[-1].end),
                    text=merged_text,
                    translation=group[0].translation,
                )
            )
        i = j

    return out, {
        "source_segments": len(segments),
        "merged_segments": len(out),
        "merged_groups": merged_groups,
    }


def run_asr_whispercpp(
    audio_path: Path,
    whisper_bin: Path,
    model_path: Path,
    output_prefix: Path,
    language: str = "zh",
    threads: Optional[int] = None,
    beam_size: Optional[int] = None,
    vad_enable: bool = False,
    vad_model: Optional[Path] = None,
    vad_thold: Optional[float] = None,
    vad_min_sil_ms: Optional[int] = None,
) -> List[Segment]:
    """
    调用 whisper.cpp CLI，输出 JSON，再解析为 Segment 列表。
    需要 whisper.cpp 可执行文件（通常名为 main 或 whisper.cpp）和 ggml 模型。
    """
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(whisper_bin),
        "-m",
        str(model_path),
        "-f",
        str(audio_path),
        "-l",
        language,
        "-otxt",
        "-oj",
        "-of",
        str(output_prefix),
    ]
    if threads:
        cmd.extend(["-t", str(threads)])
    if beam_size:
        cmd.extend(["-bs", str(beam_size)])
    # whisper.cpp VAD requires an explicit VAD model path (--vad-model). If not provided,
    # we disable VAD to keep the lite pipeline robust/offline-friendly.
    if vad_enable and not vad_model:
        print("[warn] VAD enabled but --vad-model not provided; disabling VAD for whisper.cpp.")
        vad_enable = False
    if vad_enable:
        cmd.append("--vad")
        cmd.extend(["--vad-model", str(vad_model)])
        if vad_thold is not None:
            cmd.extend(["--vad-threshold", str(vad_thold)])
        if vad_min_sil_ms is not None:
            cmd.extend(["--vad-min-silence-duration-ms", str(vad_min_sil_ms)])
    # whisper.cpp can run for minutes with no stdout; emit a heartbeat so users
    # don't think the pipeline is stuck at "[2/7] Running ASR...".
    run_cmd_with_heartbeat(cmd, heartbeat_s=15.0, label="asr")

    json_path = output_prefix.with_suffix(".json")
    if not json_path.exists():
        raise FileNotFoundError(f"Whisper.cpp JSON not found: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments: List[Segment] = []

    # whisper.cpp JSON may emit either "segments" or "transcription" with timestamps strings.
    raw_segments = data.get("segments") or data.get("transcription") or []
    for seg in raw_segments:
        if "start" in seg and "end" in seg:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        else:
            # parse "00:00:04,000" style timestamps if present
            ts = seg.get("timestamps", {})

            def _parse_ts(val: str) -> float:
                if not val:
                    return 0.0
                hms, ms = val.split(",")
                hh, mm, ss = hms.split(":")
                return int(hh) * 3600 + int(mm) * 60 + float(ss) + int(ms) / 1000.0

            start = _parse_ts(ts.get("from"))
            end = _parse_ts(ts.get("to"))
        segments.append(
            Segment(
                start=start,
                end=end,
                text=str(seg.get("text", "")).strip(),
            )
        )
    return segments

