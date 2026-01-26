import json
import re
import subprocess
from pathlib import Path
from typing import Optional


def ffprobe_display_wh(p: Path) -> Optional[tuple[int, int]]:
    """
    Return display width/height with rotation handled (ffprobe rotation tag or displaymatrix).

    Best-effort:
    - Prefer ffprobe JSON output (fast and reliable)
    - Fall back to parsing `ffmpeg -i` output (Windows packaged builds may ship ffmpeg.exe but not ffprobe.exe)
    """

    def _probe_with_ffmpeg() -> Optional[tuple[int, int]]:
        try:
            cmd = ["ffmpeg", "-hide_banner", "-i", str(p)]
            cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
            out = cp.stdout or ""
            # Stream #0:0 ... Video: ... 1920x1080 ...
            m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
            if not m:
                return None
            w = int(m.group(1))
            h = int(m.group(2))
            # rotation can appear as: "rotate          : 90"
            rot = 0
            mrot = re.search(r"rotate\s*:\s*(-?\d+)", out)
            if mrot:
                rot = int(mrot.group(1)) % 360
            if rot in (90, 270):
                return (h, w)
            return (w, h)
        except Exception:
            return None

    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,rotation:stream_tags=rotate",
            "-of",
            "json",
            str(p),
        ]
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        data = json.loads(cp.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            return None
        s0 = streams[0] or {}
        w = int(s0.get("width") or 0)
        h = int(s0.get("height") or 0)
        if w <= 0 or h <= 0:
            return None
        rot = 0
        try:
            rot = int(s0.get("rotation") or 0)
        except Exception:
            rot = 0
        try:
            tags = s0.get("tags") or {}
            if not rot and "rotate" in tags:
                rot = int(tags.get("rotate") or 0)
        except Exception:
            pass
        rot = rot % 360
        if rot in (90, 270):
            return (h, w)
        return (w, h)
    except Exception:
        return _probe_with_ffmpeg()


def probe_duration_s(p: Path) -> Optional[float]:
    """Best-effort duration probe: prefer ffprobe, fall back to parsing ffmpeg output."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(p)]
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        data = json.loads(cp.stdout or "{}")
        fmt = data.get("format") or {}
        dur = fmt.get("duration")
        if dur is None:
            return None
        return float(dur)
    except Exception:
        try:
            cmd = ["ffmpeg", "-hide_banner", "-i", str(p)]
            cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
            out = cp.stdout or ""
            # Duration: 00:00:15.04,
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
            if not m:
                return None
            hh = float(m.group(1))
            mm = float(m.group(2))
            ss = float(m.group(3))
            return hh * 3600.0 + mm * 60.0 + ss
        except Exception:
            return None

