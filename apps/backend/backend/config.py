import os
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


def load_defaults(config_path: Path) -> Dict[str, Any]:
    """Load YAML defaults and expand environment variables."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    return expand_env_vars(data)


def expand_env_vars(val: Any) -> Any:
    if isinstance(val, str):
        return os.path.expandvars(val)
    if isinstance(val, dict):
        return {k: expand_env_vars(v) for k, v in val.items()}
    if isinstance(val, list):
        return [expand_env_vars(v) for v in val]
    return val


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(a or {})
    for k, v in (b or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_user_override_dir(repo_root: Path) -> Optional[Path]:
    raw = (os.environ.get("YGF_CONFIG_OVERRIDE_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    default_dir = repo_root / "config_overrides"
    return default_dir if default_dir.exists() else None


def _collect_override_files(override_dir: Optional[Path]) -> List[Path]:
    if override_dir is None or not override_dir.exists():
        return []
    return sorted([p for p in override_dir.glob("*.yaml") if p.is_file()] + [p for p in override_dir.glob("*.yml") if p.is_file()])


def load_config_stack(config_path: Path, defaults_path: Optional[Path] = None, repo_root: Optional[Path] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    root = (repo_root or config_path.parent.parent).resolve()
    defaults = load_defaults(defaults_path) if defaults_path and defaults_path.exists() else {}
    active = load_defaults(config_path)
    merged = deep_merge(defaults, active)

    override_dir = get_user_override_dir(root)
    override_files = _collect_override_files(override_dir)
    for override_path in override_files:
        merged = deep_merge(merged, load_defaults(override_path))

    merged_json = json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    meta = {
        "root": str(root),
        "defaults_path": str(defaults_path) if defaults_path else "",
        "active_config_path": str(config_path),
        "override_dir": str(override_dir) if override_dir else "",
        "override_files": [str(p) for p in override_files],
        "source_chain": [p for p in ([str(defaults_path)] if defaults_path and defaults_path.exists() else []) + [str(config_path)] + [str(p) for p in override_files]],
        "merged_hash": hashlib.sha256(merged_json.encode("utf-8")).hexdigest(),
    }
    return merged, meta


