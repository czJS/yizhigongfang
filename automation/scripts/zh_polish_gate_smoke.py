#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.quality_pipeline_impl import (
    Segment,
    ZhPolishArtifacts,
    _apply_review_gate_policy,
    _build_zh_gate_summary,
    _rule_based_suspect,
    _select_zh_opt_candidate_items,
    _write_zh_polish_artifacts,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke-test zh_polish suspect detection, candidate pinning, pause gate, and artifact semantics.")
    ap.add_argument("--report-json", default="", help="Optional path to write a JSON report.")
    args = ap.parse_args()

    segments = [
        Segment(start=0.00, end=0.60, text="这时"),
        Segment(start=0.60, end=1.10, text="扎手把"),
        Segment(start=1.10, end=1.75, text="于蚊子正面硬钢"),
        Segment(start=1.75, end=2.30, text="智床"),
        Segment(start=2.30, end=3.40, text="他继续往前走。"),
        Segment(start=3.40, end=4.00, text="修好汽车重组"),
    ]

    rule_reasons_by_idx = {}
    for idx, seg in enumerate(segments, 1):
        rr = _rule_based_suspect(seg)
        if rr:
            rule_reasons_by_idx[idx] = rr

    hit_idxs = sorted(rule_reasons_by_idx.keys())
    _assert(hit_idxs == [2, 3, 4, 6], f"unexpected suspect idxs: {hit_idxs}")

    items_all = [(idx, seg.text) for idx, seg in enumerate(segments, 1)]
    picked = _select_zh_opt_candidate_items(items_all, rule_reasons_by_idx=rule_reasons_by_idx, max_lines=2)
    picked_idxs = [idx for idx, _ in picked]
    _assert(set(hit_idxs).issubset(set(picked_idxs)), f"rule-based candidates were dropped: picked={picked_idxs}, expected={hit_idxs}")

    suspects = [
        {
            "idx": idx,
            "text": segments[idx - 1].text,
            "rule_reasons": rule_reasons_by_idx[idx],
            "risk": "high",
            "need_review": True,
            "spans": [],
        }
        for idx in hit_idxs
    ]
    gate_summary = _build_zh_gate_summary(
        suspects,
        phrase_error="",
        min_high_risk=1,
        min_total_suspects=2,
        pause_on_phrase_error=True,
    )
    _assert(bool(gate_summary.get("should_pause")), f"gate did not pause: {gate_summary}")
    forced_pause = _apply_review_gate_policy(gate_summary, review_enabled=True)
    _assert(bool(forced_pause.get("should_pause")), f"review_enabled must force pause: {forced_pause}")
    _assert("review_enabled" in (forced_pause.get("pause_reasons") or []), f"missing forced pause reason: {forced_pause}")

    artifacts = ZhPolishArtifacts(phrase_items=[], suspects=suspects, gate_summary=forced_pause)
    with tempfile.TemporaryDirectory(prefix="zh_polish_gate_smoke_") as td:
        phrase_path = Path(td) / "chs.phrases.json"
        suspect_path = Path(td) / "chs.suspects.json"
        _write_zh_polish_artifacts(
            chs_phrases_json=phrase_path,
            chs_suspects_json=suspect_path,
            artifacts=artifacts,
            zh_phrase_error="",
            zh_polish_enabled=True,
            review_gate_enabled=True,
            zh_opt_enabled=True,
        )
        phrase_doc = json.loads(phrase_path.read_text(encoding="utf-8"))
        suspect_doc = json.loads(suspect_path.read_text(encoding="utf-8"))
        phrase_meta = phrase_doc.get("meta") or {}
        suspect_meta = suspect_doc.get("meta") or {}
        for meta in (phrase_meta, suspect_meta):
            _assert(meta.get("zh_polish_enabled") is True, f"zh_polish_enabled missing: {meta}")
            _assert(meta.get("review_gate_enabled") is True, f"review_gate_enabled missing: {meta}")
            _assert(meta.get("zh_opt_enabled") is True, f"zh_opt_enabled missing: {meta}")
            _assert(meta.get("zh_phrase_enable") is True, f"legacy zh_phrase_enable not aligned: {meta}")

    report = {
        "suspect_idxs": hit_idxs,
        "picked_idxs": picked_idxs,
        "gate_summary": forced_pause,
        "artifact_meta_keys": sorted(
            {
                *list((phrase_meta or {}).keys()),
                *list((suspect_meta or {}).keys()),
            }
        ),
    }
    if args.report_json:
        out = Path(args.report_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
