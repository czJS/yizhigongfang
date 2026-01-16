#!/usr/bin/env python3
"""
Quality pipeline: WhisperX + local LLM + Coqui/Piper TTS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
import time
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    import torch  # type: ignore
except Exception:  # pragma: no cover
    torch = None  # type: ignore

try:
    import whisperx  # type: ignore
except Exception:
    whisperx = None  # type: ignore

# 复用轻量版的音频/视频/tts 工具

# Ensure project root (/app) is on sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import asr_translate_tts as lite
from scripts.asr_normalize import load_asr_dict, normalize_asr_zh_text
from backend.subtitle_display import build_display_items  # screen-friendly subtitle track


# ----------------------
# Data structures
# ----------------------
@dataclass
class Segment:
    start: float
    end: float
    text: str
    translation: Optional[str] = None
    # Optional TTS script (separate from subtitle translation). When present, TTS should prefer this.
    tts: Optional[str] = None


# ----------------------
# Helpers
# ----------------------
def check_dep():
    missing = []
    if torch is None:
        missing.append("torch 缺失：pip install torch --extra-index-url https://download.pytorch.org/whl/cu121")
    if whisperx is None:
        missing.append("whisperx 缺失：pip install -U whisperx torchaudio")
    return missing


def _format_glossary_hint(glossary: List[Dict[str, Any]], zh_lines: List[str], *, max_items: int = 12) -> str:
    """
    Build a compact glossary hint snippet for the current chunk.
    This is NOT hard enforcement; it's a prompt hint to reduce drift.
    """
    if not glossary:
        return ""
    hit: List[Dict[str, Any]] = []
    for term in glossary:
        src = str(term.get("src") or "").strip()
        tgt = str(term.get("tgt") or "").strip()
        if not src or not tgt:
            continue
        if any(src in (z or "") for z in zh_lines):
            hit.append(term)
    if not hit:
        return ""
    hit = hit[: max(1, int(max_items))]
    lines = []
    for t in hit:
        src = str(t.get("src") or "").strip()
        tgt = str(t.get("tgt") or "").strip()
        if not src or not tgt:
            continue
        forbidden = [str(x).strip() for x in (t.get("forbidden") or []) if str(x).strip()]
        note = str(t.get("note") or "").strip()
        s = f"- {src} -> {tgt}"
        if forbidden:
            s += f" (avoid: {', '.join(forbidden[:4])})"
        if note:
            s += f" # {note}"
        lines.append(s)
    return "Terminology hints (when the Chinese contains the term, prefer the target translation):\n" + "\n".join(lines)


def _build_glossary_variant_map(glossary: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Build zh_variant -> en_target mapping from glossary, including aliases.
    This map is used for placeholder protection (stable) and prompt hints.
    """
    m: Dict[str, str] = {}
    for term in glossary or []:
        if not isinstance(term, dict):
            continue
        src = str(term.get("src") or "").strip()
        tgt = str(term.get("tgt") or "").strip()
        if not src or not tgt:
            continue
        m[src] = tgt
        for a in term.get("aliases") or []:
            aa = str(a or "").strip()
            if aa:
                m[aa] = tgt
    return m


def _protect_glossary_terms(text: str, variant_map: Dict[str, str], *, max_replacements: int = 6) -> tuple[str, List[tuple[str, str]]]:
    """
    Replace zh glossary variants with placeholder tokens and return (protected_text, used[token->tgt]).
    Uses @@GLS..@@ tokens so that lite.restore_entities can restore them robustly.
    """
    if not text or not variant_map:
        return text, []
    out = text
    used: List[tuple[str, str]] = []
    i = 0
    for zh in sorted(variant_map.keys(), key=len, reverse=True):
        if not zh or zh not in out:
            continue
        if max_replacements and len(used) >= int(max_replacements):
            break
        token = f"@@GLS{i:02d}@@"
        i += 1
        out = out.replace(zh, token)
        used.append((token, variant_map[zh]))
    return out, used


def translate_segments_llm(
    segments: List[Segment],
    endpoint: str,
    model: str,
    api_key: str,
    chunk_size: int = 2,
    *,
    context_window: int = 0,
    topic_hint: str = "",
    style_hint: str = "",
    max_words_per_line: int = 0,
    compact_enable: bool = False,
    compact_aggressive: bool = False,
    compact_temperature: float = 0.1,
    compact_max_tokens: int = 96,
    compact_timeout_s: int = 120,
    long_zh_chars: int = 60,
    long_en_words: int = 22,
    long_target_words: int = 18,
    prompt_mode: str = "short",  # short|long
    long_fallback_enable: bool = True,
    long_examples_enable: bool = True,
    glossary: Optional[List[Dict[str, Any]]] = None,
    glossary_prompt_enable: bool = False,
    selfcheck_enable: bool = False,
    mt_json_enable: bool = False,
    context_src_lines: Optional[List[str]] = None,
) -> List[Segment]:
    """Chunked translation using OpenAI-compatible /v1/chat/completions."""
    if not segments:
        return segments
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i : i + n]

    out: List[Segment] = []

    def list_models() -> List[str]:
        try:
            r = requests.get(f"{endpoint}/models", headers=headers, timeout=30)
            if r.status_code != 200:
                return []
            data = r.json() or {}
            items = data.get("data") or []
            ids = []
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    ids.append(str(it["id"]))
            return ids
        except Exception:
            return []

    def post_chat(body: dict) -> requests.Response:
        """
        Ollama runners can occasionally crash under memory pressure (500 + EOF).
        We retry with backoff to allow the server to restart the runner.
        """
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                resp = requests.post(f"{endpoint}/chat/completions", json=body, headers=headers, timeout=180)
                # 500 with runner crash is often transient; allow retry
                if resp.status_code == 500 and "runner" in (resp.text or "").lower():
                    raise RuntimeError(resp.text)
                return resp
            except Exception as exc:
                last_exc = exc
                sleep_s = 1.0 * (2**attempt)
                print(f"[warn] LLM request failed (attempt {attempt+1}/4): {exc}. Retrying in {sleep_s:.1f}s")
                time.sleep(sleep_s)
        raise RuntimeError(
            "LLM request failed repeatedly. This may be due to Ollama runner crashes (OOM/CPU pressure).\n"
            f"- endpoint: {endpoint}\n- model: {body.get('model')}\n"
            "Check Ollama logs: `docker logs yzh-ollama-1 --tail 200`.\n"
            f"Last error: {last_exc}"
        )

    def _selfcheck_lines(zh_lines: List[str], en_lines: List[str]) -> List[str]:
        """
        LLM-only self-check: ask the model to correct issues (person/number/missing facts/terminology).
        Implemented per-line to reduce cross-line contamination; if a request fails, keep the original line.
        """
        if not zh_lines or not en_lines:
            return en_lines
        fixed: List[str] = []
        for zh, en in zip(zh_lines, en_lines):
            zh = (zh or "").strip()
            en0 = (en or "").strip()
            if not zh or not en0:
                fixed.append(en0)
                continue
            prompt = (
                "You are reviewing ONE subtitle translation.\n"
                "If the English line has issues (wrong person/number, missing key facts, wrong term), rewrite it.\n"
                "If it's OK, output the EXACT SAME line as provided.\n"
                "Rules:\n"
                "- ENGLISH ONLY.\n"
                "- ONE LINE ONLY.\n"
                "- No numbering/bullets/extra commentary.\n"
                f"ZH: {zh}\n"
                f"EN: {en0}\n"
            )
            body = {
                "model": model,
                "messages": [{"role": "system", "content": "Subtitle translation quality reviewer."}, {"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 128,
                "options": {"num_ctx": 2048, "num_batch": 128},
            }
            try:
                resp = post_chat(body)
                if resp.status_code != 200:
                    fixed.append(en0)
                    continue
                content = (resp.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                s = str(content).strip()
                lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
                s = lines[0] if lines else ""
                s = re.sub(r"^\s*[-–•]+\s*", "", s)
                s = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s)
                s = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s).strip()
                s = re.sub(r"\s+", " ", s).strip()
                fixed.append(s or en0)
            except Exception:
                fixed.append(en0)
        return fixed

    def _needs_selfcheck(zh: str, en: str) -> bool:
        """
        Lightweight heuristics to decide whether we should spend an extra LLM call for self-check.
        This is NOT a rule-based rewrite; it only decides whether to invoke an LLM review pass.
        """
        z = (zh or "").strip()
        e = (en or "").strip()
        if not z or not e:
            return False
        low = e.lower()
        # obvious person drift / narration drift
        if re.search(r"\b(i|me|my|we|our|us)\b", low):
            return True
        # common plural subject drift for "女人/她" style narration
        if ("女人" in z or "她" in z) and re.search(r"\bwomen\b", low):
            return True
        # incomplete fragments (common failure mode)
        if e.endswith((",", ";", ":")):
            return True
        if re.search(r"\b(and|or|but|to|of|with|for)$", low):
            return True
        if len(z) >= 12 and len(e) <= 12:
            return True
        return False

    def _ok_en_line(s: str) -> bool:
        t = (s or "").strip()
        if not t:
            return False
        if "\n" in t or "\r" in t:
            return False
        if len(t) > 180:
            return False
        if re.search(r"\b(and|or|but|to|of|with|for)$", t.lower()):
            return False
        return True

    def _translate_one_json(line_zh: str, *, ctx_block: str = "", topic: str = "", glossary_hint: str = "") -> str:
        parts: List[str] = [
            "Return STRICT JSON ONLY.",
            "Schema: {\"final\": \"...\"}",
            "Rules:",
            "- Translate ONLY the SRC line (do not translate context).",
            "- ENGLISH ONLY in final.",
            "- ONE LINE ONLY.",
            "- Preserve any placeholder tokens like @@ENTAA@@ verbatim.",
        ]
        if topic:
            parts.append(f"Topic hint: {topic}")
        if glossary_hint:
            parts.append(glossary_hint)
        parts.append(f"SRC: {line_zh.strip()}")
        if ctx_block:
            parts.append(ctx_block)
        user = "\n".join(parts) + "\n"
        body = {
            "model": model,
            "messages": [{"role": "system", "content": "You output strict JSON only."}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": 180,
            "options": {"num_ctx": 2048, "num_batch": 128},
        }
        resp = post_chat(body)
        if resp.status_code != 200:
            raise RuntimeError(resp.text)
        content = (resp.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        obj = json.loads(str(content).strip())
        if not isinstance(obj, dict):
            raise ValueError("mt-json not an object")
        out = str(obj.get("final") or "").strip()
        out = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", out).strip()
        out = re.sub(r"\s+", " ", out).strip()
        if not _ok_en_line(out):
            raise ValueError("mt-json produced invalid line")
        return out

    # make context lines available (prefer original zh lines, not protected placeholders)
    ctx_lines = context_src_lines if (context_src_lines and len(context_src_lines) == len(segments)) else [s.text for s in segments]

    # Best-practice stability: when context/selfcheck/mt-json is enabled, do per-line requests to reduce cross-line contamination.
    effective_chunk_size = 1 if (max(0, int(context_window or 0)) > 0 or bool(selfcheck_enable) or bool(mt_json_enable)) else max(1, int(chunk_size or 1))

    for group in chunks(list(enumerate(segments)), effective_chunk_size):
        idxs = [i for i, _ in group]
        segs = [s for _, s in group]
        src_lines = [s.text.strip() for s in segs]
        zh_for_hint = [ctx_lines[i].strip() for i in idxs]
        glossary_hint = ""
        if glossary_prompt_enable and glossary:
            glossary_hint = _format_glossary_hint(glossary, zh_for_hint)
        topic_hint = (topic_hint or "").strip()
        cw = max(0, int(context_window or 0))
        ctx_blocks: List[str] = []
        if cw > 0:
            for i in idxs:
                prev = ctx_lines[i - 1].strip() if i - 1 >= 0 else ""
                nxt = ctx_lines[i + 1].strip() if i + 1 < len(ctx_lines) else ""
                ctx_blocks.append(f"prev: {prev}\nnext: {nxt}")
        else:
            ctx_blocks = ["" for _ in idxs]

        cleaned: List[str] = []
        if mt_json_enable:
            for k in range(len(src_lines)):
                ctx_block = ""
                if cw > 0:
                    ctx_block = f"[context]\\n{ctx_blocks[k]}\\n[/context]"
                try:
                    cleaned.append(_translate_one_json(src_lines[k], ctx_block=ctx_block, topic=topic_hint, glossary_hint=glossary_hint))
                except Exception:
                    cleaned.append("")
        else:
            style = (style_hint or "").strip()
            pm = (prompt_mode or "short").strip().lower()
            pm = "long" if pm == "long" else "short"
            long_fb = bool(long_fallback_enable)

            def _build_style_block(*, long: bool) -> str:
                """
                Two-stage prompt strategy:
                - short: minimal constraints (fast path)
                - long: full best-practice rules + self-check + optional examples (fallback path)
                """
                if not long:
                    return (
                        "\nStyle:\n"
                        + (f"- {style}\n" if style else "")
                        + "- American English daily dialogue.\n"
                        + "- Conversational, idiomatic, and concise.\n"
                        + "- Avoid overly formal connectors (e.g., moreover/therefore).\n"
                        + "- Preserve tone (question/negation/command/exclamation).\n"
                        + "- NEVER add new facts.\n"
                    )
                blk = (
                    "\nWorkflow (do NOT output intermediate steps):\n"
                    "- Step 1: Rewrite the Chinese subtitle in your head with the SAME meaning. Do NOT add or remove facts.\n"
                    "- Step 2: Translate the rewritten version into American English daily dialogue.\n"
                    "\nStyle requirements:\n"
                    + (f"- {style}\n" if style else "")
                    + "- Make it conversational, idiomatic, and easy for Americans to understand.\n"
                    + "- Keep it concise. Prefer short sentences; avoid long clauses.\n"
                    + "- Avoid overly formal connectors (e.g., moreover/therefore). Prefer casual words (e.g., so/then/actually).\n"
                    + "- Preserve the original tone: questions/negation/commands/exclamations.\n"
                    "\nProhibited (VERY IMPORTANT):\n"
                    + "- Do NOT add new information (reasons, background, opinions) not in the source.\n"
                    + "- Do NOT change facts, time order, causality, person reference, or negation.\n"
                    + "- If uncertain, be conservative and literal rather than making things up.\n"
                    "\nSelf-check (do NOT output):\n"
                    + "- Did you add/remove facts?\n"
                    + "- Is it conversational and concise?\n"
                    + "- Does each output line align 1:1 with the input line?\n"
                )
                if bool(long_examples_enable):
                    blk += (
                        "\nExamples:\n"
                        "ZH: 你别装了，我都看见了。\nEN: Stop pretending. I saw it.\n"
                        "ZH: 现在不是吵架的时候，我们得先走。\nEN: This isn’t the time to argue. We need to go.\n"
                        "ZH: 你到底想说什么？别绕弯子。\nEN: What are you trying to say? Get to the point.\n"
                    )
                return blk

            style_block = _build_style_block(long=(pm == "long"))
            prompt = (
                "You are a professional translator.\n"
                "Translate the following Chinese lines to natural English.\n"
                "Rules:\n"
                "- Output ENGLISH ONLY (no Chinese characters).\n"
                "- Output one line per input line.\n"
                "- Do NOT include numbering, bullets, quotes, or any extra commentary.\n"
                "- Preserve any placeholder tokens like @@ENTAA@@ verbatim.\n"
                + (f"- Keep each output line <= {int(max_words_per_line)} words. If longer, compress while preserving meaning.\n" if int(max_words_per_line or 0) > 0 else "")
                + "- Do NOT add information not present in the source.\n"
                + "- Do NOT change person/number/negation/causality.\n"
                + ("- Context may be provided (prev/next lines). Use it ONLY for disambiguation.\n" if cw > 0 else "")
                + style_block
                + ("\n" + f"Topic hint: {topic_hint}\n" if topic_hint else "")
                + ("\n" + glossary_hint + "\n" if glossary_hint else "")
                + "\n".join(
                    f"{k+1}. {src_lines[k]}"
                    + (f"\n[context]\n{ctx_blocks[k]}\n[/context]" if cw > 0 else "")
                    for k in range(len(src_lines))
                )
            )
            body = {
                "model": model,
                "messages": [{"role": "system", "content": "Translate Chinese to English, keep meaning concise."}, {"role": "user", "content": prompt}],
                "temperature": 0.3,
                # Keep responses small/stable
                "max_tokens": 256,
                # Ollama-specific knob passthrough (safe if ignored)
                "options": {
                    "num_ctx": 2048,
                    "num_batch": 128,
                },
            }
            resp = post_chat(body)
            if resp.status_code != 200:
                # Common: Ollama returns 404 when model isn't pulled. Auto-fallback to an available model.
                if resp.status_code == 404 and "not found" in (resp.text or "").lower():
                    available = list_models()
                    if available:
                        fallback = available[0]
                        print(f"[warn] LLM model '{model}' not found. Falling back to '{fallback}'.")
                        body["model"] = fallback
                        resp = post_chat(body)
                        if resp.status_code != 200:
                            raise RuntimeError(
                                f"LLM translation failed after fallback: {resp.status_code} {resp.text}\n"
                                f"- configured: {model}\n- fallback: {fallback}\n- available: {available}"
                            )
                    else:
                        raise RuntimeError(
                            f"LLM translation failed: {resp.status_code} {resp.text}\n"
                            f"- configured: {model}\n- available: (failed to list, try `ollama list`)"
                        )
                else:
                    raise RuntimeError(f"LLM translation failed: {resp.status_code} {resp.text}")
            content = resp.json()["choices"][0]["message"]["content"]
            # Normalize lines and strip bullets/numbering that LLMs often add.
            lines = [line.strip() for line in content.split("\n") if line.strip()]
            for line in lines:
                s = line.strip()
                s = re.sub(r"^\s*[-–•]+\s*", "", s)
                s = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s)  # "1. xxx", "2) xxx", "3- xxx"
                cleaned.append(s.strip())
            # enforce english-only (strip any CJK/fullwidth chars that may leak from LLM)
            cleaned = [re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s).strip() for s in cleaned]
        # align lengths
        while len(cleaned) < len(segs):
            cleaned.append("")
        cleaned = cleaned[: len(segs)]

        def _fix_bad_en(s: str) -> str:
            """
            Extremely low-risk English post-fix (non-LLM):
            - collapse duplicated short function words (e.g. 'the the', 'a a')
            - remove 'a the' / 'the a' typos
            - collapse whitespace
            """
            t = str(s or "")
            t = re.sub(r"\s+", " ", t).strip()
            if not t:
                return t
            # Common duplicated function words
            t = re.sub(r"\b(the|a|an|to|of|in|on|and|but|or)\s+\1\b", r"\1", t, flags=re.IGNORECASE)
            # Common article bigram typos
            t = re.sub(r"\b(a|an)\s+the\b", "the", t, flags=re.IGNORECASE)
            t = re.sub(r"\bthe\s+(a|an)\b", "the", t, flags=re.IGNORECASE)
            t = re.sub(r"\s+", " ", t).strip()
            return t

        cleaned = [_fix_bad_en(s) for s in cleaned]

        # Stage-2 fallback: if short prompt produced questionable outputs, retry those lines with the long prompt.
        # This follows common production practice: fast path first, expensive prompt only on demand.
        if (not mt_json_enable) and pm == "short" and long_fb:
            def _translate_one_long(line_zh: str, *, ctx_block: str = "") -> str:
                prompt2 = (
                    "You are a professional subtitle translator.\n"
                    "Translate the following Chinese line to American English daily dialogue subtitle.\n"
                    "Rules:\n"
                    "- Output ENGLISH ONLY.\n"
                    "- ONE LINE ONLY.\n"
                    "- Do NOT include numbering/bullets/quotes/extra commentary.\n"
                    "- Preserve any placeholder tokens like @@ENTAA@@ verbatim.\n"
                    + (f"- Keep output <= {int(max_words_per_line)} words (soft budget; do NOT drop key facts).\n" if int(max_words_per_line or 0) > 0 else "")
                    + "- Do NOT add new facts.\n"
                    + "- Do NOT change negation/causality/person reference.\n"
                    + _build_style_block(long=True)
                    + ("\n" + f"Topic hint: {topic_hint}\n" if topic_hint else "")
                    + ("\n" + glossary_hint + "\n" if glossary_hint else "")
                    + f"\nSRC: {line_zh.strip()}\n"
                    + (f"\n[context]\n{ctx_block}\n[/context]\n" if ctx_block else "")
                )
                body2 = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Translate Chinese to English subtitles. Be natural and faithful."},
                        {"role": "user", "content": prompt2},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 256,
                    "options": {"num_ctx": 2048, "num_batch": 128},
                }
                resp2 = post_chat(body2)
                if resp2.status_code != 200:
                    return ""
                content2 = (resp2.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                # Normalize to single line English
                lines2 = [line.strip() for line in str(content2).split("\n") if line.strip()]
                s2 = lines2[0] if lines2 else ""
                s2 = re.sub(r"^\s*[-–•]+\s*", "", s2)
                s2 = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s2)
                s2 = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s2).strip()
                s2 = re.sub(r"\s+", " ", s2).strip()
                s2 = _fix_bad_en(s2)
                return s2

            for k in range(len(cleaned)):
                zh0 = src_lines[k]
                en0 = cleaned[k]
                # Trigger fallback on invalid line or "needs_selfcheck" heuristics (fragment/too short etc.)
                if (not _ok_en_line(en0)) or _needs_selfcheck(zh0, en0):
                    ctx_block = ""
                    if cw > 0 and k < len(ctx_blocks):
                        ctx_block = ctx_blocks[k]
                    alt = _translate_one_long(zh0, ctx_block=ctx_block)
                    if alt and _ok_en_line(alt):
                        cleaned[k] = alt

        # Optional post-compact pass: if a line exceeds max_words_per_line, ask local LLM to rewrite within budget.
        # Fallback-safe: if LLM fails, keep the original line (do NOT hard-trim here).
        mw = int(max_words_per_line or 0)
        if mw > 0 and bool(compact_enable):
            for j in range(len(cleaned)):
                en = (cleaned[j] or "").strip()
                if not en:
                    continue
                words = [w for w in re.split(r"\s+", en) if w]
                if len(words) <= mw:
                    continue
                zh = src_lines[j] if j < len(src_lines) else ""
                aggressive = bool(compact_aggressive or len(words) > int(mw * 1.5))
                rewritten = _rewrite_en_to_budget_llm(
                    endpoint=endpoint,
                    model=model,
                    api_key=api_key,
                    zh=zh,
                    en=en,
                    max_words=mw,
                    aggressive=aggressive,
                    temperature=float(compact_temperature),
                    max_tokens=int(compact_max_tokens),
                    timeout_s=int(compact_timeout_s),
                )
                if rewritten:
                    cleaned[j] = rewritten

        # Long-line compression (triggered only for extra-long source/translation).
        # Goal: avoid over-fast TTS by compressing just the problematic lines.
        lz = int(long_zh_chars or 0)
        le = int(long_en_words or 0)
        lt = int(long_target_words or 0)
        if lt > 0:
            for j in range(len(cleaned)):
                en = (cleaned[j] or "").strip()
                if not en:
                    continue
                words = [w for w in re.split(r"\s+", en) if w]
                zh = (src_lines[j] or "").strip() if j < len(src_lines) else ""
                zh_len = len(zh)
                if (lz and zh_len >= lz) or (le and len(words) >= le):
                    if len(words) <= lt:
                        continue
                    aggressive = bool(len(words) > int(lt * 1.5))
                    rewritten = _rewrite_en_to_budget_llm(
                        endpoint=endpoint,
                        model=model,
                        api_key=api_key,
                        zh=zh,
                        en=en,
                        max_words=lt,
                        aggressive=aggressive,
                        temperature=float(compact_temperature),
                        max_tokens=int(compact_max_tokens),
                        timeout_s=int(compact_timeout_s),
                    )
                    if rewritten:
                        cleaned[j] = rewritten
                    else:
                        trimmed = _trim_en_to_word_budget(en, max_words=lt, min_words=3)
                        if trimmed:
                            cleaned[j] = trimmed

        # Robustness: if the LLM fails to produce enough lines and we end up with empty translations,
        # retry missing lines individually (chunk_size=1). This avoids blank subtitle segments.
        missing_idxs = [i for i, (src, tr) in enumerate(zip(src_lines, cleaned)) if src.strip() and not tr.strip()]
        if missing_idxs:
            print(f"[warn] LLM returned {len(cleaned) - len(missing_idxs)}/{len(group)} non-empty lines; retrying {len(missing_idxs)} lines individually...")

            def translate_one(line_zh: str) -> str:
                body1 = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Translate Chinese to English, keep meaning concise."},
                        {
                            "role": "user",
                            "content": (
                                "Translate the following Chinese line to natural English.\n"
                                "Rules:\n"
                                "- Output ENGLISH ONLY (no Chinese characters).\n"
                                "- Do NOT include numbering, bullets, quotes, or any extra commentary.\n"
                                f"{line_zh.strip()}"
                            ),
                        },
                    ],
                    "temperature": 0.2,
                    "max_tokens": 128,
                    "options": {"num_ctx": 2048, "num_batch": 128},
                }
                r1 = post_chat(body1)
                if r1.status_code != 200:
                    return ""
                c1 = (r1.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                s1 = str(c1).strip()
                s1 = re.sub(r"^\s*[-–•]+\s*", "", s1)
                s1 = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s1)
                s1 = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s1).strip()
                s1 = re.sub(r"\s+", " ", s1).strip()
                return _fix_bad_en(s1)

            for mi in missing_idxs:
                cleaned[mi] = translate_one(src_lines[mi]) or cleaned[mi]

        # Robustness: retry once for obviously broken English lines (common LLM failure modes).
        def _is_bad_line(src_zh: str, en: str) -> bool:
            s = (en or "").strip()
            if not s:
                return True
            low = s.lower()
            # duplicated function words / obvious article errors
            if re.search(r"\b(the|a|an|to|of|in|on|and|but|or)\s+\1\b", low):
                return True
            if " a the " in f" {low} " or " the a " in f" {low} ":
                return True
            # very short outputs for long Chinese inputs (likely truncation)
            if len(src_zh.strip()) >= 12 and len(s) <= 12:
                return True
            # fragment ending with comma/semicolon (often incomplete)
            if s.endswith((",", ";", ":")):
                return True
            # dangling conjunctions / prepositions (often incomplete)
            if re.search(r"\b(and|or|but|to|of|with|for)$", low):
                return True
            return False

        bad_idxs = [i for i, (src, tr) in enumerate(zip(src_lines, cleaned)) if _is_bad_line(src, tr)]
        # avoid double-retrying the same line that was already retried for emptiness
        bad_idxs = [i for i in bad_idxs if i not in set(missing_idxs)]
        if bad_idxs:
            print(f"[warn] LLM produced {len(bad_idxs)} suspicious lines; retrying once individually...")

            def translate_one_strict(line_zh: str) -> str:
                body1 = {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Translate Chinese to English.\n"
                                "Hard rules:\n"
                                "- Output ENGLISH ONLY.\n"
                                "- Output a COMPLETE sentence (not a fragment).\n"
                                "- Do NOT change the subject's gender/person if unclear; prefer neutral wording.\n"
                                "- No numbering/bullets/extra commentary."
                            ),
                        },
                        {"role": "user", "content": line_zh.strip()},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 160,
                    "options": {"num_ctx": 2048, "num_batch": 128},
                }
                r1 = post_chat(body1)
                if r1.status_code != 200:
                    return ""
                c1 = (r1.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                s1 = str(c1).strip()
                s1 = re.sub(r"^\s*[-–•]+\s*", "", s1)
                s1 = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s1)
                s1 = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s1).strip()
                s1 = re.sub(r"\s+", " ", s1).strip()
                return _fix_bad_en(s1)

            for bi in bad_idxs:
                improved = translate_one_strict(src_lines[bi])
                if improved and not _is_bad_line(src_lines[bi], improved):
                    cleaned[bi] = improved

        if selfcheck_enable:
            # Only self-check suspicious lines to keep cost low and avoid unnecessary rewrites.
            fixed = list(cleaned)
            for j, (zhj, enj) in enumerate(zip(zh_for_hint, cleaned)):
                if _needs_selfcheck(zhj, enj):
                    fixed[j] = _selfcheck_lines([zhj], [enj])[0]
            cleaned = [_fix_bad_en(s) for s in fixed]

        for seg, tr in zip(segs, cleaned):
            out.append(Segment(start=seg.start, end=seg.end, text=seg.text, translation=tr))
    return out


