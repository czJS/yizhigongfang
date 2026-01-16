import json
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+")
_BULLET_OR_NUMBER_RE = re.compile(r"^\s*([-–•]+|\d+\s*[\.\)\-:])\s*")


@dataclass
class SrtItem:
    idx: int
    start_s: float
    end_s: float
    text: str


def _parse_ts(ts: str) -> float:
    # "HH:MM:SS,mmm"
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def parse_srt(path: Path) -> List[SrtItem]:
    """
    Minimal SRT parser:
    - supports blank lines between blocks
    - supports multiline text
    """
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.rstrip("\n\r") for ln in raw.splitlines()]
    out: List[SrtItem] = []

    i = 0
    while i < len(lines):
        # skip empties
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break

        # idx line
        idx_line = lines[i].strip()
        i += 1
        try:
            idx = int(idx_line)
        except Exception:
            # Not a valid block start, skip line
            continue

        # timing line
        if i >= len(lines):
            break
        timing = lines[i].strip()
        i += 1
        if "-->" not in timing:
            continue
        a, b = [x.strip() for x in timing.split("-->", 1)]
        try:
            start_s = _parse_ts(a)
            end_s = _parse_ts(b)
        except Exception:
            continue

        text_lines: List[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        text = "\n".join(text_lines).strip()
        out.append(SrtItem(idx=idx, start_s=start_s, end_s=end_s, text=text))
    return out


def _load_glossary_items(path: Path) -> List[Dict[str, Any]]:
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "{}")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            src = str(it.get("src") or "").strip()
            tgt = str(it.get("tgt") or "").strip()
            if not src or not tgt:
                continue
            out.append(
                {
                    "id": str(it.get("id") or ""),
                    "src": src,
                    "tgt": tgt,
                    "aliases": [str(x).strip() for x in (it.get("aliases") or []) if str(x).strip()],
                    "forbidden": [str(x).strip() for x in (it.get("forbidden") or []) if str(x).strip()],
                    "note": str(it.get("note") or "").strip(),
                }
            )
        return out
    except Exception:
        return []


def _probe_duration_seconds(path: Path) -> Optional[float]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            return None
        val = (proc.stdout or "").strip()
        if not val:
            return None
        return float(val)
    except Exception:
        return None


def _get_gate(cfg: Dict[str, Any], key: str, default: Any) -> Any:
    gates = (cfg or {}).get("quality_gates") or {}
    return gates.get(key, default)


