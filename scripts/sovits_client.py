"""
SoVITS client helpers.

HTTP 模式（推荐）：服务器需支持
  POST {endpoint}
  JSON: {"text": "...", "ref_audio": "path_or_url", "speaker": "..."}
  返回 audio/wav 字节，或 JSON {"audio": base64 或 "data": base64}

本地命令模式：调用用户提供的命令模板，使用 {text} {ref_audio} {speaker} {out} 占位符。
"""
from __future__ import annotations

import base64
import shlex
import subprocess
from pathlib import Path
from typing import Optional

import requests


def synthesize_sovits_http(text: str, endpoint: str, ref_audio: Optional[str], speaker: Optional[str], out_path: Path) -> None:
    payload = {"text": text}
    if ref_audio:
        payload["ref_audio"] = ref_audio
    if speaker:
        payload["speaker"] = speaker
    resp = requests.post(endpoint, json=payload, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"SoVITS TTS failed: {resp.status_code} {resp.text}")
    content_type = resp.headers.get("Content-Type", "")
    data = resp.content
    if "application/json" in content_type:
        obj = resp.json()
        audio_b64 = obj.get("audio") or obj.get("data")
        if not audio_b64:
            raise RuntimeError("SoVITS JSON missing audio field")
        data = base64.b64decode(audio_b64)
    out_path.write_bytes(data)


def synthesize_sovits_local_cmd(text: str, cmd_template: str, ref_audio: Optional[str], speaker: Optional[str], out_path: Path) -> None:
    """
    通过本地命令行调用 SoVITS 推理。
    命令模板示例：
      python3 sovits_cli.py --text "{text}" --ref "{ref_audio}" --spk "{speaker}" --out "{out}"
    """
    cmd_filled = cmd_template.format(
        text=text.replace('"', '\\"'),
        ref_audio=(ref_audio or ""),
        speaker=(speaker or ""),
        out=str(out_path),
    )
    cmd_parts = shlex.split(cmd_filled)
    proc = subprocess.run(cmd_parts, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"SoVITS local cmd failed: {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    if not out_path.exists():
        raise RuntimeError("SoVITS local cmd did not produce output file.")

