import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AsrFixItem:
    id: str
    src: str
    tgt: str
    note: str
    scope: str


@dataclass
class EnFixItem:
    id: str
    src: str
    tgt: str
    note: str
    scope: str


def default_doc() -> Dict[str, Any]:
    # v2: remove ZH->EN (terms) chain; keep only:
    # - ZH->ZH (asr_fixes)
    # - EN->EN (en_fixes)
    return {"version": 1, "updated_at": 0, "asr_fixes": [], "en_fixes": [], "settings": {}}


def _norm_list_str(raw: Any) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("must be a list")
    out: List[str] = []
    for x in raw:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


def _normalize_asr_fix(raw: Dict[str, Any], idx: int) -> AsrFixItem:
    src = str(raw.get("src") or "").strip()
    tgt = str(raw.get("tgt") or "").strip()
    if not src or not tgt:
        raise ValueError(f"ruleset.asr_fix #{idx}: missing src/tgt")
    item_id = str(raw.get("id") or f"a{idx:04d}")
    note = str(raw.get("note") or "").strip()
    scope = str(raw.get("scope") or "global").strip() or "global"
    return AsrFixItem(id=item_id, src=src, tgt=tgt, note=note, scope=scope)


def _normalize_en_fix(raw: Dict[str, Any], idx: int) -> EnFixItem:
    src = str(raw.get("src") or "").strip()
    tgt = str(raw.get("tgt") or "").strip()
    if not src or not tgt:
        raise ValueError(f"ruleset.en_fix #{idx}: missing src/tgt")
    item_id = str(raw.get("id") or f"e{idx:04d}")
    note = str(raw.get("note") or "").strip()
    scope = str(raw.get("scope") or "global").strip() or "global"
    return EnFixItem(id=item_id, src=src, tgt=tgt, note=note, scope=scope)


def validate_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError("ruleset doc must be an object")
    version = int(doc.get("version") or 1)
    updated_at = int(doc.get("updated_at") or 0)

    # v2: `terms` (ZH->EN) is removed. For backward compatibility we accept it in input
    # but we drop it at runtime. If a legacy term looks like a ZH->ZH correction
    # (Chinese target), migrate it into asr_fixes.
    terms_raw = doc.get("terms")
    if terms_raw is not None and not isinstance(terms_raw, list):
        raise ValueError("ruleset.terms must be a list (legacy)")

    asr_raw = doc.get("asr_fixes")
    if asr_raw is None:
        asr_raw = []
    if not isinstance(asr_raw, list):
        raise ValueError("ruleset.asr_fixes must be a list")

    en_raw = doc.get("en_fixes")
    if en_raw is None:
        en_raw = []
    if not isinstance(en_raw, list):
        raise ValueError("ruleset.en_fixes must be a list")
    settings = doc.get("settings") or {}
    if not isinstance(settings, dict):
        raise ValueError("ruleset.settings must be an object")

    asr_fixes: List[Dict[str, Any]] = []
    seen_asr_ids = set()
    for i, raw in enumerate(asr_raw, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"ruleset.asr_fix #{i}: must be an object")
        it = _normalize_asr_fix(raw, i)
        if it.id in seen_asr_ids:
            raise ValueError(f"duplicate ruleset.asr_fix id: {it.id}")
        seen_asr_ids.add(it.id)
        asr_fixes.append({"id": it.id, "src": it.src, "tgt": it.tgt, "note": it.note, "scope": it.scope})

    def _looks_like_zh(s: str) -> bool:
        return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", str(s or "")))

    # Migrate legacy terms into asr_fixes when it is clearly a ZH->ZH correction.
    try:
        if isinstance(terms_raw, list):
            existing_by_src = {str(it.get("src") or "").strip(): True for it in asr_fixes if str(it.get("src") or "").strip()}
            for i, raw in enumerate(terms_raw, 1):
                if not isinstance(raw, dict):
                    continue
                src = str(raw.get("src") or "").strip()
                tgt = str(raw.get("tgt") or "").strip()
                if not src or not tgt:
                    continue
                if not _looks_like_zh(tgt):
                    continue
                if existing_by_src.get(src):
                    continue
                # Keep a stable id prefix for migrated items.
                item_id = str(raw.get("id") or f"a_migrated_{i:04d}")
                note = str(raw.get("note") or "").strip()
                scope = str(raw.get("scope") or "global").strip() or "global"
                asr_fixes.append({"id": item_id, "src": src, "tgt": tgt, "note": note, "scope": scope})
                existing_by_src[src] = True
    except Exception:
        pass

    en_fixes: List[Dict[str, Any]] = []
    seen_en_ids = set()
    for i, raw in enumerate(en_raw, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"ruleset.en_fix #{i}: must be an object")
        it = _normalize_en_fix(raw, i)
        if it.id in seen_en_ids:
            raise ValueError(f"duplicate ruleset.en_fix id: {it.id}")
        seen_en_ids.add(it.id)
        en_fixes.append({"id": it.id, "src": it.src, "tgt": it.tgt, "note": it.note, "scope": it.scope})

    return {
        "version": version,
        "updated_at": updated_at,
        "asr_fixes": asr_fixes,
        "en_fixes": en_fixes,
        "settings": settings,
    }


