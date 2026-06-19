"""Tool result rendering policy for the CLI.

This module is the *single* CLI entry point for rendering tool output.
It is a thin policy dispatcher: each built-in tool has a small renderer
that consumes a :class:`ToolExecutionResult` and returns a one-line
summary. The dynamic region uses :func:`render_use_preview` and
:func:`render_running` to show what's happening right now; the static
region uses :func:`render_tool_result` / :func:`render_fallback_tool_result`
to commit the final summary.

The architecture mirrors the reference implementation's split
between the ``UserToolResultMessage`` container (handled by the
framework) and the per-tool ``renderToolResultMessage`` function
(handled by the policy). In OneCode the framework container lives in
:mod:`ui.cli.terminal.static_output` (the ``⎿`` prefix) and the policy
lives here. Tools do not get to inject their own container prefix.

Renderer rules:

- Renderers are pure functions of the result and the workspace path.
  They must not call the tool, read the file, or run subprocesses.
- Renderers must never raise; if a renderer throws, the dispatcher
  falls back to :func:`render_fallback_tool_result` so the user still
  sees something useful.
- Renderers return plain strings; colour is added by the framework
  layer (static region wraps the line in a style).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from services.tools.types import ToolExecutionResult
from ui.cli.views.common import display_path


# --- policy types ----------------------------------------------------------


class ToolCliRenderer(Protocol):
    """The minimal interface a built-in tool may fulfil.

    Tools are not required to implement every method; missing methods
    cause the dispatcher to fall back to the default renderer for that
    lifecycle. The dynamic region only needs the preview methods; the
    static region only needs the result methods.
    """

    def render_use_preview(self, tool_name: str, tool_input: Any) -> str:
        """A bounded, single-line preview of an in-flight tool call."""

    def render_running(self, tool_name: str, tool_input: Any) -> str:
        """A bounded status line shown while a tool is running."""

    def render_success(self, result: ToolExecutionResult, *, workspace: Path | None) -> str:
        """A one-line summary of a successful tool result."""

    def render_error(self, result: ToolExecutionResult, *, workspace: Path | None) -> str:
        """A one-line summary of a failed tool result."""


@dataclass(frozen=True)
class BuiltinToolRenderer:
    """A concrete implementation of :class:`ToolCliRenderer` for one tool.

    Any field may be ``None``; the dispatcher uses the fallback for
    that lifecycle when the renderer did not opt in.
    """

    name: str
    render_use_preview: Callable[[str, Any], str] | None = None
    render_running: Callable[[str, Any], str] | None = None
    render_success: Callable[[ToolExecutionResult, Path | None], str] | None = None
    render_error: Callable[[ToolExecutionResult, Path | None], str] | None = None


# Backwards-compatibility alias used by existing callers (static
# output). The signature is preserved so the dispatcher table can be
# populated from older ``ToolResultRenderer`` callables.
ToolResultRenderer = Callable[[ToolExecutionResult, Path], str]


# --- public entry points ---------------------------------------------------


def render_tool_result(result: ToolExecutionResult, *, workspace: Path) -> str:
    """Return the static-region summary line for a completed tool result.

    The dispatcher prefers the policy's ``render_success`` /
    ``render_error`` method; if those are missing or raise, it falls
    back to :func:`render_fallback_tool_result`.
    """

    policy = _POLICIES.get(result.tool_name)
    if policy is not None:
        method = policy.render_error if result.is_error else policy.render_success
        if method is not None:
            try:
                return method(result, workspace)
            except Exception:
                pass
    return render_fallback_tool_result(result)


def render_fallback_tool_result(result: Any) -> str:
    """A safe, generic summary used when no policy exists or it raised.

    The fallback must never raise and must always return a string so
    the CLI never crashes on a malformed result.
    """

    tool_name = getattr(result, "tool_name", "unknown_tool") or "unknown_tool"
    call_id = getattr(result, "tool_call_id", "unknown_call") or "unknown_call"
    if getattr(result, "is_error", False):
        return f"[{tool_name} error] call {call_id}"
    return f"[{tool_name}] call {call_id}"


def render_use_preview(tool_name: str, tool_input: Any) -> str:
    """Dynamic-region preview shown when a tool call is first announced.

    The result is intentionally bounded (no full JSON, no unbounded
    paths) because the dynamic region must stay short and stable.
    """

    policy = _POLICIES.get(tool_name)
    if policy is not None and policy.render_use_preview is not None:
        try:
            text = policy.render_use_preview(tool_name, tool_input)
            if text:
                return text
        except Exception:
            pass
    # Generic fallback: a single-line key=value preview.
    return _default_use_preview(tool_name, tool_input)


def render_running(tool_name: str, tool_input: Any) -> str:
    """Dynamic-region status shown while a tool is still in flight.

    Used as a per-line item in the active-tools list. Mirrors the
    Bash tool's ``MAX_COMMAND_DISPLAY_LINES = 2`` / 160-char budget.
    """

    policy = _POLICIES.get(tool_name)
    if policy is not None and policy.render_running is not None:
        try:
            text = policy.render_running(tool_name, tool_input)
            if text:
                return text
        except Exception:
            pass
    return _default_use_preview(tool_name, tool_input)


# --- policy registration ---------------------------------------------------


_POLICIES: dict[str, BuiltinToolRenderer] = {}


def register_renderer(renderer: BuiltinToolRenderer) -> None:
    """Register (or replace) a CLI policy for a tool name.

    Intended for tests and any future plugin that wants to override
    the per-tool summary. Built-in tools are registered at import
    time below.
    """

    _POLICIES[renderer.name] = renderer


def registered_renderers() -> dict[str, BuiltinToolRenderer]:
    """Return a copy of the registered policies (for tests)."""

    return dict(_POLICIES)


# --- defaults --------------------------------------------------------------


def _default_use_preview(tool_name: str, tool_input: Any) -> str:
    """Generic preview used when no tool-specific renderer exists."""

    preview = _summarize_arguments(_as_dict(tool_input), limit=120)
    if preview:
        return f"tool: {tool_name} {preview}"
    return f"tool: {tool_name}"


def _summarize_arguments(arguments: dict[str, Any], *, limit: int = 120) -> str:
    """Format a tool call's input as a bounded one-line preview."""

    parts: list[str] = []
    for key, value in arguments.items():
        rendered = _render_argument_value(value)
        parts.append(f"{key}={rendered}")
        if sum(len(part) for part in parts) > limit:
            break
    text = " ".join(parts)
    if len(text) > limit:
        return text[: max(limit - 1, 0)] + "…"
    return text


