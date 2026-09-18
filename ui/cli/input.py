"""非交互式与回退命令行输入辅助函数。"""

from __future__ import annotations

import sys
from dataclasses import dataclass


def read_batch_line(prompt: str = "") -> str:
    """从 stdin 读取单行提交输入，不进行交互式编辑。"""

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
    """从 stdin 读取单次确认选项（批处理/回退路径）。"""

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
