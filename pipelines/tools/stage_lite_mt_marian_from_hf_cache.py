from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional


def _repo_root_from_here() -> Path:
    # /repo/pipelines/tools/*.py -> parents[2] is /repo
    return Path(__file__).resolve().parents[2]


def _hf_cache_model_dir(cache_dir: Path, hf_id: str) -> Path:
    return cache_dir / f"models--{hf_id.replace('/', '--')}"


def _pick_snapshot_dir(model_dir: Path) -> Path:
    snaps = model_dir / "snapshots"
    if not snaps.exists():
        raise FileNotFoundError(f"HF cache snapshots dir not found: {snaps}")
    cands = [p for p in snaps.iterdir() if p.is_dir()]
    if not cands:
        raise FileNotFoundError(f"No snapshots found under: {snaps}")
    # Prefer newest snapshot (mtime).
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]


def _first_existing(base: Path, names: Iterable[str]) -> Optional[Path]:
    for n in names:
        p = base / n
        if p.exists():
            return p
    return None


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    # follow_symlinks=True is important for HF cache snapshots.
    shutil.copy2(src, dst, follow_symlinks=True)


def stage_marian_opus_mt_zh_en(*, cache_dir: Path, dst_dir: Path) -> None:
    hf_id = "Helsinki-NLP/opus-mt-zh-en"
    model_dir = _hf_cache_model_dir(cache_dir, hf_id)
    snap = _pick_snapshot_dir(model_dir)

    required = [
        "config.json",
        "tokenizer_config.json",
        "vocab.json",
        "source.spm",
        "target.spm",
    ]
    optional = [
        "generation_config.json",
        "README.md",
    ]

    weight = _first_existing(snap, ["model.safetensors", "pytorch_model.bin"])
    if not weight:
        raise FileNotFoundError(
            "No weights found in HF snapshot. Expected one of: model.safetensors, pytorch_model.bin\n"
            f"- snapshot: {snap}"
        )

    print("[stage] src snapshot:", snap)
    print("[stage] dst dir:", dst_dir)

    for fn in required:
        src = snap / fn
        if not src.exists():
            raise FileNotFoundError(f"Missing required file in snapshot: {src}")
        _copy(src, dst_dir / fn)
        print("[stage] copied:", fn)

    _copy(weight, dst_dir / weight.name)
    print("[stage] copied:", weight.name)

    for fn in optional:
        src = snap / fn
        if src.exists():
            _copy(src, dst_dir / fn)
            print("[stage] copied (optional):", fn)

    # Verify
    if not (dst_dir / "config.json").exists():
        raise RuntimeError("Stage failed: config.json not found in dst.")
    if not ((dst_dir / "model.safetensors").exists() or (dst_dir / "pytorch_model.bin").exists()):
        raise RuntimeError("Stage failed: weights not found in dst.")


def main() -> int:
    repo_root = _repo_root_from_here()
    p = argparse.ArgumentParser(
        description="Stage lite Marian MT weights from HuggingFace cache into assets/ for packaging/offline runs."
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=repo_root / "assets" / "models" / "common_cache_hf",
        help="HuggingFace hub cache dir (contains models--*/snapshots/*). Default: assets/models/common_cache_hf",
    )
    p.add_argument(
        "--dst-dir",
        type=Path,
        default=repo_root / "assets" / "models" / "lite_mt_marian_opus_mt_zh_en",
        help="Destination model dir to make self-contained. Default: assets/models/lite_mt_marian_opus_mt_zh_en",
    )
    args = p.parse_args()

    try:
        stage_marian_opus_mt_zh_en(cache_dir=args.cache_dir, dst_dir=args.dst_dir)
        print("[stage] done.")
        return 0
    except Exception as e:
        print("[stage] failed:", str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

