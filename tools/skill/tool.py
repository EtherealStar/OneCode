"""Tool descriptor for loading OneCode skills on demand."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from services.attachments import AttachmentMessage
from services.skills import SkillCommand
from services.subagents.types import SubagentResult
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.skill.prompt import PROMPT


class SkillProvider(Protocol):
    def find_skill(self, name: str, cwd: Path) -> SkillCommand | None:
        ...


class SkillForkRunner(Protocol):
    async def run_skill(
        self,
        *,
        skill: SkillCommand,
        args: str,
        parent_session_id: str,
        parent_tool_call_id: str,
    ) -> SubagentResult:
        ...


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skill": {"type": "string"},
        "args": {"type": "string"},
    },
    "required": ["skill"],
    "additionalProperties": False,
}


def descriptor(
    *,
    skill_provider: SkillProvider,
    cwd: Path | Callable[[], Path],
    fork_runner: SkillForkRunner | Callable[[], SkillForkRunner | None] | None = None,
) -> ToolDescriptor:
    return ToolDescriptor(
        name="skill",
        description="Load and execute a OneCode skill by name.",
        input_schema=INPUT_SCHEMA,
        handler=_handler_for(skill_provider, cwd, fork_runner),
        prompt=PROMPT,
        search_hint="load project or user skill instructions",
        validate_input=_validator_for(skill_provider, cwd),
        classify_input=_classify_input,
        output_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "is_error": {"type": "boolean"},
                "metadata": {"type": "object"},
            },
            "required": ["content", "is_error"],
            "additionalProperties": False,
        },
    )


def _validator_for(
    skill_provider: SkillProvider,
    cwd: Path | Callable[[], Path],
):
    def validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
        """Validate that the requested skill exists and may be invoked."""

        skill_name = _skill_name(tool_input)
        if not skill_name:
            return ValidationResult.failure("skill must be a non-empty string.")
        command = skill_provider.find_skill(skill_name, _resolve_cwd(cwd))
        if command is None:
            return ValidationResult.failure(f"Unknown skill: {skill_name}")
        if command.disable_model_invocation:
            return ValidationResult.failure(f"Skill cannot be model-invoked: {skill_name}")
        if not command.user_invocable:
            return ValidationResult.failure(f"Skill is not user-invocable: {skill_name}")
        return ValidationResult.success()

    return validate


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    skill_name = _skill_name(tool_input)
    return ToolCallClassification(
        read_only=False,
        modifies_filesystem=False,
        concurrency_safe=False,
        targets=(
            ToolTarget(
                kind="session_state",
                operation="skill_load",
                value=skill_name,
            ),
        ),
        result_policy=ToolResultPolicy(
            max_result_size_chars=100_000,
            persist_when_exceeded=False,
            preview_chars=4_000,
        ),
        permission_subject=f"skill:{skill_name}",
    )


def _handler_for(
    skill_provider: SkillProvider,
    cwd: Path | Callable[[], Path],
    fork_runner: SkillForkRunner | Callable[[], SkillForkRunner | None] | None,
):
    async def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        """Load an inline skill attachment or delegate a fork skill to a child."""

        skill_name = _skill_name(tool_input)
        command = skill_provider.find_skill(skill_name, _resolve_cwd(cwd))
        if command is None:
            return _error(runtime, "unknown_skill", f"Unknown skill: {skill_name}")
        args = str(tool_input.get("args") or "")
        if command.context == "fork":
            runner = _resolve_fork_runner(fork_runner)
            if runner is None:
                return _error(
                    runtime,
                    "skill_fork_unavailable",
                    f"Fork runner is not configured for skill: {command.name}",
                )
            result = await runner.run_skill(
                skill=command,
                args=args,
                parent_session_id=runtime.state.session_id,
                parent_tool_call_id=runtime.tool_call_id,
            )
            return _fork_result(runtime, command, result)
        attachment = _skill_attachment(command, args)
        return ToolExecutionResult(
            tool_call_id=runtime.tool_call_id,
            tool_name="skill",
            content=f"Launching skill: {command.name}",
            metadata={
                "skill_name": command.name,
                "skill_context": "inline",
                "allowed_tools": command.allowed_tools,
            },
            followup_messages=(attachment,),
        )

    return handle


def _skill_attachment(command: SkillCommand, args: str) -> dict[str, Any]:
    """Build the durable internal attachment that later projects into context."""

    content = _expanded_content(command)
    return AttachmentMessage(
        {
            "type": "skill",
            "skill_name": command.name,
            "content": content,
            "args": args,
            "source": command.source,
            "root": str(command.root) if command.root is not None else "",
            "allowed_tools": command.allowed_tools,
            "model": command.model,
        },
        source="skill_tool",
    ).to_message()


def _expanded_content(command: SkillCommand) -> str:
    content = command.content
    if command.root is None:
        return content
    root_text = str(command.root)
    return (
        f"Base directory for this skill: {root_text}\n\n"
        + content.replace("${ONECODE_SKILL_DIR}", root_text)
    )


def _fork_result(
    runtime: ToolRuntime,
    command: SkillCommand,
    result: SubagentResult,
) -> ToolExecutionResult:
    payload = {
        "agent_type": result.agent_type,
        "child_session_id": result.session_id,
        "tool_result_count": result.tool_result_count,
        "transition": result.transition,
        "final_text": result.final_text,
    }
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="skill",
        content=json.dumps(payload, ensure_ascii=False),
        is_error=result.is_error,
        metadata={
            "skill_name": command.name,
            "skill_context": "fork",
            "child_session_id": result.session_id,
            "allowed_tools": command.allowed_tools,
            **({"error": result.metadata.get("error")} if result.is_error else {}),
        },
    )


def _error(runtime: ToolRuntime, error: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="skill",
        content=json.dumps({"error": error, "message": message}, ensure_ascii=False),
        is_error=True,
        metadata={"error": error},
    )


def _resolve_fork_runner(
    runner: SkillForkRunner | Callable[[], SkillForkRunner | None] | None,
) -> SkillForkRunner | None:
    if runner is None:
        return None
    if callable(runner) and not hasattr(runner, "run_skill"):
        return runner()
    return runner  # type: ignore[return-value]


def _resolve_cwd(cwd: Path | Callable[[], Path]) -> Path:
    value = cwd() if callable(cwd) else cwd
    return Path(value).resolve()


def _skill_name(tool_input: dict[str, Any]) -> str:
    value = tool_input.get("skill")
    return str(value).strip().lstrip("/") if isinstance(value, str) else ""
