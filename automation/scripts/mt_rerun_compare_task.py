#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Tuple

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipelines.lib.asr.lite_asr import write_srt
from pipelines.lib.text.en_replace import apply_en_replacements, load_en_dict
from pipelines.quality_pipeline_impl import Segment, translate_segments_llm


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


def _read_srt_texts(path: Path) -> List[str]:
    segs = read_srt_segments(path)
    out: List[str] = []
    for s in segs:
        out.append((s.translation or s.text or "").replace("\n", " ").strip())
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--llm-endpoint", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--llm-model", default="qwen3.5:9b")
    ap.add_argument("--llm-chunk-size", type=int, default=2)
    ap.add_argument(
        "--use-task-meta",
        action="store_true",
        default=True,
        help="Use outputs/<task>/task_meta.json MT params as defaults (can still override via CLI).",
    )
    ap.add_argument("--no-task-meta", action="store_true", default=False, help="Ignore task_meta.json and use CLI defaults")

    ap.add_argument("--mt-prompt-profile", default="subtitle_clean_v1")
    ap.add_argument("--mt-prompt-mode", choices=["short", "long"], default="long")
    ap.add_argument("--mt-long-fallback-enable", action="store_true", default=True)
    ap.add_argument("--mt-long-fallback-max-lines", type=int, default=10)
    ap.add_argument("--mt-long-fallback-max-ratio", type=float, default=0.25)
    ap.add_argument("--mt-long-examples-enable", action="store_true", default=False)
    ap.add_argument("--mt-two-pass-disable", action="store_true", default=False)
    ap.add_argument("--mt-reasoning-effort", default="")

    ap.add_argument("--mt-style", default="")
    ap.add_argument("--mt-context-window", type=int, default=1)
    ap.add_argument("--mt-max-words-per-line", type=int, default=22)

    ap.add_argument("--selfcheck-max-lines", type=int, default=10)
    ap.add_argument("--selfcheck-max-ratio", type=float, default=0.25)

    ap.add_argument("--baseline-eng", default="eng.srt")
    ap.add_argument("--chs", default="chs.srt")
    ap.add_argument("--out-srt", default="eng.rerun.mt_v2.srt")
    args = ap.parse_args()

    task_dir = Path("outputs") / str(args.task_id)
    chs_srt = task_dir / str(args.chs)
    baseline_srt = task_dir / str(args.baseline_eng)
    if not chs_srt.exists():
        raise FileNotFoundError(f"Missing {chs_srt}")
    if not baseline_srt.exists():
        raise FileNotFoundError(f"Missing baseline {baseline_srt}")

    use_meta = bool(args.use_task_meta) and (not bool(args.no_task_meta))
    meta = _load_task_meta(task_dir) if use_meta else {}
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}

    mt_context_window = int(params.get("mt_context_window", args.mt_context_window) or args.mt_context_window)
    mt_style = str(params.get("mt_style", args.mt_style) or args.mt_style).strip()
    mt_max_words = int(params.get("mt_max_words_per_line", args.mt_max_words_per_line) or args.mt_max_words_per_line)
    mt_prompt_mode = str(params.get("mt_prompt_mode", args.mt_prompt_mode) or args.mt_prompt_mode).strip().lower()
    mt_prompt_mode = "long" if mt_prompt_mode == "long" else "short"
    mt_long_fb = bool(params.get("mt_long_fallback_enable", args.mt_long_fallback_enable))
    mt_long_ex = bool(params.get("mt_long_examples_enable", args.mt_long_examples_enable))

    # rules center en_dict
    en_dict_path = task_dir / ".ygf_rules" / "en_dict.json"
    en_dict = load_en_dict(en_dict_path) if en_dict_path.exists() else []

    segs = read_srt_segments(chs_srt)
    t0 = time.time()
    segs_en = translate_segments_llm(
        segs,
        endpoint=str(args.llm_endpoint),
        model=str(args.llm_model),
        api_key="",
        chunk_size=max(1, int(args.llm_chunk_size or 2)),
        context_window=max(0, int(mt_context_window)),
        style_hint=str(mt_style),
        max_words_per_line=max(0, int(mt_max_words)),
        prompt_mode=str(mt_prompt_mode),
        prompt_profile=str(args.mt_prompt_profile or "").strip(),
        two_pass_enable=(not bool(args.mt_two_pass_disable)),
        long_fallback_enable=bool(mt_long_fb),
        long_fallback_max_lines=max(0, int(args.mt_long_fallback_max_lines)),
        long_fallback_max_ratio=float(args.mt_long_fallback_max_ratio),
        long_examples_enable=bool(mt_long_ex),
        glossary=None,
        selfcheck_enable=True,
        selfcheck_max_lines=max(0, int(args.selfcheck_max_lines)),
        selfcheck_max_ratio=float(args.selfcheck_max_ratio),
        context_src_lines=[s.text for s in segs],
        mt_reasoning_effort=str(args.mt_reasoning_effort or "").strip(),
    )
    elapsed_s = time.time() - t0

    if en_dict:
        apply_en_replacements(segs_en, en_dict)

    out_srt = task_dir / str(args.out_srt)
    write_srt(out_srt, segs_en, text_attr="translation")

    base_lines = _read_srt_texts(baseline_srt)
    new_lines = _read_srt_texts(out_srt)
    zh_lines = _read_srt_texts(chs_srt)

    # Align lengths (best-effort)
    n = max(len(base_lines), len(new_lines), len(zh_lines))
    while len(base_lines) < n:
        base_lines.append("")
    while len(new_lines) < n:
        new_lines.append("")
    while len(zh_lines) < n:
        zh_lines.append("")

    changed: List[Tuple[int, str, str, str]] = []
    for i in range(n):
        if (base_lines[i] or "").strip() != (new_lines[i] or "").strip():
            changed.append((i + 1, zh_lines[i], base_lines[i], new_lines[i]))

    now = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path("automation") / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"mt_rerun_compare_{args.task_id}_{now}.md"

    payload = {
        "task_id": str(args.task_id),
        "chs": str(chs_srt),
        "baseline": str(baseline_srt),
        "out": str(out_srt),
        "changed_lines": len(changed),
        "baseline_stats": analyze_lines(base_lines),
        "new_stats": analyze_lines(new_lines),
        "mt": {
            "endpoint": str(args.llm_endpoint),
            "model": str(args.llm_model),
            "chunk_size": int(args.llm_chunk_size),
            "prompt_profile": str(args.mt_prompt_profile),
            "prompt_mode": str(mt_prompt_mode),
            "two_pass_enable": (not bool(args.mt_two_pass_disable)),
            "long_fallback_enable": bool(mt_long_fb),
            "long_fallback_max_lines": int(args.mt_long_fallback_max_lines),
            "long_fallback_max_ratio": float(args.mt_long_fallback_max_ratio),
            "selfcheck_max_lines": int(args.selfcheck_max_lines),
            "selfcheck_max_ratio": float(args.selfcheck_max_ratio),
        },
        "timing": {
            "elapsed_s": round(float(elapsed_s), 3),
            "lines": int(n),
            "sec_per_line": round(float(elapsed_s) / max(1, int(n)), 3),
        },
    }

    md: List[str] = []
    md.append(f"# MT rerun compare ({args.task_id})")
    md.append("")
    md.append(f"- CHS: `{chs_srt}`")
    md.append(f"- Baseline EN: `{baseline_srt}`")
    md.append(f"- New EN: `{out_srt}`")
    md.append("")
    md.append("## Summary")
    md.append("")
    md.append("```json")
    md.append(json.dumps(payload, ensure_ascii=False, indent=2))
    md.append("```")
    md.append("")
    md.append("## Line-by-line diffs (only changed)")
    md.append("")
    if not changed:
        md.append("- (No changes)")
    else:
        for idx, zh, base, new in changed:
            md.append(f"### {idx}")
            md.append(f"- ZH: {zh}")
            md.append(f"- OLD: {base}")
            md.append(f"- NEW: {new}")
            md.append("")

    report_path.write_text("\n".join(md).rstrip() + "\n", encoding="utf-8")
    print(json.dumps({"out_srt": str(out_srt), "report": str(report_path), "summary": payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

