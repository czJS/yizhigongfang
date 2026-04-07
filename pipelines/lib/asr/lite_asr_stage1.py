from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pipelines.lib.asr.lite_asr import Segment
from pipelines.lib.text.zh_text import clean_zh_text


_HOMO_CHAR_CACHE: Dict[str, Dict[str, List[str]]] = {}
_SHAPE_CHAR_CACHE: Dict[str, Dict[str, List[str]]] = {}
_CONFUSABLE_CHAR_CACHE: Dict[tuple[str, str], Dict[str, List[str]]] = {}
_ZH_WORD_SET_CACHE: Dict[tuple[str, int, int], Set[str]] = {}
_ZH_WORD_MASK_CACHE: Dict[tuple[str, int], Dict[str, List[str]]] = {}
_PROJECT_CONFUSION_CACHE: Dict[str, List[Dict[str, Any]]] = {}

_GENERIC_FUNCTION_WORDS = ("于", "把", "被", "给", "向", "对", "跟", "从", "在", "为", "往")
_GENERIC_DANGLING_TAILS = ("重新", "继续", "开始", "准备", "正在")
_GENERIC_REACTION_PAT = re.compile(r"(连连|不断|纷纷)(叫|喊|说|哭|笑|骂|打|跑|走|飞|跳)[\u4e00-\u9fff]{0,1}")
_GENERIC_VALID_REACTION_TAILS = {"叫苦", "叫喊", "叫唤", "叫嚷", "叫屈", "叫冤", "哭喊", "哭叫", "喊叫"}
_GENERIC_SHORT_TOKEN_SKIP = {
    "我们",
    "你们",
    "他们",
    "一个",
    "这个",
    "那个",
    "这里",
    "那里",
    "这样",
    "那样",
    "然后",
    "但是",
    "因为",
    "所以",
}
_HIGH_RISK_REASONS = {
    "乱码/异常字符",
    "疑似ASR脏词/生造词",
    "疑似不通顺搭配",
    "疑似动宾搭配异常",
    "疑似动词缺失/错置",
    "短句但含异常词",
}


@dataclass
class AsrStage1Options:
    glossary_fix_enable: bool = True
    low_cost_clean_enable: bool = True
    badline_detect_enable: bool = True
    same_pinyin_path: Optional[Path] = None
    same_stroke_path: Optional[Path] = None
    project_confusions_path: Optional[Path] = None
    lexicon_path: Optional[Path] = None
    proper_nouns_path: Optional[Path] = None
    output_dir: Optional[Path] = None


@dataclass
class Stage1LineState:
    idx: int
    start: float
    end: float
    before: str
    after_glossary: str
    working_text: str
    local_candidate: str
    rule_reasons: List[str]
    local_hints: List[str]
    route_tier: str
    severity: str
    base_badness: float
    working_badness: float
    prev_text: str = ""
    next_text: str = ""


@dataclass
class Stage1Candidate:
    idx: int
    source: str
    text: str
    badness: float
    score: float
    badness_drop: float = 0.0
    edit_span_size: int = 0
    evidence_kinds: List[str] = field(default_factory=list)
    selected: bool = False


@dataclass
class Stage1RepairOption:
    key: str
    text: str
    source: str
    badness_drop: float
    edit_span_size: int
    evidence_kinds: List[str]
    score: float


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_data_path(path: Optional[Path | str]) -> Optional[Path]:
    if not path:
        return None
    p = Path(str(path))
    if p.is_absolute():
        return p if p.exists() else None
    cand = (_repo_root() / p).resolve()
    return cand if cand.exists() else None


def _iter_lexicon_paths(primary_path: Optional[Path | str], *, include_extras: bool = False) -> Iterable[Path]:
    primary = _resolve_data_path(primary_path)
    if primary:
        yield primary
        if include_extras:
            parent = primary.parent
            for extra_name in ("idioms_4char.txt", "idioms_extra.txt"):
                extra = parent / extra_name
                if extra.exists() and extra != primary:
                    yield extra


def _load_same_pinyin_char_map(path: Optional[Path | str]) -> Dict[str, List[str]]:
    p = str(path or "").strip()
    if not p:
        return {}
    if p in _HOMO_CHAR_CACHE:
        return _HOMO_CHAR_CACHE[p]
    mp: Dict[str, List[str]] = {}
    try:
        rp = _resolve_data_path(p)
        if not rp:
            _HOMO_CHAR_CACHE[p] = {}
            return {}
        raw = rp.read_text(encoding="utf-8", errors="ignore")
        for ln in raw.splitlines():
            s = (ln or "").strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            key = parts[0].strip()
            if len(key) != 1:
                continue
            chars: List[str] = []
            for tok in parts[1:]:
                for ch in tok.strip():
                    if ch and ch != key:
                        chars.append(ch)
            seen: Set[str] = set()
            chars2: List[str] = []
            for ch in chars:
                if ch in seen:
                    continue
                seen.add(ch)
                chars2.append(ch)
            if chars2:
                mp[key] = chars2
    except Exception:
        mp = {}
    _HOMO_CHAR_CACHE[p] = mp
    return mp


def _load_same_stroke_char_map(path: Optional[Path | str]) -> Dict[str, List[str]]:
    p = str(path or "").strip()
    if not p:
        return {}
    if p in _SHAPE_CHAR_CACHE:
        return _SHAPE_CHAR_CACHE[p]
    mp: Dict[str, List[str]] = {}
    try:
        rp = _resolve_data_path(p)
        if not rp:
            _SHAPE_CHAR_CACHE[p] = {}
            return {}
        for ln in rp.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = (ln or "").strip().lstrip("\ufeff")
            if not s or s.startswith("#"):
                continue
            chars: List[str] = []
            for tok in re.split(r"[\t ]+", s):
                for ch in tok.strip():
                    if ch and re.fullmatch(r"[\u4e00-\u9fff]", ch):
                        chars.append(ch)
            uniq: List[str] = []
            seen: Set[str] = set()
            for ch in chars:
                if ch in seen:
                    continue
                seen.add(ch)
                uniq.append(ch)
            for ch in uniq:
                bucket = mp.setdefault(ch, [])
                for other in uniq:
                    if other != ch and other not in bucket:
                        bucket.append(other)
    except Exception:
        mp = {}
    _SHAPE_CHAR_CACHE[p] = mp
    return mp


