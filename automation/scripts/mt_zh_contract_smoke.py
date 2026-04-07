#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _log(msg: str) -> None:
    print(msg, flush=True)


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
            data = resp.read() or b""
            return code, hdrs, data
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
    deadline = time.time() + max(5, int(timeout_s or 120))
    ok_streak = 0
    last_err = ""
    while time.time() < deadline:
        try:
            code, _, payload = _http_json("GET", _join_url(base_url, "/api/health"), None, timeout_s=5)
            if code == 200 and isinstance(payload, dict):
                ok_streak += 1
                if ok_streak >= 2:
                    return
            else:
                ok_streak = 0
                last_err = f"http={code}, payload={payload!r}"
        except Exception as exc:
            ok_streak = 0
            last_err = str(exc)
        time.sleep(1.5)
    raise RuntimeError(f"backend not ready within {timeout_s}s: {last_err or 'unknown error'}")


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ensure_short_clip(src_path: Path, *, clip_duration_s: int, clip_start_s: int, out_dir: Path) -> Path:
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
        str(max(0, int(clip_start_s))),
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
    body, boundary = _multipart_file(
        "file",
        media_path.name,
        media_path.read_bytes(),
        content_type="video/mp4",
    )
    code, _, payload = _http_request(
        "POST",
        _join_url(base, "/api/upload"),
        body=body,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        timeout_s=timeout_s,
    )
    if code != 200:
        raise RuntimeError(f"upload failed: http {code}, payload={payload[:200]!r}")
    try:
        obj = json.loads(payload.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise RuntimeError(f"upload response parse failed: {exc}") from exc
    uploaded_path = str((obj or {}).get("path") or "")
    if not uploaded_path:
        raise RuntimeError(f"upload missing path: {obj!r}")
    return uploaded_path


def _download_task_file(base: str, task_id: str, rel_path: str, *, timeout_s: int) -> bytes:
    qp = urllib.parse.quote(rel_path, safe="")
    url = _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/download?path={qp}")
    code, _, data = _http_request("GET", url, None, headers=None, timeout_s=timeout_s)
    if code != 200:
        raise RuntimeError(f"download failed: http {code}, path={rel_path}")
    return data or b""


def _poll_status(base: str, task_id: str, *, timeout_s: int, interval_s: float) -> Dict[str, Any]:
    deadline = time.time() + float(timeout_s)
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
    raise RuntimeError(f"poll timeout, last={last}")


def _start_task(base: str, uploaded_path: str, *, params: Dict[str, Any], timeout_s: int) -> str:
    payload = {
        "video": uploaded_path,
        "mode": "quality",
        "preset": "quality",
        "params": params,
    }
    code, _, obj = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=timeout_s)
    if code != 200 or not isinstance(obj, dict):
        raise RuntimeError(f"start task failed: http {code}, payload={obj!r}")
    task_id = str(obj.get("task_id") or "")
    if not task_id:
        raise RuntimeError(f"missing task_id: {obj!r}")
    return task_id


def _cancel_task(base: str, task_id: str) -> None:
    try:
        _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/cancel"), {}, timeout_s=20)
    except Exception:
        pass


def _load_metrics(base: str, task_id: str, *, timeout_s: int) -> Dict[str, Any]:
    raw = _download_task_file(base, task_id, "llm_contract_metrics.json", timeout_s=timeout_s)
    obj = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    if not isinstance(obj, dict):
        raise RuntimeError("llm_contract_metrics.json is not a dict")
    return obj


def _summarize_bucket(metrics: Dict[str, Any], stage: str) -> Dict[str, int]:
    stats = (((metrics or {}).get("stats") or {}).get(stage) if isinstance(metrics, dict) else None) or {}
    if not isinstance(stats, dict):
        return {}
    out: Dict[str, int] = {}
    for key in (
        "requests",
        "contract_retry",
        "adaptive_splits",
        "fallback_legacy_format",
        "contract_invalid",
        "success_chunks",
    ):
        try:
            out[key] = int(stats.get(key, 0) or 0)
        except Exception:
            out[key] = 0
    return out


