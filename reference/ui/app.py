from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID, uuid4

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.css.query import NoMatches
from textual.widgets import Static, TextArea

from ..provider.config import (
    Configured,
    ProviderConfigStore,
    ProviderConfiguration,
    ProviderConfigurationDraft,
)
from ..provider.errors import ProviderConfigurationError
from ..provider.openai import ModelInfo, OpenAICompatibleModelAdapter
from ..workspace_references import WorkspacePathIndex, WorkspaceReferenceService
from ..ports import Cancellation
from ..context import ContextManager, PromptInputs
from ..prompts import BEHAVIOR_RULES, RISK_CONSTRAINTS, VALIDATION_RULES
from ..hooks import FastToolValidationHook, HookDispatcher, HookRegistry
from ..tools import build_default_registry
from ..documents import DocumentCache
from ..tools.authorization import PermissionDecision, PermissionRequest
from ..tools.config import ExternalToolConfigLoader
from ..tools.executor import ToolExecutor
from ..tools.file_state import ReadFileStateStore
from ..tools.read_docs.client import MinerUClient
from ..tools.todo_write.tool import TodoStore
from ..memory.runner import MemoryAgentRunner
from ..memory.runtime import MemoryRuntime
from ..memory.selector import ModelRelevantMemorySelector
from ..memory.selection_hook import MemorySelectionHook, MemorySelectionState
from ..memory.store import FileMemoryStore
from ..repository import SessionRepository
from ..storage_paths import SESSIONS_ROOT
from ..trace import NullTraceSink, TraceContext, TraceEvent, TraceEventType, TraceSink, sanitize_error
from ..updates import (
    AssistantMessageCompleted,
    AssistantMessageDiscarded,
    AssistantMessageStarted,
    PermissionRequested,
    PermissionResolved,
    RunTerminated,
    ToolResultCompleted,
    TodosChanged,
    SessionUsageUpdated,
    UserMessageCommitted,
)
from ..ui.projection import MessageLifecycle, UiProjection
from .commands import CommandName, complete_command, has_slash_query, parse_command, suggest_commands
from .completion import CompletionItem, CompletionMode, CompletionOverlay
from .composer import Composer
from .workspace_completion import WorkspaceCompletionController, find_workspace_query
from .modals.model_picker import ModelPickerModal
from .modals.provider_connect import ProviderConnectModal
from .modals.session_picker import SessionPickerModal
from .modals.permission import PermissionModal
from .status_modal import StatusModal, StatusViewState
from ..usage import SessionUsage
from .renderers.status import RunState
from .renderers.todo import render_todo_list
from .renderers.tool import build_tool_presentation_registry
from .refresh_scheduler import UiRefreshScheduler
from .session_facade import RuntimeSession
from .status_bar import StatusBar
from .theme import apply_theme
from .viewport import MessageViewport, NewContentButton
from .runtime import RuntimeConfig, RuntimeConfigStore


class ModelCatalog(Protocol):
    async def list_models(self, cancellation: Cancellation) -> tuple[ModelInfo, ...]: ...


class _UnavailableLoop:
    async def run(self, initial_messages, user_message, system_prompt, max_turns, committer, cancellation, run_id):
        from ..domain import AgentRunResult, ErrorInfo, StopReason

        await committer.finish_run(run_id, AgentRunResult(StopReason.MODEL_UNAVAILABLE, 0, error=ErrorInfo("model_unavailable", "尚未配置模型")))


