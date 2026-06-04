"""AST-classified Git Bash tool."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.bash.ast_model import BashAnalysis, BashParseError
from tools.bash.parser import parse_bash
from tools.bash.paths import targets_for_analysis
from tools.bash.prompt import PROMPT
from tools.bash.readonly import classify_readonly
from tools.bash.runner import DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS, BashRunner, GitBashRunner
from tools.bash.semantics import check_semantics, effective_command_name, interpret_exit


RESULT_POLICY = ToolResultPolicy(
    max_result_size_chars=30_000,
    persist_when_exceeded=False,
    preview_chars=4_000,
)


@dataclass(frozen=True)
class BashPlan:
    analysis: BashAnalysis | None
    parse_error: BashParseError | None
    read_only: bool
    reason: str | None
    targets: tuple[ToolTarget, ...]


class BashInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command: str
    timeout_ms: int | None = Field(default=None, ge=1, le=MAX_TIMEOUT_MS)
    description: str | None = None

    @field_validator("command", "description")
    @classmethod
    def _strip_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty.")
        return stripped


INPUT_SCHEMA: dict[str, Any] = BashInput.model_json_schema()


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="bash",
        description="Execute a Git Bash command with AST-based classification and sandbox-aware permissions.",
        input_schema=INPUT_SCHEMA,
        handler=_handle,
        prompt=PROMPT,
        search_hint="execute git bash commands",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _parse_input(tool_input: dict[str, Any]) -> BashInput:
    return BashInput.model_validate(tool_input)


def _validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
    try:
        _parse_input(tool_input)
    except ValidationError as exc:
        return ValidationResult.failure(_validation_message(exc))
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    parsed = _parse_input(tool_input)
    plan = _build_plan(parsed.command)
    return ToolCallClassification(
        read_only=plan.read_only,
        modifies_filesystem=not plan.read_only,
        concurrency_safe=plan.read_only,
        targets=plan.targets,
        result_policy=RESULT_POLICY,
        permission_subject=f"bash:{_subject_prefix(parsed.command)}",
    )


def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolExecutionResult:
    return _handle_with_runner(tool_input, runtime, GitBashRunner())


def _handle_with_runner(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
    runner: BashRunner,
) -> ToolExecutionResult:
    parsed = _parse_input(tool_input)
    plan = _build_plan(parsed.command)
    cwd = runtime.guard.boundary.cwd if runtime.guard is not None else Path.cwd()
    timeout_ms = parsed.timeout_ms or DEFAULT_TIMEOUT_MS
    try:
        result = runner.run(parsed.command, cwd=cwd, timeout_ms=timeout_ms)
    except FileNotFoundError as exc:
        return _error_result(
            "git_bash_not_found",
            str(exc),
            {"read_only": plan.read_only, "command_count": _command_count(plan)},
        )
    except Exception as exc:
        return _error_result(
            "bash_execution_error",
            str(exc),
            {"read_only": plan.read_only, "command_count": _command_count(plan)},
        )

    command_name = _last_effective_command_name(plan)
    interpretation = interpret_exit(
        command_name,
        result.exit_code,
        result.stdout,
        result.stderr,
    )
    is_error = result.timed_out or interpretation.is_error
    content = _format_result(
        command=parsed.command,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        note=interpretation.message,
        timed_out=result.timed_out,
    )
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="bash",
        content=content,
        is_error=is_error,
        metadata={
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
            "read_only": plan.read_only,
            "command_count": _command_count(plan),
            "semantic_message": plan.reason,
            "stdout_chars": len(result.stdout),
            "stderr_chars": len(result.stderr),
            "command_name": command_name,
        },
    )


def _build_plan(command: str) -> BashPlan:
    parsed = parse_bash(command)
    command_target = ToolTarget(
        kind="command",
        operation="execute",
        value=command,
        metadata={"shell": "git_bash"},
    )
    if isinstance(parsed, BashParseError):
        return BashPlan(
            analysis=None,
            parse_error=parsed,
            read_only=False,
            reason=parsed.reason,
            targets=(ToolTarget(
                kind="command",
                operation="execute",
                value=command,
                metadata={"shell": "git_bash", "parse_error": parsed.reason},
            ),),
        )
    semantic = check_semantics(parsed)
    if not semantic.ok:
        return BashPlan(
            analysis=parsed,
            parse_error=None,
            read_only=False,
            reason=semantic.reason,
            targets=(*targets_for_analysis(parsed), command_target),
        )
    readonly = classify_readonly(parsed)
    targets = targets_for_analysis(parsed)
    if readonly.read_only:
        return BashPlan(
            analysis=parsed,
            parse_error=None,
            read_only=True,
            reason=None,
            targets=targets,
        )
    return BashPlan(
        analysis=parsed,
        parse_error=None,
        read_only=False,
        reason=readonly.reason,
        targets=(*targets, command_target),
    )


def _last_effective_command_name(plan: BashPlan) -> str | None:
    if plan.analysis is None or not plan.analysis.commands:
        return None
    return effective_command_name(plan.analysis.commands[-1].argv)


def _command_count(plan: BashPlan) -> int:
    return len(plan.analysis.commands) if plan.analysis is not None else 0


def _subject_prefix(command: str) -> str:
    compact = " ".join(command.split())
    return compact[:80]


def _format_result(
    *,
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    note: str | None,
    timed_out: bool,
) -> str:
    lines = [f"command: {command}", f"exit_code: {exit_code}"]
    if timed_out:
        lines.append("timed_out: true")
    if note:
        lines.append(f"note: {note}")
    lines.extend(["", "stdout:", stdout.rstrip(), "", "stderr:", stderr.rstrip()])
    return "\n".join(lines).rstrip()


def _error_result(
    error: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="bash",
        content=json.dumps({"error": error, "message": message}, ensure_ascii=False),
        is_error=True,
        metadata={"error": error, **(metadata or {})},
    )


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    prefix = f"{location}: " if location else ""
    return f"{prefix}{first.get('msg', 'Tool input is invalid.')}"