def _write_report(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _wait_for_ollama_model_ready(endpoint: str, model: str, *, timeout_s: int) -> None:
    base = str(endpoint or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    deadline = time.time() + max(30, int(timeout_s or 600))
    last_err = ""
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            code_ps, _, payload_ps = _http_json("GET", _join_url(base, "/api/ps"), None, timeout_s=15)
            if code_ps == 200 and isinstance(payload_ps, dict):
                models = payload_ps.get("models") or []
                if isinstance(models, list):
                    for item in models:
                        if isinstance(item, dict) and str(item.get("model") or item.get("name") or "") == str(model or ""):
                            return
            code, _, payload = _http_json(
                "POST",
                _join_url(base, "/api/generate"),
                {
                    "model": str(model or ""),
                    "prompt": "Reply with OK only.",
                    "stream": False,
                    "keep_alive": -1,
                    "options": {"num_predict": 8},
                },
                timeout_s=min(240, max(90, int(timeout_s or 600) // 4)),
            )
            if code == 200 and isinstance(payload, dict):
                text = str(payload.get("response") or "").strip()
                if text:
                    return
                last_err = f"http=200 empty_response payload={payload!r}"
            else:
                last_err = f"http={code} payload={payload!r}"
        except Exception as exc:
            last_err = str(exc)
        _log(f"[warmup] attempt={attempt} not ready yet: {last_err}")
        time.sleep(min(20.0, 4.0 + attempt * 2.0))
    raise RuntimeError(f"ollama model not ready within {timeout_s}s: {last_err}")


def _run_direct_mode(args: argparse.Namespace) -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from pipelines.quality_pipeline_impl import (  # type: ignore
        Segment,
        _contract_stats_snapshot,
        _optimize_zh_lines_with_risk_llm_adaptive,
        translate_segments_llm,
    )

    _wait_for_ollama_model_ready(
        str(args.llm_endpoint),
        str(args.llm_model),
        timeout_s=max(600, int(args.warm_timeout_s or 600)),
    )

    zh_lines = [
        "此刻怂哥想抽根华子。",
        "车上那个人一直盯着包里的东西。",
    ]
    segments = [Segment(start=float(i), end=float(i) + 2.0, text=line) for i, line in enumerate(zh_lines)]
    t0 = time.time()
    zh_items = [(i + 1, line) for i, line in enumerate(zh_lines)]
    zh_out = _optimize_zh_lines_with_risk_llm_adaptive(
        endpoint=str(args.llm_endpoint),
        model=str(args.llm_model),
        api_key=str(args.llm_api_key or ""),
        items=zh_items,
        request_timeout_s=max(420, int(args.timeout_s or 420)),
        request_retries=1,
        label="direct_zh_opt",
    )
    seg_en = translate_segments_llm(
        segments,
        endpoint=str(args.llm_endpoint),
        model=str(args.llm_model),
        api_key=str(args.llm_api_key or ""),
        chunk_size=1,
        context_window=0,
        style_hint="concise natural subtitle English",
        max_words_per_line=16,
        prompt_mode="short",
        prompt_profile="",
        two_pass_enable=False,
        long_fallback_enable=False,
        long_examples_enable=False,
        selfcheck_enable=False,
        selfcheck_max_lines=0,
        selfcheck_max_ratio=0.0,
        mt_reasoning_effort="none",
        request_timeout_s=max(420, int(args.timeout_s or 420)),
        request_retries=1,
    )
    elapsed_s = round(time.time() - t0, 1)
    return {
        "mode": "direct",
        "elapsed_s": elapsed_s,
        "zh_opt_items": [
            {
                "idx": int(k),
                "base": str(v.get("base") or ""),
                "opt": str(v.get("opt") or ""),
                "risk": str(v.get("risk") or ""),
                "changed": bool(v.get("changed")),
            }
            for k, v in sorted((zh_out or {}).items())
        ],
        "mt_lines": [str(getattr(seg, "translation", "") or "") for seg in seg_en],
        "metrics": {
            "version": 1,
            "stats": _contract_stats_snapshot(),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fast smoke for MT/zh_polish structured-output stability.")
    ap.add_argument("--base-url", default="http://127.0.0.1:5175")
    ap.add_argument("--mode", choices=["direct", "api"], default="direct", help="direct=call pipeline functions on text samples; api=run short backend tasks")
    ap.add_argument("--llm-endpoint", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--llm-model", default="qwen3.5:9b")
    ap.add_argument("--llm-api-key", default="")
    ap.add_argument("--media-file", default="", help="Local media file path. If empty, prefer test_media/S1_clean_20s.mp4 then S1_clean.mp4")
    ap.add_argument("--make-short-clip", action="store_true", help="Create and use a short clip from --media-file before upload")
    ap.add_argument("--clip-duration-s", type=int, default=20)
    ap.add_argument("--clip-start-s", type=int, default=10)
    ap.add_argument("--timeout-s", type=int, default=420)
    ap.add_argument("--warm-timeout-s", type=int, default=600)
    ap.add_argument("--poll-timeout-s", type=int, default=2400)
    ap.add_argument("--poll-interval-s", type=float, default=2.0)
    ap.add_argument("--report-dir", default=os.path.join("automation", "reports"))
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    stamp = _now_stamp()
    report_path = Path(args.report_dir) / f"mt_zh_contract_smoke_{stamp}.json"

    if args.mode == "direct":
        report = {
            "time": stamp,
            "mode": "direct",
            "llm_endpoint": str(args.llm_endpoint),
            "llm_model": str(args.llm_model),
        }
        try:
            report["result"] = _run_direct_mode(args)
            report["passed"] = True
            _write_report(report_path, report)
            _log(f"[done] report={report_path}")
            return 0
        except Exception as exc:
            report["passed"] = False
            report["error"] = str(exc)
            _write_report(report_path, report)
            _log(f"[done] report={report_path}")
            return 2

    base = str(args.base_url or "").rstrip("/")
    _wait_for_backend_ready(base, timeout_s=max(30, int(args.timeout_s or 120)))

    media_path = Path(str(args.media_file or "")).expanduser()
    if not str(media_path):
        media_path = repo_root / "test_media" / "S1_clean_20s.mp4"
        if not media_path.exists():
            media_path = repo_root / "test_media" / "S1_clean.mp4"
    if not media_path.exists():
        raise SystemExit(f"media file not found: {media_path}")

    if bool(args.make_short_clip):
        media_path = _ensure_short_clip(
            media_path,
            clip_duration_s=int(args.clip_duration_s or 20),
            clip_start_s=int(args.clip_start_s or 10),
            out_dir=repo_root / "test_media",
        )

    _log(f"[media] using {media_path}")
    uploaded_path = _upload_media(base, media_path, timeout_s=max(60, int(args.timeout_s or 120)))
    _log(f"[media] uploaded_path={uploaded_path}")

    results: List[Dict[str, Any]] = []
    scenarios = [
        (
            "zh_polish",
            {
                "review_enabled": True,
                "stop_after": "zh_polish",
                "zh_phrase_candidate_max_lines": 4,
                "zh_phrase_chunk_lines": 2,
                "zh_opt_request_timeout_s": 300,
                "zh_opt_request_retries": 3,
            },
        ),
        (
            "mt",
            {
                "review_enabled": False,
                "stop_after": "mt",
                "zh_phrase_candidate_max_lines": 4,
                "zh_phrase_chunk_lines": 2,
                "zh_opt_request_timeout_s": 300,
                "zh_opt_request_retries": 3,
                "mt_request_timeout_s": 600,
                "mt_request_retries": 3,
                "llm_chunk_size": 4,
            },
        ),
    ]

    exit_code = 0
    for name, params in scenarios:
        task_id = ""
        t0 = time.time()
        try:
            _log(f"[run] start {name}")
            task_id = _start_task(base, uploaded_path, params=params, timeout_s=max(120, int(args.timeout_s or 120)))
            status = _poll_status(
                base,
                task_id,
                timeout_s=max(300, int(args.poll_timeout_s or 2400)),
                interval_s=max(0.5, float(args.poll_interval_s or 2.0)),
            )
            state = str(status.get("state") or "")
            metrics = _load_metrics(base, task_id, timeout_s=max(30, int(args.timeout_s or 120)))
            item = {
                "name": name,
                "task_id": task_id,
                "state": state,
                "stage_name": status.get("stage_name"),
                "elapsed_s": round(time.time() - t0, 1),
                "metrics": metrics,
                "zh_opt": _summarize_bucket(metrics, "zh_opt"),
                "mt": _summarize_bucket(metrics, "mt"),
            }
            if name == "zh_polish":
                try:
                    item["chs_llm_json_bytes"] = len(_download_task_file(base, task_id, "chs.llm.json", timeout_s=max(30, int(args.timeout_s or 120))))
                except Exception as exc:
                    item["chs_llm_json_error"] = str(exc)
            if name == "mt":
                try:
                    item["eng_srt_bytes"] = len(_download_task_file(base, task_id, "eng.srt", timeout_s=max(30, int(args.timeout_s or 120))))
                except Exception as exc:
                    item["eng_srt_error"] = str(exc)
            results.append(item)
            if state not in {"paused", "completed"}:
                exit_code = 2
        except Exception as exc:
            exit_code = 2
            results.append(
                {
                    "name": name,
                    "task_id": task_id,
                    "error": str(exc),
                    "elapsed_s": round(time.time() - t0, 1),
                }
            )
        finally:
            if task_id:
                _cancel_task(base, task_id)

    report = {
        "time": stamp,
        "base_url": base,
        "media_file": str(media_path),
        "uploaded_path": uploaded_path,
        "results": results,
        "passed": exit_code == 0,
    }
    _write_report(report_path, report)
    _log(f"[done] report={report_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
