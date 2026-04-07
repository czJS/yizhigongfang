#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def run_fw(audio_path: str) -> dict:
    from faster_whisper import WhisperModel

    print("[compare] start large-v3-turbo load", flush=True)
    t0 = time.time()
    model = WhisperModel(
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
        num_workers=1,
    )
    load_s = time.time() - t0
    print(f"[compare] large-v3-turbo loaded in {load_s:.3f}s", flush=True)
    t1 = time.time()
    print("[compare] start large-v3-turbo transcribe", flush=True)
    seg_iter, info = model.transcribe(
        audio_path,
        language="zh",
        task="transcribe",
        beam_size=1,
        best_of=1,
        condition_on_previous_text=False,
        chunk_length=20,
        vad_filter=False,
    )
    segs = []
    for s in seg_iter:
        segs.append(
            {
                "start": round(float(getattr(s, "start", 0.0)), 3),
                "end": round(float(getattr(s, "end", 0.0)), 3),
                "text": str(getattr(s, "text", "")).strip(),
            }
        )
    transcribe_s = time.time() - t1
    print(f"[compare] large-v3-turbo transcribed in {transcribe_s:.3f}s", flush=True)
    return {
        "profile": "large-v3-turbo",
        "engine": "faster-whisper",
        "language": getattr(info, "language", ""),
        "load_s": round(load_s, 3),
        "transcribe_s": round(transcribe_s, 3),
        "segments_n": len(segs),
        "preview": segs[:12],
    }


def _to_s(v):
    try:
        n = float(v)
    except Exception:
        return None
    return n / 1000.0 if abs(n) > 1000.0 else n


def run_sv(audio_path: str) -> dict:
    from funasr import AutoModel
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    print("[compare] start sensevoice-small load", flush=True)
    t0 = time.time()
    model = AutoModel(
        model="FunAudioLLM/SenseVoiceSmall",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device="cpu",
        hub="hf",
    )
    load_s = time.time() - t0
    print(f"[compare] sensevoice-small loaded in {load_s:.3f}s", flush=True)
    t1 = time.time()
    print("[compare] start sensevoice-small transcribe", flush=True)
    res = model.generate(
        input=audio_path,
        cache={},
        language="auto",
        use_itn=True,
        batch_size_s=20,
        merge_vad=True,
        merge_length_s=15,
        output_timestamp=True,
    )
    transcribe_s = time.time() - t1
    print(f"[compare] sensevoice-small transcribed in {transcribe_s:.3f}s", flush=True)
    segs = []
    items = res if isinstance(res, list) else [res]
    for item in items:
        if not isinstance(item, dict):
            continue
        sentence_info = item.get("sentence_info") or item.get("sentences") or []
        if isinstance(sentence_info, list) and sentence_info:
            for sent in sentence_info:
                if not isinstance(sent, dict):
                    continue
                text = rich_transcription_postprocess(str(sent.get("text") or sent.get("sentence") or "").strip())
                if not text:
                    continue
                ts = sent.get("timestamp")
                start = end = None
                if isinstance(ts, list) and ts:
                    first = ts[0]
                    last = ts[-1]
                    if isinstance(first, (list, tuple)) and len(first) >= 2:
                        start = _to_s(first[0])
                    if isinstance(last, (list, tuple)) and len(last) >= 2:
                        end = _to_s(last[1])
                if start is None:
                    start = _to_s(sent.get("start"))
                if end is None:
                    end = _to_s(sent.get("end"))
                segs.append(
                    {
                        "start": round(float(start or 0.0), 3),
                        "end": round(float(end or 0.0), 3),
                        "text": text,
                    }
                )
        else:
            text = rich_transcription_postprocess(str(item.get("text") or "").strip())
            segs.append({"start": 0.0, "end": 0.0, "text": text})
    return {
        "profile": "sensevoice-small",
        "engine": "sensevoice",
        "load_s": round(load_s, 3),
        "transcribe_s": round(transcribe_s, 3),
        "segments_n": len(segs),
        "preview": segs[:12],
        "raw_keys": sorted(list(items[0].keys())) if items and isinstance(items[0], dict) else [],
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: asr_experiment_container_compare.py <audio_path>")
    audio_path = sys.argv[1]
    out = {"audio_path": audio_path, "results": []}
    for fn in [run_fw, run_sv]:
        try:
            print(f"[compare] running {fn.__name__}", flush=True)
            out["results"].append(fn(audio_path))
        except Exception as exc:
            out["results"].append(
                {
                    "profile": fn.__name__,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
