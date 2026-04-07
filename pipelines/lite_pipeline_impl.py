from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from pydub import AudioSegment  # type: ignore
except ImportError:  # pragma: no cover - optional runtime dependency
    AudioSegment = None  # type: ignore

from pipelines.lib.text.asr_normalize import load_asr_dict, normalize_asr_zh_text
from pipelines.lib.text.en_replace import apply_en_replacements, load_en_dict
from pipelines.lib.utils.exec_utils import ensure_tool
from pipelines.lib.media.ffmpeg_mux import mux_video_audio
from pipelines.lib.glossary.glossary import apply_glossary_to_segments, load_glossary
from pipelines.lib.asr.lite_asr import (
    Segment,
    enforce_min_duration,
    extract_audio,
    run_asr_whispercpp,
    write_srt,
)
from pipelines.lib.asr.lite_asr_stage1 import AsrStage1Options, apply_asr_stage1_repairs
from pipelines.lib.lite_translate import translate_segments
from pipelines.lib.tts.lite_tts import (
    build_coqui_tts,
    build_kokoro_tts,
    save_audio,
    synthesize_segments_coqui,
    synthesize_segments_kokoro,
)
from pipelines.lib.mt.mt import build_batch_translator, build_polisher, build_translator
from pipelines.lib.media.lite_delivery import apply_subtitle_postprocess, apply_tts_plan
from pipelines.lib.lite_resume import (
    VALID_LITE_RESUME_STAGES,
    normalize_lite_resume_from,
    should_run_lite_asr,
    should_run_lite_mt,
    should_run_lite_tts,
)
from pipelines.lib.text.srt_io import read_srt_texts, read_srt_texts_ordered
from pipelines.lib.media.subtitles_burn import burn_subtitles
from pipelines.lib.text.text_enrich import build_languagetool, load_replacements
from pipelines.lib.text.zh_convert import zh_to_simplified
from pipelines.lib.text.translate_post import conservative_shorten_en

LITE_TTS_PLAN_WPS = 2.6
LITE_TTS_PLAN_MIN_WORDS = 3
LITE_TTS_PLAN_MAX_CPS = 20.0
LITE_TTS_PLAN_SAFETY_MARGIN = 0.02
LITE_SUBTITLE_WRAP_ENABLE = True
LITE_SUBTITLE_MAX_CHARS_PER_LINE = 42
LITE_SUBTITLE_MAX_LINES = 2


def _lite_subtitle_max_cps(args: argparse.Namespace) -> float:
    try:
        value = float(getattr(args, "subtitle_max_cps", LITE_TTS_PLAN_MAX_CPS) or LITE_TTS_PLAN_MAX_CPS)
    except Exception:
        value = float(LITE_TTS_PLAN_MAX_CPS)
    return max(8.0, value)


def _lite_subtitle_max_chars_per_line(args: argparse.Namespace) -> int:
    try:
        value = int(getattr(args, "subtitle_max_chars_per_line", LITE_SUBTITLE_MAX_CHARS_PER_LINE) or LITE_SUBTITLE_MAX_CHARS_PER_LINE)
    except Exception:
        value = int(LITE_SUBTITLE_MAX_CHARS_PER_LINE)
    return max(16, value)


def _lite_subtitle_max_lines(args: argparse.Namespace) -> int:
    try:
        value = int(getattr(args, "subtitle_max_lines", LITE_SUBTITLE_MAX_LINES) or LITE_SUBTITLE_MAX_LINES)
    except Exception:
        value = int(LITE_SUBTITLE_MAX_LINES)
    return max(1, value)


