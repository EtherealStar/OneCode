"""Stable hook event names."""

from __future__ import annotations

from enum import StrEnum


class HookEvent(StrEnum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    TOOL_ERROR = "ToolError"
