"""Guarded complete text file write tool."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
    is_guard_policy_allowed,
)
from tools.write_file.prompt import PROMPT
from utils.text_io import read_text_file, write_text_file


MAX_DIFF_CHARS = 4_000

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["file_path", "content"],
    "additionalProperties": False,
}


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="write_file",
        description="Create or overwrite a local text file.",
        input_schema=INPUT_SCHEMA,
        handler=_handle,
        prompt=PROMPT,
        search_hint="create or overwrite files",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    file_path = str(tool_input["file_path"])
    return ToolCallClassification(
        read_only=False,
        modifies_filesystem=True,
        concurrency_safe=False,
        targets=(ToolTarget(kind="file", operation="write", value=file_path),),
        result_policy=ToolResultPolicy(
            max_result_size_chars=50_000,
            persist_when_exceeded=True,
            preview_chars=4_000,
        ),
        permission_subject=f"write_file:{file_path}",
    )


def _validate(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ValidationResult:
    if not str(tool_input["file_path"]).strip():
        return ValidationResult.failure("file_path must not be empty.")
    return ValidationResult.success()


def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolExecutionResult:
    if runtime.guard is None:
        raise RuntimeError("write_file requires a sandbox guard.")
    policy = runtime.guard.check_write_target(tool_input["file_path"])
    if not is_guard_policy_allowed(policy, runtime):
        payload = policy.to_tool_error()
        if policy.action == "ask":
            payload["error"] = "path_guard_ask_required"
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="write_file",
            content=json.dumps(payload, ensure_ascii=False),
            is_error=True,
            metadata={"error": payload["error"]},
        )

    path = policy.normalized_path
    content = tool_input["content"]
    line_count = _line_count(content)

    if path.exists() and path.is_dir():
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="write_file",
            content=f"Cannot write directory as file: {path}",
            is_error=True,
            metadata={"error": "path_is_directory", "path": str(path)},
        )

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_file(path, content)
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="write_file",
            content=f"Created {path} ({line_count} line(s)).",
            metadata={
                "path": str(path),
                "operation": "create",
                "line_count": line_count,
            },
        )

    cached_result = _validate_cached_file_state(runtime, path)
    if cached_result is not None:
        return cached_result

    before = read_text_file(path)
    write_text_file(path, content)
    diff, truncated = _short_diff(before, content)
    result_content = f"Updated {path} ({line_count} line(s))."
    if diff:
        result_content = f"{result_content}\n\n{diff}"
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="write_file",
        content=result_content,
        metadata={
            "path": str(path),
            "operation": "update",
            "line_count": line_count,
            "diff": diff,
            "diff_truncated": truncated,
        },
    )


def _validate_cached_file_state(
    runtime: ToolRuntime,
    path: Path,
) -> ToolExecutionResult | None:
    if runtime.file_state_cache is None:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="write_file",
            content="File must be read in this session before overwriting.",
            is_error=True,
            metadata={"error": "file_not_read", "path": str(path)},
        )
    cached = runtime.file_state_cache.get(path)
    if cached is None:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="write_file",
            content="File must be read in this session before overwriting.",
            is_error=True,
            metadata={"error": "file_not_read", "path": str(path)},
        )
    if cached.partial:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="write_file",
            content="File must be fully read in this session before overwriting.",
            is_error=True,
            metadata={"error": "file_not_fully_read", "path": str(path)},
        )

    current_mtime_ns = path.stat().st_mtime_ns
    if current_mtime_ns == cached.mtime_ns:
        return None

    current = read_text_file(path)
    if current != cached.content:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="write_file",
            content="File changed after it was read; read it again before overwriting.",
            is_error=True,
            metadata={"error": "file_unexpectedly_modified", "path": str(path)},
        )
    return None


def _short_diff(before: str, after: str) -> tuple[str, bool]:
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    return f"{diff[:MAX_DIFF_CHARS]}\n[diff truncated]", True


def _line_count(content: str) -> int:
    if content == "":
        return 0
    return len(content.splitlines())