def _load_confusable_char_map(
    same_pinyin_path: Optional[Path | str],
    same_stroke_path: Optional[Path | str],
) -> Dict[str, List[str]]:
    key = (str(same_pinyin_path or "").strip(), str(same_stroke_path or "").strip())
    if key in _CONFUSABLE_CHAR_CACHE:
        return _CONFUSABLE_CHAR_CACHE[key]
    merged: Dict[str, List[str]] = {}
    for mp in (_load_same_pinyin_char_map(same_pinyin_path), _load_same_stroke_char_map(same_stroke_path)):
        for ch, items in mp.items():
            bucket = merged.setdefault(ch, [])
            for item in items:
                if item not in bucket:
                    bucket.append(item)
    _CONFUSABLE_CHAR_CACHE[key] = merged
    return merged


def _load_project_confusions(path: Optional[Path | str]) -> List[Dict[str, Any]]:
    p = str(path or "").strip()
    if not p:
        return []
    if p in _PROJECT_CONFUSION_CACHE:
        return _PROJECT_CONFUSION_CACHE[p]
    out: List[Dict[str, Any]] = []
    try:
        rp = _resolve_data_path(p)
        if not rp:
            _PROJECT_CONFUSION_CACHE[p] = []
            return []
        raw = json.loads(rp.read_text(encoding="utf-8", errors="ignore") or "[]")
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            wrong = str(it.get("wrong") or "").strip()
            candidates = [str(x).strip() for x in (it.get("candidates") or []) if str(x).strip()]
            if not wrong or not candidates:
                continue
            out.append(
                {
                    "wrong": wrong,
                    "candidates": candidates,
                    "type": str(it.get("type") or "").strip(),
                    "evidence_count": int(it.get("evidence_count") or 0),
                    "sources": [str(x).strip() for x in (it.get("sources") or []) if str(x).strip()],
                    "requires_high_risk": bool(it.get("requires_high_risk", True)),
                    "max_edit_distance": max(1, int(it.get("max_edit_distance") or 2)),
                    "notes": str(it.get("notes") or "").strip(),
                }
            )
    except Exception:
        out = []
    _PROJECT_CONFUSION_CACHE[p] = out
    return out


def _project_confusion_hits(
    text: str,
    *,
    path: Optional[Path | str],
    min_evidence_count: int = 2,
) -> List[Dict[str, Any]]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    out: List[Dict[str, Any]] = []
    for item in _load_project_confusions(path):
        wrong = str(item.get("wrong") or "").strip()
        if not wrong or len(wrong) > 8:
            continue
        if int(item.get("evidence_count") or 0) < int(min_evidence_count):
            continue
        if wrong in compact:
            out.append(item)
    return out


def _load_zh_word_set(
    path: Optional[Path | str],
    *,
    min_len: int = 2,
    max_len: int = 4,
    include_extras: bool = False,
) -> Set[str]:
    key = (f"{str(path or '').strip()}|extras={int(include_extras)}", int(min_len), int(max_len))
    if key in _ZH_WORD_SET_CACHE:
        return _ZH_WORD_SET_CACHE[key]
    out: Set[str] = set()
    try:
        for rp in _iter_lexicon_paths(path, include_extras=include_extras):
            for ln in rp.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = (ln or "").strip().lstrip("\ufeff")
                if not s or s.startswith("#"):
                    continue
                word = re.split(r"[\t ]+", s, maxsplit=1)[0].strip()
                if not (min_len <= len(word) <= max_len):
                    continue
                if not re.fullmatch(r"[\u4e00-\u9fff]+", word):
                    continue
                out.add(word)
    except Exception:
        out = set()
    _ZH_WORD_SET_CACHE[key] = out
    return out


def _load_zh_word_mask_index(path: Optional[Path | str], *, word_len: int) -> Dict[str, List[str]]:
    key = (str(path or "").strip(), int(word_len))
    if key in _ZH_WORD_MASK_CACHE:
        return _ZH_WORD_MASK_CACHE[key]
    out: Dict[str, List[str]] = {}
    if word_len <= 0:
        _ZH_WORD_MASK_CACHE[key] = out
        return out
    words = _load_zh_word_set(str(path or "").strip(), min_len=word_len, max_len=word_len, include_extras=True)
    try:
        for word in sorted(words):
            for pos in range(word_len):
                mask = word[:pos] + "*" + word[pos + 1 :]
                bucket = out.setdefault(mask, [])
                bucket.append(word)
    except Exception:
        out = {}
    _ZH_WORD_MASK_CACHE[key] = out
    return out


def _generic_confusable_terms(
    text: str,
    *,
    max_terms: int = 4,
    lexicon_path: Optional[Path | str] = None,
    proper_nouns_path: Optional[Path | str] = None,
    same_pinyin_path: Optional[Path | str] = None,
    same_stroke_path: Optional[Path | str] = None,
) -> List[str]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    lexicon_words = _load_zh_word_set(lexicon_path, min_len=2, max_len=4, include_extras=True)
    proper_nouns = _load_zh_word_set(proper_nouns_path, min_len=2, max_len=8, include_extras=False)
    confusable_map = _load_confusable_char_map(same_pinyin_path, same_stroke_path)
    out: List[str] = []
    seen: Set[str] = set()
    for length in (3, 2):
        mask_index = _load_zh_word_mask_index(lexicon_path, word_len=length)
        for i in range(0, max(0, len(compact) - length + 1)):
            sub = compact[i : i + length]
            if sub in seen or sub in _GENERIC_SHORT_TOKEN_SKIP:
                continue
            if not re.fullmatch(r"[\u4e00-\u9fff]+", sub):
                continue
            if sub in lexicon_words or sub in proper_nouns:
                continue
            has_confusable = False
            for pos in range(length):
                mask = sub[:pos] + "*" + sub[pos + 1 :]
                for cand in (mask_index.get(mask) or []):
                    if cand == sub:
                        continue
                    changed_chars = sum(1 for a, b in zip(sub, cand) if a != b)
                    if changed_chars <= 0:
                        continue
                    if _same_pinyin_change_count(sub, cand, confusable_map) == changed_chars:
                        has_confusable = True
                        break
                if has_confusable:
                    break
            if not has_confusable:
                continue
            seen.add(sub)
            out.append(sub)
            if len(out) >= int(max_terms):
                return out
    return out


def _has_confusable_after_function_word(compact: str, confusable_terms: List[str]) -> bool:
    for fw in _GENERIC_FUNCTION_WORDS:
        for term in confusable_terms:
            if fw + term in compact:
                return True
    return False


def _has_generic_dangling_tail(compact: str) -> bool:
    return any(compact.endswith(tok) for tok in _GENERIC_DANGLING_TAILS)


def _generic_reaction_windows(text: str) -> List[str]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    out: List[str] = []
    for m in _GENERIC_REACTION_PAT.finditer(compact):
        frag = str(m.group(0) or "")
        if len(frag) >= 2:
            tail = frag[-2:]
            if re.fullmatch(r"[\u4e00-\u9fff]{2}", tail) and tail not in _GENERIC_VALID_REACTION_TAILS and tail not in out:
                out.append(tail)
    return out


