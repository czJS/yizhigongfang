#!/usr/bin/env python3
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from pipelines.lib.asr.lite_asr import extract_audio, run_asr_whispercpp
from pipelines.lib.asr.lite_asr_stage1 import (
    _load_same_pinyin_char_map,
    _load_same_stroke_char_map,
    _load_zh_word_set,
)
from pipelines.lib.text.asr_normalize import load_asr_dict, normalize_asr_zh_text
from pipelines.lib.text.srt_io import read_srt_texts
from pipelines.lib.text.zh_convert import zh_to_simplified

_FW_MODEL_CACHE: Dict[Tuple[str, int], Any] = {}


def _read_json_or_jsonl(path: Path) -> List[Dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".jsonl":
        out: List[Dict[str, Any]] = []
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            obj = json.loads(s)
            if isinstance(obj, dict):
                out.append(obj)
        return out
    obj = json.loads(raw or "[]")
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        items = obj.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    raise ValueError(f"Unsupported manifest format: {path}")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _safe_rmtree(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _is_url_ref(value: str) -> bool:
    s = str(value or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cleanup_download_artifacts(output_path: Path) -> None:
    for cand in output_path.parent.glob(output_path.stem + ".*"):
        _safe_unlink(cand)


def _norm_text(text: str, asr_dict: Dict[str, str]) -> str:
    return normalize_asr_zh_text(str(text or ""), to_simplified_fn=zh_to_simplified, asr_dict=asr_dict)


def _ffprobe_duration_s(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return 0.0
    try:
        return max(0.0, float((proc.stdout or "").strip() or 0.0))
    except Exception:
        return 0.0


def _probe_remote_duration_s(url: str, cookiefile: str = "") -> float:
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        raise RuntimeError("yt_dlp python module not available on host") from exc
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    opts.update(_yt_dlp_auth_opts(cookiefile))
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(str(url), download=False)
    if not isinstance(info, dict):
        return 0.0
    try:
        return max(0.0, float(info.get("duration") or 0.0))
    except Exception:
        return 0.0


def _probe_remote_info(url: str, cookiefile: str = "") -> Dict[str, Any]:
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        raise RuntimeError("yt_dlp python module not available on host") from exc
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    opts.update(_yt_dlp_auth_opts(cookiefile))
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(str(url), download=False)
    if not isinstance(info, dict):
        return {}
    return info


def _download_video_yt_dlp(url: str, output_path: Path, *, max_height: int = 540, cookiefile: str = "") -> Path:
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        raise RuntimeError("yt_dlp python module not available on host") from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        return output_path
    tmp_template = str(output_path.with_suffix(".%(ext)s"))
    format_candidates = [
        f"bv*[height<={int(max_height)}]+ba/b[height<={int(max_height)}]/bv*+ba/b",
        "b/bv*+ba",
    ]
    common_opts: Dict[str, Any] = {
        "quiet": False,
        "no_warnings": True,
        "outtmpl": tmp_template,
        "merge_output_format": output_path.suffix.lstrip(".") or "mp4",
        "noplaylist": True,
        "retries": 3,
    }
    common_opts.update(_yt_dlp_auth_opts(cookiefile))
    last_exc: Optional[Exception] = None
    for fmt in format_candidates:
        opts = dict(common_opts)
        opts["format"] = fmt
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([str(url)])
            break
        except Exception as exc:
            last_exc = exc
    if not output_path.exists() and last_exc is not None:
        raise last_exc
    if output_path.exists():
        return output_path
    candidates = sorted(output_path.parent.glob(output_path.stem + ".*"))
    for cand in candidates:
        if cand.is_file():
            if cand != output_path:
                cand.rename(output_path)
            return output_path
    raise FileNotFoundError(f"downloaded file not found for url: {url}")


def _yt_dlp_auth_opts(cookiefile: str = "") -> Dict[str, Any]:
    if str(cookiefile or "").strip():
        return {"cookiefile": str(cookiefile).strip()}
    chrome_cookie_db = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Cookies"
    if chrome_cookie_db.exists():
        return {"cookiesfrombrowser": ("chrome",)}
    return {}


def _platform_bucket(name: str) -> str:
    s = str(name or "").strip().lower()
    if any(k in s for k in ("douyin", "抖音", "dy")):
        return "douyin"
    if any(k in s for k in ("bilibili", "b站", "bili")):
        return "bilibili"
    return "other"


def _source_meta(row: Dict[str, Any], key: str, default: str = "") -> str:
    meta = row.get("meta")
    if isinstance(meta, dict) and str(meta.get(key) or "").strip():
        return str(meta.get(key) or "").strip()
    return str(row.get(key) or default).strip()


def _normalize_source_row(row: Dict[str, Any]) -> Dict[str, Any]:
    video = str(row.get("video") or row.get("file") or row.get("url") or "").strip()
    if not video:
        raise ValueError(f"source row missing video/file/url: {row}")
    source_id = str(row.get("id") or row.get("source_id") or Path(video).stem).strip()
    platform = _source_meta(row, "platform", "other")
    category = _source_meta(row, "category", "uncategorized")
    account_type = _source_meta(row, "account_type", "")
    duration_s = float(row.get("duration_s") or row.get("probe_duration") or 0.0)
    meta = dict(row.get("meta") or {}) if isinstance(row.get("meta"), dict) else {}
    title = str(meta.get("title") or row.get("probe_title") or row.get("title") or "").strip()
    uploader = str(meta.get("uploader") or row.get("probe_uploader") or row.get("uploader") or "").strip()
    if title:
        meta["title"] = title
    if uploader:
        meta["uploader"] = uploader
    return {
        "id": source_id,
        "video": video,
        "platform": platform,
        "platform_bucket": _platform_bucket(platform),
        "category": category,
        "account_type": account_type,
        "duration_s": duration_s,
        "is_remote": _is_url_ref(video),
        "meta": meta,
    }


DEFAULT_SPEECH_INCLUDE_CATEGORY_KEYWORDS = [
    "职场",
    "career_share",
    "日常",
    "生活",
    "vlog",
    "社科",
    "法律",
    "law_commentary",
    "law_rights",
    "律师",
    "普法",
    "说法",
    "心理",
    "psychology_education",
    "人文",
    "历史",
    "history_explain",
    "财经",
    "商业",
    "macro_finance",
    "sales_explain",
    "product_explain",
    "校园",
    "学习",
    "亲子",
    "育儿",
    "家居",
    "房产",
    "家装",
    "出行",
    "汽车",
    "健康",
    "医疗",
    "科普",
    "知识",
    "情感",
    "小剧场",
    "搞笑",
    "手工",
    "美妆",
    "护肤",
    "穿搭",
    "美食记录",
    "三农",
    "读书",
    "knowledge_share",
    "news_commentary",
]

DEFAULT_SPEECH_EXCLUDE_CATEGORY_KEYWORDS = [
    "音乐",
    "翻唱",
    "演奏",
    "乐评",
    "舞蹈",
    "宅舞",
    "鬼畜",
    "特摄",
    "同人",
    "手书",
    "动画",
    "动漫",
    "游戏",
    "电子竞技",
    "音游",
    "mmd",
    "mv",
    "gmv",
    "影视",
    "综艺",
    "模玩",
]

DEFAULT_SPEECH_EXCLUDE_TITLE_KEYWORDS = [
    "翻唱",
    "演奏",
    "纯享",
    "直拍",
    "舞蹈",
    "鬼畜",
    "mad",
    "amv",
    "mmd",
    "gmv",
    "pv",
    "mv",
    "ost",
    "主题曲",
    "reaction",
    "游戏",
    "动画片",
    "二次元",
    "原创动画",
    "歌词",
    "片头",
    "片尾",
    "独播剧场",
    "次回予告",
    "预告",
]

DEFAULT_SPEECH_EXCLUDE_TITLE_PATTERNS = [
    r"次回予告",
    r"下[一1]集预告",
    r"独播剧场",
    r"television\s+series",
    r"\bexclusive\b",
    r"第\s*\d+\s*[话話集]",
]

DEFAULT_RUNTIME_SOURCE_REJECT_KEYWORDS = [
    "歌词",
    "独播剧场",
    "television series",
    "exclusive",
    "次回予告",
    "下集预告",
]

_JP_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{4,}")


def _text_has_any_keyword(text: str, keywords: List[str]) -> bool:
    s = str(text or "").strip().lower()
    return any(str(k or "").strip().lower() in s for k in keywords if str(k or "").strip())


def _text_hits_any_pattern(text: str, patterns: List[str]) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    return any(re.search(pattern, s, flags=re.IGNORECASE) for pattern in patterns if str(pattern or "").strip())


def _speech_title_reject_reason(title: str, exclude_title_keywords: Optional[List[str]]) -> str:
    s = str(title or "").strip()
    if not s:
        return ""
    exclude_title_kw = list(exclude_title_keywords or DEFAULT_SPEECH_EXCLUDE_TITLE_KEYWORDS)
    if _text_has_any_keyword(s, exclude_title_kw):
        return f"unsuitable_title:{s[:80]}"
    if _text_hits_any_pattern(s, DEFAULT_SPEECH_EXCLUDE_TITLE_PATTERNS):
        return f"unsuitable_title_pattern:{s[:80]}"
    if _JP_KANA_RE.search(s):
        return f"unsuitable_title_script:{s[:80]}"
    return ""


def _speech_source_reject_reason(
    row: Dict[str, Any],
    *,
    source_profile: str,
    include_category_keywords: Optional[List[str]],
    exclude_category_keywords: Optional[List[str]],
    exclude_title_keywords: Optional[List[str]],
) -> str:
    profile = str(source_profile or "general").strip().lower()
    if profile not in {"speech_focused", "speech", "talk"}:
        return ""
    category = str(row.get("category") or "").strip()
    title = str((row.get("meta") or {}).get("title") or "").strip()
    include_kw = list(include_category_keywords or DEFAULT_SPEECH_INCLUDE_CATEGORY_KEYWORDS)
    exclude_cat_kw = list(exclude_category_keywords or DEFAULT_SPEECH_EXCLUDE_CATEGORY_KEYWORDS)
    if _text_has_any_keyword(category, exclude_cat_kw):
        return f"unsuitable_category:{category}"
    title_reject_reason = _speech_title_reject_reason(title, exclude_title_keywords)
    if title_reject_reason:
        return title_reject_reason
    if include_kw and not (_text_has_any_keyword(category, include_kw) or _text_has_any_keyword(title, include_kw)):
        return f"not_speech_focused:{category or title[:80]}"
    return ""


def _prepare_source_manifest(
    rows: List[Dict[str, Any]],
    *,
    cookiefile: str,
    min_duration_s: float,
    max_duration_s: float,
    source_profile: str = "general",
    include_category_keywords: Optional[List[str]] = None,
    exclude_category_keywords: Optional[List[str]] = None,
    exclude_title_keywords: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    dedup: Dict[str, Dict[str, Any]] = {}
    for raw in rows:
        row = _normalize_source_row(raw)
        dedup.setdefault(str(row["video"]), row)
    prepared: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for row in dedup.values():
        cur = dict(row)
        try:
            if float(cur.get("duration_s") or 0.0) <= 0:
                if bool(cur.get("is_remote")):
                    info = _probe_remote_info(str(cur["video"]), cookiefile=cookiefile)
                    cur["duration_s"] = max(0.0, float(info.get("duration") or 0.0))
                    title = str(info.get("title") or "").strip()
                    if title:
                        meta = dict(cur.get("meta") or {})
                        meta.setdefault("title", title)
                        cur["meta"] = meta
                else:
                    cur["duration_s"] = _ffprobe_duration_s(Path(str(cur["video"])))
            duration_s = float(cur.get("duration_s") or 0.0)
            if duration_s < float(min_duration_s):
                rejected.append({**cur, "reject_reason": f"too_short:{duration_s:.3f}"})
                continue
            if float(max_duration_s or 0.0) > 0 and duration_s > float(max_duration_s):
                rejected.append({**cur, "reject_reason": f"too_long:{duration_s:.3f}"})
                continue
            reject_reason = _speech_source_reject_reason(
                cur,
                source_profile=source_profile,
                include_category_keywords=include_category_keywords,
                exclude_category_keywords=exclude_category_keywords,
                exclude_title_keywords=exclude_title_keywords,
            )
            if reject_reason:
                rejected.append({**cur, "reject_reason": reject_reason})
                continue
            prepared.append(cur)
        except Exception as exc:
            rejected.append({**cur, "reject_reason": f"probe_error:{str(exc)[:300]}"})
    return prepared, rejected


def _round_targets(total: int, ratios: Dict[str, float]) -> Dict[str, int]:
    raw = {k: float(total) * float(v) for k, v in ratios.items()}
    out = {k: int(math.floor(v)) for k, v in raw.items()}
    remain = int(total) - sum(out.values())
    for key, _frac in sorted(raw.items(), key=lambda kv: (kv[1] - math.floor(kv[1]), kv[0]), reverse=True):
        if remain <= 0:
            break
        out[key] += 1
        remain -= 1
    return out


def _pick_diverse_sources(rows: List[Dict[str, Any]], target_n: int, seed: int) -> List[Dict[str, Any]]:
    if len(rows) <= target_n:
        return list(rows)
    rng = random.Random(seed)
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = f"{row.get('category') or 'uncategorized'}::{row.get('account_type') or 'unknown'}"
        grouped[key].append(row)
    for items in grouped.values():
        rng.shuffle(items)
    keys = sorted(grouped.keys(), key=lambda k: (len(grouped[k]), k), reverse=True)
    out: List[Dict[str, Any]] = []
    while len(out) < target_n and keys:
        next_keys: List[str] = []
        for key in keys:
            bucket = grouped.get(key) or []
            if not bucket:
                continue
            out.append(bucket.pop())
            if len(out) >= target_n:
                break
            if bucket:
                next_keys.append(key)
        keys = next_keys
    return out[:target_n]


def _allocate_clip_budget(rows: List[Dict[str, Any]], total_clips: int) -> Dict[str, int]:
    if not rows:
        return {}
    weights: Dict[str, float] = {}
    for row in rows:
        dur = max(60.0, float(row.get("duration_s") or 0.0))
        weights[str(row["id"])] = max(1.0, min(4.0, dur / 180.0))
    total_weight = sum(weights.values()) or 1.0
    raw = {sid: (float(total_clips) * w / total_weight) for sid, w in weights.items()}
    out = {sid: max(1, int(math.floor(v))) for sid, v in raw.items()}
    remain = int(total_clips) - sum(out.values())
    for sid, _ in sorted(raw.items(), key=lambda kv: (kv[1] - math.floor(kv[1]), kv[0]), reverse=True):
        if remain <= 0:
            break
        out[sid] += 1
        remain -= 1
    return out


def _build_windows(
    *,
    duration_s: float,
    count: int,
    clip_len_s: int,
    long_clip_len_s: int,
    long_clip_ratio: float,
    min_gap_s: float,
    seed: int,
) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if duration_s <= max(clip_len_s, long_clip_len_s):
        return out
    rng = random.Random(seed)
    long_count = min(int(round(count * max(0.0, min(1.0, long_clip_ratio)))), count)
    lengths = [int(long_clip_len_s)] * long_count + [int(clip_len_s)] * max(0, count - long_count)
    if not lengths:
        return out
    rng.shuffle(lengths)
    prev_end = 0.0
    for idx, cur_len in enumerate(lengths):
        slots_left = max(1, len(lengths) - idx)
        free_right = max(0.0, duration_s - cur_len)
        if free_right <= 0:
            continue
        anchor = free_right * ((idx + 0.5) / max(1, len(lengths)))
        jitter = min(5.0, max(1.0, duration_s / max(10.0, float(len(lengths)))))
        start = max(0.0, min(free_right, anchor + rng.uniform(-jitter, jitter)))
        min_start = max(0.0, prev_end + min_gap_s)
        if start < min_start:
            start = min_start
        room_for_rest = sum(lengths[idx + 1 :]) + max(0, slots_left - 1) * min_gap_s
        max_start = max(0.0, duration_s - cur_len - room_for_rest)
        if start > max_start and idx < len(lengths) - 1:
            start = max(0.0, max_start)
        end = min(duration_s, start + cur_len)
        if end - start < cur_len * 0.9:
            continue
        if out and start < out[-1][1] + min_gap_s:
            start = out[-1][1] + min_gap_s
            end = min(duration_s, start + cur_len)
        if end - start < cur_len * 0.9:
            continue
        out.append((round(start, 3), round(end, 3)))
        prev_end = end
    return out


def cmd_plan_sampling(args: argparse.Namespace) -> None:
    rows = [_normalize_source_row(x) for x in _read_json_or_jsonl(args.source_manifest)]
    dedup: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        dedup.setdefault(str(row["video"]), row)
    rows = list(dedup.values())
    if not rows:
        raise SystemExit("No valid sources found.")

    for row in rows:
        if float(row.get("duration_s") or 0.0) <= 0:
            if bool(row.get("is_remote")):
                row["duration_s"] = _probe_remote_duration_s(str(row["video"]), str(getattr(args, "download_cookiefile", "") or ""))
            else:
                row["duration_s"] = _ffprobe_duration_s(Path(str(row["video"])))
    rows = [row for row in rows if float(row.get("duration_s") or 0.0) >= max(args.clip_len_s, args.long_clip_len_s) + 5.0]
    if float(getattr(args, "max_source_duration_s", 0.0) or 0.0) > 0:
        rows = [row for row in rows if float(row.get("duration_s") or 0.0) <= float(args.max_source_duration_s)]
    rows = [
        row
        for row in rows
        if not _speech_source_reject_reason(
            row,
            source_profile=str(getattr(args, "source_profile", "general") or "general"),
            include_category_keywords=list(getattr(args, "include_category_keywords", None) or []),
            exclude_category_keywords=list(getattr(args, "exclude_category_keywords", None) or []),
            exclude_title_keywords=list(getattr(args, "exclude_title_keywords", None) or []),
        )
    ]
    if not rows:
        raise SystemExit("No sources long enough for clip planning.")

    ratios = {"douyin": args.ratio_douyin, "bilibili": args.ratio_bilibili, "other": args.ratio_other}
    targets = _round_targets(args.target_sources, ratios)
    by_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_bucket[str(row.get("platform_bucket") or "other")].append(row)

    selected: List[Dict[str, Any]] = []
    for bucket, bucket_target in targets.items():
        picked = _pick_diverse_sources(by_bucket.get(bucket, []), int(bucket_target), args.seed + len(selected))
        selected.extend(picked)

    if len(selected) < int(args.target_sources):
        selected_ids = {str(x["id"]) for x in selected}
        leftovers = [row for row in rows if str(row["id"]) not in selected_ids]
        need = int(args.target_sources) - len(selected)
        selected.extend(_pick_diverse_sources(leftovers, need, args.seed + 999))

    selected = selected[: int(args.target_sources)]
    clip_budgets = _allocate_clip_budget(selected, int(args.total_clips))

    out_dir = Path(args.out_dir)
    clips_dir = out_dir / "clips"
    source_rows: List[Dict[str, Any]] = []
    clip_rows: List[Dict[str, Any]] = []
    case_rows: List[Dict[str, Any]] = []
    clip_idx = 1
    for source in selected:
        source_rows.append(source)
        windows = _build_windows(
            duration_s=float(source.get("duration_s") or 0.0),
            count=max(1, int(clip_budgets.get(str(source["id"]), 1))),
            clip_len_s=int(args.clip_len_s),
            long_clip_len_s=int(args.long_clip_len_s),
            long_clip_ratio=float(args.long_clip_ratio),
            min_gap_s=float(args.min_gap_s),
            seed=args.seed + clip_idx,
        )
        for start_s, end_s in windows:
            case_id = f"{args.case_prefix}_{clip_idx:06d}"
            clip_idx += 1
            clip = {
                "id": case_id,
                "source_id": source["id"],
                "video": source["video"],
                "is_remote": bool(source.get("is_remote")),
                "platform": source["platform"],
                "platform_bucket": source["platform_bucket"],
                "category": source["category"],
                "account_type": source["account_type"],
                "start_s": start_s,
                "end_s": end_s,
                "duration_s": round(end_s - start_s, 3),
            }
            clip_rows.append(clip)
            case_rows.append(
                {
                    "id": case_id,
                    "video": str(clips_dir / f"{case_id}.mp4"),
                    "meta": {
                        "source_id": source["id"],
                        "source_video": source["video"],
                        "source_is_remote": bool(source.get("is_remote")),
                        "platform": source["platform"],
                        "platform_bucket": source["platform_bucket"],
                        "category": source["category"],
                        "account_type": source["account_type"],
                        "start_s": start_s,
                        "end_s": end_s,
                        "duration_s": round(end_s - start_s, 3),
                    },
                }
            )

    summary = {
        "task": "asr_project_confusion_sampling",
        "target_sources": int(args.target_sources),
        "selected_sources": len(source_rows),
        "target_clips": int(args.total_clips),
        "planned_clips": len(clip_rows),
        "platform_ratio_requested": ratios,
        "platform_ratio_actual": {
            key: round(sum(1 for row in source_rows if row.get("platform_bucket") == key) / max(1, len(source_rows)), 4)
            for key in ("douyin", "bilibili", "other")
        },
        "clip_len_s": int(args.clip_len_s),
        "long_clip_len_s": int(args.long_clip_len_s),
        "long_clip_ratio": float(args.long_clip_ratio),
    }
    _write_json(out_dir / "sampling_manifest.json", summary)
    _write_jsonl(out_dir / "sampled_sources.jsonl", source_rows)
    _write_json(
        out_dir / "clip_plan.json",
        {
            "task": "asr_project_confusion_clip_plan",
            "summary": summary,
            "sources": source_rows,
            "clips": clip_rows,
        },
    )
    _write_jsonl(out_dir / "cases.jsonl", case_rows)
    print(f"[ok] wrote sampling plan to {out_dir}")


def _ffmpeg_cut(input_video: Path, output_video: Path, start_s: float, end_s: float) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.1, float(end_s) - float(start_s))
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_s),
        "-i",
        str(input_video),
        "-t",
        str(dur),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "ffmpeg cut failed")


def _probe_media_streams(path: Path) -> Dict[str, bool]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "ffprobe stream probe failed")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    has_audio = any(str((s or {}).get("codec_type") or "") == "audio" for s in streams if isinstance(s, dict))
    has_video = any(str((s or {}).get("codec_type") or "") == "video" for s in streams if isinstance(s, dict))
    return {"has_audio": bool(has_audio), "has_video": bool(has_video)}


def cmd_slice_clips(args: argparse.Namespace) -> None:
    plan = json.loads(Path(args.clip_plan).read_text(encoding="utf-8"))
    clips = [x for x in (plan.get("clips") or []) if isinstance(x, dict)]
    out_dir = Path(args.out_dir)
    cases: List[Dict[str, Any]] = []
    for clip in clips:
        case_id = str(clip.get("id") or "").strip()
        if not case_id:
            continue
        src = Path(str(clip.get("video") or ""))
        if not src.exists():
            raise FileNotFoundError(f"source video missing: {src}")
        out_video = out_dir / "clips" / f"{case_id}.mp4"
        _ffmpeg_cut(src, out_video, float(clip.get("start_s") or 0.0), float(clip.get("end_s") or 0.0))
        cases.append(
            {
                "id": case_id,
                "video": str(out_video),
                "meta": {
                    "source_id": clip.get("source_id"),
                    "platform": clip.get("platform"),
                    "platform_bucket": clip.get("platform_bucket"),
                    "category": clip.get("category"),
                    "account_type": clip.get("account_type"),
                    "start_s": clip.get("start_s"),
                    "end_s": clip.get("end_s"),
                    "duration_s": clip.get("duration_s"),
                },
            }
        )
    _write_jsonl(out_dir / "cases.jsonl", cases)
    print(f"[ok] sliced {len(cases)} clips under {out_dir / 'clips'}")


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


def _run_teacher_faster_whisper(
    *,
    audio_path: Path,
    model_root: Path,
    repo_id: str,
    cpu_threads: int,
) -> str:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:
        raise RuntimeError("python module not available: faster_whisper") from exc
    snap = _resolve_local_snapshot(model_root, repo_id, required_files=["model.bin", "config.json"])
    if not snap:
        raise RuntimeError(f"teacher snapshot not found for repo: {repo_id}")
    cache_key = (str(snap), max(1, int(cpu_threads)))
    model = _FW_MODEL_CACHE.get(cache_key)
    if model is None:
        model = WhisperModel(str(snap), device="cpu", compute_type="int8", cpu_threads=max(1, int(cpu_threads)), num_workers=1)
        _FW_MODEL_CACHE[cache_key] = model
    seg_iter, _info = model.transcribe(
        str(audio_path),
        language="zh",
        task="transcribe",
        beam_size=3,
        best_of=3,
        condition_on_previous_text=False,
        chunk_length=30,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.45,
        word_timestamps=False,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 160},
        temperature=0.0,
    )
    return "".join(str(getattr(seg, "text", "") or "").strip() for seg in seg_iter)


