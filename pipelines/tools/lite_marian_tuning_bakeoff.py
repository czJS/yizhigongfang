#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from pipelines.lib.asr.lite_asr import Segment, write_srt
from pipelines.lib.glossary.entity_protect import protect_entities, restore_entities
from pipelines.lib.mt.mt import build_translator
from pipelines.lib.mt.mt_split import split_translation_by_src_lengths
from pipelines.lib.mt.sentence_unit import build_sentence_unit_groups
from pipelines.lib.text.translate_post import clean_en, dedupe_phrases, dedupe_repeats, protect_nums, restore, rule_polish
from pipelines.lib.text.zh_text import clean_zh_text


DEFAULT_CANDIDATES: List[Dict[str, str]] = [
    {"id": "marian_current", "label": "Marian current single inference"},
    {"id": "marian_batch", "label": "Marian batched inference"},
    {"id": "marian_ctranslate2", "label": "Marian CTranslate2 int8"},
]


@dataclass
class PreparedGroup:
    idxs: List[int]
    texts: List[str]
    merged_text: str
    protected_text: str
    nums: List[Tuple[str, str]]
    ent_used: List[Tuple[str, str]]


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


def _load_segments(audio_json: Path) -> List[Segment]:
    data = json.loads(audio_json.read_text(encoding="utf-8", errors="ignore") or "[]")
    return [Segment(**item) for item in data if isinstance(item, dict)]


def _prepare_groups(segments: List[Segment]) -> List[PreparedGroup]:
    merged = build_sentence_unit_groups(
        segments,
        enable=True,
        min_chars=12,
        max_chars=60,
        max_segs=3,
        max_gap_s=0.6,
        boundary_punct="。！？!?.,",
        break_words=["但", "而", "于是", "然后", "忽然", "突然", "不过", "结果", "同时"],
    )
    groups: List[PreparedGroup] = []
    for group in merged:
        idxs = [i for i, _ in group]
        texts = [s.text for _, s in group]
        merged_text = clean_zh_text(" ".join((t or "").strip() for t in texts))
        protected_text = merged_text
        ent_used: List[Tuple[str, str]] = []
        protected_text, nums = protect_nums(protected_text)
        groups.append(
            PreparedGroup(
                idxs=idxs,
                texts=texts,
                merged_text=merged_text,
                protected_text=protected_text,
                nums=nums,
                ent_used=ent_used,
            )
        )
    return groups


def _restore_and_split(group: PreparedGroup, raw_translation: str, segments: List[Segment]) -> List[Segment]:
    en = restore(str(raw_translation or ""), group.nums)
    en = restore_entities(en, group.ent_used)
    pieces = split_translation_by_src_lengths(group.texts, en)
    out: List[Segment] = []
    for i, piece in enumerate(pieces):
        seg_idx = group.idxs[i] if i < len(group.idxs) else group.idxs[-1]
        seg = segments[seg_idx]
        piece_clean = dedupe_phrases(dedupe_repeats(rule_polish(clean_en(piece))))
        out.append(Segment(start=seg.start, end=seg.end, text=seg.text, translation=piece_clean))
    return out


def _translate_case_current(segments: List[Segment], translate_fn) -> List[Segment]:
    groups = _prepare_groups(segments)
    results: List[Segment] = []
    for group in groups:
        en = translate_fn(group.protected_text)
        results.extend(_restore_and_split(group, en, segments))
    results.sort(key=lambda s: s.start)
    return results


def _build_hf_batch_runner(model_dir: Path, device: str, cache_dir: Optional[str]):
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore
    import torch  # type: ignore

    runtime_device = "cpu" if device == "cpu" else device
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), cache_dir=cache_dir, local_files_only=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(str(model_dir), cache_dir=cache_dir, local_files_only=True)
    model = model.to(runtime_device)
    model.eval()

    def run_batch(texts: Sequence[str]) -> List[str]:
        if not texts:
            return []
        encoded = tokenizer(
            list(texts),
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        encoded = {k: v.to(runtime_device) for k, v in encoded.items()}
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                num_beams=4,
                max_new_tokens=256,
                renormalize_logits=True,
            )
        return [x.strip() for x in tokenizer.batch_decode(generated, skip_special_tokens=True)]

    return run_batch


