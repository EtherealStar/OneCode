"""行内 REPL：OneCode CLI 的 TTY 终端入口。

InlineRepl 将静态区域与动态区域连接在一起并驱动主循环。
它的设计保持小巧精炼：每个可见行为均存在于专有模块中（静态输出、提示词输入、流式传输、瞬态页面），
该类仅充当统筹协调者。

循环结构：

    while not done:
        submission = prompt.read()
        if submission.kind == CANCEL/EXIT:
            shutdown(); break
        向静态区域回显用户输入的行
        if line 以 "/" 开头:
            result = dispatch_command(...)
            handle_command_result(result)
        else:
            await run_agent_turn(line)
            按 FIFO 顺序排空排队输入，每项通过 _handle_command（斜杠命令）或 _run_turn（提示词）处理

一个“轮次”表示 agent 循环的一次完整执行，包含所有工具调用和排队的后续操作。
InputQueue 与 StreamingSession 共享，以便用户在 agent 轮次运行时继续键入；
运行中轮次输入框将新的提交推入同一队列，REPL 在轮次结束后将其排空。
"""

from __future__ import annotations

import asyncio
import shutil

from rich.console import Console
from rich.text import Text

from services.plans import build_plan_attachments_for_state
from ui.cli import renderer
from ui.cli.commands import dispatch_command
from ui.cli.resume import list_session_summaries, restore_runtime_from_target
from ui.cli.terminal.connect_flow import run_connect_flow
from ui.cli.terminal.detect import detect_terminal_brightness
from ui.cli.terminal.interaction_host import TerminalInteractionHost
from ui.cli.terminal.page import TransientPage
from ui.cli.terminal.permission_prompt import TtyPermissionPrompter
from ui.cli.terminal.prompt_session import (
    PromptSession,
    SubmissionKind,
)
from ui.cli.terminal.queue import InputQueue
from ui.cli.terminal.selector import SelectorItem, TransientSelector
from ui.cli.terminal.static_output import print_user_submitted
from ui.cli.terminal.stream_session import StreamingSession
from ui.cli.terminal.transcript_replay import replay_messages_to_static
from ui.cli.theme import rich_theme_for
from ui.cli.types import CliRuntime, CommandResult


