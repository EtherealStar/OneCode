"""Stable hook event names."""

from __future__ import annotations

from enum import StrEnum


class HookEvent(StrEnum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    TOOL_ERROR = "ToolError"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    ASSISTANT_MESSAGE_COMPLETED = "AssistantMessageCompleted"
    TURN_STOPPED = "TurnStopped"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    COMPACT_FAILED = "CompactFailed"
