import subprocess
from pathlib import Path
from typing import List, Optional

from .media_probe import ffprobe_display_wh, probe_duration_s


def mux_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    sync_strategy: str = "slow",
    slow_max_ratio: float = 1.10,
    threshold_s: float = 0.05,
    tail_pad_max_s: float = 0.80,
    erase_subtitle_enable: bool = False,
    erase_subtitle_method: str = "delogo",
    erase_subtitle_coord_mode: str = "ratio",
    erase_subtitle_x: float = 0.0,
    erase_subtitle_y: float = 0.78,
    erase_subtitle_w: float = 1.0,
    erase_subtitle_h: float = 0.22,
    erase_subtitle_blur_radius: int = 12,
) -> None:
    """
    Mux video + new audio (hearing-first).

    - If audio is longer:
      - sync_strategy=slow: slow down whole video up to slow_max_ratio; if still shorter, pad last frame
        up to tail_pad_max_s to avoid abrupt ending.
      - sync_strategy=freeze: treated as "slow" with ratio=1.0 (no padding).
    - If audio is not longer: fast path (copy video, aac audio, -shortest).

    Optional:
    - erase_subtitle_enable: apply a best-effort erase filter (blur overlay / delogo) before muxing.
    """

    def _build_erase_filter() -> tuple[str, bool]:
        if not erase_subtitle_enable:
            return "", False
        m = (erase_subtitle_method or "delogo").strip().lower()
        coord = (erase_subtitle_coord_mode or "ratio").strip().lower()
        x = float(erase_subtitle_x or 0.0)
        y = float(erase_subtitle_y or 0.0)
        w = float(erase_subtitle_w or 0.0)
        h = float(erase_subtitle_h or 0.0)
        band = int(erase_subtitle_blur_radius or 0)
        band = max(0, min(band, 200))

        if coord == "px":
            xp = int(round(x))
            yp = int(round(y))
            wp = int(round(w))
            hp = int(round(h))
        else:
            wh = ffprobe_display_wh(video_path)
            if not wh:
                return "", False
            W, H = wh
            xp = int(round(max(0.0, min(1.0, x)) * W))
            yp = int(round(max(0.0, min(1.0, y)) * H))
            wp = int(round(max(0.0, min(1.0, w)) * W))
            hp = int(round(max(0.0, min(1.0, h)) * H))

        # clamp
        xp = max(0, xp)
        yp = max(0, yp)
        wp = max(2, wp)
        hp = max(2, hp)
        # Keep inside frame when possible
        wh2 = ffprobe_display_wh(video_path)
        if wh2:
            W, H = wh2
            if wp > W:
                wp = W
                xp = 0
            if hp > H:
                hp = H
                yp = 0
            if xp + wp > W:
                xp = max(0, W - wp)
            if yp + hp > H:
                yp = max(0, H - hp)

        # Use a precise blur overlay so the affected region exactly matches the rectangle.
        if m in {"delogo", "blur", "boxblur"}:
            radius = max(1, int(band or 8))
            vf = (
                f"split=2[base][tmp];"
                f"[tmp]crop={wp}:{hp}:{xp}:{yp},boxblur={radius}:1[blur];"
                f"[base][blur]overlay={xp}:{yp}"
            )
            return vf, True

        # fallback to delogo
        xp2 = int(xp - band)
        yp2 = int(yp - band)
        wp2 = int(wp + 2 * band)
        hp2 = int(hp + 2 * band)
        return f"delogo=x={xp2}:y={yp2}:w={wp2}:h={hp2}:show=0", False

    def _run_ffmpeg(cmd: List[str], label: str) -> subprocess.CompletedProcess:
        print(f"[mux] ffmpeg ({label}): {' '.join(cmd)}")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg ({label}) failed: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        return proc

    # Ensure inputs exist
    if not Path(video_path).exists():
        raise RuntimeError(f"mux failed: video not found: {video_path}")
    if not Path(audio_path).exists():
        raise RuntimeError(f"mux failed: audio not found: {audio_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    erase_vf, erase_complex = _build_erase_filter()

    v_dur = probe_duration_s(video_path)
    a_dur = probe_duration_s(audio_path)

    if v_dur is not None and a_dur is not None and a_dur > v_dur + float(threshold_s):
        strat = (sync_strategy or "slow").strip().lower()
        max_ratio = max(1.0, float(slow_max_ratio))
        ratio = max(1.0, float(a_dur) / max(float(v_dur), 0.001))

        vf_parts: List[str] = []
        if erase_vf and not erase_complex:
            vf_parts.append(erase_vf)
        if strat == "freeze":
            slow_ratio = 1.0
        else:
            slow_ratio = min(ratio, max_ratio)
            if slow_ratio > 1.0 + 1e-6:
                vf_parts.append(f"setpts={slow_ratio:.6f}*PTS")

        new_v = float(v_dur) * float(slow_ratio)
        remain = max(float(a_dur) - new_v, 0.0)
        tail_pad = min(float(tail_pad_max_s or 0.0), remain)
        if tail_pad > 0.02:
            vf_parts.append(f"tpad=stop_mode=clone:stop_duration={tail_pad:.3f}")

        if erase_complex:
            vf = erase_vf
            if slow_ratio > 1.0 + 1e-6:
                vf = f"{vf},setpts={slow_ratio:.6f}*PTS"
            if tail_pad > 0.02:
                vf = f"{vf},tpad=stop_mode=clone:stop_duration={tail_pad:.3f}"
        else:
            vf = ",".join(vf_parts)

        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path)]
        if vf:
            cmd += ["-filter:v", vf]
        cmd += [
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
            "-shortest",
            str(output_path),
        ]
        _run_ffmpeg(cmd, label="slow")
        if not output_path.exists():
            raise RuntimeError(f"mux failed: {output_path} not created (slow path)")
        return

    # Fast path: if no erase filter, copy video stream; otherwise re-encode with filter.
    if not erase_vf:
        cmd2 = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ]
        _run_ffmpeg(cmd2, label="copy")
        if not output_path.exists():
            raise RuntimeError(f"mux failed (copy path): {output_path} not created")
        return

    cmd3 = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-filter:v",
        erase_vf,
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
        "-shortest",
        str(output_path),
    ]
    _run_ffmpeg(cmd3, label="filter")
    if not output_path.exists():
        raise RuntimeError(f"mux failed (filter path): {output_path} not created")

