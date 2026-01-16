from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Optional


_ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200F\uFEFF]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

# Chinese punctuation we keep tight (no surrounding spaces)
_CJK_PUNCT = "，。！？；：、】【、"
_SPACE_AROUND_CJK_PUNCT_RE = re.compile(rf"\s*([{re.escape(_CJK_PUNCT)}])\s*")
_MULTISPACE_RE = re.compile(r"\s+")


_ASCII_TO_CJK_PUNCT = {
    ",": "，",
    ".": "。",
    "?": "？",
    "!": "！",
    ";": "；",
    ":": "：",
}


def load_asr_dict(path: Optional[Path]) -> Dict[str, str]:
    """
    Load an optional ASR normalization dictionary.

    Supported JSON formats:
    - {"错字": "正字", ...}
    - {"items": [{"src": "错字", "tgt": "正字"}, ...]}
    """
    if not path:
        return {}
    try:
        if not path.exists():
            return {}
        raw = path.read_text(encoding="utf-8", errors="ignore") or ""
        data = json.loads(raw)
        out: Dict[str, str] = {}
        if isinstance(data, dict):
            if all(isinstance(k, str) for k in data.keys()) and all(isinstance(v, str) for v in data.values()):
                out.update({k: v for k, v in data.items() if k and v is not None})
                return out
            items = data.get("items")
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    src = str(it.get("src") or "").strip()
                    tgt = str(it.get("tgt") or "").strip()
                    if src and tgt:
                        out[src] = tgt
        return out
    except Exception:
        return {}


def _apply_exact_replacements(text: str, mapping: Dict[str, str]) -> str:
    if not text or not mapping:
        return text
    out = text
    # replace longer keys first to reduce overlap issues
    keys = sorted([k for k in mapping.keys() if k], key=len, reverse=True)
    for k in keys:
        v = mapping.get(k)
        if not v:
            continue
        out = out.replace(k, v)
    return out


def normalize_asr_zh_text(
    text: str,
    *,
    to_simplified_fn=None,
    asr_dict: Optional[Dict[str, str]] = None,
) -> str:
    """
    Extremely low-risk Chinese ASR normalization (rule-based).

    Scope (no-content-change unless dictionary provides explicit mapping):
    - remove control / zero-width characters
    - normalize fullwidth spaces
    - collapse whitespace
    - normalize repeated punctuation
    - tighten spaces around Chinese punctuation
    - convert basic ASCII punctuation to Chinese punctuation
    - (optional) apply a small project dictionary for known ASR typos
    - (optional) convert Traditional -> Simplified via provided function
    """
    s = text or ""
    # 1) remove invisible noise
    s = _ZERO_WIDTH_RE.sub("", s)
    s = _CTRL_RE.sub("", s)
    # 2) normalize spaces
    s = s.replace("\u3000", " ")
    s = _MULTISPACE_RE.sub(" ", s).strip()
    # 3) basic punctuation normalize (ASCII -> CJK)
    for a, b in _ASCII_TO_CJK_PUNCT.items():
        s = s.replace(a, b)
    # 4) normalize repeated punctuation
    s = re.sub(r"[，]{2,}", "，", s)
    s = re.sub(r"[。]{2,}", "。", s)
    s = re.sub(r"[！]{2,}", "！", s)
    s = re.sub(r"[？]{2,}", "？", s)
    # 5) tighten spaces around CJK punct
    s = _SPACE_AROUND_CJK_PUNCT_RE.sub(r"\1", s)
    s = s.strip()
    # 6) optional dictionary (explicit, project-owned)
    if asr_dict:
        s = _apply_exact_replacements(s, asr_dict)
    # 7) optional t2s
    if to_simplified_fn is not None:
        try:
            s = str(to_simplified_fn(s))
        except Exception:
            pass
    return s