def _detect_asr_dirty_sentence_signals(
    text: str,
    *,
    lexicon_path: Optional[Path | str] = None,
    proper_nouns_path: Optional[Path | str] = None,
    same_pinyin_path: Optional[Path | str] = None,
    same_stroke_path: Optional[Path | str] = None,
    project_confusions_path: Optional[Path | str] = None,
) -> List[str]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    reasons: List[str] = []
    confusable_terms = _generic_confusable_terms(
        compact,
        lexicon_path=lexicon_path,
        proper_nouns_path=proper_nouns_path,
        same_pinyin_path=same_pinyin_path,
        same_stroke_path=same_stroke_path,
    )
    project_hits = _project_confusion_hits(compact, path=project_confusions_path)
    reaction_windows = _generic_reaction_windows(compact)
    if project_hits:
        reasons.append("项目混淆命中")
    elif confusable_terms:
        reasons.append("疑似ASR脏词/生造词")
    if _has_confusable_after_function_word(compact, confusable_terms):
        reasons.append("疑似不通顺搭配")
    if reaction_windows:
        reasons.append("疑似动宾搭配异常")
    if _has_generic_dangling_tail(compact):
        reasons.append("疑似动词缺失/错置")
    if 3 <= len(compact) <= 16 and (confusable_terms or _has_confusable_after_function_word(compact, confusable_terms) or reaction_windows):
        reasons.append("短句但含异常词")
    return reasons[:4]


def _negative_replacement_patterns() -> List[Tuple[str, str]]:
    return [
        ("带", "戴"),
        ("真", "心"),
        ("稀", "悉"),
    ]


def _is_negative_common_word_swap(base_mid: str, opt_mid: str) -> bool:
    if len(base_mid) != len(opt_mid) or not base_mid or not opt_mid:
        return False
    if base_mid == opt_mid:
        return False
    for src, tgt in _negative_replacement_patterns():
        if src in base_mid and tgt in opt_mid:
            return True
    return False


def _is_truncation_like(base: str, opt: str) -> bool:
    base_s = re.sub(r"\s+", "", str(base or ""))
    opt_s = re.sub(r"\s+", "", str(opt or ""))
    if not base_s or not opt_s or len(opt_s) >= len(base_s):
        return False
    if base_s.startswith(opt_s) or base_s.endswith(opt_s):
        return len(base_s) - len(opt_s) >= 1
    return False


def _route_tier_for_line(
    text: str,
    *,
    reasons: List[str],
    duration_s: float,
    opts: AsrStage1Options,
) -> str:
    compact = re.sub(r"\s+", "", clean_zh_text(text))
    badness = _zh_repair_line_badness(
        compact,
        lexicon_path=opts.lexicon_path,
        proper_nouns_path=opts.proper_nouns_path,
        same_pinyin_path=opts.same_pinyin_path,
        same_stroke_path=opts.same_stroke_path,
    )
    hard_reason_set = {
        "乱码/异常字符",
        "疑似不通顺搭配",
        "疑似动宾搭配异常",
        "疑似动词缺失/错置",
        "重复标点",
        "文本极短但时长较长",
        "文本较长但时长较短",
    }
    soft_reason_set = {
        "疑似ASR脏词/生造词",
        "短句但含异常词",
        "项目混淆命中",
        "疑似专名/称谓一致性",
    }
    hard_hits = sum(1 for r in reasons if r in hard_reason_set)
    soft_hits = sum(1 for r in reasons if r in soft_reason_set)
    timing_hard = (len(compact) <= 2 and duration_s >= 3.0) or (len(compact) >= 28 and duration_s <= 0.8)
    project_hits = _project_confusion_hits(compact, path=opts.project_confusions_path)
    short_local_hits = 0
    for hit in project_hits:
        wrong = str(hit.get("wrong") or "")
        if 1 <= len(wrong) <= 2:
            short_local_hits += 1
    if hard_hits > 0 or timing_hard:
        return "hard"
    if short_local_hits > 0 and (soft_hits > 0 or badness >= 4.0):
        return "hard"
    if soft_hits > 0 or badness >= 3.0:
        return "soft"
    return "safe"


def _detect_repair_candidate_reason(text: str) -> List[str]:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return []
    reasons: List[str] = []
    if re.search(r"(羽人|公主|西域|中原|四大天王|女巫手下|小土豆|杂兵)", compact):
        reasons.append("疑似专名/称谓一致性")
    return reasons[:2]


def _zh_repair_line_badness(
    text: str,
    *,
    lexicon_path: Optional[Path | str] = None,
    proper_nouns_path: Optional[Path | str] = None,
    same_pinyin_path: Optional[Path | str] = None,
    same_stroke_path: Optional[Path | str] = None,
) -> float:
    compact = re.sub(r"\s+", "", clean_zh_text(str(text or "")))
    if not compact:
        return 0.0
    score = 0.0
    confusable_terms = _generic_confusable_terms(
        compact,
        lexicon_path=lexicon_path,
        proper_nouns_path=proper_nouns_path,
        same_pinyin_path=same_pinyin_path,
        same_stroke_path=same_stroke_path,
    )
    score += 4.0 * len(
        _detect_asr_dirty_sentence_signals(
            compact,
            lexicon_path=lexicon_path,
            proper_nouns_path=proper_nouns_path,
            same_pinyin_path=same_pinyin_path,
            same_stroke_path=same_stroke_path,
        )
    )
    if confusable_terms:
        score += 2.5 + min(2.0, 0.7 * len(confusable_terms))
    if _has_confusable_after_function_word(compact, confusable_terms):
        score += 2.2
    if _generic_reaction_windows(compact):
        score += 1.8
    if _has_generic_dangling_tail(compact):
        score += 2.0
    return score


def _same_pinyin_change_count(src: str, tgt: str, homo_map: Dict[str, List[str]]) -> int:
    if len(src) != len(tgt):
        return 0
    count = 0
    for a, b in zip(src, tgt):
        if a == b:
            continue
        if b in (homo_map.get(a) or []):
            count += 1
    return count


def _contextual_repair_bonus(line: str, src: str, cand: str) -> float:
    compact = re.sub(r"\s+", "", clean_zh_text(str(line or "")))
    if not compact or src == cand:
        return 0.0
    bonus = 0.0
    if src.startswith("叫") and cand.startswith("叫") and re.search(r"(连连|百姓|众人|村民|路人)", compact):
        reaction_bonus = {
            "叫苦": 3.4,
            "叫喊": 3.0,
            "叫唤": 2.9,
            "叫嚷": 2.8,
            "叫屈": 2.7,
            "叫冤": 2.7,
        }
        if cand in reaction_bonus:
            bonus += reaction_bonus[cand]
        elif cand.endswith(("跑", "冲", "跳", "飞")):
            bonus -= 1.8
    return bonus


