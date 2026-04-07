from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple


def clean_zh_text(text: str) -> str:
    """
    Normalize Chinese subtitle text to reduce MT instability:
    - collapse whitespace/newlines
    - normalize common punctuation repeats
    """
    s = (text or "").replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # normalize repeated punct
    s = re.sub(r"[，,]{2,}", "，", s)
    s = re.sub(r"[。\.]{2,}", "。", s)
    s = re.sub(r"[！!]{2,}", "！", s)
    s = re.sub(r"[？\?]{2,}", "？", s)
    return s


ZH_STOPWORDS = {
    "我们",
    "你们",
    "他们",
    "她们",
    "它们",
    "这个",
    "那个",
    "这里",
    "那里",
    "现在",
    "因为",
    "所以",
    "但是",
    "然后",
    "于是",
    "而且",
    "如果",
    "就会",
}


# 纯角色/身份类通用词：默认不作为专名保护。
ZH_GENERIC_ROLE_WORDS = {
    "王",
    "后",
    "皇帝",
    "皇",
    "公主",
    "王子",
    "女巫",
    "法师",
    "将军",
    "大人",
    "老师",
    "教练",
    "队长",
    "领袖",
    "男人",
    "女人",
    "老人",
    "新人",
    "粉丝",
    "队友",
    "大师",
}

ZH_PERSON_TITLE_SUFFIXES = {
    "先生",
    "女士",
    "博士",
    "教授",
    "校长",
    "院长",
    "局长",
    "主任",
    "书记",
    "总裁",
    "经理",
}

ZH_ALIAS_SUFFIXES = {
    "叔",
    "哥",
    "姐",
    "嫂",
    "爷",
    "妈",
    "总",
}

ZH_ORG_LOC_SUFFIXES = {
    "局",
    "司",
    "公司",
    "大学",
    "学院",
    "银行",
    "医院",
    "法院",
    "协会",
    "联盟",
    "部门",
    "大厦",
    "广场",
    "中学",
    "小学",
    "国",
    "城",
    "镇",
    "山",
    "河",
    "宫",
    "岛",
    "州",
    "省",
    "市",
    "县",
    "村",
    "堡",
}

ZH_BAD_INSIDE_CHARS = set("的了着在给为和与是就都也把被对向从到后前里上下将会让还而并跟于但")
ZH_NAME_LEFT_CONTEXT_CHARS = set("我的你他她它这那老小大舅叔姨伯姑父母哥姐弟妹")
ZH_BAD_LEADING_CHARS = set("各每某其该本此这那另不")
ZH_BAD_TRAILING_CHARS = set("他她它们的了着吗呢啊呀吧")


