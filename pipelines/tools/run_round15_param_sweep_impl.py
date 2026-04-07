#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Round1.5：可调参数扫参（short3），按“降本”策略自动执行：

- S1：两点快筛（baseline vs A），覆盖 4.5.2 清单里的全部“可三点扫参”项
- S2：入围 Top-K 做复核（baseline vs P + baseline vs A）

候选点来源（单一事实来源）：docs/质量模式配置项测试流程.md -> 4.5.2

输出：
- outputs/eval/e2e_quality_short3_round15_s1/  (baseline + s1_* runs)
- outputs/eval/e2e_quality_short3_round15_s2/  (baseline + s2_* runs)
- reports/e2e_quality/report_short3_round15_s1.{json,md}
- reports/e2e_quality/report_short3_round15_s2.{json,md}

备注：
- baseline 默认只使用 --base-config（不额外开启任何开关）。某些参数在对应功能未开启时会“无效果”，这是预期现象。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from scripts.eval_quality_e2e_suite import bootstrap_prob_improve, e2e_score_from_quality_report


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


def _safe_run_name(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:120] if len(s) > 120 else s


def _parse_value(tok: str) -> Any:
    t = tok.strip()
    if not t:
        return t
    # int / float
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return float(t)
    return t


@dataclass
class ParamCandidate:
    key: str
    desc_cn: str
    cli: str
    stage: str
    points: List[Any]  # usually [P,D,A] or [P,A]

    def p(self) -> Optional[Any]:
        if len(self.points) >= 3:
            return self.points[0]
        if len(self.points) == 2:
            return self.points[0]
        return None

    def d(self) -> Optional[Any]:
        if len(self.points) >= 3:
            return self.points[1]
        if len(self.points) == 2:
            return self.points[0]
        return None

    def a(self) -> Optional[Any]:
        if len(self.points) >= 3:
            return self.points[2]
        if len(self.points) == 2:
            return self.points[1]
        if len(self.points) == 1:
            return self.points[0]
        return None


def load_candidates_from_doc(doc_path: Path) -> List[ParamCandidate]:
    txt = doc_path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"#### 4\.5\.2[\s\S]*?(?=#### 4\.5\.3)", txt)
    if not m:
        raise SystemExit("Cannot find section 4.5.2 in docs/质量模式配置项测试流程.md")
    sec = m.group(0)

    stage = "UNKNOWN"
    out: List[ParamCandidate] = []

    stage_re = re.compile(r"^#####\s+(?P<stage>.+?)\s*$")
    # Allow backticks around CLI and tolerate extra spaces.
    # Allow trailing notes after the bold P/D/A block, e.g. "**P/D/A：1 / 2 / 3**（注：...）"
    line_re = re.compile(
        r"^\-\s+`(?P<key>[^`]+)`：(?P<desc>.+?)（CLI：(?P<cli>[^）]+)）→\s+\*\*P/D/A：(?P<pts>.+?)\*\*(?:\s*.*)?$"
    )

    for raw in sec.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        sm = stage_re.match(ln)
        if sm:
            stage = sm.group("stage").strip()
            continue
        mm = line_re.match(ln)
        if not mm:
            continue
        key = mm.group("key").strip()
        desc = mm.group("desc").strip()
        cli = mm.group("cli").strip()
        pts_raw = [x.strip() for x in mm.group("pts").split("/") if x.strip()]
        points = [_parse_value(x) for x in pts_raw]
        out.append(ParamCandidate(key=key, desc_cn=desc, cli=cli, stage=stage, points=points))

    if not out:
        raise SystemExit("Parsed 0 candidates from doc section 4.5.2 (format mismatch).")
    return out


