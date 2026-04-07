# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


try:
    _ROOT = Path(__file__).resolve().parent
except NameError:
    _ROOT = Path(".").resolve()

_CANONICAL = _ROOT / "packaging" / "windows" / "pyinstaller" / "backend_server.spec"
if not _CANONICAL.exists():
    raise FileNotFoundError(f"canonical backend spec not found: {_CANONICAL}")

exec(compile(_CANONICAL.read_text(encoding="utf-8"), str(_CANONICAL), "exec"))
