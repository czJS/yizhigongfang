#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _read_json(path: Path) -> Dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    return obj if isinstance(obj, dict) else {}


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _pick_candidate(result: Dict[str, Any]) -> Tuple[str, bool, str]:
    baseline_id = str(result.get("baseline_candidate") or "")
    candidates = result.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return baseline_id or "unknown", False, "no candidates found"

    baseline: Optional[Dict[str, Any]] = None
    parsed: List[Dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") or {}
        parsed_item = {
            "id": str(item.get("id") or ""),
            "label": str(item.get("label") or ""),
            "elapsed_s_mean": _num(metrics.get("elapsed_s_mean")),
            "passed_rate": _num(metrics.get("passed_rate")),
            "fail_rate": _num(metrics.get("fail_rate")),
            "artifacts_ok_rate": _num(metrics.get("artifacts_ok_rate")),
            "added_cost_vs_baseline": _num(metrics.get("added_cost_vs_baseline")),
            "subjective_score": _num(metrics.get("subjective_score")),
        }
        if parsed_item["id"] == baseline_id:
            baseline = parsed_item
        parsed.append(parsed_item)

    if baseline is None:
        baseline = parsed[0]
        baseline_id = str(baseline["id"])

    complete = [
        item
        for item in parsed
        if item["passed_rate"] is not None
        and item["fail_rate"] is not None
        and item["artifacts_ok_rate"] is not None
    ]
    if not complete:
        return baseline_id, False, "metrics incomplete, keep baseline"

    eligible: List[Dict[str, Any]] = []
    for item in complete:
        if baseline["passed_rate"] is not None and item["passed_rate"] < baseline["passed_rate"]:
            continue
        if baseline["fail_rate"] is not None and item["fail_rate"] > baseline["fail_rate"]:
            continue
        if baseline["artifacts_ok_rate"] is not None and item["artifacts_ok_rate"] < baseline["artifacts_ok_rate"]:
            continue
        eligible.append(item)

    if not eligible:
        return baseline_id, False, "no candidate met baseline hard rules"

    def sort_key(item: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
        return (
            -(item["passed_rate"] or 0.0),
            item["fail_rate"] or 9999.0,
            -(item["artifacts_ok_rate"] or 0.0),
            item["added_cost_vs_baseline"] if item["added_cost_vs_baseline"] is not None else 9999.0,
            -(item["subjective_score"] or 0.0),
        )

    winner = sorted(eligible, key=sort_key)[0]
    replace_now = winner["id"] != baseline_id
    if replace_now:
        reason = f"{winner['id']} beats baseline under phase1 rules"
    else:
        reason = "baseline remains best under phase1 rules"
    return str(winner["id"]), replace_now, reason


def _llm_summary(mt_result: Dict[str, Any]) -> Dict[str, Any]:
    sidecar = mt_result.get("sidecar_llm_experiment") or {}
    if not isinstance(sidecar, dict):
        return {"tested": False, "candidate": "", "phase2_recommendation": False, "reason": "no sidecar field"}
    tested = bool(sidecar.get("tested"))
    candidate = str(sidecar.get("candidate") or "")
    metrics = sidecar.get("metrics") or {}
    recommend = bool(metrics.get("phase2_recommendation"))
    if not tested:
        return {"tested": False, "candidate": candidate, "phase2_recommendation": False, "reason": "not tested in phase1"}
    if recommend:
        return {"tested": True, "candidate": candidate, "phase2_recommendation": True, "reason": "worth carrying into phase2"}
    return {"tested": True, "candidate": candidate, "phase2_recommendation": False, "reason": "not worth entering default chain now"}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_md(summary: Dict[str, Any]) -> str:
    return f"""# lite phase1 summary

## ASR
- winner: `{summary['asr']['winner']}`
- replace_now: `{str(summary['asr']['replace_now']).lower()}`
- reason: {summary['asr']['reason']}

## MT
- winner: `{summary['mt']['winner']}`
- replace_now: `{str(summary['mt']['replace_now']).lower()}`
- reason: {summary['mt']['reason']}

## TTS
- winner: `{summary['tts']['winner']}`
- replace_now: `{str(summary['tts']['replace_now']).lower()}`
- reason: {summary['tts']['reason']}

## small LLM sidecar
- tested: `{str(summary['llm_sidecar']['tested']).lower()}`
- candidate: `{summary['llm_sidecar']['candidate']}`
- phase2_recommendation: `{str(summary['llm_sidecar']['phase2_recommendation']).lower()}`
- reason: {summary['llm_sidecar']['reason']}

## final action items
{chr(10).join(f"- {item}" for item in summary["final_action_items"])}
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Summarize phase1 ASR/MT/TTS bakeoff result templates into a final decision.")
    ap.add_argument("--asr", type=Path, required=True, help="ASR results_template.json path")
    ap.add_argument("--mt", type=Path, required=True, help="MT results_template.json path")
    ap.add_argument("--tts", type=Path, required=True, help="TTS results_template.json path")
    ap.add_argument("--out-dir", type=Path, required=True, help="Summary output directory")
    args = ap.parse_args()

    asr = _read_json(args.asr)
    mt = _read_json(args.mt)
    tts = _read_json(args.tts)

    asr_winner, asr_replace, asr_reason = _pick_candidate(asr)
    mt_winner, mt_replace, mt_reason = _pick_candidate(mt)
    tts_winner, tts_replace, tts_reason = _pick_candidate(tts)
    llm_sidecar = _llm_summary(mt)

    final_action_items: List[str] = []
    final_action_items.append(("replace ASR with " if asr_replace else "keep current ASR: ") + asr_winner)
    final_action_items.append(("replace MT with " if mt_replace else "keep current MT: ") + mt_winner)
    final_action_items.append(("replace TTS with " if tts_replace else "keep current TTS: ") + tts_winner)
    if llm_sidecar["phase2_recommendation"]:
        final_action_items.append(f"carry sidecar LLM into phase2: {llm_sidecar['candidate']}")
    else:
        final_action_items.append("do not add small LLM to default chain in phase1")

    summary = {
        "phase": "lite_phase1",
        "asr": {"winner": asr_winner, "replace_now": asr_replace, "reason": asr_reason},
        "mt": {"winner": mt_winner, "replace_now": mt_replace, "reason": mt_reason},
        "tts": {"winner": tts_winner, "replace_now": tts_replace, "reason": tts_reason},
        "llm_sidecar": llm_sidecar,
        "final_action_items": final_action_items,
    }

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "phase1_summary.json", summary)
    (out_dir / "phase1_summary.md").write_text(_build_md(summary), encoding="utf-8")

    print(str(out_dir / "phase1_summary.json"))
    print(str(out_dir / "phase1_summary.md"))


if __name__ == "__main__":
    main()