def write_experiments_yaml(path: Path, *, baseline_name: str, experiments: Dict[str, Dict[str, Any]]) -> None:
    cfg: Dict[str, Any] = {
        "baseline": {"name": baseline_name, "overrides": {}},
        "experiments": {k: {"overrides": v} for k, v in experiments.items()},
        "reuse": {"freeze_asr_for_non_asr_experiments": True, "method": "baseline_asr"},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_scores(segments: List[Dict[str, Any]], run_dir: Path) -> Tuple[List[float], List[Dict[str, Any]]]:
    scores: List[float] = []
    rows: List[Dict[str, Any]] = []
    for s in segments:
        sid = str(s.get("id") or "").strip()
        wd = run_dir / sid
        rep_p = wd / "quality_report.json"
        rep = (
            _read_json(rep_p)
            if rep_p.exists()
            else {
                "passed": False,
                "errors": [f"missing {rep_p}"],
                "checks": {"required_artifacts": {"missing": ["quality_report.json"]}},
            }
        )
        m = e2e_score_from_quality_report(rep)
        scores.append(float(m["e2e_score_100"]))
        rows.append({"id": sid, "work_dir": str(wd), **m})
    return scores, rows


def _mean(xs: List[float]) -> float:
    return float(sum(xs) / max(1, len(xs)))


def eval_runs(
    *,
    segments: List[Dict[str, Any]],
    baseline_dir: Path,
    run_map: Dict[str, Path],
    bootstrap_iters: int,
    seed: int,
) -> Dict[str, Any]:
    base_scores, base_rows = load_scores(segments, baseline_dir)
    out: Dict[str, Any] = {
        "baseline": {
            "n": len(base_scores),
            "passed_rate": round(sum(1 for r in base_rows if r.get("passed")) / float(max(1, len(base_rows))), 4),
            "e2e_score_100_mean": round(_mean(base_scores), 2),
            "per_segment": base_rows,
        },
        "runs": {},
    }
    for name, p in run_map.items():
        scores, rows = load_scores(segments, p)
        out["runs"][name] = {
            "n": len(scores),
            "passed_rate": round(sum(1 for r in rows if r.get("passed")) / float(max(1, len(rows))), 4),
            "e2e_score_100_mean": round(_mean(scores), 2),
            "bootstrap": bootstrap_prob_improve(base_scores, scores, iters=int(bootstrap_iters), seed=int(seed)),
            "per_segment": rows,
        }
    return out


def write_md(path: Path, *, title: str, baseline: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- baseline: passed_rate={baseline.get('passed_rate')} e2e_mean={baseline.get('e2e_score_100_mean')}")
    lines.append("")
    lines.append("| rank | key | stage | cli | point | value | delta_e2e | p_improve | passed_rate | e2e_mean | note |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(rows, start=1):
        lines.append(
            f"| {i} | `{r.get('key')}` | {r.get('stage')} | {r.get('cli')} | {r.get('point')} | {r.get('value')} | {r.get('delta_e2e')} | {r.get('p_improve')} | {r.get('passed_rate')} | {r.get('e2e_mean')} | {r.get('note','')} |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Round1.5 parameter sweep (short3) with cost-reduction strategy.")
    ap.add_argument("--doc", type=Path, default=Path("/app/docs/质量模式配置项测试流程.md"))
    ap.add_argument("--segments", type=Path, default=Path("/app/eval/e2e_quality/segments_short3.docker.jsonl"))
    ap.add_argument("--base-config", type=Path, default=Path("/app/config/quality.yaml"))
    ap.add_argument("--bootstrap-iters", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--top-k", type=int, default=15)
    ap.add_argument("--min-p", type=float, default=0.8)
    ap.add_argument("--min-delta", type=float, default=0.2)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out-root-s1", type=Path, default=Path("/app/outputs/eval/e2e_quality_short3_round15_s1"))
    ap.add_argument("--out-root-s2", type=Path, default=Path("/app/outputs/eval/e2e_quality_short3_round15_s2"))
    ap.add_argument("--report-json-s1", type=Path, default=Path("/app/reports/e2e_quality/report_short3_round15_s1.json"))
    ap.add_argument("--report-md-s1", type=Path, default=Path("/app/reports/e2e_quality/report_short3_round15_s1.md"))
    ap.add_argument("--report-json-s2", type=Path, default=Path("/app/reports/e2e_quality/report_short3_round15_s2.json"))
    ap.add_argument("--report-md-s2", type=Path, default=Path("/app/reports/e2e_quality/report_short3_round15_s2.md"))
    args = ap.parse_args()

    cands = load_candidates_from_doc(Path(args.doc))
    # Guardrail: exclude placeholder / non-effective keys so Round1.5 never wastes compute.
    # NOTE: `diarization` is currently parsed/passed through but not used in `scripts/quality_pipeline.py::run_whisperx`.
    # Also exclude dictionary/data-driven knobs (glossary/dict/path driven): they do not have stable, reusable
    # conclusions unless the data file is version-pinned and treated as part of the test input.
    EXCLUDE_KEYS = {
        "diarization",
        "asr_normalize_dict",
        "glossary_placeholder_max",
        "tts_backend",
        "piper_model",
        "piper_bin",
    }
    cands = [c for c in cands if str(getattr(c, "key", "") or "").strip() not in EXCLUDE_KEYS]
    segments = _read_jsonl(Path(args.segments))
    segments = [s for s in segments if isinstance(s, dict) and str(s.get("id") or "").strip()]
    if not segments:
        raise SystemExit("segments is empty")

    # S1: baseline vs A
    s1_exp: Dict[str, Dict[str, Any]] = {}
    s1_meta: Dict[str, Dict[str, Any]] = {}
    for c in cands:
        a = c.a()
        if a is None:
            continue
        run = _safe_run_name("s1_%s_A" % c.key)
        s1_exp[run] = {c.key: a}
        s1_meta[run] = {"key": c.key, "stage": c.stage, "cli": c.cli, "desc_cn": c.desc_cn, "point": "A", "value": a}

    exp_s1 = Path(args.out_root_s1) / "_experiments.round15_s1.yaml"
    write_experiments_yaml(exp_s1, baseline_name="baseline", experiments=s1_exp)
    t0 = time.time()
    subprocess.check_call(
        [
            sys.executable,
            "/app/scripts/run_quality_e2e.py",
            "--segments",
            str(Path(args.segments)),
            "--experiments",
            str(exp_s1),
            "--base-config",
            str(Path(args.base_config)),
            "--out-root",
            str(Path(args.out_root_s1)),
            "--jobs",
            str(int(args.jobs)),
        ]
    )

    base_dir_s1 = Path(args.out_root_s1) / "baseline"
    run_map_s1 = {name: Path(args.out_root_s1) / name for name in s1_exp.keys()}
    s1_eval = eval_runs(segments=segments, baseline_dir=base_dir_s1, run_map=run_map_s1, bootstrap_iters=int(args.bootstrap_iters), seed=int(args.seed))
    s1_eval["meta"] = {"strategy": "S1 baseline vs A", "seconds": round(time.time() - t0, 2), "runs_n": len(run_map_s1)}
    s1_eval["run_meta"] = s1_meta
    Path(args.report_json_s1).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_json_s1).write_text(json.dumps(s1_eval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    base_mean = float((s1_eval.get("baseline") or {}).get("e2e_score_100_mean") or 0.0)
    base_pass = float((s1_eval.get("baseline") or {}).get("passed_rate") or 0.0)
    scored_s1: List[Dict[str, Any]] = []
    for run_name, rr in (s1_eval.get("runs") or {}).items():
        meta = s1_meta.get(run_name) or {}
        e2e_mean = float(rr.get("e2e_score_100_mean") or 0.0)
        passed_rate = float(rr.get("passed_rate") or 0.0)
        delta = round(e2e_mean - base_mean, 3)
        p = float(((rr.get("bootstrap") or {}) or {}).get("p_improve") or 0.0)
        note = "" if (passed_rate + 1e-9) >= base_pass else "drop_passed"
        scored_s1.append(
            {
                "run": run_name,
                "key": meta.get("key"),
                "stage": meta.get("stage"),
                "cli": meta.get("cli"),
                "point": meta.get("point"),
                "value": meta.get("value"),
                "delta_e2e": delta,
                "p_improve": round(p, 6),
                "passed_rate": round(passed_rate, 4),
                "e2e_mean": round(e2e_mean, 2),
                "note": note,
            }
        )
    scored_s1.sort(key=lambda x: (x.get("note") == "", float(x.get("delta_e2e") or 0.0), float(x.get("p_improve") or 0.0)), reverse=True)
    write_md(Path(args.report_md_s1), title="Round1.5 S1（short3 两点快筛）", baseline=(s1_eval.get("baseline") or {}), rows=scored_s1[:50])

    # Select Top-K for S2 (only those not dropping passed_rate)
    finalists: List[Dict[str, Any]] = []
    for r in scored_s1:
        if r.get("note"):
            continue
        if float(r.get("p_improve") or 0.0) < float(args.min_p):
            continue
        if float(r.get("delta_e2e") or 0.0) < float(args.min_delta):
            continue
        finalists.append(r)
        if len(finalists) >= max(0, int(args.top_k)):
            break

    if not finalists:
        Path(args.report_json_s2).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_json_s2).write_text(json.dumps({"status": "skipped", "reason": "no finalists"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        Path(args.report_md_s2).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_md_s2).write_text("# Round1.5 S2\n\n(no finalists)\n", encoding="utf-8")
        print("[ok] S2 skipped: no finalists")
        return

    key2cand = {c.key: c for c in cands}
    s2_exp: Dict[str, Dict[str, Any]] = {}
    s2_meta: Dict[str, Dict[str, Any]] = {}
    for r in finalists:
        key = str(r.get("key") or "")
        c = key2cand.get(key)
        if not c:
            continue
        p_val = c.p()
        a_val = c.a()
        if p_val is not None:
            runp = _safe_run_name("s2_%s_P" % key)
            s2_exp[runp] = {key: p_val}
            s2_meta[runp] = {"key": key, "stage": c.stage, "cli": c.cli, "desc_cn": c.desc_cn, "point": "P", "value": p_val}
        if a_val is not None:
            runa = _safe_run_name("s2_%s_A" % key)
            s2_exp[runa] = {key: a_val}
            s2_meta[runa] = {"key": key, "stage": c.stage, "cli": c.cli, "desc_cn": c.desc_cn, "point": "A", "value": a_val}

    exp_s2 = Path(args.out_root_s2) / "_experiments.round15_s2.yaml"
    write_experiments_yaml(exp_s2, baseline_name="baseline", experiments=s2_exp)
    t1 = time.time()
    subprocess.check_call(
        [
            sys.executable,
            "/app/scripts/run_quality_e2e.py",
            "--segments",
            str(Path(args.segments)),
            "--experiments",
            str(exp_s2),
            "--base-config",
            str(Path(args.base_config)),
            "--out-root",
            str(Path(args.out_root_s2)),
            "--jobs",
            str(int(args.jobs)),
        ]
    )

    base_dir_s2 = Path(args.out_root_s2) / "baseline"
    run_map_s2 = {name: Path(args.out_root_s2) / name for name in s2_exp.keys()}
    s2_eval = eval_runs(segments=segments, baseline_dir=base_dir_s2, run_map=run_map_s2, bootstrap_iters=int(args.bootstrap_iters), seed=int(args.seed))
    s2_eval["meta"] = {
        "strategy": "S2 finalists P/A",
        "seconds": round(time.time() - t1, 2),
        "runs_n": len(run_map_s2),
        "selected_from_s1": finalists,
    }
    s2_eval["run_meta"] = s2_meta
    Path(args.report_json_s2).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_json_s2).write_text(json.dumps(s2_eval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    base_mean2 = float((s2_eval.get("baseline") or {}).get("e2e_score_100_mean") or 0.0)
    base_pass2 = float((s2_eval.get("baseline") or {}).get("passed_rate") or 0.0)
    scored_s2: List[Dict[str, Any]] = []
    for run_name, rr in (s2_eval.get("runs") or {}).items():
        meta = s2_meta.get(run_name) or {}
        e2e_mean = float(rr.get("e2e_score_100_mean") or 0.0)
        passed_rate = float(rr.get("passed_rate") or 0.0)
        delta = round(e2e_mean - base_mean2, 3)
        p = float(((rr.get("bootstrap") or {}) or {}).get("p_improve") or 0.0)
        note = "" if (passed_rate + 1e-9) >= base_pass2 else "drop_passed"
        scored_s2.append(
            {
                "run": run_name,
                "key": meta.get("key"),
                "stage": meta.get("stage"),
                "cli": meta.get("cli"),
                "point": meta.get("point"),
                "value": meta.get("value"),
                "delta_e2e": delta,
                "p_improve": round(p, 6),
                "passed_rate": round(passed_rate, 4),
                "e2e_mean": round(e2e_mean, 2),
                "note": note,
            }
        )
    scored_s2.sort(key=lambda x: (x.get("note") == "", float(x.get("delta_e2e") or 0.0), float(x.get("p_improve") or 0.0)), reverse=True)
    write_md(Path(args.report_md_s2), title="Round1.5 S2（short3 入围项复核）", baseline=(s2_eval.get("baseline") or {}), rows=scored_s2[:50])
    print("[ok] wrote %s and %s" % (str(args.report_json_s1), str(args.report_json_s2)))


if __name__ == "__main__":
    main()


