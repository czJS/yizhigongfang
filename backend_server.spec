# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


# NOTE: PyInstaller may execute this spec without defining `__file__` in the spec namespace,
# so we rely on the current working directory (our build scripts `cd` to repo root).
_ROOT = Path(".").resolve()

# NOTE:
# The packaged `backend_server.exe` runs pipeline scripts via `runpy.run_path(...)` for lite/online.
# We explicitly analyze ONLY the lite/online scripts to bundle their dependencies.
# The "quality" pipeline is handled by a separate executable (quality_worker.exe) for dependency isolation,
# so we MUST NOT analyze `quality_pipeline.py` here (otherwise WhisperX heavy deps leak into backend_server.exe).
_SCRIPT_TARGETS = [
    str(_ROOT / "scripts" / "asr_translate_tts.py"),
    str(_ROOT / "scripts" / "online_pipeline.py"),
]

a = Analysis(
    ['backend\\app.py', *_SCRIPT_TARGETS],
    pathex=[str(_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='backend_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
