#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"gate config must be a JSON object: {path}")
    return data


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
        ok_count = sum(1 for item in self.items if item.ok)
        total = len(self.items)
        return {"passed": ok_count == total, "ok": ok_count, "total": total}


def _run_cmd(
    reporter: Reporter,
    *,
    name: str,
    cmd: List[str],
    cwd: Path,
    timeout_s: int,
) -> Tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=max(int(timeout_s or 0), 1),
            check=False,
        )
        out = proc.stdout or ""
        reporter.add(
            name,
            proc.returncode == 0,
            detail=f"exit={proc.returncode}",
            meta={"cmd": cmd, "cwd": str(cwd), "output": out[-8000:]},
        )
        return proc.returncode == 0, out
    except subprocess.TimeoutExpired:
        reporter.add(name, False, detail=f"timeout after {timeout_s}s", meta={"cmd": cmd, "cwd": str(cwd)})
        return False, ""


def _pytest_available() -> bool:
    return importlib.util.find_spec("pytest") is not None


def _http_health_via_openssl(url: str) -> Optional[Dict[str, Any]]:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return None

    host = str(parsed.hostname)
    port = int(parsed.port or 443)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    request_text = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    proc = subprocess.run(
        ["openssl", "s_client", "-connect", f"{host}:{port}", "-servername", host, "-quiet"],
        input=request_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        return None

    raw = proc.stdout or ""
    if "\r\n\r\n" in raw:
        _, body = raw.split("\r\n\r\n", 1)
    elif "\n\n" in raw:
        _, body = raw.split("\n\n", 1)
    else:
        return None

    data = json.loads(body.strip())
    return data if isinstance(data, dict) else None


def _http_health(base_url: str) -> Dict[str, Any]:
    url = str(base_url or "").rstrip("/") + "/api/health"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        fallback = _http_health_via_openssl(url)
        if fallback is not None:
            return fallback
        raise


def _node_live_probe(base_url: str, *, insecure: bool = False) -> Dict[str, Any]:
    script = r"""
const rawBase = String(process.argv[1] || '').trim();
const insecure = process.argv[2] === '1';
if (!rawBase) {
  console.error('missing base url');
  process.exit(2);
}
const target = new URL('/api/health', rawBase.endsWith('/') ? rawBase : rawBase + '/');
const transport = target.protocol === 'https:' ? require('https') : require('http');
const req = transport.request(target, { method: 'GET', timeout: 15000, rejectUnauthorized: !insecure }, (res) => {
  let body = '';
  res.setEncoding('utf8');
  res.on('data', (chunk) => { body += chunk; });
  res.on('end', () => {
    process.stdout.write(JSON.stringify({
      ok: res.statusCode >= 200 && res.statusCode < 300,
      statusCode: res.statusCode,
      insecure,
      body,
    }));
  });
});
req.on('timeout', () => req.destroy(new Error('timeout')));
req.on('error', (err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
req.end();
"""
    proc = subprocess.run(
        ["node", "-e", script, base_url, "1" if insecure else "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=25,
        check=False,
        env=os.environ.copy(),
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "node probe failed").strip())
    payload = json.loads(proc.stdout or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("node probe returned invalid payload")
    return payload


def _electron_live_probe(repo_root: Path, base_url: str, trusted_hosts: Optional[List[str]] = None) -> Dict[str, Any]:
    desktop_dir = repo_root / "apps" / "desktop"
    probe_script = desktop_dir / "electron_tls_probe.js"
    env = os.environ.copy()
    if trusted_hosts:
        env["PROBE_TRUSTED_COMPAT_HOSTS"] = ",".join([host for host in trusted_hosts if host])
    proc = subprocess.run(
        [
            "env",
            "-u",
            "ELECTRON_RUN_AS_NODE",
            "./node_modules/.bin/electron",
            str(probe_script),
            str(base_url).rstrip("/") + "/api/health",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=40,
        check=False,
        cwd=str(desktop_dir),
        env=env,
    )
    out = proc.stdout or ""
    if proc.returncode != 0:
        raise RuntimeError(out.strip() or "electron probe failed")
    payload = json.loads(out.strip().splitlines()[-1] if out.strip() else "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("electron probe returned invalid payload")
    return payload


def _parse_base_url_list(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    items: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


def _extract_hostnames(base_urls: List[str]) -> List[str]:
    items: List[str] = []
    for raw in base_urls:
        try:
            host = str(urlparse(raw).hostname or "").strip()
        except Exception:
            host = ""
        if host and host not in items:
            items.append(host)
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Run auth/login/license regression gate")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dev-smoke", action="store_true")
    mode.add_argument("--regression-gate", action="store_true")
    mode.add_argument("--release-gate", action="store_true")
    parser.add_argument("--base-url", default="", help="Optional live auth service base URL, e.g. http://127.0.0.1:8001")
    parser.add_argument(
        "--compat-base-url",
        action="append",
        default=[],
        help="Optional live compat auth base URL; can be passed multiple times",
    )
    parser.add_argument("--output-dir", default="", help="Optional report directory override")
    args = parser.parse_args()

    repo_root = _repo_root()
    config_name = (
        "auth_dev_smoke.json"
        if args.dev_smoke
        else "auth_regression_gate.json"
        if args.regression_gate
        else "auth_release_gate.json"
    )
    config = _load_json(repo_root / "automation" / "configs" / config_name)
    out_dir = Path(args.output_dir).resolve() if args.output_dir else repo_root / "automation" / "reports" / "auth_regress"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()
    summary_path = out_dir / f"auth_regress_{stamp}.json"

    reporter = Reporter()
    base_url = str(args.base_url or config.get("base_url") or "").strip()
    compat_base_urls = _parse_base_url_list(args.compat_base_url or config.get("compat_base_urls") or [])
    trusted_compat_hosts = _extract_hostnames(compat_base_urls)

    if bool(config.get("run_compileall", True)):
        _run_cmd(
            reporter,
            name="compileall auth suite",
            cmd=[
                sys.executable,
                "-m",
                "compileall",
                str((repo_root / "apps" / "auth_service").resolve()),
                str((repo_root / "tests" / "test_auth_service_api.py").resolve()),
                str((repo_root / "automation" / "scripts" / "auth_api_regress.py").resolve()),
            ],
            cwd=repo_root,
            timeout_s=int(config.get("compileall_timeout_s", 120)),
        )

    if bool(config.get("run_pytest", True)):
        pytest_targets = config.get("pytest_targets") or ["tests/test_auth_service_api.py"]
        pytest_cmd = [sys.executable, "-m", "pytest", *pytest_targets]
        if not _pytest_available():
            pytest_cmd = [sys.executable, *pytest_targets]
        _run_cmd(
            reporter,
            name="pytest auth service",
            cmd=pytest_cmd,
            cwd=repo_root,
            timeout_s=int(config.get("pytest_timeout_s", 180)),
        )

    if bool(config.get("run_desktop_tests", True)):
        _run_cmd(
            reporter,
            name="desktop auth vitest",
            cmd=["npm", "run", "test:auth"],
            cwd=repo_root / "apps" / "desktop",
            timeout_s=int(config.get("desktop_timeout_s", 180)),
        )

    if bool(config.get("run_live_healthcheck", False)):
        if not base_url:
            reporter.add("live auth health", True, detail="skipped: base URL not provided")
        else:
            try:
                payload = _http_health(base_url)
                ok = bool(payload.get("mysql")) and bool(payload.get("redis"))
                reporter.add("live auth health", ok, detail=json.dumps(payload, ensure_ascii=False), meta=payload)
            except urllib.error.HTTPError as exc:
                reporter.add("live auth health", False, detail=f"http {exc.code}: {exc.reason}")
            except Exception as exc:
                reporter.add("live auth health", False, detail=str(exc))

    if bool(config.get("run_live_node_probe", False)):
        if not base_url:
            reporter.add("live desktop auth probe", True, detail="skipped: base URL not provided")
        else:
            try:
                payload = _node_live_probe(base_url)
                ok = bool(payload.get("ok"))
                reporter.add("live desktop auth probe", ok, detail=json.dumps(payload, ensure_ascii=False), meta=payload)
            except Exception as exc:
                reporter.add("live desktop auth probe", False, detail=str(exc))

    if bool(config.get("run_live_electron_probe", False)):
        if not base_url:
            reporter.add("live electron auth probe", True, detail="skipped: base URL not provided")
        else:
            try:
                payload = _electron_live_probe(repo_root, base_url, trusted_hosts=trusted_compat_hosts)
                ok = bool(payload.get("ok"))
                reporter.add("live electron auth probe", ok, detail=json.dumps(payload, ensure_ascii=False), meta=payload)
            except Exception as exc:
                reporter.add("live electron auth probe", False, detail=str(exc))

    for compat_base_url in compat_base_urls:
        if bool(config.get("run_live_healthcheck", False)):
            try:
                payload = _http_health(compat_base_url)
                ok = bool(payload.get("mysql")) and bool(payload.get("redis"))
                reporter.add(
                    f"live compat auth health [{compat_base_url}]",
                    ok,
                    detail=json.dumps(payload, ensure_ascii=False),
                    meta=payload,
                )
            except urllib.error.HTTPError as exc:
                reporter.add(f"live compat auth health [{compat_base_url}]", False, detail=f"http {exc.code}: {exc.reason}")
            except Exception as exc:
                reporter.add(f"live compat auth health [{compat_base_url}]", False, detail=str(exc))

        if bool(config.get("run_live_node_probe", False)):
            try:
                payload = _node_live_probe(compat_base_url, insecure=True)
                ok = bool(payload.get("ok"))
                reporter.add(
                    f"live compat node probe [{compat_base_url}]",
                    ok,
                    detail=json.dumps(payload, ensure_ascii=False),
                    meta=payload,
                )
            except Exception as exc:
                reporter.add(f"live compat node probe [{compat_base_url}]", False, detail=str(exc))

        if bool(config.get("run_live_electron_probe", False)):
            try:
                payload = _electron_live_probe(repo_root, compat_base_url, trusted_hosts=trusted_compat_hosts)
                ok = bool(payload.get("ok"))
                reporter.add(
                    f"live compat electron probe [{compat_base_url}]",
                    ok,
                    detail=json.dumps(payload, ensure_ascii=False),
                    meta=payload,
                )
            except Exception as exc:
                reporter.add(f"live compat electron probe [{compat_base_url}]", False, detail=str(exc))

    summary = {
        "profile": config_name,
        "base_url": base_url,
        "compat_base_urls": compat_base_urls,
        "results": [item.__dict__ for item in reporter.items],
        "summary": reporter.summary(),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
