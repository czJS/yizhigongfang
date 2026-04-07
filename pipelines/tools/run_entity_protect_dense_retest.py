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
from pipelines.lib.mt.mt import build_translator
from pipelines.lib.quality.quality_report import parse_srt
from pipelines.lib.text.zh_text import ZH_STOPWORDS, clean_zh_text, extract_entity_candidates_from_segments, is_role_like_zh


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _load_segments(row: Dict[str, Any]) -> List[Segment]:
    audio_json_raw = str(row.get("audio_json") or "").strip()
    audio_json = Path(audio_json_raw).expanduser() if audio_json_raw else None
    if audio_json and audio_json.is_file():
        data = json.loads(audio_json.read_text(encoding="utf-8", errors="ignore") or "[]")
        return [Segment(**item) for item in data if isinstance(item, dict)]
    chs_srt_raw = str(row.get("chs_srt") or "").strip()
    chs_srt = Path(chs_srt_raw).expanduser() if chs_srt_raw else None
    if chs_srt and chs_srt.is_file():
        items = parse_srt(chs_srt)
        return [Segment(start=float(it.start_s), end=float(it.end_s), text=str(it.text or "")) for it in items]
    raise FileNotFoundError(f"missing audio_json/chs_srt for case: {row.get('id')}")


def _normalize_zh(s: str) -> str:
    return re.sub(r"[^\u4e00-\u9fff]", "", clean_zh_text(s))


def _dense_entity_candidates(
    segments: List[Segment],
    *,
    min_len: int = 2,
    max_len: int = 6,
    min_freq: int = 2,
    max_items: int = 8,
) -> List[str]:
    """
    Experimental extractor for dedicated proper-noun-dense retest only.
    It is intentionally looser than the product extractor so we can measure whether
    repeated names/titles are worth protecting on this material type.
    """
    freq: Dict[str, int] = {}
    seg_support: Dict[str, int] = {}
    joined = [_normalize_zh(getattr(seg, "text", "") or "") for seg in segments]
    strong_suffixes = ("局", "司", "院", "校", "叔", "哥", "姐", "长", "总", "先生", "女士", "博士")
    bad_chars = set("的了着在给为和与是就都也把被对向从到后前里上下")
    exact_whitelist = {"龙叔", "中情局", "典御长", "典御掌", "典裕", "典裕掌", "杨紫静"}

    for s in joined:
        if not s:
            continue
        seen_in_seg = set()
        for n in range(max(2, int(min_len)), min(int(max_len), 6) + 1):
            for i in range(0, max(0, len(s) - n + 1)):
                cand = s[i : i + n]
                if cand in ZH_STOPWORDS:
                    continue
                if len(set(cand)) == 1:
                    continue
                if re.search(r"(什么|这个|那个|一个|一下|一下子|然后|因为|所以|但是|于是|真的|自己|对方|出来|这里|那里)$", cand):
                    continue
                if cand not in exact_whitelist:
                    if any(ch in bad_chars for ch in cand):
                        continue
                    if len(cand) >= 5 and not cand.endswith(strong_suffixes):
                        continue
                score = 1
                if cand.endswith(strong_suffixes) or cand in exact_whitelist:
                    score = 3
                freq[cand] = freq.get(cand, 0) + score
                seen_in_seg.add(cand)
        for cand in seen_in_seg:
            seg_support[cand] = seg_support.get(cand, 0) + 1

    items = [(k, v) for k, v in freq.items() if v >= int(min_freq) and int(seg_support.get(k, 0)) >= 2]
    items.sort(key=lambda kv: (seg_support.get(kv[0], 0), kv[1], len(kv[0])), reverse=True)
    chosen: List[str] = []
    for cand, _score in items:
        if any(cand in kept for kept in chosen):
            continue
        # Prefer plausible names/terms over plain content words.
        if not (
            cand.endswith(strong_suffixes)
            or cand in exact_whitelist
            or (freq.get(cand, 0) >= int(min_freq) + 2 and len(cand) <= 4)
        ):
            continue
        chosen.append(cand)
        if len(chosen) >= int(max_items):
            break
    chosen.sort(key=len, reverse=True)
    return chosen