def parse_args() -> argparse.Namespace:
    # 命令行参数定义：模型、设备、输入输出路径等
    p = argparse.ArgumentParser(description="Chinese video -> English dub + subtitles (offline-first)")
    p.add_argument("--video", type=Path, required=True, help="Input video file")
    p.add_argument("--output-dir", type=Path, default=Path("outputs"), help="Directory for outputs")
    p.add_argument("--glossary", type=Path, default=Path("assets/glossary/glossary.json"), help="Glossary JSON path (optional)")
    p.add_argument("--chs-override-srt", type=Path, default=None, help="Override chs.srt content when rerunning MT (review workflow)")
    p.add_argument("--eng-override-srt", type=Path, default=None, help="Override eng.srt content when rerunning TTS (review workflow)")
    # ASR (whisper.cpp)
    p.add_argument("--asr-backend", choices=["whispercpp"], default="whispercpp", help="ASR backend (default whisper.cpp)")
    p.add_argument("--whispercpp-bin", type=Path, default=Path("bin/main"), help="Path to whisper.cpp executable (e.g., bin/main)")
    p.add_argument(
        "--whispercpp-model",
        type=Path,
        default=Path("assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin"),
        help="Path to whisper.cpp ggml model",
    )
    p.add_argument("--whispercpp-threads", type=int, default=None, help="Threads for whisper.cpp (optional)")
    p.add_argument("--whispercpp-beam-size", type=int, default=None, help="Beam size for whisper.cpp decoding (optional)")
    # MT
    p.add_argument("--mt-model", default="Helsinki-NLP/opus-mt-zh-en", help="MT model id")
    p.add_argument("--mt-device", default="auto", help="MT device: auto/cpu/cuda")
    p.add_argument("--mt-cache-dir", default=None, help="Transformers cache dir for MT models (offline mode)")
    p.add_argument("--mt-batch-enable", action="store_true", help="Enable batched MT inference for lite mainline")
    p.add_argument("--mt-batch-size", type=int, default=8, help="Batch size for MT inference when batching is enabled")
    p.add_argument("--offline", action="store_true", help="Fully offline: disable any model downloads (Transformers/HF)")
    # TTS
    p.add_argument(
        "--kokoro-model",
        type=Path,
        default=Path("assets/models/lite_tts_kokoro_onnx/kokoro-v1.0.onnx"),
        help="Kokoro ONNX model path",
    )
    p.add_argument(
        "--kokoro-voices",
        type=Path,
        default=Path("assets/models/lite_tts_kokoro_onnx/voices-v1.0.bin"),
        help="Kokoro ONNX voices path",
    )
    p.add_argument("--kokoro-voice", default="af_bella", help="Kokoro voice id")
    p.add_argument("--kokoro-language", default="en-us", help="Kokoro language code")
    p.add_argument("--kokoro-speed", type=float, default=1.0, help="Kokoro speech speed")
    p.add_argument("--tts-backend", choices=["kokoro_onnx", "coqui"], default="kokoro_onnx", help="TTS backend")
    p.add_argument("--coqui-model", default="tts_models/multilingual/multi-dataset/xtts_v2", help="Coqui TTS model name")
    p.add_argument("--coqui-device", default="auto", help="Coqui TTS device: auto/cpu/cuda")
    p.add_argument("--coqui-speaker", default=None, help="Coqui speaker name if multi-speaker model")
    p.add_argument("--coqui-language", default=None, help="Coqui language code if multilingual model")
    # 音频预处理 / VAD
    p.add_argument("--denoise", action="store_true", help="Apply simple denoise (ffmpeg arnndn) when extracting audio")
    p.add_argument("--denoise-model", type=Path, default=None, help="Path to arnndn model (.onnx)")
    p.add_argument("--vad-enable", action="store_true", help="Enable whisper.cpp VAD")
    p.add_argument("--vad-model", type=Path, default=None, help="Path to whisper.cpp VAD model file (required when --vad-enable)")
    p.add_argument("--vad-thold", type=float, default=None, help="whisper.cpp VAD threshold (e.g., 0.6)")
    p.add_argument("--vad-min-dur", type=float, default=None, help="whisper.cpp VAD min silence duration seconds (e.g., 1.5)")
    # 英文润色小 LLM（可选）
    p.add_argument("--en-polish-model", default=None, help="Optional English polish model id (text2text-generation); leave empty to disable")
    p.add_argument("--en-polish-device", default="auto", help="Polish model device: auto/cpu/cuda")
    p.add_argument("--lt-enable", action="store_true", help="Enable LanguageTool grammar/punctuation/typo correction")
    p.add_argument("--replacements", type=Path, default=Path("replacements.json"), help="JSON replacements rules (pattern/replace)")
    p.add_argument("--sample-rate", type=int, default=16000, help="Audio sample rate for extraction")
    p.add_argument("--tts-sample-rate", type=int, default=24000, help="Sample rate for synthesized TTS export")
    p.add_argument("--skip-tts", action="store_true", help="Skip TTS and mux; useful for ASR/MT only")
    p.add_argument("--bilingual-srt", action="store_true", help="Export bilingual SRT (zh|en)")
    # ASR text normalization (extremely low-risk). Enabled by config; can be disabled for debugging.
    p.add_argument("--asr-normalize-enable", action="store_true", help="Enable low-risk Chinese ASR text normalization")
    p.add_argument("--asr-normalize-dict", type=Path, default=Path("assets/asr_normalize/asr_zh_dict.json"), help="Optional JSON dictionary for known ASR typos (defaults to an empty dict file)")
    # English replacement dict (cautious whole-word mapping). Optional.
    p.add_argument("--en-replace-dict", type=Path, default=None, help="Optional JSON dictionary for English replacements (word-level)")
    p.add_argument(
        "--resume-from",
        choices=list(VALID_LITE_RESUME_STAGES),
        default=None,
        help="Resume from a specific stage, reusing existing artifacts under output-dir",
    )
    # Light-weight stability controls
    p.add_argument("--min-sub-dur", type=float, default=1.5, help="Minimum subtitle duration (seconds); will extend short segments")
    p.add_argument("--tts-split-len", type=int, default=100, help="Max characters per TTS chunk before splitting")
    p.add_argument("--tts-speed-max", type=float, default=1.15, help="Max speed-up factor when aligning audio")
    p.add_argument("--tts-plan-safety-margin", type=float, default=LITE_TTS_PLAN_SAFETY_MARGIN, help=argparse.SUPPRESS)
    p.add_argument("--subtitle-max-cps", type=float, default=LITE_TTS_PLAN_MAX_CPS, help=argparse.SUPPRESS)
    p.add_argument("--subtitle-max-chars-per-line", type=int, default=LITE_SUBTITLE_MAX_CHARS_PER_LINE, help=argparse.SUPPRESS)
    p.add_argument("--subtitle-max-lines", type=int, default=LITE_SUBTITLE_MAX_LINES, help=argparse.SUPPRESS)
    p.add_argument(
        "--tts-align-mode",
        choices=["atempo", "resample"],
        default="atempo",
        help="How to align TTS to time budget: atempo=better pitch preservation (recommended), resample=faster but may change timbre",
    )
    # Mux sync (hearing-first): when TTS audio is longer than video, allow bounded slow-down.
    p.add_argument("--mux-sync-strategy", choices=["slow", "freeze"], default="slow", help="When audio is longer: slow video or freeze last frame")
    p.add_argument("--mux-slow-max-ratio", type=float, default=1.18, help="Max slow-down ratio for whole video (e.g. 1.18 = 18% slower)")
    p.add_argument("--mux-slow-threshold-s", type=float, default=0.05, help="Trigger threshold seconds for applying sync strategy")

    p.add_argument("--asr-glossary-fix-enable", action="store_true", help="Apply rules-center ZH->ZH ASR fixes before MT")
    p.add_argument("--asr-low-cost-clean-enable", action="store_true", help="Enable low-cost local Chinese cleanup using open lexicon resources")
    p.add_argument("--asr-badline-detect-enable", action="store_true", help="Enable rule-based bad line detection after ASR")
    p.add_argument("--asr-same-pinyin-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_pinyin.txt"), help="Same-pinyin character map")
    p.add_argument("--asr-same-stroke-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_stroke.txt"), help="Same-stroke character map")
    p.add_argument("--asr-project-confusions-path", type=Path, default=Path("assets/zh_phrase/asr_project_confusions.json"), help="Project confusion set for high-risk routing and acceptance")
    p.add_argument("--asr-lexicon-path", type=Path, default=Path("assets/zh_phrase/chinese_xinhua_ci_2to4.txt"), help="Base Chinese lexicon for local ASR repair")
    p.add_argument("--asr-proper-nouns-path", type=Path, default=Path("assets/zh_phrase/thuocl_proper_nouns.txt"), help="Proper nouns lexicon for local ASR repair")
    p.add_argument("--asr-llm-fix-enable", action="store_true", help="Enable conservative LLM repair for high-risk Chinese ASR lines")
    p.add_argument("--asr-llm-fix-mode", type=str, default="suspect", help="LLM fix mode; lite currently only supports suspect/high-risk lines")
    p.add_argument("--asr-llm-fix-max-items", type=int, default=60, help="Max high-risk ASR lines to send to LLM")
    p.add_argument("--asr-llm-fix-max-ratio", type=float, default=0.35, help="Max ratio of ASR lines eligible for LLM repair")
    p.add_argument("--asr-llm-fix-min-chars", type=int, default=8, help="Min Chinese chars before a high-risk line can be sent to LLM")
    p.add_argument("--asr-llm-fix-batch-size", type=int, default=4, help="How many ASR lines to repair per LLM request")
    p.add_argument("--asr-llm-fix-timeout-s", type=int, default=10, help="Per-request timeout seconds for ASR LLM repair")
    p.add_argument("--asr-llm-fix-retries", type=int, default=1, help="Retry count for ASR LLM repair requests")
    p.add_argument("--asr-llm-fix-budget-s", type=float, default=30.0, help="Total LLM budget seconds for ASR stage1")
    p.add_argument("--asr-llm-fix-verify-enable", action="store_true", help="Verify changed ASR LLM repairs with a second lightweight LLM pass")
    p.add_argument("--asr-llm-fix-verify-timeout-s", type=int, default=6, help="Per-request timeout seconds for ASR LLM verify pass")
    p.add_argument("--asr-llm-fix-save-debug", action="store_true", help="Save ASR stage1 debug artifacts for review")
    p.add_argument("--asr-llm-fix-model", type=str, default="", help="LLM model id/name for ASR high-risk repair")
    p.add_argument("--asr-llm-fix-endpoint", type=str, default="", help="OpenAI-compatible endpoint for ASR high-risk repair")
    p.add_argument("--asr-llm-fix-api-key", type=str, default="", help="API key for ASR high-risk repair endpoint")
    # Subtitle burn-in style (hard-sub)
    p.add_argument("--sub-font-name", default="Arial", help="Subtitle font name for hard-burn (best-effort)")
    p.add_argument("--sub-font-size", type=int, default=18, help="Subtitle font size for hard-burn")
    p.add_argument("--sub-outline", type=int, default=1, help="Subtitle outline thickness")
    p.add_argument("--sub-shadow", type=int, default=0, help="Subtitle shadow")
    p.add_argument("--sub-margin-v", type=int, default=24, help="Subtitle vertical margin (pixels)")
    p.add_argument("--sub-alignment", type=int, default=2, help="ASS Alignment (2=bottom-center)")
    # Hard subtitle erase (burned-in subtitles on source video)
    p.add_argument("--erase-subtitle-enable", action="store_true", help="Enable best-effort source subtitle erase")
    p.add_argument("--erase-subtitle-method", default="delogo", help="Erase method: auto/fill/blur/delogo")
    p.add_argument("--erase-subtitle-coord-mode", default="ratio", help="Erase rectangle coordinate mode: ratio/px")
    p.add_argument("--erase-subtitle-x", type=float, default=0.0, help="Erase rectangle x")
    p.add_argument("--erase-subtitle-y", type=float, default=0.78, help="Erase rectangle y")
    p.add_argument("--erase-subtitle-w", type=float, default=1.0, help="Erase rectangle width")
    p.add_argument("--erase-subtitle-h", type=float, default=0.22, help="Erase rectangle height")
    p.add_argument("--erase-subtitle-blur-radius", type=int, default=12, help="Blur radius for erase blur mode")
    # Subtitle placement box (optional; takes precedence when enabled)
    p.add_argument("--sub-place-enable", action="store_true", help="Enable explicit subtitle placement box")
    p.add_argument("--sub-place-coord-mode", default="ratio", help="Subtitle placement coordinate mode: ratio/px")
    p.add_argument("--sub-place-x", type=float, default=0.0, help="Subtitle placement x")
    p.add_argument("--sub-place-y", type=float, default=0.78, help="Subtitle placement y")
    p.add_argument("--sub-place-w", type=float, default=1.0, help="Subtitle placement width")
    p.add_argument("--sub-place-h", type=float, default=0.22, help="Subtitle placement height")
    return p.parse_args()


