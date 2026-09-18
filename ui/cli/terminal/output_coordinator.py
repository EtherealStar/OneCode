"""Terminal 输出协调器。

旧实现里，StreamingSession._feed 在事件循环中直接调用
print_tool_result 写 stdout。结果与 prompt_toolkit 动态区的擦除
与重绘产生竞态：动态区还在屏上时，Rich 静态输出可能插入到动态区
内部，造成视觉撕裂。

本模块引入 TerminalOutputCoordinator：

- 它是流式会话里唯一允许调用 print_tool_result 和
  print_assistant_markdown 的组件。
- 它持有 pending_commits 队列和 active_app 标志。
  queue_commit 只追加，不写 stdout；
  flush_ready_checkpoints 是 async 边界：dynamic app 仍在运行
  时通过 prompt_toolkit.application.run_in_terminal 临时挂起
  动态区再写静态区；dynamic app 已退出时直接写。
- 它用 print_static 写简单的状态行（取消提示）以便测试
  捕获。Rich 静态输出本身仍由 ui.cli.terminal.static_output
  提供；coordinator 不重新实现 Rich 渲染。
- 单元测试用捕获的 Rich console 验证 queue 和 flush 顺序，
  保证写入只在 flush_ready_checkpoints 后出现，并且不会重
  复输出。
- checkpoint 去重基于 (assistant_call_id, sequence) 双重
  键，而不是文本或工具名，保证同一 checkpoint 多次 queue 仍然
  只写一次。

历史 API：
旧 flush_static_commits 表示 turn 结束统一提交，这是用户可
见时序问题的根源。本模块不再提供该语义；checkpoint 提交由
StreamingSession 在每个 ready commit 出现时调用，completed
事件到达时也只 flush 尚未提交的部分，不重复打印已提交的 commit。
本模块只暴露 async flush_ready_checkpoints。
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


# 排队等待下次刷新的单次静态文本行。用于取消通知及流式路径希望推入回滚历史的其他小型 Rich 可渲染对象。
@dataclass
class _PendingStatusLine:
    text: Union[str, "Text"]


@dataclass
class _PendingCommit:
    """包装 StaticCommit 以携带工作区元数据。

    协调器需要工作区以分发对应的工具渲染器；
    静态提交本身与载荷无关，因此工作区信息存放在此处而非硬编码到提交中。
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
    """待处理静态提交的仅追加队列。

    测试通过断言插入顺序来验证 flush_ready_checkpoints 是否保留事件投递顺序。
    队列在成功排空后由 flush_ready_checkpoints 重置。
    _seen_keys 为去重集合：已存在于该集合中的 (assistant_call_id, sequence)
    在二次调用 queue_commit 时将被丢弃。
    """

    commits: list["StaticCommit"] = field(default_factory=list)
    status_lines: list[_PendingStatusLine] = field(default_factory=list)
    _seen_keys: set[tuple[str, int]] = field(default_factory=set)


class TerminalOutputCoordinator:
    """流式路径的集中式静态区域提交调度器。

    生命周期：

        coord = TerminalOutputCoordinator()
        coord.begin_dynamic_app()
        # ... 事件到达，reducer 与 session 调用 queue_commit
        await coord.flush_ready_checkpoints()  # 安全地在提示符上方输出
        coord.end_dynamic_app()                # 动态应用已退出

    begin/end 标记是具备实际行为的逻辑：
    当动态应用标记为活跃时，刷新操作通过 prompt_toolkit 的 run_in_terminal 进行路由，
    防止 Rich 输出与动态渲染器发生时序竞争。
    """

    def __init__(self) -> None:
        self._queue = _CommitQueue()
        self._in_dynamic_app: bool = False
        self._flush_lock = asyncio.Lock()

    # --- 生命周期 ---

    def begin_dynamic_app(self) -> None:
        """标记 prompt_toolkit 预览应用正在运行。

        测试无需调用此方法即可保证正确性（有无标记刷新行为一致），
        但记录该标记以便未来的门禁逻辑直接使用而无需更改此 API。
        """

        self._in_dynamic_app = True

    def end_dynamic_app(self) -> None:
        """标记 prompt_toolkit 预览应用已退出。"""

        self._in_dynamic_app = False

    # --- 队列 ---

    def queue_commit(
        self,
        commit: "StaticCommit",
        *,
        workspace: Path | None = None,
    ) -> None:
        """暂存检查点提交。

        调用此方法不会向 stdout 输出，提交追加至内部队列，
        仅在调用 flush_ready_checkpoints 时刷新输出。
        重复提交（相同的 assistant_call_id 与 sequence）会被静默丢弃，
        因此重复重试相同提交不会在回滚历史中重复打印。
        committed 是单向标志，在提交写入后置位；
        已提交的项在二次刷新时为空操作。

        workspace 会转发给静态区域工具渲染器用于 tool_result 提交，
        以便针对具体工具的格式化器解析路径并选取合适的摘要行。
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
        """暂存单次静态文本行（例如取消通知）。

        该行在 flush_ready_checkpoints 期间逐字追加到静态控制台。
        保留在协调器中而非直接调用 print_static，以确保流式路径不会绕过协调器。
        """

        self._queue.status_lines.append(_PendingStatusLine(text=text))

    # --- 刷新 ---

    async def flush_ready_checkpoints(self) -> None:
        """将队列中所有检查点写入静态区域。

        写入顺序具有确定性：

        1. StaticCommit 载荷按照入队顺序输出：assistant markdown 通过
           print_assistant_markdown 打印；工具结果提交使用
           print_tool_result 并提取底层 ToolExecutionResult 中的 call_id。
        2. 状态行（例如取消通知）。

        刷新完成后清空已排空的提交队列；
        同时清空 _seen_keys 以便后续刷新周期能够接收新提交。
        各个提交上的 committed 标志不会被修改，它们位于状态侧的
        StaticCommit 上并由 reducer 的提交路径更新。
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
        """将已排空的检查点批次写入 stdout。"""

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

    # --- 测试与流式会话使用的检查辅助方法 ---

    def pending_commit_count(self) -> int:
        return len(self._queue.commits)

    def pending_status_line_count(self) -> int:
        return len(self._queue.status_lines)

__all__ = ["TerminalOutputCoordinator"]