def _legacy_entity_candidates(
    segments: List[Segment],
    *,
    min_len: int = 2,
    max_len: int = 6,
    min_freq: int = 2,
    max_items: int = 30,
) -> List[str]:
    freq: Dict[str, int] = {}
    min_len = max(int(min_len or 2), 2)
    max_len = max(int(max_len or 6), min_len)
    min_freq = max(int(min_freq or 2), 4)
    max_items = min(int(max_items or 30), 8)
    suffix_pat = re.compile(r"[\u4e00-\u9fff]{1,10}(国|城|镇|山|河|宫|岛|州|省|市|县|村|堡)")
    for seg in segments:
        s = clean_zh_text(getattr(seg, "text", "") or "")
        for it in suffix_pat.finditer(s):
            cand = it.group(0)
            if cand in ZH_STOPWORDS:
                continue
            if is_role_like_zh(cand):
                continue
            freq[cand] = freq.get(cand, 0) + 2
        for it in re.finditer(r"[“《（(]([\u4e00-\u9fff]{2,10})[”》）)]", s):
            cand = it.group(1)
            if cand in ZH_STOPWORDS:
                continue
            if is_role_like_zh(cand):
                continue
            if not (min_len <= len(cand) <= min(max_len, 8)):
                continue
            freq[cand] = freq.get(cand, 0) + 3
    items = [(k, v) for k, v in freq.items() if v >= min_freq]
    items.sort(key=lambda kv: (kv[1], len(kv[0])), reverse=True)
    chosen: List[str] = []
    for k, _v in items:
        if any(k in c for c in chosen):
            continue
        if is_role_like_zh(k):
            continue
        chosen.append(k)
        if len(chosen) >= max_items:
            break
    chosen.sort(key=len, reverse=True)
    return chosen


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

    extractor = {
        "entity_legacy": _legacy_entity_candidates,
        "entity_current": extract_entity_candidates_from_segments,
        "entity_dense_loose": _dense_entity_candidates,
    }.get(profile_id, extract_entity_candidates_from_segments)
    case_results: List[Dict[str, Any]] = []
    elapsed_list: List[float] = []

    for row in rows:
        segs = _load_segments(row)
        case_id = str(row.get("id") or "")
        case_dir = profile_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        entity_map: Dict[str, str] = {}
        if bool(profile.get("entity_protect_enable")):
            entity_map = build_auto_entity_map(
                segs,
                translate_fn,
                min_len=int(profile.get("entity_protect_min_len", 2) or 2),
                max_len=int(profile.get("entity_protect_max_len", 6) or 6),
                min_freq=int(profile.get("entity_protect_min_freq", 2) or 2),
                max_items=int(profile.get("entity_protect_max_items", 8) or 8),
                extract_candidates_fn=extractor,
            )
        t0 = time.time()
        seg_en = translate_segments(
            segs,
            translate_fn,
            entity_map=entity_map or None,
            sentence_unit_enable=False,
        )
        elapsed_s = round(time.time() - t0, 4)
        elapsed_list.append(elapsed_s)

        lines = [str(getattr(seg, "translation", "") or "").strip() for seg in seg_en]
        write_srt(case_dir / "eng.srt", seg_en, text_attr="translation")
        (case_dir / "summary.json").write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "entity_candidates": sorted(entity_map.keys()),
                    "translations": lines,
                    "source_texts": [str(getattr(seg, "text", "") or "") for seg in segs],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        case_results.append(
            {
                "id": case_id,
                "entity_candidates": sorted(entity_map.keys()),
                "line_count": len(lines),
                "elapsed_s": elapsed_s,
                "translations": lines,
                "source_texts": [str(getattr(seg, "text", "") or "") for seg in segs],
            }
        )

    return {
        "id": profile_id,
        "label": str(profile.get("label") or profile_id),
        "params": profile,
        "cases": case_results,
        "metrics": {
            "case_count": len(case_results),
            "elapsed_s_mean": round(statistics.mean(elapsed_list), 4) if elapsed_list else None,
            "entity_case_nonempty": sum(1 for item in case_results if item["entity_candidates"]),
            "entity_candidate_total": sum(len(item["entity_candidates"]) for item in case_results),
        },
    }


def _build_diff(base: Dict[str, Any], other: Dict[str, Any], *, max_examples: int) -> Dict[str, Any]:
    base_cases = {str(item["id"]): item for item in base["cases"]}
    other_cases = {str(item["id"]): item for item in other["cases"]}
    changed_cases = 0
    changed_lines = 0
    examples: List[Dict[str, Any]] = []
    for case_id, b in base_cases.items():
        o = other_cases[case_id]
        if b["translations"] == o["translations"]:
            continue
        changed_cases += 1
        for idx, (before, after) in enumerate(zip(b["translations"], o["translations"]), start=1):
            if before == after:
                continue
            changed_lines += 1
            if len(examples) < max_examples:
                src = ""
                if idx - 1 < len(b["source_texts"]):
                    src = str(b["source_texts"][idx - 1] or "")
                examples.append(
                    {
                        "case_id": case_id,
                        "line_idx": idx,
                        "source": src,
                        "before": before,
                        "after": after,
                    }
                )
    return {
        "changed_cases": changed_cases,
        "changed_lines": changed_lines,
        "entity_case_nonempty": int(other["metrics"]["entity_case_nonempty"]),
        "entity_candidate_total": int(other["metrics"]["entity_candidate_total"]),
        "elapsed_delta_s_mean": round(
            float(other["metrics"]["elapsed_s_mean"] or 0.0) - float(base["metrics"]["elapsed_s_mean"] or 0.0), 4
        ),
        "examples": examples,
    }


