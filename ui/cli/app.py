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
from services.compaction import (
    ContextCompactionService,
    SessionMemoryStore,
    SessionMemoryUpdater,
    ToolResultStore,
)
from services.context.message_store import MessageStore
from services.guard import SandboxBoundary, SandboxGuard
from services.hooks import HookRegistry
from services.model.types import ProviderError
from services.observability import JsonlTraceSink, TraceRecorder
from services.permissions import PermissionPolicy, SessionPermissionStore
from services.subagents import CurrentModelContext, SubagentRunner
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from tools.agent import descriptor as agent_descriptor
from tools.bash import descriptor as bash_descriptor
from tools.edit_file import descriptor as edit_file_descriptor
from tools.glob import descriptor as glob_descriptor
from tools.grep import descriptor as grep_descriptor
from tools.read_file import descriptor as read_file_descriptor
from ui.cli import renderer
from ui.cli.commands import handle_command
from ui.cli.permissions import CliPermissionPrompter
from ui.cli.types import CliRuntime


def build_runtime(workspace: Path) -> CliRuntime:
    workspace = workspace.resolve()
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=workspace / ".onecode",
        session_id=state.session_id,
        cwd=workspace,
    )
    permission_store = SessionPermissionStore()
    permission_policy = PermissionPolicy(permission_store)
    trace_sink = JsonlTraceSink(workspace / ".onecode", state.session_id)
    trace_recorder = TraceRecorder(
        session_id=state.session_id,
        workspace=workspace,
        sink=trace_sink,
    )
    base_descriptors = (
        read_file_descriptor(),
        edit_file_descriptor(),
        glob_descriptor(),
        grep_descriptor(),
        bash_descriptor(),
    )
    registry = ToolRegistry(base_descriptors, permission_policy=permission_policy)
    prompt_assembler = DynamicPromptAssembler(workspace, tool_registry=registry)
    result_store = ToolResultStore(message_store.transcript_store.session_dir)
    session_memory_store = SessionMemoryStore(message_store.transcript_store.session_dir)
    session_memory_updater = SessionMemoryUpdater(
        session_memory_store,
        trace_recorder=trace_recorder,
    )
    hooks = HookRegistry(trace_recorder=trace_recorder)
    compaction_service = ContextCompactionService(
        message_store=message_store,
        session_memory_store=session_memory_store,
        result_store=result_store,
        hooks=hooks,
        trace_recorder=trace_recorder,
    )
    context_engine = ContextEngine(
        message_store,
        prompt_assembler=prompt_assembler,
        tool_schema_provider=registry,
        context_preparer=compaction_service,
    )
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    permission_prompter = CliPermissionPrompter()
    current_model_context = CurrentModelContext()
    model_client = create_model_client(workspace / ".env")
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
    compaction_service.bind_runtime(subagent_runner=subagent_runner)
    registry.register(agent_descriptor(subagent_runner))
    tool_executor = RegistryToolExecutor(
        registry,
        guard=guard,
        hooks=hooks,
        permission_policy=permission_policy,
        permission_prompter=permission_prompter,
        trace_recorder=trace_recorder,
        result_store=result_store,
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
        session_memory_updater=session_memory_updater,
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
        session_memory_updater=session_memory_updater,
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
            saw_delta = False
            final_text = ""
            async for event in runtime.loop.stream(line):
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
