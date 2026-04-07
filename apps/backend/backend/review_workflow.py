import difflib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.quality_report import generate_quality_report, write_quality_report
from pipelines.lib.media.subtitles_burn import burn_subtitles


def _ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _run(cmd: list, cwd: Optional[Path] = None) -> Tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode, proc.stdout or ""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def unified_diff(a: str, b: str, fromfile: str, tofile: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            a.splitlines(),
            b.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )


def mux_video_audio(
    source_video: Path,
    tts_wav: Path,
    output_video: Path,
    *,
    sync_strategy: str = "slow",
    slow_max_ratio: float = 1.08,
    threshold_s: float = 0.05,
) -> Tuple[int, str]:
    """
    Mux video + new audio.
    If audio is longer than video:
    - sync_strategy=slow: slow down whole video up to slow_max_ratio; if still shorter, pad tail frame as fallback.
    - sync_strategy=freeze: only pad tail frame.
    """

    def _dur_s(p: Path) -> Optional[float]:
        try:
            cmd = [_ffmpeg_path().replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(p)]
            rc, out = _run(cmd)
            if rc != 0:
                return None
            data = json.loads(out or "{}")
            d = float((data.get("format") or {}).get("duration") or 0)
            return d if d > 0 else None
        except Exception:
            return None

    v_dur = _dur_s(source_video)
    a_dur = _dur_s(tts_wav)

    if v_dur is not None and a_dur is not None and a_dur > v_dur + float(threshold_s):
        extra = max(a_dur - v_dur, 0.0)
        strat = (sync_strategy or "slow").strip().lower()
        max_ratio = max(1.0, float(slow_max_ratio))
        ratio = max(1.0, float(a_dur) / max(float(v_dur), 0.001))
        vf_parts = []
        if strat == "slow":
            slow_ratio = min(ratio, max_ratio)
            if slow_ratio > 1.0 + 1e-6:
                vf_parts.append(f"setpts={slow_ratio:.6f}*PTS")
            new_v = float(v_dur) * float(slow_ratio)
            remain = max(float(a_dur) - new_v, 0.0)
            if remain > 0.02:
                vf_parts.append(f"tpad=stop_mode=clone:stop_duration={remain:.3f}")
        else:
            vf_parts.append(f"tpad=stop_mode=clone:stop_duration={extra:.3f}")
        vf = ",".join(vf_parts)

        cmd = [
            _ffmpeg_path(),
            "-y",
            "-i",
            str(source_video),
            "-i",
            str(tts_wav),
            "-filter:v",
            vf,
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-t",
            f"{a_dur:.3f}",
            str(output_video),
        ]
        return _run(cmd)

    cmd2 = [
        _ffmpeg_path(),
        "-y",
        "-i",
        str(source_video),
        "-i",
        str(tts_wav),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_video),
    ]
    return _run(cmd2)


def embed_subtitles(
    input_video: Path,
    eng_srt: Path,
    output_video: Path,
    *,
    font_name: str = "Arial",
    font_size: int = 18,
    outline: int = 1,
    shadow: int = 0,
    margin_v: int = 24,
    alignment: int = 2,
    # Subtitle placement box (optional): force subtitle to box center (takes precedence when enabled).
    place_enable: bool = False,
    place_coord_mode: str = "ratio",
    place_x: float = 0.0,
    place_y: float = 0.78,
    place_w: float = 1.0,
    place_h: float = 0.22,
) -> Tuple[int, str]:
    """
    Burn subtitles (hard-sub) for deliverables. This path must match the main pipeline behavior.
    We delegate to the shared subtitle burner so review/regen respects:
    - sub_font_size/sub_margin_v/sub_alignment
    - subtitle placement box (sub_place_*) when enabled
    """
    try:
        # Robustness: empty/invalid srt should not hard-fail "apply" flows.
        # For silent/empty-subtitle tasks, produce a valid deliverable by copying the video as-is.
        if not read_text(eng_srt).strip():
            output_video.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(input_video, output_video)
            return 0, "skipped (empty srt)"
        burn_subtitles(
            input_video,
            eng_srt,
            output_video,
            font_name=font_name,
            font_size=int(font_size),
            outline=int(outline),
            shadow=int(shadow),
            margin_v=int(margin_v),
            alignment=int(alignment),
            place_enable=bool(place_enable),
            place_coord_mode=str(place_coord_mode or "ratio"),
            place_x=float(place_x or 0.0),
            place_y=float(place_y or 0.78),
            place_w=float(place_w or 1.0),
            place_h=float(place_h or 0.22),
        )
        return 0, "ok"
    except Exception as exc:
        return 1, f"embed_subtitles failed: {exc}"


def regenerate_quality_report(task_id: str, mode: str, work_dir: Path, source_video: Optional[Path], cfg: Dict[str, Any]) -> None:
    report = generate_quality_report(
        task_id=task_id,
        mode=mode,
        work_dir=work_dir,
        source_video=source_video,
        cfg=cfg,
    )
    write_quality_report(work_dir / "quality_report.json", report)


