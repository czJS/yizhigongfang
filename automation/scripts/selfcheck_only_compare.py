#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipelines.lib.text.srt_io import read_srt_texts  # noqa: E402


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


_RE_POV = re.compile(r"\b(i|me|my|we|our|us)\b", re.I)
_RE_GENDERED = re.compile(r"\b(he|him|his|she|her|hers)\b", re.I)
_RE_FRAGMENT_OPEN = re.compile(r"^(watching|seeing|looking|while|when|because|although|if|as)\b", re.I)
_RE_TABOO = re.compile(r"\b(hemorrhoid|hemorrhoids|anus|anal|penis|vagina)\b", re.I)


def analyze(lines: List[str]) -> dict:
    def c(rx: re.Pattern[str]) -> int:
        return sum(1 for s in lines if rx.search((s or "").strip()))

    return {
        "lines": len(lines),
        "pov_lines": c(_RE_POV),
        "gendered_pronoun_lines": c(_RE_GENDERED),
        "fragment_open_lines": c(_RE_FRAGMENT_OPEN),
        "taboo_lines": c(_RE_TABOO),
        "taboo_examples": [s for s in lines if _RE_TABOO.search(s or "")][:10],
        "fragment_examples": [s for s in lines if _RE_FRAGMENT_OPEN.search((s or "").strip())][:10],
    }


def needs_selfcheck(zh: str, en: str) -> bool:
    z = (zh or "").strip()
    e = (en or "").strip()
    if not z or not e:
        return False
    low = e.lower()
    if re.search(r"\b(i|me|my|we|our|us)\b", low) and not re.search(r"[我咱们我们俺本人]", z):
        return True
    if re.search(r"\b(she|her|hers)\b", low) and not re.search(r"(她|女人|女孩|姑娘|女士|老婆|妻|女友|女的)", z):
        return True
    if re.search(r"\b(he|him|his)\b", low) and not re.search(r"(他|男人|男的|小伙|先生|老公|丈夫|男友)", z):
        return True
    if _RE_FRAGMENT_OPEN.match(low.strip()):
        return True
    if _RE_TABOO.search(low):
        return True
    if re.search(r"\bswat\w*\b.*\bbites?\b", low):
        return True
    if e.endswith((",", ";", ":")):
        return True
    if re.search(r"\b(and|or|but|to|of|with|for)$", low):
        return True
    if len(z) >= 12 and len(e) <= 12:
        return True
    return False


def ollama_chat(endpoint: str, body: dict, *, timeout_s: int) -> str:
    url = endpoint.rstrip("/") + "/chat/completions"
    r = requests.post(url, json=body, timeout=timeout_s)
    if r.status_code != 200:
        return ""
    return (r.json() or {}).get("choices", [{}])[0].get("message", {}).get("content", "") or ""


