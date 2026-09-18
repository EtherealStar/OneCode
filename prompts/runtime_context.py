"""系统提示词组装可用的运行时事实。"""

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
    """提示词可见的运行时事实。

    此处刻意排除了 session id、provider 配置、CLI 模式、
    API 密钥、transcript 路径以及其他程序内部细节。
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
