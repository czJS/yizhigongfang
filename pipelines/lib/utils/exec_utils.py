from __future__ import annotations

import hashlib
import os
import platform
import shutil
import struct
import subprocess
import time
from pathlib import Path
from typing import List, Optional


def _repo_root() -> Path:
    # /repo/pipelines/lib/utils/exec_utils.py -> parents[3] is /repo
    return Path(__file__).resolve().parents[3]


def run_cmd(
    cmd: List[str],
    check: bool = True,
    env: Optional[dict] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """运行子进程命令，失败时抛出详细日志，便于排查。"""
    # Use Windows-friendly quoting for human-readable error messages.
    try:
        pretty = subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(cmd)
    except Exception:
        pretty = " ".join(str(x) for x in cmd)
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        env=env,
        input=input_text,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed (rc={proc.returncode}): {pretty}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def run_cmd_with_heartbeat(
    cmd: List[str],
    *,
    heartbeat_s: float = 15.0,
    label: str = "task",
    check: bool = True,
    env: Optional[dict] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """
    Run a command and emit periodic "still running" logs.

    This is useful for long-running tools (e.g. whisper.cpp) that may not print progress,
    otherwise users often think the pipeline is stuck.
    """
    if heartbeat_s <= 0:
        return run_cmd(cmd, check=check, env=env, input_text=input_text)

    # Use Windows-friendly quoting for human-readable error messages.
    try:
        pretty = subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(cmd)
    except Exception:
        pretty = " ".join(str(x) for x in cmd)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if input_text is not None else None,
        text=True,
        errors="replace",
        env=env,
    )
    if input_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(input_text)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    start = time.time()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def _coerce_text(v: object) -> str:
        if v is None:
            return ""
        if isinstance(v, bytes):
            return v.decode("utf-8", errors="replace")
        if isinstance(v, str):
            return v
        return str(v)

    while True:
        try:
            out, err = proc.communicate(timeout=heartbeat_s)
            if out:
                stdout_parts.append(out)
            if err:
                stderr_parts.append(err)
            break
        except subprocess.TimeoutExpired as e:
            # Best-effort: append any partial output already received.
            if getattr(e, "stdout", None):
                stdout_parts.append(_coerce_text(e.stdout))
            if getattr(e, "stderr", None):
                stderr_parts.append(_coerce_text(e.stderr))
            elapsed_s = int(time.time() - start)
            print(f"[{label}] still running... {elapsed_s}s")

    cp = subprocess.CompletedProcess(
        cmd,
        proc.returncode,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )
    if check and cp.returncode != 0:
        raise RuntimeError(f"Command failed (rc={cp.returncode}): {pretty}\nstdout:\n{cp.stdout}\nstderr:\n{cp.stderr}")
    return cp


def ensure_tool(name: str) -> None:
    """
    Ensure external tools (ffmpeg / piper / whisper.cpp) are available.
    In docker we often pass absolute paths; for those, shutil.which() won't work reliably.
    Also validate CPU architecture for piper to avoid late "rosetta/ld-linux-x86-64" crashes.
    """
    # Absolute/relative path case
    if os.sep in name or name.startswith("."):
        p = Path(name)
        if not p.is_absolute():
            # Resolve relative to repo root to avoid cwd sensitivity
            p = _repo_root() / p
        if not (p.exists() and os.access(str(p), os.X_OK)):
            raise SystemExit(f"Missing required tool: {name}. Please install and retry.")
        # Arch sanity check for piper binary (common pitfall on Apple Silicon / ARM containers)
        if p.name == "piper":
            ensure_elf_arch_compatible(p)
        return

    # PATH lookup case
    resolved = shutil.which(name)
    if not resolved:
        raise SystemExit(f"Missing required tool: {name}. Please install and retry.")
    if Path(resolved).name == "piper":
        ensure_elf_arch_compatible(Path(resolved))


# Cache: configured piper_bin -> runnable piper path (possibly under /tmp)
PIPER_BIN_CACHE: dict[str, str] = {}


def resolve_path_like(name: str) -> Path:
    """Resolve a configured tool path to an absolute path (repo-root relative if needed)."""
    p = Path(name)
    if not p.is_absolute():
        p = _repo_root() / p
    return p


def prepare_piper_bin(configured: str) -> str:
    """
    Make sure piper is *runnable* inside containers.

    On Docker Desktop/macOS bind mounts, files can have +x but still fail at runtime with:
      bash: .../piper: Permission denied
    which usually means the mount is `noexec`.

    Fix: copy the whole piper folder to /tmp and execute from there.
    """
    if configured in PIPER_BIN_CACHE:
        return PIPER_BIN_CACHE[configured]

    # PATH lookup: we can't easily relocate; just return as-is
    if os.sep not in configured and not configured.startswith("."):
        PIPER_BIN_CACHE[configured] = configured
        return configured

    p = resolve_path_like(configured)
    if not p.exists():
        PIPER_BIN_CACHE[configured] = configured
        return configured

    # Sanity check arch early (gives clearer error than "rosetta/ld-linux")
    if p.name == "piper":
        ensure_elf_arch_compatible(p)

    # Fast runnable check: try to exec --help; noexec shows up as EACCES
    try:
        subprocess.run(
            [str(p), "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        PIPER_BIN_CACHE[configured] = str(p)
        return str(p)
    except OSError as e:
        if getattr(e, "errno", None) != 13:
            PIPER_BIN_CACHE[configured] = str(p)
            return str(p)

    # Permission denied: likely noexec mount. Copy full folder to /tmp and run from there.
    src_dir = p.parent
    tag = hashlib.sha1(str(src_dir).encode("utf-8")).hexdigest()[:10]
    dst_dir = Path("/tmp") / f"piper_{tag}"
    try:
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        dst_piper = dst_dir / p.name
        # Ensure exec bits on the copied binaries
        for bin_name in ("piper", "piper_phonemize"):
            bp = dst_dir / bin_name
            if bp.exists():
                bp.chmod(0o755)
        # Tiny delay to avoid edge-case inode race in some FS
        time.sleep(0.02)
        PIPER_BIN_CACHE[configured] = str(dst_piper)
        return str(dst_piper)
    except Exception:
        # Fall back to original; caller will raise a clearer error from run_cmd
        PIPER_BIN_CACHE[configured] = str(p)
        return str(p)


def find_espeak_data_dir(piper_bin: str) -> Optional[Path]:
    """
    Find an espeak-ng-data directory that contains `phontab`.

    Piper phonemization needs espeak-ng-data. Depending on how piper is packaged/mounted,
    the data may live next to the binary (e.g. /app/bin/piper/espeak-ng-data).
    """
    candidates: List[Path] = []
    try:
        pb = Path(piper_bin)
        if pb.is_absolute():
            candidates.append(pb.parent / "espeak-ng-data")
            candidates.append(pb.parent / "share" / "espeak-ng-data")
            candidates.append(pb.parent.parent / "share" / "espeak-ng-data")
    except Exception:
        pass

    candidates.extend(
        [
            Path("/app/bin/piper/espeak-ng-data"),
            Path("/usr/share/espeak-ng-data"),
            Path("/usr/lib/espeak-ng-data"),
            Path("/usr/libexec/espeak-ng-data"),
        ]
    )

    for d in candidates:
        try:
            if (d / "phontab").exists():
                return d
        except Exception:
            continue
    return None


def ensure_elf_arch_compatible(binary: Path) -> None:
    """
    Validate ELF e_machine matches runtime arch for Linux containers.
    - x86_64: e_machine=62
    - aarch64: e_machine=183
    """
    try:
        data = binary.read_bytes()
        if data[:4] != b"\x7fELF":
            return
        ei_data = data[5]  # 1=little, 2=big
        endian = "<" if ei_data == 1 else ">"
        e_machine = struct.unpack(endian + "H", data[18:20])[0]
        host = platform.machine().lower()
        expected = None
        if host in {"x86_64", "amd64"}:
            expected = 62
        elif host in {"aarch64", "arm64"}:
            expected = 183
        if expected and e_machine != expected:
            raise SystemExit(
                "Piper 二进制架构与当前容器架构不匹配，无法运行。\n"
                f"- 容器架构: {platform.machine()}\n"
                f"- piper: {binary}\n"
                f"- ELF e_machine: {e_machine}（x86_64=62, aarch64=183）\n"
                "解决方案（二选一）：\n"
                "1) 提供 linux-aarch64(arm64) 版 piper，替换到 /app/bin/piper/piper（或配置 piper_bin 指向它）\n"
                "2) 把整个 backend 容器切换为 linux/amd64，并同时使用 amd64 的 whisper-cli/ffmpeg（保持同一架构）\n"
                "（不会将 lite 流程降级为 quality；只是工具架构需要一致）"
            )
    except SystemExit:
        raise
    except Exception:
        # If anything goes wrong, don't block execution; run_cmd will surface errors.
        return

