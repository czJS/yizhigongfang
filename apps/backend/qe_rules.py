from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


_RE_NUM = re.compile(r"\d+(?:\.\d+)?")
_RE_FRAC = re.compile(r"\b\d+\s*/\s*\d+\b")
_RE_PERCENT_WORD = re.compile(r"\b(\d+(?:\.\d+)?)\s*(percent|pct)\b", flags=re.IGNORECASE)


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def extract_number_tokens(text: str) -> List[str]:
    """
    Extract normalized number tokens from text.
    - Captures integers/decimals and fractions and normalizes "N percent" to "N%".
    - This is language-agnostic and intentionally conservative (digits only).
    """
    t = _norm_space(text)
    if not t:
        return []
    out: List[str] = []
    # fractions first
    for m in _RE_FRAC.finditer(t):
        out.append(m.group(0).replace(" ", ""))
    # normalize "N percent" -> "N%"
    for m in _RE_PERCENT_WORD.finditer(t):
        out.append(f"{m.group(1)}%")
    # explicit percent sign: keep as "N%"
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", t):
        out.append(f"{m.group(1)}%")
    # raw numbers
    for m in _RE_NUM.finditer(t):
        out.append(m.group(0))
    # de-dup while keeping order
    seen = set()
    out2: List[str] = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        out2.append(x)
    return out2


def number_mismatch(zh: str, en: str) -> Dict[str, Any]:
    """
    Return a structured number consistency signal. Conservative:
    - Only triggers mismatch when ZH has numbers but EN misses some of them.
    - EN extra numbers are recorded but do NOT auto-trigger mismatch (subtitle contexts can be tricky).
    """
    zh_nums = extract_number_tokens(zh)
    en_nums = extract_number_tokens(en)
    zh_set = set(zh_nums)
    en_set = set(en_nums)
    missing = sorted(list(zh_set - en_set))
    extra = sorted(list(en_set - zh_set))
    mismatch = bool(zh_set) and bool(missing)
    return {"mismatch": mismatch, "zh_nums": zh_nums, "en_nums": en_nums, "missing": missing, "extra": extra}


_ZH_FIRST_PERSON = re.compile(r"(我|我们|咱们|咱|俺|本人|在下)")
_EN_FIRST_PERSON = re.compile(r"\b(i|me|my|mine|we|us|our|ours)\b", flags=re.IGNORECASE)


def has_first_person_zh(zh: str) -> bool:
    return bool(_ZH_FIRST_PERSON.search(str(zh or "")))


def has_first_person_en(en: str) -> bool:
    return bool(_EN_FIRST_PERSON.search(str(en or "")))


def person_drift_suspect(zh: str, en: str) -> bool:
    """
    Best-effort drift detector:
    - Only suspect when EN contains first-person but ZH does NOT.
    - If ZH itself is first-person, EN first-person is allowed.
    """
    return (not has_first_person_zh(zh)) and has_first_person_en(en)


def dangling_en_suspect(en: str) -> bool:
    low = str(en or "").strip().lower()
    if not low:
        return True
    if low.endswith((",", ";", ":")):
        return True
    if re.search(r"\b(and|or|but|to|of|with|for)$", low):
        return True
    if re.search(r"\bof\s*[\.\!\?]$", low):
        return True
    return False


def artifact_en_suspect(en: str) -> bool:
    e = str(en or "")
    low = e.lower()
    if "@@" in e:
        return True
    if re.search(r"\bthe the\b", low):
        return True
    if re.search(r"\bready to (him|her|them|it|us|me|you)\b", low):
        return True
    if "now ruled" in low and "is now ruled" not in low and "was now ruled" not in low:
        return True
    return False


def too_short_vs_zh_suspect(zh: str, en: str, *, zh_min: int = 14, en_max: int = 12) -> bool:
    z = str(zh or "").strip()
    e = str(en or "").strip()
    if len(z) >= int(zh_min) and len(e) <= int(en_max):
        return True
    return False


