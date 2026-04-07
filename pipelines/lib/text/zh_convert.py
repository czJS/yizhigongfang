from __future__ import annotations

from typing import Optional

try:
    from opencc import OpenCC  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    OpenCC = None  # type: ignore


_opencc_t2s: Optional["OpenCC"] = None
_opencc_warned = False


def zh_to_simplified(text: str) -> str:
    """
    Convert Traditional Chinese to Simplified Chinese (t2s) for better consistency.

    Best-effort:
    - If OpenCC is unavailable, returns input unchanged.
    - If conversion fails, returns input unchanged.
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

