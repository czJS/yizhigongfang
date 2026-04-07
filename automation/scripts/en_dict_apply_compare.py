#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipelines.lib.text.en_replace import apply_en_replacements, load_en_dict  # noqa: E402


def _parse_srt_time(t: str) -> float:
    hh, mm, rest = t.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def read_srt_blocks(path: Path) -> List[Tuple[float, float, str]]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.rstrip("\n\r") for ln in raw.splitlines()]
    out: List[Tuple[float, float, str]] = []
    i = 0
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        i += 1  # index
        if i >= len(lines):
            break
        timing = (lines[i] or "").strip()
        i += 1
        if "-->" not in timing:
            continue
        a, b = [x.strip() for x in timing.split("-->", 1)]
        start = _parse_srt_time(a)
        end = _parse_srt_time(b)
        txt: List[str] = []
        while i < len(lines) and lines[i].strip():
            txt.append(lines[i].strip())
            i += 1
        out.append((start, end, "\n".join(txt).strip()))
    return out


def write_srt_blocks(path: Path, blocks: List[Tuple[float, float, str]]) -> None:
    def fmt(sec: float) -> str:
        ms = int(round(sec * 1000))
        hh, rem = divmod(ms, 3_600_000)
        mm, rem = divmod(rem, 60_000)
        ss, ms2 = divmod(rem, 1_000)
        return f"{hh:02}:{mm:02}:{ss:02},{ms2:03}"

    parts: List[str] = []
    for idx, (start, end, text) in enumerate(blocks, 1):
        parts.append(f"{idx}\n{fmt(start)} --> {fmt(end)}\n{text}\n")
    path.write_text("\n".join(parts), encoding="utf-8")


_RE_TABOO = re.compile(r"\b(hemorrhoid|hemorrhoids|anus|anal|penis|vagina)\b", re.I)
_RE_FRAGMENT_OPEN = re.compile(r"^(watching|seeing|looking|while|when|because|although|if|as)\b", re.I)


def stats(lines: List[str]) -> dict:
    return {
        "lines": len(lines),
        "taboo_lines": sum(1 for s in lines if _RE_TABOO.search(s or "")),
        "fragment_open_lines": sum(1 for s in lines if _RE_FRAGMENT_OPEN.search((s or "").strip())),
        "taboo_examples": [s for s in lines if _RE_TABOO.search(s or "")][:10],
        "fragment_examples": [s for s in lines if _RE_FRAGMENT_OPEN.search((s or "").strip())][:10],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", default="51037cd9ad07")
    ap.add_argument("--in-srt", default="eng.rerun.fixed.srt")
    ap.add_argument("--dict-path", default=".ygf_rules/en_dict.json")
    args = ap.parse_args()

    task_dir = Path("outputs") / str(args.task_id)
    in_path = task_dir / str(args.in_srt)
    dict_path = task_dir / str(args.dict_path)
    if not in_path.exists():
        raise FileNotFoundError(f"Missing {in_path}")
    if not dict_path.exists():
        raise FileNotFoundError(f"Missing {dict_path}")

    mapping = load_en_dict(dict_path)
    blocks = read_srt_blocks(in_path)
    out_blocks: List[Tuple[float, float, str]] = []
    in_lines = []
    out_lines = []
    for start, end, txt in blocks:
        t = (txt or "").replace("\n", " ").strip()
        in_lines.append(t)
        t2 = apply_en_replacements(t, mapping).strip()
        out_lines.append(t2)
        out_blocks.append((start, end, t2))

    out_path = task_dir / f"{in_path.stem}.dict_applied.srt"
    write_srt_blocks(out_path, out_blocks)

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path("automation") / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"en_dict_apply_compare_{args.task_id}_{stamp}.md"
    payload = {"in": stats(in_lines), "out": stats(out_lines), "dict_size": len(mapping)}
    report = []
    report.append(f"# en_dict apply compare ({args.task_id})")
    report.append("")
    report.append(f"- Input: `{in_path}`")
    report.append(f"- Dict: `{dict_path}` (size={len(mapping)})")
    report.append(f"- Output: `{out_path}`")
    report.append("")
    report.append("```json")
    report.append(json.dumps(payload, ensure_ascii=False, indent=2))
    report.append("```")
    report.append("")
    report_path.write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({"out_srt": str(out_path), "report": str(report_path), "stats": payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

