from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set

LISTABLE_LITE_ARTIFACTS: List[str] = [
    "asr_whispercpp.json",
    "audio.json",
    "audio.wav",
    "chs.srt",
    "chs.review.srt",
    "llm_contract_metrics.json",
    "eng.srt",
    "eng.review.srt",
    "bilingual.srt",
    "quality_report.json",
    "tts_full.wav",
    "output_en.mp4",
    "output_en_sub.mp4",
]

BASE_REQUIRED_LITE_ARTIFACTS: List[str] = [
    "audio.json",
    "chs.srt",
    "eng.srt",
]

FULL_REQUIRED_LITE_ARTIFACTS: List[str] = [
    "tts_plan.json",
    "tts_full.wav",
    "output_en.mp4",
    "output_en_sub.mp4",
]

LITE_DELIVERABLE_ARTIFACTS: Set[str] = {
    "output_en_sub.mp4",
    "chs.srt",
    "eng.srt",
    "bilingual.srt",
}

LITE_REVIEW_ARTIFACTS: Set[str] = {
    "chs.review.srt",
    "eng.review.srt",
    "review_audit.jsonl",
}

LITE_RESUME_ARTIFACTS: Set[str] = {
    "asr_whispercpp.json",
    "audio.json",
    "audio.wav",
    "eng_tts.srt",
    "eng_tts_raw.srt",
    "tts_full.wav",
    "output_en.mp4",
}

LITE_DIAGNOSTIC_ARTIFACTS: Set[str] = {
    "tts_fit.json",
    "mt_topic_auto.json",
    "run.log",
}


def list_existing_lite_artifacts(work_dir: Path) -> List[Dict[str, object]]:
    found: List[Dict[str, object]] = []
    for name in LISTABLE_LITE_ARTIFACTS:
        path = work_dir / name
        if path.exists():
            found.append({"name": name, "path": str(path), "size": path.stat().st_size})
    return found


def collect_lite_cleanup_targets(
    *,
    include_resume: bool,
    include_review: bool,
    include_diagnostics: bool,
) -> List[str]:
    to_remove: Set[str] = set()
    if include_diagnostics:
        to_remove |= LITE_DIAGNOSTIC_ARTIFACTS
    if include_review:
        to_remove |= LITE_REVIEW_ARTIFACTS
    if include_resume:
        to_remove |= LITE_RESUME_ARTIFACTS
    to_remove -= LITE_DELIVERABLE_ARTIFACTS
    return sorted(to_remove)
