"""OneCode TUI 轻量级 Textual 应用程序。

该 App 仅负责展现层的编排连接：组合会话视图、状态栏、输入框与补全浮层；
订阅 :class:`~application.session.SessionController`；转发用户意图；并展示交互弹窗面板。
它不执行 Agent 循环、不消费私有队列、不收集附件，也不重新绑定运行时。
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any, Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Static

from application.commands import CommandOutcome, parse_invocation
from application.session import SessionController
from application.sessions import list_session_summaries
from application.types import (
    InteractionRequest,
    InteractionRequested,
    InteractionResolved,
    RunFailed,
    SnapshotUpdate,
    ToolUpdate,
)
from services.model.types import ProviderError
from services.permissions import PermissionResponse
from services.questions.types import AnswerRecord, QuestionResponse
from ui.tui.command_views import command_view
from ui.tui.completion import CompletionItem, CompletionOverlay
from ui.tui.composer import Composer
from ui.tui.conversation import ConversationView, DetailRequested
from ui.tui.modals import (
    CommandOutputModal,
    CredentialModal,
    McpTrustModal,
    ModelPickerModal,
    PermissionModal,
    PlanApprovalModal,
    ProviderPickerModal,
    QuestionModal,
    SessionPickerModal,
)
from ui.tui.projection import ConversationProjection
from ui.tui.renderers.status import RunState
from ui.tui.status_bar import StatusBar
from ui.tui.theme import apply_theme

RuntimeFactory = Callable[..., Any]


def default_runtime_factory(workspace: Path, *, trust_prompt: Callable | None = None):
    from application.runtime import build_runtime, build_unconfigured_runtime

    try:
        return build_runtime(
            workspace, trust_prompt=trust_prompt, mcp_trust_mode="prompt"
        )
    except ProviderError:
        return build_unconfigured_runtime(workspace)


class OneCodeTuiApp(App[None]):
    TITLE = "OneCode"
    CSS_PATH = "onecode.tcss"
    BINDINGS = [
        Binding("ctrl+c", "cancel_or_clear", "取消/清空", show=False),
        Binding("end,ctrl+end", "jump_to_latest", "最新消息", show=False),
        Binding("ctrl+o", "toggle_details", "展开/折叠详情", show=False),
        Binding("f8", "withdraw_last", "撤回队列", show=False),
        Binding("f9", "resume_queue", "继续队列", show=False),
    ]

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        controller: SessionController | None = None,
        runtime_factory: RuntimeFactory | None = None,
        trust_prompt: Callable[[Any], str] | None = None,
        model_fetcher: Callable[..., Any] | None = None,
        env_writer: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__()
        self.workspace = (workspace or Path.cwd()).resolve()
        self._controller = controller
        self._runtime_factory = runtime_factory or default_runtime_factory
        self._trust_prompt = trust_prompt
        self._model_fetcher = model_fetcher or _default_model_fetcher
        self._env_writer = env_writer or _default_env_writer
        self.projection = ConversationProjection()
        self._watch_task: asyncio.Task[None] | None = None
        self._startup_task: asyncio.Task[None] | None = None
        self._panel_queue: deque[Any] = deque()
        self._panel_task: asyncio.Task[None] | None = None
        self._current_modal: Any = None
        self._completion_task: asyncio.Task[None] | None = None
        self._completion_generation = 0
        self._details_expanded = False
        self._event_loop: asyncio.AbstractEventLoop | None = None

    # --- 界面组件编排 -----------------------------------------------------

    def compose(self) -> ComposeResult:
        with Container(id="chat-wrap"):
            yield ConversationView(id="conversation-view")
        yield StatusBar(id="status-bar")
        with Container(id="composer-wrap"):
            yield CompletionOverlay(id="completion-overlay")
            yield Composer(id="composer")
            yield Static(
                "输入消息，/ 打开命令；Ctrl+Enter 换行（部分终端用 Ctrl+J）"
                "；F8 撤回队列，F9 继续队列；Ctrl+O 展开详情",
                id="composer-hint",
            )

    async def on_mount(self) -> None:
        apply_theme(self)
        self._event_loop = asyncio.get_running_loop()
        self._render_hint()
        self.query_one(Composer).focus()
        self._update_status()
        if self._controller is not None:
            self._watch_task = asyncio.create_task(self._watch())
            await self._start_controller()
        else:
            self._startup_task = asyncio.create_task(self._startup())

    async def on_unmount(self) -> None:
        for task in (
            self._startup_task,
            self._watch_task,
            self._panel_task,
            self._completion_task,
        ):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        view = self.query_one_optional(ConversationView)
        if view is not None:
            view.close()
        if self._controller is not None and not self._controller.closed:
            await self._controller.close()

    # --- 启动流程 ---------------------------------------------------------

    async def _startup(self) -> None:
        try:
            runtime = await asyncio.to_thread(
                self._runtime_factory,
                self.workspace,
                trust_prompt=self._trust_prompt or self._modal_trust_prompt,
            )
        except Exception as exc:
            self.notify(f"启动失败: {exc}", severity="error")
            return
        self._controller = SessionController(runtime)
        self._watch_task = asyncio.create_task(self._watch())
        await self._start_controller()

    async def _start_controller(self) -> None:
        assert self._controller is not None
        await self._controller.start()
        self._update_status()

    def _modal_trust_prompt(self, request: Any) -> str:
        """同步信任确认回调，可安全地从运行时线程中调用。"""

        loop = self._event_loop
        if loop is None:
            return "skip"
        future = asyncio.run_coroutine_threadsafe(
            self._ask_trust(request), loop
        )
        try:
            return future.result()
        except Exception:
            return "skip"

    async def _ask_trust(self, request: Any) -> str:
        result = await self._show_modal(McpTrustModal(request))
        return result or "skip"

    async def _show_modal(self, modal: Any) -> Any:
        """在任意异步上下文中弹出模态对话框并等待其返回结果。

        Textual 仅允许在活跃的 worker 内部调用 ``push_screen_wait``，
        因此本方法将普通协程任务（或运行时信任提示线程）桥接转换为 worker 执行。
        """

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._current_modal = modal

        async def run() -> None:
            try:
                result = await self.push_screen_wait(modal)
            except Exception as exc:  # pragma: no cover - defensive
                if not future.done():
                    future.set_exception(exc)
                return
            finally:
                if self._current_modal is modal:
                    self._current_modal = None
            if not future.done():
                future.set_result(result)

        self.run_worker(run(), group="modal", exit_on_error=False)
        return await future

    def _dismiss_current_modal(self) -> None:
        modal = self._current_modal
        if modal is None:
            return
        self._current_modal = None
        if modal in self.screen_stack:
            self.pop_screen()

    # --- 状态监听 ---------------------------------------------------------

    async def _watch(self) -> None:
        assert self._controller is not None
        try:
            async for update in self._controller.watch():
                if isinstance(update, SnapshotUpdate):
                    change = self.projection.replace(update.snapshot)
                elif self.projection.resync_required:
                    change = self.projection.replace(self._controller.snapshot())
                else:
                    change = self.projection.apply(update)
                self._apply_change(change)
                self._handle_update(update)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self.notify(f"会话更新失败: {exc}", severity="error")

    def _apply_change(self, change: Any) -> None:
        view = self.query_one_optional(ConversationView)
        if view is not None:
            view.update(self.projection, change)
        self._update_status()

    def _handle_update(self, update: Any) -> None:
        if isinstance(update, InteractionRequested):
            self._handle_interaction_request(update.request)
        elif isinstance(update, InteractionResolved):
            self._dismiss_current_modal()
        elif isinstance(update, ToolUpdate):
            self._maybe_plan_approval(update)
        elif isinstance(update, RunFailed):
            self.notify(update.error or "运行失败", severity="error")

    # --- 交互面板队列 -----------------------------------------------------

    def _enqueue_panel(self, run_panel: Callable[[], Any]) -> None:
        self._panel_queue.append(run_panel)
        if self._panel_task is None or self._panel_task.done():
            self._panel_task = asyncio.create_task(self._drain_panels())

    async def _drain_panels(self) -> None:
        try:
            while self._panel_queue:
                run_panel = self._panel_queue.popleft()
                try:
                    await run_panel()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - defensive
                    await self._record_error("interaction", exc)
        finally:
            self._panel_task = None

    # --- 交互处理 ---------------------------------------------------------

    def _maybe_plan_approval(self, update: ToolUpdate) -> None:
        if update.tool_name != "exit_plan_mode" or update.status != "completed":
            return
        metadata = update.result.metadata if update.result is not None else {}
        if not metadata.get("awaiting_approval"):
            return
        self._enqueue_panel(
            lambda metadata=metadata, result=update.result: self._run_plan_approval(
                metadata, result
            )
        )

    async def _run_plan_approval(self, metadata: Any, result: Any) -> None:
        assert self._controller is not None
        path = str(metadata.get("plan_path", ""))
        content = self._read_plan_content()
        summary = _result_summary(result)
        choice = await self._show_modal(
            PlanApprovalModal(path=path, content=content, summary=summary)
        )
        if choice == "approve":
            await self._controller.execute_command("/plan approve")
        elif choice == "reject":
            await self._controller.execute_command("/plan reject")

    def _read_plan_content(self) -> str:
        if self._controller is None:
            return ""
        plan_store = getattr(self._controller.runtime, "plan_store", None)
        state = getattr(self._controller.runtime, "state", None)
        if plan_store is None or state is None:
            return ""
        try:
            if getattr(state.plan, "plan_slug", None) is None:
                return ""
            plan_file = plan_store.read_plan(state)
            return plan_file.read() if plan_file.exists() else ""
        except Exception:
            return ""

    def _handle_interaction_request(self, request: InteractionRequest) -> None:
        self._enqueue_panel(
            lambda request=request: self._resolve_interaction(request)
        )

    async def _resolve_interaction(self, request: InteractionRequest) -> None:
        controller = self._controller
        if controller is None:
            return
        kind = request.kind
        if kind == "permission":
            response = await self._show_modal(PermissionModal(request.payload))
            await controller.respond(
                request.request_id,
                kind,
                response or PermissionResponse(action="deny"),
            )
        elif kind == "question":
            response = await self._ask_questions(request.payload)
            await controller.respond(request.request_id, kind, response)
        elif kind == "mcp_trust":
            choice = await self._show_modal(McpTrustModal(request.payload))
            await controller.respond(request.request_id, kind, choice or "skip")
        elif kind == "plan_approval":
            choice = await self._show_modal(
                PlanApprovalModal(
                    path=_plan_path(request.payload),
                    content=_plan_text(request.payload),
                    summary=_plan_summary(request.payload),
                )
            )
            if choice == "approve":
                await controller.respond(request.request_id, kind, "approve")
            elif choice == "reject":
                await controller.respond(request.request_id, kind, "reject")
            else:
                await controller.respond(
                    request.request_id, kind, {"cancelled": True}
                )
        else:
            await controller.respond(request.request_id, kind, None)

    async def _ask_questions(self, questions: Any) -> QuestionResponse:
        answers: list[AnswerRecord] = []
        for question in questions or ():
            selection = await self._show_modal(QuestionModal(question))
            if selection is None:
                return QuestionResponse(declined=True, feedback="cancelled")
            answer: Any = (
                tuple(selection) if question.multi_select else selection[0]
            )
            answers.append(AnswerRecord(question=question.question, answer=answer))
        return QuestionResponse(answers=tuple(answers))

    # --- 输入框事件 -------------------------------------------------------

    def on_composer_submitted(self, event: Composer.Submitted) -> None:
        asyncio.create_task(self._submit_text(event.text))

    async def _submit_text(self, text: str) -> None:
        controller = self._controller
        composer = self.query_one_optional(Composer)
        if controller is None:
            self.notify("正在初始化，请稍候…", severity="warning")
            return
        invocation = parse_invocation(text)
        try:
            if invocation is None:
                receipt = await controller.submit(text)
                if receipt.status == "rejected":
                    self.notify(
                        _reject_reason(receipt.reason), severity="warning"
                    )
                    return
                if composer is not None and composer.text == text:
                    composer.clear_text()
            else:
                outcome = await controller.execute_command(text)
                if composer is not None and composer.text == text:
                    composer.clear_text()
                await self._handle_command_outcome(outcome)
        except Exception as exc:
            self.notify("执行失败，请重试", severity="error")
            await self._record_error("submit", exc)

    async def _handle_command_outcome(self, outcome: CommandOutcome) -> None:
        action = outcome.action
        if action == "exit":
            self.exit()
            return
        if action == "resume_selector":
            await self._open_session_picker()
            return
        if action == "connect":
            await self._run_connect_flow()
            return
        view = command_view(outcome)
        if view is not None:
            title, renderable = view
            await self._show_modal(CommandOutputModal(title, renderable))
        elif outcome.error:
            self.notify(outcome.error, severity="error")

    def on_composer_cancel_requested(self, event: Composer.CancelRequested) -> None:
        asyncio.create_task(self._cancel_or_clear())

    async def action_cancel_or_clear(self) -> None:
        await self._cancel_or_clear()

    async def _cancel_or_clear(self) -> None:
        if self._controller is None:
            return
        if self.projection.run.active:
            self.notify("正在停止当前运行…")
            await self._controller.cancel_active()
            return
        # 没有活跃的运行：Ctrl+C 绝不退出程序，也绝不静默删除草稿。
        # 清空输入是显式的用户操作，而非快捷键的附带副作用。
        self.notify("没有正在运行的请求")

    def action_withdraw_last(self) -> None:
        asyncio.create_task(self._withdraw_last())

    async def _withdraw_last(self) -> None:
        if self._controller is None:
            return
        queue = self.projection.queue
        if not queue:
            self.notify("队列为空")
            return
        item = queue[-1]
        result = await self._controller.withdraw(item.input_id)
        if not result.withdrawn:
            self.notify(f"无法撤回: {result.reason}", severity="warning")
            return
        composer = self.query_one_optional(Composer)
        if composer is not None and not composer.text:
            composer.set_text(result.text, cursor=len(result.text))
            self.notify("已撤回到输入框")
        else:
            # 绝不覆盖已存在的草稿；保留已撤回的文本可见，以便用户自行复制。
            self.notify(f"已撤回（草稿非空，未覆盖）：{result.text}")

    def action_resume_queue(self) -> None:
        asyncio.create_task(self._resume_queue())

    async def _resume_queue(self) -> None:
        if self._controller is None:
            return
        if not self.projection.paused:
            self.notify("队列未暂停")
            return
        await self._controller.resume_queue()
        self.notify("继续执行队列")

    def action_jump_to_latest(self) -> None:
        view = self.query_one_optional(ConversationView)
        if view is not None:
            view.viewport.jump_to_latest()

    def action_toggle_details(self) -> None:
        self._details_expanded = not self._details_expanded
        view = self.query_one_optional(ConversationView)
        if view is not None:
            view.viewport.set_all_details_expanded(self._details_expanded)
            view.refresh_now()

    def on_detail_requested(self, event: DetailRequested) -> None:
        if self._controller is None or event.detail_ref is None:
            return
        asyncio.create_task(self._controller.load_detail(event.detail_ref))

    # --- 自动补全 ---------------------------------------------------------

    def on_text_area_changed(self, event) -> None:
        composer = event.text_area
        if composer.id != "composer":
            return
        overlay = self.query_one_optional(CompletionOverlay)
        hint = self.query_one_optional("#composer-hint", Static)
        if hint is not None:
            hint.display = not composer.text
        if overlay is None:
            return
        text = composer.text
        if text.startswith("/") and " " not in text:
            self._show_command_completion(overlay, text)
            return
        if self._has_at_query(text, composer.cursor_offset()):
            self._schedule_file_completion(overlay, text, composer.cursor_offset())
            return
        overlay.exit_mode()

    def _show_command_completion(
        self, overlay: CompletionOverlay, text: str
    ) -> None:
        from application.commands import command_registry

        prefix = text.lower()
        items: list[CompletionItem] = []
        for spec in command_registry():
            displays = (spec.display_name, *(
                f"/{alias}" for alias in spec.aliases
            ))
            for display in displays:
                if not display.startswith(prefix):
                    continue
                description = spec.description
                if spec.argument_hint:
                    description = f"{description} {spec.argument_hint}"
                items.append(
                    CompletionItem(
                        mode="command",
                        identity=display,
                        primary=display,
                        description=description,
                        replacement=display,
                    )
                )
        if overlay.mode != "command":
            overlay.enter_mode("command")
        overlay.replace_items(tuple(items), empty_label="无匹配命令")

    def _has_at_query(self, text: str, cursor: int) -> bool:
        before = text[:cursor]
        index = before.rfind("@")
        if index < 0:
            return False
        if index > 0 and not before[index - 1].isspace():
            return False
        return True

    def _schedule_file_completion(
        self, overlay: CompletionOverlay, text: str, cursor: int
    ) -> None:
        self._completion_generation += 1
        generation = self._completion_generation
        if overlay.mode != "file":
            overlay.enter_mode("file")
        overlay.set_pending()
        self._completion_task = asyncio.create_task(
            self._compute_file_completion(text, cursor, generation)
        )

    async def _compute_file_completion(
        self, text: str, cursor: int, generation: int
    ) -> None:
        try:
            items = await asyncio.to_thread(
                _file_completion_items, self.workspace, text, cursor
            )
        except Exception:
            items = ()
        if generation != self._completion_generation:
            return
        overlay = self.query_one_optional(CompletionOverlay)
        if overlay is None or overlay.mode != "file":
            return
        overlay.replace_items(tuple(items), empty_label="无匹配项")

    def on_option_list_option_selected(self, event) -> None:
        overlay = self.query_one_optional(CompletionOverlay)
        if overlay is None or event.option_list is not overlay:
            return
        item = overlay.item_for_option(str(event.option.id))
        if item is None:
            return
        composer = self.query_one_optional(Composer)
        if composer is None:
            return
        if item.mode == "command":
            composer.set_text(item.replacement, cursor=len(item.replacement))
        else:
            _apply_file_completion(composer, item.replacement)
        overlay.exit_mode()
        composer.focus()

    def on_completion_overlay_dismissed(
        self, event: CompletionOverlay.Dismissed
    ) -> None:
        self._completion_generation += 1

    def on_composer_completion_requested(
        self, event: Composer.CompletionRequested
    ) -> None:
        overlay = self.query_one_optional(CompletionOverlay)
        if overlay is None:
            return
        if overlay.mode == "command" and overlay.highlighted is not None:
            overlay.action_select()
            return
        composer = self.query_one_optional(Composer)
        if composer is None:
            return
        from application.commands import command_registry

        text = event.text
        if text.startswith("/") and " " not in text:
            prefix = text.lower()
            matches = [
                display
                for spec in command_registry()
                for display in (spec.display_name, *(f"/{a}" for a in spec.aliases))
                if display.startswith(prefix)
            ]
            if len(matches) == 1:
                composer.set_text(matches[0], cursor=len(matches[0]))
            return
        self._schedule_file_completion(overlay, text, event.cursor_offset)

    # --- 会话选择器与连接流程 ---------------------------------------------

    async def _open_session_picker(self) -> None:
        try:
            sessions = await asyncio.to_thread(
                list_session_summaries, self.workspace
            )
        except Exception as exc:
            self.notify("无法读取会话列表", severity="error")
            await self._record_error("session_list", exc)
            return
        selected = await self._show_modal(SessionPickerModal(sessions))
        if not selected or self._controller is None:
            return
        outcome = await self._controller.execute_command(f"/resume {selected}")
        if outcome.error:
            self.notify(outcome.error, severity="error")

    async def _run_connect_flow(self) -> None:
        from ui.cli.connect import (
            ProviderEnvUpdate,
            existing_key_for_provider,
            list_connect_options,
        )
        from infrastructure.providers.catalog import get_provider_definition

        options = list_connect_options()
        if not options:
            self.notify("没有可用的供应商。", severity="warning")
            return
        choice = await self._show_modal(ProviderPickerModal(options))
        if choice is None:
            return
        provider = get_provider_definition(choice.provider_id)
        existing = existing_key_for_provider(self.workspace / ".env", provider.id)
        credentials = await self._show_modal(
            CredentialModal(provider, existing_key=existing)
        )
        if credentials is None:
            return
        models: tuple[str, ...] = ()
        try:
            fetched = await asyncio.to_thread(
                self._model_fetcher,
                provider,
                credentials.api_key,
                credentials.base_url,
            )
            models = tuple(model.id for model in fetched)
        except Exception:
            models = ()
        model = await self._show_modal(ModelPickerModal(models))
        if not model:
            return
        try:
            await asyncio.to_thread(
                self._env_writer,
                self.workspace / ".env",
                ProviderEnvUpdate(
                    provider_id=provider.id,
                    model=model,
                    api_key=credentials.api_key,
                    base_url=credentials.base_url,
                ),
            )
        except Exception as exc:
            self.notify("无法保存模型配置", severity="error")
            await self._record_error("connect_save", exc)
            return
        if self._controller is not None:
            ok = await self._controller.reload_model_config()
            if ok:
                self.notify("模型连接已更新")
                self._update_status()
            else:
                self.notify("配置已保存，但模型重载失败", severity="warning")

    # --- 状态同步 ---------------------------------------------------------

    def _update_status(self) -> None:
        bar = self.query_one_optional(StatusBar)
        if bar is None:
            return
        model = None
        if self._controller is not None:
            model = getattr(self._controller.runtime, "model", None) or None
        bar.update_status(str(self.workspace), model)
        bar.update_run_state(self._derive_run_state())

    def _derive_run_state(self) -> RunState | None:
        projection = self.projection
        if projection.run.active:
            return RunState("running")
        queued = len(projection.queue)
        if queued:
            return RunState("queued", queued)
        if projection.status in {"failed", "error", "cleanup_failed"}:
            return RunState("error")
        return None

    def _render_hint(self) -> None:
        hint = self.query_one_optional("#composer-hint", Static)
        if hint is not None:
            hint.display = True

    async def _record_error(self, operation: str, error: BaseException) -> None:
        if self._controller is None:
            return
        recorder = getattr(
            self._controller.runtime, "error_log_recorder", None
        )
        if recorder is None:
            return
        try:
            recorder.record_error(error, source=f"tui_{operation}")
        except Exception:
            pass


# --- 辅助函数 ---------------------------------------------------------


def _reject_reason(reason: str) -> str:
    return {
        "not_configured": "尚未配置模型，请使用 /connect。",
        "empty": "请输入内容。",
        "closed": "会话已关闭。",
    }.get(reason, f"提交被拒绝: {reason}")


def _result_summary(result: Any) -> str:
    if result is None:
        return ""
    try:
        payload = json.loads(result.content)
    except (TypeError, ValueError):
        return ""
    if isinstance(payload, dict):
        return str(payload.get("summary", ""))
    return ""


def _payload_field(payload: Any, *names: str, default: Any = "") -> Any:
    if isinstance(payload, dict):
        for name in names:
            value = payload.get(name)
            if value:
                return value
    for name in names:
        value = getattr(payload, name, None)
        if value:
            return value
    return default


def _plan_path(payload: Any) -> str:
    return str(_payload_field(payload, "plan_path", "path", "plan_slug"))


def _plan_text(payload: Any) -> str:
    return str(_payload_field(payload, "plan_excerpt", "content", "plan"))


def _plan_summary(payload: Any) -> str:
    return str(_payload_field(payload, "summary"))


def _file_completion_items(workspace: Path, text: str, cursor: int):
    from ui.cli.suggestions import suggestions_for

    shim = _WorkspaceShim(workspace)
    return [
        CompletionItem(
            mode="file",
            identity=item.id,
            primary=item.display,
            description=item.description,
            replacement=item.replacement,
        )
        for item in suggestions_for(shim, text, cursor)
        if item.kind in {"file", "directory"}
    ]


class _WorkspaceShim:
    """仅向建议查询暴露 ``workspace`` 属性的极简适配对象。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace


def _apply_file_completion(composer: Composer, replacement: str) -> None:
    text = composer.text
    cursor = composer.cursor_offset()
    before = text[:cursor]
    index = before.rfind("@")
    if index < 0:
        return
    new_text = text[: index + 1] + replacement + text[cursor:]
    composer.set_text(new_text, cursor=index + 1 + len(replacement))


def _default_model_fetcher(provider, api_key, base_url):
    from infrastructure.providers.model_catalog import fetch_models_for_connect

    return fetch_models_for_connect(provider, api_key, base_url)


def _default_env_writer(env_path, update):
    from ui.cli.connect import write_provider_env

    return write_provider_env(env_path, update)


def run_tui(workspace: Path | None = None) -> int:
    OneCodeTuiApp(workspace=workspace).run()
    return 0


__all__ = ["OneCodeTuiApp", "default_runtime_factory", "run_tui"]