def _render_argument_value(value: Any, *, inner_limit: int = 40) -> str:
    if isinstance(value, str):
        compact = " ".join(value.split())
        if len(compact) > inner_limit:
            return f'"{compact[: inner_limit - 1]}…"'
        return f'"{compact}"'
    if isinstance(value, (list, tuple)):
        return f"<{len(value)} items>"
    if isinstance(value, dict):
        return f"<{len(value)} keys>"
    return str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


# --- shared helpers --------------------------------------------------------


def _metadata_path(metadata: dict[str, Any], workspace: Path) -> str:
    path = metadata.get("path")
    if not path:
        return ""
    return display_path(str(path), workspace)


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


def _with_pagination(prefix: str, summary: str, metadata: dict[str, Any]) -> str:
    if metadata.get("truncated") is not True:
        return f"{prefix} {summary}"
    limit = metadata.get("applied_limit")
    offset = _number(metadata.get("applied_offset"), default=0)
    if limit is None:
        return f"{prefix} {summary}, truncated"
    return f"{prefix} {summary}, showing first {limit} after offset {offset}"


def _error_summary(
    tool_name: str,
    metadata: dict[str, Any],
    workspace: Path | None,
) -> str:
    error = _text(metadata.get("error"), "error")
    if workspace is None:
        return f"[{tool_name} error] {error}"
    path = _metadata_path(metadata, workspace)
    suffix = f" {path}" if path else ""
    return f"[{tool_name} error] {error}{suffix}"


# --- per-tool policies -----------------------------------------------------


