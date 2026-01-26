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
import platform
import struct
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from pydub import AudioSegment  # type: ignore
except ImportError:  # pragma: no cover - optional runtime dependency
    AudioSegment = None  # type: ignore

try:
    import torch  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    torch = None  # type: ignore

try:
    from opencc import OpenCC  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    OpenCC = None  # type: ignore

from scripts.asr_normalize import load_asr_dict, normalize_asr_zh_text


_opencc_t2s = None
_opencc_warned = False


def zh_to_simplified(text: str) -> str:
    """
    Convert Traditional Chinese to Simplified Chinese (t2s) for better consistency.
    If OpenCC is unavailable, return input unchanged (best-effort).
    """
    global _opencc_t2s, _opencc_warned
    if not text:
        return text
    if OpenCC is None:
        if not _opencc_warned:
            _opencc_warned = True
            print("[warn] OpenCC not available; cannot convert zh to Simplified. (install opencc-python-reimplemented)")
        return text
    try:
        if _opencc_t2s is None:
            _opencc_t2s = OpenCC("t2s")
        return _opencc_t2s.convert(text)
    except Exception:
        return text


def clean_zh_text(text: str) -> str:
    """
    Normalize Chinese subtitle text to reduce MT instability:
    - collapse whitespace/newlines
    - normalize common punctuation repeats
    """
    s = (text or "").replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # normalize repeated punct
    s = re.sub(r"[，,]{2,}", "，", s)
    s = re.sub(r"[。\.]{2,}", "。", s)
    s = re.sub(r"[！!]{2,}", "！", s)
    s = re.sub(r"[？\?]{2,}", "？", s)
    return s


_ZH_STOPWORDS = {
    "我们",
    "你们",
    "他们",
    "她们",
    "它们",
    "这个",
    "那个",
    "这里",
    "那里",
    "现在",
    "因为",
    "所以",
    "但是",
    "然后",
    "于是",
    "而且",
    "如果",
    "就会",
}


# 角色/身份类词：本期明确不做保护（误伤成本高，且对通用项目收益不稳定）
_ZH_ROLE_SUFFIXES = {
    "王",
    "后",
    "皇",
    "公主",
    "王子",
    "女巫",
    "法师",
    "将军",
    "大人",
    "老师",
    "教练",
    "队长",
    "领袖",
}


