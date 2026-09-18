"""TUI 渲染器：消息正文、工具呈现与状态栏。

各渲染器负责将投影值类型转换为 Rich 可渲染对象。
它们绝不读取文件、不调用工具，也不依赖 Textual 控件，因此在测试中可直接配合基础的
:class:`rich.console.Console` 使用。
"""

from __future__ import annotations

from ui.tui.renderers.message import MarkdownCaches, render_message
from ui.tui.renderers.status import (
    SPINNER_FRAMES,
    RunState,
    render_run_state,
    render_status_left,
)
from ui.tui.renderers.tool import (
    ToolPresentation,
    ToolPresentationRegistry,
    build_tool_presentation_registry,
    first_line_excerpt,
    redact_sensitive,
)

__all__ = [
    "MarkdownCaches",
    "RunState",
    "SPINNER_FRAMES",
    "ToolPresentation",
    "ToolPresentationRegistry",
    "build_tool_presentation_registry",
    "first_line_excerpt",
    "redact_sensitive",
    "render_message",
    "render_run_state",
    "render_status_left",
]
