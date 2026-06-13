"""Runtime overview and usage views."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ui.cli.theme import MASCOT_CAT, SYMBOLS
from ui.cli.types import CliRuntime
from ui.cli.views.common import display_path, key_value_table, titled_section


def render_banner(runtime: CliRuntime) -> Table:
    # 左列彩色小猫吉祥物，右列产品名/工作区/模型信息，双列网格无边框。
    mascot = Text(MASCOT_CAT, style="onecode.mascot")
    info = Group(
        Text("OneCode", style="onecode.title"),
        Text(str(runtime.workspace), style="onecode.path"),
        Text(runtime.model, style="onecode.model"),
    )
    grid = Table.grid(padding=(0, 2))
    grid.add_column()
    grid.add_column()
    grid.add_row(mascot, info)
    return grid


def render_status(runtime: CliRuntime) -> Group:
    usage = runtime.state.usage
    transition = (
        runtime.state.last_transition.value
        if runtime.state.last_transition is not None
        else "none"
    )
    table = key_value_table()
    table.add_row("workspace", str(runtime.workspace))
    table.add_row("session", runtime.state.session_id)
    table.add_row("provider", runtime.provider_label)
    table.add_row("model", runtime.model)
    table.add_row("turns", f"{runtime.state.turn_count}/{runtime.state.max_turns}")
    table.add_row("last transition", transition)
    table.add_row(
        "usage",
        (
            f"input={usage.input_tokens}, output={usage.output_tokens}, "
            f"cache_read={usage.cache_read_input_tokens}, "
            f"cache_write={usage.cache_creation_input_tokens}"
        ),
    )
    table.add_row(
        "transcript",
        display_path(runtime.message_store.transcript_store.messages_path, runtime.workspace),
    )
    trace_path = runtime.trace_recorder.trace_path
    table.add_row(
        "trace",
        display_path(trace_path, runtime.workspace) if trace_path is not None else "disabled",
    )
    error_path = runtime.error_log_recorder.error_log_path
    table.add_row(
        "errors",
        display_path(error_path, runtime.workspace) if error_path is not None else "disabled",
    )
    table.add_row("mcp", _mcp_summary(runtime))
    table.add_row("background tasks", _background_task_summary(runtime))
    table.add_row("memory", _memory_summary(runtime))
    table.add_row("compaction", _compaction_summary(runtime))
    return titled_section("Status", table, style="onecode.info")


def render_usage(runtime: CliRuntime) -> Group:
    usage = runtime.state.usage
    table = key_value_table()
    table.add_row("input tokens", str(usage.input_tokens))
    table.add_row("output tokens", str(usage.output_tokens))
    table.add_row("cache read tokens", str(usage.cache_read_input_tokens))
    table.add_row("cache write tokens", str(usage.cache_creation_input_tokens))
    table.add_row("turns", f"{runtime.state.turn_count}/{runtime.state.max_turns}")
    if runtime.compaction_service is not None:
        config = runtime.compaction_service.config
        table.add_row(
            "auto compact threshold",
            str(config.auto_compact_threshold_tokens),
        )
        table.add_row(
            "auto compact failures",
            str(runtime.state.metadata.get("auto_compact_failure_count", 0)),
        )
    else:
        table.add_row("auto compact threshold", "disabled")
    compaction = runtime.state.metadata.get("last_compaction")
    if isinstance(compaction, dict):
        table.add_row(
            "last compact",
            (
                f"{compaction.get('trigger', 'unknown')} "
                f"{compaction.get('token_before', 0)}->{compaction.get('token_after', 0)}"
            ),
        )
    else:
        table.add_row("last compact", "none")
    return titled_section("Usage", table, style="onecode.metric")


def _mcp_summary(runtime: CliRuntime) -> str:
    if runtime.mcp_manager is None:
        return "disabled"
    snapshot = runtime.mcp_manager.snapshot()
    if not snapshot.statuses:
        return "no servers configured"
    connected = sum(1 for status in snapshot.statuses if status.state == "connected")
    failed = sum(1 for status in snapshot.statuses if status.state == "failed")
    disabled = sum(1 for status in snapshot.statuses if status.state == "disabled")
    untrusted = sum(1 for status in snapshot.statuses if status.state == "untrusted")
    tool_count = sum(status.tool_count for status in snapshot.statuses)
    return (
        f"servers={len(snapshot.statuses)} connected={connected} "
        f"failed={failed} disabled={disabled} untrusted={untrusted} tools={tool_count}"
    )


def _background_task_summary(runtime: CliRuntime) -> str:
    manager = runtime.background_task_manager
    if manager is None:
        return "disabled"
    tasks = manager.list_tasks()
    running = sum(1 for task in tasks if task.status == "running")
    completed = sum(1 for task in tasks if task.status == "completed")
    failed = sum(1 for task in tasks if task.status == "failed")
    killed = sum(1 for task in tasks if task.status == "killed")
    return (
        f"total={len(tasks)} running={running} completed={completed} "
        f"failed={failed} killed={killed}"
    )


def _memory_summary(runtime: CliRuntime) -> str:
    session = "session=disabled"
    if runtime.session_memory_store is not None:
        memory = runtime.session_memory_store.read()
        session = "session=present" if memory is not None else "session=missing"
    store = runtime.long_term_memory_store
    if store is None:
        return f"{session} long-term=disabled"
    return f"{session} long-term topics={len(store.scan())}"


def _compaction_summary(runtime: CliRuntime) -> str:
    if runtime.compaction_service is None:
        return "disabled"
    compaction = runtime.state.metadata.get("last_compaction")
    if isinstance(compaction, dict):
        return (
            f"{compaction.get('trigger', 'unknown')} "
            f"{compaction.get('token_before', 0)}->{compaction.get('token_after', 0)}"
        )
    return f"{SYMBOLS.pending} ready"
