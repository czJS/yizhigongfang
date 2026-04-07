#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from pipelines.lib.asr.lite_asr import Segment, extract_audio, run_asr_whispercpp, write_srt
from pipelines.lib.text.asr_normalize import load_asr_dict, normalize_asr_zh_text
from pipelines.lib.text.srt_io import read_srt_texts
from pipelines.lib.text.zh_convert import zh_to_simplified


DEFAULT_CANDIDATES: List[Dict[str, str]] = [
    {"id": "whispercpp", "label": "whisper.cpp + quantized model"},
    {"id": "whispercpp_final", "label": "whisper.cpp final tuned"},
    {"id": "faster_whisper_int8", "label": "faster-whisper int8 / CPU"},
    {"id": "faster_whisper_int8_tuned", "label": "faster-whisper int8 / CPU tuned"},
    {"id": "sherpa_paraformer", "label": "sherpa-onnx / paraformer"},
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


def _levenshtein(a: List[str], b: List[str]) -> int:
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    cur = [0] * (m + 1)
    for i in range(1, n + 1):
        cur[0] = i
        ai = a[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev, cur = cur, prev
    return prev[m]


def _chars_zh(s: str) -> List[str]:
    return list((s or "").replace(" ", "").strip())


def _join_srt_text(path: Path) -> str:
    return "\n".join(t.strip() for t in read_srt_texts(path) if str(t).strip())


def _cer(ref_zh: str, pred_zh: str) -> float:
    ref_n = normalize_asr_zh_text(ref_zh)
    pred_n = normalize_asr_zh_text(pred_zh)
    ref_chars = _chars_zh(ref_n)
    pred_chars = _chars_zh(pred_n)
    edits = _levenshtein(ref_chars, pred_chars)
    return float(edits / max(1, len(ref_chars)))


def _resolve_local_snapshot(root: Path, repo_id: str, required_files: Optional[List[str]] = None) -> Optional[Path]:
    required = required_files or ["model.bin", "config.json"]
    repo_dir = root / ("models--" + repo_id.replace("/", "--"))
    try:
        if repo_dir.exists() and all((repo_dir / f).exists() for f in required):
            return repo_dir
    except Exception:
        pass
    snap_root = repo_dir / "snapshots"
    if not snap_root.exists():
        return None
    candidates: List[Path] = []
    for snap in sorted(snap_root.iterdir()):
        if snap.is_dir() and all((snap / f).exists() for f in required):
            candidates.append(snap)
    return candidates[-1] if candidates else None


def _normalize_segments(segments: List[Segment], asr_dict: Dict[str, str]) -> List[Segment]:
    out: List[Segment] = []
    for seg in segments:
        out.append(
            Segment(
                start=float(seg.start),
                end=float(seg.end),
                text=normalize_asr_zh_text(seg.text, to_simplified_fn=zh_to_simplified, asr_dict=asr_dict),
                translation=getattr(seg, "translation", None),
            )
        )
    return out


def _audio_duration_s(audio_path: Path) -> float:
    try:
        with wave.open(str(audio_path), "rb") as wav_in:
            frames = float(wav_in.getnframes())
            rate = float(wav_in.getframerate() or 1.0)
            return max(0.0, frames / rate)
    except Exception:
        return 0.0


def _run_faster_whisper_cpu(
    audio_path: Path,
    model_root: Path,
    repo_id: str,
    profile: str = "base",
    cpu_threads: int = 4,
) -> List[Segment]:
    if not importlib.util.find_spec("faster_whisper"):
        raise RuntimeError("python module not available: faster_whisper")
    snap = _resolve_local_snapshot(model_root, repo_id, required_files=["model.bin", "config.json"])
    if not snap:
        raise RuntimeError(f"local faster-whisper snapshot not found for repo: {repo_id}")
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(str(snap), device="cpu", compute_type="int8", cpu_threads=max(1, int(cpu_threads)), num_workers=1)
    kwargs: Dict[str, Any] = {
        "language": "zh",
        "task": "transcribe",
        "beam_size": 1,
        "best_of": 1,
        "condition_on_previous_text": False,
        "chunk_length": 20,
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "word_timestamps": True,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 2000, "speech_pad_ms": 320},
    }
    if profile == "tuned":
        kwargs.update(
            {
                "chunk_length": 30,
                "compression_ratio_threshold": 2.8,
                "log_prob_threshold": -2.0,
                "no_speech_threshold": 0.2,
                "word_timestamps": False,
                "vad_filter": False,
                "temperature": 0.0,
            }
        )
    elif profile == "final":
        kwargs.update(
            {
                "chunk_length": 30,
                "compression_ratio_threshold": 2.4,
                "log_prob_threshold": -1.0,
                "no_speech_threshold": 0.45,
                "word_timestamps": False,
                "vad_filter": True,
                "vad_parameters": {"min_silence_duration_ms": 700, "speech_pad_ms": 160},
                "temperature": 0.0,
            }
        )
    seg_iter, _info = model.transcribe(
        str(audio_path),
        **kwargs,
    )
    out: List[Segment] = []
    for s in seg_iter:
        out.append(
            Segment(
                start=float(getattr(s, "start", 0.0) or 0.0),
                end=float(getattr(s, "end", 0.0) or 0.0),
                text=str(getattr(s, "text", "")).strip(),
            )
        )
    return out


def _resolve_sherpa_paraformer_dir(model_root: Path) -> Path:
    required = ["tokens.txt"]
    direct_candidates = [model_root]
    if model_root.exists():
        direct_candidates.extend([p for p in sorted(model_root.iterdir()) if p.is_dir()])
    for cand in direct_candidates:
        if any((cand / name).exists() for name in ["model.int8.onnx", "model.onnx"]) and all((cand / f).exists() for f in required):
            return cand
    raise RuntimeError(f"sherpa paraformer model files not found under: {model_root}")


def _run_sherpa_paraformer(audio_path: Path, model_root: Path, num_threads: int = 4) -> List[Segment]:
    if not importlib.util.find_spec("sherpa_onnx"):
        raise RuntimeError("python module not available: sherpa_onnx")
    model_dir = _resolve_sherpa_paraformer_dir(model_root)
    model_path = model_dir / "model.int8.onnx"
    if not model_path.exists():
        model_path = model_dir / "model.onnx"
    if not model_path.exists():
        raise RuntimeError(f"sherpa paraformer model missing under: {model_dir}")
    tokens_path = model_dir / "tokens.txt"
    cmd = [
        "sherpa-onnx-offline",
        f"--tokens={tokens_path}",
        f"--paraformer={model_path}",
        f"--num-threads={max(1, int(num_threads))}",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"sherpa-onnx-offline failed: {msg}")
    payload: Optional[Dict[str, Any]] = None
    for line in reversed((proc.stdout or "").splitlines()):
        raw = line.strip()
        if not raw.startswith("{"):
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict) and "text" in obj:
            payload = obj
            break
    if not payload:
        raise RuntimeError("sherpa-onnx-offline returned no JSON text payload")
    text = str(payload.get("text") or "").strip()
    if not text:
        return []
    dur = _audio_duration_s(audio_path)
    end_s = dur if dur > 0 else 1.0
    return [Segment(start=0.0, end=end_s, text=text)]


