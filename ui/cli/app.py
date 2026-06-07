"""OneCode CLI entry point."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Sequence

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
from services.compaction import (
    ContextCompactionService,
    SessionMemoryExtractionService,
    SessionMemoryStore,
    ToolResultStore,
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
    McpConnectionManager,
    build_mcp_tool_descriptors,
    load_project_mcp_config,
)
from services.observability import JsonlTraceSink, TraceRecorder
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
from ui.cli.commands import handle_command
from ui.cli.permissions import CliPermissionPrompter
from ui.cli.types import CliRuntime


def build_runtime(workspace: Path) -> CliRuntime:
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
    mcp_config = load_project_mcp_config(workspace)
    mcp_manager = McpConnectionManager(
        workspace,
        mcp_config,
        trace_recorder=trace_recorder,
    )
    mcp_snapshot = mcp_manager.connect_all_blocking()
    state.metadata["mcp_server_instructions"] = mcp_snapshot.instructions
    mcp_descriptors = build_mcp_tool_descriptors(mcp_manager)
    hooks = HookRegistry(trace_recorder=trace_recorder)
    task_store = TaskStore(workspace)
    runner_ref: dict[str, SubagentRunner] = {}
    base_descriptors = (
        read_file_descriptor(),
        edit_file_descriptor(),
        write_file_descriptor(),
        glob_descriptor(),
        grep_descriptor(),
        bash_descriptor(),
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
    result_store = ToolResultStore(message_store.transcript_store.session_dir)
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
    permission_prompter = CliPermissionPrompter()
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
    hooks.register(
        HookEvent.TURN_STOPPED,
        lambda payload: long_term_memory_extractor.maybe_extract_after_model_response(
            tuple(payload.get("messages", ())),
            payload["state"],
            assistant_message=payload.get("assistant_message") or {},
            tool_calls=tuple(payload.get("tool_calls", ())),
            usage=payload.get("usage"),
        ),
    )
    compaction_service.bind_runtime(subagent_runner=subagent_runner)
    compaction_service.bind_runtime(session_memory_extractor=session_memory_extractor)
    registry.register(agent_descriptor(subagent_runner))
    tool_executor = RegistryToolExecutor(
        registry,
        guard=guard,
        hooks=hooks,
        permission_policy=permission_policy,
        permission_prompter=permission_prompter,
        trace_recorder=trace_recorder,
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
    )


async def main_loop_async(runtime: CliRuntime) -> int:
    print(renderer.render_banner(runtime))
    print()
    while True:
        try:
            line = await asyncio.to_thread(input, "onecode> ")
        except EOFError:
            print()
            runtime.message_store.flush_transcript()
            runtime.trace_recorder.flush()
            if runtime.mcp_manager is not None:
                await runtime.mcp_manager.close_all()
            return 0
        except KeyboardInterrupt:
            print("\nUse /exit to quit.")
            continue

        line = line.strip()
        if not line:
            continue

        if line.startswith("/"):
            result = handle_command(runtime, line)
            if result.runtime is not None:
                runtime = result.runtime
            if result.should_exit:
                return 0
            continue

        try:
            print(renderer.render_running())
            attachments = ()
            if runtime.attachment_collector is not None:
                attachments = await runtime.attachment_collector.collect_for_user_turn(
                    line,
                    runtime.state,
                    runtime.message_store.current_messages(),
                    is_main_thread=True,
                )
            saw_delta = False
            final_text = ""
            async for event in runtime.loop.stream(line, attachments=attachments):
                if event.type == "assistant_delta":
                    saw_delta = True
                    print(renderer.render_assistant_delta(event.text), end="", flush=True)
                elif event.type == "tool_result" and event.result is not None:
                    print(renderer.render_tool_result_summary(event.result))
                elif event.type == "completed":
                    final_text = event.text
            if saw_delta:
                print()
            else:
                print(renderer.render_assistant(final_text))
        except KeyboardInterrupt:
            print("\nInterrupted. Use /exit to quit.")
        except Exception as exc:
            print(renderer.render_error(str(exc)))


def main_loop(runtime: CliRuntime) -> int:
    return asyncio.run(main_loop_async(runtime))


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    workspace = Path.cwd()
    try:
        runtime = build_runtime(workspace)
    except ProviderError as exc:
        print(renderer.render_error(exc.message))
        return 1
    except Exception as exc:
        print(renderer.render_error(str(exc)))
        return 1
    return asyncio.run(main_loop_async(runtime))


if __name__ == "__main__":
    raise SystemExit(main())
