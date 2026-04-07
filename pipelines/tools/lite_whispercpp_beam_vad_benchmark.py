#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from pipelines.lib.asr.lite_asr import Segment, extract_audio, run_asr_whispercpp, write_srt
from pipelines.lib.text.asr_normalize import load_asr_dict, normalize_asr_zh_text
from pipelines.lib.text.srt_io import read_srt_texts
from pipelines.lib.text.zh_convert import zh_to_simplified


DEFAULT_CANDIDATES: List[Dict[str, Any]] = [
    {"id": "beam2_vadoff", "label": "beam=2, VAD=off", "beam_size": 2, "vad_enable": False},
    {"id": "beam2_vadon", "label": "beam=2, VAD=on", "beam_size": 2, "vad_enable": True},
    {"id": "beam5_vadoff", "label": "beam=5, VAD=off", "beam_size": 5, "vad_enable": False},
    {"id": "beam5_vadon", "label": "beam=5, VAD=on", "beam_size": 5, "vad_enable": True},
    {"id": "beam8_vadoff", "label": "beam=8, VAD=off", "beam_size": 8, "vad_enable": False},
    {"id": "beam8_vadon", "label": "beam=8, VAD=on", "beam_size": 8, "vad_enable": True},
]


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


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _join_srt_text(path: Path) -> str:
    return "\n".join(t.strip() for t in read_srt_texts(path) if str(t).strip())


def _normalize_segments(segments: List[Segment], asr_dict: Dict[str, str]) -> List[Segment]:
    normalized: List[Segment] = []
    for seg in segments:
        normalized.append(
            Segment(
                start=float(seg.start),
                end=float(seg.end),
                text=normalize_asr_zh_text(seg.text, to_simplified_fn=zh_to_simplified, asr_dict=asr_dict),
                translation=getattr(seg, "translation", None),
            )
        )
    return normalized


def _candidate_entry(results: Dict[str, Any], candidate_id: str) -> Dict[str, Any]:
    for item in results["candidates"]:
        if item["id"] == candidate_id:
            return item
    raise KeyError(candidate_id)


def build_manifest(cases_path: Path, run_id: str, notes: str) -> Dict[str, Any]:
    return {
        "phase": "lite_phase1",
        "capability": "whispercpp_beam_vad",
        "run_id": run_id,
        "cases_path": str(cases_path),
        "notes": notes,
        "candidates": DEFAULT_CANDIDATES,
        "fairness": {
            "same_model": True,
            "same_threads": True,
            "same_dataset": True,
            "vary_only": ["beam_size", "vad_enable"],
        },
    }


