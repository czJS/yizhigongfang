#!/usr/bin/env python3
"""
Online pipeline: uses remote ASR / MT / TTS services (OpenAI-like endpoints).
Requirements: remote endpoints must support:
  - ASR: POST file -> JSON {"segments": [{"start": float,"end": float,"text": str}, ...]}
  - MT:  POST /v1/chat/completions (OpenAI schema) -> English translations
  - TTS: POST json {"text": "...", "voice": "..."} -> audio/wav bytes
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import requests

from scripts import asr_translate_tts as lite


@dataclass
class Segment:
    start: float
    end: float
    text: str
    translation: Optional[str] = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Online pipeline using remote ASR/MT/TTS")
    p.add_argument("--video", type=Path, required=True, help="Input video file")
    p.add_argument("--output-dir", type=Path, default=Path("outputs"), help="Directory for outputs")
    p.add_argument("--sample-rate", type=int, default=16000, help="Audio sample rate")
    p.add_argument("--min-sub-dur", type=float, default=1.5, help="Minimum subtitle duration (seconds)")
    p.add_argument("--tts-split-len", type=int, default=80, help="Max characters per TTS chunk")
    p.add_argument("--tts-speed-max", type=float, default=1.1, help="Max speed factor")
    # burned-in subtitle erase (optional)
    p.add_argument("--erase-subtitle-enable", action="store_true", help="Enable burned-in subtitle erase/obscure on source video frames")
    p.add_argument("--erase-subtitle-method", default="delogo", help="Erase method (currently delogo)")
    p.add_argument("--erase-subtitle-coord-mode", default="ratio", choices=["ratio", "px"], help="Coordinate mode for erase region")
    p.add_argument("--erase-subtitle-x", type=float, default=0.0, help="Erase region X (ratio or px)")
    p.add_argument("--erase-subtitle-y", type=float, default=0.78, help="Erase region Y (ratio or px)")
    p.add_argument("--erase-subtitle-w", type=float, default=1.0, help="Erase region width (ratio or px)")
    p.add_argument("--erase-subtitle-h", type=float, default=0.22, help="Erase region height (ratio or px)")
    p.add_argument("--erase-subtitle-blur-radius", type=int, default=12, help="Aggressiveness (mapped to delogo band)")
    # Remote endpoints
    p.add_argument("--asr-endpoint", required=True, help="ASR endpoint URL")
    p.add_argument("--asr-api-key", default="", help="ASR API key")
    p.add_argument("--mt-endpoint", required=True, help="MT endpoint URL (OpenAI chat/completions)")
    p.add_argument("--mt-model", default="gpt-4o-mini", help="MT model")
    p.add_argument("--mt-api-key", default="", help="MT API key")
    p.add_argument("--tts-endpoint", required=True, help="TTS endpoint URL")
    p.add_argument("--tts-voice", default="en-US-amy", help="TTS voice id")
    p.add_argument("--tts-api-key", default="", help="TTS API key")
    p.add_argument("--mode", default="online", help="Mode flag (online)")
    p.add_argument("--skip-tts", action="store_true", help="Skip TTS")
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
    return p.parse_args()


def call_asr(endpoint: str, api_key: str, audio_path: Path) -> List[Segment]:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    files = {"file": audio_path.open("rb")}
    resp = requests.post(endpoint, files=files, headers=headers, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"ASR failed: {resp.status_code} {resp.text}")
    data = resp.json()
    segs = []
    for item in data.get("segments", []):
        segs.append(Segment(start=float(item["start"]), end=float(item["end"]), text=str(item["text"])))
    return segs


def translate_segments_remote(
    segments: List[Segment],
    endpoint: str,
    model: str,
    api_key: str,
    chunk_size: int = 5,
) -> List[Segment]:
    if not segments:
        return segments
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i : i + n]

    out: List[Segment] = []
    for group in chunks(segments, chunk_size):
        src_lines = [s.text.strip() for s in group]
        prompt = (
            "You are a professional translator. Translate the following Chinese lines to concise, natural English. "
            "Return one line per input line, keep order, no commentary.\n"
            + "\n".join(f"{i+1}. {line}" for i, line in enumerate(src_lines))
        )
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Translate Chinese to English, concise, natural, keep meaning."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }
        resp = requests.post(endpoint, json=body, headers=headers, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"MT failed: {resp.status_code} {resp.text}")
        content = resp.json()["choices"][0]["message"]["content"]
        lines = [line.strip(" \n\r-") for line in content.split("\n") if line.strip()]
        cleaned = []
        for line in lines:
            cleaned.append(line.split(".", 1)[1].strip() if line[:2].isdigit() and "." in line else line)
        while len(cleaned) < len(group):
            cleaned.append("")
        cleaned = cleaned[: len(group)]
        for seg, tr in zip(group, cleaned):
            out.append(Segment(start=seg.start, end=seg.end, text=seg.text, translation=tr))
    return out


def tts_remote(text: str, endpoint: str, voice: str, api_key: str, out_path: Path) -> None:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"text": text, "voice": voice}
    resp = requests.post(endpoint, json=payload, headers=headers, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"TTS failed: {resp.status_code} {resp.text}")
    content_type = resp.headers.get("Content-Type", "")
    data = resp.content
    if "application/json" in content_type:
        # expect base64 in JSON
        obj = resp.json()
        audio_b64 = obj.get("audio") or obj.get("data")
        if not audio_b64:
            raise RuntimeError("TTS JSON missing audio field")
        data = base64.b64decode(audio_b64)
    out_path.write_bytes(data)


def main() -> None:
    args = parse_args()

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    work_tts = output_dir / "tts_segments"
    work_asr_prefix = output_dir / "asr_remote"

    audio_pcm = output_dir / "audio.wav"
    audio_json = output_dir / "audio.json"
    chs_srt = output_dir / "chs.srt"
    eng_srt = output_dir / "eng.srt"
    bi_srt = output_dir / "bilingual.srt"
    tts_wav = output_dir / "tts_full.wav"
    video_dub = output_dir / "output_en.mp4"
    video_sub = output_dir / "output_en_sub.mp4"

    print("[1/7] Extracting audio...")
    lite.extract_audio(
        args.video,
        audio_pcm,
        sample_rate=args.sample_rate,
        denoise=False,
        denoise_model=None,
    )

    print("[2/7] Running ASR (remote)...")
    segments = call_asr(args.asr_endpoint, args.asr_api_key, audio_pcm)
    segments = lite.enforce_min_duration(segments, min_duration=args.min_sub_dur)
    audio_json.write_text(json.dumps([seg.__dict__ for seg in segments], ensure_ascii=False, indent=2), encoding="utf-8")
    lite.write_srt(chs_srt, segments, text_attr="text")

    print("[3/7] Translating (remote LLM)...")
    seg_en = translate_segments_remote(
        segments,
        endpoint=args.mt_endpoint,
        model=args.mt_model,
        api_key=args.mt_api_key,
        chunk_size=5,
    )
    lite.write_srt(eng_srt, seg_en, text_attr="translation")
    if True:
        bilingual_segments = []
        for seg in seg_en:
            bilingual_text = f"{seg.text}\n{seg.translation}"
            bilingual_segments.append(Segment(start=seg.start, end=seg.end, text=bilingual_text, translation=seg.translation))
        lite.write_srt(bi_srt, bilingual_segments, text_attr="text")

    if args.skip_tts:
        print("Skip TTS enabled; generated subtitles only.")
        return

    print("[5/7] Synthesizing TTS (remote)...")
    work_tts.mkdir(parents=True, exist_ok=True)
    audio_chunks = []
    for idx, seg in enumerate(seg_en, 1):
        if not seg.translation:
            raise RuntimeError("Missing translation for TTS.")
        seg.translation = lite.clean_tts_text(seg.translation)
        parts = lite.split_for_tts(seg.translation, max_len=args.tts_split_len)
        total_len = sum(len(p) for p in parts) or 1
        part_chunks = []
        target_ms = max((seg.end - seg.start) * 1000.0, 300.0)
        for j, part in enumerate(parts):
            part_ms = max(target_ms * len(part) / total_len, 200.0)
            seg_wav = work_tts / f"seg_{idx:04d}_p{j}.wav"
            tts_remote(part, args.tts_endpoint, args.tts_voice, args.tts_api_key, seg_wav)
            wav = lite.AudioSegment.from_file(seg_wav)
            wav_aligned = lite.stretch_or_pad(wav, target_ms=part_ms, allow_speed_change=True, max_speed=args.tts_speed_max)
            part_chunks.append(wav_aligned)
        combined_part = sum(part_chunks[1:], part_chunks[0]) if part_chunks else None
        if combined_part is None:
            raise RuntimeError("No audio generated for segment.")
        combined_part = lite.stretch_or_pad(combined_part, target_ms=target_ms, allow_speed_change=True, max_speed=args.tts_speed_max)
        audio_chunks.append(combined_part)
    if not audio_chunks:
        raise RuntimeError("No audio chunks synthesized.")
    combined = sum(audio_chunks[1:], audio_chunks[0])
    lite.save_audio(combined, tts_wav, sample_rate=args.sample_rate)

    print("[6/7] Muxing video with new audio...")
    lite.mux_video_audio(
        args.video,
        tts_wav,
        video_dub,
        erase_subtitle_enable=bool(getattr(args, "erase_subtitle_enable", False)),
        erase_subtitle_method=str(getattr(args, "erase_subtitle_method", "delogo") or "delogo"),
        erase_subtitle_coord_mode=str(getattr(args, "erase_subtitle_coord_mode", "ratio") or "ratio"),
        erase_subtitle_x=float(getattr(args, "erase_subtitle_x", 0.0) or 0.0),
        erase_subtitle_y=float(getattr(args, "erase_subtitle_y", 0.78) or 0.78),
        erase_subtitle_w=float(getattr(args, "erase_subtitle_w", 1.0) or 1.0),
        erase_subtitle_h=float(getattr(args, "erase_subtitle_h", 0.22) or 0.22),
        erase_subtitle_blur_radius=int(getattr(args, "erase_subtitle_blur_radius", 12) or 12),
    )

    print("[7/7] Embedding subtitles...")
    lite.burn_subtitles(
        video_dub,
        eng_srt,
        video_sub,
        font_name=str(getattr(args, "sub_font_name", "Arial") or "Arial"),
        font_size=int(getattr(args, "sub_font_size", 18) or 18),
        outline=int(getattr(args, "sub_outline", 1) or 1),
        shadow=int(getattr(args, "sub_shadow", 0) or 0),
        margin_v=int(getattr(args, "sub_margin_v", 24) or 24),
        alignment=int(getattr(args, "sub_alignment", 2) or 2),
        place_enable=bool(getattr(args, "sub_place_enable", False)),
        place_coord_mode=str(getattr(args, "sub_place_coord_mode", "ratio") or "ratio"),
        place_x=float(getattr(args, "sub_place_x", 0.0) or 0.0),
        place_y=float(getattr(args, "sub_place_y", 0.78) or 0.78),
        place_w=float(getattr(args, "sub_place_w", 1.0) or 1.0),
        place_h=float(getattr(args, "sub_place_h", 0.22) or 0.22),
    )

    print("Done.")
    print(f"Outputs in: {output_dir}")
    print(f"- ASR JSON:   {audio_json}")
    print(f"- CHS SRT:    {chs_srt}")
    print(f"- ENG SRT:    {eng_srt}")
    print(f"- BI  SRT:    {bi_srt}")
    print(f"- TTS audio:  {tts_wav}")
    print(f"- Video dub:  {video_dub}")
    print(f"- Video+sub:  {video_sub}")


if __name__ == "__main__":
    main()