def translate_segments_llm_tra(
    segments: List[Segment],
    endpoint: str,
    model: str,
    api_key: str,
    chunk_size: int = 2,
    *,
    save_debug_path: Optional[Path] = None,
    tra_json_enable: bool = False,
    context_window: int = 0,
    topic_hint: str = "",
    style_hint: str = "",
    max_words_per_line: int = 0,
    compact_enable: bool = False,
    compact_aggressive: bool = False,
    compact_temperature: float = 0.1,
    compact_max_tokens: int = 96,
    compact_timeout_s: int = 120,
    long_zh_chars: int = 60,
    long_en_words: int = 22,
    long_target_words: int = 18,
    glossary: Optional[List[Dict[str, Any]]] = None,
    glossary_prompt_enable: bool = False,
    context_src_lines: Optional[List[str]] = None,
) -> tuple[List[Segment], Dict[str, Any]]:
    """
    P1: 3-step Translate-Reflect-Adapt (TRA), with strict line alignment and fallback.

    Output contract:
    - final output is ENGLISH ONLY
    - one line per input line
    - if any step fails repeatedly, falls back to faithful output for that chunk
    """
    # New implementation: run TRA per-line (chunk_size forced to 1 internally) to guarantee strict 1:1.
    if not segments:
        return segments, {"version": 2, "enabled": True, "items": []}

    # Context lines are based on original Chinese segments (not placeholders) when provided.
    ctx_lines = context_src_lines if (context_src_lines and len(context_src_lines) == len(segments)) else [s.text for s in segments]

    # If TRA JSON is enabled, we do a single structured call per segment to get faithful/issues/final,
    # which improves stability (no line alignment problems) and reduces drift.
    faithful_segs: List[Segment] = []
    if not tra_json_enable:
        faithful_segs = translate_segments_llm(
            [Segment(start=s.start, end=s.end, text=s.text) for s in segments],
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            chunk_size=1,
            context_window=context_window,
            topic_hint=topic_hint,
            glossary=glossary,
            glossary_prompt_enable=glossary_prompt_enable,
            selfcheck_enable=False,
            long_zh_chars=long_zh_chars,
            long_en_words=long_en_words,
            long_target_words=long_target_words,
            context_src_lines=context_src_lines,
        )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def post_chat(body: dict) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                resp = requests.post(f"{endpoint}/chat/completions", json=body, headers=headers, timeout=240)
                if resp.status_code == 500 and "runner" in (resp.text or "").lower():
                    raise RuntimeError(resp.text)
                return resp
            except Exception as exc:
                last_exc = exc
                sleep_s = 1.0 * (2**attempt)
                print(f"[warn] LLM request failed (attempt {attempt+1}/4): {exc}. Retrying in {sleep_s:.1f}s")
                time.sleep(sleep_s)
        raise RuntimeError(f"LLM request failed repeatedly: {last_exc}")

    def _clean_one_line(content: str) -> str:
        s = str(content or "").strip()
        if not s:
            return ""
        # Take first non-empty line only (strict 1-line output).
        lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
        s = lines[0] if lines else ""
        s = re.sub(r"^\s*[-–•]+\s*", "", s)
        s = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s)
        # strip cjk/fullwidth
        s = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _call_one(system: str, user: str, *, temperature: float, max_tokens: int) -> str:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "options": {"num_ctx": 2048, "num_batch": 128},
        }
        resp = post_chat(body)
        if resp.status_code != 200:
            raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text}")
        content = (resp.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        return _clean_one_line(str(content))

    debug: Dict[str, Any] = {"version": 4, "enabled": True, "items": [], "tra_json_enable": bool(tra_json_enable)}
    out: List[Segment] = []
    fallback_n = 0

    for idx0, src_seg in enumerate(segments):
        zh = (src_seg.text or "").strip()
        cw = max(0, int(context_window or 0))
        prev = ctx_lines[idx0 - 1].strip() if (idx0 - 1 >= 0 and cw > 0) else ""
        nxt = ctx_lines[idx0 + 1].strip() if (idx0 + 1 < len(ctx_lines) and cw > 0) else ""
        glossary_hint = _format_glossary_hint(glossary or [], [zh]) if (glossary_prompt_enable and glossary) else ""
        topic = (topic_hint or "").strip()

        faithful = ""
        final = ""
        note = "OK"

        if tra_json_enable:
            # Structured output (JSON only) for stability; fallback to single-step translation on parse failure.
            parts: List[str] = [
                "Return STRICT JSON ONLY.",
                "Produce a JSON object with keys: faithful, issues, final.",
                "- faithful: a faithful literal translation as ONE COMPLETE sentence (no dangling conjunctions like 'and/to/of')",
                "- issues: an array of short strings describing problems in faithful (or empty)",
                "- final: a natural subtitle line with MINIMAL edits, preserving facts and terminology",
                "Rules:",
                "- ENGLISH ONLY in values",
                "- ONE LINE ONLY in faithful and final",
                "- DO NOT include information from CONTEXT_PREV/CONTEXT_NEXT; context is for disambiguation ONLY",
                "- DO NOT merge multiple subtitle lines; translate SRC line only",
                "- final must not change person/number unless explicitly stated",
                "- IMPORTANT: Do NOT add new information (reasons/background/opinions) not in SRC",
                "- Preserve tone (question/negation/command/exclamation)",
            ]
            style = (style_hint or "").strip()
            if style:
                parts.append(f"Style: {style}")
                parts.append("Prefer American English daily dialogue; be conversational and concise.")
                if int(max_words_per_line or 0) > 0:
                    parts.append(f"final must be <= {int(max_words_per_line)} words.")
            if topic:
                parts.append(f"Topic hint: {topic}")
            if glossary_hint:
                parts.append(glossary_hint)
            parts.append(f"SRC: {zh}")
            if cw > 0:
                parts.append(f"CONTEXT_PREV: {prev}")
                parts.append(f"CONTEXT_NEXT: {nxt}")
            user = "\n".join(parts) + "\n"
            body = {
                "model": model,
                "messages": [{"role": "system", "content": "You output strict JSON only."}, {"role": "user", "content": user}],
                "temperature": 0.1,
                "max_tokens": 220,
                "options": {"num_ctx": 2048, "num_batch": 128},
            }
            try:
                resp = post_chat(body)
                if resp.status_code != 200:
                    raise RuntimeError(resp.text)
                content = (resp.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                obj = json.loads(str(content).strip())
                if not isinstance(obj, dict):
                    raise ValueError("TRA JSON not an object")
                note_items = obj.get("issues")
                if isinstance(note_items, list):
                    note = "; ".join([str(x).strip() for x in note_items if str(x).strip()][:4]) or "OK"
                else:
                    note = "OK"
                faithful = str(obj.get("faithful") or "").strip()
                final = str(obj.get("final") or "").strip()
                faithful = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", faithful).strip()
                final = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", final).strip()
                faithful = re.sub(r"\s+", " ", faithful).strip()
                final = re.sub(r"\s+", " ", final).strip()
                final = final or faithful
                # Validate structure: one line, non-empty, and not overly long (avoid cross-line merges).
                def _ok_line(s: str) -> bool:
                    t = (s or "").strip()
                    if not t:
                        return False
                    if "\n" in t or "\r" in t:
                        return False
                    if len(t) > 180:
                        return False
                    # avoid dangling fragments
                    if re.search(r"\b(and|or|but|to|of|with|for)$", t.lower()):
                        return False
                    return True

                if not _ok_line(faithful) or not _ok_line(final):
                    raise ValueError("TRA JSON produced invalid lines")
            except Exception:
                debug.setdefault("tra_json_fail_n", 0)
                debug["tra_json_fail_n"] = int(debug["tra_json_fail_n"]) + 1
                # Fallback: reuse robust single-step translator with context/topic/glossary prompt
                fallback_seg = translate_segments_llm(
                    [Segment(start=src_seg.start, end=src_seg.end, text=src_seg.text)],
                    endpoint=endpoint,
                    model=model,
                    api_key=api_key,
                    chunk_size=1,
                    context_window=context_window,
                    topic_hint=topic_hint,
                    style_hint=style_hint,
                    max_words_per_line=max_words_per_line,
                    compact_enable=compact_enable,
                    compact_aggressive=compact_aggressive,
                    compact_temperature=compact_temperature,
                    compact_max_tokens=compact_max_tokens,
                    compact_timeout_s=compact_timeout_s,
                    long_zh_chars=long_zh_chars,
                    long_en_words=long_en_words,
                    long_target_words=long_target_words,
                    glossary=glossary,
                    glossary_prompt_enable=glossary_prompt_enable,
                    selfcheck_enable=False,
                    context_src_lines=ctx_lines,
                )[0]
                faithful = (fallback_seg.translation or "").strip()
                final = faithful
                fallback_n += 1
        else:
            # Non-JSON TRA: faithful comes from step1 translator
            fseg = faithful_segs[idx0] if idx0 < len(faithful_segs) else Segment(start=src_seg.start, end=src_seg.end, text=src_seg.text, translation="")
            faithful = (fseg.translation or "").strip()
            if not faithful:
                fallback_n += 1
                final = faithful
                note = "EMPTY_FAITHFUL"
            else:
                # Reflect (one-liner)
                note = _call_one(
                    system="You are a subtitle translation reviewer. Output ONE short English sentence or 'OK'.",
                    user=(
                        "Review the faithful translation and point out issues briefly.\n"
                        "Rules: ENGLISH ONLY, ONE LINE ONLY, no numbering/bullets.\n"
                        f"SRC: {zh}\nFAITHFUL: {faithful}\n"
                    ),
                    temperature=0.2,
                    max_tokens=96,
                )
                if not note:
                    note = "OK"
                # Adapt (one-liner)
                final = _call_one(
                    system="You rewrite faithful translation into a natural English subtitle. Output ONE line only.",
                    user=(
                        "Polish the faithful translation into natural English subtitle with MINIMAL edits.\n"
                        "Rules:\n"
                        "- ENGLISH ONLY\n"
                        "- ONE LINE ONLY\n"
                        "- No numbering/bullets/extra commentary\n"
                        "- Do NOT omit key facts\n"
                        "- Keep wording as close to FAITHFUL as possible; only fix grammar/clarity/terminology\n"
                        + (f"- Keep final <= {int(max_words_per_line)} words by compressing if needed.\n" if int(max_words_per_line or 0) > 0 else "")
                        + (f"- Style: {style_hint.strip()}\n" if (style_hint or "").strip() else "")
                        + "- Prefer American English daily dialogue; be conversational and concise.\n"
                        + "- Avoid overly formal connectors (e.g., moreover/therefore). Prefer casual words (e.g., so/then/actually).\n"
                        + "- Preserve tone (question/negation/command/exclamation).\n"
                        + "- Do NOT add new information not present in SRC.\n"
                        f"SRC: {zh}\nFAITHFUL: {faithful}\nNOTE: {note}\n"
                    ),
                    temperature=0.2,
                    max_tokens=160,
                )
                if not final:
                    fallback_n += 1
                    final = faithful

        # Optional post-compact on TRA final: keep within word budget, friendlier than hard trim.
        mw = int(max_words_per_line or 0)
        if mw > 0 and bool(compact_enable) and final:
            words = [w for w in re.split(r"\s+", str(final).strip()) if w]
            if len(words) > mw:
                aggressive = bool(compact_aggressive or len(words) > int(mw * 1.5))
                rewritten = _rewrite_en_to_budget_llm(
                    endpoint=endpoint,
                    model=model,
                    api_key=api_key,
                    zh=zh,
                    en=final,
                    max_words=mw,
                    aggressive=aggressive,
                    temperature=float(compact_temperature),
                    max_tokens=int(compact_max_tokens),
                    timeout_s=int(compact_timeout_s),
                )
                if rewritten:
                    final = rewritten

        # Long-line compression for TRA final (only for extra-long source/translation).
        lz = int(long_zh_chars or 0)
        le = int(long_en_words or 0)
        lt = int(long_target_words or 0)
        if lt > 0 and final:
            words = [w for w in re.split(r"\s+", str(final).strip()) if w]
            zh_len = len(str(zh or "").strip())
            if ((lz and zh_len >= lz) or (le and len(words) >= le)) and len(words) > lt:
                aggressive = bool(len(words) > int(lt * 1.5))
                rewritten = _rewrite_en_to_budget_llm(
                    endpoint=endpoint,
                    model=model,
                    api_key=api_key,
                    zh=zh,
                    en=final,
                    max_words=lt,
                    aggressive=aggressive,
                    temperature=float(compact_temperature),
                    max_tokens=int(compact_max_tokens),
                    timeout_s=int(compact_timeout_s),
                )
                if rewritten:
                    final = rewritten
                else:
                    trimmed = _trim_en_to_word_budget(str(final), max_words=lt, min_words=3)
                    if trimmed:
                        final = trimmed

        out.append(Segment(start=src_seg.start, end=src_seg.end, text=src_seg.text, translation=final))
        debug["items"].append(
            {
                "start": float(src_seg.start),
                "end": float(src_seg.end),
                "zh": src_seg.text,
                "faithful": faithful,
                "reflect": note,
                "final": final,
            }
        )

    debug["fallback_lines"] = int(fallback_n)
    if save_debug_path is not None:
        try:
            save_debug_path.write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return out, debug


def split_segments_for_subtitles(segments: List[Segment], max_chars: int = 50) -> List[Segment]:
    """
    Split long segments into smaller ones for better subtitles/translation.
    Uses punctuation-first, then hard split by character length.
    """
    if not segments:
        return segments

    def split_text(text: str) -> List[str]:
        t = re.sub(r"\s+", " ", text).strip()
        if not t:
            return [""]
        if len(t) <= max_chars:
            return [t]
        # split by common Chinese/English punctuation
        parts = [p.strip() for p in re.split(r"(?<=[。！？；，,\.!?;])\s*", t) if p.strip()]
        out: List[str] = []
        for p in parts:
            if len(p) <= max_chars:
                out.append(p)
            else:
                # hard split long chunk
                for i in range(0, len(p), max_chars):
                    chunk = p[i : i + max_chars].strip()
                    if chunk:
                        out.append(chunk)
        return out or [t]

    out_segments: List[Segment] = []
    for seg in segments:
        pieces = split_text(seg.text)
        if len(pieces) <= 1:
            out_segments.append(seg)
            continue
        total = sum(max(len(p), 1) for p in pieces)
        dur = max(seg.end - seg.start, 0.001)
        cursor = seg.start
        for i, p in enumerate(pieces):
            frac = max(len(p), 1) / total
            piece_dur = dur * frac
            # last piece ends exactly at seg.end
            end = seg.end if i == len(pieces) - 1 else cursor + piece_dur
            out_segments.append(Segment(start=float(cursor), end=float(end), text=p))
            cursor = end
    return out_segments


def meaning_split_segments(segments: List[Segment], min_chars: int = 60, max_parts: int = 3) -> List[Segment]:
    """
    Split only overly long segments using punctuation-first strategy.
    Cap max parts to keep alignment stable. If split generates too many parts,
    merge the tail back into the last part (fail-safe).
    """
    if not segments:
        return segments
    out: List[Segment] = []
    min_chars = int(min_chars or 0)
    max_parts = max(1, int(max_parts or 1))
    for seg in segments:
        text = (seg.text or "").strip()
        if not text or len(text) < min_chars:
            out.append(seg)
            continue
        # Derive a max_chars budget to limit splits to max_parts.
        per = max(int(round(len(text) / float(max_parts))), 10)
        pieces = split_segments_for_subtitles([seg], max_chars=per)
        if len(pieces) <= max_parts:
            out.extend(pieces)
            continue
        # Merge tail pieces into the last allowed part
        head = pieces[: max_parts - 1]
        tail = pieces[max_parts - 1 :]
        if not tail:
            out.extend(pieces)
            continue
        merged_text = " ".join([p.text for p in tail if p.text]).strip()
        merged = Segment(
            start=tail[0].start,
            end=tail[-1].end,
            text=merged_text,
        )
        out.extend(head)
        out.append(merged)
    return out


# ----------------------
# P0: Subtitle post-process + TTS script separation
# ----------------------
_WS_RE = re.compile(r"\s+")
_BRACKET_RE = re.compile(r"(\([^)]*\)|\[[^\]]*\]|\{[^}]*\}|（[^）]*）|【[^】]*】|《[^》]*》)")


def _normalize_en_line(s: str) -> str:
    # collapse whitespace/newlines
    return _WS_RE.sub(" ", (s or "").replace("\n", " ")).strip()


def _wrap_en_for_subtitle(s: str, *, max_chars_per_line: int, max_lines: int = 2) -> str:
    """
    Soft-wrap English into <= max_lines lines to reduce long-line warnings.
    NOTE: CPS is computed with newlines replaced by spaces, so wrapping does NOT reduce CPS.
    """
    t = _normalize_en_line(s)
    if not t or max_lines <= 1 or len(t) <= max_chars_per_line:
        return t
    words = t.split(" ")
    if len(words) <= 1:
        # hard wrap
        return "\n".join([t[i : i + max_chars_per_line] for i in range(0, min(len(t), max_chars_per_line * max_lines), max_chars_per_line)])
    # Greedy pack words into lines
    lines: List[str] = []
    cur: List[str] = []
    for w in words:
        if not cur:
            cur = [w]
            continue
        cand = (" ".join(cur + [w])).strip()
        # If we already have max_lines-1 finished lines, keep everything in the last line
        # (we will clamp it later). This avoids creating more lines than allowed.
        if len(lines) >= max_lines - 1:
            cur.append(w)
            continue
        if len(cand) <= max_chars_per_line:
            cur.append(w)
        else:
            lines.append(" ".join(cur).strip())
            cur = [w]
    if cur:
        lines.append(" ".join(cur).strip())
    # Trim to max_lines and max_chars_per_line per line (hard clamp, best-effort)
    lines = [ln.strip() for ln in lines if ln.strip()][:max_lines]
    clamped: List[str] = []
    for ln in lines:
        if len(ln) <= max_chars_per_line:
            clamped.append(ln)
        else:
            clamped.append(ln[: max_chars_per_line - 1].rstrip() + "…")
    return "\n".join(clamped).strip()


def _enforce_max_cps_by_extending(
    segments: List[Segment],
    *,
    max_cps: float,
    safety_gap_s: float = 0.2,
    audio_total_s: Optional[float] = None,
) -> Dict[str, float]:
    """
    Reduce CPS warnings without changing segment count/text:
    - If cps > max_cps, extend segment end time into the following gap (without overlapping next start).
    - This preserves 1:1 mapping with chs.srt and is safe for review workflows.
    """
    fixed = 0
    extended_s = 0.0
    if not segments or not max_cps or max_cps <= 0:
        return {"fixed": 0, "extended_s": 0.0}
    for i, seg in enumerate(segments):
        txt = _normalize_en_line(seg.translation or "")
        if not txt:
            continue
        dur = max(float(seg.end) - float(seg.start), 0.001)
        cps = len(txt) / dur
        if cps <= max_cps:
            continue
        need_dur = len(txt) / max_cps
        need = max(need_dur - dur, 0.0)
        if need <= 0:
            continue
        if i < len(segments) - 1:
            next_start = float(segments[i + 1].start)
            headroom = max(0.0, next_start - float(seg.end) - float(safety_gap_s))
        else:
            if audio_total_s is not None:
                headroom = max(0.0, float(audio_total_s) - float(seg.end))
            else:
                headroom = 0.0
        inc = min(need, headroom)
        if inc <= 1e-6:
            continue
        seg.end = float(seg.end) + inc
        fixed += 1
        extended_s += inc
    return {"fixed": float(fixed), "extended_s": float(extended_s)}


def _build_tts_script(en: str) -> str:
    """
    Minimal "TTS稿" generation (P0):
    - strip bracketed asides
    - normalize whitespace
    - keep punctuation (for pauses)
    - ensure it isn't empty after cleaning
    """
    t = str(en or "")
    t = t.replace("&", " and ")
    t = _BRACKET_RE.sub(" ", t)
    t = _normalize_en_line(t)
    # keep sentence ending punctuation for better prosody
    if t and not re.search(r"[.!?]$", t):
        t = t + "."
    # final cleanup using the shared lite cleaner (removes CJK/fullwidth and junk)
    try:
        t = lite.clean_tts_text(t)  # type: ignore[attr-defined]
    except Exception:
        t = _normalize_en_line(t)
    return t


def _estimate_en_seconds(text: str, *, wps: float = 2.6) -> float:
    """
    Extremely lightweight speaking-time estimator for English:
    - base: words / wps
    - pauses: commas/semicolons/colons add 0.12s; sentence end punctuation adds 0.22s
    This is intentionally conservative & stable (no ML/LLM).
    """
    t = _normalize_en_line(text)
    if not t:
        return 0.0
    words = [w for w in re.split(r"\s+", t) if w]
    base = (len(words) / max(float(wps), 0.5)) if words else 0.0
    pauses = 0.12 * len(re.findall(r"[,;:]", t)) + 0.22 * len(re.findall(r"[.!?]", t))
    return float(base + pauses)


def _trim_en_to_word_budget(text: str, *, max_words: int, min_words: int = 3) -> str:
    """
    Rule-based trimming that tries to keep the beginning intact and cut at a nearby punctuation boundary.
    Output is cleaned via lite.clean_tts_text to avoid feeding junk to TTS.
    """
    t = _normalize_en_line(text)
    if not t:
        return ""
    tokens = [w for w in t.split(" ") if w]
    if len(tokens) <= int(max_words):
        return t
    keep = max(int(min_words), min(int(max_words), len(tokens)))
    cut = keep
    window = 6
    for j in range(max(1, keep - window), keep + 1):
        if j <= 1 or j >= len(tokens):
            continue
        if re.search(r"[.!?]$", tokens[j - 1]) or re.search(r"[,;:]$", tokens[j - 1]):
            cut = j
    out = " ".join(tokens[:cut]).strip()
    out = re.sub(r"[,:;]+$", "", out).strip()
    if out and not re.search(r"[.!?]$", out):
        out = out + "."
    try:
        out = lite.clean_tts_text(out)  # type: ignore[attr-defined]
    except Exception:
        out = _normalize_en_line(out)
    return out


def _clean_en_one_line(content: str) -> str:
    """
    Best-effort cleanup for LLM outputs:
    - take first non-empty line
    - strip bullets/numbering
    - remove CJK/fullwidth characters
    - normalize whitespace
    """
    s = str(content or "").strip()
    if not s:
        return ""
    lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
    s = lines[0] if lines else ""
    s = re.sub(r"^\s*[-–•]+\s*", "", s)
    s = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", s)
    s = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _rewrite_en_to_budget_llm(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    zh: str,
    en: str,
    max_words: int,
    aggressive: bool,
    temperature: float = 0.1,
    max_tokens: int = 96,
    timeout_s: int = 120,
) -> str:
    """
    Use local LLM (OpenAI-compatible /v1/chat/completions) to rewrite an English line to fit within max_words.
    This is used as a friendlier alternative to hard word trimming when a line is over-budget.
    Fallback-safe: caller should verify constraints and fall back to rule-based trimming if needed.
    """
    try:
        import requests  # type: ignore
    except Exception:
        return ""
    max_words = max(int(max_words), 1)
    en0 = _normalize_en_line(en)
    zh0 = (zh or "").strip()
    if not en0:
        return ""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    sys_prompt = "You rewrite English subtitles to be shorter while preserving meaning and natural speech."
    user_parts: List[str] = [
        f"Rewrite the English subtitle to fit within {max_words} words.",
        "Rules:",
        "- Output ENGLISH ONLY.",
        "- Output ONE LINE ONLY.",
        f"- Word count MUST be <= {max_words}.",
        "- Preserve numbers, names, and negation.",
        "- Preserve intent: question/command/emphasis.",
        "- Remove fillers, redundancies, and side comments.",
        "- Do NOT add new information.",
        "- Make it a COMPLETE sentence (avoid dangling fragments like 'and/to/of').",
        "- Prefer conversational subtitle style: short, natural, spoken.",
        "- Avoid formal connectors (e.g., moreover/therefore); prefer so/then/actually/just.",
    ]
    if aggressive:
        user_parts += [
            "- Aggressive mode: you MAY drop secondary details, but keep the main event and result.",
        ]
    else:
        user_parts += [
            "- Prefer minimal rewriting; preserve details unless necessary to fit the limit.",
        ]
    if zh0:
        user_parts += [
            "",
            "Chinese meaning reference (do NOT output Chinese):",
            f"SRC_ZH: {zh0}",
        ]
    user_parts += [
        "",
        f"ORIGINAL_EN: {en0}",
        "OUTPUT_EN:",
    ]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": "\n".join(user_parts)}],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "options": {"num_ctx": 2048, "num_batch": 128},
    }

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(f"{endpoint}/chat/completions", json=body, headers=headers, timeout=int(timeout_s))
            if resp.status_code != 200:
                raise RuntimeError(resp.text)
            content = (resp.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            s = _clean_en_one_line(str(content))
            if not s:
                return ""
            # Enforce budget strictly.
            words = [w for w in s.split(" ") if w]
            if len(words) > max_words:
                return ""
            return s
        except Exception as exc:
            last_exc = exc
            time.sleep(1.0 * (2**attempt))
    _ = last_exc
    return ""

def run_whisperx(
    audio_path: Path,
    model_id: str,
    device: str = "cuda",
    model_dir: Optional[Path] = None,
    diarization: bool = False,
    vad_enable: bool = False,
    vad_thold: Optional[float] = None,
    vad_min_sil_s: Optional[float] = None,
) -> List[Segment]:
    if whisperx is None:
        raise RuntimeError("whisperx 未安装。")
    # Treat env offline flags as authoritative (TaskManager sets these in fully-local mode).
    env_offline = os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    def resolve_local_snapshot(root: Optional[Path], repo_id: str, required_files: Optional[List[str]] = None) -> Optional[Path]:
        """
        Resolve a local HuggingFace cache snapshot folder under `root`.

        Example repo_id: "Systran/faster-whisper-medium"
        Expected layout:
          root/models--Systran--faster-whisper-medium/snapshots/<hash>/(model.bin, config.json, ...)
        """
        if not root:
            return None
        repo_dir = root / ("models--" + repo_id.replace("/", "--"))
        snap_root = repo_dir / "snapshots"
        if not snap_root.exists():
            return None
        required = required_files or ["model.bin", "config.json"]
        candidates: list[Path] = []
        for snap in sorted(snap_root.iterdir()):
            if not snap.is_dir():
                continue
            if all((snap / f).exists() for f in required):
                candidates.append(snap)
        return candidates[-1] if candidates else None

    def has_local_hf_snapshot(root: Optional[Path], repo_id: str, required_files: List[str]) -> bool:
        """Check whether a HF-style cached snapshot exists locally with required files."""
        snap = resolve_local_snapshot(root, repo_id, required_files=required_files)
        if not snap:
            return False
        return all((snap / f).exists() for f in required_files)

    device = "cuda" if device == "cuda" and torch and torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "float32"

    print("  [2/7][1/4] Loading WhisperX model...")
    # Prefer loading from local snapshot folder to avoid any online HF lookup.
    model_to_load = model_id
    local_files_only = False
    offline_mode = False
    if model_dir:
        # Force HuggingFace/Transformers to use the provided cache dir (do NOT use setdefault,
        # otherwise an existing env value can keep pointing to a different cache).
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(model_dir)
        # HF_HOME is used by transformers/huggingface_hub as a base for caches/configs.
        os.environ["HF_HOME"] = str(model_dir)
    if model_dir:
        # `whisperx.load_model()` accepts either:
        # - repo id, e.g. "Systran/faster-whisper-medium"
        # - shorthand, e.g. "medium" (internally maps to Systran/faster-whisper-medium)
        # - local folder path to a CT2 model snapshot directory
        repo_candidates: List[str] = []
        if "/" in model_id:
            repo_candidates.append(model_id)
        else:
            # Map shorthand to the upstream faster-whisper repo id.
            if model_id.startswith("faster-whisper-"):
                repo_candidates.append(f"Systran/{model_id}")
            else:
                repo_candidates.append(f"Systran/faster-whisper-{model_id}")

        local_snap = None
        for repo_id in repo_candidates:
            local_snap = resolve_local_snapshot(model_dir, repo_id, required_files=["model.bin", "config.json"])
            if local_snap:
                break

        if local_snap:
            model_to_load = str(local_snap)
            local_files_only = True
            # We already have all files locally; keep HF fully offline to avoid DNS stalls.
            os.environ["HF_HUB_OFFLINE"] = "1"
            # Transformers also needs its own offline flag, otherwise it may still retry HF requests.
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            os.environ["HF_DATASETS_OFFLINE"] = "1"
            offline_mode = True
        else:
            # Helpful hint about where we looked.
            for repo_id in repo_candidates:
                print(f"  [warn] 未在本地找到模型缓存：{repo_id}")
                print(f"        已检查目录: {model_dir / ('models--' + repo_id.replace('/', '--'))}")
            if env_offline:
                raise RuntimeError(
                    "当前为全离线模式（HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE=1），且本地缺少 WhisperX 模型缓存。\n"
                    "请先把模型放入 assets/models/whisperx 对应的 models--<repo>/snapshots/<hash>/ 目录。"
                )
            print("        将尝试在线下载（若当前环境无法联网会失败）。")
    if model_dir:
        print(f"        whisperx_model_dir: {model_dir}")
    if local_files_only:
        print(f"        使用本地 WhisperX 模型快照：{model_to_load}")
    else:
        print(f"        使用 WhisperX 模型ID：{model_to_load}")
    t0 = time.time()
    try:
        model = whisperx.load_model(
            model_to_load,
            device=device,
            compute_type=compute_type,
            download_root=str(model_dir) if model_dir else None,
            local_files_only=local_files_only,
        )
    except RuntimeError as exc:  # noqa: PIE786
        msg = str(exc)
        if "model.bin" in msg:
            raise RuntimeError(
                f"WhisperX 模型缺失：{msg}\n"
                f"请确认 {model_dir or '默认缓存目录'} 下存在 model.bin，或联网后重试。\n"
                f"可用镜像示例：HF_ENDPOINT=https://hf-mirror.com python3 -c \"import whisperx; whisperx.load_model('{model_id}', device='{device}', compute_type='{compute_type}', download_root='{model_dir}')\""
            ) from exc
        raise
    print(f"  [2/7][1/4] 模型加载完成，用时 {time.time() - t0:.1f}s")

    print("  [2/7][2/4] 转录中...")
    t1 = time.time()
    # VAD support note:
    # - whisperx.asr.FasterWhisperPipeline.transcribe() does NOT accept vad_* kwargs (it has a fixed signature)
    # - the underlying faster-whisper WhisperModel.transcribe() DOES support vad_filter/vad_parameters
    # We prefer calling the inner model for *all* runs to get finer segments than the pipeline's chunked output.
    inner = getattr(model, "model", None)
    result = None
    if inner is not None and hasattr(inner, "transcribe"):
        try:
            vad_options = None
            if vad_enable:
                try:
                    from faster_whisper.vad import VadOptions  # type: ignore
                    params = {}
                    # This repo's faster-whisper uses VadOptions(onset/offset/..., min_silence_duration_ms=...)
                    if vad_thold is not None:
                        onset = float(vad_thold)
                        params["onset"] = onset
                        # Keep offset lower than onset to avoid cutting off trailing speech.
                        params["offset"] = max(0.1, min(0.35, onset - 0.15))
                    if vad_min_sil_s is not None:
                        params["min_silence_duration_ms"] = int(float(vad_min_sil_s) * 1000)
                    vad_options = VadOptions(**params) if params else VadOptions()
                except Exception as exc:
                    print(f"[warn] Failed to build VadOptions; continuing without VAD: {exc}")
                    vad_options = None
            seg_iter, info = inner.transcribe(  # type: ignore[attr-defined]
                str(audio_path),
                language="zh",
                task="transcribe",
                vad_filter=bool(vad_enable),
                vad_parameters=vad_options,
            )
            segments_raw = []
            for s in seg_iter:
                segments_raw.append(
                    {
                        "start": float(getattr(s, "start", 0.0)),
                        "end": float(getattr(s, "end", 0.0)),
                        "text": str(getattr(s, "text", "")).strip(),
                    }
                )
            result = {"segments": segments_raw, "language": getattr(info, "language", "zh")}
            print(f"  [2/7][2/4] 原始分段数：{len(segments_raw)} (vad={'on' if vad_enable else 'off'})")
        except Exception as exc:
            print(f"[warn] WhisperX inner transcribe failed; falling back to pipeline: {exc}")
            result = None
    if result is None:
        if vad_enable:
            print("[warn] VAD enabled but whisperx pipeline does not expose VAD; running without VAD.")
        result = model.transcribe(str(audio_path), language="zh")
    print(f"  [2/7][2/4] 转录完成，用时 {time.time() - t1:.1f}s")

    segments_raw = result["segments"]

    print("  [2/7][3/4] 加载对齐模型...")
    # Alignment model is typically downloaded from HuggingFace (e.g. wav2vec2). In offline setups,
    # trying to load it causes long HF retry loops. We skip alignment entirely when offline.
    is_offline = offline_mode or env_offline
    align_model_name: Optional[str] = None
    if is_offline:
        # If user has manually cached the align model locally, we can still run alignment offline.
        lang = str(result.get("language", "zh"))
        align_repo: Optional[str] = None
        align_required: List[str] = []
        if lang == "zh":
            align_repo = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"
            # Minimum set needed by Transformers to run Wav2Vec2Processor + model weights.
            align_required = [
                "config.json",
                "preprocessor_config.json",
                "tokenizer_config.json",
                "pytorch_model.bin",
                "vocab.json",
                "special_tokens_map.json",
            ]

        if align_repo and model_dir and has_local_hf_snapshot(model_dir, align_repo, align_required):
            # IMPORTANT: Use the local snapshot folder path as model_name, so transformers will not
            # try any network HEAD/resolve calls for repo files.
            snap = resolve_local_snapshot(model_dir, align_repo, required_files=align_required)
            align_model_name = str(snap) if snap else None
            print(f"  [info] 检测到本地对齐模型缓存：{align_repo}，将继续执行对齐（离线）。")
            if align_model_name:
                print(f"  [info] 使用本地对齐模型快照路径：{align_model_name}")
        else:
            print("  [warn] 当前为离线模式，且未检测到本地对齐模型缓存，跳过对齐步骤（避免 HuggingFace 重试）。")
            segments_out: List[Segment] = []
            for seg in segments_raw:
                segments_out.append(
                    Segment(
                        start=float(seg.get("start", 0.0)),
                        end=float(seg.get("end", 0.0)),
                        text=str(seg.get("text", "")).strip(),
                    )
                )
            return segments_out
    try:
        model_a, metadata = whisperx.load_align_model(
            language_code=result.get("language", "zh"),
            device=device,
            model_name=align_model_name,
            model_dir=str(model_dir) if model_dir else None,
        )
    except Exception as exc:  # pragma: no cover
        # Alignment model often requires extra downloads; in offline setups we gracefully degrade.
        print(f"  [warn] 对齐模型加载失败，将跳过对齐，直接使用原始分段：{exc}")
        segments_out: List[Segment] = []
        for seg in segments_raw:
            segments_out.append(
                Segment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=str(seg.get("text", "")).strip(),
                )
            )
        return segments_out

    print("  [2/7][4/4] 对齐中...")
    try:
        result_aligned = whisperx.align(segments_raw, model_a, metadata, str(audio_path), device=device)
    except Exception as exc:  # pragma: no cover
        print(f"  [warn] 对齐失败，将跳过对齐，直接使用原始分段：{exc}")
        segments_out: List[Segment] = []
        for seg in segments_raw:
            segments_out.append(
                Segment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=str(seg.get("text", "")).strip(),
                )
            )
        return segments_out
    print(f"  [2/7] ASR 对齐后片段数：{len(result_aligned['segments'])}")
    segments_out: List[Segment] = []
    for seg in result_aligned["segments"]:
        segments_out.append(Segment(start=float(seg["start"]), end=float(seg["end"]), text=str(seg.get("text", "")).strip()))
    return segments_out


