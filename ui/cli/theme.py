"""CLI 的 Rich 样式名称与文本符号。

ui.cli.terminal 中的内联渲染模型需要两套独立的 Rich 主题：
一套用于暗色终端，另一套用于亮色终端。两套主题均仅定义前景色样式，
背景色始终交由终端宿主控制，使内联区域能够继承用户的黑底白字或白底黑字配置。
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.theme import Theme


@dataclass(frozen=True)
class StatusSymbols:
    success: str = "✓"
    error: str = "✗"
    warning: str = "!"
    info: str = "i"
    pending: str = "○"
    loading: str = "…"
    pointer: str = "›"


SYMBOLS = StatusSymbols()

# 启动横幅左侧的吉祥物字符画（紧凑三行小猫），用 onecode.mascot 样式着色。
MASCOT_CAT = r"""
 /\_/\
( o.o )
 > ^ <
""".strip("\n")


def _base_palette() -> dict[str, str]:
    """两套主题共享的纯前景色名称。

    不包含 bg 或 background 键，背景色交由终端宿主控制，
    确保内联区域在亮色和暗色配置下均清晰可读。
    """

    return {
        "onecode.title": "bold cyan",
        "onecode.accent": "cyan",
        "onecode.mascot": "bold yellow",
        "onecode.subtle": "dim",
        "onecode.dim": "dim",
        "onecode.command": "bold magenta",
        "onecode.path": "cyan",
        "onecode.success": "green",
        "onecode.error": "bold red",
        "onecode.warning": "yellow",
        "onecode.info": "blue",
        "onecode.permission": "yellow",
        "onecode.model": "green",
        "onecode.session": "magenta",
        "onecode.metric": "bold",
        # Rich 表格与 Markdown 表格渲染对象按名称引用这些默认样式。
        # 保持纯前景色，确保命令行输出仍能继承终端宿主的背景色。
        "table.header": "bold",
        "table.footer": "",
        "table.cell": "",
        "table.title": "bold",
        "table.caption": "dim",
        "markdown.table.border": "dim",
        "markdown.table.header": "bold",
    }


# 兼容旧名（RICH_THEME 仍指向与历史完全相同的暗色主题）。
RICH_THEME = Theme(_base_palette(), inherit=False)

# 显式两份主题：暗色保留原配色；亮色把"subtle / 暗色背景色"调亮以
# 在白底上可读。其余颜色维持 ANSI 16 色调，保持与暗色视觉接近。
RICH_THEME_DARK = Theme(_base_palette(), inherit=False)

_RICH_THEME_LIGHT_PALETTE = _base_palette() | {
    "onecode.subtle": "grey50",
    "onecode.mascot": "dark_orange3",
    "onecode.path": "dark_cyan",
}
RICH_THEME_LIGHT = Theme(_RICH_THEME_LIGHT_PALETTE, inherit=False)


def rich_theme_for(brightness: str) -> Theme:
    """根据探测到的亮度选择纯前景色的 Rich 主题。

    回退选项（dark）保留既有行为，未运行
    ui.cli.terminal.detect.detect_terminal_brightness 的调用方视觉不受影响。
    """

    if brightness == "light":
        return RICH_THEME_LIGHT
    return RICH_THEME_DARK
