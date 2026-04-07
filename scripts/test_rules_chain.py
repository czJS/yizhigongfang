#!/usr/bin/env python3
"""
Regression test: Rules Center + zh_phrase + MT integration (Docker dev).

What it tests (end-to-end via backend HTTP API):
1) ZH->ZH (asr_fixes): must rewrite Chinese BEFORE MT (including when resuming from MT).
2) EN->EN (en_fixes): must apply as cautious post-replacement on final EN subtitles (post-MT).
3) zh_phrase: reextract tool runs and produces non-empty artifacts.

Usage:
  python3 scripts/test_rules_chain.py --base http://127.0.0.1:5175 --video /app/outputs/uploads/1_.mp4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests


def _j(d: Any) -> str:
    return json.dumps(d, ensure_ascii=False, indent=2)


def http(method: str, url: str, *, json_body: Any | None = None, params: Dict[str, Any] | None = None, timeout: int = 60) -> requests.Response:
    return requests.request(method, url, json=json_body, params=params, timeout=timeout)


def get_json(url: str, *, params: Dict[str, Any] | None = None, timeout: int = 60) -> Any:
    r = http("GET", url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def put_json(url: str, payload: Any, *, timeout: int = 60) -> Any:
    r = http("PUT", url, json_body=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def post_json(url: str, payload: Any, *, timeout: int = 60) -> Any:
    r = http("POST", url, json_body=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def download_text(base: str, task_id: str, path: str) -> str:
    r = http("GET", f"{base}/api/tasks/{task_id}/download", params={"path": path}, timeout=60)
    r.raise_for_status()
    return r.text or ""


def wait_task_done(base: str, task_id: str, *, timeout_s: int = 1800, poll_s: float = 3.0) -> Dict[str, Any]:
    t0 = time.time()
    last = {}
    while True:
        st = get_json(f"{base}/api/tasks/{task_id}/status", timeout=30)
        last = st or {}
        state = str(last.get("state") or "")
        if state and state != "running":
            return last
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"task timeout after {timeout_s}s: {last}")
        time.sleep(poll_s)


def parse_srt_blocks(raw: str) -> List[Tuple[int, str]]:
    # returns (idx, text)
    s = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", s.strip())
    out: List[Tuple[int, str]] = []
    for b in blocks:
        lines = [ln.strip() for ln in b.splitlines()]
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0])
        except Exception:
            continue
        text = "\n".join([ln for ln in lines[2:] if ln.strip()]).strip()
        out.append((idx, text))
    out.sort(key=lambda x: x[0])
    return out


def pick_line_containing(srt_text: str, needle: str) -> Optional[Tuple[int, str]]:
    for idx, text in parse_srt_blocks(srt_text):
        if needle in (text or ""):
            return idx, text
    return None


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5175")
    ap.add_argument("--video", default="/app/outputs/uploads/1_.mp4")
    ap.add_argument("--mode", default="quality", choices=["quality", "lite", "online"])
    ap.add_argument("--preset", default=None)
    ap.add_argument("--timeout-s", type=int, default=2200)
    args = ap.parse_args()

    base = str(args.base).rstrip("/")

    # 0) Health
    health = get_json(f"{base}/api/health", timeout=15)
    print("[health] ok")

    # 1) Read & patch global ruleset: add three deterministic rules.
    rs = get_json(f"{base}/api/rulesets/global", timeout=30)
    if not isinstance(rs, dict):
        raise RuntimeError("global ruleset is not an object")

    # Keep original doc so we can restore after test.
    rs_orig = dict(rs)

    rs2 = dict(rs)
    rs2.setdefault("asr_fixes", [])
    rs2.setdefault("en_fixes", [])
    rs2.setdefault("settings", {})

    # Remove previous test rules (id prefix ygf_test_)
    def _keep(arr: Any) -> List[Dict[str, Any]]:
        out = []
        for it in (arr or []):
            if not isinstance(it, dict):
                continue
            if str(it.get("id") or "").startswith("ygf_test_"):
                continue
            out.append(it)
        return out

    rs2["asr_fixes"] = _keep(rs2.get("asr_fixes"))
    rs2["en_fixes"] = _keep(rs2.get("en_fixes"))

    # Test rules
    # ZH->ZH: force "智窗" to "痔疮"
    rs2["asr_fixes"].append({"id": "ygf_test_asr_1", "src": "智窗", "tgt": "痔疮", "note": "test", "scope": "global"})
    # EN->EN: normalize "Mosquito" -> "mosquito"
    rs2["en_fixes"].append({"id": "ygf_test_en_1", "src": "Mosquito", "tgt": "mosquito", "note": "test", "scope": "global"})

    saved = put_json(f"{base}/api/rulesets/global", rs2, timeout=60)
    print("[ruleset] saved global ruleset with test rules")

    # 2) Start a new task using the provided video (ensures we exercise materialization of .ygf_rules).
    # Force a pause after zh_polish so we can validate zh_phrase artifacts without them
    # being overwritten by a later resume run with review_enabled=off.
    payload = {"video": args.video, "params": {"review_enabled": True, "stop_after": "zh_polish"}, "mode": args.mode}
    if args.preset:
        payload["preset"] = args.preset
    start = post_json(f"{base}/api/tasks/start", payload, timeout=60)
    task_id = str(start.get("task_id") or "")
    if not task_id:
        raise RuntimeError(f"failed to start task: {start}")
    print(f"[task] started {task_id}")

    # 3) Wait until it pauses (zh_gate or stop_after barrier) or completes.
    st = wait_task_done(base, task_id, timeout_s=args.timeout_s, poll_s=3.0)
    print(f"[task] state={st.get('state')} stage={st.get('stage')} msg={(st.get('message') or '')[:120]}")

    # 4) Run reextract zh phrases (refresh suspects/phrases)
    zh_phrase_items_n = 0
    try:
        rex = post_json(f"{base}/api/tasks/{task_id}/review/reextract_zh_phrases", {}, timeout=15 * 60)
        # Prefer counting the tool response to avoid later overwrites by resume runs.
        if isinstance(rex, dict):
            zh_phrase_items_n = int(rex.get("items_n") or rex.get("items") or 0) if isinstance(rex.get("items_n"), (int, float, str)) else 0
        phrases_raw = download_text(base, task_id, "chs.phrases.json")
        phrases = json.loads(phrases_raw or "{}")
        phrases_n = len((phrases.get("items") or []) if isinstance(phrases, dict) else [])
        print(f"[zh_phrase] phrases items={phrases_n}")
        zh_phrase_items_n = max(zh_phrase_items_n, phrases_n)
    except Exception as exc:
        raise RuntimeError(f"reextract failed: {exc}")

    # 5) Resume from MT (this triggers translation + en_fixes post replace)
    _ = post_json(f"{base}/api/tasks/{task_id}/resume", {"resume_from": "mt", "params": {"review_enabled": False, "stop_after": ""}}, timeout=60)
    st2 = wait_task_done(base, task_id, timeout_s=args.timeout_s, poll_s=3.0)
    print(f"[task] after resume state={st2.get('state')} msg={(st2.get('message') or '')[:120]}")
    if str(st2.get("state")) != "completed":
        raise RuntimeError(f"task did not complete: {st2}")

    chs_srt = download_text(base, task_id, "chs.srt")
    eng_srt = download_text(base, task_id, "eng.srt")

    checks: List[CheckResult] = []

    # Check 1: ZH->ZH rule applied (chs.srt contains 痔疮 when there is 智窗)
    if "智窗" in chs_srt:
        checks.append(CheckResult("asr_fixes(ZH->ZH) applied", False, "chs.srt still contains 智窗"))
    elif "痔疮" in chs_srt:
        checks.append(CheckResult("asr_fixes(ZH->ZH) applied", True, "found 痔疮 in chs.srt"))
    else:
        checks.append(CheckResult("asr_fixes(ZH->ZH) applied", False, "neither 智窗 nor 痔疮 found; video may not contain the token"))

    # Check 2: EN->EN fix applied (Mosquito -> mosquito)
    if "Mosquito" in eng_srt:
        checks.append(CheckResult("en_fixes(EN->EN) applied", False, "eng.srt still contains 'Mosquito'"))
    else:
        # Only meaningful if mosquito appears at all.
        if "mosquito" in eng_srt.lower():
            checks.append(CheckResult("en_fixes(EN->EN) applied", True, "no 'Mosquito'; 'mosquito' present"))
        else:
            checks.append(CheckResult("en_fixes(EN->EN) applied", False, "no mosquito token found; cannot verify on this clip"))

    # Check 3: zh_phrase produced items (from the reextract step, not the resume run)
    checks.append(CheckResult("zh_phrase reextract non-empty", zh_phrase_items_n > 0, f"items={zh_phrase_items_n}"))

    print("\n=== CHECKS ===")
    ok_all = True
    for c in checks:
        ok_all = ok_all and c.ok
        print(f"- {'PASS' if c.ok else 'FAIL'} {c.name}: {c.detail}")

    # Show helpful excerpts for manual inspection
    print("\n=== EXCERPTS ===")
    hit = pick_line_containing(chs_srt, "痔疮") or pick_line_containing(chs_srt, "智窗")
    if hit:
        print(f"[chs] #{hit[0]} {hit[1]}")
    hit2 = pick_line_containing(chs_srt, "痔疮")
    if hit2:
        print(f"[chs] #{hit2[0]} {hit2[1]}")
    print("\n[result] " + ("PASS" if ok_all else "FAIL"))
    # Restore original global ruleset (best-effort).
    try:
        put_json(f"{base}/api/rulesets/global", rs_orig, timeout=60)
        print("[ruleset] restored original global ruleset")
    except Exception as exc:
        print(f"[warn] failed to restore global ruleset: {exc}")
    # Cleanup: leave task artifacts for debugging; user can cleanup later.
    return 0 if ok_all else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