@dataclass(frozen=True)
class LiteArtifacts:
    output_dir: Path
    work_tts: Path
    work_asr_prefix: Path
    audio_pcm: Path
    audio_json: Path
    chs_srt: Path
    eng_srt: Path
    bi_srt: Path
    tts_plan_json: Path
    tts_wav: Path
    video_dub: Path
    video_sub: Path


def _prepare_runtime(args: argparse.Namespace, *, need_asr: bool, need_tts: bool) -> None:
    ensure_tool("ffmpeg")
    if need_asr and args.asr_backend == "whispercpp":
        ensure_tool(str(args.whispercpp_bin))


def _build_artifacts(output_dir: Path) -> LiteArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    return LiteArtifacts(
        output_dir=output_dir,
        work_tts=output_dir / "tts_segments",
        work_asr_prefix=output_dir / "asr_whispercpp",
        audio_pcm=output_dir / "audio.wav",
        audio_json=output_dir / "audio.json",
        chs_srt=output_dir / "chs.srt",
        eng_srt=output_dir / "eng.srt",
        bi_srt=output_dir / "bilingual.srt",
        tts_plan_json=output_dir / "tts_plan.json",
        tts_wav=output_dir / "tts_full.wav",
        video_dub=output_dir / "output_en.mp4",
        video_sub=output_dir / "output_en_sub.mp4",
    )


