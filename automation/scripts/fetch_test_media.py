#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Default to a widely accessible mirror (avoids 403 on some direct CDN links).
# Video: "Sintel" sample (Blender Foundation; commonly CC-BY) hosted by Google sample bucket.
DEFAULT_SOURCE_URL = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4"

# Mandarin (中文) speech audio (clear modern Mandarin reading; LibriVox / Internet Archive).
# Chosen because it's easier for native speakers to understand than some historical recordings.
DEFAULT_AUDIO_URL = "https://www.archive.org/download/multilingual_short_stories_0906_librivox/chinese_congcong_zhu_sn_64kb.mp3"


def _now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _run(cmd: List[str], timeout_s: int = 1800) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout_s)
    if proc.returncode != 0:
        out = (proc.stdout or "").strip()
        raise RuntimeError(f"command failed (exit={proc.returncode}): {' '.join(cmd)}\n{out[-4000:]}")


def _download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            # Some hosts block default urllib user-agent.
            "User-Agent": "Mozilla/5.0 (compatible; YGF-TestMediaFetcher/1.0)",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    out.write_bytes(data)


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


@dataclass
class ClipSpec:
    name: str
    start_s: float
    dur_s: float
    extra_vf: Optional[str] = None
    extra_af: Optional[str] = None
    extra_filter_complex: Optional[str] = None


