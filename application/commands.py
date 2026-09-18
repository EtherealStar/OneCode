"""Command parsing, registry, and business operations for the application layer.

Commands return structured :class:`CommandOutcome` data. They never carry Rich
renderables, reset a view, or replay messages: those are UI concerns. View
commands read a consistent snapshot immediately; mutation commands run at a
safe point; lifecycle commands are handled by the controller.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from services.permissions import (
    PermissionBehavior,
    PermissionUpdate,
    PermissionUpdateType,
    permission_rule_value_from_string,
)
from services.plans import (
    PlanStore,
    build_plan_attachments_for_state,
    enter_plan_mode,
    exit_plan_mode,
)
from services.tasks import TaskStoreError, resolve_task_list_id

CommandCategory = Literal["view", "mutate", "lifecycle"]


@dataclass(frozen=True)
class CommandInvocation:
    raw: str
    name: str
    args: tuple[str, ...]
    arg_text: str


@dataclass(frozen=True)
class CommandOutcome:
    name: str
    category: CommandCategory
    status: str = "ok"
    text: str = ""
    data: Any = None
    action: str | None = None
    submit_text: str | None = None
    error: str | None = None
    attachments: tuple[dict[str, Any], ...] = ()


CommandHandler = Callable[["Any", CommandInvocation], Awaitable[CommandOutcome]]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    handler: CommandHandler
    category: CommandCategory = "view"
    argument_hint: str = ""
    aliases: tuple[str, ...] = ()

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
            category="mutate",
            argument_hint="[add|remove|replace allow|deny|ask <rule...>]",
        ),
        CommandSpec("skills", "Show visible skills.", _skills),
        CommandSpec("tasks", "Show durable and background tasks.", _tasks),
        CommandSpec("mcp", "Show MCP servers and discovered tools.", _mcp),
        CommandSpec(
            "compact",
            "Compact the active session context.",
            _compact,
            category="mutate",
            argument_hint="[focus]",
        ),
        CommandSpec(
            "plan",
            "Enter plan mode, show the current plan, or open the plan file.",
            _plan,
            category="mutate",
            argument_hint="[<description> | show | open | approve | reject]",
        ),
        CommandSpec(
            "resume",
            "Restore a previous session.",
            _resume,
            category="lifecycle",
            argument_hint="[session-id-or-title-or-messages.jsonl]",
            aliases=("continue",),
        ),
        CommandSpec(
            "connect",
            "Configure model provider credentials.",
            _connect,
            category="lifecycle",
        ),
        CommandSpec("clear", "Start a fresh session.", _clear, category="lifecycle"),
        CommandSpec("exit", "Flush state and exit.", _exit, category="lifecycle"),
    )


def visible_commands() -> tuple[CommandSpec, ...]:
    return command_registry()


def spec_by_name() -> dict[str, CommandSpec]:
    specs: dict[str, CommandSpec] = {}
    for spec in command_registry():
        specs[spec.name] = spec
        for alias in spec.aliases:
            specs[alias] = spec
    return specs


def parse_invocation(line: str) -> CommandInvocation | None:
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


async def dispatch(controller: Any, line: str) -> CommandOutcome:
    invocation = parse_invocation(line)
    if invocation is None:
        return CommandOutcome(name="", category="view", status="noop")
    spec = spec_by_name().get(invocation.name)
    if spec is None:
        return CommandOutcome(
            name=invocation.name,
            category="view",
            status="unknown",
            text=f"Unknown command: /{invocation.name}.",
        )
    if effective_category(spec, invocation) == "mutate":
        await controller.await_safe_point()
        async with controller.command_lock:
            return await spec.handler(controller, invocation)
    return await spec.handler(controller, invocation)


def effective_category(spec: CommandSpec, invocation: CommandInvocation) -> CommandCategory:
    """Classify by concrete invocation, not just the command name.

    ``/permissions`` (no args) and ``/plan show|open`` read a consistent
    snapshot and must not wait behind a running turn; their mutation
    subcommands still serialize at a safe point.
    """

    if spec.name == "permissions":
        return "mutate" if invocation.args else "view"
    if spec.name == "plan":
        subcommand, _ = _plan_subcommand(invocation)
        return "view" if subcommand in {"show", "open"} else "mutate"
    return spec.category


# --- view handlers ---------------------------------------------------------


def _runtime(controller: Any) -> Any:
    return controller.runtime


async def _status(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    runtime = _runtime(controller)
    state = runtime.state
    data = {
        "session_id": state.session_id,
        "provider_label": getattr(runtime, "provider_label", ""),
        "model": getattr(runtime, "model", ""),
        "configured": bool(getattr(runtime, "configured", True)),
        "turn_count": state.turn_count,
        "permission_mode": state.permission_mode.value,
        "plan_mode": state.is_plan_mode(),
        "workspace": str(getattr(runtime, "workspace", "")),
    }
    return CommandOutcome(name="status", category="view", data=data)


async def _usage(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    usage = _runtime(controller).state.usage
    data = {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(
            usage, "cache_creation_input_tokens", 0
        ),
    }
    return CommandOutcome(name="usage", category="view", data=data)


async def _memory(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    runtime = _runtime(controller)
    session_memory_store = getattr(runtime, "session_memory_store", None)
    long_term_memory_store = getattr(runtime, "long_term_memory_store", None)
    data = {
        "session_memory_path": (
            str(getattr(session_memory_store, "path", ""))
            if session_memory_store is not None
            else None
        ),
        "long_term_memory_dir": (
            str(getattr(long_term_memory_store, "memory_dir", ""))
            if long_term_memory_store is not None
            else None
        ),
    }
    return CommandOutcome(name="memory", category="view", data=data)


async def _skills(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    runtime = _runtime(controller)
    provider = getattr(runtime, "skill_provider", None)
    names: list[str] = []
    if provider is not None and hasattr(provider, "visible_skills"):
        try:
            skills = tuple(provider.visible_skills(str(getattr(runtime, "workspace", ""))))
            names = [getattr(skill, "name", str(skill)) for skill in skills]
        except Exception:
            names = []
    return CommandOutcome(name="skills", category="view", data=names)


async def _mcp(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    runtime = _runtime(controller)
    manager = getattr(runtime, "mcp_manager", None)
    servers: list[dict[str, Any]] = []
    if manager is not None and hasattr(manager, "snapshot"):
        try:
            snapshot = manager.snapshot()
            for status in getattr(snapshot, "statuses", ()):
                servers.append(
                    {
                        "name": getattr(status, "name", ""),
                        "status": getattr(status, "status", ""),
                    }
                )
        except Exception:
            servers = []
    return CommandOutcome(name="mcp", category="view", data=servers)


async def _tasks(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    runtime = _runtime(controller)
    task_list_id: str | None = None
    tasks: list[dict[str, Any]] = []
    error: str | None = None
    task_store = getattr(runtime, "task_store", None)
    if task_store is None:
        error = "Task tracking is not enabled for this runtime."
    else:
        try:
            task_list_id = resolve_task_list_id(runtime.state)
            for task in task_store.list_tasks(task_list_id):
                tasks.append(
                    {
                        "id": getattr(task, "id", ""),
                        "description": getattr(task, "description", ""),
                        "status": getattr(task, "status", ""),
                    }
                )
        except TaskStoreError as exc:
            error = str(exc)
    background = []
    manager = getattr(runtime, "background_task_manager", None)
    if manager is not None:
        for task in manager.list_tasks():
            background.append(
                {
                    "id": getattr(task, "id", ""),
                    "description": getattr(task, "description", ""),
                    "status": getattr(task, "status", ""),
                }
            )
    return CommandOutcome(
        name="tasks",
        category="view",
        data={"task_list_id": task_list_id, "tasks": tasks, "background": background},
        error=error,
    )


async def _permissions(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    runtime = _runtime(controller)
    policy = getattr(runtime, "permission_policy", None)
    project_store = getattr(policy, "project_store", None)
    if not invocation.args:
        rules: list[str] = []
        if project_store is not None:
            try:
                for rule in project_store.load_rules():
                    rules.append(str(rule))
            except Exception:
                rules = []
        return CommandOutcome(name="permissions", category="view", data={"rules": rules})
    if project_store is None:
        return CommandOutcome(
            name="permissions",
            category="mutate",
            status="rejected",
            error="Project permission settings are not enabled for this runtime.",
        )
    parsed = _parse_permissions_args(invocation.arg_text)
    if parsed is None:
        return CommandOutcome(
            name="permissions",
            category="mutate",
            status="rejected",
            error="Usage: /permissions add|remove|replace allow|deny|ask <rule...>",
        )
    action, behavior_text, raw_rules = parsed
    update_type = _permission_update_type(action)
    if update_type is None:
        return CommandOutcome(
            name="permissions",
            category="mutate",
            status="rejected",
            error="Permission update must be add, remove, or replace.",
        )
    behavior = _permission_behavior(behavior_text)
    if behavior is None:
        return CommandOutcome(
            name="permissions",
            category="mutate",
            status="rejected",
            error="Permission behavior must be allow, deny, or ask.",
        )
    try:
        rules = tuple(
            permission_rule_value_from_string(raw_rule) for raw_rule in raw_rules
        )
        update = PermissionUpdate(
            type=update_type,
            rules=rules,
            behavior=behavior,
            destination="projectSettings",
        )
        project_store.apply_update(update)
    except Exception as exc:
        return CommandOutcome(
            name="permissions",
            category="mutate",
            status="rejected",
            error=str(exc),
        )
    return CommandOutcome(
        name="permissions",
        category="mutate",
        data={"type": update.type, "behavior": update.behavior},
    )


async def _plan(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    runtime = _runtime(controller)
    plan_store: PlanStore | None = getattr(runtime, "plan_store", None)
    if plan_store is None:
        return CommandOutcome(
            name="plan",
            category="mutate",
            status="rejected",
            error="Plan store is not configured for this runtime.",
        )
    subcommand, remaining = _plan_subcommand(invocation)
    if subcommand == "open":
        if runtime.state.plan.plan_slug is None:
            enter_plan_mode(runtime.state, plan_store)
        plan_file = plan_store.read_plan(runtime.state)
        return CommandOutcome(
            name="plan", category="view", data={"path": str(plan_file.path)}
        )
    if subcommand == "approve":
        if not runtime.state.is_plan_mode():
            return CommandOutcome(
                name="plan",
                category="mutate",
                status="rejected",
                error="Cannot approve a plan: runtime is not in plan mode.",
            )
        exit_plan_mode(runtime.state, plan_store, approved=True)
        return CommandOutcome(
            name="plan",
            category="mutate",
            text="Plan approved.",
            attachments=tuple(
                build_plan_attachments_for_state(runtime.state, plan_store)
            ),
        )
    if subcommand == "reject":
        if not runtime.state.is_plan_mode():
            return CommandOutcome(
                name="plan",
                category="mutate",
                status="rejected",
                error="Cannot reject a plan: runtime is not in plan mode.",
            )
        exit_plan_mode(runtime.state, plan_store, approved=False)
        return CommandOutcome(
            name="plan",
            category="mutate",
            text="Plan rejected.",
            attachments=tuple(
                build_plan_attachments_for_state(runtime.state, plan_store)
            ),
        )
    if subcommand == "show":
        if runtime.state.plan.plan_slug is None:
            return CommandOutcome(
                name="plan", category="view", data={"path": None, "content": ""}
            )
        plan_file = plan_store.read_plan(runtime.state)
        content = plan_file.read() if plan_file.exists() else ""
        return CommandOutcome(
            name="plan",
            category="view",
            data={"path": str(plan_file.path), "content": content},
        )
    if not runtime.state.is_plan_mode():
        enter_plan_mode(runtime.state, plan_store)
    queued = remaining.strip()
    return CommandOutcome(
        name="plan",
        category="mutate",
        action="submit" if queued else None,
        submit_text=queued or None,
        data={"plan_mode": True},
    )


async def _compact(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    runtime = _runtime(controller)
    service = getattr(runtime, "compaction_service", None)
    if service is None:
        return CommandOutcome(
            name="compact",
            category="mutate",
            status="rejected",
            error="Compaction is not enabled for this runtime.",
        )
    focus = invocation.arg_text.strip() or None
    try:
        result = await service.manual_compact(runtime.state, focus=focus)
    except Exception as exc:
        return CommandOutcome(
            name="compact", category="mutate", status="rejected", error=str(exc)
        )
    runtime.message_store.flush_transcript()
    runtime.trace_recorder.flush()
    return CommandOutcome(
        name="compact",
        category="mutate",
        data={
            "trigger": getattr(getattr(result, "trigger", None), "value", None),
            "token_before": getattr(result, "token_before", None),
            "token_after": getattr(result, "token_after", None),
            "message_count": len(getattr(result, "messages", ())),
        },
    )


# --- lifecycle handlers ----------------------------------------------------


async def _resume(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    if not invocation.args:
        return CommandOutcome(
            name="resume", category="lifecycle", action="resume_selector"
        )
    if len(invocation.args) != 1:
        return CommandOutcome(
            name="resume",
            category="lifecycle",
            status="rejected",
            error="Usage: /resume [target]",
        )
    return CommandOutcome(
        name="resume",
        category="lifecycle",
        action="resume",
        data={"target": invocation.args[0]},
    )


async def _connect(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    return CommandOutcome(name="connect", category="lifecycle", action="connect")


async def _clear(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    return CommandOutcome(name="clear", category="lifecycle", action="clear")


async def _exit(controller: Any, invocation: CommandInvocation) -> CommandOutcome:
    return CommandOutcome(name="exit", category="lifecycle", action="exit")


# --- parsing helpers -------------------------------------------------------


def _plan_subcommand(invocation: CommandInvocation) -> tuple[str, str]:
    if not invocation.args:
        return "enter", ""
    head = invocation.args[0].lower()
    if head in {"show", "view", "status"}:
        return "show", ""
    if head in {"open", "edit", "path"}:
        return "open", ""
    if head in {"approve", "accept", "yes"}:
        return "approve", ""
    if head in {"reject", "deny", "no"}:
        return "reject", ""
    return "enter", invocation.arg_text


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _permission_update_type(value: str) -> PermissionUpdateType | None:
    mapping: dict[str, PermissionUpdateType] = {
        "add": "addRules",
        "remove": "removeRules",
        "replace": "replaceRules",
    }
    return mapping.get(value.lower())


def _permission_behavior(value: str) -> PermissionBehavior | None:
    mapping: dict[str, PermissionBehavior] = {
        "allow": "allow",
        "deny": "deny",
        "ask": "ask",
    }
    return mapping.get(value.lower())


def _parse_permissions_args(
    arg_text: str,
) -> tuple[str, str, tuple[str, ...]] | None:
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


__all__ = [
    "CommandInvocation",
    "CommandOutcome",
    "CommandSpec",
    "command_registry",
    "dispatch",
    "parse_invocation",
    "spec_by_name",
    "visible_commands",
]