def _require_resume_artifact(path: Path, *, resume_from: Optional[str]) -> None:
    if not path.exists():
        raise SystemExit(f"resume_from={resume_from} 但缺少 {path}")


def _load_audio_total_ms(audio_pcm: Path) -> Optional[float]:
    if AudioSegment is None or not audio_pcm.exists():
        return None
    try:
        return float(len(AudioSegment.from_file(audio_pcm)))
    except Exception:
        return None


def _normalize_asr_segments(segments: List[Segment], args: argparse.Namespace) -> None:
    asr_dict = (
        load_asr_dict(getattr(args, "asr_normalize_dict", None))
        if getattr(args, "asr_normalize_enable", False)
        else {}
    )
    for seg in segments:
        seg.text = normalize_asr_zh_text(seg.text, to_simplified_fn=zh_to_simplified, asr_dict=asr_dict)


def _build_asr_stage1_options(args: argparse.Namespace, output_dir: Path) -> AsrStage1Options:
    return AsrStage1Options(
        glossary_fix_enable=bool(getattr(args, "asr_glossary_fix_enable", False)),
        low_cost_clean_enable=bool(getattr(args, "asr_low_cost_clean_enable", False)),
        badline_detect_enable=bool(getattr(args, "asr_badline_detect_enable", False)),
        same_pinyin_path=getattr(args, "asr_same_pinyin_path", None),
        same_stroke_path=getattr(args, "asr_same_stroke_path", None),
        project_confusions_path=getattr(args, "asr_project_confusions_path", None),
        lexicon_path=getattr(args, "asr_lexicon_path", None),
        proper_nouns_path=getattr(args, "asr_proper_nouns_path", None),
        output_dir=output_dir,
    )


