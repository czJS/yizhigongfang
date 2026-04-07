#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.lib.asr.lite_asr import Segment
from pipelines.lib.asr.lite_asr_stage1 import (
    AsrStage1Options,
    _project_confusion_hits as lite_project_confusion_hits,
    _segment_rule_reasons,
    _severity_for_line,
)
import pipelines.quality_pipeline_impl as quality_pipeline_impl
AUTO_TEXT_KEYS = [
    "text",
    "student_zh",
    "example_student_zh",
    "chs",
    "zh",
    "optimized_pattern_wrong",
    "best_pattern_wrong",
    "pattern_wrong",
    "wrong",
]


def _read_json_or_jsonl(path: Path) -> List[Dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".jsonl":
        out: List[Dict[str, Any]] = []
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            obj = json.loads(s)
            if isinstance(obj, dict):
                out.append(obj)
        return out
    obj = json.loads(raw or "[]")
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        items = obj.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    raise ValueError(f"Unsupported corpus format: {path}")


def _load_asset_items(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "{}")
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict)]


def _preview(text: str, limit: int = 64) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def _extract_text_from_row(row: Dict[str, Any], text_key: str = "") -> str:
    if text_key:
        return str(row.get(text_key) or "").strip()
    for key in AUTO_TEXT_KEYS:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _load_corpus_lines(path: Path, *, text_key: str = "", max_lines: int = 0, dedupe: bool = False) -> List[str]:
    if path.suffix.lower() in {".txt", ".md"}:
        raw_lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()]
        lines = [ln for ln in raw_lines if ln]
    else:
        rows = _read_json_or_jsonl(path)
        lines = []
        for row in rows:
            text = _extract_text_from_row(row, text_key=text_key)
            if text:
                lines.append(text)
    if dedupe:
        seen = set()
        deduped: List[str] = []
        for line in lines:
            if line in seen:
                continue
            seen.add(line)
            deduped.append(line)
        lines = deduped
    if max_lines > 0:
        lines = lines[:max_lines]
    return lines


def _asset_type_counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    c = Counter()
    for item in items:
        c[str(item.get("type") or "unknown").strip() or "unknown"] += 1
    return dict(sorted(c.items(), key=lambda kv: kv[0]))


