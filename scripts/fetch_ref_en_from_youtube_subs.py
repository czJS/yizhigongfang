#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import random
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_TS_VTT = re.compile(
    r"(?P<s>\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*-->\s*(?P<e>\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _norm_en(s: str) -> str:
    t = str(s or "").replace("\r", "").strip()
    t = _TAG.sub("", t)
    t = html.unescape(t)
    t = t.replace("\n", " ")
    t = _WS.sub(" ", t).strip()
    return t


def _is_good_line(s: str) -> bool:
    t = _norm_en(s)
    if not t:
        return False
    # filter common non-speech cues
    low = t.lower()
    if low in {"[music]", "(music)", "[applause]", "(applause)"}:
        return False
    if low.startswith("[") and low.endswith("]"):
        return False
    if low.startswith("(") and low.endswith(")"):
        return False
    # require some letters
    if sum(ch.isalpha() for ch in t) < 3:
        return False
    # subtitle-like length (avoid ultra-long)
    toks = [x for x in re.split(r"[^a-zA-Z0-9']+", t) if x]
    if len(toks) < 2:
        return False
    if len(toks) > 24:
        return False
    return True


def _vtt_time_to_srt(ts: str) -> str:
    # VTT: HH:MM:SS.mmm or MM:SS.mmm
    parts = ts.split(":")
    if len(parts) == 2:
        mm, ss_ms = parts
        hh = "00"
    else:
        hh, mm, ss_ms = parts
    ss, ms = ss_ms.split(".")
    return f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d},{int(ms):03d}"


@dataclass
class Cue:
    start: str
    end: str
    text: str
    video_id: str
    url: str


def parse_webvtt(raw: str, *, video_id: str, url: str) -> List[Cue]:
    lines = [ln.rstrip("\n\r") for ln in (raw or "").splitlines()]
    cues: List[Cue] = []
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if not ln or ln.upper().startswith("WEBVTT"):
            i += 1
            continue
        m = _TS_VTT.search(ln)
        if not m:
            i += 1
            continue
        s, e = m.group("s"), m.group("e")
        i += 1
        text_lines: List[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        txt = _norm_en(" ".join(text_lines))
        if txt:
            cues.append(Cue(start=s, end=e, text=txt, video_id=video_id, url=url))
        i += 1
    return cues


def write_srt(path: Path, cues: List[Cue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out: List[str] = []
    for idx, c in enumerate(cues, 1):
        out.append(str(idx))
        out.append(f"{_vtt_time_to_srt(c.start)} --> {_vtt_time_to_srt(c.end)}")
        out.append(c.text or "…")
        out.append("")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def run_yt_dlp(url: str, *, out_dir: Path, lang: str) -> Tuple[str, List[Path]]:
    """
    Download subtitles for a YouTube URL as WebVTT.
    Returns (video_id, [downloaded subtitle paths]).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # use yt-dlp's video id for stable filenames
    tmpl = str(out_dir / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        f"{lang},en,en-US,en-GB",
        "--sub-format",
        "vtt",
        "-o",
        tmpl,
        url,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"yt-dlp failed for {url}:\n{p.stdout}\n{p.stderr}")
    # find video id from output files
    vtts = sorted(out_dir.glob("*.vtt"))
    if not vtts:
        raise RuntimeError(f"No .vtt subtitles downloaded for {url}. yt-dlp output:\n{p.stdout}\n{p.stderr}")
    # heuristic: newest file id is the first chunk before "."
    vid = vtts[-1].name.split(".", 1)[0]
    # return only those matching this id
    picked = [x for x in vtts if x.name.startswith(vid + ".")]
    return vid, picked


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch English subtitle tracks from YouTube and extract high-quality ref_en lines.")
    ap.add_argument("--urls", nargs="*", default=None, help="YouTube URLs (default: Blender open movies)")
    ap.add_argument("--n", type=int, default=200, help="How many ref_en lines to extract (default: 200)")
    ap.add_argument("--out-jsonl", type=Path, required=True, help="Output JSONL: {id, ref_en, meta}")
    ap.add_argument("--out-eng-srt", type=Path, default=None, help="Optional merged eng.srt with timestamps (from VTT cues)")
    ap.add_argument("--tmp-dir", type=Path, default=Path("eval/fluency_en/public_srt/tmp_ytdlp"), help="Download cache dir")
    ap.add_argument("--lang", type=str, default="en", help="Subtitle language code (default: en)")
    ap.add_argument("--seed", type=int, default=42, help="Shuffle seed")
    args = ap.parse_args()

    urls = list(args.urls or [])
    if not urls:
        # Default: Blender Foundation open movies on YouTube (stable, high-quality captions).
        urls = [
            "https://www.youtube.com/watch?v=YE7VzlLtp-4",  # Big Buck Bunny (official)
            "https://www.youtube.com/watch?v=eRsGyueVLvQ",  # Sintel (official)
            "https://www.youtube.com/watch?v=R6MlUcmOul8",  # Tears of Steel (official)
        ]

    all_cues: List[Cue] = []
    for u in urls:
        vid, files = run_yt_dlp(u, out_dir=Path(args.tmp_dir), lang=str(args.lang))
        for fp in files:
            raw = fp.read_text(encoding="utf-8", errors="ignore")
            all_cues.extend(parse_webvtt(raw, video_id=vid, url=u))

    # Build candidate lines (dedup by normalized text)
    uniq: Dict[str, Cue] = {}
    for c in all_cues:
        if not _is_good_line(c.text):
            continue
        k = _norm_en(c.text).lower()
        if k not in uniq:
            uniq[k] = c

    cues = list(uniq.values())
    if not cues:
        raise SystemExit("No usable subtitle lines extracted. Try different URLs or language codes.")

    rng = random.Random(int(args.seed))
    rng.shuffle(cues)
    picked = cues[: max(1, int(args.n))]

    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.out_jsonl.open("w", encoding="utf-8") as f:
        for i, c in enumerate(picked, 1):
            out = {
                "id": f"yt-{c.video_id}-{i:05d}",
                "ref_en": c.text,
                "meta": {"source": "youtube_subs", "video_id": c.video_id, "url": c.url, "start": c.start, "end": c.end},
            }
            f.write(json.dumps(out, ensure_ascii=False) + "\n")

    if args.out_eng_srt:
        write_srt(Path(args.out_eng_srt), picked)

    print(f"[ok] wrote {args.out_jsonl} (n={len(picked)}, unique_pool={len(cues)})")
    if args.out_eng_srt:
        print(f"[ok] wrote {args.out_eng_srt} (n={len(picked)})")


if __name__ == "__main__":
    main()


