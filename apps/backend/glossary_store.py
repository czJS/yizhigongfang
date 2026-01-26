import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class GlossaryItem:
    id: str
    src: str
    tgt: str
    aliases: List[str]
    forbidden: List[str]
    note: str
    scope: str  # e.g. global/project/video


def default_doc() -> Dict[str, Any]:
    return {"version": 1, "updated_at": 0, "items": []}


def _normalize_item(raw: Dict[str, Any], idx: int) -> GlossaryItem:
    src = str(raw.get("src") or "").strip()
    tgt = str(raw.get("tgt") or "").strip()
    if not src:
        raise ValueError(f"glossary item #{idx}: missing src")
    item_id = str(raw.get("id") or f"t{idx:04d}")
    aliases = raw.get("aliases") or []
    forbidden = raw.get("forbidden") or []
    if not isinstance(aliases, list) or not isinstance(forbidden, list):
        raise ValueError(f"glossary item #{idx}: aliases/forbidden must be list")
    aliases = [str(x).strip() for x in aliases if str(x).strip()]
    forbidden = [str(x).strip() for x in forbidden if str(x).strip()]
    note = str(raw.get("note") or "").strip()
    scope = str(raw.get("scope") or "global").strip() or "global"
    return GlossaryItem(
        id=item_id,
        src=src,
        tgt=tgt,
        aliases=aliases,
        forbidden=forbidden,
        note=note,
        scope=scope,
    )


def validate_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError("glossary doc must be an object")
    items = doc.get("items")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError("glossary.items must be a list")
    normalized: List[Dict[str, Any]] = []
    seen_ids = set()
    for i, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"glossary item #{i}: must be an object")
        it = _normalize_item(raw, i)
        if it.id in seen_ids:
            raise ValueError(f"duplicate glossary item id: {it.id}")
        seen_ids.add(it.id)
        normalized.append(
            {
                "id": it.id,
                "src": it.src,
                "tgt": it.tgt,
                "aliases": it.aliases,
                "forbidden": it.forbidden,
                "note": it.note,
                "scope": it.scope,
            }
        )
    return {
        "version": int(doc.get("version") or 1),
        "updated_at": int(doc.get("updated_at") or 0),
        "items": normalized,
    }


def load_glossary(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return default_doc()
    try:
        doc = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "{}")
        if not isinstance(doc, dict):
            return default_doc()
        # validate but do not mutate file on read
        validated = validate_doc(doc)
        if not validated.get("updated_at"):
            validated["updated_at"] = int(time.time())
        return validated
    except Exception:
        return default_doc()


def save_glossary(path: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    validated = validate_doc(doc)
    validated["updated_at"] = int(time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    # best-effort backup
    if path.exists():
        try:
            bak = path.with_suffix(path.suffix + f".bak.{validated['updated_at']}")
            bak.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass
    path.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
    return validated


