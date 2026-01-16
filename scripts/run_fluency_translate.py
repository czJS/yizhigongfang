#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _read_jsonl(p: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        obj = json.loads(ln)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout_s: int = 120) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    obj = json.loads(raw or "{}")
    return obj if isinstance(obj, dict) else {}


def _openai_chat(
    endpoint: str,
    model: str,
    api_key: str,
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 512,
    timeout_s: int = 120,
) -> str:
    base = endpoint.rstrip("/")
    # accept both ".../v1" and "..." as endpoint base
    if base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"
    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    obj = _post_json(url, payload, headers=headers, timeout_s=timeout_s)
    try:
        return str(obj["choices"][0]["message"]["content"])
    except Exception:
        return ""


def _safe_json_extract(s: str) -> Optional[Dict[str, Any]]:
    """
    Very small helper: try parse full string as JSON; if fails, try parse the first {...} block.
    """
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # naive bracket extraction
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(s[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def build_messages(
    zh: str,
    *,
    topic: str = "",
    enable_tra: bool = False,
    enable_selfcheck: bool = False,
) -> List[Dict[str, str]]:
    sys = (
        "You are a professional subtitle translator.\n"
        "Goal: produce fluent, natural, conversational English subtitles.\n"
        "You may rewrite for naturalness, but do NOT add new facts.\n"
        "Keep it concise and subtitle-friendly."
    )
    if topic.strip():
        sys += f"\nContext/style: {topic.strip()}"

    if enable_tra:
        user = (
            "Translate the Chinese subtitle into natural conversational English subtitle.\n"
            "Return STRICT JSON with keys: faithful, issues, final.\n"
            "- faithful: literal but correct translation\n"
            "- issues: short bullet list of improvements for subtitle naturalness\n"
            "- final: the final subtitle English (natural, concise)\n"
            f"\nChinese:\n{zh}\n"
        )
    else:
        user = (
            "Translate the Chinese subtitle into natural conversational English subtitle.\n"
            "Return ONLY the final English subtitle.\n"
            f"\nChinese:\n{zh}\n"
        )
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": user}]
    if enable_selfcheck and not enable_tra:
        # two-pass: translate then selfcheck
        msgs.append(
            {
                "role": "user",
                "content": (
                    "Now self-check and rewrite the English to sound more natural as subtitles. "
                    "Keep meaning, avoid adding info. Return ONLY the improved English."
                ),
            }
        )
    return msgs


def main() -> None:
    ap = argparse.ArgumentParser(description="Run English fluency translation on cases.jsonl and write preds.jsonl (supports single/multi toggle).")
    ap.add_argument("--cases", type=Path, required=True, help="cases.jsonl path")
    ap.add_argument("--out", type=Path, required=True, help="output preds.jsonl path")
    ap.add_argument("--resume", action="store_true", help="Resume from existing --out (append, skip completed ids)")
    ap.add_argument("--endpoint", type=str, default=os.getenv("LLM_ENDPOINT", ""), help="OpenAI-compatible endpoint (env: LLM_ENDPOINT)")
    ap.add_argument("--model", type=str, default=os.getenv("LLM_MODEL", ""), help="Model (env: LLM_MODEL)")
    ap.add_argument("--api-key", type=str, default=os.getenv("LLM_API_KEY", ""), help="API key (env: LLM_API_KEY)")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--sleep-s", type=float, default=0.0, help="Sleep between requests (rate limit)")
    ap.add_argument("--enable-tra", action="store_true", help="Enable TRA-style structured rewrite (single toggle)")
    ap.add_argument("--enable-selfcheck", action="store_true", help="Enable self-check rewrite (single toggle)")
    ap.add_argument("--topic", type=str, default="", help="Optional topic/style hint")
    args = ap.parse_args()

    if not args.endpoint or not args.model:
        raise SystemExit("Missing --endpoint/--model (or env LLM_ENDPOINT/LLM_MODEL)")

    rows = _read_jsonl(args.cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    done_ids: set[str] = set()
    if args.resume and args.out.exists():
        try:
            for ln in args.out.read_text(encoding="utf-8", errors="ignore").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    cid = str(obj.get("id") or "").strip()
                    pred = str(obj.get("pred_en") or "")
                    err = str(((obj.get("meta") or {}) if isinstance(obj.get("meta"), dict) else {}).get("error") or "")
                    if cid and (pred.strip() or err.strip()):
                        done_ids.add(cid)
        except Exception:
            # resume is best-effort; fall back to start-over if parsing fails
            done_ids = set()
            args.resume = False

    done = 0
    t0 = time.time()
    mode = "a" if (args.resume and args.out.exists()) else "w"
    if args.resume and args.out.exists():
        print(f"[resume] existing={args.out} done_ids={len(done_ids)} total_cases={len(rows)}", flush=True)
    with args.out.open(mode, encoding="utf-8") as f:
        for idx, r in enumerate(rows, 1):
            cid = str(r.get("id") or "").strip()
            zh = str(r.get("zh") or "")
            if not cid:
                continue
            if args.resume and cid in done_ids:
                continue
            msgs = build_messages(
                zh,
                topic=str(args.topic or ""),
                enable_tra=bool(args.enable_tra),
                enable_selfcheck=bool(args.enable_selfcheck),
            )
            try:
                content = _openai_chat(
                    endpoint=str(args.endpoint),
                    model=str(args.model),
                    api_key=str(args.api_key or ""),
                    messages=msgs,
                    temperature=float(args.temperature),
                    max_tokens=int(args.max_tokens),
                )
            except urllib.error.HTTPError as e:
                content = ""
                try:
                    content = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                pred = ""
                err = f"http_error:{getattr(e, 'code', '')}"
            except Exception as e:
                pred = ""
                err = f"error:{e}"
            else:
                err = ""
                pred = content.strip()
                if args.enable_tra:
                    obj = _safe_json_extract(content)
                    if isinstance(obj, dict) and str(obj.get("final") or "").strip():
                        pred = str(obj.get("final") or "").strip()
            out_obj = {
                "id": cid,
                "pred_en": pred,
                "meta": {
                    "enable_tra": bool(args.enable_tra),
                    "enable_selfcheck": bool(args.enable_selfcheck),
                    "topic": str(args.topic or ""),
                    "error": err,
                },
            }
            f.write(json.dumps(out_obj, ensure_ascii=False) + "\n")
            f.flush()
            done += 1
            if idx % 10 == 0:
                dt = time.time() - t0
                rate = done / max(1e-9, dt)
                print(f"[progress] {done}/{len(rows)} ({rate:.2f} items/s)", flush=True)
            if args.sleep_s and args.sleep_s > 0:
                time.sleep(float(args.sleep_s))

    dt = time.time() - t0
    print(f"[ok] wrote {args.out} (n={done}, {dt:.1f}s)")


if __name__ == "__main__":
    main()


