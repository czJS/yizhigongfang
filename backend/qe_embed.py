from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import requests

_LOCAL_CACHE: dict[str, Any] = {}


def _dot(a: List[float], b: List[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _norm(a: List[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in a))


def cosine_sim(a: List[float], b: List[float]) -> Optional[float]:
    if not a or not b:
        return None
    na = _norm(a)
    nb = _norm(b)
    if na <= 0 or nb <= 0:
        return None
    return float(_dot(a, b) / (na * nb))


def try_embed_texts_openai(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    texts: List[str],
    timeout_s: int = 60,
) -> Tuple[Optional[List[List[float]]], Optional[str]]:
    """
    Best-effort OpenAI-compatible embeddings call.
    Returns (embeddings, error). On error, embeddings is None and error is a short string.
    """
    ep = str(endpoint or "").strip().rstrip("/")
    if not ep or not model or not texts:
        return None, "missing endpoint/model/texts"
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body: Dict[str, Any] = {"model": model, "input": texts}
    try:
        url = f"{ep}/embeddings"
        r = requests.post(url, json=body, headers=headers, timeout=int(timeout_s or 60))
        if r.status_code == 404:
            # Ollama compatibility fallback:
            # - Some ollama versions expose embeddings only at /api/embeddings (non-OpenAI schema).
            # - In that case, we do per-text calls (still OK for short videos).
            base = ep
            if base.endswith("/v1"):
                base = base[: -len("/v1")]
            out2: List[List[float]] = []
            for t in texts:
                b2: Dict[str, Any] = {"model": model, "prompt": str(t or "")}
                r2 = requests.post(f"{base}/api/embeddings", json=b2, headers=headers, timeout=int(timeout_s or 60))
                if r2.status_code != 200:
                    return None, f"embeddings http {r2.status_code}"
                obj2 = r2.json() or {}
                emb2 = obj2.get("embedding")
                if not isinstance(emb2, list):
                    out2.append([])
                else:
                    out2.append([float(x) for x in emb2 if isinstance(x, (int, float))])
            if len(out2) != len(texts):
                return None, "embeddings length mismatch"
            return out2, None
        if r.status_code != 200:
            return None, f"embeddings http {r.status_code}"
        obj = r.json() or {}
        data = obj.get("data")
        if not isinstance(data, list):
            return None, "embeddings invalid response"
        out: List[List[float]] = []
        for it in data:
            if not isinstance(it, dict):
                out.append([])
                continue
            emb = it.get("embedding")
            if isinstance(emb, list):
                out.append([float(x) for x in emb if isinstance(x, (int, float))])
            else:
                out.append([])
        if len(out) != len(texts):
            return None, "embeddings length mismatch"
        return out, None
    except Exception as exc:
        return None, f"embeddings exception: {str(exc)[:120]}"


def _try_embed_texts_local_transformers(
    *,
    model_id: str,
    texts: List[str],
) -> Tuple[Optional[List[List[float]]], Optional[str]]:
    """
    Local CPU embedding fallback using transformers (no Ollama dependency).
    Requires torch + transformers in the environment.
    """
    if not model_id or not texts:
        return None, "missing local model_id/texts"
    try:
        import torch  # type: ignore
        from transformers import AutoModel, AutoTokenizer  # type: ignore
    except Exception as exc:
        return None, f"local embeddings deps missing: {str(exc)[:120]}"

    # Cache model/tokenizer per model_id in-process (big speedup for short videos).
    tok_key = f"tok::{model_id}"
    mod_key = f"mod::{model_id}"
    tok = _LOCAL_CACHE.get(tok_key)
    mod = _LOCAL_CACHE.get(mod_key)
    try:
        if tok is None:
            tok = AutoTokenizer.from_pretrained(model_id)
            _LOCAL_CACHE[tok_key] = tok
        if mod is None:
            mod = AutoModel.from_pretrained(model_id)
            mod.eval()
            _LOCAL_CACHE[mod_key] = mod
    except Exception as exc:
        return None, f"local embeddings load failed: {str(exc)[:160]}"

    # Mean pooling over last hidden state with attention mask.
    # NOTE: Many embedding models prefer a specific prompt format; callers should pass preformatted texts.
    try:
        embs: List[List[float]] = []
        # small batch to keep memory stable on CPU
        bs = 32
        for i in range(0, len(texts), bs):
            batch = [str(x or "") for x in texts[i : i + bs]]
            enc = tok(batch, padding=True, truncation=True, return_tensors="pt")
            with torch.no_grad():
                out = mod(**enc)
                last = out.last_hidden_state  # [B, T, H]
                mask = enc.get("attention_mask")
                if mask is None:
                    pooled = last.mean(dim=1)
                else:
                    m = mask.unsqueeze(-1).to(last.dtype)
                    summed = (last * m).sum(dim=1)
                    denom = m.sum(dim=1).clamp(min=1e-6)
                    pooled = summed / denom
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            for row in pooled.cpu().tolist():
                embs.append([float(x) for x in row])
        if len(embs) != len(texts):
            return None, "local embeddings length mismatch"
        return embs, None
    except Exception as exc:
        return None, f"local embeddings encode failed: {str(exc)[:160]}"


def pairwise_crosslingual_similarity(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    pairs: List[Tuple[str, str]],
    timeout_s: int = 60,
) -> Tuple[List[Optional[float]], Optional[str]]:
    """
    Compute cosine similarity for pairs using one embeddings batch call.
    Returns ([sim|None...], error). Error is best-effort; sims may still be all None.
    """
    if not pairs:
        return [], None
    flat: List[str] = []
    for a, b in pairs:
        flat.append(str(a or ""))
        flat.append(str(b or ""))
    embs, err = try_embed_texts_openai(endpoint=endpoint, model=model, api_key=api_key, texts=flat, timeout_s=timeout_s)
    if not embs:
        # If embeddings endpoints are missing (common on older Ollama), fall back to local transformers embedding.
        if err and "http 404" in err.lower():
            # Default to a strong multilingual embedding model when callers pass an Ollama-style name.
            local_model = model
            # Heuristic: if model looks like an ollama name (contains ":" or is short), prefer a known HF model.
            if ":" in local_model or len(local_model) <= 24:
                local_model = "intfloat/multilingual-e5-small"
                # Apply e5 recommended prefix to improve cross-lingual retrieval quality.
                flat2 = []
                for t in flat:
                    flat2.append("query: " + str(t or ""))
                embs2, err2 = _try_embed_texts_local_transformers(model_id=local_model, texts=flat2)
            else:
                embs2, err2 = _try_embed_texts_local_transformers(model_id=local_model, texts=flat)
            if embs2:
                embs = embs2
                err = None
            else:
                return [None for _ in pairs], (err2 or err)
        else:
            return [None for _ in pairs], err
    sims: List[Optional[float]] = []
    for i in range(0, len(embs), 2):
        sims.append(cosine_sim(embs[i], embs[i + 1]) if (i + 1) < len(embs) else None)
    return sims, err


