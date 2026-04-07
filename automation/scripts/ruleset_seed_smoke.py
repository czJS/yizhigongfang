#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.ruleset_store import load_ruleset, merge_rulesets, ruleset_to_asr_dict


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    seed_path = REPO_ROOT / "assets" / "rules" / "ruleset.seed.json"
    global_path = REPO_ROOT / "assets" / "rules" / "ruleset.global.json"
    seed = load_ruleset(seed_path)
    global_doc = load_ruleset(global_path)
    merged = merge_rulesets(seed, global_doc)
    asr_map = ruleset_to_asr_dict(merged)

    expected = {
        "智床": "痔疮",
        "神龙百尾": "神龙摆尾",
        "正面硬钢": "正面硬刚",
    }
    for src, tgt in expected.items():
        _assert(asr_map.get(src) == tgt, f"missing seed fix: {src}->{tgt}")

    print(
        json.dumps(
            {
                "status": "ok",
                "seed_items": len(seed.get("asr_fixes") or []),
                "merged_items": len(merged.get("asr_fixes") or []),
                "checked": expected,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
