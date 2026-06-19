"""Rich rendering helpers for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from services.background_tasks import BackgroundTaskState
from services.tools.types import ToolExecutionResult
from services.tasks import TaskRecord
from ui.cli.theme import RICH_THEME, SYMBOLS
from ui.cli.tool_renderers import render_fallback_tool_result, render_tool_result
from ui.cli.types import CliRuntime
from ui.cli.views.common import display_path, preview, render_to_text, titled_section
from ui.cli.views.mcp import render_mcp
from ui.cli.views.memory import render_memory
from ui.cli.views.permissions import render_permissions
from ui.cli.views.resume import render_session_summaries
from ui.cli.views.skills import render_skills
from ui.cli.views.status import render_banner, render_status, render_usage
from ui.cli.views.tasks import render_tasks as render_tasks_view


def console() -> Console:
    return Console(theme=RICH_THEME)


def print_renderable(renderable: object | None) -> None:
    if renderable is not None:
        console().print(renderable)


def render_running() -> str:
    return f"{SYMBOLS.loading} Running..."


def render_assistant(text: str) -> str:
    # assistant 回复统一带 onecode> 前缀（非流式路径；流式路径在 app 层单独加一次）。
    body = text if text else "(assistant returned no text)"
    return f"onecode> {body}"


def render_assistant_delta(text: str) -> str:
    return text


def render_tool_result_summary(result: Any, *, workspace: Path | None = None) -> str:
    if isinstance(result, ToolExecutionResult) and workspace is not None:
        return f"\n{render_tool_result(result, workspace=workspace)}"
    return f"\n{render_fallback_tool_result(result)}"


def render_error(message: str) -> Text:
    return Text(f"{SYMBOLS.error} {message}", style="onecode.error")


def render_tools(descriptors: Iterable[Any]) -> Group:
    table = Table(box=None, show_header=True, header_style="onecode.subtle")
    table.add_column("tool")
    table.add_column("description")
    for descriptor in descriptors:
        table.add_row(descriptor.name, descriptor.description)
    return titled_section("Enabled tools", table, style="onecode.info")


def render_tasks(
    runtime: CliRuntime,
    tasks: Iterable[TaskRecord],
    *,
    task_list_id: str | None,
    tasks_dir: Path | None,
    background_tasks: Iterable[BackgroundTaskState] = (),
    durable_error: str | None = None,
) -> Group:
    return render_tasks_view(
        runtime,
        tasks,
        task_list_id=task_list_id,
        tasks_dir=tasks_dir,
        background_tasks=background_tasks,
        durable_error=durable_error,
    )


def render_background_tasks(
    runtime: CliRuntime,
    tasks: Iterable[BackgroundTaskState],
) -> Group:
    return render_tasks_view(
        runtime,
        (),
        task_list_id=None,
        tasks_dir=None,
        background_tasks=tasks,
    )


def render_mcp_status(runtime: CliRuntime, *, show_tools: bool = True) -> Group:
    _ = show_tools
    return render_mcp(runtime)


def render_history(messages: Iterable[dict[str, Any]], *, start_index: int = 1) -> Group:
    items = list(messages)
    table = Table(box=None, show_header=True, header_style="onecode.subtle")
    table.add_column("#", no_wrap=True)
    table.add_column("role")
    table.add_column("detail")
    if not items:
        table.add_row("-", "none", "Recent messages: none")
    for index, message in enumerate(items, start=start_index):
        table.add_row(str(index), _message_role(message), _message_detail(message))
    return titled_section("Recent messages", table, style="onecode.info")


def render_trace(records: Iterable[dict[str, Any]]) -> Group:
    items = list(records)
    table = Table(box=None, show_header=True, header_style="onecode.subtle")
    table.add_column("timestamp")
    table.add_column("type")
    table.add_column("name")
    table.add_column("detail")
    if not items:
        table.add_row("", "", "", "No trace records.")
    for record in items:
        attributes = record.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        detail = " ".join(
            part
            for part in (
                _trace_attribute("duration_ms", attributes),
                _trace_attribute("tool_name", attributes),
                _trace_attribute("transition", attributes),
                _trace_attribute("error", attributes),
                _trace_attribute("error_type", attributes),
            )
            if part
        )
        table.add_row(
            preview(record.get("timestamp")),
            preview(record.get("record_type")),
            preview(record.get("name")),
            detail,
        )
    return titled_section("Recent trace", table, style="onecode.info")


def render_clear(old_session_id: str, new_session_id: str) -> Text:
    return Text(
        (
            f"{SYMBOLS.success} Started new session {new_session_id}. "
            f"Previous session {old_session_id} is still in .onecode/sessions."
        ),
        style="onecode.success",
    )


def render_resume(session_id: str, messages_path: Path, workspace: Path) -> Text:
    return Text(
        (
            f"{SYMBOLS.success} Restored session {session_id} from "
            f"{display_path(messages_path, workspace)}."
        ),
        style="onecode.success",
    )


def render_compact(result: Any, runtime: CliRuntime) -> Group:
    memory_path = (
        runtime.session_memory_store.path
        if runtime.session_memory_store is not None
        else None
    )
    table = Table.grid(padding=(0, 2))
    table.add_column(style="onecode.subtle", no_wrap=True)
    table.add_column()
    table.add_row("trigger", getattr(result, "trigger").value)
    table.add_row(
        "tokens",
        f"{getattr(result, 'token_before')} -> {getattr(result, 'token_after')}",
    )
    table.add_row("messages", str(len(getattr(result, "messages"))))
    table.add_row(
        "transcript",
        display_path(runtime.message_store.transcript_store.messages_path, runtime.workspace),
    )
    if memory_path is not None:
        table.add_row("session memory", display_path(memory_path, runtime.workspace))
    return titled_section("Compacted session", table, style="onecode.success")


def render_unknown_command(command: str) -> Text:
    return render_error(
        f"Unknown command: /{command}. Press Tab after / to see available commands."
    )


def render_group(*renderables: object) -> Group:
    return Group(*renderables)


def _message_role(message: dict[str, Any]) -> str:
    role = message.get("role")
    return role if isinstance(role, str) else "unknown"


def _message_detail(message: dict[str, Any]) -> str:
    role = message.get("role")
    if role == "tool_result":
        tool_name = message.get("tool_name") or "unknown_tool"
        call_id = message.get("tool_call_id") or "unknown_call"
        error = " error" if message.get("is_error") is True else ""
        return f"{tool_name} {call_id}{error}: {preview(message.get('content'))}"

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        names = []
        for call in tool_calls:
            if isinstance(call, dict):
                function = call.get("function")
                if isinstance(function, dict) and isinstance(function.get("name"), str):
                    names.append(function["name"])
        if names:
            return f"<tool call: {', '.join(names)}>"

    return preview(message.get("content"))


def _trace_attribute(name: str, attributes: dict[str, Any]) -> str:
    value = attributes.get(name)
    if value is None:
        return ""
    return f"{name}={preview(value)}"
