from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Optional


def load_en_dict(path: Optional[Path]) -> Dict[str, str]:
    """
    Load an optional English replacement dictionary.

    Supported JSON formats:
    - {"foo": "bar", ...}
    - {"items": [{"src": "foo", "tgt": "bar"}, ...]}
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


def _safe_word_pattern(src: str) -> re.Pattern:
    """
    Build a cautious, whole-word-ish, case-insensitive pattern.
    - We avoid replacing inside longer words/numbers: use ASCII word boundary by negative/positive lookaround.
    - Works for phrases too (boundaries only on the ends).
    """
    s = re.escape(src)
    return re.compile(rf"(?i)(?<![A-Za-z0-9]){s}(?![A-Za-z0-9])")


def apply_en_replacements(text: str, mapping: Dict[str, str]) -> str:
    if not text or not mapping:
        return text
    out = text
    # longer keys first to reduce partial overlap issues
    keys = sorted([k for k in mapping.keys() if k], key=len, reverse=True)
    for k in keys:
        v = mapping.get(k)
        if not v:
            continue
        try:
            out = _safe_word_pattern(k).sub(v, out)
        except Exception:
            # best-effort: never crash the pipeline on a bad rule
            continue
    return out

