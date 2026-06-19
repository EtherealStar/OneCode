"""Shared CLI runtime types."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from infrastructure.providers.factory import create_model_client
from infrastructure.filesystem.onecode_paths import sessions_dir
from prompts.assembler import DynamicPromptAssembler
from services.attachments import AttachmentCollector, AttachmentContextPreparer
from services.background_tasks import BackgroundTaskManager
from services.context.message_store import MessageStore
from services.context.current_model_context import CurrentModelContext
from services.compaction import (
    ContextCompactionService,
    SessionMemoryExtractionService,
    SessionMemoryStore,
    SessionMemoryUpdater,
)
from services.guard import SandboxGuard
from services.observability import ErrorLogRecorder, TraceRecorder
from services.mcp import McpConnectionManager
from services.hooks import HookRegistry
from services.memory import (
    InstructionMemoryLoader,
    LongTermMemoryExtractionService,
    LongTermMemoryPromptProvider,
    LongTermMemoryStore,
    RelevantMemoryContextPreparer,
    RelevantMemorySelector,
)
from services.permissions import (
    PermissionPolicy,
    PermissionPrompter,
    SessionPermissionStore,
)
from services.skills import SkillCatalogProvider
from services.subagents.runner import SubagentRunner
from services.tasks import TaskStore
from services.tools.executor import RegistryToolExecutor, ToolExecutor
from services.tools.file_state import FileStateCache
from services.tools.registry import ToolRegistry
from services.tools.types import ToolDescriptor
from tools.agent import descriptor as agent_descriptor
from ui.cli.session_memory import BackgroundSessionMemoryExtractor
from utils.toolResultStorage import ToolResultStorage


CommandPresentation = Literal["inline", "page"]
CommandInteraction = Literal["resume_selector", "connect"]


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
    error_log_recorder: ErrorLogRecorder = field(
        default_factory=lambda: ErrorLogRecorder.noop()
    )
    current_model_context: CurrentModelContext | None = None
    subagent_runner: SubagentRunner | None = None
    compaction_service: ContextCompactionService | None = None
    session_memory_store: SessionMemoryStore | None = None
    session_memory_extractor: SessionMemoryExtractionService | None = None
    session_memory_updater: SessionMemoryUpdater | None = None
    attachment_collector: AttachmentCollector | None = None
    skill_provider: SkillCatalogProvider | None = None
    mcp_manager: McpConnectionManager | None = None
    hooks: HookRegistry | None = None
    long_term_memory_store: LongTermMemoryStore | None = None
    long_term_memory_extractor: LongTermMemoryExtractionService | None = None
    instruction_memory_loader: InstructionMemoryLoader | None = None
    long_term_memory_provider: LongTermMemoryPromptProvider | None = None
    memory_selector: RelevantMemorySelector | None = None
    task_store: TaskStore | None = None
    background_task_manager: BackgroundTaskManager | None = None
    guard: SandboxGuard | None = None
    base_descriptors: tuple[ToolDescriptor, ...] = ()
    subagent_runner_ref: dict[str, SubagentRunner] | None = None
    long_term_memory_extractor_ref: dict[str, LongTermMemoryExtractionService] | None = None

    def with_session(
        self,
        *,
        state: RuntimeState,
        message_store: MessageStore,
        file_state_cache: FileStateCache | None = None,
    ) -> "CliRuntime":
        self.trace_recorder.switch_session(state.session_id)
        self.error_log_recorder.switch_session(state.session_id)
        if self.current_model_context is not None:
            self.current_model_context.snapshot = None
        if self.subagent_runner is not None:
            self.subagent_runner.bind_parent_message_store(message_store)
        state.metadata["workspace"] = str(self.workspace)
        state.metadata["session_memory_resume_needs_extraction"] = True
        try:
            resume_generation = int(
                state.metadata.get("session_memory_resume_generation", 0)
            )
        except (TypeError, ValueError):
            resume_generation = 0
        state.metadata["session_memory_resume_generation"] = resume_generation + 1
        session_memory_store = None
        result_store = ToolResultStorage(message_store.transcript_store.session_dir)
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
        file_state_cache = file_state_cache or FileStateCache()
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
                instruction_memory_loader=self.instruction_memory_loader,
                long_term_memory_provider=self.long_term_memory_provider,
            ),
            tool_schema_provider=self.registry,
            context_preparer=AttachmentContextPreparer(
                RelevantMemoryContextPreparer(
                    self.long_term_memory_store,
                    self.memory_selector,
                    inner=self.compaction_service,
                )
                if self.long_term_memory_store is not None
                and self.memory_selector is not None
                else self.compaction_service
            ),
        )
        loop = AgentLoop(
            state=state,
            message_store=message_store,
            context_engine=context_engine,
            model_client=self.model_client,
            tool_executor=self.tool_executor,
            trace_recorder=self.trace_recorder,
            current_model_context=self.current_model_context,
            hooks=self.hooks,
            compaction_service=self.compaction_service,
            session_memory_extractor=(
                BackgroundSessionMemoryExtractor(
                    session_memory_extractor,
                    self.background_task_manager,
                )
                if session_memory_extractor is not None
                and self.background_task_manager is not None
                else session_memory_extractor
            ),
            session_memory_updater=session_memory_updater,
            error_log_recorder=self.error_log_recorder,
        )
        if self.permission_store is not None:
            self.permission_store.clear()
        if self.mcp_manager is not None:
            state.metadata["mcp_server_instructions"] = self.mcp_manager.snapshot().instructions
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

    def with_model_config(self) -> "CliRuntime":
        """Reload `.env` provider settings while preserving the active session."""

        model_client = create_model_client(self.workspace / ".env")
        config = model_client.config
        current_model_context = self.current_model_context or CurrentModelContext()
        current_model_context.snapshot = None
        memory_selector = RelevantMemorySelector(
            model_client=model_client,
            trace_recorder=self.trace_recorder,
        )

        subagent_runner = self.subagent_runner
        if (
            self.guard is not None
            and self.permission_policy is not None
            and self.base_descriptors
        ):
            subagent_runner = SubagentRunner(
                workspace=self.workspace,
                transcript_root=sessions_dir(self.workspace),
                parent_message_store=self.message_store,
                current_model_context=current_model_context,
                model_client=model_client,
                base_descriptors=self.base_descriptors,
                guard=self.guard,
                permission_policy=self.permission_policy,
                permission_prompter=self.permission_prompter,
                trace_recorder=self.trace_recorder,
            )
            if self.subagent_runner_ref is not None:
                self.subagent_runner_ref["runner"] = subagent_runner

        registry = self.registry
        if subagent_runner is not None and self.base_descriptors:
            registry = ToolRegistry(
                (
                    *self.base_descriptors,
                    agent_descriptor(subagent_runner, self.background_task_manager),
                ),
                permission_policy=self.permission_policy,
            )

        session_memory_extractor = self.session_memory_extractor
        if self.session_memory_store is not None and subagent_runner is not None:
            session_memory_extractor = SessionMemoryExtractionService(
                self.session_memory_store,
                subagent_runner=subagent_runner,
                trace_recorder=self.trace_recorder,
            )
        session_memory_updater = self.session_memory_updater
        if self.session_memory_store is not None:
            session_memory_updater = SessionMemoryUpdater(
                self.session_memory_store,
                trace_recorder=self.trace_recorder,
            )
        long_term_memory_extractor = self.long_term_memory_extractor
        if self.long_term_memory_store is not None and subagent_runner is not None:
            long_term_memory_extractor = LongTermMemoryExtractionService(
                self.long_term_memory_store,
                subagent_runner=subagent_runner,
                trace_recorder=self.trace_recorder,
            )
            if self.long_term_memory_extractor_ref is not None:
                self.long_term_memory_extractor_ref["extractor"] = (
                    long_term_memory_extractor
                )

        if self.compaction_service is not None:
            self.compaction_service.bind_runtime(subagent_runner=subagent_runner)
            self.compaction_service.bind_runtime(
                session_memory_extractor=session_memory_extractor
            )

        context_engine = ContextEngine(
            self.message_store,
            prompt_assembler=DynamicPromptAssembler(
                self.workspace,
                tool_registry=registry,
                skill_provider=self.skill_provider,
                instruction_memory_loader=self.instruction_memory_loader,
                long_term_memory_provider=self.long_term_memory_provider,
            ),
            tool_schema_provider=registry,
            context_preparer=AttachmentContextPreparer(
                RelevantMemoryContextPreparer(
                    self.long_term_memory_store,
                    memory_selector,
                    inner=self.compaction_service,
                )
                if self.long_term_memory_store is not None
                else self.compaction_service
            ),
        )

        result_store = ToolResultStorage(self.message_store.transcript_store.session_dir)
        file_state_cache = (
            self.tool_executor.file_state_cache
            if hasattr(self.tool_executor, "file_state_cache")
            else FileStateCache()
        )
        tool_executor: ToolExecutor = self.tool_executor
        if self.guard is not None:
            tool_executor = RegistryToolExecutor(
                registry,
                guard=self.guard,
                hooks=self.hooks,
                permission_policy=self.permission_policy,
                permission_prompter=self.permission_prompter,
                trace_recorder=self.trace_recorder,
                error_log_recorder=self.error_log_recorder,
                result_store=result_store,
                file_state_cache=file_state_cache,
            )

        loop = AgentLoop(
            state=self.state,
            message_store=self.message_store,
            context_engine=context_engine,
            model_client=model_client,
            tool_executor=tool_executor,
            trace_recorder=self.trace_recorder,
            current_model_context=current_model_context,
            hooks=self.hooks,
            compaction_service=self.compaction_service,
            session_memory_extractor=(
                BackgroundSessionMemoryExtractor(
                    session_memory_extractor,
                    self.background_task_manager,
                )
                if session_memory_extractor is not None
                and self.background_task_manager is not None
                else session_memory_extractor
            ),
            session_memory_updater=session_memory_updater,
            error_log_recorder=self.error_log_recorder,
        )
        return replace(
            self,
            registry=registry,
            loop=loop,
            provider_label=config.display_name,
            model=config.model,
            model_client=model_client,
            tool_executor=tool_executor,
            current_model_context=current_model_context,
            subagent_runner=subagent_runner,
            session_memory_extractor=session_memory_extractor,
            session_memory_updater=session_memory_updater,
            long_term_memory_extractor=long_term_memory_extractor,
            memory_selector=memory_selector,
        )


@dataclass(frozen=True)
class CommandResult:
    should_exit: bool = False
    runtime: CliRuntime | None = None
    renderable: object | None = None
    presentation: CommandPresentation = "inline"
    interaction: CommandInteraction | None = None
    reset_main_view: bool = False
    # Messages a successful command asks the REPL to replay into the main
    # scrollback using the normal static-output renderers. This is purely a
    # UI replay request (e.g. session resume); it is not a source of truth
    # for the model context, which lives in the runtime's MessageStore.
    replay_messages: tuple[dict[str, Any], ...] = field(default_factory=tuple)
