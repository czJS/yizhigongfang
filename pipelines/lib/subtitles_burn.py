from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from .media_probe import ffprobe_display_wh


def burn_subtitles(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    *,
    font_name: str = "Arial",
    font_size: int = 18,
    outline: int = 1,
    shadow: int = 0,
    margin_v: int = 24,
    alignment: int = 2,
    place_enable: bool = False,
    place_coord_mode: str = "ratio",
    place_x: float = 0.0,
    place_y: float = 0.78,
    place_w: float = 1.0,
    place_h: float = 0.22,
) -> None:
    """
    Burn subtitles into the video (hard-sub) so they are visible in common players by default.
    Fallback: if hard-burn fails (e.g., ffmpeg without libass), embed as soft subtitle track (mov_text).
    """

    def _escape_subtitles_filter_path(p: Path) -> str:
        s = str(p.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        return f"'{s}'"

    def _parse_srt_items(path: Path) -> List[tuple[str, str, str]]:
        raw = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
        blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
        out: List[tuple[str, str, str]] = []

        def _srt_ts_to_ass(ts: str) -> str:
            ts = ts.strip()
            if "," in ts:
                hhmmss, ms = ts.split(",", 1)
            else:
                hhmmss, ms = ts, "0"
            hh, mm, ss = [int(x) for x in hhmmss.split(":")]
            cs = int(round(int(ms[:3].ljust(3, "0")) / 10.0))
            return f"{hh:d}:{mm:02d}:{ss:02d}.{cs:02d}"

        for b in blocks:
            lines = b.split("\n")
            if len(lines) < 2:
                continue
            time_line = lines[1] if "-->" in lines[1] else (lines[0] if "-->" in lines[0] else "")
            if "-->" not in time_line:
                continue
            left, right = [x.strip() for x in time_line.split("-->", 1)]
            start = _srt_ts_to_ass(left)
            end = _srt_ts_to_ass(right.split(" ", 1)[0].strip())
            text_lines = lines[2:] if time_line == lines[1] else lines[1:]
            text = "\n".join(text_lines).strip()
            if not text:
                continue
            out.append((start, end, text))
        return out

    def _escape_ass_text(s: str) -> str:
        s = s.replace("{", "(").replace("}", ")")
        return s.replace("\n", r"\N")

    def _write_ass_with_pos(srt_in: Path, ass_out: Path, *, cx: int, cy: int, play_w: int, play_h: int) -> None:
        items = _parse_srt_items(srt_in)
        font_name2 = (font_name or "Arial").replace("\n", " ").replace("\r", " ").replace("'", "")
        outline2 = int(outline)
        shadow2 = int(shadow)
        fs2 = int(font_size)
        header = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {int(play_w)}",
            f"PlayResY: {int(play_h)}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{font_name2},{fs2},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,{outline2},{shadow2},5,0,0,0,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        lines: List[str] = []
        for start, end, text in items:
            t = _escape_ass_text(text)
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\an5\\pos({cx},{cy})}}{t}")
        ass_out.write_text("\n".join(header + lines) + "\n", encoding="utf-8")

    # 1) Try hard-burn
    try:
        vf = ""
        if place_enable:
            wh = ffprobe_display_wh(video_path)
            if wh:
                W, H = wh
                coord = (place_coord_mode or "ratio").strip().lower()
                if coord == "px":
                    x1 = float(place_x or 0.0)
                    y1 = float(place_y or 0.0)
                    w1 = float(place_w or 0.0)
                    h1 = float(place_h or 0.0)
                    cx = int(round(x1 + w1 / 2.0))
                    cy = int(round(y1 + h1 / 2.0))
                else:
                    x1 = max(0.0, min(1.0, float(place_x or 0.0)))
                    y1 = max(0.0, min(1.0, float(place_y or 0.0)))
                    w1 = max(0.0, min(1.0, float(place_w or 1.0)))
                    h1 = max(0.0, min(1.0, float(place_h or 0.22)))
                    cx = int(round((x1 + w1 / 2.0) * W))
                    cy = int(round((y1 + h1 / 2.0) * H))
                cx = max(0, min(cx, W))
                cy = max(0, min(cy, H))
                ass_path = srt_path.with_suffix(".place.ass")
                _write_ass_with_pos(srt_path, ass_path, cx=cx, cy=cy, play_w=W, play_h=H)
                vf = f"subtitles={_escape_subtitles_filter_path(ass_path)}:charenc=UTF-8"

        if not vf:
            srt_abs = srt_path.resolve()
            font_name2 = (font_name or "Arial").replace("'", "")
            vf = (
                f"subtitles={_escape_subtitles_filter_path(srt_abs)}"
                f":charenc=UTF-8"
                f":force_style='FontName={font_name2},FontSize={int(font_size)},"
                f"Outline={int(outline)},Shadow={int(shadow)},MarginV={int(margin_v)},Alignment={int(alignment)}'"
            )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout or "ffmpeg failed")
        return
    except Exception as exc:
        print(f"[warn] burn_subtitles hard-burn failed, fallback to soft subtitles: {exc}")

    # 2) Fallback to soft subtitle track
    cmd2 = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(srt_path),
        "-c",
        "copy",
        "-c:s",
        "mov_text",
        str(output_path),
    ]
    proc2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr or proc2.stdout or "ffmpeg soft-sub failed")

