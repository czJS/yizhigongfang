#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_TS = re.compile(
    r"(?P<s>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<e>\d{2}:\d{2}:\d{2},\d{3})"
)

_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm"}


def _repo_root() -> Path:
    # scripts/ -> repo root
    return Path(__file__).resolve().parents[1]


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _parse_srt_time(ts: str) -> float:
    # HH:MM:SS,mmm
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def _format_srt_time(seconds: float) -> str:
    ms_total = int(round(max(0.0, float(seconds)) * 1000.0))
    hh, rem = divmod(ms_total, 3_600_000)
    mm, rem = divmod(rem, 60_000)
    ss, ms = divmod(rem, 1_000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


@dataclass
class Cue:
    start_s: float
    end_s: float
    text: str


def parse_srt_cues(raw: str) -> List[Cue]:
    """
    Minimal SRT parser that keeps timestamps.
    - ignores index line strictly (tolerant)
    - joins multi-line text blocks by '\n'
    """
    lines = [ln.rstrip("\n\r") for ln in (raw or "").splitlines()]
    cues: List[Cue] = []
    i = 0
    while i < len(lines):
        # skip blanks
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        # optional index line
        if lines[i].strip().isdigit():
            i += 1
        if i >= len(lines):
            break
        m = _TS.search(lines[i])
        if not m:
            i += 1
            continue
        s = _parse_srt_time(m.group("s"))
        e = _parse_srt_time(m.group("e"))
        i += 1
        txt: List[str] = []
        while i < len(lines) and lines[i].strip():
            txt.append(lines[i].strip())
            i += 1
        text = "\n".join(txt).strip()
        if text:
            cues.append(Cue(start_s=float(s), end_s=float(e), text=text))
    # normalize ordering
    cues.sort(key=lambda c: (c.start_s, c.end_s))
    return cues


def looks_like_srt_file(p: Path) -> bool:
    """
    Accept non-standard subtitle filenames like '*.srt_en' (no .srt suffix) as long as the content looks like SRT.
    """
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    # quick probe: must contain at least one timestamp arrow line
    return bool(_TS.search(raw or ""))


def write_srt_cues(path: Path, cues: List[Cue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out: List[str] = []
    for idx, c in enumerate(cues, 1):
        out.append(str(idx))
        out.append(f"{_format_srt_time(c.start_s)} --> {_format_srt_time(c.end_s)}")
        out.append(c.text or "…")
        out.append("")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def clip_cues(cues: List[Cue], *, start_s: float, end_s: float) -> List[Cue]:
    out: List[Cue] = []
    for c in cues:
        if c.end_s <= start_s:
            continue
        if c.start_s >= end_s:
            break
        s = max(start_s, c.start_s) - start_s
        e = min(end_s, c.end_s) - start_s
        if e <= 0:
            continue
        out.append(Cue(start_s=float(s), end_s=float(e), text=c.text))
    return out


def join_text(cues: List[Cue]) -> str:
    # keep line breaks within cue, but join cues with '\n'
    lines: List[str] = []
    for c in cues:
        t = (c.text or "").strip()
        if t:
            lines.append(t)
    return "\n".join(lines).strip()


def ffprobe_duration_s(video: Path) -> float:
    ffprobe = "ffprobe"
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    try:
        return float((proc.stdout or "").strip())
    except Exception as exc:
        raise RuntimeError(f"failed to parse duration: {proc.stdout!r}") from exc


def ffmpeg_cut_clip(src: Path, dst: Path, *, start_s: float, end_s: float) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.2, float(end_s) - float(start_s))
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{float(start_s):.3f}",
        "-t",
        f"{float(dur):.3f}",
        "-i",
        str(src),
        "-map",
        "0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(dst),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg cut failed:\n{proc.stderr.strip()}")


def propose_windows_from_cues(
    cues: List[Cue],
    *,
    target_dur_s: float,
    min_dur_s: float,
    max_dur_s: float,
    pad_s: float,
) -> List[Tuple[float, float]]:
    """
    Turn a list of subtitle cues into a list of segment windows (start,end) that roughly match target duration.
    We group contiguous cues until duration crosses target (clamped to max).
    """
    if not cues:
        return []
    windows: List[Tuple[float, float]] = []
    i = 0
    while i < len(cues):
        s0 = float(cues[i].start_s)
        e0 = float(cues[i].end_s)
        j = i
        while j + 1 < len(cues) and (e0 - s0) < target_dur_s:
            j += 1
            e0 = max(e0, float(cues[j].end_s))
            # stop if it becomes too long
            if (e0 - s0) >= max_dur_s:
                break
        dur = e0 - s0
        if dur < min_dur_s:
            # skip tiny tail
            i = j + 1
            continue
        s = max(0.0, s0 - pad_s)
        e = e0 + pad_s
        windows.append((float(s), float(e)))
        i = j + 1
    return windows


def _path_for_docker(p: Path) -> str:
    # If path is within repo root, convert to /app/...
    repo = _repo_root()
    try:
        rel = p.resolve().relative_to(repo.resolve())
    except Exception:
        return str(p)
    return str(Path("/app") / rel)


def _pick_srt_by_lang(files: List[Path]) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Heuristic picking for (chs, eng) from a list of subtitle-like files.
    """
    def _is_zh(x: Path) -> bool:
        n = x.name.lower()
        return ("chs" in n) or ("zh" in n) or ("中文" in x.name) or n.endswith(".cn.srt") or n.endswith("_cn.srt")

    def _is_en(x: Path) -> bool:
        n = x.name.lower()
        return ("eng" in n) or ("en" in n) or ("英文" in x.name) or n.endswith(".en.srt") or n.endswith("_en.srt") or n.endswith(".srt_en") or n.endswith("_srt_en")

    zh = [p for p in files if _is_zh(p)]
    en = [p for p in files if _is_en(p)]
    if zh and en:
        return zh[0], en[0]
    if len(files) >= 2:
        return files[0], files[1]
    if len(files) == 1:
        return files[0], None
    return None, None


def _iter_video_sets(in_dir: Path) -> List[Tuple[Path, Path, Path]]:
    """
    Support two layouts:
    1) Directory layout:
       <in-dir>/<id>/video.mp4 + chs.srt + eng.srt
    2) Flat layout:
       <in-dir>/<stem>.mp4 + <stem>.srt + <stem>.srt_en (or other naming variants)
    Returns: list of (video, chs_srt, eng_srt)
    """
    out: List[Tuple[Path, Path, Path]] = []

    # Layout 1: subdirs
    subdirs = sorted([p for p in in_dir.iterdir() if p.is_dir()])
    for vd in subdirs:
        # pick any video file (prefer video.mp4)
        vids = [p for p in vd.iterdir() if p.is_file() and p.suffix.lower() in _VIDEO_EXTS]
        if not vids:
            continue
        v = vd / "video.mp4"
        if not v.exists():
            v = vids[0]

        # subtitle-like files: *.srt plus things like *.srt_en that look like SRT
        subs: List[Path] = []
        for p in vd.iterdir():
            if not p.is_file():
                continue
            if p.suffix.lower() == ".srt" or looks_like_srt_file(p):
                subs.append(p)
        chs, eng = _pick_srt_by_lang(sorted(subs))
        if v.exists() and chs and eng:
            out.append((v, chs, eng))

    if out:
        return out

    # Layout 2: flat (no usable subdirs)
    vids = sorted([p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() in _VIDEO_EXTS])
    for v in vids:
        stem = v.stem
        # common patterns for zh
        zh_candidates = [
            in_dir / f"{stem}.srt",
            in_dir / f"{stem}.chs.srt",
            in_dir / f"{stem}.zh.srt",
        ]
        # common patterns for en
        en_candidates = [
            in_dir / f"{stem}.eng.srt",
            in_dir / f"{stem}.en.srt",
            in_dir / f"{stem}.srt_en",   # your current naming
            in_dir / f"{stem}.srt.en",
        ]

        # pick first existing
        chs = next((p for p in zh_candidates if p.exists()), None)
        eng = next((p for p in en_candidates if p.exists()), None)

        # if still missing, try scanning all files starting with stem and looking like srt
        if not (chs and eng):
            subs: List[Path] = []
            for p in in_dir.iterdir():
                if not p.is_file():
                    continue
                if not p.name.startswith(stem + "."):
                    continue
                if p.suffix.lower() == ".srt" or looks_like_srt_file(p):
                    subs.append(p)
            chs2, eng2 = _pick_srt_by_lang(sorted(subs))
            chs = chs or chs2
            eng = eng or eng2

        if not chs or not eng:
            continue
        out.append((v, chs, eng))

    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build an E2E-20 dataset from a few long videos + timecoded zh/en golden SRT.")
    ap.add_argument(
        "--in-dir",
        type=Path,
        default=Path("eval/e2e_quality/golden_videos"),
        help="Input dir. Each subdir: {video.mp4, chs.srt, eng.srt}.",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("eval/e2e_quality/e2e20_from_videos"),
        help="Output dir: clips/, golden_segments/, segments_20.*.jsonl, fluency_cases_20.jsonl",
    )
    ap.add_argument("--n", type=int, default=20, help="Target number of segments to generate")
    ap.add_argument("--target-dur-s", type=float, default=65.0, help="Target segment duration (seconds)")
    ap.add_argument("--min-dur-s", type=float, default=40.0, help="Min segment duration (seconds)")
    ap.add_argument("--max-dur-s", type=float, default=95.0, help="Max segment duration (seconds)")
    ap.add_argument("--pad-s", type=float, default=0.2, help="Padding seconds on each side when cutting")
    args = ap.parse_args()

    repo = _repo_root()
    in_dir = (repo / args.in_dir).resolve() if not args.in_dir.is_absolute() else args.in_dir.resolve()
    out_dir = (repo / args.out_dir).resolve() if not args.out_dir.is_absolute() else args.out_dir.resolve()

    if not in_dir.exists():
        raise SystemExit(f"in-dir not found: {in_dir}")

    triples = _iter_video_sets(in_dir)
    if not triples:
        raise SystemExit(
            "No usable golden video sets found.\n"
            f"- in-dir: {in_dir}\n"
            "Expected either:\n"
            "1) <in-dir>/<id>/video.mp4 + chs.srt + eng.srt\n"
            "2) <in-dir>/<stem>.mp4 + <stem>.srt + <stem>.srt_en (or .en.srt)\n"
        )

    candidates: List[Dict[str, Any]] = []
    for v, chs, eng in triples:
        dur_s = ffprobe_duration_s(v)
        eng_cues = parse_srt_cues(_read_text(eng))
        chs_cues = parse_srt_cues(_read_text(chs))
        wins = propose_windows_from_cues(
            eng_cues,
            target_dur_s=float(args.target_dur_s),
            min_dur_s=float(args.min_dur_s),
            max_dur_s=float(args.max_dur_s),
            pad_s=float(args.pad_s),
        )
        # fallback: if eng cues too sparse, split by time
        if not wins:
            step = max(float(args.min_dur_s), min(float(args.target_dur_s), dur_s))
            k = int(math.ceil(dur_s / step))
            for i in range(k):
                s = i * step
                e = min(dur_s, s + step)
                if (e - s) >= float(args.min_dur_s):
                    wins.append((float(s), float(e)))
        for (s, e) in wins:
            candidates.append(
                {
                    "video_dir": str(v.parent),
                    "video": str(v),
                    "duration_s": round(float(dur_s), 3),
                    "start_s": round(float(s), 3),
                    "end_s": round(float(e), 3),
                    "gold_chs": str(chs),
                    "gold_eng": str(eng),
                    "eng_cues_n": len(eng_cues),
                    "chs_cues_n": len(chs_cues),
                }
            )

    if not candidates:
        raise SystemExit("no candidate windows found")

    # Deterministic selection: take candidates in input order, and cap to --n
    picked = candidates[: max(1, int(args.n))]
    if len(picked) < int(args.n):
        print(f"[warn] only {len(picked)} segments available (< n={int(args.n)}). Proceeding.", flush=True)

    clips_dir = out_dir / "clips"
    golden_dir = out_dir / "golden_segments"
    meta_dir = out_dir / "meta"
    clips_dir.mkdir(parents=True, exist_ok=True)
    golden_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    seg_rows_host: List[Dict[str, Any]] = []
    seg_rows_docker: List[Dict[str, Any]] = []
    fluency_cases: List[Dict[str, Any]] = []

    for idx, item in enumerate(picked, 1):
        sid = f"seg-{idx:04d}"
        src_v = Path(str(item["video"]))
        s = float(item["start_s"])
        e = float(item["end_s"])

        clip_path = (clips_dir / f"{sid}.mp4").resolve()
        ffmpeg_cut_clip(src_v, clip_path, start_s=s, end_s=e)

        # cut golden SRTs for this window and shift times
        chs_cues = parse_srt_cues(_read_text(Path(str(item["gold_chs"]))))
        eng_cues = parse_srt_cues(_read_text(Path(str(item["gold_eng"]))))
        chs_seg = clip_cues(chs_cues, start_s=s, end_s=e)
        eng_seg = clip_cues(eng_cues, start_s=s, end_s=e)

        gdir = golden_dir / sid
        write_srt_cues(gdir / "chs.srt", chs_seg)
        write_srt_cues(gdir / "eng.srt", eng_seg)

        # e2e segments jsonl (video is the clip file)
        seg_rows_host.append({"id": sid, "video": str(clip_path), "meta": {"source_video": str(src_v), "start_s": s, "end_s": e}})
        seg_rows_docker.append({"id": sid, "video": _path_for_docker(clip_path), "meta": {"source_video": _path_for_docker(src_v), "start_s": s, "end_s": e}})

        # fluency cases jsonl (zh/ref_en are from golden segments)
        fluency_cases.append(
            {
                "id": sid,
                "zh": join_text(chs_seg),
                "ref_en": join_text(eng_seg),
                "source": "e2e_golden_video",
                "meta": {"golden_dir": _path_for_docker(gdir), "clip": _path_for_docker(clip_path)},
            }
        )

    (meta_dir / "picked_windows.json").write_text(json.dumps(picked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_jsonl(out_dir / "segments_20.host.jsonl", seg_rows_host)
    _write_jsonl(out_dir / "segments_20.docker.jsonl", seg_rows_docker)
    _write_jsonl(out_dir / "fluency_cases_20.jsonl", fluency_cases)

    print("[ok] wrote:")
    print(f"  - {out_dir / 'segments_20.host.jsonl'}")
    print(f"  - {out_dir / 'segments_20.docker.jsonl'}")
    print(f"  - {out_dir / 'fluency_cases_20.jsonl'}")
    print(f"  - {meta_dir / 'picked_windows.json'}")
    print(f"[hint] golden segments: {golden_dir}")
    print(f"[hint] clips: {clips_dir}")


if __name__ == "__main__":
    main()