def _run_teacher_sensevoice(
    *,
    audio_path: Path,
    model_id: str,
    model_dir: Path,
) -> str:
    from pipelines.quality_pipeline_impl import run_sensevoice_asr  # lazy import

    segments = run_sensevoice_asr(
        audio_path=audio_path,
        model_id=str(model_id),
        device="cpu",
        model_dir=Path(model_dir),
        audio_total_s=_ffprobe_duration_s(audio_path),
    )
    return "".join(str(getattr(seg, "text", "") or "").strip() for seg in segments)


def _run_teacher_transcript(*, audio_path: Path, config: Dict[str, Any]) -> str:
    backend = str(config.get("backend") or "faster_whisper").strip().lower()
    if backend == "disabled":
        return ""
    if backend == "faster_whisper":
        return _run_teacher_faster_whisper(
            audio_path=audio_path,
            model_root=Path(config["model_root"]),
            repo_id=str(config["repo_id"]),
            cpu_threads=int(config.get("threads") or 4),
        )
    if backend == "sensevoice":
        return _run_teacher_sensevoice(
            audio_path=audio_path,
            model_id=str(config["sensevoice_model"]),
            model_dir=Path(config["sensevoice_model_dir"]),
        )
    raise RuntimeError(f"unsupported teacher backend: {backend}")


def _join_student_segments(segments: List[Any]) -> str:
    return "".join(str(getattr(seg, "text", "") or "").strip() for seg in segments)


def _strip_zh_punct(s: str) -> str:
    return re.sub(r"[，。！？；：、,.!?;:\s]+", "", str(s or ""))


