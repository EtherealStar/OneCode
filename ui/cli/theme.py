"""Rich style names and text symbols for the CLI.

The inline rendering model in :mod:`ui.cli.terminal` requires two
separate Rich themes: one for dark hosts and one for light hosts. Both
themes only define **foreground** styles — backgrounds are always
left to the terminal host so the inline region inherits the user's
white-on-black or black-on-white profile.
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
    """Foreground-only color names shared by both themes.

    No ``"bg"`` or ``"background"`` keys — backgrounds stay under the
    terminal host's control so the inline region is readable in both
    light and dark profiles.
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
        # Rich Table / Markdown table renderables reference these
        # default styles by name. Keep them foreground-only so CLI
        # output still inherits the terminal host background.
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
    """Pick a foreground-only Rich theme for the detected brightness.

    The fallback (``"dark"``) preserves historical behavior so callers
    that don't run :func:`ui.cli.terminal.detect.detect_terminal_brightness`
    see no visual change.
    """

    if brightness == "light":
        return RICH_THEME_LIGHT
    return RICH_THEME_DARK