def glossary_missing_suspect(zh: str, en: str, glossary: Optional[List[Dict[str, Any]]]) -> Tuple[bool, List[str]]:
    """
    Detect likely missing terminology translations.
    Returns (suspect, missed_src_terms[]). Conservative: only checks when src appears in ZH.
    """
    if not glossary:
        return False, []
    z = str(zh or "")
    low = str(en or "").lower()
    missed: List[str] = []
    for term in glossary:
        if not isinstance(term, dict):
            continue
        src = str(term.get("src") or "").strip()
        tgt = str(term.get("tgt") or "").strip()
        if not src or not tgt:
            continue
        if src not in z:
            continue
        if tgt.lower() in low:
            continue
        aliases = [str(x).strip().lower() for x in (term.get("aliases") or []) if str(x).strip()]
        if aliases and any(a in low for a in aliases):
            continue
        missed.append(src)
    return (len(missed) > 0), missed


def build_suspect_reasons(
    *,
    idx: int,
    zh: str,
    en: str,
    prev_en: str = "",
    prev_zh: str = "",
    glossary: Optional[List[Dict[str, Any]]] = None,
    sim: Optional[float] = None,
    sim_threshold: Optional[float] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """
    Produce a list of reason tags and a compact debug payload.
    Reason tags are stable identifiers for aggregation / regression.
    """
    reasons: List[str] = []
    debug: Dict[str, Any] = {"idx": int(idx), "sim": sim}
    z = str(zh or "").strip()
    e = str(en or "").strip()
    if not z or not e:
        reasons.append("empty")
        return reasons, debug

    if artifact_en_suspect(e):
        reasons.append("artifact")
    if dangling_en_suspect(e):
        reasons.append("dangling")
    if too_short_vs_zh_suspect(z, e):
        reasons.append("too_short")

    nm = number_mismatch(z, e)
    debug["numbers"] = nm
    if nm.get("mismatch"):
        reasons.append("number_mismatch")

    if person_drift_suspect(z, e):
        reasons.append("person_drift")

    if prev_en and e == prev_en and z != (prev_zh or ""):
        reasons.append("repeat_en")

    miss_ok, missed = glossary_missing_suspect(z, e, glossary)
    if miss_ok:
        reasons.append("glossary_missing")
        debug["glossary_missing_src"] = missed[:6]

    if sim is not None and sim_threshold is not None and sim < float(sim_threshold):
        reasons.append("low_sim")

    return reasons, debug


def build_topk(items: List[Dict[str, Any]], *, key: str, topk: int = 20) -> List[Dict[str, Any]]:
    """
    Select topK items by a numeric key (ascending; e.g. low similarity).
    Items are shallow-copied and truncated for report display.
    """
    topk = max(1, int(topk or 20))
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for it in items:
        try:
            v = float(it.get(key))  # type: ignore[arg-type]
        except Exception:
            continue
        scored.append((v, it))
    scored.sort(key=lambda x: x[0])
    out: List[Dict[str, Any]] = []
    for v, it in scored[:topk]:
        out.append(
            {
                "idx": it.get("idx"),
                key: v,
                "reasons": it.get("reasons", []),
                "zh": (it.get("zh") or "")[:120],
                "en": (it.get("en") or "")[:120],
                "fixed": (it.get("fixed") or "")[:120],
                "applied_by": it.get("applied_by"),
            }
        )
    return out


def aggregate_qe_report_v2(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate report-level histograms and Top-K lists.
    Pure function for testing.
    """
    reasons_hist: Dict[str, int] = {}
    err_hist: Dict[str, int] = {}
    changed = 0
    for it in items or []:
        if bool(it.get("applied")):
            changed += 1
        for r in it.get("reasons", []) or []:
            rr = str(r).strip()
            if not rr:
                continue
            reasons_hist[rr] = int(reasons_hist.get(rr, 0) or 0) + 1
        for e in it.get("error_type", []) or []:
            ee = str(e).strip()
            if not ee:
                continue
            err_hist[ee] = int(err_hist.get(ee, 0) or 0) + 1

    low_sim_topk = build_topk(items or [], key="sim", topk=20)
    return {
        "reasons_histogram": dict(sorted(reasons_hist.items(), key=lambda kv: (-kv[1], kv[0]))),
        "error_type_histogram": dict(sorted(err_hist.items(), key=lambda kv: (-kv[1], kv[0]))),
        "changed": int(changed),
        "low_sim_topk": low_sim_topk,
    }