class _ConfiguredLoop:
    def __init__(
        self, configuration, tool_configuration, permission_requester, todo_store,
        read_file_state_store, workspace_root: Path,
        memory_selection_hook=None, memory_runtime=None, trace_sink=None,
    ) -> None:
        self.configuration = configuration
        self.tool_configuration = tool_configuration
        self.permission_requester = permission_requester
        self.todo_store = todo_store
        self.read_file_state_store = read_file_state_store
        self.workspace_root = workspace_root
        self.memory_selection_hook = memory_selection_hook
        self.memory_runtime = memory_runtime
        self.trace_sink = trace_sink

    async def run(self, initial_messages, user_message, system_prompt, max_turns, committer, cancellation, run_id):
        from ..loop import AgentLoop

        external_names = []
        capabilities = {"todo_store": self.todo_store}
        enabled_reads = set()
        mineru_client = None
        tavily_client = None
        if self.tool_configuration.tavily_api_key:
            from tavily import AsyncTavilyClient
            tavily_client = AsyncTavilyClient(api_key=self.tool_configuration.tavily_api_key)
            capabilities["tavily_client"] = tavily_client
            external_names.append("web_search")
            enabled_reads.add("api.tavily.com")
        if self.tool_configuration.mineru_api_token:
            mineru_client = MinerUClient(self.tool_configuration.mineru_api_token)
            capabilities["mineru_client"] = mineru_client
            external_names.append("read_docs")
        registry = build_default_registry(external_tools=tuple(external_names))
        hook_registry = HookRegistry()
        if self.memory_selection_hook is not None:
            hook_registry.register_pre_model_call(self.memory_selection_hook)
        hook_registry.register_pre_tool_use(FastToolValidationHook())
        if self.memory_runtime is not None:
            hook_registry.register_agent_run_stopped(self.memory_runtime)
        dispatcher = HookDispatcher(hook_registry.freeze(), self.trace_sink)
        model = OpenAICompatibleModelAdapter(self.configuration)
        workspace = self.workspace_root
        document_registry = committer.ensure_document_registry(workspace)
        capabilities["document_cache"] = DocumentCache(workspace, document_registry)
        authorizer = committer.ensure_target_authorizer(
            workspace,
            enabled_external_reads=enabled_reads,
            requester=self.permission_requester,
        )
        executor = ToolExecutor(
            registry.enabled_view(), workspace, str(committer.session_id),
            runtime_capabilities=capabilities, target_authorizer=authorizer,
            read_file_state_store=self.read_file_state_store,
        )
        agents_path = workspace / "AGENTS.md"
        try:
            agents_md = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else ""
        except OSError:
            agents_md = ""
        frozen_now = datetime.now().astimezone()
        # 工作空间事实只在 AgentRun 开始时读取一次，后续 ModelCall 复用同一快照。
        prompt_inputs = PromptInputs(
            identity=system_prompt,
            behavior_rules=BEHAVIOR_RULES,
            risk_constraints=RISK_CONSTRAINTS,
            validation_rules=VALIDATION_RULES,
            workspace_state=str(workspace),
            agents_md=agents_md,
            current_time=frozen_now,
            timezone_name=str(frozen_now.tzinfo or ""),
        )
        try:
            return await AgentLoop(
                model,
                ContextManager(),
                executor,
                registry.enabled_view().specs,
                dispatcher=dispatcher,
                model_configuration=self.configuration,
            ).run(initial_messages, user_message, prompt_inputs, max_turns, committer, cancellation, run_id)
        finally:
            closers = [model.close()]
            if mineru_client is not None:
                closers.append(mineru_client.close())
            if tavily_client is not None:
                closers.append(tavily_client.close())
            await asyncio.gather(*closers, return_exceptions=True)