def is_role_like_zh(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    if t in ZH_GENERIC_ROLE_WORDS:
        return True
    # Bare short honorifics like "老师/主任" are usually titles, not stable names.
    if len(t) <= 2 and any(t.endswith(x) for x in ZH_PERSON_TITLE_SUFFIXES):
        return True
    return False


def _normalize_zh_only(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fff]", "", clean_zh_text(text))


def _contains_bad_inside_chars(candidate: str) -> bool:
    if not candidate:
        return True
    if candidate in ZH_STOPWORDS:
        return True
    if is_role_like_zh(candidate):
        return True
    if candidate[0] in ZH_BAD_LEADING_CHARS:
        return True
    if candidate[:2] in {"各个", "这个", "那个", "一个", "另一", "一些"}:
        return True
    if candidate[-1] in ZH_BAD_TRAILING_CHARS:
        return True
    if any(ch in ZH_BAD_INSIDE_CHARS for ch in candidate):
        return True
    if len(set(candidate)) == 1:
        return True
    return False


def _has_org_loc_suffix(candidate: str) -> bool:
    return any(candidate.endswith(suf) for suf in ZH_ORG_LOC_SUFFIXES)


def _has_person_title_suffix(candidate: str) -> bool:
    return any(candidate.endswith(suf) for suf in ZH_PERSON_TITLE_SUFFIXES)


def _has_alias_suffix(candidate: str) -> bool:
    return any(candidate.endswith(suf) for suf in ZH_ALIAS_SUFFIXES)


def _pick_name_core_before_suffix(text: str, suffix_start: int, *, min_chars: int, max_chars: int) -> str:
    i = int(suffix_start) - 1
    chars: List[str] = []
    while i >= 0 and len(chars) < max_chars:
        ch = text[i]
        if not re.match(r"[\u4e00-\u9fff]", ch):
            break
        chars.append(ch)
        i -= 1
    window = "".join(reversed(chars))
    if not window:
        return ""
    for n in range(min(max_chars, len(window)), max(min_chars, 1) - 1, -1):
        core = window[-n:]
        if not core:
            continue
        if core[0] in ZH_NAME_LEFT_CONTEXT_CHARS:
            continue
        if _contains_bad_inside_chars(core):
            continue
        return core
    return ""


def _add_signal(
    candidate: str,
    *,
    score_map: Dict[str, int],
    support_map: Dict[str, Set[int]],
    seg_idx: int,
    score: int,
) -> None:
    t = candidate.strip()
    if not t:
        return
    score_map[t] = score_map.get(t, 0) + int(score)
    support_map.setdefault(t, set()).add(int(seg_idx))


def _collect_pattern_candidates(
    text: str,
    *,
    seg_idx: int,
    min_len: int,
    max_len: int,
    score_map: Dict[str, int],
    support_map: Dict[str, Set[int]],
) -> None:
    # Quoted/book/paren spans are high-precision term/name hints.
    for it in re.finditer(r"[“\"《（(【\[]([\u4e00-\u9fff]{2,12})[”\"》）)\]】]", text):
        cand = it.group(1).strip()
        if not (min_len <= len(cand) <= max(max_len, min_len)):
            continue
        if _contains_bad_inside_chars(cand):
            continue
        _add_signal(cand, score_map=score_map, support_map=support_map, seg_idx=seg_idx, score=6)

    # Name + title, e.g. 杨紫静先生
    title_pat = re.compile(r"(先生|女士|博士|教授|校长|院长|局长|主任|书记|总裁|经理)")
    for it in title_pat.finditer(text):
        suffix = it.group(1)
        prefix = _pick_name_core_before_suffix(text, it.start(), min_chars=2, max_chars=4)
        cand = (prefix + suffix).strip()
        if not prefix:
            continue
        if not (min_len <= len(cand) <= max(max_len, min_len)):
            continue
        _add_signal(cand, score_map=score_map, support_map=support_map, seg_idx=seg_idx, score=6)

    # Organization / location suffixes, e.g. 中情局
    org_loc_pat = re.compile(
        r"([\u4e00-\u9fff]{1,8}(?:公司|大学|学院|银行|医院|法院|协会|联盟|部门|大厦|广场|中学|小学|国|城|镇|山|河|宫|岛|州|省|市|县|村|堡|局|司))"
    )
    for it in org_loc_pat.finditer(text):
        cand = it.group(1).strip()
        if not (min_len <= len(cand) <= max(max_len, min_len)):
            continue
        if _contains_bad_inside_chars(cand):
            continue
        _add_signal(cand, score_map=score_map, support_map=support_map, seg_idx=seg_idx, score=5)

    # Nickname / address style names, e.g. 龙叔
    alias_pat = re.compile(r"(叔|哥|姐|嫂|爷|妈|总)")
    for it in alias_pat.finditer(text):
        suffix = it.group(1)
        prefix = _pick_name_core_before_suffix(text, it.start(), min_chars=1, max_chars=2)
        cand = (prefix + suffix).strip()
        if not prefix:
            continue
        if not (min_len <= len(cand) <= max(max_len, min_len)):
            continue
        _add_signal(cand, score_map=score_map, support_map=support_map, seg_idx=seg_idx, score=5)


def _collect_repeated_ngram_candidates(
    text: str,
    *,
    seg_idx: int,
    min_len: int,
    max_len: int,
    score_map: Dict[str, int],
    support_map: Dict[str, Set[int]],
) -> None:
    zh = _normalize_zh_only(text)
    if not zh:
        return
    for n in range(max(2, int(min_len)), min(int(max_len), 8) + 1):
        for i in range(0, max(0, len(zh) - n + 1)):
            cand = zh[i : i + n]
            if _contains_bad_inside_chars(cand):
                continue
            # Repeated n-gram mining should stay conservative:
            # prefer 3-6 chars, but allow 2-char nicknames / org endings.
            if len(cand) < 3 and not (_has_alias_suffix(cand) or _has_org_loc_suffix(cand)):
                continue
            score = 2 if len(cand) >= 3 else 1
            if _has_org_loc_suffix(cand):
                score += 2
            if _has_person_title_suffix(cand):
                score += 2
            if _has_alias_suffix(cand):
                score += 2
            _add_signal(cand, score_map=score_map, support_map=support_map, seg_idx=seg_idx, score=score)


def extract_entity_candidates_from_segments(
    segments,
    *,
    min_len: int = 2,
    max_len: int = 6,
    min_freq: int = 2,
    max_items: int = 30,
) -> List[str]:
    """
    Heuristic candidate extraction for proper nouns / key terms from Chinese subtitles.
    We aim for *stability* and low false positives, not perfect NER.
    """
    # Practical high-precision strategy inspired by terminology mining best practices:
    # 1) combine multiple high-confidence candidate sources
    # 2) use cross-segment repetition, not just one-line hits
    # 3) keep candidate list small and targeted
    score_map: Dict[str, int] = {}
    support_map: Dict[str, Set[int]] = {}
    min_len = max(int(min_len or 2), 2)
    max_len = max(int(max_len or 6), min_len)
    min_freq = max(int(min_freq or 2), 2)
    max_items = min(int(max_items or 30), 8)
    for seg_idx, seg in enumerate(segments):
        s = clean_zh_text(getattr(seg, "text", "") or "")
        if not s:
            continue
        _collect_pattern_candidates(
            s,
            seg_idx=seg_idx,
            min_len=min_len,
            max_len=max_len,
            score_map=score_map,
            support_map=support_map,
        )
        _collect_repeated_ngram_candidates(
            s,
            seg_idx=seg_idx,
            min_len=min_len,
            max_len=max_len,
            score_map=score_map,
            support_map=support_map,
        )

    items: List[Tuple[str, int, int]] = []
    for cand, score in score_map.items():
        support = len(support_map.get(cand, set()))
        high_precision = _has_org_loc_suffix(cand) or _has_person_title_suffix(cand) or _has_alias_suffix(cand)
        if score < min_freq:
            continue
        if support < 2 and not high_precision:
            continue
        items.append((cand, score, support))
    # Prefer broader support first, then score, then longer forms.
    items.sort(key=lambda item: (item[2], item[1], len(item[0])), reverse=True)
    chosen: List[str] = []
    for k, _score, _support in items:
        # avoid picking substrings of already chosen
        if any(k in c or c in k for c in chosen):
            continue
        if is_role_like_zh(k):
            continue
        chosen.append(k)
        if len(chosen) >= max_items:
            break
    # replace longer first to reduce overlap issues
    chosen.sort(key=len, reverse=True)
    return chosen

