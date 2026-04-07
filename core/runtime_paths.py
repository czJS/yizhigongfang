from __future__ import annotations

import os
import sys
from pathlib import Path


def detect_repo_root() -> Path:
    """
    Single source of truth for repo/resources root resolution.

    Priority:
    1) YGF_APP_ROOT (Electron packaged resources dir)
    2) sys.executable parent when frozen (PyInstaller)
    3) best-effort walk upwards to find v2 layout (configs/ + pipelines/)
    """
    repo_root_env = os.environ.get("YGF_APP_ROOT", "").strip()
    if repo_root_env:
        return Path(repo_root_env).resolve()
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
    except Exception:
        pass
    start = Path(__file__).resolve()
    for p in [start.parent, *start.parents]:
        try:
            if (p / "configs").exists() and (p / "pipelines").exists():
                return p
        except Exception:
            continue
    return start.parents[1]


def pick_config_dir(root: Path) -> Path:
    """
    Repo layout v2 uses `configs/`.

    NOTE: We intentionally do NOT fall back to legacy `config/`.
    """
    return root / "configs"


def pick_pipelines_dir(root: Path) -> Path:
    """Repo layout v2 uses `pipelines/`."""
    return root / "pipelines"

