"""Conversation display package: view, virtual viewport, layout, caches.

Attributes are resolved lazily to keep the renderers -> render_cache import
from triggering the view -> viewport -> message cycle at package import time.
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