def build_results_template(run_id: str) -> Dict[str, Any]:
    return {
        "phase": "lite_phase1",
        "capability": "whispercpp_beam_vad",
        "run_id": run_id,
        "baseline_candidate": "beam5_vadon",
        "candidates": [
            {
                "id": item["id"],
                "label": item["label"],
                "beam_size": item["beam_size"],
                "vad_enable": item["vad_enable"],
                "metrics": {
                    "elapsed_s_mean": None,
                    "passed_rate": None,
                    "fail_rate": None,
                    "artifacts_ok_rate": None,
                    "avg_segments": None,
                    "avg_chars": None,
                    "added_cost_vs_baseline": None,
                },
                "case_results": [],
                "notes": "",
            }
            for item in DEFAULT_CANDIDATES
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark whisper.cpp beam/VAD combinations on the same CN20 dataset.")
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--run-id", type=str, required=True)
    ap.add_argument("--notes", type=str, default="")
    ap.add_argument("--sample-rate", type=int, default=16000)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--whispercpp-bin", type=Path, default=Path("bin/whisper-cli"))
    ap.add_argument("--whispercpp-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin"))
    ap.add_argument("--whispercpp-threads", type=int, default=8)
    ap.add_argument("--vad-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin"))
    ap.add_argument("--vad-threshold", type=float, default=0.5)
    ap.add_argument("--vad-min-sil-ms", type=int, default=180)
    ap.add_argument("--asr-normalize-dict", type=Path, default=Path("assets/asr_normalize/asr_zh_dict.json"))
    args = ap.parse_args()

    rows = _read_jsonl(args.cases)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    shared_audio_root = out_dir / "shared_audio"
    runs_root = out_dir / "runs"
    asr_dict = load_asr_dict(args.asr_normalize_dict) if args.asr_normalize_dict else {}

    manifest = build_manifest(args.cases, args.run_id, args.notes)
    results = build_results_template(args.run_id)

    baseline_elapsed = None
    for cand in DEFAULT_CANDIDATES:
        cid = str(cand["id"])
        entry = _candidate_entry(results, cid)
        elapsed_ok: List[float] = []
        seg_counts: List[int] = []
        char_counts: List[int] = []
        case_results: List[Dict[str, Any]] = []
        fail_count = 0
        ok_count = 0

        for row in rows:
            case_id = str(row.get("id") or "").strip()
            video = Path(str(row.get("video") or ""))
            if not case_id:
                continue
            shared_case = shared_audio_root / case_id
            shared_case.mkdir(parents=True, exist_ok=True)
            audio_path = shared_case / "audio.wav"
            if not audio_path.exists():
                extract_audio(video_path=video, audio_path=audio_path, sample_rate=args.sample_rate, denoise=False)

            case_dir = runs_root / cid / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            audio_json = case_dir / "audio.json"
            chs_srt = case_dir / "chs.srt"
            pred_joined = ""
            artifact_ok = False
            err = ""
            status = "ok"
            seg_count = 0
            t0 = time.time()
            try:
                segments = run_asr_whispercpp(
                    audio_path=audio_path,
                    whisper_bin=args.whispercpp_bin,
                    model_path=args.whispercpp_model,
                    output_prefix=case_dir / "asr_whispercpp",
                    language="zh",
                    threads=args.whispercpp_threads,
                    beam_size=int(cand["beam_size"]),
                    vad_enable=bool(cand["vad_enable"]),
                    vad_model=args.vad_model if cand["vad_enable"] else None,
                    vad_thold=args.vad_threshold if cand["vad_enable"] else None,
                    vad_min_sil_ms=args.vad_min_sil_ms if cand["vad_enable"] else None,
                )
                segments = _normalize_segments(segments, asr_dict)
                seg_count = len(segments)
                audio_json.write_text(json.dumps([seg.__dict__ for seg in segments], ensure_ascii=False, indent=2), encoding="utf-8")
                write_srt(chs_srt, segments, text_attr="text")
                pred_joined = _join_srt_text(chs_srt)
                artifact_ok = audio_json.exists() and chs_srt.exists() and bool(pred_joined.strip())
                if not artifact_ok:
                    status = "artifact_missing"
            except Exception as exc:
                status = "failed"
                err = str(exc)
            elapsed_s = round(time.time() - t0, 4)
            if artifact_ok:
                ok_count += 1
                elapsed_ok.append(elapsed_s)
                seg_counts.append(seg_count)
                char_counts.append(len(pred_joined.replace("\n", "")))
            else:
                fail_count += 1
            case_results.append(
                {
                    "id": case_id,
                    "status": status,
                    "elapsed_s": elapsed_s,
                    "artifact_ok": artifact_ok,
                    "segments_count": seg_count,
                    "pred_chs_srt": str(chs_srt) if artifact_ok else "",
                    "pred_preview": "\n".join(pred_joined.splitlines()[:4]),
                    "error": err,
                }
            )

        total = max(1, len(case_results))
        entry["case_results"] = case_results
        entry["metrics"] = {
            "elapsed_s_mean": round(statistics.mean(elapsed_ok), 4) if elapsed_ok else None,
            "passed_rate": round(ok_count / total, 4),
            "fail_rate": round(fail_count / total, 4),
            "artifacts_ok_rate": round(ok_count / total, 4),
            "avg_segments": round(statistics.mean(seg_counts), 2) if seg_counts else None,
            "avg_chars": round(statistics.mean(char_counts), 2) if char_counts else None,
            "added_cost_vs_baseline": None,
        }
        entry["notes"] = "all cases completed" if fail_count == 0 else "inspect case_results"
        if cid == "beam5_vadon":
            baseline_elapsed = entry["metrics"]["elapsed_s_mean"]

    for entry in results["candidates"]:
        val = entry["metrics"].get("elapsed_s_mean")
        if val is not None and baseline_elapsed is not None:
            entry["metrics"]["added_cost_vs_baseline"] = round(float(val) - float(baseline_elapsed), 4)

    _write_json(out_dir / "manifest.json", manifest)
    _write_json(out_dir / "results_template.json", results)
    print(str(out_dir / "manifest.json"))
    print(str(out_dir / "results_template.json"))


if __name__ == "__main__":
    main()
