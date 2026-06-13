"""Interactive CLI permission panels."""

from __future__ import annotations

import asyncio
import json
from typing import Callable

from services.permissions import (
    PermissionRequest,
    PermissionResponse,
    PermissionRuleValue,
    PermissionUpdate,
)
from ui.cli.input import ConfirmOption, read_confirm_sync

InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]


class CliPermissionPrompter:
    def __init__(
        self,
        *,
        input_func: InputFunc | None = None,
        output_func: OutputFunc = print,
    ) -> None:
        self._input = input_func
        self._output = output_func

    async def request_permission(
        self,
        request: PermissionRequest,
    ) -> PermissionResponse:
        self._output(render_permission_panel(request))
        try:
            if self._input is None:
                choice = await asyncio.to_thread(
                    read_confirm_sync,
                    _prompt_line(request),
                    _confirm_options(request),
                )
            else:
                choice = await asyncio.to_thread(
                    self._input,
                    _prompt_line(request),
                )
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
        if normalized in {"p", "project"} and request.descriptor.name == "bash":
            return PermissionResponse(
                action="allow",
                scope="project",
                permission_updates=(
                    PermissionUpdate(
                        type="addRules",
                        rules=(
                            PermissionRuleValue(
                                tool_name="bash",
                                rule_content=_bash_project_rule_content(request),
                            ),
                        ),
                        behavior="allow",
                        destination="projectSettings",
                    ),
                ),
            )
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
    if tool_name == "write_file":
        return _write_file_panel(request)
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


def _write_file_panel(request: PermissionRequest) -> str:
    tool_input = request.tool_input
    content = str(tool_input.get("content", ""))
    return "\n".join(
        [
            "",
            "Write file permission requested",
            f"reason: {request.decision.reason}",
            *_target_lines(request),
            f"line_count: {_line_count(content)}",
            f"content_preview: {_preview(content)}",
            _options_line("allow writes in this directory for this session"),
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
            _options_line(
                "allow matching directory targets for this session",
                project_label="allow this command prefix for this project",
            ),
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


def _options_line(session_label: str, *, project_label: str | None = None) -> str:
    parts = [f"[y] allow once", f"[s] {session_label}"]
    if project_label is not None:
        parts.append(f"[p] {project_label}")
    parts.append("[n] deny")
    return "  ".join(parts)


def _prompt_line(request: PermissionRequest) -> str:
    if request.descriptor.name == "bash":
        return "Allow? [y] once  [s] session directory  [p] project rule  [n] deny: "
    return "Allow? [y] once  [s] session directory  [n] deny: "


def _confirm_options(request: PermissionRequest) -> tuple[ConfirmOption, ...]:
    options = [
        ConfirmOption("y", "y once", aliases=("yes",)),
        ConfirmOption("s", "s session", aliases=("session",)),
    ]
    if request.descriptor.name == "bash":
        options.append(ConfirmOption("p", "p project", aliases=("project",)))
    options.append(ConfirmOption("n", "n deny", aliases=("no", "deny")))
    return tuple(options)


def _bash_project_rule_content(request: PermissionRequest) -> str:
    command = " ".join(str(request.tool_input.get("command", "")).split())
    if command.startswith("npm run "):
        return "npm run:*"
    return command


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
