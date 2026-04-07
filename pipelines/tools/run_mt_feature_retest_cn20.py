#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from pipelines.lib.asr.lite_asr import Segment, write_srt
from pipelines.lib.glossary.entity_protect import build_auto_entity_map
from pipelines.lib.lite_translate import translate_segments
from pipelines.lib.media.lite_delivery import normalize_en_line
from pipelines.lib.mt.mt import build_translator
from pipelines.lib.text.zh_text import extract_entity_candidates_from_segments
from pipelines.tools.lite_phase1_mt_bakeoff import _load_segments, _read_jsonl


DEFAULT_BREAK_WORDS = ["但", "而", "于是", "然后", "忽然", "突然", "不过", "结果", "同时"]


def _resolve_app_path(path_str: str) -> str:
    s = str(path_str or "").strip()
    if not s:
        return s
    if s.startswith("/app/"):
        return str(repo_root / s[len("/app/") :])
    return s


def _load_case(row: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Segment]]:
    fixed = dict(row)
    for key in ["video", "audio_json", "chs_srt"]:
        if key in fixed:
            fixed[key] = _resolve_app_path(str(fixed.get(key) or ""))
    segs = _load_segments(fixed)
    return fixed, segs


def _en_line_is_fragment(s: str) -> bool:
    t = normalize_en_line(s or "")
    if not t:
        return True
    low = t.lower().strip()
    low2 = low.rstrip(".!?")
    words = [w for w in re.split(r"\s+", re.sub(r"[^A-Za-z\s']+", " ", t)) if w]
    if len(words) <= 1:
        return True
    if re.search(
        r"\b(a|an|the|to|of|with|for|and|or|but|because|that|which|is|are|was|were|do|does|did|can|could|will|would|should|may|might|into|from|in|on|at|about)$",
        low2,
    ):
        return True
    if re.search(r"\b(my|your|his|her|its|our|their|this|that|these|those|every)\.?$", low):
        return True
    return any(ord(ch) > 127 for ch in t)


def _profile_defs() -> List[Dict[str, Any]]:
    return [
        {
            "id": "base",
            "label": "baseline",
            "sentence_unit_enable": False,
            "entity_protect_enable": False,
        },
        {
            "id": "sentence_unit_on",
            "label": "sentence_unit on",
            "sentence_unit_enable": True,
            "sentence_unit_min_chars": 12,
            "sentence_unit_max_chars": 60,
            "sentence_unit_max_segs": 3,
            "sentence_unit_max_gap_s": 0.6,
            "sentence_unit_boundary_punct": "。！？!?.,",
            "sentence_unit_break_words": list(DEFAULT_BREAK_WORDS),
            "entity_protect_enable": False,
        },
        {
            "id": "entity_protect_on",
            "label": "entity_protect on",
            "sentence_unit_enable": False,
            "entity_protect_enable": True,
            "entity_protect_min_len": 2,
            "entity_protect_max_len": 8,
            "entity_protect_min_freq": 4,
            "entity_protect_max_items": 8,
        },
        {
            "id": "combo_on",
            "label": "sentence_unit + entity_protect",
            "sentence_unit_enable": True,
            "sentence_unit_min_chars": 12,
            "sentence_unit_max_chars": 60,
            "sentence_unit_max_segs": 3,
            "sentence_unit_max_gap_s": 0.6,
            "sentence_unit_boundary_punct": "。！？!?.,",
            "sentence_unit_break_words": list(DEFAULT_BREAK_WORDS),
            "entity_protect_enable": True,
            "entity_protect_min_len": 2,
            "entity_protect_max_len": 8,
            "entity_protect_min_freq": 4,
            "entity_protect_max_items": 8,
        },
    ]


