from __future__ import annotations

import sys
from pathlib import Path


def _repo_root_from_here() -> Path:
    # /repo/pipelines/tools/*.py -> parents[2] is /repo
    return Path(__file__).resolve().parents[2]


def _ok(msg: str) -> None:
    print("[ok] ", msg)


def _warn(msg: str) -> None:
    print("[warn]", msg)


def _fail(msg: str) -> None:
    print("[fail]", msg)


def _exists(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False


def main() -> int:
    repo = _repo_root_from_here()
    assets = repo / "assets" / "models"

    failures: list[str] = []

    # Lite must-have (packaging best practice)
    asr_small = assets / "lite_asr_whispercpp" / "ggml-small-q5_1.bin"
    if _exists(asr_small):
        _ok(f"lite ASR model present: {asr_small}")
    else:
        failures.append(f"missing lite ASR model: {asr_small}")

    piper_onnx = assets / "lite_tts_piper" / "en_US-amy-low.onnx"
    if _exists(piper_onnx):
        _ok(f"lite Piper model present: {piper_onnx}")
    else:
        failures.append(f"missing lite Piper model: {piper_onnx}")

    lite_mt = assets / "lite_mt_marian_opus_mt_zh_en"
    mt_cfg = lite_mt / "config.json"
    mt_weight_ok = _exists(lite_mt / "model.safetensors") or _exists(lite_mt / "pytorch_model.bin")
    if _exists(mt_cfg) and mt_weight_ok:
        _ok(f"lite MT model complete: {lite_mt}")
    else:
        failures.append(
            "lite MT model incomplete: expected config.json + (pytorch_model.bin or model.safetensors) under "
            f"{lite_mt}. You can run: python3 pipelines/tools/stage_lite_mt_marian_from_hf_cache.py"
        )

    # Development/optional caches
    hf_cache = assets / "common_cache_hf"
    if _exists(hf_cache):
        _ok(f"HF cache dir present: {hf_cache}")
    else:
        _warn(f"HF cache dir missing (ok for fully bundled builds): {hf_cache}")

    # Quality mode: optional unless you want offline quality out of box
    q_tts = assets / "quality_tts_coqui"
    if _exists(q_tts) and any(q_tts.iterdir()):
        _ok(f"quality Coqui models dir present: {q_tts}")
    else:
        _warn(f"quality Coqui models dir empty/missing (ok if you don't ship quality offline): {q_tts}")

    q_asr = assets / "quality_asr_whisperx"
    if _exists(q_asr) and any(q_asr.iterdir()):
        _ok(f"quality WhisperX cache dir present: {q_asr}")
    else:
        _warn(f"quality WhisperX cache dir empty/missing (ok if quality mode can download): {q_asr}")

    if failures:
        for f in failures:
            _fail(f)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