def _percentile(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    p = max(0.0, min(1.0, float(p)))
    ys = sorted(xs)
    if len(ys) == 1:
        return float(ys[0])
    k = (len(ys) - 1) * p
    f = int(k)
    c = min(f + 1, len(ys) - 1)
    if f == c:
        return float(ys[f])
    d0 = ys[f] * (c - k)
    d1 = ys[c] * (k - f)
    return float(d0 + d1)


def _estimate_en_seconds(text: str, *, wps: float = 2.6) -> float:
    t = (text or "").replace("\n", " ").strip()
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return 0.0
    words = [w for w in re.split(r"\s+", t) if w]
    base = (len(words) / max(float(wps), 0.5)) if words else 0.0
    pauses = 0.12 * len(re.findall(r"[,;:]", t)) + 0.22 * len(re.findall(r"[.!?]", t))
    return float(base + pauses)


def _analyze_wav_clipping(path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"skipped": True, "reason": "", "frames": 0, "clipped_samples": 0, "clipped_ratio": 0.0}
    try:
        with wave.open(str(path), "rb") as wf:
            n_frames = int(wf.getnframes() or 0)
            sampwidth = int(wf.getsampwidth() or 0)
            n_channels = int(wf.getnchannels() or 0)
            if n_frames <= 0 or sampwidth <= 0 or n_channels <= 0:
                out["reason"] = "empty wav"
                return out
            if sampwidth != 2:
                out["reason"] = f"unsupported sample width: {sampwidth}"
                return out
            raw = wf.readframes(n_frames)
            import struct

            samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
            maxv = 32767
            clipped = sum(1 for s in samples if abs(int(s)) >= maxv - 1)
            out.update(
                {
                    "skipped": False,
                    "reason": "",
                    "frames": int(n_frames),
                    "channels": int(n_channels),
                    "sample_width": int(sampwidth),
                    "sample_rate": int(wf.getframerate() or 0),
                    "clipped_samples": int(clipped),
                    "clipped_ratio": float(clipped) / float(max(len(samples), 1)),
                }
            )
            return out
    except Exception as exc:
        out["reason"] = str(exc)
        return out


def generate_quality_report(
    *,
    task_id: str,
    mode: str,
    work_dir: Path,
    source_video: Optional[Path],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate a rule-based quality report (offline, no-LLM) for task outputs.
    This is a "public capability": it applies to both lite and quality modes.
    """
    report: Dict[str, Any] = {
        "version": 1,
        "task_id": task_id,
        "mode": mode,
        "work_dir": str(work_dir),
        "passed": True,
        "errors": [],
        "warnings": [],
        "checks": {},
        "metrics": {},
    }

    def fail(msg: str) -> None:
        report["passed"] = False
        report["errors"].append(msg)

    def warn(msg: str) -> None:
        report["warnings"].append(msg)

    # ---- presence checks
    # IMPORTANT:
    # - This report is generated best-effort even when the task failed.
    # - Many artifacts are optional depending on mode / options (e.g. subtitle-only runs),
    #   so we should NOT fail the report just because some media outputs are missing.
    # - We only hard-require eng.srt because most checks rely on it.
    required = ["eng.srt"]
    missing_required = [name for name in required if not (work_dir / name).exists()]
    # "expected" outputs: nice to have for delivery, but may be absent depending on run mode.
    expected = ["output_en_sub.mp4", "output_en.mp4", "tts_full.wav", "audio.wav"]
    missing_expected = [name for name in expected if not (work_dir / name).exists()]
    report["checks"]["required_artifacts"] = {
        "required": required,
        "missing_required": missing_required,
        "expected": expected,
        "missing_expected": missing_expected,
    }
    if missing_required:
        fail(f"缺少关键产物：{missing_required}（未生成英文字幕，通常表示流程在翻译前失败）")
    if missing_expected:
        warn(f"未生成部分交付产物：{missing_expected}（可能是选项/模式导致，也可能是任务中途失败）")

    eng_srt = work_dir / "eng.srt"
    if eng_srt.exists():
        try:
            items = parse_srt(eng_srt)
        except Exception as exc:
            items = []
            fail(f"failed to parse eng.srt: {exc}")
    else:
        items = []

    # ---- english purity / formatting checks
    allow_cjk = bool(_get_gate(cfg, "allow_cjk_in_english_srt", False))
    max_chars_per_line = int(_get_gate(cfg, "max_chars_per_line", 80))
    max_cps = float(_get_gate(cfg, "max_cps", 20.0))
    max_empty_ratio = float(_get_gate(cfg, "max_empty_ratio", 0.10))

    cjk_hits: List[Dict[str, Any]] = []
    numbering_hits: List[Dict[str, Any]] = []
    long_line_hits: List[Dict[str, Any]] = []
    fast_read_hits: List[Dict[str, Any]] = []
    empty_count = 0
    total_count = len(items)

    for it in items:
        text = (it.text or "").strip()
        if not text:
            empty_count += 1
            continue
        if not allow_cjk and _CJK_RE.search(text):
            cjk_hits.append({"idx": it.idx, "text": text[:160]})
        first_line = text.splitlines()[0] if text else ""
        if _BULLET_OR_NUMBER_RE.match(first_line):
            numbering_hits.append({"idx": it.idx, "text": first_line[:160]})
        # line length
        for ln in text.splitlines():
            if len(ln) > max_chars_per_line:
                long_line_hits.append({"idx": it.idx, "len": len(ln), "text": ln[:160]})
                break
        # reading speed
        dur = max(it.end_s - it.start_s, 0.001)
        cps = len(text.replace("\n", " ")) / dur
        if cps > max_cps:
            fast_read_hits.append({"idx": it.idx, "cps": round(cps, 2), "dur_s": round(dur, 3), "text": text[:160]})

    empty_ratio = (empty_count / total_count) if total_count else 0.0

    report["checks"]["english_purity"] = {"allow_cjk": allow_cjk, "cjk_hits": cjk_hits[:30], "cjk_hits_n": len(cjk_hits)}
    report["checks"]["format_numbering_bullets"] = {"hits": numbering_hits[:30], "hits_n": len(numbering_hits)}
    report["checks"]["line_length"] = {"max_chars_per_line": max_chars_per_line, "hits": long_line_hits[:30], "hits_n": len(long_line_hits)}
    report["checks"]["reading_speed"] = {"max_cps": max_cps, "hits": fast_read_hits[:30], "hits_n": len(fast_read_hits)}
    report["metrics"]["eng_srt_items"] = total_count
    report["metrics"]["eng_srt_empty_ratio"] = round(empty_ratio, 4)

    if cjk_hits:
        fail(f"eng.srt contains non-English characters (cjk/fullwidth): {len(cjk_hits)} items")
    if numbering_hits:
        warn(f"eng.srt contains numbering/bullets at line start: {len(numbering_hits)} items")
    if long_line_hits:
        warn(f"eng.srt contains overly long lines (> {max_chars_per_line} chars): {len(long_line_hits)} items")
    if fast_read_hits:
        warn(f"eng.srt reading speed too high (> {max_cps} cps): {len(fast_read_hits)} items")
    if empty_ratio > max_empty_ratio:
        warn(f"eng.srt empty lines ratio too high: {empty_ratio:.2%} (> {max_empty_ratio:.2%})")

    # ---- timeline sanity checks (universal, offline)
    neg_dur_hits: List[Dict[str, Any]] = []
    overlap_hits: List[Dict[str, Any]] = []
    short_hits: List[Dict[str, Any]] = []
    durations: List[float] = []
    gaps: List[float] = []
    prev: Optional[SrtItem] = None
    for it in items:
        dur = float(it.end_s - it.start_s)
        durations.append(max(dur, 0.0))
        if dur <= 0.0:
            neg_dur_hits.append({"idx": it.idx, "dur_s": round(dur, 3), "text": (it.text or "")[:160]})
        if 0.0 < dur < 0.35:
            short_hits.append({"idx": it.idx, "dur_s": round(dur, 3), "text": (it.text or "")[:160]})
        if prev is not None:
            gap = float(it.start_s - prev.end_s)
            gaps.append(gap)
            if it.start_s < prev.end_s - 0.001:
                overlap_hits.append(
                    {"idx": it.idx, "prev_idx": prev.idx, "overlap_s": round(prev.end_s - it.start_s, 3), "text": (it.text or "")[:160]}
                )
        prev = it
    report["checks"]["timeline_sanity"] = {
        "negative_or_zero_dur_n": len(neg_dur_hits),
        "overlap_n": len(overlap_hits),
        "too_short_n": len(short_hits),
        "negative_or_zero_dur": neg_dur_hits[:30],
        "overlaps": overlap_hits[:30],
        "too_short": short_hits[:30],
    }
    report["metrics"]["dur_s_p50"] = round(_percentile(durations, 0.50) or 0.0, 3) if durations else 0.0
    report["metrics"]["dur_s_p95"] = round(_percentile(durations, 0.95) or 0.0, 3) if durations else 0.0
    report["metrics"]["gap_s_p50"] = round(_percentile(gaps, 0.50) or 0.0, 3) if gaps else 0.0
    report["metrics"]["gap_s_p95"] = round(_percentile(gaps, 0.95) or 0.0, 3) if gaps else 0.0
    if neg_dur_hits:
        fail(f"eng.srt has non-positive durations: {len(neg_dur_hits)} items")
    if overlap_hits:
        warn(f"eng.srt has overlapping subtitle timing: {len(overlap_hits)} items")

    # ---- optional: TTS script checks (eng_tts.srt)
    eng_tts = work_dir / "eng_tts.srt"
    if eng_tts.exists():
        try:
            tts_items = parse_srt(eng_tts)
        except Exception as exc:
            tts_items = []
            warn(f"failed to parse eng_tts.srt: {exc}")

        tts_long_hits: List[Dict[str, Any]] = []
        tts_fast_hits: List[Dict[str, Any]] = []
        tts_empty = 0
        tts_total = len(tts_items)

        for it in tts_items:
            text = (it.text or "").strip()
            if not text:
                tts_empty += 1
                continue
            # line length
            for ln in text.splitlines():
                if len(ln) > max_chars_per_line:
                    tts_long_hits.append({"idx": it.idx, "len": len(ln), "text": ln[:160]})
                    break
            # reading speed
            dur = max(it.end_s - it.start_s, 0.001)
            cps = len(text.replace("\n", " ")) / dur
            if cps > max_cps:
                tts_fast_hits.append({"idx": it.idx, "cps": round(cps, 2), "dur_s": round(dur, 3), "text": text[:160]})

        tts_empty_ratio = (tts_empty / tts_total) if tts_total else 0.0
        report["checks"]["tts_script_line_length"] = {"max_chars_per_line": max_chars_per_line, "hits": tts_long_hits[:30], "hits_n": len(tts_long_hits)}
        report["checks"]["tts_script_reading_speed"] = {"max_cps": max_cps, "hits": tts_fast_hits[:30], "hits_n": len(tts_fast_hits)}
        report["metrics"]["eng_tts_srt_items"] = tts_total
        report["metrics"]["eng_tts_empty_ratio"] = round(tts_empty_ratio, 4)
        if tts_long_hits:
            warn(f"eng_tts.srt contains overly long lines (> {max_chars_per_line} chars): {len(tts_long_hits)} items")
        if tts_fast_hits:
            warn(f"eng_tts.srt reading speed too high (> {max_cps} cps): {len(tts_fast_hits)} items")

        # TTS time-budget risk (est speaking time vs allocated subtitle duration)
        try:
            wps = None
            tts_fit_json = work_dir / "tts_fit.json"
            if tts_fit_json.exists():
                try:
                    doc = json.loads(tts_fit_json.read_text(encoding="utf-8", errors="ignore") or "{}")
                    wps = (doc.get("params") or {}).get("wps")
                except Exception:
                    wps = None
            if wps is None:
                wps = (cfg.get("defaults") or {}).get("tts_fit_wps", 2.6)
            wps_f = float(wps or 2.6)
            risk_hits: List[Dict[str, Any]] = []
            for it in tts_items:
                txt = (it.text or "").strip()
                if not txt:
                    continue
                dur = max(it.end_s - it.start_s, 0.001)
                est = _estimate_en_seconds(txt, wps=wps_f)
                ratio = est / dur if dur > 0 else 0.0
                if ratio >= 1.25:
                    risk_hits.append({"idx": it.idx, "dur_s": round(dur, 3), "est_s": round(est, 3), "ratio": round(ratio, 2), "text": txt[:160]})
            risk_hits.sort(key=lambda x: float(x.get("ratio") or 0.0), reverse=True)
            report["checks"]["tts_risk"] = {"wps": round(wps_f, 3), "hits_n": len(risk_hits), "hits": risk_hits[:30]}
            report["metrics"]["tts_risk_hits_n"] = len(risk_hits)
            if risk_hits:
                warn(f"eng_tts.srt has segments likely too long for time budget: {len(risk_hits)} items (ratio>=1.25)")
        except Exception as exc:
            report["checks"]["tts_risk"] = {"skipped": True, "reason": str(exc)}
    else:
        report["checks"]["tts_risk"] = {"skipped": True, "reason": "eng_tts.srt not found"}

    # ---- audio clipping check (tts_full.wav)
    try:
        wav = work_dir / "tts_full.wav"
        if wav.exists():
            clip = _analyze_wav_clipping(wav)
            report["checks"]["tts_audio_clipping"] = clip
            if not clip.get("skipped") and float(clip.get("clipped_ratio") or 0.0) > 0.002:
                warn(f"tts_full.wav may be clipping: clipped_ratio={clip.get('clipped_ratio')}")
        else:
            report["checks"]["tts_audio_clipping"] = {"skipped": True, "reason": "tts_full.wav not found"}
    except Exception as exc:
        report["checks"]["tts_audio_clipping"] = {"skipped": True, "reason": str(exc)}

    # ---- terminology checks (glossary)
    try:
        repo_root = Path(__file__).resolve().parents[1]
        glossary_path = repo_root / "assets" / "glossary" / "glossary.json"
        glossary = _load_glossary_items(glossary_path)
        chs_srt = work_dir / "chs.srt"
        term_hits = 0
        term_missing = 0
        term_forbidden = 0
        missing_samples: List[Dict[str, Any]] = []
        forbidden_samples: List[Dict[str, Any]] = []
        if glossary and chs_srt.exists() and eng_srt.exists():
            zh_items = parse_srt(chs_srt)
            en_items = items  # already parsed
            # align by order
            n = min(len(zh_items), len(en_items))
            for i in range(n):
                zh = (zh_items[i].text or "").strip()
                en = (en_items[i].text or "").strip()
                for term in glossary:
                    if term["src"] not in zh:
                        continue
                    term_hits += 1
                    tgt = term["tgt"]
                    aliases = term.get("aliases") or []
                    forbidden = term.get("forbidden") or []
                    en_l = en.lower()
                    has_tgt = tgt.lower() in en_l
                    has_alias = any(a.lower() in en_l for a in aliases if a)
                    if not has_tgt and not has_alias:
                        term_missing += 1
                        if len(missing_samples) < 30:
                            missing_samples.append({"idx": en_items[i].idx, "src": term["src"], "tgt": tgt, "en": en[:160]})
                    for bad in forbidden:
                        if bad and bad.lower() in en_l:
                            term_forbidden += 1
                            if len(forbidden_samples) < 30:
                                forbidden_samples.append({"idx": en_items[i].idx, "src": term["src"], "bad": bad, "tgt": tgt, "en": en[:160]})
        report["checks"]["terminology"] = {
            "glossary_items_n": len(glossary),
            "hits_n": term_hits,
            "missing_n": term_missing,
            "forbidden_n": term_forbidden,
            "missing_samples": missing_samples,
            "forbidden_samples": forbidden_samples,
        }
        if term_missing > 0:
            warn(f"glossary terms missing in eng.srt when src present: {term_missing}")
        if term_forbidden > 0:
            warn(f"glossary forbidden variants found in eng.srt: {term_forbidden}")
    except Exception as exc:
        report["checks"]["terminology"] = {"skipped": True, "reason": str(exc)}

    # ---- truncation check (output video vs source)
    max_truncation_s = float(_get_gate(cfg, "max_truncation_s", 1.0))
    max_truncation_ratio = float(_get_gate(cfg, "max_truncation_ratio", 0.03))  # 3%
    out_video = work_dir / "output_en.mp4"

    src_dur = _probe_duration_seconds(source_video) if source_video and source_video.exists() else None
    out_dur = _probe_duration_seconds(out_video) if out_video.exists() else None
    report["metrics"]["source_duration_s"] = src_dur
    report["metrics"]["output_duration_s"] = out_dur
    trunc = None
    if src_dur is not None and out_dur is not None:
        trunc = max(src_dur - out_dur, 0.0)
        ratio = trunc / src_dur if src_dur > 0 else 0.0
        report["checks"]["video_truncation"] = {
            "max_truncation_s": max_truncation_s,
            "max_truncation_ratio": max_truncation_ratio,
            "truncation_s": round(trunc, 3),
            "truncation_ratio": round(ratio, 4),
        }
        if trunc > max_truncation_s and ratio > max_truncation_ratio:
            fail(f"output video appears truncated: -{trunc:.2f}s ({ratio:.1%}) vs source")
    else:
        report["checks"]["video_truncation"] = {"skipped": True, "reason": "ffprobe missing or durations unavailable"}

    return report


def write_quality_report(path: Path, report: Dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