def _is_role_like_zh(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    if t in _ZH_ROLE_SUFFIXES:
        return True
    return any(t.endswith(x) for x in _ZH_ROLE_SUFFIXES)


def _extract_entity_candidates_from_segments(
    segments: List["Segment"],
    *,
    min_len: int = 2,
    max_len: int = 6,
    min_freq: int = 2,
    max_items: int = 30,
) -> List[str]:
    """
    Heuristic candidate extraction for proper nouns / key terms from Chinese subtitles.
    We aim for *stability* and low false positives, not perfect NER.
    """
    # EXTREMELY CONSERVATIVE extraction (本期策略：宁可少保护，也不打爆翻译)：
    # - only quoted/book/paren phrases
    # - only geo suffix patterns (国/城/镇/山/河/宫/岛/州/省/市/县/村/堡)
    # - explicitly exclude role/title words (王/公主/女巫/将军...)
    # - hard clamps: min_freq>=4, max_items<=8
    freq: Dict[str, int] = {}
    min_len = max(int(min_len or 2), 2)
    max_len = max(int(max_len or 6), min_len)
    min_freq = max(int(min_freq or 2), 4)
    max_items = min(int(max_items or 30), 8)

    suffix_pat = re.compile(r"[\u4e00-\u9fff]{1,10}(国|城|镇|山|河|宫|岛|州|省|市|县|村|堡)")
    for seg in segments:
        s = clean_zh_text(seg.text)
        for it in suffix_pat.finditer(s):
            cand = it.group(0)
            if cand in _ZH_STOPWORDS:
                continue
            if _is_role_like_zh(cand):
                continue
            freq[cand] = freq.get(cand, 0) + 2  # higher weight
        # quoted/bracketed phrases (higher confidence)
        for it in re.finditer(r"[“《（(]([\u4e00-\u9fff]{2,10})[”》）)]", s):
            cand = it.group(1)
            if cand in _ZH_STOPWORDS:
                continue
            if _is_role_like_zh(cand):
                continue
            if not (min_len <= len(cand) <= min(max_len, 8)):
                continue
            freq[cand] = freq.get(cand, 0) + 3

    items = [(k, v) for k, v in freq.items() if v >= min_freq]
    # prefer longer + higher freq
    items.sort(key=lambda kv: (kv[1], len(kv[0])), reverse=True)
    chosen: List[str] = []
    for k, _v in items:
        # avoid picking substrings of already chosen
        if any(k in c for c in chosen):
            continue
        if _is_role_like_zh(k):
            continue
        chosen.append(k)
        if len(chosen) >= max_items:
            break
    # replace longer first to reduce overlap issues
    chosen.sort(key=len, reverse=True)
    return chosen


def _idx_to_token(i: int) -> str:
    # Tokens without digits so protect_nums() won't touch them.
    # NOTE: some MT models (e.g., Marian) may strip punctuation like '@', so we use plain letters.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    a = alphabet[i % 26]
    b = alphabet[(i // 26) % 26]
    return f"ENT{b}{a}"


def build_auto_entity_map(
    segments: List["Segment"],
    translate_fn,
    *,
    min_len: int = 2,
    max_len: int = 6,
    min_freq: int = 2,
    max_items: int = 30,
) -> Dict[str, str]:
    """
    Build a per-task entity map (zh->en) without a pre-existing glossary, prioritizing TTS readability.
    We translate each candidate entity once, then protect it with placeholders during full-sentence MT.
    """
    cands = _extract_entity_candidates_from_segments(
        segments,
        min_len=min_len,
        max_len=max_len,
        min_freq=min_freq,
        max_items=max_items,
    )
    mapping: Dict[str, str] = {}
    # If the translated "entity" is too generic, protecting it usually hurts more than helps.
    _GENERIC_EN = {
        "people",
        "person",
        "woman",
        "man",
        "girl",
        "boy",
        "city",
        "country",
        "king",
        "queen",
        "princess",
        "witch",
        "leader",
        "money",
        "fire",
        "nature",
        "wall",
        "street",
        "teacher",
    }
    for c in cands:
        try:
            en = str(translate_fn(c)).strip()
        except Exception:
            en = ""
        en = re.sub(r"\s+", " ", en).strip()
        # strip any leaked CJK
        en = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", en).strip()
        if not en:
            continue
        if en.lower() in _GENERIC_EN:
            continue
        # avoid extremely long expansions
        if len(en) > 40:
            en = " ".join(en.split()[:8]).strip()
        mapping[c] = en
    return mapping


def protect_entities(
    text: str,
    entity_map: Dict[str, str],
    *,
    max_replacements: int = 1,
) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Replace zh entity occurrences with stable placeholder tokens and return the token->en mapping.
    """
    if not entity_map:
        return text, []
    out = text
    used: List[Tuple[str, str]] = []
    i = 0
    for zh in sorted(entity_map.keys(), key=len, reverse=True):
        if not zh or zh not in out:
            continue
        if max_replacements and len(used) >= int(max_replacements):
            break
        token = _idx_to_token(i)
        i += 1
        out = out.replace(zh, token)
        used.append((token, entity_map[zh]))
    return out, used


def restore_entities(text: str, used: List[Tuple[str, str]]) -> str:
    """
    Restore placeholder tokens back to English entity names.
    Some MT models may alter token punctuation/casing; we do a best-effort restore:
    - exact replace on token
    - also replace a stripped core token (e.g., '@@ENTAA@@' -> 'ENTAA') with word boundaries
    """
    out = text or ""
    for token, en in used:
        if not token:
            continue
        core = token.replace("@", "")
        # Handle token variants with @@ wrappers and optional whitespace, e.g. '@@GLS00@@' -> '@@ GLS00 @@'
        # Some LLMs may insert/remove spaces around special tokens.
        if core:
            out = re.sub(rf"@@\s*{re.escape(core)}\s*@@", en, out, flags=re.IGNORECASE)
        # Exact
        out = out.replace(token, en)
        # Core (case-insensitive), bounded to avoid accidental partial matches
        if core and core != token:
            out = re.sub(rf"(?<![A-Za-z0-9]){re.escape(core)}(?![A-Za-z0-9])", en, out, flags=re.IGNORECASE)
        # Also handle plain core even when token itself is plain (ENTAA)
        if core:
            out = re.sub(rf"(?<![A-Za-z0-9]){re.escape(core)}(?![A-Za-z0-9])", en, out, flags=re.IGNORECASE)
    return out


_END_STOPWORDS = {
    "and",
    "the",
    "of",
    "to",
    "in",
    "a",
    "an",
    "with",
    "from",
    "that",
    "which",
    "for",
    "as",
    "at",
    "on",
    "into",
    "onto",
    "over",
    "under",
    "by",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "can",
    "could",
    "should",
    "may",
    "might",
}

_START_STOPWORDS = {
    "and",
    "but",
    "or",
    "because",
    "when",
    "so",
    "then",
    "also",
    "however",
    "therefore",
    "thus",
    "yet",
}

_AUX_LIKE = {
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "can",
    "could",
    "should",
    "may",
    "might",
    "to",
}


def _count_word_tokens(tokens: List[str]) -> int:
    return sum(1 for t in tokens if re.match(r"^[A-Za-z0-9]", t or ""))


def tokenize_en(text: str) -> List[str]:
    # Keep simple punctuation as separate tokens; collapse whitespace.
    s = re.sub(r"\s+", " ", (text or "")).strip()
    if not s:
        return []
    return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?|[.,;:?!]", s)


def join_en_tokens(tokens: List[str]) -> str:
    out = ""
    for tok in tokens:
        if tok in {".", ",", ";", ":", "?", "!"}:
            out = out.rstrip() + tok
        else:
            out += (" " if out else "") + tok
    return out.strip()


def _piece_penalty(tokens: List[str]) -> int:
    if not tokens:
        return 10
    word_n = _count_word_tokens(tokens)
    first = tokens[0].lower()
    last = tokens[-1].lower()
    p = 0
    if first == "," or first in _START_STOPWORDS:
        p += 12
    if last in _END_STOPWORDS:
        p += 12
    if last in {",", ";", ":"}:
        p += 2
    # Avoid one-word lines (especially function/aux words like "Had"/"But")
    if word_n <= 1:
        p += 10
        w = ""
        for t in tokens:
            if re.match(r"^[A-Za-z0-9]", t or ""):
                w = t.lower()
                break
        if w in _START_STOPWORDS or w in _END_STOPWORDS or w in _AUX_LIKE:
            p += 10
    # Also discourage very short lines starting with conjunctions (e.g. "But anyone")
    if word_n <= 2 and first in _START_STOPWORDS:
        p += 10
    return p


def adjust_alloc_grammar(tokens: List[str], alloc: List[int], window: int = 3) -> List[int]:
    """
    Improve split boundaries to avoid broken English like '... and the' at end of a line.
    Local boundary search only; O(n*window) and very cheap.
    """
    if not tokens or not alloc:
        return alloc
    # Convert alloc to boundary positions.
    bounds: List[int] = []
    pos = 0
    for n in alloc[:-1]:
        pos += max(0, n)
        bounds.append(pos)
    # adjust each boundary
    for bi, b in enumerate(bounds):
        best_b = b
        best_score = 1_000_000
        for shift in range(-window, window + 1):
            nb = b + shift
            if nb <= 0 or nb >= len(tokens):
                continue
            # ensure each piece has at least 1 token when possible
            left_start = 0 if bi == 0 else bounds[bi - 1]
            right_end = len(tokens) if bi == len(bounds) - 1 else bounds[bi + 1]
            if nb - left_start < 1:
                continue
            if right_end - nb < 1:
                continue
            left = tokens[left_start:nb]
            right = tokens[nb:right_end]
            score = _piece_penalty(left) + _piece_penalty(right)
            # prefer splitting at punctuation boundaries
            if left and left[-1] in {".", "!", "?"}:
                score -= 2
            if left and left[-1] in {",", ";", ":"}:
                score -= 1
            if right and right[0] == ",":
                score += 2
            if score < best_score:
                best_score = score
                best_b = nb
        bounds[bi] = best_b
    # rebuild alloc from bounds
    new_alloc: List[int] = []
    prev = 0
    for b in bounds:
        new_alloc.append(max(0, b - prev))
        prev = b
    new_alloc.append(max(0, len(tokens) - prev))
    return new_alloc


def split_translation_by_src_lengths(src_texts: List[str], en_text: str) -> List[str]:
    """
    Split an English translation back into N pieces (N=len(src_texts)) with grammar-aware boundaries.
    """
    n = len(src_texts)
    if n <= 1:
        return [en_text.strip()]
    tokens = tokenize_en(en_text)
    if not tokens:
        return [""] * n
    # Prefer allocating a few tokens per piece when we have enough tokens.
    # This avoids overly short lines like "But anyone" or "You're gonna get".
    if len(tokens) >= 3 * n:
        min_take = 3
    elif len(tokens) >= 2 * n:
        min_take = 2
    else:
        min_take = 1
    total_src_len = sum(max(len(t), 1) for t in src_texts) or 1
    alloc: List[int] = []
    remaining = len(tokens)
    for i, t in enumerate(src_texts):
        if i == n - 1:
            take = remaining
        else:
            take = max(min_take, round(len(tokens) * len(t) / total_src_len))
            take = min(take, max(0, remaining - (n - i - 1)))
        alloc.append(take)
        remaining -= take
    alloc = adjust_alloc_grammar(tokens, alloc, window=3)
    pieces: List[str] = []
    pos2 = 0
    for take in alloc:
        chunk = tokens[pos2 : pos2 + take]
        pos2 += take
        pieces.append(join_en_tokens(chunk))
    # Ensure length
    while len(pieces) < n:
        pieces.append("")
    if len(pieces) > n:
        pieces = pieces[:n]
    return pieces


@dataclass
class Segment:
    # 单条语音片段的时间戳与文本（text 为中文，translation 为英文）
    start: float
    end: float
    text: str
    translation: Optional[str] = None


def load_glossary(path: Optional[Path]) -> List[Dict]:
    """
    Load a simple glossary JSON:
      { "items": [ { "src": "...", "tgt": "...", "aliases": [...], "forbidden": [...], "note": "..." } ] }
    """
    if not path:
        return []
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "{}")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out: List[Dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            src = str(it.get("src") or "").strip()
            if not src:
                continue
            out.append(
                {
                    "id": str(it.get("id") or ""),
                    "src": src,
                    "tgt": str(it.get("tgt") or "").strip(),
                    "aliases": [str(x).strip() for x in (it.get("aliases") or []) if str(x).strip()],
                    "forbidden": [str(x).strip() for x in (it.get("forbidden") or []) if str(x).strip()],
                    "note": str(it.get("note") or "").strip(),
                    "scope": str(it.get("scope") or "global").strip() or "global",
                }
            )
        return out
    except Exception:
        return []


def apply_glossary_to_segments(segments: List[Segment], glossary: List[Dict]) -> Dict[str, int]:
    """
    Enforce terminology by normalizing english outputs when the corresponding Chinese `src` term
    is present in the segment's Chinese text.

    Strategy (safe, offline):
    - Only act when `term.src` appears in `seg.text` (Chinese).
    - Replace any `aliases`/`forbidden` occurrences in `seg.translation` with `term.tgt`.
    - Do not attempt to "insert" missing terms (we only normalize existing variants).
    """
    stats = {"segments": len(segments), "term_hits": 0, "normalized": 0, "forbidden_hits": 0, "missing": 0}
    if not segments or not glossary:
        return stats

    def _ci_contains(hay: str, needle: str) -> bool:
        return needle.lower() in hay.lower()

    for seg in segments:
        if not seg.translation:
            continue
        zh = seg.text or ""
        en = seg.translation or ""
        for term in glossary:
            src = term.get("src") or ""
            tgt = term.get("tgt") or ""
            if not src or not tgt:
                continue
            if src not in zh:
                continue
            stats["term_hits"] += 1
            replaced_any = False
            # forbidden / aliases normalization
            for bad in (term.get("forbidden") or []) + (term.get("aliases") or []):
                bad = str(bad).strip()
                if not bad:
                    continue
                if _ci_contains(en, bad):
                    if bad in (term.get("forbidden") or []):
                        stats["forbidden_hits"] += 1
                    en2 = re.sub(re.escape(bad), tgt, en, flags=re.IGNORECASE)
                    if en2 != en:
                        en = en2
                        replaced_any = True
            if replaced_any:
                stats["normalized"] += 1
            # missing: src appears but neither tgt nor any alias appears
            if not _ci_contains(en, tgt):
                aliases = [str(x) for x in (term.get("aliases") or []) if str(x).strip()]
                if not any(_ci_contains(en, a) for a in aliases):
                    stats["missing"] += 1
        seg.translation = en
    return stats


def run_cmd(
    cmd: List[str],
    check: bool = True,
    env: Optional[dict] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """运行子进程命令，失败时抛出详细日志，便于排查。"""
    # Use Windows-friendly quoting for human-readable error messages.
    try:
        pretty = subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(cmd)
    except Exception:
        pretty = " ".join(str(x) for x in cmd)
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        env=env,
        input=input_text,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed (rc={proc.returncode}): {pretty}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _ffprobe_display_wh(p: Path) -> Optional[tuple[int, int]]:
    """
    Return display width/height with rotation handled (ffprobe rotation tag or displaymatrix).
    This keeps PlayRes consistent with what players actually render, ensuring WYSIWYG for subtitle placement.
    """
    try:
        from pipelines.lib.media_probe import ffprobe_display_wh

        return ffprobe_display_wh(p)
    except Exception:
        return None


def _probe_duration_s(p: Path) -> Optional[float]:
    """Best-effort duration probe: prefer ffprobe, fall back to parsing ffmpeg output."""
    try:
        from pipelines.lib.media_probe import probe_duration_s

        return probe_duration_s(p)
    except Exception:
        return None


def ensure_tool(name: str) -> None:
    """
    Ensure external tools (ffmpeg / piper / whisper.cpp) are available.
    In docker we often pass absolute paths; for those, shutil.which() won't work reliably.
    Also validate CPU architecture for piper to avoid late "rosetta/ld-linux-x86-64" crashes.
    """
    # Absolute/relative path case
    if os.sep in name or name.startswith("."):
        p = Path(name)
        if not p.is_absolute():
            # Resolve relative to repo root to avoid cwd sensitivity
            repo_root = Path(__file__).resolve().parents[1]
            p = repo_root / p
        if not (p.exists() and os.access(str(p), os.X_OK)):
            raise SystemExit(f"Missing required tool: {name}. Please install and retry.")
        # Arch sanity check for piper binary (common pitfall on Apple Silicon / ARM containers)
        if p.name == "piper":
            _ensure_elf_arch_compatible(p)
        return

    # PATH lookup case
    resolved = shutil.which(name)
    if not resolved:
        raise SystemExit(f"Missing required tool: {name}. Please install and retry.")
    if Path(resolved).name == "piper":
        _ensure_elf_arch_compatible(Path(resolved))


# Cache: configured piper_bin -> runnable piper path (possibly under /tmp)
_PIPER_BIN_CACHE: dict[str, str] = {}


def _resolve_path_like(name: str) -> Path:
    """Resolve a configured tool path to an absolute path (repo-root relative if needed)."""
    p = Path(name)
    if not p.is_absolute():
        repo_root = Path(__file__).resolve().parents[1]
        p = repo_root / p
    return p


def prepare_piper_bin(configured: str) -> str:
    """
    Make sure piper is *runnable* inside containers.

    On Docker Desktop/macOS bind mounts, files can have +x but still fail at runtime with:
      bash: .../piper: Permission denied
    which usually means the mount is `noexec`.

    Fix: copy the whole piper folder to /tmp and execute from there.
    """
    if configured in _PIPER_BIN_CACHE:
        return _PIPER_BIN_CACHE[configured]

    # PATH lookup: we can't easily relocate; just return as-is
    if os.sep not in configured and not configured.startswith("."):
        _PIPER_BIN_CACHE[configured] = configured
        return configured

    p = _resolve_path_like(configured)
    if not p.exists():
        _PIPER_BIN_CACHE[configured] = configured
        return configured

    # Sanity check arch early (gives clearer error than "rosetta/ld-linux")
    if p.name == "piper":
        _ensure_elf_arch_compatible(p)

    # Fast runnable check: try to exec --help; noexec shows up as EACCES
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

    # Permission denied: likely noexec mount. Copy full folder to /tmp and run from there.
    src_dir = p.parent
    tag = hashlib.sha1(str(src_dir).encode("utf-8")).hexdigest()[:10]
    dst_dir = Path("/tmp") / f"piper_{tag}"
    try:
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        dst_piper = dst_dir / p.name
        # Ensure exec bits on the copied binaries
        for bin_name in ("piper", "piper_phonemize"):
            bp = dst_dir / bin_name
            if bp.exists():
                bp.chmod(0o755)
        # Tiny delay to avoid edge-case inode race in some FS
        time.sleep(0.02)
        _PIPER_BIN_CACHE[configured] = str(dst_piper)
        return str(dst_piper)
    except Exception:
        # Fall back to original; caller will raise a clearer error from run_cmd
        _PIPER_BIN_CACHE[configured] = str(p)
        return str(p)


def _find_espeak_data_dir(piper_bin: str) -> Optional[Path]:
    """
    Find an espeak-ng-data directory that contains `phontab`.

    Piper phonemization needs espeak-ng-data. Depending on how piper is packaged/mounted,
    the data may live next to the binary (e.g. /app/local_bin/piper/espeak-ng-data)
    or in an image-bundled location (e.g. /app/bin/piper/espeak-ng-data).
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


def _ensure_elf_arch_compatible(binary: Path) -> None:
    """
    Validate ELF e_machine matches runtime arch for Linux containers.
    - x86_64: e_machine=62
    - aarch64: e_machine=183
    """
    try:
        data = binary.read_bytes()
        if data[:4] != b"\x7fELF":
            return
        ei_data = data[5]  # 1=little, 2=big
        endian = "<" if ei_data == 1 else ">"
        e_machine = struct.unpack(endian + "H", data[18:20])[0]
        host = platform.machine().lower()
        expected = None
        if host in {"x86_64", "amd64"}:
            expected = 62
        elif host in {"aarch64", "arm64"}:
            expected = 183
        if expected and e_machine != expected:
            raise SystemExit(
                "Piper 二进制架构与当前容器架构不匹配，无法运行。\n"
                f"- 容器架构: {platform.machine()}\n"
                f"- piper: {binary}\n"
                f"- ELF e_machine: {e_machine}（x86_64=62, aarch64=183）\n"
                "解决方案（二选一）：\n"
                "1) 提供 linux-aarch64(arm64) 版 piper，替换到 /app/local_bin/piper/piper（或配置 piper_bin 指向它）\n"
                "2) 把整个 backend 容器切换为 linux/amd64，并同时使用 amd64 的 whisper-cli/ffmpeg（保持同一架构）\n"
                "（不会将 lite 流程降级为 quality；只是工具架构需要一致）"
            )
    except SystemExit:
        raise
    except Exception:
        # If anything goes wrong, don't block execution; run_cmd will surface errors.
        return


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
    return [p for p in final_parts if p.strip()]


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


def run_asr_whispercpp(
    audio_path: Path,
    whisper_bin: Path,
    model_path: Path,
    output_prefix: Path,
    language: str = "zh",
    threads: Optional[int] = None,
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


def build_translator(model_id: str, device: str = "auto", cache_dir: Optional[str] = None, offline: bool = False):
    # 构建翻译 pipeline（Marian/NLLB/M2M100），自动选择 CPU/GPU
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline

    if device == "auto":
        if torch is not None and torch.cuda.is_available():  # type: ignore
            device = 0
        else:
            device = -1

    # Fully-local mode: never attempt network. Require the model to exist in cache/local path.
    model_path_or_id = model_id
    # Allow passing a local directory path (containing config.json) as mt_model.
    # Resolve relative paths against repo root (/app) for container runs to avoid
    # mistakenly treating "assets/..." as a HuggingFace model id.
    repo_root = Path(__file__).resolve().parents[1]
    raw = Path(model_id)
    looks_like_path = raw.is_absolute() or str(model_id).startswith((".", os.sep, "assets" + os.sep)) or (os.sep in str(model_id))
    if looks_like_path:
        p = raw if raw.is_absolute() else (repo_root / raw)
        if not p.exists():
            raise RuntimeError(
                "全离线模式下未找到本地翻译模型目录。\n"
                f"- 期望目录: {p}\n"
                "请确认你已把 Marian(opus-mt) 模型文件放入该目录。"
            )
        if p.is_dir() and not (p / "config.json").exists():
            raise RuntimeError(
                "翻译模型目录已存在，但缺少必要文件（至少需要 config.json）。\n"
                f"- 目录: {p}\n"
                "Marian(opus-mt) 最小文件集建议包含：\n"
                "- config.json\n"
                "- pytorch_model.bin\n"
                "- source.spm\n"
                "- target.spm\n"
                "- vocab.json\n"
                "- tokenizer_config.json\n"
                "（可选：generation_config.json；不需要：tf_model.h5、rust_model.ot）"
            )
        model_path_or_id = str(p)

    # Only do HF-cache structure checks when model_id is a HuggingFace repo id (contains "/")
    if offline and cache_dir and ("/" in str(model_id)) and isinstance(model_path_or_id, str) and model_path_or_id == model_id:
        # Best-effort check to fail fast with actionable message when the HF cache is empty.
        cache_root = Path(cache_dir)
        hf_dir = cache_root / f"models--{model_id.replace('/', '--')}"
        has_snapshot = False
        if hf_dir.exists():
            snap = hf_dir / "snapshots"
            if snap.exists():
                for cfg in snap.glob("*/config.json"):
                    has_snapshot = True
                    break
        if not has_snapshot:
            raise RuntimeError(
                "全离线模式下未找到本地翻译模型缓存。\n"
                f"- 需要的模型: {model_id}\n"
                f"- 当前 mt_cache_dir: {cache_dir}\n"
                "请先把模型放到 HF 缓存目录，例如：\n"
                f"- {cache_dir}/models--{model_id.replace('/', '--')}/snapshots/<hash>/config.json\n"
                "（也可以把 --mt-model 直接改成一个本地目录路径，该目录内包含 config.json）。"
            )

    tokenizer = AutoTokenizer.from_pretrained(
        model_path_or_id,
        cache_dir=cache_dir,
        local_files_only=offline,
    )
    model = AutoModelForSeq2SeqLM.from_pretrained(
        model_path_or_id,
        cache_dir=cache_dir,
        local_files_only=offline,
    )
    translator = pipeline(
        "translation",
        model=model,
        tokenizer=tokenizer,
        device=device,
        src_lang="zh",
        tgt_lang="en",
        model_kwargs={"local_files_only": offline},
    )

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
    # In fully offline runs, require local cache/path. Callers should ensure env offline flags are set.
    offline = os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    polish = pipeline("text2text-generation", model=model_id, device=device, model_kwargs={"local_files_only": offline})

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
    entity_map: Optional[Dict[str, str]] = None,
    *,
    sentence_unit_enable: bool = False,
    sentence_unit_min_chars: int = 12,
    sentence_unit_max_chars: int = 60,
    sentence_unit_max_segs: int = 3,
    sentence_unit_max_gap_s: float = 0.6,
    sentence_unit_boundary_punct: str = "。！？!?.,",
    sentence_unit_break_words: Optional[List[str]] = None,
) -> List[Segment]:
    # 合并翻译再回填；数字占位保护；规则级英文清理；可选词典替换/LT/外部润色（默认关闭）
    punct = set(sentence_unit_boundary_punct or "。！？!?.,")

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

    # -----------------------------
    # Sentence-unit merge strategy
    # -----------------------------
    def build_groups() -> List[List[Tuple[int, Segment]]]:
        # Backward compatible behavior when disabled: keep the old heuristic exactly.
        if not sentence_unit_enable:
            legacy_punct = set("。！？!?.,")
            max_chars = 40  # legacy heuristic
            merged: List[List[Tuple[int, Segment]]] = []
            buf: List[Tuple[int, Segment]] = []
            buf_chars = 0
            for idx, seg in enumerate(segments):
                buf.append((idx, seg))
                buf_chars += len(seg.text)
                if (seg.text and seg.text[-1] in legacy_punct) or len(buf) >= 2 or buf_chars >= max_chars:
                    merged.append(buf)
                    buf = []
                    buf_chars = 0
            if buf:
                merged.append(buf)
            return merged

        boundary = set(sentence_unit_boundary_punct or "。！？!?.,")
        break_words = [w for w in (sentence_unit_break_words or []) if str(w).strip()]
        merged2: List[List[Tuple[int, Segment]]] = []
        buf2: List[Tuple[int, Segment]] = []
        buf_chars2 = 0

        # Structural threshold: prefer merging fragments until we have something "translatable enough".
        # This is intentionally lightweight and heuristic-based (min-risk).
        _verbish = re.compile(r"(是|有|在|要|会|能|可以|必须|应该|觉得|认为|知道|说|讲|问|去|来|做|看到|听到)")
        def _has_predicate(s: str) -> bool:
            ss = (s or "").strip()
            if not ss:
                return False
            return bool(_verbish.search(ss))

        for idx, seg in enumerate(segments):
            # Discourse break words: if a new segment starts with "但/而/于是/然后..." we tend to start a new unit.
            if buf2 and break_words:
                head = (seg.text or "").strip()
                if any(head.startswith(w) for w in break_words):
                    merged2.append(buf2)
                    buf2 = []
                    buf_chars2 = 0

            # If there is a large gap, do not merge across it (min-risk).
            if buf2:
                prev = buf2[-1][1]
                gap = float(seg.start) - float(prev.end)
                if gap > float(sentence_unit_max_gap_s):
                    merged2.append(buf2)
                    buf2 = []
                    buf_chars2 = 0

            buf2.append((idx, seg))
            buf_chars2 += len(seg.text or "")

            # Stop conditions (min-risk): size, length, punctuation when enough context.
            last_text = (seg.text or "").strip()
            enough = (buf_chars2 >= int(sentence_unit_min_chars)) or _has_predicate("".join((s.text or "") for _i, s in buf2))
            hit_boundary = bool(last_text) and (last_text[-1] in boundary)
            too_many = len(buf2) >= int(sentence_unit_max_segs)
            too_long = buf_chars2 >= int(sentence_unit_max_chars)
            if too_many or too_long or (enough and hit_boundary):
                merged2.append(buf2)
                buf2 = []
                buf_chars2 = 0

        if buf2:
            merged2.append(buf2)
        return merged2

    merged = build_groups()

    results: List[Segment] = []
    for group in merged:
        idxs = [i for i, _ in group]
        texts = [s.text for _, s in group]

        # Pre-translation normalization (low-risk): collapse whitespace and normalize repeated punctuation.
        merged_text = clean_zh_text(" ".join(t.strip() for t in texts))
        # 1) protect entities (no digits in tokens to avoid protect_nums affecting them)
        protected_text = merged_text
        ent_used: List[Tuple[str, str]] = []
        if entity_map:
            protected_text, ent_used = protect_entities(protected_text, entity_map)
        # 2) protect numbers
        protected_text, nums = protect_nums(protected_text)
        # 3) translate
        en = translate_fn(protected_text)
        # 4) restore
        en = restore(en, nums)
        en = restore_entities(en, ent_used)

        pieces = split_translation_by_src_lengths(texts, str(en))
        for i, piece in enumerate(pieces):
            seg_idx = idxs[i] if i < len(idxs) else idxs[-1]
            seg = segments[seg_idx]
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
    # Ensure piper can find its sibling helper (piper_phonemize) after relocation to /tmp
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
    # Note: piper reads text from STDIN. Some builds do NOT support a --text argument.
    run_cmd(cmd, env=env, input_text=text + "\n")


def stretch_or_pad(
    audio: AudioSegment,
    target_ms: float,
    allow_speed_change: bool = True,
    max_speed: float = 1.08,
    align_mode: str = "resample",
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
) -> AudioSegment:
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
    split_len: int = 80,
    max_speed: float = 1.08,
    align_mode: str = "resample",
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
    missing_tr = 0
    for idx, seg in enumerate(segments, 1):
        raw_tts = getattr(seg, "tts", None)
        if raw_tts is None:
            raw_tts = seg.translation
        if raw_tts is None:
            raw_tts = ""
        seg.translation = clean_tts_text(str(raw_tts))
        target_ms = max((seg.end - seg.start) * 1000.0, 300.0)  # 最短 300ms，避免过短导致非自然
        gap_ms = max(seg.start * 1000.0 - cursor_ms, 0.0)
        if gap_ms >= 5.0:
            audio_chunks.append(AudioSegment.silent(duration=int(round(gap_ms))).set_frame_rate(sample_rate))
            cursor_ms += gap_ms
        if not seg.translation:
            missing_tr += 1
            # Fully stripped (e.g. non-English). Produce silence instead of synthesizing junk.
            audio_chunks.append(AudioSegment.silent(duration=target_ms).set_frame_rate(sample_rate))
            cursor_ms = max(cursor_ms, seg.end * 1000.0)
            continue

        # 若文本过长，先拆分子句，按长度比例分配时长再拼接，最后整体对齐
        parts = tts_splits(seg.translation, max_len=split_len)
        total_len = sum(len(p) for p in parts) or 1
        part_chunks: List[AudioSegment] = []
        for j, part in enumerate(parts):
            part_ms = max(target_ms * len(part) / total_len, 200.0)
            part_clean = clean_tts_text(part)
            if not part_clean:
                part_chunks.append(AudioSegment.silent(duration=part_ms).set_frame_rate(sample_rate))
                continue
            seg_wav = work_dir / f"seg_{idx:04d}_p{j}.wav"
            tts.tts_to_file(
                text=part_clean,
                file_path=str(seg_wav),
                speaker=speaker,
                language=language,
            )
            wav = AudioSegment.from_file(seg_wav)
            wav_aligned = stretch_or_pad(wav, target_ms=part_ms, allow_speed_change=True, max_speed=max_speed, align_mode=align_mode)
            part_chunks.append(wav_aligned)
        if not part_chunks:
            raise ValueError("No audio chunks synthesized for segment.")
        combined_part = sum(part_chunks[1:], part_chunks[0])
        combined_part = stretch_or_pad(combined_part, target_ms=target_ms, allow_speed_change=True, max_speed=max_speed, align_mode=align_mode)
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
        except Exception:
            pass

        # In some PyInstaller(onefile) builds, `gruut` is present but its `VERSION` data file
        # is missing from the bundle, causing:
        #   FileNotFoundError: ...\_MEIxxxx\gruut\VERSION
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
    return CoquiTTS(model_name=model_name, progress_bar=False, gpu=use_gpu)


def mux_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    sync_strategy: str = "slow",
    slow_max_ratio: float = 1.10,
    threshold_s: float = 0.05,
    tail_pad_max_s: float = 0.80,
    erase_subtitle_enable: bool = False,
    erase_subtitle_method: str = "delogo",
    erase_subtitle_coord_mode: str = "ratio",
    erase_subtitle_x: float = 0.0,
    erase_subtitle_y: float = 0.78,
    erase_subtitle_w: float = 1.0,
    erase_subtitle_h: float = 0.22,
    erase_subtitle_blur_radius: int = 12,
) -> None:
    # v2: delegate to shared library
    from pipelines.lib.ffmpeg_mux import mux_video_audio as _mux

    return _mux(
        video_path,
        audio_path,
        output_path,
        sync_strategy=sync_strategy,
        slow_max_ratio=slow_max_ratio,
        threshold_s=threshold_s,
        tail_pad_max_s=tail_pad_max_s,
        erase_subtitle_enable=erase_subtitle_enable,
        erase_subtitle_method=erase_subtitle_method,
        erase_subtitle_coord_mode=erase_subtitle_coord_mode,
        erase_subtitle_x=erase_subtitle_x,
        erase_subtitle_y=erase_subtitle_y,
        erase_subtitle_w=erase_subtitle_w,
        erase_subtitle_h=erase_subtitle_h,
        erase_subtitle_blur_radius=erase_subtitle_blur_radius,
    )


def burn_subtitles(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    *,
    font_name: str = "Arial",
    font_size: int = 18,
    outline: int = 1,
    shadow: int = 0,
    margin_v: int = 24,
    alignment: int = 2,
    place_enable: bool = False,
    place_coord_mode: str = "ratio",
    place_x: float = 0.0,
    place_y: float = 0.78,
    place_w: float = 1.0,
    place_h: float = 0.22,
) -> None:
    # v2: delegate to shared library
    from pipelines.lib.subtitles_burn import burn_subtitles as _burn

    return _burn(
        video_path,
        srt_path,
        output_path,
        font_name=font_name,
        font_size=font_size,
        outline=outline,
        shadow=shadow,
        margin_v=margin_v,
        alignment=alignment,
        place_enable=place_enable,
        place_coord_mode=place_coord_mode,
        place_x=place_x,
        place_y=place_y,
        place_w=place_w,
        place_h=place_h,
    )


def _read_srt_texts(path: Path) -> List[str]:
    """
    Minimal SRT reader that returns text blocks in order.
    Used for resume-from flows to restore translations from eng.srt.
    """
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.rstrip("\n\r") for ln in raw.splitlines()]
    out: List[str] = []
    i = 0
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        # index line
        i += 1
        if i >= len(lines):
            break
        # timing line
        if "-->" in (lines[i] or ""):
            i += 1
        else:
            i += 1
            continue
        text_lines: List[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        out.append("\n".join(text_lines).strip())
    return out


def _read_srt_texts_ordered(path: Path) -> List[str]:
    """
    Read SRT texts in order (block order), preserving multi-line blocks joined with '\n'.
    This is used for review overrides where we assume 1:1 ordering with existing segments.
    """
    return _read_srt_texts(path)


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
    p.add_argument("--mt-cache-dir", default=None, help="Transformers cache dir for MT models (offline mode)")
    p.add_argument("--offline", action="store_true", help="Fully offline: disable any model downloads (Transformers/HF)")
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
    p.add_argument("--vad-model", type=Path, default=None, help="Path to whisper.cpp VAD model file (required when --vad-enable)")
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
    # Sentence-unit merge (general, min-risk). Disabled by default; enable explicitly.
    p.add_argument("--sentence-unit-enable", action="store_true", help="Enable sentence-unit merge before translation (min-risk)")
    p.add_argument("--sentence-unit-min-chars", type=int, default=12, help="Min Chinese chars in a unit before closing on punctuation")
    p.add_argument("--sentence-unit-max-chars", type=int, default=60, help="Max Chinese chars per unit (conservative)")
    p.add_argument("--sentence-unit-max-segs", type=int, default=3, help="Max segments per unit")
    p.add_argument("--sentence-unit-max-gap-s", type=float, default=0.6, help="Do not merge across gaps larger than this (seconds)")
    p.add_argument("--sentence-unit-boundary-punct", type=str, default="。！？!?.,", help="Punctuation that can close a unit when long enough")
    p.add_argument(
        "--sentence-unit-break-words",
        type=str,
        default="",
        help="Comma-separated Chinese discourse break words to avoid merging across (e.g. 但,而,于是,然后).",
    )
    # ASR text normalization (extremely low-risk). Enabled by config; can be disabled for debugging.
    p.add_argument("--asr-normalize-enable", action="store_true", help="Enable low-risk Chinese ASR text normalization")
    p.add_argument(
        "--asr-normalize-dict",
        type=Path,
        default=Path("assets/asr_normalize/asr_zh_dict.json"),
        help="Optional JSON dictionary for known ASR typos (defaults to an empty dict file)",
    )
    # Auto entity protection (optional): extract zh candidates -> translate once -> protect with placeholders.
    p.add_argument("--entity-protect-enable", action="store_true", help="Enable auto entity protection (proper nouns/terms) during MT")
    p.add_argument("--entity-protect-min-len", type=int, default=2, help="Min CJK length for entity candidates")
    p.add_argument("--entity-protect-max-len", type=int, default=6, help="Max CJK length for entity candidates")
    p.add_argument("--entity-protect-min-freq", type=int, default=2, help="Min frequency for entity candidates across segments")
    p.add_argument("--entity-protect-max-items", type=int, default=30, help="Max number of entity candidates per task")
    p.add_argument(
        "--resume-from",
        choices=["asr", "mt", "tts", "mux"],
        default=None,
        help="Resume from a specific stage, reusing existing artifacts under output-dir",
    )
    # Light-weight stability controls
    p.add_argument("--min-sub-dur", type=float, default=1.5, help="Minimum subtitle duration (seconds); will extend short segments")
    p.add_argument("--tts-split-len", type=int, default=80, help="Max characters per TTS chunk before splitting")
    p.add_argument("--tts-speed-max", type=float, default=1.08, help="Max speed-up factor when aligning audio")
    p.add_argument(
        "--tts-align-mode",
        choices=["atempo", "resample"],
        default="resample",
        help="How to align TTS to time budget: atempo=better pitch preservation (recommended), resample=faster but may change timbre",
    )

    # ------------------------------------------------------------
    # P2-ASR (experimental in lite): accept flags for compatibility
    # ------------------------------------------------------------
    # These flags are used by quality.yaml defaults and by TaskManager pass-through.
    # The lite pipeline currently does NOT implement these enhancements; we accept
    # the args to avoid hard failures and will print a warning when enabled.
    p.add_argument("--asr-preprocess-enable", action="store_true", help="(compat) enable ASR audio preprocess (NOT implemented in lite)")
    p.add_argument("--asr-preprocess-loudnorm", action="store_true", help="(compat) loudnorm during ASR preprocess (NOT implemented in lite)")
    p.add_argument("--asr-preprocess-highpass", type=int, default=None, help="(compat) highpass Hz for preprocess (NOT implemented in lite)")
    p.add_argument("--asr-preprocess-lowpass", type=int, default=None, help="(compat) lowpass Hz for preprocess (NOT implemented in lite)")
    p.add_argument("--asr-preprocess-ffmpeg-extra", type=str, default="", help="(compat) extra ffmpeg filter args (NOT implemented in lite)")

    p.add_argument("--asr-merge-short-enable", action="store_true", help="(compat) merge very short ASR segments (NOT implemented in lite)")
    p.add_argument("--asr-merge-min-dur-s", type=float, default=0.8, help="(compat) min dur for merge-short (NOT implemented in lite)")
    p.add_argument("--asr-merge-min-chars", type=int, default=6, help="(compat) min chars for merge-short (NOT implemented in lite)")
    p.add_argument("--asr-merge-max-gap-s", type=float, default=0.25, help="(compat) max gap for merge-short (NOT implemented in lite)")
    p.add_argument("--asr-merge-max-group-chars", type=int, default=120, help="(compat) max group chars for merge-short (NOT implemented in lite)")
    p.add_argument("--asr-merge-save-debug", action="store_true", help="(compat) save debug files for merge-short (NOT implemented in lite)")

    p.add_argument("--asr-llm-fix-enable", action="store_true", help="(compat) enable ASR typo fix via LLM (NOT implemented in lite)")
    p.add_argument("--asr-llm-fix-mode", type=str, default="suspect", help="(compat) LLM fix mode (NOT implemented in lite)")
    p.add_argument("--asr-llm-fix-max-items", type=int, default=60, help="(compat) LLM fix max items (NOT implemented in lite)")
    p.add_argument("--asr-llm-fix-min-chars", type=int, default=12, help="(compat) LLM fix min chars (NOT implemented in lite)")
    p.add_argument("--asr-llm-fix-save-debug", action="store_true", help="(compat) save LLM fix debug (NOT implemented in lite)")
    p.add_argument("--asr-llm-fix-model", type=str, default="", help="(compat) LLM model id/name (NOT implemented in lite)")
    # Subtitle burn-in style (hard-sub)
    p.add_argument("--sub-font-name", default="Arial", help="Subtitle font name for hard-burn (best-effort)")
    p.add_argument("--sub-font-size", type=int, default=18, help="Subtitle font size for hard-burn")
    p.add_argument("--sub-outline", type=int, default=1, help="Subtitle outline thickness")
    p.add_argument("--sub-shadow", type=int, default=0, help="Subtitle shadow")
    p.add_argument("--sub-margin-v", type=int, default=24, help="Subtitle vertical margin (pixels)")
    p.add_argument("--sub-alignment", type=int, default=2, help="ASS Alignment (2=bottom-center)")
    return p.parse_args()


def main() -> None:
    # 主流程：准备 -> ASR -> 翻译 ->（可选）TTS -> 复合 -> 字幕封装
    args = parse_args()
    mode = getattr(args, "mode", "lite")
    if mode in {"quality", "online"}:
        raise SystemExit(f"Mode '{mode}' not supported in this pipeline. Use lite or select another pipeline.")
    ensure_tool("ffmpeg")

    # Compatibility warnings (do not fail the run).
    if getattr(args, "asr_preprocess_enable", False):
        print("[warn] lite pipeline: --asr-preprocess-* flags are accepted for compatibility but are NOT implemented; ignoring.")
    if getattr(args, "asr_merge_short_enable", False):
        print("[warn] lite pipeline: --asr-merge-short-* flags are accepted for compatibility but are NOT implemented; ignoring.")
    if getattr(args, "asr_llm_fix_enable", False):
        print("[warn] lite pipeline: --asr-llm-fix-* flags are accepted for compatibility but are NOT implemented; ignoring.")
    resume_from = getattr(args, "resume_from", None)
    need_asr = resume_from is None or resume_from == "asr"
    need_tts = (not args.skip_tts) and (resume_from is None or resume_from in {"asr", "mt", "tts"})
    if need_tts and args.tts_backend == "piper":
        # Make piper runnable even when bind mount is `noexec` (Docker Desktop on macOS).
        args.piper_bin = prepare_piper_bin(args.piper_bin)
        ensure_tool(args.piper_bin)
    if need_asr and args.asr_backend == "whispercpp":
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

    # ---- Stage 1: extract audio (or reuse)
    if resume_from is None or resume_from == "asr":
        print("[1/7] Extracting audio...")
        extract_audio(
            args.video,
            audio_pcm,
            sample_rate=args.sample_rate,
            denoise=args.denoise,
            denoise_model=args.denoise_model,
        )
    else:
        if not audio_pcm.exists():
            raise SystemExit(f"resume_from={resume_from} 但缺少 {audio_pcm}")

    # Used to pad final synthesized audio so ffmpeg -shortest won't truncate the video.
    audio_total_ms: Optional[float] = None
    if AudioSegment is not None and audio_pcm.exists():
        try:
            audio_total_ms = float(len(AudioSegment.from_file(audio_pcm)))
        except Exception:
            audio_total_ms = None

    # ---- Stage 2: ASR (or reuse)
    if resume_from is None or resume_from == "asr":
        print("[2/7] Running ASR (whisper.cpp)...")
        segments = run_asr_whispercpp(
            audio_path=audio_pcm,
            whisper_bin=args.whispercpp_bin,
            model_path=args.whispercpp_model,
            output_prefix=work_asr_prefix,
            language="zh",
            threads=args.whispercpp_threads,
            vad_enable=args.vad_enable,
            vad_model=args.vad_model,
            vad_thold=args.vad_thold,
            vad_min_sil_ms=int(args.vad_min_dur * 1000) if args.vad_min_dur else None,
        )
        print(f"ASR segments: {len(segments)}")
        segments = enforce_min_duration(segments, min_duration=args.min_sub_dur)
        # Low-risk ASR normalization (best-effort). This runs on ASR output only (not on review overrides).
        asr_dict = load_asr_dict(getattr(args, "asr_normalize_dict", None)) if getattr(args, "asr_normalize_enable", False) else {}
        for seg in segments:
            seg.text = normalize_asr_zh_text(seg.text, to_simplified_fn=zh_to_simplified, asr_dict=asr_dict)
        audio_json.write_text(
            json.dumps([seg.__dict__ for seg in segments], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_srt(chs_srt, segments, text_attr="text")
    else:
        if not audio_json.exists():
            raise SystemExit(f"resume_from={resume_from} 但缺少 {audio_json}")
        data = json.loads(audio_json.read_text(encoding='utf-8', errors='ignore') or "[]")
        segments = [Segment(**item) for item in data]
        asr_dict = load_asr_dict(getattr(args, "asr_normalize_dict", None)) if getattr(args, "asr_normalize_enable", False) else {}
        for seg in segments:
            seg.text = normalize_asr_zh_text(seg.text, to_simplified_fn=zh_to_simplified, asr_dict=asr_dict)
        if not chs_srt.exists():
            write_srt(chs_srt, segments, text_attr="text")

    # ---- Stage 3/4: MT (or reuse)
    if resume_from is None or resume_from in {"asr", "mt"}:
        print("[3/7] Building translator...")
        # If review provides an override CHS SRT, use it as the MT source (keeping timestamps).
        if getattr(args, "chs_override_srt", None):
            ov = Path(getattr(args, "chs_override_srt"))
            if ov.exists():
                texts = _read_srt_texts_ordered(ov)
                if texts:
                    for i, seg in enumerate(segments):
                        if i < len(texts) and texts[i].strip():
                            seg.text = zh_to_simplified(texts[i].strip())
                    # Also overwrite chs.srt so artifacts reflect the review.
                    try:
                        chs_srt.write_text(zh_to_simplified(ov.read_text(encoding="utf-8", errors="ignore")), encoding="utf-8")
                    except Exception:
                        pass
        offline = bool(args.offline) or os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
        translate_fn = build_translator(args.mt_model, device=args.mt_device, cache_dir=args.mt_cache_dir, offline=offline)
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
        if getattr(args, "sentence_unit_enable", False):
            print(
                "[4/7] Sentence-unit merge enabled: "
                f"min_chars={getattr(args, 'sentence_unit_min_chars', 12)}, "
                f"max_chars={getattr(args, 'sentence_unit_max_chars', 60)}, "
                f"max_segs={getattr(args, 'sentence_unit_max_segs', 3)}, "
                f"max_gap_s={getattr(args, 'sentence_unit_max_gap_s', 0.6)}, "
                f"boundary_punct={getattr(args, 'sentence_unit_boundary_punct', '。！？!?.,')}"
            )
        # Auto entity protection (optional): build a per-task mapping (zh -> en) once, then reuse for all MT calls.
        entity_map = None
        if getattr(args, "entity_protect_enable", False):
            try:
                entity_map = build_auto_entity_map(
                    segments,
                    translate_fn,
                    min_len=int(getattr(args, "entity_protect_min_len", 2) or 2),
                    max_len=int(getattr(args, "entity_protect_max_len", 6) or 6),
                    min_freq=int(getattr(args, "entity_protect_min_freq", 2) or 2),
                    max_items=int(getattr(args, "entity_protect_max_items", 30) or 30),
                )
                print(f"[4a] Entity protection enabled: {len(entity_map)} candidates")
            except Exception as exc:
                print(f"[warn] Failed to build entity map; continuing without protection: {exc}")
                entity_map = None

        break_words_raw = str(getattr(args, "sentence_unit_break_words", "") or "")
        break_words = [w.strip() for w in re.split(r"[,，\s]+", break_words_raw) if w.strip()]

        seg_en = translate_segments(
            segments,
            translate_fn,
            polish_fn=polish_fn,
            lt_fn=lt_fn,
            replacement_rules=replacement_rules,
            entity_map=entity_map,
            sentence_unit_enable=bool(getattr(args, "sentence_unit_enable", False)),
            sentence_unit_min_chars=int(getattr(args, "sentence_unit_min_chars", 12) or 12),
            sentence_unit_max_chars=int(getattr(args, "sentence_unit_max_chars", 60) or 60),
            sentence_unit_max_segs=int(getattr(args, "sentence_unit_max_segs", 3) or 3),
            sentence_unit_max_gap_s=float(getattr(args, "sentence_unit_max_gap_s", 0.6) or 0.6),
            sentence_unit_boundary_punct=str(getattr(args, "sentence_unit_boundary_punct", "。！？!?.,") or "。！？!?.,"),
            sentence_unit_break_words=break_words,
        )
        glossary = load_glossary(getattr(args, "glossary", None))
        if glossary:
            stats = apply_glossary_to_segments(seg_en, glossary)
            print(f"[4b] Glossary applied: {stats}")
        write_srt(eng_srt, seg_en, text_attr="translation")
    else:
        override = getattr(args, "eng_override_srt", None)
        eng_path = Path(override) if override else eng_srt
        if not eng_path.exists():
            raise SystemExit(f"resume_from={resume_from} 但缺少 {eng_path}")
        en_texts = _read_srt_texts(eng_path)
        seg_en = segments
        for i, seg in enumerate(seg_en):
            if i < len(en_texts):
                seg.translation = en_texts[i]
            else:
                seg.translation = seg.translation or ""
        # Keep eng.srt in sync for later embed
        try:
            if eng_path != eng_srt:
                eng_srt.write_text(eng_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass

    if args.bilingual_srt:
        bilingual_segments = []
        for seg in seg_en:
            bilingual_text = f"{zh_to_simplified(seg.text)}\n{seg.translation}"
            bilingual_segments.append(
                Segment(start=seg.start, end=seg.end, text=bilingual_text, translation=seg.translation)
            )
        write_srt(bi_srt, bilingual_segments, text_attr="text")

    if args.skip_tts:
        print("Skip TTS enabled; generated subtitles only.")
        return

    # ---- Stage 5: TTS (or reuse)
    if resume_from is None or resume_from in {"asr", "mt", "tts"}:
        print(f"[5/7] Synthesizing TTS with {args.tts_backend}...")
        if args.tts_backend == "piper":
            combined_audio = synthesize_segments(
                seg_en,
                model_path=args.piper_model,
                work_dir=work_tts,
                piper_bin=args.piper_bin,
                allow_speed_change=True,
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
                work_dir=work_tts,
                sample_rate=args.sample_rate,
                speaker=args.coqui_speaker,
                language=args.coqui_language,
                split_len=args.tts_split_len,
                max_speed=args.tts_speed_max,
                align_mode=getattr(args, "tts_align_mode", "resample"),
                pad_to_ms=audio_total_ms,
            )
        save_audio(combined_audio, tts_wav, sample_rate=args.sample_rate)
    else:
        if not tts_wav.exists():
            raise SystemExit(f"resume_from=mux 但缺少 {tts_wav}")

    print("[6/7] Muxing video with new audio...")
    mux_video_audio(args.video, tts_wav, video_dub)

    print("[7/7] Embedding subtitles...")
    srt_to_burn = bi_srt if getattr(args, "bilingual_srt", False) and bi_srt.exists() else eng_srt
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
    )

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

