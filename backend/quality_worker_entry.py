"""
Quality worker entrypoint (packaged separately from backend_server.exe).

Why:
- Quality pipeline depends on WhisperX and heavy ML stacks whose dependency versions conflict
  with the lightweight/backend dependencies.
- We ship this as a separate executable (quality_worker.exe) built from its own venv.

Runtime contract:
- Supports the same runner interface as backend_server.exe:
    quality_worker.exe --run-pipeline quality <pipeline-args...>
- In installed app, YGF_APP_ROOT points to the Electron resources directory, where:
    - scripts/quality_pipeline.py exists on disk
    - bin/ffmpeg.exe etc exist on disk
"""

from __future__ import annotations

import os
import runpy
import sys
import traceback
from pathlib import Path

from backend.runtime_paths import detect_repo_root, pick_pipelines_dir

def _detect_root() -> Path:
    return detect_repo_root()


def _prepare_env(root: Path) -> None:
    # Ensure bundled binaries are discoverable (ffmpeg/whisper-cli, etc.).
    bin_dir = root / "bin"
    if bin_dir.exists():
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        # Some subprocess resolution on Windows depends on PATHEXT; guard against empty env in packaged contexts.
        if os.name == "nt" and not os.environ.get("PATHEXT"):
            os.environ["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"
        ffmpeg_exe = bin_dir / "ffmpeg.exe"
        if ffmpeg_exe.exists():
            # Helps libs that probe ffmpeg at import-time (e.g. pydub).
            os.environ.setdefault("FFMPEG_BINARY", str(ffmpeg_exe))

    # Reduce noisy warnings in packaged app (does not affect functionality):
    # - HF symlink warning on Windows (caching still works without symlinks).
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    # Keep Matplotlib cache off C: and stable across runs to avoid repeated "building the font cache".
    # Electron sets TEMP/TMP to user_data/tmp for packaged runs; reuse it when available.
    try:
        base_tmp = os.environ.get("TMP") or os.environ.get("TEMP") or ""
        if base_tmp:
            mpl_dir = Path(base_tmp) / "matplotlib"
            mpl_dir.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))
    except Exception:
        pass

    # Ensure repo root is importable for `from scripts import ...` and `from backend import ...`
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _patch_torch_safe_globals_for_omegaconf() -> None:
    """
    PyTorch 2.6+ changed torch.load default `weights_only=True`, which restricts unpickling.
    WhisperX's pyannote VAD checkpoint includes OmegaConf metadata objects, causing:
      _pickle.UnpicklingError: Unsupported global ... omegaconf.base.ContainerMetadata

    Some checkpoints also reference typing objects (e.g. typing.Any), which are blocked by default
    under weights_only=True and need to be allowlisted explicitly.

    In our app, these checkpoints come from our bundled model pack / HF cache and are trusted.
    This patch is best-effort and only expands the allowlist.
    """
    try:
        import torch  # type: ignore
        import typing
        import collections

        ser = getattr(torch, "serialization", None)
        add = getattr(ser, "add_safe_globals", None) if ser is not None else None
        if not callable(add):
            return
        from omegaconf import DictConfig, ListConfig  # type: ignore
        from omegaconf.base import ContainerMetadata  # type: ignore

        # Allowlist a minimal set of known-safe globals used by our shipped checkpoints.
        # Some checkpoints may also reference bare builtins like `list`.
        add([DictConfig, ListConfig, ContainerMetadata, typing.Any, list, collections.defaultdict])
    except Exception:
        # Keep silent; downstream will raise the original error if any.
        return


def _force_torch_load_weights_only_false() -> None:
    """
    In PyTorch 2.6+, torch.load defaults to weights_only=True (safer unpickling).
    However, some of the bundled/expected checkpoints used by WhisperX/pyannote contain pickled
    objects that are blocked under weights_only=True, and the set of required allowlisted globals
    can vary across versions (typing.Any / list / collections.defaultdict / ...).

    For the packaged app, these checkpoints come from our model pack / known HF cache and are trusted.
    So we force weights_only=False globally in the quality worker process to avoid repeated failures.
    """
    try:
        import torch  # type: ignore

        orig_load = getattr(torch, "load", None)
        if not callable(orig_load):
            return

        def _patched_load(*a, **kw):  # type: ignore[no-untyped-def]
            kw["weights_only"] = False
            return orig_load(*a, **kw)

        torch.load = _patched_load  # type: ignore[assignment]
        ser = getattr(torch, "serialization", None)
        if ser is not None and hasattr(ser, "load"):
            try:
                ser.load = _patched_load  # type: ignore[assignment]
            except Exception:
                pass
    except Exception:
        return


def _ensure_gruut_version_file() -> None:
    """
    Coqui TTS imports `gruut`, which expects a `VERSION` data file. In some PyInstaller(onefile)
    builds this file is missing from the extracted bundle, causing an import-time crash:
      FileNotFoundError: ...\\_MEIxxxx\\gruut\\VERSION

    Best-effort: create the file in the extracted bundle directory before importing TTS.
    """
    try:
        mei = getattr(sys, "_MEIPASS", None)
        if not mei:
            return
        vp = Path(mei) / "gruut" / "VERSION"
        if vp.exists():
            return
        vp.parent.mkdir(parents=True, exist_ok=True)
        vp.write_text("0.0.0", encoding="utf-8")
    except Exception:
        return


