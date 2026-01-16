#!/usr/bin/env python3
from __future__ import annotations

"""
Run translation on eval/fluency_en cases using the *current* quality_pipeline prompt logic.

Why:
- run_fluency_translate.py uses its own prompt; it doesn't reflect the product prompt we just tuned.
- This script imports translate_segments_llm / translate_segments_llm_tra from scripts/quality_pipeline.py
  so it evaluates the real in-product translation prompt.

Output format matches scripts/eval_fluency_suite.py: jsonl with {id, pred_en, meta}.
"""

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List


def _read_jsonl(p: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        obj = json.loads(ln)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Run quality_pipeline translation prompt on cases.jsonl and write preds.jsonl")
    ap.add_argument("--cases", type=Path, required=True, help="cases.jsonl path (must include zh)")
    ap.add_argument("--out", type=Path, required=True, help="output preds.jsonl path")
    ap.add_argument("--resume", action="store_true", help="Resume from existing --out (append, skip completed ids)")
    ap.add_argument("--endpoint", type=str, default=os.getenv("LLM_ENDPOINT", ""), help="OpenAI-compatible endpoint (env: LLM_ENDPOINT)")
    ap.add_argument("--model", type=str, default=os.getenv("LLM_MODEL", ""), help="Model (env: LLM_MODEL)")
    ap.add_argument("--api-key", type=str, default=os.getenv("LLM_API_KEY", ""), help="API key (env: LLM_API_KEY)")
    ap.add_argument("--chunk-size", type=int, default=2, help="How many lines per request when allowed (quality_pipeline will force 1 when context/json enabled)")
    ap.add_argument("--context-window", type=int, default=0, help="prev/next context window (0=off)")
    ap.add_argument("--topic", type=str, default="", help="Optional topic hint")
    ap.add_argument("--style", type=str, default="", help="Optional style hint (mt_style)")
    ap.add_argument("--max-words-per-line", type=int, default=0, help="Max words per line (0=off)")
    ap.add_argument("--compact-enable", action="store_true", help="Enable compact rewrite for over-long lines")
    ap.add_argument("--compact-aggressive", action="store_true", help="Allow aggressive compact rewrite")
    ap.add_argument("--compact-temperature", type=float, default=0.1)
    ap.add_argument("--compact-max-tokens", type=int, default=96)
    ap.add_argument("--compact-timeout-s", type=int, default=120)
    ap.add_argument("--enable-tra", action="store_true", help="Enable TRA translation path (quality_pipeline implementation)")
    ap.add_argument("--tra-json-enable", action="store_true", help="Use structured TRA JSON output (more stable)")
    args = ap.parse_args()

    if not args.endpoint or not args.model:
        raise SystemExit("Missing --endpoint/--model (or env LLM_ENDPOINT/LLM_MODEL)")

    # Import the real product prompt logic.
    # NOTE: This module is large; keep imports inside main to avoid import-time cost when used as a library.
    from scripts.quality_pipeline import Segment, translate_segments_llm, translate_segments_llm_tra  # type: ignore

    rows = _read_jsonl(args.cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    done_ids: set[str] = set()
    if args.resume and args.out.exists():
        try:
            for ln in args.out.read_text(encoding="utf-8", errors="ignore").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    cid = str(obj.get("id") or "").strip()
                    pred = str(obj.get("pred_en") or "")
                    err = str(((obj.get("meta") or {}) if isinstance(obj.get("meta"), dict) else {}).get("error") or "")
                    if cid and (pred.strip() or err.strip()):
                        done_ids.add(cid)
        except Exception:
            done_ids = set()
            args.resume = False

    mode = "a" if (args.resume and args.out.exists()) else "w"
    t0 = time.time()
    n_done = 0
    with args.out.open(mode, encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            cid = str(r.get("id") or "").strip()
            zh = str(r.get("zh") or "").strip()
            if not cid or not zh:
                continue
            if args.resume and cid in done_ids:
                continue

            segs = [Segment(start=0.0, end=1.0, text=zh)]
            pred = ""
            err = ""
            try:
                if args.enable_tra:
                    out_segs, _dbg = translate_segments_llm_tra(
                        segs,
                        endpoint=str(args.endpoint),
                        model=str(args.model),
                        api_key=str(args.api_key or ""),
                        chunk_size=max(1, int(args.chunk_size)),
                        save_debug_path=None,
                        tra_json_enable=bool(args.tra_json_enable),
                        context_window=max(0, int(args.context_window)),
                        topic_hint=str(args.topic or ""),
                        style_hint=str(args.style or ""),
                        max_words_per_line=int(args.max_words_per_line or 0),
                        compact_enable=bool(args.compact_enable),
                        compact_aggressive=bool(args.compact_aggressive),
                        compact_temperature=float(args.compact_temperature),
                        compact_max_tokens=int(args.compact_max_tokens),
                        compact_timeout_s=int(args.compact_timeout_s),
                        glossary=None,
                        glossary_prompt_enable=False,
                        context_src_lines=None,
                    )
                    pred = str((out_segs[0].translation if out_segs else "") or "").strip()
                else:
                    out_segs = translate_segments_llm(
                        segs,
                        endpoint=str(args.endpoint),
                        model=str(args.model),
                        api_key=str(args.api_key or ""),
                        chunk_size=max(1, int(args.chunk_size)),
                        context_window=max(0, int(args.context_window)),
                        topic_hint=str(args.topic or ""),
                        style_hint=str(args.style or ""),
                        max_words_per_line=int(args.max_words_per_line or 0),
                        compact_enable=bool(args.compact_enable),
                        compact_aggressive=bool(args.compact_aggressive),
                        compact_temperature=float(args.compact_temperature),
                        compact_max_tokens=int(args.compact_max_tokens),
                        compact_timeout_s=int(args.compact_timeout_s),
                        glossary=None,
                        glossary_prompt_enable=False,
                        selfcheck_enable=False,
                        mt_json_enable=False,
                        context_src_lines=None,
                    )
                    pred = str((out_segs[0].translation if out_segs else "") or "").strip()
            except Exception as e:
                pred = ""
                err = f"error:{e}"

            out_obj = {
                "id": cid,
                "pred_en": pred,
                "meta": {
                    "engine": "quality_pipeline_prompt",
                    "enable_tra": bool(args.enable_tra),
                    "tra_json_enable": bool(args.tra_json_enable),
                    "topic": str(args.topic or ""),
                    "style": str(args.style or ""),
                    "max_words_per_line": int(args.max_words_per_line or 0),
                    "compact_enable": bool(args.compact_enable),
                    "compact_aggressive": bool(args.compact_aggressive),
                    "error": err,
                },
            }
            f.write(json.dumps(out_obj, ensure_ascii=False) + "\n")
            f.flush()
            n_done += 1
            if i % 20 == 0:
                dt = time.time() - t0
                rate = n_done / max(1e-9, dt)
                print(f"[progress] {n_done}/{len(rows)} ({rate:.2f} items/s)", flush=True)

    dt = time.time() - t0
    print(f"[ok] wrote {args.out} (n={n_done}, {dt:.1f}s)")


if __name__ == "__main__":
    main()