def _run_or_resume_asr(
    args: argparse.Namespace,
    *,
    artifacts: LiteArtifacts,
    resume_from: Optional[str],
) -> List[Segment]:
    if should_run_lite_asr(resume_from):
        print("[1/7] Extracting audio...")
        extract_audio(
            args.video,
            artifacts.audio_pcm,
            sample_rate=args.sample_rate,
            denoise=args.denoise,
            denoise_model=args.denoise_model,
        )

        print("[2/7] Running ASR (whisper.cpp)...")
        segments = run_asr_whispercpp(
            audio_path=artifacts.audio_pcm,
            whisper_bin=args.whispercpp_bin,
            model_path=args.whispercpp_model,
            output_prefix=artifacts.work_asr_prefix,
            language="zh",
            threads=args.whispercpp_threads,
            beam_size=args.whispercpp_beam_size,
            vad_enable=args.vad_enable,
            vad_model=args.vad_model,
            vad_thold=args.vad_thold,
            vad_min_sil_ms=int(args.vad_min_dur * 1000) if args.vad_min_dur else None,
        )
        print(f"ASR segments: {len(segments)}")
        segments = enforce_min_duration(segments, min_duration=args.min_sub_dur)
        _normalize_asr_segments(segments, args)
        glossary = load_glossary(getattr(args, "glossary", None))
        asr_stage1_report = apply_asr_stage1_repairs(
            segments,
            glossary=glossary,
            opts=_build_asr_stage1_options(args, artifacts.output_dir),
        )
        print(
            "[2a/7] ASR stage1: "
            f"glossary={asr_stage1_report['summary']['glossary_segments_changed']}, "
            f"low_cost={asr_stage1_report['summary']['low_cost_segments_changed']}, "
            f"suspects={asr_stage1_report['summary']['suspect_segments_total']}, "
            f"high_risk={asr_stage1_report['summary']['high_risk_segments_total']}"
        )
    else:
        _require_resume_artifact(artifacts.audio_pcm, resume_from=resume_from)
        _require_resume_artifact(artifacts.audio_json, resume_from=resume_from)
        data = json.loads(artifacts.audio_json.read_text(encoding="utf-8", errors="ignore") or "[]")
        segments = [Segment(**item) for item in data]
        _normalize_asr_segments(segments, args)
        glossary = load_glossary(getattr(args, "glossary", None))
        apply_asr_stage1_repairs(
            segments,
            glossary=glossary,
            opts=_build_asr_stage1_options(args, artifacts.output_dir),
        )

    artifacts.audio_json.write_text(
        json.dumps([seg.__dict__ for seg in segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_srt(artifacts.chs_srt, segments, text_attr="text")
    return segments


def _apply_chs_override(segments: List[Segment], args: argparse.Namespace, chs_srt: Path) -> None:
    override_path = getattr(args, "chs_override_srt", None)
    if not override_path:
        return
    ov = Path(override_path)
    if not ov.exists():
        return
    texts = read_srt_texts_ordered(ov)
    if not texts:
        return
    for i, seg in enumerate(segments):
        if i < len(texts) and texts[i].strip():
            seg.text = zh_to_simplified(texts[i].strip())
    try:
        chs_srt.write_text(zh_to_simplified(ov.read_text(encoding="utf-8", errors="ignore")), encoding="utf-8")
    except Exception:
        pass


def _build_mt_runtime(
    args: argparse.Namespace,
) -> tuple[
    Callable[[str], str],
    Optional[Callable[[List[str]], List[str]]],
    Any,
    Any,
    Dict[str, str],
]:
    offline = bool(args.offline) or os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    translate_batch_fn: Optional[Callable[[List[str]], List[str]]] = None
    if getattr(args, "mt_batch_enable", False):
        batch_size = max(1, int(getattr(args, "mt_batch_size", 8) or 8))
        print(f"[3a] MT batching enabled: batch_size={batch_size}")
        translate_batch_fn = build_batch_translator(
            args.mt_model,
            device=args.mt_device,
            cache_dir=args.mt_cache_dir,
            offline=offline,
            batch_size=batch_size,
        )
        translate_fn = lambda text: translate_batch_fn([text])[0]
    else:
        translate_fn = build_translator(args.mt_model, device=args.mt_device, cache_dir=args.mt_cache_dir, offline=offline)

    polish_fn = None
    if args.en_polish_model:
        print(f"[3b] Building English polisher: {args.en_polish_model}")
        polish_fn = build_polisher(args.en_polish_model, device=args.en_polish_device)

    lt_fn = None
    if args.lt_enable:
        print("[3c] Building LanguageTool (grammar/punctuation/typo)...")
        lt_fn = build_languagetool()

    en_dict = load_en_dict(getattr(args, "en_replace_dict", None))
    return translate_fn, translate_batch_fn, polish_fn, lt_fn, en_dict


def _apply_eng_override(seg_en: List[Segment], args: argparse.Namespace, eng_srt: Path) -> None:
    override_path = getattr(args, "eng_override_srt", None)
    if not override_path:
        return
    try:
        ov = Path(override_path)
        if not ov.exists():
            return
        texts = read_srt_texts_ordered(ov)
        if texts:
            for i, seg in enumerate(seg_en):
                if i < len(texts) and texts[i].strip():
                    seg.translation = texts[i].strip()
            try:
                eng_srt.write_text(ov.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            except Exception:
                pass
    except Exception as exc:
        print(f"[warn] Failed to apply eng_override_srt during MT path; continuing: {exc}")


def _run_or_resume_mt(
    args: argparse.Namespace,
    *,
    segments: List[Segment],
    artifacts: LiteArtifacts,
    resume_from: Optional[str],
) -> List[Segment]:
    if should_run_lite_mt(resume_from):
        print("[3/7] Building translator...")
        _apply_chs_override(segments, args, artifacts.chs_srt)
        translate_fn, translate_batch_fn, polish_fn, lt_fn, en_dict = _build_mt_runtime(args)
        replacement_rules = load_replacements(args.replacements)

        print("[4/7] Translating segments...")
        seg_en = translate_segments(
            segments,
            translate_fn,
            translate_batch_fn=translate_batch_fn,
            polish_fn=polish_fn,
            lt_fn=lt_fn,
            replacement_rules=replacement_rules,
        )
        _apply_eng_override(seg_en, args, artifacts.eng_srt)
        glossary = load_glossary(getattr(args, "glossary", None))
        if glossary:
            stats = apply_glossary_to_segments(seg_en, glossary)
            print(f"[4a] Glossary applied: {stats}")
        if en_dict:
            for seg in seg_en:
                seg.translation = apply_en_replacements(getattr(seg, "translation", "") or "", en_dict)
        shortened = 0
        max_cps = _lite_subtitle_max_cps(args)
        for seg in seg_en:
            before = str(getattr(seg, "translation", "") or "")
            after = conservative_shorten_en(
                before,
                duration_s=max(float(seg.end) - float(seg.start), 0.001),
                max_cps=max_cps,
            )
            if after != before:
                seg.translation = after
                shortened += 1
        if shortened > 0:
            print(f"[4a2] conservative_shorten: shortened={shortened} max_cps={max_cps:g}")
        if not artifacts.eng_srt.exists() or not getattr(args, "eng_override_srt", None):
            write_srt(artifacts.eng_srt, seg_en, text_attr="translation")
        return seg_en

    override = getattr(args, "eng_override_srt", None)
    eng_path = Path(override) if override else artifacts.eng_srt
    _require_resume_artifact(eng_path, resume_from=resume_from)
    en_texts = read_srt_texts(eng_path)
    seg_en = segments
    for i, seg in enumerate(seg_en):
        seg.translation = en_texts[i] if i < len(en_texts) else (seg.translation or "")
    try:
        if eng_path != artifacts.eng_srt:
            artifacts.eng_srt.write_text(eng_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    except Exception:
        pass
    return seg_en


def _write_tts_plan(args: argparse.Namespace, seg_en: List[Segment], *, video_path: Path, tts_plan_json: Path) -> None:
    try:
        plan_doc = apply_tts_plan(
            seg_en,
            video_path=video_path,
            max_speed=float(getattr(args, "tts_speed_max", 1.15) or 1.15),
            wps=LITE_TTS_PLAN_WPS,
            min_dur=float(getattr(args, "min_sub_dur", 1.5) or 1.5),
            max_cps=_lite_subtitle_max_cps(args),
            mux_slow_max_ratio=float(getattr(args, "mux_slow_max_ratio", 1.18) or 1.18),
            tts_plan_safety_margin=float(getattr(args, "tts_plan_safety_margin", LITE_TTS_PLAN_SAFETY_MARGIN) or LITE_TTS_PLAN_SAFETY_MARGIN),
            tts_fit_min_words=LITE_TTS_PLAN_MIN_WORDS,
        )
        tts_plan_json.write_text(json.dumps(plan_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            "[4b] tts_plan: "
            f"enabled, rebalanced={bool(plan_doc.get('rebalanced'))}, "
            f"items={len(seg_en)}"
        )
    except Exception as exc:
        print(f"[warn] tts_plan failed, continuing without it: {exc}")


def _finalize_eng_subtitles(args: argparse.Namespace, seg_en: List[Segment], eng_srt: Path) -> None:
    report = apply_subtitle_postprocess(
        seg_en,
        wrap_enable=LITE_SUBTITLE_WRAP_ENABLE,
        max_chars_per_line=_lite_subtitle_max_chars_per_line(args),
        max_lines=_lite_subtitle_max_lines(args),
    )
    write_srt(eng_srt, seg_en, text_attr="translation")
    if int(report.get("normalized") or 0) > 0 or int(report.get("wrapped") or 0) > 0:
        print(
            "[4c] subtitle_postprocess: "
            f"normalized={int(report.get('normalized') or 0)}, "
            f"wrapped={int(report.get('wrapped') or 0)}"
        )


def _write_bilingual_srt(seg_en: List[Segment], bi_srt: Path) -> None:
    bilingual_segments = []
    for seg in seg_en:
        bilingual_text = f"{zh_to_simplified(seg.text)}\\n{seg.translation}"
        bilingual_segments.append(Segment(start=seg.start, end=seg.end, text=bilingual_text, translation=seg.translation))
    write_srt(bi_srt, bilingual_segments, text_attr="text")


def _run_or_resume_tts(
    args: argparse.Namespace,
    *,
    seg_en: List[Segment],
    artifacts: LiteArtifacts,
    resume_from: Optional[str],
    audio_total_ms: Optional[float],
) -> None:
    if not should_run_lite_tts(resume_from):
        _require_resume_artifact(artifacts.tts_wav, resume_from=resume_from)
        return

    print(f"[5/7] Synthesizing TTS with {args.tts_backend}...")
    # Robustness: if ASR/MT produced no segments, still emit a silent track so
    # the lite pipeline degrades gracefully instead of failing in TTS.
    if not seg_en:
        if AudioSegment is None:
            raise RuntimeError("No subtitle segments and pydub unavailable; cannot generate silent TTS.")
        dur_ms = int(round(float(audio_total_ms) if audio_total_ms is not None else 1000.0))
        dur_ms = max(dur_ms, 300)
        combined_audio = AudioSegment.silent(duration=dur_ms).set_frame_rate(int(args.tts_sample_rate or args.sample_rate or 16000))
        save_audio(combined_audio, artifacts.tts_wav, sample_rate=args.tts_sample_rate)
        print(f"[5/7] tts_fallback: segs=0 mode=silence audio_ms={dur_ms}")
        return

    if args.tts_backend == "kokoro_onnx":
        kokoro = build_kokoro_tts(args.kokoro_model, args.kokoro_voices)
        combined_audio = synthesize_segments_kokoro(
            seg_en,
            kokoro=kokoro,
            work_dir=artifacts.work_tts,
            sample_rate=args.tts_sample_rate,
            voice=args.kokoro_voice,
            language=args.kokoro_language,
            speed=args.kokoro_speed,
            split_len=args.tts_split_len,
            max_speed=args.tts_speed_max,
            align_mode=getattr(args, "tts_align_mode", "resample"),
            pad_to_ms=audio_total_ms,
        )
    else:
        tts = build_coqui_tts(model_name=args.coqui_model, device=args.coqui_device)
        combined_audio = synthesize_segments_coqui(
            seg_en,
            tts=tts,
            work_dir=artifacts.work_tts,
            sample_rate=args.sample_rate,
            speaker=args.coqui_speaker,
            language=args.coqui_language,
            split_len=args.tts_split_len,
            max_speed=args.tts_speed_max,
            align_mode=getattr(args, "tts_align_mode", "resample"),
            pad_to_ms=audio_total_ms,
        )
    save_audio(combined_audio, artifacts.tts_wav, sample_rate=args.tts_sample_rate)


def _mux_and_embed(args: argparse.Namespace, artifacts: LiteArtifacts) -> None:
    print("[6/7] Muxing video with new audio...")
    erase_enable = bool(getattr(args, "erase_subtitle_enable", False))
    erase_w = float(getattr(args, "erase_subtitle_w", 1.0) or 0.0)
    erase_h = float(getattr(args, "erase_subtitle_h", 0.22) or 0.0)
    mux_video_audio(
        args.video,
        artifacts.tts_wav,
        artifacts.video_dub,
        sync_strategy=str(getattr(args, "mux_sync_strategy", "slow") or "slow"),
        slow_max_ratio=float(getattr(args, "mux_slow_max_ratio", 1.18) or 1.18),
        threshold_s=float(getattr(args, "mux_slow_threshold_s", 0.05) or 0.05),
        tail_pad_max_s=0.0,
        erase_subtitle_enable=erase_enable,
        erase_subtitle_method=str(getattr(args, "erase_subtitle_method", "delogo") or "delogo"),
        erase_subtitle_coord_mode=str(getattr(args, "erase_subtitle_coord_mode", "ratio") or "ratio"),
        erase_subtitle_x=float(getattr(args, "erase_subtitle_x", 0.0) or 0.0),
        erase_subtitle_y=float(getattr(args, "erase_subtitle_y", 0.78) or 0.78),
        erase_subtitle_w=erase_w if erase_w else float(getattr(args, "erase_subtitle_w", 1.0) or 1.0),
        erase_subtitle_h=erase_h if erase_h else float(getattr(args, "erase_subtitle_h", 0.22) or 0.22),
        erase_subtitle_blur_radius=int(getattr(args, "erase_subtitle_blur_radius", 12) or 12),
    )

    print("[7/7] Embedding subtitles...")
    srt_to_burn = artifacts.bi_srt if getattr(args, "bilingual_srt", False) and artifacts.bi_srt.exists() else artifacts.eng_srt
    if not srt_to_burn.exists() or not srt_to_burn.read_text(encoding="utf-8", errors="ignore").strip():
        shutil.copyfile(artifacts.video_dub, artifacts.video_sub)
        print("[7/7] subtitle_fallback: empty_srt=true mode=copy_video")
        return
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
        "[7/7] subtitle_burn_layout: "
        f"source={'sub_place' if bool(getattr(args, 'sub_place_enable', False)) else ('erase_rect' if bool(getattr(args, 'erase_subtitle_enable', False)) else 'default')} "
        f"place_enable={place_enable} coord={place_coord_mode} x={place_x} y={place_y} w={place_w} h={place_h} "
        f"font={int(getattr(args, 'sub_font_size', 18) or 18)}"
    )
    burn_subtitles(
        artifacts.video_dub,
        srt_to_burn,
        artifacts.video_sub,
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


def _print_outputs(args: argparse.Namespace, artifacts: LiteArtifacts) -> None:
    print("Done.")
    print(f"Outputs in: {artifacts.output_dir}")
    print(f"- ASR JSON:   {artifacts.audio_json}")
    print(f"- CHS SRT:    {artifacts.chs_srt}")
    print(f"- ENG SRT:    {artifacts.eng_srt}")
    if args.bilingual_srt:
        print(f"- BI SRT:     {artifacts.bi_srt}")
    if artifacts.tts_plan_json.exists():
        print(f"- TTS plan:   {artifacts.tts_plan_json}")
    print(f"- TTS audio:  {artifacts.tts_wav}")
    print(f"- Video dub:  {artifacts.video_dub}")
    print(f"- Video+sub:  {artifacts.video_sub}")


def main() -> None:
    # 主流程：准备 -> ASR -> 翻译 ->（可选）TTS -> 复合 -> 字幕封装
    args = parse_args()
    mode = getattr(args, "mode", "lite")
    if mode in {"quality", "online"}:
        raise SystemExit(f"Mode '{mode}' not supported in this pipeline. Use lite or select another pipeline.")
    resume_from = normalize_lite_resume_from(getattr(args, "resume_from", None))
    need_asr = should_run_lite_asr(resume_from)
    need_tts = (not args.skip_tts) and should_run_lite_tts(resume_from)
    _prepare_runtime(args, need_asr=need_asr, need_tts=need_tts)
    artifacts = _build_artifacts(args.output_dir)
    segments = _run_or_resume_asr(args, artifacts=artifacts, resume_from=resume_from)
    audio_total_ms = _load_audio_total_ms(artifacts.audio_pcm)

    seg_en = _run_or_resume_mt(args, segments=segments, artifacts=artifacts, resume_from=resume_from)
    _write_tts_plan(args, seg_en, video_path=Path(args.video), tts_plan_json=artifacts.tts_plan_json)
    _finalize_eng_subtitles(args, seg_en, artifacts.eng_srt)

    if args.bilingual_srt:
        _write_bilingual_srt(seg_en, artifacts.bi_srt)

    if args.skip_tts:
        print("Skip TTS enabled; generated subtitles only.")
        return

    _run_or_resume_tts(
        args,
        seg_en=seg_en,
        artifacts=artifacts,
        resume_from=resume_from,
        audio_total_ms=audio_total_ms,
    )
    _mux_and_embed(args, artifacts)
    _print_outputs(args, artifacts)


