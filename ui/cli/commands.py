"""Slash command handling for the CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Thread
from typing import Any

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.context.transcript import JsonlTranscriptStore
from services.tasks import TaskStoreError, resolve_task_list_id
from ui.cli import renderer
from ui.cli.types import CliRuntime, CommandResult


def handle_command(runtime: CliRuntime, line: str) -> CommandResult:
    parts = _split_command(line)
    if not parts:
        return CommandResult()

    command = parts[0].lower()
    args = parts[1:]

    if command == "/help":
        print(renderer.render_help())
        return CommandResult()
    if command == "/tools":
        print(renderer.render_tools(runtime.registry.descriptors()))
        return CommandResult()
    if command == "/tasks":
        print(_tasks(runtime))
        return CommandResult()
    if command == "/mcp":
        print(renderer.render_mcp_status(runtime, show_tools=args[:1] == ["tools"]))
        return CommandResult()
    if command == "/status":
        print(renderer.render_status(runtime))
        return CommandResult()
    if command == "/history":
        print(renderer.render_history(_recent_messages(runtime, args)))
        return CommandResult()
    if command == "/trace":
        print(renderer.render_trace(_recent_trace_records(runtime, args)))
        return CommandResult()
    if command == "/compact":
        print(_compact(runtime, args))
        return CommandResult()
    if command == "/clear":
        return _clear(runtime)
    if command == "/resume":
        result = _resume(runtime, args)
        if result.runtime is not None:
            runtime = result.runtime
        return result
    if command in {"/exit", "/quit"}:
        runtime.message_store.flush_transcript()
        runtime.trace_recorder.flush()
        if runtime.mcp_manager is not None:
            _run_async_blocking(runtime.mcp_manager.close_all())
        return CommandResult(should_exit=True)

    print(renderer.render_error(f"Unknown command: {command}. Use /help."))
    return CommandResult()


def resolve_resume_target(workspace: Path, target: str) -> JsonlTranscriptStore:
    target_path = Path(target).expanduser()
    if not target_path.is_absolute():
        target_path = workspace / target_path

    if target_path.suffix.lower() == ".jsonl" or target_path.is_file():
        messages_path = target_path
    else:
        messages_path = workspace / ".onecode" / target / "messages.jsonl"

    if not messages_path.exists():
        raise ValueError(f"Transcript does not exist: {messages_path}")
    if not messages_path.is_file():
        raise ValueError(f"Transcript target is not a file: {messages_path}")
    if messages_path.suffix.lower() != ".jsonl":
        raise ValueError(f"Transcript target must be a JSONL file: {messages_path}")

    session_dir = messages_path.parent
    return JsonlTranscriptStore(
        session_dir.parent,
        session_dir.name,
        cwd=workspace,
    )


def _split_command(line: str) -> list[str]:
    command, separator, rest = line.strip().partition(" ")
    if not command:
        return []
    if not separator:
        return [command]
    rest = _strip_matching_quotes(rest.strip())
    if command.lower() == "/resume":
        return [command, rest] if rest else [command]
    return [command, *rest.split()]


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _recent_messages(runtime: CliRuntime, args: list[str]) -> tuple[dict, ...]:
    limit = 20
    if args:
        try:
            limit = int(args[0])
        except ValueError:
            print(renderer.render_error("history count must be an integer."))
            return ()
    if limit < 1:
        print(renderer.render_error("history count must be positive."))
        return ()
    messages = runtime.message_store.current_messages()
    return messages[-limit:]


def _recent_trace_records(runtime: CliRuntime, args: list[str]) -> list[dict]:
    limit = 20
    if args:
        try:
            limit = int(args[0])
        except ValueError:
            print(renderer.render_error("trace count must be an integer."))
            return []
    if limit < 1:
        print(renderer.render_error("trace count must be positive."))
        return []
    return runtime.trace_recorder.recent_records(limit)


def _clear(runtime: CliRuntime) -> CommandResult:
    old_session_id = runtime.state.session_id
    runtime.message_store.flush_transcript()
    new_session_id = runtime.state.start_new_session()
    runtime.message_store.clear_for_new_session(new_session_id)
    cleared = runtime.with_session(
        state=runtime.state,
        message_store=runtime.message_store,
    )
    print(renderer.render_clear(old_session_id, new_session_id))
    return CommandResult(runtime=cleared)


def _resume(runtime: CliRuntime, args: list[str]) -> CommandResult:
    if len(args) != 1:
        print(renderer.render_error("Usage: /resume <session-id-or-messages.jsonl>"))
        return CommandResult()

    try:
        transcript_store = resolve_resume_target(runtime.workspace, args[0])
        if not transcript_store.load_messages():
            raise ValueError(f"Transcript has no loadable messages: {transcript_store.messages_path}")
        runtime.message_store.flush_transcript()
        state = RuntimeState(max_turns=runtime.state.max_turns)
        message_store = MessageStore.from_transcript(transcript_store, state)
    except Exception as exc:
        print(renderer.render_error(str(exc)))
        return CommandResult()

    resumed = runtime.with_session(state=state, message_store=message_store)
    print(
        renderer.render_resume(
            resumed.state.session_id,
            resumed.message_store.transcript_store.messages_path,
            resumed.workspace,
        )
    )
    return CommandResult(runtime=resumed)


def _compact(runtime: CliRuntime, args: list[str]) -> str:
    if runtime.compaction_service is None:
        return renderer.render_error("Compaction is not enabled for this runtime.")
    focus = " ".join(args).strip() or None
    try:
        result = _run_async_blocking(
            runtime.compaction_service.manual_compact(runtime.state, focus=focus)
        )
    except Exception as exc:
        return renderer.render_error(str(exc))
    runtime.message_store.flush_transcript()
    runtime.trace_recorder.flush()
    return renderer.render_compact(result, runtime)


def _tasks(runtime: CliRuntime) -> str:
    if runtime.task_store is None:
        return renderer.render_error("Task tracking is not enabled for this runtime.")
    task_list_id = resolve_task_list_id(runtime.state)
    try:
        tasks = runtime.task_store.list_tasks(task_list_id)
    except TaskStoreError as exc:
        return renderer.render_error(str(exc))
    return renderer.render_tasks(
        runtime,
        tasks,
        task_list_id=task_list_id,
        tasks_dir=runtime.task_store.tasks_dir(task_list_id),
    )


def _run_async_blocking(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(awaitable)
        except BaseException as exc:
            result["error"] = exc

    thread = Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")
