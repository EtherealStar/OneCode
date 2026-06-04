"""Shared CLI runtime types."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
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

    def with_session(
        self,
        *,
        state: RuntimeState,
        message_store: MessageStore,
    ) -> "CliRuntime":
        context_engine = ContextEngine(message_store, tool_schema_provider=self.registry)
        loop = AgentLoop(
            state=state,
            message_store=message_store,
            context_engine=context_engine,
            model_client=self.model_client,
            tool_executor=self.tool_executor,
        )
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
