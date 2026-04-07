#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def parse_srt_texts(raw: str) -> List[str]:
    """
    Minimal SRT reader that returns text blocks in order, joining multi-line blocks by '\n'.
    """
    lines = [ln.rstrip("\n\r") for ln in (raw or "").splitlines()]
    out: List[str] = []
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
        # timing line
        if "-->" in (lines[i] or ""):
            i += 1
        else:
            i += 1
            continue
        text_lines: List[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        out.append("\n".join(text_lines).strip())
    return out


def norm_line(s: str) -> str:
    t = str(s or "")
    t = t.replace("\r", "").strip()
    # treat line breaks as spaces for similarity
    t = re.sub(r"\s+", " ", t.replace("\n", " ")).strip()
    return t


def sim(a: str, b: str) -> float:
    return float(SequenceMatcher(None, norm_line(a), norm_line(b)).ratio())


def compare_lists(base: List[str], cand: List[str]) -> Dict[str, Any]:
    n = max(len(base), len(cand))
    exact = 0
    sims: List[float] = []
    changed: List[Dict[str, Any]] = []
    for i in range(n):
        a = base[i] if i < len(base) else ""
        b = cand[i] if i < len(cand) else ""
        s = sim(a, b)
        sims.append(s)
        if norm_line(a) == norm_line(b):
            exact += 1
        else:
            if len(changed) < 40:
                changed.append({"idx": i + 1, "base": a[:200], "cand": b[:200], "sim": round(s, 4)})
    avg_sim = sum(sims) / max(len(sims), 1)
    return {
        "base_n": len(base),
        "cand_n": len(cand),
        "aligned_n": n,
        "exact_match_n": exact,
        "exact_match_ratio": round(exact / max(n, 1), 4),
        "avg_similarity": round(avg_sim, 4),
        "changed_top": changed,
    }


def try_load_json(p: Path) -> Optional[Dict[str, Any]]:
    try:
        if not p.exists():
            return None
        obj = json.loads(read_text(p) or "{}")
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare eng.srt between runs and a golden baseline.")
    ap.add_argument("--golden-dir", type=Path, required=True, help="Directory containing golden eng.srt")
    ap.add_argument("--run-a", type=Path, required=True, help="Run A output dir")
    ap.add_argument("--run-b", type=Path, required=True, help="Run B output dir")
    ap.add_argument("--out", type=Path, required=True, help="Output JSON path")
    args = ap.parse_args()

    golden_srt = args.golden_dir / "eng.srt"
    a_srt = args.run_a / "eng.srt"
    b_srt = args.run_b / "eng.srt"

    golden = parse_srt_texts(read_text(golden_srt) if golden_srt.exists() else "")
    a = parse_srt_texts(read_text(a_srt) if a_srt.exists() else "")
    b = parse_srt_texts(read_text(b_srt) if b_srt.exists() else "")

    rep: Dict[str, Any] = {
        "golden_dir": str(args.golden_dir),
        "run_a": str(args.run_a),
        "run_b": str(args.run_b),
        "compare": {
            "a_vs_golden": compare_lists(golden, a),
            "b_vs_golden": compare_lists(golden, b),
            "b_vs_a": compare_lists(a, b),
        },
        "qe": {
            "a": try_load_json(args.run_a / "qe_report.json"),
            "b": try_load_json(args.run_b / "qe_report.json"),
        },
    }

    # light summary for qe reports
    def _qe_summary(obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not obj:
            return {"present": False}
        return {
            "present": True,
            "version": obj.get("version"),
            "mode": obj.get("mode"),
            "threshold": obj.get("threshold"),
            "fixed": obj.get("fixed"),
            "segments": obj.get("segments"),
            "budget": obj.get("budget"),
            "aggregations": obj.get("aggregations"),
        }

    rep["qe_summary"] = {"a": _qe_summary(rep["qe"]["a"]), "b": _qe_summary(rep["qe"]["b"])}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] wrote {args.out}")


if __name__ == "__main__":
    main()


