from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Literal, Optional


_WS_RE = re.compile(r"\s+")


def normalize_en_line(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").replace("\n", " ")).strip()


def estimate_en_seconds(text: str, *, wps: float = 2.6) -> float:
    """
    Extremely lightweight speaking-time estimator for English (offline, deterministic).
    - base: words / wps
    - pauses: commas/semicolons/colons add 0.12s; sentence end punctuation adds 0.22s
    """
    t = normalize_en_line(text)
    if not t:
        return 0.0
    words = [w for w in re.split(r"\s+", t) if w]
    base = (len(words) / max(float(wps), 0.5)) if words else 0.0
    pauses = 0.12 * len(re.findall(r"[,;:]", t)) + 0.22 * len(re.findall(r"[.!?]", t))
    return float(base + pauses)


Action = Literal["pad", "speed", "trim"]


@dataclass
class Plan:
    action: Action
    dur_s: float
    est_s: float
    required_speed: float
    planned_max_speed: float
    pause_s: float

    def to_dict(self) -> Dict:
        return {
            "action": self.action,
            "dur_s": round(self.dur_s, 3),
            "est_s": round(self.est_s, 3),
            "required_speed": round(self.required_speed, 3),
            "planned_max_speed": round(self.planned_max_speed, 3),
            "pause_s": round(self.pause_s, 3),
        }


def plan_tts_segment(
    text: str,
    *,
    dur_s: float,
    wps: float,
    global_speed_max: float,
    safety_margin: float = 0.05,
    min_cap: float = 1.05,
) -> Plan:
    """
    P1-2: per-segment speed planning (deterministic).
    - action=pad: est <= dur; pad remaining time with silence (natural pause at tail)
    - action=speed: est within dur*global_speed_max; allow limited speed-up
    - action=trim: est exceeds dur*global_speed_max; rely on text trimming + speed-up cap
    """
    dur_s = max(float(dur_s), 0.001)
    global_speed_max = max(float(global_speed_max), 1.0)
    wps = max(float(wps), 0.5)
    est = estimate_en_seconds(text, wps=wps)
    required = est / dur_s if dur_s > 0 else 1.0
    pause = max(dur_s - est, 0.0)
    if required <= 1.0:
        action: Action = "pad"
        planned_cap = min(global_speed_max, max(float(min_cap), 1.0 + float(safety_margin)))
    elif required <= global_speed_max:
        action = "speed"
        planned_cap = min(global_speed_max, max(float(min_cap), required + float(safety_margin)))
    else:
        action = "trim"
        planned_cap = float(global_speed_max)
    return Plan(action=action, dur_s=dur_s, est_s=est, required_speed=required, planned_max_speed=planned_cap, pause_s=pause)


def maybe_get_float(d: Dict, key: str, default: Optional[float] = None) -> Optional[float]:
    try:
        if key not in d:
            return default
        v = d.get(key)
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