def _levenshtein_chars(a: str, b: str) -> int:
    aa = list(a)
    bb = list(b)
    n, m = len(aa), len(bb)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    cur = [0] * (m + 1)
    for i in range(1, n + 1):
        cur[0] = i
        ai = aa[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == bb[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev, cur = cur, prev
    return prev[m]


def _char_replace_hits(src: str, tgt: str, mp: Dict[str, List[str]]) -> int:
    if len(src) != len(tgt):
        return 0
    count = 0
    for a, b in zip(src, tgt):
        if a == b:
            continue
        if b in (mp.get(a) or []):
            count += 1
    return count


def _candidate_type(wrong: str, candidate: str, proper_nouns: set[str]) -> str:
    func_chars = {"的", "地", "得", "了", "着", "把", "被", "于", "对", "在", "向", "给"}
    if len(wrong) == 1 and len(candidate) == 1 and any(ch in func_chars for ch in wrong + candidate):
        return "single_function_word"
    if any(ch in func_chars for ch in wrong + candidate):
        return "function_word"
    if candidate in proper_nouns and 2 <= len(candidate) <= 8:
        return "proper_noun"
    if len(wrong) == 1 and len(candidate) == 1:
        return "single_char"
    if max(len(wrong), len(candidate)) <= 2:
        return "double_char"
    return "short_phrase"


def _build_candidate(
    *,
    wrong: str,
    candidate: str,
    same_pinyin_map: Dict[str, List[str]],
    same_stroke_map: Dict[str, List[str]],
    lexicon_words: set[str],
    proper_nouns: set[str],
) -> Optional[Dict[str, Any]]:
    wrong = _strip_zh_punct(wrong)
    candidate = _strip_zh_punct(candidate)
    if not wrong or not candidate or wrong == candidate:
        return None
    max_len = max(len(wrong), len(candidate))
    edit_distance = _levenshtein_chars(wrong, candidate)
    if max_len > 6:
        return None
    if edit_distance > 3:
        return None
    wrong_zh = sum(1 for ch in wrong if re.fullmatch(r"[\u4e00-\u9fff]", ch))
    cand_zh = sum(1 for ch in candidate if re.fullmatch(r"[\u4e00-\u9fff]", ch))
    if wrong_zh < len(wrong) or cand_zh < len(candidate):
        return None
    same_pinyin_hit = _char_replace_hits(wrong, candidate, same_pinyin_map) > 0
    same_stroke_hit = _char_replace_hits(wrong, candidate, same_stroke_map) > 0
    wrong_lexicon_hit = wrong in lexicon_words
    lexicon_hit = candidate in lexicon_words
    proper_noun_hit = candidate in proper_nouns
    evidence_hits = int(same_pinyin_hit) + int(same_stroke_hit) + int(lexicon_hit) + int(proper_noun_hit)
    if max_len > 4 and evidence_hits == 0:
        return None
    if max_len > 3 and edit_distance > 2 and evidence_hits == 0:
        return None
    return {
        "wrong": wrong,
        "candidate": candidate,
        "type": _candidate_type(wrong, candidate, proper_nouns),
        "edit_distance": edit_distance,
        "same_pinyin_hit": same_pinyin_hit,
        "same_stroke_hit": same_stroke_hit,
        "wrong_lexicon_hit": wrong_lexicon_hit,
        "lexicon_hit": lexicon_hit,
        "proper_noun_hit": proper_noun_hit,
        "nonword_error_hit": lexicon_hit and not wrong_lexicon_hit and not proper_noun_hit,
    }


def _candidate_rank_key(cand: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
    evidence_hits = int(bool(cand.get("same_pinyin_hit"))) + int(bool(cand.get("same_stroke_hit"))) + int(bool(cand.get("lexicon_hit"))) + int(bool(cand.get("proper_noun_hit")))
    wrong = str(cand.get("wrong") or "")
    candidate = str(cand.get("candidate") or "")
    return (
        evidence_hits,
        -int(cand.get("edit_distance") or 0),
        -max(len(wrong), len(candidate)),
        -abs(len(wrong) - len(candidate)),
        -len(candidate),
    )


def _extract_local_candidate(
    *,
    student_text: str,
    teacher_text: str,
    same_pinyin_map: Dict[str, List[str]],
    same_stroke_map: Dict[str, List[str]],
    lexicon_words: set[str],
    proper_nouns: set[str],
) -> Optional[Dict[str, Any]]:
    src = _strip_zh_punct(student_text)
    tgt = _strip_zh_punct(teacher_text)
    if not src or not tgt or src == tgt:
        return None
    best: Optional[Dict[str, Any]] = None
    matcher = SequenceMatcher(a=src, b=tgt, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        cand = _build_candidate(
            wrong=src[i1:i2],
            candidate=tgt[j1:j2],
            same_pinyin_map=same_pinyin_map,
            same_stroke_map=same_stroke_map,
            lexicon_words=lexicon_words,
            proper_nouns=proper_nouns,
        )
        if cand is None:
            continue
        if best is None or _candidate_rank_key(cand) > _candidate_rank_key(best):
            best = cand
    if best is not None:
        return best
    prefix = 0
    while prefix < len(src) and prefix < len(tgt) and src[prefix] == tgt[prefix]:
        prefix += 1
    suffix = 0
    max_suffix = min(len(src) - prefix, len(tgt) - prefix)
    while suffix < max_suffix and src[len(src) - 1 - suffix] == tgt[len(tgt) - 1 - suffix]:
        suffix += 1
    return _build_candidate(
        wrong=src[prefix : len(src) - suffix if suffix else len(src)],
        candidate=tgt[prefix : len(tgt) - suffix if suffix else len(tgt)],
        same_pinyin_map=same_pinyin_map,
        same_stroke_map=same_stroke_map,
        lexicon_words=lexicon_words,
        proper_nouns=proper_nouns,
    )


def _runtime_transcript_noise_reason(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return "empty"
    lowered = s.lower()
    if "歌词" in s:
        return "lyrics"
    if _text_has_any_keyword(lowered, DEFAULT_RUNTIME_SOURCE_REJECT_KEYWORDS):
        return "promo_boilerplate"
    if _JP_KANA_RE.search(s):
        return "non_zh_script"
    if sum(1 for ch in s if ch.isascii() and ch.isalpha()) >= 8 or len(_LATIN_TOKEN_RE.findall(s)) >= 2:
        return "latin_heavy"
    normalized = _strip_zh_punct(s)
    if normalized.count("我爱你") >= 3 or normalized.count("哦") >= 8 or normalized.count("咱") >= 4:
        return "repetitive_noise"
    return ""


def _runtime_source_reject_reason(transcript_row: Dict[str, Any]) -> str:
    texts = [
        str(transcript_row.get("student_zh") or ""),
        str(transcript_row.get("teacher_a_zh") or ""),
        str(transcript_row.get("teacher_b_zh") or ""),
    ]
    reasons = [_runtime_transcript_noise_reason(text) for text in texts]
    empty_count = sum(1 for reason in reasons if reason == "empty")
    if "lyrics" in reasons:
        return "runtime_lyrics"
    if "promo_boilerplate" in reasons:
        return "runtime_promo_boilerplate"
    if "non_zh_script" in reasons and empty_count >= 1:
        return "runtime_non_zh_script"
    if "latin_heavy" in reasons and empty_count >= 1:
        return "runtime_latin_or_intro"
    if "repetitive_noise" in reasons and empty_count >= 1:
        return "runtime_repetitive_noise"
    if empty_count >= 2:
        return "runtime_multi_empty"
    return ""


def _merge_candidate_accumulator(
    acc: Dict[Tuple[str, str], Dict[str, Any]],
    cand: Dict[str, Any],
    meta: Dict[str, Any],
    case_id: str,
    student_text: str,
    teacher_texts: Dict[str, str],
    teacher_labels: List[str],
    *,
    consensus: bool,
    max_examples: int,
) -> None:
    key = (str(cand["wrong"]), str(cand["candidate"]))
    row = acc.setdefault(
        key,
        {
            "wrong": str(cand["wrong"]),
            "candidate": str(cand["candidate"]),
            "type": str(cand["type"]),
            "edit_distance": int(cand["edit_distance"]),
            "count_total": 0,
            "clip_count": 0,
            "source_platforms": set(),
            "source_videos": set(),
            "source_ids": set(),
            "same_pinyin_hit_count": 0,
            "same_stroke_hit_count": 0,
            "wrong_lexicon_hit_count": 0,
            "lexicon_hit_count": 0,
            "proper_noun_hit_count": 0,
            "teacher_vote_total": 0,
            "teacher_a_hit_clips": 0,
            "teacher_b_hit_clips": 0,
            "consensus_clip_count": 0,
            "clip_ids": set(),
            "example_clips": [],
        },
    )
    is_new_clip = case_id not in row["clip_ids"]
    if is_new_clip:
        row["clip_ids"].add(case_id)
        row["count_total"] += 1
        row["clip_count"] += 1
        row["source_platforms"].add(str(meta.get("platform_bucket") or meta.get("platform") or "other"))
        row["source_videos"].add(str(meta.get("source_video") or meta.get("source_id") or case_id))
        row["source_ids"].add(str(meta.get("source_id") or ""))
    if cand["same_pinyin_hit"]:
        row["same_pinyin_hit_count"] += 1
    if cand["same_stroke_hit"]:
        row["same_stroke_hit_count"] += 1
    if cand.get("wrong_lexicon_hit"):
        row["wrong_lexicon_hit_count"] += 1
    if cand["lexicon_hit"]:
        row["lexicon_hit_count"] += 1
    if cand["proper_noun_hit"]:
        row["proper_noun_hit_count"] += 1
    row["teacher_vote_total"] += len(teacher_labels)
    if "teacher_a" in teacher_labels:
        row["teacher_a_hit_clips"] += 1
    if "teacher_b" in teacher_labels:
        row["teacher_b_hit_clips"] += 1
    if consensus:
        row["consensus_clip_count"] += 1
    if is_new_clip and len(row["example_clips"]) < int(max_examples):
        row["example_clips"].append(
            {
                "id": case_id,
                "student_zh": student_text,
                "teacher_a_zh": str(teacher_texts.get("teacher_a") or ""),
                "teacher_b_zh": str(teacher_texts.get("teacher_b") or ""),
                "teacher_support_labels": list(teacher_labels),
                "consensus": bool(consensus),
                "platform": meta.get("platform"),
                "platform_bucket": meta.get("platform_bucket"),
                "source_id": meta.get("source_id"),
            }
        )


def _finalize_candidate_items(grouped: Dict[Tuple[str, str], Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in grouped.values():
        items.append(
            {
                "wrong": row["wrong"],
                "candidate": row["candidate"],
                "type": row["type"],
                "edit_distance": int(row["edit_distance"]),
                "count_total": int(row["count_total"]),
                "clip_count": int(row["clip_count"]),
                "source_platforms": sorted(x for x in row["source_platforms"] if x),
                "source_video_count": len(row["source_videos"]),
                "source_ids": sorted(x for x in row["source_ids"] if x),
                "same_pinyin_hit": int(row["same_pinyin_hit_count"]) > 0,
                "same_stroke_hit": int(row["same_stroke_hit_count"]) > 0,
                "wrong_lexicon_hit": int(row["wrong_lexicon_hit_count"]) > 0,
                "lexicon_hit": int(row["lexicon_hit_count"]) > 0,
                "proper_noun_hit": int(row["proper_noun_hit_count"]) > 0,
                "same_pinyin_hit_count": int(row["same_pinyin_hit_count"]),
                "same_stroke_hit_count": int(row["same_stroke_hit_count"]),
                "wrong_lexicon_hit_count": int(row["wrong_lexicon_hit_count"]),
                "lexicon_hit_count": int(row["lexicon_hit_count"]),
                "proper_noun_hit_count": int(row["proper_noun_hit_count"]),
                "nonword_error_hit": (
                    int(row["lexicon_hit_count"]) > 0
                    and int(row["wrong_lexicon_hit_count"]) == 0
                    and int(row["proper_noun_hit_count"]) == 0
                ),
                "teacher_vote_total": int(row["teacher_vote_total"]),
                "teacher_a_hit_clips": int(row["teacher_a_hit_clips"]),
                "teacher_b_hit_clips": int(row["teacher_b_hit_clips"]),
                "consensus_clip_count": int(row["consensus_clip_count"]),
                "example_clips": row["example_clips"],
            }
        )
    items.sort(
        key=lambda x: (
            int(x.get("consensus_clip_count") or 0),
            int(x.get("teacher_vote_total") or 0),
            int(x.get("source_video_count") or 0),
            len(x.get("source_platforms") or []),
            int(x.get("count_total") or 0),
            int(bool(x.get("same_pinyin_hit"))) + int(bool(x.get("same_stroke_hit"))) + int(bool(x.get("lexicon_hit"))) + int(bool(x.get("proper_noun_hit"))),
            -int(x.get("edit_distance") or 0),
        ),
        reverse=True,
    )
    return items


def _write_candidate_outputs(out_dir: Path, transcripts: List[Dict[str, Any]], grouped: Dict[Tuple[str, str], Dict[str, Any]], clips_total: int) -> None:
    items = _finalize_candidate_items(grouped)
    review_rows = []
    for item in items:
        review_rows.append(
            {
                **item,
                "review_decision": "",
                "final_candidate": str(item.get("candidate") or ""),
                "review_notes": "",
            }
        )
    _write_jsonl(out_dir / "transcripts.jsonl", transcripts)
    _write_json(
        out_dir / "candidate_pool.json",
        {
            "task": "asr_project_confusion_candidate_pool",
            "clips_total": int(clips_total),
            "candidate_count": len(items),
            "items": items,
        },
    )
    _write_jsonl(out_dir / "candidate_pool.review.jsonl", review_rows)


def _classify_case_error(exc: Exception) -> str:
    msg = str(exc or "").lower()
    if "does not contain any stream" in msg or "no stream" in msg:
        return "no_media_stream"
    if "invalid data found" in msg or "moov atom not found" in msg:
        return "corrupt_media"
    if "input video not found" in msg or "clip missing" in msg or "source video missing" in msg:
        return "missing_media"
    if "timed out" in msg:
        return "timeout"
    return "runtime_error"


def _mine_cases(cases: List[Dict[str, Any]], args: argparse.Namespace, out_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]]]:
    asr_dict = load_asr_dict(Path(args.asr_normalize_dict))
    same_pinyin_map = _load_same_pinyin_char_map(str(args.same_pinyin_path))
    same_stroke_map = _load_same_stroke_char_map(str(args.same_stroke_path))
    lexicon_words = _load_zh_word_set(str(args.lexicon_path), min_len=2, max_len=4, include_extras=True)
    proper_nouns = _load_zh_word_set(str(args.proper_nouns_path), min_len=2, max_len=8, include_extras=False)
    teacher_a_cfg = {
        "backend": str(getattr(args, "teacher_backend", "faster_whisper") or "faster_whisper"),
        "model_root": Path(getattr(args, "teacher_model_root", Path("assets/models/quality_asr_whisperx"))),
        "repo_id": str(getattr(args, "teacher_repo_id", "mobiuslabsgmbh/faster-whisper-large-v3-turbo") or "mobiuslabsgmbh/faster-whisper-large-v3-turbo"),
        "threads": int(getattr(args, "teacher_threads", 4) or 4),
        "sensevoice_model": str(getattr(args, "teacher_sensevoice_model", "FunAudioLLM/SenseVoiceSmall") or "FunAudioLLM/SenseVoiceSmall"),
        "sensevoice_model_dir": Path(getattr(args, "teacher_sensevoice_model_dir", Path("assets/models/common_cache_hf"))),
    }
    teacher_b_cfg = {
        "backend": str(getattr(args, "teacher_b_backend", "faster_whisper") or "faster_whisper"),
        "model_root": Path(getattr(args, "teacher_b_model_root", Path("assets/models/quality_asr_whisperx"))),
        "repo_id": str(getattr(args, "teacher_b_repo_id", "Systran/faster-whisper-medium") or "Systran/faster-whisper-medium"),
        "threads": int(getattr(args, "teacher_b_threads", 4) or 4),
        "sensevoice_model": str(getattr(args, "teacher_b_sensevoice_model", "FunAudioLLM/SenseVoiceSmall") or "FunAudioLLM/SenseVoiceSmall"),
        "sensevoice_model_dir": Path(getattr(args, "teacher_b_sensevoice_model_dir", Path("assets/models/common_cache_hf"))),
    }

    transcripts: List[Dict[str, Any]] = []
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    skipped_cases: List[Dict[str, Any]] = []
    teacher_b_mode = str(getattr(args, "teacher_b_mode", "always") or "always").strip().lower()
    for idx, case in enumerate(cases, start=1):
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            continue
        meta = dict(case.get("meta") or {}) if isinstance(case.get("meta"), dict) else {}
        video_path = Path(str(case.get("video") or ""))
        run_dir = out_dir / "runs" / case_id
        run_dir.mkdir(parents=True, exist_ok=True)
        audio_path = run_dir / "audio.wav"
        student_prefix = run_dir / "student_whispercpp"
        try:
            if not video_path.exists():
                raise FileNotFoundError(f"clip missing: {video_path}")
            extract_audio(video_path, audio_path, sample_rate=int(args.sample_rate))
            student_segments = run_asr_whispercpp(
                audio_path=audio_path,
                whisper_bin=Path(args.whisper_bin),
                model_path=Path(args.whisper_model),
                output_prefix=student_prefix,
                language="zh",
                threads=int(args.student_threads) if args.student_threads else None,
                beam_size=int(args.student_beam_size),
                vad_enable=bool(args.student_vad_enable),
                vad_model=Path(args.vad_model) if args.student_vad_enable and args.vad_model else None,
                vad_thold=float(args.vad_thold),
                vad_min_sil_ms=int(args.vad_min_sil_ms),
            )
            teacher_a_raw = _run_teacher_transcript(audio_path=audio_path, config=teacher_a_cfg)
            student_text = _norm_text(_join_student_segments(student_segments), asr_dict)
            teacher_a_text = _norm_text(teacher_a_raw, asr_dict)
            cand_a = _extract_local_candidate(
                student_text=student_text,
                teacher_text=teacher_a_text,
                same_pinyin_map=same_pinyin_map,
                same_stroke_map=same_stroke_map,
                lexicon_words=lexicon_words,
                proper_nouns=proper_nouns,
            )
            run_teacher_b = teacher_b_mode == "always" or (teacher_b_mode == "candidate_only" and cand_a is not None)
            teacher_b_raw = _run_teacher_transcript(audio_path=audio_path, config=teacher_b_cfg) if run_teacher_b else ""
            teacher_b_text = _norm_text(teacher_b_raw, asr_dict)
            cand_b = _extract_local_candidate(
                student_text=student_text,
                teacher_text=teacher_b_text,
                same_pinyin_map=same_pinyin_map,
                same_stroke_map=same_stroke_map,
                lexicon_words=lexicon_words,
                proper_nouns=proper_nouns,
            )
            consensus_key: Optional[Tuple[str, str]] = None
            if cand_a and cand_b:
                key_a = (str(cand_a["wrong"]), str(cand_a["candidate"]))
                key_b = (str(cand_b["wrong"]), str(cand_b["candidate"]))
                if key_a == key_b:
                    consensus_key = key_a
            transcripts.append(
                {
                    "id": case_id,
                    "student_zh": student_text,
                    "teacher_a_zh": teacher_a_text,
                    "teacher_b_zh": teacher_b_text,
                    "teacher_a_candidate": cand_a,
                    "teacher_b_candidate": cand_b,
                    "consensus_candidate": cand_a if consensus_key else None,
                    "meta": meta,
                }
            )
            candidate_entries: Dict[Tuple[str, str], Dict[str, Any]] = {}
            if cand_a:
                key_a = (str(cand_a["wrong"]), str(cand_a["candidate"]))
                candidate_entries[key_a] = {"cand": cand_a, "labels": ["teacher_a"]}
            if cand_b:
                key_b = (str(cand_b["wrong"]), str(cand_b["candidate"]))
                if key_b in candidate_entries:
                    candidate_entries[key_b]["labels"].append("teacher_b")
                else:
                    candidate_entries[key_b] = {"cand": cand_b, "labels": ["teacher_b"]}
            for key, item in candidate_entries.items():
                _merge_candidate_accumulator(
                    grouped,
                    item["cand"],
                    meta,
                    case_id,
                    student_text,
                    {"teacher_a": teacher_a_text, "teacher_b": teacher_b_text},
                    item["labels"],
                    consensus=(consensus_key == key),
                    max_examples=int(args.max_examples),
                )
        except Exception as exc:
            if not bool(getattr(args, "skip_case_errors", False)):
                raise
            skipped = {
                "id": case_id,
                "video": str(video_path),
                "stage": "mine_case",
                "error_type": _classify_case_error(exc),
                "error": str(exc)[:1000],
                "ts": _now_iso(),
                "meta": meta,
            }
            skipped_cases.append(skipped)
            print(f"[warn] skip mined clip {case_id}: {skipped['error_type']}: {exc}")
        finally:
            if bool(args.cleanup_audio):
                _safe_unlink(audio_path)
            if bool(args.cleanup_student_json):
                _safe_unlink(student_prefix.with_suffix(".json"))
                _safe_unlink(student_prefix.with_suffix(".txt"))
            if bool(args.delete_clip_after_mine):
                _safe_unlink(video_path)
        if idx % 20 == 0:
            print(f"[mine] processed {idx}/{len(cases)} clips")
    if skipped_cases:
        _write_jsonl(out_dir / "skipped_cases.jsonl", skipped_cases)
    return transcripts, grouped


def cmd_mine(args: argparse.Namespace) -> None:
    cases = _read_json_or_jsonl(Path(args.cases_jsonl))
    if not cases:
        raise SystemExit("No cases to mine.")
    out_dir = Path(args.out_dir)
    transcripts, grouped = _mine_cases(cases, args, out_dir)
    _write_candidate_outputs(out_dir, transcripts, grouped, clips_total=len(cases))
    print(f"[ok] mined {len(grouped)} candidate pairs from {len(cases)} clips")


def _chunk_list(items: List[Dict[str, Any]], batch_size: int) -> List[List[Dict[str, Any]]]:
    size = max(1, int(batch_size))
    return [items[i : i + size] for i in range(0, len(items), size)]


def _copy_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(**vars(args))


def _merge_transcripts(dst: List[Dict[str, Any]], src: List[Dict[str, Any]]) -> None:
    dst.extend(src)


def _merge_grouped_candidates(dst: Dict[Tuple[str, str], Dict[str, Any]], src: Dict[Tuple[str, str], Dict[str, Any]], max_examples: int) -> None:
    for key, row in src.items():
        cur = dst.setdefault(
            key,
            {
                "wrong": row["wrong"],
                "candidate": row["candidate"],
                "type": row["type"],
                "edit_distance": int(row["edit_distance"]),
                "count_total": 0,
                "clip_count": 0,
                "source_platforms": set(),
                "source_videos": set(),
                "source_ids": set(),
                "same_pinyin_hit_count": 0,
                "same_stroke_hit_count": 0,
                "wrong_lexicon_hit_count": 0,
                "lexicon_hit_count": 0,
                "proper_noun_hit_count": 0,
                "teacher_vote_total": 0,
                "teacher_a_hit_clips": 0,
                "teacher_b_hit_clips": 0,
                "consensus_clip_count": 0,
                "clip_ids": set(),
                "example_clips": [],
            },
        )
        cur["count_total"] += int(row.get("count_total") or 0)
        cur["clip_count"] += int(row.get("clip_count") or 0)
        cur["source_platforms"].update(set(row.get("source_platforms") or []))
        cur["source_videos"].update(set(row.get("source_videos") or []))
        cur["source_ids"].update(set(row.get("source_ids") or []))
        cur["same_pinyin_hit_count"] += int(row.get("same_pinyin_hit_count") or 0)
        cur["same_stroke_hit_count"] += int(row.get("same_stroke_hit_count") or 0)
        cur["wrong_lexicon_hit_count"] += int(row.get("wrong_lexicon_hit_count") or 0)
        cur["lexicon_hit_count"] += int(row.get("lexicon_hit_count") or 0)
        cur["proper_noun_hit_count"] += int(row.get("proper_noun_hit_count") or 0)
        cur["teacher_vote_total"] += int(row.get("teacher_vote_total") or 0)
        cur["teacher_a_hit_clips"] += int(row.get("teacher_a_hit_clips") or 0)
        cur["teacher_b_hit_clips"] += int(row.get("teacher_b_hit_clips") or 0)
        cur["consensus_clip_count"] += int(row.get("consensus_clip_count") or 0)
        for cid in row.get("clip_ids") or []:
            if cid:
                cur["clip_ids"].add(str(cid))
        for ex in row.get("example_clips") or []:
            if len(cur["example_clips"]) >= int(max_examples):
                break
            ex_id = str((ex or {}).get("id") or "").strip() if isinstance(ex, dict) else ""
            if ex_id and all(str(item.get("id") or "") != ex_id for item in cur["example_clips"]):
                cur["example_clips"].append(ex)


def _host_path_to_container_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(repo_root)
    except Exception as exc:
        raise ValueError(f"path is outside repo_root and not visible to container: {resolved}") from exc
    return "/app/" + rel.as_posix()


def _arg_path_to_container(value: Path) -> str:
    p = Path(value)
    if str(p).startswith("/app/"):
        return str(p)
    if p.is_absolute():
        return _host_path_to_container_path(p)
    return "/app/" + p.as_posix()


def _grouped_from_candidate_pool(path: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    pool = json.loads(path.read_text(encoding="utf-8"))
    items = [x for x in (pool.get("items") or []) if isinstance(x, dict)]
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in items:
        wrong = str(row.get("wrong") or "").strip()
        candidate = str(row.get("candidate") or "").strip()
        if not wrong or not candidate:
            continue
        out[(wrong, candidate)] = {
            "wrong": wrong,
            "candidate": candidate,
            "type": str(row.get("type") or "").strip(),
            "edit_distance": int(row.get("edit_distance") or 0),
            "count_total": int(row.get("count_total") or 0),
            "clip_count": int(row.get("clip_count") or 0),
            "source_platforms": set(row.get("source_platforms") or []),
            "source_videos": set(row.get("source_ids") or []),
            "source_ids": set(row.get("source_ids") or []),
            "same_pinyin_hit_count": int(row.get("same_pinyin_hit_count") or 0),
            "same_stroke_hit_count": int(row.get("same_stroke_hit_count") or 0),
            "wrong_lexicon_hit_count": int(row.get("wrong_lexicon_hit_count") or 0),
            "lexicon_hit_count": int(row.get("lexicon_hit_count") or 0),
            "proper_noun_hit_count": int(row.get("proper_noun_hit_count") or 0),
            "teacher_vote_total": int(row.get("teacher_vote_total") or 0),
            "teacher_a_hit_clips": int(row.get("teacher_a_hit_clips") or 0),
            "teacher_b_hit_clips": int(row.get("teacher_b_hit_clips") or 0),
            "consensus_clip_count": int(row.get("consensus_clip_count") or 0),
            "clip_ids": set(),
            "example_clips": list(row.get("example_clips") or []),
        }
    return out


def _build_container_mine_cmd(args: argparse.Namespace, cases_jsonl: Path, out_dir: Path) -> List[str]:
    cmd = [
        "docker",
        "exec",
        "yizhi-backend-lite",
        "python3",
        "/app/pipelines/tools/mine_asr_project_confusions.py",
        "mine",
        "--cases-jsonl",
        _host_path_to_container_path(cases_jsonl),
        "--out-dir",
        _host_path_to_container_path(out_dir),
        "--whisper-bin",
        _arg_path_to_container(args.whisper_bin),
        "--whisper-model",
        _arg_path_to_container(args.whisper_model),
        "--vad-model",
        _arg_path_to_container(args.vad_model),
        "--student-threads",
        str(args.student_threads),
        "--student-beam-size",
        str(args.student_beam_size),
        "--vad-thold",
        str(args.vad_thold),
        "--vad-min-sil-ms",
        str(args.vad_min_sil_ms),
        "--teacher-backend",
        str(args.teacher_backend),
        "--teacher-model-root",
        _arg_path_to_container(args.teacher_model_root),
        "--teacher-repo-id",
        str(args.teacher_repo_id),
        "--teacher-threads",
        str(args.teacher_threads),
        "--teacher-sensevoice-model",
        str(args.teacher_sensevoice_model),
        "--teacher-sensevoice-model-dir",
        _arg_path_to_container(args.teacher_sensevoice_model_dir),
        "--teacher-b-backend",
        str(args.teacher_b_backend),
        "--teacher-b-mode",
        str(getattr(args, "teacher_b_mode", "always") or "always"),
        "--teacher-b-model-root",
        _arg_path_to_container(args.teacher_b_model_root),
        "--teacher-b-repo-id",
        str(args.teacher_b_repo_id),
        "--teacher-b-threads",
        str(args.teacher_b_threads),
        "--teacher-b-sensevoice-model",
        str(args.teacher_b_sensevoice_model),
        "--teacher-b-sensevoice-model-dir",
        _arg_path_to_container(args.teacher_b_sensevoice_model_dir),
        "--sample-rate",
        str(args.sample_rate),
        "--asr-normalize-dict",
        _arg_path_to_container(args.asr_normalize_dict),
        "--same-pinyin-path",
        _arg_path_to_container(args.same_pinyin_path),
        "--same-stroke-path",
        _arg_path_to_container(args.same_stroke_path),
        "--lexicon-path",
        _arg_path_to_container(args.lexicon_path),
        "--proper-nouns-path",
        _arg_path_to_container(args.proper_nouns_path),
        "--max-examples",
        str(args.max_examples),
    ]
    if bool(args.student_vad_enable):
        cmd.append("--student-vad-enable")
    if bool(args.cleanup_audio):
        cmd.append("--cleanup-audio")
    if bool(args.cleanup_student_json):
        cmd.append("--cleanup-student-json")
    if bool(args.delete_batch_clips):
        cmd.append("--delete-clip-after-mine")
    if bool(getattr(args, "skip_case_errors", False)):
        cmd.append("--skip-case-errors")
    return cmd


def cmd_batch_runner(args: argparse.Namespace) -> None:
    plan = json.loads(Path(args.clip_plan).read_text(encoding="utf-8"))
    clips = [x for x in (plan.get("clips") or []) if isinstance(x, dict)]
    if not clips:
        raise SystemExit("No clips found in clip plan.")
    out_dir = Path(args.out_dir)
    batches = _chunk_list(clips, int(args.batch_size))
    aggregate_transcripts: List[Dict[str, Any]] = []
    aggregate_grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    processed_clips = 0

    for batch_idx, batch_clips in enumerate(batches, start=1):
        batch_dir = out_dir / "batches" / f"batch_{batch_idx:04d}"
        batch_clips_dir = batch_dir / "clips"
        batch_cases: List[Dict[str, Any]] = []
        print(f"[batch] {batch_idx}/{len(batches)} preparing {len(batch_clips)} clips")
        for clip in batch_clips:
            case_id = str(clip.get("id") or "").strip()
            if not case_id:
                continue
            src = Path(str(clip.get("video") or ""))
            if not src.exists():
                raise FileNotFoundError(f"source video missing: {src}")
            out_video = batch_clips_dir / f"{case_id}.mp4"
            _ffmpeg_cut(src, out_video, float(clip.get("start_s") or 0.0), float(clip.get("end_s") or 0.0))
            stream_info = _probe_media_streams(out_video)
            if not bool(stream_info.get("has_audio")):
                raise RuntimeError(f"cut clip has no audio stream: {out_video}")
            batch_cases.append(
                {
                    "id": case_id,
                    "video": str(out_video),
                    "meta": {
                        "source_id": clip.get("source_id"),
                        "source_video": clip.get("video"),
                        "platform": clip.get("platform"),
                        "platform_bucket": clip.get("platform_bucket"),
                        "category": clip.get("category"),
                        "account_type": clip.get("account_type"),
                        "start_s": clip.get("start_s"),
                        "end_s": clip.get("end_s"),
                        "duration_s": clip.get("duration_s"),
                    },
                }
            )
        if not batch_cases:
            continue
        batch_args = _copy_namespace(args)
        setattr(batch_args, "delete_clip_after_mine", bool(args.delete_batch_clips))
        transcripts, grouped = _mine_cases(batch_cases, batch_args, batch_dir / "mine")
        _write_candidate_outputs(batch_dir / "mine", transcripts, grouped, clips_total=len(batch_cases))
        _merge_transcripts(aggregate_transcripts, transcripts)
        _merge_grouped_candidates(aggregate_grouped, grouped, int(args.max_examples))
        processed_clips += len(batch_cases)
        _write_candidate_outputs(out_dir, aggregate_transcripts, aggregate_grouped, clips_total=processed_clips)
        _write_json(
            out_dir / "batch_progress.json",
            {
                "task": "asr_project_confusion_batch_runner",
                "batches_total": len(batches),
                "batches_completed": batch_idx,
                "clips_total": len(clips),
                "clips_processed": processed_clips,
                "candidate_pairs_so_far": len(aggregate_grouped),
            },
        )
        if bool(args.delete_batch_clips):
            shutil.rmtree(batch_clips_dir, ignore_errors=True)
        if bool(args.delete_batch_runs):
            shutil.rmtree(batch_dir / "mine" / "runs", ignore_errors=True)
        print(f"[batch] {batch_idx}/{len(batches)} done; processed={processed_clips}/{len(clips)}")

    print(f"[ok] batch runner finished: clips={processed_clips}, candidate_pairs={len(aggregate_grouped)}")


def cmd_download_batch_runner(args: argparse.Namespace) -> None:
    plan = json.loads(Path(args.clip_plan).read_text(encoding="utf-8"))
    clips = [x for x in (plan.get("clips") or []) if isinstance(x, dict)]
    if not clips:
        raise SystemExit("No clips found in clip plan.")
    out_dir = Path(args.out_dir)
    try:
        out_dir.resolve().relative_to((repo_root / "outputs").resolve())
    except Exception as exc:
        raise SystemExit("download-batch-runner requires --out-dir under repo outputs/ so the container can see temp clips") from exc
    batches = _chunk_list(clips, int(args.batch_size))
    aggregate_transcripts: List[Dict[str, Any]] = []
    aggregate_grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    processed_clips = 0
    skipped_cases: List[Dict[str, Any]] = []
    runtime_rejected_sources: List[Dict[str, Any]] = []
    runtime_rejected_source_map: Dict[str, Dict[str, Any]] = {}
    status_path = out_dir / "batch_progress.json"
    events_path = out_dir / "events.jsonl"
    batches_path = out_dir / "batch_history.jsonl"
    runtime_rejected_sources_path = out_dir / "runtime_rejected_sources.jsonl"
    completed_batch_idx = 0
    source_cache_dir = out_dir / "source_cache"
    source_cache_dir.mkdir(parents=True, exist_ok=True)
    source_cache: Dict[str, Path] = {}

    def write_status(stage: str, **extra: Any) -> None:
        payload = {
            "task": "asr_project_confusion_download_batch_runner",
            "stage": stage,
            "updated_at": _now_iso(),
            "batches_total": len(batches),
            "clips_total": len(clips),
            "clips_processed": processed_clips,
            "candidate_pairs_so_far": len(aggregate_grouped),
            "skipped_cases": len(skipped_cases),
            "runtime_rejected_sources": len(runtime_rejected_source_map),
        }
        payload.update(extra)
        _write_json(status_path, payload)

    if bool(getattr(args, "resume", False)):
        if (out_dir / "transcripts.jsonl").exists():
            aggregate_transcripts = [x for x in _read_json_or_jsonl(out_dir / "transcripts.jsonl") if isinstance(x, dict)]
        if (out_dir / "candidate_pool.json").exists():
            aggregate_grouped = _grouped_from_candidate_pool(out_dir / "candidate_pool.json")
        if (out_dir / "skipped_cases.jsonl").exists():
            skipped_cases = [x for x in _read_json_or_jsonl(out_dir / "skipped_cases.jsonl") if isinstance(x, dict)]
        if runtime_rejected_sources_path.exists():
            runtime_rejected_sources = [x for x in _read_json_or_jsonl(runtime_rejected_sources_path) if isinstance(x, dict)]
            runtime_rejected_source_map = {
                str(x.get("source_id") or "").strip(): x
                for x in runtime_rejected_sources
                if str(x.get("source_id") or "").strip()
            }
        if batches_path.exists():
            history_rows = [x for x in _read_json_or_jsonl(batches_path) if isinstance(x, dict)]
            completed_rows = [
                row
                for row in history_rows
                if str(row.get("status") or "").strip() in {"completed", "skipped_all"}
            ]
            if completed_rows:
                last_row = max(completed_rows, key=lambda x: int(x.get("batch_idx") or 0))
                completed_batch_idx = int(last_row.get("batch_idx") or 0)
                processed_clips = int(last_row.get("clips_processed_total") or 0)
        if completed_batch_idx > 0:
            print(f"[resume] continuing from batch {completed_batch_idx + 1}/{len(batches)}")
            _append_jsonl(
                events_path,
                {
                    "ts": _now_iso(),
                    "event": "runner_resumed",
                    "completed_batch_idx": completed_batch_idx,
                    "clips_processed_total": processed_clips,
                    "candidate_pairs_so_far": len(aggregate_grouped),
                    "skipped_cases_total": len(skipped_cases),
                },
            )
            write_status("resumed", current_batch=completed_batch_idx + 1)

    write_status("started")
    _append_jsonl(events_path, {"ts": _now_iso(), "event": "runner_started", "batches_total": len(batches), "clips_total": len(clips)})

    for batch_idx, batch_clips in enumerate(batches, start=1):
        if batch_idx <= completed_batch_idx:
            continue
        batch_dir = out_dir / "batches" / f"batch_{batch_idx:04d}"
        batch_clips_dir = batch_dir / "clips"
        batch_sources_dir = batch_dir / "source_videos"
        batch_cases_path = batch_dir / "cases.container.jsonl"
        batch_cases: List[Dict[str, Any]] = []
        print(f"[download-batch] {batch_idx}/{len(batches)} preparing {len(batch_clips)} clips")
        write_status("batch_preparing", current_batch=batch_idx, batch_clips=len(batch_clips))
        _append_jsonl(events_path, {"ts": _now_iso(), "event": "batch_preparing", "batch_idx": batch_idx, "batch_clips": len(batch_clips)})
        for clip in batch_clips:
            case_id = str(clip.get("id") or "").strip()
            source_ref = str(clip.get("video") or "").strip()
            source_id = str(clip.get("source_id") or case_id).strip()
            if not case_id or not source_ref:
                continue
            if source_id in runtime_rejected_source_map:
                skipped = {
                    "id": case_id,
                    "source_id": source_id,
                    "source_video": source_ref,
                    "stage": "runtime_source_reject",
                    "error": str(runtime_rejected_source_map[source_id].get("reason") or "runtime_source_reject"),
                    "batch_idx": batch_idx,
                    "ts": _now_iso(),
                }
                skipped_cases.append(skipped)
                _append_jsonl(events_path, {"ts": _now_iso(), "event": "clip_skipped", **skipped})
                continue
            out_video = batch_clips_dir / f"{case_id}.mp4"
            try:
                local_src = source_cache.get(source_ref)
                if local_src is None:
                    if _is_url_ref(source_ref):
                        local_src = source_cache_dir / f"{source_id}.mp4"
                        if not local_src.exists():
                            _download_video_yt_dlp(
                                source_ref,
                                local_src,
                                max_height=int(args.download_max_height),
                                cookiefile=str(args.download_cookiefile or ""),
                            )
                    else:
                        local_src = Path(source_ref)
                        if not local_src.exists():
                            raise FileNotFoundError(f"source video missing: {local_src}")
                    source_cache[source_ref] = local_src
                _ffmpeg_cut(local_src, out_video, float(clip.get("start_s") or 0.0), float(clip.get("end_s") or 0.0))
                stream_info = _probe_media_streams(out_video)
                if not bool(stream_info.get("has_audio")):
                    raise RuntimeError(f"cut clip has no audio stream: {out_video}")
                batch_cases.append(
                    {
                        "id": case_id,
                        "video": _host_path_to_container_path(out_video),
                        "meta": {
                            "source_id": clip.get("source_id"),
                            "source_video": source_ref,
                            "source_is_remote": bool(clip.get("is_remote")),
                            "platform": clip.get("platform"),
                            "platform_bucket": clip.get("platform_bucket"),
                            "category": clip.get("category"),
                            "account_type": clip.get("account_type"),
                            "start_s": clip.get("start_s"),
                            "end_s": clip.get("end_s"),
                            "duration_s": clip.get("duration_s"),
                        },
                    }
                )
            except Exception as exc:
                if not bool(getattr(args, "skip_source_errors", False)):
                    raise
                print(f"[warn] skip clip {case_id}: {exc}")
                skipped = {
                    "id": case_id,
                    "source_id": source_id,
                    "source_video": source_ref,
                    "stage": "download_or_cut",
                    "error": str(exc)[:500],
                    "batch_idx": batch_idx,
                    "ts": _now_iso(),
                }
                skipped_cases.append(skipped)
                _append_jsonl(events_path, {"ts": _now_iso(), "event": "clip_skipped", **skipped})
                _safe_unlink(out_video)
                continue
        if not batch_cases:
            _write_jsonl(out_dir / "skipped_cases.jsonl", skipped_cases)
            _append_jsonl(batches_path, {"ts": _now_iso(), "batch_idx": batch_idx, "status": "skipped_all", "requested_clips": len(batch_clips), "usable_clips": 0})
            write_status("batch_skipped_all", current_batch=batch_idx)
            continue
        _write_jsonl(batch_cases_path, batch_cases)
        mine_out_dir = batch_dir / "mine"
        write_status("batch_mining", current_batch=batch_idx, usable_batch_clips=len(batch_cases))
        _append_jsonl(events_path, {"ts": _now_iso(), "event": "batch_mining", "batch_idx": batch_idx, "usable_clips": len(batch_cases)})
        cmd = _build_container_mine_cmd(args, batch_cases_path, mine_out_dir)
        try:
            subprocess.run(cmd, check=True, cwd=str(repo_root))
        except subprocess.CalledProcessError as exc:
            write_status("failed", current_batch=batch_idx, usable_batch_clips=len(batch_cases), error=str(exc)[:1000])
            _append_jsonl(events_path, {"ts": _now_iso(), "event": "batch_failed", "batch_idx": batch_idx, "error": str(exc)[:1000]})
            raise
        transcripts = _read_json_or_jsonl(mine_out_dir / "transcripts.jsonl")
        grouped = _grouped_from_candidate_pool(mine_out_dir / "candidate_pool.json")
        mine_skipped_path = mine_out_dir / "skipped_cases.jsonl"
        if mine_skipped_path.exists():
            for row in _read_json_or_jsonl(mine_skipped_path):
                if not isinstance(row, dict):
                    continue
                cur = dict(row)
                cur.setdefault("batch_idx", batch_idx)
                skipped_cases.append(cur)
        for row in transcripts:
            if not isinstance(row, dict):
                continue
            source_id = str(((row.get("meta") or {}).get("source_id")) or "").strip()
            if not source_id or source_id in runtime_rejected_source_map:
                continue
            reject_reason = _runtime_source_reject_reason(row)
            if not reject_reason:
                continue
            source_row = {
                "ts": _now_iso(),
                "source_id": source_id,
                "source_video": str(((row.get("meta") or {}).get("source_video")) or ""),
                "category": str(((row.get("meta") or {}).get("category")) or ""),
                "reason": reject_reason,
                "batch_idx": batch_idx,
                "case_id": str(row.get("id") or ""),
            }
            runtime_rejected_source_map[source_id] = source_row
            runtime_rejected_sources.append(source_row)
            _append_jsonl(
                events_path,
                {"ts": _now_iso(), "event": "source_rejected_runtime", **source_row},
            )
        _merge_transcripts(aggregate_transcripts, transcripts)
        _merge_grouped_candidates(aggregate_grouped, grouped, int(args.max_examples))
        processed_clips += len(batch_cases)
        _write_candidate_outputs(out_dir, aggregate_transcripts, aggregate_grouped, clips_total=processed_clips)
        if runtime_rejected_sources:
            _write_jsonl(runtime_rejected_sources_path, runtime_rejected_sources)
        _write_json(
            status_path,
            {
                "task": "asr_project_confusion_download_batch_runner",
                "stage": "batch_completed",
                "updated_at": _now_iso(),
                "batches_total": len(batches),
                "batches_completed": batch_idx,
                "clips_total": len(clips),
                "clips_processed": processed_clips,
                "candidate_pairs_so_far": len(aggregate_grouped),
                "skipped_cases": len(skipped_cases),
                "runtime_rejected_sources": len(runtime_rejected_source_map),
                "current_batch": batch_idx,
                "usable_batch_clips": len(batch_cases),
            },
        )
        if skipped_cases:
            _write_jsonl(out_dir / "skipped_cases.jsonl", skipped_cases)
        _append_jsonl(
            batches_path,
            {
                "ts": _now_iso(),
                "batch_idx": batch_idx,
                "status": "completed",
                "requested_clips": len(batch_clips),
                "usable_clips": len(batch_cases),
                "clips_processed_total": processed_clips,
                "candidate_pairs_so_far": len(aggregate_grouped),
                "skipped_cases_total": len(skipped_cases),
            },
        )
        _append_jsonl(
            events_path,
            {
                "ts": _now_iso(),
                "event": "batch_completed",
                "batch_idx": batch_idx,
                "usable_clips": len(batch_cases),
                "clips_processed_total": processed_clips,
                "candidate_pairs_so_far": len(aggregate_grouped),
            },
        )
        if bool(args.delete_batch_clips):
            _safe_rmtree(batch_clips_dir)
        if bool(args.delete_batch_runs):
            _safe_rmtree(batch_dir / "mine" / "runs")
        if bool(args.delete_batch_source_videos):
            _safe_rmtree(batch_sources_dir)
        print(f"[download-batch] {batch_idx}/{len(batches)} done; processed={processed_clips}/{len(clips)}")

    if skipped_cases:
        _write_jsonl(out_dir / "skipped_cases.jsonl", skipped_cases)
    if runtime_rejected_sources:
        _write_jsonl(runtime_rejected_sources_path, runtime_rejected_sources)
    write_status("completed", batches_completed=len(batches))
    _append_jsonl(events_path, {"ts": _now_iso(), "event": "runner_completed", "clips_processed_total": processed_clips, "candidate_pairs_so_far": len(aggregate_grouped), "skipped_cases_total": len(skipped_cases)})
    print(f"[ok] download batch runner finished: clips={processed_clips}, candidate_pairs={len(aggregate_grouped)}")


def _candidate_evidence_score(item: Dict[str, Any]) -> int:
    score = 0
    score += min(8, int(item.get("count_total") or 0))
    score += min(5, int(item.get("source_video_count") or 0))
    score += min(3, len(item.get("source_platforms") or []))
    score += int(bool(item.get("same_pinyin_hit")))
    score += int(bool(item.get("same_stroke_hit")))
    score += int(bool(item.get("lexicon_hit")))
    score += int(bool(item.get("proper_noun_hit")))
    return score


def _read_review_jsonl(path: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    rows = _read_json_or_jsonl(path)
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        wrong = str(row.get("wrong") or "").strip()
        candidate = str(row.get("candidate") or "").strip()
        if wrong and candidate:
            out[(wrong, candidate)] = row
    return out


def _int_or_default(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _repo_rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except Exception:
        return str(path)


def _collect_example_pairs(rows: List[Dict[str, Any]], limit: int = 4) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for row in rows:
        wrong = str(row.get("wrong") or "").strip()
        candidate = str(row.get("candidate") or row.get("final_candidate") or "").strip()
        if not wrong or not candidate:
            continue
        label = f"{wrong} -> {candidate}"
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
        if len(out) >= max(1, int(limit or 4)):
            break
    return out


def _read_removed_examples(path: Optional[Path]) -> List[str]:
    if path is None:
        return []
    rows = _read_json_or_jsonl(path)
    out: List[str] = []
    seen: set[str] = set()
    for row in rows:
        wrong = str(row.get("wrong") or "").strip()
        candidate = str(row.get("candidate") or row.get("final_candidate") or "").strip()
        if not wrong or not candidate:
            continue
        label = f"{wrong} -> {candidate}"
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def _build_asset_from_final_reviewed_rows(
    rows: List[Dict[str, Any]],
    *,
    source_label: str,
    max_edit_distance: int = 2,
    evidence_floor: int = 2,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    type_counts: Dict[str, int] = defaultdict(int)
    reviewed_count = 0
    for row in rows:
        wrong = str(row.get("wrong") or "").strip()
        candidate = str(row.get("candidate") or row.get("final_candidate") or "").strip()
        pair_type = str(row.get("pair_type") or row.get("type") or "").strip() or "reviewed"
        if not wrong or not candidate:
            continue
        reviewed_count += 1
        type_counts[pair_type] += 1
        dst = grouped.setdefault(
            wrong,
            {
                "wrong": wrong,
                "candidates": [],
                "type": pair_type,
                "evidence_count": 0,
                "sources": set(),
                "requires_high_risk": True,
                "max_edit_distance": max(1, int(max_edit_distance or 2)),
                "notes": [],
            },
        )
        if candidate not in dst["candidates"]:
            dst["candidates"].append(candidate)
        dst["evidence_count"] = max(
            int(dst["evidence_count"]),
            int(evidence_floor or 2),
            _int_or_default(row.get("teacher_vote_total"), 0),
            _int_or_default(row.get("consensus_clip_count"), 0),
            _int_or_default(row.get("source_video_count"), 0),
        )
        dst["sources"].add(str(source_label or "manual_review").strip() or "manual_review")
        note_fields = [
            str(row.get("optimized_reason") or "").strip(),
            str(row.get("second_pass_note") or "").strip(),
        ]
        pattern_wrong = str(row.get("optimized_pattern_wrong") or row.get("best_pattern_wrong") or "").strip()
        pattern_candidate = str(row.get("optimized_pattern_candidate") or row.get("best_pattern_candidate") or "").strip()
        if pattern_wrong and pattern_candidate:
            note_fields.append(f"例:{pattern_wrong}->{pattern_candidate}")
        for note in note_fields:
            if note and note not in dst["notes"]:
                dst["notes"].append(note)

    asset_items: List[Dict[str, Any]] = []
    for row in grouped.values():
        asset_items.append(
            {
                "wrong": row["wrong"],
                "candidates": row["candidates"],
                "type": row["type"],
                "evidence_count": int(row["evidence_count"]),
                "sources": sorted(row["sources"]),
                "requires_high_risk": True,
                "max_edit_distance": int(row["max_edit_distance"]),
                "notes": " | ".join(row["notes"][:3]).strip(),
            }
        )
    asset_items.sort(
        key=lambda x: (
            int(x.get("evidence_count") or 0),
            len(x.get("candidates") or []),
            len(str(x.get("wrong") or "")),
            str(x.get("wrong") or ""),
        ),
        reverse=True,
    )
    asset = {
        "version": 2,
        "notes": "Reviewed ASR project confusions promoted from the final manually reviewed confusion set. Only accepted items should enter this file.",
        "items": asset_items,
    }
    summary = {
        "reviewed_pair_count": int(reviewed_count),
        "formal_item_count": len(asset_items),
        "type_counts": dict(sorted(type_counts.items(), key=lambda kv: kv[0])),
        "example_kept_pairs": _collect_example_pairs(rows, limit=6),
    }
    return asset, summary


def _render_publish_summary_md(
    *,
    reviewed_final_path: Path,
    asset_out: Path,
    source_label: str,
    summary: Dict[str, Any],
    args: argparse.Namespace,
    removed_examples: List[str],
) -> str:
    lines: List[str] = []
    lines.append("# ASR项目混淆正式集发布摘要")
    lines.append("")
    lines.append(f"- 终审清单：`{_repo_rel(reviewed_final_path)}`")
    lines.append(f"- 正式集资产：`{_repo_rel(asset_out)}`")
    lines.append(f"- 来源标签：`{source_label}`")
    lines.append(f"- 终审 pair 数：`{int(summary.get('reviewed_pair_count') or 0)}`")
    lines.append(f"- 正式集条数：`{int(summary.get('formal_item_count') or 0)}`")
    type_counts = summary.get("type_counts") or {}
    if isinstance(type_counts, dict) and type_counts:
        parts = [f"`{k}={int(v)}`" for k, v in sorted(type_counts.items())]
        lines.append(f"- 结构分布：{' / '.join(parts)}")
    if int(getattr(args, "manual_pool_size", 0) or 0) > 0:
        lines.append(f"- 人工候选池规模：`{int(args.manual_pool_size)}`")
    if any(int(getattr(args, name, 0) or 0) > 0 for name in ("first_pass_accept", "first_pass_review", "first_pass_reject")):
        lines.append(
            "- 首轮模拟审核："
            f" `accept={int(getattr(args, 'first_pass_accept', 0) or 0)}`"
            f" / `review={int(getattr(args, 'first_pass_review', 0) or 0)}`"
            f" / `reject={int(getattr(args, 'first_pass_reject', 0) or 0)}`"
        )
    if any(int(getattr(args, name, 0) or 0) > 0 for name in ("core_accept", "core_review", "core_reject")):
        lines.append(
            "- 核心 pair 粗统计："
            f" `accept={int(getattr(args, 'core_accept', 0) or 0)}`"
            f" / `review={int(getattr(args, 'core_review', 0) or 0)}`"
            f" / `reject={int(getattr(args, 'core_reject', 0) or 0)}`"
        )
    if any(int(getattr(args, name, 0) or 0) > 0 for name in ("second_pass_promoted", "second_pass_kept_review", "second_pass_rejected")):
        lines.append(
            "- 二次复核："
            f" `promoted={int(getattr(args, 'second_pass_promoted', 0) or 0)}`"
            f" / `kept_review={int(getattr(args, 'second_pass_kept_review', 0) or 0)}`"
            f" / `rejected={int(getattr(args, 'second_pass_rejected', 0) or 0)}`"
        )
    kept = summary.get("example_kept_pairs") or []
    if kept:
        lines.append("")
        lines.append("## 代表性保留")
        lines.append("")
        for item in kept[:6]:
            lines.append(f"- `{item}`")
    if removed_examples:
        lines.append("")
        lines.append("## 代表性删除")
        lines.append("")
        for item in removed_examples[:6]:
            lines.append(f"- `{item}`")
    return "\n".join(lines).rstrip() + "\n"


def _next_archive_subsection_title(doc_path: Path, default_title: str) -> str:
    try:
        raw = doc_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return f"### 6.1 {default_title}"
    nums = [int(m.group(1)) for m in re.finditer(r"^###\s+6\.(\d+)\b", raw, flags=re.MULTILINE)]
    next_num = (max(nums) + 1) if nums else 1
    return f"### 6.{next_num} {default_title}"


def _append_archive_section(
    *,
    doc_path: Path,
    reviewed_final_path: Path,
    asset_out: Path,
    source_label: str,
    summary: Dict[str, Any],
    args: argparse.Namespace,
    removed_examples: List[str],
) -> str:
    title = str(getattr(args, "archive_title", "") or "").strip() or datetime.now().strftime("%Y-%m-%d 正式集发布")
    header = _next_archive_subsection_title(doc_path, title)
    lines: List[str] = ["", header, ""]
    if str(getattr(args, "archive_note", "") or "").strip():
        lines.append(str(getattr(args, "archive_note") or "").strip())
        lines.append("")
    lines.append(f"- 来源：`{str(getattr(args, 'source_run', '') or source_label).strip() or source_label}`")
    if int(getattr(args, "manual_pool_size", 0) or 0) > 0:
        lines.append(f"- 人工候选池规模：`{int(args.manual_pool_size)}`")
    if any(int(getattr(args, name, 0) or 0) > 0 for name in ("first_pass_accept", "first_pass_review", "first_pass_reject")):
        lines.append("- 初始模拟审核：")
        lines.append(
            f"  - 行级：`accept={int(getattr(args, 'first_pass_accept', 0) or 0)}`，"
            f"`review={int(getattr(args, 'first_pass_review', 0) or 0)}`，"
            f"`reject={int(getattr(args, 'first_pass_reject', 0) or 0)}`"
        )
    if any(int(getattr(args, name, 0) or 0) > 0 for name in ("core_accept", "core_review", "core_reject")):
        lines.append(
            f"  - 核心 pair：`accept={int(getattr(args, 'core_accept', 0) or 0)}`，"
            f"`review={int(getattr(args, 'core_review', 0) or 0)}`，"
            f"`reject={int(getattr(args, 'core_reject', 0) or 0)}`"
        )
    if any(int(getattr(args, name, 0) or 0) > 0 for name in ("second_pass_promoted", "second_pass_kept_review", "second_pass_rejected")):
        lines.append("- 二次复核：")
        lines.append(
            f"  - `promoted={int(getattr(args, 'second_pass_promoted', 0) or 0)}`"
            f" / `kept_review={int(getattr(args, 'second_pass_kept_review', 0) or 0)}`"
            f" / `rejected={int(getattr(args, 'second_pass_rejected', 0) or 0)}`"
        )
    lines.append(f"- 终审清单：`{_repo_rel(reviewed_final_path)}`")
    lines.append(f"- 正式集资产：`{_repo_rel(asset_out)}`")
    lines.append(f"- 正式集条数：`{int(summary.get('formal_item_count') or 0)}`")
    kept = summary.get("example_kept_pairs") or []
    if kept:
        lines.append("- 代表性保留：")
        for item in kept[:4]:
            lines.append(f"  - `{item}`")
    if removed_examples:
        lines.append("- 代表性删除：")
        for item in removed_examples[:4]:
            lines.append(f"  - `{item}`")
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    with doc_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    return header


def _is_single_char_candidate(item: Dict[str, Any]) -> bool:
    return str(item.get("type") or "").strip() in {"single_char", "single_function_word"}


def _single_char_shortlist_allowed(item: Dict[str, Any], args: argparse.Namespace) -> bool:
    if not _is_single_char_candidate(item):
        return True
    cand_type = str(item.get("type") or "").strip()
    if cand_type == "single_function_word" and bool(getattr(args, "allow_single_function_word", False)):
        return (
            int(item.get("consensus_clip_count") or 0) >= int(getattr(args, "single_char_min_consensus", 2) or 2)
            and int(item.get("teacher_vote_total") or 0) >= int(getattr(args, "single_char_min_teacher_votes", 4) or 4)
        )
    if not bool(getattr(args, "allow_single_char", False)):
        return False
    return (
        int(item.get("consensus_clip_count") or 0) >= int(getattr(args, "single_char_min_consensus", 2) or 2)
        and int(item.get("source_video_count") or 0) >= int(getattr(args, "single_char_min_source_video_count", 2) or 2)
        and int(item.get("teacher_vote_total") or 0) >= int(getattr(args, "single_char_min_teacher_votes", 4) or 4)
        and (
            bool(item.get("same_pinyin_hit"))
            or bool(item.get("same_stroke_hit"))
            or bool(item.get("lexicon_hit"))
            or bool(item.get("proper_noun_hit"))
        )
    )


def _build_legacy_shortlist(items: List[Dict[str, Any]], args: argparse.Namespace) -> List[Dict[str, Any]]:
    shortlisted = [
        item
        for item in items
        if int(item.get("count_total") or 0) >= int(args.min_count_total)
        and int(item.get("source_video_count") or 0) >= int(args.min_source_video_count)
        and int(item.get("edit_distance") or 99) <= int(args.max_edit_distance)
        and _single_char_shortlist_allowed(item, args)
    ]
    shortlisted.sort(
        key=lambda x: (
            _candidate_evidence_score(x),
            int(x.get("source_video_count") or 0),
            len(x.get("source_platforms") or []),
            int(x.get("count_total") or 0),
        ),
        reverse=True,
    )
    if int(args.max_shortlist) > 0:
        shortlisted = shortlisted[: int(args.max_shortlist)]
    return shortlisted


def _compact_pattern_text(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(text or ""))


def _transcript_pair_occurrences(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    pairs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    consensus = row.get("consensus_candidate") if isinstance(row.get("consensus_candidate"), dict) else None
    consensus_key = None
    if consensus:
        consensus_key = (str(consensus.get("wrong") or ""), str(consensus.get("candidate") or ""))
    teacher_specs = [
        ("teacher_a", row.get("teacher_a_candidate"), str(row.get("teacher_a_zh") or "")),
        ("teacher_b", row.get("teacher_b_candidate"), str(row.get("teacher_b_zh") or "")),
    ]
    for label, cand, teacher_text in teacher_specs:
        if not isinstance(cand, dict):
            continue
        wrong = str(cand.get("wrong") or "").strip()
        candidate = str(cand.get("candidate") or "").strip()
        if not wrong or not candidate:
            continue
        key = (wrong, candidate)
        slot = pairs.setdefault(
            key,
            {
                "candidate": cand,
                "teacher_labels": [],
                "teacher_texts": [],
                "consensus": bool(consensus_key == key),
            },
        )
        slot["teacher_labels"].append(label)
        if teacher_text:
            slot["teacher_texts"].append(teacher_text)
    out: List[Dict[str, Any]] = []
    for (wrong, candidate), info in pairs.items():
        out.append(
            {
                "wrong": wrong,
                "candidate": candidate,
                "teacher_labels": list(info["teacher_labels"]),
                "teacher_texts": list(info["teacher_texts"]),
                "consensus": bool(info["consensus"]),
            }
        )
    return out


def _extract_phrase_pattern(student_text: str, teacher_text: str, wrong: str, candidate: str, *, context_chars: int = 2) -> Optional[Dict[str, Any]]:
    student = _compact_pattern_text(student_text)
    teacher = _compact_pattern_text(teacher_text)
    wrong_norm = _strip_zh_punct(wrong)
    candidate_norm = _strip_zh_punct(candidate)
    if not student or not teacher or not wrong_norm or not candidate_norm:
        return None
    matches: List[Dict[str, Any]] = []
    opcodes = SequenceMatcher(None, student, teacher).get_opcodes()
    for idx, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag != "replace":
            continue
        src = _strip_zh_punct(student[i1:i2])
        tgt = _strip_zh_punct(teacher[j1:j2])
        if src != wrong_norm or tgt != candidate_norm:
            continue
        left = ""
        right = ""
        if idx > 0 and opcodes[idx - 1][0] == "equal":
            pi1, pi2 = opcodes[idx - 1][1], opcodes[idx - 1][2]
            left = student[pi1:pi2][-context_chars:]
        if idx + 1 < len(opcodes) and opcodes[idx + 1][0] == "equal":
            ni1, ni2 = opcodes[idx + 1][1], opcodes[idx + 1][2]
            right = student[ni1:ni2][:context_chars]
        matches.append(
            {
                "left_context": left,
                "right_context": right,
                "pattern_wrong": left + wrong_norm + right,
                "pattern_candidate": left + candidate_norm + right,
                "context_chars": len(left) + len(right),
                "alignment": "replace",
            }
        )
    if not matches and candidate_norm in teacher:
        for m in re.finditer(re.escape(wrong_norm), student):
            pos = m.start()
            left = student[max(0, pos - context_chars) : pos]
            right = student[pos + len(wrong_norm) : pos + len(wrong_norm) + context_chars]
            matches.append(
                {
                    "left_context": left,
                    "right_context": right,
                    "pattern_wrong": left + wrong_norm + right,
                    "pattern_candidate": left + candidate_norm + right,
                    "context_chars": len(left) + len(right),
                    "alignment": "fallback",
                }
            )
    if not matches:
        return None
    matches.sort(
        key=lambda x: (
            int(x.get("alignment") == "replace"),
            int(x.get("context_chars") or 0),
            len(str(x.get("pattern_wrong") or "")),
        ),
        reverse=True,
    )
    return matches[0]


def _pattern_variants(pattern: Dict[str, Any], wrong: str, candidate: str) -> List[Dict[str, Any]]:
    wrong_norm = _strip_zh_punct(wrong)
    candidate_norm = _strip_zh_punct(candidate)
    left = str(pattern.get("left_context") or "")
    right = str(pattern.get("right_context") or "")
    variants: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str, str]] = set()

    def add_variant(scope: str, pattern_wrong: str, pattern_candidate: str, context_len: int) -> None:
        key = (scope, pattern_wrong, pattern_candidate)
        if key in seen or not pattern_wrong or not pattern_candidate or pattern_wrong == pattern_candidate:
            return
        seen.add(key)
        variants.append(
            {
                "scope": scope,
                "pattern_wrong": pattern_wrong,
                "pattern_candidate": pattern_candidate,
                "context_chars": context_len,
                "left_context": left,
                "right_context": right,
            }
        )

    if left:
        add_variant("left", left + wrong_norm, left + candidate_norm, len(left))
    if right:
        add_variant("right", wrong_norm + right, candidate_norm + right, len(right))
    if left and right:
        add_variant("both", left + wrong_norm + right, left + candidate_norm + right, len(left) + len(right))
    return variants


def _pattern_evidence_score(item: Dict[str, Any]) -> int:
    score = 0
    score += min(8, int(item.get("count_total") or 0))
    score += min(5, int(item.get("source_video_count") or 0))
    score += min(6, int(item.get("teacher_vote_total") or 0))
    score += min(4, int(item.get("consensus_clip_count") or 0))
    score += min(4, int(item.get("context_chars") or 0))
    score += int(bool(item.get("same_pinyin_hit")))
    score += int(bool(item.get("same_stroke_hit")))
    score += int(bool(item.get("lexicon_hit")))
    score += int(bool(item.get("proper_noun_hit")))
    score += 2 * int(bool(item.get("nonword_error_hit")))
    return score


def _nonword_error_hit(item: Dict[str, Any]) -> bool:
    if bool(item.get("proper_noun_hit")):
        return False
    return bool(item.get("lexicon_hit")) and not bool(item.get("wrong_lexicon_hit"))


def _manual_review_priority(item: Dict[str, Any]) -> int:
    pair_type = str(item.get("pair_type") or "").strip()
    if pair_type == "short_phrase":
        return 3
    if pair_type == "double_char" and _nonword_error_hit(item):
        return 2
    if pair_type == "double_char":
        return 1
    return 0


def _high_quality_double_char_for_review(item: Dict[str, Any]) -> bool:
    if str(item.get("pair_type") or "").strip() != "double_char":
        return True
    if _nonword_error_hit(item):
        return True
    if int(item.get("teacher_vote_total") or 0) < 2:
        return False
    if int(item.get("consensus_clip_count") or 0) < 1:
        return False
    evidence_hits = (
        int(bool(item.get("same_pinyin_hit")))
        + int(bool(item.get("same_stroke_hit")))
        + int(bool(item.get("lexicon_hit")))
        + int(bool(item.get("proper_noun_hit")))
    )
    if bool(item.get("lexicon_hit")):
        return True
    return evidence_hits >= 2


def _looks_like_proper_noun_watch(item: Dict[str, Any]) -> bool:
    pair_type = str(item.get("pair_type") or "").strip()
    wrong = str(item.get("wrong") or "")
    candidate = str(item.get("candidate") or "")
    if pair_type == "proper_noun" or bool(item.get("proper_noun_hit")):
        return True
    if (
        max(len(wrong), len(candidate)) <= 3
        and bool(item.get("same_pinyin_hit"))
        and bool(item.get("same_stroke_hit"))
        and not bool(item.get("lexicon_hit"))
        and int(item.get("count_total") or 0) >= 2
    ):
        return True
    return False


def _contextual_confusion_allowed(item: Dict[str, Any], args: argparse.Namespace) -> bool:
    pair_type = str(item.get("pair_type") or "").strip()
    if pair_type not in {"double_char", "short_phrase"}:
        return False
    if int(item.get("count_total") or 0) < int(getattr(args, "contextual_min_count_total", 2) or 2):
        return False
    if int(item.get("teacher_vote_total") or 0) < int(getattr(args, "contextual_min_teacher_votes", 2) or 2):
        return False
    if int(item.get("source_video_count") or 0) < int(getattr(args, "contextual_min_source_video_count", 2) or 2):
        return False
    if int(item.get("consensus_clip_count") or 0) < int(getattr(args, "contextual_min_consensus", 1) or 1):
        return False
    if int(item.get("context_chars") or 0) < int(getattr(args, "contextual_min_context_chars", 2) or 2):
        return False
    return True


def _manual_review_allowed(item: Dict[str, Any], args: argparse.Namespace) -> bool:
    pair_type = str(item.get("pair_type") or "").strip()
    if pair_type in {"single_function_word", "function_word"}:
        return False
    if pair_type == "single_char":
        return False
    if int(item.get("teacher_vote_total") or 0) < int(getattr(args, "review_min_teacher_votes", 1) or 1):
        return False
    if int(item.get("context_chars") or 0) < int(getattr(args, "review_min_context_chars", 1) or 1):
        return False
    pattern_wrong = str(item.get("pattern_wrong") or "")
    if len(pattern_wrong) < int(getattr(args, "review_min_pattern_len", 4) or 4):
        return False
    evidence_hits = (
        int(bool(item.get("same_pinyin_hit")))
        + int(bool(item.get("same_stroke_hit")))
        + int(bool(item.get("lexicon_hit")))
        + int(bool(item.get("proper_noun_hit")))
    )
    if pair_type == "single_char" and evidence_hits == 0:
        return False
    if not _high_quality_double_char_for_review(item):
        return False
    if (
        int(item.get("source_video_count") or 0) < int(getattr(args, "review_min_source_video_count", 1) or 1)
        and int(item.get("consensus_clip_count") or 0) < int(getattr(args, "review_min_consensus", 1) or 1)
        and not bool(item.get("proper_noun_hit"))
        and int(item.get("teacher_vote_total") or 0) < int(getattr(args, "review_high_teacher_votes", 3) or 3)
    ):
        return False
    return True


def _finalize_pattern_rows(grouped: Dict[Tuple[str, str], Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in grouped.values():
        rows.append(
            {
                "wrong": row["wrong"],
                "candidate": row["candidate"],
                "pattern_wrong": row["pattern_wrong"],
                "pattern_candidate": row["pattern_candidate"],
                "pattern_scope": row["pattern_scope"],
                "left_context": row["left_context"],
                "right_context": row["right_context"],
                "context_chars": int(row["context_chars"]),
                "pair_type": row["pair_type"],
                "edit_distance": int(row["edit_distance"]),
                "count_total": int(row["count_total"]),
                "clip_count": int(row["clip_count"]),
                "source_platforms": sorted(x for x in row["source_platforms"] if x),
                "source_video_count": len(row["source_videos"]),
                "source_ids": sorted(x for x in row["source_ids"] if x),
                "same_pinyin_hit": bool(row["same_pinyin_hit"]),
                "same_stroke_hit": bool(row["same_stroke_hit"]),
                "wrong_lexicon_hit": bool(row.get("wrong_lexicon_hit")),
                "lexicon_hit": bool(row["lexicon_hit"]),
                "proper_noun_hit": bool(row["proper_noun_hit"]),
                "nonword_error_hit": bool(row.get("nonword_error_hit")),
                "teacher_vote_total": int(row["teacher_vote_total"]),
                "teacher_a_hit_clips": int(row["teacher_a_hit_clips"]),
                "teacher_b_hit_clips": int(row["teacher_b_hit_clips"]),
                "consensus_clip_count": int(row["consensus_clip_count"]),
                "example_clips": list(row["example_clips"]),
            }
        )
    rows.sort(
        key=lambda x: (
            _pattern_evidence_score(x),
            int(x.get("source_video_count") or 0),
            int(x.get("count_total") or 0),
            len(str(x.get("pattern_wrong") or "")),
        ),
        reverse=True,
    )
    return rows


def _build_pattern_candidates(
    items: List[Dict[str, Any]],
    transcript_rows: List[Dict[str, Any]],
    *,
    context_chars: int,
    lexicon_words: set[str],
    proper_nouns: set[str],
) -> List[Dict[str, Any]]:
    pool_map = {
        (str(item.get("wrong") or ""), str(item.get("candidate") or "")): item
        for item in items
        if str(item.get("wrong") or "").strip() and str(item.get("candidate") or "").strip()
    }
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in transcript_rows:
        student_zh = str(row.get("student_zh") or "")
        if not student_zh:
            continue
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        case_id = str(row.get("id") or "")
        for occ in _transcript_pair_occurrences(row):
            pair_key = (str(occ.get("wrong") or ""), str(occ.get("candidate") or ""))
            pool_item = pool_map.get(pair_key)
            if not pool_item:
                continue
            patterns = []
            for teacher_text in occ.get("teacher_texts") or []:
                pattern = _extract_phrase_pattern(
                    student_zh,
                    str(teacher_text or ""),
                    pair_key[0],
                    pair_key[1],
                    context_chars=context_chars,
                )
                if pattern:
                    patterns.append(pattern)
            if not patterns:
                continue
            patterns.sort(
                key=lambda x: (
                    int(x.get("alignment") == "replace"),
                    int(x.get("context_chars") or 0),
                    len(str(x.get("pattern_wrong") or "")),
                ),
                reverse=True,
            )
            pattern = patterns[0]
            wrong_lexicon_hit = bool(pool_item.get("wrong_lexicon_hit")) if "wrong_lexicon_hit" in pool_item else pair_key[0] in lexicon_words
            lexicon_hit = bool(pool_item.get("lexicon_hit")) if "lexicon_hit" in pool_item else pair_key[1] in lexicon_words
            proper_noun_hit = bool(pool_item.get("proper_noun_hit")) if "proper_noun_hit" in pool_item else pair_key[1] in proper_nouns
            for variant in _pattern_variants(pattern, pair_key[0], pair_key[1]):
                key = (str(variant.get("pattern_wrong") or ""), str(variant.get("pattern_candidate") or ""))
                if not key[0] or not key[1] or key[0] == key[1]:
                    continue
                group = grouped.setdefault(
                    key,
                    {
                        "wrong": pair_key[0],
                        "candidate": pair_key[1],
                        "pattern_wrong": key[0],
                        "pattern_candidate": key[1],
                        "pattern_scope": str(variant.get("scope") or ""),
                        "left_context": str(variant.get("left_context") or pattern.get("left_context") or ""),
                        "right_context": str(variant.get("right_context") or pattern.get("right_context") or ""),
                        "context_chars": int(variant.get("context_chars") or 0),
                        "pair_type": str(pool_item.get("type") or ""),
                        "edit_distance": int(pool_item.get("edit_distance") or 0),
                        "count_total": 0,
                        "clip_count": 0,
                        "source_platforms": set(),
                        "source_videos": set(),
                        "source_ids": set(),
                        "clip_ids": set(),
                        "same_pinyin_hit": bool(pool_item.get("same_pinyin_hit")),
                        "same_stroke_hit": bool(pool_item.get("same_stroke_hit")),
                        "wrong_lexicon_hit": wrong_lexicon_hit,
                        "lexicon_hit": lexicon_hit,
                        "proper_noun_hit": proper_noun_hit,
                        "nonword_error_hit": lexicon_hit and not wrong_lexicon_hit and not proper_noun_hit,
                        "teacher_vote_total": 0,
                        "teacher_a_hit_clips": 0,
                        "teacher_b_hit_clips": 0,
                        "consensus_clip_count": 0,
                        "example_clips": [],
                    },
                )
                is_new_clip = case_id not in group["clip_ids"]
                if is_new_clip:
                    group["clip_ids"].add(case_id)
                    group["count_total"] += 1
                    group["clip_count"] += 1
                    group["source_platforms"].add(str(meta.get("platform_bucket") or meta.get("platform") or "other"))
                    group["source_videos"].add(str(meta.get("source_video") or meta.get("source_id") or case_id))
                    group["source_ids"].add(str(meta.get("source_id") or ""))
                    if len(group["example_clips"]) < 5:
                        group["example_clips"].append(
                            {
                                "id": case_id,
                                "student_zh": student_zh,
                                "teacher_zh": str((occ.get("teacher_texts") or [""])[0] or ""),
                                "pattern_wrong": key[0],
                                "pattern_candidate": key[1],
                                "pattern_scope": str(variant.get("scope") or ""),
                                "teacher_support_labels": list(occ.get("teacher_labels") or []),
                                "consensus": bool(occ.get("consensus")),
                                "source_id": meta.get("source_id"),
                                "platform": meta.get("platform"),
                                "category": meta.get("category"),
                            }
                        )
                labels = list(occ.get("teacher_labels") or [])
                group["teacher_vote_total"] += len(labels)
                if "teacher_a" in labels:
                    group["teacher_a_hit_clips"] += 1
                if "teacher_b" in labels:
                    group["teacher_b_hit_clips"] += 1
                if bool(occ.get("consensus")):
                    group["consensus_clip_count"] += 1
    return _finalize_pattern_rows(grouped)


def cmd_promote_patterns(args: argparse.Namespace) -> None:
    pool = json.loads(Path(args.candidate_pool).read_text(encoding="utf-8"))
    items = [x for x in (pool.get("items") or []) if isinstance(x, dict)]
    transcript_rows = _read_json_or_jsonl(Path(args.transcripts_jsonl))
    lexicon_words = _load_zh_word_set(str(args.lexicon_path), min_len=2, max_len=4, include_extras=True)
    proper_nouns = _load_zh_word_set(str(args.proper_nouns_path), min_len=2, max_len=8, include_extras=False)
    legacy_shortlist = _build_legacy_shortlist(items, args)
    pattern_rows = _build_pattern_candidates(
        items,
        transcript_rows,
        context_chars=int(getattr(args, "pattern_context_chars", 2) or 2),
        lexicon_words=lexicon_words,
        proper_nouns=proper_nouns,
    )
    phrase_confusions: List[Dict[str, Any]] = []
    contextual_confusions: List[Dict[str, Any]] = []
    manual_review_pool: List[Dict[str, Any]] = []
    proper_noun_watchlist: List[Dict[str, Any]] = []
    for item in pattern_rows:
        if _looks_like_proper_noun_watch(item):
            proper_noun_watchlist.append(item)
            continue
        if (
            int(item.get("count_total") or 0) >= int(getattr(args, "phrase_min_count_total", 2) or 2)
            and int(item.get("source_video_count") or 0) >= int(getattr(args, "phrase_min_source_video_count", 2) or 2)
            and int(item.get("teacher_vote_total") or 0) >= int(getattr(args, "phrase_min_teacher_votes", 4) or 4)
            and int(item.get("consensus_clip_count") or 0) >= int(getattr(args, "phrase_min_consensus", 1) or 1)
            and int(item.get("context_chars") or 0) >= int(getattr(args, "phrase_min_context_chars", 2) or 2)
        ):
            phrase_confusions.append(item)
            continue
        if _contextual_confusion_allowed(item, args):
            contextual_confusions.append(item)
            continue
        if _manual_review_allowed(item, args):
            manual_review_pool.append(item)
    manual_review_pool.sort(
        key=lambda x: (
            _manual_review_priority(x),
            _pattern_evidence_score(x),
            int(x.get("teacher_vote_total") or 0),
            int(x.get("consensus_clip_count") or 0),
            int(x.get("count_total") or 0),
            len(str(x.get("pattern_wrong") or "")),
        ),
        reverse=True,
    )
    phrase_confusions = phrase_confusions[: int(getattr(args, "phrase_max_items", 120) or 120)]
    contextual_confusions = contextual_confusions[: int(getattr(args, "contextual_max_items", 120) or 120)]
    manual_review_pool = manual_review_pool[: int(getattr(args, "review_max_items", 2000) or 2000)]
    proper_noun_watchlist = proper_noun_watchlist[: int(getattr(args, "proper_noun_max_items", 120) or 120)]
    out_dir = Path(args.out_dir)
    pattern_review = []
    for item in phrase_confusions + contextual_confusions + manual_review_pool:
        pattern_review.append(
            {
                **item,
                "review_decision": "",
                "final_candidate": str(item.get("pattern_candidate") or ""),
                "review_notes": "",
                "asset_type": (
                    "phrase_confusions"
                    if item in phrase_confusions
                    else ("contextual_confusions" if item in contextual_confusions else "manual_review_pool")
                ),
            }
        )
    _write_json(
        out_dir / "pattern_candidates.suggested.json",
        {
            "task": "asr_project_confusion_pattern_suggestions",
            "legacy_shortlist_count": len(legacy_shortlist),
            "phrase_confusions_count": len(phrase_confusions),
            "contextual_confusions_count": len(contextual_confusions),
            "manual_review_pool_count": len(manual_review_pool),
            "proper_noun_watchlist_count": len(proper_noun_watchlist),
            "thresholds": {
                "legacy_min_count_total": int(args.min_count_total),
                "legacy_min_source_video_count": int(args.min_source_video_count),
                "legacy_max_edit_distance": int(args.max_edit_distance),
                "phrase_min_count_total": int(getattr(args, "phrase_min_count_total", 2) or 2),
                "phrase_min_source_video_count": int(getattr(args, "phrase_min_source_video_count", 2) or 2),
                "phrase_min_teacher_votes": int(getattr(args, "phrase_min_teacher_votes", 4) or 4),
                "phrase_min_consensus": int(getattr(args, "phrase_min_consensus", 1) or 1),
                "phrase_min_context_chars": int(getattr(args, "phrase_min_context_chars", 2) or 2),
                "contextual_min_count_total": int(getattr(args, "contextual_min_count_total", 2) or 2),
                "contextual_min_teacher_votes": int(getattr(args, "contextual_min_teacher_votes", 2) or 2),
                "contextual_min_source_video_count": int(getattr(args, "contextual_min_source_video_count", 2) or 2),
                "contextual_min_consensus": int(getattr(args, "contextual_min_consensus", 1) or 1),
                "contextual_min_context_chars": int(getattr(args, "contextual_min_context_chars", 2) or 2),
                "review_min_teacher_votes": int(getattr(args, "review_min_teacher_votes", 2) or 2),
                "review_min_context_chars": int(getattr(args, "review_min_context_chars", 2) or 2),
                "review_min_pattern_len": int(getattr(args, "review_min_pattern_len", 4) or 4),
                "review_min_source_video_count": int(getattr(args, "review_min_source_video_count", 1) or 1),
                "review_min_consensus": int(getattr(args, "review_min_consensus", 1) or 1),
                "review_high_teacher_votes": int(getattr(args, "review_high_teacher_votes", 3) or 3),
                "pattern_context_chars": int(getattr(args, "pattern_context_chars", 2) or 2),
            },
            "legacy_shortlist": legacy_shortlist,
            "phrase_confusions": phrase_confusions,
            "contextual_confusions": contextual_confusions,
            "manual_review_pool": manual_review_pool,
            "proper_noun_watchlist": proper_noun_watchlist,
        },
    )
    _write_jsonl(out_dir / "pattern_candidates.review.jsonl", pattern_review)
    _write_json(
        out_dir / "asr_project_phrase_confusions.generated.json",
        {
            "version": 2,
            "notes": "Phrase-level ASR confusion patterns only. Safe-by-default shortlist; no global bare-word replacements.",
            "items": phrase_confusions,
        },
    )
    _write_jsonl(out_dir / "asr_project_contextual_confusions.review.jsonl", contextual_confusions)
    _write_jsonl(out_dir / "asr_project_manual_review_pool.review.jsonl", manual_review_pool)
    _write_json(
        out_dir / "asr_project_proper_noun_watchlist.json",
        {
            "version": 1,
            "notes": "Observed proper-noun-like or brand-like confusions. Review-only; never apply as global replacements.",
            "items": proper_noun_watchlist,
        },
    )
    _write_json(
        out_dir / "pattern_compare.summary.json",
        {
            "task": "asr_project_confusion_pattern_compare",
            "legacy_shortlist_count": len(legacy_shortlist),
            "phrase_confusions_count": len(phrase_confusions),
            "contextual_confusions_count": len(contextual_confusions),
            "manual_review_pool_count": len(manual_review_pool),
            "proper_noun_watchlist_count": len(proper_noun_watchlist),
            "candidate_pool_count": len(items),
            "transcript_count": len(transcript_rows),
            "paths": {
                "legacy_shortlist": str(out_dir / "formal_candidates.suggested.json"),
                "pattern_suggestions": str(out_dir / "pattern_candidates.suggested.json"),
                "phrase_confusions": str(out_dir / "asr_project_phrase_confusions.generated.json"),
                "contextual_confusions_review": str(out_dir / "asr_project_contextual_confusions.review.jsonl"),
                "manual_review_pool": str(out_dir / "asr_project_manual_review_pool.review.jsonl"),
                "proper_noun_watchlist": str(out_dir / "asr_project_proper_noun_watchlist.json"),
            },
        },
    )
    _write_json(
        out_dir / "formal_candidates.suggested.json",
        {
            "task": "asr_project_confusion_formal_suggestions",
            "candidate_count": len(legacy_shortlist),
            "thresholds": {
                "min_count_total": int(args.min_count_total),
                "min_source_video_count": int(args.min_source_video_count),
                "max_edit_distance": int(args.max_edit_distance),
                "max_shortlist": int(args.max_shortlist),
                "allow_single_char": bool(getattr(args, "allow_single_char", False)),
                "allow_single_function_word": bool(getattr(args, "allow_single_function_word", False)),
                "single_char_min_consensus": int(getattr(args, "single_char_min_consensus", 2) or 2),
                "single_char_min_source_video_count": int(getattr(args, "single_char_min_source_video_count", 2) or 2),
                "single_char_min_teacher_votes": int(getattr(args, "single_char_min_teacher_votes", 4) or 4),
            },
            "items": legacy_shortlist,
        },
    )
    legacy_review = []
    for item in legacy_shortlist:
        legacy_review.append(
            {
                **item,
                "review_decision": "",
                "final_candidate": str(item.get("candidate") or ""),
                "review_notes": "",
                "auto_recommend": "accept" if len(item.get("source_platforms") or []) >= 2 else "review",
            }
        )
    _write_jsonl(out_dir / "formal_candidates.review.jsonl", legacy_review)
    print(
        f"[ok] pattern promote wrote phrase={len(phrase_confusions)}, "
        f"contextual={len(contextual_confusions)}, manual_review={len(manual_review_pool)}, "
        f"proper_noun_watch={len(proper_noun_watchlist)}, "
        f"legacy={len(legacy_shortlist)}"
    )


def cmd_promote(args: argparse.Namespace) -> None:
    pool = json.loads(Path(args.candidate_pool).read_text(encoding="utf-8"))
    items = [x for x in (pool.get("items") or []) if isinstance(x, dict)]
    shortlisted = _build_legacy_shortlist(items, args)
    out_dir = Path(args.out_dir)
    review_seed = []
    for item in shortlisted:
        review_seed.append(
            {
                **item,
                "review_decision": "",
                "final_candidate": str(item.get("candidate") or ""),
                "review_notes": "",
                "auto_recommend": "accept" if len(item.get("source_platforms") or []) >= 2 else "review",
            }
        )
    _write_json(
        out_dir / "formal_candidates.suggested.json",
        {
            "task": "asr_project_confusion_formal_suggestions",
            "candidate_count": len(shortlisted),
            "thresholds": {
                "min_count_total": int(args.min_count_total),
                "min_source_video_count": int(args.min_source_video_count),
                "max_edit_distance": int(args.max_edit_distance),
                "max_shortlist": int(args.max_shortlist),
                "allow_single_char": bool(getattr(args, "allow_single_char", False)),
                "allow_single_function_word": bool(getattr(args, "allow_single_function_word", False)),
                "single_char_min_consensus": int(getattr(args, "single_char_min_consensus", 2) or 2),
                "single_char_min_source_video_count": int(getattr(args, "single_char_min_source_video_count", 2) or 2),
                "single_char_min_teacher_votes": int(getattr(args, "single_char_min_teacher_votes", 4) or 4),
            },
            "items": shortlisted,
        },
    )
    _write_jsonl(out_dir / "formal_candidates.review.jsonl", review_seed)

    if not args.review_jsonl:
        print(f"[ok] wrote suggestion shortlist ({len(shortlisted)}) to {out_dir}")
        return

    reviewed = _read_review_jsonl(Path(args.review_jsonl))
    accepted_rows: List[Dict[str, Any]] = []
    for item in shortlisted:
        review = reviewed.get((str(item.get("wrong") or ""), str(item.get("candidate") or "")))
        if not review:
            continue
        decision = str(review.get("review_decision") or "").strip().lower()
        if decision not in {"accept", "accepted", "keep"}:
            continue
        final_candidate = str(review.get("final_candidate") or item.get("candidate") or "").strip()
        if not final_candidate:
            continue
        accepted_rows.append({**item, "final_candidate": final_candidate, "review_notes": str(review.get("review_notes") or "").strip()})

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in accepted_rows:
        wrong = str(row.get("wrong") or "").strip()
        if not wrong:
            continue
        dst = grouped.setdefault(
            wrong,
            {
                "wrong": wrong,
                "candidates": [],
                "type": str(row.get("type") or "").strip(),
                "evidence_count": 0,
                "sources": set(),
                "requires_high_risk": True,
                "max_edit_distance": int(row.get("edit_distance") or 2),
                "notes": [],
            },
        )
        cand = str(row.get("final_candidate") or "").strip()
        if cand and cand not in dst["candidates"]:
            dst["candidates"].append(cand)
        dst["evidence_count"] = max(int(dst["evidence_count"]), int(row.get("count_total") or 0))
        for src in row.get("source_platforms") or []:
            if src:
                dst["sources"].add(str(src))
        if str(row.get("review_notes") or "").strip():
            dst["notes"].append(str(row.get("review_notes") or "").strip())

    asset_items = []
    for row in grouped.values():
        asset_items.append(
            {
                "wrong": row["wrong"],
                "candidates": row["candidates"],
                "type": row["type"],
                "evidence_count": int(row["evidence_count"]),
                "sources": sorted(row["sources"]),
                "requires_high_risk": True,
                "max_edit_distance": int(row["max_edit_distance"]),
                "notes": " | ".join(row["notes"][:3]).strip(),
            }
        )
    asset_items.sort(key=lambda x: (int(x.get("evidence_count") or 0), len(x.get("candidates") or []), x.get("wrong") or ""), reverse=True)
    _write_json(
        out_dir / "asr_project_confusions.generated.json",
        {
            "version": 1,
            "notes": "Reviewed ASR project confusions generated from the mining pipeline. Only accepted repeated patterns are included.",
            "items": asset_items,
        },
    )
    print(f"[ok] promoted {len(asset_items)} reviewed items")


def cmd_publish_reviewed_final(args: argparse.Namespace) -> None:
    reviewed_final_path = Path(args.reviewed_final_json)
    rows = _read_json_or_jsonl(reviewed_final_path)
    asset_out = Path(args.asset_out)
    out_dir = Path(args.out_dir)
    source_label = str(getattr(args, "source_label", "") or "").strip() or reviewed_final_path.parent.name
    asset, summary = _build_asset_from_final_reviewed_rows(
        rows,
        source_label=source_label,
        max_edit_distance=int(getattr(args, "max_edit_distance", 2) or 2),
        evidence_floor=int(getattr(args, "evidence_floor", 2) or 2),
    )
    _write_json(asset_out, asset)
    removed_examples = _read_removed_examples(getattr(args, "removed_examples_json", None))
    publish_summary = {
        "task": "asr_project_confusion_publish_reviewed_final",
        "reviewed_final_json": _repo_rel(reviewed_final_path),
        "asset_out": _repo_rel(asset_out),
        "source_label": source_label,
        "archive_doc": _repo_rel(Path(args.archive_doc)) if getattr(args, "archive_doc", None) else "",
        "removed_example_count": len(removed_examples),
        "stats": {
            "manual_pool_size": int(getattr(args, "manual_pool_size", 0) or 0),
            "first_pass_accept": int(getattr(args, "first_pass_accept", 0) or 0),
            "first_pass_review": int(getattr(args, "first_pass_review", 0) or 0),
            "first_pass_reject": int(getattr(args, "first_pass_reject", 0) or 0),
            "core_accept": int(getattr(args, "core_accept", 0) or 0),
            "core_review": int(getattr(args, "core_review", 0) or 0),
            "core_reject": int(getattr(args, "core_reject", 0) or 0),
            "second_pass_promoted": int(getattr(args, "second_pass_promoted", 0) or 0),
            "second_pass_kept_review": int(getattr(args, "second_pass_kept_review", 0) or 0),
            "second_pass_rejected": int(getattr(args, "second_pass_rejected", 0) or 0),
        },
        "summary": summary,
        "removed_examples": removed_examples[:12],
    }
    _write_json(out_dir / "publish_formal_asset.summary.json", publish_summary)
    _write_json(
        out_dir / "asr_project_confusions.generated.json",
        asset,
    )
    md = _render_publish_summary_md(
        reviewed_final_path=reviewed_final_path,
        asset_out=asset_out,
        source_label=source_label,
        summary=summary,
        args=args,
        removed_examples=removed_examples,
    )
    (out_dir / "publish_formal_asset.summary.md").parent.mkdir(parents=True, exist_ok=True)
    (out_dir / "publish_formal_asset.summary.md").write_text(md, encoding="utf-8")
    archive_header = ""
    if getattr(args, "archive_doc", None):
        archive_header = _append_archive_section(
            doc_path=Path(args.archive_doc),
            reviewed_final_path=reviewed_final_path,
            asset_out=asset_out,
            source_label=source_label,
            summary=summary,
            args=args,
            removed_examples=removed_examples,
        )
    print(
        f"[ok] published reviewed final -> asset={asset_out} "
        f"items={int(summary.get('formal_item_count') or 0)}"
    )
    if archive_header:
        print(f"[ok] appended archive section {archive_header} to {Path(args.archive_doc)}")


def cmd_auto_url_pipeline(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir)
    try:
        work_dir.resolve().relative_to((repo_root / "outputs").resolve())
    except Exception as exc:
        raise SystemExit("auto-url-pipeline requires --work-dir under repo outputs/ so container-visible runtime files are preserved") from exc

    status_path = work_dir / "auto_pipeline_status.json"
    events_path = work_dir / "auto_pipeline_events.jsonl"

    def write_status(stage: str, **extra: Any) -> None:
        payload = {
            "task": "asr_project_confusion_auto_url_pipeline",
            "stage": stage,
            "updated_at": _now_iso(),
            "source_manifest": str(args.source_manifest),
            "work_dir": str(work_dir),
        }
        payload.update(extra)
        _write_json(status_path, payload)

    def log_event(event: str, **extra: Any) -> None:
        payload = {"ts": _now_iso(), "event": event, "work_dir": str(work_dir)}
        payload.update(extra)
        _append_jsonl(events_path, payload)

    prepared_dir = work_dir / "prepared"
    plan_dir = work_dir / "plan"
    run_dir = work_dir / "run"
    promote_dir = work_dir / "promote"

    write_status("prepare_sources_started")
    log_event("prepare_sources_started", source_manifest=str(args.source_manifest))
    source_rows = _read_json_or_jsonl(Path(args.source_manifest))
    min_source_duration_s = max(args.clip_len_s, args.long_clip_len_s) + 5.0
    valid_rows, rejected_rows = _prepare_source_manifest(
        source_rows,
        cookiefile=str(args.download_cookiefile or ""),
        min_duration_s=float(min_source_duration_s),
        max_duration_s=float(args.max_source_duration_s or 0.0),
        source_profile=str(getattr(args, "source_profile", "speech_focused") or "speech_focused"),
        include_category_keywords=list(getattr(args, "include_category_keywords", None) or []),
        exclude_category_keywords=list(getattr(args, "exclude_category_keywords", None) or []),
        exclude_title_keywords=list(getattr(args, "exclude_title_keywords", None) or []),
    )
    _write_jsonl(prepared_dir / "manifest.valid.jsonl", valid_rows)
    _write_json(prepared_dir / "manifest.rejected.json", rejected_rows)
    _write_json(
        prepared_dir / "manifest.summary.json",
        {
            "input_count": len(source_rows),
            "valid_count": len(valid_rows),
            "rejected_count": len(rejected_rows),
            "min_source_duration_s": float(min_source_duration_s),
            "max_source_duration_s": float(args.max_source_duration_s or 0.0),
        },
    )
    if not valid_rows:
        log_event("prepare_sources_failed", reason="no_valid_sources")
        write_status("failed", reason="no_valid_sources")
        raise SystemExit("No valid sources remain after unattended source preparation.")

    write_status("plan_sampling_started", valid_count=len(valid_rows))
    log_event("prepare_sources_completed", valid_count=len(valid_rows), rejected_count=len(rejected_rows))
    log_event("plan_sampling_started", valid_count=len(valid_rows))
    plan_args = argparse.Namespace(
        source_manifest=prepared_dir / "manifest.valid.jsonl",
        out_dir=plan_dir,
        target_sources=int(args.target_sources),
        total_clips=int(args.total_clips),
        clip_len_s=int(args.clip_len_s),
        long_clip_len_s=int(args.long_clip_len_s),
        long_clip_ratio=float(args.long_clip_ratio),
        min_gap_s=float(args.min_gap_s),
        max_source_duration_s=float(args.max_source_duration_s),
        ratio_douyin=float(args.ratio_douyin),
        ratio_bilibili=float(args.ratio_bilibili),
        ratio_other=float(args.ratio_other),
        download_cookiefile=str(args.download_cookiefile or ""),
        source_profile=str(getattr(args, "source_profile", "speech_focused") or "speech_focused"),
        include_category_keywords=list(getattr(args, "include_category_keywords", None) or []),
        exclude_category_keywords=list(getattr(args, "exclude_category_keywords", None) or []),
        exclude_title_keywords=list(getattr(args, "exclude_title_keywords", None) or []),
        case_prefix=str(args.case_prefix),
        seed=int(args.seed),
    )
    cmd_plan_sampling(plan_args)
    log_event("plan_sampling_completed", clip_plan=str(plan_dir / "clip_plan.json"))

    write_status("download_batch_runner_started", clip_plan=str(plan_dir / "clip_plan.json"))
    log_event("download_batch_runner_started", clip_plan=str(plan_dir / "clip_plan.json"))
    run_args = _copy_namespace(args)
    setattr(run_args, "clip_plan", plan_dir / "clip_plan.json")
    setattr(run_args, "out_dir", run_dir)
    setattr(run_args, "skip_source_errors", True)
    setattr(run_args, "skip_case_errors", True)
    cmd_download_batch_runner(run_args)
    log_event("download_batch_runner_completed", candidate_pool=str(run_dir / "candidate_pool.json"))

    write_status("promote_started", candidate_pool=str(run_dir / "candidate_pool.json"))
    log_event("promote_started", candidate_pool=str(run_dir / "candidate_pool.json"))
    promote_args = argparse.Namespace(
        candidate_pool=run_dir / "candidate_pool.json",
        out_dir=promote_dir,
        review_jsonl=None,
        min_count_total=int(args.promote_min_count_total),
        min_source_video_count=int(args.promote_min_source_video_count),
        max_edit_distance=int(args.promote_max_edit_distance),
        max_shortlist=int(args.promote_max_shortlist),
        allow_single_char=bool(getattr(args, "promote_allow_single_char", False)),
        allow_single_function_word=bool(getattr(args, "promote_allow_single_function_word", False)),
        single_char_min_consensus=int(getattr(args, "promote_single_char_min_consensus", 2) or 2),
        single_char_min_source_video_count=int(getattr(args, "promote_single_char_min_source_video_count", 2) or 2),
        single_char_min_teacher_votes=int(getattr(args, "promote_single_char_min_teacher_votes", 4) or 4),
        lexicon_path=Path(getattr(args, "lexicon_path", Path("assets/zh_phrase/chinese_xinhua_ci_2to4.txt"))),
        proper_nouns_path=Path(getattr(args, "proper_nouns_path", Path("assets/zh_phrase/thuocl_proper_nouns.txt"))),
    )
    setattr(promote_args, "transcripts_jsonl", run_dir / "transcripts.jsonl")
    cmd_promote_patterns(promote_args)
    log_event("promote_completed", suggestions=str(promote_dir / "pattern_candidates.suggested.json"))

    candidate_pool = json.loads((run_dir / "candidate_pool.json").read_text(encoding="utf-8"))
    suggestions = json.loads((promote_dir / "pattern_candidates.suggested.json").read_text(encoding="utf-8"))
    summary = {
        "task": "asr_project_confusion_auto_url_pipeline",
        "source_manifest": str(args.source_manifest),
        "work_dir": str(work_dir),
        "prepared_valid_sources": len(valid_rows),
        "prepared_rejected_sources": len(rejected_rows),
        "planned_clips": int(((json.loads((plan_dir / "clip_plan.json").read_text(encoding="utf-8"))).get("summary") or {}).get("planned_clips") or 0),
        "run_candidate_pairs": int(candidate_pool.get("candidate_count") or 0),
        "shortlist_candidates": int(suggestions.get("phrase_confusions_count") or 0),
        "legacy_shortlist_candidates": int(suggestions.get("legacy_shortlist_count") or 0),
        "paths": {
            "prepared_manifest": str(prepared_dir / "manifest.valid.jsonl"),
            "rejected_manifest": str(prepared_dir / "manifest.rejected.json"),
            "clip_plan": str(plan_dir / "clip_plan.json"),
            "candidate_pool": str(run_dir / "candidate_pool.json"),
            "review_seed": str(promote_dir / "pattern_candidates.review.jsonl"),
            "suggestions": str(promote_dir / "pattern_candidates.suggested.json"),
            "phrase_confusions": str(promote_dir / "asr_project_phrase_confusions.generated.json"),
            "contextual_confusions_review": str(promote_dir / "asr_project_contextual_confusions.review.jsonl"),
            "proper_noun_watchlist": str(promote_dir / "asr_project_proper_noun_watchlist.json"),
        },
    }
    _write_json(work_dir / "auto_pipeline_summary.json", summary)
    write_status("completed", **summary)
    log_event("completed", **summary)
    print(
        f"[ok] auto pipeline finished: sources={len(valid_rows)}, "
        f"candidates={int(candidate_pool.get('candidate_count') or 0)}, "
        f"phrase_confusions={int(suggestions.get('phrase_confusions_count') or 0)}, "
        f"legacy_shortlist={int(suggestions.get('legacy_shortlist_count') or 0)}"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Mine repeated ASR confusion candidates from multi-source short-video clips.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("plan-sampling", help="Build a 12-hour sampling and clip plan from source videos")
    p1.add_argument("--source-manifest", type=Path, required=True, help="JSON/JSONL with source videos and platform/category metadata")
    p1.add_argument("--out-dir", type=Path, required=True, help="Output directory for sampling plan artifacts")
    p1.add_argument("--target-sources", type=int, default=150)
    p1.add_argument("--total-clips", type=int, default=2400)
    p1.add_argument("--clip-len-s", type=int, default=15)
    p1.add_argument("--long-clip-len-s", type=int, default=20)
    p1.add_argument("--long-clip-ratio", type=float, default=0.1)
    p1.add_argument("--min-gap-s", type=float, default=4.0)
    p1.add_argument("--max-source-duration-s", type=float, default=300.0, help="Drop overly long source videos; 0 disables the limit")
    p1.add_argument("--source-profile", choices=["general", "speech_focused"], default="general", help="Optional source suitability filter profile")
    p1.add_argument("--include-category-keywords", nargs="*", default=[], help="Only keep sources whose category/title hits one of these keywords")
    p1.add_argument("--exclude-category-keywords", nargs="*", default=[], help="Reject sources whose category hits one of these keywords")
    p1.add_argument("--exclude-title-keywords", nargs="*", default=[], help="Reject sources whose title hits one of these keywords")
    p1.add_argument("--ratio-douyin", type=float, default=0.6)
    p1.add_argument("--ratio-bilibili", type=float, default=0.2)
    p1.add_argument("--ratio-other", type=float, default=0.2)
    p1.add_argument("--download-cookiefile", type=str, default="", help="Optional Netscape cookie file for remote URL metadata probing")
    p1.add_argument("--case-prefix", type=str, default="conf")
    p1.add_argument("--seed", type=int, default=42)
    p1.set_defaults(func=cmd_plan_sampling)

    p2 = sub.add_parser("slice-clips", help="Render clips from a clip plan")
    p2.add_argument("--clip-plan", type=Path, required=True, help="clip_plan.json generated by plan-sampling")
    p2.add_argument("--out-dir", type=Path, required=True, help="Output dataset directory")
    p2.set_defaults(func=cmd_slice_clips)

    p3 = sub.add_parser("mine", help="Run student/teacher transcripts and build a candidate pool")
    p3.add_argument("--cases-jsonl", type=Path, required=True)
    p3.add_argument("--out-dir", type=Path, required=True)
    p3.add_argument("--whisper-bin", type=Path, default=Path("bin/whisper-cli"))
    p3.add_argument("--whisper-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin"))
    p3.add_argument("--vad-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin"))
    p3.add_argument("--student-threads", type=int, default=4)
    p3.add_argument("--student-beam-size", type=int, default=5)
    p3.add_argument("--student-vad-enable", action="store_true")
    p3.add_argument("--vad-thold", type=float, default=0.5)
    p3.add_argument("--vad-min-sil-ms", type=int, default=180)
    p3.add_argument("--teacher-backend", choices=["faster_whisper", "sensevoice"], default="faster_whisper")
    p3.add_argument("--teacher-model-root", type=Path, default=Path("assets/models/quality_asr_whisperx"))
    p3.add_argument("--teacher-repo-id", type=str, default="mobiuslabsgmbh/faster-whisper-large-v3-turbo")
    p3.add_argument("--teacher-threads", type=int, default=4)
    p3.add_argument("--teacher-sensevoice-model", type=str, default="FunAudioLLM/SenseVoiceSmall")
    p3.add_argument("--teacher-sensevoice-model-dir", type=Path, default=Path("assets/models/common_cache_hf"))
    p3.add_argument("--teacher-b-backend", choices=["disabled", "faster_whisper", "sensevoice"], default="faster_whisper")
    p3.add_argument("--teacher-b-mode", choices=["always", "candidate_only"], default="always", help="Run teacher B on every clip, or only when teacher A already found a local candidate")
    p3.add_argument("--teacher-b-model-root", type=Path, default=Path("assets/models/quality_asr_whisperx"))
    p3.add_argument("--teacher-b-repo-id", type=str, default="Systran/faster-whisper-medium")
    p3.add_argument("--teacher-b-threads", type=int, default=4)
    p3.add_argument("--teacher-b-sensevoice-model", type=str, default="FunAudioLLM/SenseVoiceSmall")
    p3.add_argument("--teacher-b-sensevoice-model-dir", type=Path, default=Path("assets/models/common_cache_hf"))
    p3.add_argument("--sample-rate", type=int, default=16000)
    p3.add_argument("--asr-normalize-dict", type=Path, default=Path("assets/asr_normalize/asr_zh_dict.json"))
    p3.add_argument("--same-pinyin-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_pinyin.txt"))
    p3.add_argument("--same-stroke-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_stroke.txt"))
    p3.add_argument("--lexicon-path", type=Path, default=Path("assets/zh_phrase/chinese_xinhua_ci_2to4.txt"))
    p3.add_argument("--proper-nouns-path", type=Path, default=Path("assets/zh_phrase/thuocl_proper_nouns.txt"))
    p3.add_argument("--max-examples", type=int, default=5)
    p3.add_argument("--cleanup-audio", action="store_true", help="Delete per-clip extracted wav after each case")
    p3.add_argument("--cleanup-student-json", action="store_true", help="Delete per-clip whisper.cpp json/txt after each case")
    p3.add_argument("--delete-clip-after-mine", action="store_true", help="Delete input clip after mining it; use only on disposable sliced datasets")
    p3.add_argument("--skip-case-errors", action="store_true", help="Skip bad clips during mining instead of failing the whole batch")
    p3.set_defaults(func=cmd_mine)

    p4 = sub.add_parser("batch-runner", help="Slice, mine, aggregate, and delete temporary clips batch by batch")
    p4.add_argument("--clip-plan", type=Path, required=True, help="clip_plan.json generated by plan-sampling")
    p4.add_argument("--out-dir", type=Path, required=True, help="Output directory for aggregated artifacts")
    p4.add_argument("--batch-size", type=int, default=80, help="How many clips to process per batch")
    p4.add_argument("--whisper-bin", type=Path, default=Path("bin/whisper-cli"))
    p4.add_argument("--whisper-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin"))
    p4.add_argument("--vad-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin"))
    p4.add_argument("--student-threads", type=int, default=4)
    p4.add_argument("--student-beam-size", type=int, default=5)
    p4.add_argument("--student-vad-enable", action="store_true")
    p4.add_argument("--vad-thold", type=float, default=0.5)
    p4.add_argument("--vad-min-sil-ms", type=int, default=180)
    p4.add_argument("--teacher-backend", choices=["faster_whisper", "sensevoice"], default="faster_whisper")
    p4.add_argument("--teacher-model-root", type=Path, default=Path("assets/models/quality_asr_whisperx"))
    p4.add_argument("--teacher-repo-id", type=str, default="mobiuslabsgmbh/faster-whisper-large-v3-turbo")
    p4.add_argument("--teacher-threads", type=int, default=4)
    p4.add_argument("--teacher-sensevoice-model", type=str, default="FunAudioLLM/SenseVoiceSmall")
    p4.add_argument("--teacher-sensevoice-model-dir", type=Path, default=Path("assets/models/common_cache_hf"))
    p4.add_argument("--teacher-b-backend", choices=["disabled", "faster_whisper", "sensevoice"], default="faster_whisper")
    p4.add_argument("--teacher-b-mode", choices=["always", "candidate_only"], default="always")
    p4.add_argument("--teacher-b-model-root", type=Path, default=Path("assets/models/quality_asr_whisperx"))
    p4.add_argument("--teacher-b-repo-id", type=str, default="Systran/faster-whisper-medium")
    p4.add_argument("--teacher-b-threads", type=int, default=4)
    p4.add_argument("--teacher-b-sensevoice-model", type=str, default="FunAudioLLM/SenseVoiceSmall")
    p4.add_argument("--teacher-b-sensevoice-model-dir", type=Path, default=Path("assets/models/common_cache_hf"))
    p4.add_argument("--sample-rate", type=int, default=16000)
    p4.add_argument("--asr-normalize-dict", type=Path, default=Path("assets/asr_normalize/asr_zh_dict.json"))
    p4.add_argument("--same-pinyin-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_pinyin.txt"))
    p4.add_argument("--same-stroke-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_stroke.txt"))
    p4.add_argument("--lexicon-path", type=Path, default=Path("assets/zh_phrase/chinese_xinhua_ci_2to4.txt"))
    p4.add_argument("--proper-nouns-path", type=Path, default=Path("assets/zh_phrase/thuocl_proper_nouns.txt"))
    p4.add_argument("--max-examples", type=int, default=5)
    p4.add_argument("--cleanup-audio", action="store_true", help="Delete per-clip extracted wav after each case")
    p4.add_argument("--cleanup-student-json", action="store_true", help="Delete per-clip whisper.cpp json/txt after each case")
    p4.add_argument("--delete-batch-clips", action="store_true", help="Delete each batch's sliced clips after mining")
    p4.add_argument("--delete-batch-runs", action="store_true", help="Delete each batch's per-case run directory after aggregation")
    p4.add_argument("--skip-case-errors", action="store_true", help="Skip bad clips during mining instead of failing the whole batch")
    p4.set_defaults(func=cmd_batch_runner)

    p45 = sub.add_parser("download-batch-runner", help="For URL clip plans: download source videos batch by batch, mine in container, and delete temporary sources/clips")
    p45.add_argument("--clip-plan", type=Path, required=True, help="clip_plan.json generated by plan-sampling")
    p45.add_argument("--out-dir", type=Path, required=True, help="Output directory for aggregated artifacts")
    p45.add_argument("--batch-size", type=int, default=40, help="How many clips to process per batch")
    p45.add_argument("--download-max-height", type=int, default=540, help="Cap downloaded source video height to control storage")
    p45.add_argument("--download-cookiefile", type=str, default="", help="Optional Netscape cookie file for yt_dlp downloads")
    p45.add_argument("--whisper-bin", type=Path, default=Path("bin/whisper-cli"))
    p45.add_argument("--whisper-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin"))
    p45.add_argument("--vad-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin"))
    p45.add_argument("--student-threads", type=int, default=4)
    p45.add_argument("--student-beam-size", type=int, default=5)
    p45.add_argument("--student-vad-enable", action="store_true")
    p45.add_argument("--vad-thold", type=float, default=0.5)
    p45.add_argument("--vad-min-sil-ms", type=int, default=180)
    p45.add_argument("--teacher-backend", choices=["faster_whisper", "sensevoice"], default="faster_whisper")
    p45.add_argument("--teacher-model-root", type=Path, default=Path("assets/models/quality_asr_whisperx"))
    p45.add_argument("--teacher-repo-id", type=str, default="mobiuslabsgmbh/faster-whisper-large-v3-turbo")
    p45.add_argument("--teacher-threads", type=int, default=4)
    p45.add_argument("--teacher-sensevoice-model", type=str, default="FunAudioLLM/SenseVoiceSmall")
    p45.add_argument("--teacher-sensevoice-model-dir", type=Path, default=Path("assets/models/common_cache_hf"))
    p45.add_argument("--teacher-b-backend", choices=["disabled", "faster_whisper", "sensevoice"], default="faster_whisper")
    p45.add_argument("--teacher-b-mode", choices=["always", "candidate_only"], default="always")
    p45.add_argument("--teacher-b-model-root", type=Path, default=Path("assets/models/quality_asr_whisperx"))
    p45.add_argument("--teacher-b-repo-id", type=str, default="Systran/faster-whisper-medium")
    p45.add_argument("--teacher-b-threads", type=int, default=4)
    p45.add_argument("--teacher-b-sensevoice-model", type=str, default="FunAudioLLM/SenseVoiceSmall")
    p45.add_argument("--teacher-b-sensevoice-model-dir", type=Path, default=Path("assets/models/common_cache_hf"))
    p45.add_argument("--sample-rate", type=int, default=16000)
    p45.add_argument("--asr-normalize-dict", type=Path, default=Path("assets/asr_normalize/asr_zh_dict.json"))
    p45.add_argument("--same-pinyin-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_pinyin.txt"))
    p45.add_argument("--same-stroke-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_stroke.txt"))
    p45.add_argument("--lexicon-path", type=Path, default=Path("assets/zh_phrase/chinese_xinhua_ci_2to4.txt"))
    p45.add_argument("--proper-nouns-path", type=Path, default=Path("assets/zh_phrase/thuocl_proper_nouns.txt"))
    p45.add_argument("--max-examples", type=int, default=5)
    p45.add_argument("--cleanup-audio", action="store_true", help="Delete per-clip extracted wav after each case")
    p45.add_argument("--cleanup-student-json", action="store_true", help="Delete per-clip whisper.cpp json/txt after each case")
    p45.add_argument("--delete-batch-source-videos", action="store_true", help="Delete each batch's downloaded source videos after aggregation")
    p45.add_argument("--delete-batch-clips", action="store_true", help="Delete each batch's sliced clips after mining")
    p45.add_argument("--delete-batch-runs", action="store_true", help="Delete each batch's per-case run directory after aggregation")
    p45.add_argument("--skip-source-errors", action="store_true", help="Skip bad URLs or clip render failures instead of aborting the whole run")
    p45.add_argument("--skip-case-errors", action="store_true", help="Skip bad mined clips instead of aborting the whole run")
    p45.add_argument("--resume", action="store_true", help="Resume from completed batches using existing output artifacts")
    p45.set_defaults(func=cmd_download_batch_runner)

    p46 = sub.add_parser("auto-url-pipeline", help="Run unattended URL pipeline: prepare sources, plan clips, batch download+mine, and emit shortlist")
    p46.add_argument("--source-manifest", type=Path, required=True, help="Seed JSON/JSONL containing local paths or remote URLs")
    p46.add_argument("--work-dir", type=Path, required=True, help="Workspace output directory under outputs/")
    p46.add_argument("--target-sources", type=int, default=60)
    p46.add_argument("--total-clips", type=int, default=240)
    p46.add_argument("--clip-len-s", type=int, default=15)
    p46.add_argument("--long-clip-len-s", type=int, default=20)
    p46.add_argument("--long-clip-ratio", type=float, default=0.1)
    p46.add_argument("--min-gap-s", type=float, default=4.0)
    p46.add_argument("--max-source-duration-s", type=float, default=300.0, help="Keep unattended sources short; 0 disables the limit")
    p46.add_argument("--source-profile", choices=["general", "speech_focused"], default="speech_focused", help="Filter remote sources to speech-friendly categories/titles")
    p46.add_argument("--include-category-keywords", nargs="*", default=[], help="Only keep sources whose category/title hits one of these keywords")
    p46.add_argument("--exclude-category-keywords", nargs="*", default=[], help="Reject sources whose category hits one of these keywords")
    p46.add_argument("--exclude-title-keywords", nargs="*", default=[], help="Reject sources whose title hits one of these keywords")
    p46.add_argument("--ratio-douyin", type=float, default=0.0)
    p46.add_argument("--ratio-bilibili", type=float, default=1.0)
    p46.add_argument("--ratio-other", type=float, default=0.0)
    p46.add_argument("--download-cookiefile", type=str, default="", help="Optional Netscape cookie file for remote probing/downloading")
    p46.add_argument("--case-prefix", type=str, default="conf")
    p46.add_argument("--seed", type=int, default=42)
    p46.add_argument("--batch-size", type=int, default=4, help="How many clips to process per batch")
    p46.add_argument("--download-max-height", type=int, default=540, help="Cap downloaded source video height to control storage")
    p46.add_argument("--whisper-bin", type=Path, default=Path("bin/whisper-cli"))
    p46.add_argument("--whisper-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-small-q5_1.bin"))
    p46.add_argument("--vad-model", type=Path, default=Path("assets/models/lite_asr_whispercpp/ggml-silero-v6.2.0.bin"))
    p46.add_argument("--student-threads", type=int, default=4)
    p46.add_argument("--student-beam-size", type=int, default=5)
    p46.add_argument("--student-vad-enable", action="store_true")
    p46.add_argument("--vad-thold", type=float, default=0.5)
    p46.add_argument("--vad-min-sil-ms", type=int, default=180)
    p46.add_argument("--teacher-backend", choices=["faster_whisper", "sensevoice"], default="faster_whisper")
    p46.add_argument("--teacher-model-root", type=Path, default=Path("assets/models/quality_asr_whisperx"))
    p46.add_argument("--teacher-repo-id", type=str, default="mobiuslabsgmbh/faster-whisper-large-v3-turbo")
    p46.add_argument("--teacher-threads", type=int, default=4)
    p46.add_argument("--teacher-sensevoice-model", type=str, default="FunAudioLLM/SenseVoiceSmall")
    p46.add_argument("--teacher-sensevoice-model-dir", type=Path, default=Path("assets/models/common_cache_hf"))
    p46.add_argument("--teacher-b-backend", choices=["disabled", "faster_whisper", "sensevoice"], default="faster_whisper")
    p46.add_argument("--teacher-b-mode", choices=["always", "candidate_only"], default="always")
    p46.add_argument("--teacher-b-model-root", type=Path, default=Path("assets/models/quality_asr_whisperx"))
    p46.add_argument("--teacher-b-repo-id", type=str, default="Systran/faster-whisper-medium")
    p46.add_argument("--teacher-b-threads", type=int, default=4)
    p46.add_argument("--teacher-b-sensevoice-model", type=str, default="FunAudioLLM/SenseVoiceSmall")
    p46.add_argument("--teacher-b-sensevoice-model-dir", type=Path, default=Path("assets/models/common_cache_hf"))
    p46.add_argument("--sample-rate", type=int, default=16000)
    p46.add_argument("--asr-normalize-dict", type=Path, default=Path("assets/asr_normalize/asr_zh_dict.json"))
    p46.add_argument("--same-pinyin-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_pinyin.txt"))
    p46.add_argument("--same-stroke-path", type=Path, default=Path("assets/zh_phrase/pycorrector_same_stroke.txt"))
    p46.add_argument("--lexicon-path", type=Path, default=Path("assets/zh_phrase/chinese_xinhua_ci_2to4.txt"))
    p46.add_argument("--proper-nouns-path", type=Path, default=Path("assets/zh_phrase/thuocl_proper_nouns.txt"))
    p46.add_argument("--max-examples", type=int, default=5)
    p46.add_argument("--cleanup-audio", action="store_true")
    p46.add_argument("--cleanup-student-json", action="store_true")
    p46.add_argument("--delete-batch-source-videos", action="store_true")
    p46.add_argument("--delete-batch-clips", action="store_true")
    p46.add_argument("--delete-batch-runs", action="store_true")
    p46.add_argument("--skip-case-errors", action="store_true")
    p46.add_argument("--resume", action="store_true", help="Resume the run stage from an existing work directory")
    p46.add_argument("--promote-min-count-total", type=int, default=2)
    p46.add_argument("--promote-min-source-video-count", type=int, default=2)
    p46.add_argument("--promote-max-edit-distance", type=int, default=2)
    p46.add_argument("--promote-max-shortlist", type=int, default=120)
    p46.add_argument("--promote-allow-single-char", action="store_true")
    p46.add_argument("--promote-allow-single-function-word", action="store_true")
    p46.add_argument("--promote-single-char-min-consensus", type=int, default=2)
    p46.add_argument("--promote-single-char-min-source-video-count", type=int, default=2)
    p46.add_argument("--promote-single-char-min-teacher-votes", type=int, default=4)
    p46.set_defaults(func=cmd_auto_url_pipeline)

    p5 = sub.add_parser("promote", help="Shortlist formal candidates and optionally build a reviewed asset")
    p5.add_argument("--candidate-pool", type=Path, required=True)
    p5.add_argument("--out-dir", type=Path, required=True)
    p5.add_argument("--review-jsonl", type=Path, default=None, help="Reviewed JSONL; accepted items become a generated asset")
    p5.add_argument("--min-count-total", type=int, default=2)
    p5.add_argument("--min-source-video-count", type=int, default=2)
    p5.add_argument("--max-edit-distance", type=int, default=2)
    p5.add_argument("--max-shortlist", type=int, default=120)
    p5.add_argument("--allow-single-char", action="store_true", help="Include ordinary single-char items in shortlist if they pass stricter gates")
    p5.add_argument("--allow-single-function-word", action="store_true", help="Include single function-word items if they pass stricter gates")
    p5.add_argument("--single-char-min-consensus", type=int, default=2)
    p5.add_argument("--single-char-min-source-video-count", type=int, default=2)
    p5.add_argument("--single-char-min-teacher-votes", type=int, default=4)
    p5.set_defaults(func=cmd_promote)
    p6 = sub.add_parser("promote-patterns", help="Build safe phrase/contextual/proper-noun pattern assets and compare against legacy shortlist")
    p6.add_argument("--candidate-pool", type=Path, required=True)
    p6.add_argument("--transcripts-jsonl", type=Path, required=True)
    p6.add_argument("--out-dir", type=Path, required=True)
    p6.add_argument("--min-count-total", type=int, default=2)
    p6.add_argument("--min-source-video-count", type=int, default=2)
    p6.add_argument("--max-edit-distance", type=int, default=2)
    p6.add_argument("--max-shortlist", type=int, default=120)
    p6.add_argument("--allow-single-char", action="store_true")
    p6.add_argument("--allow-single-function-word", action="store_true")
    p6.add_argument("--single-char-min-consensus", type=int, default=2)
    p6.add_argument("--single-char-min-source-video-count", type=int, default=2)
    p6.add_argument("--single-char-min-teacher-votes", type=int, default=4)
    p6.add_argument("--pattern-context-chars", type=int, default=2)
    p6.add_argument("--phrase-min-count-total", type=int, default=2)
    p6.add_argument("--phrase-min-source-video-count", type=int, default=2)
    p6.add_argument("--phrase-min-teacher-votes", type=int, default=4)
    p6.add_argument("--phrase-min-consensus", type=int, default=1)
    p6.add_argument("--phrase-min-context-chars", type=int, default=2)
    p6.add_argument("--phrase-max-items", type=int, default=120)
    p6.add_argument("--contextual-min-count-total", type=int, default=2)
    p6.add_argument("--contextual-min-teacher-votes", type=int, default=2)
    p6.add_argument("--contextual-min-source-video-count", type=int, default=2)
    p6.add_argument("--contextual-min-consensus", type=int, default=1)
    p6.add_argument("--contextual-min-context-chars", type=int, default=2)
    p6.add_argument("--contextual-max-items", type=int, default=120)
    p6.add_argument("--review-min-teacher-votes", type=int, default=1)
    p6.add_argument("--review-min-context-chars", type=int, default=1)
    p6.add_argument("--review-min-pattern-len", type=int, default=4)
    p6.add_argument("--review-min-source-video-count", type=int, default=1)
    p6.add_argument("--review-min-consensus", type=int, default=1)
    p6.add_argument("--review-high-teacher-votes", type=int, default=3)
    p6.add_argument("--review-max-items", type=int, default=2000)
    p6.add_argument("--proper-noun-max-items", type=int, default=120)
    p6.add_argument("--lexicon-path", type=Path, default=Path("assets/zh_phrase/chinese_xinhua_ci_2to4.txt"))
    p6.add_argument("--proper-nouns-path", type=Path, default=Path("assets/zh_phrase/thuocl_proper_nouns.txt"))
    p6.set_defaults(func=cmd_promote_patterns)
    p7 = sub.add_parser("publish-reviewed-final", help="Publish a manually finalized review list into the formal asset and optional archive doc")
    p7.add_argument("--reviewed-final-json", type=Path, required=True, help="Final accepted review list JSON/JSONL after manual cleanup")
    p7.add_argument("--out-dir", type=Path, required=True, help="Directory for publish summaries")
    p7.add_argument("--asset-out", type=Path, default=Path("assets/zh_phrase/asr_project_confusions.json"))
    p7.add_argument("--source-label", type=str, default="", help="Source label written into asset sources[]")
    p7.add_argument("--source-run", type=str, default="", help="Human-readable source run label for archive docs")
    p7.add_argument("--manual-pool-size", type=int, default=0)
    p7.add_argument("--first-pass-accept", type=int, default=0)
    p7.add_argument("--first-pass-review", type=int, default=0)
    p7.add_argument("--first-pass-reject", type=int, default=0)
    p7.add_argument("--core-accept", type=int, default=0)
    p7.add_argument("--core-review", type=int, default=0)
    p7.add_argument("--core-reject", type=int, default=0)
    p7.add_argument("--second-pass-promoted", type=int, default=0)
    p7.add_argument("--second-pass-kept-review", type=int, default=0)
    p7.add_argument("--second-pass-rejected", type=int, default=0)
    p7.add_argument("--removed-examples-json", type=Path, default=None, help="Optional JSON/JSONL listing representative removed pairs")
    p7.add_argument("--archive-doc", type=Path, default=None, help="Optional markdown doc to append an archive subsection to")
    p7.add_argument("--archive-title", type=str, default="", help="Optional archive subsection title, e.g. 2026-04-02 第二版正式集")
    p7.add_argument("--archive-note", type=str, default="", help="Optional paragraph inserted below the archive subsection title")
    p7.add_argument("--max-edit-distance", type=int, default=2)
    p7.add_argument("--evidence-floor", type=int, default=2)
    p7.set_defaults(func=cmd_publish_reviewed_final)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