def _collect_local_repair_windows(
    line: str,
    *,
    lexicon_path: Optional[Path | str] = None,
    proper_nouns_path: Optional[Path | str] = None,
    same_pinyin_path: Optional[Path | str] = None,
    same_stroke_path: Optional[Path | str] = None,
    project_confusions_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    s = str(line or "")
    if not s:
        return []
    out: List[Dict[str, Any]] = []
    seen: Set[Tuple[int, int, str]] = set()

    def _append_window(start: int, end: int, text: str) -> None:
        key = (int(start), int(end), str(text or ""))
        if start < 0 or end <= start or not text or key in seen:
            return
        seen.add(key)
        out.append({"start": int(start), "end": int(end), "text": str(text or ""), "explicit": []})

    def _append_window_with_candidates(start: int, end: int, text: str, candidates: List[str]) -> None:
        key = (int(start), int(end), str(text or ""))
        cand_list = [str(x).strip() for x in (candidates or []) if str(x).strip()]
        if start < 0 or end <= start or not text or not cand_list:
            return
        if key in seen:
            for item in out:
                if (item["start"], item["end"], item["text"]) != key:
                    continue
                cur = item.setdefault("explicit", [])
                for cand in cand_list:
                    if cand not in cur:
                        cur.append(cand)
                return
            return
        seen.add(key)
        out.append({"start": int(start), "end": int(end), "text": str(text or ""), "explicit": cand_list})

    confusable_map = _load_confusable_char_map(same_pinyin_path, same_stroke_path)

    def _masked_same_pinyin_candidates(src: str) -> List[str]:
        src2 = str(src or "").strip()
        if not (2 <= len(src2) <= 4) or not re.fullmatch(r"[\u4e00-\u9fff]+", src2):
            return []
        candidates: List[str] = []
        mask_indexes = [
            _load_zh_word_mask_index(lexicon_path, word_len=len(src2)),
            _load_zh_word_mask_index(proper_nouns_path, word_len=len(src2)),
        ]
        for mask_index in mask_indexes:
            for pos in range(len(src2)):
                mask = src2[:pos] + "*" + src2[pos + 1 :]
                for cand in (mask_index.get(mask) or []):
                    if cand == src2 or cand in candidates:
                        continue
                    changed_chars = sum(1 for a, b in zip(src2, cand) if a != b)
                    if changed_chars <= 0:
                        continue
                    if _same_pinyin_change_count(src2, cand, confusable_map) >= 1:
                        candidates.append(cand)
        return candidates[:6]

    for term in _generic_confusable_terms(
        s,
        lexicon_path=lexicon_path,
        proper_nouns_path=proper_nouns_path,
        same_pinyin_path=same_pinyin_path,
        same_stroke_path=same_stroke_path,
    ):
        start = 0
        while start < len(s):
            at = s.find(term, start)
            if at < 0:
                break
            _append_window(int(at), int(at + len(term)), term)
            start = at + len(term)
    for term in _generic_reaction_windows(s):
        start = 0
        while start < len(s):
            at = s.find(term, start)
            if at < 0:
                break
            _append_window(int(at), int(at + len(term)), term)
            start = at + len(term)
    for item in _project_confusion_hits(s, path=project_confusions_path):
        wrong = str(item.get("wrong") or "").strip()
        candidates = [str(x).strip() for x in (item.get("candidates") or []) if str(x).strip()]
        start = 0
        while start < len(s):
            at = s.find(wrong, start)
            if at < 0:
                break
            _append_window_with_candidates(int(at), int(at + len(wrong)), wrong, candidates)
            start = at + len(wrong)
    compact = re.sub(r"\s+", "", s)
    for length in (2,):
        for i in range(0, max(0, len(compact) - length + 1)):
            sub = compact[i : i + length]
            cand_list = _masked_same_pinyin_candidates(sub)
            if not cand_list:
                continue
            at = s.find(sub)
            if at < 0:
                continue
            _append_window_with_candidates(int(at), int(at + len(sub)), sub, cand_list)
    return out


def _pick_local_zh_repair(
    *,
    line: str,
    rule_reasons: List[str],
    same_pinyin_path: Optional[Path | str],
    same_stroke_path: Optional[Path | str],
    project_confusions_path: Optional[Path | str],
    lexicon_path: Optional[Path | str],
    proper_nouns_path: Optional[Path | str],
) -> Tuple[str, List[str]]:
    s = str(line or "").strip()
    if not s:
        return "", []
    base_badness = _zh_repair_line_badness(
        s,
        lexicon_path=lexicon_path,
        proper_nouns_path=proper_nouns_path,
        same_pinyin_path=same_pinyin_path,
        same_stroke_path=same_stroke_path,
    )
    if base_badness <= 0 and not rule_reasons:
        return "", []

    confusable_map = _load_confusable_char_map(same_pinyin_path, same_stroke_path)
    lexicon_words = _load_zh_word_set(lexicon_path, min_len=2, max_len=4, include_extras=True)
    proper_nouns = _load_zh_word_set(proper_nouns_path, min_len=2, max_len=8, include_extras=False)
    windows = _collect_local_repair_windows(
        s,
        lexicon_path=lexicon_path,
        proper_nouns_path=proper_nouns_path,
        same_pinyin_path=same_pinyin_path,
        same_stroke_path=same_stroke_path,
        project_confusions_path=project_confusions_path,
    )
    if not windows:
        return "", []

    scored: List[Tuple[float, str, str]] = []
    for win in windows:
        start = int(win.get("start", -1))
        end = int(win.get("end", -1))
        src = str(win.get("text") or "")
        if not (0 <= start < end <= len(s)) or s[start:end] != src:
            continue
        candidates: List[str] = []
        for cand in win.get("explicit") or []:
            c = str(cand or "").strip()
            if c and c != src and c not in candidates:
                candidates.append(c)
        if 2 <= len(src) <= 4 and re.fullmatch(r"[\u4e00-\u9fff]+", src):
            mask_index = _load_zh_word_mask_index(lexicon_path, word_len=len(src))
            for pos in range(len(src)):
                mask = src[:pos] + "*" + src[pos + 1 :]
                for cand in (mask_index.get(mask) or []):
                    if cand != src and cand not in candidates:
                        candidates.append(cand)
        for cand in candidates:
            if len(cand) != len(src) or cand == src:
                continue
            if len(src) > 2 and not ("疑似专名/称谓一致性" in (rule_reasons or []) and len(src) <= 4):
                continue
            if cand in proper_nouns and "疑似专名/称谓一致性" not in (rule_reasons or []):
                continue
            changed_chars = sum(1 for a, b in zip(src, cand) if a != b)
            if changed_chars <= 0:
                continue
            if changed_chars > 2:
                continue
            cand_line = s[:start] + cand + s[end:]
            if _is_truncation_like(s, cand_line):
                continue
            cand_badness = _zh_repair_line_badness(
                cand_line,
                lexicon_path=lexicon_path,
                proper_nouns_path=proper_nouns_path,
                same_pinyin_path=same_pinyin_path,
                same_stroke_path=same_stroke_path,
            )
            same_pinyin_changes = _same_pinyin_change_count(src, cand, confusable_map)
            context_bonus = _contextual_repair_bonus(s, src, cand)
            project_hint = any(str(h.get("wrong") or "").strip() == src and cand in (h.get("candidates") or []) for h in _project_confusion_hits(s, path=project_confusions_path))
            if len(src) <= 3:
                short_candidate_ok = False
                if "疑似专名/称谓一致性" in (rule_reasons or []) and cand in proper_nouns:
                    short_candidate_ok = True
                elif project_hint:
                    short_candidate_ok = True
                elif same_pinyin_changes == changed_chars and changed_chars > 0 and cand in lexicon_words and context_bonus >= 2.8:
                    short_candidate_ok = True
                elif cand in lexicon_words and context_bonus >= 2.8:
                    short_candidate_ok = True
                if not short_candidate_ok:
                    continue
            score = (base_badness - cand_badness) * 2.8
            if src not in lexicon_words and cand in lexicon_words:
                score += 1.8
            if cand in lexicon_words:
                score += 0.6
            if project_hint:
                score += 1.6
            if same_pinyin_changes == changed_chars and changed_chars > 0:
                score += 1.4
            elif same_pinyin_changes > 0:
                score += 0.7
            if changed_chars == 1:
                score += 0.8
            score += context_bonus
            if cand_badness >= base_badness and same_pinyin_changes == 0:
                score -= 2.5
            if score <= 0:
                continue
            scored.append((score, cand_line, f"{src}->{cand}"))

    if not scored:
        return "", []
    scored.sort(key=lambda it: (-it[0], len(it[1]), it[2]))
    best_score, best_line, _best_hint = scored[0]
    hints: List[str] = []
    for _score, _line, hint in scored[:3]:
        if hint not in hints:
            hints.append(hint)
    if best_score >= 5.5 and _zh_repair_line_badness(
        best_line,
        lexicon_path=lexicon_path,
        proper_nouns_path=proper_nouns_path,
        same_pinyin_path=same_pinyin_path,
        same_stroke_path=same_stroke_path,
    ) < base_badness:
        return best_line, hints
    return "", hints


def _extract_single_replacement(base: str, opt: str) -> Tuple[str, str]:
    base_s = str(base or "").strip()
    opt_s = str(opt or "").strip()
    if base_s == opt_s:
        return "", ""
    prefix = 0
    while prefix < len(base_s) and prefix < len(opt_s) and base_s[prefix] == opt_s[prefix]:
        prefix += 1
    suffix = 0
    max_suffix = min(len(base_s) - prefix, len(opt_s) - prefix)
    while suffix < max_suffix and base_s[len(base_s) - 1 - suffix] == opt_s[len(opt_s) - 1 - suffix]:
        suffix += 1
    base_mid = base_s[prefix : len(base_s) - suffix if suffix else len(base_s)]
    opt_mid = opt_s[prefix : len(opt_s) - suffix if suffix else len(opt_s)]
    return base_mid, opt_mid


def _hint_targets_from_pairs(hints: List[str]) -> Set[str]:
    out: Set[str] = set()
    for hint in hints or []:
        s = str(hint or "").strip()
        if "->" not in s:
            continue
        _src, tgt = s.split("->", 1)
        tgt2 = str(tgt or "").strip()
        if tgt2:
            out.add(tgt2)
    return out


def _hint_candidate_sentences(base: str, hints: List[str]) -> List[str]:
    base_s = str(base or "").strip()
    if not base_s:
        return []
    out: List[str] = []
    for hint in hints or []:
        s = str(hint or "").strip()
        if "->" not in s:
            continue
        src, tgt = s.split("->", 1)
        src_s = str(src or "").strip()
        tgt_s = str(tgt or "").strip()
        if not src_s or not tgt_s or src_s == tgt_s:
            continue
        if src_s not in base_s:
            continue
        cand = base_s.replace(src_s, tgt_s, 1)
        if cand != base_s and cand not in out:
            out.append(cand)
    return out[:3]


def _derive_grammar_option_sentences(base: str) -> List[str]:
    base_s = str(base or "").strip()
    if not base_s:
        return []
    out: List[str] = []
    if "得起来" in base_s:
        out.append(base_s.replace("得起来", "了起来", 1))
    for pat in (r"地([\u4e00-\u9fff]{1,3}了起来)", r"地([\u4e00-\u9fff]{1,3}起来)"):
        m = re.search(pat, base_s)
        if not m:
            continue
        cand = base_s[: m.start()] + str(m.group(1) or "") + base_s[m.end() :]
        if cand and cand != base_s and cand not in out:
            out.append(cand)
    return out[:2]


def _derive_repair_option_sentences(
    base: str,
    *,
    local_hints: List[str],
    opts: AsrStage1Options,
    local_candidate: str = "",
) -> List[str]:
    base_s = str(base or "").strip()
    if not base_s:
        return []
    out: List[str] = []
    if local_candidate and local_candidate != base_s:
        out.append(str(local_candidate).strip())
    for cand in _hint_candidate_sentences(base_s, local_hints):
        if cand not in out:
            out.append(cand)
    lexicon_words = _load_zh_word_set(opts.lexicon_path, min_len=2, max_len=4, include_extras=True)
    proper_nouns = _load_zh_word_set(opts.proper_nouns_path, min_len=2, max_len=8, include_extras=False)
    confusable_map = _load_confusable_char_map(opts.same_pinyin_path, opts.same_stroke_path)
    project_hits = _project_confusion_hits(base_s, path=opts.project_confusions_path)
    windows = _collect_local_repair_windows(
        base_s,
        lexicon_path=opts.lexicon_path,
        proper_nouns_path=opts.proper_nouns_path,
        same_pinyin_path=opts.same_pinyin_path,
        same_stroke_path=opts.same_stroke_path,
        project_confusions_path=opts.project_confusions_path,
    )
    scored: List[Tuple[float, str]] = []
    for win in windows:
        start = int(win.get("start", -1))
        end = int(win.get("end", -1))
        src = str(win.get("text") or "").strip()
        if not (0 <= start < end <= len(base_s)) or not src or base_s[start:end] != src:
            continue
        project_hint = any(str(hit.get("wrong") or "").strip() == src for hit in project_hits)
        src_is_known = src in lexicon_words or src in proper_nouns
        for cand in win.get("explicit") or []:
            tgt = str(cand or "").strip()
            if not tgt or tgt == src or len(tgt) != len(src):
                continue
            changed_chars = sum(1 for a, b in zip(src, tgt) if a != b)
            if changed_chars <= 0 or changed_chars > 2:
                continue
            if _same_pinyin_change_count(src, tgt, confusable_map) <= 0:
                continue
            if tgt not in lexicon_words and tgt not in proper_nouns:
                continue
            if src_is_known and not project_hint:
                continue
            cand_line = base_s[:start] + tgt + base_s[end:]
            if cand_line == base_s:
                continue
            bad_drop = _line_badness(base_s, opts=opts) - _line_badness(cand_line, opts=opts)
            score = float(bad_drop) + (1.0 if tgt in proper_nouns else 0.0) + (0.6 if tgt in lexicon_words else 0.0)
            scored.append((score, cand_line))
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    for _score, cand_line in scored:
        if cand_line not in out:
            out.append(cand_line)
        if len(out) >= 4:
            break
    return out[:4]


def _candidate_edit_span_size(base: str, opt: str) -> int:
    base_mid, opt_mid = _extract_single_replacement(base, opt)
    return max(len(base_mid), len(opt_mid))


def _candidate_evidence_kinds(
    *,
    base: str,
    opt: str,
    source: str,
    rule_reasons: List[str],
    local_hints: List[str],
    opts: AsrStage1Options,
) -> Set[str]:
    base_s = str(base or "").strip()
    opt_s = str(opt or "").strip()
    if not base_s or not opt_s or base_s == opt_s:
        return set()
    base_mid, opt_mid = _extract_single_replacement(base_s, opt_s)
    if not base_mid and not opt_mid:
        return set()
    evidence: Set[str] = set()
    hint_targets = _hint_targets_from_pairs(local_hints)
    if source == "local":
        evidence.add("local_candidate")
    if source == "hint":
        evidence.add("hint_option")
    if source == "grammar":
        evidence.add("grammar_option")
    if opt_mid in hint_targets or any(tgt and tgt in opt_s for tgt in hint_targets):
        evidence.add("local_hint")
    reason_set = {str(r or "").strip() for r in (rule_reasons or []) if str(r or "").strip()}
    if reason_set & {"疑似不通顺搭配", "疑似动宾搭配异常", "疑似动词缺失/错置"}:
        if any(ch in {"了", "着", "的", "地", "得"} for ch in (base_mid + opt_mid)):
            evidence.add("grammar")
    confusable_map = _load_confusable_char_map(opts.same_pinyin_path, opts.same_stroke_path)
    if len(base_mid) == len(opt_mid) and _same_pinyin_change_count(base_mid, opt_mid, confusable_map) >= 1:
        evidence.add("confusable")
    lexicon_words = _load_zh_word_set(opts.lexicon_path, min_len=2, max_len=4, include_extras=True)
    proper_nouns = _load_zh_word_set(opts.proper_nouns_path, min_len=2, max_len=8, include_extras=False)
    if opt_mid in lexicon_words:
        evidence.add("lexicon")
    if opt_mid in proper_nouns:
        evidence.add("proper_noun")
    if any(str(hit.get("wrong") or "").strip() == base_mid and opt_mid in (hit.get("candidates") or []) for hit in _project_confusion_hits(base_s, path=opts.project_confusions_path)):
        evidence.add("project_confusion")
    if _contextual_repair_bonus(base_s, base_mid, opt_mid) >= 2.8:
        evidence.add("context_bonus")
    base_badness = _line_badness(base_s, opts=opts)
    opt_badness = _line_badness(opt_s, opts=opts)
    if opt_badness + 1.0 <= base_badness:
        evidence.add("badness_drop")
    return evidence


def _repair_option_score(
    *,
    base: str,
    option_text: str,
    source: str,
    evidence_kinds: Set[str],
    opts: AsrStage1Options,
) -> float:
    badness_drop = _line_badness(str(base or "").strip(), opts=opts) - _line_badness(str(option_text or "").strip(), opts=opts)
    edit_span_size = _candidate_edit_span_size(base, option_text)
    source_bonus = {"local": 4.0, "hint": 3.6, "grammar": 2.2, "derived": 1.2}.get(source, 1.0)
    score = float(badness_drop) * 3.2 + float(len(evidence_kinds)) * 1.1 + source_bonus
    score -= max(0, int(edit_span_size) - 2) * 0.7
    return float(score)


def _build_stage1_candidate_lattice(state: Stage1LineState, *, opts: AsrStage1Options) -> List[Stage1RepairOption]:
    proposals: Dict[str, Stage1RepairOption] = {}

    def _add_option(text: str, *, source: str) -> None:
        candidate_text = str(text or "").strip()
        if not candidate_text or candidate_text == state.working_text:
            return
        evidence_kinds = _candidate_evidence_kinds(
            base=state.working_text,
            opt=candidate_text,
            source=source,
            rule_reasons=list(state.rule_reasons or []),
            local_hints=list(state.local_hints or []),
            opts=opts,
        )
        edit_span_size = _candidate_edit_span_size(state.working_text, candidate_text)
        badness_drop = _line_badness(state.working_text, opts=opts) - _line_badness(candidate_text, opts=opts)
        if source == "grammar":
            if edit_span_size > 4 or not (evidence_kinds & {"grammar", "badness_drop", "local_hint"}):
                return
        elif source == "derived":
            if not (
                evidence_kinds & {"project_confusion", "proper_noun", "lexicon", "context_bonus"}
                or (("badness_drop" in evidence_kinds) and ("confusable" in evidence_kinds) and badness_drop >= 1.5)
            ):
                return
            if badness_drop <= 0 and not (evidence_kinds & {"project_confusion", "proper_noun", "context_bonus"}):
                return
        elif source == "hint":
            if edit_span_size > 6:
                return
        score = _repair_option_score(
            base=state.working_text,
            option_text=candidate_text,
            source=source,
            evidence_kinds=evidence_kinds,
            opts=opts,
        )
        if score <= 0:
            return
        cur = proposals.get(candidate_text)
        option = Stage1RepairOption(
            key="",
            text=candidate_text,
            source=source,
            badness_drop=float(badness_drop),
            edit_span_size=int(edit_span_size),
            evidence_kinds=sorted(evidence_kinds),
            score=float(score),
        )
        if cur is None or option.score > cur.score:
            proposals[candidate_text] = option

    if state.local_candidate and state.local_candidate != state.working_text:
        _add_option(state.local_candidate, source="local")
    for cand in _hint_candidate_sentences(state.working_text, list(state.local_hints or [])):
        _add_option(cand, source="hint")
    grammar_reasons = {"疑似不通顺搭配", "疑似动宾搭配异常", "疑似动词缺失/错置"}
    if any(reason in grammar_reasons for reason in (state.rule_reasons or [])):
        for cand in _derive_grammar_option_sentences(state.working_text):
            _add_option(cand, source="grammar")
    for cand in _derive_repair_option_sentences(
        state.working_text,
        local_hints=list(state.local_hints or []),
        opts=opts,
        local_candidate=state.local_candidate,
    ):
        _add_option(cand, source="derived")

    ranked = sorted(proposals.values(), key=lambda item: (-item.score, -item.badness_drop, item.edit_span_size, item.text))
    ranked_preferred = [item for item in ranked if item.source in {"local", "hint", "grammar"}]
    ranked_fallback = [item for item in ranked if item.source not in {"local", "hint", "grammar"}]
    out: List[Stage1RepairOption] = []
    final_ranked: List[Stage1RepairOption] = []
    for option in ranked_preferred + ranked_fallback:
        if option.text in {item.text for item in final_ranked}:
            continue
        final_ranked.append(option)
        if len(final_ranked) >= 3:
            break
    for idx, option in enumerate(final_ranked, start=1):
        option.key = f"option_{idx}"
        out.append(option)
    return out


def _derive_stage1_micro_hints(line: str, rule_reasons: List[str], *, opts: AsrStage1Options) -> List[str]:
    s = clean_zh_text(str(line or "")).strip()
    if not s:
        return []
    reasons = {str(r or "").strip() for r in (rule_reasons or []) if str(r or "").strip()}
    hints: List[str] = []

    def _tail_verb(prefix: str) -> str:
        raw = re.sub(r"[^\u4e00-\u9fff]", "", str(prefix or ""))
        if not raw:
            return ""
        leading_particles = {"就", "又", "才", "再", "还", "便", "都", "正"}
        for size in (3, 2, 1):
            if len(raw) >= size:
                cand = raw[-size:]
                while len(cand) >= 2 and cand[:1] in leading_particles:
                    cand = cand[1:]
                if cand not in _GENERIC_SHORT_TOKEN_SKIP:
                    return cand
        return ""

    # High-value, low-risk grammatical repairs that often unblock downstream MT.
    if reasons & _HIGH_RISK_REASONS:
        for m in re.finditer(r"([\u4e00-\u9fff]{1,8})得起来", s):
            verb = _tail_verb(m.group(1))
            if verb:
                hint = f"{verb}得起来->{verb}了起来"
                if hint not in hints:
                    hints.append(hint)
        for m in re.finditer(r"([\u4e00-\u9fff]{1,8})得出来", s):
            verb = _tail_verb(m.group(1))
            if verb:
                hint = f"{verb}得出来->{verb}了出来"
                if hint not in hints:
                    hints.append(hint)

    # Reaction phrase confusion is common in noisy ASR and benefits from a very small targeted hint.
    if re.search(r"(连连|不断|纷纷)叫跑", s):
        hints.append("叫跑->叫苦")

    out: List[str] = []
    for hint in hints:
        hh = str(hint or "").strip()
        if hh and hh not in out:
            out.append(hh)
    return out[:3]


def _apply_zh_glossary_inplace(segments: List[Segment], glossary: List[Dict[str, Any]] | None) -> int:
    if not segments or not glossary:
        return 0
    pairs: List[tuple[str, str]] = []
    for term in glossary:
        if not isinstance(term, dict):
            continue
        src = str((term or {}).get("src") or "").strip()
        tgt = str((term or {}).get("tgt") or "").strip()
        if not src or not tgt:
            continue
        if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", tgt):
            pairs.append((src, tgt))
    if not pairs:
        return 0
    hits = 0
    for seg in segments:
        z = str(getattr(seg, "text", "") or "")
        if not z:
            continue
        z2 = z
        for src, tgt in pairs:
            if src in z2:
                z2 = z2.replace(src, tgt)
        if z2 != z:
            hits += 1
            seg.text = z2
    return hits


def _segment_rule_reasons(seg: Segment, opts: AsrStage1Options) -> List[str]:
    t = str(getattr(seg, "text", "") or "")
    dur = max(float(seg.end) - float(seg.start), 0.0)
    compact = re.sub(r"\s+", "", clean_zh_text(t))
    reasons: List[str] = []
    if "\uFFFD" in t or "�" in t:
        reasons.append("乱码/异常字符")
    if re.search(r"([！？。；，])\1\1+", t):
        reasons.append("重复标点")
    if len(compact) <= 2 and dur >= 3.0:
        reasons.append("文本极短但时长较长")
    if len(compact) >= 28 and dur <= 0.8:
        reasons.append("文本较长但时长较短")
    reasons.extend(
        _detect_asr_dirty_sentence_signals(
            compact,
            lexicon_path=opts.lexicon_path,
            proper_nouns_path=opts.proper_nouns_path,
            same_pinyin_path=opts.same_pinyin_path,
            same_stroke_path=opts.same_stroke_path,
            project_confusions_path=opts.project_confusions_path,
        )
    )
    reasons.extend(_detect_repair_candidate_reason(compact))
    out: List[str] = []
    for r in reasons:
        rr = str(r or "").strip()
        if rr and rr not in out:
            out.append(rr)
    return out[:4]


def _severity_for_line(text: str, reasons: List[str], opts: AsrStage1Options) -> str:
    tier = _route_tier_for_line(
        text,
        reasons=reasons,
        duration_s=0.0,
        opts=opts,
    )
    if tier == "hard":
        return "high"
    if tier == "soft":
        return "medium"
    return "low"


def _line_badness(text: str, *, opts: AsrStage1Options) -> float:
    return _zh_repair_line_badness(
        text,
        lexicon_path=opts.lexicon_path,
        proper_nouns_path=opts.proper_nouns_path,
        same_pinyin_path=opts.same_pinyin_path,
        same_stroke_path=opts.same_stroke_path,
    )


def _build_stage1_line_states(
    segments: List[Segment],
    *,
    before_all: List[str],
    opts: AsrStage1Options,
    report: Dict[str, Any],
) -> List[Stage1LineState]:
    states: List[Stage1LineState] = []
    for idx, seg in enumerate(segments, start=1):
        before = before_all[idx - 1]
        after_glossary = str(seg.text or "")
        reasons = _segment_rule_reasons(seg, opts) if opts.badline_detect_enable else []
        local_candidate = ""
        local_hints: List[str] = []
        if opts.low_cost_clean_enable:
            local_candidate, local_hints = _pick_local_zh_repair(
                line=after_glossary,
                rule_reasons=reasons,
                same_pinyin_path=opts.same_pinyin_path,
                same_stroke_path=opts.same_stroke_path,
                project_confusions_path=opts.project_confusions_path,
                lexicon_path=opts.lexicon_path,
                proper_nouns_path=opts.proper_nouns_path,
            )
        working_text = str(after_glossary)
        if not local_hints:
            local_hints = _derive_stage1_micro_hints(working_text, reasons, opts=opts)
        route_tier = _route_tier_for_line(
            working_text,
            reasons=reasons,
            duration_s=max(float(seg.end) - float(seg.start), 0.0),
            opts=opts,
        )
        severity = "high" if route_tier == "hard" else ("medium" if route_tier == "soft" else "low")
        if route_tier in {"hard", "soft"}:
            report["summary"]["suspect_segments_total"] += 1
        if route_tier == "hard":
            report["summary"]["high_risk_segments_total"] += 1
        states.append(
            Stage1LineState(
                idx=idx,
                start=float(seg.start),
                end=float(seg.end),
                before=before,
                after_glossary=after_glossary,
                working_text=working_text,
                local_candidate=str(local_candidate or ""),
                rule_reasons=reasons,
                local_hints=local_hints,
                route_tier=route_tier,
                severity=severity,
                base_badness=_line_badness(after_glossary, opts=opts),
                working_badness=_line_badness(working_text, opts=opts),
            )
        )
    for i, state in enumerate(states):
        if i > 0:
            state.prev_text = states[i - 1].working_text.strip()
        if i + 1 < len(states):
            state.next_text = states[i + 1].working_text.strip()
    return states


def _candidate_similarity(base: str, opt: str) -> float:
    base_s = re.sub(r"\s+", "", clean_zh_text(str(base or "")))
    opt_s = re.sub(r"\s+", "", clean_zh_text(str(opt or "")))
    if not base_s and not opt_s:
        return 1.0
    if not base_s or not opt_s:
        return 0.0
    return float(SequenceMatcher(None, base_s, opt_s).ratio())


def _score_stage1_candidate(
    state: Stage1LineState,
    *,
    source: str,
    text: str,
    opts: AsrStage1Options,
) -> Stage1Candidate:
    candidate_text = str(text or "").strip()
    if not candidate_text:
        return Stage1Candidate(idx=state.idx, source=source, text="", badness=999.0, score=-1e9)
    badness = _line_badness(candidate_text, opts=opts)
    similarity = _candidate_similarity(state.after_glossary, candidate_text)
    evidence_kinds = set(_candidate_evidence_kinds(
        base=state.working_text,
        opt=candidate_text,
        source=source,
        rule_reasons=list(state.rule_reasons or []),
        local_hints=list(state.local_hints or []),
        opts=opts,
    ))
    badness_drop = float(state.base_badness - badness)
    edit_span_size = int(_candidate_edit_span_size(state.working_text, candidate_text))
    score = badness_drop * 4.0 + float(len(evidence_kinds)) * 1.3
    score -= max(0.0, float(edit_span_size) - 2.0) * 0.7
    score -= max(0.0, 0.94 - similarity) * 4.5
    if source == "base":
        score = 0.2
    else:
        score += 1.0
    return Stage1Candidate(
        idx=state.idx,
        source=source,
        text=candidate_text,
        badness=badness,
        score=score,
        badness_drop=badness_drop,
        edit_span_size=edit_span_size,
        evidence_kinds=sorted(evidence_kinds),
    )


def _select_stage1_candidate(
    state: Stage1LineState,
    *,
    opts: AsrStage1Options,
) -> Tuple[Stage1Candidate, List[Stage1Candidate]]:
    candidates: List[Stage1Candidate] = [
        _score_stage1_candidate(state, source="base", text=state.after_glossary, opts=opts),
    ]
    if state.local_candidate and state.local_candidate != state.after_glossary:
        candidates.append(_score_stage1_candidate(state, source="local", text=state.local_candidate, opts=opts))
    source_order = {"local": 0, "base": 1}
    candidates.sort(key=lambda cand: (-cand.score, source_order.get(cand.source, 9), len(cand.text)))
    selected = candidates[0]
    for cand in candidates:
        if cand is selected:
            cand.selected = True
    return selected, candidates


def apply_asr_stage1_repairs(
    segments: List[Segment],
    *,
    glossary: Optional[List[Dict[str, Any]]],
    opts: AsrStage1Options,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "options": _json_safe(asdict(opts)),
        "summary": {
            "segments": len(segments),
            "glossary_segments_changed": 0,
            "low_cost_segments_changed": 0,
            "suspect_segments_total": 0,
            "high_risk_segments_total": 0,
            "total_segments_changed": 0,
        },
        "items": [],
    }
    if not segments:
        return report

    before_all = [str(seg.text or "") for seg in segments]
    if opts.glossary_fix_enable:
        report["summary"]["glossary_segments_changed"] = _apply_zh_glossary_inplace(segments, glossary)
    states = _build_stage1_line_states(segments, before_all=before_all, opts=opts, report=report)
    items_meta: List[Dict[str, Any]] = []
    for state in states:
        selected, candidates = _select_stage1_candidate(
            state,
            opts=opts,
        )
        segments[state.idx - 1].text = selected.text
        local_changed = selected.source == "local" and selected.text != state.after_glossary
        if local_changed:
            report["summary"]["low_cost_segments_changed"] += 1
        items_meta.append(
            {
                "idx": state.idx,
                "start": state.start,
                "end": state.end,
                "before": state.before,
                "after_glossary": state.after_glossary,
                "after_local": state.working_text,
                "rule_reasons": list(state.rule_reasons or []),
                "local_hints": list(state.local_hints or []),
                "route_tier": state.route_tier,
                "severity": state.severity,
                "local_changed": local_changed,
                "selected_source": selected.source,
                "final": selected.text,
                "candidates": [
                    {
                        "source": cand.source,
                        "text": cand.text,
                        "badness": cand.badness,
                        "score": cand.score,
                        "badness_drop": cand.badness_drop,
                        "edit_span_size": cand.edit_span_size,
                        "evidence_kinds": list(cand.evidence_kinds or []),
                        "selected": cand.selected,
                    }
                    for cand in candidates
                ],
            }
        )
    changed_total = 0
    for item in items_meta:
        if str(item["before"] or "") != str(item["final"] or ""):
            changed_total += 1
    report["summary"]["total_segments_changed"] = changed_total
    report["items"] = items_meta

    if opts.output_dir:
        try:
            opts.output_dir.mkdir(parents=True, exist_ok=True)
            (opts.output_dir / "asr_stage1_report.json").write_text(
                json.dumps(_json_safe(report), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
    return report
