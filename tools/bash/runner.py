"""Git Bash command runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import time
from typing import Protocol

from utils.text_io import decode_text


DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000


@dataclass(frozen=True)
class BashRunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


class BashRunner(Protocol):
    def run(self, command: str, *, cwd: Path, timeout_ms: int) -> BashRunResult:
        ...


class GitBashRunner:
    def __init__(self, bash_exe: Path | None = None) -> None:
        self._bash_exe = bash_exe

    def run(self, command: str, *, cwd: Path, timeout_ms: int) -> BashRunResult:
        bash = self._bash_exe or find_git_bash()
        if bash is None:
            raise FileNotFoundError(
                "Git Bash was not found. Install Git for Windows or add bash.exe to PATH."
            )
        start = time.monotonic()
        try:
            completed = subprocess.run(
                [str(bash), "--noprofile", "--norc", "-lc", command],
                cwd=cwd,
                capture_output=True,
                check=False,
                timeout=timeout_ms / 1000,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return BashRunResult(
                exit_code=124,
                stdout=decode_text(exc.stdout),
                stderr=decode_text(exc.stderr) or "Command timed out.",
                duration_ms=duration_ms,
                timed_out=True,
            )
        duration_ms = int((time.monotonic() - start) * 1000)
        return BashRunResult(
            exit_code=completed.returncode,
            stdout=decode_text(completed.stdout),
            stderr=decode_text(completed.stderr),
            duration_ms=duration_ms,
        )


def find_git_bash() -> Path | None:
    """Find Git Bash without invoking a shell."""

    path_candidate = shutil.which("bash.exe") or shutil.which("bash")
    if path_candidate:
        return Path(path_candidate)
    for raw in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
    ):
        candidate = Path(raw)
        if candidate.exists():
            return candidate
    return None
