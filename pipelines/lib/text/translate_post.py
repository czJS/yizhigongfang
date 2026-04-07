from __future__ import annotations

import re
from typing import List, Tuple


def clean_en(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\bIn order to\b", "To", s, flags=re.IGNORECASE)
    if s:
        s = s[0].upper() + s[1:]
    return s


def rule_polish(s: str) -> str:
    rules = [
        (r"\bIn order to\b", "To"),
        (r"\bTherefore\b", "So"),
        (r"\s+,", ","),
        (r",\s+", ", "),
        (r"\s+\.", "."),
        (r"\s+\?", "?"),
        (r"\s+!", "!"),
        (r"\s+'", "'"),
        (r"\bcan not\b", "cannot"),
        (r"\bdo not\b", "don't"),
        (r"\bis not\b", "isn't"),
    ]
    for pat, rep in rules:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    return s.strip()


def dedupe_repeats(text: str, ngram: int = 3, max_rep: int = 2) -> str:
    # 简单 n-gram 去重，防止 TTS 遇到重复长句
    words = (text or "").split()
    seen = []
    out: List[str] = []
    for w in words:
        out.append(w)
        if len(out) >= ngram:
            tail = tuple(out[-ngram:])
            seen.append(tail)
            # 如果最近 2*ngram 范围内重复超过阈值则移除
            recent = seen[-max_rep:]
            if len(recent) == max_rep and len(set(recent)) == 1:
                out = out[:-ngram]
                break
    return " ".join(out)


def dedupe_phrases(text: str, max_len: int = 6) -> str:
    # 对连续重复的 4~6 词短语做窗口去重
    words = (text or "").split()
    if len(words) <= max_len:
        return text
    out: List[str] = []
    i = 0
    while i < len(words):
        window = words[i : i + max_len]
        next_window = words[i + max_len : i + 2 * max_len]
        if window and next_window and window == next_window:
            out.extend(window)
            i += max_len * 2
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


def apply_replacements(text: str, rules: List[dict]) -> str:
    # 词典替换，按配置顺序应用，优先级高的写在前面
    out = text
    for item in rules:
        pat = item.get("pattern")
        rep = item.get("replace", "")
        flags = re.IGNORECASE if item.get("ignore_case", True) else 0
        if not pat:
            continue
        out = re.sub(pat, rep, out, flags=flags)
    return out


def protect_nums(text: str):
    used: List[Tuple[str, str]] = []

    def repl(m):
        token = f"__NUM{len(used)}__"
        used.append((token, m.group(0)))
        return token

    new_text = re.sub(r"\d+", repl, text)
    return new_text, used


def restore(text: str, used: List[Tuple[str, str]]):
    out = text
    for token, val in used:
        out = out.replace(token, val)
    return out



_CONSERVATIVE_CONTRACTION_RULES = [
    (r"\bI am\b", "I'm"),
    (r"\byou are\b", "you're"),
    (r"\bwe are\b", "we're"),
    (r"\bthey are\b", "they're"),
    (r"\bit is\b", "it's"),
    (r"\bthat is\b", "that's"),
    (r"\bthere is\b", "there's"),
    (r"\bdo not\b", "don't"),
    (r"\bdoes not\b", "doesn't"),
    (r"\bdid not\b", "didn't"),
    (r"\bwould not\b", "wouldn't"),
    (r"\bcould not\b", "couldn't"),
    (r"\bshould not\b", "shouldn't"),
    (r"\bwill not\b", "won't"),
    (r"\bis not\b", "isn't"),
    (r"\bare not\b", "aren't"),
    (r"\bwas not\b", "wasn't"),
    (r"\bwere not\b", "weren't"),
    (r"\bhave not\b", "haven't"),
    (r"\bhas not\b", "hasn't"),
    (r"\bhad not\b", "hadn't"),
]

_CONSERVATIVE_PHRASE_RULES = [
    (r"\band then\b", "then"),
    (r"\bso then\b", "then"),
    (r"\bwell,?\s+", ""),
]

_CONSERVATIVE_FILLER_RULES = [
    (r"^you know,?\s+", ""),
    (r"^I mean,?\s+", ""),
    (r"^actually,?\s+", ""),
    (r"^basically,?\s+", ""),
    (r",\s*you know,?", ""),
    (r",\s*I mean,?", ""),
    (r",\s*actually,?", ""),
    (r",\s*basically,?", ""),
]


def _reading_cps(text: str, duration_s: float) -> float:
    dur = max(float(duration_s or 0.0), 0.001)
    return len((text or '').replace('\n', ' ')) / dur


def _apply_regex_rules(text: str, rules: List[Tuple[str, str]]) -> str:
    out = text
    for pat, rep in rules:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    return clean_en(rule_polish(out))


def conservative_shorten_en(
    text: str,
    *,
    duration_s: float,
    max_cps: float,
    edge_margin_cps: float = 1.0,
    max_shrink_ratio: float = 0.08,
    max_shrink_chars: int = 12,
) -> str:
    base = clean_en(rule_polish(text or ""))
    if not base:
        return base
    current_cps = _reading_cps(base, duration_s)
    if current_cps <= float(max_cps):
        return base
    if current_cps > float(max_cps) + float(edge_margin_cps):
        return base
    if len(base) < 28:
        return base

    shrink_budget = max(6, min(int(max_shrink_chars), int(len(base) * float(max_shrink_ratio))))
    candidates = [base]

    contracted = _apply_regex_rules(base, _CONSERVATIVE_CONTRACTION_RULES)
    if contracted != candidates[-1]:
        candidates.append(contracted)

    phrased = _apply_regex_rules(candidates[-1], _CONSERVATIVE_PHRASE_RULES)
    if phrased != candidates[-1]:
        candidates.append(phrased)

    # Keep filler stripping away from number-heavy lines to avoid touching compact factual statements.
    if not re.search(r"\d|[%$€£¥]", base):
        filler = _apply_regex_rules(candidates[-1], _CONSERVATIVE_FILLER_RULES)
        if filler != candidates[-1]:
            candidates.append(filler)

    best = base
    best_cps = current_cps
    base_words = len(base.split())
    for cand in candidates[1:]:
        if not cand or cand == base:
            continue
        removed_chars = len(base) - len(cand)
        removed_words = max(base_words - len(cand.split()), 0)
        cand_cps = _reading_cps(cand, duration_s)
        if removed_chars <= 0:
            continue
        if removed_chars > shrink_budget:
            continue
        if removed_words > 3:
            continue
        if cand_cps > best_cps:
            continue
        best = cand
        best_cps = cand_cps
        if cand_cps <= float(max_cps):
            break
    return best
