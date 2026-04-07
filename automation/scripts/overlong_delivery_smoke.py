#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _http_request(
    method: str,
    url: str,
    body: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout_s: int = 60,
) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url=url, data=body, method=method.upper())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            code = int(getattr(resp, "status", 200))
            hdrs = {k.lower(): v for k, v in dict(resp.headers).items()}
            return code, hdrs, resp.read() or b""
    except urllib.error.HTTPError as e:
        data = e.read() if hasattr(e, "read") else b""
        hdrs = {k.lower(): v for k, v in dict(getattr(e, "headers", {}) or {}).items()}
        return int(e.code), hdrs, data


def _http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout_s: int = 60,
) -> Tuple[int, Dict[str, str], Any]:
    body = None
    headers: Dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["content-type"] = "application/json"
    code, hdrs, data = _http_request(method, url, body=body, headers=headers, timeout_s=timeout_s)
    if not data:
        return code, hdrs, None
    try:
        return code, hdrs, json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return code, hdrs, data.decode("utf-8", errors="replace")


def _multipart_file(field: str, filename: str, content: bytes, content_type: str = "application/octet-stream") -> Tuple[bytes, str]:
    boundary = "----ygfBoundary" + str(int(time.time() * 1000))
    crlf = b"\r\n"
    parts = [
        b"--" + boundary.encode("ascii"),
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'.encode("utf-8"),
        f"Content-Type: {content_type}".encode("utf-8"),
        b"",
        content,
        b"--" + boundary.encode("ascii") + b"--",
        b"",
    ]
    return crlf.join(parts), boundary