def _asset_candidate_map(items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for item in items:
        wrong = str(item.get("wrong") or "").strip()
        candidates = sorted({str(x).strip() for x in (item.get("candidates") or []) if str(x).strip()})
        if wrong:
            out[wrong] = candidates
    return out


def _compare_asset_catalog(before_items: List[Dict[str, Any]], after_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    before_map = _asset_candidate_map(before_items)
    after_map = _asset_candidate_map(after_items)
    before_wrongs = set(before_map)
    after_wrongs = set(after_map)
    changed_candidates = []
    for wrong in sorted(before_wrongs & after_wrongs):
        if before_map[wrong] != after_map[wrong]:
            changed_candidates.append(
                {
                    "wrong": wrong,
                    "before_candidates": before_map[wrong],
                    "after_candidates": after_map[wrong],
                }
            )
    return {
        "before_items": len(before_items),
        "after_items": len(after_items),
        "before_type_counts": _asset_type_counts(before_items),
        "after_type_counts": _asset_type_counts(after_items),
        "added_wrongs": sorted(after_wrongs - before_wrongs),
        "removed_wrongs": sorted(before_wrongs - after_wrongs),
        "changed_candidates": changed_candidates,
    }


def _severity_rank(name: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(str(name or "").strip(), 0)


def _eval_asr_direct(lines: List[str], asset_path: Path) -> Dict[str, Any]:
    line_hits = 0
    total_hits = 0
    hit_counter: Counter[str] = Counter()
    per_line: List[Dict[str, Any]] = []
    for line in lines:
        hits = lite_project_confusion_hits(line, path=asset_path)
        wrongs = [str(x.get("wrong") or "").strip() for x in hits if str(x.get("wrong") or "").strip()]
        if wrongs:
            line_hits += 1
            total_hits += len(wrongs)
            hit_counter.update(wrongs)
        per_line.append({"text": line, "wrongs": wrongs})
    return {
        "lines": len(lines),
        "lines_with_hits": line_hits,
        "total_hits": total_hits,
        "unique_wrongs_hit": len(hit_counter),
        "top_wrongs": hit_counter.most_common(20),
        "per_line": per_line,
    }


def _build_lite_opts(asset_path: Path, args: argparse.Namespace) -> AsrStage1Options:
    return AsrStage1Options(
        glossary_fix_enable=True,
        low_cost_clean_enable=True,
        badline_detect_enable=True,
        same_pinyin_path=args.same_pinyin_path,
        same_stroke_path=args.same_stroke_path,
        project_confusions_path=asset_path,
        lexicon_path=args.lexicon_path,
        proper_nouns_path=args.proper_nouns_path,
    )


def _eval_lite(lines: List[str], asset_path: Path, args: argparse.Namespace) -> Dict[str, Any]:
    opts = _build_lite_opts(asset_path, args)
    project_reason_lines = 0
    severity_counter: Counter[str] = Counter()
    per_line: List[Dict[str, Any]] = []
    for line in lines:
        seg = Segment(start=0.0, end=max(1.5, len(line) / 8.0), text=line)
        reasons = _segment_rule_reasons(seg, opts)
        severity = _severity_for_line(line, reasons, opts)
        if "项目混淆命中" in reasons:
            project_reason_lines += 1
        severity_counter[severity] += 1
        per_line.append({"text": line, "reasons": reasons, "severity": severity})
    return {
        "lines": len(lines),
        "lines_with_project_reason": project_reason_lines,
        "severity_counts": dict(sorted(severity_counter.items(), key=lambda kv: kv[0])),
        "per_line": per_line,
    }


def _eval_quality(lines: List[str], asset_path: Path) -> Dict[str, Any]:
    old_path = quality_pipeline_impl._DEFAULT_PROJECT_CONFUSIONS_PATH
    try:
        quality_pipeline_impl._DEFAULT_PROJECT_CONFUSIONS_PATH = str(asset_path)
        quality_pipeline_impl._PROJECT_CONFUSION_CACHE.clear()
        project_hit_lines = 0
        suspect_lines = 0
        project_reason_lines = 0
        hit_counter: Counter[str] = Counter()
        per_line: List[Dict[str, Any]] = []
        for line in lines:
            hits = quality_pipeline_impl._project_confusion_hits(line)
            wrongs = [str(x.get("wrong") or "").strip() for x in hits if str(x.get("wrong") or "").strip()]
            seg = quality_pipeline_impl.Segment(start=0.0, end=max(1.5, len(line) / 8.0), text=line)
            reasons = quality_pipeline_impl._rule_based_suspect(seg)
            if wrongs:
                project_hit_lines += 1
                hit_counter.update(wrongs)
            if reasons:
                suspect_lines += 1
            if "疑似项目高频混淆" in reasons:
                project_reason_lines += 1
            per_line.append({"text": line, "wrongs": wrongs, "reasons": reasons})
        return {
            "lines": len(lines),
            "lines_with_project_hits": project_hit_lines,
            "lines_with_project_reason": project_reason_lines,
            "suspect_lines": suspect_lines,
            "unique_wrongs_hit": len(hit_counter),
            "top_wrongs": hit_counter.most_common(20),
            "per_line": per_line,
        }
    finally:
        quality_pipeline_impl._DEFAULT_PROJECT_CONFUSIONS_PATH = old_path
        quality_pipeline_impl._PROJECT_CONFUSION_CACHE.clear()


def _diff_asr_direct(before: Dict[str, Any], after: Dict[str, Any], max_examples: int) -> Dict[str, Any]:
    added_examples: List[Dict[str, Any]] = []
    removed_examples: List[Dict[str, Any]] = []
    for b, a in zip(before["per_line"], after["per_line"]):
        bset = set(b["wrongs"])
        aset = set(a["wrongs"])
        if aset - bset and len(added_examples) < max_examples:
            added_examples.append(
                {
                    "text": _preview(a["text"]),
                    "added_wrongs": sorted(aset - bset),
                    "removed_wrongs": sorted(bset - aset),
                }
            )
        if bset - aset and len(removed_examples) < max_examples:
            removed_examples.append(
                {
                    "text": _preview(a["text"]),
                    "added_wrongs": sorted(aset - bset),
                    "removed_wrongs": sorted(bset - aset),
                }
            )
    return {
        "before_lines_with_hits": before["lines_with_hits"],
        "after_lines_with_hits": after["lines_with_hits"],
        "before_total_hits": before["total_hits"],
        "after_total_hits": after["total_hits"],
        "delta_lines_with_hits": after["lines_with_hits"] - before["lines_with_hits"],
        "delta_total_hits": after["total_hits"] - before["total_hits"],
        "added_examples": added_examples,
        "removed_examples": removed_examples,
        "before_top_wrongs": before["top_wrongs"],
        "after_top_wrongs": after["top_wrongs"],
    }


def _diff_lite(before: Dict[str, Any], after: Dict[str, Any], max_examples: int) -> Dict[str, Any]:
    added_project_examples: List[Dict[str, Any]] = []
    upgraded_examples: List[Dict[str, Any]] = []
    for b, a in zip(before["per_line"], after["per_line"]):
        b_reasons = set(b["reasons"])
        a_reasons = set(a["reasons"])
        if "项目混淆命中" in a_reasons and "项目混淆命中" not in b_reasons and len(added_project_examples) < max_examples:
            added_project_examples.append({"text": _preview(a["text"]), "reasons_after": a["reasons"]})
        if _severity_rank(a["severity"]) > _severity_rank(b["severity"]) and len(upgraded_examples) < max_examples:
            upgraded_examples.append(
                {
                    "text": _preview(a["text"]),
                    "severity_before": b["severity"],
                    "severity_after": a["severity"],
                    "reasons_after": a["reasons"],
                }
            )
    return {
        "before_lines_with_project_reason": before["lines_with_project_reason"],
        "after_lines_with_project_reason": after["lines_with_project_reason"],
        "delta_lines_with_project_reason": after["lines_with_project_reason"] - before["lines_with_project_reason"],
        "before_severity_counts": before["severity_counts"],
        "after_severity_counts": after["severity_counts"],
        "added_project_examples": added_project_examples,
        "severity_up_examples": upgraded_examples,
    }


def _diff_quality(before: Dict[str, Any], after: Dict[str, Any], max_examples: int) -> Dict[str, Any]:
    added_project_examples: List[Dict[str, Any]] = []
    suspect_added_examples: List[Dict[str, Any]] = []
    for b, a in zip(before["per_line"], after["per_line"]):
        b_hits = set(b["wrongs"])
        a_hits = set(a["wrongs"])
        b_reasons = set(b["reasons"])
        a_reasons = set(a["reasons"])
        if a_hits - b_hits and len(added_project_examples) < max_examples:
            added_project_examples.append({"text": _preview(a["text"]), "added_wrongs": sorted(a_hits - b_hits)})
        if a_reasons and not b_reasons and len(suspect_added_examples) < max_examples:
            suspect_added_examples.append({"text": _preview(a["text"]), "reasons_after": a["reasons"]})
    return {
        "before_lines_with_project_hits": before["lines_with_project_hits"],
        "after_lines_with_project_hits": after["lines_with_project_hits"],
        "delta_lines_with_project_hits": after["lines_with_project_hits"] - before["lines_with_project_hits"],
        "before_lines_with_project_reason": before["lines_with_project_reason"],
        "after_lines_with_project_reason": after["lines_with_project_reason"],
        "delta_lines_with_project_reason": after["lines_with_project_reason"] - before["lines_with_project_reason"],
        "before_suspect_lines": before["suspect_lines"],
        "after_suspect_lines": after["suspect_lines"],
        "delta_suspect_lines": after["suspect_lines"] - before["suspect_lines"],
        "added_project_examples": added_project_examples,
        "suspect_added_examples": suspect_added_examples,
        "before_top_wrongs": before["top_wrongs"],
        "after_top_wrongs": after["top_wrongs"],
    }


def _render_md(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# 正式集变更前后对比")
    lines.append("")
    lines.append(f"- 语料：`{report['inputs']['corpus']}`")
    lines.append(f"- before：`{report['inputs']['before_asset']}`")
    lines.append(f"- after：`{report['inputs']['after_asset']}`")
    lines.append(f"- lines：`{report['inputs']['line_count']}`")
    lines.append("")

    catalog = report["asset_catalog"]
    lines.append("## 1. 资产层")
    lines.append("")
    lines.append(f"- before 条数：`{catalog['before_items']}`")
    lines.append(f"- after 条数：`{catalog['after_items']}`")
    lines.append(f"- 新增 wrong：`{len(catalog['added_wrongs'])}`")
    lines.append(f"- 删除 wrong：`{len(catalog['removed_wrongs'])}`")
    lines.append(f"- 候选变更项：`{len(catalog['changed_candidates'])}`")
    lines.append("")

    asr = report["asr_compare"]
    lines.append("## 2. ASR 直接命中")
    lines.append("")
    lines.append(f"- before lines_with_hits：`{asr['before_lines_with_hits']}`")
    lines.append(f"- after lines_with_hits：`{asr['after_lines_with_hits']}`")
    lines.append(f"- before total_hits：`{asr['before_total_hits']}`")
    lines.append(f"- after total_hits：`{asr['after_total_hits']}`")
    lines.append("")

    lite = report["lite_compare"]
    lines.append("## 3. Lite 阶段")
    lines.append("")
    lines.append(f"- before 项目混淆命中行：`{lite['before_lines_with_project_reason']}`")
    lines.append(f"- after 项目混淆命中行：`{lite['after_lines_with_project_reason']}`")
    lines.append(f"- delta：`{lite['delta_lines_with_project_reason']}`")
    lines.append("")

    quality = report["quality_compare"]
    lines.append("## 4. Quality 阶段")
    lines.append("")
    lines.append(f"- before 项目混淆命中行：`{quality['before_lines_with_project_hits']}`")
    lines.append(f"- after 项目混淆命中行：`{quality['after_lines_with_project_hits']}`")
    lines.append(f"- before suspect_lines：`{quality['before_suspect_lines']}`")
    lines.append(f"- after suspect_lines：`{quality['after_suspect_lines']}`")
    lines.append("")

    def _add_examples(title: str, items: Iterable[Dict[str, Any]]) -> None:
        data = list(items)
        if not data:
            return
        lines.append(f"## {title}")
        lines.append("")
        for item in data:
            lines.append(f"- `{item.get('text', '')}`")
            extras = {k: v for k, v in item.items() if k != "text"}
            if extras:
                lines.append(f"  - {json.dumps(extras, ensure_ascii=False)}")
        lines.append("")

    _add_examples("5. ASR 新增命中样例", asr["added_examples"])
    _add_examples("6. Lite 新增项目混淆样例", lite["added_project_examples"])
    _add_examples("7. Quality 新增命中样例", quality["added_project_examples"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare before/after ASR project confusion assets on the same Chinese text corpus.")
    p.add_argument("--before-asset", type=Path, required=True)
    p.add_argument("--after-asset", type=Path, required=True)
    p.add_argument("--corpus", type=Path, required=True, help="JSON/JSONL/TXT corpus; auto-detects a Chinese text field")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--text-key", type=str, default="", help="Optional explicit text field name for JSON/JSONL corpus")
    p.add_argument("--max-lines", type=int, default=0, help="0 means all lines")
    p.add_argument("--dedupe-lines", action="store_true")
    p.add_argument("--max-examples", type=int, default=12)
    p.add_argument("--same-pinyin-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_pinyin.txt"))
    p.add_argument("--same-stroke-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_stroke.txt"))
    p.add_argument("--lexicon-path", type=Path, default=Path("assets/zh_phrase/chinese_xinhua_ci_2to4.txt"))
    p.add_argument("--proper-nouns-path", type=Path, default=Path("assets/zh_phrase/thuocl_proper_nouns.txt"))
    return p.parse_args()


def main() -> None:
    args = parse_args()
    lines = _load_corpus_lines(
        args.corpus,
        text_key=str(args.text_key or "").strip(),
        max_lines=int(args.max_lines or 0),
        dedupe=bool(args.dedupe_lines),
    )
    before_items = _load_asset_items(args.before_asset)
    after_items = _load_asset_items(args.after_asset)

    before_asr = _eval_asr_direct(lines, args.before_asset)
    after_asr = _eval_asr_direct(lines, args.after_asset)
    before_lite = _eval_lite(lines, args.before_asset, args)
    after_lite = _eval_lite(lines, args.after_asset, args)
    before_quality = _eval_quality(lines, args.before_asset)
    after_quality = _eval_quality(lines, args.after_asset)

    report = {
        "inputs": {
            "before_asset": str(args.before_asset),
            "after_asset": str(args.after_asset),
            "corpus": str(args.corpus),
            "line_count": len(lines),
            "text_key": str(args.text_key or ""),
            "dedupe_lines": bool(args.dedupe_lines),
        },
        "asset_catalog": _compare_asset_catalog(before_items, after_items),
        "asr_compare": _diff_asr_direct(before_asr, after_asr, int(args.max_examples or 12)),
        "lite_compare": _diff_lite(before_lite, after_lite, int(args.max_examples or 12)),
        "quality_compare": _diff_quality(before_quality, after_quality, int(args.max_examples or 12)),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "project_confusion_compare.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.out_dir / "project_confusion_compare.md").write_text(_render_md(report), encoding="utf-8")
    print(str(args.out_dir / "project_confusion_compare.md"))


if __name__ == "__main__":
    main()
