from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import List

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
    stage_t0 = time.perf_counter()

    def _escape_filter_path(p: Path) -> str:
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

    def _write_ass_with_pos(
        srt_in: Path,
        ass_out: Path,
        *,
        pos_x: int,
        pos_y: int,
        ass_alignment: int,
        play_w: int,
        play_h: int,
    ) -> None:
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
            f"Style: Default,{font_name2},{fs2},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,{outline2},{shadow2},{ass_alignment},0,0,0,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        lines: List[str] = []
        for start, end, text in items:
            t = _escape_ass_text(text)
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\an{ass_alignment}\\pos({pos_x},{pos_y})}}{t}")
        ass_out.write_text("\n".join(header + lines) + "\n", encoding="utf-8")

    def _run_ffmpeg_with_filter(vf: str) -> str:
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
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout or "ffmpeg failed")
        print(f"[burn] ffmpeg_done: elapsed_s={(time.perf_counter() - t0):.3f}")
        return vf

    # 1) Try hard-burn
    hard_burn_errors: List[str] = []
    ass_path: Path | None = None
    try:
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
                    x1 *= W
                    y1 *= H
                    w1 *= W
                    h1 *= H
                x1 = float(x1)
                y1 = float(y1)
                w1 = float(w1)
                h1 = float(h1)
                x2 = x1 + max(0.0, w1)
                y2 = y1 + max(0.0, h1)
                # Rectangle semantics:
                # - X/Y are the box start (top-left), matching the UI labels.
                # - The subtitle block should be vertically centered inside the picked box.
                # - Preserve horizontal intent from the original alignment.
                src_align = int(alignment or 2)
                if src_align not in {1, 2, 3, 4, 5, 6, 7, 8, 9}:
                    src_align = 2
                if src_align in {1, 4, 7}:
                    align = 4
                    pos_x = int(round(x1))
                elif src_align in {3, 6, 9}:
                    align = 6
                    pos_x = int(round(x2))
                else:
                    align = 5
                    pos_x = int(round((x1 + x2) / 2.0))
                pos_y = int(round((y1 + y2) / 2.0))
                pos_x = max(0, min(pos_x, W))
                pos_y = max(0, min(pos_y, H))
                ass_path = srt_path.with_suffix(".place.ass")
                print(
                    "[burn] custom placement resolved: "
                    f"play={W}x{H} coord={coord} pos=({pos_x},{pos_y}) align={align} anchor=box_center "
                    f"box=({place_x},{place_y},{place_w},{place_h}) font={font_size}"
                )
                _write_ass_with_pos(
                    srt_path,
                    ass_path,
                    pos_x=pos_x,
                    pos_y=pos_y,
                    ass_alignment=align,
                    play_w=W,
                    play_h=H,
                )
                for vf in (
                    f"ass={_escape_filter_path(ass_path)}",
                    f"subtitles={_escape_filter_path(ass_path)}",
                ):
                    try:
                        print(f"[burn] hard-burn filter: {vf}")
                        _run_ffmpeg_with_filter(vf)
                        print(f"[burn] stage_done: mode=hardburn total_s={(time.perf_counter() - stage_t0):.3f}")
                        return
                    except Exception as exc:
                        hard_burn_errors.append(str(exc))
            else:
                hard_burn_errors.append("ffprobe_display_wh returned empty")

        srt_abs = srt_path.resolve()
        font_name2 = (font_name or "Arial").replace("'", "")
        vf = (
            f"subtitles={_escape_filter_path(srt_abs)}"
            f":charenc=UTF-8"
            f":force_style='FontName={font_name2},FontSize={int(font_size)},"
            f"Outline={int(outline)},Shadow={int(shadow)},MarginV={int(margin_v)},Alignment={int(alignment)}'"
        )
        _run_ffmpeg_with_filter(vf)
        print(f"[burn] stage_done: mode=hardburn-default total_s={(time.perf_counter() - stage_t0):.3f}")
        return
    except Exception as exc:
        hard_burn_errors.append(str(exc))

    if place_enable:
        detail = " | ".join(x for x in hard_burn_errors if x)[:3000]
        raise RuntimeError(f"subtitle hard-burn with custom placement failed: {detail}")
    print(f"[warn] burn_subtitles hard-burn failed, fallback to soft subtitles: {' | '.join(hard_burn_errors)[:2000]}")

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
    print(f"[burn] stage_done: mode=softsub total_s={(time.perf_counter() - stage_t0):.3f}")