def _run_candidate(
    candidate_id: str,
    audio_path: Path,
    case_dir: Path,
    whispercpp_bin: Path,
    whispercpp_model: Path,
    whispercpp_threads: Optional[int],
    whispercpp_final_threads: int,
    whispercpp_vad_model: Optional[Path],
    whispercpp_vad_thold: Optional[float],
    whispercpp_vad_min_sil_ms: Optional[int],
    faster_model_root: Path,
    faster_repo_id: str,
    sherpa_paraformer_model_root: Path,
    sherpa_threads: int,
    asr_dict: Dict[str, str],
) -> List[Segment]:
    if candidate_id == "whispercpp":
        segments = run_asr_whispercpp(
            audio_path=audio_path,
            whisper_bin=whispercpp_bin,
            model_path=whispercpp_model,
            output_prefix=case_dir / "asr_whispercpp",
            language="zh",
            threads=whispercpp_threads,
        )
        return _normalize_segments(segments, asr_dict)
    if candidate_id == "whispercpp_final":
        segments = run_asr_whispercpp(
            audio_path=audio_path,
            whisper_bin=whispercpp_bin,
            model_path=whispercpp_model,
            output_prefix=case_dir / "asr_whispercpp_final",
            language="zh",
            threads=whispercpp_final_threads,
            vad_enable=whispercpp_vad_model is not None,
            vad_model=whispercpp_vad_model,
            vad_thold=whispercpp_vad_thold,
            vad_min_sil_ms=whispercpp_vad_min_sil_ms,
        )
        return _normalize_segments(segments, asr_dict)
    if candidate_id == "faster_whisper_int8":
        segments = _run_faster_whisper_cpu(audio_path=audio_path, model_root=faster_model_root, repo_id=faster_repo_id, profile="base", cpu_threads=4)
        return _normalize_segments(segments, asr_dict)
    if candidate_id == "faster_whisper_int8_tuned":
        segments = _run_faster_whisper_cpu(audio_path=audio_path, model_root=faster_model_root, repo_id=faster_repo_id, profile="tuned", cpu_threads=4)
        return _normalize_segments(segments, asr_dict)
    if candidate_id == "sherpa_paraformer":
        segments = _run_sherpa_paraformer(audio_path=audio_path, model_root=sherpa_paraformer_model_root, num_threads=sherpa_threads)
        return _normalize_segments(segments, asr_dict)
    raise RuntimeError(f"unknown candidate: {candidate_id}")


