#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipelines.lib.asr.lite_asr import write_srt
from pipelines.lib.text.en_replace import apply_en_replacements, load_en_dict
from pipelines.quality_pipeline_impl import (
    Segment,
    _constrained_zh_polish_llm,
    _extract_zh_risky_spans_llm_two_pass,
    _lock_line_by_spans,
    _merge_dedupe_spans_same_line,
    _rule_based_suspect,
    translate_segments_llm,
)


def _parse_srt_time(t: str) -> float:
    hh, mm, rest = t.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def read_srt_segments(path: Path) -> List[Segment]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.rstrip("\n\r") for ln in raw.splitlines()]
    segs: List[Segment] = []
    i = 0
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        # index line
        i += 1
        if i >= len(lines):
            break
        timing = (lines[i] or "").strip()
        i += 1
        if "-->" not in timing:
            continue
        a, b = [x.strip() for x in timing.split("-->", 1)]
        start = _parse_srt_time(a)
        end = _parse_srt_time(b)
        text_lines: List[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        text = "\n".join(text_lines).strip()
        segs.append(Segment(start=start, end=end, text=text, translation=None))
    return segs


def _read_srt_texts(path: Path, *, use_translation: bool = False) -> List[str]:
    segs = read_srt_segments(path)
    out: List[str] = []
    for s in segs:
        v = s.translation if use_translation else s.text
        out.append((v or "").replace("\n", " ").strip())
    return out


_RE_POV = re.compile(r"\b(i|me|my|we|our|us)\b", re.I)
_RE_GENDERED = re.compile(r"\b(he|him|his|she|her|hers)\b", re.I)
_RE_FRAGMENT_OPEN = re.compile(r"^(watching|seeing|looking|while|when|because|although|if|as)\b", re.I)
_RE_VULGAR = re.compile(r"\b(hemorrhoid|hemorrhoids|anus|anal|penis|vagina)\b", re.I)


def analyze_lines(lines: List[str]) -> dict:
    def _count(rex: re.Pattern[str]) -> int:
        return sum(1 for s in lines if rex.search(s or ""))

    return {
        "lines": len(lines),
        "pov_lines": _count(_RE_POV),
        "gendered_pronoun_lines": _count(_RE_GENDERED),
        "fragment_open_lines": _count(_RE_FRAGMENT_OPEN),
        "vulgar_lines": _count(_RE_VULGAR),
        "vulgar_examples": [s for s in lines if _RE_VULGAR.search(s or "")][:10],
        "fragment_examples": [s for s in lines if _RE_FRAGMENT_OPEN.search((s or "").strip())][:10],
    }


def _load_task_meta(task_dir: Path) -> dict:
    p = task_dir / "task_meta.json"
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8", errors="ignore") or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _score_idx(idx: int, spans: List[Dict[str, Any]], rule_reasons: List[str]) -> int:
    s = 0
    for sp in spans or []:
        r = str((sp or {}).get("risk") or "").lower()
        if r.startswith("h"):
            s += 4
        elif r.startswith("m"):
            s += 3
        else:
            s += 2
    s += 2 * len(rule_reasons or [])
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)

    ap.add_argument("--llm-endpoint", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--llm-model", default="qwen3.5:9b")
    ap.add_argument("--phrase-model", default="", help="Optional dedicated model for zh phrase extraction (defaults to llm-model)")
    ap.add_argument("--llm-chunk-size", type=int, default=4)

    ap.add_argument("--zh-phrase-chunk-lines", type=int, default=20)
    ap.add_argument("--zh-phrase-max-spans", type=int, default=3)
    ap.add_argument("--zh-phrase-max-total", type=int, default=30)
    ap.add_argument("--no-zh-phrase-second-pass", action="store_true", default=False)

    ap.add_argument("--zh-polish-max-lines", type=int, default=40, help="Max suspect lines to rewrite (top-scored)")

    ap.add_argument("--mt-prompt-profile", default="subtitle_clean_v1")
    ap.add_argument("--mt-prompt-mode", choices=["short", "long"], default="short")
    ap.add_argument("--mt-two-pass-disable", action="store_true", default=False)
    ap.add_argument("--mt-long-fallback-enable", action="store_true", default=True)
    ap.add_argument("--mt-long-fallback-max-lines", type=int, default=6)
    ap.add_argument("--mt-long-fallback-max-ratio", type=float, default=0.15)
    ap.add_argument("--mt-long-examples-enable", action="store_true", default=False)
    ap.add_argument("--mt-reasoning-effort", default="none")

    ap.add_argument("--mt-style", default="")
    ap.add_argument("--mt-context-window", type=int, default=0)
    ap.add_argument("--mt-max-words-per-line", type=int, default=22)

    ap.add_argument("--selfcheck-max-lines", type=int, default=10)
    ap.add_argument("--selfcheck-max-ratio", type=float, default=0.25)

    ap.add_argument("--chs", default="chs.srt")
    ap.add_argument("--baseline-eng", default="eng.srt")
    ap.add_argument("--out-chs", default="chs.rerun.zhopt_v1.srt")
    ap.add_argument("--out-eng", default="eng.rerun.zhopt_mt_v1.srt")
    args = ap.parse_args()

    task_dir = Path("outputs") / str(args.task_id)
    chs_srt = task_dir / str(args.chs)
    baseline_srt = task_dir / str(args.baseline_eng)
    if not chs_srt.exists():
        raise FileNotFoundError(f"Missing {chs_srt}")
    if not baseline_srt.exists():
        raise FileNotFoundError(f"Missing baseline {baseline_srt}")

    segs_base = read_srt_segments(chs_srt)
    items_all: List[Tuple[int, str]] = [(i + 1, str(segs_base[i].text or "")) for i in range(len(segs_base))]

    # Rule-based suspects (cheap)
    rule_reasons_by_idx: Dict[int, List[str]] = {}
    for i, seg in enumerate(segs_base, 1):
        rr = _rule_based_suspect(seg)
        if rr:
            rule_reasons_by_idx[int(i)] = rr

    # LLM phrase extraction
    phrase_model = str(args.phrase_model or "").strip() or str(args.llm_model)
    spans_by_idx: Dict[int, List[Dict[str, Any]]] = {}
    t_p1 = time.time()
    for j in range(0, len(items_all), max(1, int(args.zh_phrase_chunk_lines))):
        chunk = items_all[j : j + max(1, int(args.zh_phrase_chunk_lines))]
        got = _extract_zh_risky_spans_llm_two_pass(
            endpoint=str(args.llm_endpoint),
            model=str(phrase_model),
            api_key="",
            items=chunk,
            max_spans_per_line=max(1, int(args.zh_phrase_max_spans)),
            max_total_spans=max(1, int(args.zh_phrase_max_total)),
            second_pass=(not bool(args.no_zh_phrase_second_pass)),
            second_pass_max_lines=min(5, max(2, int(max(1, int(args.zh_phrase_chunk_lines)) // 3))),
            second_pass_trigger_min_spans=1,
            log_enabled=True,
            log_prefix="[zhopt][P1]",
        )
        for k, v in (got or {}).items():
            if v:
                line = str(items_all[int(k) - 1][1]) if 1 <= int(k) <= len(items_all) else ""
                spans_by_idx[int(k)] = _merge_dedupe_spans_same_line(line, [dict(x) for x in (v or [])], max_spans=max(1, int(args.zh_phrase_max_spans)))
    p1_elapsed = time.time() - t_p1

    suspect_idxs = sorted({*spans_by_idx.keys(), *rule_reasons_by_idx.keys()})
    # Score and pick top N for rewrite.
    scored = []
    for idx in suspect_idxs:
        scored.append((idx, _score_idx(idx, spans_by_idx.get(idx, []) or [], rule_reasons_by_idx.get(idx, []) or [])))
    scored.sort(key=lambda x: (-x[1], x[0]))
    max_polish = max(0, int(args.zh_polish_max_lines))
    polish_idxs = [idx for idx, _ in scored[:max_polish]] if max_polish > 0 else []
    polish_set = set(polish_idxs)

    # Constrained polish for selected suspect lines (lock span texts)
    llm_lines_by_idx: Dict[int, str] = {}
    t_p2 = time.time()
    if polish_idxs:
        locked_inputs: List[Tuple[int, str]] = []
        for idx in polish_idxs:
            line = str(items_all[idx - 1][1]) if 1 <= idx <= len(items_all) else ""
            spans = spans_by_idx.get(idx, []) or []
            locked, _ = _lock_line_by_spans(line, spans)
            locked_inputs.append((idx, locked))
        chunk_n = max(1, int(args.zh_phrase_chunk_lines))
        for j in range(0, len(locked_inputs), chunk_n):
            chunk = locked_inputs[j : j + chunk_n]
            try:
                got = _constrained_zh_polish_llm(
                    endpoint=str(args.llm_endpoint),
                    model=str(args.llm_model),
                    api_key="",
                    items=chunk,
                )
            except Exception as exc:
                print(f"[warn] zh_polish failed for chunk size={len(chunk)}: {type(exc).__name__}: {exc}", flush=True)
                got = {}
            for idx, opt in (got or {}).items():
                llm_lines_by_idx[int(idx)] = str(opt or "").strip()
    p2_elapsed = time.time() - t_p2

    # Apply with lock validation (same as pipeline)
    segs_polished: List[Segment] = []
    zh_changed: List[Tuple[int, str, str]] = []
    for i, seg in enumerate(segs_base, 1):
        base = str(seg.text or "")
        opt = str(llm_lines_by_idx.get(i, "") or "").strip()
        if opt and i in polish_set:
            locked_texts = [str((sp or {}).get("text") or "") for sp in (spans_by_idx.get(i, []) or []) if str((sp or {}).get("text") or "")]
            if any(t and (t not in opt) for t in locked_texts):
                opt = ""
        if opt:
            opt2 = re.sub(r"<<LOCK\\d+>>", "", opt)
            opt2 = re.sub(r"<</LOCK\\d+>>", "", opt2)
            opt2 = re.sub(r"\\s+", " ", opt2).strip()
            final = opt2 or base
        else:
            final = base
        if final.strip() != base.strip():
            zh_changed.append((i, base, final))
        segs_polished.append(Segment(start=seg.start, end=seg.end, text=final, translation=None))

    out_chs = task_dir / str(args.out_chs)
    write_srt(out_chs, segs_polished, text_attr="text")

    # MT on polished CHS
    en_dict_path = task_dir / ".ygf_rules" / "en_dict.json"
    en_dict = load_en_dict(en_dict_path) if en_dict_path.exists() else []

    t_mt = time.time()
    segs_en = translate_segments_llm(
        segs_polished,
        endpoint=str(args.llm_endpoint),
        model=str(args.llm_model),
        api_key="",
        chunk_size=max(1, int(args.llm_chunk_size or 4)),
        context_window=max(0, int(args.mt_context_window)),
        style_hint=str(args.mt_style or "").strip(),
        max_words_per_line=max(0, int(args.mt_max_words_per_line)),
        prompt_mode=str(args.mt_prompt_mode),
        prompt_profile=str(args.mt_prompt_profile or "").strip(),
        two_pass_enable=(not bool(args.mt_two_pass_disable)),
        long_fallback_enable=bool(args.mt_long_fallback_enable),
        long_fallback_max_lines=max(0, int(args.mt_long_fallback_max_lines)),
        long_fallback_max_ratio=float(args.mt_long_fallback_max_ratio),
        long_examples_enable=bool(args.mt_long_examples_enable),
        glossary=None,
        selfcheck_enable=True,
        selfcheck_max_lines=max(0, int(args.selfcheck_max_lines)),
        selfcheck_max_ratio=float(args.selfcheck_max_ratio),
        context_src_lines=[s.text for s in segs_polished],
        mt_reasoning_effort=str(args.mt_reasoning_effort or "").strip(),
    )
    mt_elapsed = time.time() - t_mt

    if en_dict:
        apply_en_replacements(segs_en, en_dict)

    out_eng = task_dir / str(args.out_eng)
    write_srt(out_eng, segs_en, text_attr="translation")

    base_lines = _read_srt_texts(baseline_srt)
    new_lines = _read_srt_texts(out_eng)
    zh_lines_new = _read_srt_texts(out_chs)
    zh_lines_base = _read_srt_texts(chs_srt)

    n = max(len(base_lines), len(new_lines), len(zh_lines_new), len(zh_lines_base))
    while len(base_lines) < n:
        base_lines.append("")
    while len(new_lines) < n:
        new_lines.append("")
    while len(zh_lines_new) < n:
        zh_lines_new.append("")
    while len(zh_lines_base) < n:
        zh_lines_base.append("")

    en_changed: List[Tuple[int, str, str, str]] = []
    for i in range(n):
        if (base_lines[i] or "").strip() != (new_lines[i] or "").strip():
            en_changed.append((i + 1, zh_lines_new[i], base_lines[i], new_lines[i]))

    now = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path("automation") / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"zh_polish_mt_rerun_compare_{args.task_id}_{now}.md"

    payload = {
        "task_id": str(args.task_id),
        "paths": {"chs_base": str(chs_srt), "chs_polished": str(out_chs), "en_base": str(baseline_srt), "en_new": str(out_eng)},
        "zh": {
            "suspects": len(suspect_idxs),
            "polish_candidates": len(polish_idxs),
            "zh_changed_lines": len(zh_changed),
            "p1_phrase_extract_s": round(float(p1_elapsed), 3),
            "p2_polish_s": round(float(p2_elapsed), 3),
        },
        "mt": {
            "endpoint": str(args.llm_endpoint),
            "model": str(args.llm_model),
            "chunk_size": int(args.llm_chunk_size),
            "prompt_profile": str(args.mt_prompt_profile),
            "prompt_mode": str(args.mt_prompt_mode),
            "two_pass_enable": (not bool(args.mt_two_pass_disable)),
            "long_fallback_enable": bool(args.mt_long_fallback_enable),
            "long_fallback_max_lines": int(args.mt_long_fallback_max_lines),
            "long_fallback_max_ratio": float(args.mt_long_fallback_max_ratio),
            "selfcheck_max_lines": int(args.selfcheck_max_lines),
            "selfcheck_max_ratio": float(args.selfcheck_max_ratio),
            "reasoning_effort": str(args.mt_reasoning_effort or "").strip(),
        },
        "timing": {
            "mt_elapsed_s": round(float(mt_elapsed), 3),
            "lines": int(n),
            "mt_sec_per_line": round(float(mt_elapsed) / max(1, int(n)), 3),
        },
        "baseline_stats": analyze_lines(base_lines),
        "new_stats": analyze_lines(new_lines),
        "en_changed_lines": len(en_changed),
    }

    md: List[str] = []
    md.append(f"# ZH polish + MT rerun compare ({args.task_id})")
    md.append("")
    md.append("## Summary")
    md.append("")
    md.append("```json")
    md.append(json.dumps(payload, ensure_ascii=False, indent=2))
    md.append("```")
    md.append("")

    md.append("## Chinese diffs (only changed by zh_opt)")
    md.append("")
    if not zh_changed:
        md.append("- (No zh changes)")
    else:
        for idx, base, opt in zh_changed[:120]:
            md.append(f"### {idx}")
            md.append("")
            md.append(f"- Base: {base}")
            md.append(f"- Opt:  {opt}")
            md.append("")
        if len(zh_changed) > 120:
            md.append(f"- ... truncated: {len(zh_changed) - 120} more ...")
            md.append("")

    md.append("## English diffs (only changed)")
    md.append("")
    if not en_changed:
        md.append("- (No en changes)")
    else:
        for idx, zh, base, new in en_changed[:160]:
            md.append(f"### {idx}")
            md.append("")
            md.append(f"- ZH(polished): {zh}")
            md.append(f"- Baseline: {base}")
            md.append(f"- New:      {new}")
            md.append("")
        if len(en_changed) > 160:
            md.append(f"- ... truncated: {len(en_changed) - 160} more ...")
            md.append("")

    report_path.write_text("\n".join(md).strip() + "\n", encoding="utf-8")
    print(f"[ok] wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