def main() -> None:
    root = _detect_root()

    # ---------------------------------------------------------
    # Self-check mode (packaging/smoke test)
    # ---------------------------------------------------------
    # Usage (packaged):
    #   set YGF_APP_ROOT=<resources>
    #   quality_worker.exe --self-check
    #
    # Avoids running a full pipeline to validate dependency compatibility.
    if "--self-check" in sys.argv:
        _prepare_env(root)
        _patch_torch_safe_globals_for_omegaconf()
        _force_torch_load_weights_only_false()
        _ensure_gruut_version_file()
        info = {"ok": True, "root": str(root), "imports": {}, "versions": {}}
        try:
            import torch  # type: ignore

            info["versions"]["torch"] = getattr(torch, "__version__", "")
        except Exception as exc:
            info["ok"] = False
            info["imports"]["torch"] = f"FAIL: {exc}"
        else:
            info["imports"]["torch"] = "OK"

        try:
            import omegaconf  # type: ignore

            info["versions"]["omegaconf"] = getattr(omegaconf, "__version__", "")
        except Exception as exc:
            info["ok"] = False
            info["imports"]["omegaconf"] = f"FAIL: {exc}"
        else:
            info["imports"]["omegaconf"] = "OK"

        try:
            import whisperx  # type: ignore

            info["versions"]["whisperx"] = getattr(whisperx, "__version__", "")
        except Exception as exc:
            info["ok"] = False
            info["imports"]["whisperx"] = f"FAIL: {exc}"
        else:
            info["imports"]["whisperx"] = "OK"

        # VAD path we rely on in quality mode: faster-whisper + ONNXRuntime (Silero VAD assets).
        try:
            import faster_whisper  # type: ignore

            info["imports"]["faster_whisper"] = "OK"
            info["versions"]["faster_whisper"] = getattr(faster_whisper, "__version__", "")
        except Exception as exc:
            info["ok"] = False
            info["imports"]["faster_whisper"] = f"FAIL: {exc}"

        try:
            import onnxruntime  # type: ignore

            info["imports"]["onnxruntime"] = "OK"
            info["versions"]["onnxruntime"] = getattr(onnxruntime, "__version__", "")
        except Exception as exc:
            info["ok"] = False
            info["imports"]["onnxruntime"] = f"FAIL: {exc}"

        # TTS backend we ship: Coqui TTS.
        try:
            import TTS  # type: ignore

            info["imports"]["TTS"] = "OK"
            info["versions"]["TTS"] = getattr(TTS, "__version__", "")
        except Exception as exc:
            info["ok"] = False
            info["imports"]["TTS"] = f"FAIL: {exc}"

        # `TTS` imports phonemizers, some of which depend on `jamo` JSON data files (Korean phonemizer).
        # Validate that the critical data file exists in the bundle.
        try:
            import jamo  # type: ignore

            info["imports"]["jamo"] = "OK"
            jamo_dir = Path(getattr(jamo, "__file__", "")).resolve().parent
            data_file = jamo_dir / "data" / "U+11xx.json"
            info["versions"]["jamo"] = getattr(jamo, "__version__", "")
            info["imports"]["jamo.data"] = "OK" if data_file.exists() else f"FAIL: missing {data_file}"
            if not data_file.exists():
                info["ok"] = False
        except Exception as exc:
            info["ok"] = False
            info["imports"]["jamo"] = f"FAIL: {exc}"

        try:
            import pyannote.audio  # type: ignore

            info["imports"]["pyannote.audio"] = "OK"
        except Exception as exc:
            # pyannote is optional depending on whisperx VAD configuration, but in our quality worker it is expected.
            info["ok"] = False
            info["imports"]["pyannote.audio"] = f"FAIL: {exc}"

        try:
            import lightning_fabric  # type: ignore

            info["imports"]["lightning_fabric"] = "OK"
        except Exception as exc:
            info["ok"] = False
            info["imports"]["lightning_fabric"] = f"FAIL: {exc}"

        try:
            import json as _json

            print(_json.dumps(info, ensure_ascii=False, indent=2))
        finally:
            raise SystemExit(0 if info.get("ok") else 2)

    if "--run-pipeline" not in sys.argv:
        raise SystemExit("usage: quality_worker.exe --run-pipeline quality <script-args...>")

    try:
        idx = sys.argv.index("--run-pipeline")
        mode = (sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "").strip() or "quality"
        forwarded = sys.argv[idx + 2 :]
    except Exception:
        mode = "quality"
        forwarded = []

    scripts_dir = pick_pipelines_dir(root)
    script_map = {
        "quality": scripts_dir / "quality_pipeline.py",
        # Keep compatibility if callers pass mode explicitly.
        "lite": scripts_dir / "asr_translate_tts.py",
        "online": scripts_dir / "online_pipeline.py",
    }
    script_path = script_map.get(mode, script_map["quality"])
    if not script_path.exists():
        raise FileNotFoundError(f"pipeline script not found for mode={mode}: {script_path}")

    _prepare_env(root)
    _patch_torch_safe_globals_for_omegaconf()
    _force_torch_load_weights_only_false()
    _ensure_gruut_version_file()

    # Run the script as __main__, forwarding the original CLI args.
    sys.argv = [str(script_path), *forwarded]
    try:
        runpy.run_path(str(script_path), run_name="__main__")
    except SystemExit as exc:
        # Preserve script failures.
        if isinstance(exc.code, int):
            raise SystemExit(exc.code)
        if exc.code is None:
            raise SystemExit(0)
        try:
            print(str(exc.code), file=sys.stderr)
        finally:
            raise SystemExit(1)
    except Exception:
        # Avoid PyInstaller "windowed traceback" dialog: print a full traceback to stderr
        # so the parent (Electron/task runner) can capture logs, then exit non-zero.
        try:
            traceback.print_exc(file=sys.stderr)
        finally:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