def _bash_use_preview(tool_name: str, tool_input: Any) -> str:
    """Show the first ~2 lines / 160 chars of the command, per the reference.

    Matches ``BashTool/UI.tsx``'s ``MAX_COMMAND_DISPLAY_LINES = 2`` /
    ``MAX_COMMAND_DISPLAY_CHARS = 160`` budget.
    """

    cmd = ""
    if isinstance(tool_input, dict):
        cmd = str(tool_input.get("command") or "")
    first_lines = "\n".join(cmd.splitlines()[:2])
    if len(first_lines) > 160:
        first_lines = first_lines[:159] + "…"
    if first_lines:
        return f"tool: {tool_name} {first_lines}"
    return f"tool: {tool_name}"


def _bash_running(tool_name: str, tool_input: Any) -> str:
    return _bash_use_preview(tool_name, tool_input)


def _bash_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    if metadata.get("background") is True:
        task_id = _text(metadata.get("task_id"), "unknown_task")
        status = _text(metadata.get("status"), "unknown")
        output_file = metadata.get("output_file")
        suffix = ""
        if output_file and workspace is not None:
            suffix = f", output {display_path(str(output_file), workspace)}"
        return f"[bash] Started background task {task_id} ({status}){suffix}"

    if metadata.get("error") is not None and metadata.get("exit_code") is None:
        return f"[bash error] {_text(metadata.get('error'), 'error')}"

    ws = workspace or Path(".")
    exit_code = _number(metadata.get("exit_code"), default=0)
    duration = _number(metadata.get("duration_ms"), default=0)
    stdout_chars = _number(metadata.get("stdout_chars"), default=0)
    stderr_chars = _number(metadata.get("stderr_chars"), default=0)
    timed_out = ", timed out" if metadata.get("timed_out") is True else ""
    return (
        f"[bash] exit {exit_code} in {duration} ms{timed_out}, "
        f"stdout {stdout_chars} chars, stderr {stderr_chars} chars"
    )


