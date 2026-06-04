"""Slash command handling for the CLI."""

from __future__ import annotations

from pathlib import Path

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.context.transcript import JsonlTranscriptStore
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
    if command == "/status":
        print(renderer.render_status(runtime))
        return CommandResult()
    if command == "/history":
        print(renderer.render_history(_recent_messages(runtime, args)))
        return CommandResult()
    if command == "/clear":
        print(_clear(runtime))
        return CommandResult()
    if command == "/resume":
        result = _resume(runtime, args)
        if result.runtime is not None:
            runtime = result.runtime
        return result
    if command in {"/exit", "/quit"}:
        runtime.message_store.flush_transcript()
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


def _clear(runtime: CliRuntime) -> str:
    old_session_id = runtime.state.session_id
    runtime.message_store.flush_transcript()
    new_session_id = runtime.state.start_new_session()
    runtime.message_store.clear_for_new_session(new_session_id)
    return renderer.render_clear(old_session_id, new_session_id)


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
