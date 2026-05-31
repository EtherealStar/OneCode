from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .tools import ToolRegistry


@dataclass(frozen=True)
class PromptContext:
    cwd: Path
    tool_registry: ToolRegistry
    compaction_enabled: bool = True
    permission_mode: str = "workspace"


def assemble_system_prompt(ctx: PromptContext) -> str:
    sections = [
        identity_section(),
        behavior_section(),
        workspace_section(ctx.cwd),
        tool_policy_section(ctx.tool_registry),
    ]
    if ctx.compaction_enabled:
        sections.append(compaction_section())
    if ctx.permission_mode:
        sections.append(permission_section(ctx.permission_mode))
    return "\n\n".join(section for section in sections if section)


def identity_section() -> str:
    return "You are OneCode, a CLI coding agent. Solve coding tasks by inspecting and editing the local workspace."


def behavior_section() -> str:
    return (
        "Work directly and concretely. Use tools when filesystem or command output is needed. "
        "When no further tool call is needed, provide the final answer and stop."
    )


def workspace_section(cwd: Path) -> str:
    return f"Current workspace: {cwd}"


def tool_policy_section(registry: ToolRegistry) -> str:
    lines = ["Available tools are assembled at runtime:"]
    for tool in registry.tools():
        meta = tool.meta
        safety = "read-only" if meta.read_only else "write-capable"
        concurrency = "concurrency-safe" if meta.concurrency_safe else "serial"
        lines.append(f"- {meta.name}: {meta.description} ({safety}, {concurrency})")
    return "\n".join(lines)


def compaction_section() -> str:
    return (
        "Long sessions may be compacted automatically. If an earlier tool result was compacted, "
        "rerun the relevant read or command instead of relying on missing details."
    )


def permission_section(mode: str) -> str:
    return (
        f"Permission mode: {mode}. Keep file writes inside the workspace. "
        "Avoid destructive shell commands unless explicitly requested."
    )
