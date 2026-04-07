import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.ruleset_store import default_doc as ruleset_default_doc
from backend.ruleset_store import validate_doc as validate_ruleset_doc


_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{6,80}$")


def _safe_template_path(templates_dir: Path, template_id: str) -> Path:
    tid = str(template_id or "").strip()
    if not _SAFE_ID_RE.match(tid):
        raise ValueError("invalid template id")
    p = (templates_dir / f"{tid}.json").resolve(strict=False)
    root = templates_dir.resolve(strict=False)
    try:
        p.relative_to(root)
    except Exception as exc:
        raise ValueError(f"invalid template path: {exc}")
    return p


def _load_json_best_effort(path: Path) -> Dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "{}")
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def list_templates(templates_dir: Path) -> List[Dict[str, Any]]:
    """
    Return minimal template infos for UI listing:
    - id, name, updated_at
    """
    out: List[Dict[str, Any]] = []
    try:
        if not templates_dir.exists():
            return []
        for p in sorted(templates_dir.glob("*.json")):
            doc = _load_json_best_effort(p)
            tid = str(doc.get("id") or p.stem).strip()
            name = str(doc.get("name") or "").strip() or tid
            updated_at = int(doc.get("updated_at") or 0)
            if tid:
                out.append({"id": tid, "name": name, "updated_at": updated_at})
    except Exception:
        return []
    # newest first
    out.sort(key=lambda x: int(x.get("updated_at") or 0), reverse=True)
    return out


def load_template(templates_dir: Path, template_id: str) -> Dict[str, Any]:
    p = _safe_template_path(templates_dir, template_id)
    if not p.exists():
        raise FileNotFoundError("template not found")
    raw = _load_json_best_effort(p)
    tid = str(raw.get("id") or template_id).strip() or str(template_id)
    name = str(raw.get("name") or "").strip() or tid
    created_at = int(raw.get("created_at") or 0)
    updated_at = int(raw.get("updated_at") or 0)
    doc_raw = raw.get("doc") if isinstance(raw.get("doc"), dict) else (raw.get("ruleset") if isinstance(raw.get("ruleset"), dict) else None)
    doc = validate_ruleset_doc(doc_raw or ruleset_default_doc())
    return {"id": tid, "name": name, "created_at": created_at, "updated_at": updated_at, "doc": doc}


def load_template_doc(templates_dir: Path, template_id: str) -> Dict[str, Any]:
    return load_template(templates_dir, template_id).get("doc") or ruleset_default_doc()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def create_template(templates_dir: Path, name: str, doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    templates_dir.mkdir(parents=True, exist_ok=True)
    tid = _new_id()
    now = int(time.time())
    tpl = {
        "id": tid,
        "name": str(name or "").strip() or f"模板-{tid}",
        "created_at": now,
        "updated_at": now,
        "doc": validate_ruleset_doc(doc or ruleset_default_doc()),
    }
    p = _safe_template_path(templates_dir, tid)
    p.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    return tpl


def update_template(templates_dir: Path, template_id: str, *, name: Optional[str] = None, doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    current = load_template(templates_dir, template_id)
    if name is not None:
        current["name"] = str(name or "").strip() or current["id"]
    if doc is not None:
        current["doc"] = validate_ruleset_doc(doc)
    current["updated_at"] = int(time.time())
    p = _safe_template_path(templates_dir, template_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def delete_template(templates_dir: Path, template_id: str) -> None:
    p = _safe_template_path(templates_dir, template_id)
    if not p.exists():
        return
    try:
        p.unlink()
    except Exception as exc:
        raise RuntimeError(str(exc))


def import_template_from_json(templates_dir: Path, raw: Dict[str, Any], *, name_hint: str = "") -> Dict[str, Any]:
    """
    Accept either:
    - {id?, name?, doc:{ruleset...}}  (template export)
    - {version, terms, asr_fixes, settings} (ruleset doc)
    """
    if not isinstance(raw, dict):
        raise ValueError("template json must be an object")
    if isinstance(raw.get("doc"), dict):
        doc = raw.get("doc")  # type: ignore[assignment]
        name = str(raw.get("name") or name_hint or "").strip()
        return create_template(templates_dir, name or "导入模板", doc=doc)  # new id
    # treat as ruleset doc
    doc = validate_ruleset_doc(raw)
    name = str(name_hint or raw.get("name") or "").strip()
    return create_template(templates_dir, name or "导入模板", doc=doc)

