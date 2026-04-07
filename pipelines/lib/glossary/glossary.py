from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


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


def apply_glossary_to_segments(segments: List[Any], glossary: List[Dict]) -> Dict[str, int]:
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
        tr = getattr(seg, "translation", None)
        if not tr:
            continue
        zh = getattr(seg, "text", "") or ""
        en = tr or ""
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
        setattr(seg, "translation", en)
    return stats

