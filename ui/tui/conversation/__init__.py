"""会话显示包：视图、虚拟视口、布局与渲染缓存。

采用延迟解析属性，避免 renderers -> render_cache 的导入在包加载阶段触发
view -> viewport -> message 的循环依赖。
"""

from __future__ import annotations

import importlib

_LAZY: dict[str, tuple[str, str]] = {
    "VirtualLayoutIndex": (
        "ui.tui.conversation.layout_index",
        "VirtualLayoutIndex",
    ),
    "MarkdownBlockCache": (
        "ui.tui.conversation.render_cache",
        "MarkdownBlockCache",
    ),
    "split_closed_blocks": (
        "ui.tui.conversation.render_cache",
        "split_closed_blocks",
    ),
    "UiRefreshScheduler": (
        "ui.tui.conversation.refresh_scheduler",
        "UiRefreshScheduler",
    ),
    "ConversationView": ("ui.tui.conversation.view", "ConversationView"),
    "DetailRequested": ("ui.tui.conversation.viewport", "DetailRequested"),
    "MessageViewport": ("ui.tui.conversation.viewport", "MessageViewport"),
    "MessageWidget": ("ui.tui.conversation.viewport", "MessageWidget"),
    "NewContentButton": ("ui.tui.conversation.viewport", "NewContentButton"),
    "DEFAULT_OVERSCAN": (
        "ui.tui.conversation.viewport",
        "DEFAULT_OVERSCAN",
    ),
}

__all__ = sorted(_LAZY)


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
