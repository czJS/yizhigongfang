import os
from pathlib import Path
from typing import Any, Dict

import yaml


def load_defaults(config_path: Path) -> Dict[str, Any]:
    """Load YAML defaults and expand environment variables."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    def expand(val: Any) -> Any:
        if isinstance(val, str):
            return os.path.expandvars(val)
        if isinstance(val, dict):
            return {k: expand(v) for k, v in val.items()}
        if isinstance(val, list):
            return [expand(v) for v in val]
        return val

    return expand(data)


