"""OneCode CLI entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from infrastructure.providers.factory import create_model_client
from prompts.assembler import DynamicPromptAssembler
from services.context.message_store import MessageStore
from services.guard import SandboxBoundary, SandboxGuard
from services.model.types import ProviderError
from services.permissions import PermissionPolicy, SessionPermissionStore
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
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
    registry = ToolRegistry(
        [
            read_file_descriptor(),
            edit_file_descriptor(),
            glob_descriptor(),
            grep_descriptor(),
            bash_descriptor(),
        ],
        permission_policy=permission_policy,
    )
    prompt_assembler = DynamicPromptAssembler(workspace, tool_registry=registry)
    context_engine = ContextEngine(
        message_store,
        prompt_assembler=prompt_assembler,
        tool_schema_provider=registry,
    )
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    permission_prompter = CliPermissionPrompter()
    tool_executor = RegistryToolExecutor(
        registry,
        guard=guard,
        permission_policy=permission_policy,
        permission_prompter=permission_prompter,
    )
    model_client = create_model_client(workspace / ".env")
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=context_engine,
        model_client=model_client,
        tool_executor=tool_executor,
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
    )


def main_loop(runtime: CliRuntime) -> int:
    print(renderer.render_banner(runtime))
    print()
    while True:
        try:
            line = input("onecode> ")
        except EOFError:
            print()
            runtime.message_store.flush_transcript()
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
            final_text = runtime.loop.run(line)
            print(renderer.render_assistant(final_text))
        except KeyboardInterrupt:
            print("\nInterrupted. Use /exit to quit.")
        except Exception as exc:
            print(renderer.render_error(str(exc)))


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
    return main_loop(runtime)


if __name__ == "__main__":
    raise SystemExit(main())
