"""Tool descriptor for built-in subagent delegation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from services.subagents.types import SubagentRequest
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.agent.prompt import PROMPT

if TYPE_CHECKING:
    from services.background_tasks import BackgroundTaskManager
    from services.subagents.runner import SubagentRunner

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "subagent_type": {"type": "string"},
        "run_in_background": {"type": "boolean"},
        "focus_paths": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}


def descriptor(
    runner: SubagentRunner,
    background_task_manager: BackgroundTaskManager | None = None,
) -> ToolDescriptor:
    return ToolDescriptor(
        name="agent",
        description="Delegate a bounded task to a built-in subagent.",
        input_schema=INPUT_SCHEMA,
        handler=_handler_for(runner, background_task_manager),
        prompt=PROMPT,
        search_hint="delegate to subagent",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _handler_for(
    runner: SubagentRunner,
    background_task_manager: BackgroundTaskManager | None = None,
):
    async def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        if tool_input.get("run_in_background") is True:
            return _start_background_agent(tool_input, runtime, runner, background_task_manager)
        # The handler is the only bridge from tool execution into child runtime.
        result = await runner.run(
            SubagentRequest(
                prompt=str(tool_input["prompt"]),
                subagent_type=tool_input.get("subagent_type"),
                parent_session_id=runtime.state.session_id,
                parent_tool_call_id=runtime.tool_call_id,
                metadata=_child_metadata(runtime, tool_input),
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


def _start_background_agent(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
    runner: SubagentRunner,
    background_task_manager: BackgroundTaskManager | None,
) -> ToolExecutionResult:
    if background_task_manager is None:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="agent",
            content=json.dumps(
                {
                    "error": "background_tasks_not_enabled",
                    "message": "Background task execution is not enabled for this runtime.",
                },
                ensure_ascii=False,
            ),
            is_error=True,
            metadata={"error": "background_tasks_not_enabled"},
        )
    prompt = str(tool_input["prompt"])
    subagent_type = tool_input.get("subagent_type")
    metadata = _child_metadata(runtime, tool_input)

    async def run(task_id: str) -> dict[str, Any]:
        request = SubagentRequest(
            prompt=prompt,
            subagent_type=subagent_type,
            parent_session_id=runtime.state.session_id,
            parent_tool_call_id=runtime.tool_call_id,
            metadata={
                **metadata,
                "background_task_id": task_id,
                "background_task_type": "local_agent",
            },
        )
        result = await runner.run(request)
        task = background_task_manager.get(task_id)
        if task is not None:
            output_path = Path(str(task.metadata.get("output_path_abs", "")))
            if output_path:
                with output_path.open("a", encoding="utf-8", errors="replace") as handle:
                    handle.write(f"child_session_id: {result.session_id}\n")
                    handle.write(f"agent_type: {result.agent_type}\n")
                    handle.write(f"transition: {result.transition}\n\n")
                    handle.write(result.final_text)
                    if result.final_text and not result.final_text.endswith("\n"):
                        handle.write("\n")
        if result.is_error:
            raise RuntimeError(result.final_text)
        return {
            "summary": (
                f"Background agent {task_id} completed with child session "
                f"{result.session_id}."
            ),
            "agent_type": result.agent_type,
            "child_session_id": result.session_id,
            "transition": result.transition,
            "tool_result_count": result.tool_result_count,
            "final_text": result.final_text,
        }

    task = background_task_manager.start_agent(
        description=_description(prompt, subagent_type),
        state=runtime.state,
        run=run,
        tool_use_id=runtime.tool_call_id,
        metadata={
            "prompt": prompt,
            "agent_type": subagent_type or "fork",
            "parent_session_id": runtime.state.session_id,
        },
    )
    payload = {
        "task_id": task.id,
        "task_type": task.type,
        "status": task.status,
        "agent_type": subagent_type or "fork",
        "output_file": task.output_file,
    }
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="agent",
        content=json.dumps(payload, ensure_ascii=False),
        metadata={**payload, "background": True},
    )


def _child_metadata(runtime: ToolRuntime, tool_input: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    task_list_id = runtime.state.metadata.get("task_list_id")
    if isinstance(task_list_id, str) and task_list_id:
        metadata["task_list_id"] = task_list_id
        metadata["parent_task_list_id"] = task_list_id
    focus_paths = tool_input.get("focus_paths")
    if isinstance(focus_paths, list):
        cleaned: list[str] = []
        for entry in focus_paths:
            if isinstance(entry, str) and entry.strip():
                cleaned.append(entry)
        if cleaned:
            metadata["focus_paths"] = tuple(cleaned)
    return metadata


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
    run_in_background = tool_input.get("run_in_background")
    if run_in_background is not None and not isinstance(run_in_background, bool):
        return ValidationResult.failure("run_in_background must be a boolean.")
    focus_paths = tool_input.get("focus_paths")
    if focus_paths is not None:
        if not isinstance(focus_paths, list):
            return ValidationResult.failure("focus_paths must be a list of strings.")
        for entry in focus_paths:
            if not isinstance(entry, str) or not entry.strip():
                return ValidationResult.failure(
                    "focus_paths entries must be non-empty strings."
                )
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    # ``subagent_type="explore"`` is the only allowed agent flavor in plan
    # mode; we mark the call as read_only so the plan-mode permission policy
    # lets it through. The child runtime itself is forced read-only by the
    # subagent runner; this classifier only describes the parent call.
    targets: tuple[ToolTarget, ...] = (
        ToolTarget(
            kind="session_state",
            operation="mutate_state",
            value="subagent",
        ),
    )
    return ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=False,
        targets=targets,
        permission_subject="agent:subagent",
    )


def _description(prompt: str, subagent_type: Any) -> str:
    compact = " ".join(prompt.split())
    if len(compact) > 80:
        compact = compact[:77] + "..."
    return f"{subagent_type or 'fork'}: {compact}"
