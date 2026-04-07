import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _srt_time_to_s(t: str) -> float:
    s = str(t or "").strip()
    m = re.match(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})", s)
    if not m:
        m2 = re.match(r"^(\d{2}):(\d{2}):(\d{2})$", s)
        if not m2:
            return 0.0
        hh, mm, ss = [int(x) for x in m2.groups()]
        return float(hh * 3600 + mm * 60 + ss)
    hh, mm, ss, ms = m.groups()
    ms_i = int(ms.ljust(3, "0")[:3])
    return float(int(hh) * 3600 + int(mm) * 60 + int(ss) + ms_i / 1000.0)


def _parse_srt_blocks(raw: str) -> List[Dict[str, Any]]:
    s = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = s.split("\n")
    out: List[Dict[str, Any]] = []
    i = 0

    def is_timing(ln: str) -> bool:
        return "-->" in (ln or "")

    while i < len(lines):
        while i < len(lines) and not (lines[i] or "").strip():
            i += 1
        if i >= len(lines):
            break
        idx_line = (lines[i] or "").strip()
        try:
            _ = int(idx_line)
        except Exception:
            i += 1
            continue
        i += 1
        if i >= len(lines):
            break
        timing = (lines[i] or "").strip()
        if not is_timing(timing):
            i += 1
            continue
        parts = timing.split("-->")
        start = (parts[0] or "").strip()
        end = (parts[1] or "").strip() if len(parts) > 1 else ""
        i += 1
        texts: List[str] = []
        while i < len(lines) and (lines[i] or "").strip():
            texts.append(lines[i])
            i += 1
        out.append({"start": start, "end": end, "text": "\n".join(texts).strip()})
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, required=True)
    # 默认使用本地 Ollama（OpenAI 兼容 /v1）；如需其他服务请显式传参覆盖
    p.add_argument("--llm-endpoint", default="http://127.0.0.1:11434/v1")
    p.add_argument("--llm-model", default="qwen3.5:9b")
    p.add_argument("--llm-api-key", default="")
    p.add_argument("--max-spans", type=int, default=2)
    p.add_argument("--max-total", type=int, default=16)
    p.add_argument("--candidate-max-lines", type=int, default=24)
    p.add_argument("--force-one-per-line", action="store_true")
    p.add_argument("--idiom-enable", action="store_true")
    p.add_argument("--idiom-path", default="assets/zh_phrase/idioms_4char.txt")
    p.add_argument("--same-pinyin-path", default="assets/zh_phrase/pycorrector_same_pinyin.txt")
    args = p.parse_args()

    out_dir: Path = args.output_dir
    if not out_dir.exists():
        print(json.dumps({"error": f"output-dir not found: {out_dir}"}, ensure_ascii=False))
        sys.exit(2)

    chs_base = out_dir / "chs.srt"
    chs_review = out_dir / "chs.review.srt"
    chs_path = chs_review if chs_review.exists() else chs_base
    if not chs_path.exists():
        print(json.dumps({"error": "chs.srt/chs.review.srt not found"}, ensure_ascii=False))
        sys.exit(2)

    raw = chs_path.read_text(encoding="utf-8", errors="ignore")
    blocks = _parse_srt_blocks(raw)

    # Import heavy pipeline code only in this subprocess.
    from pipelines.quality_pipeline_impl import (  # type: ignore
        Segment,
        _apply_zh_glossary_inplace,
        _extract_zh_risky_spans_llm_two_pass,
        _force_span_from_line,
        _idiom_spans_from_line,
        _idiom_spans_from_line_fuzzy,
        _load_idioms_4char,
        _load_same_pinyin_char_map,
        _cap_repeated_spans_by_text,
        _merge_dedupe_spans_same_line,
        _pattern_spans_from_line,
        _rule_based_suspect,
    )
    from pipelines.lib.glossary.glossary import load_glossary  # type: ignore

    segments: List[Any] = []
    items: List[Tuple[int, str]] = []
    for i, b in enumerate(blocks, 1):
        st = _srt_time_to_s(str(b.get("start") or "0"))
        ed = _srt_time_to_s(str(b.get("end") or "0"))
        txt = str(b.get("text") or "").strip()
        segments.append(Segment(start=float(st), end=float(ed), text=txt))
        items.append((int(i), txt))

    # Apply rules center ZH->ZH fixes BEFORE extraction (so UI sees phrases from corrected text),
    # even when we only refresh artifacts without resuming the full pipeline.
    zh_fix_hits = 0
    try:
        gpath = out_dir / ".ygf_rules" / "glossary.json"
        glossary = load_glossary(gpath if gpath.exists() else None)
        zh_fix_hits = int(_apply_zh_glossary_inplace(segments, glossary) or 0)
        if zh_fix_hits:
            items = [(int(i), str(getattr(seg, "text", "") or "").strip()) for i, seg in enumerate(segments, 1)]
            print(f"[reextract] applied zh glossary fixes: hits={zh_fix_hits}/{len(segments)}")
    except Exception:
        zh_fix_hits = 0

    spans_by_idx: Dict[int, List[Dict[str, Any]]] = {}
    phrase_items: List[Dict[str, Any]] = []
    suspects: List[Dict[str, Any]] = []
    zh_phrase_error = ""

    # Rule reasons
    rule_reasons_by_idx: Dict[int, List[str]] = {}
    for i, seg in enumerate(segments, 1):
        rr = _rule_based_suspect(seg)
        if rr:
            rule_reasons_by_idx[int(i)] = rr

    def score_line(text: str) -> float:
        t = str(text or "").strip()
        c = re.sub(r"\s+", "", t)
        if not c:
            return 0.0
        L = len(c)
        base = min(max(L, 0), 24) / 24.0
        has4 = 1.0 if re.search(r"[\u4e00-\u9fff]{4}", c) else 0.0
        mixed = 1.0 if re.search(r"[A-Za-z0-9]", c) and re.search(r"[\u4e00-\u9fff]", c) else 0.0
        quoted = 1.0 if any(x in t for x in ["“", "”", "\"", "「", "」", "『", "』"]) else 0.0
        emph = 1.0 if re.search(r"[！？!?.…]", t) else 0.0
        nick = 1.0 if re.search(r"[\u4e00-\u9fff]{1,3}(哥|姐|爷|叔|婶|妹|弟|总|哥们)$", c) else 0.0
        return 0.52 * base + 0.25 * has4 + 0.10 * mixed + 0.06 * quoted + 0.03 * emph + 0.04 * nick

    def pick_candidates(all_items: List[Tuple[int, str]], include: List[int], max_lines: int) -> List[Tuple[int, str]]:
        if max_lines <= 0 or len(all_items) <= max_lines:
            return all_items
        include_set = set(int(x) for x in include if int(x) > 0)
        pinned = [(i, t) for i, t in all_items if int(i) in include_set]
        rest = [(i, t) for i, t in all_items if int(i) not in include_set]
        budget = max(1, int(max_lines))
        if len(pinned) >= budget:
            return pinned[:budget]
        rest2 = sorted(rest, key=lambda it: score_line(it[1]), reverse=True)
        picked = pinned + rest2[: max(0, budget - len(pinned))]
        return sorted(picked, key=lambda x: int(x[0]))

    try:
        candidate_max = int(args.candidate_max_lines or 0)
        include_idxs = list(rule_reasons_by_idx.keys())
        items_pick = pick_candidates(items, include_idxs, candidate_max) if candidate_max > 0 else items
        got = _extract_zh_risky_spans_llm_two_pass(
            endpoint=str(args.llm_endpoint),
            model=str(args.llm_model),
            api_key=str(args.llm_api_key or ""),
            items=items_pick,
            max_spans_per_line=int(args.max_spans),
            max_total_spans=int(args.max_total),
            second_pass=True,
            second_pass_max_lines=5,
            second_pass_trigger_min_spans=1,
            log_enabled=True,
            log_prefix="[reextract]",
        )
        for k, v in (got or {}).items():
            if v:
                vv = []
                for sp in (v or []):
                    if not isinstance(sp, dict):
                        continue
                    sp2 = dict(sp)
                    sp2.setdefault("source", "llm")
                    sp2.setdefault("reasons", ["llm_extract"])
                    sp2.setdefault("confidence", 0.7 if str(sp2.get("risk") or "").lower().startswith("h") else 0.6)
                    vv.append(sp2)
                if vv:
                    spans_by_idx[int(k)] = vv
    except Exception as exc:
        zh_phrase_error = f"{type(exc).__name__}: {exc}"

    # deterministic add-ons
    idioms4 = _load_idioms_4char(str(args.idiom_path or "")) if bool(args.idiom_enable) else set()
    homo_map = _load_same_pinyin_char_map(str(args.same_pinyin_path or "")) if (bool(args.idiom_enable) and str(args.same_pinyin_path or "").strip()) else {}
    for i, seg in enumerate(segments, 1):
        idx = int(i)
        line = str(getattr(seg, "text", "") or "")
        llm_spans = [dict(x) for x in (spans_by_idx.get(idx, []) or [])]
        dict_spans = []
        if idioms4:
            dict_spans += _idiom_spans_from_line(line, idioms4)
            if homo_map:
                dict_spans += _idiom_spans_from_line_fuzzy(line, idioms4=idioms4, homo_map=homo_map)
        pattern_spans = _pattern_spans_from_line(line) if not llm_spans else []
        merged = llm_spans + dict_spans + pattern_spans
        spans_by_idx[idx] = _merge_dedupe_spans_same_line(line, merged, max_spans=int(args.max_spans or 2))

    # Global noise control: cap repeated heuristic spans across the whole subtitle set.
    spans_by_idx = _cap_repeated_spans_by_text(spans_by_idx, max_occ=1, sources={"pattern"})

    if bool(args.force_one_per_line):
        forced_n = 0
        for i, seg in enumerate(segments, 1):
            idx = int(i)
            if spans_by_idx.get(idx):
                continue
            sp = _force_span_from_line(str(getattr(seg, "text", "") or ""))
            if sp:
                spans_by_idx[idx] = [sp]
                forced_n += 1
        print(f"[reextract] force_one_per_line: enabled, forced_lines={forced_n}/{len(segments)}")

    for i, seg in enumerate(segments, 1):
        idx = int(i)
        line = str(getattr(seg, "text", "") or "")
        spans = spans_by_idx.get(idx, [])
        rule_rr = rule_reasons_by_idx.get(idx, [])
        if spans:
            phrase_items.append({"idx": idx, "text": line, "spans": spans})
        if spans or rule_rr:
            suspects.append({"idx": idx, "text": line, "spans": spans, "rule_reasons": rule_rr})

    try:
        (out_dir / "chs.phrases.json").write_text(
            json.dumps({"items": phrase_items, "meta": {"phrase_extraction_error": zh_phrase_error}}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    try:
        (out_dir / "chs.suspects.json").write_text(
            json.dumps({"items": suspects, "meta": {"phrase_extraction_error": zh_phrase_error}}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    print(
        json.dumps(
            {
                "status": "ok",
                "chs_used": chs_path.name,
                "candidate_lines": int(args.candidate_max_lines or 0),
                "zh_glossary_hits": int(zh_fix_hits),
                "suspects_n": len(suspects),
                "phrase_lines_n": len(phrase_items),
                "phrase_error": zh_phrase_error,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

