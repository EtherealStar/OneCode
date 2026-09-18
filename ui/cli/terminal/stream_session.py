"""动态区域实时流式预览。

执行计划 docs/exec-plans/active/cli-checkpoint-stream-rendering.md
把旧的 turn-end-only flush 路径整体替换为事件驱动 checkpoint 提交：

- ui.cli.terminal.stream_state：
  CliStreamUiState 数据模型与 StaticCommit 队列。
- ui.cli.terminal.stream_reducer：纯函数 reducer，事件到状态的折叠；
  在 assistant_message_completed 时把当前 streaming_text 提交成
  assistant_markdown checkpoint 并清空 streaming_text；
  在 tool_result 时把工具结果按声明顺序释放成 tool_result checkpoint。
- ui.cli.terminal.stream_view：state 到 prompt_toolkit ANSI / FormattedText 的转换。
- ui.cli.terminal.output_coordinator：
  TerminalOutputCoordinator 是静态区写出的唯一入口，支持 queue_commit 与 flush_ready_checkpoints。

执行计划 docs/exec-plans/active/cli-running-input-queue.md 进一步
把运行中输入框合并进 StreamingSession 动态区：agent 输出期间，用户
在底部的 prompt_toolkit 输入框继续输入并按 Enter，文本会被推入共享
InputQueue，动态区仍然由同一个 prompt_toolkit Application 绘制（不是嵌套应用），
completed / error 事件或主动 Esc / Ctrl-C 才会让 session 退出。
InlineRepl 是队列的唯一消费者，_run_turn() 结束后按 FIFO 依次 drain。

本模块保留 StreamingSession 作为对外入口。
StreamingSession 内部状态改为新的 CliStreamUiState，事件循环改为：

1. 用 StreamingCoalescer 合并高频事件；
2. 调用 reduce_stream_event 把事件折叠进 state；
3. 把 reducer 产生的 ready StaticCommit 提交给
   TerminalOutputCoordinator 的 queue_commit；
4. 立即调用 flush_ready_checkpoints 把 ready 队列写入静态区
   （动态 app 仍在运行；coordinator 的 run_in_terminal 钩子
   让 Rich 写入和动态区擦除保持原子）；
5. 调用 render_stream_body_ansi 和 render_status_fragments
   重绘动态区，已 commit 的 assistant 文本和工具结果从动态 state
   中消失，新的动态区在最新静态输出下面继续显示；
6. 运行中输入框（若传入了 queue）显示在动态区底部，按 Enter
   把文本推入 InputQueue，buffer 立即清空；
7. 直到 completed 或 error 才结束 session。最终 completed
   到达时，只 flush 尚未提交的 checkpoint，不重复打印已提交内容。

设计说明：

- 静态区写入只走 coordinator。_feed 任何位置都不能直接调用
  print_tool_result 或 print_assistant_markdown。
- assistant tail 走 markdown 缓存。view 调用
  render_cached_markdown 复用 markdown_rendering 的 TextCache，稳定前缀不会重复 lex。
- 节流策略不变。StreamingCoalescer 合并高频事件，view 重绘受 _THROTTLE_INTERVAL 节流。
- checkpoint 与 assistant message 同 id 绑定。reducer 已经
  把所有 commit 标上 assistant_call_id / model_turn_index，
  coordinator 在 flush 时不依赖这些字段做调度，但它们保留在
  commit 上以供未来渲染、测试和恢复使用。
- 运行中输入框复用 idle 提示的补全器。InlineCompleter 读取
  CliRuntime，因此 session 接受可选的 runtime 来提供补全。
  未提供 runtime 时，补全退化为空（等价于普通文本输入），不会破坏已有构造方式。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style
from rich.text import Text

from ui.cli.terminal.completer import InlineCompleter
from ui.cli.terminal.interaction_host import TerminalInteractionHost
from ui.cli.terminal.output_coordinator import TerminalOutputCoordinator
from ui.cli.terminal.queue import InputQueue
from ui.cli.terminal.stream_reducer import (
    queue_assistant_checkpoint,
    reduce_stream_event,
    release_ready_tool_result_commits,
)
from ui.cli.terminal.stream_state import CliStreamUiState, CommitKind
from ui.cli.terminal.stream_view import (
    render_status_fragments,
    render_stream_body_ansi,
)
from ui.cli.terminal.streaming_coalescer import StreamingCoalescer

# 兼容旧测试里 ``from ui.cli.terminal.stream_session import StreamingSession``
# 的导入形式 — 这些名字在 reducer 模块中存在,这里 re-export 是为了让
# ``_feed`` 内部如果需要重置状态时仍可访问。``StreamingSession`` 自身
# 不直接调用它们(走 reducer),但保留 export 不会破坏现有测试。
from ui.cli.types import CliRuntime


#: 50 ms = 20 fps; 与旧实现保持一致。
_THROTTLE_INTERVAL = 0.05
#: 动态区 preview 段最大可视行数；与 view 模块中的常量保持一致。
_PREVIEW_MAX_LINES = 12
#: 动态区底部 running input box 高度：单行即可。
_INPUT_BOX_HEIGHT = 1
#: running input box 的 prompt gutter 样式 — 跟 idle prompt 的 ``>`` 区分,
#: 使用 ``▌`` 表示"运行中可继续输入"。
_RUNNING_GUTTER = "▌ "


# 动态区运行中输入框的样式表。前景色定义,背景由终端宿主决定。
_RUNNING_INPUT_STYLE = Style.from_dict(
    {
        "running-border": "#666666",
        "running-gutter": "ansicyan bold",
    }
)


class StreamingSession:
    """在消费 agent 事件流的同时运行实时预览。

    该会话拥有 CliStreamUiState、StreamingCoalescer 和 TerminalOutputCoordinator。
    事件流转路径为：合并器 → reducer → coordinator → view；
    唯一允许向 stdout 输出的组件是 coordinator，且仅在
    TerminalOutputCoordinator.flush_ready_checkpoints 之后。

    queue 为可选的共享 InputQueue。若提供，动态区域底部会增加单行输入框；
    按 Enter 将键入的内容推入队列并清空缓冲区。InlineRepl 是该队列的唯一消费者，
    将在当前轮次结束后将其排空。当 queue 为 None 时，动态区域保持最初的
    “纯预览”形式（供测试或无需队列的单次调用方使用）。
    """

    def __init__(
        self,
        *,
        throttle: float = _THROTTLE_INTERVAL,
        coalesce_window_seconds: float = 0.016,
        workspace: Path | None = None,
        queue: InputQueue | None = None,
        runtime: CliRuntime | None = None,
        interaction_host: TerminalInteractionHost | None = None,
    ) -> None:
        self.state: CliStreamUiState = CliStreamUiState()
        self.coordinator: TerminalOutputCoordinator = TerminalOutputCoordinator()
        self._throttle = throttle
        self._coalesce_window = coalesce_window_seconds
        self._workspace = workspace
        self._queue = queue
        self._runtime = runtime
        self._interaction_host = interaction_host
        self._completer = InlineCompleter(runtime) if queue is not None else None
        self._cancel = asyncio.Event()
        self._preview_complete = asyncio.Event()
        self._finalised = False

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def cancelled_partial(self) -> str:
        """用户取消时可见的局部 assistant 文本。"""

        return self.state.streaming_text

    # --- main entry point --------------------------------------------

    async def run(
        self,
        events,  # async iterator of AgentEvent
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> CliStreamUiState:
        """并发驱动预览应用和事件输送器。

        返回最终的 CliStreamUiState。会话在 reducer 生成就绪检查点后
        立即刷新输出，因此调用方无需单独执行最终刷新：
        静态回滚历史会按检查点生成顺序完整体现。
        """

        app = self._build_app(input=input, output=output)
        if self._interaction_host is not None:
            self._interaction_host.bind_app(app)
        self.coordinator.begin_dynamic_app()
        feeder: asyncio.Task[None] | None = None
        feeder_already_awaited = False

        def start_feeder() -> None:
            nonlocal feeder
            feeder = asyncio.create_task(self._feed(events, app))

        try:
            await app.run_async(pre_run=start_feeder)
        finally:
            if feeder is not None and not feeder.done() and self._cancel.is_set():
                self._cancel.set()
                feeder.cancel()
                try:
                    await feeder
                    feeder_already_awaited = True
                except asyncio.CancelledError:
                    feeder_already_awaited = True
                    pass
        self.coordinator.end_dynamic_app()
        if self._interaction_host is not None:
            self._interaction_host.unbind_app(app)
        self._finalised = True
        if self._cancel.is_set():
            self.coordinator.queue_status_line(
                Text("已取消", style="onecode.warning")
            )
        # 在 session 收尾阶段,可能 reducer 还在最后一波 commit 之
        # 中(例如 ``completed`` 事件触发的 assistant_markdown),
        # 再做一次 commit + flush 兜底。reducer 自己保证
        # ``streaming_text`` 在 ``completed`` 之后为空,所以不会
        # 重复打印。
        self._commit_pending_to_coordinator()
        await self.coordinator.flush_ready_checkpoints()
        if feeder is not None and not feeder_already_awaited:
            await feeder
        return self.state

    async def _feed(self, events, app: Application) -> None:
        """将 agent 事件流输送至 reducer 和 coordinator。

        事件流经 StreamingCoalescer，在 16 毫秒窗口内将高频事件脉冲
        （assistant_delta 和 tool_progress）折叠为单次 reducer 处理。
        低频事件（工具生命周期、错误）会刷新任何待处理批次并立即生效。

        关键：每次 reducer 产生 ready checkpoint 之后，session 立即把它交给
        coordinator 并 flush，然后 invalidate 动态 app 让 prompt_toolkit 重绘到新位置；
        这样静态区和动态区不会融合。动态 app 不会因为 assistant_message_completed 之后
        没有 active tools 就提前结束，它要等到 completed 或 error 才退出。
        """

        coalescer = StreamingCoalescer(
            apply=lambda event: self._apply_event(event),
            window_seconds=self._coalesce_window,
            clock=time.monotonic,
        )
        last_render = 0.0
        try:
            async for event in events:
                if self._cancel.is_set():
                    break
                pushed_low_freq = coalescer.push(event)
                # 把 reducer 产生的 ready commit 移交给 coordinator
                # 并立即 flush;flush 之后 invalidate 动态 app,让
                # prompt_toolkit 在新的位置重绘。
                self._commit_pending_to_coordinator()
                await self.coordinator.flush_ready_checkpoints()
                now = time.monotonic()
                should_redraw = pushed_low_freq or (
                    (now - last_render) >= self._throttle
                    and coalescer.should_flush(now)
                )
                if should_redraw:
                    if coalescer.should_flush(now):
                        coalescer.flush()
                    last_render = now
                    _safe_invalidate(app)
        finally:
            coalescer.flush()
            self._commit_pending_to_coordinator()
            await self.coordinator.flush_ready_checkpoints()
            _safe_invalidate(app)
            _safe_exit(app)

    def _apply_event(self, event: object) -> None:
        """输送给合并器的单事件入口点。

        委托给纯函数 reducer。reducer 本身将生成的提交直接暂存在
        state.pending_static_commits 上；后续由输送器调用的
        _commit_pending_to_coordinator 与 flush 才会真正将其排空至 stdout。
        """

        reduce_stream_event(self.state, event)

    def _commit_pending_to_coordinator(self) -> None:
        """将 reducer 生成的提交暂存至协调器。

        遍历 state.pending_static_commits，寻找 committed 标志仍为 False 的条目。
        逐项交给 coordinator.queue_commit 并将 committed 标志置为 True，
        避免在下次调用时重复暂存。

        这是流式路径中唯一将 reducer 产生的 commit 移交给 coordinator 的地方；
        coordinator 的 flush 才是真正写入 stdout 的入口。
        """

        for commit in self.state.pending_static_commits:
            if commit.committed:
                continue
            self.coordinator.queue_commit(commit, workspace=self._workspace)
            commit.committed = True

    def _build_app(
        self,
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> Application[None]:
        bindings = self._build_key_bindings()

        def preview_text():  # type: ignore[no-untyped-def]
            try:
                width = app.output.get_size().columns  # type: ignore[union-attr]
            except Exception:
                width = 80
            if self._interaction_host is not None:
                permission_body = self._interaction_host.render_body(width=width)
                if permission_body is not None:
                    return permission_body
            # 当运行中输入框共享同一个 InputQueue 时,把队列快照
            # 传给 view 以便在动态区显示 queued preview;否则不
            # 传(None 表示空预览,不会显示额外行)。
            queued_snapshot = (
                self._queue.snapshot() if self._queue is not None else None
            )
            return render_stream_body_ansi(
                self.state,
                width=width,
                queued_inputs=queued_snapshot,
            )

        def status_text():  # type: ignore[no-untyped-def]
            if self._interaction_host is not None:
                permission_status = self._interaction_host.render_status()
                if permission_status is not None:
                    return permission_status
            return render_status_fragments(self.state)

        preview_window = Window(
            content=FormattedTextControl(preview_text),
            height=Dimension(min=1, max=_PREVIEW_MAX_LINES + 1),
            wrap_lines=True,
        )
        status_window = Window(
            height=Dimension(min=1, max=1),
            content=FormattedTextControl(status_text),
        )

        children: list[Window] = [preview_window, status_window]
        # 仅有 ``queue`` 传入时才追加运行中输入框,这样不破坏旧
        # ``StreamingSession()`` 构造路径(测试场景无队列)。
        focus_target = preview_window
        if self._queue is not None:
            running_windows = self._build_running_input_windows()
            children.extend(running_windows)
            # 焦点放在运行中输入框上,这样用户键入会进入 buffer
            # 而不是被 preview window 吞掉。
            focus_target = self._running_input_window
        layout = Layout(HSplit(children), focused_element=focus_target)
        app: Application[None] = Application(
            layout=layout,
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
            key_bindings=bindings,
            input=input,
            output=output,
            style=_RUNNING_INPUT_STYLE,
        )
        # 把 input box 的 buffer 暴露给 key bindings 闭包,避免
        # ``_build_key_bindings`` 需要传多个参数。
        self._app = app
        return app

    def _build_running_input_windows(self) -> list[Window]:
        """构建底部的运行中输入行。

        布局：顶部边框（─）、带有 ▌ 栏的单行可编辑缓冲区，以及底部边框。
        结构与空闲提示符一致，但不包含建议面板和提示行
        （主要用途为入队排队，建议仍可通过 InlineCompleter 获取）。

        缓冲区的 accept_handler 被连接为将键入的文本推入共享的
        InputQueue 并重置缓冲区。我们使用 accept_handler
        （prompt_toolkit 中“缓冲区被采纳”的标准钩子）而非单独的
        Keys.Enter 按键绑定，因为后者会与缓冲区自身的 Enter 处理发生时序竞争
        （非多行缓冲区可能会插入换行符并丢失键入的文本）。
        """

        completer = self._completer or InlineCompleter(self._runtime)
        queue = self._queue
        assert queue is not None

        def _accept(buffer: Buffer) -> bool:
            """将键入的文本推入队列并重置缓冲区。

            返回 False 告知 prompt_toolkit 在处理器返回后重置缓冲区文本，
            这正是排队输入框所需的操作行为。
            """

            text = buffer.text.strip()
            if not text:
                # 空提交：保持空缓冲区，不对队列做任何修改。
                buffer.reset()
                return False
            queue.push(text)
            buffer.reset()
            return False

        self._running_buffer = Buffer(
            completer=completer,
            complete_while_typing=True,
            multiline=False,
            accept_handler=_accept,
        )
        gutter = _RUNNING_GUTTER
        self._running_buffer_control = BufferControl(
            buffer=self._running_buffer,
            input_processors=[BeforeInput(gutter, style="class:running-gutter")],
            include_default_input_processors=True,
        )
        self._running_input_window = Window(
            content=self._running_buffer_control,
            height=Dimension(min=_INPUT_BOX_HEIGHT, max=_INPUT_BOX_HEIGHT),
            wrap_lines=False,
        )
        return [
            _border_window("running-border"),
            self._running_input_window,
            _border_window("running-border"),
        ]

    def _build_key_bindings(self) -> KeyBindings:
        """装配运行中轮次的按键绑定。

        Esc / Ctrl-C 取消当前轮次（设置 _cancel 事件，退出 app）。
        运行中输入框的 Enter 行为由缓冲区的 accept_handler 处理，
        因此本方法仅负责全局取消绑定。
        """

        bindings = KeyBindings()
        no_permission_modal = Condition(
            lambda: self._interaction_host is None
            or self._interaction_host.active_permission is None
        )

        @bindings.add(Keys.Escape, eager=True, filter=no_permission_modal)
        @bindings.add(Keys.ControlC, eager=True, filter=no_permission_modal)
        def _on_cancel(event) -> None:  # type: ignore[no-untyped-def]
            self._cancel_turn(event)

        if self._interaction_host is not None:
            return merge_key_bindings(
                [
                    self._interaction_host.key_bindings(
                        fallback_cancel=lambda event: self._cancel_turn(event),
                        exit_on_complete=False,
                    ),
                    bindings,
                ]
            )
        return bindings

    def _cancel_turn(self, event) -> None:  # type: ignore[no-untyped-def]
        self._cancel.set()
        event.app.exit()


# --- 防御性辅助函数 ---


def _safe_invalidate(app: Application) -> None:
    try:
        if app.is_running:
            app.invalidate()
    except Exception:
        pass


def _safe_exit(app: Application) -> None:
    try:
        if app.is_running:
            app.exit()
    except Exception:
        pass


def _border_window(style_class: str) -> Window:
    """运行中输入框使用的单行 ─ 边框。

    统一收敛于此处，以便运行中输入框能够复用与空闲提示符相同的视觉分隔线。
    """

    return Window(
        height=Dimension(min=1, max=1),
        char="─",
        style=f"class:{style_class}",
    )


__all__ = ["StreamingSession"]
