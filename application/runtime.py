"""Application-layer runtime assembly and rebinding.

This module owns the concrete wiring that used to live in
``ui.cli.app`` and ``ui.cli.types``. It builds the runtime, rebinds it to a
new session, and reloads the model configuration. It deliberately does not
import any UI module: the application layer depends on
``core``/``services``/``infrastructure`` only.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from infrastructure.filesystem.onecode_paths import sessions_dir
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
    SessionMemoryUpdater,
)
from services.context.current_model_context import CurrentModelContext
from services.context.message_store import MessageStore
from services.guard import SandboxBoundary, SandboxGuard
from services.hooks import HookEvent, HookRegistry
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
from services.memory import (
    InstructionMemoryLoader,
    LongTermMemoryExtractionService,
    LongTermMemoryPromptProvider,
    LongTermMemoryStore,
    RelevantMemoryContextPreparer,
    RelevantMemorySelector,
)
from services.model.types import ProviderError
from services.observability import (
    ErrorLogRecorder,
    JsonlErrorLogSink,
    JsonlTraceSink,
    TraceRecorder,
)
from services.permissions import (
    PermissionPolicy,
    PermissionPrompter,
    PermissionResponse,
    ProjectPermissionSettingsStore,
    SessionPermissionStore,
)
from services.plans.store import PlanStore
from services.questions.prompter import UserQuestionPrompter
from services.skills import LoaderSkillCatalogProvider, SkillCatalogProvider
from services.subagents.runner import SubagentRunner
from services.tasks import TaskStore
from services.tools.executor import RegistryToolExecutor, ToolExecutor
from services.tools.file_state import FileStateCache
from services.tools.registry import ToolRegistry
from services.tools.types import ToolDescriptor
from tools.agent import descriptor as agent_descriptor
from tools.ask_user_question import descriptor as ask_user_question_descriptor
from tools.bash import descriptor as bash_descriptor
from tools.background_task_stop import descriptor as background_task_stop_descriptor
from tools.edit_file import descriptor as edit_file_descriptor
from tools.enter_plan_mode import descriptor as enter_plan_mode_descriptor
from tools.exit_plan_mode import descriptor as exit_plan_mode_descriptor
from tools.glob import descriptor as glob_descriptor
from tools.grep import descriptor as grep_descriptor
from tools.read_file import descriptor as read_file_descriptor
from tools.skill import descriptor as skill_descriptor
from tools.task_create import descriptor as task_create_descriptor
from tools.task_get import descriptor as task_get_descriptor
from tools.task_list import descriptor as task_list_descriptor
from tools.task_update import descriptor as task_update_descriptor
from tools.write_file import descriptor as write_file_descriptor
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


class DefaultUserQuestionPrompter:
    """Non-interactive fallback for the ``ask_user_question`` tool.

    Interactive adapters (batch/TUI) install their own prompter. This fallback
    accepts the first option of each question so a runtime that has not
    installed an adapter still produces a structured, non-crashing answer.
    """

    async def ask_questions(
        self,
        questions: tuple[Any, ...],
    ) -> Any:
        from services.questions.types import AnswerRecord, QuestionResponse

        answers: list[AnswerRecord] = []
        for question in questions:
            if not question.options:
                return QuestionResponse(declined=True, feedback="no_options")
            label = question.options[0].label
            answers.append(
                AnswerRecord(
                    question=question.question,
                    answer=label if not question.multi_select else (label,),
                )
            )
        return QuestionResponse(answers=tuple(answers))


class DeferredPermissionPrompter:
    """Indirection that lets the session controller install its own prompter.

    The tool executor captures the prompter at assembly time. The application
    creates this proxy so the ``SessionController`` can route permission
    requests through its interaction coordinator after construction.
    """

    def __init__(self, target: PermissionPrompter | None = None) -> None:
        self.target = target

    async def request_permission(self, request) -> PermissionResponse:
        if self.target is None:
            return PermissionResponse(
                action="deny",
                feedback="No permission prompter is installed.",
            )
        return await self.target.request_permission(request)


class DeferredUserQuestionPrompter:
    """Indirection for the ``ask_user_question`` tool prompter."""

    def __init__(self, target: Any | None = None) -> None:
        self.target = target or DefaultUserQuestionPrompter()

    async def ask_questions(self, questions) -> Any:
        return await self.target.ask_questions(questions)


class BackgroundSessionMemoryExtractor:
    """Schedule session-memory extraction without blocking the active turn."""

    def __init__(
        self,
        extractor: SessionMemoryExtractionService,
        background_task_manager: BackgroundTaskManager,
    ) -> None:
        self.extractor = extractor
        self._background_task_manager = background_task_manager

    async def maybe_extract_after_model_response(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        assistant_message: dict[str, Any],
        tool_calls: tuple[Any, ...],
        usage: Any | None = None,
    ) -> None:
        job = self.extractor.prepare_extraction_job(
            messages,
            state,
            assistant_message=assistant_message,
            tool_calls=tool_calls,
            usage=usage,
        )
        if job is None:
            return

        async def run(task_id: str) -> dict[str, Any]:
            task = self._background_task_manager.get(task_id)
            output_path = (
                Path(str(task.metadata.get("output_path_abs", "")))
                if task is not None
                else None
            )
            if output_path is not None:
                _append_output(output_path, "dream: updating session memory\n")
                _append_output(output_path, f"parent_session_id: {job.parent_session_id}\n")
            try:
                result = await self.extractor.run_extraction_job(job, state)
            except asyncio.CancelledError:
                self.extractor.record_background_cancelled(state)
                raise
            if output_path is not None:
                _append_output(output_path, "dream: completed\n")
                result_session_id = result.get("result_session_id")
                if result_session_id:
                    _append_output(output_path, f"child_session_id: {result_session_id}\n")
            return result

        task = self._background_task_manager.start_dream(
            description="updating session memory",
            state=state,
            run=run,
            metadata={"memory_path": str(self.extractor.store.path)},
        )
        self.extractor.record_background_task(state, task.id)

    async def wait_for_current_extraction(self, state: RuntimeState) -> None:
        await self.extractor.wait_for_current_extraction(state)


def _append_output(path: Path, text: str) -> None:
    try:
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
    except OSError:
        return


@dataclass
class ApplicationRuntime:
    workspace: Path
    state: RuntimeState
    message_store: MessageStore
    registry: ToolRegistry | None = None
    loop: AgentLoop | None = None
    provider_label: str = ""
    model: str = ""
    model_client: Any = None
    tool_executor: ToolExecutor | None = None
    configured: bool = True
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
    plan_store: PlanStore | None = None
    user_question_prompter: UserQuestionPrompter | None = None

    def with_session(
        self,
        *,
        state: RuntimeState,
        message_store: MessageStore,
        file_state_cache: FileStateCache | None = None,
    ) -> "ApplicationRuntime":
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
            plan_store=self.plan_store,
            user_question_prompter=self.user_question_prompter,
        )

    def with_model_config(self) -> "ApplicationRuntime":
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
            plan_store=self.plan_store,
            user_question_prompter=self.user_question_prompter,
            configured=True,
        )


def build_runtime(
    workspace: Path,
    *,
    trust_prompt: Callable[[McpTrustPromptRequest], TrustChoice] | None = None,
    permission_prompter: PermissionPrompter | None = None,
    mcp_trust_mode: McpTrustMode = "prompt",
) -> ApplicationRuntime:
    workspace = workspace.resolve()
    state = RuntimeState()
    state.metadata["workspace"] = str(workspace)
    message_store = MessageStore(
        transcript_root=sessions_dir(workspace),
        session_id=state.session_id,
        cwd=workspace,
    )
    permission_prompter = DeferredPermissionPrompter(permission_prompter)
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
    trace_sink = JsonlTraceSink(sessions_dir(workspace), state.session_id)
    trace_recorder = TraceRecorder(
        session_id=state.session_id,
        workspace=workspace,
        sink=trace_sink,
    )
    error_log_sink = JsonlErrorLogSink(sessions_dir(workspace), state.session_id)
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
    plan_store = PlanStore(workspace)
    user_question_prompter = DeferredUserQuestionPrompter()
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
        enter_plan_mode_descriptor(plan_store),
        exit_plan_mode_descriptor(plan_store),
        ask_user_question_descriptor(user_question_prompter),
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
        transcript_root=sessions_dir(workspace),
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
    background_session_memory_extractor = BackgroundSessionMemoryExtractor(
        session_memory_extractor,
        background_task_manager,
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
        session_memory_extractor=background_session_memory_extractor,
        error_log_recorder=error_log_recorder,
    )
    config = model_client.config
    return ApplicationRuntime(
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
        plan_store=plan_store,
        user_question_prompter=user_question_prompter,
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
            # No interactive adapter was installed: never read stdin from the
            # application layer. Leave the server untrusted and visible in the
            # runtime metadata so a UI can prompt for trust later.
            print("Project MCP stdio server requires trust before it can run:")
            print(f"  server: {config.name}")
            print(f"  command: {request.command}")
            print(f"  args: {request.args}")
            print(f"  cwd: {request.cwd}")
            print(f"  explicit env keys: {request.explicit_env_keys}")
            print(f"  base env keys: {request.base_env_keys}")
            print(f"Skipping untrusted MCP server: {config.name}")
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


def build_unconfigured_runtime(workspace: Path) -> ApplicationRuntime:
    """Create a minimal runtime when ``.env`` is missing or incomplete.

    The returned runtime has ``configured=False`` so input is blocked
    except for configuration and exit operations.
    """

    workspace = workspace.resolve()
    state = RuntimeState()
    state.metadata["workspace"] = str(workspace)
    message_store = MessageStore(
        transcript_root=sessions_dir(workspace),
        session_id=state.session_id,
        cwd=workspace,
    )
    trace_sink = JsonlTraceSink(sessions_dir(workspace), state.session_id)
    trace_recorder = TraceRecorder(
        session_id=state.session_id,
        workspace=workspace,
        sink=trace_sink,
    )
    error_log_sink = JsonlErrorLogSink(sessions_dir(workspace), state.session_id)
    error_log_recorder = ErrorLogRecorder(
        session_id=state.session_id,
        workspace=workspace,
        sink=error_log_sink,
    )
    return ApplicationRuntime(
        workspace=workspace,
        state=state,
        message_store=message_store,
        configured=False,
        trace_recorder=trace_recorder,
        error_log_recorder=error_log_recorder,
    )


__all__ = [
    "ApplicationRuntime",
    "BackgroundSessionMemoryExtractor",
    "McpTrustPromptRequest",
    "McpTrustMode",
    "TrustChoice",
    "build_runtime",
    "build_unconfigured_runtime",
]
