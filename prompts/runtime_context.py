"""Runtime facts available to system prompt assembly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.runtime_state import RuntimeState
from services.tools.types import ToolDescriptor

if TYPE_CHECKING:
    from services.skills import SkillCommand


@dataclass(frozen=True)
class PromptRuntimeContext:
    """Prompt-visible runtime facts.

    This intentionally excludes session id, provider configuration, CLI mode,
    API keys, transcript paths, and other program-internal details.
    """

    state: RuntimeState
    cwd: Path
    visible_tools: tuple[ToolDescriptor, ...] = ()
    visible_skills: tuple["SkillCommand", ...] = ()
    files_read: tuple[str, ...] = ()
    transition: str | None = None
    mcp_server_instructions: dict[str, str] | None = None
    instruction_memory: str = ""
    instruction_memory_fingerprint: str = ""
    long_term_memory_prompt: str = ""
    long_term_memory_fingerprint: str = ""
