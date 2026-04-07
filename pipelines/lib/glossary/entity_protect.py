from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


def _idx_to_token(i: int) -> str:
    # Tokens without digits so protect_nums() won't touch them.
    # NOTE: some MT models (e.g., Marian) may strip punctuation like '@', so we use plain letters.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    a = alphabet[i % 26]
    b = alphabet[(i // 26) % 26]
    return f"ENT{b}{a}"


def build_auto_entity_map(
    segments: List[Any],
    translate_fn,
    *,
    min_len: int = 2,
    max_len: int = 6,
    min_freq: int = 2,
    max_items: int = 30,
    clean_zh_text_fn=None,
    extract_candidates_fn=None,
    is_role_like_zh_fn=None,
    zh_stopwords: set | None = None,
) -> Dict[str, str]:
    """
    Build a per-task entity map (zh->en) without a pre-existing glossary, prioritizing TTS readability.
    We translate each candidate entity once, then protect it with placeholders during full-sentence MT.

    This module is a thin extraction layer: callers may optionally pass in the project's existing
    candidate-extraction helpers to keep behavior identical.
    """
    if extract_candidates_fn is None:
        # If caller doesn't provide extraction logic, do nothing (min-risk).
        return {}

    cands = extract_candidates_fn(
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
        "sir",
        "mr",
        "mrs",
        "miss",
        "doctor",
        "professor",
        "captain",
        "chief",
        "emperor",
        "your majesty",
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
        en_low = en.lower().strip(" .,!?:;\"'")
        if en_low in _GENERIC_EN:
            continue
        if len(en_low.split()) <= 2 and en_low in {"sir", "mr", "mrs", "miss", "doctor", "professor", "chief", "captain"}:
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
    max_replacements: int = 2,
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

