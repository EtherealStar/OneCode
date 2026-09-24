"""动态区域提示词输入。

本模块负责行内 REPL 中位于屏幕底部的提示词输入区。
它是一个非全屏的 prompt_toolkit.Application：

- 拥有顶部和底部的 ─ 边框，
- 在 > 栏后显示可编辑缓冲区，
- 为 / 命令和 @ 文件补全浮动显示补全菜单（InlineCompleter），
- 在补全菜单打开时将 Enter 视为“提交”，将 Tab 视为“填入但不提交”。

提示词会话返回结构化的 PromptSubmission 而非纯字符串，
以便 REPL 循环无需魔术字符串即可区分提交、取消和退出。

关于运行中轮次输入的说明：入队不是本模块的职责。
Agent 运行时路径在 ui.cli.terminal.stream_session.StreamingSession 中实现，
它在动态区域底部托管自己的 prompt_toolkit 输入框，并将提交推入共享的
InputQueue。PromptSession 仅在构造时读取传入的底层 InputQueue 引用，
自身从不调用 queue.push。

Enter/Tab 语义使用 prompt_toolkit 原生的 Buffer.complete_state 作为“当前高亮项”
的唯一定义，而不是自行维护索引。当菜单打开但未显式选中任何项时，默认指向首个补全项，
这与首项被隐式选中的交互行为一致。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style

from ui.cli.terminal.completer import InlineCompleter
from ui.cli.terminal.queue import InputQueue
from ui.cli.types import CliRuntime


class SubmissionKind(str, Enum):
    """提示词提交的触发方式。"""

    SUBMIT = "submit"
    CANCEL = "cancel"
    EXIT = "exit"


@dataclass(frozen=True)
class PromptSubmission:
    """单次提示词会话调用的结果。"""

    kind: SubmissionKind
    text: str = ""


# prompt_toolkit 样式类：仅设置前景色，使终端宿主的背景色生效。
# 边框使用暗灰色以避免在浅色或深色配置文件中发生冲突。
_PROMPT_STYLE = Style.from_dict(
    {
        "prompt-border": "#666666",
        "prompt-gutter": "ansicyan bold",
        "prompt-hint": "#666666",
        "suggestion": "#888888",
        "suggestion-current": "ansicyan bold",
        "suggestion-meta": "#777777",
        "suggestion-meta-current": "ansiwhite",
    }
)

_EXIT_CONFIRM_HINT = "Press Ctrl-C again to exit"
_OSC11_REPLY_FRAGMENT = re.compile(
    r"(?:\x1b)?\]11;rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\|\\)?"
)


def strip_osc11_reply_fragments(text: str) -> str:
    """移除已知可能泄漏到输入中的窄 OSC 11 响应片段。"""

    return _OSC11_REPLY_FRAGMENT.sub("", text)


def _highlighted_completion(buffer: Buffer) -> Completion | None:
    """返回 Enter/Tab 应当作用的目标补全项。

    解析优先级：

    1. 用户显式导航到的补全项（complete_state.current_completion）。
    2. 已打开菜单中的首个补全项。
    3. 同步计算出的首个补全项。complete_while_typing 通过后台任务填充
       complete_state，因此当快速驱动提示词（或在测试中无界面驱动）时，
       Enter 触发时菜单可能尚未打开。直接计算补全项可消除该时序竞争，
       同时不会在非补全上下文中自动采纳：补全器对纯文本返回空，
       此处保持为 None 并提交字面行。
    """

    state = buffer.complete_state
    if state is not None:
        if state.current_completion is not None:
            return state.current_completion
        if state.completions:
            return state.completions[0]
    completer = buffer.completer
    if completer is None:
        return None
    completions = list(completer.get_completions(buffer.document, CompleteEvent()))
    if completions:
        return completions[0]
    return None


def _completion_kind(completion: Completion) -> str | None:
    item = getattr(completion, "_suggestion_item", None)
    kind = getattr(item, "kind", None)
    return kind if isinstance(kind, str) else None


def _directory_mention_token_end(buffer: Buffer) -> int | None:
    text = buffer.text
    cursor = buffer.cursor_position
    at_index = text.rfind("@", 0, min(cursor + 1, len(text)))
    if at_index < 0:
        return None
    if at_index > 0 and not text[at_index - 1].isspace():
        return None
    end = cursor
    while end < len(text) and not text[end].isspace():
        end += 1
    token = text[at_index + 1 : end]
    if not token or not token.endswith("/"):
        return None
    return end


def _apply_completion_for_edit(buffer: Buffer, completion: Completion) -> None:
    buffer.apply_completion(completion)
    kind = _completion_kind(completion)
    if kind == "file" and not buffer.text.endswith(" "):
        buffer.insert_text(" ")


class PromptSession:
    """A reusable wrapper around a prompt_toolkit Application."""

    def __init__(
        self,
        runtime: CliRuntime | None,
        queue: InputQueue,
        *,
        bottom_hint: str = "",
        exit_confirm_window_seconds: float = 1.5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._queue = queue
        self._bottom_hint = bottom_hint
        self._exit_confirm_window_seconds = exit_confirm_window_seconds
        self._clock = clock
        self._completer = InlineCompleter(runtime)
        self._pending_exit_at: float | None = None
        self._suppress_next_text_reset = False
        self._active_hint: _PromptHint | None = None

    async def read(
        self,
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> PromptSubmission:
        """阻塞直到用户提交、取消或退出。

        input/output 为测试注入点，测试传入
        prompt_toolkit.input.create_pipe_input 管道和
        prompt_toolkit.output.DummyOutput。在生产环境中二者均为
        None，prompt_toolkit 绑定到真实终端。
        """

        result: list[PromptSubmission | None] = [None]
        app = self._build_application(result, input=input, output=output)
        await app.run_async()
        # Ctrl-C/Ctrl-D 处理器总是设置结果；
        # 未触发处理器就正常退出的情况视为取消。
        return result[0] or PromptSubmission(kind=SubmissionKind.CANCEL)

    # --- 内部实现 ---

    def _build_application(
        self,
        result: list[PromptSubmission | None],
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> Application[None]:
        buffer = Buffer(
            completer=self._completer,
            complete_while_typing=True,
            multiline=False,
            on_text_changed=self._on_buffer_text_changed,
        )
        hint = _PromptHint(self._current_bottom_hint())
        self._active_hint = hint
        buffer_control = BufferControl(
            buffer=buffer,
            input_processors=[BeforeInput("> ", style="class:prompt-gutter")],
            include_default_input_processors=True,
        )
        bindings = self._build_key_bindings(buffer, result, hint)

        prompt_window = Window(
            content=buffer_control,
            height=Dimension(min=1, max=1),
            wrap_lines=False,
        )
        suggestion_panel = _suggestion_panel(buffer)
        body = HSplit(
            [
                _spacer_window(),
                _border_window(),
                prompt_window,
                suggestion_panel,
                _hint_window(buffer, hint.text),
                _border_window(),
            ]
        )
        return Application(
            layout=Layout(body, focused_element=prompt_window),
            style=_PROMPT_STYLE,
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
            key_bindings=bindings,
            input=input,
            output=output,
        )

    def _build_key_bindings(
        self,
        buffer: Buffer,
        result: list[PromptSubmission | None],
        hint: _PromptHint,
    ) -> KeyBindings:
        bindings = KeyBindings()

        def finish(submission: PromptSubmission, event) -> None:  # type: ignore[no-untyped-def]
            self._reset_pending_exit()
            result[0] = submission
            event.app.exit()

        @bindings.add(Keys.Enter, eager=True)
        def _on_enter(event) -> None:  # type: ignore[no-untyped-def]
            directory_mention_end = _directory_mention_token_end(buffer)
            if directory_mention_end is not None:
                if buffer.complete_state is not None:
                    buffer.cancel_completion()
                buffer.cursor_position = directory_mention_end
                if (
                    directory_mention_end >= len(buffer.text)
                    or buffer.text[directory_mention_end] != " "
                ):
                    buffer.insert_text(" ")
                hint.reset()
                event.app.invalidate()
                return
            completion = _highlighted_completion(buffer)
            if completion is not None:
                kind = _completion_kind(completion)
                if kind in {"file", "directory"}:
                    # 文件提及通常后续带有自然语言请求，因此采纳补全后保持提示词继续打开。
                    _apply_completion_for_edit(buffer, completion)
                    hint.reset()
                    event.app.invalidate()
                    return
                # 命令/会话补全属于完整的输入目标：采纳高亮项并立即提交。
                buffer.apply_completion(completion)
                text = buffer.text.strip()
                if text:
                    finish(PromptSubmission(SubmissionKind.SUBMIT, text), event)
                return
            text = buffer.text.strip()
            if not text:
                return
            finish(PromptSubmission(SubmissionKind.SUBMIT, text), event)

        @bindings.add(Keys.Tab, eager=True)
        def _on_tab(event) -> None:  # type: ignore[no-untyped-def]
            self._reset_pending_exit()
            hint.reset()
            completion = _highlighted_completion(buffer)
            if completion is not None:
                # 菜单已打开且按 Tab：用补全项填充输入框但不提交。下次按 Enter 才会提交。
                _apply_completion_for_edit(buffer, completion)
                return
            # 无菜单：触发补全以向用户展示建议列表。
            buffer.start_completion(select_first=False)

        @bindings.add(Keys.Down, eager=True)
        def _on_down(event) -> None:  # type: ignore[no-untyped-def]
            self._reset_pending_exit()
            hint.reset()
            if buffer.complete_state is not None:
                buffer.complete_next()
            else:
                buffer.start_completion(select_first=True)

        @bindings.add(Keys.Up, eager=True)
        def _on_up(event) -> None:  # type: ignore[no-untyped-def]
            self._reset_pending_exit()
            hint.reset()
            if buffer.complete_state is not None:
                buffer.complete_previous()

        @bindings.add(Keys.ControlC, eager=True)
        def _on_ctrl_c(event) -> None:  # type: ignore[no-untyped-def]
            now = self._clock()
            if self._pending_exit_at is not None:
                elapsed = now - self._pending_exit_at
                if elapsed <= self._exit_confirm_window_seconds:
                    finish(PromptSubmission(SubmissionKind.EXIT), event)
                    return
            self._pending_exit_at = now
            hint.set(_EXIT_CONFIRM_HINT)
            asyncio.create_task(self._expire_exit_hint_after(now, hint, event.app))
            if buffer.complete_state is not None:
                buffer.cancel_completion()
            if buffer.text:
                self._suppress_next_text_reset = True
                buffer.text = ""
            event.app.invalidate()

        @bindings.add(Keys.ControlD, eager=True)
        def _on_ctrl_d(event) -> None:  # type: ignore[no-untyped-def]
            # Shell 风格 EOF：空缓冲区按 Ctrl-D 退出 REPL。
            if not buffer.text:
                finish(PromptSubmission(SubmissionKind.EXIT), event)

        @bindings.add(Keys.Escape, eager=True)
        def _on_escape(event) -> None:  # type: ignore[no-untyped-def]
            # Esc 键在菜单打开时关闭补全菜单；否则为空操作。
            if buffer.complete_state is not None:
                buffer.cancel_completion()

        return bindings

    def _on_buffer_text_changed(self, buffer: Buffer) -> None:
        cleaned = strip_osc11_reply_fragments(buffer.text)
        if cleaned != buffer.text:
            cursor = min(buffer.cursor_position, len(cleaned))
            self._suppress_next_text_reset = True
            buffer.text = cleaned
            buffer.cursor_position = cursor
            return
        if self._suppress_next_text_reset:
            self._suppress_next_text_reset = False
            return
        if buffer.text:
            self._reset_pending_exit()
            if self._active_hint is not None:
                self._active_hint.reset()

    def _reset_pending_exit(self) -> None:
        self._pending_exit_at = None

    def _current_bottom_hint(self) -> str:
        if self._bottom_hint:
            return self._bottom_hint
        state = getattr(self._runtime, "state", None)
        if state is not None and state.is_plan_mode():
            return "plan mode on"
        return ""

    async def _expire_exit_hint_after(
        self,
        timestamp: float,
        hint: _PromptHint,
        app: Application[None],
    ) -> None:
        await asyncio.sleep(self._exit_confirm_window_seconds)
        if self._pending_exit_at != timestamp:
            return
        self._reset_pending_exit()
        hint.reset()
        app.invalidate()


# --- 布局辅助函数 ---


def _border_window() -> Window:
    return Window(
        height=Dimension(min=1, max=1),
        char="─",
        style="class:prompt-border",
    )


def _spacer_window() -> Window:
    return Window(height=Dimension(min=1, max=1), char="")


def _hint_window(buffer: Buffer, hint: Callable[[], str]) -> ConditionalContainer:
    window = Window(
        height=Dimension(min=1, max=1),
        content=FormattedTextControl(
            lambda: [("class:prompt-hint", _hint_text(buffer, hint))]
        ),
        style="class:prompt-hint",
    )
    return ConditionalContainer(
        window,
        filter=Condition(lambda: bool(_hint_text(buffer, hint))),
    )


def _hint_text(buffer: Buffer, hint: Callable[[], str]) -> str:
    text = hint()
    if text:
        return text
    if _suggestion_rows(buffer):
        return "Enter to accept · Tab to fill · ↑↓ to choose"
    return ""


class _PromptHint:
    def __init__(self, default: str) -> None:
        self._default = default
        self._text = default

    def text(self) -> str:
        return self._text

    def set(self, text: str) -> None:
        self._text = text

    def reset(self) -> None:
        self._text = self._default


def _suggestion_panel(buffer: Buffer) -> ConditionalContainer:
    window = Window(
        height=Dimension(min=1, max=8),
        content=FormattedTextControl(lambda: _suggestion_fragments(buffer)),
        dont_extend_height=True,
        style="class:suggestion",
    )
    return ConditionalContainer(
        window,
        filter=Condition(lambda: bool(_suggestion_rows(buffer))),
    )


def _suggestion_rows(buffer: Buffer) -> tuple[tuple[Completion, bool], ...]:
    state = buffer.complete_state
    if state is None or not state.completions:
        if buffer.completer is None:
            return ()
        completions = list(
            buffer.completer.get_completions(buffer.document, CompleteEvent())
        )
        return tuple(
            (completion, index == 0) for index, completion in enumerate(completions[:8])
        )
    completions = tuple(state.completions[:8])
    if not completions:
        return ()
    current = state.current_completion
    if current is None:
        current_index = 0
    else:
        try:
            current_index = completions.index(current)
        except ValueError:
            current_index = 0
    return tuple(
        (completion, index == current_index)
        for index, completion in enumerate(completions)
    )


def _suggestion_fragments(buffer: Buffer) -> FormattedText:
    rows = _suggestion_rows(buffer)
    if not rows:
        return FormattedText([])
    command_width = min(
        max(len(completion.display_text) for completion, _ in rows),
        32,
    )
    fragments: list[tuple[str, str]] = []
    for index, (completion, selected) in enumerate(rows):
        display = completion.display_text
        meta = completion.display_meta_text
        pointer = "> " if selected else "  "
        display_style = "class:suggestion-current" if selected else "class:suggestion"
        meta_style = (
            "class:suggestion-meta-current" if selected else "class:suggestion-meta"
        )
        fragments.append((display_style, pointer))
        fragments.append((display_style, display.ljust(command_width)))
        if meta:
            fragments.append((meta_style, f"  {meta}"))
        if index < len(rows) - 1:
            fragments.append(("", "\n"))
    return FormattedText(fragments)


__all__ = [
    "PromptSession",
    "PromptSubmission",
    "SubmissionKind",
    "strip_osc11_reply_fragments",
]
