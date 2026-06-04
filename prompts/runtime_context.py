"""Runtime facts available to system prompt assembly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.runtime_state import RuntimeState
from services.tools.types import ToolDescriptor


@dataclass(frozen=True)
class PromptRuntimeContext:
    """Prompt-visible runtime facts.

    This intentionally excludes session id, provider configuration, CLI mode,
    API keys, transcript paths, and other program-internal details.
    """

    state: RuntimeState
    cwd: Path
    visible_tools: tuple[ToolDescriptor, ...] = ()
    files_read: tuple[str, ...] = ()
    transition: str | None = None
