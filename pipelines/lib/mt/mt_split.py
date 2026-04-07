from __future__ import annotations

import re
from typing import List


END_STOPWORDS = {
    "and",
    "the",
    "of",
    "to",
    "in",
    "a",
    "an",
    "with",
    "from",
    "that",
    "which",
    "for",
    "as",
    "at",
    "on",
    "into",
    "onto",
    "over",
    "under",
    "by",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "can",
    "could",
    "should",
    "may",
    "might",
}

START_STOPWORDS = {
    "and",
    "but",
    "or",
    "because",
    "when",
    "so",
    "then",
    "also",
    "however",
    "therefore",
    "thus",
    "yet",
}

AUX_LIKE = {
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "can",
    "could",
    "should",
    "may",
    "might",
    "to",
}


def count_word_tokens(tokens: List[str]) -> int:
    return sum(1 for t in tokens if re.match(r"^[A-Za-z0-9]", t or ""))


def tokenize_en(text: str) -> List[str]:
    # Keep simple punctuation as separate tokens; collapse whitespace.
    s = re.sub(r"\s+", " ", (text or "")).strip()
    if not s:
        return []
    return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?|[.,;:?!]", s)


def join_en_tokens(tokens: List[str]) -> str:
    out = ""
    for tok in tokens:
        if tok in {".", ",", ";", ":", "?", "!"}:
            out = out.rstrip() + tok
        else:
            out += (" " if out else "") + tok
    return out.strip()


def piece_penalty(tokens: List[str]) -> int:
    if not tokens:
        return 10
    word_n = count_word_tokens(tokens)
    first = tokens[0].lower()
    last = tokens[-1].lower()
    p = 0
    if first == "," or first in START_STOPWORDS:
        p += 12
    if last in END_STOPWORDS:
        p += 12
    if last in {",", ";", ":"}:
        p += 2
    # Avoid one-word lines (especially function/aux words like "Had"/"But")
    if word_n <= 1:
        p += 10
        w = ""
        for t in tokens:
            if re.match(r"^[A-Za-z0-9]", t or ""):
                w = t.lower()
                break
        if w in START_STOPWORDS or w in END_STOPWORDS or w in AUX_LIKE:
            p += 10
    # Also discourage very short lines starting with conjunctions (e.g. "But anyone")
    if word_n <= 2 and first in START_STOPWORDS:
        p += 10
    return p


def adjust_alloc_grammar(tokens: List[str], alloc: List[int], window: int = 3) -> List[int]:
    """
    Improve split boundaries to avoid broken English like '... and the' at end of a line.
    Local boundary search only; O(n*window) and very cheap.
    """
    if not tokens or not alloc:
        return alloc
    # Convert alloc to boundary positions.
    bounds: List[int] = []
    pos = 0
    for n in alloc[:-1]:
        pos += max(0, n)
        bounds.append(pos)
    # adjust each boundary
    for bi, b in enumerate(bounds):
        best_b = b
        best_score = 1_000_000
        for shift in range(-window, window + 1):
            nb = b + shift
            if nb <= 0 or nb >= len(tokens):
                continue
            # ensure each piece has at least 1 token when possible
            left_start = 0 if bi == 0 else bounds[bi - 1]
            right_end = len(tokens) if bi == len(bounds) - 1 else bounds[bi + 1]
            if nb - left_start < 1:
                continue
            if right_end - nb < 1:
                continue
            left = tokens[left_start:nb]
            right = tokens[nb:right_end]
            score = piece_penalty(left) + piece_penalty(right)
            # prefer splitting at punctuation boundaries
            if left and left[-1] in {".", "!", "?"}:
                score -= 2
            if left and left[-1] in {",", ";", ":"}:
                score -= 1
            if right and right[0] == ",":
                score += 2
            if score < best_score:
                best_score = score
                best_b = nb
        bounds[bi] = best_b
    # rebuild alloc from bounds
    new_alloc: List[int] = []
    prev = 0
    for b in bounds:
        new_alloc.append(max(0, b - prev))
        prev = b
    new_alloc.append(max(0, len(tokens) - prev))
    return new_alloc


def split_translation_by_src_lengths(src_texts: List[str], en_text: str) -> List[str]:
    """
    Split an English translation back into N pieces (N=len(src_texts)) with grammar-aware boundaries.
    """
    n = len(src_texts)
    if n <= 1:
        return [en_text.strip()]
    tokens = tokenize_en(en_text)
    if not tokens:
        return [""] * n
    # Prefer allocating a few tokens per piece when we have enough tokens.
    # This avoids overly short lines like "But anyone" or "You're gonna get".
    if len(tokens) >= 3 * n:
        min_take = 3
    elif len(tokens) >= 2 * n:
        min_take = 2
    else:
        min_take = 1
    total_src_len = sum(max(len(t), 1) for t in src_texts) or 1
    alloc: List[int] = []
    remaining = len(tokens)
    for i, t in enumerate(src_texts):
        if i == n - 1:
            take = remaining
        else:
            take = max(min_take, round(len(tokens) * len(t) / total_src_len))
            take = min(take, max(0, remaining - (n - i - 1)))
        alloc.append(take)
        remaining -= take
    alloc = adjust_alloc_grammar(tokens, alloc, window=3)
    pieces: List[str] = []
    pos2 = 0
    for take in alloc:
        chunk = tokens[pos2 : pos2 + take]
        pos2 += take
        pieces.append(join_en_tokens(chunk))
    # Ensure length
    while len(pieces) < n:
        pieces.append("")
    if len(pieces) > n:
        pieces = pieces[:n]
    return pieces

