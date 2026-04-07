from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

VALID_LITE_RESUME_STAGES: Sequence[str] = ("asr", "mt", "tts", "mux")

_REQUIRED_LITE_RESUME_ARTIFACTS = {
    "asr": [],
    "mt": ["audio.wav", "audio.json"],
    "tts": ["audio.wav", "audio.json", "eng.srt"],
    "mux": ["audio.wav", "audio.json", "eng.srt", "tts_full.wav"],
}


def normalize_lite_resume_from(resume_from: Optional[str]) -> Optional[str]:
    if resume_from is None:
        return None
    value = str(resume_from).strip().lower()
    if not value:
        return None
    if value not in VALID_LITE_RESUME_STAGES:
        raise ValueError(f"invalid lite resume_from: {resume_from}")
    return value


def should_run_lite_asr(resume_from: Optional[str]) -> bool:
    stage = normalize_lite_resume_from(resume_from)
    return stage in {None, "asr"}


def should_run_lite_mt(resume_from: Optional[str]) -> bool:
    stage = normalize_lite_resume_from(resume_from)
    return stage in {None, "asr", "mt"}


def should_run_lite_tts(resume_from: Optional[str]) -> bool:
    stage = normalize_lite_resume_from(resume_from)
    return stage in {None, "asr", "mt", "tts"}


def required_lite_resume_artifacts(resume_from: Optional[str]) -> List[str]:
    stage = normalize_lite_resume_from(resume_from)
    if stage is None:
        return []
    return list(_REQUIRED_LITE_RESUME_ARTIFACTS[stage])


def collect_missing_lite_resume_artifacts(work_dir: Path, resume_from: Optional[str]) -> List[str]:
    required = required_lite_resume_artifacts(resume_from)
    return [name for name in required if not (work_dir / name).exists()]