def _wait_for_backend_ready(base_url: str, *, timeout_s: int = 120) -> None:
    deadline = time.time() + max(10, int(timeout_s or 120))
    while time.time() < deadline:
        code, _, payload = _http_json("GET", _join_url(base_url, "/api/health"), None, timeout_s=5)
        if code == 200 and isinstance(payload, dict):
            return
        time.sleep(1.5)
    raise RuntimeError("backend not ready")


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def _ensure_short_clip(src_path: Path, *, clip_duration_s: int, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{src_path.stem}_{clip_duration_s}s{src_path.suffix}"
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    cmd = [
        _ffmpeg(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        "0",
        "-t",
        str(max(5, int(clip_duration_s))),
        "-i",
        str(src_path),
        "-c",
        "copy",
        str(dst),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"ffmpeg clip failed: {proc.stdout[-1000:]}")
    return dst


def _upload_media(base: str, media_path: Path, *, timeout_s: int) -> str:
    body, boundary = _multipart_file("file", media_path.name, media_path.read_bytes(), content_type="video/mp4")
    code, _, payload = _http_request(
        "POST",
        _join_url(base, "/api/upload"),
        body=body,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        timeout_s=timeout_s,
    )
    if code != 200:
        raise RuntimeError(f"upload failed: http {code}")
    obj = json.loads(payload.decode("utf-8", errors="replace") or "{}")
    uploaded_path = str((obj or {}).get("path") or "")
    if not uploaded_path:
        raise RuntimeError(f"upload missing path: {obj!r}")
    return uploaded_path


def _poll_status(base: str, task_id: str, *, timeout_s: int, interval_s: float) -> Dict[str, Any]:
    deadline = time.time() + max(30, int(timeout_s or 120))
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        code, _, payload = _http_json(
            "GET",
            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/status"),
            None,
            timeout_s=30,
        )
        if code == 200 and isinstance(payload, dict):
            last = payload
            state = str(payload.get("state") or "")
            if state in {"paused", "completed", "failed", "cancelled"}:
                return payload
        time.sleep(max(0.5, float(interval_s)))
    raise RuntimeError(f"poll timeout: last={last}")


def _parse_srt_blocks(content: str) -> List[Dict[str, str]]:
    blocks: List[Dict[str, str]] = []
    raw_blocks = [blk.strip() for blk in (content or "").replace("\r\n", "\n").split("\n\n") if blk.strip()]
    for blk in raw_blocks:
        lines = [ln for ln in blk.split("\n") if ln.strip()]
        if len(lines) < 3:
            continue
        blocks.append(
            {
                "index": lines[0].strip(),
                "timing": lines[1].strip(),
                "text": "\n".join(lines[2:]).strip(),
            }
        )
    return blocks


def _render_srt(blocks: List[Dict[str, str]], texts: List[str]) -> str:
    out: List[str] = []
    if not texts:
        raise RuntimeError("overlong sample texts are empty")
    for i, blk in enumerate(blocks):
        text = texts[i % len(texts)].strip()
        out.append(str(blk.get("index") or (i + 1)).strip())
        out.append(str(blk.get("timing") or "").strip())
        out.append(text)
        out.append("")
    return "\n".join(out).strip() + "\n"


def _max_srt_line_chars(path: Path) -> int:
    if not path.exists():
        return 0
    mx = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.isdigit() or "-->" in s:
            continue
        mx = max(mx, len(s))
    return mx


def _ffprobe_media(path: Path) -> Dict[str, Any]:
    cmd = [
        _ffprobe(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stdout[-500:]}")
    obj = json.loads(proc.stdout or "{}")
    streams = obj.get("streams") if isinstance(obj, dict) else []
    fmt = obj.get("format") if isinstance(obj, dict) else {}
    duration_s = 0.0
    try:
        duration_s = float((fmt or {}).get("duration") or 0.0)
    except Exception:
        duration_s = 0.0
    has_audio = any(isinstance(s, dict) and s.get("codec_type") == "audio" for s in (streams or []))
    has_video = any(isinstance(s, dict) and s.get("codec_type") == "video" for s in (streams or []))
    return {"duration_s": duration_s, "has_audio": has_audio, "has_video": has_video}


def _default_overlong_lines() -> List[str]:
    return [
        "When spring comes back, the willow branches that once looked lifeless will slowly turn green again, and the peach blossoms that faded away will open once more in the light.",
        "But please tell me this carefully and plainly, you thoughtful and observant people: why do the days that leave us never come back to us in the way they once did?",
        "Was there someone who quietly stole them away while nobody was paying attention, and if so, who was that person and where did they disappear to afterward?",
        "If they really ran away on their own, then when exactly did they decide to leave, and why did they go without saying a single word to the rest of us?",
        "I do not know how many days have been handed to me, but I can clearly feel that, little by little, the things I once thought I held tightly are slipping away.",
        "When I count in silence, more than eight thousand days have already slipped past me, and yet they feel as though they vanished almost without warning.",
        "They pass through my hands like a drop of water resting on the sharp point of a needle, visible for a moment and then gone before I can hold on to it.",
        "So I can only ask, again and again, whether there is still any honest and gentle way to live through these days without letting them vanish too quickly.",
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a focused overlong-line delivery smoke on quality mode.")
    ap.add_argument("--base-url", default="http://127.0.0.1:5175")
    ap.add_argument("--media-file", default="outputs/uploads/S1_clean_20s.mp4")
    ap.add_argument("--source-media", default="outputs/uploads/S1_clean.mp4")
    ap.add_argument("--clip-duration-s", type=int, default=20)
    ap.add_argument("--timeout-s", type=int, default=1800)
    ap.add_argument("--poll-interval-s", type=float, default=3.0)
    ap.add_argument("--report-dir", default="automation/reports")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    media_path = repo_root / str(args.media_file)
    if not media_path.exists():
        source_media = repo_root / str(args.source_media)
        if not source_media.exists():
            raise SystemExit(f"source media not found: {source_media}")
        media_path = _ensure_short_clip(source_media, clip_duration_s=args.clip_duration_s, out_dir=source_media.parent)

    _wait_for_backend_ready(args.base_url, timeout_s=min(args.timeout_s, 180))
    uploaded_path = _upload_media(args.base_url, media_path, timeout_s=min(args.timeout_s, 300))
    _log(f"[upload] uploaded_path={uploaded_path}")

    params = {
        "review_enabled": True,
        "stop_after": "zh_polish",
        "zh_phrase_candidate_max_lines": 4,
        "zh_phrase_chunk_lines": 2,
    }
    code, _, obj = _http_json(
        "POST",
        _join_url(args.base_url, "/api/tasks/start"),
        {
            "video": uploaded_path,
            "mode": "quality",
            "preset": "quality",
            "params": params,
        },
        timeout_s=min(args.timeout_s, 300),
    )
    if code != 200 or not isinstance(obj, dict):
        raise SystemExit(f"start task failed: http={code}, payload={obj!r}")
    task_id = str(obj.get("task_id") or "")
    if not task_id:
        raise SystemExit(f"missing task_id: {obj!r}")
    _log(f"[task] task_id={task_id}")

    paused = _poll_status(args.base_url, task_id, timeout_s=args.timeout_s, interval_s=args.poll_interval_s)
    paused_state = str(paused.get("state") or "")
    if paused_state not in {"paused", "completed"}:
        raise SystemExit(f"expected paused/completed at zh_polish, got {paused_state}")

    task_dir = repo_root / "outputs" / task_id
    chs_path = task_dir / "chs.srt"
    if not chs_path.exists():
        raise SystemExit(f"missing chs.srt: {chs_path}")
    chs_blocks = _parse_srt_blocks(chs_path.read_text(encoding="utf-8", errors="ignore"))
    if not chs_blocks:
        raise SystemExit("failed to parse chs.srt blocks")

    eng_review = _render_srt(chs_blocks, _default_overlong_lines())
    (task_dir / "eng.srt").write_text(eng_review, encoding="utf-8")
    (task_dir / "eng.review.srt").write_text(eng_review, encoding="utf-8")
    code_put, _, obj_put = _http_json(
        "PUT",
        _join_url(args.base_url, f"/api/tasks/{urllib.parse.quote(task_id)}/review/eng_srt"),
        {"content": eng_review},
        timeout_s=min(args.timeout_s, 120),
    )
    if code_put != 200:
        raise SystemExit(f"upload eng review failed: http={code_put}, payload={obj_put!r}")

    code_run, _, obj_run = _http_json(
        "POST",
        _join_url(args.base_url, f"/api/tasks/{urllib.parse.quote(task_id)}/review/run"),
        {"lang": "eng"},
        timeout_s=min(args.timeout_s, 120),
    )
    if code_run != 200 or not isinstance(obj_run, dict):
        raise SystemExit(f"review/run eng failed: http={code_run}, payload={obj_run!r}")
    resumed_task_id = str(obj_run.get("task_id") or task_id)
    _log(f"[resume] task_id={resumed_task_id}")

    final_status = _poll_status(args.base_url, resumed_task_id, timeout_s=args.timeout_s, interval_s=args.poll_interval_s)
    if str(final_status.get("state") or "") != "completed":
        raise SystemExit(f"overlong smoke did not complete: {final_status!r}")

    code_qr, _, qr = _http_json(
        "GET",
        _join_url(args.base_url, f"/api/tasks/{urllib.parse.quote(resumed_task_id)}/quality_report?regen=1"),
        None,
        timeout_s=min(args.timeout_s, 120),
    )
    if code_qr != 200 or not isinstance(qr, dict):
        raise SystemExit(f"quality_report failed: http={code_qr}, payload={qr!r}")

    task_dir = repo_root / "outputs" / resumed_task_id
    output_en = task_dir / "output_en.mp4"
    output_en_sub = task_dir / "output_en_sub.mp4"
    display_srt = task_dir / "display.srt"
    if not output_en.exists() or not output_en_sub.exists():
        raise SystemExit(f"missing deliverables in {task_dir}")

    report = {
        "task_id": resumed_task_id,
        "task_dir": str(task_dir),
        "quality_gate_passed": bool(qr.get("passed") is True),
        "quality_report": qr,
        "output_en": _ffprobe_media(output_en),
        "output_en_sub": _ffprobe_media(output_en_sub),
        "display_max_chars_per_line": _max_srt_line_chars(display_srt),
        "eng_review_max_chars_per_line": _max_srt_line_chars(task_dir / "eng.review.srt"),
    }

    report_dir = repo_root / str(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"overlong_delivery_smoke_{_now_stamp()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _log(f"[report] {report_path}")
    _log(
        "[summary] "
        f"passed={report['quality_gate_passed']} "
        f"display_max_chars={report['display_max_chars_per_line']} "
        f"eng_review_max_chars={report['eng_review_max_chars_per_line']} "
        f"output_en.duration_s={report['output_en']['duration_s']:.3f} "
        f"output_en_sub.duration_s={report['output_en_sub']['duration_s']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