# ----------------------
# Pipeline
# ----------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Quality pipeline (WhisperX + LLM + SoVITS placeholder)")
    p.add_argument("--video", type=Path, required=True, help="Input video file")
    p.add_argument("--output-dir", type=Path, default=Path("outputs"), help="Directory for outputs")
    p.add_argument("--glossary", type=Path, default=Path("assets/glossary/glossary.json"), help="Glossary JSON path (optional)")
    p.add_argument("--chs-override-srt", type=Path, default=None, help="Override chs.srt content when rerunning MT (review workflow)")
    p.add_argument("--eng-override-srt", type=Path, default=None, help="Override eng.srt content when rerunning TTS (review workflow)")
    p.add_argument("--whisperx-model", default="large-v3", help="WhisperX model id")
    p.add_argument("--whisperx-model-dir", type=Path, default=Path("assets/models/whisperx"), help="WhisperX model cache dir")
    p.add_argument("--diarization", action="store_true", help="Enable diarization (if supported)")
    p.add_argument("--llm-endpoint", default="http://localhost:8000/v1", help="Local LLM endpoint")
    p.add_argument("--llm-model", default="Qwen2.5-7B-Instruct", help="LLM model name")
    p.add_argument("--llm-api-key", default="", help="LLM API key if required")
    p.add_argument("--llm-chunk-size", type=int, default=2, help="How many ASR segments per LLM call (lower is more stable)")
    # MT quality levers (prompt-level, general)
    p.add_argument("--mt-context-window", type=int, default=0, help="Include prev/next Chinese lines as context (0=off, 1=prev+next)")
    p.add_argument("--mt-topic", type=str, default="", help="Optional video/topic hint for translation (prompt only)")
    p.add_argument("--mt-style", type=str, default="", help="Translation style hint (e.g., American English daily dialogue, concise)")
    p.add_argument("--mt-max-words-per-line", type=int, default=0, help="Max words per translated line (0=off). Used for subtitle concision.")
    p.add_argument("--mt-compact-enable", action="store_true", help="If a translated line exceeds max words, use local LLM to rewrite within budget")
    p.add_argument("--mt-compact-aggressive", action="store_true", help="Allow more aggressive compression when compacting over-long lines")
    p.add_argument("--mt-compact-temperature", type=float, default=0.1, help="Temperature for compact rewrite (lower is more stable)")
    p.add_argument("--mt-compact-max-tokens", type=int, default=96, help="Max tokens for compact rewrite response")
    p.add_argument("--mt-compact-timeout-s", type=int, default=120, help="Timeout seconds for compact rewrite request")
    p.add_argument("--mt-long-zh-chars", type=int, default=60, help="Trigger long-line compression when zh chars >= this")
    p.add_argument("--mt-long-en-words", type=int, default=22, help="Trigger long-line compression when en words >= this")
    p.add_argument("--mt-long-target-words", type=int, default=18, help="Target max words after long-line compression")
    # Two-stage prompt strategy (best practice): short prompt by default, long prompt only for bad lines.
    p.add_argument("--mt-prompt-mode", choices=["short", "long"], default="short", help="Prompt mode for MT: short or long (short recommended)")
    p.add_argument("--mt-long-fallback-enable", action="store_true", help="When mt-prompt-mode=short, retry bad lines with a longer prompt")
    p.add_argument("--mt-long-examples-enable", action="store_true", help="Include examples in the long prompt (better quality, slower)")
    p.add_argument("--glossary-prompt-enable", action="store_true", help="Inject glossary hints into translation prompt (not hard enforcement)")
    p.add_argument("--llm-selfcheck-enable", action="store_true", help="Enable LLM self-check pass to improve consistency (prompt-only, fallback-safe)")
    p.add_argument("--mt-json-enable", action="store_true", help="Use structured JSON output for single-step translation (final only); fallback-safe")
    p.add_argument("--mt-topic-auto-enable", action="store_true", help="Auto-generate mt_topic once using local LLM (quality mode only)")
    p.add_argument("--mt-topic-auto-max-segs", type=int, default=20, help="How many first segments to use for topic auto-summary")
    p.add_argument("--qe-enable", action="store_true", help="Enable QE-driven selective fix (local LLM review->fix low-quality lines)")
    p.add_argument("--qe-threshold", type=float, default=3.5, help="QE threshold (1-5); fix when min score < threshold")
    p.add_argument("--qe-save-report", action="store_true", help="Save qe_report.json to output-dir")
    p.add_argument("--glossary-placeholder-enable", action="store_true", help="Protect glossary terms with placeholders before MT, then restore to tgt (stable, offline)")
    p.add_argument("--glossary-placeholder-max", type=int, default=6, help="Max glossary placeholder replacements per segment")
    p.add_argument("--meaning-split-enable", action="store_true", help="Enable semantic splitting for overly long Chinese segments")
    p.add_argument("--meaning-split-min-chars", type=int, default=60, help="Min chars to trigger semantic splitting")
    p.add_argument("--meaning-split-max-parts", type=int, default=3, help="Max parts after semantic splitting")
    p.add_argument("--tts-backend", choices=["coqui", "piper"], default="coqui", help="TTS backend")
    p.add_argument("--piper-model", type=Path, default=Path("assets/models/en_US-amy-low.onnx"), help="Piper ONNX model path")
    p.add_argument("--piper-bin", default="piper", help="Path to piper executable")
    p.add_argument("--coqui-model", default="tts_models/en/ljspeech/tacotron2-DDC", help="Coqui TTS model name")
    p.add_argument("--coqui-device", default="auto", help="Coqui TTS device: auto/cpu/cuda")
    p.add_argument("--sample-rate", type=int, default=16000, help="Sample rate for extraction and TTS export")
    # Denoise during audio extraction (safe fallback in lite.extract_audio: arnndn without model -> anlmdn)
    p.add_argument("--denoise", action="store_true", help="Enable simple denoise during audio extraction (ffmpeg)")
    p.add_argument("--denoise-model", type=Path, default=None, help="Optional ffmpeg arnndn model file path")
    # VAD for WhisperX/faster-whisper transcription (segmentation/silence filtering)
    p.add_argument("--vad-enable", action="store_true", help="Enable VAD filter during WhisperX transcription (faster-whisper)")
    p.add_argument("--vad-thold", type=float, default=None, help="VAD threshold (0-1). Higher is stricter (fewer segments).")
    p.add_argument("--vad-min-dur", type=float, default=None, help="Minimum silence duration (seconds) to split segments.")
    p.add_argument("--max-sentence-len", type=int, default=50, help="Max characters per subtitle segment before splitting")
    p.add_argument("--min-sub-dur", type=float, default=1.8, help="Minimum subtitle duration (seconds)")
    p.add_argument("--tts-split-len", type=int, default=80, help="Max characters per TTS chunk")
    p.add_argument("--tts-speed-max", type=float, default=1.08, help="Max speed-up factor when aligning audio")
    p.add_argument("--tts-align-mode", choices=["atempo", "resample"], default="resample", help="Time-stretch mode for TTS alignment (atempo preserves pitch)")
    # Hard subtitle erase (burned-in subtitles on video frames) - applied during mux (before burn_subtitles).
    p.add_argument("--erase-subtitle-enable", action="store_true", help="Enable burned-in subtitle erase/obscure on source video frames")
    p.add_argument("--erase-subtitle-method", default="delogo", help="Erase method (currently delogo)")
    p.add_argument("--erase-subtitle-coord-mode", default="ratio", choices=["ratio", "px"], help="Coordinate mode for erase region")
    p.add_argument("--erase-subtitle-x", type=float, default=0.0, help="Erase region X (ratio or px)")
    p.add_argument("--erase-subtitle-y", type=float, default=0.78, help="Erase region Y (ratio or px)")
    p.add_argument("--erase-subtitle-w", type=float, default=1.0, help="Erase region width (ratio or px)")
    p.add_argument("--erase-subtitle-h", type=float, default=0.22, help="Erase region height (ratio or px)")
    p.add_argument("--erase-subtitle-blur-radius", type=int, default=12, help="Aggressiveness (mapped to delogo band)")
    p.add_argument("--mode", default="quality", help="Mode flag (quality)")
    p.add_argument("--resume-from", choices=["asr", "mt", "tts", "mux"], default=None, help="Resume from a specific stage")
    p.add_argument("--skip-tts", action="store_true", help="Skip TTS (for ASR/MT only)")
    # P0: subtitle post-process and TTS-script separation (safe defaults: off)
    p.add_argument("--subtitle-postprocess-enable", action="store_true", help="Enable P0 subtitle post-process (wrap + cps-fix)")
    p.add_argument("--subtitle-wrap-enable", action="store_true", help="Enable soft wrap for long English subtitle lines")
    p.add_argument("--subtitle-wrap-max-lines", type=int, default=2, help="Max wrapped lines per subtitle block (when wrap enabled)")
    p.add_argument("--subtitle-max-chars-per-line", type=int, default=80, help="Max chars per line for wrapping (best-effort)")
    p.add_argument("--subtitle-cps-fix-enable", action="store_true", help="Enable CPS fix by extending end timestamps into gaps")
    p.add_argument("--subtitle-max-cps", type=float, default=20.0, help="Max CPS used by CPS-fix (characters per second)")
    p.add_argument("--subtitle-cps-safety-gap", type=float, default=0.2, help="Keep at least this gap (seconds) before next segment")
    p.add_argument("--tts-script-enable", action="store_true", help="Generate eng_tts.srt and use it for TTS")
    p.add_argument("--tts-script-strict-clean-enable", action="store_true", help="Strictly clean TTS script (URLs/emails/units etc.)")

    # P0: display subtitles (extra deliverable; readability-oriented)
    p.add_argument("--display-srt-enable", action="store_true", help="Generate display subtitle srt (readability-oriented)")
    p.add_argument("--display-use-for-embed", action="store_true", help="Use display subtitle for embedding into video")
    p.add_argument("--display-max-chars-per-line", type=int, default=42, help="Display subtitle max chars per line")
    p.add_argument("--display-max-lines", type=int, default=2, help="Display subtitle max lines per block")
    p.add_argument("--display-merge-enable", action="store_true", help="Merge adjacent short blocks for display subtitle")
    p.add_argument("--display-merge-max-gap-s", type=float, default=0.25, help="Max gap seconds to merge for display subtitle")
    p.add_argument("--display-merge-max-chars", type=int, default=80, help="Max merged chars for display subtitle")
    p.add_argument("--display-split-enable", action="store_true", help="Split overly long blocks for display subtitle")
    p.add_argument("--display-split-max-chars", type=int, default=86, help="Split threshold chars for display subtitle")
    # Subtitle burn-in style (hard-sub)
    p.add_argument("--sub-font-name", default="Arial", help="Subtitle font name for hard-burn (best-effort)")
    p.add_argument("--sub-font-size", type=int, default=18, help="Subtitle font size for hard-burn")
    p.add_argument("--sub-outline", type=int, default=1, help="Subtitle outline thickness")
    p.add_argument("--sub-shadow", type=int, default=0, help="Subtitle shadow")
    p.add_argument("--sub-margin-v", type=int, default=24, help="Subtitle vertical margin (pixels)")
    p.add_argument("--sub-alignment", type=int, default=2, help="ASS Alignment (2=bottom-center)")
    # Subtitle placement box (optional): when enabled, subtitles are forced to the center of this box.
    # This takes precedence over alignment/margins.
    p.add_argument("--sub-place-enable", action="store_true", help="Force subtitle position to the center of a user-defined box")
    p.add_argument("--sub-place-coord-mode", default="ratio", choices=["ratio", "px"], help="Coordinate mode for subtitle box")
    p.add_argument("--sub-place-x", type=float, default=0.0, help="Subtitle box X (ratio or px)")
    p.add_argument("--sub-place-y", type=float, default=0.78, help="Subtitle box Y (ratio or px)")
    p.add_argument("--sub-place-w", type=float, default=1.0, help="Subtitle box width (ratio or px)")
    p.add_argument("--sub-place-h", type=float, default=0.22, help="Subtitle box height (ratio or px)")
    # Mux sync (hearing-first): when audio is longer than video
    p.add_argument("--mux-sync-strategy", choices=["slow", "freeze"], default="slow", help="When audio is longer: slow video or freeze last frame")
    p.add_argument("--mux-slow-max-ratio", type=float, default=1.10, help="Max slow-down ratio for whole video (e.g. 1.10 = 10% slower)")
    p.add_argument("--mux-slow-threshold-s", type=float, default=0.05, help="Trigger threshold seconds for applying sync strategy")
    # P1: TTS script fitting (trim TTS script to fit time budget under tts_speed_max; rule-based, stable)
    p.add_argument("--tts-fit-enable", action="store_true", help="Enable P1 TTS fitting (trim TTS script to fit duration)")
    p.add_argument("--tts-fit-wps", type=float, default=2.6, help="Estimated English words per second for fitting")
    p.add_argument("--tts-fit-min-words", type=int, default=3, help="Minimum words to keep when trimming")
    p.add_argument("--tts-fit-save-raw", action="store_true", help="Save eng_tts_raw.srt before trimming (when tts-fit enabled)")
    # Friendlier over-budget handling: use local LLM to rewrite an English line within word budget (fallback-safe)
    p.add_argument("--tts-trim-llm-enable", action="store_true", help="Use local LLM to rewrite over-budget English lines (friendlier than hard trim)")
    p.add_argument("--tts-trim-llm-aggressive", action="store_true", help="Allow aggressive compression when rewriting (may drop details)")
    p.add_argument("--tts-trim-llm-temperature", type=float, default=0.1, help="LLM temperature for rewriting (lower is more stable)")
    p.add_argument("--tts-trim-llm-max-tokens", type=int, default=96, help="Max tokens for rewrite response")
    p.add_argument("--tts-trim-llm-timeout-s", type=int, default=120, help="Timeout seconds for rewrite request")
    # P1-2: per-segment TTS planning (hearing-first)
    p.add_argument("--tts-plan-enable", action="store_true", help="Enable P1-2 TTS time-budget planning (hearing-first)")
    p.add_argument("--tts-plan-safety-margin", type=float, default=0.05, help="Reserved tail margin seconds (planning only)")
    p.add_argument("--tts-plan-min-cap", type=float, default=1.05, help="Minimum speed cap used by planner (planning only)")
    # P1: TRA multi-step translation (Faithful -> Reflect -> Adapt)
    p.add_argument("--tra-enable", action="store_true", help="Enable 3-step Translate-Reflect-Adapt translation")
    p.add_argument("--tra-save-debug", action="store_true", help="Save TRA debug json to tra_debug.json")
    p.add_argument("--tra-json-enable", action="store_true", help="Use structured JSON output for TRA (faithful/issues/final); fallback on parse failure")
    p.add_argument("--tra-auto-enable", action="store_true", help="Auto mode for TRA (experimental)")
    # Sentence-unit merge (general, min-risk). Disabled by default; enable explicitly.
    p.add_argument("--sentence-unit-enable", action="store_true", help="Enable sentence-unit merge before translation (min-risk)")
    p.add_argument("--sentence-unit-min-chars", type=int, default=12, help="Min Chinese chars in a unit before closing on punctuation")
    p.add_argument("--sentence-unit-max-chars", type=int, default=60, help="Max Chinese chars per unit (conservative)")
    p.add_argument("--sentence-unit-max-segs", type=int, default=3, help="Max segments per unit")
    p.add_argument("--sentence-unit-max-gap-s", type=float, default=0.6, help="Do not merge across gaps larger than this (seconds)")
    p.add_argument("--sentence-unit-boundary-punct", type=str, default="。！？!?.,", help="Punctuation that can close a unit when long enough")
    p.add_argument(
        "--sentence-unit-break-words",
        type=str,
        default="",
        help="Comma-separated Chinese discourse break words to avoid merging across (e.g. 但,而,于是,然后).",
    )
    # ASR text normalization (extremely low-risk). Enabled by config; can be disabled for debugging.
    p.add_argument("--asr-normalize-enable", action="store_true", help="Enable low-risk Chinese ASR text normalization")
    p.add_argument(
        "--asr-normalize-dict",
        type=Path,
        default=Path("assets/asr_normalize/asr_zh_dict.json"),
        help="Optional JSON dictionary for known ASR typos (defaults to an empty dict file)",
    )
    # Auto entity protection (optional): extract zh candidates -> translate once -> protect with placeholders.
    p.add_argument("--entity-protect-enable", action="store_true", help="Enable auto entity protection (proper nouns/terms) during MT")
    p.add_argument("--entity-protect-min-len", type=int, default=2, help="Min CJK length for entity candidates")
    p.add_argument("--entity-protect-max-len", type=int, default=6, help="Max CJK length for entity candidates")
    p.add_argument("--entity-protect-min-freq", type=int, default=2, help="Min frequency for entity candidates across segments")
    p.add_argument("--entity-protect-max-items", type=int, default=30, help="Max number of entity candidates per task")
    args, unknown = p.parse_known_args()
    if unknown:
        print(f"[quality] Ignoring unknown args: {unknown}")
    return args


