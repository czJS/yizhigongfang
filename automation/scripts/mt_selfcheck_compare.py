#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List, Tuple

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipelines.lib.asr.lite_asr import write_srt
from pipelines.lib.text.en_replace import apply_en_replacements, load_en_dict
from pipelines.quality_pipeline_impl import Segment, translate_segments_llm


def _parse_srt_time(t: str) -> float:
    # "HH:MM:SS,mmm"
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
        # index
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


def _read_srt_texts(path: Path) -> List[str]:
    segs = read_srt_segments(path)
    return [(s.translation or s.text or "").replace("\n", " ").strip() for s in segs]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", default="51037cd9ad07")
    ap.add_argument("--llm-endpoint", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--llm-model", default="qwen3.5:9b")
    ap.add_argument("--llm-chunk-size", type=int, default=2)
    ap.add_argument("--mt-style", default="Neutral, natural English subtitles; clear and faithful; avoid slang/memes; concise but complete")
    ap.add_argument("--mt-context-window", type=int, default=1)
    ap.add_argument("--mt-max-words-per-line", type=int, default=22)
    ap.add_argument("--mt-prompt-mode", choices=["short", "long"], default="long")
    ap.add_argument("--mt-long-fallback-enable", action="store_true", default=True)
    ap.add_argument("--mt-long-fallback-max-lines", type=int, default=10)
    ap.add_argument("--mt-long-fallback-max-ratio", type=float, default=0.25)
    ap.add_argument("--mt-long-examples-enable", action="store_true", default=False)
    ap.add_argument("--mt-prompt-profile", default="subtitle_clean_v1")
    ap.add_argument("--mt-two-pass-disable", action="store_true", default=False)
    ap.add_argument("--mt-reasoning-effort", default="")
    ap.add_argument("--selfcheck-max-lines", type=int, default=10)
    ap.add_argument("--selfcheck-max-ratio", type=float, default=0.25)
    ap.add_argument("--out-name", default="eng.rerun.selfcheck.fixed.srt")
    args = ap.parse_args()

    task_dir = Path("outputs") / str(args.task_id)
    chs_srt = task_dir / "chs.srt"
    if not chs_srt.exists():
        raise FileNotFoundError(f"Missing {chs_srt}")

    # Load project en_dict if present (rules center derived file).
    en_dict_path = task_dir / ".ygf_rules" / "en_dict.json"
    en_dict = load_en_dict(en_dict_path) if en_dict_path.exists() else []

    segs = read_srt_segments(chs_srt)
    segs_en = translate_segments_llm(
        segs,
        endpoint=str(args.llm_endpoint),
        model=str(args.llm_model),
        api_key="",
        chunk_size=max(1, int(args.llm_chunk_size or 2)),
        context_window=max(0, int(args.mt_context_window)),
        style_hint=str(args.mt_style),
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
        context_src_lines=[s.text for s in segs],
        mt_reasoning_effort=str(args.mt_reasoning_effort or "").strip(),
    )

    if en_dict:
        apply_en_replacements(segs_en, en_dict)

    out_srt = task_dir / str(args.out_name)
    write_srt(out_srt, segs_en, text_attr="translation")

    # Compare against existing baseline rerun (if present)
    baseline = task_dir / "eng.rerun.fixed.srt"
    now = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path("automation") / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"mt_selfcheck_compare_{args.task_id}_{now}.md"

    new_lines = _read_srt_texts(out_srt)
    new_stats = analyze_lines(new_lines)
    base_stats = None
    base_lines: List[str] = []
    if baseline.exists():
        base_lines = _read_srt_texts(baseline)
        base_stats = analyze_lines(base_lines)

    report = []
    report.append(f"# MT selfcheck compare ({args.task_id})")
    report.append("")
    report.append(f"- Output: `{out_srt}`")
    if baseline.exists():
        report.append(f"- Baseline: `{baseline}`")
    report.append("")
    report.append("## Stats")
    report.append("")
    report.append("```json")
    payload = {"baseline": base_stats, "selfcheck": new_stats}
    report.append(json.dumps(payload, ensure_ascii=False, indent=2))
    report.append("```")
    report.append("")
    report.append("## Examples (selfcheck)")
    report.append("")
    if new_stats["fragment_examples"]:
        report.append("### Fragment-like openings")
        for s in new_stats["fragment_examples"]:
            report.append(f"- {s}")
        report.append("")
    if new_stats["vulgar_examples"]:
        report.append("### Vulgar literals (should be 0 ideally)")
        for s in new_stats["vulgar_examples"]:
            report.append(f"- {s}")
        report.append("")

    report_path.write_text("\n".join(report).strip() + "\n", encoding="utf-8")
    print(json.dumps({"out_srt": str(out_srt), "report": str(report_path), "stats": payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

