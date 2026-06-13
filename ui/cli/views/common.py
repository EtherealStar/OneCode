"""Shared helpers for Rich CLI views."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ui.cli.theme import RICH_THEME, SYMBOLS

PREVIEW_CHARS = 180

# 匹配 SGR（颜色/样式）类 ANSI 转义码，供 strip_ansi 在测试断言时去色用。
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def display_path(path: Path | str, workspace: Path) -> str:
    value = Path(path)
    try:
        return str(value.relative_to(workspace))
    except ValueError:
        return str(value)


def preview(value: Any, limit: int = PREVIEW_CHARS) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        text = " ".join(preview_block(block) for block in value)
    elif value is None:
        text = ""
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def preview_block(block: Any) -> str:
    if isinstance(block, dict):
        text = block.get("text")
        if isinstance(text, str):
            return text
    return str(block)


def key_value_table(*, title: str | None = None) -> Table:
    table = Table.grid(padding=(0, 2))
    if title:
        table.title = title
    table.add_column(style="onecode.subtle", no_wrap=True)
    table.add_column()
    return table


def titled_section(title: str, body: object, *, style: str = "onecode.title") -> Group:
    """渲染“仅顶部一条全宽横线 + 彩色标题 + 默认正文”的区块（替代旧的四边框 Panel）。"""

    heading = Rule(
        Text(f" {title} ", style=style),
        characters="─",
        style="onecode.subtle",
        align="left",
    )
    return Group(heading, body)


def titled_panel(title: str, renderable: object, *, style: str = "onecode.info") -> Group:
    return titled_section(title, renderable, style=style)


def empty_panel(title: str, message: str) -> Group:
    return titled_section(title, Text(f"{SYMBOLS.info} {message}", style="onecode.subtle"))


def strip_ansi(text: str) -> str:
    """去除文本中的 ANSI 样式码，便于测试对纯文本内容做断言。"""

    return _ANSI_RE.sub("", text)


def render_to_text(renderable: object | None, *, width: int = 120) -> str:
    if renderable is None:
        return ""
    # 现在唯一的渲染路径是彩色输出：force_terminal + truecolor 让 rich 产出 ANSI，
    # export_text(styles=True) 保留样式码（每段样式结尾自带重置码，按行切片不会破坏样式）。
    # 必须同时显式设置 height，rich 才会采用显式 width（否则会回退到检测到的终端宽度）。
    console = Console(
        record=True,
        force_terminal=True,
        color_system="truecolor",
        width=width,
        height=10_000,
        theme=RICH_THEME,
    )
    console.print(renderable)
    return console.export_text(styles=True).rstrip()


def section_group(*renderables: object) -> Group:
    return Group(*renderables)