def _translate_case_batch(segments: List[Segment], batch_runner, batch_size: int) -> List[Segment]:
    groups = _prepare_groups(segments)
    texts = [g.protected_text for g in groups]
    translations: List[str] = []
    for i in range(0, len(texts), max(1, batch_size)):
        translations.extend(batch_runner(texts[i : i + max(1, batch_size)]))
    results: List[Segment] = []
    for group, en in zip(groups, translations):
        results.extend(_restore_and_split(group, en, segments))
    results.sort(key=lambda s: s.start)
    return results


def _ensure_ct2_model(model_dir: Path, out_dir: Path, force: bool = False) -> Path:
    import ctranslate2  # type: ignore

    if out_dir.exists() and (out_dir / "model.bin").exists() and not force:
        return out_dir
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    converter = ctranslate2.converters.TransformersConverter(
        str(model_dir),
        copy_files=["tokenizer_config.json", "vocab.json", "source.spm", "target.spm"],
    )
    converter.convert(str(out_dir), quantization="int8")
    return out_dir


def _build_ct2_batch_runner(model_dir: Path, ct2_dir: Path, batch_size: int):
    import ctranslate2  # type: ignore
    import sentencepiece as spm  # type: ignore

    ct2_model_dir = _ensure_ct2_model(model_dir=model_dir, out_dir=ct2_dir)
    src_sp = spm.SentencePieceProcessor(model_file=str(model_dir / "source.spm"))
    tgt_sp = spm.SentencePieceProcessor(model_file=str(model_dir / "target.spm"))
    translator = ctranslate2.Translator(str(ct2_model_dir), device="cpu", compute_type="int8")

    def run_batch(texts: Sequence[str]) -> List[str]:
        if not texts:
            return []
        source = [src_sp.encode(t, out_type=str) for t in texts]
        results = translator.translate_batch(source, beam_size=4, max_decoding_length=256, batch_type="examples", max_batch_size=max(1, batch_size))
        return [tgt_sp.decode(res.hypotheses[0]) for res in results]

    return run_batch


def build_manifest(cases_path: Path, cases_count: int, run_id: str, notes: str) -> Dict[str, Any]:
    return {
        "phase": "lite_phase1",
        "capability": "mt_marian_tuning",
        "run_id": run_id,
        "cases_path": str(cases_path),
        "cases_count": cases_count,
        "notes": notes,
        "candidates": DEFAULT_CANDIDATES,
        "fairness": {
            "same_cases": True,
            "same_sentence_unit_rules": True,
            "same_postprocess_rules": True,
            "same_model_family": "Helsinki-NLP/opus-mt-zh-en",
        },
    }


def build_results_template(run_id: str) -> Dict[str, Any]:
    return {
        "phase": "lite_phase1",
        "capability": "mt_marian_tuning",
        "run_id": run_id,
        "baseline_candidate": "marian_current",
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
                },
                "case_results": [],
                "notes": "",
            }
            for item in DEFAULT_CANDIDATES
        ],
    }


def _candidate_entry(results: Dict[str, Any], candidate_id: str) -> Dict[str, Any]:
    for item in results["candidates"]:
        if item["id"] == candidate_id:
            return item
    raise KeyError(candidate_id)


