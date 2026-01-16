#!/usr/bin/env python3
"""
End-to-end CLI: Chinese video -> English dub + English/Chinese subtitles (offline-first).
Steps:
1) Extract audio with ffmpeg
2) ASR with Whisper.cpp (ggml) -> segments with timestamps
3) MT with Hugging Face Transformers (Marian/NLLB) -> English text
4) TTS with piper CLI -> per-segment audio, padded/aligned to timestamps
5) Concatenate TTS audio, mux with original video, and embed subtitles

Requirements (pip):
  pip install -U transformers sentencepiece pydub pysubs2
External tools:
  - ffmpeg (installed)
  - whisper.cpp binary + ggml model (e.g., ggml-small-q5_0.bin)
  - piper binary + English ONNX model (e.g., en_US-amy-low.onnx)

中文提示：
  - 这是离线流程：音频提取 -> 识别(whisper.cpp) -> 翻译 -> 合成 -> 复合。
  - 需准备 whisper.cpp 可执行文件与 ggml 模型（默认 small-q5_0），以及 piper 与英文 ONNX 模型。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import os
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

try:
    from pydub import AudioSegment  # type: ignore
except ImportError:  # pragma: no cover - optional runtime dependency
    AudioSegment = None  # type: ignore

try:
    import torch  # type: ignore
except ImportError:  # pragma: no cover - optional runtime dependency
    torch = None  # type: ignore


@dataclass
class Segment:
    # 单条语音片段的时间戳与文本（text 为中文，translation 为英文）
    start: float
    end: float
    text: str
    translation: Optional[str] = None


def run_cmd(
    cmd: List[str],
    check: bool = True,
    env: Optional[dict] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """运行子进程命令，失败时抛出详细日志，便于排查。"""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        input=input_text,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def ensure_tool(name: str) -> None:
    """
    Ensure external tools (ffmpeg / piper / whisper.cpp) are available.
    Support absolute/relative paths in docker (shutil.which() won't work for those).
    """
    if os.sep in name or name.startswith("."):
        p = Path(name)
        if not p.is_absolute():
            repo_root = Path(__file__).resolve().parents[1]
            p = repo_root / p
        if not (p.exists() and os.access(str(p), os.X_OK)):
            raise SystemExit(f"Missing required tool: {name}. Please install and retry.")
        return
    if not shutil.which(name):
        raise SystemExit(f"Missing required tool: {name}. Please install and retry.")


_PIPER_BIN_CACHE: dict[str, str] = {}


def _resolve_path_like(name: str) -> Path:
    p = Path(name)
    if not p.is_absolute():
        repo_root = Path(__file__).resolve().parents[1]
        p = repo_root / p
    return p


def prepare_piper_bin(configured: str) -> str:
    """
    Docker Desktop/macOS bind mount sometimes runs into `noexec`, causing runtime:
      bash: .../piper: Permission denied
    Workaround: copy the whole piper folder to /tmp and run from there.
    """
    if configured in _PIPER_BIN_CACHE:
        return _PIPER_BIN_CACHE[configured]
    if os.sep not in configured and not configured.startswith("."):
        _PIPER_BIN_CACHE[configured] = configured
        return configured
    p = _resolve_path_like(configured)
    if not p.exists():
        _PIPER_BIN_CACHE[configured] = configured
        return configured
    try:
        subprocess.run(
            [str(p), "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        _PIPER_BIN_CACHE[configured] = str(p)
        return str(p)
    except OSError as e:
        if getattr(e, "errno", None) != 13:
            _PIPER_BIN_CACHE[configured] = str(p)
            return str(p)
    src_dir = p.parent
    tag = hashlib.sha1(str(src_dir).encode("utf-8")).hexdigest()[:10]
    dst_dir = Path("/tmp") / f"piper_{tag}"
    try:
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        for bin_name in ("piper", "piper_phonemize"):
            bp = dst_dir / bin_name
            if bp.exists():
                bp.chmod(0o755)
        time.sleep(0.02)
        dst_piper = dst_dir / p.name
        _PIPER_BIN_CACHE[configured] = str(dst_piper)
        return str(dst_piper)
    except Exception:
        _PIPER_BIN_CACHE[configured] = str(p)
        return str(p)


def _find_espeak_data_dir(piper_bin: str) -> Optional[Path]:
    """
    Find an espeak-ng-data directory that contains `phontab`.
    Piper phonemization needs espeak-ng-data; location varies by packaging.
    """
    candidates: List[Path] = []
    try:
        pb = Path(piper_bin)
        if pb.is_absolute():
            candidates.append(pb.parent / "espeak-ng-data")
            candidates.append(pb.parent / "share" / "espeak-ng-data")
            candidates.append(pb.parent.parent / "share" / "espeak-ng-data")
    except Exception:
        pass
    candidates.extend(
        [
            Path("/app/bin/piper/espeak-ng-data"),
            Path("/usr/share/espeak-ng-data"),
            Path("/usr/lib/espeak-ng-data"),
            Path("/usr/libexec/espeak-ng-data"),
        ]
    )
    for d in candidates:
        try:
            if (d / "phontab").exists():
                return d
        except Exception:
            continue
    return None


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


def extract_audio(
    video_path: Path,
    audio_path: Path,
    sample_rate: int = 16000,
    denoise: bool = False,
    denoise_model: Optional[Path] = None,
) -> None:
    # 统一转单声道、16k 采样，提升 ASR 稳定性；可选简单去噪
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
        if denoise_model:
            cmd.extend(["-af", f"arnndn=m={denoise_model}"])
        else:
            cmd.extend(["-af", "arnndn"])
    cmd.append(str(audio_path))
    run_cmd(cmd)


def run_asr_whispercpp(
    audio_path: Path,
    whisper_bin: Path,
    model_path: Path,
    output_prefix: Path,
    language: str = "zh",
    threads: Optional[int] = None,
    vad_enable: bool = False,
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
    if vad_enable:
        cmd.append("--vad")
        if vad_thold is not None:
            cmd.extend(["--vad-threshold", str(vad_thold)])
        if vad_min_sil_ms is not None:
            cmd.extend(["--vad-min-silence-duration-ms", str(vad_min_sil_ms)])
    run_cmd(cmd)

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


def build_translator(model_id: str, device: str = "auto"):
    # 构建翻译 pipeline（Marian/NLLB/M2M100），自动选择 CPU/GPU
    from transformers import AutoTokenizer, pipeline

    if device == "auto":
        if torch is not None and torch.cuda.is_available():  # type: ignore
            device = 0
        else:
            device = -1

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    translator = pipeline("translation", model=model_id, tokenizer=tokenizer, device=device, src_lang="zh", tgt_lang="en")

    def translate(text: str) -> str:
        out = translator(text, max_length=512, truncation=True)
        return out[0]["translation_text"]

    return translate


def build_polisher(model_id: str, device: str = "auto"):
    # 小型英文润色/纠错模型（text2text-generation），默认 GEC
    from transformers import pipeline

    if device == "auto":
        if torch is not None and torch.cuda.is_available():  # type: ignore
            device = 0
        else:
            device = -1
    polish = pipeline("text2text-generation", model=model_id, device=device)

    def polish_fn(text: str) -> str:
        out = polish(text, max_new_tokens=96, truncation=True)
        return out[0]["generated_text"]

    return polish_fn


def load_replacements(path: Optional[Path]) -> List[dict]:
    """加载词典替换规则（JSON），每条包含 pattern、replace、ignore_case。"""
    if not path:
        return []
    try:
        import json
    except Exception:
        return []
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    except Exception:
        return []
    return []


def build_languagetool():
    """LanguageTool 规则纠错（Grammar/Punctuation/Typo），可选启用。"""
    try:
        import language_tool_python
    except ImportError as exc:
        raise SystemExit("LanguageTool not installed. Please `pip install language-tool-python`.") from exc

    tool = language_tool_python.LanguageTool("en-US")
    allowed = {"Grammar", "Punctuation", "Typo"}

    def lt_fn(text: str) -> str:
        matches = [m for m in tool.check(text) if m.ruleIssueType in allowed]
        corrected = language_tool_python.utils.correct(text, matches)
        return corrected

    return lt_fn


def translate_segments(
    segments: List[Segment],
    translate_fn,
    polish_fn=None,
    lt_fn=None,
    replacement_rules: Optional[List[dict]] = None,
) -> List[Segment]:
    # 合并翻译再回填；数字占位保护；规则级英文清理；可选词典替换/LT/外部润色（默认关闭）
    punct = set("。！？!?.,")

    def clean_en(s: str) -> str:
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"\bIn order to\b", "To", s, flags=re.IGNORECASE)
        if s:
            s = s[0].upper() + s[1:]
        return s

    def rule_polish(s: str) -> str:
        rules = [
            (r"\bIn order to\b", "To"),
            (r"\bTherefore\b", "So"),
            (r"\s+,", ","),
            (r",\s+", ", "),
            (r"\s+\.", "."),
            (r"\s+\?", "?"),
            (r"\s+!", "!"),
            (r"\s+'", "'"),
            (r"\bcan not\b", "cannot"),
            (r"\bdo not\b", "don't"),
            (r"\bis not\b", "isn't"),
        ]
        for pat, rep in rules:
            s = re.sub(pat, rep, s, flags=re.IGNORECASE)
        return s.strip()

    def dedupe_repeats(text: str, ngram: int = 3, max_rep: int = 2) -> str:
        # 简单 n-gram 去重，防止 TTS 遇到重复长句
        words = text.split()
        seen = []
        out = []
        for w in words:
            out.append(w)
            if len(out) >= ngram:
                tail = tuple(out[-ngram:])
                seen.append(tail)
                # 如果最近 2*ngram 范围内重复超过阈值则移除
                recent = seen[-max_rep:]
                if len(recent) == max_rep and len(set(recent)) == 1:
                    out = out[:-ngram]
                    break
        return " ".join(out)

    def dedupe_phrases(text: str, max_len: int = 6) -> str:
        # 对连续重复的 4~6 词短语做窗口去重
        words = text.split()
        if len(words) <= max_len:
            return text
        out = []
        i = 0
        while i < len(words):
            window = words[i : i + max_len]
            next_window = words[i + max_len : i + 2 * max_len]
            if window and next_window and window == next_window:
                out.extend(window)
                i += max_len * 2
            else:
                out.append(words[i])
                i += 1
        return " ".join(out)

    def apply_replacements(text: str, rules: List[dict]) -> str:
        # 词典替换，按配置顺序应用，优先级高的写在前面
        out = text
        for item in rules:
            pat = item.get("pattern")
            rep = item.get("replace", "")
            flags = re.IGNORECASE if item.get("ignore_case", True) else 0
            if not pat:
                continue
            out = re.sub(pat, rep, out, flags=flags)
        return out

    def protect_nums(text: str):
        used = []
        def repl(m):
            token = f"__NUM{len(used)}__"
            used.append((token, m.group(0)))
            return token
        new_text = re.sub(r"\d+", repl, text)
        return new_text, used

    def restore(text: str, used):
        for token, val in used:
            text = text.replace(token, val)
        return text

    max_chars = 40  # 进一步收紧合并阈值，控制单条合成文本长度
    # 合并相邻段：遇标点或累计 2 段，或累计长度超阈值，进一步缩短单条 TTS 输入
    merged = []
    buf = []
    buf_chars = 0
    for idx, seg in enumerate(segments):
        buf.append((idx, seg))
        buf_chars += len(seg.text)
        if (seg.text and seg.text[-1] in punct) or len(buf) >= 2 or buf_chars >= max_chars:
            merged.append(buf)
            buf = []
            buf_chars = 0
    if buf:
        merged.append(buf)

    results: List[Segment] = []
    for group in merged:
        idxs = [i for i, _ in group]
        texts = [s.text for _, s in group]
        total_src_len = sum(max(len(t), 1) for t in texts)

        merged_text = " ".join(t.strip() for t in texts)
        protected_text, nums = protect_nums(merged_text)
        en = translate_fn(protected_text)
        en = restore(en, nums)

        words = en.split()
        alloc = []
        remaining = len(words)
        for i, t in enumerate(texts):
            if i == len(texts) - 1:
                take = remaining
            else:
                take = max(1, round(len(words) * len(t) / total_src_len))
                take = min(take, remaining - (len(texts) - i - 1))
            alloc.append(take)
            remaining -= take

        pos = 0
        for i, take in enumerate(alloc):
            seg_idx = idxs[i]
            seg = segments[seg_idx]
            piece = " ".join(words[pos:pos + take]).strip()
            pos += take
            piece_clean = dedupe_phrases(dedupe_repeats(rule_polish(clean_en(piece))))
            if replacement_rules:
                piece_clean = apply_replacements(piece_clean, replacement_rules)
            if lt_fn:
                try:
                    piece_clean = lt_fn(piece_clean).strip()
                except Exception:
                    pass
            if polish_fn:
                polished = polish_fn(piece_clean).strip()
                # 拒答或空输出则回退
                if not polished or polished.lower().startswith("i'm sorry"):
                    polished = piece_clean
                piece_clean = polished
            results.append(Segment(start=seg.start, end=seg.end, text=seg.text, translation=piece_clean))

    results.sort(key=lambda s: s.start)
    return results


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
    try:
        pb = Path(piper_bin)
        if pb.is_absolute():
            env["PATH"] = str(pb.parent) + os.pathsep + env.get("PATH", "")
    except Exception:
        pass
    espeak_dir = _find_espeak_data_dir(piper_bin)
    cmd = [
        piper_bin,
        "--model",
        str(model_path),
        "--output_file",
        str(output_wav),
    ]
    if espeak_dir is not None:
        cmd.extend(["--espeak_data", str(espeak_dir)])
    run_cmd(cmd, env=env, input_text=text + "\n")


def stretch_or_pad(
    audio: AudioSegment,
    target_ms: float,
    allow_speed_change: bool = True,
    max_speed: float = 1.1,
) -> AudioSegment:
    """
    若语音短于目标时长则补静音；超长则可微调倍速或截断，尽量贴合字幕时间。
    速度上限 max_speed（默认 1.2x），避免合成端被过度提速导致“飙语速”。
    """
    current = len(audio)
    delta = target_ms - current
    if delta >= 0:
        return audio + AudioSegment.silent(duration=delta)
    # audio is longer than target
    if not allow_speed_change or target_ms <= 0:
        return audio[: int(max(target_ms, 0))]
    speed = min(current / max(target_ms, 1), max_speed)
    # Increase speed to reduce duration (pitch will rise slightly)
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
    pad_to_ms: Optional[float] = None,
) -> AudioSegment:
    # 按时间轴合成（保留开头/段间静音，避免压缩时间线）
    if AudioSegment is None:
        raise SystemExit("pydub is required for TTS post-processing. Please install pydub.")

    work_dir.mkdir(parents=True, exist_ok=True)
    audio_chunks: List[AudioSegment] = []
    cursor_ms: float = 0.0
    for idx, seg in enumerate(segments, 1):
        if not seg.translation:
            raise ValueError("Missing translation text for TTS synthesis.")
        gap_ms = max(seg.start * 1000.0 - cursor_ms, 0.0)
        if gap_ms >= 5.0:
            audio_chunks.append(AudioSegment.silent(duration=int(round(gap_ms))))
            cursor_ms += gap_ms
        seg_wav = work_dir / f"seg_{idx:04d}.wav"
        synthesize_with_piper(seg.translation, model_path=model_path, output_wav=seg_wav, piper_bin=piper_bin)
        wav = AudioSegment.from_file(seg_wav)
        target_ms = (seg.end - seg.start) * 1000.0
        wav_aligned = stretch_or_pad(wav, target_ms=target_ms, allow_speed_change=allow_speed_change)
        audio_chunks.append(wav_aligned)
        cursor_ms = max(cursor_ms, seg.end * 1000.0)
    if not audio_chunks:
        raise ValueError("No audio chunks synthesized.")
    if pad_to_ms is not None and pad_to_ms > cursor_ms:
        audio_chunks.append(AudioSegment.silent(duration=int(round(pad_to_ms - cursor_ms))))
    combined = sum(audio_chunks[1:], audio_chunks[0])
    return combined


def save_audio(audio: AudioSegment, path: Path, sample_rate: int = 22050) -> None:
    # 输出统一采样率的最终配音文件
    path.parent.mkdir(parents=True, exist_ok=True)
    audio.export(path, format="wav", parameters=["-ar", str(sample_rate)])


def synthesize_segments_coqui(
    segments: List[Segment],
    tts,
    work_dir: Path,
    sample_rate: int,
    speaker: Optional[str] = None,
    language: Optional[str] = None,
    pad_to_ms: Optional[float] = None,
) -> AudioSegment:
    """
    使用 Coqui TTS（纯 Python，无外部 dylib 依赖）分段合成。
    """
    if AudioSegment is None:
        raise SystemExit("pydub is required for TTS post-processing. Please install pydub.")
    work_dir.mkdir(parents=True, exist_ok=True)

    audio_chunks: List[AudioSegment] = []
    cursor_ms: float = 0.0

    def tts_splits(text: str, max_len: int = 50) -> List[str]:
        """将过长英文按标点/空格拆分，避免单次合成过长导致重复或飙速。"""
        text = text.replace("\n", " ").strip()
        if len(text) <= max_len:
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
        # 再次保证每段不超长，必要时按空格硬切
        final_parts: List[str] = []
        for p in parts:
            if len(p) <= max_len:
                final_parts.append(p)
            else:
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
        return [p for p in final_parts if p.strip()]
    for idx, seg in enumerate(segments, 1):
        if not seg.translation:
            raise ValueError("Missing translation text for TTS synthesis.")
        target_ms = max((seg.end - seg.start) * 1000.0, 300.0)  # 最短 300ms，避免过短导致非自然
        gap_ms = max(seg.start * 1000.0 - cursor_ms, 0.0)
        if gap_ms >= 5.0:
            audio_chunks.append(AudioSegment.silent(duration=int(round(gap_ms))).set_frame_rate(sample_rate))
            cursor_ms += gap_ms

        # 若文本过长，先拆分子句，按长度比例分配时长再拼接，最后整体对齐
        parts = tts_splits(seg.translation, max_len=80)
        total_len = sum(len(p) for p in parts) or 1
        part_chunks: List[AudioSegment] = []
        for j, part in enumerate(parts):
            part_ms = max(target_ms * len(part) / total_len, 200.0)
            seg_wav = work_dir / f"seg_{idx:04d}_p{j}.wav"
            tts.tts_to_file(
                text=part,
                file_path=str(seg_wav),
                speaker=speaker,
                language=language,
            )
            wav = AudioSegment.from_file(seg_wav)
            wav_aligned = stretch_or_pad(wav, target_ms=part_ms, allow_speed_change=True, max_speed=1.1)
            part_chunks.append(wav_aligned)
        if not part_chunks:
            raise ValueError("No audio chunks synthesized for segment.")
        combined_part = sum(part_chunks[1:], part_chunks[0])
        combined_part = stretch_or_pad(combined_part, target_ms=target_ms, allow_speed_change=True, max_speed=1.1)
        combined_part = combined_part.set_frame_rate(sample_rate)
        audio_chunks.append(combined_part)
        cursor_ms = max(cursor_ms, seg.end * 1000.0)
    if not audio_chunks:
        raise ValueError("No audio chunks synthesized.")
    if pad_to_ms is not None and pad_to_ms > cursor_ms:
        audio_chunks.append(AudioSegment.silent(duration=int(round(pad_to_ms - cursor_ms))).set_frame_rate(sample_rate))
    combined = sum(audio_chunks[1:], audio_chunks[0])
    return combined


def build_coqui_tts(model_name: str, device: str = "auto"):
    """构建 Coqui TTS 接口，按需启用 GPU。"""
    try:
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
    return CoquiTTS(model_name=model_name, progress_bar=False, gpu=use_gpu)


def mux_video_audio(video_path: Path, audio_path: Path, output_path: Path) -> None:
    # 使用 ffmpeg 将新音频与原视频画面复合，视频流直接拷贝
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_path),
    ]
    run_cmd(cmd)


def burn_subtitles(video_path: Path, srt_path: Path, output_path: Path) -> None:
    # 将 SRT 以 mov_text 形式封装到 mp4 容器，避免重编码
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(srt_path),
        "-c",
        "copy",
        "-c:s",
        "mov_text",
        str(output_path),
    ]
    run_cmd(cmd)


def parse_args() -> argparse.Namespace:
    # 命令行参数定义：模型、设备、输入输出路径等
    p = argparse.ArgumentParser(description="Chinese video -> English dub + subtitles (offline-first)")
    p.add_argument("--video", type=Path, required=True, help="Input video file")
    p.add_argument("--output-dir", type=Path, default=Path("outputs"), help="Directory for outputs")
    # ASR (whisper.cpp)
    p.add_argument("--asr-backend", choices=["whispercpp"], default="whispercpp", help="ASR backend (default whisper.cpp)")
    p.add_argument(
        "--whispercpp-bin",
        type=Path,
        default=Path("bin/main"),
        help="Path to whisper.cpp executable (e.g., bin/main)",
    )
    p.add_argument(
        "--whispercpp-model",
        type=Path,
        default=Path("assets/models/ggml-medium-q5_0.bin"),
        help="Path to whisper.cpp ggml model",
    )
    p.add_argument("--whispercpp-threads", type=int, default=None, help="Threads for whisper.cpp (optional)")
    # MT
    p.add_argument("--mt-model", default="Helsinki-NLP/opus-mt-zh-en", help="MT model id")
    p.add_argument("--mt-device", default="auto", help="MT device: auto/cpu/cuda")
    # TTS
    p.add_argument("--piper-model", type=Path, default=Path("assets/models/en_US-amy-low.onnx"), help="Piper ONNX model path")
    p.add_argument("--piper-bin", default="piper", help="Path to piper executable")
    p.add_argument("--tts-backend", choices=["piper", "coqui"], default="piper", help="TTS backend (default piper)")
    p.add_argument("--coqui-model", default="tts_models/en/ljspeech/tacotron2-DDC", help="Coqui TTS model name")
    p.add_argument("--coqui-device", default="auto", help="Coqui TTS device: auto/cpu/cuda")
    p.add_argument("--coqui-speaker", default=None, help="Coqui speaker name if multi-speaker model")
    p.add_argument("--coqui-language", default=None, help="Coqui language code if multilingual model")
    # 音频预处理 / VAD
    p.add_argument("--denoise", action="store_true", help="Apply simple denoise (ffmpeg arnndn) when extracting audio")
    p.add_argument("--denoise-model", type=Path, default=None, help="Path to arnndn model (.onnx)")
    p.add_argument("--vad-enable", action="store_true", help="Enable whisper.cpp VAD")
    p.add_argument("--vad-thold", type=float, default=None, help="whisper.cpp VAD threshold (e.g., 0.6)")
    p.add_argument("--vad-min-dur", type=float, default=None, help="whisper.cpp VAD min silence duration seconds (e.g., 1.5)")
    # 英文润色小 LLM（可选）
    p.add_argument(
        "--en-polish-model",
        default=None,
        help="Optional English polish model id (text2text-generation); leave empty to disable",
    )
    p.add_argument("--en-polish-device", default="auto", help="Polish model device: auto/cpu/cuda")
    p.add_argument("--lt-enable", action="store_true", help="Enable LanguageTool grammar/punctuation/typo correction")
    p.add_argument("--replacements", type=Path, default=Path("replacements.json"), help="JSON replacements rules (pattern/replace)")
    p.add_argument("--sample-rate", type=int, default=16000, help="Audio sample rate for extraction and export")
    p.add_argument("--skip-tts", action="store_true", help="Skip TTS and mux; useful for ASR/MT only")
    p.add_argument("--bilingual-srt", action="store_true", help="Export bilingual SRT (zh|en)")
    return p.parse_args()


def main() -> None:
    # 主流程：准备 -> ASR -> 翻译 ->（可选）TTS -> 复合 -> 字幕封装
    args = parse_args()
    ensure_tool("ffmpeg")
    if not args.skip_tts and args.tts_backend == "piper":
        args.piper_bin = prepare_piper_bin(args.piper_bin)
        ensure_tool(args.piper_bin)
    if args.asr_backend == "whispercpp":
        ensure_tool(str(args.whispercpp_bin))

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    work_tts = output_dir / "tts_segments"
    work_asr_prefix = output_dir / "asr_whispercpp"

    audio_pcm = output_dir / "audio.wav"
    audio_json = output_dir / "audio.json"
    chs_srt = output_dir / "chs.srt"
    eng_srt = output_dir / "eng.srt"
    bi_srt = output_dir / "bilingual.srt"
    tts_wav = output_dir / "tts_full.wav"
    video_dub = output_dir / "output_en.mp4"
    video_sub = output_dir / "output_en_sub.mp4"

    print("[1/7] Extracting audio...")
    extract_audio(
        args.video,
        audio_pcm,
        sample_rate=args.sample_rate,
        denoise=args.denoise,
        denoise_model=args.denoise_model,
    )

    print("[2/7] Running ASR (whisper.cpp)...")
    segments = run_asr_whispercpp(
        audio_path=audio_pcm,
        whisper_bin=args.whispercpp_bin,
        model_path=args.whispercpp_model,
        output_prefix=work_asr_prefix,
        language="zh",
        threads=args.whispercpp_threads,
        vad_enable=args.vad_enable,
        vad_thold=args.vad_thold,
        vad_min_sil_ms=int(args.vad_min_dur * 1000) if args.vad_min_dur else None,
    )
    print(f"ASR segments: {len(segments)}")

    # Save raw ASR result as JSON for reference
    audio_json.write_text(
        json.dumps([seg.__dict__ for seg in segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_srt(chs_srt, segments, text_attr="text")

    print("[3/7] Building translator...")
    translate_fn = build_translator(args.mt_model, device=args.mt_device)
    polish_fn = None
    lt_fn = None
    if args.en_polish_model:
        print(f"[3b] Building English polisher: {args.en_polish_model}")
        polish_fn = build_polisher(args.en_polish_model, device=args.en_polish_device)
    if args.lt_enable:
        print("[3c] Building LanguageTool (grammar/punctuation/typo)...")
        lt_fn = build_languagetool()
    replacement_rules = load_replacements(args.replacements)

    print("[4/7] Translating segments...")
    seg_en = translate_segments(
        segments,
        translate_fn,
        polish_fn=polish_fn,
        lt_fn=lt_fn,
        replacement_rules=replacement_rules,
    )
    write_srt(eng_srt, seg_en, text_attr="translation")

    if args.bilingual_srt:
        bilingual_segments = []
        for seg in seg_en:
            bilingual_text = f"{seg.text}\n{seg.translation}"
            bilingual_segments.append(
                Segment(start=seg.start, end=seg.end, text=bilingual_text, translation=seg.translation)
            )
        write_srt(bi_srt, bilingual_segments, text_attr="text")

    if args.skip_tts:
        print("Skip TTS enabled; generated subtitles only.")
        return

    print(f"[5/7] Synthesizing TTS with {args.tts_backend}...")
    if args.tts_backend == "piper":
        combined_audio = synthesize_segments(
            seg_en,
            model_path=args.piper_model,
            work_dir=work_tts,
            piper_bin=args.piper_bin,
            allow_speed_change=True,
        )
    else:
        tts = build_coqui_tts(model_name=args.coqui_model, device=args.coqui_device)
        combined_audio = synthesize_segments_coqui(
            seg_en,
            tts=tts,
            work_dir=work_tts,
            sample_rate=args.sample_rate,
            speaker=args.coqui_speaker,
            language=args.coqui_language,
        )
    save_audio(combined_audio, tts_wav, sample_rate=args.sample_rate)

    print("[6/7] Muxing video with new audio...")
    mux_video_audio(args.video, tts_wav, video_dub)

    print("[7/7] Embedding subtitles...")
    burn_subtitles(video_dub, eng_srt, video_sub)

    print("Done.")
    print(f"Outputs in: {output_dir}")
    print(f"- ASR JSON:   {audio_json}")
    print(f"- CHS SRT:    {chs_srt}")
    print(f"- ENG SRT:    {eng_srt}")
    if args.bilingual_srt:
        print(f"- BI SRT:     {bi_srt}")
    print(f"- TTS audio:  {tts_wav}")
    print(f"- Video dub:  {video_dub}")
    print(f"- Video+sub:  {video_sub}")


if __name__ == "__main__":
    main()

