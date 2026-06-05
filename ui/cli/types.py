"""Shared CLI runtime types."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from prompts.assembler import DynamicPromptAssembler
from services.context.message_store import MessageStore
from services.observability import TraceRecorder
from services.permissions import (
    PermissionPolicy,
    PermissionPrompter,
    SessionPermissionStore,
)
from services.subagents import CurrentModelContext, SubagentRunner
from services.tools.executor import ToolExecutor
from services.tools.registry import ToolRegistry


@dataclass
class CliRuntime:
    workspace: Path
    state: RuntimeState
    message_store: MessageStore
    registry: ToolRegistry
    loop: AgentLoop
    provider_label: str
    model: str
    model_client: Any
    tool_executor: ToolExecutor
    permission_store: SessionPermissionStore | None = None
    permission_policy: PermissionPolicy | None = None
    permission_prompter: PermissionPrompter | None = None
    trace_recorder: TraceRecorder = field(
        default_factory=lambda: TraceRecorder.noop()
    )
    current_model_context: CurrentModelContext | None = None
    subagent_runner: SubagentRunner | None = None

    def with_session(
        self,
        *,
        state: RuntimeState,
        message_store: MessageStore,
    ) -> "CliRuntime":
        self.trace_recorder.switch_session(state.session_id)
        if self.current_model_context is not None:
            self.current_model_context.snapshot = None
        if self.subagent_runner is not None:
            self.subagent_runner.bind_parent_message_store(message_store)
        context_engine = ContextEngine(
            message_store,
            prompt_assembler=DynamicPromptAssembler(
                self.workspace,
                tool_registry=self.registry,
            ),
            tool_schema_provider=self.registry,
        )
        loop = AgentLoop(
            state=state,
            message_store=message_store,
            context_engine=context_engine,
            model_client=self.model_client,
            tool_executor=self.tool_executor,
            trace_recorder=self.trace_recorder,
            current_model_context=self.current_model_context,
        )
        if self.permission_store is not None:
            self.permission_store.clear()
        return replace(
            self,
            state=state,
            message_store=message_store,
            loop=loop,
        )


@dataclass(frozen=True)
class CommandResult:
    should_exit: bool = False
    runtime: CliRuntime | None = None
