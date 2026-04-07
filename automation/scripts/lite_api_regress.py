#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return json.dumps({"_repr": repr(value)}, ensure_ascii=False, indent=2)


def _load_gate_config(path: str) -> Dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"gate config not found: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"gate config must be a json object: {config_path}")
    return data


def _parse_json_dict(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _merge_overrides_json(raw: str, extra: Dict[str, Any]) -> str:
    merged = _parse_json_dict(raw)
    merged.update(extra or {})
    return json.dumps(merged, ensure_ascii=False) if merged else ""


def _require_video_selector(video: str, cases_manifest: str, case_id: str) -> Tuple[str, str, str]:
    video = str(video or "").strip()
    cases_manifest = str(cases_manifest or "").strip()
    case_id = str(case_id or "").strip()
    if video:
        return video, "", ""
    if cases_manifest and case_id:
        return "", cases_manifest, case_id
    raise RuntimeError("需要提供 --video，或同时提供 --cases-manifest 与 --case-id")


def _tail_text(path: Path, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:] if len(text) > max_chars else text


def _read_json_dict(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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


def _log(message: str) -> None:
    print(message, flush=True)


def _run_cmd(
    reporter: Reporter,
    *,
    name: str,
    cmd: List[str],
    cwd: Path,
    timeout_s: int,
    summary_path: Optional[Path] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
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
        summary = _read_json_dict(summary_path) if summary_path else {}
        ok = proc.returncode == 0
        detail = f"exit={proc.returncode}"
        if summary:
            if "ok" in summary:
                detail += f", summary.ok={summary.get('ok')}"
            elif "all_ok" in summary:
                detail += f", summary.all_ok={summary.get('all_ok')}"
        meta = {
            "cmd": cmd,
            "cwd": str(cwd),
            "output": (proc.stdout or "")[-8000:],
            "summary_path": str(summary_path) if summary_path else "",
            "summary": summary,
        }
        if extra_meta:
            meta.update(extra_meta)
        reporter.add(name, ok, detail=detail, meta=meta)
        return ok, summary
    except subprocess.TimeoutExpired:
        meta = {"cmd": cmd, "cwd": str(cwd), "summary_path": str(summary_path) if summary_path else ""}
        if extra_meta:
            meta.update(extra_meta)
        reporter.add(name, False, detail=f"timeout after {timeout_s}s", meta=meta)
        return False, {}
    except Exception as exc:
        meta = {"cmd": cmd, "cwd": str(cwd), "summary_path": str(summary_path) if summary_path else ""}
        if extra_meta:
            meta.update(extra_meta)
        reporter.add(name, False, detail=str(exc), meta=meta)
        return False, {}


def _compileall_cmd(repo_root: Path) -> List[str]:
    return [
        sys.executable,
        "-m",
        "compileall",
        str((repo_root / "automation" / "scripts").resolve()),
        str((repo_root / "pipelines").resolve()),
        str((repo_root / "scripts").resolve()),
        str((repo_root / "apps" / "backend" / "backend").resolve()),
    ]


def _default_test_targets() -> List[str]:
    return [
        "tests/test_lite_api_regress.py",
        "tests/test_backend_lite_api.py",
        "tests/test_lite_command_builder.py",
        "tests/test_lite_pipeline_impl.py",
        "tests/test_run_lite_e2e.py",
        "tests/test_run_lite_smoke_impl.py",
        "tests/test_run_lite_pipeline_smoke_impl.py",
        "tests/test_run_lite_resume_smoke_impl.py",
        "tests/test_run_lite_golden20_smoke_suite_impl.py",
    ]


def _pytest_cmd(repo_root: Path, pytest_targets: List[str]) -> List[str]:
    cmd = [sys.executable, "-m", "pytest"]
    if pytest_targets:
        cmd.extend(pytest_targets)
    else:
        cmd.extend(_default_test_targets())
    return cmd


def _desktop_test_cmd() -> List[str]:
    return ["npm", "run", "test:logic"]


def _auth_gate_cmd(repo_root: Path, mode: str, base_url: str) -> List[str]:
    cmd = [sys.executable, str((repo_root / "automation" / "scripts" / "auth_api_regress.py").resolve())]
    normalized = str(mode or "").strip() or "dev-smoke"
    if normalized not in {"dev-smoke", "regression-gate", "release-gate"}:
        normalized = "dev-smoke"
    cmd.append(f"--{normalized}")
    if str(base_url or "").strip():
        cmd.extend(["--base-url", str(base_url).strip()])
    return cmd


def _python_test_cmd(test_target: str) -> List[str]:
    return [sys.executable, str(test_target)]


def _append_video_selector(cmd: List[str], *, video: str, cases_manifest: str, case_id: str) -> None:
    resolved_video, resolved_manifest, resolved_case_id = _require_video_selector(video, cases_manifest, case_id)
    if resolved_video:
        cmd.extend(["--video", resolved_video])
    else:
        cmd.extend(["--cases-manifest", resolved_manifest, "--case-id", resolved_case_id])


def _build_smoke_cmd(repo_root: Path, args: argparse.Namespace, output_dir: Path) -> Tuple[List[str], Path]:
    script = (repo_root / "pipelines" / "tools" / "run_lite_pipeline_smoke_impl.py").resolve()
    cmd = [
        sys.executable,
        str(script),
        "--output-dir",
        str(output_dir),
        "--config",
        str(args.config),
        "--preset",
        str(args.preset),
        "--max-runtime-s",
        str(int(args.smoke_max_runtime_s or 0)),
        "--log-max-kb",
        str(int(args.log_max_kb or 0)),
    ]
    _append_video_selector(cmd, video=args.video, cases_manifest=args.cases_manifest, case_id=args.case_id)
    overrides_json = _merge_overrides_json(
        str(args.smoke_overrides_json or ""),
        {"skip_tts": True} if bool(args.smoke_skip_tts) else {},
    )
    if overrides_json:
        cmd.extend(["--overrides-json", overrides_json])
    if bool(args.smoke_skip_tts):
        cmd.append("--skip-tts")
    if bool(args.smoke_require_quality_report):
        cmd.append("--require-quality-report")
    if bool(args.cleanup_artifacts):
        cmd.append("--cleanup-artifacts")
    return cmd, output_dir / "lite_smoke_summary.json"


def _build_resume_cmd(repo_root: Path, args: argparse.Namespace, output_dir: Path) -> Tuple[List[str], Path]:
    script = (repo_root / "pipelines" / "tools" / "run_lite_resume_smoke_impl.py").resolve()
    cmd = [
        sys.executable,
        str(script),
        "--output-dir",
        str(output_dir),
        "--config",
        str(args.config),
        "--preset",
        str(args.preset),
        "--resume-from",
        str(args.resume_from),
        "--max-runtime-s",
        str(int(args.resume_max_runtime_s or 0)),
        "--log-max-kb",
        str(int(args.log_max_kb or 0)),
    ]
    _append_video_selector(cmd, video=args.video, cases_manifest=args.cases_manifest, case_id=args.case_id)
    base_overrides_json = _merge_overrides_json(
        str(args.resume_base_overrides_json or ""),
        {"skip_tts": True} if str(args.resume_from) in {"tts", "mux"} else {},
    )
    if base_overrides_json:
        cmd.extend(["--base-overrides-json", base_overrides_json])
    if str(args.resume_overrides_json or "").strip():
        cmd.extend(["--resume-overrides-json", str(args.resume_overrides_json)])
    if bool(args.resume_skip_tts):
        cmd.append("--skip-tts")
    if bool(args.resume_require_quality_report):
        cmd.append("--require-quality-report")
    if bool(args.cleanup_artifacts):
        cmd.append("--cleanup-artifacts")
    if bool(args.resume_reuse_base):
        cmd.append("--no-prepare-base")
    return cmd, output_dir / "lite_resume_smoke_summary.json"


def _build_golden20_cmd(repo_root: Path, args: argparse.Namespace, output_dir: Path) -> Tuple[List[str], Path]:
    script = (repo_root / "pipelines" / "tools" / "run_lite_golden20_smoke_suite_impl.py").resolve()
    cmd = [
        sys.executable,
        str(script),
        "--out-root",
        str(output_dir),
        "--cases-manifest",
        str(args.golden20_manifest),
        "--config",
        str(args.config),
        "--preset",
        str(args.preset),
        "--max-runtime-s",
        str(int(args.golden20_max_runtime_s or 0)),
        "--log-max-kb",
        str(int(args.log_max_kb or 0)),
    ]
    overrides_json = _merge_overrides_json(
        str(args.golden20_overrides_json or ""),
        {"skip_tts": True} if bool(args.golden20_skip_tts) else {},
    )
    cmd.extend(["--overrides-json", overrides_json or "{}"])
    for case_id in list(args.golden20_case_id or []):
        if str(case_id).strip():
            cmd.extend(["--case-id", str(case_id).strip()])
    if bool(args.golden20_skip_tts):
        cmd.append("--skip-tts")
    if bool(args.golden20_require_quality_report):
        cmd.append("--require-quality-report")
    if bool(args.cleanup_artifacts):
        cmd.append("--cleanup-artifacts")
    return cmd, output_dir / "golden20_smoke_suite_summary.json"


def _render_markdown_report(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines.append("# Lite Mode Gate Report")
    lines.append("")
    lines.append(f"- Time: `{payload.get('time')}`")
    lines.append(f"- Profile: `{payload.get('profile')}`")
    lines.append(f"- Passed: `{summary.get('passed')}`")
    lines.append(f"- Checks: `{summary.get('ok')}/{summary.get('total')}`")
    lines.append(f"- Report root: `{payload.get('report_root')}`")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    for item in payload.get("items") or []:
        name = item.get("name")
        ok = item.get("ok")
        detail = item.get("detail") or ""
        lines.append(f"- `{'PASS' if ok else 'FAIL'}` `{name}`: {detail}")
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        summary_doc = meta.get("summary") if isinstance(meta.get("summary"), dict) else {}
        if summary_doc:
            lines.append(f"  summary: `{json.dumps(summary_doc, ensure_ascii=False)}`")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    repo_root = _repo_root()
    ap = argparse.ArgumentParser(description="Lite-mode automation gate runner")
    ap.add_argument("--config", default="configs/defaults.yaml", help="Base config YAML")
    ap.add_argument("--preset", default="normal", help="Lite preset key")
    ap.add_argument("--video", default="", help="Input video path for single/resume smoke")
    ap.add_argument("--cases-manifest", default="", help="Optional manifest for single/resume smoke")
    ap.add_argument("--case-id", default="", help="Case id in manifest for single/resume smoke")
    ap.add_argument(
        "--golden20-manifest",
        default=str(repo_root / "reports" / "lite_phase1" / "golden20_lite_1min" / "cases.jsonl"),
        help="Manifest used by golden20 smoke suite",
    )
    ap.add_argument("--golden20-case-id", action="append", default=[], help="Optional case id(s) for golden20 suite")
    ap.add_argument("--report-dir", default=str(repo_root / "automation" / "reports"), help="Report output directory")
    ap.add_argument("--profile-name", default="custom", help="Profile name written into report")
    ap.add_argument("--run-compileall", action="store_true", help="Run python compileall on lite-related paths")
    ap.add_argument("--run-pytest", action="store_true", help="Run lite gate-related pytest targets")
    ap.add_argument("--run-desktop-tests", action="store_true", help="Run desktop vitest suite for lite-facing UI")
    ap.add_argument("--run-auth-gate", action="store_true", help="Run login/auth/license regression gate")
    ap.add_argument("--run-smoke", action="store_true", help="Run single lite pipeline smoke")
    ap.add_argument("--run-resume", action="store_true", help="Run lite resume smoke")
    ap.add_argument("--run-golden20", action="store_true", help="Run representative golden20 smoke suite")
    ap.add_argument("--pytest-target", action="append", default=[], help="Optional pytest target(s)")
    ap.add_argument("--smoke-overrides-json", default="", help="Overrides JSON for single smoke")
    ap.add_argument("--resume-base-overrides-json", default="", help="Base-run overrides JSON for resume smoke")
    ap.add_argument("--resume-overrides-json", default="", help="Resume-run overrides JSON")
    ap.add_argument("--golden20-overrides-json", default="", help="Overrides JSON for golden20 suite")
    ap.add_argument("--resume-from", default="tts", help="Resume stage for resume smoke")
    ap.add_argument("--smoke-skip-tts", action="store_true", help="Validate subtitle-only contract in single smoke")
    ap.add_argument("--resume-skip-tts", action="store_true", help="Validate subtitle-only contract in resume smoke")
    ap.add_argument("--golden20-skip-tts", action="store_true", help="Validate subtitle-only contract in golden20 suite")
    ap.add_argument("--smoke-require-quality-report", action="store_true", help="Require quality_report.json in single smoke")
    ap.add_argument("--resume-require-quality-report", action="store_true", help="Require quality_report.json in resume smoke")
    ap.add_argument("--golden20-require-quality-report", action="store_true", help="Require quality_report.json in golden20 suite")
    ap.add_argument("--smoke-max-runtime-s", type=int, default=240, help="Hard timeout for single smoke")
    ap.add_argument("--resume-max-runtime-s", type=int, default=240, help="Hard timeout for resume smoke")
    ap.add_argument("--golden20-max-runtime-s", type=int, default=240, help="Hard timeout per case in golden20 suite")
    ap.add_argument("--timeout-s", type=int, default=3600, help="Subprocess timeout for each gate step")
    ap.add_argument("--log-max-kb", type=int, default=256, help="Runner log cap forwarded to lite smoke scripts")
    ap.add_argument("--cleanup-artifacts", action="store_true", help="Forward cleanup flag to lite smoke scripts")
    ap.add_argument("--resume-reuse-base", action="store_true", help="Reuse existing output dir for resume smoke")
    ap.add_argument("--auth-gate-mode", default="dev-smoke", help="Auth gate mode: dev-smoke / regression-gate / release-gate")
    ap.add_argument("--auth-base-url", default="", help="Optional live auth service base URL for auth gate")
    ap.add_argument("--dev-smoke", action="store_true", help="Load automation/configs/lite_dev_smoke.json")
    ap.add_argument("--regression-gate", action="store_true", help="Load automation/configs/lite_regression_gate.json")
    ap.add_argument("--release-gate", action="store_true", help="Load automation/configs/lite_release_gate.json")
    ap.add_argument("--gate-config", default="", help="Custom gate config JSON")
    args = ap.parse_args()

    preset_flags = [bool(args.dev_smoke), bool(args.regression_gate), bool(args.release_gate)]
    if sum(1 for flag in preset_flags if flag) > 1:
        raise SystemExit("Only one of --dev-smoke / --regression-gate / --release-gate may be used at a time.")

    gate_config_path = str(args.gate_config or "").strip()
    if not gate_config_path:
        if args.dev_smoke:
            gate_config_path = str(repo_root / "automation" / "configs" / "lite_dev_smoke.json")
            args.profile_name = "dev-smoke"
        elif args.regression_gate:
            gate_config_path = str(repo_root / "automation" / "configs" / "lite_regression_gate.json")
            args.profile_name = "regression-gate"
        elif args.release_gate:
            gate_config_path = str(repo_root / "automation" / "configs" / "lite_release_gate.json")
            args.profile_name = "release-gate"

    if gate_config_path:
        gate_cfg = _load_gate_config(gate_config_path)
        for key, value in gate_cfg.items():
            if hasattr(args, key):
                setattr(args, key, value)

    stamp = _now_stamp()
    report_dir = Path(str(args.report_dir)).expanduser().resolve()
    report_root = report_dir / f"lite_api_regress_{stamp}"
    _ensure_dir(report_root)
    json_path = report_dir / f"lite_api_regress_{stamp}.json"
    md_path = report_dir / f"lite_api_regress_{stamp}.md"

    reporter = Reporter()
    _log(f"[run] profile={args.profile_name}")
    _log(f"[run] report_root={report_root}")
    if gate_config_path:
        _log(f"[run] gate_config={gate_config_path}")

    try:
        if bool(args.run_compileall):
            ok, _ = _run_cmd(
                reporter,
                name="A-0 compileall",
                cmd=_compileall_cmd(repo_root),
                cwd=repo_root,
                timeout_s=int(args.timeout_s or 0),
            )
            if not ok:
                raise RuntimeError("compileall failed")

        if bool(args.run_pytest):
            test_targets = list(args.pytest_target or []) or _default_test_targets()
            if importlib.util.find_spec("pytest") is not None:
                ok, _ = _run_cmd(
                    reporter,
                    name="A-1 lite pytest",
                    cmd=_pytest_cmd(repo_root, test_targets),
                    cwd=repo_root,
                    timeout_s=int(args.timeout_s or 0),
                )
                if not ok:
                    raise RuntimeError("pytest failed")
            else:
                reporter.add("A-1 pytest runtime", True, detail="pytest unavailable, fallback to direct python test files")
                all_ok = True
                for test_target in test_targets:
                    ok, _ = _run_cmd(
                        reporter,
                        name=f"A-1 fallback {test_target}",
                        cmd=_python_test_cmd(test_target),
                        cwd=repo_root,
                        timeout_s=int(args.timeout_s or 0),
                    )
                    all_ok = all_ok and ok
                if not all_ok:
                    raise RuntimeError("fallback python tests failed")

        if bool(args.run_desktop_tests):
            ok, _ = _run_cmd(
                reporter,
                name="A-2 desktop vitest",
                cmd=_desktop_test_cmd(),
                cwd=(repo_root / "apps" / "desktop").resolve(),
                timeout_s=int(args.timeout_s or 0),
            )
            if not ok:
                raise RuntimeError("desktop vitest failed")

        if bool(args.run_auth_gate):
            ok, _ = _run_cmd(
                reporter,
                name=f"A-3 auth gate ({args.auth_gate_mode})",
                cmd=_auth_gate_cmd(repo_root, str(args.auth_gate_mode), str(args.auth_base_url)),
                cwd=repo_root,
                timeout_s=int(args.timeout_s or 0),
            )
            if not ok:
                raise RuntimeError("auth gate failed")

        if bool(args.run_smoke):
            smoke_dir = report_root / "single_smoke"
            cmd, summary_path = _build_smoke_cmd(repo_root, args, smoke_dir)
            ok, _ = _run_cmd(
                reporter,
                name="B-0 lite single smoke",
                cmd=cmd,
                cwd=repo_root,
                timeout_s=int(args.timeout_s or 0),
                summary_path=summary_path,
                extra_meta={"work_dir": str(smoke_dir)},
            )
            if not ok:
                raise RuntimeError("single smoke failed")

        if bool(args.run_resume):
            resume_dir = report_root / "resume_smoke"
            cmd, summary_path = _build_resume_cmd(repo_root, args, resume_dir)
            ok, _ = _run_cmd(
                reporter,
                name="B-1 lite resume smoke",
                cmd=cmd,
                cwd=repo_root,
                timeout_s=int(args.timeout_s or 0),
                summary_path=summary_path,
                extra_meta={"work_dir": str(resume_dir), "resume_from": str(args.resume_from)},
            )
            if not ok:
                raise RuntimeError("resume smoke failed")

        if bool(args.run_golden20):
            suite_dir = report_root / "golden20_suite"
            cmd, summary_path = _build_golden20_cmd(repo_root, args, suite_dir)
            ok, _ = _run_cmd(
                reporter,
                name="B-2 lite golden20 suite",
                cmd=cmd,
                cwd=repo_root,
                timeout_s=int(args.timeout_s or 0),
                summary_path=summary_path,
                extra_meta={"work_dir": str(suite_dir), "manifest": str(args.golden20_manifest)},
            )
            if not ok:
                raise RuntimeError("golden20 suite failed")
    except Exception as exc:
        reporter.add("Z-0 runner", False, detail=str(exc), meta={"traceback": traceback.format_exc()[-12000:]})

    payload = {
        "time": stamp,
        "profile": str(args.profile_name),
        "gate_config": gate_config_path,
        "report_root": str(report_root),
        "args": vars(args),
        "summary": reporter.summary(),
        "items": [item.__dict__ for item in reporter.items],
    }
    _write_text(json_path, _safe_json_dumps(payload))
    _write_text(md_path, _render_markdown_report(payload))
    _log(f"[done] report_json={json_path}")
    _log(f"[done] report_md={md_path}")
    return 0 if bool(payload["summary"].get("passed")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
