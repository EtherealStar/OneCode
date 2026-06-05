"""Tool descriptor for built-in subagent delegation."""

from __future__ import annotations

import json
from typing import Any

from services.subagents import SubagentRequest, SubagentRunner
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.agent.prompt import PROMPT

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "subagent_type": {"type": "string"},
    },
    "required": ["prompt"],
    "additionalProperties": False,
}


def descriptor(runner: SubagentRunner) -> ToolDescriptor:
    return ToolDescriptor(
        name="agent",
        description="Delegate a bounded task to a built-in subagent.",
        input_schema=INPUT_SCHEMA,
        handler=_handler_for(runner),
        prompt=PROMPT,
        search_hint="delegate to subagent",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _handler_for(runner: SubagentRunner):
    async def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        # The handler is the only bridge from tool execution into child runtime.
        result = await runner.run(
            SubagentRequest(
                prompt=str(tool_input["prompt"]),
                subagent_type=tool_input.get("subagent_type"),
                parent_session_id=runtime.state.session_id,
                parent_tool_call_id=runtime.tool_call_id,
            )
        )
        payload = {
            "agent_type": result.agent_type,
            "child_session_id": result.session_id,
            "is_fork": result.metadata.get("is_fork") is True,
            "tool_result_count": result.tool_result_count,
            "transition": result.transition,
            "final_text": result.final_text,
        }
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="agent",
            content=json.dumps(payload, ensure_ascii=False),
            is_error=result.is_error,
            metadata={
                "agent_type": result.agent_type,
                "child_session_id": result.session_id,
                "is_fork": result.metadata.get("is_fork") is True,
                "tool_result_count": result.tool_result_count,
                "transition": result.transition,
                **(
                    {"error": result.metadata["error"]}
                    if result.is_error and "error" in result.metadata
                    else {}
                ),
            },
        )

    return handle


def _validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
    # Keep schema validation structural and use this function for semantic checks.
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return ValidationResult.failure("prompt must be a non-empty string.")
    subagent_type = tool_input.get("subagent_type")
    if subagent_type is not None and (
        not isinstance(subagent_type, str) or not subagent_type.strip()
    ):
        return ValidationResult.failure("subagent_type must be a non-empty string.")
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    return ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=False,
        targets=(
            ToolTarget(
                kind="session_state",
                operation="mutate_state",
                value="subagent",
            ),
        ),
        permission_subject="agent:subagent",
    )