def _bash_error(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    if metadata.get("background") is True:
        task_id = _text(metadata.get("task_id"), "unknown_task")
        return f"[bash error] Background task {task_id} failed"
    if metadata.get("error") is not None and metadata.get("exit_code") is None:
        return f"[bash error] {_text(metadata.get('error'), 'error')}"
    ws = workspace or Path(".")
    exit_code = _number(metadata.get("exit_code"), default=0)
    duration = _number(metadata.get("duration_ms"), default=0)
    stdout_chars = _number(metadata.get("stdout_chars"), default=0)
    stderr_chars = _number(metadata.get("stderr_chars"), default=0)
    timed_out = ", timed out" if metadata.get("timed_out") is True else ""
    return (
        f"[bash error] exit {exit_code} in {duration} ms{timed_out}, "
        f"stdout {stdout_chars} chars, stderr {stderr_chars} chars"
    )


def _read_file_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    ws = workspace or Path(".")
    line_count = _number(metadata.get("line_count"), default=0)
    path = _metadata_path(metadata, ws)
    suffix = f" from {path}" if path else ""
    offset = _number(metadata.get("offset"), default=1)
    if offset > 1:
        suffix = f"{suffix} from line {offset}"
    return f"[read_file] Read {line_count} line(s){suffix}"


def _read_file_use_preview(tool_name: str, tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        path = tool_input.get("path")
        if path:
            return f"tool: {tool_name} path=\"{path}\""
    return _default_use_preview(tool_name, tool_input)


def _grep_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    ws = workspace or Path(".")
    mode = metadata.get("mode")
    num_files = _number(metadata.get("num_files"), default=0)
    if mode == "count":
        num_matches = _number(metadata.get("num_matches"), default=0)
        summary = f"Found {num_matches} matches across {num_files} files"
    elif mode == "content":
        num_matches = _number(
            metadata.get("num_matches"),
            default=_number(metadata.get("num_lines"), default=0),
        )
        summary = f"Found {num_matches} matches across {num_files} files"
    else:
        summary = f"Found {num_files} files"
    return _with_pagination("[grep]", summary, metadata)


def _grep_use_preview(tool_name: str, tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        pattern = tool_input.get("pattern")
        if pattern:
            compact = " ".join(str(pattern).split())
            if len(compact) > 60:
                compact = compact[:59] + "…"
            return f"tool: {tool_name} pattern=\"{compact}\""
    return _default_use_preview(tool_name, tool_input)


def _glob_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    total = _number(
        metadata.get("total_matches_before_pagination"),
        default=_number(metadata.get("num_files"), default=0),
    )
    shown = _number(metadata.get("num_files"), default=total)
    summary = f"Found {total} files"
    if metadata.get("truncated") is True or shown != total:
        summary = f"{summary}, showing {shown}"
        offset = _number(metadata.get("applied_offset"), default=0)
        if offset:
            summary = f"{summary} after offset {offset}"
    return f"[glob] {summary}"


def _glob_use_preview(tool_name: str, tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        pattern = tool_input.get("pattern")
        if pattern:
            return f"tool: {tool_name} pattern=\"{pattern}\""
    return _default_use_preview(tool_name, tool_input)


def _write_file_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    ws = workspace or Path(".")
    operation = str(metadata.get("operation") or "update").lower()
    verb = "Created" if operation == "create" else "Updated"
    path = _metadata_path(metadata, ws)
    line_count = _number(metadata.get("line_count"), default=0)
    suffix = ", diff truncated" if metadata.get("diff_truncated") is True else ""
    return f"[write_file] {verb} {path} ({line_count} line(s){suffix})"


def _edit_file_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    ws = workspace or Path(".")
    path = _metadata_path(metadata, ws)
    replacement_count = _number(metadata.get("replacement_count"), default=0)
    return f"[edit_file] Edited {path} with {replacement_count} replacement(s)"


# Register the built-in policies. Unknown / MCP tools fall through to
# the generic dispatcher in :func:`render_tool_result`.
register_renderer(
    BuiltinToolRenderer(
        name="bash",
        render_use_preview=_bash_use_preview,
        render_running=_bash_running,
        render_success=_bash_success,
        render_error=_bash_error,
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="read_file",
        render_use_preview=_read_file_use_preview,
        render_success=_read_file_success,
        render_error=lambda r, workspace: _error_summary("read_file", r.metadata, workspace),
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="grep",
        render_use_preview=_grep_use_preview,
        render_success=_grep_success,
        render_error=lambda r, workspace: _error_summary("grep", r.metadata, workspace),
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="glob",
        render_use_preview=_glob_use_preview,
        render_success=_glob_success,
        render_error=lambda r, workspace: _error_summary("glob", r.metadata, workspace),
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="write_file",
        render_success=_write_file_success,
        render_error=lambda r, workspace: _error_summary("write_file", r.metadata, workspace),
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="edit_file",
        render_success=_edit_file_success,
        render_error=lambda r, workspace: _error_summary("edit_file", r.metadata, workspace),
    )
)


__all__ = [
    "BuiltinToolRenderer",
    "RENDERERS",  # legacy alias kept for tests
    "ToolCliRenderer",
    "ToolResultRenderer",
    "register_renderer",
    "registered_renderers",
    "render_fallback_tool_result",
    "render_running",
    "render_tool_result",
    "render_use_preview",
]


# --- legacy compatibility -------------------------------------------------


def _legacy_dispatch_for(name: str, result: ToolExecutionResult, workspace: Path) -> str:
    """Adapt the new policy API back to the old ``ToolResultRenderer`` shape.

    Some pre-refactor tests still import a ``RENDERERS`` map and look
    up a callable by tool name. This shim bridges the two APIs without
    forking the policy interface.
    """

    policy = _POLICIES.get(name)
    if policy is None:
        return render_fallback_tool_result(result)
    method = policy.render_error if result.is_error else policy.render_success
    if method is None:
        return render_fallback_tool_result(result)
    try:
        return method(result, workspace)
    except Exception:
        return render_fallback_tool_result(result)


# Some tests and downstream code still import a ``RENDERERS`` map of
# ``ToolResultRenderer`` callables. The map is built once from the
# registered policies.
RENDERERS: dict[str, ToolResultRenderer] = {
    name: (lambda _name=name: (lambda result, workspace: _legacy_dispatch_for(_name, result, workspace)))()
    for name in _POLICIES
}
