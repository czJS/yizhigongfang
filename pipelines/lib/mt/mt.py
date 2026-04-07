from __future__ import annotations

import os
from contextlib import nullcontext
from pathlib import Path
from typing import List, Optional, Sequence, Union

try:
    import torch  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    torch = None  # type: ignore


def _repo_root_from_here() -> Path:
    # /repo/pipelines/lib/mt/mt.py -> parents[3] is /repo
    p = Path(__file__).resolve()
    try:
        return p.parents[3]
    except Exception:
        # Fallback (shouldn't happen), keep behavior reasonable.
        return p.parents[len(p.parents) - 1]


def _has_transformers_weights(model_dir: Path) -> bool:
    # Accept both PyTorch .bin and safetensors layouts.
    for fn in ("pytorch_model.bin", "model.safetensors", "tf_model.h5", "flax_model.msgpack"):
        if (model_dir / fn).exists():
            return True
    return False


def _infer_hf_model_id_from_local_dir(model_dir: Path) -> Optional[str]:
    # Our recommended lite MT mirror directory name.
    if model_dir.name == "lite_mt_marian_opus_mt_zh_en":
        return "Helsinki-NLP/opus-mt-zh-en"
    readme = model_dir / "README.md"
    if readme.exists():
        try:
            s = readme.read_text(encoding="utf-8", errors="ignore")
            # Common pattern in our docs: `Helsinki-NLP/opus-mt-zh-en`
            import re

            m = re.search(r"`([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)`", s)
            if m:
                return m.group(1)
        except Exception:
            pass
    return None


def _normalize_runtime_device(device: Union[str, int]):
    if device == "auto":
        if torch is not None and torch.cuda.is_available():  # type: ignore
            return 0, "cuda:0"
        return -1, "cpu"
    if isinstance(device, int):
        return device, f"cuda:{device}" if device >= 0 else "cpu"
    dev_s = str(device).strip().lower()
    if dev_s == "cpu":
        return -1, "cpu"
    if dev_s == "cuda":
        return 0, "cuda:0"
    if dev_s.startswith("cuda:"):
        try:
            idx = int(dev_s.split(":", 1)[1])
        except Exception:
            idx = 0
        return idx, f"cuda:{idx}"
    return -1, "cpu"


def _resolve_model_path_or_id(
    model_id: str,
    cache_dir: Optional[str],
    offline: bool,
) -> str:
    model_path_or_id: str = model_id
    repo_root = _repo_root_from_here()
    raw = Path(model_id)
    model_id_str = str(model_id)
    looks_like_path = (
        raw.is_absolute()
        or model_id_str.startswith((".", os.sep, "assets" + os.sep))
        or (os.sep in model_id_str and (repo_root / raw).exists())
    )
    if looks_like_path:
        p = raw if raw.is_absolute() else (repo_root / raw)
        if not p.exists():
            raise RuntimeError(
                "全离线模式下未找到本地翻译模型目录。\n"
                f"- 期望目录: {p}\n"
                "请确认你已把 Marian(opus-mt) 模型文件放入该目录。"
            )
        if p.is_dir() and not (p / "config.json").exists():
            raise RuntimeError(
                "翻译模型目录已存在，但缺少必要文件（至少需要 config.json）。\n"
                f"- 目录: {p}\n"
                "Marian(opus-mt) 最小文件集建议包含：\n"
                "- config.json\n"
                "- pytorch_model.bin\n"
                "- source.spm\n"
                "- target.spm\n"
                "- vocab.json\n"
                "- tokenizer_config.json\n"
                "（可选：generation_config.json；不需要：tf_model.h5、rust_model.ot）"
            )
        if p.is_dir() and not _has_transformers_weights(p):
            # Prefer loading from HF cache (still offline) if user mounted cache_dir,
            # instead of failing hard when the local mirror directory is incomplete.
            hf_id = _infer_hf_model_id_from_local_dir(p)
            if hf_id and cache_dir:
                model_path_or_id = hf_id
            else:
                raise RuntimeError(
                    "翻译模型目录已存在，但缺少权重文件（pytorch_model.bin 或 model.safetensors）。\n"
                    f"- 目录: {p}\n"
                    "解决方法（二选一）：\n"
                    "- 把权重文件补齐到该目录（推荐）\n"
                    "- 或者配置 HuggingFace 缓存目录（mt_cache_dir）并使用已缓存的模型"
                )
        else:
            model_path_or_id = str(p)

    if (
        offline
        and cache_dir
        and not looks_like_path
        and ("/" in str(model_id))
        and isinstance(model_path_or_id, str)
        and model_path_or_id == model_id
    ):
        # Best-effort check to fail fast with actionable message when the HF cache is empty.
        # Some HF cache layouts may not contain "snapshots/" (e.g. certain xet/no_exist layouts),
        # so we only check that the model cache directory exists and looks non-empty.
        cache_root = Path(cache_dir)
        hf_dir = cache_root / f"models--{model_id.replace('/', '--')}"
        ok = False
        try:
            if hf_dir.exists():
                # Standard layout: snapshots/<hash>/config.json
                snap = hf_dir / "snapshots"
                if snap.exists() and any(snap.glob("*/config.json")):
                    ok = True
                # Alternative layout seen in some environments: .no_exist/<hash>/*
                no_exist = hf_dir / ".no_exist"
                if no_exist.exists() and any(no_exist.glob("*/*")):
                    ok = True
                # Minimal: has blobs + refs/main
                if (hf_dir / "blobs").exists() and (hf_dir / "refs" / "main").exists():
                    ok = True
        except Exception:
            ok = False
        if not ok:
            raise RuntimeError(
                "全离线模式下未找到本地翻译模型缓存。\n"
                f"- 需要的模型: {model_id}\n"
                f"- 当前 mt_cache_dir: {cache_dir}\n"
                "请先把模型放到 HF 缓存目录（建议直接挂载 ~/.cache/huggingface/hub），例如：\n"
                f"- {cache_dir}/models--{model_id.replace('/', '--')}/...\n"
                "（也可以把 --mt-model 直接改成一个本地目录路径，该目录内包含 config.json）。"
            )
    return model_path_or_id