class InlineRepl:
    """基于 prompt_toolkit 和 Rich 实现的 TTY CLI 主循环。"""

    def __init__(
        self,
        runtime: CliRuntime,
        *,
        permission_prompter: TtyPermissionPrompter | None = None,
        interaction_host: TerminalInteractionHost | None = None,
    ) -> None:
        self._runtime = runtime
        self._interaction_host = interaction_host or TerminalInteractionHost()
        self._brightness = detect_terminal_brightness()
        self._queue = InputQueue()
        self._prompt = PromptSession(runtime, self._queue)
        self._agent_running = False
        self._cancel_requested = False
        self._exiting = False
        self._shutdown_done = False
        self._pending_attachments: list[dict[str, object]] = []
        self._permission_prompter = permission_prompter or TtyPermissionPrompter(
            self._interaction_host
        )
        # 使用感知终端亮度的色彩主题，使前景色在浅色或深色宿主环境中保持清晰可读。
        # 仅用于静态区域，主题从不设置背景色。
        self._console = Console(theme=rich_theme_for(self._brightness))

    # --- public entry -----------------------------------------------------

    def run(self) -> int:
        """同步入口点。返回进程退出代码。"""

        try:
            asyncio.run(self._main_loop())
        except KeyboardInterrupt:
            self._shutdown()
            return 0
        return 0

    # --- main loop --------------------------------------------------------

    async def _main_loop(self) -> None:
        # 打印一次静态横幅。renderer.render_banner 与主题无关，
        # 因此通过刚创建的感知亮度的控制台进行渲染。
        self._console.print(renderer.render_banner(self._runtime))
        self._print_untrusted_mcp_notices(self._runtime)
        if not self._runtime.configured:
            self._console.print(
                Text(
                    "⚠ 尚未配置供应商。请输入 /connect 进行配置。",
                    style="onecode.warning",
                )
            )
        self._agent_running = False
        while True:
            submission = await self._prompt.read()
            if submission.kind is SubmissionKind.EXIT:
                self._shutdown()
                return
            if submission.kind is SubmissionKind.CANCEL:
                # 在空提示符上按 Ctrl-C：清空并继续运行。
                if self._agent_running:
                    self._cancel_requested = True
                continue
            # 普通提交。text 为字面缓冲区内容（或按 Enter 采纳高亮补全时的 replacement 文本）。
            text = submission.text.strip()
            if not text:
                continue
            print_user_submitted(text, brightness=self._brightness)
            if text.startswith("/"):
                # 在未配置模式下，仅允许 /connect 和 /exit 命令。
                if not self._runtime.configured:
                    cmd_name = text.split()[0][1:].lower()
                    if cmd_name not in {"connect", "exit"}:
                        self._console.print(
                            Text(
                                "尚未配置供应商。请先使用 /connect 配置 API 供应商。",
                                style="onecode.warning",
                            )
                        )
                        continue
                await self._handle_command(text)
                if self._exiting:
                    return
                # 某些命令（如 /clear）会变更 runtime；
                # _handle_command 已处理提示词会话重置，因此直接继续循环。
                continue
            # 未配置模式下，阻止所有非命令输入。
            if not self._runtime.configured:
                self._console.print(
                    Text(
                        "尚未配置供应商。请先使用 /connect 配置 API 供应商。",
                        style="onecode.warning",
                    )
                )
                continue
            await self._run_turn(text)
            # 按 FIFO 顺序排空排队输入。轮次处于活跃状态时，
            # 运行中输入框推入了若干条目。
            # 斜杠命令路由至命令分发器；普通提示词交回给 _run_turn。
            await self._drain_queue()

    # --- command dispatch -------------------------------------------------

    async def _handle_command(self, line: str) -> None:
        result = dispatch_command(self._runtime, line)
        if result.interaction == "resume_selector":
            result = await self._run_resume_selector()
        elif result.interaction == "connect":
            result = await self._run_connect_flow()
        if result.runtime is not None:
            self._runtime = result.runtime
            self._reset_prompt_session()
        if result.reset_main_view:
            self._reset_main_view(result.renderable)
            return
        if result.renderable is not None:
            if result.presentation == "page":
                await self._show_page(result.renderable)
            else:
                self._console.print(result.renderable)
        # 在打印行内通知之后，将恢复的历史记录重放至主回滚历史。
        # 此操作在恢复选择器退出备用屏幕后、读取下一个提示词之前执行，
        # 确保历史消息输出到主缓冲区中。
        if result.replay_messages:
            replay_messages_to_static(
                result.replay_messages,
                brightness=self._brightness,
                workspace=self._runtime.workspace,
            )
        if result.attachments:
            self._pending_attachments.extend(result.attachments)
        if result.should_exit:
            self._shutdown()
            self._exiting = True
            return
        if result.queued_prompt:
            if not self._runtime.configured:
                self._console.print(
                    Text(
                        "尚未配置供应商。请先使用 /connect 配置 API 供应商。",
                        style="onecode.warning",
                    )
                )
                return
            await self._run_turn(result.queued_prompt)
            await self._drain_queue()

    async def _run_resume_selector(self) -> CommandResult:
        summaries = list_session_summaries(self._runtime.workspace)
        if not summaries:
            return CommandResult(
                renderable=renderer.render_session_summaries(
                    summaries, self._runtime.workspace
                ),
                presentation="page",
            )

        def detail(summary: object) -> str:
            updated = getattr(summary, "updated_at", None)
            date = updated.strftime("%Y-%m-%d") if updated else ""
            count = getattr(summary, "message_count", 0)
            return f"{date}  {count} messages".strip()

        items = tuple(
            SelectorItem(
                label=getattr(summary, "title", summary.session_id),
                value=summary,
                detail=detail(summary),
            )
            for summary in summaries
        )
        selector: TransientSelector = TransientSelector("Resume", items)
        chosen = await selector.run()
        if chosen is None:
            return CommandResult()
        assert chosen.value is not None
        try:
            resumed = restore_runtime_from_target(
                self._runtime, chosen.value.session_id
            )
        except Exception as exc:  # noqa: BLE001
            return CommandResult(renderable=renderer.render_error(str(exc)))
        return CommandResult(
            runtime=resumed,
            renderable=renderer.render_resume(
                resumed.state.session_id,
                resumed.message_store.transcript_store.messages_path,
                resumed.workspace,
            ),
            presentation="inline",
            replay_messages=resumed.message_store.current_messages(),
        )

    async def _run_connect_flow(self) -> CommandResult:
        was_configured = self._runtime.configured
        result = await run_connect_flow(self._runtime)
        if result.cancelled or result.runtime is None:
            return CommandResult(renderable=result.renderable)

        runtime = result.runtime
        if not was_configured:
            from ui.cli.app import build_runtime
            from ui.cli.terminal.trust_prompt import default_trust_prompt

            try:
                runtime = build_runtime(
                    self._runtime.workspace,
                    trust_prompt=default_trust_prompt,
                    permission_prompter=self._permission_prompter,
                    mcp_trust_mode="prompt",
                )
            except Exception as exc:  # noqa: BLE001
                return CommandResult(
                    renderable=renderer.render_error(
                        f"Failed to initialize runtime: {exc}"
                    )
                )

        return CommandResult(
            runtime=runtime,
            renderable=result.renderable,
            reset_main_view=True,
        )

    async def _show_page(self, renderable: object) -> None:
        """全屏展示可渲染对象，直到用户按下 Esc。

        在非 TTY 宿主环境中该页面为空操作，因此回退为将可渲染对象行内打印至静态区域。
        """

        from ui.cli.terminal.transient import can_enter_alternate_screen

        if not can_enter_alternate_screen():
            self._console.print(renderable)
            return
        page = TransientPage(renderable)
        await page.show()

    def _reset_prompt_session(self) -> None:
        self._prompt = PromptSession(self._runtime, self._queue)

    def _reset_main_view(self, renderable: object | None) -> None:
        self._push_previous_view_out()
        self._console.print(renderer.render_banner(self._runtime))
        if renderable is not None:
            self._console.print(renderable)

    def _push_previous_view_out(self) -> None:
        for _ in range(self._terminal_height() + 1):
            self._console.print()

    def _terminal_height(self) -> int:
        return shutil.get_terminal_size((80, 24)).lines

    # --- agent turn -------------------------------------------------------

    async def _run_turn(self, line: str) -> None:
        """执行带有实时预览的单次完整 agent 轮次。

        我们将 agent 事件流交给 StreamingSession，
        由其负责动态区域预览及 Esc 取消操作。
        该会话将最终 Markdown 提交至静态区域并返回缓冲区以便记录取消状态。

        会话共享 self._queue，以便用户在 agent 繁忙时仍可继续在运行中输入框键入；
        排队的提交进入同一个 FIFO，由 _drain_queue 在轮次结束后消费。
        """

        self._agent_running = True
        session = StreamingSession(
            workspace=self._runtime.workspace,
            queue=self._queue,
            runtime=self._runtime,
            interaction_host=self._interaction_host,
        )
        try:
            events = self._agent_events(line)
            await session.run(events)
        except Exception as exc:  # noqa: BLE001
            self._runtime.error_log_recorder.record_error(
                exc,
                source="cli_main_loop",
                attributes={"turn_count": self._runtime.state.turn_count},
            )
            self._runtime.error_log_recorder.flush()
            self._console.print(renderer.render_error(str(exc)))
        finally:
            self._agent_running = False

    async def _drain_queue(self) -> None:
        """轮次结束后按 FIFO 顺序弹出排队的输入。

        每个 QueuedInput 根据其 kind 进行分发：
        slash 条目通过 _handle_command 处理，绝不会作为提示词到达模型；
        prompt 条目通过 _run_turn 重新进入 agent。
        若斜杠命令导致 runtime 被替换（如 /clear、/resume、/connect），
        则继续排空，因为命令分发器已经更新了 self._runtime 并重置了提示词会话。

        循环在首次弹出为空时停止；self._queue 是单一可信来源。
        """

        while True:
            item = self._queue.pop()
            if item is None:
                return
            print_user_submitted(item.text, brightness=self._brightness)
            if item.kind == "slash":
                await self._handle_command(item.text)
                if self._exiting:
                    # 某些命令（例如 /exit）关闭了 REPL。
                    return
                continue
            await self._run_turn(item.text)

    async def _agent_events(self, line: str):
        """生成 line 对应的 agent 事件，优先收集附件。

        循环流抛出的异常作为单个合成的 error 事件暴露出来，
        以便流式预览能够行内渲染异常而不致 REPL 崩溃。
        """

        attachments = ()
        if self._runtime.attachment_collector is not None:
            attachments = (
                await self._runtime.attachment_collector.collect_for_user_turn(
                    line,
                    self._runtime.state,
                    self._runtime.message_store.current_messages(),
                    is_main_thread=True,
                )
            )
        command_attachments = tuple(self._pending_attachments)
        self._pending_attachments.clear()
        plan_attachments = ()
        if self._runtime.plan_store is not None:
            plan_attachments = tuple(
                build_plan_attachments_for_state(
                    self._runtime.state,
                    self._runtime.plan_store,
                )
            )
        attachments = (*attachments, *command_attachments, *plan_attachments)
        loop = self._runtime.loop
        if loop is None:
            raise RuntimeError("REPL runtime has no agent loop.")
        try:
            async for event in loop.stream(line, attachments=attachments):
                yield event
        except Exception as exc:  # noqa: BLE001
            self._runtime.error_log_recorder.record_error(
                exc,
                source="cli_main_loop",
                attributes={"turn_count": self._runtime.state.turn_count},
            )
            self._runtime.error_log_recorder.flush()
            yield _error_event(str(exc))

    # --- shutdown ---------------------------------------------------------

    def _shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        runtime = self._runtime
        runtime.message_store.flush_transcript()
        runtime.trace_recorder.flush()
        runtime.error_log_recorder.flush()
        if runtime.mcp_manager is not None:
            try:
                asyncio.run(runtime.mcp_manager.close_all())
            except RuntimeError:
                # 若调用方线程已在运行事件循环，asyncio.run 会抛出异常；
                # 在这种情况下允许进程退出，依赖 atexit 处理器关闭传输通道。
                pass

    def _print_untrusted_mcp_notices(self, runtime: CliRuntime) -> None:
        raw = runtime.state.metadata.get("mcp_untrusted_servers", ())
        if not isinstance(raw, (list, tuple)):
            return
        from ui.cli.terminal.static_output import print_untrusted_mcp_notice

        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "unknown")
            command = str(item.get("command") or "")
            args = str(item.get("args") or "")
            detail = command if args == "(none)" else f"{command} {args}".strip()
            print_untrusted_mcp_notice(name, detail)


# --- helpers --------------------------------------------------------------


def _error_event(message: str) -> object:
    from core.stream_events import AgentEvent

    return AgentEvent(type="error", text=message)


__all__ = ["InlineRepl"]