def _select_candidates(candidate_ids_raw: str) -> List[Dict[str, str]]:
    raw = [part.strip() for part in str(candidate_ids_raw or "").split(",") if part.strip()]
    if not raw:
        return list(DEFAULT_CANDIDATES)
    want = set(raw)
    selected = [item for item in DEFAULT_CANDIDATES if item["id"] in want]
    if not selected:
        raise SystemExit(f"no candidate selected from: {sorted(want)}")
    return selected


def build_manifest(cases_path: Path, cases_count: int, run_id: str, hardware_tier: str, notes: str, candidates: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "phase": "lite_phase1",
        "capability": "asr",
        "run_id": run_id,
        "hardware_tier": hardware_tier,
        "cases_path": str(cases_path),
        "cases_count": cases_count,
        "notes": notes,
        "baseline_candidate": str(candidates[0]["id"]),
        "candidates": candidates,
        "decision_rules": {
            "hard_metrics": ["elapsed_s_mean", "passed_rate", "fail_rate", "artifacts_ok_rate"],
            "allow_replace_only_if": [
                "passed_rate_not_worse_than_baseline",
                "fail_rate_not_worse_than_baseline",
                "artifacts_ok_rate_not_worse_than_baseline",
            ],
        },
    }


def build_results_template(run_id: str, hardware_tier: str, candidates: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "phase": "lite_phase1",
        "capability": "asr",
        "run_id": run_id,
        "hardware_tier": hardware_tier,
        "baseline_candidate": str(candidates[0]["id"]),
        "candidates": [
            {
                "id": item["id"],
                "label": item["label"],
                "metrics": {
                    "elapsed_s_mean": None,
                    "passed_rate": None,
                    "fail_rate": None,
                    "artifacts_ok_rate": None,
                    "added_cost_vs_baseline": None,
                    "subjective_score": None,
                    "replace_recommendation": None,
                    "cer_mean": None,
                },
                "worst_samples": [],
                "case_results": [],
                "notes": "",
            }
            for item in candidates
        ],
    }


def build_readme(run_id: str, hardware_tier: str, cases_count: int, candidates: List[Dict[str, str]]) -> str:
    candidate_lines = "\n".join(f"- `{item['id']}`" for item in candidates)
    return f"""# lite_phase1 ASR bakeoff

run_id: `{run_id}`
hardware_tier: `{hardware_tier}`
cases_count: `{cases_count}`

This folder is generated by `lite_phase1_asr_bakeoff.py`.

Files:
- `manifest.json`: static experiment definition
- `results_template.json`: result file (template or measured)

Phase1 candidates:
{candidate_lines}
"""


def _candidate_entry(results: Dict[str, Any], candidate_id: str) -> Dict[str, Any]:
    for item in results.get("candidates", []):
        if isinstance(item, dict) and str(item.get("id") or "") == candidate_id:
            return item
    raise KeyError(candidate_id)