def main() -> int:
    ap = argparse.ArgumentParser(description="Download and generate 20s~1min test media clips (not committed to git).")
    ap.add_argument("--url", default=DEFAULT_SOURCE_URL, help=f"Source mp4 URL (default: {DEFAULT_SOURCE_URL})")
    ap.add_argument("--audio-url", default=DEFAULT_AUDIO_URL, help="Mandarin speech audio URL (default: LibriVox/Internet Archive)")
    ap.add_argument("--audio-start-s", type=float, default=30.0, help="Audio start offset seconds (default: 30; use正文段落)")
    ap.add_argument("--out-dir", default="test_media", help="Output directory (default: test_media)")
    ap.add_argument("--duration", type=float, default=45.0, help="Base clip duration seconds (default: 45)")
    ap.add_argument("--short-duration", type=float, default=20.0, help="Short clip duration seconds (default: 20)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing S0~S5 clips (default: skip existing)")
    ap.add_argument("--keep-source", action="store_true", help="Keep downloaded source files (default: delete after clips generated)")
    ap.add_argument("--download-only", action="store_true", help="Only download source file, do not generate clips")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()

    source_path = out_dir / f"_source_{stamp}.mp4"
    if not source_path.exists():
        print(f"[download] {args.url} -> {source_path}")
        _download(args.url, source_path)
    else:
        print(f"[download] exists: {source_path}")

    meta: Dict[str, Any] = {
        "time": stamp,
        "source_url": args.url,
        "source_file": str(source_path),
        "audio_url": args.audio_url,
        "audio_start_s": float(args.audio_start_s),
        "clips": [],
        "license_hint": "Blender Foundation videos are typically CC-BY (check URL page/source).",
    }
    (out_dir / "_sources.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    audio_suffix = Path(urllib.parse.urlparse(str(args.audio_url)).path).suffix
    if not audio_suffix or len(audio_suffix) > 8:
        audio_suffix = ".audio"
    audio_path = out_dir / f"_zh_audio_{stamp}{audio_suffix}"
    if not audio_path.exists():
        print(f"[download] {args.audio_url} -> {audio_path}")
        _download(args.audio_url, audio_path)
    else:
        print(f"[download] exists: {audio_path}")

    if args.download_only:
        print("[done] download-only")
        return 0

    ffmpeg = _ffmpeg()

    base_dur = float(args.duration)
    base_dur = max(20.0, min(base_dur, 60.0))
    short_dur = float(args.short_duration)
    short_dur = max(12.0, min(short_dur, base_dur))

    clips: List[ClipSpec] = [
        ClipSpec(name="S1_clean_20s.mp4", start_s=10.0, dur_s=short_dur),
        ClipSpec(name="S1_clean.mp4", start_s=10.0, dur_s=base_dur),
        # Burn a bottom box + text to simulate hard subtitles (for erase/placement UX).
        ClipSpec(
            name="S2_hardsub.mp4",
            start_s=10.0,
            dur_s=base_dur,
            extra_vf=(
                # Use box-only (no drawtext) to avoid missing CJK fonts on some machines.
                "drawbox=x=0:y=ih*0.78:w=iw:h=ih*0.22:color=black@0.6:t=fill"
            ),
        ),
        # Long/fast speech pressure surrogate: remove silences then speed up speech,
        # keeping the same video duration.
        ClipSpec(
            name="S3_fast.mp4",
            start_s=10.0,
            dur_s=base_dur,
            extra_filter_complex=(
                "[0:v]null[v];"
                # Keep it simple and player-friendly: single-chain processing (no concat/apad).
                f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono,atrim=0:{base_dur},asetpts=PTS-STARTPTS,"
                # remove long pauses to create more continuous speech
                "silenceremove=start_periods=1:start_duration=0.12:start_threshold=-45dB:"
                "stop_periods=-1:stop_duration=0.20:stop_threshold=-45dB,"
                # speed up speech to create pressure
                "atempo=1.30,"
                "alimiter=limit=0.95,"
                # ensure duration stays exactly base_dur so mux won't truncate early
                f"apad=pad_dur={base_dur},atrim=0:{base_dur},asetpts=PTS-STARTPTS[a]"
            ),
        ),
        # Pseudo multi-speaker dialog: alternate pitch to simulate different speakers.
        ClipSpec(
            name="S4_dialog.mp4",
            start_s=40.0,
            dur_s=base_dur,
            extra_filter_complex=(
                "[0:v]null[v];"
                # Player-friendly "two speakers": split audio into 2 tracks with different pitch/tempo,
                # delay one track a bit, then mix.
                f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono,atrim=0:{base_dur},asetpts=PTS-STARTPTS[a0];"
                "[a0]asplit=2[aL][aR];"
                # Speaker A: slightly lower pitch (via asetrate+atempo)
                "[aL]asetrate=48000*0.90,atempo=1/0.90,volume=0.95[a1];"
                # Speaker B: slightly higher pitch + delayed responses
                "[aR]asetrate=48000*1.10,atempo=1/1.10,adelay=350|350,volume=0.90[a2];"
                "[a1][a2]amix=inputs=2:weights=1 0.85:normalize=0,alimiter=limit=0.95,"
                f"apad=pad_dur={base_dur},atrim=0:{base_dur},asetpts=PTS-STARTPTS[a]"
            ),
        ),
        # Add heavy noise (robustness/gating).
        ClipSpec(
            name="S5_noise.mp4",
            start_s=10.0,
            dur_s=base_dur,
            extra_filter_complex=(
                f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono,atrim=0:{base_dur},asetpts=PTS-STARTPTS[a0];"
                f"anoisesrc=color=white:amplitude=0.12:duration={base_dur}:sample_rate=48000[n];"
                # Make noise clearly audible while keeping speech present.
                # Add limiter to avoid clipping that may break some players.
                "[a0][n]amix=inputs=2:weights=1 1.2:normalize=0[a1];"
                "[a1]alimiter=limit=0.95,atrim=0:{dur},asetpts=PTS-STARTPTS[a]".format(dur=base_dur)
            ),
        ),
        # Optional silent clip (valid video, silent audio).
        ClipSpec(
            name="S0_silent.mp4",
            start_s=0.0,
            dur_s=base_dur,
            extra_filter_complex="[0:v]null[v];anullsrc=r=48000:cl=mono,atrim=duration=%s,asetpts=PTS-STARTPTS[a]" % (base_dur,),
        ),
    ]

    for spec in clips:
        out_path = out_dir / spec.name
        if out_path.exists() and not args.force:
            print(f"[skip] exists: {out_path}")
            continue
        print(f"[gen] {spec.name}")
        cmd: List[str] = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(spec.start_s),
            "-t",
            str(spec.dur_s),
            "-i",
            str(source_path),
        ]
        # Loop Mandarin speech audio to cover duration, then trim. Start from正文段落.
        audio_start_s = float(args.audio_start_s or 0.0)
        audio_start_s = max(0.0, audio_start_s)
        cmd += ["-ss", str(audio_start_s), "-stream_loop", "-1", "-i", str(audio_path)]
        if spec.extra_filter_complex:
            # Caller-provided filter_complex should output [v] and [a] when it touches audio.
            # Always trim to duration and normalize to mono/16k.
            fc = spec.extra_filter_complex
            if "[a]" not in fc:
                # keep Mandarin audio as [a] if not overridden
                fc = f"{fc};[1:a]atrim=0:{spec.dur_s},asetpts=PTS-STARTPTS[a]"
            cmd += ["-filter_complex", fc]
            # map
            if "[v]" in spec.extra_filter_complex:
                cmd += ["-map", "[v]"]
            else:
                cmd += ["-map", "0:v:0"]
            if "[a]" in spec.extra_filter_complex:
                cmd += ["-map", "[a]"]
            else:
                cmd += ["-map", "[a]"]
        else:
            # Default: replace audio with Mandarin speech and keep optional vf/af.
            vf = spec.extra_vf
            af = spec.extra_af
            if vf and af:
                cmd += ["-vf", vf, "-af", af]
            elif vf:
                cmd += ["-vf", vf]
            elif af:
                cmd += ["-af", af]
            # Use Mandarin audio for all clips (trim to duration).
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]
            cmd += ["-af", f"atrim=0:{spec.dur_s},asetpts=PTS-STARTPTS"]
        # keep files small + stable for cross-platform playback
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            "1",
            "-ar",
            # QuickTime on macOS can be picky with uncommon AAC sample rates (e.g. 16k).
            # Use 48k for maximum player compatibility while keeping mono.
            "48000",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(out_path),
        ]
        _run(cmd, timeout_s=1800)
        meta["clips"].append(
            {
                "name": spec.name,
                "start_s": spec.start_s,
                "dur_s": spec.dur_s,
                "vf": spec.extra_vf,
                "af": spec.extra_af,
                "filter_complex": spec.extra_filter_complex,
            }
        )
        (out_dir / "_sources.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[done] clips in {out_dir}")
    if not args.keep_source:
        try:
            source_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass
        try:
            audio_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

