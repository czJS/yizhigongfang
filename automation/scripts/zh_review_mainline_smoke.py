#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.ruleset_store import load_ruleset, merge_rulesets, ruleset_to_glossary_doc
from pipelines.lib.glossary.glossary import load_glossary
from pipelines.quality_pipeline_impl import Segment, _apply_review_gate_policy, _apply_zh_glossary_inplace, _build_zh_gate_summary


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    seed_path = REPO_ROOT / "assets" / "rules" / "ruleset.seed.json"
    global_path = REPO_ROOT / "assets" / "rules" / "ruleset.global.json"
    seed_doc = load_ruleset(seed_path)
    global_doc = load_ruleset(global_path)
    merged = merge_rulesets(seed_doc, global_doc)
    glossary_doc = ruleset_to_glossary_doc(merged)

    seed_items = glossary_doc.get("items") if isinstance(glossary_doc, dict) else None
    _assert(isinstance(seed_items, list) and len(seed_items) >= 4, "seed glossary items missing")

    glossary_path = REPO_ROOT / "assets" / "glossary" / "glossary.json"
    # Runtime materialization should be compatible with the pipeline glossary format.
    runtime_glossary = load_glossary(glossary_path) if glossary_path.exists() else []
    _assert(isinstance(runtime_glossary, list), "runtime glossary shape invalid")

    segments = [
        Segment(start=0.0, end=1.0, text="医生说这是智床。"),
        Segment(start=1.0, end=2.0, text="后来他去抽血。"),
    ]
    hits = _apply_zh_glossary_inplace(segments, seed_items)
    _assert(hits >= 1, f"seed glossary not applied, hits={hits}")
    _assert("痔疮" in segments[0].text, f"expected 智床->痔疮, got={segments[0].text!r}")

    gate_summary = _build_zh_gate_summary(
        [],
        phrase_error="",
        min_high_risk=1,
        min_total_suspects=6,
        pause_on_phrase_error=False,
    )
    gate_summary = _apply_review_gate_policy(gate_summary, review_enabled=True)
    _assert(gate_summary.get("should_pause") is True, f"review_enabled should force pause: {gate_summary}")
    _assert("review_enabled" in (gate_summary.get("pause_reasons") or []), f"missing pause reason: {gate_summary}")

    report = {
        "seed_items": len(seed_items),
        "seed_hit_text": segments[0].text,
        "gate_summary": gate_summary,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
