import re
from typing import Optional


_WS_RE = re.compile(r"\s+")
_BRACKET_RE = re.compile(r"(\([^)]*\)|\[[^\]]*\]|\{[^}]*\}|（[^）]*）|【[^】]*】|《[^》]*》)")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+")


def _normalize_ws(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").replace("\n", " ")).strip()


def _strip_leading_bullets(s: str) -> str:
    # "- xxx", "1) xxx", "1. xxx"
    s = re.sub(r"^\s*([-–•]+)\s*", "", s)
    s = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s)
    return s.strip()


def _cleanup_units(text: str) -> str:
    """
    Minimal, universal unit normalization to improve TTS readability.
    Keep it conservative to avoid wrong expansions.
    """
    t = text
    # Ranges: 3-5 -> 3 to 5 (avoid reading "three dash five")
    t = re.sub(r"(\d)\s*-\s*(\d)", r"\1 to \2", t)
    # Percent
    t = re.sub(r"(\d)\s*%+", r"\1 percent", t)
    # Temperature
    t = re.sub(r"(\d)\s*°\s*C\b", r"\1 degrees Celsius", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s*°\s*F\b", r"\1 degrees Fahrenheit", t, flags=re.IGNORECASE)
    # Common units
    t = re.sub(r"(\d)\s*km\b", r"\1 kilometers", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s*mph\b", r"\1 miles per hour", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s*kph\b", r"\1 kilometers per hour", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s*kg\b", r"\1 kilograms", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s*g\b", r"\1 grams", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s*cm\b", r"\1 centimeters", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s*mm\b", r"\1 millimeters", t, flags=re.IGNORECASE)
    return t


def build_tts_script(
    en: str,
    *,
    strict_clean: bool = True,
    fallback: Optional[callable] = None,
) -> str:
    """
    Generate a "TTS稿" from an English subtitle line.

    Design goals:
    - Deterministic & universal rules (no video-specific hardcoding)
    - Low risk: never raise, always returns a string
    - Keeps punctuation for pauses (.,!? ,;:)
    """
    t = str(en or "")
    # Common literal
    t = t.replace("&", " and ")
    # Strip bracketed asides (stage directions, annotations)
    t = _BRACKET_RE.sub(" ", t)
    t = _normalize_ws(t)
    t = _strip_leading_bullets(t)
    if strict_clean:
        # Remove obvious URLs/emails (TTS tends to read them badly).
        t = re.sub(r"\bhttps?://\S+\b", " ", t, flags=re.IGNORECASE)
        t = re.sub(r"\bwww\.\S+\b", " ", t, flags=re.IGNORECASE)
        t = re.sub(r"\b\S+@\S+\b", " ", t)
        t = _cleanup_units(t)
        # Remove any CJK/fullwidth leftovers (keep it English-only for stable TTS).
        t = _CJK_RE.sub(" ", t)
        t = _normalize_ws(t)
    # Ensure a sentence-ending punctuation for prosody
    if t and not re.search(r"[.!?]$", t):
        t = t + "."
    # Optional shared fallback cleaner (e.g. scripts.asr_translate_tts.clean_tts_text)
    if fallback:
        try:
            t2 = str(fallback(t) or "")
            t = _normalize_ws(t2) or _normalize_ws(t)
        except Exception:
            t = _normalize_ws(t)
    return t


