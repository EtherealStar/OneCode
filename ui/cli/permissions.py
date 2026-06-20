"""Permission request summary rendering for CLI surfaces."""

from __future__ import annotations

import json

from services.permissions import PermissionRequest


def render_permission_request_summary(request: PermissionRequest) -> str:
    """Render the tool request details shown before permission choices.

    This module deliberately contains no input handling and no permission
    response construction. TTY, batch, and tests should build responses from
    ``request.options`` so the UI cannot add project-level grants.
    """

    tool_name = request.descriptor.name
    if tool_name == "read_file":
        return _read_file_summary(request)
    if tool_name == "edit_file":
        return _edit_file_summary(request)
    if tool_name == "write_file":
        return _write_file_summary(request)
    if tool_name == "glob":
        return _glob_summary(request)
    if tool_name == "grep":
        return _grep_summary(request)
    if tool_name == "bash":
        return _bash_summary(request)
    return _fallback_summary(request)


def _read_file_summary(request: PermissionRequest) -> str:
    return "\n".join(
        [
            "Read_file",
            "",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
        ]
    )


def _edit_file_summary(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    return "\n".join(
        [
            "Edit_file",
            "",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
            f"replace_all: {bool(tool_input.get('replace_all', False))}",
            "",
            "Proposed edit:",
            f"- old_string: {_preview(tool_input.get('old_string', ''))}",
            f"+ new_string: {_preview(tool_input.get('new_string', ''))}",
        ]
    )


def _write_file_summary(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    content = str(tool_input.get("content", ""))
    return "\n".join(
        [
            "Write_file",
            "",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
            f"line_count: {_line_count(content)}",
            f"content_preview: {_preview(content)}",
        ]
    )


def _glob_summary(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    return "\n".join(
        [
            "Glob",
            "",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
            f"pattern: {_preview(tool_input.get('pattern', ''))}",
            f"offset: {tool_input.get('offset', 0)}",
            f"head_limit: {tool_input.get('head_limit', 'default')}",
        ]
    )


def _grep_summary(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    return "\n".join(
        [
            "Grep",
            "",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
            f"pattern: {_preview(tool_input.get('pattern', ''))}",
            f"glob: {_preview(tool_input.get('glob', ''))}",
            f"output_mode: {tool_input.get('output_mode', 'files_with_matches')}",
        ]
    )


def _bash_summary(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    return "\n".join(
        [
            "Bash",
            "",
            f"reason: {request.decision.reason}",
            f"command: {_preview(tool_input.get('command', ''))}",
            f"description: {_preview(tool_input.get('description', ''))}",
            f"read_only: {request.classification.read_only}",
            f"timeout_ms: {tool_input.get('timeout_ms', 'default')}",
            *_target_lines(request),
        ]
    )


def _fallback_summary(request: PermissionRequest) -> str:
    return "\n".join(
        [
            request.descriptor.name,
            "",
            f"reason: {request.decision.reason}",
            json.dumps(request.tool_input, ensure_ascii=False, indent=2),
        ]
    )


def _target_lines(request: PermissionRequest) -> list[str]:
    lines: list[str] = []
    for index, policy in enumerate(request.decision.guard_policies, start=1):
        prefix = "target" if index == 1 else f"target {index}"
        lines.append(f"{prefix}: {policy.original_path}")
        lines.append(f"normalized: {policy.normalized_path}")
        lines.append(f"operation: {policy.operation}")
    if not lines:
        lines.append("target: unknown")
    return lines


def _preview(value: object, *, limit: int = 240) -> str:
    text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _line_count(content: str) -> int:
    if content == "":
        return 0
    return len(content.splitlines())


__all__ = ["render_permission_request_summary"]
