"""Shared CLI runtime types."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from prompts.assembler import DynamicPromptAssembler
from services.attachments import AttachmentCollector, AttachmentContextPreparer
from services.context.message_store import MessageStore
from services.context.current_model_context import CurrentModelContext
from services.compaction import (
    ContextCompactionService,
    SessionMemoryExtractionService,
    SessionMemoryStore,
    SessionMemoryUpdater,
    ToolResultStore,
)
from services.observability import TraceRecorder
from services.permissions import (
    PermissionPolicy,
    PermissionPrompter,
    SessionPermissionStore,
)
from services.skills import SkillCatalogProvider
from services.subagents.runner import SubagentRunner
from services.tools.executor import ToolExecutor
from services.tools.file_state import FileStateCache
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
    compaction_service: ContextCompactionService | None = None
    session_memory_store: SessionMemoryStore | None = None
    session_memory_extractor: SessionMemoryExtractionService | None = None
    session_memory_updater: SessionMemoryUpdater | None = None
    attachment_collector: AttachmentCollector | None = None
    skill_provider: SkillCatalogProvider | None = None

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
        state.metadata["session_memory_resume_needs_extraction"] = True
        try:
            resume_generation = int(
                state.metadata.get("session_memory_resume_generation", 0)
            )
        except (TypeError, ValueError):
            resume_generation = 0
        state.metadata["session_memory_resume_generation"] = resume_generation + 1
        session_memory_store = None
        result_store = ToolResultStore(message_store.transcript_store.session_dir)
        if self.session_memory_store is not None:
            session_memory_store = SessionMemoryStore(
                message_store.transcript_store.session_dir
            )
        bind_result_store = getattr(self.tool_executor, "bind_result_store", None)
        if callable(bind_result_store):
            bind_result_store(result_store)
        if self.compaction_service is not None:
            self.compaction_service.bind_runtime(
                message_store=message_store,
                session_memory_store=session_memory_store,
                result_store=result_store,
            )
        file_state_cache = FileStateCache()
        bind_file_state_cache = getattr(self.tool_executor, "bind_file_state_cache", None)
        if callable(bind_file_state_cache):
            bind_file_state_cache(file_state_cache)
        attachment_collector = self.attachment_collector
        if attachment_collector is not None:
            attachment_collector = AttachmentCollector(
                workspace=self.workspace,
                reader=attachment_collector.reader,
                file_state_cache=file_state_cache,
                shared_sources=attachment_collector.shared_sources,
            )
        session_memory_extractor = self.session_memory_extractor
        if session_memory_store is not None and self.subagent_runner is not None:
            session_memory_extractor = SessionMemoryExtractionService(
                session_memory_store,
                subagent_runner=self.subagent_runner,
                trace_recorder=self.trace_recorder,
            )
            bind_extractor = getattr(
                self.compaction_service,
                "bind_session_memory_extractor",
                None,
            )
            if callable(bind_extractor):
                bind_extractor(session_memory_extractor)
        session_memory_updater = self.session_memory_updater
        if session_memory_store is not None:
            session_memory_updater = SessionMemoryUpdater(
                session_memory_store,
                trace_recorder=self.trace_recorder,
            )
        context_engine = ContextEngine(
            message_store,
            prompt_assembler=DynamicPromptAssembler(
                self.workspace,
                tool_registry=self.registry,
                skill_provider=self.skill_provider,
            ),
            tool_schema_provider=self.registry,
            context_preparer=AttachmentContextPreparer(self.compaction_service),
        )
        loop = AgentLoop(
            state=state,
            message_store=message_store,
            context_engine=context_engine,
            model_client=self.model_client,
            tool_executor=self.tool_executor,
            trace_recorder=self.trace_recorder,
            current_model_context=self.current_model_context,
            compaction_service=self.compaction_service,
            session_memory_extractor=session_memory_extractor,
            session_memory_updater=session_memory_updater,
        )
        if self.permission_store is not None:
            self.permission_store.clear()
        return replace(
            self,
            state=state,
            message_store=message_store,
            loop=loop,
            session_memory_store=session_memory_store or self.session_memory_store,
            session_memory_extractor=session_memory_extractor,
            session_memory_updater=session_memory_updater,
            attachment_collector=attachment_collector,
        )


@dataclass(frozen=True)
class CommandResult:
    should_exit: bool = False
    runtime: CliRuntime | None = None
