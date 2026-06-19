"""Slash command registry and dispatch for the CLI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shlex
from threading import Thread
from typing import Any, Callable, Iterable

from services.tasks import TaskStoreError, resolve_task_list_id
from infrastructure.filesystem.onecode_paths import sessions_dir
from services.permissions import (
    PermissionBehavior,
    PermissionUpdate,
    PermissionUpdateType,
    permission_rule_value_from_string,
)
from ui.cli import renderer
from ui.cli.resume import list_session_summaries
from ui.cli.resume import resolve_resume_target as _resolve_resume_target
from ui.cli.resume import restore_runtime_from_target
from ui.cli.types import CliRuntime, CommandResult

CommandHandler = Callable[["CliRuntime", "CommandInvocation"], CommandResult]
ParameterCompleter = Callable[["CliRuntime", str], Iterable[str]]


@dataclass(frozen=True)
class CommandInvocation:
    raw: str
    name: str
    args: tuple[str, ...]
    arg_text: str


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    handler: CommandHandler
    argument_hint: str = ""
    category: str = "general"
    aliases: tuple[str, ...] = ()
    visible: bool = True
    immediate: bool = True
    parameter_completer: ParameterCompleter | None = None

    @property
    def display_name(self) -> str:
        return f"/{self.name}"


def command_registry() -> tuple[CommandSpec, ...]:
    return (
        CommandSpec("status", "Show runtime status.", _status),
        CommandSpec("usage", "Show token and turn usage.", _usage),
        CommandSpec("memory", "Show session and long-term memory state.", _memory),
        CommandSpec(
            "permissions",
            "Show permission grants or edit project rules.",
            _permissions,
            "[add|remove|replace allow|deny|ask <rule...>]",
        ),
        CommandSpec("skills", "Show visible skills.", _skills),
        CommandSpec("tasks", "Show durable and background tasks.", _tasks),
        CommandSpec("mcp", "Show MCP servers and discovered tools.", _mcp),
        CommandSpec("compact", "Compact the active session context.", _compact, "[focus]"),
        CommandSpec(
            "resume",
            "Restore a previous session.",
            _resume,
            "[session-id-or-title-or-messages.jsonl]",
            aliases=("continue",),
            parameter_completer=_resume_candidates,
        ),
        CommandSpec("connect", "Configure model provider credentials.", _connect),
        CommandSpec("clear", "Start a fresh session.", _clear),
        CommandSpec("exit", "Flush state and exit.", _exit),
    )


def visible_commands() -> tuple[CommandSpec, ...]:
    return tuple(spec for spec in command_registry() if spec.visible)


def dispatch_command(runtime: CliRuntime, line: str) -> CommandResult:
    invocation = _parse_invocation(line)
    if invocation is None:
        return CommandResult()
    spec = _spec_by_name().get(invocation.name)
    if spec is None:
        return CommandResult(renderable=renderer.render_unknown_command(invocation.name))
    return spec.handler(runtime, invocation)


def resolve_resume_target(workspace: Path, target: str):
    return _resolve_resume_target(workspace, target)


def _spec_by_name() -> dict[str, CommandSpec]:
    specs: dict[str, CommandSpec] = {}
    for spec in command_registry():
        specs[spec.name] = spec
        for alias in spec.aliases:
            specs[alias] = spec
    return specs


def _parse_invocation(line: str) -> CommandInvocation | None:
    stripped = line.strip()
    if not stripped:
        return None
    command, separator, rest = stripped.partition(" ")
    if not command.startswith("/"):
        return None
    name = command[1:].lower()
    arg_text = rest.strip() if separator else ""
    if name in {"resume", "continue", "compact"}:
        args = (_strip_matching_quotes(arg_text),) if arg_text else ()
    else:
        try:
            args = tuple(shlex.split(arg_text)) if arg_text else ()
        except ValueError:
            args = tuple(arg_text.split()) if arg_text else ()
    return CommandInvocation(raw=stripped, name=name, args=args, arg_text=arg_text)


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _status(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    _ = invocation
    return CommandResult(renderable=renderer.render_status(runtime), presentation="page")


def _usage(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    _ = invocation
    return CommandResult(renderable=renderer.render_usage(runtime), presentation="page")


def _memory(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    _ = invocation
    return CommandResult(renderable=renderer.render_memory(runtime), presentation="page")


def _permissions(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    if not invocation.args:
        return CommandResult(
            renderable=renderer.render_permissions(runtime),
            presentation="page",
        )
    parsed = _parse_permissions_args(invocation.arg_text)
    if parsed is None:
        return CommandResult(
            renderable=renderer.render_error(
                "Usage: /permissions add|remove|replace allow|deny|ask <rule...>"
            )
        )
    action, behavior_text, raw_rules = parsed
    update_type = _permission_update_type(action)
    if update_type is None:
        return CommandResult(
            renderable=renderer.render_error(
                "Permission update must be add, remove, or replace."
            )
        )
    behavior = _permission_behavior(behavior_text)
    if behavior is None:
        return CommandResult(
            renderable=renderer.render_error(
                "Permission behavior must be allow, deny, or ask."
            )
        )
    policy = runtime.permission_policy
    project_store = policy.project_store if policy is not None else None
    if project_store is None:
        return CommandResult(
            renderable=renderer.render_error(
                "Project permission settings are not enabled for this runtime."
            )
        )
    try:
        rules = tuple(
            permission_rule_value_from_string(raw_rule)
            for raw_rule in raw_rules
        )
        update = PermissionUpdate(
            type=update_type,
            rules=rules,
            behavior=behavior,
            destination="projectSettings",
        )
        project_store.apply_update(update)
    except Exception as exc:
        return CommandResult(renderable=renderer.render_error(str(exc)))
    return CommandResult(renderable=_render_permission_update(update))


def _skills(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    _ = invocation
    return CommandResult(renderable=renderer.render_skills(runtime), presentation="page")


def _mcp(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    _ = invocation
    return CommandResult(
        renderable=renderer.render_mcp_status(runtime),
        presentation="page",
    )


def _compact(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    if runtime.compaction_service is None:
        return CommandResult(
            renderable=renderer.render_error("Compaction is not enabled for this runtime.")
        )
    focus = invocation.arg_text.strip() or None
    try:
        result = _run_async_blocking(
            runtime.compaction_service.manual_compact(runtime.state, focus=focus)
        )
    except Exception as exc:
        return CommandResult(renderable=renderer.render_error(str(exc)))
    runtime.message_store.flush_transcript()
    runtime.trace_recorder.flush()
    return CommandResult(renderable=renderer.render_compact(result, runtime))


def _tasks(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    _ = invocation
    task_list_id: str | None = None
    tasks_dir: Path | None = None
    tasks: tuple[Any, ...] = ()
    durable_error: str | None = None
    if runtime.task_store is None:
        durable_error = "Task tracking is not enabled for this runtime."
    else:
        task_list_id = resolve_task_list_id(runtime.state)
        tasks_dir = runtime.task_store.tasks_dir(task_list_id)
        try:
            tasks = tuple(runtime.task_store.list_tasks(task_list_id))
        except TaskStoreError as exc:
            durable_error = str(exc)
    background_tasks = (
        tuple(runtime.background_task_manager.list_tasks())
        if runtime.background_task_manager is not None
        else ()
    )
    return CommandResult(
        renderable=renderer.render_tasks(
            runtime,
            tasks,
            task_list_id=task_list_id,
            tasks_dir=tasks_dir,
            background_tasks=background_tasks,
            durable_error=durable_error,
        ),
        presentation="page",
    )


def _clear(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    _ = invocation
    old_session_id = runtime.state.session_id
    runtime.message_store.flush_transcript()
    new_session_id = runtime.state.start_new_session()
    runtime.message_store.clear_for_new_session(new_session_id)
    cleared = runtime.with_session(
        state=runtime.state,
        message_store=runtime.message_store,
    )
    return CommandResult(
        runtime=cleared,
        renderable=renderer.render_clear(old_session_id, new_session_id),
        reset_main_view=True,
    )


def _resume(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    if not invocation.args:
        return CommandResult(interaction="resume_selector")
    if len(invocation.args) != 1:
        return CommandResult(renderable=renderer.render_error("Usage: /resume [target]"))

    try:
        target = _resolve_resume_argument(runtime, invocation.args[0])
        resumed = restore_runtime_from_target(runtime, target)
    except _MultipleResumeMatches as exc:
        return CommandResult(
            renderable=renderer.render_session_summaries(exc.matches, runtime.workspace),
            presentation="page",
        )
    except Exception as exc:
        return CommandResult(renderable=renderer.render_error(str(exc)))
    return CommandResult(
        runtime=resumed,
        renderable=renderer.render_resume(
            resumed.state.session_id,
            resumed.message_store.transcript_store.messages_path,
            resumed.workspace,
        ),
        presentation="inline",
        replay_messages=resumed.message_store.current_messages(),
    )


def _connect(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    _ = runtime
    _ = invocation
    return CommandResult(interaction="connect")


def _exit(runtime: CliRuntime, invocation: CommandInvocation) -> CommandResult:
    _ = invocation
    runtime.message_store.flush_transcript()
    runtime.trace_recorder.flush()
    runtime.error_log_recorder.flush()
    if runtime.mcp_manager is not None:
        _run_async_blocking(runtime.mcp_manager.close_all())
    return CommandResult(should_exit=True)


def _resume_candidates(runtime: CliRuntime, text: str) -> Iterable[str]:
    root = sessions_dir(runtime.workspace)
    if not root.exists():
        return ()
    prefix = text.strip()
    candidates: list[str] = []
    summaries_by_id = {
        summary.session_id: summary for summary in list_session_summaries(runtime.workspace)
    }
    for messages_path in sorted(root.glob("*/messages.jsonl")):
        session_id = messages_path.parent.name
        if not prefix or session_id.startswith(prefix):
            candidates.append(session_id)
        summary = summaries_by_id.get(session_id)
        if summary is not None and prefix and summary.title.lower().startswith(prefix.lower()):
            candidates.append(summary.title)
        display_path = str(messages_path)
        if prefix and display_path.startswith(prefix):
            candidates.append(display_path)
    return tuple(dict.fromkeys(candidates))


class _MultipleResumeMatches(Exception):
    def __init__(self, matches: tuple[Any, ...]) -> None:
        self.matches = matches
        super().__init__("Multiple sessions match that title.")


def _resolve_resume_argument(runtime: CliRuntime, target: str) -> str:
    try:
        _resolve_resume_target(runtime.workspace, target)
        return target
    except ValueError as direct_error:
        if _looks_like_path_target(target):
            raise direct_error

    needle = target.casefold()
    matches = tuple(
        summary
        for summary in list_session_summaries(runtime.workspace)
        if needle in summary.title.casefold()
    )
    if not matches:
        raise ValueError(f"No session title matches: {target}")
    if len(matches) > 1:
        raise _MultipleResumeMatches(matches)
    return matches[0].session_id


def _looks_like_path_target(target: str) -> bool:
    lowered = target.lower()
    return (
        lowered.endswith(".jsonl")
        or "/" in target
        or "\\" in target
        or ":" in target
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


def _permission_update_type(value: str) -> PermissionUpdateType | None:
    mapping: dict[str, PermissionUpdateType] = {
        "add": "addRules",
        "remove": "removeRules",
        "replace": "replaceRules",
    }
    return mapping.get(value.lower())


def _parse_permissions_args(arg_text: str) -> tuple[str, str, tuple[str, ...]] | None:
    head = arg_text.strip().split(maxsplit=2)
    if len(head) < 3:
        return None
    rules = _split_permission_rules(head[2])
    if not rules:
        return None
    return head[0], head[1], rules


def _split_permission_rules(text: str) -> tuple[str, ...]:
    rules: list[str] = []
    start: int | None = None
    depth = 0
    escaped = False
    for index, char in enumerate(text):
        if start is None:
            if char.isspace():
                continue
            start = index
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")" and depth > 0:
            depth -= 1
            continue
        if char.isspace() and depth == 0:
            assert start is not None
            rules.append(text[start:index])
            start = None
    if start is not None:
        rules.append(text[start:].strip())
    return tuple(rule for rule in rules if rule)


def _permission_behavior(value: str) -> PermissionBehavior | None:
    mapping: dict[str, PermissionBehavior] = {
        "allow": "allow",
        "deny": "deny",
        "ask": "ask",
    }
    return mapping.get(value.lower())


def _render_permission_update(update: PermissionUpdate):
    from rich.text import Text
    from services.permissions import permission_rule_value_to_string
    from ui.cli.theme import SYMBOLS

    action = {
        "addRules": "Added",
        "removeRules": "Removed",
        "replaceRules": "Replaced",
    }[update.type]
    rules = ", ".join(permission_rule_value_to_string(rule) for rule in update.rules)
    return Text(
        f"{SYMBOLS.success} {action} project {update.behavior} permission rule(s): {rules}",
        style="onecode.success",
    )