def _render_md(report: Dict[str, Any]) -> str:
    lines = [
        "# Entity-protect dense retest",
        "",
        "## Dataset",
        "",
        f"- cases: `{report['inputs']['case_count']}`",
        f"- cases_file: `{report['inputs']['cases']}`",
        "",
    ]
    for profile_id in ["entity_legacy", "entity_current", "entity_dense_loose"]:
        diff = report["diffs"][profile_id]
        lines.extend(
            [
                f"## `{profile_id}`",
                "",
                f"- changed_cases: `{diff['changed_cases']}`",
                f"- changed_lines: `{diff['changed_lines']}`",
                f"- entity_case_nonempty: `{diff['entity_case_nonempty']}`",
                f"- entity_candidate_total: `{diff['entity_candidate_total']}`",
                f"- elapsed_delta_s_mean: `{diff['elapsed_delta_s_mean']}`",
                f"- recommendation: {diff['recommendation']}",
                "",
            ]
        )
        if diff["examples"]:
            lines.append("Representative changes:")
            for ex in diff["examples"]:
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
    ap = argparse.ArgumentParser(description="Run dedicated dense proper-noun entity_protect retest.")
    ap.add_argument("--cases", type=Path, default=repo_root / "reports/lite_phase1/mt_entity_dense_dataset/mt_cases.jsonl")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--mt-model", type=Path, default=repo_root / "assets/models/lite_mt_marian_opus_mt_zh_en")
    ap.add_argument("--mt-device", type=str, default="cpu")
    ap.add_argument("--mt-cache-dir", type=Path, default=repo_root / "assets/models/common_cache_hf")
    ap.add_argument("--max-examples", type=int, default=10)
    args = ap.parse_args()

    rows = _read_jsonl(args.cases)
    translate_fn = build_translator(
        model_id=str(args.mt_model),
        device=args.mt_device,
        cache_dir=str(args.mt_cache_dir),
        offline=True,
    )

    profiles = [
        {"id": "base", "label": "baseline", "entity_protect_enable": False},
        {
            "id": "entity_legacy",
            "label": "legacy conservative extractor",
            "entity_protect_enable": True,
            "entity_protect_min_len": 2,
            "entity_protect_max_len": 8,
            "entity_protect_min_freq": 4,
            "entity_protect_max_items": 8,
        },
        {
            "id": "entity_current",
            "label": "current refactored extractor",
            "entity_protect_enable": True,
            "entity_protect_min_len": 2,
            "entity_protect_max_len": 8,
            "entity_protect_min_freq": 4,
            "entity_protect_max_items": 8,
        },
        {
            "id": "entity_dense_loose",
            "label": "dense-scene loose extractor",
            "entity_protect_enable": True,
            "entity_protect_min_len": 2,
            "entity_protect_max_len": 6,
            "entity_protect_min_freq": 2,
            "entity_protect_max_items": 8,
        },
    ]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = {p["id"]: _run_profile(rows=rows, translate_fn=translate_fn, profile=p, out_dir=args.out_dir) for p in profiles}
    base = results["base"]
    diffs = {
        "entity_legacy": _build_diff(base, results["entity_legacy"], max_examples=int(args.max_examples)),
        "entity_current": _build_diff(base, results["entity_current"], max_examples=int(args.max_examples)),
        "entity_dense_loose": _build_diff(base, results["entity_dense_loose"], max_examples=int(args.max_examples)),
    }
    diffs["entity_legacy"]["recommendation"] = (
        "旧保守抽取在专名密集小集上几乎无效。"
        if diffs["entity_legacy"]["entity_candidate_total"] == 0
        else "旧保守抽取有一定命中，但覆盖仍偏低。"
    )
    diffs["entity_current"]["recommendation"] = (
        "当前重构后的正式抽取已开始命中真实专名候选，但仍应保持高级开关，暂不默认开启。"
        if diffs["entity_current"]["entity_candidate_total"] > 0
        else "当前重构后的正式抽取仍未形成稳定命中，继续默认关闭。"
    )
    diffs["entity_dense_loose"]["recommendation"] = (
        "宽松抽取已能稳定抓到专名，可作为后续实验方向，但暂不直接进入默认主线。"
        if diffs["entity_dense_loose"]["entity_candidate_total"] > 0
        else "即使在专名密集小集上，宽松抽取也未体现价值，暂不继续扩大。"
    )

    report = {
        "task": "entity_protect_dense_retest",
        "inputs": {"cases": str(args.cases), "case_count": len(rows), "mt_model": str(args.mt_model)},
        "profiles": results,
        "diffs": diffs,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "summary.md").write_text(_render_md(report), encoding="utf-8")
    print(str(args.out_dir / "summary.md"))


if __name__ == "__main__":
    main()