def _execute(args: argparse.Namespace, rows: List[Dict[str, Any]], results: Dict[str, Any]) -> None:
    out_dir: Path = args.out_dir
    shared_audio_root = out_dir / "shared_audio"
    runs_root = out_dir / "runs"
    asr_dict = load_asr_dict(args.asr_normalize_dict) if args.asr_normalize_dict else {}

    selected_candidates = _select_candidates(getattr(args, "candidate_ids", ""))
    for cand in selected_candidates:
        candidate_id = str(cand["id"])
        candidate_entry = _candidate_entry(results, candidate_id)
        case_results: List[Dict[str, Any]] = []
        elapsed_ok: List[float] = []
        cer_ok: List[float] = []

        for row in rows[: args.limit if args.limit and args.limit > 0 else None]:
            case_id = str(row.get("id") or "").strip()
            video = Path(str(row.get("video") or ""))
            meta = row.get("meta") or {}
            gold_chs_raw = str(meta.get("gold_chs_srt") or "").strip()
            gold_chs = Path(gold_chs_raw) if gold_chs_raw else None
            if not case_id:
                continue

            shared_case = shared_audio_root / case_id
            shared_case.mkdir(parents=True, exist_ok=True)
            audio_path = shared_case / "audio.wav"
            if not audio_path.exists():
                extract_audio(video_path=video, audio_path=audio_path, sample_rate=args.sample_rate, denoise=False)

            case_dir = runs_root / candidate_id / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            audio_json = case_dir / "audio.json"
            chs_srt = case_dir / "chs.srt"
            t0 = time.time()
            err = ""
            cer_val: Optional[float] = None
            pred_joined = ""
            ref_joined = ""
            status = "ok"
            artifact_ok = False
            try:
                segments = _run_candidate(
                    candidate_id=candidate_id,
                    audio_path=audio_path,
                    case_dir=case_dir,
                    whispercpp_bin=args.whispercpp_bin,
                    whispercpp_model=args.whispercpp_model,
                    whispercpp_threads=args.whispercpp_threads,
                    whispercpp_final_threads=args.whispercpp_final_threads,
                    whispercpp_vad_model=args.whispercpp_vad_model,
                    whispercpp_vad_thold=args.whispercpp_vad_thold,
                    whispercpp_vad_min_sil_ms=args.whispercpp_vad_min_sil_ms,
                    faster_model_root=args.faster_whisper_model_root,
                    faster_repo_id=args.faster_whisper_repo_id,
                    sherpa_paraformer_model_root=args.sherpa_paraformer_model_root,
                    sherpa_threads=args.sherpa_threads,
                    asr_dict=asr_dict,
                )
                audio_json.write_text(json.dumps([seg.__dict__ for seg in segments], ensure_ascii=False, indent=2), encoding="utf-8")
                write_srt(chs_srt, segments, text_attr="text")
                pred_joined = _join_srt_text(chs_srt)
                if gold_chs and gold_chs.exists():
                    ref_joined = _join_srt_text(gold_chs)
                    cer_val = _cer(ref_joined, pred_joined)
                artifact_ok = audio_json.exists() and chs_srt.exists() and bool(pred_joined.strip())
            except Exception as exc:
                status = "failed"
                err = str(exc)
            elapsed = float(time.time() - t0)

            final_status = status
            if final_status == "ok" and not artifact_ok:
                final_status = "artifact_missing"

            rec = {
                "id": case_id,
                "status": final_status,
                "elapsed_s": round(elapsed, 4),
                "artifact_ok": artifact_ok,
                "cer": round(cer_val, 6) if cer_val is not None else None,
                "error": err,
                "video": str(video),
                "gold_chs_srt": str(gold_chs) if gold_chs else "",
                "pred_chs_srt": str(chs_srt),
                "ref_preview": ref_joined[:200],
                "pred_preview": pred_joined[:200],
            }
            case_results.append(rec)
            if artifact_ok:
                elapsed_ok.append(elapsed)
            if artifact_ok and cer_val is not None:
                cer_ok.append(cer_val)

        total = max(1, len(case_results))
        fail_count = sum(1 for r in case_results if r["status"] != "ok")
        ok_count = sum(1 for r in case_results if r["artifact_ok"])
        baseline_elapsed = None
        if candidate_id != "whispercpp":
            try:
                baseline_metrics = _candidate_entry(results, "whispercpp").get("metrics") or {}
                baseline_elapsed = baseline_metrics.get("elapsed_s_mean")
            except Exception:
                baseline_elapsed = None
        elapsed_mean = statistics.mean(elapsed_ok) if elapsed_ok else None
        added_cost = None
        if elapsed_mean is not None and baseline_elapsed not in (None, 0):
            try:
                added_cost = float(elapsed_mean) - float(baseline_elapsed)
            except Exception:
                added_cost = None

        worst = sorted(
            [r for r in case_results if r["cer"] is not None],
            key=lambda x: float(x["cer"] or 0.0),
            reverse=True,
        )[: int(args.topk)]

        candidate_entry["metrics"] = {
            "elapsed_s_mean": round(elapsed_mean, 4) if elapsed_mean is not None else None,
            "passed_rate": round(ok_count / total, 6),
            "fail_rate": round(fail_count / total, 6),
            "artifacts_ok_rate": round(ok_count / total, 6),
            "added_cost_vs_baseline": round(added_cost, 4) if added_cost is not None else None,
            "subjective_score": None,
            "replace_recommendation": None,
            "cer_mean": round(statistics.mean(cer_ok), 6) if cer_ok else None,
        }
        candidate_entry["worst_samples"] = worst
        candidate_entry["case_results"] = case_results
        if fail_count:
            candidate_entry["notes"] = f"{fail_count}/{total} cases failed; inspect case_results.error"
        else:
            candidate_entry["notes"] = "all cases completed"


