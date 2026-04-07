import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class HardwareInfo:
    cpu_cores: int
    memory_gb: float
    gpu_name: Optional[str]
    gpu_vram_gb: Optional[float]
    tier: str


def _get_memory_gb() -> float:
    try:
        import psutil  # type: ignore

        return round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        return 0.0


def _get_gpu_info() -> tuple[Optional[str], Optional[float]]:
    # Try torch first, then nvidia-smi
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1)
            return name, vram
    except Exception:
        pass

    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(["wmic", "path", "win32_VideoController", "get", "name,memorytype,videomodedescription"], text=True)
            # Parsing WMIC output is noisy; return presence only
            return out.splitlines()[1].strip(), None
        except Exception:
            return None, None

    if platform.system() == "Darwin":
        try:
            out = subprocess.check_output(["system_profiler", "SPDisplaysDataType"], text=True)
            vram = None
            name = None
            for line in out.splitlines():
                if "Chipset Model" in line:
                    name = line.split(":", 1)[1].strip()
                if "VRAM" in line:
                    try:
                        vram = float(line.split(":")[1].strip().split()[0])
                    except Exception:
                        pass
            return name, vram
        except Exception:
            return None, None

    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], text=True)
            name, vram = out.strip().split(",")
            return name.strip(), float(vram.strip().split()[0])
        except Exception:
            return None, None

    return None, None


def detect_tier(cores: int, mem: float, vram: Optional[float]) -> str:
    if (cores >= 12 and mem >= 32) or (vram and vram >= 8):
        return "high"
    if (cores >= 8 and mem >= 16) or (vram and vram >= 4):
        return "mid"
    return "normal"


def detect_hardware() -> HardwareInfo:
    cores = os.cpu_count() or 4
    mem = _get_memory_gb()
    gpu_name, gpu_vram = _get_gpu_info()
    tier = detect_tier(cores, mem, gpu_vram)
    return HardwareInfo(cpu_cores=cores, memory_gb=mem, gpu_name=gpu_name, gpu_vram_gb=gpu_vram, tier=tier)


def recommended_presets() -> dict:
    return {
        "normal": {
            "asr": "ggml-small-q5_0",
            "mt": "Marian",
            "tts": "Coqui XTTS v2 CPU（更慢）",
            "max_sentence_len": 50,
            "vad": False,
            "dedupe": True,
        },
        "mid": {
            "asr": "ggml-medium-q5_0",
            "mt": "Marian",
            "tts": "Coqui XTTS v2 GPU优先",
            "max_sentence_len": 50,
            "vad": True,
            "dedupe": True,
        },
        "high": {
            "asr": "ggml-medium-q5_0 或 large-v3 q5",
            "mt": "NLLB-200-distilled-600M",
            "tts": "Coqui GPU/VITS",
            "max_sentence_len": 45,
            "vad": True,
            "dedupe": True,
        },
    }


