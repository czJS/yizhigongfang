#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pipelines.lib.text.srt_io import read_srt_texts_ordered


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _resolve_repo_path(path_like: str | Path, *, repo_root: Path) -> Path:
    p = Path(str(path_like))
    if p.is_absolute():
        try:
            rel = p.relative_to("/app")
            return (repo_root / rel).resolve()
        except Exception:
            return p.resolve()
    return (repo_root / p).resolve()


def _normalize_text(text: Any) -> str:
    return " ".join(str(text or "").strip().split())


def _hint_candidate_sentences(base: str, hints: Sequence[str]) -> List[str]:
    base_s = _normalize_text(base)
    out: List[str] = []
    for hint in hints or []:
        s = _normalize_text(hint)
        if "->" not in s:
            continue
        src, tgt = s.split("->", 1)
        src_s = _normalize_text(src)
        tgt_s = _normalize_text(tgt)
        if not src_s or not tgt_s or src_s == tgt_s or src_s not in base_s:
            continue
        cand = base_s.replace(src_s, tgt_s, 1)
        if cand and cand != base_s and cand not in out:
            out.append(cand)
    return out


def _severity_rank(severity: str) -> int:
    s = _normalize_text(severity)
    if s == "high":
        return 0
    if s == "medium":
        return 1
    return 2


def _primary_error_type(rule_reasons: Sequence[str]) -> str:
    reasons = {_normalize_text(r) for r in (rule_reasons or []) if _normalize_text(r)}
    if "疑似专名/称谓一致性" in reasons:
        return "专名/称谓"
    if reasons & {"疑似不通顺搭配", "疑似动宾搭配异常", "疑似动词缺失/错置"}:
        return "搭配/语法"
    if "乱码/异常字符" in reasons:
        return "乱码/异常字符"
    if "项目混淆命中" in reasons:
        return "项目混淆"
    if "疑似ASR脏词/生造词" in reasons:
        return "疑似脏词/生造词"
    if "重复标点" in reasons:
        return "重复标点"
    if reasons & {"文本极短但时长较长", "文本较长但时长较短"}:
        return "时长/切分异常"
    if "短句但含异常词" in reasons:
        return "短句异常"
    return "其他"


def _discover_default_report_roots(repo_root: Path) -> List[Path]:
    base = repo_root / "reports" / "lite_phase1"
    if not base.exists():
        return []
    out: List[Path] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        if not child.name.startswith("asr_stage1_cn20_eval") or not child.name.endswith("_hostcopy"):
            continue
        if (child / "summary.json").exists():
            out.append(child)
    return out


