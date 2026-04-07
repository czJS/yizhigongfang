#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _read_bytes(path: str, max_bytes: int = 1024 * 1024 * 1024) -> bytes:
    st = os.stat(path)
    if st.st_size > max_bytes:
        raise RuntimeError(f"file too large: {st.st_size} bytes > {max_bytes} bytes: {path}")
    with open(path, "rb") as f:
        return f.read()


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
        # fall back to text
        return code, hdrs, data.decode("utf-8", errors="replace")


def _multipart_file(field: str, filename: str, content: bytes, content_type: str = "application/octet-stream") -> Tuple[bytes, str]:
    boundary = "----ygfBoundary" + str(int(time.time() * 1000))
    crlf = b"\r\n"
    parts = []
    parts.append(b"--" + boundary.encode("ascii"))
    parts.append(
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'.encode("utf-8")
    )
    parts.append(f"Content-Type: {content_type}".encode("utf-8"))
    parts.append(b"")
    parts.append(content)
    parts.append(b"--" + boundary.encode("ascii") + b"--")
    parts.append(b"")
    body = crlf.join(parts)
    return body, boundary


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    meta: Optional[Dict[str, Any]] = None


class Reporter:
    def __init__(self) -> None:
        self.items: List[CheckResult] = []

    def add(self, name: str, ok: bool, detail: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        self.items.append(CheckResult(name=name, ok=ok, detail=detail, meta=meta))

    def summary(self) -> Dict[str, Any]:
        ok_n = sum(1 for x in self.items if x.ok)
        total = len(self.items)
        return {"passed": ok_n == total, "ok": ok_n, "total": total}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _write_text(path: str, s: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)


def _auth_gate_cmd(repo_root: Path, mode: str, base_url: str) -> List[str]:
    cmd = [sys.executable, str((repo_root / "automation" / "scripts" / "auth_api_regress.py").resolve())]
    normalized = str(mode or "").strip() or "dev-smoke"
    if normalized not in {"dev-smoke", "regression-gate", "release-gate"}:
        normalized = "dev-smoke"
    cmd.append(f"--{normalized}")
    if str(base_url or "").strip():
        cmd.extend(["--base-url", str(base_url).strip()])
    return cmd


def _safe_json_dumps(x: Any) -> str:
    try:
        return json.dumps(x, ensure_ascii=False, indent=2)
    except Exception:
        return json.dumps({"_repr": repr(x)}, ensure_ascii=False, indent=2)


def _load_gate_config(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"gate config not found: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"gate config must be a json object: {path}")
    return data


def _wait_for_backend_ready(base_url: str, *, timeout_s: int = 120) -> None:
    """
    Wait for /api/health to become stable.
    """
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Quality-mode related automation checks (excluding Win packaging)")
    ap.add_argument("--base-url", default="http://127.0.0.1:5175", help="Backend base URL, default: http://127.0.0.1:5175")
    ap.add_argument("--media-file", default="", help="Local media file path used for /api/upload + /api/video/* checks")
    ap.add_argument("--timeout-s", type=int, default=180, help="Per-request timeout seconds")
    ap.add_argument("--poll-timeout-s", type=int, default=7200, help="Task polling timeout seconds (when running review flow)")
    ap.add_argument("--poll-interval-s", type=float, default=2.0, help="Polling interval seconds")
    ap.add_argument("--report-dir", default=os.path.join("automation", "reports"), help="Report output directory")
    ap.add_argument("--skip-task", action="store_true", help="Skip starting/running tasks (only run stateless API checks)")
    ap.add_argument("--skip-cleanup", action="store_true", help="Skip /cleanup checks")
    ap.add_argument("--skip-cancel", action="store_true", help="Skip /cancel checks")
    ap.add_argument("--run-resume", action="store_true", help="Run /resume check after task completed (may take time)")
    ap.add_argument("--run-compileall", action="store_true", help="Run B-1 python -m compileall (recommended)")
    ap.add_argument("--run-verify-models", action="store_true", help="Run B-4 verify_assets_models.py (recommended)")
    ap.add_argument("--run-frontend-regress", action="store_true", help="Run B-0 npm run regress in apps/desktop (optional)")
    ap.add_argument("--run-auth-gate", action="store_true", help="Run login/auth/license regression gate")
    ap.add_argument("--run-barrier", action="store_true", help="Run B-7 multi-task barrier check (may take time)")
    ap.add_argument("--run-reextract", action="store_true", help="Run reextract_zh_phrases (optional; can be slow)")
    ap.add_argument(
        "--run-review-gate",
        action="store_true",
        help="Run B-6 real zh_gate flow (review_enabled=true -> paused -> save review -> continue)",
    )
    ap.add_argument("--run-serial", action="store_true", help="Run B-21 global serial-queue check (best-effort)")
    ap.add_argument("--run-review-run", action="store_true", help="Run B-15 POST /review/run and poll to completion (expensive; requires MT+TTS)")
    ap.add_argument("--run-deliverables", action="store_true", help="Run deliverables/quality_report/ffprobe checks (requires completed task; expensive)")
    ap.add_argument("--dev-smoke", action="store_true", help="Load lightweight development smoke profile from automation/configs/quality_dev_smoke.json")
    ap.add_argument("--regression-gate", action="store_true", help="Load daily regression profile from automation/configs/quality_regression_gate.json")
    ap.add_argument("--release-gate", action="store_true", help="Load canonical release gate profile from automation/configs/quality_release_gate.json")
    ap.add_argument("--gate-config", default="", help="Load a JSON flag profile (e.g. automation/configs/quality_release_gate.json)")
    ap.add_argument(
        "--run-zh-opt",
        action="store_true",
        help="Run zh_polish LLM rewrite artifact check (expensive; requires review_enabled=true and local LLM)",
    )
    ap.add_argument("--auth-gate-mode", default="dev-smoke", help="Auth gate mode: dev-smoke / regression-gate / release-gate")
    ap.add_argument("--auth-base-url", default="", help="Optional live auth service base URL for auth gate")
    args = ap.parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    gate_config_path = str(args.gate_config or "")
    preset_flags = [bool(args.dev_smoke), bool(args.regression_gate), bool(args.release_gate)]
    if sum(1 for x in preset_flags if x) > 1:
        raise SystemExit("Only one of --dev-smoke / --regression-gate / --release-gate may be used at a time.")
    if not gate_config_path:
        if args.dev_smoke:
            gate_config_path = str(repo_root / "automation" / "configs" / "quality_dev_smoke.json")
        elif args.regression_gate:
            gate_config_path = str(repo_root / "automation" / "configs" / "quality_regression_gate.json")
        elif args.release_gate:
            gate_config_path = str(repo_root / "automation" / "configs" / "quality_release_gate.json")
    if gate_config_path:
        gate_cfg = _load_gate_config(gate_config_path)
        for key, value in gate_cfg.items():
            if hasattr(args, key):
                setattr(args, key, value)

    stamp = _now_stamp()
    _ensure_dir(args.report_dir)
    md_path = os.path.join(args.report_dir, f"quality_api_regress_{stamp}.md")
    json_path = os.path.join(args.report_dir, f"quality_api_regress_{stamp}.json")

    rep = Reporter()
    base = args.base_url.rstrip("/")
    _log(f"[run] report_md={md_path}")
    _log(f"[run] base_url={base}")
    _log(f"[run] media_file={args.media_file or '(none)'}")
    if gate_config_path:
        _log(f"[run] gate_config={gate_config_path}")
    try:
        _log("[preflight] waiting for backend readiness")
        _wait_for_backend_ready(base, timeout_s=max(20, int(args.timeout_s or 60)))
        rep.add("A-0 backend ready", True, detail="health endpoint stable")
    except Exception as e:
        rep.add("A-0 backend ready", False, detail=str(e))
        payload_out = {
            "time": stamp,
            "base_url": base,
            "args": vars(args),
            "summary": rep.summary(),
            "items": [x.__dict__ for x in rep.items],
        }
        _write_text(json_path, _safe_json_dumps(payload_out))
        _write_text(md_path, _render_markdown_report(payload_out))
        return 2

    def ok_http(name: str, code: int, want: int = 200, detail: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        rep.add(name, code == want, detail=(detail or f"http {code}, want {want}"), meta=meta)

    def run_cmd(name: str, cmd: List[str], cwd: Optional[str] = None, timeout_s: int = 1800) -> Tuple[bool, str]:
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_s,
            )
            out = (proc.stdout or "").strip()
            ok = proc.returncode == 0
            rep.add(name, ok, detail=f"exit={proc.returncode}", meta={"cmd": cmd, "cwd": cwd or "", "output": out[-8000:]})
            return ok, out
        except FileNotFoundError as e:
            rep.add(name, True, detail=f"skipped (missing executable): {e}")
            return True, ""
        except subprocess.TimeoutExpired:
            rep.add(name, False, detail=f"timeout after {timeout_s}s", meta={"cmd": cmd, "cwd": cwd or ""})
            return False, ""
        except Exception as e:
            rep.add(name, False, detail=str(e), meta={"cmd": cmd, "cwd": cwd or ""})
            return False, ""

    def download_task_meta(task_id: str) -> Dict[str, Any]:
        qp = urllib.parse.quote("task_meta.json", safe="")
        url = _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/download?path={qp}")
        code, _, data = _http_request("GET", url, None, headers=None, timeout_s=max(10, int(args.timeout_s or 60)))
        if code != 200:
            raise RuntimeError(f"download task_meta.json failed: http {code}")
        try:
            obj = json.loads((data or b"{}").decode("utf-8", errors="replace") or "{}")
        except Exception as exc:
            raise RuntimeError(f"task_meta.json parse failed: {exc}")
        if not isinstance(obj, dict):
            raise RuntimeError("task_meta.json is not a dict")
        return obj

    def download_task_file(task_id: str, rel_path: str) -> bytes:
        qp = urllib.parse.quote(rel_path, safe="")
        url = _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/download?path={qp}")
        code, _, data = _http_request("GET", url, None, headers=None, timeout_s=max(10, int(args.timeout_s or 60)))
        if code != 200:
            raise RuntimeError(f"download failed: http {code}, path={rel_path}")
        return data or b""

    def ffprobe_media(path: str) -> Dict[str, Any]:
        """
        Return a minimal parsed ffprobe json:
        - duration_s (float)
        - has_audio (bool)
        - has_video (bool)
        """
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "format=duration:stream=codec_type",
            path,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
        out = (proc.stdout or "").strip()
        if proc.returncode != 0:
            raise RuntimeError(f"ffprobe failed: exit={proc.returncode}, out={out[-500:]}")
        try:
            j = json.loads(out or "{}")
        except Exception as exc:
            raise RuntimeError(f"ffprobe json parse failed: {exc}")
        duration_s = 0.0
        try:
            duration_s = float(((j.get("format") or {}) if isinstance(j, dict) else {}).get("duration") or 0.0)
        except Exception:
            duration_s = 0.0
        streams = j.get("streams") if isinstance(j, dict) else None
        has_audio = False
        has_video = False
        if isinstance(streams, list):
            for s in streams:
                if not isinstance(s, dict):
                    continue
                ct = str(s.get("codec_type") or "").strip().lower()
                if ct == "audio":
                    has_audio = True
                if ct == "video":
                    has_video = True
        return {"duration_s": duration_s, "has_audio": has_audio, "has_video": has_video}

    # ------------------------
    # B-1: python compileall (optional but recommended)
    # ------------------------
    if args.run_compileall:
        _log("[B-1] compileall...")
        run_cmd("B-1 python -m compileall", [sys.executable, "-m", "compileall", "-q", "pipelines", "apps/backend/backend", "apps/worker_quality"], cwd=None, timeout_s=1200)
    else:
        rep.add("B-1 python -m compileall", True, detail="skipped (use --run-compileall)")

    # ------------------------
    # B-4: verify models directory (optional but recommended)
    # ------------------------
    if args.run_verify_models:
        _log("[B-4] verify_assets_models.py...")
        run_cmd("B-4 verify_assets_models.py", [sys.executable, "pipelines/tools/verify_assets_models.py"], cwd=None, timeout_s=300)
    else:
        rep.add("B-4 verify_assets_models.py", True, detail="skipped (use --run-verify-models)")

    # ------------------------
    # B-0: frontend regression (optional)
    # ------------------------
    if args.run_frontend_regress:
        _log("[B-0] npm run regress...")
        # Heuristic: if node_modules missing, regression will likely fail; mark skipped unless user prepared deps.
        nm = os.path.join("apps", "desktop", "node_modules")
        if not os.path.isdir(nm):
            rep.add("B-0 npm run regress", True, detail="skipped (apps/desktop/node_modules missing; run npm i first)")
        else:
            run_cmd("B-0 npm run regress", ["npm", "run", "regress"], cwd=os.path.join("apps", "desktop"), timeout_s=1800)
    else:
        rep.add("B-0 npm run regress", True, detail="skipped (use --run-frontend-regress)")

    if args.run_auth_gate:
        _log("[B-0A] auth_api_regress.py...")
        run_cmd(
            f"B-0A auth gate ({args.auth_gate_mode})",
            _auth_gate_cmd(repo_root, str(args.auth_gate_mode), str(args.auth_base_url)),
            cwd=None,
            timeout_s=1200,
        )
    else:
        rep.add("B-0A auth gate", True, detail="skipped (use --run-auth-gate)")

    # ------------------------
    # B-9: page-dependent APIs
    # ------------------------
    try:
        _log("[B-9] GET /api/health")
        code, _, j = _http_json("GET", _join_url(base, "/api/health"), None, timeout_s=args.timeout_s)
        ok_http("B-9 /api/health", code, 200, meta={"resp": j})
    except Exception as e:
        rep.add("B-9 /api/health", False, detail=str(e))

    cfg: Any = None
    try:
        code, _, j = _http_json("GET", _join_url(base, "/api/config"), None, timeout_s=args.timeout_s)
        ok_http("B-9 /api/config", code, 200, meta={"has_available_modes_detail": isinstance(j, dict) and "available_modes_detail" in j})
        cfg = j
        if code == 200 and isinstance(j, dict):
            amd = j.get("available_modes_detail")
            if isinstance(amd, dict) and "quality" in amd and isinstance(amd.get("quality"), dict):
                q = amd.get("quality") or {}
                rep.add(
                    "B-9 /api/config quality availability",
                    True,
                    detail=f"available={q.get('available')}, reasons={len(q.get('reasons') or [])}",
                    meta={"quality": q},
                )
            else:
                rep.add("B-9 /api/config quality availability", False, detail="missing available_modes_detail.quality")
    except Exception as e:
        rep.add("B-9 /api/config", False, detail=str(e))

    try:
        code, _, j = _http_json("GET", _join_url(base, "/api/hardware"), None, timeout_s=args.timeout_s)
        ok_http("B-9 /api/hardware", code, 200, meta={"resp": j})
    except Exception as e:
        rep.add("B-9 /api/hardware", False, detail=str(e))

    try:
        code, _, j = _http_json("GET", _join_url(base, "/api/presets"), None, timeout_s=args.timeout_s)
        ok_http("B-9 /api/presets", code, 200, meta={"keys": list(j.keys()) if isinstance(j, dict) else None})
    except Exception as e:
        rep.add("B-9 /api/presets", False, detail=str(e))

    # Global ruleset (Rules Center depends on this)
    saved_global_ruleset: Optional[Dict[str, Any]] = None
    try:
        code, _, j = _http_json("GET", _join_url(base, "/api/rulesets/global"), None, timeout_s=args.timeout_s)
        ok_http("B-9 /api/rulesets/global get", code, 200)
        if code == 200 and isinstance(j, dict):
            saved_global_ruleset = j
            code2, _, _ = _http_json("PUT", _join_url(base, "/api/rulesets/global"), saved_global_ruleset, timeout_s=args.timeout_s)
            ok_http("B-9 /api/rulesets/global put (echo)", code2, 200)
        else:
            rep.add("B-9 /api/rulesets/global put (echo)", False, detail="skip: invalid global ruleset shape")
    except Exception as e:
        rep.add("B-9 /api/rulesets/global get/put", False, detail=str(e))

    # Negative-path: invalid mode should not silently downgrade.
    try:
        code, _, j = _http_json(
            "POST",
            _join_url(base, "/api/tasks/start"),
            {"video": "/__nonexistent__", "mode": "__invalid__", "params": {}},
            timeout_s=args.timeout_s,
        )
        # This can fail as "video not found" first. Accept either 400 with video-not-found or mode-not-available; we mainly assert "not 200".
        rep.add("B-22 start_task invalid mode (negative)", code != 200, detail=f"http {code}", meta={"resp": j})
    except Exception as e:
        rep.add("B-22 start_task invalid mode (negative)", False, detail=str(e))

    # ------------------------
    # B-10: templates CRUD
    # ------------------------
    tpl_id: Optional[str] = None
    try:
        payload = {"name": f"auto_tpl_{stamp}", "doc": {"version": 1, "asr_fixes": [], "en_fixes": [], "settings": {}}}
        code, _, j = _http_json("POST", _join_url(base, "/api/rulesets/templates"), payload, timeout_s=args.timeout_s)
        ok_http("B-10 create template", code, 200, meta={"resp": j})
        if isinstance(j, dict) and j.get("id"):
            tpl_id = str(j["id"])
    except Exception as e:
        rep.add("B-10 create template", False, detail=str(e))

    try:
        code, _, j = _http_json("GET", _join_url(base, "/api/rulesets/templates"), None, timeout_s=args.timeout_s)
        count = None
        if isinstance(j, dict) and isinstance(j.get("items"), list):
            count = len(j.get("items") or [])
        elif isinstance(j, list):
            count = len(j)
        ok_http("B-10 list templates", code, 200, meta={"count": count})
    except Exception as e:
        rep.add("B-10 list templates", False, detail=str(e))

    if tpl_id:
        try:
            code, _, j = _http_json("GET", _join_url(base, f"/api/rulesets/templates/{urllib.parse.quote(tpl_id)}"), None, timeout_s=args.timeout_s)
            ok_http("B-10 get template", code, 200, meta={"resp": j})
        except Exception as e:
            rep.add("B-10 get template", False, detail=str(e))

        try:
            code, _, j = _http_json(
                "PUT",
                _join_url(base, f"/api/rulesets/templates/{urllib.parse.quote(tpl_id)}"),
                {"name": f"auto_tpl_{stamp}_upd"},
                timeout_s=args.timeout_s,
            )
            ok_http("B-10 update template", code, 200, meta={"resp": j})
        except Exception as e:
            rep.add("B-10 update template", False, detail=str(e))

        try:
            code, _, _ = _http_json("DELETE", _join_url(base, f"/api/rulesets/templates/{urllib.parse.quote(tpl_id)}"), None, timeout_s=args.timeout_s)
            ok_http("B-10 delete template", code, 200)
        except Exception as e:
            rep.add("B-10 delete template", False, detail=str(e))

    # ------------------------
    # B-11: glossary get/put
    # ------------------------
    try:
        code, _, j = _http_json("GET", _join_url(base, "/api/glossary"), None, timeout_s=args.timeout_s)
        ok_http("B-11 /api/glossary get", code, 200)
        if code == 200 and isinstance(j, dict):
            items = j.get("items") if isinstance(j.get("items"), list) else []
            next_doc = {"items": (items[:0] + [{"src": "自动化测试-错字", "tgt": "自动化测试-正字"}])}
            code2, _, _ = _http_json("PUT", _join_url(base, "/api/glossary"), next_doc, timeout_s=args.timeout_s)
            ok_http("B-11 /api/glossary put", code2, 200)
        else:
            rep.add("B-11 /api/glossary put", False, detail="skip: glossary get not ok or invalid shape")
    except Exception as e:
        rep.add("B-11 /api/glossary get/put", False, detail=str(e))

    # ------------------------
    # B-12/B-13: upload + video helpers
    # ------------------------
    uploaded_path: str = ""
    if args.media_file:
        try:
            _log("[B-12] POST /api/upload")
            content = _read_bytes(args.media_file)
            body, boundary = _multipart_file(
                "file",
                os.path.basename(args.media_file),
                content,
                content_type="application/octet-stream",
            )
            code, _, data = _http_request(
                "POST",
                _join_url(base, "/api/upload"),
                body=body,
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
                timeout_s=args.timeout_s,
            )
            resp = json.loads(data.decode("utf-8", errors="replace")) if data else None
            ok_http("B-12 /api/upload", code, 200, meta={"resp": resp})
            if isinstance(resp, dict) and resp.get("path"):
                uploaded_path = str(resp["path"])
        except Exception as e:
            rep.add("B-12 /api/upload", False, detail=str(e))

        if uploaded_path:
            try:
                code, _, j = _http_json("POST", _join_url(base, "/api/video/probe"), {"path": uploaded_path}, timeout_s=args.timeout_s)
                ok_http("B-13 /api/video/probe", code, 200, meta={"resp": j})
            except Exception as e:
                rep.add("B-13 /api/video/probe", False, detail=str(e))

            try:
                code, hdrs, data = _http_request(
                    "POST",
                    _join_url(base, "/api/video/frame"),
                    body=json.dumps({"path": uploaded_path, "t": 0, "max_width": 960}).encode("utf-8"),
                    headers={"content-type": "application/json"},
                    timeout_s=args.timeout_s,
                )
                is_png = "image/png" in (hdrs.get("content-type", "") or "")
                ok = code == 200 and is_png and len(data) > 0
                rep.add("B-13 /api/video/frame", ok, detail=f"http {code}, content-type={hdrs.get('content-type','')}, bytes={len(data)}")
                if ok:
                    out_png = os.path.join(args.report_dir, f"preview_frame_{stamp}.png")
                    with open(out_png, "wb") as f:
                        f.write(data)
            except Exception as e:
                rep.add("B-13 /api/video/frame", False, detail=str(e))
    else:
        rep.add("B-12/B-13 upload+video", True, detail="skipped (no --media-file)")

    # ------------------------
    # B-23: Quality fixed strategies (backend normalization)
    # - UX toggles are removed from UI; backend always enables readability + tts_plan
    # - Word-trimming (tts_fit) must be disabled to prevent fact loss
    # ------------------------
    if uploaded_path:
        try:
            def _start_quality(params: Dict[str, Any]) -> str:
                payload = {"video": uploaded_path, "mode": "quality", "preset": "quality", "params": params}
                code, _, j = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=args.timeout_s)
                ok_http("B-23 start task (fixed strategies)", code, 200)
                tid = str(j.get("task_id") or "") if code == 200 and isinstance(j, dict) else ""
                if not tid:
                    raise RuntimeError(f"start task failed or missing task_id: http={code}, resp={j}")
                return tid

            def _cancel_best_effort(tid: str) -> None:
                try:
                    _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid)}/cancel"), {}, timeout_s=args.timeout_s)
                except Exception:
                    pass

            def _assert_fixed(meta: Dict[str, Any]) -> None:
                p = meta.get("params") if isinstance(meta, dict) else None
                if not isinstance(p, dict):
                    raise RuntimeError("task_meta.params missing or not a dict")
                # UX-only keys should NOT persist in task params (backend should normalize away).
                if "ux_subtitle_readable" in p or "ux_tts_natural" in p or "mt_topic" in p:
                    raise RuntimeError("ux_* or mt_topic leaked into task_meta.params")
                # Denoise is always on for quality.
                if bool(p.get("denoise")) is not True:
                    raise RuntimeError(f"denoise expected true, got {p.get('denoise')!r}")

                want_readable = True
                readable_keys = [
                    "subtitle_postprocess_enable",
                    "subtitle_wrap_enable",
                    "display_srt_enable",
                    "display_use_for_embed",
                    "display_merge_enable",
                    "display_split_enable",
                ]
                for k in readable_keys:
                    if bool(p.get(k)) != want_readable:
                        raise RuntimeError(f"{k} expected {want_readable}, got {p.get(k)!r}")
                if bool(p.get("tts_plan_enable")) is not True:
                    raise RuntimeError(f"tts_plan_enable expected true, got {p.get('tts_plan_enable')!r}")
                if bool(p.get("tts_fit_enable")) is not False:
                    raise RuntimeError(f"tts_fit_enable expected false, got {p.get('tts_fit_enable')!r}")
                if bool(p.get("mt_long_fallback_enable")) is True:
                    raise RuntimeError("mt_long_fallback_enable expected false")
                if bool(p.get("mt_compact_enable")) is True:
                    raise RuntimeError("mt_compact_enable expected false")
                if bool(p.get("mt_two_pass_disable")) is not True:
                    raise RuntimeError(f"mt_two_pass_disable expected true, got {p.get('mt_two_pass_disable')!r}")
                # High-risk line self-check is default-on in quality pipeline.
                # Backend may or may not pass explicit flags; assert we are NOT explicitly disabling it.
                cmd = meta.get("cmd") if isinstance(meta, dict) else None
                if isinstance(cmd, list) and any(str(x) == "--llm-selfcheck-disable" for x in cmd):
                    raise RuntimeError("unexpected --llm-selfcheck-disable in task_meta.cmd")
                if isinstance(cmd, list):
                    joined = [str(x) for x in cmd]
                    for flag in ("--zh-phrase-enable", "--zh-post-polish-enable", "--zh-gate-min-high-risk", "--mt-request-timeout-s", "--mt-two-pass-disable"):
                        if flag not in joined:
                            raise RuntimeError(f"missing {flag} in task_meta.cmd")
                    for forbidden in ("--mt-long-fallback-enable", "--mt-compact-enable"):
                        if forbidden in joined:
                            raise RuntimeError(f"unexpected legacy MT flag in task_meta.cmd: {forbidden}")

            # Start tasks and assert mapping via task_meta.json (download).
            # Use stop_after=zh_polish so tasks are safe to cancel if picked up quickly.
            tid_legacy = _start_quality({"ux_subtitle_readable": False, "ux_tts_natural": False, "review_enabled": False, "stop_after": "zh_polish"})
            meta_legacy = download_task_meta(tid_legacy)
            _assert_fixed(meta_legacy)
            _cancel_best_effort(tid_legacy)

            tid_default = _start_quality({"review_enabled": False, "stop_after": "zh_polish"})
            meta_default = download_task_meta(tid_default)
            _assert_fixed(meta_default)
            _cancel_best_effort(tid_default)

            rep.add("B-23 quality fixed strategies (no tts_fit)", True, detail="ok")
        except Exception as e:
            rep.add("B-23 quality fixed strategies (no tts_fit)", False, detail=str(e))
    else:
        rep.add("B-23 quality fixed strategies (no tts_fit)", True, detail="skipped (need --media-file upload)")

    # ------------------------
    # B-23b: Quality MT stability params are passed through to pipeline cmd
    # ------------------------
    if uploaded_path:
        try:
            payload = {
                "video": uploaded_path,
                "mode": "quality",
                "preset": "quality",
                "params": {
                    "review_enabled": False,
                    "stop_after": "zh_polish",
                    "mt_request_timeout_s": 91,
                    "mt_request_retries": 3,
                    "llm_selfcheck_max_lines": 3,
                    "llm_selfcheck_max_ratio": 0.05,
                },
            }
            code, _, j = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=args.timeout_s)
            ok_http("B-23b start task (mt stability params)", code, 200, meta={"resp": j})
            tid = str(j.get("task_id") or "") if code == 200 and isinstance(j, dict) else ""
            if not tid:
                raise RuntimeError("missing task id")
            meta = download_task_meta(tid)
            params = meta.get("params") if isinstance(meta, dict) else None
            cmd = meta.get("cmd") if isinstance(meta, dict) else None
            if not isinstance(params, dict) or not isinstance(cmd, list):
                raise RuntimeError("task_meta missing params/cmd")
            for key, want in {
                "mt_request_timeout_s": 91,
                "mt_request_retries": 3,
                "llm_selfcheck_max_lines": 3,
            }.items():
                if int(params.get(key) or 0) != want:
                    raise RuntimeError(f"{key} expected {want}, got {params.get(key)!r}")
            got_ratio = float(params.get("llm_selfcheck_max_ratio") or 0.0)
            if abs(got_ratio - 0.05) > 1e-9:
                raise RuntimeError(f"llm_selfcheck_max_ratio expected 0.05, got {got_ratio!r}")
            joined = [str(x) for x in cmd]
            for pair in (
                ("--mt-request-timeout-s", "91"),
                ("--mt-request-retries", "3"),
                ("--llm-selfcheck-max-lines", "3"),
                ("--llm-selfcheck-max-ratio", "0.05"),
            ):
                try:
                    idx = joined.index(pair[0])
                except ValueError as exc:
                    raise RuntimeError(f"missing {pair[0]} in task_meta.cmd") from exc
                if idx + 1 >= len(joined) or joined[idx + 1] != pair[1]:
                    raise RuntimeError(f"{pair[0]} expected value {pair[1]}, got {joined[idx + 1] if idx + 1 < len(joined) else None!r}")
            try:
                _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid)}/cancel"), {}, timeout_s=args.timeout_s)
            except Exception:
                pass
            rep.add("B-23b quality MT stability params passthrough", True, detail="ok")
        except Exception as e:
            rep.add("B-23b quality MT stability params passthrough", False, detail=str(e))
    else:
        rep.add("B-23b quality MT stability params passthrough", True, detail="skipped (need --media-file upload)")

    # ------------------------
    # B-24: Ruleset template + override merge order (effective rules)
    # ------------------------
    if uploaded_path:
        tpl_id = ""
        try:
            # 1) create a template with deterministic rules
            tpl_doc = {
                "version": 1,
                "asr_fixes": [{"id": "a0001", "src": "自动化-错字", "tgt": "自动化-正字", "note": "tpl", "scope": "global"}],
                "en_fixes": [{"id": "e0001", "src": "AUTOTEST_EN", "tgt": "AUTOTEST_EN_FIXED", "note": "tpl", "scope": "global"}],
                "settings": {},
            }
            code, _, j = _http_json(
                "POST",
                _join_url(base, "/api/rulesets/templates"),
                {"name": f"auto_tpl_rules_{stamp}", "doc": tpl_doc},
                timeout_s=args.timeout_s,
            )
            ok_http("B-24 create ruleset template (with doc)", code, 200, meta={"resp": j})
            tpl_id = str(j.get("id") or "") if code == 200 and isinstance(j, dict) else ""
            if not tpl_id:
                raise RuntimeError("missing template id")

            # 2) start a task with template + override (override should win when src conflicts)
            override_doc = {
                "version": 1,
                "asr_fixes": [{"id": "a0002", "src": "自动化-错字", "tgt": "自动化-正字-OVERRIDE", "note": "ovr", "scope": "global"}],
                "en_fixes": [{"id": "e0002", "src": "AUTOTEST_EN", "tgt": "AUTOTEST_EN_FIXED_OVR", "note": "ovr", "scope": "global"}],
                "settings": {},
            }
            payload = {
                "video": uploaded_path,
                "mode": "quality",
                "preset": "quality",
                "params": {
                    "ruleset_template_id": tpl_id,
                    "ruleset_override": override_doc,
                    "review_enabled": False,
                    "stop_after": "zh_polish",
                },
            }
            code2, _, j2 = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=args.timeout_s)
            ok_http("B-24 start task (template+override)", code2, 200, meta={"resp": j2})
            tid = str(j2.get("task_id") or "") if code2 == 200 and isinstance(j2, dict) else ""
            if not tid:
                raise RuntimeError("missing task id")

            meta = download_task_meta(tid)
            rs_eff = meta.get("ruleset_effective") if isinstance(meta, dict) else None
            if not isinstance(rs_eff, dict):
                raise RuntimeError("task_meta.ruleset_effective missing or not a dict")
            asr = rs_eff.get("asr_fixes") if isinstance(rs_eff.get("asr_fixes"), list) else []
            en = rs_eff.get("en_fixes") if isinstance(rs_eff.get("en_fixes"), list) else []
            # Find by src
            def _find(items, src):
                for it in items or []:
                    if isinstance(it, dict) and str(it.get("src") or "").strip() == src:
                        return it
                return None

            it_asr = _find(asr, "自动化-错字")
            it_en = _find(en, "AUTOTEST_EN")
            if not it_asr or str(it_asr.get("tgt") or "").strip() != "自动化-正字-OVERRIDE":
                raise RuntimeError(f"ruleset_effective.asr_fixes merge failed: {it_asr}")
            if not it_en or str(it_en.get("tgt") or "").strip() != "AUTOTEST_EN_FIXED_OVR":
                raise RuntimeError(f"ruleset_effective.en_fixes merge failed: {it_en}")

            derived = meta.get("ruleset_derived") if isinstance(meta.get("ruleset_derived"), dict) else {}
            if not isinstance(derived, dict) or not derived.get("ruleset_path") or not derived.get("glossary_path"):
                raise RuntimeError("ruleset_derived missing expected paths")

            # best-effort cancel to avoid queue pollution (backend supports queued cancel)
            try:
                _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid)}/cancel"), {}, timeout_s=args.timeout_s)
            except Exception:
                pass

            rep.add("B-24 ruleset merge order (template+override)", True, detail="ok")
        except Exception as e:
            rep.add("B-24 ruleset merge order (template+override)", False, detail=str(e))
        finally:
            if tpl_id:
                try:
                    _http_json("DELETE", _join_url(base, f"/api/rulesets/templates/{urllib.parse.quote(tpl_id)}"), None, timeout_s=args.timeout_s)
                except Exception:
                    pass
    else:
        rep.add("B-24 ruleset merge order (template+override)", True, detail="skipped (need --media-file upload)")

    # ------------------------
    # B-25: Subtitle erase/place params persist (wizard Step2 contract)
    # ------------------------
    if uploaded_path:
        try:
            # Case A: erase enabled, only provide erase_* (place_* should still be allowed to be omitted)
            p1 = {
                "review_enabled": False,
                "stop_after": "zh_polish",
                "erase_subtitle_enable": True,
                "erase_subtitle_method": "delogo",
                "erase_subtitle_coord_mode": "ratio",
                "erase_subtitle_x": 0.12,
                "erase_subtitle_y": 0.73,
                "erase_subtitle_w": 0.76,
                "erase_subtitle_h": 0.12,
            }
            code, _, j = _http_json("POST", _join_url(base, "/api/tasks/start"), {"video": uploaded_path, "mode": "quality", "preset": "quality", "params": p1}, timeout_s=args.timeout_s)
            ok_http("B-25 start task (erase only)", code, 200, meta={"resp": j})
            tid1 = str(j.get("task_id") or "") if code == 200 and isinstance(j, dict) else ""
            if not tid1:
                raise RuntimeError("missing task id")
            meta1 = download_task_meta(tid1)
            params1 = meta1.get("params") if isinstance(meta1.get("params"), dict) else None
            if not isinstance(params1, dict):
                raise RuntimeError("task_meta.params missing")
            for k, v in p1.items():
                if k in ("review_enabled", "stop_after"):
                    continue
                got = params1.get(k)
                # floats may be stringified/rounded; compare numerically for ratios
                if isinstance(v, float):
                    try:
                        gv = float(got)
                    except Exception:
                        raise RuntimeError(f"{k} expected float-like, got {got!r}")
                    if abs(gv - float(v)) > 1e-3:
                        raise RuntimeError(f"{k} expected {v}, got {got!r}")
                else:
                    if got != v:
                        raise RuntimeError(f"{k} expected {v!r}, got {got!r}")

            # Case B: erase disabled, explicit place_* should persist
            p2 = {
                "review_enabled": False,
                "stop_after": "zh_polish",
                "erase_subtitle_enable": False,
                "sub_place_enable": True,
                "sub_place_coord_mode": "ratio",
                "sub_place_x": 0.05,
                "sub_place_y": 0.80,
                "sub_place_w": 0.90,
                "sub_place_h": 0.18,
            }
            code2, _, j2 = _http_json("POST", _join_url(base, "/api/tasks/start"), {"video": uploaded_path, "mode": "quality", "preset": "quality", "params": p2}, timeout_s=args.timeout_s)
            ok_http("B-25 start task (place only)", code2, 200, meta={"resp": j2})
            tid2 = str(j2.get("task_id") or "") if code2 == 200 and isinstance(j2, dict) else ""
            if not tid2:
                raise RuntimeError("missing task id")
            meta2 = download_task_meta(tid2)
            params2 = meta2.get("params") if isinstance(meta2.get("params"), dict) else None
            if not isinstance(params2, dict):
                raise RuntimeError("task_meta.params missing")
            for k, v in p2.items():
                if k in ("review_enabled", "stop_after"):
                    continue
                got = params2.get(k)
                if isinstance(v, float):
                    try:
                        gv = float(got)
                    except Exception:
                        raise RuntimeError(f"{k} expected float-like, got {got!r}")
                    if abs(gv - float(v)) > 1e-3:
                        raise RuntimeError(f"{k} expected {v}, got {got!r}")
                else:
                    if got != v:
                        raise RuntimeError(f"{k} expected {v!r}, got {got!r}")

            # cleanup: cancel queued best-effort
            try:
                _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid1)}/cancel"), {}, timeout_s=args.timeout_s)
                _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid2)}/cancel"), {}, timeout_s=args.timeout_s)
            except Exception:
                pass
            rep.add("B-25 subtitle erase/place params persist", True, detail="ok")
        except Exception as e:
            rep.add("B-25 subtitle erase/place params persist", False, detail=str(e))
    else:
        rep.add("B-25 subtitle erase/place params persist", True, detail="skipped (need --media-file upload)")

    # ------------------------
    # B-29a: Valid upload endpoints accept well-formed json
    # ------------------------
    try:
        good = json.dumps({"items": [{"src": "自动化测试-上传", "tgt": "自动化测试-术语"}]}, ensure_ascii=False).encode("utf-8")
        body, boundary = _multipart_file("file", f"good_glossary_{stamp}.json", good, content_type="application/json")
        code, _, data = _http_request(
            "POST",
            _join_url(base, "/api/glossary/upload"),
            body=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            timeout_s=args.timeout_s,
        )
        rep.add("B-29 glossary upload valid json", code == 200, detail=f"http {code}; resp={(data or b'')[:200]!r}")
    except Exception as e:
        rep.add("B-29 glossary upload valid json", False, detail=str(e))

    try:
        good = json.dumps({"version": 1, "asr_fixes": [], "en_fixes": [], "settings": {}}, ensure_ascii=False).encode("utf-8")
        body, boundary = _multipart_file("file", f"good_ruleset_{stamp}.json", good, content_type="application/json")
        code, _, data = _http_request(
            "POST",
            _join_url(base, "/api/rulesets/upload"),
            body=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            timeout_s=args.timeout_s,
        )
        rep.add("B-29 ruleset upload valid json", code == 200, detail=f"http {code}; resp={(data or b'')[:200]!r}")
    except Exception as e:
        rep.add("B-29 ruleset upload valid json", False, detail=str(e))

    try:
        good = json.dumps({"version": 1, "asr_fixes": [], "en_fixes": [], "settings": {}}, ensure_ascii=False).encode("utf-8")
        body, boundary = _multipart_file("file", f"good_template_{stamp}.json", good, content_type="application/json")
        code, _, data = _http_request(
            "POST",
            _join_url(base, "/api/rulesets/templates/upload"),
            body=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            timeout_s=args.timeout_s,
        )
        rep.add("B-29 template upload valid json", code == 200, detail=f"http {code}; resp={(data or b'')[:200]!r}")
    except Exception as e:
        rep.add("B-29 template upload valid json", False, detail=str(e))

    # ------------------------
    # B-26: Ruleset upload rejects invalid json (negative)
    # ------------------------
    try:
        bad = b'{"version":1, "asr_fixes": ['
        body, boundary = _multipart_file("file", f"bad_ruleset_{stamp}.json", bad, content_type="application/json")
        code, _, data = _http_request(
            "POST",
            _join_url(base, "/api/rulesets/upload"),
            body=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            timeout_s=args.timeout_s,
        )
        # Want a friendly 400, not 500.
        rep.add("B-26 ruleset upload invalid json (negative)", code == 400, detail=f"http {code}, want 400; resp={(data or b'')[:200]!r}")
    except Exception as e:
        rep.add("B-26 ruleset upload invalid json (negative)", False, detail=str(e))

    # ------------------------
    # B-28: Quality mode strips lite/online-only params (contract)
    # ------------------------
    if uploaded_path:
        try:
            lite_only = {
                "whispercpp_threads": 3,
                "whispercpp_model": "assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin",
                "whispercpp_bin": "bin/whisper-cli",
                "vad_enable": True,
                "vad_threshold": 0.3,
                "vad_min_dur": 0.2,
                "bilingual_srt": True,
                "dedupe": True,
                "asr_model": "whatever",
                "mt_model": "whatever",
                "mt_device": "cpu",
                "tts_backend": "piper",
                "piper_model": "assets/models/lite_tts_piper/en_US-amy-low.onnx",
                "piper_bin": "piper",
                # also try to override quality defaults
                "denoise": False,
            }
            payload = {
                "video": uploaded_path,
                "mode": "quality",
                "preset": "quality",
                "params": {"review_enabled": False, "stop_after": "zh_polish", **lite_only},
            }
            code, _, j = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=args.timeout_s)
            ok_http("B-28 start task (quality strips lite params)", code, 200, meta={"resp": j})
            tid = str(j.get("task_id") or "") if code == 200 and isinstance(j, dict) else ""
            if not tid:
                raise RuntimeError("missing task id")
            meta = download_task_meta(tid)
            p = meta.get("params") if isinstance(meta.get("params"), dict) else None
            if not isinstance(p, dict):
                raise RuntimeError("task_meta.params missing")
            stripped = [
                "whispercpp_threads",
                "whispercpp_model",
                "whispercpp_bin",
                "vad_enable",
                "vad_threshold",
                "vad_min_dur",
                "bilingual_srt",
                "dedupe",
                "asr_model",
                "mt_model",
                "mt_device",
                "tts_backend",
                "piper_model",
                "piper_bin",
            ]
            leaked = [k for k in stripped if k in p]
            if leaked:
                raise RuntimeError(f"quality params leaked (should be stripped): {leaked}")
            # Quality should enforce denoise=true (even if user tried to disable).
            if bool(p.get("denoise")) is not True:
                raise RuntimeError(f"denoise expected true in quality, got {p.get('denoise')!r}")

            try:
                _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid)}/cancel"), {}, timeout_s=args.timeout_s)
            except Exception:
                pass
            rep.add("B-28 quality strips lite params (contract)", True, detail="ok")
        except Exception as e:
            rep.add("B-28 quality strips lite params (contract)", False, detail=str(e))
    else:
        rep.add("B-28 quality strips lite params (contract)", True, detail="skipped (need --media-file upload)")

    # ------------------------
    # B-29: Templates/glossary upload rejects invalid json (negative)
    # ------------------------
    try:
        bad = b'{"version":1, "items": ['
        body, boundary = _multipart_file("file", f"bad_glossary_{stamp}.json", bad, content_type="application/json")
        code, _, data = _http_request(
            "POST",
            _join_url(base, "/api/glossary/upload"),
            body=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            timeout_s=args.timeout_s,
        )
        rep.add("B-29 glossary upload invalid json (negative)", code == 400, detail=f"http {code}, want 400; resp={(data or b'')[:200]!r}")
    except Exception as e:
        rep.add("B-29 glossary upload invalid json (negative)", False, detail=str(e))

    try:
        bad = b'{"doc":'
        body, boundary = _multipart_file("file", f"bad_template_{stamp}.json", bad, content_type="application/json")
        code, _, data = _http_request(
            "POST",
            _join_url(base, "/api/rulesets/templates/upload"),
            body=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            timeout_s=args.timeout_s,
        )
        rep.add(
            "B-29 template upload invalid json (negative)",
            code == 400,
            detail=f"http {code}, want 400; resp={(data or b'')[:200]!r}",
        )
    except Exception as e:
        rep.add("B-29 template upload invalid json (negative)", False, detail=str(e))

    # ------------------------
    # B-30: Derived rules files are downloadable + parseable (contract)
    # ------------------------
    if uploaded_path:
        try:
            payload = {
                "video": uploaded_path,
                "mode": "quality",
                "preset": "quality",
                "params": {"review_enabled": False, "stop_after": "zh_polish"},
            }
            code, _, j = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=args.timeout_s)
            ok_http("B-30 start task (derived rules files)", code, 200, meta={"resp": j})
            tid = str(j.get("task_id") or "") if code == 200 and isinstance(j, dict) else ""
            if not tid:
                raise RuntimeError("missing task id")

            # Download and validate derived files under .ygf_rules/
            eff = json.loads(download_task_file(tid, ".ygf_rules/ruleset_effective.json").decode("utf-8", errors="replace") or "{}")
            if not isinstance(eff, dict) or "asr_fixes" not in eff or "en_fixes" not in eff:
                raise RuntimeError("ruleset_effective.json unexpected shape")
            g = json.loads(download_task_file(tid, ".ygf_rules/glossary.json").decode("utf-8", errors="replace") or "{}")
            if not isinstance(g, dict) or not isinstance(g.get("items"), list):
                raise RuntimeError("glossary.json unexpected shape")
            d = json.loads(download_task_file(tid, ".ygf_rules/asr_dict.json").decode("utf-8", errors="replace") or "{}")
            if not isinstance(d, dict):
                raise RuntimeError("asr_dict.json unexpected shape")
            en_d = json.loads(download_task_file(tid, ".ygf_rules/en_dict.json").decode("utf-8", errors="replace") or "{}")
            if not isinstance(en_d, dict):
                raise RuntimeError("en_dict.json unexpected shape")

            try:
                _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid)}/cancel"), {}, timeout_s=args.timeout_s)
            except Exception:
                pass
            rep.add("B-30 derived rules files downloadable", True, detail="ok")
        except Exception as e:
            rep.add("B-30 derived rules files downloadable", False, detail=str(e))
    else:
        rep.add("B-30 derived rules files downloadable", True, detail="skipped (need --media-file upload)")

    # ------------------------
    # Optional: task-bound checks (B-14 ~ B-18)
    # ------------------------
    task_id: str = ""
    work_dir: str = ""
    artifacts: List[Dict[str, Any]] = []

    def poll_status(tid: str) -> Dict[str, Any]:
        deadline = time.time() + float(args.poll_timeout_s)
        last = None
        while time.time() < deadline:
            code, _, st = _http_json("GET", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid)}/status"), None, timeout_s=args.timeout_s)
            if code != 200 or not isinstance(st, dict):
                last = {"http": code, "resp": st}
                time.sleep(float(args.poll_interval_s))
                continue
            last = st
            state = str(st.get("state") or "")
            if state in ("completed", "failed", "paused", "cancelled"):
                return st
            time.sleep(float(args.poll_interval_s))
        raise RuntimeError(f"poll timeout, last={last}")

    def _gate_flow_params(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = dict(extra or {})
        review_enabled = bool(params.get("review_enabled"))
        stop_after_zh = str(params.get("stop_after") or "").strip() == "zh_polish"
        is_gate_flow = review_enabled or stop_after_zh
        if not is_gate_flow:
            return params
        if stop_after_zh and not review_enabled:
            # Auxiliary stop_after=zh_polish checks only need to prove the stage
            # executes and pause/resume contracts remain valid. Keep them lighter
            # than the real review_enabled flow so weak hardware doesn't burn the
            # same LLM budget twice on the same sample video.
            params.setdefault("zh_phrase_candidate_max_lines", 4)
            params.setdefault("zh_phrase_chunk_lines", 2)
            params.setdefault("zh_opt_request_timeout_s", 300)
            params.setdefault("zh_opt_request_retries", 3)
            return params
        # Real review gate flow: stay close to product defaults.
        params.setdefault("zh_phrase_candidate_max_lines", 6)
        params.setdefault("zh_phrase_chunk_lines", 4)
        params.setdefault("zh_opt_request_timeout_s", 360)
        params.setdefault("zh_opt_request_retries", 3)
        return params

    def assert_has_artifacts(name: str, arts: List[Dict[str, Any]], required_names: List[str]) -> None:
        present = {str(a.get("name") or "") for a in arts if isinstance(a, dict)}
        missing = [x for x in required_names if x not in present]
        rep.add(name, len(missing) == 0, detail=("ok" if not missing else f"missing: {missing}"), meta={"present_count": len(present)})

    def assert_contract_metrics(name: str, tid: str, *, require_mt: bool = False, require_zh: bool = False) -> None:
        try:
            raw = download_task_file(tid, "llm_contract_metrics.json").decode("utf-8", errors="replace")
            obj = json.loads(raw or "{}")
            stats = obj.get("stats") if isinstance(obj, dict) else None
            if not isinstance(stats, dict):
                raise RuntimeError("metrics.stats missing")
            zh_stats = stats.get("zh_opt") if isinstance(stats.get("zh_opt"), dict) else {}
            mt_stats = stats.get("mt") if isinstance(stats.get("mt"), dict) else {}
            if require_zh and int((zh_stats or {}).get("requests", 0) or 0) <= 0:
                raise RuntimeError("zh_opt requests missing")
            if require_mt and int((mt_stats or {}).get("requests", 0) or 0) <= 0:
                raise RuntimeError("mt requests missing")
            rep.add(
                name,
                True,
                detail=(
                    f"zh.requests={int((zh_stats or {}).get('requests', 0) or 0)}, "
                    f"mt.requests={int((mt_stats or {}).get('requests', 0) or 0)}"
                ),
            )
        except Exception as e:
            rep.add(name, False, detail=str(e))

    def assert_zh_polish_artifacts(name: str, tid: str, *, expect_pause: bool) -> None:
        try:
            raw = download_task_file(tid, "chs.suspects.json").decode("utf-8", errors="replace")
            obj = json.loads(raw or "{}")
            meta = obj.get("meta") if isinstance(obj, dict) else None
            if not isinstance(meta, dict):
                raise RuntimeError("suspects.meta missing")
            if meta.get("zh_polish_enabled") is not True:
                raise RuntimeError(f"zh_polish_enabled={meta.get('zh_polish_enabled')!r}")
            if meta.get("review_gate_enabled") is not True:
                raise RuntimeError(f"review_gate_enabled={meta.get('review_gate_enabled')!r}")
            gate = meta.get("zh_gate_summary") if isinstance(meta.get("zh_gate_summary"), dict) else {}
            if expect_pause and gate.get("should_pause") is not True:
                raise RuntimeError(f"expected should_pause=true, got {gate}")
            rep.add(
                name,
                True,
                detail=(
                    f"suspects={len((obj.get('items') or []) if isinstance(obj, dict) else [])}, "
                    f"should_pause={gate.get('should_pause')}"
                ),
            )
        except Exception as e:
            rep.add(name, False, detail=str(e))

    if not args.skip_task:
        if not uploaded_path:
            rep.add("B-5/B-14~B-18 task flow", False, detail="need --media-file to upload and get a backend-accessible path")
        else:
            if args.run_review_gate:
                try:
                    _log("[B-6] POST /api/tasks/start (quality, review_enabled=true)")
                    payload = {
                        "video": uploaded_path,
                        "mode": "quality",
                        "preset": "quality",
                        "params": _gate_flow_params({
                            "review_enabled": True,
                        }),
                    }
                    code_gate, _, j_gate = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=max(args.timeout_s, 120))
                    ok_http("B-6 start task (review_enabled=true)", code_gate, 200, meta={"resp": j_gate})
                    gate_tid = str(j_gate.get("task_id") or "") if code_gate == 200 and isinstance(j_gate, dict) else ""
                    if not gate_tid:
                        raise RuntimeError("missing task id")

                    st_gate = poll_status(gate_tid)
                    gate_state = str(st_gate.get("state") or "")
                    rep.add("B-6 wait paused/completed", gate_state in {"paused", "completed"}, detail=f"state={gate_state}, stage={st_gate.get('stage_name')}")

                    if gate_state == "paused":
                        assert_contract_metrics("B-6 llm_contract_metrics zh", gate_tid)
                        assert_zh_polish_artifacts("B-6 zh_polish artifacts", gate_tid, expect_pause=True)
                        code_chs, _, base_chs = _http_json(
                            "GET",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(gate_tid)}/review/chs_srt?which=base"),
                            None,
                            timeout_s=args.timeout_s,
                        )
                        ok_http("B-6 GET review/chs_srt base", code_chs, 200)
                        content_chs = base_chs.get("content") if isinstance(base_chs, dict) else ""
                        code_put, _, _ = _http_json(
                            "PUT",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(gate_tid)}/review/chs_srt"),
                            {"content": content_chs or ""},
                            timeout_s=args.timeout_s,
                        )
                        ok_http("B-6 PUT review/chs_srt", code_put, 200)
                        if args.run_review_run:
                            code_run, _, j_run = _http_json(
                                "POST",
                                _join_url(base, f"/api/tasks/{urllib.parse.quote(gate_tid)}/review/run"),
                                {"lang": "chs"},
                                timeout_s=args.timeout_s,
                            )
                            ok_http("B-6 POST review/run chs", code_run, 200, meta={"resp": j_run})
                            st_gate_done = poll_status(gate_tid)
                            rep.add("B-6 poll after review/run", str(st_gate_done.get("state")) == "completed", detail=f"state={st_gate_done.get('state')}")
                        else:
                            rep.add("B-6 POST review/run chs", True, detail="skipped (use --run-review-run)")
                            rep.add("B-6 poll after review/run", True, detail="skipped (use --run-review-run)")
                    else:
                        rep.add("B-6 POST review/run chs", True, detail="skipped (high-risk gate threshold not reached)")
                        rep.add("B-6 poll after review/run", True, detail="skipped (high-risk gate threshold not reached)")
                except Exception as e:
                    rep.add("B-6 review gate flow", False, detail=str(e))
            else:
                rep.add("B-6 review gate flow", True, detail="skipped (use --run-review-gate)")

            try:
                _log("[B-5] POST /api/tasks/start (quality, stop_after=zh_polish)")
                payload = {
                    "video": uploaded_path,
                    "mode": "quality",
                    "preset": "quality",
                    # Keep the baseline focused on pause/review contracts, not optional zh_post_polish runtime.
                    "params": _gate_flow_params({"review_enabled": False, "stop_after": "zh_polish"}),
                }
                code, _, j = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=args.timeout_s)
                ok_http("B-5 start task (quality, stop_after=zh_polish)", code, 200, meta={"resp": j})
                if code == 200:
                    task_id = str(j.get("task_id") or "") if isinstance(j, dict) else ""
                    if task_id:
                        _log(f"[B-5] task_id={task_id}")
            except Exception as e:
                rep.add("B-5 start task", False, detail=str(e))

            if task_id:
                try:
                    _log("[B-6/B-7] polling status...")
                    st = poll_status(task_id)
                    rep.add("B-6/B-7 wait paused/completed", True, detail=f"state={st.get('state')}, stage={st.get('stage_name')}")
                    work_dir = str(st.get("work_dir") or "")
                except Exception as e:
                    rep.add("B-6/B-7 wait paused/completed", False, detail=str(e))

            # B-21 serial queue check (best-effort): at most one task in running state at any moment.
            if args.run_serial:
                try:
                    tids: List[str] = []
                    for i in range(2):
                        payload = {
                            "video": uploaded_path,
                            "mode": "quality",
                            "preset": "quality",
                            "params": _gate_flow_params({"review_enabled": False, "stop_after": "zh_polish"}),
                        }
                        code, _, j = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=args.timeout_s)
                        ok_http(f"B-21 start serial task {i+1}", code, 200)
                        tid = str(j.get("task_id") or "") if code == 200 and isinstance(j, dict) else ""
                        if tid:
                            tids.append(tid)
                    if len(tids) < 2:
                        rep.add("B-21 serial queue", False, detail="failed to start 2 tasks")
                    else:
                        deadline = time.time() + min(120.0, float(args.poll_timeout_s))
                        any_overlap = False
                        samples = 0
                        # Bonus: queued tasks should have a non-empty log message (UX aid).
                        try:
                            code_q, _, st_q = _http_json("GET", _join_url(base, f"/api/tasks/{urllib.parse.quote(tids[-1])}/status"), None, timeout_s=args.timeout_s)
                            if code_q == 200 and isinstance(st_q, dict) and str(st_q.get("state") or "") == "queued":
                                code_l, _, lr_q = _http_json(
                                    "GET",
                                    _join_url(base, f"/api/tasks/{urllib.parse.quote(tids[-1])}/log?offset=0"),
                                    None,
                                    timeout_s=args.timeout_s,
                                )
                                ok_http("B-21 queued log http", code_l, 200)
                                if code_l == 200 and isinstance(lr_q, dict):
                                    c_q = str(lr_q.get("content") or "")
                                    rep.add("B-21 queued log has message", ("Queued" in c_q) or ("排队" in c_q), detail=c_q.strip()[:120])
                        except Exception:
                            pass
                        while time.time() < deadline:
                            states: List[str] = []
                            for tid in tids:
                                code, _, st = _http_json("GET", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid)}/status"), None, timeout_s=args.timeout_s)
                                if code != 200 or not isinstance(st, dict):
                                    states.append("unknown")
                                else:
                                    states.append(str(st.get("state") or ""))
                            samples += 1
                            if states.count("running") >= 2:
                                any_overlap = True
                                break
                            # stop early if both are paused/completed/failed/cancelled
                            if all(s in ("paused", "completed", "failed", "cancelled") for s in states):
                                break
                            time.sleep(max(0.6, float(args.poll_interval_s)))
                        rep.add("B-21 serial queue (no overlap running)", not any_overlap, detail=f"samples={samples}")
                    # Best-effort cleanup to avoid leaving extra paused tasks around.
                    try:
                        for tid in tids:
                            _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid)}/cancel"), {}, timeout_s=args.timeout_s)
                    except Exception:
                        pass
                except Exception as e:
                    rep.add("B-21 serial queue", False, detail=str(e))
            else:
                rep.add("B-21 serial queue", True, detail="skipped (use --run-serial)")

            if task_id:
                # B-15 review endpoints (chs)
                try:
                    code, _, base_chs = _http_json(
                        "GET",
                        _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/chs_srt?which=base"),
                        None,
                        timeout_s=args.timeout_s,
                    )
                    ok_http("B-15 GET review/chs_srt base", code, 200)
                    content = base_chs.get("content") if isinstance(base_chs, dict) else ""
                    code2, _, _ = _http_json(
                        "PUT",
                        _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/chs_srt"),
                        {"content": content or ""},
                        timeout_s=args.timeout_s,
                    )
                    ok_http("B-15 PUT review/chs_srt", code2, 200)
                    # upload_chs_srt (multipart)
                    body, boundary = _multipart_file("file", "chs.review.srt", (content or "").encode("utf-8"), content_type="application/x-subrip")
                    code3, _, data3 = _http_request(
                        "POST",
                        _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/upload_chs_srt"),
                        body=body,
                        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
                        timeout_s=args.timeout_s,
                    )
                    ok_http("B-15 POST review/upload_chs_srt", code3, 200, detail=f"http {code3}, bytes={len(data3)}")
                    # reextract phrases (optional; can be slow)
                    if args.run_reextract:
                        code4, _, _ = _http_json(
                            "POST",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/reextract_zh_phrases"),
                            {},
                            timeout_s=max(args.timeout_s, 12 * 60),
                        )
                        ok_http("B-15 POST review/reextract_zh_phrases", code4, 200, detail=f"http {code4}")
                    else:
                        rep.add("B-15 POST review/reextract_zh_phrases", True, detail="skipped (use --run-reextract)")
                    # diff
                    code5, _, diff = _http_json(
                        "GET",
                        _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/diff?lang=chs"),
                        None,
                        timeout_s=args.timeout_s,
                    )
                    diff_text = ""
                    if isinstance(diff, dict) and isinstance(diff.get("diff"), str):
                        diff_text = diff.get("diff") or ""
                    elif isinstance(diff, str):
                        diff_text = diff
                    rep.add(
                        "B-15 GET review/diff chs",
                        code5 == 200 and isinstance(diff_text, str),
                        detail=f"http {code5}, len={len(diff_text)}",
                    )
                    if args.run_review_run:
                        # run review (chs) => triggers MT/TTS/Mux/Embed, can be very slow depending on local LLM/TTS
                        code6, _, j6 = _http_json(
                            "POST",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/run"),
                            {"lang": "chs"},
                            timeout_s=args.timeout_s,
                        )
                        ok_http("B-15 POST review/run chs", code6, 200, meta={"resp": j6})
                        if code6 == 200 and isinstance(j6, dict) and j6.get("task_id"):
                            task_id2 = str(j6["task_id"])
                            st2 = poll_status(task_id2)
                            rep.add("B-15 poll after review/run", str(st2.get("state")) == "completed", detail=f"state={st2.get('state')}")
                            task_id = task_id2
                    else:
                        rep.add("B-15 POST review/run chs", True, detail="skipped (use --run-review-run)")
                except Exception as e:
                    rep.add("B-15 review endpoints (chs)", False, detail=str(e))

                # B-15 review endpoints (eng)
                try:
                    code_e1, _, base_eng = _http_json(
                        "GET",
                        _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/eng_srt?which=base"),
                        None,
                        timeout_s=args.timeout_s,
                    )
                    if code_e1 == 404:
                        rep.add("B-15 GET review/eng_srt base", True, detail="skipped (eng.srt not available yet; run with --run-review-run for full english review coverage)")
                        rep.add("B-15 PUT review/eng_srt", True, detail="skipped (eng.srt not available yet)")
                        rep.add("B-15 POST review/upload_eng_srt", True, detail="skipped (eng.srt not available yet)")
                        rep.add("B-15 GET review/diff eng", True, detail="skipped (eng.srt not available yet)")
                        rep.add("B-15 POST review/run eng", True, detail="skipped (eng.srt not available yet)")
                        rep.add("B-15 poll after review/run eng", True, detail="skipped (eng.srt not available yet)")
                    else:
                        ok_http("B-15 GET review/eng_srt base", code_e1, 200)
                        content_eng = base_eng.get("content") if isinstance(base_eng, dict) else ""
                        code_e2, _, _ = _http_json(
                            "PUT",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/eng_srt"),
                            {"content": content_eng or ""},
                            timeout_s=args.timeout_s,
                        )
                        ok_http("B-15 PUT review/eng_srt", code_e2, 200)
                        body_eng, boundary_eng = _multipart_file(
                            "file",
                            "eng.review.srt",
                            (content_eng or "").encode("utf-8"),
                            content_type="application/x-subrip",
                        )
                        code_e3, _, data_e3 = _http_request(
                            "POST",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/upload_eng_srt"),
                            body=body_eng,
                            headers={"content-type": f"multipart/form-data; boundary={boundary_eng}"},
                            timeout_s=args.timeout_s,
                        )
                        ok_http("B-15 POST review/upload_eng_srt", code_e3, 200, detail=f"http {code_e3}, bytes={len(data_e3)}")
                        code_e4, _, diff_eng = _http_json(
                            "GET",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/diff?lang=eng"),
                            None,
                            timeout_s=args.timeout_s,
                        )
                        diff_text_eng = ""
                        if isinstance(diff_eng, dict) and isinstance(diff_eng.get("diff"), str):
                            diff_text_eng = diff_eng.get("diff") or ""
                        elif isinstance(diff_eng, str):
                            diff_text_eng = diff_eng
                        rep.add(
                            "B-15 GET review/diff eng",
                            code_e4 == 200 and isinstance(diff_text_eng, str),
                            detail=f"http {code_e4}, len={len(diff_text_eng)}",
                        )
                        if args.run_review_run:
                            code_e5, _, j_e5 = _http_json(
                                "POST",
                                _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/run"),
                                {"lang": "eng"},
                                timeout_s=args.timeout_s,
                            )
                            ok_http("B-15 POST review/run eng", code_e5, 200, meta={"resp": j_e5})
                            if code_e5 == 200:
                                st_eng = poll_status(task_id)
                                rep.add("B-15 poll after review/run eng", str(st_eng.get("state")) == "completed", detail=f"state={st_eng.get('state')}")
                            else:
                                rep.add("B-15 poll after review/run eng", False, detail="review/run eng did not return 200")
                        else:
                            rep.add("B-15 POST review/run eng", True, detail="skipped (use --run-review-run)")
                            rep.add("B-15 poll after review/run eng", True, detail="skipped (use --run-review-run)")
                except Exception as e:
                    rep.add("B-15 review endpoints (eng)", False, detail=str(e))

                # artifacts + quality report
                try:
                    # Some artifacts (e.g. quality_report.json) may be written slightly after process exit.
                    # Retry briefly to avoid false negatives.
                    arts = None
                    code = 0
                    for _ in range(5):
                        code, _, arts = _http_json("GET", _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/artifacts"), None, timeout_s=args.timeout_s)
                        if code == 200 and isinstance(arts, dict) and isinstance(arts.get("files"), list):
                            names = {str(x.get("name") or "") for x in (arts.get("files") or []) if isinstance(x, dict)}
                            if "quality_report.json" in names:
                                break
                        time.sleep(0.6)
                    ok_http("B-5 GET artifacts", code, 200, meta={"shape": list(arts.keys()) if isinstance(arts, dict) else type(arts).__name__})
                    files = arts.get("files") if isinstance(arts, dict) else None
                    artifacts = files if isinstance(files, list) else []
                    if args.run_deliverables:
                        # B-5 deliverables presence check (document-defined "minimum deliverables")
                        assert_has_artifacts(
                            "B-5 deliverables present",
                            artifacts,
                            ["eng.srt", "output_en.mp4", "output_en_sub.mp4", "tts_full.wav", "audio.wav", "quality_report.json"],
                        )
                    else:
                        # For stop_after=zh_polish baseline, only assert early artifacts.
                        assert_has_artifacts("B-5 early artifacts present", artifacts, ["audio.wav", "chs.srt"])
                        rep.add("B-5 deliverables present", True, detail="skipped (use --run-deliverables)")
                except Exception as e:
                    rep.add("B-5 GET artifacts", False, detail=str(e))

                if task_id:
                    assert_contract_metrics(
                        "B-32 contract metrics present",
                        task_id,
                        require_mt=bool(args.run_deliverables),
                    )

                # ------------------------
                # B-31: zh_polish LLM rewrite artifacts (optional; expensive)
                # ------------------------
                if args.run_zh_opt:
                    if not uploaded_path:
                        rep.add("B-31 zh_opt artifacts", True, detail="skipped (need --media-file upload)")
                    else:
                        try:
                            _log("[B-31] POST /api/tasks/start (quality, review_enabled=true, stop_after=zh_polish)")
                            payload = {
                                "video": uploaded_path,
                                "mode": "quality",
                                "preset": "quality",
                                # Keep it light: limit candidate lines so it won't stall on CPU-only Ollama.
                                "params": {
                                    "review_enabled": True,
                                    "stop_after": "zh_polish",
                                    "zh_phrase_candidate_max_lines": 8,
                                    "zh_phrase_chunk_lines": 4,
                                    "zh_opt_request_timeout_s": 360,
                                    "zh_opt_request_retries": 3,
                                    "zh_phrase_max_total": 24,
                                    "zh_phrase_max_spans": 3,
                                },
                            }
                            code31, _, j31 = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=max(args.timeout_s, 120))
                            ok_http("B-31 start task (zh_opt)", code31, 200, meta={"resp": j31})
                            tid31 = str(j31.get("task_id") or "") if code31 == 200 and isinstance(j31, dict) else ""
                            if not tid31:
                                raise RuntimeError("missing task id")
                            st31 = poll_status(tid31)
                            rep.add("B-31 wait paused", str(st31.get("state")) == "paused", detail=f"state={st31.get('state')}, stage={st31.get('stage_name')}")

                            b_srt = download_task_file(tid31, "chs.llm.srt")
                            rep.add("B-31 download chs.llm.srt", len(b_srt or b"") > 0, detail=f"bytes={len(b_srt or b'')}")

                            obj31 = json.loads(download_task_file(tid31, "chs.llm.json").decode("utf-8", errors="replace") or "{}")
                            items31 = obj31.get("items") if isinstance(obj31, dict) else None
                            if not isinstance(items31, list) or not items31:
                                raise RuntimeError("chs.llm.json missing items")
                            # Only require "polished" when suspects exist (polish runs only on suspect lines).
                            try:
                                sus31 = json.loads(download_task_file(tid31, "chs.suspects.json").decode("utf-8", errors="replace") or "{}")
                            except Exception:
                                sus31 = {}
                            sus_items = (sus31.get("items") if isinstance(sus31, dict) else None) or []
                            sus_n = len(sus_items) if isinstance(sus_items, list) else 0
                            any_polished = any(bool((it or {}).get("polished")) for it in items31 if isinstance(it, dict))
                            if sus_n > 0:
                                rep.add("B-31 chs.llm.json has polished items", any_polished, detail=f"suspects={sus_n}, items={len(items31)}")
                            else:
                                rep.add("B-31 chs.llm.json has polished items", True, detail=f"skipped (suspects=0), items={len(items31)}")

                            try:
                                _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid31)}/cancel"), {}, timeout_s=args.timeout_s)
                            except Exception:
                                pass
                        except Exception as e:
                            rep.add("B-31 zh_opt artifacts", False, detail=str(e))
                else:
                    rep.add("B-31 zh_opt artifacts", True, detail="skipped (use --run-zh-opt)")

                if args.run_deliverables:
                    try:
                        code, _, qr = _http_json(
                            "GET",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/quality_report?regen=1"),
                            None,
                            timeout_s=args.timeout_s,
                        )
                        if code == 404:
                            time.sleep(0.8)
                            code, _, qr = _http_json(
                                "GET",
                                _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/quality_report?regen=1"),
                                None,
                                timeout_s=args.timeout_s,
                            )
                        ok_http("B-5 GET quality_report (regen=1)", code, 200, meta={"passed": (qr or {}).get("passed") if isinstance(qr, dict) else None})
                        if code == 200 and isinstance(qr, dict):
                            rep.add("B-5 quality gate passed", bool(qr.get("passed") is True), detail=f"passed={qr.get('passed')}")
                    except Exception as e:
                        rep.add("B-5 GET quality_report", False, detail=str(e))
                else:
                    rep.add("B-5 GET quality_report (regen=1)", True, detail="skipped (use --run-deliverables)")
                    rep.add("B-5 quality gate passed", True, detail="skipped (use --run-deliverables)")

                # B-8 apply review (quick redelivery) - best-effort
                if args.run_deliverables:
                    try:
                        code, _, j = _http_json(
                            "POST",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/review/apply"),
                            {"action": "mux_embed", "use": "base", "params": {}},
                            timeout_s=max(args.timeout_s, 60),
                        )
                        ok_http("B-8 POST review/apply mux_embed", code, 200, meta={"resp": j})
                    except Exception as e:
                        rep.add("B-8 POST review/apply mux_embed", False, detail=str(e))
                else:
                    rep.add("B-8 POST review/apply mux_embed", True, detail="skipped (use --run-deliverables)")

                # B-27 ffprobe deliverables (media usability): ensure mp4 has audio+video and duration>0
                if args.run_deliverables:
                    try:
                        # Refresh work_dir from latest status (task_id may change after review/run).
                        code_ws, _, st_ws = _http_json(
                            "GET",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/status"),
                            None,
                            timeout_s=args.timeout_s,
                        )
                        ok_http("B-27 GET status (for work_dir)", code_ws, 200)
                        wd = str(st_ws.get("work_dir") or "") if code_ws == 200 and isinstance(st_ws, dict) else ""
                        # Download to local temp files then ffprobe.
                        tmp_dir = os.path.join(args.report_dir, f"_tmp_media_{stamp}")
                        os.makedirs(tmp_dir, exist_ok=True)
                        checks = ["output_en.mp4", "output_en_sub.mp4"]
                        for name in checks:
                            data = download_task_file(task_id, name)
                            if not data:
                                raise RuntimeError(f"empty download: {name}")
                            local = os.path.join(tmp_dir, name)
                            with open(local, "wb") as f:
                                f.write(data)
                            info = ffprobe_media(local)
                            ok = bool(info.get("duration_s", 0.0) > 0.5 and info.get("has_audio") and info.get("has_video"))
                            rep.add(
                                f"B-27 ffprobe {name}",
                                ok,
                                detail=f"duration_s={info.get('duration_s')}, audio={info.get('has_audio')}, video={info.get('has_video')}, work_dir={wd}",
                            )
                    except FileNotFoundError as e:
                        rep.add("B-27 ffprobe deliverables", True, detail=f"skipped (missing ffprobe): {e}")
                    except Exception as e:
                        rep.add("B-27 ffprobe deliverables", False, detail=str(e))
                else:
                    rep.add("B-27 GET status (for work_dir)", True, detail="skipped (use --run-deliverables)")
                    rep.add("B-27 ffprobe deliverables", True, detail="skipped (use --run-deliverables)")

                # B-7 barrier (multi-task) - optional, may take time
                if args.run_barrier:
                    try:
                        tids: List[str] = []
                        for i in range(2):
                            payload = {
                                "video": uploaded_path,
                                "mode": "quality",
                                "preset": "quality",
                                "params": {"review_enabled": True, "stop_after": "zh_polish"},
                            }
                            code, _, j = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=args.timeout_s)
                            ok_http(f"B-7 start barrier task {i+1}", code, 200)
                            tid = str(j.get("task_id") or "") if code == 200 and isinstance(j, dict) else ""
                            if tid:
                                tids.append(tid)
                        ok_all = True
                        for tid in tids:
                            stx = poll_status(tid)
                            ok_one = str(stx.get("state")) == "paused"
                            rep.add(f"B-7 barrier paused {tid}", ok_one, detail=f"state={stx.get('state')}")
                            ok_all = ok_all and ok_one
                        rep.add("B-7 barrier summary", ok_all, detail=f"paused {sum(1 for _ in tids)}/{len(tids)}")
                    except Exception as e:
                        rep.add("B-7 barrier", False, detail=str(e))
                else:
                    rep.add("B-7 barrier", True, detail="skipped (use --run-barrier)")

                # B-20 resume (optional; may take time)
                if args.run_resume:
                    try:
                        # Resume requires the task to be NOT running.
                        # In practice, some flows (e.g. review/apply) may briefly flip the task back to running.
                        try:
                            deadline = time.time() + min(300.0, float(args.poll_timeout_s))
                            last = None
                            while time.time() < deadline:
                                cst, _, st0 = _http_json(
                                    "GET",
                                    _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/status"),
                                    None,
                                    timeout_s=args.timeout_s,
                                )
                                if cst != 200 or not isinstance(st0, dict):
                                    last = {"http": cst, "resp": st0}
                                    time.sleep(max(0.6, float(args.poll_interval_s)))
                                    continue
                                last = st0
                                s = str(st0.get("state") or "")
                                if s in ("completed", "failed", "cancelled"):
                                    break
                                # If paused here, don't try to auto-continue again; it should have been handled in B-15.
                                if s == "paused":
                                    break
                                time.sleep(max(0.6, float(args.poll_interval_s)))
                        except Exception:
                            pass

                        resume_from_stage = "mux"
                        try:
                            code_a, _, arts_resp = _http_json(
                                "GET",
                                _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/artifacts"),
                                None,
                                timeout_s=args.timeout_s,
                            )
                            files = arts_resp.get("files") if code_a == 200 and isinstance(arts_resp, dict) else []
                            present = {
                                str(it.get("name") or "")
                                for it in (files if isinstance(files, list) else [])
                                if isinstance(it, dict)
                            }
                            if "tts_full.wav" in present:
                                resume_from_stage = "mux"
                            elif "eng.srt" in present:
                                resume_from_stage = "tts"
                            else:
                                resume_from_stage = "mt"
                        except Exception:
                            resume_from_stage = "mt"

                        code, _, j = _http_json(
                            "POST",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/resume"),
                            {"resume_from": resume_from_stage, "params": {}, "preset": "quality"},
                            timeout_s=args.timeout_s,
                        )
                        ok_http(f"B-20 POST resume ({resume_from_stage})", code, 200, meta={"resp": j})
                        rid = str(j.get("task_id") or "") if code == 200 and isinstance(j, dict) else ""
                        if rid:
                            st3 = poll_status(rid)
                            rep.add(
                                "B-20 poll after resume",
                                str(st3.get("state")) == "completed",
                                detail=f"resume_from={resume_from_stage}, state={st3.get('state')}",
                            )
                        else:
                            rep.add("B-20 poll after resume", False, detail="no resumed task_id")
                    except Exception as e:
                        rep.add("B-20 resume", False, detail=str(e))
                else:
                    rep.add("B-20 resume", True, detail="skipped (use --run-resume)")

                # B-19 log
                try:
                    code, _, lr = _http_json("GET", _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/log?offset=0"), None, timeout_s=args.timeout_s)
                    ok_http("B-19 GET log offset=0", code, 200)
                    if code == 200 and isinstance(lr, dict):
                        content = lr.get("content")
                        nxt = lr.get("next_offset")
                        rep.add("B-19 log shape", isinstance(content, str) and isinstance(nxt, int), detail=f"next_offset={nxt}")
                        # Tail mode should return absolute next_offset (log length), not a negative value.
                        code2, _, lr2 = _http_json(
                            "GET",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/log?offset=-200"),
                            None,
                            timeout_s=args.timeout_s,
                        )
                        ok_http("B-19 GET log tail offset=-200", code2, 200)
                        if code2 == 200 and isinstance(lr2, dict):
                            c2 = lr2.get("content")
                            n2 = lr2.get("next_offset")
                            rep.add(
                                "B-19 log tail shape",
                                isinstance(c2, str) and isinstance(n2, int) and n2 >= 0,
                                detail=f"next_offset={n2}, tail_len={len(c2) if isinstance(c2,str) else 'na'}",
                            )
                            if isinstance(n2, int) and n2 >= 0:
                                # Appending from next_offset should return empty (or near-empty) when task is already done.
                                code3, _, lr3 = _http_json(
                                    "GET",
                                    _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/log?offset={n2}"),
                                    None,
                                    timeout_s=args.timeout_s,
                                )
                                ok_http("B-19 GET log append from next_offset", code3, 200)
                                if code3 == 200 and isinstance(lr3, dict):
                                    c3 = lr3.get("content") or ""
                                    rep.add("B-19 log append empty", isinstance(c3, str) and len(c3) == 0, detail=f"len={len(c3)}")
                    else:
                        rep.add("B-19 log shape", False, detail="invalid log response shape")
                except Exception as e:
                    rep.add("B-19 log", False, detail=str(e))

                # B-14 download + security
                try:
                    target = None
                    for a in artifacts:
                        if isinstance(a, dict) and a.get("name") == "eng.srt":
                            target = a
                            break
                    if not target and artifacts:
                        target = artifacts[0]
                    if not target:
                        rep.add("B-14 download", False, detail="no artifacts to download")
                    else:
                        p = str(target.get("path") or "")
                        # relative preferred
                        rel = str(target.get("name") or "")
                        u1 = _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/download?path={urllib.parse.quote(rel)}")
                        c1, _, b1 = _http_request("GET", u1, None, None, timeout_s=args.timeout_s)
                        rep.add("B-14 download (relative path)", c1 == 200, detail=f"http {c1}, bytes={len(b1)}")
                        if p:
                            u2 = _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/download?path={urllib.parse.quote(p)}")
                            c2, _, b2 = _http_request("GET", u2, None, None, timeout_s=args.timeout_s)
                            rep.add("B-14 download (absolute path)", c2 == 200, detail=f"http {c2}, bytes={len(b2)}")
                        # traversal attempt
                        u3 = _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/download?path={urllib.parse.quote('../configs/quality.yaml')}")
                        c3, _, _ = _http_request("GET", u3, None, None, timeout_s=args.timeout_s)
                        rep.add("B-14 download traversal blocked", c3 == 400, detail=f"http {c3}, want 400")
                except Exception as e:
                    rep.add("B-14 download+security", False, detail=str(e))

                # B-16 cleanup
                if args.skip_cleanup:
                    rep.add("B-16 cleanup", True, detail="skipped (--skip-cleanup)")
                else:
                    try:
                        for i, payload in enumerate(
                            [
                                {"include_diagnostics": True, "include_resume": False, "include_review": False},
                                {"include_diagnostics": True, "include_resume": True, "include_review": False},
                                {"include_diagnostics": True, "include_resume": False, "include_review": True},
                            ],
                            start=1,
                        ):
                            code, _, j = _http_json(
                                "POST",
                                _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/cleanup"),
                                payload,
                                timeout_s=args.timeout_s,
                            )
                            ok_http(f"B-16 cleanup variant {i}", code, 200, meta={"resp": j})
                        if args.run_deliverables:
                            # Ensure cleanup does NOT delete deliverables
                            u_keep = _join_url(
                                base,
                                f"/api/tasks/{urllib.parse.quote(task_id)}/download?path={urllib.parse.quote('output_en_sub.mp4')}",
                            )
                            c_keep, _, _ = _http_request("GET", u_keep, None, None, timeout_s=args.timeout_s)
                            rep.add("B-16 cleanup keeps deliverables", c_keep == 200, detail=f"http {c_keep}, want 200")
                        else:
                            rep.add("B-16 cleanup keeps deliverables", True, detail="skipped (use --run-deliverables)")
                    except Exception as e:
                        rep.add("B-16 cleanup", False, detail=str(e))

                # B-18 terminology
                try:
                    code, _, j = _http_json("GET", _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/terminology"), None, timeout_s=args.timeout_s)
                    if code == 404:
                        rep.add("B-18 GET terminology", True, detail="skipped (404: not available for this task/mode)")
                    else:
                        ok_http("B-18 GET terminology", code, 200)
                    if code == 200 and isinstance(j, dict):
                        content = j.get("content") if isinstance(j.get("content"), str) else ""
                        code2, _, _ = _http_json(
                            "PUT",
                            _join_url(base, f"/api/tasks/{urllib.parse.quote(task_id)}/terminology"),
                            {"content": content},
                            timeout_s=args.timeout_s,
                        )
                        ok_http("B-18 PUT terminology", code2, 200)
                    else:
                        rep.add("B-18 PUT terminology", True, detail="skipped (terminology not available)")
                except Exception as e:
                    rep.add("B-18 terminology", False, detail=str(e))

                # B-17 cancel
                if args.skip_cancel:
                    rep.add("B-17 cancel", True, detail="skipped (--skip-cancel)")
                else:
                    try:
                        # Start a second task and cancel quickly (best-effort; may finish too fast on tiny media).
                        payload = {
                            "video": uploaded_path,
                            "mode": "quality",
                            "preset": "quality",
                            "params": {"review_enabled": False},
                        }
                        code, _, j = _http_json("POST", _join_url(base, "/api/tasks/start"), payload, timeout_s=args.timeout_s)
                        ok_http("B-17 start task for cancel", code, 200)
                        tid = str(j.get("task_id") or "") if code == 200 and isinstance(j, dict) else ""
                        if tid:
                            code2, _, _ = _http_json("POST", _join_url(base, f"/api/tasks/{urllib.parse.quote(tid)}/cancel"), {}, timeout_s=args.timeout_s)
                            if code2 == 404:
                                # Task may have already ended or not found; treat as skipped if it's no longer running.
                                try:
                                    stc = poll_status(tid)
                                    state = str(stc.get("state") or "")
                                    if state != "running":
                                        rep.add("B-17 POST cancel", True, detail=f"skipped (http 404; current state={state})")
                                        rep.add("B-17 poll cancelled", True, detail=f"skipped (state={state})")
                                    else:
                                        rep.add("B-17 POST cancel", False, detail="http 404 while still running")
                                        rep.add("B-17 poll cancelled", False, detail=f"state={state}")
                                except Exception as e:
                                    rep.add("B-17 POST cancel", False, detail=f"http 404 and status check failed: {e}")
                                    rep.add("B-17 poll cancelled", False, detail="unknown")
                            else:
                                ok_http("B-17 POST cancel", code2, 200)
                                stc = poll_status(tid)
                                rep.add("B-17 poll cancelled", str(stc.get("state")) == "cancelled", detail=f"state={stc.get('state')}")
                        else:
                            rep.add("B-17 cancel flow", False, detail="no task id")
                    except Exception as e:
                        rep.add("B-17 cancel", False, detail=str(e))
    else:
        rep.add("B-14~B-18 task-bound checks", True, detail="skipped (--skip-task)")

    # ------------------------
    # Write report
    # ------------------------
    summ = rep.summary()
    payload_out = {
        "time": stamp,
        "base_url": base,
        "args": vars(args),
        "summary": summ,
        "items": [x.__dict__ for x in rep.items],
    }
    _write_text(json_path, _safe_json_dumps(payload_out) + "\n")

    lines = []
    lines.append("# 质量模式相关 API 回归报告")
    lines.append("")
    lines.append(f"- 时间：`{stamp}`")
    lines.append(f"- 后端：`{base}`")
    lines.append(f"- 结果：**{'PASS' if summ['passed'] else 'FAIL'}**（{summ['ok']}/{summ['total']}）")
    lines.append("")
    lines.append("## 明细")
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---:|---|")
    for it in rep.items:
        detail = (it.detail or "").replace("|", "\\|")
        lines.append(f"| `{it.name}` | {'PASS' if it.ok else 'FAIL'} | {detail} |")
    lines.append("")
    lines.append("## 原始 JSON")
    lines.append("")
    lines.append(f"- `{os.path.basename(json_path)}`")
    lines.append("")
    _write_text(md_path, "\n".join(lines) + "\n")

    print(md_path)
    return 0 if summ["passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(3)

