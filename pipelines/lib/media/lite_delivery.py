from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence

from pipelines.lib.asr.lite_asr import Segment, write_srt
from pipelines.lib.media.media_probe import ffprobe_display_wh, probe_duration_s
from pipelines.lib.media.subtitle_display import build_display_items

_WS_RE = re.compile(r"\s+")


def normalize_en_line(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").replace("\n", " ")).strip()


def wrap_en_for_subtitle(s: str, *, max_chars_per_line: int, max_lines: int = 2) -> str:
    """
    Soft-wrap English into <= max_lines lines to reduce long-line warnings.
    This changes layout only; it does not lower CPS.
    """
    t = normalize_en_line(s)
    if not t or max_lines <= 1 or len(t) <= max_chars_per_line:
        return t
    words = t.split(" ")
    if len(words) <= 1:
        return "\n".join(
            [t[i : i + max_chars_per_line] for i in range(0, min(len(t), max_chars_per_line * max_lines), max_chars_per_line)]
        )
    lines: List[str] = []
    cur: List[str] = []
    for w in words:
        if not cur:
            cur = [w]
            continue
        cand = (" ".join(cur + [w])).strip()
        if len(lines) >= max_lines - 1:
            cur.append(w)
            continue
        if len(cand) <= max_chars_per_line:
            cur.append(w)
        else:
            lines.append(" ".join(cur).strip())
            cur = [w]
    if cur:
        lines.append(" ".join(cur).strip())
    lines = [ln.strip() for ln in lines if ln.strip()][:max_lines]
    clamped: List[str] = []
    for ln in lines:
        if len(ln) <= max_chars_per_line:
            clamped.append(ln)
        else:
            clamped.append(ln[: max_chars_per_line - 1].rstrip() + "…")
    return "\n".join(clamped).strip()


def apply_subtitle_postprocess(
    seg_en: Sequence[Segment],
    *,
    wrap_enable: bool = False,
    max_chars_per_line: int = 80,
    max_lines: int = 2,
) -> Dict[str, Any]:
    wrapped = 0
    normalized = 0
    for seg in seg_en:
        before = str(getattr(seg, "translation", "") or "")
        after = normalize_en_line(before)
        if after != before:
            setattr(seg, "translation", after)
            normalized += 1
    if wrap_enable:
        for seg in seg_en:
            before = str(getattr(seg, "translation", "") or "")
            after = wrap_en_for_subtitle(before, max_chars_per_line=max_chars_per_line, max_lines=max_lines)
            if after != before:
                setattr(seg, "translation", after)
                wrapped += 1
    return {
        "normalized": normalized,
        "wrapped": wrapped,
        "wrap_enable": bool(wrap_enable),
        "max_chars_per_line": int(max_chars_per_line),
        "max_lines": int(max_lines),
    }


def estimate_en_seconds(text: str, *, wps: float = 2.6) -> float:
    t = normalize_en_line(text)
    if not t:
        return 0.0
    words = [w for w in re.split(r"\s+", t) if w]
    base = (len(words) / max(float(wps), 0.5)) if words else 0.0
    pauses = 0.12 * len(re.findall(r"[,;:]", t)) + 0.22 * len(re.findall(r"[.!?]", t))
    return float(base + pauses)


def tts_plan_floor_duration(text: str, *, cps_need: float, min_dur: float, hard_min: float = 0.35) -> float:
    txt = normalize_en_line(text)
    if not txt:
        return max(float(hard_min), 0.2)
    words = [w for w in re.split(r"\s+", txt) if w]
    chars = len(txt.replace("\n", " "))
    floor = max(float(hard_min), min(float(min_dur), max(0.45, float(cps_need) * 0.55)))
    if words and len(words) >= 12:
        floor = max(floor, 0.85)
    elif words and len(words) >= 8:
        floor = max(floor, 0.7)
    if chars >= 120:
        floor = max(floor, 1.1)
    elif chars >= 80:
        floor = max(floor, 0.9)
    elif chars >= 40:
        floor = max(floor, 0.7)
    return float(floor)


def rebalance_tts_plan_under_cap(
    segs: List[Segment],
    *,
    cap_end: float,
    min_gap: float,
    min_dur: float,
    max_cps: float,
) -> bool:
    if not segs:
        return False
    first_start = float(segs[0].start)
    total_budget = max(0.0, float(cap_end) - float(first_start))
    if total_budget <= 0:
        return False
    gaps_budget = max(0.0, float(min_gap) * float(max(0, len(segs) - 1)))
    total_dur_budget = total_budget - gaps_budget
    if total_dur_budget <= 0:
        return False

    desired: List[float] = []
    floors: List[float] = []
    for seg in segs:
        txt = str(getattr(seg, "translation", "") or "")
        dur = max(float(seg.end) - float(seg.start), 0.001)
        cps_need = (len(txt.replace("\n", " ")) / max(float(max_cps), 1.0)) if txt else 0.0
        desired.append(dur)
        floors.append(tts_plan_floor_duration(txt, cps_need=cps_need, min_dur=min_dur))

    total_desired = sum(desired)
    if total_desired <= total_dur_budget + 1e-6:
        return False

    total_floor = sum(floors)
    if total_floor > total_dur_budget and total_floor > 1e-6:
        scale = max(0.5, float(total_dur_budget) / float(total_floor))
        floors = [max(0.35, f * scale) for f in floors]
        total_floor = sum(floors)

    if total_floor >= total_dur_budget - 1e-6:
        alloc = list(floors)
    else:
        extra_budget = total_dur_budget - total_floor
        extra_need = max(1e-6, total_desired - total_floor)
        alloc = [f + (max(d - f, 0.0) / extra_need) * extra_budget for d, f in zip(desired, floors)]

    cursor = first_start
    for i, (seg, dur) in enumerate(zip(segs, alloc)):
        seg.start = float(cursor)
        seg.end = float(cursor + max(dur, 0.001))
        cursor = float(seg.end) + (float(min_gap) if i < len(segs) - 1 else 0.0)
    return True


def apply_tts_plan(
    seg_en: List[Segment],
    *,
    video_path: Path,
    max_speed: float,
    wps: float,
    min_dur: float,
    max_cps: float,
    mux_slow_max_ratio: float,
    tts_plan_safety_margin: float,
    tts_fit_min_words: int,
) -> Dict[str, Any]:
    min_gap = 0.04
    cap_ratio = max(1.0, min(float(mux_slow_max_ratio), 1.30))
    tail_margin = float(tts_plan_safety_margin)
    src_dur_s = probe_duration_s(video_path)
    cap_end = None
    if src_dur_s and src_dur_s > 0:
        cap_end = max(float(src_dur_s) * cap_ratio - tail_margin, 0.5)

    plans: List[Dict[str, Any]] = []
    prev_end = None
    for i, seg in enumerate(seg_en):
        st0 = float(seg.start)
        ed0 = float(seg.end)
        dur0 = max(ed0 - st0, 0.001)
        txt = str(seg.translation or "").strip()
        est = estimate_en_seconds(txt, wps=wps) if txt else 0.0
        cps_need = (len(txt) / max(max_cps, 1.0)) if txt else 0.0
        floor = min(min_dur, max(0.25, cps_need)) if txt else min_dur
        base_dur = max(dur0, 0.25) if (dur0 < 0.8 and txt) else max(dur0, floor, cps_need)
        need_dur = float(base_dur)
        st = st0 if prev_end is None else max(st0, float(prev_end) + float(min_gap))
        ed = st + need_dur
        plans.append(
            {
                "idx": i + 1,
                "text": txt[:180],
                "orig": {"start": round(st0, 3), "end": round(ed0, 3), "dur": round(dur0, 3)},
                "planned": {"start": round(st, 3), "end": round(ed, 3), "dur": round(need_dur, 3)},
                "est_s": round(float(est), 3),
                "required_speed": round((float(est) / float(need_dur)) if need_dur > 0 else 0.0, 3),
                "trim": {"mode": "rule", "aggressive": bool(dur0 < 0.8 and txt)} if txt else None,
            }
        )
        seg.start = float(st)
        seg.end = float(ed)
        prev_end = float(ed)

    rebalanced = False
    if cap_end is not None and seg_en:
        hard_cap_end = float(cap_end)
        severe_overlong_pressure = any(
            (float(p.get("required_speed") or 0.0) > float(max_speed) * 1.18)
            or (float((p.get("planned") or {}).get("dur") or 0.0) < 0.5 and len(str(p.get("text") or "")) >= 32)
            for p in plans
        )
        if severe_overlong_pressure and src_dur_s and src_dur_s > 0:
            soft_ratio = min(1.26, cap_ratio + 0.06)
            hard_cap_end = max(hard_cap_end, max(float(src_dur_s) * soft_ratio - tail_margin, 0.5))

        rebalanced = rebalance_tts_plan_under_cap(
            seg_en,
            cap_end=float(hard_cap_end),
            min_gap=float(min_gap),
            min_dur=float(min_dur),
            max_cps=float(max_cps),
        )
        if rebalanced:
            for p, seg in zip(plans, seg_en):
                p["planned"] = {
                    "start": round(float(seg.start), 3),
                    "end": round(float(seg.end), 3),
                    "dur": round(max(float(seg.end) - float(seg.start), 0.0), 3),
                }
                need = max(float(seg.end) - float(seg.start), 0.001)
                p["required_speed"] = round((float(p.get("est_s") or 0.0) / need) if need > 0 else 0.0, 3)
        elif max(float(seg.end) for seg in seg_en) > float(hard_cap_end):
            cursor_cap = float(hard_cap_end)
            for seg in reversed(seg_en):
                if float(seg.end) <= float(cursor_cap):
                    cursor_cap = float(seg.start) - float(min_gap)
                    continue
                seg.end = max(float(seg.start) + 0.35, float(cursor_cap))
                cursor_cap = float(seg.start) - float(min_gap)
        cap_end = float(hard_cap_end)

    return {
        "version": 1,
        "enabled": True,
        "params": {
            "wps": float(wps),
            "max_speed": float(max_speed),
            "min_sub_dur": float(min_dur),
            "min_gap": float(min_gap),
            "max_cps": float(max_cps),
            "cap_ratio": float(cap_ratio),
            "cap_end": cap_end,
            "min_words_default": int(tts_fit_min_words),
        },
        "rebalanced": bool(rebalanced),
        "plan_end_s": round(max((float(seg.end) for seg in seg_en), default=0.0), 3),
        "source_duration_s": round(float(src_dur_s), 3) if src_dur_s else None,
        "plans": plans[:400],
    }


def maybe_build_display_subtitles(
    *,
    video_path: Path,
    seg_en: List[Segment],
    display_srt: Path,
    display_meta_json: Path,
    display_srt_enable: bool,
    display_use_for_embed: bool,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    merge_enable: bool = False,
    merge_max_gap_s: float = 0.25,
    merge_max_chars: int = 80,
    split_enable: bool = False,
    split_max_chars: int = 86,
    sub_font_size: int = 18,
    sub_place_enable: bool = False,
    sub_place_w: float = 0.0,
    erase_subtitle_enable: bool = False,
    erase_subtitle_w: float = 0.0,
) -> bool:
    if not (display_srt_enable or display_use_for_embed):
        return False
    eff_max_chars_per_line = int(max_chars_per_line)
    if display_use_for_embed:
        box_w = float(sub_place_w) if sub_place_enable else (float(erase_subtitle_w) if erase_subtitle_enable else 0.0)
        wh = ffprobe_display_wh(video_path)
        if wh and box_w > 0:
            play_w = int(wh[0])
            usable_w_px = max(120.0, float(play_w) * float(box_w) - float(sub_font_size) * 1.2)
            approx_char_px = max(9.0, float(sub_font_size) * 0.90)
            estimated_chars = int(max(16, min(eff_max_chars_per_line, usable_w_px // approx_char_px)))
            if estimated_chars < eff_max_chars_per_line:
                eff_max_chars_per_line = estimated_chars
    src = [(float(s.start), float(s.end), str(s.translation or "")) for s in seg_en]
    items, meta = build_display_items(
        src=src,
        max_chars_per_line=eff_max_chars_per_line,
        max_lines=int(max_lines),
        merge_enable=bool(merge_enable),
        merge_max_gap_s=float(merge_max_gap_s),
        merge_max_chars=int(merge_max_chars),
        split_enable=bool(split_enable),
        split_max_chars=int(split_max_chars),
    )
    disp_segs: List[Segment] = [Segment(start=it.start, end=it.end, text="", translation=it.text) for it in items]
    write_srt(display_srt, disp_segs, text_attr="translation")
    display_meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
