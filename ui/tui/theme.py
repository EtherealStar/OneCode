"""OneCode TUI 视觉设计令牌与主题注册。

调色板是全屏 TUI 的唯一代码色彩映射。渲染器仅引用 :data:`RICH_STYLES` 中的具名样式；
绝不硬编码颜色。Textual 布局令牌定义在 :data:`ONECODE_THEME` 上，
以便控件 CSS 使用 ``$primary``/``$surface``/``$text-muted`` 等变量。
"""

from __future__ import annotations

from typing import Any

from rich.theme import Theme as RichTheme
from textual.app import App
from textual.theme import Theme

# --- 调色板 ----------------------------------------------------------------

BG = "#1A1B20"
SURFACE = "#23252D"
SURFACE_2 = "#2A2D37"
TEXT = "#E6E8EE"
MUTED = "#989CA8"
ACCENT = "#7AA2F7"
ACCENT_2 = "#E0A35E"
TOOL = "#6FC3CF"
SUCCESS = "#98C379"
ERROR = "#E06C75"

ONECODE_THEME = Theme(
    name="onecode",
    primary=ACCENT,
    secondary=TOOL,
    accent=ACCENT_2,
    warning=ACCENT_2,
    error=ERROR,
    success=SUCCESS,
    foreground=TEXT,
    background=BG,
    surface=SURFACE,
    panel=SURFACE_2,
    dark=True,
    variables={
        # 覆盖 Textual 推导的 alpha 透明度值，使 ``$text-muted`` 精确匹配设计令牌。
        "text-muted": MUTED,
        "tool": TOOL,
        "surface-2": SURFACE_2,
    },
)

#: 渲染器与 Rich Markdown 共享的具名 Rich 样式。
RICH_STYLES: dict[str, str] = {
    "ui.label.user": MUTED,
    "ui.label.agent": f"bold {ACCENT}",
    "ui.reasoning": f"italic {MUTED}",
    "ui.tool": TOOL,
    "ui.tool.summary": MUTED,
    "ui.success": SUCCESS,
    "ui.error": ERROR,
    "ui.queued": f"italic {MUTED}",
    "ui.queued.tag": ACCENT_2,
    "ui.meta": MUTED,
    "ui.attachment": TOOL,
    "ui.status.model": ACCENT,
    "ui.todo": MUTED,
    "ui.todo.pending": MUTED,
    "ui.todo.active": TOOL,
    "ui.todo.active.text": TEXT,
    "ui.todo.done": MUTED,
    "ui.todo.done.glyph": SUCCESS,
    # Rich 的 Markdown 渲染器仅支持 ``markdown.*`` 具名样式。
    "markdown.h1": f"bold {TEXT}",
    "markdown.h2": f"bold {TEXT}",
    "markdown.h3": f"bold {MUTED}",
    "markdown.h4": f"bold {MUTED}",
    "markdown.h5": f"bold {MUTED}",
    "markdown.h6": f"bold {MUTED}",
    "markdown.code": f"{ACCENT_2} on {SURFACE}",
    "markdown.code_block": f"{TEXT} on {SURFACE}",
    "markdown.link": f"underline {ACCENT}",
    "markdown.block_quote": f"italic {MUTED}",
    "markdown.hr": MUTED,
}


def _detect_code_theme() -> str:
    # 主题检测依赖已安装的 pygments；任何失败都回退到 monokai。
    try:
        from pygments.styles import get_style_by_name

        get_style_by_name("one-dark")
        return "one-dark"
    except Exception:  # noqa: BLE001
        return "monokai"


MARKDOWN_CODE_THEME = _detect_code_theme()


def apply_theme(app: App[Any]) -> None:
    """注册并选用 OneCode 主题，注入具名 Rich 样式。"""

    app.register_theme(ONECODE_THEME)
    app.theme = ONECODE_THEME.name
    app.console.push_theme(RichTheme(RICH_STYLES))


__all__ = [
    "ACCENT",
    "ACCENT_2",
    "BG",
    "ERROR",
    "MARKDOWN_CODE_THEME",
    "MUTED",
    "ONECODE_THEME",
    "RICH_STYLES",
    "SUCCESS",
    "SURFACE",
    "SURFACE_2",
    "TEXT",
    "TOOL",
    "apply_theme",
]
