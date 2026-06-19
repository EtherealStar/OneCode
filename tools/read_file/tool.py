"""Guarded text file read tool."""

from __future__ import annotations

import json
import math
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
from tools.read_file.prompt import PROMPT
from utils.text_io import read_text_file

DEFAULT_LIMIT = 2000


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "offset": {"type": "integer", "minimum": 1},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["file_path"],
    "additionalProperties": False,
}


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="read_file",
        description="Read a text file from the local filesystem.",
        input_schema=INPUT_SCHEMA,
        handler=_handle,
        prompt=PROMPT,
        search_hint="read local text files",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    file_path = str(tool_input["file_path"])
    return ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(ToolTarget(kind="file", operation="read", value=file_path),),
        result_policy=ToolResultPolicy(
            max_result_size_chars=math.inf,
            persist_when_exceeded=False,
            preview_chars=4_000,
        ),
        permission_subject=f"read_file:{file_path}",
    )


def _validate(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ValidationResult:
    offset = tool_input.get("offset", 1)
    limit = tool_input.get("limit")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
        return ValidationResult.failure("offset must be a positive integer.")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        return ValidationResult.failure("limit must be a positive integer.")
    return ValidationResult.success()


def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolExecutionResult:
    if runtime.guard is None:
        raise RuntimeError("read_file requires a sandbox guard.")
    policy = runtime.guard.check_path(tool_input["file_path"], operation="read")
    if not is_guard_policy_allowed(policy, runtime):
        payload = policy.to_tool_error()
        if policy.action == "ask":
            payload["error"] = "path_guard_ask_required"
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="read_file",
            content=json.dumps(payload, ensure_ascii=False),
            is_error=True,
            metadata={"error": payload["error"]},
        )
    path = policy.normalized_path
    if path.is_dir():
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="read_file",
            content=f"Cannot read directory as file: {path}",
            is_error=True,
            metadata={"error": "path_is_directory", "path": str(path)},
        )
    if not path.exists():
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="read_file",
            content=f"File does not exist: {path}",
            is_error=True,
            metadata={"error": "file_not_found", "path": str(path)},
        )

    text = read_text_file(path)
    lines = text.splitlines()
    offset = int(tool_input.get("offset", 1))
    limit = int(tool_input.get("limit", DEFAULT_LIMIT))
    selected = lines[offset - 1 : offset - 1 + limit]
    content = "\n".join(
        f"{line_number}\t{line}"
        for line_number, line in enumerate(selected, start=offset)
    )

    return ToolExecutionResult(
        tool_call_id="",
        tool_name="read_file",
        content=content,
        metadata={
            "path": str(path),
            "offset": offset,
            "line_count": len(selected),
        },
    )
