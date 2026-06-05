"""Built-in subagent definitions."""

from __future__ import annotations

from services.subagents.types import AgentDefinition


GENERAL_PURPOSE_PROMPT = """You are a general-purpose subagent for OneCode.

Use the available tools to perform complex searches, multi-step research, and
concise analysis. Work independently from the parent agent context. Return only
the final findings needed by the parent agent, including relevant file paths or
facts discovered through tools.
"""

EXPLORE_PROMPT = """You are the Explore subagent for OneCode.

Your job is read-only code exploration: search files, inspect implementation
details, and report precise findings. Do not modify files, run state-changing
commands, or delegate to another agent.
"""

PLAN_PROMPT = """You are the Plan subagent for OneCode.

Your job is read-only planning: inspect the codebase, identify the relevant
modules, and produce an implementation plan. Do not modify files, run
state-changing commands, or delegate to another agent.
"""


BUILT_IN_AGENTS: dict[str, AgentDefinition] = {
    "general-purpose": AgentDefinition(
        agent_type="general-purpose",
        when_to_use="Use for clean-context complex research or multi-step analysis.",
        system_prompt=GENERAL_PURPOSE_PROMPT,
        disallowed_tools=("agent",),
    ),
    "Explore": AgentDefinition(
        agent_type="Explore",
        when_to_use="Use for read-only file search and code exploration.",
        system_prompt=EXPLORE_PROMPT,
        disallowed_tools=("agent", "edit_file", "write_file"),
        read_only=True,
    ),
    "Plan": AgentDefinition(
        agent_type="Plan",
        when_to_use="Use for read-only implementation planning after inspecting code.",
        system_prompt=PLAN_PROMPT,
        disallowed_tools=("agent", "edit_file", "write_file"),
        read_only=True,
    ),
    "fork": AgentDefinition(
        agent_type="fork",
        when_to_use="Synthetic hidden agent used when subagent_type is omitted.",
        system_prompt="",
        disallowed_tools=("agent",),
        hidden=True,
    ),
}


def get_agent_definition(agent_type: str) -> AgentDefinition | None:
    """Return the built-in definition for a user-visible or synthetic agent."""

    return BUILT_IN_AGENTS.get(agent_type)
