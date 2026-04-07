#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.lib.asr.lite_asr import extract_audio
from pipelines.quality_pipeline_impl import AudioSegment, run_sensevoice_asr, run_whisperx


def _audio_duration_s(audio_path: Path) -> float:
    if AudioSegment is None:
        return 0.0
    try:
        return float(len(AudioSegment.from_file(audio_path))) / 1000.0
    except Exception:
        return 0.0


def _segments_preview(segments: List[Any], limit: int = 12) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments[: max(1, int(limit))]):
        out.append(
            {
                "idx": idx,
                "start": round(float(getattr(seg, "start", 0.0)), 3),
                "end": round(float(getattr(seg, "end", 0.0)), 3),
                "text": str(getattr(seg, "text", "") or "").strip(),
            }
        )
    return out


def _run_profile(audio_path: Path, audio_total_s: float, profile: Dict[str, Any]) -> Dict[str, Any]:
    started = time.time()
    if profile["engine"] == "sensevoice":
        segments = run_sensevoice_asr(
            audio_path=audio_path,
            model_id=str(profile["model"]),
            device=str(profile.get("device", "auto")),
            model_dir=Path(str(profile["model_dir"])),
            audio_total_s=audio_total_s or None,
        )
    else:
        segments = run_whisperx(
            audio_path=audio_path,
            model_id=str(profile["model"]),
            device=str(profile.get("device", "auto")),
            model_dir=Path(str(profile["model_dir"])),
            diarization=False,
            align_enable=False,
            audio_total_s=audio_total_s or None,
        )
    elapsed = time.time() - started
    return {
        "profile": profile["id"],
        "engine": profile["engine"],
        "model": profile["model"],
        "elapsed_s": round(elapsed, 3),
        "audio_total_s": round(audio_total_s, 3),
        "rtf": round((elapsed / audio_total_s), 4) if audio_total_s > 0 else None,
        "segments_n": len(segments),
        "preview": _segments_preview(segments, limit=int(profile.get("preview_limit", 12))),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Compare ASR experiment profiles on one noisy source video.")
    p.add_argument("--video", type=Path, required=True, help="Source video path")
    p.add_argument("--output-dir", type=Path, required=True, help="Directory to store compare outputs")
    p.add_argument("--sample-rate", type=int, default=16000, help="PCM extract sample rate")
    p.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto", help="ASR device")
    p.add_argument("--models-root", type=Path, default=Path.home() / "Library/Application Support/dubbing-gui/models", help="Root models directory")
    p.add_argument("--preview-limit", type=int, default=12, help="How many preview segments to save per profile")
    args = p.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = args.output_dir / "compare_audio.wav"
    print(f"[compare] extract audio -> {audio_path}")
    extract_audio(args.video, audio_path, sample_rate=args.sample_rate, denoise=False, denoise_model=None)
    audio_total_s = _audio_duration_s(audio_path)
    print(f"[compare] audio_total_s={audio_total_s:.2f}")

    whisperx_dir = args.models_root / "quality_asr_whisperx"
    common_cache_dir = args.models_root / "common_cache_hf"
    profiles = [
        {
            "id": "large-v3-turbo",
            "engine": "faster-whisper",
            "model": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            "model_dir": whisperx_dir,
            "device": args.device,
            "preview_limit": args.preview_limit,
        },
        {
            "id": "sensevoice-small",
            "engine": "sensevoice",
            "model": "FunAudioLLM/SenseVoiceSmall",
            "model_dir": common_cache_dir,
            "device": args.device,
            "preview_limit": args.preview_limit,
        },
    ]

    summary: Dict[str, Any] = {
        "video": str(args.video),
        "audio_path": str(audio_path),
        "audio_total_s": round(audio_total_s, 3),
        "profiles": [],
    }
    for profile in profiles:
        print(f"[compare] running profile={profile['id']} engine={profile['engine']} model={profile['model']}")
        try:
            result = _run_profile(audio_path, audio_total_s, profile)
            result["ok"] = True
        except Exception as exc:
            result = {
                "profile": profile["id"],
                "engine": profile["engine"],
                "model": profile["model"],
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        summary["profiles"].append(result)

    out_json = args.output_dir / "asr_compare_summary.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[compare] wrote {out_json}")


if __name__ == "__main__":
    main()
