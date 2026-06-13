"""Condensed tool result summaries for the default CLI conversation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from services.tools.types import ToolExecutionResult
from ui.cli.views.common import display_path

ToolResultRenderer = Callable[[ToolExecutionResult, Path], str]


def render_tool_result(result: ToolExecutionResult, *, workspace: Path) -> str:
    """Return the default CLI text for a completed tool result."""

    renderer = RENDERERS.get(result.tool_name)
    if renderer is None:
        return render_fallback_tool_result(result)
    try:
        return renderer(result, workspace)
    except Exception:
        return render_fallback_tool_result(result)


def render_fallback_tool_result(result: Any) -> str:
    tool_name = getattr(result, "tool_name", "unknown_tool")
    call_id = getattr(result, "tool_call_id", "unknown_call")
    if getattr(result, "is_error", False):
        return f"[{tool_name} error] call {call_id}"
    return f"[{tool_name}] call {call_id}"


def _render_read_file(result: ToolExecutionResult, workspace: Path) -> str:
    metadata = result.metadata
    if result.is_error:
        return _error_summary("read_file", metadata, workspace)

    line_count = _number(metadata.get("line_count"), default=0)
    path = _metadata_path(metadata, workspace)
    suffix = f" from {path}" if path else ""
    offset = _number(metadata.get("offset"), default=1)
    if offset > 1:
        suffix = f"{suffix} from line {offset}"
    return f"[read_file] Read {line_count} line(s){suffix}"


def _render_grep(result: ToolExecutionResult, workspace: Path) -> str:
    metadata = result.metadata
    if result.is_error:
        return _error_summary("grep", metadata, workspace)

    mode = metadata.get("mode")
    num_files = _number(metadata.get("num_files"), default=0)
    if mode == "count":
        num_matches = _number(metadata.get("num_matches"), default=0)
        summary = f"Found {num_matches} match(es) across {num_files} file(s)"
    elif mode == "content":
        num_lines = _number(metadata.get("num_lines"), default=0)
        summary = f"Found {num_lines} line(s) across {num_files} file(s)"
    else:
        summary = f"Found {num_files} file(s)"
    return _with_pagination("[grep]", summary, metadata)


def _render_glob(result: ToolExecutionResult, workspace: Path) -> str:
    metadata = result.metadata
    if result.is_error:
        return _error_summary("glob", metadata, workspace)

    total = _number(
        metadata.get("total_matches_before_pagination"),
        default=_number(metadata.get("num_files"), default=0),
    )
    shown = _number(metadata.get("num_files"), default=total)
    summary = f"Found {total} file(s)"
    if metadata.get("truncated") is True or shown != total:
        summary = f"{summary}, showing {shown}"
        offset = _number(metadata.get("applied_offset"), default=0)
        if offset:
            summary = f"{summary} after offset {offset}"
    return f"[glob] {summary}"


def _render_bash(result: ToolExecutionResult, workspace: Path) -> str:
    metadata = result.metadata
    if metadata.get("background") is True:
        task_id = _text(metadata.get("task_id"), "unknown_task")
        status = _text(metadata.get("status"), "unknown")
        output_file = metadata.get("output_file")
        suffix = ""
        if output_file:
            suffix = f", output {display_path(str(output_file), workspace)}"
        prefix = "[bash error]" if result.is_error else "[bash]"
        return f"{prefix} Started background task {task_id} ({status}){suffix}"

    prefix = "[bash error]" if result.is_error else "[bash]"
    if result.is_error and metadata.get("error") is not None and metadata.get("exit_code") is None:
        return f"{prefix} {_text(metadata.get('error'), 'error')}"

    exit_code = _number(metadata.get("exit_code"), default=0)
    duration = _number(metadata.get("duration_ms"), default=0)
    stdout_chars = _number(metadata.get("stdout_chars"), default=0)
    stderr_chars = _number(metadata.get("stderr_chars"), default=0)
    timed_out = ", timed out" if metadata.get("timed_out") is True else ""
    return (
        f"{prefix} exit {exit_code} in {duration} ms{timed_out}, "
        f"stdout {stdout_chars} chars, stderr {stderr_chars} chars"
    )


def _render_write_file(result: ToolExecutionResult, workspace: Path) -> str:
    metadata = result.metadata
    if result.is_error:
        return _error_summary("write_file", metadata, workspace)

    operation = str(metadata.get("operation") or "update").lower()
    verb = "Created" if operation == "create" else "Updated"
    path = _metadata_path(metadata, workspace)
    line_count = _number(metadata.get("line_count"), default=0)
    suffix = ", diff truncated" if metadata.get("diff_truncated") is True else ""
    return f"[write_file] {verb} {path} ({line_count} line(s){suffix})"


def _render_edit_file(result: ToolExecutionResult, workspace: Path) -> str:
    metadata = result.metadata
    if result.is_error:
        return _error_summary("edit_file", metadata, workspace)

    path = _metadata_path(metadata, workspace)
    replacement_count = _number(metadata.get("replacement_count"), default=0)
    return f"[edit_file] Edited {path} with {replacement_count} replacement(s)"


def _error_summary(tool_name: str, metadata: dict[str, Any], workspace: Path) -> str:
    error = _text(metadata.get("error"), "error")
    path = _metadata_path(metadata, workspace)
    suffix = f" {path}" if path else ""
    return f"[{tool_name} error] {error}{suffix}"


def _metadata_path(metadata: dict[str, Any], workspace: Path) -> str:
    path = metadata.get("path")
    if not path:
        return ""
    return display_path(str(path), workspace)


def _with_pagination(prefix: str, summary: str, metadata: dict[str, Any]) -> str:
    if metadata.get("truncated") is not True:
        return f"{prefix} {summary}"
    limit = metadata.get("applied_limit")
    offset = _number(metadata.get("applied_offset"), default=0)
    if limit is None:
        return f"{prefix} {summary}, truncated"
    return f"{prefix} {summary}, showing first {limit} after offset {offset}"


def _number(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _text(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


RENDERERS: dict[str, ToolResultRenderer] = {
    "read_file": _render_read_file,
    "grep": _render_grep,
    "glob": _render_glob,
    "bash": _render_bash,
    "write_file": _render_write_file,
    "edit_file": _render_edit_file,
}
