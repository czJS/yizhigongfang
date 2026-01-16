#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

import selectors


def _read_jsonl(p: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        obj = json.loads(ln)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(p.read_text(encoding="utf-8", errors="ignore") or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _load_yaml(p: Path) -> Dict[str, Any]:
    data = yaml.safe_load(p.read_text(encoding="utf-8", errors="ignore")) or {}
    return data if isinstance(data, dict) else {}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _is_done_success(out_dir: Path) -> bool:
    """
    Keep consistent with scripts/run_quality_e2e.py:
    done only when quality_report.json exists, passed=true, and required artifacts not missing.
    """
    rep_p = out_dir / "quality_report.json"
    if not rep_p.exists():
        return False
    rep = _read_json(rep_p)
    if rep.get("passed") is not True:
        return False
    missing = (((rep.get("checks") or {}).get("required_artifacts") or {}).get("missing") or [])
    if isinstance(missing, list) and len(missing) > 0:
        return False
    return True


def _detect_active_run(run_root: Path, *, recent_s: int = 180) -> bool:
    """
    Heuristic: treat the run as active when we see very recently updated `e2e_run.log`
    AND the segment directory does not have a finished `e2e_status.json` yet.
    This avoids starting a duplicate run that writes into the same outputs.
    """
    if not run_root.exists():
        return False
    now = time.time()
    for seg_dir in run_root.iterdir():
        if not seg_dir.is_dir():
            continue
        log_p = seg_dir / "e2e_run.log"
        st_p = seg_dir / "e2e_status.json"
        try:
            if log_p.exists() and (now - log_p.stat().st_mtime) <= float(recent_s) and (not st_p.exists()):
                return True
        except Exception:
            continue
    return False


def _stage_progress(out_root: Path, exp_yaml: Path, segments_jsonl: Path) -> Dict[str, Any]:
    segs = _read_jsonl(segments_jsonl)
    seg_ids = [str(s.get("id") or "").strip() for s in segs if str(s.get("id") or "").strip()]

    exp_cfg = _load_yaml(exp_yaml)
    baseline = (exp_cfg.get("baseline") or {}) if isinstance(exp_cfg.get("baseline"), dict) else {}
    exp_defs = (exp_cfg.get("experiments") or {}) if isinstance(exp_cfg.get("experiments"), dict) else {}
    run_names = [str(baseline.get("name") or "baseline")]
    run_names += [str(k) for k in exp_defs.keys()]

    runs: Dict[str, Any] = {}
    all_done = True
    total_success = 0
    total_failed = 0
    total_pending = 0
    total = 0
    for rn in run_names:
        rdir = out_root / rn
        succ = 0
        fail = 0
        pend = 0
        failed_ids: List[str] = []
        for sid in seg_ids:
            total += 1
            seg_dir = rdir / sid
            if _is_done_success(seg_dir):
                succ += 1
                total_success += 1
            else:
                st_p = seg_dir / "e2e_status.json"
                if st_p.exists():
                    # a run was attempted but didn't pass gates
                    fail += 1
                    total_failed += 1
                    failed_ids.append(sid)
                else:
                    pend += 1
                    total_pending += 1
        runs[rn] = {"success": succ, "failed": fail, "pending": pend, "total": len(seg_ids), "failed_ids": failed_ids[:50]}
        if succ < len(seg_ids):
            all_done = False
    return {
        "out_root": str(out_root),
        "runs": runs,
        "done": all_done,
        "total": total,
        "total_success": total_success,
        "total_failed": total_failed,
        "total_pending": total_pending,
    }


def _run_and_tee(cmd: List[str], log_path: Path, *, timeout_s: int = 0, purpose: str = "") -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n[{_now_iso()}] CMD: {' '.join(cmd)}\n")
        f.flush()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert p.stdout is not None

        timeout_s = max(0, int(timeout_s or 0))
        t0 = time.time()
        sel = selectors.DefaultSelector()
        sel.register(p.stdout, selectors.EVENT_READ)

        while True:
            rc = p.poll()
            if rc is not None:
                # Drain remaining output (best-effort)
                try:
                    for line in p.stdout:
                        if not line:
                            continue
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        f.write(line)
                        f.flush()
                except Exception:
                    pass
                return int(rc)

            if timeout_s > 0 and (time.time() - t0) >= float(timeout_s):
                note = f"[watchdog] timeout reached: {timeout_s}s"
                if purpose:
                    note += f" purpose={purpose}"
                note += f" terminating pid={p.pid}\n"
                sys.stdout.write(note)
                sys.stdout.flush()
                f.write(note)
                f.flush()
                try:
                    p.terminate()
                except Exception:
                    pass
                try:
                    p.wait(timeout=15)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
                rc2 = p.poll()
                return int(rc2 if rc2 is not None else -15)

            # Wait for readable output (short timeout so we can enforce watchdog)
            events = sel.select(timeout=0.5)
            for key, _mask in events:
                try:
                    line = key.fileobj.readline()
                except Exception:
                    line = ""
                if not line:
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
                f.write(line)
                f.flush()


def _run_cmd_string_with_live_progress(
    *,
    cmd_str: str,
    log_path: Path,
    progress_path: Path,
    stage: "Stage",
    poll_s: int,
    status_running: str,
) -> int:
    """
    Run a command (string) and periodically refresh progress.json while it is running.
    Uses bash+tee so docker logs show output while also appending to a persistent log file.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    # Use pipefail so the command return code is preserved when tee is used.
    bash_cmd = f"set -o pipefail; {cmd_str} 2>&1 | tee -a {str(log_path)}"
    proc = subprocess.Popen(["bash", "-lc", bash_cmd])
    last_tick = 0.0
    while True:
        rc = proc.poll()
        now = time.time()
        if (now - last_tick) >= max(5.0, float(poll_s)):
            last_tick = now
            try:
                progress_path.write_text(
                    json.dumps(
                        {
                            "ts": _now_iso(),
                            "stage": stage.key,
                            "stage_cn": stage.label_cn,
                            "status": status_running,
                            "detail": _stage_progress(stage.out_root, stage.experiments, stage.segments),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
        if rc is not None:
            return int(rc)
        time.sleep(1.0)


@dataclass
class Stage:
    key: str
    label_cn: str
    segments: Path
    experiments: Path
    base_config: Path
    out_root: Path
    report_json: Optional[Path] = None
    report_md: Optional[Path] = None
    eval_timeout_s: int = 0


def _eval_stage(stage: Stage, *, bootstrap_iters: int, log_path: Path) -> None:
    if not stage.report_json:
        return
    exp_cfg = _load_yaml(stage.experiments)
    baseline = (exp_cfg.get("baseline") or {}) if isinstance(exp_cfg.get("baseline"), dict) else {}
    exp_defs = (exp_cfg.get("experiments") or {}) if isinstance(exp_cfg.get("experiments"), dict) else {}
    base_name = str(baseline.get("name") or "baseline")

    run_specs = []
    for k in exp_defs.keys():
        run_specs.append(f"{k}={stage.out_root}/{k}")

    cmd = [
        sys.executable,
        "/app/scripts/eval_quality_e2e_golden_suite.py",
        "--segments",
        str(stage.segments),
        "--baseline",
        str(stage.out_root / base_name),
        "--bootstrap-iters",
        str(int(bootstrap_iters)),
        "--out-json",
        str(stage.report_json),
    ]
    if stage.report_md:
        cmd.extend(["--out-md", str(stage.report_md)])
    if run_specs:
        cmd.append("--runs")
        cmd.extend(run_specs)
    _run_and_tee(cmd, log_path, timeout_s=int(getattr(stage, "eval_timeout_s", 0) or 0), purpose="eval_stage")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Round1 一键长任务（ASR→A类→B类 short3），支持断点续跑与进度文件。"
    )
    ap.add_argument("--poll-s", type=int, default=60, help="进度轮询间隔（秒）")
    ap.add_argument("--bootstrap-iters", type=int, default=2000, help="评测 bootstrap 次数（越大越稳但越慢）")
    ap.add_argument("--retry-rounds", type=int, default=2, help="每个 stage 结束后对失败段做重试轮数（0=不重试）")
    ap.add_argument("--retry-sleep-s", type=int, default=60, help="每轮重试之间的等待秒数（给 ollama/资源降压）")
    ap.add_argument(
        "--seg-stall-timeout-s",
        type=int,
        default=900,
        help="单段卡死止血：连续 N 秒无进展且 CPU 基本不动则终止该段（0=关闭）。默认 900=15 分钟",
    )
    ap.add_argument("--seg-stall-check-s", type=int, default=10, help="单段卡死检测轮询间隔（秒）")
    ap.add_argument(
        "--seg-stall-cpu-min-jiffies",
        type=int,
        default=5,
        help="判定“CPU 不增长”的最小 jiffies 增量阈值（越小越敏感）",
    )
    ap.add_argument(
        "--seg-max-runtime-s",
        type=int,
        default=0,
        help="单段硬超时（秒，0=关闭）。到点直接终止该段，防止无限拖延。",
    )
    ap.add_argument(
        "--eval-max-runtime-s",
        type=int,
        default=1200,
        help="评测/汇总阶段硬超时（秒，0=关闭）。默认 1200=20 分钟。超时会终止评测脚本并继续流程，避免整体卡住。",
    )
    ap.add_argument(
        "--round15-enable",
        action="store_true",
        help="在 Round1（开关）阶段全部完成后，自动执行 Round1.5（short3 扫参，降本策略：S1 两点快筛→S2 入围复核）。默认关闭。",
    )
    ap.add_argument("--round15-bootstrap-iters", type=int, default=2000, help="Round1.5 的 bootstrap 次数")
    ap.add_argument("--round15-seed", type=int, default=42, help="Round1.5 的 bootstrap 随机种子")
    ap.add_argument("--round15-top-k", type=int, default=15, help="Round1.5 S1 入围后进入 S2 的最多参数数")
    ap.add_argument("--round15-min-p", type=float, default=0.8, help="Round1.5 S1 入围阈值：p_improve")
    ap.add_argument("--round15-min-delta", type=float, default=0.2, help="Round1.5 S1 入围阈值：delta_e2e")
    ap.add_argument(
        "--round15-max-runtime-s",
        type=int,
        default=0,
        help="Round1.5 总体硬超时（秒，0=关闭）。超时会终止扫参脚本并继续写 final 状态。",
    )
    ap.add_argument(
        "--progress-json",
        type=Path,
        default=Path("/app/outputs/eval/round1_onejob_progress.json"),
        help="进度文件（可 tail 监控）",
    )
    ap.add_argument(
        "--log-file",
        type=Path,
        default=Path("/app/outputs/eval/round1_onejob.log"),
        help="长任务总日志（append）",
    )
    ap.add_argument(
        "--start-from",
        type=str,
        default="",
        help="从某个 stage.key 开始（用于跳过已完成阶段），例如 asr_ablation / a_independent / qe_suite",
    )
    args = ap.parse_args()

    # Ensure unbuffered prints (progress is monitored externally).
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    stages: List[Stage] = [
        Stage(
            key="asr_ablation",
            label_cn="ASR 单开关消融（golden9，全流程）",
            segments=Path("/app/eval/e2e_quality/segments_golden_9.docker.jsonl"),
            experiments=Path("/app/eval/e2e_quality/experiments.round1_asr.yaml"),
            base_config=Path("/app/config/quality_asr_ablation.yaml"),
            out_root=Path("/app/outputs/eval/e2e_quality_golden9_asr_ablation"),
            report_json=Path("/app/reports/e2e_quality/report_golden9_round1_asr_ablation.json"),
            report_md=Path("/app/reports/e2e_quality/report_golden9_round1_asr_ablation.md"),
        ),
        Stage(
            key="a_independent",
            label_cn="A类剩余单开关（golden9）",
            segments=Path("/app/eval/e2e_quality/segments_golden_9.docker.jsonl"),
            experiments=Path("/app/eval/e2e_quality/experiments.round1_a_independent.yaml"),
            base_config=Path("/app/config/quality.yaml"),
            out_root=Path("/app/outputs/eval/e2e_quality_golden9_round1_a_independent"),
            report_json=Path("/app/reports/e2e_quality/report_golden9_round1_a_independent.json"),
            report_md=Path("/app/reports/e2e_quality/report_golden9_round1_a_independent.md"),
        ),
        Stage(
            key="qe_suite",
            label_cn="QE 套件（qe_base + 子开关）",
            segments=Path("/app/eval/e2e_quality/segments_golden_9.docker.jsonl"),
            experiments=Path("/app/eval/e2e_quality/experiments.round1_qe_suite.yaml"),
            base_config=Path("/app/config/quality.yaml"),
            out_root=Path("/app/outputs/eval/e2e_quality_golden9_round1_qe_suite"),
            report_json=Path("/app/reports/e2e_quality/report_golden9_round1_qe_suite.json"),
            report_md=Path("/app/reports/e2e_quality/report_golden9_round1_qe_suite.md"),
        ),
        Stage(
            key="tra_suite",
            label_cn="TRA 套件（tra_qe_base + 子开关）",
            segments=Path("/app/eval/e2e_quality/segments_golden_9.docker.jsonl"),
            experiments=Path("/app/eval/e2e_quality/experiments.round1_tra_suite.yaml"),
            base_config=Path("/app/config/quality.yaml"),
            out_root=Path("/app/outputs/eval/e2e_quality_golden9_round1_tra_suite"),
            report_json=Path("/app/reports/e2e_quality/report_golden9_round1_tra_suite.json"),
            report_md=Path("/app/reports/e2e_quality/report_golden9_round1_tra_suite.md"),
        ),
        Stage(
            key="tts_script_suite",
            label_cn="TTS 朗读稿套件（tts_script_base + strict_clean）",
            segments=Path("/app/eval/e2e_quality/segments_golden_9.docker.jsonl"),
            experiments=Path("/app/eval/e2e_quality/experiments.round1_tts_script_suite.yaml"),
            base_config=Path("/app/config/quality.yaml"),
            out_root=Path("/app/outputs/eval/e2e_quality_golden9_round1_tts_script_suite"),
            report_json=Path("/app/reports/e2e_quality/report_golden9_round1_tts_script_suite.json"),
            report_md=Path("/app/reports/e2e_quality/report_golden9_round1_tts_script_suite.md"),
        ),
        Stage(
            key="b_display_short3",
            label_cn="B类：展示字幕（short3）",
            segments=Path("/app/eval/e2e_quality/segments_short3.docker.jsonl"),
            experiments=Path("/app/eval/e2e_quality/experiments.round1_display_suite_short3.yaml"),
            base_config=Path("/app/config/quality.yaml"),
            out_root=Path("/app/outputs/eval/e2e_quality_short3_display_suite"),
            report_json=Path("/app/reports/e2e_quality/report_short3_round1_display_suite.json"),
            report_md=Path("/app/reports/e2e_quality/report_short3_round1_display_suite.md"),
        ),
        Stage(
            key="b_bgm_short3",
            label_cn="B类：混音/BGM（short3）",
            segments=Path("/app/eval/e2e_quality/segments_short3.docker.jsonl"),
            experiments=Path("/app/eval/e2e_quality/experiments.round1_bgm_suite_short3.yaml"),
            base_config=Path("/app/config/quality.yaml"),
            out_root=Path("/app/outputs/eval/e2e_quality_short3_bgm_suite"),
            report_json=Path("/app/reports/e2e_quality/report_short3_round1_bgm_suite.json"),
            report_md=Path("/app/reports/e2e_quality/report_short3_round1_bgm_suite.md"),
        ),
        Stage(
            key="b_erase_short3",
            label_cn="B类：硬字幕擦除（short3）",
            segments=Path("/app/eval/e2e_quality/segments_short3.docker.jsonl"),
            experiments=Path("/app/eval/e2e_quality/experiments.round1_erase_suite_short3.yaml"),
            base_config=Path("/app/config/quality.yaml"),
            out_root=Path("/app/outputs/eval/e2e_quality_short3_erase_suite"),
            report_json=Path("/app/reports/e2e_quality/report_short3_round1_erase_suite.json"),
            report_md=Path("/app/reports/e2e_quality/report_short3_round1_erase_suite.md"),
        ),
    ]

    start_from = str(args.start_from or "").strip()
    if start_from:
        keys = [s.key for s in stages]
        if start_from not in keys:
            raise SystemExit(f"--start-from invalid: {start_from}. allowed: {keys}")
        stages = stages[keys.index(start_from) :]

    progress_path: Path = Path(args.progress_json)
    log_path: Path = Path(args.log_file)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    for stage in stages:
        # Wait if we detect an active run writing into the same output root.
        exp_cfg = _load_yaml(stage.experiments)
        base_name = str(((exp_cfg.get("baseline") or {}) or {}).get("name") or "baseline")
        base_run_root = stage.out_root / base_name
        while _detect_active_run(base_run_root):
            prog = {
                "ts": _now_iso(),
                "stage": stage.key,
                "stage_cn": stage.label_cn,
                "status": "waiting_active_run",
                "hint_cn": "检测到该阶段已有正在运行的进程在写 outputs；等待其结束以避免并发写入。",
                "detail": _stage_progress(stage.out_root, stage.experiments, stage.segments),
            }
            progress_path.write_text(json.dumps(prog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            time.sleep(max(5, int(args.poll_s)))

        # Update progress before running (or resuming).
        progress_path.write_text(
            json.dumps(
                {
                    "ts": _now_iso(),
                    "stage": stage.key,
                    "stage_cn": stage.label_cn,
                    "status": "starting_or_resuming",
                    "detail": _stage_progress(stage.out_root, stage.experiments, stage.segments),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # Run (resume automatically via run_quality_e2e's per-segment done check),
        # and keep progress.json refreshing even when a single segment takes long.
        base_cmd = (
            f"{sys.executable} /app/scripts/run_quality_e2e.py "
            f"--segments {stage.segments} "
            f"--experiments {stage.experiments} "
            f"--base-config {stage.base_config} "
            f"--out-root {stage.out_root} "
            f"--jobs 1 "
            f"--seg-stall-timeout-s {int(args.seg_stall_timeout_s)} "
            f"--seg-stall-check-s {int(args.seg_stall_check_s)} "
            f"--seg-stall-cpu-min-jiffies {int(args.seg_stall_cpu_min_jiffies)} "
            f"--seg-max-runtime-s {int(args.seg_max_runtime_s)}"
        )
        rc = _run_cmd_string_with_live_progress(
            cmd_str=base_cmd,
            log_path=log_path,
            progress_path=progress_path,
            stage=stage,
            poll_s=int(args.poll_s),
            status_running="running",
        )

        # After run, compute progress snapshot.
        snap = _stage_progress(stage.out_root, stage.experiments, stage.segments)
        if snap.get("done"):
            status = "done"
        else:
            # Continue to next stages even when some segments fail; failures are recorded in progress.json.
            status = "done_with_failures"
        prog1 = {
            "ts": _now_iso(),
            "stage": stage.key,
            "stage_cn": stage.label_cn,
            "status": status,
            "return_code": rc,
            "detail": snap,
        }
        progress_path.write_text(json.dumps(prog1, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Retry failed segments a few rounds (best-effort). This helps transient ollama timeouts.
        retry_rounds = max(0, int(getattr(args, "retry_rounds", 0) or 0))
        retry_sleep_s = max(0, int(getattr(args, "retry_sleep_s", 0) or 0))
        for r in range(retry_rounds):
            snap2 = _stage_progress(stage.out_root, stage.experiments, stage.segments)
            if snap2.get("done"):
                break
            progress_path.write_text(
                json.dumps(
                    {
                        "ts": _now_iso(),
                        "stage": stage.key,
                        "stage_cn": stage.label_cn,
                        "status": "retrying_failed_segments",
                        "retry_round": r + 1,
                        "retry_rounds": retry_rounds,
                        "sleep_s": retry_sleep_s,
                        "detail": snap2,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            if retry_sleep_s > 0:
                time.sleep(float(retry_sleep_s))
            _ = _run_cmd_string_with_live_progress(
                cmd_str=base_cmd,
                log_path=log_path,
                progress_path=progress_path,
                stage=stage,
                poll_s=int(args.poll_s),
                status_running="running_retry_round",
            )

        # Evaluate & write reports (best-effort; does not block future resume).
        try:
            stage.eval_timeout_s = int(getattr(args, "eval_max_runtime_s", 0) or 0)
            _eval_stage(stage, bootstrap_iters=int(args.bootstrap_iters), log_path=log_path)
        except Exception as exc:
            err = {
                "ts": _now_iso(),
                "stage": stage.key,
                "stage_cn": stage.label_cn,
                "status": "eval_failed",
                "error": str(exc)[:400],
            }
            progress_path.write_text(json.dumps(err, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Do NOT stop on failures; keep going so user gets the full matrix in one long job.
        # Resume semantics still hold: rerunning the job will skip passed segments and retry pending ones.

    # Optional: Round1.5 parameter sweep on short3 (cost-reduction strategy).
    if bool(getattr(args, "round15_enable", False)):
        try:
            progress_path.write_text(
                json.dumps(
                    {
                        "ts": _now_iso(),
                        "stage": "round15_param_sweep",
                        "stage_cn": "Round1.5 扫参（short3，降本：S1→S2）",
                        "status": "starting",
                        "hint_cn": "开始执行 Round1.5 参数扫参。输出将写入 outputs/eval 与 reports/e2e_quality。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            cmd = [
                sys.executable,
                "/app/scripts/run_round15_param_sweep.py",
                "--doc",
                "/app/docs/质量模式配置项测试流程.md",
                "--segments",
                "/app/eval/e2e_quality/segments_short3.docker.jsonl",
                "--base-config",
                "/app/config/quality.yaml",
                "--bootstrap-iters",
                str(int(getattr(args, "round15_bootstrap_iters", 2000) or 2000)),
                "--seed",
                str(int(getattr(args, "round15_seed", 42) or 42)),
                "--top-k",
                str(int(getattr(args, "round15_top_k", 15) or 15)),
                "--min-p",
                str(float(getattr(args, "round15_min_p", 0.8) or 0.8)),
                "--min-delta",
                str(float(getattr(args, "round15_min_delta", 0.2) or 0.2)),
                "--jobs",
                "1",
            ]
            rc = _run_and_tee(cmd, log_path, timeout_s=int(getattr(args, "round15_max_runtime_s", 0) or 0), purpose="round15_param_sweep")
            progress_path.write_text(
                json.dumps(
                    {
                        "ts": _now_iso(),
                        "stage": "round15_param_sweep",
                        "stage_cn": "Round1.5 扫参（short3，降本：S1→S2）",
                        "status": "done" if int(rc) == 0 else "done_with_failures",
                        "return_code": int(rc),
                        "hint_cn": "Round1.5 已结束。请查看 reports/e2e_quality/report_short3_round15_s1.* 与 report_short3_round15_s2.*。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            progress_path.write_text(
                json.dumps(
                    {
                        "ts": _now_iso(),
                        "stage": "round15_param_sweep",
                        "stage_cn": "Round1.5 扫参（short3，降本：S1→S2）",
                        "status": "failed",
                        "error": str(exc)[:400],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    final = {"ts": _now_iso(), "status": "all_done", "hint_cn": "全部阶段完成。可查看 reports/e2e_quality 下的报告（round1 + round1.5）。"}
    progress_path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()