def main() -> None:
    ap = argparse.ArgumentParser(description="Run or generate phase1 ASR bakeoff for lite mode.")
    ap.add_argument("--cases", type=Path, required=True, help="Input cases jsonl")
    ap.add_argument("--out-dir", type=Path, required=True, help="Output directory")
    ap.add_argument("--hardware-tier", type=str, default="normal", choices=["normal", "mid", "high"])
    ap.add_argument("--run-id", type=str, required=True, help="Run identifier")
    ap.add_argument("--notes", type=str, default="", help="Optional note")
    ap.add_argument("--execute", action="store_true", help="Run real candidate execution instead of only generating templates")
    ap.add_argument("--limit", type=int, default=0, help="Optional limit for cases (0 means all)")
    ap.add_argument("--topk", type=int, default=5, help="Top-K worst samples to keep per candidate")
    ap.add_argument("--sample-rate", type=int, default=16000, help="Audio extraction sample rate")
    ap.add_argument("--asr-normalize-dict", type=Path, default=Path("assets/asr_normalize/asr_zh_dict.json"))
    ap.add_argument("--whispercpp-bin", type=Path, default=Path("bin/whisper-cli"))
    ap.add_argument("--whispercpp-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin"))
    ap.add_argument("--whispercpp-threads", type=int, default=None)
    ap.add_argument("--whispercpp-final-threads", type=int, default=8)
    ap.add_argument("--whispercpp-vad-model", type=Path, default=None)
    ap.add_argument("--whispercpp-vad-thold", type=float, default=0.5)
    ap.add_argument("--whispercpp-vad-min-sil-ms", type=int, default=180)
    ap.add_argument("--faster-whisper-model-root", type=Path, default=Path("assets/models/quality_asr_whisperx"))
    ap.add_argument("--faster-whisper-repo-id", type=str, default="Systran/faster-whisper-medium")
    ap.add_argument("--sherpa-paraformer-model-root", type=Path, default=Path("assets/models/lite_asr_sherpa_paraformer"))
    ap.add_argument("--sherpa-threads", type=int, default=4)
    ap.add_argument("--candidate-ids", type=str, default="", help="Comma separated candidate ids to run; empty means all")
    args = ap.parse_args()

    rows = _read_jsonl(args.cases)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    selected_candidates = _select_candidates(args.candidate_ids)
    manifest = build_manifest(args.cases, len(rows[: args.limit if args.limit and args.limit > 0 else None]), args.run_id, args.hardware_tier, args.notes, selected_candidates)
    results = build_results_template(args.run_id, args.hardware_tier, selected_candidates)

    if args.execute:
        _execute(args=args, rows=rows, results=results)

    _write_json(out_dir / "manifest.json", manifest)
    _write_json(out_dir / "results_template.json", results)
    (out_dir / "README.md").write_text(build_readme(args.run_id, args.hardware_tier, len(rows), selected_candidates), encoding="utf-8")

    print(str(out_dir / "manifest.json"))
    print(str(out_dir / "results_template.json"))


if __name__ == "__main__":
    main()
