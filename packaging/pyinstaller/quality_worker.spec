# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata, collect_dynamic_libs
import os
import site

# Build scripts `cd` to repo root before invoking PyInstaller.
_ROOT = Path(".").resolve()

# WhisperX uses lazy/dynamic imports (e.g. whisperx.asr) that PyInstaller won't see via static analysis.
# Collect all whisperx submodules explicitly to avoid runtime ModuleNotFoundError.
_HIDDEN = []
try:
    _HIDDEN += collect_submodules("whisperx")
except Exception:
    pass

# Our worker runs pipeline scripts via runpy.run_path(), and those scripts import many modules.
# We DO NOT list scripts/*.py as entry scripts in Analysis (it would execute them sequentially and
# can cause post-run argparse failures). Instead, include them as hidden imports so PyInstaller
# analyzes them and bundles their dependencies without executing them.
try:
    _HIDDEN += collect_submodules("scripts")
except Exception:
    pass

# Pipelines v2 shared library (used by legacy scripts via local imports).
try:
    _HIDDEN += collect_submodules("pipelines")
except Exception:
    pass

# NOTE:
# Even though our pipeline prefers faster-whisper's Silero VAD, WhisperX's ASR module imports
# `whisperx.vads` (which imports pyannote + speechbrain) at import time. In PyInstaller onefile,
# SpeechBrain also relies on os.listdir() discovery, which requires real on-disk .py files.
# Therefore we must bundle pyannote + speechbrain and keep modules as files on disk (noarchive=True).
try:
    _HIDDEN += collect_submodules("pyannote.audio")
except Exception:
    pass
try:
    _HIDDEN += collect_submodules("speechbrain")
except Exception:
    pass

# Coqui TTS uses dynamic imports across the `TTS` package. Bundle its submodules explicitly.
try:
    _HIDDEN += collect_submodules("TTS")
except Exception:
    pass

# Some deps (notably lightning/lightning_fabric) read non-.py data files at runtime
# (e.g. lightning_fabric/version.info). Include their package data + dist metadata explicitly.
_DATAS = []
for _pkg in ["lightning_fabric", "lightning", "pytorch_lightning"]:
    try:
        _DATAS += collect_data_files(_pkg, include_py_files=False)
    except Exception:
        pass
    try:
        _DATAS += copy_metadata(_pkg)
    except Exception:
        pass

# Coqui TTS imports many phonemizers at import-time; some of them (e.g. Korean) depend on `jamo`
# and expect JSON data files under jamo/data/*.json. If not bundled, onefile runtime crashes with:
#   FileNotFoundError: ... _MEI...\\jamo\\data\\U+11xx.json
for _pkg in ["jamo"]:
    try:
        _DATAS += collect_data_files(_pkg, include_py_files=False)
    except Exception:
        pass
    try:
        _DATAS += copy_metadata(_pkg)
    except Exception:
        pass

# Coqui TTS depends on `trainer` which expects a runtime VERSION file:
#   FileNotFoundError: ... _MEI...\\trainer\\VERSION
for _pkg in ["trainer"]:
    try:
        _DATAS += collect_data_files(_pkg, include_py_files=False)
    except Exception:
        pass
    try:
        _DATAS += copy_metadata(_pkg)
    except Exception:
        pass

# Fallback: force-include trainer/VERSION if present (some wheels may confuse collect_data_files()).
try:
    import trainer  # type: ignore

    _trainer_dir = Path(trainer.__file__).resolve().parent
    _trainer_ver = _trainer_dir / "VERSION"
    if _trainer_ver.exists():
        _DATAS.append((str(_trainer_ver), "trainer"))
except Exception:
    pass

# Coqui TTS text pipeline may import `gruut`, which reads VERSION at runtime:
#   FileNotFoundError: ... _MEI...\\gruut\\VERSION
for _pkg in ["gruut", "gruut_ipa", "gruut_lang_en"]:
    try:
        _DATAS += collect_data_files(_pkg, include_py_files=False)
    except Exception:
        pass
    try:
        _DATAS += copy_metadata(_pkg)
    except Exception:
        pass

# Some environments/wheels may install `gruut/VERSION` in a way that `collect_data_files()` misses.
# Add a hard fallback to force-include the VERSION file if present.
try:
    import gruut  # type: ignore

    _gruut_dir = Path(gruut.__file__).resolve().parent
    _gruut_ver = _gruut_dir / "VERSION"
    if _gruut_ver.exists():
        _DATAS.append((str(_gruut_ver), "gruut"))
except Exception:
    pass

# faster-whisper bundles Silero VAD ONNX assets under faster_whisper/assets/ (e.g. silero_vad_v6.onnx).
# If not collected, runtime VAD filtering fails with:
#   NO_SUCHFILE: ... _MEI...\\faster_whisper\\assets\\silero_vad_v6.onnx
for _pkg in ["faster_whisper"]:
    try:
        _DATAS += collect_data_files(_pkg, include_py_files=False)
    except Exception:
        pass
    try:
        _DATAS += copy_metadata(_pkg)
    except Exception:
        pass

# Coqui TTS package data + metadata (configs, speaker maps, etc.)
for _pkg in ["TTS"]:
    try:
        _DATAS += collect_data_files(_pkg, include_py_files=False)
    except Exception:
        pass
    try:
        _DATAS += copy_metadata(_pkg)
    except Exception:
        pass

# numba may require Intel TBB runtime DLL (tbb12.dll) on Windows.
# `tbb` wheels sometimes ship DLLs without a Python package, so we search site-packages for tbb*.dll.
_BIN = []
try:
    for sp in site.getsitepackages():
        for name in ["tbb12.dll", "tbbmalloc.dll", "tbbmalloc_proxy.dll", "tcmlib.dll"]:
            p = os.path.join(sp, name)
            if os.path.exists(p):
                _BIN.append((p, "."))
except Exception:
    pass

# WhisperX VAD (pyannote) loads a bundled model from whisperx/assets/pytorch_model.bin.
# Ensure WhisperX package data is included, otherwise onefile runtime will crash with:
#   FileNotFoundError: ... _MEI...\\whisperx\\assets\\pytorch_model.bin
try:
    _DATAS += collect_data_files("whisperx", include_py_files=False)
except Exception:
    pass
try:
    _DATAS += copy_metadata("whisperx")
except Exception:
    pass

# SpeechBrain uses os.listdir() and expects source ".py" files to exist on disk.
try:
    _DATAS += collect_data_files("speechbrain", include_py_files=True)
except Exception:
    pass
try:
    _DATAS += copy_metadata("speechbrain")
except Exception:
    pass

a = Analysis(
    ["backend\\quality_worker_entry.py"],
    pathex=[str(_ROOT)],
    binaries=_BIN,
    datas=_DATAS,
    hiddenimports=_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    # IMPORTANT: keep pure python modules as files on disk (not inside PYZ archive)
    # so SpeechBrain's os.listdir-based module discovery works in onefile mode.
    noarchive=True,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="quality_worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Build as a console subsystem app so `--self-check` prints output when run manually.
    # Our backend spawns it with CREATE_NO_WINDOW, so end-users won't see a flashing terminal.
    console=True,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

