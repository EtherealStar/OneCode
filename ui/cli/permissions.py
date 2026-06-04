"""Interactive CLI permission panels."""

from __future__ import annotations

import json
from typing import Callable

from services.permissions import PermissionRequest, PermissionResponse

InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]


class CliPermissionPrompter:
    def __init__(
        self,
        *,
        input_func: InputFunc = input,
        output_func: OutputFunc = print,
    ) -> None:
        self._input = input_func
        self._output = output_func

    def request_permission(
        self,
        request: PermissionRequest,
    ) -> PermissionResponse:
        self._output(render_permission_panel(request))
        try:
            choice = self._input("Allow? [y] once  [s] session directory  [n] deny: ")
        except (EOFError, KeyboardInterrupt):
            return PermissionResponse(
                action="deny",
                feedback="Permission prompt was interrupted.",
            )

        normalized = choice.strip().lower()
        if normalized in {"y", "yes"}:
            return PermissionResponse(action="allow", scope="once")
        if normalized in {"s", "session"}:
            return PermissionResponse(action="allow", scope="session")
        return PermissionResponse(
            action="deny",
            feedback="User denied the permission request.",
        )


def render_permission_panel(request: PermissionRequest) -> str:
    tool_name = request.descriptor.name
    if tool_name == "read_file":
        return _read_file_panel(request)
    if tool_name == "edit_file":
        return _edit_file_panel(request)
    if tool_name == "glob":
        return _glob_panel(request)
    if tool_name == "grep":
        return _grep_panel(request)
    if tool_name == "bash":
        return _bash_panel(request)
    return _fallback_panel(request)


def _read_file_panel(request: PermissionRequest) -> str:
    return "\n".join(
        [
            "",
            "Read file permission requested",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
            _options_line("allow reads in this directory for this session"),
        ]
    )


def _edit_file_panel(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    return "\n".join(
        [
            "",
            "Edit file permission requested",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
            f"replace_all: {bool(tool_input.get('replace_all', False))}",
            "",
            "Proposed edit:",
            f"- old_string: {_preview(tool_input.get('old_string', ''))}",
            f"+ new_string: {_preview(tool_input.get('new_string', ''))}",
            _options_line("allow edits in this directory for this session"),
        ]
    )


def _glob_panel(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    return "\n".join(
        [
            "",
            "Search files permission requested",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
            f"pattern: {_preview(tool_input.get('pattern', ''))}",
            f"offset: {tool_input.get('offset', 0)}",
            f"head_limit: {tool_input.get('head_limit', 'default')}",
            _options_line("allow listing this directory for this session"),
        ]
    )


def _grep_panel(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    return "\n".join(
        [
            "",
            "Search contents permission requested",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
            f"pattern: {_preview(tool_input.get('pattern', ''))}",
            f"glob: {_preview(tool_input.get('glob', ''))}",
            f"output_mode: {tool_input.get('output_mode', 'files_with_matches')}",
            _options_line("allow searching this directory for this session"),
        ]
    )


def _bash_panel(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    return "\n".join(
        [
            "",
            "Bash command permission requested",
            f"reason: {request.decision.reason}",
            f"command: {_preview(tool_input.get('command', ''))}",
            f"description: {_preview(tool_input.get('description', ''))}",
            f"read_only: {request.classification.read_only}",
            f"timeout_ms: {tool_input.get('timeout_ms', 'default')}",
            *_target_lines(request),
            _options_line("allow matching directory targets for this session"),
        ]
    )


def _fallback_panel(request: PermissionRequest) -> str:
    return "\n".join(
        [
            "",
            f"Permission requested: {request.descriptor.name}",
            f"reason: {request.decision.reason}",
            json.dumps(request.tool_input, ensure_ascii=False, indent=2),
            _options_line("allow this directory for this session"),
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


def _options_line(session_label: str) -> str:
    return f"[y] allow once  [s] {session_label}  [n] deny"


def _preview(value: object, *, limit: int = 240) -> str:
    text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text