def _load_translation_runtime(
    model_id: str,
    device: Union[str, int],
    cache_dir: Optional[str],
    offline: bool,
):
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore

    _device_index, runtime_device = _normalize_runtime_device(device)
    model_path_or_id = _resolve_model_path_or_id(model_id, cache_dir=cache_dir, offline=offline)

    tokenizer = AutoTokenizer.from_pretrained(
        model_path_or_id,
        cache_dir=cache_dir,
        local_files_only=offline,
    )
    model = AutoModelForSeq2SeqLM.from_pretrained(
        model_path_or_id,
        cache_dir=cache_dir,
        local_files_only=offline,
    )
    if torch is not None:  # type: ignore
        model = model.to(runtime_device)  # type: ignore[attr-defined]
    model.eval()

    model_type = str(getattr(getattr(model, "config", None), "model_type", "") or "").lower()
    path_hint = str(model_path_or_id).lower()
    is_nllb = "nllb" in model_type or "nllb" in path_hint
    is_m2m100 = "m2m_100" in model_type or "m2m100" in path_hint

    # Keep generation policy simple and identical across candidates:
    # - no sampling
    # - modest beam search
    # - explicit target language for multilingual models
    common_generate_kwargs = {
        "num_beams": 4,
        "max_new_tokens": 256,
        "renormalize_logits": True,
    }
    return tokenizer, model, runtime_device, is_nllb, is_m2m100, common_generate_kwargs


def build_batch_translator(
    model_id: str,
    device: Union[str, int] = "auto",
    cache_dir: Optional[str] = None,
    offline: bool = False,
    batch_size: int = 8,
):
    """
    Build a batched HuggingFace translation callable (zh->en).

    The returned function accepts a sequence of strings and preserves input order.
    Empty inputs are returned as empty outputs without touching the model.
    """
    tokenizer, model, runtime_device, is_nllb, is_m2m100, common_generate_kwargs = _load_translation_runtime(
        model_id,
        device=device,
        cache_dir=cache_dir,
        offline=offline,
    )
    effective_batch_size = max(1, int(batch_size or 1))

    def translate_batch(texts: Sequence[str]) -> List[str]:
        raw_texts = [str(text or "").strip() for text in texts]
        outputs = [""] * len(raw_texts)
        work_items = [(idx, text) for idx, text in enumerate(raw_texts) if text]
        if not work_items:
            return outputs

        for start in range(0, len(work_items), effective_batch_size):
            chunk = work_items[start : start + effective_batch_size]
            chunk_texts = [text for _, text in chunk]
            if is_nllb and hasattr(tokenizer, "src_lang"):
                tokenizer.src_lang = "zho_Hans"
            elif is_m2m100 and hasattr(tokenizer, "src_lang"):
                tokenizer.src_lang = "zh"
            encoded = tokenizer(
                chunk_texts,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            if torch is not None:  # type: ignore
                encoded = {k: v.to(runtime_device) for k, v in encoded.items()}
            generate_kwargs = dict(common_generate_kwargs)
            if is_nllb:
                lang_code_to_id = getattr(tokenizer, "lang_code_to_id", {}) or {}
                forced = lang_code_to_id.get("eng_Latn")
                if forced is None and hasattr(tokenizer, "convert_tokens_to_ids"):
                    forced = tokenizer.convert_tokens_to_ids("eng_Latn")
                if forced is not None:
                    generate_kwargs["forced_bos_token_id"] = int(forced)
            elif is_m2m100:
                forced = tokenizer.get_lang_id("en") if hasattr(tokenizer, "get_lang_id") else None
                if forced is not None:
                    generate_kwargs["forced_bos_token_id"] = int(forced)
            with torch.no_grad() if torch is not None else nullcontext():
                generated = model.generate(**encoded, **generate_kwargs)
            decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
            for (idx, _), pred in zip(chunk, decoded):
                outputs[idx] = str(pred or "").strip()
        return outputs

    return translate_batch


def build_translator(
    model_id: str,
    device: Union[str, int] = "auto",
    cache_dir: Optional[str] = None,
    offline: bool = False,
):
    """
    Build a single-string HuggingFace translation callable (zh->en).
    """
    translate_batch = build_batch_translator(
        model_id,
        device=device,
        cache_dir=cache_dir,
        offline=offline,
        batch_size=1,
    )

    def translate(text: str) -> str:
        return translate_batch([text])[0]

    return translate


def build_polisher(model_id: str, device: Union[str, int] = "auto"):
    """
    Build a lightweight English polisher (text2text-generation).

    Offline behavior is controlled via env:
    - HF_HUB_OFFLINE=1 or TRANSFORMERS_OFFLINE=1
    """
    from transformers import pipeline  # type: ignore

    if device == "auto":
        if torch is not None and torch.cuda.is_available():  # type: ignore
            device = 0
        else:
            device = -1

    offline = os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    polish = pipeline(
        "text2text-generation",
        model=model_id,
        device=device,
        model_kwargs={"local_files_only": offline},
    )

    def polish_fn(text: str) -> str:
        out = polish(text, max_new_tokens=96, truncation=True)
        return out[0]["generated_text"]

    return polish_fn

