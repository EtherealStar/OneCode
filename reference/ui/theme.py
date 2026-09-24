"""MiniAgent 终端 UI 的视觉 token 与主题注册。

调色板的权威是仓库根目录 `DESIGN.md`；本文件是它唯一的代码映射。
渲染器只引用 `RICH_STYLES` 中的命名样式，不写死颜色。
"""

from __future__ import annotations

from rich.theme import Theme as RichTheme
from textual.app import App
from textual.theme import Theme

# --- 调色板（与 DESIGN.md 的 Colors 表一一对应） ---
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

MINIAGENT_THEME = Theme(
    name="miniagent",
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
        # 覆盖 Textual 派生的 alpha 值，让 $text-muted 精确命中 token。
        "text-muted": MUTED,
        "tool": TOOL,
        "surface-2": SURFACE_2,
    },
)

# 命名样式：渲染器与 Rich Markdown 共用一套语义词汇。
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
    # 状态栏模型名：accent 但不加粗（与 MiniAgent 标签区分）。
    "ui.status.model": ACCENT,
    # TodoList 只读区：pending 灰、in_progress 青色焦点、completed 绿勾。
    "ui.todo": MUTED,
    "ui.todo.pending": MUTED,
    "ui.todo.active": TOOL,
    "ui.todo.active.text": TEXT,
    "ui.todo.done": MUTED,
    "ui.todo.done.glyph": SUCCESS,
    # Rich 的 Markdown 渲染器只认 markdown.* 命名样式。
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
    try:
        from pygments.styles import get_style_by_name

        get_style_by_name("one-dark")
        return "one-dark"
    except Exception:
        return "monokai"


MARKDOWN_CODE_THEME = _detect_code_theme()


def apply_theme(app: App[object]) -> None:
    """注册并选中 MiniAgent 主题，同时把命名样式注入 Rich console。"""
    app.register_theme(MINIAGENT_THEME)
    app.theme = MINIAGENT_THEME.name
    app.console.push_theme(RichTheme(RICH_STYLES))
