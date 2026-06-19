"""Guarded exact string edit tool."""

from __future__ import annotations

import json
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
from tools.edit_file.prompt import PROMPT
from utils.text_io import read_text_file, write_text_file


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "old_string": {"type": "string"},
        "new_string": {"type": "string"},
        "replace_all": {"type": "boolean"},
    },
    "required": ["file_path", "old_string", "new_string"],
    "additionalProperties": False,
}


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="edit_file",
        description="Perform exact string replacements in a local text file.",
        input_schema=INPUT_SCHEMA,
        handler=_handle,
        prompt=PROMPT,
        search_hint="edit local text files",
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
        permission_subject=f"edit_file:{file_path}",
    )


def _validate(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ValidationResult:
    if tool_input["old_string"] == tool_input["new_string"]:
        return ValidationResult.failure("old_string and new_string must differ.")
    replace_all = tool_input.get("replace_all", False)
    if not isinstance(replace_all, bool):
        return ValidationResult.failure("replace_all must be a boolean.")
    return ValidationResult.success()


def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolExecutionResult:
    if runtime.guard is None:
        raise RuntimeError("edit_file requires a sandbox guard.")
    policy = runtime.guard.check_write_target(tool_input["file_path"])
    if not is_guard_policy_allowed(policy, runtime):
        payload = policy.to_tool_error()
        if policy.action == "ask":
            payload["error"] = "path_guard_ask_required"
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="edit_file",
            content=json.dumps(payload, ensure_ascii=False),
            is_error=True,
            metadata={"error": payload["error"]},
        )
    path = policy.normalized_path
    old_string = tool_input["old_string"]
    new_string = tool_input["new_string"]
    replace_all = tool_input.get("replace_all", False)

    if path.exists() and path.is_dir():
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="edit_file",
            content=f"Cannot edit directory as file: {path}",
            is_error=True,
            metadata={"error": "path_is_directory", "path": str(path)},
        )

    if not path.exists():
        if old_string != "":
            return ToolExecutionResult(
                tool_call_id="",
                tool_name="edit_file",
                content="Cannot edit missing file unless old_string is empty.",
                is_error=True,
                metadata={"error": "file_not_found", "path": str(path)},
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_file(path, new_string)
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="edit_file",
            content=f"Created {path} with 1 replacement.",
            metadata={"path": str(path), "replacement_count": 1},
        )

    # 已存在文件必须先读后改，确保编辑基于已观察内容，
    # 而不是猜测的路径或过期模型假设。
    if not _was_read(runtime, path):
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="edit_file",
            content="File must be read in this session before editing.",
            is_error=True,
            metadata={"error": "file_not_read", "path": str(path)},
        )

    text = read_text_file(path)
    occurrence_count = text.count(old_string)
    if occurrence_count == 0:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="edit_file",
            content="old_string was not found in the file.",
            is_error=True,
            metadata={"error": "old_string_not_found", "path": str(path)},
        )
    if occurrence_count > 1 and not replace_all:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="edit_file",
            content=(
                "old_string appears multiple times; provide more context or set "
                "replace_all=true."
            ),
            is_error=True,
            metadata={
                "error": "multiple_matches",
                "path": str(path),
                "match_count": occurrence_count,
            },
        )

    replacement_count = occurrence_count if replace_all else 1
    updated = (
        text.replace(old_string, new_string)
        if replace_all
        else text.replace(old_string, new_string, 1)
    )
    write_text_file(path, updated)

    return ToolExecutionResult(
        tool_call_id="",
        tool_name="edit_file",
        content=f"Edited {path} with {replacement_count} replacement(s).",
        metadata={"path": str(path), "replacement_count": replacement_count},
    )


def _was_read(runtime: ToolRuntime, path) -> bool:
    files_read = runtime.state.metadata.get("files_read", set())
    return str(path) in files_read
