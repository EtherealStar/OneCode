"""Composable system prompt sections."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from prompts.runtime_context import PromptRuntimeContext

PROMPT_VERSION = "dynamic-system-prompt-v1"


@dataclass(frozen=True)
class PromptSection:
    key: str
    title: str
    body: str
    fingerprint: str
    cacheable: bool = True

    def render(self) -> str:
        return f"# {self.title}\n{self.body.strip()}"


def identity_section(context: PromptRuntimeContext) -> PromptSection:
    del context
    body = (
        "You are OneCode, a coding agent running inside this workspace. "
        "Your job is to help the user complete code work using repository facts "
        "and the tools currently available to this runtime."
    )
    return PromptSection(
        key="identity",
        title="Identity",
        body=body,
        fingerprint=PROMPT_VERSION,
    )


def behavior_rules_section(context: PromptRuntimeContext) -> PromptSection:
    del context
    body = "\n".join(
        [
            "- Use repository facts and tool results as the basis for code changes.",
            "- Read relevant files before editing them.",
            "- Use available tools when you need to inspect files or search the workspace.",
            "- Respect sandbox and guard decisions. A denied capability is unavailable.",
            "- Do not claim that you ran commands, read files, or changed code unless that happened.",
            "- Keep runtime boundaries clear: main loop orchestration, tools for actions, prompts for guidance, guard for safety, and hooks for lifecycle extension.",
            "- Do not rely on model promises for safety; executable tool and guard paths define what is allowed.",
        ]
    )
    return PromptSection(
        key="behavior_rules",
        title="Behavior Rules",
        body=body,
        fingerprint=PROMPT_VERSION,
    )


def workspace_state_section(context: PromptRuntimeContext) -> PromptSection:
    lines = [f"cwd: {context.cwd}"]
    tool_names = [tool.name for tool in context.visible_tools]
    if tool_names:
        lines.append(f"available tools: {', '.join(tool_names)}")
    else:
        lines.append("available tools: none")

    if context.files_read:
        lines.append("files read:")
        lines.extend(f"- {path}" for path in context.files_read)

    fingerprint = _fingerprint(
        "workspace_state",
        str(context.cwd),
        "\n".join(context.files_read),
        ",".join(tool_names),
    )
    return PromptSection(
        key="workspace_state",
        title="Workspace State",
        body="\n".join(lines),
        fingerprint=fingerprint,
    )


def available_tools_section(context: PromptRuntimeContext) -> PromptSection:
    if not context.visible_tools:
        body = "No tools are currently available."
    else:
        body = "\n".join(
            f"- {tool.name}: {tool.description}" for tool in context.visible_tools
        )
    fingerprint = _fingerprint(
        "available_tools",
        "\n".join(f"{tool.name}:{tool.description}" for tool in context.visible_tools),
    )
    return PromptSection(
        key="available_tools",
        title="Available Tools",
        body=body,
        fingerprint=fingerprint,
    )


def tool_prompt_sections(context: PromptRuntimeContext) -> tuple[PromptSection, ...]:
    sections: list[PromptSection] = []
    for tool in context.visible_tools:
        prompt = tool.prompt.strip()
        if not prompt:
            continue
        sections.append(
            PromptSection(
                key="tool_prompt:" + tool.name,
                title=f"Tool: {tool.name}",
                body=prompt,
                fingerprint=_fingerprint(tool.name, prompt),
            )
        )
    return tuple(sections)


def default_sections(context: PromptRuntimeContext) -> tuple[PromptSection, ...]:
    return (
        identity_section(context),
        behavior_rules_section(context),
        workspace_state_section(context),
        available_tools_section(context),
        *tool_prompt_sections(context),
    )


def _fingerprint(*parts: str) -> str:
    payload = "\0".join(parts)
    return sha256(payload.encode("utf-8")).hexdigest()
