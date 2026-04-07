#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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
    max_tokens: int = 320,
    timeout_s: int = 120,
) -> str:
    base = endpoint.rstrip("/")
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


def build_rewrite_messages(*, ref_en: str, zh: str = "", topic: str = "") -> List[Dict[str, str]]:
    sys = (
        "You are a professional English subtitle editor.\n"
        "Rewrite the given English into fluent, natural, conversational subtitle English.\n"
        "You MAY rewrite for naturalness, but MUST preserve meaning and MUST NOT add new facts.\n"
        "Keep it concise and subtitle-friendly.\n"
        "Return ONLY the rewritten English subtitle line(s)."
    )
    if topic.strip():
        sys += f"\nContext/style: {topic.strip()}"
    user = "Rewrite this English as natural subtitles:\n\nEN:\n" + (ref_en or "").strip() + "\n"
    if zh.strip():
        user += "\nChinese context (for meaning check only, do NOT translate it):\n" + zh.strip() + "\n"
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def _srt_time(i: int) -> Tuple[str, str]:
    # Dummy timestamps (parser in build_fluency_eval_set ignores timings; this is for SRT validity).
    start_ms = (i - 1) * 1000
    end_ms = start_ms + 900
    def _fmt(ms: int) -> str:
        s = ms // 1000
        hh = s // 3600
        mm = (s % 3600) // 60
        ss = s % 60
        mmm = ms % 1000
        return f"{hh:02d}:{mm:02d}:{ss:02d},{mmm:03d}"
    return _fmt(start_ms), _fmt(end_ms)


def write_srt(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parts: List[str] = []
    for i, t in enumerate(lines, 1):
        st, et = _srt_time(i)
        parts.append(str(i))
        parts.append(f"{st} --> {et}")
        parts.append((t or "").strip() or "…")
        parts.append("")  # blank line
    path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build a synthetic 'golden' English subtitle set by rewriting public ref_en into more conversational subtitle English."
    )
    ap.add_argument("--cases", type=Path, required=True, help="Input cases.jsonl (must include id/ref_en; zh optional)")
    ap.add_argument("--n", type=int, default=200, help="How many samples to generate (default: 200)")
    ap.add_argument("--seed", type=int, default=42, help="Sampling seed")
    ap.add_argument("--out-jsonl", type=Path, required=True, help="Output JSONL: {id, zh, ref_en, gold_en, meta}")
    ap.add_argument("--out-eng-srt", type=Path, default=None, help="Optional output eng.srt (dummy timings)")
    ap.add_argument("--out-chs-srt", type=Path, default=None, help="Optional output chs.srt (dummy timings)")
    ap.add_argument("--resume", action="store_true", help="Resume from existing --out-jsonl (skip completed ids)")
    ap.add_argument("--endpoint", type=str, default=os.getenv("LLM_ENDPOINT", ""), help="OpenAI-compatible endpoint (env: LLM_ENDPOINT)")
    ap.add_argument("--model", type=str, default=os.getenv("LLM_MODEL", ""), help="Model (env: LLM_MODEL)")
    ap.add_argument("--api-key", type=str, default=os.getenv("LLM_API_KEY", ""), help="API key (env: LLM_API_KEY)")
    ap.add_argument("--topic", type=str, default="Narration, conversational subtitle English", help="Style hint")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=320)
    ap.add_argument("--sleep-s", type=float, default=0.0)
    args = ap.parse_args()

    if not args.endpoint or not args.model:
        raise SystemExit("Missing --endpoint/--model (or env LLM_ENDPOINT/LLM_MODEL)")

    items = _read_jsonl(Path(args.cases))
    # filter valid
    cand = []
    for it in items:
        cid = str(it.get("id") or "").strip()
        ref_en = str(it.get("ref_en") or "").strip()
        if not cid or not ref_en:
            continue
        cand.append(it)
    if not cand:
        raise SystemExit(f"No valid samples found in {args.cases}")

    rng = random.Random(int(args.seed))
    rng.shuffle(cand)
    cand = cand[: max(1, int(args.n))]

    done_ids: set[str] = set()
    if args.resume and Path(args.out_jsonl).exists():
        try:
            for ln in Path(args.out_jsonl).read_text(encoding="utf-8", errors="ignore").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    cid = str(obj.get("id") or "").strip()
                    gold = str(obj.get("gold_en") or "").strip()
                    if cid and gold:
                        done_ids.add(cid)
        except Exception:
            done_ids = set()
            args.resume = False

    Path(args.out_jsonl).parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if (args.resume and Path(args.out_jsonl).exists()) else "w"
    t0 = time.time()
    wrote = 0
    with Path(args.out_jsonl).open(mode, encoding="utf-8") as f:
        for idx, it in enumerate(cand, 1):
            cid = str(it.get("id") or "").strip()
            if not cid:
                continue
            if args.resume and cid in done_ids:
                continue
            zh = str(it.get("zh") or "")
            ref_en = str(it.get("ref_en") or "")
            msgs = build_rewrite_messages(ref_en=ref_en, zh=zh, topic=str(args.topic or ""))
            gold = _openai_chat(
                endpoint=str(args.endpoint),
                model=str(args.model),
                api_key=str(args.api_key or ""),
                messages=msgs,
                temperature=float(args.temperature),
                max_tokens=int(args.max_tokens),
            ).strip()
            if not gold:
                gold = ref_en.strip()
            out_obj = {
                "id": cid,
                "zh": zh,
                "ref_en": ref_en,
                "gold_en": gold,
                "meta": {
                    "source_cases": str(Path(args.cases)),
                    "seed": int(args.seed),
                    "topic": str(args.topic or ""),
                    "temperature": float(args.temperature),
                    "model": str(args.model),
                },
            }
            f.write(json.dumps(out_obj, ensure_ascii=False) + "\n")
            f.flush()
            wrote += 1
            if idx % 10 == 0:
                dt = time.time() - t0
                rate = wrote / max(1e-9, dt)
                print(f"[progress] wrote={wrote} / target={len(cand)} ({rate:.2f} items/s)", flush=True)
            if args.sleep_s and args.sleep_s > 0:
                time.sleep(float(args.sleep_s))

    # Build SRTs from final JSONL (in current file order)
    rows = _read_jsonl(Path(args.out_jsonl))
    rows = [r for r in rows if isinstance(r, dict) and str(r.get("id") or "").strip()]
    gold_lines = [str(r.get("gold_en") or "").strip() for r in rows]
    zh_lines = [str(r.get("zh") or "").strip() for r in rows]

    if args.out_eng_srt:
        write_srt(Path(args.out_eng_srt), gold_lines)
    if args.out_chs_srt:
        write_srt(Path(args.out_chs_srt), zh_lines)

    print(f"[ok] wrote {args.out_jsonl} (new={wrote}, total={len(rows)})")
    if args.out_eng_srt:
        print(f"[ok] wrote {args.out_eng_srt} (n={len(gold_lines)})")
    if args.out_chs_srt:
        print(f"[ok] wrote {args.out_chs_srt} (n={len(zh_lines)})")


if __name__ == "__main__":
    main()