class MiniAgentApp(App[None]):
    TITLE = "MiniAgent"
    CSS_PATH = "miniagent.tcss"
    BINDINGS = [
        Binding("ctrl+c", "cancel_or_quit", "取消/退出", show=True),
        # Composer 聚焦时 TextArea 原生占用 End（行尾）；Ctrl+End 始终返回最新。
        Binding("end,ctrl+end", "jump_to_latest", "最新消息", show=False),
        Binding("ctrl+o", "toggle_details", "展开/折叠详情", show=False),
    ]

    def __init__(
        self,
        *,
        repository: SessionRepository | None = None,
        loop_factory: Callable[[], object] | None = None,
        model_catalog: ModelCatalog | None = None,
        workspace_root: Path | None = None,
        dotenv_path: Path | None = None,
        provider_adapter_factory: Callable[[ProviderConfiguration], ModelCatalog] | None = None,
        trace_sink: TraceSink | None = None,
        raise_ui_exceptions: bool = False,
        workspace_index: WorkspacePathIndex | None = None,
        reference_service: WorkspaceReferenceService | None = None,
        memory_runtime: MemoryRuntime | None = None,
    ) -> None:
        super().__init__()
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self._workspace_index = workspace_index or WorkspacePathIndex(self.workspace_root)
        self._reference_service = reference_service or WorkspaceReferenceService(self.workspace_root)
        self._workspace_completion = WorkspaceCompletionController()
        self._dotenv_path = dotenv_path or self.workspace_root / ".env"
        self._provider_store = ProviderConfigStore(self._dotenv_path)
        try:
            loaded = self._provider_store.load()
        except ProviderConfigurationError:
            loaded = None
        self._provider_configuration = loaded.configuration if isinstance(loaded, Configured) else None
        self.repository = repository or SessionRepository(self.workspace_root / SESSIONS_ROOT)
        self.projection = UiProjection()
        self._tool_presentations = build_tool_presentation_registry(self.workspace_root)
        self.current: RuntimeSession | None = None
        self._loop_factory = loop_factory or self._default_loop_factory
        self.config = RuntimeConfigStore(
            RuntimeConfig(self._provider_configuration.model if self._provider_configuration else None)
        )
        self._todo_store = TodoStore()
        self._read_file_state_store = ReadFileStateStore()
        self._transition_lock = asyncio.Lock()
        self._last_ctrl_c = 0.0
        self._model_catalog = model_catalog
        self._provider_adapter_factory = provider_adapter_factory or OpenAICompatibleModelAdapter
        self._cached_model_catalog: tuple[ModelInfo, ...] | None = None
        self._trace_sink = trace_sink or NullTraceSink()
        self._memory_selection_hook = None
        if memory_runtime is None:
            memory_store = FileMemoryStore(self.workspace_root)
            memory_store.ensure_exists()
            selection_state = MemorySelectionState()
            self._memory_selection_hook = MemorySelectionHook(
                memory_store,
                ModelRelevantMemorySelector(),
                selection_state,
                trace_sink=self._trace_sink,
            )
            memory_runner = MemoryAgentRunner(
                self.workspace_root,
                read_file_state_store=self._read_file_state_store,
                trace_sink=self._trace_sink,
            )
            memory_runtime = MemoryRuntime(
                selection_state,
                memory_runner,
                trace_sink=self._trace_sink,
                provider_configuration_getter=self._current_provider_configuration,
            )
        self._memory_runtime = memory_runtime
        self._memory_runtime_closed = False
        self._accepted_runs: dict[UUID, tuple[UUID, UUID]] = {}
        self._raise_ui_exceptions = raise_ui_exceptions
        self._refresh_scheduler: UiRefreshScheduler | None = None
        self._run_active = False
        self._permission_task: asyncio.Task[None] | None = None
        self._model_task: asyncio.Task[tuple[tuple[ModelInfo, ...] | None, str | None]] | None = None
        self._model_delivery_tasks: set[asyncio.Task[None]] = set()
        self._completion_suppressed_text: str | None = None
        self._connect_drafts: dict[ModelPickerModal, ProviderConfigurationDraft] = {}
        self._details_expanded = False
        self._status_modal: StatusModal | None = None

    def compose(self) -> ComposeResult:
        with Container(id="chat-wrap"):
            yield Static(id="todo-list")
            yield MessageViewport(tool_presentations=self._tool_presentations, id="message-viewport")
            yield NewContentButton("↓ 新内容", id="new-content")
        yield StatusBar(id="status-bar")
        with Container(id="composer-wrap"):
            yield Composer(id="composer")
            yield Static("输入消息，/ 打开命令", id="composer-hint")
            yield CompletionOverlay(id="completion-overlay")

    async def on_mount(self) -> None:
        apply_theme(self)
        self.query_one(Composer).focus()
        self._refresh_scheduler = UiRefreshScheduler(self.set_timer, self._flush_view)
        self._update_status()
        self._update_run_state()
        try:
            await self._workspace_index.start()
        except Exception:
            # 索引是非权威候选投影，启动失败不能阻断普通输入与已接受引用提交。
            pass

    async def on_unmount(self) -> None:
        self._close_status_modal()
        self._workspace_completion.close()
        try:
            await self._workspace_index.close()
        except Exception:
            pass
        if self._refresh_scheduler is not None:
            self._refresh_scheduler.close()
        if self._model_task is not None:
            self._model_task.cancel()
            try:
                await self._model_task
            except asyncio.CancelledError:
                pass
            self._model_task = None
        deliveries = tuple(self._model_delivery_tasks)
        for task in deliveries:
            task.cancel()
        if deliveries:
            await asyncio.gather(*deliveries, return_exceptions=True)
        self._model_delivery_tasks.clear()
        self._close_permission_modal()
        if self.current is not None:
            await self.current.stop("APPLICATION_UNMOUNTED")
            self.current = None
        await self._close_memory_runtime()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "composer":
            try:
                composer = self.query_one(Composer)
                composer.sync_external_text()
                self.query_one("#composer-hint", Static).display = not event.text_area.text
                overlay = self.query_one(CompletionOverlay)
                if event.text_area.text == self._completion_suppressed_text:
                    overlay.hide()
                    return
                self._completion_suppressed_text = None
                if has_slash_query(event.text_area.text):
                    self._workspace_completion.close()
                    items = tuple(
                        CompletionItem(CompletionMode.SLASH, item.command.value, item.command.value, item.description, item)
                        for item in suggest_commands(event.text_area.text)
                    )
                    if overlay.mode is not CompletionMode.SLASH:
                        overlay.enter_mode(CompletionMode.SLASH)
                    # 查询边界拥有模式；候选为空只替换为不可选择的安静状态。
                    overlay.replace_items(items, empty_label="无匹配命令")
                    return
                snapshot = composer.reference_state.snapshot()
                ranges = tuple((item.start, item.end) for item in snapshot.references)
                query = find_workspace_query(
                    snapshot.text,
                    composer.cursor_offset(),
                    ranges,
                    parse_command(snapshot.text) is not None,
                )
                if query is None:
                    self._workspace_completion.close()
                    overlay.exit_mode()
                    return
                try:
                    self._workspace_index.request_refresh_if_degraded()
                except Exception:
                    pass
                if overlay.mode is not CompletionMode.WORKSPACE:
                    # Workspace Completion Mode 一进入就拥有固定容量，与首批结果数量无关。
                    overlay.enter_mode(CompletionMode.WORKSPACE)
                    overlay.replace_items((), empty_label="无匹配项")
                task = self._workspace_completion.update(snapshot, query, self._workspace_index.snapshot())
                if overlay.option_count:
                    overlay.set_pending()
                if task is not None:
                    task.add_done_callback(lambda done: self.call_later(self._apply_workspace_result, done))
            except NoMatches:
                # A queued Changed message may arrive after a modal becomes the
                # current screen; the underlying composer remains unchanged.
                pass

    async def on_composer_completion_requested(self, event: Composer.CompletionRequested) -> None:
        ranges = tuple((item.start, item.end) for item in event.snapshot.references)
        query = find_workspace_query(
            event.snapshot.text,
            event.cursor_offset,
            ranges,
            parse_command(event.snapshot.text) is not None,
        )
        if query is not None and query != self._workspace_completion.query:
            self._workspace_completion.update(event.snapshot, query, self._workspace_index.snapshot())
        if self._workspace_completion.query is not None:
            candidate = await self._workspace_completion.resolve_for_accept()
            if candidate is not None and self._workspace_completion.query is not None:
                self.query_one(Composer).accept_workspace(self._workspace_completion.query, candidate)
                self._workspace_completion.close()
                self.query_one(CompletionOverlay).hide()
            return
        completed = complete_command(event.text)
        composer = self.query_one(Composer)
        if completed is not None:
            composer.text = completed
            composer.move_cursor((0, len(completed)))
            return
        items = tuple(
            CompletionItem(CompletionMode.SLASH, item.command.value, item.command.value, item.description, item)
            for item in suggest_commands(event.text)
        )
        if has_slash_query(event.text):
            overlay = self.query_one(CompletionOverlay)
            if overlay.mode is not CompletionMode.SLASH:
                overlay.enter_mode(CompletionMode.SLASH)
            overlay.replace_items(items, empty_label="无匹配命令")

    def on_option_list_option_selected(self, event) -> None:
        if not isinstance(event.option_list, CompletionOverlay):
            return
        item = event.option_list.item_for_option(str(event.option.id))
        if item is None:
            return
        if item.mode is CompletionMode.WORKSPACE:
            query = self._workspace_completion.query
            if query is not None and item.payload is not None:
                composer = self.query_one(Composer)
                composer.accept_workspace(query, item.payload)
                self._workspace_completion.close()
                event.option_list.hide()
                composer.focus()
            return
        if item.mode is not CompletionMode.SLASH:
            return
        selected = item.identity
        composer = self.query_one(Composer)
        self._completion_suppressed_text = selected
        composer.text = selected
        composer.move_cursor((0, len(selected)))
        event.option_list.hide()
        composer.focus()

    def on_completion_overlay_dismissed(self, event: CompletionOverlay.Dismissed) -> None:
        if event.mode is CompletionMode.WORKSPACE:
            self._workspace_completion.close()

    async def on_message_widget_withdraw_requested(self, event) -> None:
        if self.current is not None:
            await self.current.withdraw(event.message_id)

    async def on_composer_submitted(self, event: Composer.Submitted) -> None:
        composer = self.query_one(Composer)
        ranges = tuple((item.start, item.end) for item in event.snapshot.references)
        query = find_workspace_query(
            event.snapshot.text,
            composer.cursor_offset(),
            ranges,
            parse_command(event.snapshot.text) is not None,
        )
        if query is not None and query != self._workspace_completion.query:
            self._workspace_completion.update(event.snapshot, query, self._workspace_index.snapshot())
        if self._workspace_completion.query is not None:
            candidate = await self._workspace_completion.resolve_for_accept()
            if candidate is not None and self._workspace_completion.query is not None:
                self.query_one(Composer).accept_workspace(self._workspace_completion.query, candidate)
                self._workspace_completion.close()
                self.query_one(CompletionOverlay).hide()
                return
        command = parse_command(event.text)
        if command is CommandName.QUIT:
            self.query_one(Composer).clear_accepted()
            await self.action_quit()
            return
        if command is CommandName.CLEAR:
            self.query_one(Composer).clear_accepted()
            await self.clear_session()
            return
        if command is CommandName.MODEL:
            self.query_one(Composer).clear_accepted()
            cached = None
            if self._cached_model_catalog is not None:
                cached = tuple(model.id for model in self._cached_model_catalog)
            modal = ModelPickerModal(cached, current=self._model_name())
            self.push_screen(modal, self._model_selected)
            return
        if command is CommandName.CONNECT:
            self.query_one(Composer).clear_accepted()
            self.push_screen(ProviderConnectModal(), self._connect_credentials_collected)
            return
        if command is CommandName.SESSION:
            self.query_one(Composer).clear_accepted()
            await self.open_session_picker()
            return
        if command is CommandName.STATUS:
            self.query_one(Composer).clear_accepted()
            self._open_status_modal()
            return
        try:
            user_message = await asyncio.to_thread(self._reference_service.build_user_message, event.snapshot)
            if self.current is None:
                async with self._transition_lock:
                    if self.current is None:
                        self.current, accepted = await RuntimeSession.start(
                            self.repository,
                            user_message,
                            loop_factory=self._loop_factory,
                            todo_store=self._todo_store,
                            trace_sink=self._trace_sink,
                            raise_ui_exceptions=self._raise_ui_exceptions,
                        )
                        self.current.subscribe(self._on_update)
                        self._accepted_runs[accepted.message_id] = (
                            self.current.session_id,
                            accepted.run_id,
                        )
                        # 首条 UserMessage 在订阅前已经提交，必须显式报告其 Run。
                        self._memory_runtime.record_session_created()
                        self._memory_runtime.record_user_activity()
                        self._memory_runtime.record_run_started(
                            self.current.session_id, accepted.run_id
                        )
                        dirty = self.projection.replace(await self.current.snapshot())
                        self._request_refresh(dirty, structural=True, immediate=True)
                        self._open_pending_permission()
            else:
                accepted = await self.current.submit(user_message)
                self._accepted_runs[accepted.message_id] = (
                    self.current.session_id,
                    accepted.run_id,
                )
                self._memory_runtime.record_user_activity()
            del accepted
            composer = self.query_one(Composer)
            if composer.reference_state.snapshot().revision == event.snapshot.revision:
                composer.clear_accepted()
            self._request_refresh(set(), structural=True, immediate=True)
        except Exception as exc:
            self.notify("无法提交消息，请重试", severity="error")
            await self._record_ui_error("submit", exc)
            # Composer 从未在发布 Submitted 时清空，因此失败可直接重试。

    def _apply_workspace_result(self, task: asyncio.Task) -> None:
        try:
            result = task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            overlay = self.query_one(CompletionOverlay)
            if overlay.mode is CompletionMode.WORKSPACE:
                overlay.replace_items((), empty_label="无匹配项")
            return
        if not self._workspace_completion.accept_result(result):
            return
        items = tuple(
            CompletionItem(
                CompletionMode.WORKSPACE,
                candidate.identity,
                candidate.display_path,
                "文件夹" if candidate.kind.value == "directory" else "文件",
                candidate,
            )
            for candidate in self._workspace_completion.candidates
        )
        overlay = self.query_one(CompletionOverlay)
        if overlay.mode is CompletionMode.WORKSPACE:
            overlay.replace_items(items, empty_label="无匹配项")

    async def on_composer_cancel_requested(self, event: Composer.CancelRequested) -> None:
        await self.action_cancel_or_quit()

    async def action_cancel_or_quit(self) -> None:
        """Ctrl+C 优先级（textual-ui.md §11）：permission > 清空输入 > 取消 Run > 双击退出。"""
        import time

        if self._permission_task is not None and self.current is not None:
            if self._cancel_current_once():
                self._last_ctrl_c = 0.0
                self.notify("正在停止当前运行…")
                return
        composer = self.query_one(Composer)
        if composer.text:
            composer.clear_accepted()
            return
        if self._cancel_current_once():
            self._last_ctrl_c = 0.0
            self.notify("正在停止当前运行…")
            return
        now = time.monotonic()
        if now - self._last_ctrl_c <= 1.5:
            await self.action_quit()
        else:
            self._last_ctrl_c = now
            self.notify("再次按 Ctrl+C 退出")

    def _cancel_current_once(self) -> bool:
        if (
            self.current is None
            or not self._run_active
            or getattr(self.current, "cancellation_requested", False)
        ):
            return False
        return self.current.cancel_active()

    def action_jump_to_latest(self) -> None:
        try:
            self.query_one(MessageViewport).jump_to_latest()
        except NoMatches:
            pass

    def action_toggle_details(self) -> None:
        self._details_expanded = not self._details_expanded
        try:
            self.query_one(MessageViewport).set_all_details_expanded(self._details_expanded)
        except NoMatches:
            pass

    async def action_quit(self) -> None:
        self._close_status_modal()
        async with self._transition_lock:
            if self.current is not None:
                await self.current.stop("APPLICATION_SHUTDOWN")
                self.current = None
        if self._refresh_scheduler is not None:
            self._refresh_scheduler.close()
        await self._close_memory_runtime()
        await self._trace_sink.close()
        self.exit()

    async def clear_session(self) -> None:
        async with self._transition_lock:
            self._close_status_modal()
            if self.current is not None:
                await self.current.stop("SESSION_CLEARED")
                self.current = None
            dirty = self.projection.clear()
            self._run_active = False
            self._last_ctrl_c = 0.0
            self._close_permission_modal()
            self._request_refresh(dirty, structural=True, immediate=True)

    async def open_session_picker(self) -> None:
        try:
            sessions = await self.repository.list_sessions()
            self.push_screen(SessionPickerModal(sessions), self._session_selected)
        except Exception as exc:
            self.notify("无法读取 Session", severity="error")
            await self._record_ui_error("session_list", exc)

    async def switch_session(self, session_id: str) -> None:
        async with self._transition_lock:
            target_id = UUID(session_id)
            if self.current is not None and self.current.session_id == target_id:
                return
            opened = await self.repository.open_session(target_id)
            replacement: RuntimeSession | None = None
            try:
                replacement = await RuntimeSession.open(
                    opened,
                    loop_factory=self._loop_factory,
                    todo_store=self._todo_store,
                    trace_sink=self._trace_sink,
                    raise_ui_exceptions=self._raise_ui_exceptions,
                )
                if self.current is not None:
                    await self.current.stop("SESSION_SWITCHED")
                self._close_status_modal()
                self.current = None
                replacement.subscribe(self._on_update)
                replacement.start_worker()
                snapshot = await replacement.snapshot()
                self.current = replacement
                replacement = None
                dirty = self.projection.replace(snapshot)
                self._run_active = False
                self._last_ctrl_c = 0.0
                self._close_permission_modal()
                self._request_refresh(dirty, structural=True, immediate=True)
                self._open_pending_permission()
            finally:
                if replacement is not None:
                    await replacement.stop("SWITCH_FAILED")

    async def _on_update(self, update: object) -> None:
        before = tuple(message.message_id for message in self.projection.messages)
        dirty = self.projection.apply(update)
        after = tuple(message.message_id for message in self.projection.messages)
        if isinstance(update, (UserMessageCommitted, AssistantMessageStarted)):
            self._run_active = True
            self._last_ctrl_c = 0.0
            if isinstance(update, UserMessageCommitted):
                identity = self._accepted_runs.get(update.message.message_id)
                if identity is not None:
                    self._memory_runtime.record_run_started(*identity)
        elif isinstance(update, RunTerminated):
            self._run_active = False
            self._last_ctrl_c = 0.0
            identity = next(
                (
                    value for value in self._accepted_runs.values()
                    if value[1] == update.run_id
                ),
                None,
            )
            if identity is not None:
                self._memory_runtime.record_run_stopped(*identity)
                self._accepted_runs = {
                    message_id: value
                    for message_id, value in self._accepted_runs.items()
                    if value != identity
                }
        immediate = isinstance(
            update,
            (AssistantMessageCompleted, AssistantMessageDiscarded, ToolResultCompleted, UserMessageCommitted, RunTerminated, TodosChanged, PermissionRequested),
        )
        self._request_refresh(dirty, structural=before != after or isinstance(update, RunTerminated), immediate=immediate)
        if isinstance(update, PermissionRequested) and self._permission_task is None:
            self._permission_task = asyncio.create_task(self._show_permission(update.request))
        elif isinstance(update, PermissionResolved):
            self._close_permission_modal()
        if isinstance(update, SessionUsageUpdated) and self._status_modal is not None:
            self._status_modal.update_usage(self.projection.usage)

    def _open_status_modal(self) -> None:
        self._close_status_modal()
        modal = StatusModal(StatusViewState(
            session_name=self.projection.title if self.current is not None else None,
            session_id=self.current.session_id if self.current is not None else None,
            workspace=str(self.workspace_root),
            base_url=self._provider_configuration.base_url if self._provider_configuration is not None else None,
            model=self._model_name(),
            usage=self.projection.usage if self.current is not None else SessionUsage.zero(),
        ))
        self._status_modal = modal
        self.push_screen(modal, self._status_modal_closed)

    def _status_modal_closed(self, _: None) -> None:
        self._status_modal = None
        try:
            self.query_one(Composer).focus()
        except NoMatches:
            pass

    def _close_status_modal(self) -> None:
        modal = self._status_modal
        self._status_modal = None
        if modal is not None and modal in self.screen_stack:
            modal.dismiss(None)

    def _refresh_view(self) -> None:
        self._request_refresh(
            {message.message_id for message in self.projection.messages},
            structural=True,
            immediate=True,
        )

    def _request_refresh(self, dirty: set[UUID], *, structural: bool = False, immediate: bool = False) -> None:
        if self._refresh_scheduler is None:
            return
        self._refresh_scheduler.request(dirty, structural=structural, immediate=immediate)

    def _flush_view(self, dirty: set[UUID], structural: bool) -> None:
        try:
            self.query_one(MessageViewport).sync_projection(self.projection, dirty, structural=structural)
            self._render_todos()
            self._update_run_state()
            self._update_status()
        except Exception as exc:
            asyncio.create_task(self._record_ui_error("refresh", exc))
            if self._raise_ui_exceptions:
                raise
            self.notify("界面刷新失败，请重新打开 Session", severity="error")

    def _derive_run_state(self) -> RunState | None:
        messages = self.projection.messages
        if self._run_active or any(message.lifecycle is MessageLifecycle.DRAFT for message in messages):
            return RunState("running")
        queued = sum(1 for message in messages if message.lifecycle is MessageLifecycle.QUEUED)
        if queued:
            return RunState("queued", queued)
        return None

    def _update_run_state(self) -> None:
        self.query_one(StatusBar).update_run_state(self._derive_run_state())

    def _update_status(self) -> None:
        self.query_one(StatusBar).update_status(str(self.workspace_root), self._model_name())

    def _model_name(self) -> str | None:
        return self.config.get().model

    async def _load_models(self) -> tuple[ModelInfo, ...]:
        catalog = self._model_catalog
        owned = None
        if catalog is None:
            if self._provider_configuration is None:
                raise RuntimeError("model catalog unavailable")
            owned = self._provider_adapter_factory(self._provider_configuration)
            catalog = owned
        try:
            return await catalog.list_models(Cancellation())
        finally:
            if owned is not None:
                close = getattr(owned, "close", None)
                if close is not None:
                    await close()

    def on_model_picker_modal_refresh_requested(self, event: ModelPickerModal.RefreshRequested) -> None:
        draft = self._connect_drafts.get(event.picker)
        if draft is not None:
            delivery = asyncio.create_task(self._refresh_connect_models(event.picker, draft))
            self._model_delivery_tasks.add(delivery)
            delivery.add_done_callback(self._model_delivery_tasks.discard)
            return
        if self._model_task is None or self._model_task.done():
            self._model_task = asyncio.create_task(self._refresh_model_catalog())
        delivery = asyncio.create_task(self._deliver_model_catalog(event.picker, self._model_task))
        self._model_delivery_tasks.add(delivery)
        delivery.add_done_callback(self._model_delivery_tasks.discard)

    async def _refresh_model_catalog(self) -> tuple[tuple[ModelInfo, ...] | None, str | None]:
        try:
            models = await self._load_models()
            self._cached_model_catalog = models
            return models, None
        except Exception as exc:
            await self._record_ui_error("model_list", exc)
            return None, "无法获取模型列表"

    async def _deliver_model_catalog(
        self,
        modal: ModelPickerModal,
        task: asyncio.Task[tuple[tuple[ModelInfo, ...] | None, str | None]],
    ) -> None:
        models, error = await asyncio.shield(task)
        if modal not in self.screen_stack:
            return
        if error is not None:
            modal.show_error(error)
        else:
            modal.show_models(model.id for model in models or ())

    async def _refresh_connect_models(
        self, modal: ModelPickerModal, draft: ProviderConfigurationDraft
    ) -> None:
        adapter = None
        try:
            probe = ProviderConfiguration("catalog-probe", draft.base_url, draft.api_key)
            adapter = self._provider_adapter_factory(probe)
            models = await adapter.list_models(Cancellation())
            if modal in self.screen_stack:
                modal.show_models(model.id for model in models)
        except Exception as exc:
            await self._record_ui_error("connect_model_list", exc)
            if modal in self.screen_stack:
                modal.show_error("无法获取模型列表")
        finally:
            close = getattr(adapter, "close", None)
            if close is not None:
                await close()

    def _default_loop_factory(self) -> object:
        if self._provider_configuration is None:
            return _UnavailableLoop()
        selected = self.config.get().model
        configuration = replace(self._provider_configuration, model=selected) if selected else self._provider_configuration
        tool_configuration = ExternalToolConfigLoader().load(os.environ, self._dotenv_path)
        return _ConfiguredLoop(
            configuration,
            tool_configuration,
            self._session_permission_requester,
            self._todo_store,
            self._read_file_state_store,
            self.workspace_root,
            self._memory_selection_hook,
            self._memory_runtime,
            self._trace_sink,
        )

    def _current_provider_configuration(self) -> ProviderConfiguration | None:
        configuration = self._provider_configuration
        if configuration is None:
            return None
        selected = self.config.get().model
        return replace(configuration, model=selected) if selected else configuration

    async def _close_memory_runtime(self) -> None:
        if self._memory_runtime_closed:
            return
        self._memory_runtime_closed = True
        await self._memory_runtime.close()

    async def _session_permission_requester(self, request: PermissionRequest, cancellation) -> PermissionDecision:
        if self.current is None:
            return PermissionDecision.DENY
        return await self.current.request_permission(request, cancellation)

    async def _show_permission(self, request: PermissionRequest) -> None:
        modal = PermissionModal(request)
        try:
            result = asyncio.get_running_loop().create_future()

            def dismissed(decision: PermissionDecision) -> None:
                if not result.done():
                    result.set_result(decision)

            self.push_screen(modal, dismissed)
            decision = await result
            if self.current is not None:
                self.current.resolve_permission(decision)
        except asyncio.CancelledError:
            if self.screen is modal:
                self.pop_screen()
            if self.current is not None:
                self.current.resolve_permission(PermissionDecision.DENY)
        finally:
            self._permission_task = None

    def _model_selected(self, selected: str | None) -> None:
        if not selected:
            return
        if self._provider_configuration is None:
            self.notify("尚未配置模型，请先使用 /connect", severity="warning")
            return
        try:
            draft = ProviderConfigurationDraft(
                self._provider_configuration.base_url,
                self._provider_configuration.api_key,
                selected,
            )
            configured = self._provider_store.commit(draft)
        except Exception as exc:
            self.notify("无法保存模型配置", severity="error")
            asyncio.create_task(self._record_ui_error("model_save", exc))
            return
        self._provider_configuration = configured.configuration
        self.config.set_model(selected)
        self._update_status()

    def _connect_credentials_collected(self, draft: ProviderConfigurationDraft | None) -> None:
        if draft is None:
            return
        modal = ModelPickerModal(None, current=None)
        self._connect_drafts[modal] = draft
        self.push_screen(
            modal,
            lambda selected, picker=modal: self._connect_model_selected(picker, selected),
        )

    def _connect_model_selected(self, picker: ModelPickerModal, selected: str | None) -> None:
        draft = self._connect_drafts.pop(picker, None)
        if draft is None or not selected:
            return
        adapter = None
        try:
            complete = draft.with_model(selected)
            candidate = ProviderConfiguration(selected, complete.base_url, complete.api_key)
            adapter = self._provider_adapter_factory(candidate)
            configured = self._provider_store.commit(complete)
        except Exception as exc:
            self.notify("无法保存模型连接", severity="error")
            asyncio.create_task(self._record_ui_error("connect_save", exc))
            close = getattr(adapter, "close", None)
            if close is not None:
                asyncio.create_task(close())
            return
        close = getattr(adapter, "close", None)
        if close is not None:
            asyncio.create_task(close())
        self._provider_configuration = configured.configuration
        self.config.set_model(configured.configuration.model)
        self._update_status()
        self.notify("模型连接已更新")

    def _session_selected(self, selected: str | None) -> None:
        if selected:
            asyncio.create_task(self.switch_session(selected))

    def _open_pending_permission(self) -> None:
        request = self.projection.pending_permission
        if request is not None and self._permission_task is None:
            self._permission_task = asyncio.create_task(self._show_permission(request))

    def _close_permission_modal(self) -> None:
        if self._permission_task is not None:
            self._permission_task.cancel()
            self._permission_task = None

    def _render_todos(self) -> None:
        widget = self.query_one("#todo-list", Static)
        widget.display = bool(self.projection.todo_list)
        if self.projection.todo_list:
            widget.update(render_todo_list(self.projection.todo_list))

    async def _record_ui_error(self, operation: str, error: BaseException) -> None:
        zero = UUID(int=0)
        context = TraceContext(uuid4(), uuid4(), None, self.current.session_id if self.current else zero, zero)
        safe = sanitize_error(error)
        safe["message"] = ""
        payload = {"name": "ui_error", "operation": operation, "error": safe}
        try:
            await self._trace_sink.emit(TraceEvent(TraceEventType.SPAN_FINISHED, context, payload))
        except Exception:
            pass
