"""Guarded text file read tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.tools.types import (
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ValidationResult,
)
from tools.read_file.prompt import PROMPT

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
        read_only=True,
        modifies_filesystem=False,
        requires_guard=True,
        concurrency_safe=True,
        max_result_size_chars=None,
        prompt=PROMPT,
        validate_input=_validate,
        get_path=lambda tool_input: tool_input.get("file_path"),
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
    if policy.action != "allow":
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

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    offset = int(tool_input.get("offset", 1))
    limit = int(tool_input.get("limit", DEFAULT_LIMIT))
    selected = lines[offset - 1 : offset - 1 + limit]
    content = "\n".join(
        f"{line_number}\t{line}"
        for line_number, line in enumerate(selected, start=offset)
    )

    files_read = runtime.state.metadata.setdefault("files_read", set())
    if not isinstance(files_read, set):
        files_read = set(files_read)
        runtime.state.metadata["files_read"] = files_read
    files_read.add(str(path))

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
