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
    3) repo root inferred from backend/ directory
    """
    repo_root_env = os.environ.get("YGF_APP_ROOT", "").strip()
    if repo_root_env:
        return Path(repo_root_env).resolve()
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
    except Exception:
        pass
    return Path(__file__).resolve().parents[1]


def pick_config_dir(root: Path) -> Path:
    """Repo layout v2 prefers `configs/`, with legacy fallback to `config/`."""
    try:
        p = root / "configs"
        if p.exists():
            return p
    except Exception:
        pass
    return root / "config"


def pick_pipelines_dir(root: Path) -> Path:
    """Repo layout v2 prefers `pipelines/`, with legacy fallback to `scripts/`."""
    try:
        p = root / "pipelines"
        if p.exists():
            return p
    except Exception:
        pass
    return root / "scripts"

