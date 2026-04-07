from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional


def load_replacements(path: Optional[Path]) -> List[dict]:
    """加载词典替换规则（JSON），每条包含 pattern、replace、ignore_case。"""
    if not path:
        return []
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "[]")
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    except Exception:
        return []
    return []


def build_languagetool():
    """LanguageTool 规则纠错（Grammar/Punctuation/Typo），可选启用。"""
    try:
        import language_tool_python  # type: ignore
    except ImportError as exc:
        raise SystemExit("LanguageTool not installed. Please `pip install language-tool-python`.") from exc

    tool = language_tool_python.LanguageTool("en-US")
    allowed = {"Grammar", "Punctuation", "Typo"}

    def lt_fn(text: str) -> str:
        matches = [m for m in tool.check(text) if m.ruleIssueType in allowed]
        corrected = language_tool_python.utils.correct(text, matches)
        return corrected

    return lt_fn