def _run_candidate(
    candidate_id: str,
    rows: List[Dict[str, Any]],
    out_dir: Path,
    marian_model_dir: Path,
    ct2_model_dir: Path,
    batch_size: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    case_results: List[Dict[str, Any]] = []
    elapsed_ok: List[float] = []
    fail_count = 0
    ok_count = 0

    current_fn = None
    batch_fn = None
    ct2_fn = None
    if candidate_id == "marian_current":
        current_fn = build_translator(str(marian_model_dir), device="cpu", cache_dir=None, offline=True)
    elif candidate_id == "marian_batch":
        batch_fn = _build_hf_batch_runner(marian_model_dir, device="cpu", cache_dir=None)
    elif candidate_id == "marian_ctranslate2":
        ct2_fn = _build_ct2_batch_runner(marian_model_dir, ct2_dir=ct2_model_dir, batch_size=batch_size)
    else:
        raise RuntimeError(f"unknown candidate: {candidate_id}")

    for row in rows:
        case_id = str(row["id"])
        audio_json = Path(str(row["audio_json"]))
        segments = _load_segments(audio_json)
        case_dir = out_dir / "runs" / candidate_id / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        eng_srt = case_dir / "eng.srt"
        eng_json = case_dir / "eng.json"
        err = ""
        artifact_ok = False
        t0 = time.time()
        try:
            if candidate_id == "marian_current":
                seg_en = _translate_case_current(segments, current_fn)
            elif candidate_id == "marian_batch":
                seg_en = _translate_case_batch(segments, batch_fn, batch_size=batch_size)
            else:
                seg_en = _translate_case_batch(segments, ct2_fn, batch_size=batch_size)
            elapsed = round(time.time() - t0, 4)
            write_srt(eng_srt, seg_en, text_attr="translation")
            eng_json.write_text(json.dumps([seg.__dict__ for seg in seg_en], ensure_ascii=False, indent=2), encoding="utf-8")
            pred_preview = "\n".join([str(seg.translation).strip() for seg in seg_en if str(seg.translation).strip()][:4]).strip()
            source_preview = "\n".join([str(seg.text).strip() for seg in seg_en if str(seg.text).strip()][:4]).strip()
            artifact_ok = eng_srt.exists() and bool(pred_preview)
            if artifact_ok:
                ok_count += 1
                elapsed_ok.append(elapsed)
                status = "ok"
            else:
                fail_count += 1
                status = "artifact_missing"
            case_results.append(
                {
                    "id": case_id,
                    "status": status,
                    "elapsed_s": elapsed,
                    "artifact_ok": artifact_ok,
                    "error": "",
                    "pred_eng_srt": str(eng_srt),
                    "source_preview": source_preview,
                    "pred_preview": pred_preview,
                }
            )
        except Exception as exc:
            fail_count += 1
            case_results.append(
                {
                    "id": case_id,
                    "status": "error",
                    "elapsed_s": None,
                    "artifact_ok": False,
                    "error": str(exc),
                    "pred_eng_srt": "",
                    "source_preview": "",
                    "pred_preview": "",
                }
            )
    total = max(1, len(case_results))
    metrics = {
        "elapsed_s_mean": round(statistics.mean(elapsed_ok), 4) if elapsed_ok else None,
        "passed_rate": round(ok_count / total, 4),
        "fail_rate": round(fail_count / total, 4),
        "artifacts_ok_rate": round(ok_count / total, 4),
        "added_cost_vs_baseline": None,
    }
    return case_results, metrics


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark Marian current, batched, and CTranslate2 variants on the same cn20 MT set.")
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--run-id", type=str, required=True)
    ap.add_argument("--notes", type=str, default="")
    ap.add_argument("--marian-model-dir", type=Path, default=Path("assets/models/lite_mt_marian_opus_mt_zh_en"))
    ap.add_argument("--ct2-model-dir", type=Path, default=Path("assets/models/ct2_marian_zh_en"))
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    rows = _read_jsonl(args.cases)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    results = build_results_template(args.run_id)
    manifest = build_manifest(args.cases, len(rows), args.run_id, args.notes)

    baseline_elapsed = None
    for candidate in DEFAULT_CANDIDATES:
        cid = candidate["id"]
        case_results, metrics = _run_candidate(
            candidate_id=cid,
            rows=rows,
            out_dir=out_dir,
            marian_model_dir=args.marian_model_dir,
            ct2_model_dir=args.ct2_model_dir,
            batch_size=args.batch_size,
        )
        entry = _candidate_entry(results, cid)
        entry["case_results"] = case_results
        entry["metrics"] = metrics
        entry["notes"] = "all cases completed" if metrics["fail_rate"] == 0 else "inspect case_results"
        if cid == "marian_current":
            baseline_elapsed = metrics["elapsed_s_mean"]

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