def load_ruleset(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return default_doc()
    try:
        doc = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "{}")
        if not isinstance(doc, dict):
            return default_doc()
        validated = validate_doc(doc)
        if not validated.get("updated_at"):
            validated["updated_at"] = int(time.time())
        return validated
    except Exception:
        return default_doc()


def save_ruleset(path: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    validated = validate_doc(doc)
    validated["updated_at"] = int(time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            bak = path.with_suffix(path.suffix + f".bak.{validated['updated_at']}")
            bak.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass
    path.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
    return validated


def merge_rulesets(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge override into base (both already validated or best-effort objects).
    Conflict key: `src` (human identity). Override replaces base when src matches.
    """
    b = validate_doc(base or {})
    o = validate_doc(override or {}) if override else default_doc()

    # Merge ASR fixes by src
    asr_by_src: Dict[str, Dict[str, Any]] = {}
    for it in (b.get("asr_fixes") or []):
        src = str(it.get("src") or "").strip()
        if src:
            asr_by_src[src] = dict(it)
    for it in (o.get("asr_fixes") or []):
        src = str(it.get("src") or "").strip()
        if src:
            asr_by_src[src] = dict(it)
    asr_fixes = list(asr_by_src.values())

    # Settings: shallow merge
    settings = dict(b.get("settings") or {})
    settings.update(dict(o.get("settings") or {}))

    # Merge EN fixes by src
    en_by_src: Dict[str, Dict[str, Any]] = {}
    for it in (b.get("en_fixes") or []):
        src = str(it.get("src") or "").strip()
        if src:
            en_by_src[src] = dict(it)
    for it in (o.get("en_fixes") or []):
        src = str(it.get("src") or "").strip()
        if src:
            en_by_src[src] = dict(it)
    en_fixes = list(en_by_src.values())

    merged = {
        "version": int(b.get("version") or 1),
        "updated_at": int(time.time()),
        "asr_fixes": asr_fixes,
        "en_fixes": en_fixes,
        "settings": settings,
    }

    # IMPORTANT:
    # IDs are user-irrelevant and may collide across layers (global/template/override),
    # which would make the merged doc invalid under validate_doc()'s uniqueness checks.
    # Re-generate IDs deterministically by letting validate_doc assign them by order.
    try:
        for it in merged.get("asr_fixes") or []:
            if isinstance(it, dict):
                it.pop("id", None)
        for it in merged.get("en_fixes") or []:
            if isinstance(it, dict):
                it.pop("id", None)
        return validate_doc(merged)
    except Exception:
        return merged


def ruleset_to_glossary_doc(ruleset: Dict[str, Any]) -> Dict[str, Any]:
    rs = validate_doc(ruleset or {})
    items = []
    # v2: glossary is used for ZH->ZH corrections (asr_fixes) only.
    for it in rs.get("asr_fixes") or []:
        items.append(
            {
                "id": str(it.get("id") or ""),
                "src": str(it.get("src") or "").strip(),
                "tgt": str(it.get("tgt") or "").strip(),
                "note": str(it.get("note") or "").strip(),
                "scope": str(it.get("scope") or "global"),
                "aliases": [],
                "forbidden": [],
            }
        )
    return {"version": int(rs.get("version") or 1), "updated_at": int(rs.get("updated_at") or 0), "items": items}


def glossary_doc_to_ruleset_asr_fixes(glossary_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Accept a GlossaryDoc shape and convert to ruleset.asr_fixes (ZH->ZH corrections).
    """
    if not isinstance(glossary_doc, dict):
        return []
    items = glossary_doc.get("items")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for i, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            continue
        src = str(raw.get("src") or "").strip()
        tgt = str(raw.get("tgt") or "").strip()
        if not src:
            continue
        if not tgt:
            continue
        out.append(
            {
                "id": str(raw.get("id") or f"a{i:04d}"),
                "src": src,
                "note": str(raw.get("note") or "").strip(),
                "scope": str(raw.get("scope") or "global").strip() or "global",
                "tgt": tgt,
            }
        )
    return out


def ruleset_to_asr_dict(ruleset: Dict[str, Any]) -> Dict[str, str]:
    rs = validate_doc(ruleset or {})
    out: Dict[str, str] = {}
    for it in rs.get("asr_fixes") or []:
        src = str(it.get("src") or "").strip()
        tgt = str(it.get("tgt") or "").strip()
        if src and tgt:
            out[src] = tgt
    return out


def ruleset_to_en_dict(ruleset: Dict[str, Any]) -> Dict[str, str]:
    rs = validate_doc(ruleset or {})
    out: Dict[str, str] = {}
    for it in rs.get("en_fixes") or []:
        src = str(it.get("src") or "").strip()
        tgt = str(it.get("tgt") or "").strip()
        if src and tgt:
            out[src] = tgt
    return out

