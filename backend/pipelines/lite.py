"""
轻量模式管道：whisper.cpp + Marian/NLLB + Piper/Coqui
当前主脚本仍复用 scripts/asr_translate_tts.py，通过 TaskManager 路由。
后续若需要可将步骤拆分为独立函数以便复用/测试。
"""

DEFAULT_SCRIPT = "scripts/asr_translate_tts.py"

