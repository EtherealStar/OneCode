"""Plain text rendering helpers for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from services.background_tasks import BackgroundTaskState
from services.tasks import TaskRecord
from ui.cli.types import CliRuntime

PREVIEW_CHARS = 180


def render_banner(runtime: CliRuntime) -> str:
    return "\n".join(
        [
            "OneCode CLI",
            f"cwd: {runtime.workspace}",
            f"session: {runtime.state.session_id}",
            f"model: {runtime.provider_label} / {runtime.model}",
            "commands: /help /tools /tasks /background-tasks /mcp /status /history /trace /compact /resume /clear /exit",
        ]
    )


def render_help() -> str:
    return "\n".join(
        [
            "Commands:",
            "  /help              Show commands.",
            "  /tools             List enabled tools.",
            "  /tasks             Show current durable task list.",
            "  /background-tasks  Show in-process background execution tasks.",
            "  /mcp [tools]       Show MCP server status and discovered tools.",
            "  /status            Show current runtime status.",
            "  /history [n]       Show recent message summaries.",
            "  /trace [n]         Show recent trace event summaries.",
            "  /compact [focus]   Compact the active session context.",
            "  /resume <target>   Restore .onecode session id or messages.jsonl path.",
            "  /clear             Start a fresh session without deleting old transcripts.",
            "  /exit, /quit       Flush transcript and exit.",
        ]
    )


def render_running() -> str:
    return "Running..."


def render_assistant(text: str) -> str:
    return text if text else "(assistant returned no text)"


def render_assistant_delta(text: str) -> str:
    return text


def render_tool_result_summary(result: Any) -> str:
    status = "error" if getattr(result, "is_error", False) else "ok"
    tool_name = getattr(result, "tool_name", "unknown_tool")
    call_id = getattr(result, "tool_call_id", "unknown_call")
    return f"\n[{tool_name} {call_id} {status}]"


def render_error(message: str) -> str:
    return f"Error: {message}"


def render_tools(descriptors: Iterable[Any]) -> str:
    lines = ["Enabled tools:"]
    for descriptor in descriptors:
        lines.append(f"  {descriptor.name}: {descriptor.description}")
    return "\n".join(lines)


def render_tasks(
    runtime: CliRuntime,
    tasks: Iterable[TaskRecord],
    *,
    task_list_id: str,
    tasks_dir: Path,
) -> str:
    items = [task for task in tasks if task.metadata.get("_internal") is not True]
    if not items:
        return f"No tasks found for task list {task_list_id}."
    by_id = {task.id: task for task in items}
    lines = [
        "Tasks:",
        f"  task list: {task_list_id}",
        f"  path: {_display_path(tasks_dir, runtime.workspace)}",
    ]
    for task in items:
        suffix = ""
        unfinished_blockers = [
            blocker_id
            for blocker_id in task.blocked_by
            if by_id.get(blocker_id) is not None
            and by_id[blocker_id].status != "completed"
        ]
        if task.owner:
            suffix += f" owner={task.owner}"
        if unfinished_blockers:
            suffix += " [blocked by " + ", ".join(f"#{item}" for item in unfinished_blockers) + "]"
        lines.append(f"  #{task.id} [{task.status}] {task.subject}{suffix}")
    return "\n".join(lines)


def render_background_tasks(
    runtime: CliRuntime,
    tasks: Iterable[BackgroundTaskState],
) -> str:
    items = list(tasks)
    if not items:
        return "Background tasks: none"
    lines = ["Background tasks:"]
    for task in items[-20:]:
        lines.append(f"  {task.id} [{task.type} {task.status}] {task.description}")
        lines.append(f"    output: {_display_path(Path(task.output_file), runtime.workspace)}")
        detail = _background_task_detail(task)
        if detail:
            lines.append(f"    {detail}")
    return "\n".join(lines)


def render_status(runtime: CliRuntime) -> str:
    usage = runtime.state.usage
    transition = (
        runtime.state.last_transition.value
        if runtime.state.last_transition is not None
        else "none"
    )
    transcript_path = runtime.message_store.transcript_store.messages_path
    trace_path = runtime.trace_recorder.trace_path
    trace_display = (
        _display_path(trace_path, runtime.workspace)
        if trace_path is not None
        else "disabled"
    )
    error_log_path = runtime.error_log_recorder.error_log_path
    error_log_display = (
        _display_path(error_log_path, runtime.workspace)
        if error_log_path is not None
        else "disabled"
    )
    compaction = runtime.state.metadata.get("last_compaction")
    compact_lines = _compact_status_lines(runtime, compaction)
    memory_lines = _long_term_memory_status_lines(runtime)
    mcp_lines = _mcp_status_lines(runtime)
    background_lines = _background_task_status_lines(runtime)
    return "\n".join(
        [
            "Status:",
            f"  workspace: {runtime.workspace}",
            f"  session: {runtime.state.session_id}",
            f"  provider: {runtime.provider_label}",
            f"  model: {runtime.model}",
            f"  turns: {runtime.state.turn_count}/{runtime.state.max_turns}",
            f"  last transition: {transition}",
            (
                "  usage: "
                f"input={usage.input_tokens}, output={usage.output_tokens}, "
                f"cache_read={usage.cache_read_input_tokens}, "
                f"cache_write={usage.cache_creation_input_tokens}"
            ),
            f"  transcript: {_display_path(transcript_path, runtime.workspace)}",
            f"  trace: {trace_display}",
            f"  errors: {error_log_display}",
            *mcp_lines,
            *background_lines,
            *memory_lines,
            *compact_lines,
        ]
    )


def render_mcp_status(runtime: CliRuntime, *, show_tools: bool = False) -> str:
    if runtime.mcp_manager is None:
        return "MCP: disabled"
    snapshot = runtime.mcp_manager.snapshot()
    if not snapshot.statuses:
        return "MCP: no servers configured"
    lines = ["MCP servers:"]
    for status in snapshot.statuses:
        detail = f"  {status.name} [{status.transport}] {status.state}"
        if status.state == "connected":
            detail += f" tools={status.tool_count}"
            if status.instructions_present:
                detail += " instructions=yes"
        if status.error:
            detail += f" error={status.error}"
        lines.append(detail)
    if show_tools:
        lines.append("MCP tools:")
        if not snapshot.tools:
            lines.append("  none")
        for tool in snapshot.tools:
            lines.append(
                f"  {tool.descriptor_name}: {tool.server_name}/{tool.tool_name}"
            )
    return "\n".join(lines)


def render_history(messages: Iterable[dict[str, Any]], *, start_index: int = 1) -> str:
    items = list(messages)
    if not items:
        return "Recent messages: none"

    lines = ["Recent messages:"]
    for index, message in enumerate(items, start=start_index):
        role = _message_role(message)
        detail = _message_detail(message)
        lines.append(f"[{index}] {role}: {detail}")
    return "\n".join(lines)


def render_trace(records: Iterable[dict[str, Any]]) -> str:
    items = list(records)
    if not items:
        return "No trace records."

    lines = ["Recent trace:"]
    for record in items:
        attributes = record.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        lines.append(
            " ".join(
                part
                for part in (
                    _preview(record.get("timestamp")),
                    _preview(record.get("record_type")),
                    _preview(record.get("name")),
                    _trace_attribute("duration_ms", attributes),
                    _trace_attribute("tool_name", attributes),
                    _trace_attribute("transition", attributes),
                    _trace_attribute("error", attributes),
                    _trace_attribute("error_type", attributes),
                )
                if part
            )
        )
    return "\n".join(lines)


def render_clear(old_session_id: str, new_session_id: str) -> str:
    return (
        f"Started new session {new_session_id}. "
        f"Previous session {old_session_id} is still in .onecode."
    )


def render_resume(session_id: str, messages_path: Path, workspace: Path) -> str:
    return (
        f"Restored session {session_id} from "
        f"{_display_path(messages_path, workspace)}."
    )


def render_compact(result: Any, runtime: CliRuntime) -> str:
    memory_path = (
        runtime.session_memory_store.path
        if runtime.session_memory_store is not None
        else None
    )
    lines = [
        "Compacted session:",
        f"  trigger: {getattr(result, 'trigger').value}",
        f"  tokens: {getattr(result, 'token_before')} -> {getattr(result, 'token_after')}",
        f"  messages: {len(getattr(result, 'messages'))}",
        (
            "  transcript: "
            f"{_display_path(runtime.message_store.transcript_store.messages_path, runtime.workspace)}"
        ),
    ]
    if memory_path is not None:
        lines.append(f"  session memory: {_display_path(memory_path, runtime.workspace)}")
    return "\n".join(lines)


def _message_role(message: dict[str, Any]) -> str:
    role = message.get("role")
    return role if isinstance(role, str) else "unknown"


def _message_detail(message: dict[str, Any]) -> str:
    role = message.get("role")
    if role == "tool_result":
        tool_name = message.get("tool_name") or "unknown_tool"
        call_id = message.get("tool_call_id") or "unknown_call"
        error = " error" if message.get("is_error") is True else ""
        return f"{tool_name} {call_id}{error}: {_preview(message.get('content'))}"

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

    return _preview(message.get("content"))


def _preview(value: Any) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        text = " ".join(_preview_block(block) for block in value)
    elif value is None:
        text = ""
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > PREVIEW_CHARS:
        return f"{text[:PREVIEW_CHARS]}..."
    return text


def _preview_block(block: Any) -> str:
    if isinstance(block, dict):
        text = block.get("text")
        if isinstance(text, str):
            return text
    return str(block)


def _trace_attribute(name: str, attributes: dict[str, Any]) -> str:
    value = attributes.get(name)
    if value is None:
        return ""
    return f"{name}={_preview(value)}"


def _compact_status_lines(runtime: CliRuntime, compaction: Any) -> list[str]:
    lines: list[str] = []
    if runtime.compaction_service is not None:
        config = runtime.compaction_service.config
        lines.extend(
            [
                f"  auto compact threshold: {config.auto_compact_threshold_tokens}",
                (
                    "  auto compact failures: "
                    f"{runtime.state.metadata.get('auto_compact_failure_count', 0)}"
                ),
            ]
        )
    if isinstance(compaction, dict):
        lines.append(
            "  last compact: "
            f"{compaction.get('trigger', 'unknown')} "
            f"{compaction.get('token_before', 0)}->{compaction.get('token_after', 0)}"
        )
    if runtime.session_memory_store is not None:
        memory = runtime.session_memory_store.read()
        if memory is None:
            lines.append(
                f"  session memory: {_display_path(runtime.session_memory_store.path, runtime.workspace)} (missing)"
            )
        else:
            lines.append(
                "  session memory: "
                f"{_display_path(runtime.session_memory_store.path, runtime.workspace)} "
                f"updated={memory.updated_at or 'unknown'}"
            )
        extraction = runtime.state.metadata.get("session_memory_extraction")
        if isinstance(extraction, dict):
            lines.append(
                "  memory extraction: "
                f"{extraction.get('last_status', 'unknown')} "
                f"tokens={extraction.get('message_tokens', 0)} "
                f"tools={extraction.get('tool_call_count', 0)} "
                f"running={extraction.get('running', False)}"
            )
    return lines


def _mcp_status_lines(runtime: CliRuntime) -> list[str]:
    if runtime.mcp_manager is None:
        return ["  mcp: disabled"]
    snapshot = runtime.mcp_manager.snapshot()
    if not snapshot.statuses:
        return ["  mcp: no servers configured"]
    connected = sum(1 for status in snapshot.statuses if status.state == "connected")
    failed = sum(1 for status in snapshot.statuses if status.state == "failed")
    disabled = sum(1 for status in snapshot.statuses if status.state == "disabled")
    untrusted = sum(1 for status in snapshot.statuses if status.state == "untrusted")
    tool_count = sum(status.tool_count for status in snapshot.statuses)
    return [
        (
            "  mcp: "
            f"servers={len(snapshot.statuses)} connected={connected} "
            f"failed={failed} disabled={disabled} untrusted={untrusted} "
            f"tools={tool_count}"
        )
    ]


def _long_term_memory_status_lines(runtime: CliRuntime) -> list[str]:
    store = runtime.long_term_memory_store
    if store is None:
        return ["  long-term memory: disabled"]
    topic_count = len(store.scan())
    index_state = "present" if store.entrypoint_path.exists() else "missing"
    lines = [
        f"  long-term memory dir: {_display_path(store.memory_dir, runtime.workspace)}",
        f"  long-term memory index: {index_state}",
        f"  long-term memory topics: {topic_count}",
    ]
    extraction = runtime.state.metadata.get("long_term_memory_extraction")
    if isinstance(extraction, dict):
        lines.append(
            "  long-term memory extraction: "
            f"{extraction.get('last_status', extraction.get('last_decision', 'unknown'))} "
            f"running={extraction.get('running', False)}"
        )
    surfaced = runtime.state.metadata.get("long_term_memory_surface_paths")
    if isinstance(surfaced, list) and surfaced:
        lines.append(f"  relevant memories surfaced: {', '.join(str(item) for item in surfaced[:5])}")
    return lines


def _background_task_status_lines(runtime: CliRuntime) -> list[str]:
    manager = runtime.background_task_manager
    if manager is None:
        return ["  background tasks: disabled"]
    tasks = manager.list_tasks()
    running = sum(1 for task in tasks if task.status == "running")
    completed = sum(1 for task in tasks if task.status == "completed")
    failed = sum(1 for task in tasks if task.status == "failed")
    killed = sum(1 for task in tasks if task.status == "killed")
    return [
        (
            "  background tasks: "
            f"total={len(tasks)} running={running} completed={completed} "
            f"failed={failed} killed={killed}"
        )
    ]


def _background_task_detail(task: BackgroundTaskState) -> str:
    if task.type == "local_bash":
        parts = []
        if "exit_code" in task.metadata:
            parts.append(f"exit_code={task.metadata.get('exit_code')}")
        if task.metadata.get("timed_out") is True:
            parts.append("timed_out=true")
        return " ".join(parts)
    if task.type == "local_agent":
        child = task.metadata.get("child_session_id")
        return f"child_session_id={child}" if child else ""
    if task.type == "dream":
        child = task.metadata.get("result_session_id")
        return f"result_session_id={child}" if child else ""
    return ""


def _display_path(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)
