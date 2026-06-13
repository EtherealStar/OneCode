"""OneCode CLI entry point."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from services.permissions import PermissionPrompter

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from infrastructure.providers.factory import create_model_client
from prompts.assembler import DynamicPromptAssembler
from services.attachments import (
    AttachmentCollector,
    AttachmentContextPreparer,
    AttachmentFileReader,
)
from services.background_tasks import (
    BackgroundTaskManager,
    BackgroundTaskNotificationSource,
)
from services.compaction import (
    ContextCompactionService,
    SessionMemoryExtractionService,
    SessionMemoryStore,
)
from services.context.current_model_context import CurrentModelContext
from services.context.message_store import MessageStore
from services.guard import SandboxBoundary, SandboxGuard
from services.hooks import HookRegistry
from services.hooks import HookEvent
from services.memory import (
    InstructionMemoryLoader,
    LongTermMemoryExtractionService,
    LongTermMemoryPromptProvider,
    LongTermMemoryStore,
    RelevantMemoryContextPreparer,
    RelevantMemorySelector,
)
from services.model.types import ProviderError
from services.mcp import (
    BASE_STDIO_ENV_ALLOWLIST,
    McpConfigSet,
    McpConnectionManager,
    McpServerConfig,
    McpTrustPolicy,
    McpTrustStore,
    build_mcp_tool_descriptors,
    fingerprint_mcp_server,
    load_project_mcp_config,
)
from services.observability import (
    ErrorLogRecorder,
    JsonlErrorLogSink,
    JsonlTraceSink,
    TraceRecorder,
)
from services.permissions import (
    PermissionPolicy,
    ProjectPermissionSettingsStore,
    SessionPermissionStore,
)
from services.skills import LoaderSkillCatalogProvider
from services.subagents.runner import SubagentRunner
from services.tasks import TaskStore
from services.tools.executor import RegistryToolExecutor
from services.tools.file_state import FileStateCache
from services.tools.registry import ToolRegistry
from tools.agent import descriptor as agent_descriptor
from tools.bash import descriptor as bash_descriptor
from tools.background_task_stop import descriptor as background_task_stop_descriptor
from tools.edit_file import descriptor as edit_file_descriptor
from tools.glob import descriptor as glob_descriptor
from tools.grep import descriptor as grep_descriptor
from tools.read_file import descriptor as read_file_descriptor
from tools.skill import descriptor as skill_descriptor
from tools.task_create import descriptor as task_create_descriptor
from tools.task_get import descriptor as task_get_descriptor
from tools.task_list import descriptor as task_list_descriptor
from tools.task_update import descriptor as task_update_descriptor
from tools.write_file import descriptor as write_file_descriptor
from ui.cli import renderer
from ui.cli.input import ConfirmOption, read_confirm_sync
from ui.cli.permissions import CliPermissionPrompter
from ui.cli.types import CliRuntime
from utils.toolResultStorage import ToolResultStorage

TrustChoice = Literal["trust", "skip"]
McpTrustMode = Literal["prompt", "skip"]


@dataclass(frozen=True)
class McpTrustPromptRequest:
    server_name: str
    command: str
    args: str
    cwd: str
    explicit_env_keys: str
    base_env_keys: str


def build_runtime(
    workspace: Path,
    *,
    trust_prompt: Callable[[McpTrustPromptRequest], TrustChoice] | None = None,
    permission_prompter: PermissionPrompter | None = None,
    mcp_trust_mode: McpTrustMode = "prompt",
) -> CliRuntime:
    workspace = workspace.resolve()
    state = RuntimeState()
    state.metadata["workspace"] = str(workspace)
    message_store = MessageStore(
        transcript_root=workspace / ".onecode",
        session_id=state.session_id,
        cwd=workspace,
    )
    permission_store = SessionPermissionStore()
    project_permission_store = ProjectPermissionSettingsStore(
        workspace / ".onecode" / "settings.json"
    )
    project_permission_store.load_rules()
    permission_policy = PermissionPolicy(
        permission_store,
        project_store=project_permission_store,
    )
    skill_provider = LoaderSkillCatalogProvider()
    trace_sink = JsonlTraceSink(workspace / ".onecode", state.session_id)
    trace_recorder = TraceRecorder(
        session_id=state.session_id,
        workspace=workspace,
        sink=trace_sink,
    )
    error_log_sink = JsonlErrorLogSink(workspace / ".onecode", state.session_id)
    error_log_recorder = ErrorLogRecorder(
        session_id=state.session_id,
        workspace=workspace,
        sink=error_log_sink,
    )
    try:
        mcp_config = load_project_mcp_config(workspace)
    except Exception as exc:
        error_log_recorder.record_error(exc, source="mcp_config")
        error_log_recorder.flush()
        raise
    mcp_trust_store = McpTrustStore(workspace / ".onecode" / "settings.json")
    if mcp_trust_mode == "prompt":
        _prompt_for_project_mcp_trust(
            workspace,
            mcp_config,
            mcp_trust_store,
            trust_prompt=trust_prompt,
        )
    state.metadata["mcp_untrusted_servers"] = _collect_untrusted_project_mcp_servers(
        workspace,
        mcp_config,
        mcp_trust_store,
    )
    mcp_manager = McpConnectionManager(
        workspace,
        mcp_config,
        trace_recorder=trace_recorder,
        error_log_recorder=error_log_recorder,
        trust_policy=McpTrustPolicy(mcp_trust_store),
    )
    mcp_snapshot = mcp_manager.connect_all_blocking()
    state.metadata["mcp_server_instructions"] = mcp_snapshot.instructions
    mcp_descriptors = build_mcp_tool_descriptors(mcp_manager)
    hooks = HookRegistry(trace_recorder=trace_recorder)
    task_store = TaskStore(workspace)
    background_task_manager = BackgroundTaskManager(
        workspace=workspace,
        trace_recorder=trace_recorder,
    )
    runner_ref: dict[str, SubagentRunner] = {}
    base_descriptors = (
        read_file_descriptor(),
        edit_file_descriptor(),
        write_file_descriptor(),
        glob_descriptor(),
        grep_descriptor(),
        bash_descriptor(background_task_manager),
        background_task_stop_descriptor(background_task_manager),
        skill_descriptor(
            skill_provider=skill_provider,
            cwd=lambda: workspace,
            fork_runner=lambda: runner_ref.get("runner"),
        ),
        task_create_descriptor(task_store, hooks),
        task_get_descriptor(task_store),
        task_update_descriptor(task_store, hooks),
        task_list_descriptor(task_store),
        *mcp_descriptors,
    )
    registry = ToolRegistry(base_descriptors, permission_policy=permission_policy)
    result_store = ToolResultStorage(message_store.transcript_store.session_dir)
    session_memory_store = SessionMemoryStore(message_store.transcript_store.session_dir)
    long_term_memory_store = LongTermMemoryStore(workspace)
    instruction_memory_loader = InstructionMemoryLoader(
        workspace,
        trace_recorder=trace_recorder,
    )
    long_term_memory_provider = LongTermMemoryPromptProvider(long_term_memory_store)
    prompt_assembler = DynamicPromptAssembler(
        workspace,
        tool_registry=registry,
        skill_provider=skill_provider,
        instruction_memory_loader=instruction_memory_loader,
        long_term_memory_provider=long_term_memory_provider,
    )
    compaction_service = ContextCompactionService(
        message_store=message_store,
        session_memory_store=session_memory_store,
        result_store=result_store,
        hooks=hooks,
        trace_recorder=trace_recorder,
    )
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    permission_prompter = permission_prompter or CliPermissionPrompter()
    file_state_cache = FileStateCache()
    attachment_reader = AttachmentFileReader(
        guard=guard,
        permission_policy=permission_policy,
        permission_prompter=permission_prompter,
    )
    attachment_collector = AttachmentCollector(
        workspace=workspace,
        reader=attachment_reader,
        file_state_cache=file_state_cache,
        shared_sources=(
            BackgroundTaskNotificationSource(background_task_manager),
        ),
    )
    current_model_context = CurrentModelContext()
    model_client = create_model_client(workspace / ".env")
    memory_selector = RelevantMemorySelector(
        model_client=model_client,
        trace_recorder=trace_recorder,
    )
    context_engine = ContextEngine(
        message_store,
        prompt_assembler=prompt_assembler,
        tool_schema_provider=registry,
        context_preparer=AttachmentContextPreparer(
            RelevantMemoryContextPreparer(
                long_term_memory_store,
                memory_selector,
                inner=compaction_service,
            )
        ),
    )
    subagent_runner = SubagentRunner(
        workspace=workspace,
        transcript_root=workspace / ".onecode",
        parent_message_store=message_store,
        current_model_context=current_model_context,
        model_client=model_client,
        base_descriptors=base_descriptors,
        guard=guard,
        permission_policy=permission_policy,
        permission_prompter=permission_prompter,
        trace_recorder=trace_recorder,
    )
    runner_ref["runner"] = subagent_runner
    session_memory_extractor = SessionMemoryExtractionService(
        session_memory_store,
        subagent_runner=subagent_runner,
        trace_recorder=trace_recorder,
    )
    long_term_memory_extractor = LongTermMemoryExtractionService(
        long_term_memory_store,
        subagent_runner=subagent_runner,
        trace_recorder=trace_recorder,
    )
    long_term_memory_extractor_ref = {"extractor": long_term_memory_extractor}
    hooks.register(
        HookEvent.TURN_STOPPED,
        lambda payload: _start_long_term_memory_dream(
            payload,
            long_term_memory_extractor=long_term_memory_extractor_ref["extractor"],
            background_task_manager=background_task_manager,
        ),
    )
    compaction_service.bind_runtime(subagent_runner=subagent_runner)
    compaction_service.bind_runtime(session_memory_extractor=session_memory_extractor)
    registry.register(agent_descriptor(subagent_runner, background_task_manager))
    tool_executor = RegistryToolExecutor(
        registry,
        guard=guard,
        hooks=hooks,
        permission_policy=permission_policy,
        permission_prompter=permission_prompter,
        trace_recorder=trace_recorder,
        error_log_recorder=error_log_recorder,
        result_store=result_store,
        file_state_cache=file_state_cache,
    )
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=context_engine,
        model_client=model_client,
        tool_executor=tool_executor,
        trace_recorder=trace_recorder,
        current_model_context=current_model_context,
        hooks=hooks,
        compaction_service=compaction_service,
        session_memory_extractor=session_memory_extractor,
        error_log_recorder=error_log_recorder,
    )
    config = model_client.config
    return CliRuntime(
        workspace=workspace,
        state=state,
        message_store=message_store,
        registry=registry,
        loop=loop,
        provider_label=config.display_name,
        model=config.model,
        model_client=model_client,
        tool_executor=tool_executor,
        permission_store=permission_store,
        permission_policy=permission_policy,
        permission_prompter=permission_prompter,
        trace_recorder=trace_recorder,
        error_log_recorder=error_log_recorder,
        current_model_context=current_model_context,
        subagent_runner=subagent_runner,
        compaction_service=compaction_service,
        session_memory_store=session_memory_store,
        session_memory_extractor=session_memory_extractor,
        attachment_collector=attachment_collector,
        skill_provider=skill_provider,
        mcp_manager=mcp_manager,
        hooks=hooks,
        long_term_memory_store=long_term_memory_store,
        long_term_memory_extractor=long_term_memory_extractor,
        instruction_memory_loader=instruction_memory_loader,
        long_term_memory_provider=long_term_memory_provider,
        memory_selector=memory_selector,
        task_store=task_store,
        background_task_manager=background_task_manager,
        guard=guard,
        base_descriptors=base_descriptors,
        subagent_runner_ref=runner_ref,
        long_term_memory_extractor_ref=long_term_memory_extractor_ref,
    )


def _prompt_for_project_mcp_trust(
    workspace: Path,
    mcp_config: McpConfigSet,
    trust_store: McpTrustStore,
    *,
    trust_prompt: Callable[[McpTrustPromptRequest], TrustChoice] | None = None,
) -> None:
    for config, request in _iter_untrusted_project_mcp_server_requests(
        workspace,
        mcp_config,
        trust_store,
    ):
        fingerprint = fingerprint_mcp_server(config, workspace)
        if trust_prompt is not None:
            response = trust_prompt(request)
        else:
            print("Project MCP stdio server requires trust before it can run:")
            print(f"  server: {config.name}")
            print(f"  command: {request.command}")
            print(f"  args: {request.args}")
            print(f"  cwd: {request.cwd}")
            print(f"  explicit env keys: {request.explicit_env_keys}")
            print(f"  base env keys: {request.base_env_keys}")
            try:
                response = read_confirm_sync(
                    "Trust this project MCP server?",
                    (
                        ConfirmOption("trust", "t trust", aliases=("t", "y", "yes")),
                        ConfirmOption("skip", "s skip", aliases=("s", "n", "no")),
                    ),
                )
            except (EOFError, KeyboardInterrupt):
                print("Skipping untrusted MCP server.")
                continue
        if response == "trust":
            trust_store.trust_server(
                config.name,
                fingerprint,
                transport=config.transport,
            )
            print(f"Trusted MCP server: {config.name}")
        else:
            print(f"Skipped MCP server: {config.name}")


def _collect_untrusted_project_mcp_servers(
    workspace: Path,
    mcp_config: McpConfigSet,
    trust_store: McpTrustStore,
) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "name": request.server_name,
            "command": request.command,
            "args": request.args,
            "cwd": request.cwd,
            "explicit_env_keys": request.explicit_env_keys,
            "base_env_keys": request.base_env_keys,
        }
        for _, request in _iter_untrusted_project_mcp_server_requests(
            workspace,
            mcp_config,
            trust_store,
        )
    )


def _iter_untrusted_project_mcp_server_requests(
    workspace: Path,
    mcp_config: McpConfigSet,
    trust_store: McpTrustStore,
) -> tuple[tuple[McpServerConfig, McpTrustPromptRequest], ...]:
    allowed_env_keys = {item.upper() for item in BASE_STDIO_ENV_ALLOWLIST}
    base_env_keys = sorted(
        key for key in os.environ if key.upper() in allowed_env_keys
    )
    requests = []
    for config in mcp_config.servers.values():
        if (
            getattr(config, "transport", None) != "stdio"
            or getattr(config, "enabled", False) is not True
        ):
            continue
        fingerprint = fingerprint_mcp_server(config, workspace)
        if trust_store.is_trusted(config.name, fingerprint):
            continue
        requests.append(
            (
                config,
                McpTrustPromptRequest(
                    server_name=config.name,
                    command=config.command or "",
                    args=" ".join(config.args) if config.args else "(none)",
                    cwd=str(workspace),
                    explicit_env_keys=(
                        ", ".join(sorted(config.env)) if config.env else "(none)"
                    ),
                    base_env_keys=(
                        ", ".join(base_env_keys) if base_env_keys else "(none)"
                    ),
                ),
            )
        )
    return tuple(requests)


async def _start_long_term_memory_dream(
    payload: dict,
    *,
    long_term_memory_extractor: LongTermMemoryExtractionService,
    background_task_manager: BackgroundTaskManager,
) -> None:
    state = payload["state"]
    job = long_term_memory_extractor.prepare_extraction_job(
        tuple(payload.get("messages", ())),
        state,
        tool_calls=tuple(payload.get("tool_calls", ())),
    )
    if job is None:
        return

    async def run(task_id: str) -> dict[str, object]:
        task = background_task_manager.get(task_id)
        output_path = (
            Path(str(task.metadata.get("output_path_abs", "")))
            if task is not None
            else None
        )
        if output_path is not None:
            with output_path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write("dream: updating long-term memory\n")
                handle.write(f"parent_session_id: {job.parent_session_id}\n")
        await long_term_memory_extractor.run_extraction_job(job, state)
        metadata = state.metadata.get("long_term_memory_extraction")
        result_session_id = None
        if isinstance(metadata, dict):
            result_session_id = metadata.get("last_result_session_id")
        if output_path is not None:
            with output_path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write("dream: completed\n")
                if result_session_id:
                    handle.write(f"child_session_id: {result_session_id}\n")
        return {
            "summary": "Long-term memory dream completed.",
            "result_session_id": result_session_id,
        }

    background_task_manager.start_dream(
        description="updating long-term memory",
        state=state,
        run=run,
        metadata={"memory_dir": str(long_term_memory_extractor.store.memory_dir)},
    )


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    workspace = Path.cwd()
    if not sys.stdin.isatty():
        from ui.cli.batch import run_batch

        return run_batch(workspace)
    if not sys.stdout.isatty():
        print(
            "Error: OneCode CLI requires an interactive terminal: stdout is not a TTY. "
            "Run `uv run python -m ui.cli.app` from a real terminal window, "
            "or pipe a prompt into the command to use batch mode.",
            file=sys.stderr,
        )
        return 1

    # The TTY path is an inline REPL (prompt_toolkit + Rich). We wire
    # MCP trust prompts through ``default_trust_prompt`` and start with
    # ``mcp_trust_mode="prompt"`` so the user is asked to trust project
    # stdio servers on startup rather than having them silently skipped.
    from ui.cli.terminal.permission_prompt import TtyPermissionPrompter
    from ui.cli.terminal.repl import InlineRepl
    from ui.cli.terminal.trust_prompt import default_trust_prompt

    try:
        permission_prompter = TtyPermissionPrompter()
        runtime = build_runtime(
            workspace,
            trust_prompt=default_trust_prompt,
            permission_prompter=permission_prompter,
            mcp_trust_mode="prompt",
        )
        repl = InlineRepl(runtime, permission_prompter=permission_prompter)
        return repl.run()
    except ProviderError as exc:
        renderer.print_renderable(renderer.render_error(exc.message))
        return 1
    except Exception as exc:
        renderer.print_renderable(renderer.render_error(str(exc)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
