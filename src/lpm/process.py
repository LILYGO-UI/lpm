# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import IO, Sequence

from .errors import CommandError, LpmError


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise LpmError(f"required command is not installed: {name}")
    return path


def run(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    argv = [str(item) for item in command]
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except OSError as exc:
        raise LpmError(f"cannot run {argv[0]}: {exc}") from exc
    if result.returncode != 0:
        if capture:
            detail = (result.stderr or result.stdout or "").strip()
            if detail:
                raise LpmError(f"{detail}\ncommand failed: {' '.join(argv)}")
        raise CommandError(argv, result.returncode)
    return result


def spawn(command: Sequence[str | Path], *, cwd: Path, log: IO[bytes]) -> subprocess.Popen[bytes]:
    argv = [str(item) for item in command]
    try:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        raise LpmError(f"cannot start {argv[0]}: {exc}") from exc


def is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_matches(pid: int, executable: Path) -> bool:
    """Best-effort guard against signaling a PID reused by another process."""
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        if proc_cmdline.is_file():
            command = proc_cmdline.read_bytes().replace(b"\0", b" ").decode(errors="replace")
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            command = result.stdout
    except OSError:
        return False
    return str(executable) in command


def stop_process_group(pid: int, timeout: float) -> bool:
    if not is_running(pid):
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running(pid):
            return True
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return True
