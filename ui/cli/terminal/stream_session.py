"""Live-streaming preview for the dynamic region (execplan §M4)。

执行计划 ``docs/exec-plans/active/cli-checkpoint-stream-rendering.md``
把旧的"turn-end-only flush"路径整体替换为事件驱动 checkpoint 提交:

- :mod:`ui.cli.terminal.stream_state` —
  :class:`CliStreamUiState` 数据模型 + ``StaticCommit`` 队列。
- :mod:`ui.cli.terminal.stream_reducer` — 纯函数 reducer,事件 →
  state;在 ``assistant_message_completed`` 时把当前
  ``streaming_text`` 提交成 assistant_markdown checkpoint,并清
  空 ``streaming_text``;在 ``tool_result`` 时把工具结果按声明
  顺序释放成 tool_result checkpoint。
- :mod:`ui.cli.terminal.stream_view` — state → prompt_toolkit
  ``ANSI`` / ``FormattedText``。
- :mod:`ui.cli.terminal.output_coordinator` —
  :class:`TerminalOutputCoordinator` 是静态区写出的唯一入口,支
  持 ``queue_commit`` / ``flush_ready_checkpoints``。

执行计划 ``docs/exec-plans/active/cli-running-input-queue.md`` 进一步
把运行中输入框合并进 ``StreamingSession`` 动态区:agent 输出期间,用户
在底部的 prompt_toolkit 输入框继续输入并按 Enter,文本会被推入共享
``InputQueue``,动态区仍然由同一个 prompt_toolkit ``Application`` 绘
制(不是嵌套应用),``completed`` / ``error`` 事件或主动 ``Esc`` /
``Ctrl-C`` 才会让 session 退出。``InlineRepl`` 是队列的唯
一消费者,``_run_turn()`` 结束后按 FIFO 依次 drain。

本模块保留 :class:`StreamingSession` 作为对外入口。
``StreamingSession`` 内部状态改为新的 ``CliStreamUiState``,
事件循环改为:

1. 用 :class:`StreamingCoalescer` 合并高频事件;
2. 调 :func:`reduce_stream_event` 把事件折叠进 state;
3. 把 reducer 产生的 ready ``StaticCommit`` 提交给
   :class:`TerminalOutputCoordinator` 的 ``queue_commit``;
4. 立即调用 ``flush_ready_checkpoints`` 把 ready 队列写入静态区
   (动态 app 仍在运行;coordinator 的 ``run_in_terminal`` 钩子
   让 Rich 写入和动态区擦除保持原子);
5. 调 :func:`render_stream_body_ansi` 和 :func:`render_status_fragments`
   重绘动态区,已 commit 的 assistant 文本和工具结果从动态 state
   中消失,新的动态区在最新静态输出下面继续显示;
6. 运行中输入框(若传入了 ``queue``)显示在动态区底部,按 Enter
   把文本推入 ``InputQueue``,buffer 立即清空;
7. 直到 ``completed`` 或 ``error`` 才结束 session。最终
   ``completed`` 到达时,只 flush 尚未提交的 checkpoint,不重复
   打印已提交内容。

设计说明:

- **静态区写入只走 coordinator**。``_feed`` 任何位置都不能直接调
  ``print_tool_result`` 或 ``print_assistant_markdown``。
- **assistant tail 走 markdown 缓存**。view 调用
  :func:`render_cached_markdown` 复用 :mod:`markdown_rendering` 的
  TextCache,稳定前缀不会重复 lex。
- **节流策略不变**。``StreamingCoalescer`` 合并高频事件,view 重绘
  受 ``_THROTTLE_INTERVAL`` 节流。
- **checkpoint 与 assistant message 同 id 绑定**。reducer 已经
  把所有 commit 标上 ``assistant_call_id`` / ``model_turn_index``,
  coordinator 在 flush 时不依赖这些字段做调度,但它们保留在
  commit 上以供未来渲染、测试和恢复使用。
- **运行中输入框复用 idle 提示的补全器**。``InlineCompleter`` 读
  ``CliRuntime``,因此 session 接受一个可选 ``runtime`` 来提供补全。
  未提供 runtime 时,补全退化为空(等价于普通文本输入),不会破坏
  已有 ``StreamingSession()`` 构造方式。
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
    """Run the live preview while draining an agent event stream.

    The session owns a :class:`CliStreamUiState`,
    :class:`StreamingCoalescer`, and :class:`TerminalOutputCoordinator`.
    Events flow through the coalescer → reducer → coordinator →
    view; the only component allowed to write to stdout is the
    coordinator, and only after
    :meth:`TerminalOutputCoordinator.flush_ready_checkpoints`.

    ``queue`` is an optional shared :class:`InputQueue`. When
    provided, the dynamic region gains a single-line input box at
    the bottom; pressing ``Enter`` pushes the typed line onto the
    queue and clears the buffer. ``InlineRepl`` is the only
    consumer of that queue and will drain it after the current
    turn finishes. When ``queue`` is ``None`` the dynamic region
    stays in its original "preview only" form (used by tests and
    any future single-shot caller that does not need a queue).
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
        """Partial assistant text visible when the user cancelled."""

        return self.state.streaming_text

    # --- main entry point --------------------------------------------

    async def run(
        self,
        events,  # async iterator of AgentEvent
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> CliStreamUiState:
        """Drive the preview app and the event feeder concurrently.

        Returns the final :class:`CliStreamUiState`. The session
        flushes ready checkpoints as soon as the reducer produces
        them, so callers do not need a separate final flush — the
        static scrollback reflects every checkpoint in the order
        they were produced.
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
        """Drain the agent event stream into the reducer + coordinator.

        Events go through :class:`StreamingCoalescer` which folds
        bursts of high-frequency events (``assistant_delta`` and
        ``tool_progress``) into a single reducer pass within a 16 ms
        window. Low-frequency events (tool lifecycle, errors) flush
        any pending batch and apply themselves immediately.

        关键:每次 reducer 产生 ready checkpoint 之后,session 立
        刻把它交给 coordinator 并 flush,然后 invalidate 动态 app
        让 prompt_toolkit 重绘到新位置;这样静态区和动态区不会融
        合。动态 app 不会因为 ``assistant_message_completed`` 之
        后没有 active tools 就提前结束 — 它要等到 ``completed``
        或 ``error`` 才退出。
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
        """Single-event entry point fed to the coalescer.

        Delegates to the pure reducer. The reducer itself stages
        any produced commits directly on ``state.pending_static_commits``;
        the next ``_commit_pending_to_coordinator`` + ``flush`` call
        from the feeder is what actually drains them to stdout.
        """

        reduce_stream_event(self.state, event)

    def _commit_pending_to_coordinator(self) -> None:
        """Stage any reducer-produced commits on the coordinator.

        Iterates ``state.pending_static_commits`` looking for
        entries whose ``committed`` flag is still ``False``. Each
        one is handed to ``coordinator.queue_commit`` and the
        ``committed`` flag is flipped so the same commit is not
        re-staged on the next call.

        这是 streaming path 中唯一一处把 reducer 产生的 commit 移
        交给 coordinator 的地方;coordinator 的 ``flush`` 才是真
        正写入 stdout 的入口。
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
        """Build the bottom running-input row.

        Layout: a top border (``─``), a single-line editable buffer
        with the ``▌`` gutter, and a bottom border — the same shape
        as the idle prompt but without the suggestion panel and
        hint line (queueing is the primary use-case, suggestions
        remain available through ``InlineCompleter``).

        The buffer's ``accept_handler`` is wired to push the typed
        text onto the shared :class:`InputQueue` and clear the
        buffer. We use the ``accept_handler`` (the canonical
        prompt_toolkit hook for "buffer accepted") instead of a
        separate ``Keys.Enter`` binding because the latter races
        with the buffer's own Enter handling — a non-multiline
        buffer would otherwise insert a newline and lose the
        typed text.
        """

        completer = self._completer or InlineCompleter(self._runtime)
        queue = self._queue
        assert queue is not None

        def _accept(buffer: Buffer) -> bool:
            """Push the typed text onto the queue and reset the buffer.

            Returning ``False`` tells prompt_toolkit to reset the
            buffer text after the handler returns, which is the
            behaviour we want for a queueing input box.
            """

            text = buffer.text.strip()
            if not text:
                # Empty submit: keep the empty buffer, no queue mutation.
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
        """Wire the running-turn key bindings.

        ``Esc`` / ``Ctrl-C`` cancel the current turn (set the
        ``_cancel`` event, exit the app). The running input box's
        ``Enter`` behaviour is handled by the buffer's
        ``accept_handler`` (see :meth:`_build_running_input_windows`),
        so this method only owns the global cancel bindings.
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


# --- defensive helpers --------------------------------------------------


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
    """One-line ``─`` border used by the running input box.

    Centralised here so the running box can opt into the same
    visual separator as the idle prompt.
    """

    return Window(
        height=Dimension(min=1, max=1),
        char="─",
        style=f"class:{style_class}",
    )


__all__ = ["StreamingSession"]
