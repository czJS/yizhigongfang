#!/usr/bin/env python3
"""
Stub SoVITS CLI.

用途：
  - 提供本地命令模板占位，质量模式可直接跑通
  - 当前行为：生成 0.1 秒静音 wav（不做真实推理）

替换方式：
  - 将此脚本改为调用你的真实 SoVITS 推理逻辑，保持参数兼容：
      --config --ckpt --text --ref --spk --out
"""
import argparse
import wave


def write_silence_wav(path: str, duration_sec: float = 0.1, sample_rate: int = 16000):
    n_samples = int(duration_sec * sample_rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_samples)


def parse_args():
    p = argparse.ArgumentParser(description="Stub SoVITS CLI (generates silence). Replace with real SoVITS inference.")
    p.add_argument("--config", type=str, required=False, help="Path to SoVITS config")
    p.add_argument("--ckpt", type=str, required=False, help="Path to SoVITS checkpoint")
    p.add_argument("--text", type=str, required=True, help="Input text")
    p.add_argument("--ref", type=str, default="", help="Reference audio path")
    p.add_argument("--spk", type=str, default="default", help="Speaker name")
    p.add_argument("--out", type=str, required=True, help="Output wav path")
    return p.parse_args()


def main():
    args = parse_args()
    # 仅输出静音占位，便于管线跑通；请按需替换为真实推理
    write_silence_wav(args.out, duration_sec=0.1, sample_rate=16000)


if __name__ == "__main__":
    main()

