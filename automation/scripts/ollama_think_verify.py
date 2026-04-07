#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from typing import Any, Dict

import requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:11434", help="Ollama native base (no /v1)")
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--timeout-s", type=int, default=60)
    args = ap.parse_args()

    url = str(args.base).rstrip("/") + "/api/chat"
    body: Dict[str, Any] = {
        "model": str(args.model),
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": "Reply with the final answer only."},
            {"role": "user", "content": "Translate: 扎手把定时炸弹按在男人车上"},
        ],
        "options": {"temperature": 0.1, "num_predict": 64},
    }
    t0 = time.time()
    r = requests.post(url, json=body, timeout=int(args.timeout_s))
    elapsed = time.time() - t0
    ok = r.status_code == 200
    data = {}
    content = ""
    try:
        data = r.json() if ok else {}
        msg = (data.get("message") or {}) if isinstance(data, dict) else {}
        content = str(msg.get("content") or "")
    except Exception:
        content = ""

    has_think_tags = ("<think>" in content.lower()) or ("</think>" in content.lower())
    out = {
        "url": url,
        "status": r.status_code,
        "elapsed_s": round(elapsed, 3),
        "think_param_sent": True,
        "content_len": len(content),
        "content_preview": content[:120],
        "contains_think_tags": bool(has_think_tags),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

