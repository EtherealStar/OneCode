"""Non-interactive and fallback CLI input helpers."""

from __future__ import annotations

import sys
from dataclasses import dataclass


def read_batch_line(prompt: str = "") -> str:
    """Read one submitted line from stdin without interactive editing."""

    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    line = sys.stdin.readline()
    if line == "":
        raise EOFError
    return line.rstrip("\r\n")


@dataclass(frozen=True)
class ConfirmOption:
    value: str
    label: str
    aliases: tuple[str, ...] = ()


def read_confirm_sync(title: str, options: tuple[ConfirmOption, ...]) -> str:
    """Read a single confirm choice from stdin (batch / fallback path)."""

    print(title)
    for option in options:
        print(f"  {option.label}")
    alias_map = {
        alias: option.value
        for option in options
        for alias in (option.value, *option.aliases)
    }
    while True:
        line = read_batch_line("> ").strip().lower()
        if line in alias_map:
            return alias_map[line]
        for option in options:
            if line == option.value:
                return option.value
