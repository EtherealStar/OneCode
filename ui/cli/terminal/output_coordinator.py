"""Terminal 输出协调器（execplan §M3）。

旧实现里,``StreamingSession._feed`` 在事件循环中直接调用
``print_tool_result`` 写 stdout。结果与 prompt_toolkit 动态区的擦除
/重绘产生竞态:动态区还在屏上时,Rich 静态输出可能"插入"到动态区
内部,造成视觉撕裂。

本模块引入 :class:`TerminalOutputCoordinator`:

- 它是流式会话里**唯一**允许调用 :func:`print_tool_result` 和
  :func:`print_assistant_markdown` 的组件。
- 它持有 ``pending_commits`` 队列和 ``active_app`` 标志。
  ``queue_commit`` 只 append,不写 stdout;
  ``flush_ready_checkpoints`` 是 async 边界:dynamic app 仍在运行
  时通过 ``prompt_toolkit.application.run_in_terminal`` 临时挂起
  动态区再写静态区;dynamic app 已退出时直接写。
- 它用 :func:`print_static` 写简单的状态行(取消提示)以便测试
  捕获。Rich 静态输出本身仍由 :mod:`ui.cli.terminal.static_output`
  提供;coordinator 不重新实现 Rich 渲染。
- 单元测试用 captured Rich console 验证 queue 和 flush 顺序,
  保证写入只在 ``flush_ready_checkpoints`` 后出现,并且不会重
  复输出。
- checkpoint 去重基于 ``(assistant_call_id, sequence)`` 双重
  键,而不是文本或工具名,保证同一 checkpoint 多次 queue 仍然
  只写一次。

历史 API
--------
旧 ``flush_static_commits`` 表示 turn 结束统一提交 — 这是用户可
见时序问题的根源。本模块不再提供该语义;checkpoint 提交由
``StreamingSession`` 在每个 ready commit 出现时调用,``completed``
事件到达时也只 flush 尚未提交的部分,不重复打印已提交的 commit。
本模块只暴露 async ``flush_ready_checkpoints``。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from prompt_toolkit.application import run_in_terminal

from ui.cli.terminal.static_output import (
    print_assistant_markdown,
    print_static,
    print_tool_result,
)

if TYPE_CHECKING:
    from rich.text import Text

    from ui.cli.terminal.stream_state import StaticCommit


#: A one-off static line queued for the next flush. Used for the
#: ``已取消`` notice and any other small Rich renderable that the
#: streaming path wants to push into the scrollback.
@dataclass
class _PendingStatusLine:
    text: Union[str, "Text"]


@dataclass
class _PendingCommit:
    """Wraps a :class:`StaticCommit` so we can carry workspace metadata.

    The coordinator needs the workspace to dispatch the right tool
    renderer; the static commit itself is payload-agnostic, so the
    workspace lives here instead of being baked into the commit.
    """

    commit: "StaticCommit"
    workspace: Path | None = None

    @property
    def is_assistant_markdown(self) -> bool:
        return self.commit.is_assistant_markdown

    @property
    def is_tool_result(self) -> bool:
        return self.commit.is_tool_result

    @property
    def payload(self) -> Any:
        return self.commit.payload

    @property
    def assistant_call_id(self) -> str:
        return self.commit.assistant_call_id

    @property
    def sequence(self) -> int:
        return self.commit.sequence


@dataclass
class _CommitQueue:
    """Append-only queue of pending static commits.

    Tests assert on insertion order to confirm
    ``flush_ready_checkpoints`` preserves the order events were
    delivered in. The queue is reset by ``flush_ready_checkpoints``
    after a successful drain. ``_seen_keys`` is the dedup set: any
    ``(assistant_call_id, sequence)`` already in this set is
    dropped on a second ``queue_commit`` call.
    """

    commits: list["StaticCommit"] = field(default_factory=list)
    status_lines: list[_PendingStatusLine] = field(default_factory=list)
    _seen_keys: set[tuple[str, int]] = field(default_factory=set)


class TerminalOutputCoordinator:
    """Centralised static-region commit scheduler for the streaming path.

    Lifecycle::

        coord = TerminalOutputCoordinator()
        coord.begin_dynamic_app()
        # ... events arrive, reducer + session call queue_commit
        await coord.flush_ready_checkpoints()  # safely write above the prompt
        coord.end_dynamic_app()                # dynamic app is gone

    The ``begin/end`` markers are active behaviour, not decoration:
    while the dynamic app is marked active, flushes are routed
    through prompt_toolkit's ``run_in_terminal`` so Rich output does
    not race the dynamic renderer.
    """

    def __init__(self) -> None:
        self._queue = _CommitQueue()
        self._in_dynamic_app: bool = False
        self._flush_lock = asyncio.Lock()

    # --- lifecycle ------------------------------------------------------

    def begin_dynamic_app(self) -> None:
        """Mark that the prompt_toolkit preview app is now running.

        Tests don't need to call this for correctness — the flush
        behaviour is the same with or without the marker — but the
        marker is recorded so future gating logic can rely on it
        without changing this API.
        """

        self._in_dynamic_app = True

    def end_dynamic_app(self) -> None:
        """Mark that the prompt_toolkit preview app has exited."""

        self._in_dynamic_app = False

    # --- queue ----------------------------------------------------------

    def queue_commit(
        self,
        commit: "StaticCommit",
        *,
        workspace: Path | None = None,
    ) -> None:
        """Stage a checkpoint commit.

        Calling this method never writes to stdout — the commit is
        appended to the internal queue and only flushed by
        :meth:`flush_ready_checkpoints`. Duplicate commits (same
        ``assistant_call_id`` and ``sequence``) are silently dropped
        so retrying the same commit doesn't re-print to scrollback.
        ``committed`` is a one-way flag flipped after the commit
        has been written; the second flush is a no-op for already
        committed entries.

        ``workspace`` is forwarded to the static-region tool renderer
        for ``tool_result`` commits so the per-tool formatter can
        resolve paths and pick the right summary line.
        """

        if commit.committed:
            return
        key = (commit.assistant_call_id, commit.sequence)
        if key in self._queue._seen_keys:
            return
        self._queue._seen_keys.add(key)
        self._queue.commits.append(
            _PendingCommit(commit=commit, workspace=workspace)
        )

    def queue_status_line(self, text: "Text | str") -> None:
        """Queue a one-off static line (e.g. the ``已取消`` notice).

        The line is appended verbatim to the static console during
        :meth:`flush_ready_checkpoints`. We keep this on the
        coordinator rather than reaching for :func:`print_static`
        directly so the streaming path never bypasses the
        coordinator.
        """

        self._queue.status_lines.append(_PendingStatusLine(text=text))

    # --- flush ----------------------------------------------------------

    async def flush_ready_checkpoints(self) -> None:
        """Write every queued checkpoint to the static region.

        The order is deterministic:

        1. ``StaticCommit`` payloads, in the order they were queued:
           assistant markdown is printed through
           :func:`print_assistant_markdown`; tool result commits use
           :func:`print_tool_result` with the ``call_id`` extracted
           from the underlying :class:`ToolExecutionResult`.
        2. Status lines (e.g. cancellation notices).

        After flushing, the drained commit queue is cleared;
        ``_seen_keys`` is cleared too so a future flush cycle can
        re-queue fresh commits. ``committed`` flags on individual
        commits are not touched — they live on the state-side
        :class:`StaticCommit` and are flipped by the reducer's
        commit path.
        """

        if not self._queue.commits and not self._queue.status_lines:
            return

        async with self._flush_lock:
            commits = list(self._queue.commits)
            status_lines = list(self._queue.status_lines)
            if not commits and not status_lines:
                return
            self._queue.commits.clear()
            self._queue.status_lines.clear()
            self._queue._seen_keys.clear()

            def write_static() -> None:
                self._write_static(commits, status_lines)

            if self._in_dynamic_app:
                await run_in_terminal(write_static, render_cli_done=False)
            else:
                write_static()

    def _write_static(
        self,
        commits: list[_PendingCommit],
        status_lines: list[_PendingStatusLine],
    ) -> None:
        """Write an already-drained checkpoint batch to stdout."""

        for pending in commits:
            commit = pending.commit
            if commit.is_assistant_markdown:
                text = str(commit.payload or "")
                print_assistant_markdown(text)
            elif commit.is_tool_result:
                result = commit.payload
                call_id = getattr(result, "tool_call_id", "") or ""
                print_tool_result(
                    result,
                    call_id=call_id,
                    workspace=pending.workspace,
                )
        for line in status_lines:
            print_static(line.text)

    # --- inspection helpers used by tests and the streaming session ----

    def pending_commit_count(self) -> int:
        return len(self._queue.commits)

    def pending_status_line_count(self) -> int:
        return len(self._queue.status_lines)

__all__ = ["TerminalOutputCoordinator"]