def main() -> None:
    args = parse_args()

    missing = check_dep()
    if missing:
        sys.exit("质量模式依赖未满足：\n- " + "\n- ".join(missing))

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    work_tts = output_dir / "tts_segments"
    work_asr_prefix = output_dir / "asr_whisperx"

    audio_pcm = output_dir / "audio.wav"
    audio_json = output_dir / "audio.json"
    chs_srt = output_dir / "chs.srt"
    eng_srt = output_dir / "eng.srt"
    bi_srt = output_dir / "bilingual.srt"
    display_srt = output_dir / "display.srt"
    display_meta_json = output_dir / "display_meta.json"
    eng_tts_srt = output_dir / "eng_tts.srt"
    eng_tts_raw_srt = output_dir / "eng_tts_raw.srt"
    tts_fit_json = output_dir / "tts_fit.json"
    tra_debug_json = output_dir / "tra_debug.json"
    tts_wav = output_dir / "tts_full.wav"
    video_dub = output_dir / "output_en.mp4"
    video_sub = output_dir / "output_en_sub.mp4"
    tts_plan_json = output_dir / "tts_plan.json"

    if args.resume_from is None or args.resume_from == "asr":
        print("[1/7] Extracting audio...")
        # Quality 模式支持去噪：沿用 lite.extract_audio 的安全逻辑（若未提供 arnndn 模型则回退 anlmdn）
        lite.extract_audio(
            args.video,
            audio_pcm,
            sample_rate=args.sample_rate,
            denoise=bool(getattr(args, "denoise", False)),
            denoise_model=getattr(args, "denoise_model", None),
        )
        audio_total_ms = None
        try:
            if lite.AudioSegment is not None:
                audio_total_ms = float(len(lite.AudioSegment.from_file(audio_pcm)))
        except Exception:
            audio_total_ms = None

        print("[2/7] Running ASR (WhisperX)...")
        segments = run_whisperx(
            audio_path=audio_pcm,
            model_id=args.whisperx_model,
            device="cuda",
            model_dir=args.whisperx_model_dir,
            diarization=args.diarization,
            vad_enable=bool(getattr(args, "vad_enable", False)),
            vad_thold=getattr(args, "vad_thold", None),
            vad_min_sil_s=getattr(args, "vad_min_dur", None),
        )
        # Low-risk ASR normalization (best-effort). This runs on ASR output only (not on review overrides).
        asr_dict = load_asr_dict(getattr(args, "asr_normalize_dict", None)) if getattr(args, "asr_normalize_enable", False) else {}
        for seg in segments:
            seg.text = normalize_asr_zh_text(seg.text, to_simplified_fn=lite.zh_to_simplified, asr_dict=asr_dict)
        # Split overly long ASR segments for better subtitles and more reliable translation.
        segments = split_segments_for_subtitles(segments, max_chars=args.max_sentence_len)
        if getattr(args, "meaning_split_enable", False):
            segments = meaning_split_segments(
                segments,
                min_chars=int(getattr(args, "meaning_split_min_chars", 60) or 60),
                max_parts=int(getattr(args, "meaning_split_max_parts", 3) or 3),
            )
        print(f"[2/7] Subtitle segments after split: {len(segments)} (max_sentence_len={args.max_sentence_len})")
        segments = lite.enforce_min_duration(segments, min_duration=args.min_sub_dur)
        audio_json.write_text(json.dumps([seg.__dict__ for seg in segments], ensure_ascii=False, indent=2), encoding="utf-8")
        lite.write_srt(chs_srt, segments, text_attr="text")
    else:
        # 简单恢复：从已有 json 读取
        if not audio_json.exists():
            sys.exit("resume_from=mt 但缺少 audio.json")
        data = json.loads(audio_json.read_text(encoding="utf-8"))
        segments = [Segment(**item) for item in data]
        asr_dict = load_asr_dict(getattr(args, "asr_normalize_dict", None)) if getattr(args, "asr_normalize_enable", False) else {}
        for seg in segments:
            seg.text = normalize_asr_zh_text(seg.text, to_simplified_fn=lite.zh_to_simplified, asr_dict=asr_dict)
        if not chs_srt.exists():
            lite.write_srt(chs_srt, segments, text_attr="text")

    if args.resume_from is None or args.resume_from in {"asr", "mt"}:
        print("[3/7] Translating with local LLM...")
        tra_enabled = bool(getattr(args, "tra_enable", False))
        tra_save_debug = bool(getattr(args, "tra_save_debug", False))
        tra_json_enable = bool(getattr(args, "tra_json_enable", False))
        mt_context_window = int(getattr(args, "mt_context_window", 0) or 0)
        mt_topic = str(getattr(args, "mt_topic", "") or "")
        mt_style = str(getattr(args, "mt_style", "") or "").strip()
        mt_max_words_per_line = int(getattr(args, "mt_max_words_per_line", 0) or 0)
        mt_compact_enable = bool(getattr(args, "mt_compact_enable", False))
        mt_compact_aggressive = bool(getattr(args, "mt_compact_aggressive", False))
        mt_compact_temperature = float(getattr(args, "mt_compact_temperature", 0.1) or 0.1)
        mt_compact_max_tokens = int(getattr(args, "mt_compact_max_tokens", 96) or 96)
        mt_compact_timeout_s = int(getattr(args, "mt_compact_timeout_s", 120) or 120)
        mt_long_zh_chars = int(getattr(args, "mt_long_zh_chars", 60) or 60)
        mt_long_en_words = int(getattr(args, "mt_long_en_words", 22) or 22)
        mt_long_target_words = int(getattr(args, "mt_long_target_words", 18) or 18)
        mt_prompt_mode = str(getattr(args, "mt_prompt_mode", "short") or "short").strip().lower()
        mt_prompt_mode = "long" if mt_prompt_mode == "long" else "short"
        mt_long_fallback_enable = bool(getattr(args, "mt_long_fallback_enable", False))
        mt_long_examples_enable = bool(getattr(args, "mt_long_examples_enable", False))
        glossary_prompt_enable = bool(getattr(args, "glossary_prompt_enable", False))
        selfcheck_enable = bool(getattr(args, "llm_selfcheck_enable", False))
        mt_json_enable = bool(getattr(args, "mt_json_enable", False))
        mt_topic_auto_enable = bool(getattr(args, "mt_topic_auto_enable", False))
        mt_topic_auto_max_segs = int(getattr(args, "mt_topic_auto_max_segs", 20) or 20)
        qe_enable = bool(getattr(args, "qe_enable", False))
        qe_threshold = float(getattr(args, "qe_threshold", 3.5) or 3.5)
        qe_save_report = bool(getattr(args, "qe_save_report", False))
        glossary_placeholder_enable = bool(getattr(args, "glossary_placeholder_enable", False))
        glossary_placeholder_max = int(getattr(args, "glossary_placeholder_max", 6) or 6)
        tra_debug_path = tra_debug_json if tra_save_debug else None
        # TRA uses per-line strict alignment; to keep behavior predictable, we do not combine segments into sentence-units.
        if tra_enabled and getattr(args, "sentence_unit_enable", False):
            print("[warn] TRA enabled: disabling sentence_unit merge for stability.")
            args.sentence_unit_enable = False

        glossary = lite.load_glossary(getattr(args, "glossary", None))
        glossary_variant_map = _build_glossary_variant_map(glossary) if glossary else {}
        # Context should use original Chinese lines (not placeholders); keep a copy here.
        mt_context_src_lines = [s.text for s in segments]

        # P2: Auto topic summary (one local LLM call). Only when enabled and user didn't provide mt_topic.
        mt_topic_auto_json = output_dir / "mt_topic_auto.json"
        if mt_topic_auto_enable and not mt_topic.strip():
            take = max(3, min(len(mt_context_src_lines), mt_topic_auto_max_segs))
            sample = [mt_context_src_lines[i].strip() for i in range(take) if mt_context_src_lines[i].strip()]
            if sample:
                prompt = (
                    "Return STRICT JSON ONLY.\n"
                    "Schema: {\"topic_hint\": \"...\", \"persona_rules\": [\"...\"]}\n"
                    "Goal: produce a short topic/persona hint for subtitle translation.\n"
                    "Rules:\n"
                    "- Keep it concise (<= 160 chars for topic_hint).\n"
                    "- persona_rules should be 3-6 short items.\n"
                    "- Prefer third-person narration unless clearly first-person.\n"
                    "Input Chinese lines:\n"
                    + "\n".join(f"- {x}" for x in sample[:take])
                )
                body = {
                    "model": args.llm_model,
                    "messages": [{"role": "system", "content": "You output strict JSON only."}, {"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 220,
                    "options": {"num_ctx": 2048, "num_batch": 128},
                }
                try:
                    resp = requests.post(
                        f"{args.llm_endpoint}/chat/completions",
                        json=body,
                        headers={"Content-Type": "application/json"},
                        timeout=180,
                    )
                    if resp.status_code == 200:
                        content = (resp.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                        obj = json.loads(str(content).strip())
                        if isinstance(obj, dict):
                            th = str(obj.get("topic_hint") or "").strip()
                            pr = obj.get("persona_rules") if isinstance(obj.get("persona_rules"), list) else []
                            pr2 = [str(x).strip() for x in pr if str(x).strip()][:6]
                            if th:
                                mt_topic = th + ("; " + "; ".join(pr2) if pr2 else "")
                            try:
                                mt_topic_auto_json.write_text(
                                    json.dumps({"topic_hint": th, "persona_rules": pr2}, ensure_ascii=False, indent=2),
                                    encoding="utf-8",
                                )
                            except Exception:
                                pass
                except Exception as exc:
                    print(f"[warn] mt_topic_auto failed, continuing without it: {exc}")
        if getattr(args, "sentence_unit_enable", False):
            print(
                "[3/7] Sentence-unit merge enabled: "
                f"min_chars={getattr(args, 'sentence_unit_min_chars', 12)}, "
                f"max_chars={getattr(args, 'sentence_unit_max_chars', 60)}, "
                f"max_segs={getattr(args, 'sentence_unit_max_segs', 3)}, "
                f"max_gap_s={getattr(args, 'sentence_unit_max_gap_s', 0.6)}, "
                f"boundary_punct={getattr(args, 'sentence_unit_boundary_punct', '。！？!?.,')}"
            )
        # If review provides an override CHS SRT, use it as the MT source (keeping timestamps).
        if getattr(args, "chs_override_srt", None):
            ov = Path(getattr(args, "chs_override_srt"))
            if ov.exists():
                texts = lite._read_srt_texts(ov)  # type: ignore[attr-defined]
                if texts:
                    for i, seg in enumerate(segments):
                        if i < len(texts) and texts[i].strip():
                            seg.text = lite.zh_to_simplified(texts[i].strip())
                    try:
                        chs_srt.write_text(lite.zh_to_simplified(ov.read_text(encoding="utf-8", errors="ignore")), encoding="utf-8")
                    except Exception:
                        pass
        # ----------------------------
        # Optional: auto entity protection
        # ----------------------------
        entity_map = None
        if getattr(args, "entity_protect_enable", False):
            try:
                cands = lite._extract_entity_candidates_from_segments(  # type: ignore[attr-defined]
                    segments,  # runtime: only needs .text
                    min_len=int(getattr(args, "entity_protect_min_len", 2) or 2),
                    max_len=int(getattr(args, "entity_protect_max_len", 6) or 6),
                    min_freq=int(getattr(args, "entity_protect_min_freq", 2) or 2),
                    max_items=int(getattr(args, "entity_protect_max_items", 30) or 30),
                )
                if cands:
                    # Translate candidates in batches via the same LLM endpoint.
                    term_segs = [Segment(start=0.0, end=0.0, text=c) for c in cands]
                    term_tr = translate_segments_llm(
                        term_segs,
                        endpoint=args.llm_endpoint,
                        model=args.llm_model,
                        api_key=args.llm_api_key,
                        chunk_size=8,
                    )
                    entity_map = {}
                    for cand, seg_t in zip(cands, term_tr):
                        en = str(seg_t.translation or "").strip()
                        en = re.sub(r"\s+", " ", en).strip()
                        # strip any leaked CJK
                        en = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", en).strip()
                        if not en:
                            continue
                        if len(en) > 40:
                            en = " ".join(en.split()[:8]).strip()
                        entity_map[cand] = en
                    print(f"[3a] Entity protection enabled: {len(entity_map)} candidates")
            except Exception as exc:
                print(f"[warn] Failed to build entity map; continuing without protection: {exc}")
                entity_map = None

        def _protect_chunk(seg_chunk: List[Segment]) -> tuple[List[Segment], List[List[tuple[str, str]]]]:
            used_per: List[List[tuple[str, str]]] = []
            protected: List[Segment] = []
            for s in seg_chunk:
                t0 = s.text
                used_all: List[tuple[str, str]] = []
                if glossary_placeholder_enable and glossary_variant_map:
                    t0, used_g = _protect_glossary_terms(t0, glossary_variant_map, max_replacements=glossary_placeholder_max)
                    used_all.extend(used_g)
                if entity_map:
                    t0, used_e = lite.protect_entities(t0, entity_map, max_replacements=2)  # type: ignore[arg-type]
                    used_all.extend(used_e)
                used_per.append(used_all)
                protected.append(Segment(start=s.start, end=s.end, text=t0))
            return protected, used_per

        # Sentence-unit merge (min-risk): merge short adjacent segments before translation, then split back.
        if getattr(args, "sentence_unit_enable", False):
            boundary = set(str(getattr(args, "sentence_unit_boundary_punct", "。！？!?.,") or "。！？!?.,"))
            min_chars = int(getattr(args, "sentence_unit_min_chars", 12) or 12)
            max_chars = int(getattr(args, "sentence_unit_max_chars", 60) or 60)
            max_segs = int(getattr(args, "sentence_unit_max_segs", 3) or 3)
            max_gap_s = float(getattr(args, "sentence_unit_max_gap_s", 0.6) or 0.6)
            break_words_raw = str(getattr(args, "sentence_unit_break_words", "") or "")
            break_words = [w.strip() for w in re.split(r"[,，\s]+", break_words_raw) if w.strip()]

            # Structural threshold: prefer merging fragments until we have something "translatable enough".
            _verbish = re.compile(r"(是|有|在|要|会|能|可以|必须|应该|觉得|认为|知道|说|讲|问|去|来|做|看到|听到)")
            def _has_predicate(s: str) -> bool:
                ss = (s or "").strip()
                if not ss:
                    return False
                return bool(_verbish.search(ss))

            unit_groups: List[List[int]] = []
            buf: List[int] = []
            buf_chars = 0
            for i, seg in enumerate(segments):
                # Discourse break words: if a new segment starts with "但/而/于是/然后..." we tend to start a new unit.
                if buf and break_words:
                    head = (seg.text or "").strip()
                    if any(head.startswith(w) for w in break_words):
                        unit_groups.append(buf)
                        buf = []
                        buf_chars = 0
                if buf:
                    prev = segments[buf[-1]]
                    gap = float(seg.start) - float(prev.end)
                    if gap > max_gap_s:
                        unit_groups.append(buf)
                        buf = []
                        buf_chars = 0
                buf.append(i)
                buf_chars += len(seg.text or "")
                last = (seg.text or "").strip()
                enough = (buf_chars >= min_chars) or _has_predicate("".join((segments[j].text or "") for j in buf))
                hit = bool(last) and (last[-1] in boundary)
                if len(buf) >= max_segs or buf_chars >= max_chars or (enough and hit):
                    unit_groups.append(buf)
                    buf = []
                    buf_chars = 0
            if buf:
                unit_groups.append(buf)

            # Translate per unit (with optional entity protection)
            unit_segments: List[Segment] = []
            unit_src_texts: List[List[str]] = []
            unit_used: List[List[tuple[str, str]]] = []
            for g in unit_groups:
                texts = [segments[j].text for j in g]
                unit_src_texts.append(texts)
                unit_text = lite.clean_zh_text(" ".join(t.strip() for t in texts))
                if entity_map:
                    unit_text, used = lite.protect_entities(unit_text, entity_map)  # type: ignore[arg-type]
                    unit_used.append(used)
                else:
                    unit_used.append([])
                unit_segments.append(Segment(start=segments[g[0]].start, end=segments[g[-1]].end, text=unit_text))

            unit_translated = translate_segments_llm(
                unit_segments,
                endpoint=args.llm_endpoint,
                model=args.llm_model,
                api_key=args.llm_api_key,
                chunk_size=max(1, int(getattr(args, "llm_chunk_size", 2) or 2)),
                context_window=0,
                topic_hint=mt_topic,
                style_hint=mt_style,
                max_words_per_line=mt_max_words_per_line,
                compact_enable=mt_compact_enable,
                compact_aggressive=mt_compact_aggressive,
                compact_temperature=mt_compact_temperature,
                compact_max_tokens=mt_compact_max_tokens,
                compact_timeout_s=mt_compact_timeout_s,
                long_zh_chars=mt_long_zh_chars,
                long_en_words=mt_long_en_words,
                long_target_words=mt_long_target_words,
                prompt_mode=mt_prompt_mode,
                long_fallback_enable=mt_long_fallback_enable,
                long_examples_enable=mt_long_examples_enable,
                glossary=glossary,
                glossary_prompt_enable=glossary_prompt_enable,
                selfcheck_enable=selfcheck_enable,
                mt_json_enable=mt_json_enable,
            )

            # Split unit translation back to segments (grammar-aware boundaries)
            seg_en = segments
            for g, texts, u, used in zip(unit_groups, unit_src_texts, unit_translated, unit_used):
                en = (u.translation or "").strip()
                if used:
                    en = lite.restore_entities(en, used)
                pieces = lite.split_translation_by_src_lengths(texts, en)
                for seg_idx, piece in zip(g, pieces):
                    seg_en[seg_idx].translation = (piece or "").strip()
        else:
            # Per-segment translation (with optional entity protection)
            protected, used_per = _protect_chunk(segments)
            if tra_enabled:
                seg_en, _dbg = translate_segments_llm_tra(
                    protected,
                    endpoint=args.llm_endpoint,
                    model=args.llm_model,
                    api_key=args.llm_api_key,
                    chunk_size=max(1, int(getattr(args, "llm_chunk_size", 2) or 2)),
                    save_debug_path=tra_debug_path,
                    tra_json_enable=tra_json_enable,
                    context_window=mt_context_window,
                    topic_hint=mt_topic,
                    style_hint=mt_style,
                    max_words_per_line=mt_max_words_per_line,
                    compact_enable=mt_compact_enable,
                    compact_aggressive=mt_compact_aggressive,
                    compact_temperature=mt_compact_temperature,
                    compact_max_tokens=mt_compact_max_tokens,
                    compact_timeout_s=mt_compact_timeout_s,
                    long_zh_chars=mt_long_zh_chars,
                    long_en_words=mt_long_en_words,
                    long_target_words=mt_long_target_words,
                    glossary=glossary,
                    glossary_prompt_enable=glossary_prompt_enable,
                    context_src_lines=mt_context_src_lines,
                )
            else:
                seg_en = translate_segments_llm(
                    protected,
                    endpoint=args.llm_endpoint,
                    model=args.llm_model,
                    api_key=args.llm_api_key,
                    chunk_size=max(1, int(getattr(args, "llm_chunk_size", 2) or 2)),
                    context_window=mt_context_window,
                    topic_hint=mt_topic,
                    style_hint=mt_style,
                    max_words_per_line=mt_max_words_per_line,
                    compact_enable=mt_compact_enable,
                    compact_aggressive=mt_compact_aggressive,
                    compact_temperature=mt_compact_temperature,
                    compact_max_tokens=mt_compact_max_tokens,
                    compact_timeout_s=mt_compact_timeout_s,
                    long_zh_chars=mt_long_zh_chars,
                    long_en_words=mt_long_en_words,
                    long_target_words=mt_long_target_words,
                    prompt_mode=mt_prompt_mode,
                    long_fallback_enable=mt_long_fallback_enable,
                    long_examples_enable=mt_long_examples_enable,
                    glossary=glossary,
                    glossary_prompt_enable=glossary_prompt_enable,
                    selfcheck_enable=selfcheck_enable,
                    mt_json_enable=mt_json_enable,
                    context_src_lines=mt_context_src_lines,
                )
            for seg, used in zip(seg_en, used_per):
                if used and seg.translation is not None:
                    seg.translation = lite.restore_entities(seg.translation, used)
        if glossary:
            stats = lite.apply_glossary_to_segments(seg_en, glossary)
            print(f"[3b] Glossary applied: {stats}")

        # P1.5: QE-driven selective fix (local LLM review -> fix low-quality lines). Fallback-safe.
        qe_report_path = output_dir / "qe_report.json"
        if qe_enable:
            rep = {
                "version": 1,
                "enabled": True,
                "threshold": qe_threshold,
                "segments": len(seg_en),
                "fixed": 0,
                "items": [],
            }

            def _qe_ok_line(s: str) -> bool:
                t = (s or "").strip()
                if not t:
                    return False
                if "\n" in t or "\r" in t:
                    return False
                if len(t) > 180:
                    return False
                if re.search(r"\b(and|or|but|to|of|with|for)$", t.lower()):
                    return False
                return True

            def _qe_call_one(zh: str, en: str, prev: str, nxt: str) -> dict:
                topic = (mt_topic or "").strip()
                glossary_hint_local = ""
                if glossary_prompt_enable and glossary:
                    glossary_hint_local = _format_glossary_hint(glossary, [zh])
                prompt = (
                    "Return STRICT JSON ONLY.\n"
                    "Schema: {\"adequacy\":1-5,\"faithfulness\":1-5,\"fluency\":1-5,\"should_fix\":true/false,\"fixed\":\"...\"}\n"
                    "You are evaluating ONE subtitle translation.\n"
                    "Rules:\n"
                    "- Do NOT add new facts.\n"
                    "- Keep person/number consistent.\n"
                    "- If should_fix is false, fixed MUST equal the ORIGINAL EN exactly.\n"
                    "- If should_fix is true, fixed should be a MINIMAL-EDIT improved line.\n"
                    + (f"Topic hint: {topic}\n" if topic else "")
                    + (glossary_hint_local + "\n" if glossary_hint_local else "")
                    + f"ZH: {zh}\n"
                    + (f"CONTEXT_PREV: {prev}\nCONTEXT_NEXT: {nxt}\n" if (prev or nxt) else "")
                    + f"EN: {en}\n"
                )
                body = {
                    "model": args.llm_model,
                    "messages": [{"role": "system", "content": "You output strict JSON only."}, {"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 240,
                    "options": {"num_ctx": 2048, "num_batch": 128},
                }
                r = requests.post(f"{args.llm_endpoint}/chat/completions", json=body, headers={"Content-Type": "application/json"}, timeout=180)
                if r.status_code != 200:
                    raise RuntimeError(r.text)
                content = (r.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                obj = json.loads(str(content).strip())
                if not isinstance(obj, dict):
                    raise ValueError("qe json not an object")
                return obj

            cw = max(0, int(mt_context_window or 0))
            for i, seg in enumerate(seg_en):
                zh = (seg.text or "").strip()
                en0 = (seg.translation or "").strip()
                prev = mt_context_src_lines[i - 1].strip() if (cw > 0 and i - 1 >= 0) else ""
                nxt = mt_context_src_lines[i + 1].strip() if (cw > 0 and i + 1 < len(mt_context_src_lines)) else ""
                item = {"idx": i + 1, "zh": zh, "en": en0}
                if not zh or not en0:
                    item.update({"adequacy": None, "faithfulness": None, "fluency": None, "should_fix": False, "fixed": en0, "applied": False})
                    rep["items"].append(item)
                    continue
                try:
                    obj = _qe_call_one(zh, en0, prev, nxt)
                    a = float(obj.get("adequacy") or 0)
                    f = float(obj.get("faithfulness") or 0)
                    fl = float(obj.get("fluency") or 0)
                    should_fix = bool(obj.get("should_fix"))
                    fixed = str(obj.get("fixed") or "").strip()
                    fixed = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", fixed).strip()
                    fixed = re.sub(r"\s+", " ", fixed).strip()
                    min_score = min(a, f, fl) if all(x > 0 for x in (a, f, fl)) else 0
                    applied = False
                    if should_fix and min_score < float(qe_threshold) and fixed and fixed != en0 and _qe_ok_line(fixed):
                        seg.translation = fixed
                        applied = True
                        rep["fixed"] += 1
                    item.update({"adequacy": a, "faithfulness": f, "fluency": fl, "should_fix": should_fix, "fixed": fixed or en0, "applied": applied})
                except Exception as exc:
                    item.update({"adequacy": None, "faithfulness": None, "fluency": None, "should_fix": False, "fixed": en0, "applied": False, "error": str(exc)[:200]})
                rep["items"].append(item)
            if qe_save_report:
                try:
                    qe_report_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            print(f"[p1.5] qe_fix: enabled, fixed={rep['fixed']}/{rep['segments']}, threshold={qe_threshold}")
        lite.write_srt(eng_srt, seg_en, text_attr="translation")
        # P0: generate TTS script (subtitle vs TTS separation)
        if getattr(args, "tts_script_enable", False):
            for seg in seg_en:
                seg.tts = _build_tts_script(seg.translation or "")
    else:
        seg_en = segments
        # Restore translations for TTS when resuming from tts/mux.
        override = getattr(args, "eng_override_srt", None)
        eng_path = Path(override) if override else eng_srt
        if eng_path.exists():
            texts = lite._read_srt_texts(eng_path)  # type: ignore[attr-defined]
            for i, seg in enumerate(seg_en):
                if i < len(texts):
                    seg.translation = texts[i]
                else:
                    seg.translation = seg.translation or ""
            # Keep eng.srt in sync for later embed
            try:
                if eng_path != eng_srt:
                    eng_srt.write_text(eng_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            except Exception:
                pass
        # P0: regenerate TTS script from the loaded subtitles (base or review override)
        if getattr(args, "tts_script_enable", False):
            for seg in seg_en:
                seg.tts = _build_tts_script(seg.translation or "")

    # ----------------------------
    # P1: TTS fitting (trim TTS script to fit duration under max speed)
    # ----------------------------
    tts_fit_stats = {
        "version": 1,
        "enabled": bool(getattr(args, "tts_fit_enable", False)),
        "segments": 0,
        "trimmed": 0,
        "trimmed_words_total": 0,
        "params": {
            "wps": float(getattr(args, "tts_fit_wps", 2.6) or 2.6),
            "min_words": int(getattr(args, "tts_fit_min_words", 3) or 3),
            "tts_speed_max": float(getattr(args, "tts_speed_max", 1.1) or 1.1),
        },
        "trimmed_samples": [],
    }
    if getattr(args, "tts_fit_enable", False) and getattr(args, "tts_script_enable", False):
        wps = float(getattr(args, "tts_fit_wps", 2.6) or 2.6)
        min_words = int(getattr(args, "tts_fit_min_words", 3) or 3)
        speed_max = float(getattr(args, "tts_speed_max", 1.1) or 1.1)
        # Snapshot raw TTS script before trimming (helps verify the effect).
        if getattr(args, "tts_fit_save_raw", False):
            try:
                lite.write_srt(eng_tts_raw_srt, seg_en, text_attr="tts")
            except Exception:
                pass
        for seg in seg_en:
            tts_fit_stats["segments"] += 1
            raw = (seg.tts or "").strip()
            if not raw:
                continue
            dur = max(float(seg.end) - float(seg.start), 0.001)
            budget_s = dur * max(speed_max, 1.0)
            est_s = _estimate_en_seconds(raw, wps=wps)
            if est_s <= budget_s:
                continue
            words = [w for w in _normalize_en_line(raw).split(" ") if w]
            pauses = 0.12 * len(re.findall(r"[,;:]", raw)) + 0.22 * len(re.findall(r"[.!?]", raw))
            budget_words = int(max(1, (max(budget_s - pauses, 0.2) * wps)))
            if budget_words >= len(words):
                continue
            trimmed = _trim_en_to_word_budget(raw, max_words=budget_words, min_words=min_words)
            if trimmed and trimmed != raw:
                seg.tts = trimmed
                tts_fit_stats["trimmed"] += 1
                tts_fit_stats["trimmed_words_total"] += max(0, len(words) - len([w for w in trimmed.split(" ") if w]))
                if len(tts_fit_stats["trimmed_samples"]) < 8:
                    tts_fit_stats["trimmed_samples"].append(
                        {
                            "idx": int(tts_fit_stats["segments"]),
                            "start": round(float(seg.start), 3),
                            "end": round(float(seg.end), 3),
                            "dur_s": round(dur, 3),
                            "budget_s": round(budget_s, 3),
                            "est_s": round(est_s, 3),
                            "words_raw": len(words),
                            "words_budget": budget_words,
                            "raw": raw[:180],
                            "trimmed": trimmed[:180],
                        }
                    )
        try:
            tts_fit_json.write_text(json.dumps(tts_fit_stats, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        print(
            f"[p1] tts_fit: enabled, trimmed={tts_fit_stats['trimmed']}/{tts_fit_stats['segments']}, "
            f"trimmed_words_total={tts_fit_stats['trimmed_words_total']}, wps={wps}, min_words={min_words}, tts_speed_max={speed_max}"
        )

    # ----------------------------
    # P0: subtitle post-process
    # ----------------------------
    if getattr(args, "subtitle_postprocess_enable", False):
        # Normalize translations to a single line first (reduces formatting pollution).
        for seg in seg_en:
            seg.translation = _normalize_en_line(seg.translation or "")
        # Optional soft wrap to reduce "long line" warnings.
        if getattr(args, "subtitle_wrap_enable", False):
            max_chars = int(getattr(args, "subtitle_max_chars_per_line", 80) or 80)
            max_lines = int(getattr(args, "subtitle_wrap_max_lines", 2) or 2)
            wrapped = 0
            for seg in seg_en:
                before = seg.translation or ""
                after = _wrap_en_for_subtitle(before, max_chars_per_line=max_chars, max_lines=max_lines)
                if after != before:
                    seg.translation = after
                    wrapped += 1
            print(f"[p0] subtitle_wrap: enabled, wrapped={wrapped}, max_chars_per_line={max_chars}, max_lines={max_lines}")
        # Optional CPS fix by extending timestamps into gaps (keeps segment count stable for review)
        if getattr(args, "subtitle_cps_fix_enable", False):
            audio_total_s = (float(audio_total_ms) / 1000.0) if ("audio_total_ms" in locals() and audio_total_ms is not None) else None
            stats = _enforce_max_cps_by_extending(
                seg_en,
                max_cps=float(getattr(args, "subtitle_max_cps", 20.0) or 20.0),
                safety_gap_s=float(getattr(args, "subtitle_cps_safety_gap", 0.2) or 0.2),
                audio_total_s=audio_total_s,
            )
            print(
                f"[p0] subtitle_cps_fix: enabled, fixed={int(stats['fixed'])}, "
                f"extended_s={stats['extended_s']:.3f}, max_cps={getattr(args, 'subtitle_max_cps', 20.0)}"
            )

    # ----------------------------
    # P1-2: TTS time-budget planning (hearing-first; no eng_tts.srt required)
    # - Keep required speed <= tts_speed_max (e.g., 1.15)
    # - Borrow time by shifting segment timeline forward (subtitles follow audio)
    # - Last resort: if we hit the source end cap, lightly trim the tail segments deterministically
    # ----------------------------
    if getattr(args, "tts_plan_enable", False):
        try:
            wps = float(getattr(args, "tts_fit_wps", 2.6) or 2.6)
            max_speed = float(getattr(args, "tts_speed_max", 1.15) or 1.15)
            # Planning min duration: keep short lines from inflating too much.
            # We still respect CPS & max_speed constraints; this is just a floor to avoid 0.2s segments.
            min_dur = float(getattr(args, "min_sub_dur", 1.8) or 1.8)
            min_gap = 0.04  # keep tiny gap to avoid overlaps
            max_cps = float(getattr(args, "subtitle_max_cps", 20.0) or 20.0)
            # Hard cap total duration to avoid “补帧太多”.
            # Use source video duration * mux_slow_max_ratio as upper bound (hearing-first but bounded).
            cap_ratio = float(getattr(args, "mux_slow_max_ratio", 1.08) or 1.08)
            cap_ratio = max(1.0, min(cap_ratio, 1.30))
            tail_margin = float(getattr(args, "tts_plan_safety_margin", 0.05) or 0.05)
            min_words = int(getattr(args, "tts_fit_min_words", 1) or 1)
            src_dur_s = None
            try:
                cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(args.video)]
                out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
                src_dur_s = float(json.loads(out)["format"]["duration"])
            except Exception:
                src_dur_s = None
            cap_end = None
            if src_dur_s and src_dur_s > 0:
                cap_end = max(float(src_dur_s) * cap_ratio - tail_margin, 0.5)

            def _word_budget_for_duration(text: str, *, dur_s: float) -> int:
                """
                Compute a deterministic word budget to fit within dur_s at max_speed.
                We subtract simple punctuation pauses so the remaining time is for words.
                """
                if not text:
                    return 0
                d = max(float(dur_s), 0.05)
                pauses = 0.12 * len(re.findall(r"[,;:]", text)) + 0.22 * len(re.findall(r"[.!?]", text))
                budget_words = int(max(1, (max(d * max_speed - pauses, 0.15) * wps)))
                return budget_words

            llm_trim_enable = bool(getattr(args, "tts_trim_llm_enable", False))
            llm_trim_aggressive_cfg = bool(getattr(args, "tts_trim_llm_aggressive", False))
            llm_temp = float(getattr(args, "tts_trim_llm_temperature", 0.1) or 0.1)
            llm_max_tokens = int(getattr(args, "tts_trim_llm_max_tokens", 96) or 96)
            llm_timeout_s = int(getattr(args, "tts_trim_llm_timeout_s", 120) or 120)
            min_words_default = int(getattr(args, "tts_fit_min_words", 3) or 3)

            plans = []
            prev_end = None
            for i, seg in enumerate(seg_en):
                st0 = float(seg.start)
                ed0 = float(seg.end)
                dur0 = max(ed0 - st0, 0.001)
                txt = str(seg.translation or "").strip()
                est = _estimate_en_seconds(txt, wps=wps) if txt else 0.0
                # Minimal duration from CPS. We keep a tiny floor so 0.2s segments don't become unreadable,
                # but we now prefer trimming text (global) over extending the whole timeline.
                cps_need = (len(txt) / max(max_cps, 1.0)) if txt else 0.0
                floor = min(min_dur, max(0.25, cps_need)) if txt else min_dur
                # "Aggressive" mode for very short segments: do NOT inflate to cps_need; trim instead.
                if dur0 < 0.8 and txt:
                    base_dur = max(dur0, 0.25)
                else:
                    base_dur = max(dur0, floor, cps_need)

                # Global deterministic trimming: if this line is over-budget for its duration, trim it now.
                # This avoids pushing the entire timeline forward (which creates long tail /补帧 pressure).
                if txt:
                    budget_words = _word_budget_for_duration(txt, dur_s=base_dur)
                    words = [w for w in re.split(r"\s+", txt) if w]
                    if budget_words > 0 and budget_words < len(words):
                        aggressive_line = bool(llm_trim_aggressive_cfg or dur0 < 0.8 or budget_words <= 4)
                        min_words = 1 if aggressive_line else min_words_default
                        budget_words = max(int(budget_words), int(min_words))
                        rewritten = ""
                        if llm_trim_enable and str(getattr(args, "llm_endpoint", "") or "").strip():
                            rewritten = _rewrite_en_to_budget_llm(
                                endpoint=str(getattr(args, "llm_endpoint", "")).strip(),
                                model=str(getattr(args, "llm_model", "") or "").strip(),
                                api_key=str(getattr(args, "llm_api_key", "") or "").strip(),
                                zh=str(getattr(seg, "text", "") or ""),
                                en=txt,
                                max_words=int(budget_words),
                                aggressive=aggressive_line,
                                temperature=llm_temp,
                                max_tokens=llm_max_tokens,
                                timeout_s=llm_timeout_s,
                            )
                        if rewritten:
                            seg.translation = rewritten
                            txt = rewritten
                            est = _estimate_en_seconds(txt, wps=wps) if txt else 0.0
                        else:
                            trimmed = _trim_en_to_word_budget(txt, max_words=int(budget_words), min_words=int(min_words))
                            if trimmed and trimmed != txt:
                                seg.translation = trimmed
                                txt = trimmed
                                est = _estimate_en_seconds(txt, wps=wps) if txt else 0.0

                need_dur = float(base_dur)
                st = st0
                if prev_end is not None:
                    st = max(st, float(prev_end) + float(min_gap))
                ed = st + need_dur
                plans.append(
                    {
                        "idx": i + 1,
                        "text": txt[:180],
                        "orig": {"start": round(st0, 3), "end": round(ed0, 3), "dur": round(dur0, 3)},
                        "planned": {"start": round(st, 3), "end": round(ed, 3), "dur": round(need_dur, 3)},
                        "est_s": round(float(est), 3),
                        "required_speed": round((float(est) / float(need_dur)) if need_dur > 0 else 0.0, 3),
                        "trim": {
                            "llm": bool(llm_trim_enable),
                            "aggressive_cfg": bool(llm_trim_aggressive_cfg),
                        }
                        if txt
                        else None,
                    }
                )
                seg.start = float(st)
                seg.end = float(ed)
                prev_end = float(ed)

            # Cap to bounded max end; trim only if absolutely necessary
            if cap_end is not None and seg_en:
                # Walk backwards: if the tail exceeds cap_end, cap & trim that segment to fit under max_speed
                for i in range(len(seg_en) - 1, -1, -1):
                    seg = seg_en[i]
                    if float(seg.end) <= cap_end:
                        cap_end = float(seg.start) - float(min_gap)
                        continue
                    seg.end = max(float(seg.start) + 0.2, cap_end)
                    cap_end = float(seg.start) - float(min_gap)
                    txt = str(seg.translation or "").strip()
                    dur = max(float(seg.end) - float(seg.start), 0.001)
                    est = _estimate_en_seconds(txt, wps=wps) if txt else 0.0
                    if txt:
                        budget_words = _word_budget_for_duration(txt, dur_s=dur)
                        words = [w for w in re.split(r"\s+", txt) if w]
                        if budget_words > 0 and budget_words < len(words):
                            aggressive_line = bool(llm_trim_aggressive_cfg or dur < 0.8 or budget_words <= 4)
                            min_words = 1 if aggressive_line else min_words_default
                            budget_words = max(int(budget_words), int(min_words))
                            rewritten = ""
                            if llm_trim_enable and str(getattr(args, "llm_endpoint", "") or "").strip():
                                rewritten = _rewrite_en_to_budget_llm(
                                    endpoint=str(getattr(args, "llm_endpoint", "")).strip(),
                                    model=str(getattr(args, "llm_model", "") or "").strip(),
                                    api_key=str(getattr(args, "llm_api_key", "") or "").strip(),
                                    zh=str(getattr(seg, "text", "") or ""),
                                    en=txt,
                                    max_words=int(budget_words),
                                    aggressive=aggressive_line,
                                    temperature=llm_temp,
                                    max_tokens=llm_max_tokens,
                                    timeout_s=llm_timeout_s,
                                )
                            if rewritten:
                                seg.translation = rewritten
                            else:
                                trimmed = _trim_en_to_word_budget(txt, max_words=int(budget_words), min_words=int(min_words))
                                if trimmed and trimmed != txt:
                                    seg.translation = trimmed

            try:
                tts_plan_json.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "enabled": True,
                            "params": {
                                "wps": wps,
                                "max_speed": max_speed,
                                "min_sub_dur": min_dur,
                                "min_gap": min_gap,
                                "cap_ratio": cap_ratio,
                                "cap_end": cap_end,
                                "min_words_default": min_words_default,
                                "trim_llm_enable": llm_trim_enable,
                            },
                            "plans": plans[:400],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            print(f"[p1.2] tts_plan: enabled, max_speed={max_speed}, wps={wps}, min_sub_dur={min_dur}")
        except Exception as exc:
            print(f"[warn] tts_plan failed, continuing without it: {exc}")

    # Always rewrite subtitle artifacts from current in-memory segments to keep timestamps/text consistent.
    # (This is important when CPS-fix adjusted timestamps or when review overrides were loaded.)
    lite.write_srt(chs_srt, seg_en, text_attr="text")
    lite.write_srt(eng_srt, seg_en, text_attr="translation")
    bilingual_enabled = getattr(args, "bilingual_srt", True)
    if bilingual_enabled:
        bilingual_segments = []
        for seg in seg_en:
            bilingual_text = f"{seg.text}\n{seg.translation}"
            bilingual_segments.append(Segment(start=seg.start, end=seg.end, text=bilingual_text, translation=seg.translation))
        lite.write_srt(bi_srt, bilingual_segments, text_attr="text")
    if getattr(args, "tts_script_enable", False):
        lite.write_srt(eng_tts_srt, seg_en, text_attr="tts")

    # ----------------------------
    # P0: display subtitles (screen-friendly)
    # ----------------------------
    if getattr(args, "display_srt_enable", False) or getattr(args, "display_use_for_embed", False):
        try:
            src = [(float(s.start), float(s.end), str(s.translation or "")) for s in seg_en]
            items, meta = build_display_items(
                src=src,
                max_chars_per_line=int(getattr(args, "display_max_chars_per_line", 42) or 42),
                max_lines=int(getattr(args, "display_max_lines", 2) or 2),
                merge_enable=bool(getattr(args, "display_merge_enable", False)),
                merge_max_gap_s=float(getattr(args, "display_merge_max_gap_s", 0.25) or 0.25),
                merge_max_chars=int(getattr(args, "display_merge_max_chars", 80) or 80),
                split_enable=bool(getattr(args, "display_split_enable", False)),
                split_max_chars=int(getattr(args, "display_split_max_chars", 86) or 86),
            )
            # Write display.srt (uses translation field)
            disp_segs: List[Segment] = [Segment(start=it.start, end=it.end, text="", translation=it.text) for it in items]
            lite.write_srt(display_srt, disp_segs, text_attr="translation")
            try:
                display_meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            print(f"[p0] display_srt: enabled, items={len(disp_segs)}, out={display_srt.name}")
        except Exception as exc:
            print(f"[warn] display_srt generation failed, continue without it: {exc}")

    # Persist updated timings/fields for resume flows.
    try:
        audio_json.write_text(json.dumps([seg.__dict__ for seg in seg_en], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    if args.skip_tts:
        print("Skip TTS enabled; generated subtitles only.")
        return

    # If we didn't just extract audio (resume), still try to compute total duration for padding.
    if "audio_total_ms" not in locals():
        audio_total_ms = None
        try:
            if lite.AudioSegment is not None and audio_pcm.exists():
                audio_total_ms = float(len(lite.AudioSegment.from_file(audio_pcm)))
        except Exception:
            audio_total_ms = None

    if args.resume_from is None or args.resume_from in {"asr", "mt", "tts"}:
        print(f"[5/7] Synthesizing TTS with {args.tts_backend}...")
        if args.tts_backend == "piper":
            combined_audio = lite.synthesize_segments(
                seg_en,
                model_path=args.piper_model,
                work_dir=work_tts,
                piper_bin=args.piper_bin,
                allow_speed_change=True,
                split_len=args.tts_split_len,
                max_speed=args.tts_speed_max,
                align_mode=str(getattr(args, "tts_align_mode", "resample") or "resample"),
                pad_to_ms=audio_total_ms,
            )
        else:
            tts = lite.build_coqui_tts(model_name=args.coqui_model, device=args.coqui_device)
            combined_audio = lite.synthesize_segments_coqui(
                seg_en,
                tts=tts,
                work_dir=work_tts,
                sample_rate=args.sample_rate,
                speaker=None,
                language=None,
                split_len=args.tts_split_len,
                max_speed=args.tts_speed_max,
                align_mode=str(getattr(args, "tts_align_mode", "resample") or "resample"),
                pad_to_ms=audio_total_ms,
            )
        lite.save_audio(combined_audio, tts_wav, sample_rate=args.sample_rate)

    print("[6/7] Muxing video with new audio...")
    # Auto-enable erase if user给了矩形但忘记开关
    erase_enable = bool(getattr(args, "erase_subtitle_enable", False))
    erase_w = float(getattr(args, "erase_subtitle_w", 1.0) or 0.0)
    erase_h = float(getattr(args, "erase_subtitle_h", 0.22) or 0.0)
    if not erase_enable and erase_w > 0 and erase_h > 0:
        erase_enable = True

    lite.mux_video_audio(
        args.video,
        tts_wav,
        video_dub,
        sync_strategy=str(getattr(args, "mux_sync_strategy", "slow") or "slow"),
        slow_max_ratio=float(getattr(args, "mux_slow_max_ratio", 1.08) or 1.08),
        threshold_s=float(getattr(args, "mux_slow_threshold_s", 0.05) or 0.05),
        erase_subtitle_enable=erase_enable,
        erase_subtitle_method=str(getattr(args, "erase_subtitle_method", "delogo") or "delogo"),
        erase_subtitle_coord_mode=str(getattr(args, "erase_subtitle_coord_mode", "ratio") or "ratio"),
        erase_subtitle_x=float(getattr(args, "erase_subtitle_x", 0.0) or 0.0),
        erase_subtitle_y=float(getattr(args, "erase_subtitle_y", 0.78) or 0.78),
        erase_subtitle_w=erase_w if erase_w else float(getattr(args, "erase_subtitle_w", 1.0) or 1.0),
        erase_subtitle_h=erase_h if erase_h else float(getattr(args, "erase_subtitle_h", 0.22) or 0.22),
        erase_subtitle_blur_radius=int(getattr(args, "erase_subtitle_blur_radius", 12) or 12),
    )
    if not video_dub.exists():
        raise RuntimeError(f"mux failed: {video_dub} not created")

    print("[7/7] Embedding subtitles...")
    srt_to_burn = display_srt if (getattr(args, "display_use_for_embed", False) and display_srt.exists()) else eng_srt
    # Placement precedence: if subtitle erase is enabled, new subtitles should be forced into the same box center.
    # This prevents overlap with original hard-subs and matches product expectation: "处理字幕为主" > 样式对齐.
    place_enable = bool(getattr(args, "sub_place_enable", False))
    place_coord_mode = str(getattr(args, "sub_place_coord_mode", "ratio") or "ratio")
    place_x = float(getattr(args, "sub_place_x", 0.0) or 0.0)
    place_y = float(getattr(args, "sub_place_y", 0.78) or 0.78)
    place_w = float(getattr(args, "sub_place_w", 1.0) or 1.0)
    place_h = float(getattr(args, "sub_place_h", 0.22) or 0.22)
    if bool(getattr(args, "erase_subtitle_enable", False)):
        place_enable = True
        place_coord_mode = str(getattr(args, "erase_subtitle_coord_mode", "ratio") or "ratio")
        place_x = float(getattr(args, "erase_subtitle_x", 0.0) or 0.0)
        place_y = float(getattr(args, "erase_subtitle_y", 0.78) or 0.78)
        place_w = float(getattr(args, "erase_subtitle_w", 1.0) or 1.0)
        place_h = float(getattr(args, "erase_subtitle_h", 0.22) or 0.22)
    lite.burn_subtitles(
        video_dub,
        srt_to_burn,
        video_sub,
        font_name=str(getattr(args, "sub_font_name", "Arial") or "Arial"),
        font_size=int(getattr(args, "sub_font_size", 18) or 18),
        outline=int(getattr(args, "sub_outline", 1) or 1),
        shadow=int(getattr(args, "sub_shadow", 0) or 0),
        margin_v=int(getattr(args, "sub_margin_v", 24) or 24),
        alignment=int(getattr(args, "sub_alignment", 2) or 2),
        place_enable=place_enable,
        place_coord_mode=place_coord_mode,
        place_x=place_x,
        place_y=place_y,
        place_w=place_w,
        place_h=place_h,
    )

    print("Done.")
    print(f"Outputs in: {output_dir}")
    print(f"- ASR JSON:   {audio_json}")
    print(f"- CHS SRT:    {chs_srt}")
    print(f"- ENG SRT:    {eng_srt}")
    print(f"- BI  SRT:    {bi_srt}")
    print(f"- TTS audio:  {tts_wav}")
    print(f"- Video dub:  {video_dub}")
    print(f"- Video+sub:  {video_sub}")


if __name__ == "__main__":
    main()