def normalize_one_line(s: str) -> str:
    t = str(s or "").strip()
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    t = lines[0] if lines else ""
    t = re.sub(r"^\s*[-–•]+\s*", "", t)
    t = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", t)
    t = re.sub(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", t).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def selfcheck_one(zh: str, en: str, *, endpoint: str, model: str, timeout_s: int) -> str:
    prompt = "\n".join(
        [
            "You are reviewing ONE subtitle translation.",
            "If the English line has issues (fragment, wrong person/number/POV, invented identity attributes, awkward collocation, crude literal translation), rewrite it.",
            "If it's OK, output the EXACT SAME line as provided.",
            "Rules:",
            "- ENGLISH ONLY.",
            "- ONE LINE ONLY.",
            "- No numbering/bullets/extra commentary.",
            "- Do NOT invent identity attributes: gender, number, relationships, or narrator POV.",
            "- Avoid gendered pronouns (he/she) unless the Chinese clearly implies gender.",
            "- Avoid switching POV (I/we) unless explicitly present in Chinese.",
            "- Output a COMPLETE sentence (not a fragment).",
            "- Use natural collocations.",
            "- For exaggerated/comic narration or vulgar slang, rewrite into CLEAN comedic narration (PG-13).",
            f"ZH: {zh.strip()}",
            f"EN: {en.strip()}",
        ]
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": "Subtitle translation quality reviewer."}, {"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 128,
        "options": {"num_ctx": 2048, "num_batch": 128},
    }
    out = normalize_one_line(ollama_chat(endpoint, body, timeout_s=timeout_s)) or en
    if _RE_TABOO.search(out):
        prompt2 = "\n".join(
            [
                "Rewrite the English subtitle into CLEAN comedic narration (PG-13).",
                "Hard rules:",
                "- ENGLISH ONLY.",
                "- ONE LINE ONLY.",
                "- Do NOT use explicit sexual/bodily terms (including: hemorrhoid/anus/anal/penis/vagina).",
                "- Do NOT invent identity attributes (gender/relationships/POV).",
                "- Keep meaning and comedic intent; soften vulgar slang.",
                f"ZH: {zh.strip()}",
                f"EN_BAD: {out.strip()}",
                "EN_CLEAN:",
            ]
        )
        body2 = {
            "model": model,
            "messages": [{"role": "system", "content": "Subtitle translation cleaner."}, {"role": "user", "content": prompt2}],
            "temperature": 0.0,
            "max_tokens": 96,
        }
        out2 = normalize_one_line(ollama_chat(endpoint, body2, timeout_s=timeout_s))
        if out2 and not _RE_TABOO.search(out2):
            out = out2
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", default="51037cd9ad07")
    ap.add_argument("--baseline", default="eng.rerun.fixed.srt")
    ap.add_argument("--endpoint", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--max-lines", type=int, default=10)
    ap.add_argument("--max-ratio", type=float, default=0.25)
    ap.add_argument("--timeout-s", type=int, default=180)
    args = ap.parse_args()

    task_dir = Path("outputs") / str(args.task_id)
    chs_path = task_dir / "chs.srt"
    base_path = task_dir / str(args.baseline)
    if not chs_path.exists():
        raise FileNotFoundError(f"Missing {chs_path}")
    if not base_path.exists():
        raise FileNotFoundError(f"Missing {base_path}")

    zh_lines = [t.replace("\n", " ").strip() for t in read_srt_texts(chs_path)]
    base_blocks = read_srt_blocks(base_path)
    base_lines = [t.replace("\n", " ").strip() for _, _, t in base_blocks]
    if len(zh_lines) != len(base_lines):
        raise RuntimeError(f"line count mismatch: zh={len(zh_lines)} en={len(base_lines)}")

    idx_need = [i for i, (z, e) in enumerate(zip(zh_lines, base_lines)) if needs_selfcheck(z, e)]
    cap = max(0, int(args.max_lines))
    if args.max_ratio and args.max_ratio > 0:
        cap = min(cap, int(max(1, round(len(base_lines) * float(args.max_ratio)))))
    idx_pick = idx_need[:cap]

    out_lines = list(base_lines)
    for i in idx_pick:
        out_lines[i] = selfcheck_one(
            zh_lines[i],
            out_lines[i],
            endpoint=str(args.endpoint),
            model=str(args.model),
            timeout_s=max(30, int(args.timeout_s)),
        )

    # Write SRT with original timings
    out_path = task_dir / f"{base_path.stem}.selfcheck_only.srt"
    srt = []
    for idx, ((start, end, _), text) in enumerate(zip(base_blocks, out_lines), 1):
        # reuse original timing line formatting from seconds
        def fmt(sec: float) -> str:
            ms = int(round(sec * 1000))
            hh, rem = divmod(ms, 3_600_000)
            mm, rem = divmod(rem, 60_000)
            ss, ms2 = divmod(rem, 1_000)
            return f"{hh:02}:{mm:02}:{ss:02},{ms2:03}"

        srt.append(f"{idx}\n{fmt(start)} --> {fmt(end)}\n{text}\n")
    out_path.write_text("\n".join(srt), encoding="utf-8")

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path("automation") / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"selfcheck_only_compare_{args.task_id}_{stamp}.md"
    payload = {"baseline": analyze(base_lines), "selfcheck_only": analyze(out_lines), "picked": idx_pick, "need": idx_need}
    report = []
    report.append(f"# selfcheck-only compare ({args.task_id})")
    report.append("")
    report.append(f"- Baseline: `{base_path}`")
    report.append(f"- Output: `{out_path}`")
    report.append("")
    report.append("## Stats")
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