def _load_case_manifest(cases_manifest: Path, *, repo_root: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in _read_jsonl(cases_manifest):
        case_id = _normalize_text(row.get("id"))
        if not case_id:
            continue
        ref_srt = _normalize_text(row.get("chs_srt"))
        out[case_id] = {
            "id": case_id,
            "meta": dict(row.get("meta") or {}),
            "reference_srt": _resolve_repo_path(ref_srt, repo_root=repo_root) if ref_srt else None,
        }
    return out


def _extract_local_and_candidates(item: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    base = _normalize_text(item.get("after_glossary") or item.get("before") or "")
    local = base
    candidate_map: Dict[str, str] = {}
    for cand in item.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        source = _normalize_text(cand.get("source"))
        text = _normalize_text(cand.get("text"))
        if not text or text == base or source == "base":
            continue
        if source == "local":
            local = text
        candidate_map.setdefault(text, source or "candidate")
    for option in item.get("repair_options") or []:
        if not isinstance(option, dict):
            continue
        text = _normalize_text(option.get("text"))
        source = _normalize_text(option.get("source")) or "repair_option"
        if text and text != base:
            candidate_map.setdefault(text, source)
            if source == "local":
                local = text
    for text in _hint_candidate_sentences(base, item.get("local_hints") or []):
        candidate_map.setdefault(text, "hint")
    candidates = [{"text": text, "source": source} for text, source in sorted(candidate_map.items(), key=lambda kv: (kv[1], kv[0]))]
    return local, candidates


def _build_reference_lookup(report_items: Sequence[Dict[str, Any]], reference_lines: Sequence[str]) -> Dict[int, Dict[str, str]]:
    out: Dict[int, Dict[str, str]] = {}
    report_lines = [_normalize_text(item.get("after_glossary") or item.get("before") or "") for item in report_items]
    for idx, _item in enumerate(report_items, start=1):
        prev_base = report_lines[idx - 2] if idx > 1 else ""
        next_base = report_lines[idx] if idx < len(report_items) else ""
        optimal = _normalize_text(reference_lines[idx - 1]) if idx - 1 < len(reference_lines) else ""
        out[idx] = {
            "prev_base": prev_base,
            "next_base": next_base,
            "optimal": optimal,
        }
    return out


def build_stage1_offline_rows(
    *,
    report_roots: Sequence[Path],
    cases_manifest: Path,
    repo_root: Optional[Path] = None,
    max_items: int = 200,
) -> List[Dict[str, Any]]:
    root = repo_root or _repo_root()
    case_manifest = _load_case_manifest(cases_manifest, repo_root=root)
    merged: Dict[Tuple[str, int, str], Dict[str, Any]] = {}

    for report_root in report_roots:
        report_root = Path(report_root)
        if not report_root.exists():
            continue
        report_label = report_root.name
        for report_path in sorted(report_root.glob("*/asr_stage1_report.json")):
            case_id = report_path.parent.name
            report = _read_json(report_path)
            items = list(report.get("items") or [])
            case_meta = case_manifest.get(case_id) or {"id": case_id, "meta": {}, "reference_srt": None}
            ref_lines: List[str] = []
            ref_srt = case_meta.get("reference_srt")
            if isinstance(ref_srt, Path) and ref_srt.exists():
                ref_lines = read_srt_texts_ordered(ref_srt)
            ref_lookup = _build_reference_lookup(items, ref_lines)
            for item in items:
                if not isinstance(item, dict):
                    continue
                idx = int(item.get("idx") or 0)
                base = _normalize_text(item.get("after_glossary") or item.get("before") or "")
                if idx <= 0 or not base:
                    continue
                reasons = [_normalize_text(r) for r in (item.get("rule_reasons") or []) if _normalize_text(r)]
                route_tier = _normalize_text(item.get("route_tier"))
                if not reasons and route_tier not in {"soft", "hard"} and not item.get("llm_requested"):
                    continue
                local, candidates = _extract_local_and_candidates(item)
                ref = ref_lookup.get(idx) or {}
                optimal = _normalize_text(ref.get("optimal"))
                if not optimal:
                    continue
                key = (case_id, idx, base)
                row = merged.setdefault(
                    key,
                    {
                        "id": f"{case_id}:{idx}",
                        "case_id": case_id,
                        "line_idx": idx,
                        "base": base,
                        "local": local,
                        "candidates": [],
                        "optimal": optimal,
                        "error_type": _primary_error_type(reasons),
                        "rule_reasons": [],
                        "severity": _normalize_text(item.get("severity")),
                        "route_tier": route_tier,
                        "target_change": optimal != base,
                        "meta": dict(case_meta.get("meta") or {}),
                        "reference_srt": str(ref_srt) if ref_srt else "",
                        "prev_base": _normalize_text(ref.get("prev_base")),
                        "next_base": _normalize_text(ref.get("next_base")),
                        "report_sources": [],
                    },
                )
                if row["local"] == row["base"] and local != row["base"]:
                    row["local"] = local
                if _severity_rank(_normalize_text(item.get("severity"))) < _severity_rank(str(row.get("severity") or "")):
                    row["severity"] = _normalize_text(item.get("severity"))
                for reason in reasons:
                    if reason and reason not in row["rule_reasons"]:
                        row["rule_reasons"].append(reason)
                existing_candidates = {cand["text"]: cand["source"] for cand in row["candidates"]}
                for cand in candidates:
                    text = _normalize_text(cand.get("text"))
                    source = _normalize_text(cand.get("source"))
                    if text and text not in existing_candidates and text != row["base"]:
                        row["candidates"].append({"text": text, "source": source})
                        existing_candidates[text] = source
                if report_label not in row["report_sources"]:
                    row["report_sources"].append(report_label)

    rows = list(merged.values())
    rows.sort(
        key=lambda row: (
            0 if row.get("target_change") else 1,
            _severity_rank(str(row.get("severity") or "")),
            -len(row.get("rule_reasons") or []),
            -len(row.get("candidates") or []),
            str(row.get("case_id") or ""),
            int(row.get("line_idx") or 0),
        )
    )
    if max_items > 0:
        rows = rows[: max_items]
    return rows


def build_stage1_offline_summary(rows: Sequence[Dict[str, Any]], *, report_roots: Sequence[Path], cases_manifest: Path) -> Dict[str, Any]:
    error_type_counts: Dict[str, int] = defaultdict(int)
    severity_counts: Dict[str, int] = defaultdict(int)
    target_change = 0
    for row in rows:
        error_type_counts[str(row.get("error_type") or "其他")] += 1
        severity_counts[str(row.get("severity") or "low")] += 1
        if bool(row.get("target_change")):
            target_change += 1
    return {
        "dataset": "stage1_offline_cn20",
        "items": len(rows),
        "target_change_items": target_change,
        "keep_base_items": len(rows) - target_change,
        "error_type_counts": dict(sorted(error_type_counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "report_roots": [str(Path(p)) for p in report_roots],
        "cases_manifest": str(cases_manifest),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a Stage1 offline suspect set from existing CN20 Stage1 reports.")
    p.add_argument(
        "--cases-manifest",
        type=Path,
        default=Path("reports/lite_phase1/mt_cn20_dataset/mt_cases.jsonl"),
        help="Manifest with case id -> reference chs.srt mapping",
    )
    p.add_argument(
        "--report-root",
        dest="report_roots",
        action="append",
        type=Path,
        default=[],
        help="Hostcopy report root containing <case>/asr_stage1_report.json. Can be repeated.",
    )
    p.add_argument(
        "--output-jsonl",
        type=Path,
        default=Path("reports/stage1_offline/stage1_cn20_offline_set.jsonl"),
        help="Output JSONL path",
    )
    p.add_argument(
        "--output-summary",
        type=Path,
        default=Path("reports/stage1_offline/stage1_cn20_offline_set.summary.json"),
        help="Output summary JSON path",
    )
    p.add_argument("--max-items", type=int, default=200, help="Maximum number of rows to keep")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = _repo_root()
    report_roots = list(args.report_roots or []) or _discover_default_report_roots(repo_root)
    if not report_roots:
        raise SystemExit("No Stage1 CN20 report roots found. Pass --report-root explicitly.")
    rows = build_stage1_offline_rows(
        report_roots=report_roots,
        cases_manifest=_resolve_repo_path(args.cases_manifest, repo_root=repo_root),
        repo_root=repo_root,
        max_items=max(0, int(args.max_items or 0)),
    )
    summary = build_stage1_offline_summary(
        rows,
        report_roots=report_roots,
        cases_manifest=_resolve_repo_path(args.cases_manifest, repo_root=repo_root),
    )
    _write_jsonl(_resolve_repo_path(args.output_jsonl, repo_root=repo_root), rows)
    _write_json(_resolve_repo_path(args.output_summary, repo_root=repo_root), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
