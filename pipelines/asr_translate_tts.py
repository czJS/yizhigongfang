import runpy
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    target = repo_root / "scripts" / "asr_translate_tts.py"
    if not target.exists():
        raise FileNotFoundError(f"legacy pipeline script not found: {target}")
    try:
        sys.argv[0] = str(target)
    except Exception:
        pass
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()