def _run_profile(
    *,
    rows: List[Dict[str, Any]],
    translate_fn,
    profile: Dict[str, Any],
    out_dir: Path,
) -> Dict[str, Any]:
    profile_id = str(profile["id"])
    profile_dir = out_dir / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)

    case_results: List[Dict[str, Any]] = []
    elapsed_s_list: List[float] = []
    entity_case_nonempty = 0
    entity_candidate_total = 0
    entity_samples: List[str] = []

    for row in rows:
        fixed_row, segs = _load_case(row)
        case_id = str(fixed_row.get("id") or "")
        case_dir = profile_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        entity_map: Dict[str, str] = {}
        if bool(profile.get("entity_protect_enable")):
            entity_map = build_auto_entity_map(
                segs,
                translate_fn,
                min_len=int(profile.get("entity_protect_min_len", 2) or 2),
                max_len=int(profile.get("entity_protect_max_len", 8) or 8),
                min_freq=int(profile.get("entity_protect_min_freq", 4) or 4),
                max_items=int(profile.get("entity_protect_max_items", 8) or 8),
                extract_candidates_fn=extract_entity_candidates_from_segments,
            )
            if entity_map:
                entity_case_nonempty += 1
                entity_candidate_total += len(entity_map)
                for key in list(entity_map.keys())[:3]:
                    if key not in entity_samples:
                        entity_samples.append(key)

        t0 = time.time()
        seg_en = translate_segments(
            segs,
            translate_fn,
            entity_map=entity_map or None,
            sentence_unit_enable=bool(profile.get("sentence_unit_enable")),
            sentence_unit_min_chars=int(profile.get("sentence_unit_min_chars", 12) or 12),
            sentence_unit_max_chars=int(profile.get("sentence_unit_max_chars", 60) or 60),
            sentence_unit_max_segs=int(profile.get("sentence_unit_max_segs", 3) or 3),
            sentence_unit_max_gap_s=float(profile.get("sentence_unit_max_gap_s", 0.6) or 0.6),
            sentence_unit_boundary_punct=str(profile.get("sentence_unit_boundary_punct", "。！？!?.,") or "。！？!?.,"),
            sentence_unit_break_words=list(profile.get("sentence_unit_break_words", DEFAULT_BREAK_WORDS) or DEFAULT_BREAK_WORDS),
        )
        elapsed_s = round(time.time() - t0, 4)
        elapsed_s_list.append(elapsed_s)

        write_srt(case_dir / "eng.srt", seg_en, text_attr="translation")
        (case_dir / "eng.json").write_text(json.dumps([seg.__dict__ for seg in seg_en], ensure_ascii=False, indent=2), encoding="utf-8")

        fragments = sum(1 for seg in seg_en if _en_line_is_fragment(str(getattr(seg, "translation", "") or "")))
        lines = [normalize_en_line(str(getattr(seg, "translation", "") or "")) for seg in seg_en]
        case_results.append(
            {
                "id": case_id,
                "video": str(fixed_row.get("video") or ""),
                "line_count": len(lines),
                "fragment_lines": fragments,
                "elapsed_s": elapsed_s,
                "entity_candidates": sorted(entity_map.keys())[:8],
                "translations": lines,
                "source_texts": [str(getattr(seg, "text", "") or "") for seg in segs],
            }
        )

    summary = {
        "id": profile_id,
        "label": str(profile.get("label") or profile_id),
        "params": profile,
        "cases": case_results,
        "metrics": {
            "case_count": len(case_results),
            "elapsed_s_mean": round(statistics.mean(elapsed_s_list), 4) if elapsed_s_list else None,
            "fragment_lines_total": sum(int(item["fragment_lines"]) for item in case_results),
            "lines_total": sum(int(item["line_count"]) for item in case_results),
            "entity_case_nonempty": int(entity_case_nonempty),
            "entity_candidate_total": int(entity_candidate_total),
            "entity_samples": entity_samples[:10],
        },
    }
    (profile_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _build_diff(base: Dict[str, Any], other: Dict[str, Any], *, max_examples: int) -> Dict[str, Any]:
    base_cases = {str(item["id"]): item for item in base["cases"]}
    other_cases = {str(item["id"]): item for item in other["cases"]}
    changed_cases = 0
    changed_lines = 0
    examples: List[Dict[str, Any]] = []
    for case_id, base_case in base_cases.items():
        other_case = other_cases.get(case_id)
        if not other_case:
            continue
        b_lines = list(base_case.get("translations") or [])
        o_lines = list(other_case.get("translations") or [])
        if b_lines == o_lines:
            continue
        changed_cases += 1
        for idx, (b_line, o_line) in enumerate(zip(b_lines, o_lines), start=1):
            if b_line == o_line:
                continue
            changed_lines += 1
            if len(examples) < max_examples:
                src = ""
                src_list = base_case.get("source_texts") or []
                if idx - 1 < len(src_list):
                    src = str(src_list[idx - 1] or "")
                examples.append(
                    {
                        "case_id": case_id,
                        "line_idx": idx,
                        "source": src,
                        "before": b_line,
                        "after": o_line,
                    }
                )
        if len(o_lines) != len(b_lines):
            changed_lines += abs(len(o_lines) - len(b_lines))
    return {
        "changed_cases": changed_cases,
        "changed_lines": changed_lines,
        "fragment_delta": int(other["metrics"]["fragment_lines_total"]) - int(base["metrics"]["fragment_lines_total"]),
        "elapsed_delta_s_mean": round(
            float(other["metrics"]["elapsed_s_mean"] or 0.0) - float(base["metrics"]["elapsed_s_mean"] or 0.0), 4
        ),
        "entity_case_nonempty": int(other["metrics"]["entity_case_nonempty"]),
        "entity_candidate_total": int(other["metrics"]["entity_candidate_total"]),
        "entity_samples": list(other["metrics"].get("entity_samples") or []),
        "examples": examples,
    }


def _recommendation(profile_id: str, diff: Dict[str, Any]) -> str:
    if profile_id == "sentence_unit_on":
        if int(diff["fragment_delta"]) < 0 and int(diff["changed_lines"]) <= 20:
            return "高级开关可保留；若后续人工复核样本确认收益稳定，再考虑默认开"
        if int(diff["fragment_delta"]) >= 0:
            return "继续默认关闭；当前 cn20 上未看到足够稳定的正收益"
        return "保留高级开关，不建议默认开"
    if profile_id == "entity_protect_on":
        if int(diff["entity_case_nonempty"]) < 3:
            return "当前 20 条素材专名密度不足，先继续默认关闭，仅保留高级开关"
        if int(diff["changed_lines"]) == 0:
            return "保留高级开关；当前样本上信号偏弱，不建议默认开"
        return "保留高级开关，只在专名密集素材上继续复测"
    if profile_id == "combo_on":
        if int(diff["changed_lines"]) == 0:
            return "组合收益不明显，当前不作为默认方案"
        return "仅作上限方案，不进入默认主线"
    return ""


def _render_md(report: Dict[str, Any]) -> str:
    base_metrics = report["profiles"]["base"]["metrics"]
    lines: List[str] = [
        "# CN20 MT feature retest",
        "",
        "## Baseline",
        "",
        f"- case_count: `{base_metrics['case_count']}`",
        f"- lines_total: `{base_metrics['lines_total']}`",
        f"- fragment_lines_total: `{base_metrics['fragment_lines_total']}`",
        f"- elapsed_s_mean: `{base_metrics['elapsed_s_mean']}`",
        "",
        "## Feature decisions",
        "",
    ]
    for profile_id in ["sentence_unit_on", "entity_protect_on", "combo_on"]:
        diff = report["diffs"][profile_id]
        lines.extend(
            [
                f"### `{profile_id}`",
                "",
                f"- changed_cases: `{diff['changed_cases']}`",
                f"- changed_lines: `{diff['changed_lines']}`",
                f"- fragment_delta_vs_base: `{diff['fragment_delta']}`",
                f"- elapsed_delta_s_mean: `{diff['elapsed_delta_s_mean']}`",
                f"- entity_case_nonempty: `{diff['entity_case_nonempty']}`",
                f"- entity_candidate_total: `{diff['entity_candidate_total']}`",
                f"- recommendation: {diff['recommendation']}",
                "",
            ]
        )
        if diff["entity_samples"]:
            lines.append(f"- entity_samples: `{', '.join(diff['entity_samples'])}`")
            lines.append("")
        if diff["examples"]:
            lines.append("Representative changes:")
            for ex in diff["examples"][:6]:
                lines.extend(
                    [
                        f"- `{ex['case_id']}#{ex['line_idx']}`",
                        f"  - zh: `{ex['source']}`",
                        f"  - before: `{ex['before']}`",
                        f"  - after: `{ex['after']}`",
                    ]
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Run CN20 decision retest for sentence_unit and entity_protect.")
    ap.add_argument("--cases", type=Path, default=repo_root / "reports/lite_phase1/mt_cn20_dataset/mt_cases.jsonl")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--mt-model", type=Path, default=repo_root / "assets/models/lite_mt_marian_opus_mt_zh_en")
    ap.add_argument("--mt-device", type=str, default="cpu")
    ap.add_argument("--mt-cache-dir", type=Path, default=repo_root / "assets/models/common_cache_hf")
    ap.add_argument("--max-examples", type=int, default=12)
    args = ap.parse_args()

    rows = _read_jsonl(args.cases)
    translate_fn = build_translator(
        model_id=str(args.mt_model),
        device=args.mt_device,
        cache_dir=str(args.mt_cache_dir),
        offline=True,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    profiles = {}
    for profile in _profile_defs():
        profiles[profile["id"]] = _run_profile(rows=rows, translate_fn=translate_fn, profile=profile, out_dir=args.out_dir)

    base = profiles["base"]
    diffs: Dict[str, Any] = {}
    for profile_id in ["sentence_unit_on", "entity_protect_on", "combo_on"]:
        diff = _build_diff(base, profiles[profile_id], max_examples=int(args.max_examples or 12))
        diff["recommendation"] = _recommendation(profile_id, diff)
        diffs[profile_id] = diff

    report = {
        "task": "cn20_mt_feature_retest",
        "inputs": {
            "cases": str(args.cases),
            "mt_model": str(args.mt_model),
            "case_count": len(rows),
        },
        "profiles": profiles,
        "diffs": diffs,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "summary.md").write_text(_render_md(report), encoding="utf-8")
    print(str(args.out_dir / "summary.md"))


if __name__ == "__main__":
    main()
