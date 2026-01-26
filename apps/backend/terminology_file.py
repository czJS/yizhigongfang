from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple


_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,10}")


def _safe_json_loads(raw: str) -> Dict[str, Any]:
    try:
        obj = json.loads(raw or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def suggest_zh_terms(
    zh_lines: Sequence[str],
    *,
    min_len: int = 2,
    max_len: int = 8,
    min_freq: int = 3,
    max_items: int = 30,
    stopwords: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Extremely simple, universal term suggestion:
    - Count CJK runs (2..10) and pick frequent ones.
    - No NER, no video-specific rules.
    """
    sw = {s.strip() for s in (stopwords or []) if str(s or "").strip()}
    freq: Dict[str, int] = {}
    for ln in zh_lines:
        s = str(ln or "").strip()
        for m in _CJK_RUN_RE.finditer(s):
            tok = m.group(0)
            if not (min_len <= len(tok) <= max_len):
                continue
            if tok in sw:
                continue
            freq[tok] = freq.get(tok, 0) + 1
    items = [(k, v) for k, v in freq.items() if v >= int(min_freq)]
    items.sort(key=lambda kv: (kv[1], len(kv[0])), reverse=True)
    out: List[Dict[str, Any]] = []
    for k, v in items:
        if len(out) >= int(max_items):
            break
        # de-dup substrings (prefer longer)
        if any(k in (it.get("src") or "") for it in out):
            continue
        out.append({"src": k, "freq": int(v), "tgt": ""})
    return out


def build_terminology_doc(
    *,
    zh_lines: Sequence[str],
    glossary_items: Optional[Sequence[Dict[str, Any]]] = None,
    topic_hint: str = "",
    stopwords: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    A human-editable terminology file:
    - suggested_terms: auto suggestions (editable)
    - force_translate: user-enforced mappings for this task (takes precedence over suggestions)
    """
    return {
        "version": 1,
        "created_at": int(time.time()),
        "topic_hint": str(topic_hint or ""),
        "glossary_items_n": len(glossary_items or []),
        "suggested_terms": suggest_zh_terms(zh_lines, stopwords=stopwords),
        "force_translate": [],  # user edits: [{"src":"张三","tgt":"Zhang San"}]
        "notes": "Edit force_translate to enforce per-task terms. Keep this file universal; avoid video-specific hacks.",
    }


def load_terminology_doc(path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    return _safe_json_loads(raw)


def extract_force_translate(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    items = doc.get("force_translate") or []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        src = str(it.get("src") or "").strip()
        tgt = str(it.get("tgt") or "").strip()
        if not src or not tgt:
            continue
        out.append({"src": src, "tgt": tgt})
    return out


def merge_force_terms_into_glossary(
    glossary: Optional[List[Dict[str, Any]]],
    force_terms: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """
    Convert per-task force terms into glossary items (prompt-level and placeholder-level can reuse it).
    Keep it additive and conflict-safe (task-level overrides win by src exact match).
    """
    base = list(glossary or [])
    if not force_terms:
        return base
    existing = {str(it.get("src") or "").strip(): i for i, it in enumerate(base) if isinstance(it, dict)}
    for ft in force_terms:
        src = str(ft.get("src") or "").strip()
        tgt = str(ft.get("tgt") or "").strip()
        if not src or not tgt:
            continue
        item = {"id": f"task:{src}", "src": src, "tgt": tgt, "scope": "task"}
        if src in existing:
            base[existing[src]] = {**base[existing[src]], **item}
        else:
            base.append(item)
    return base


