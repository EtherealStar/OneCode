"""CLI 流式 UI 状态模型（execplan §M1）。

本模块定义一个 turn 内（从用户提交到 completed/error 退出）CLI 动态区
的临时状态模型。状态与 ``core/loop.py``、``MessageStore`` 等模型事实
解耦 — 它是 UI 渲染的事实来源，但不是上下文或 transcript 的事实来源。

设计动机：

- **替换** 旧 ``ui.cli.terminal.turn_render_state.TurnRenderState`` 的混合
  职责。旧类同时承载"动态区状态 + 已完成但未提交到 scrollback 的工具
  结果",让 reducer、view、flush 三者互相耦合,flush 阶段直接调用
  ``print_tool_result`` 写 stdout,从而造成动态区擦除和静态写入竞争。
- **借鉴** ``docs/references/ui/screens/REPL.tsx`` 的状态分层：把
  ``streamingText``、``streamingToolUses``、``streamMode``、助手定稿
  标志、turn 完成标志分到独立字段,让 reducer、view、coordinator 三
  个职责之间通过 state object 通信。
- **纯数据**。state 不会 import Rich、prompt_toolkit、static_output,
  不会在自身方法里产生 I/O。任何写入 stdout 的动作都走
  :mod:`ui.cli.terminal.output_coordinator`。

Checkpoint 提交模型 (execplan §M1)
------------------------------------

state 持有两类待提交给 coordinator 的 ``StaticCommit``:

- ``assistant_markdown`` : 某次 assistant message 的最终 markdown。
  它的提交边界是 ``assistant_message_completed``。提交后,
  ``streaming_text`` 必须被清空,允许下一轮 assistant 文本从空
  动态区继续流式显示。
- ``tool_result`` : 工具结果。提交顺序以模型声明工具的顺序为准,
  而不是以完成时间为准 — 并发安全工具可能后声明先完成,必须等
  同一 ``assistant_call_id`` 下从最小未提交 index 开始连续完成
  才能提交下一个结果。

每个 commit 必须携带稳定的 ``assistant_call_id`` 和
``model_turn_index``,这是 checkpoint 与 assistant message 的
UI 归属回链。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.stream_events import AgentEvent
    from services.tools.types import ToolExecutionResult


#: ``stream_mode`` 字段的合法值集合。把它们写成模块级常量便于测试和
#: 文档检索；不引入 enum 是因为 dataclass 默认值直接用字符串更易读。
class StreamMode:
    """The current phase of the in-flight turn.

    Mirrors the reference REPL's ``streamMode`` field — a single
    string the view reads to decide what to draw in the body and
    status row.
    """

    REQUESTING = "requesting"  # 等待模型响应
    RESPONDING = "responding"  # 模型正在流式产生 assistant 文本
    TOOL_INPUT = "tool_input"  # 收到 tool_call_ready 但还没 tool_started
    TOOL_RUNNING = "tool_running"  # 至少一个工具在 running
    AWAITING_MODEL = "awaiting_model"  # 工具都完成, 等待模型下一轮
    COMPLETED = "completed"  # turn 结束, 不再接收事件
    ERROR = "error"  # 出现 error, 等待 coordinator 收尾


#: 工具生命周期状态。
class ToolStatus:
    """Lifecycle status for one tool call in the active bucket."""

    QUEUED = "queued"  # tool_call_ready 已收到, 等待 tool_started
    RUNNING = "running"  # tool_started 已收到, 等待 tool_result
    COMPLETED = "completed"  # tool_result 已收到
    ERROR = "error"  # 工具失败, 不会再产生 result


#: 动态区视图中同时显示的最大活跃工具数量；超过则折叠为一条
#: ``…  N more tools running`` 摘要行。
VISIBLE_ACTIVE_TOOL_LIMIT = 3


@dataclass
class StreamingToolUseState:
    """In-flight tool call tracked by the reducer.

    Distinct from :class:`StaticCommit` — this represents a
    tool call the model announced (or the runtime started) and that
    has not yet produced a result. The view reads ``status`` and
    ``progress`` to render the tool panel.
    """

    call_id: str
    tool_name: str = ""
    status: str = ToolStatus.QUEUED
    input_preview: str = ""
    progress: str = ""


#: ``StaticCommit`` 提交类型。
class CommitKind:
    """The kind of static-region commit a :class:`StaticCommit` represents.

    Currently two flavours: the completed assistant message body, and
    a tool result line. The coordinator prints them with the same
    Rich pipeline but they are stored separately so the dynamic
    region can show them in different positions in the scrollback
    even when interleaved.
    """

    ASSISTANT_MARKDOWN = "assistant_markdown"
    TOOL_RESULT = "tool_result"


@dataclass
class StaticCommit:
    """A checkpoint-ready payload waiting to be committed to scrollback.

    Carries the stable ``assistant_call_id`` and ``model_turn_index``
    so the coordinator (and any future re-render) can match the
    commit to the assistant message that produced it. ``sequence``
    is a process-local monotonically increasing id that the
    coordinator uses to deduplicate commits across restages; the
    pair ``(assistant_call_id, sequence)`` is the unique identity.

    ``declared_index`` is the tool's declaration order within the
    same ``assistant_call_id``; it is ``None`` for assistant
    markdown commits. The reducer uses it to release tool result
    commits strictly in declaration order.
    """

    sequence: int
    kind: str
    payload: Any
    model_turn_index: int
    assistant_call_id: str
    declared_index: int | None = None
    committed: bool = False

    @property
    def is_tool_result(self) -> bool:
        return self.kind == CommitKind.TOOL_RESULT

    @property
    def is_assistant_markdown(self) -> bool:
        return self.kind == CommitKind.ASSISTANT_MARKDOWN


#: 旧 ``CompletedToolCommit`` 的别名,保留是为了不破坏正在用它的测
#: 试;``pending_static_commits`` 列表现在直接持有
#: :class:`StaticCommit`,见 :class:`CliStreamUiState`。
CompletedToolCommit = StaticCommit


@dataclass
class CliStreamUiState:
    """All in-memory state for one turn's dynamic region.

    Constructed once at the start of a turn, mutated by
    :func:`reduce_stream_event` in :mod:`ui.cli.terminal.stream_reducer`,
    read by :mod:`ui.cli.terminal.stream_view` (to render the dynamic
    region) and :class:`TerminalOutputCoordinator` (to decide when to
    flush pending commits).
    """

    #: Concatenated assistant text from every ``assistant_delta`` so far.
    #: When the assistant message is committed to the static region,
    #: the reducer clears this string so the next round of assistant
    #: text starts streaming in a fresh dynamic region.
    streaming_text: str = ""
    #: 当前 assistant message 的稳定归属 id。每次进入新模型调用
    #: 时,reducer 用 ``AgentEvent.metadata["assistant_call_id"]``
    #: 覆盖这个值;reducer 也用它把 tool_result 归到正确的 message。
    current_assistant_call_id: str = ""
    #: 当前 assistant message 对应的 model turn 序号。
    current_model_turn_index: int | None = None
    #: Active tool calls by ``call_id`` (insertion order preserved).
    tools: dict[str, StreamingToolUseState] = field(default_factory=dict)
    #: 工具 call_id → 所属 ``assistant_call_id`` 的映射。reducer 在
    #: 收到 ``tool_call_ready`` 时填充,``tool_result`` 时用来找到
    #: 提交入口。
    tool_call_to_assistant_call_id: dict[str, str] = field(default_factory=dict)
    #: 工具 call_id → 在所属 ``assistant_call_id`` 下的声明顺序。
    #: 0-based,严格按 ``tool_call_ready`` 出现顺序递增。
    tool_call_declared_index: dict[str, int] = field(default_factory=dict)
    #: 同一 ``assistant_call_id`` 下,按 ``declared_index`` 收集的
    #: 已完成 tool_result bucket。reducer 决定哪些可以按声明顺序释
    #: 放成 :class:`StaticCommit`。
    completed_tool_results_by_assistant: dict[str, dict[int, "ToolExecutionResult"]] = field(
        default_factory=dict
    )
    #: 同一 ``assistant_call_id`` 下,下一个可释放的
    #: ``declared_index``。reducer 自增。
    next_tool_result_index_to_release_by_assistant: dict[str, int] = field(
        default_factory=dict
    )
    #: 下一个可分配的 ``StaticCommit.sequence``。
    next_commit_sequence: int = 0
    #: 等待提交到 scrollback 的 checkpoint 队列。reducer 负责
    #: append,coordinator 负责 drain。包含 assistant_markdown
    #: 和 tool_result 两类。
    pending_static_commits: list[StaticCommit] = field(default_factory=list)
    #: Current turn phase. Updated by the reducer; read by the view to
    #: decide what the body and status line should show.
    stream_mode: str = StreamMode.REQUESTING
    #: Error text from the most recent ``error`` event. Empty when no
    #: error has been seen this turn.
    error_text: str = ""
    #: Set when ``assistant_message_completed`` arrives. The view
    #: uses this to allow early preview finalisation when no tools
    #: are still running.
    assistant_completed: bool = False
    #: Set when ``completed`` arrives. The coordinator uses it as a
    #: trigger for the final markdown commit; the view uses it to
    #: lock the status line to "completed".
    turn_completed: bool = False
    #: Set when ``assistant_message_completed`` has already emitted a
    #: checkpoint for the current assistant message. ``completed`` 的
    #: 收尾路径用这个标志来避免在同一条 assistant message 上重复
    #: emit checkpoint。
    assistant_committed: bool = False

    # --- helpers used by the reducer and view ---------------------------

    def has_active_tools(self) -> bool:
        """``True`` when at least one tool is queued or running."""

        return any(
            tool.status in (ToolStatus.QUEUED, ToolStatus.RUNNING)
            for tool in self.tools.values()
        )

    def active_tool_count(self) -> int:
        """How many tools are currently queued or running."""

        return sum(
            1
            for tool in self.tools.values()
            if tool.status in (ToolStatus.QUEUED, ToolStatus.RUNNING)
        )

    def uncommitted_commits(self) -> list[StaticCommit]:
        """Return commits that have not yet been flushed to scrollback.

        The coordinator calls this to know what to print next. A
        list (rather than a generator) keeps the call deterministic
        and easy to test.
        """

        return [c for c in self.pending_static_commits if not c.committed]

    def ready_tool_results_for(
        self,
        assistant_call_id: str,
    ) -> list[StaticCommit]:
        """Return ready tool result commits for a given assistant call id.

        A tool result commit is "ready" once it has been staged
        (declared) and is the next-in-line for release: its
        ``declared_index`` matches
        ``next_tool_result_index_to_release_by_assistant`` for that
        assistant call. The list is returned in declaration order.
        """

        next_index = self.next_tool_result_index_to_release_by_assistant.get(
            assistant_call_id, 0
        )
        ready: list[StaticCommit] = []
        for commit in self.pending_static_commits:
            if commit.committed:
                continue
            if commit.assistant_call_id != assistant_call_id:
                continue
            if not commit.is_tool_result:
                continue
            if commit.declared_index == next_index:
                ready.append(commit)
        return ready

    def visible_active_tools(self, *, limit: int = VISIBLE_ACTIVE_TOOL_LIMIT) -> list[StreamingToolUseState]:
        """Return up to ``limit`` active tools for the dynamic panel.

        Tools with an empty ``tool_name`` (the rare case where the
        model emits a tool call without a name) are filtered out so
        the dynamic region never shows a blank line. Insertion order
        is preserved so the user sees a stable list while tools run.
        """

        ordered = [
            tool
            for tool in self.tools.values()
            if tool.tool_name
            and tool.status in (ToolStatus.QUEUED, ToolStatus.RUNNING)
        ]
        if len(ordered) <= limit:
            return ordered
        return ordered[:limit]

    def overflow_active_count(self, *, limit: int = VISIBLE_ACTIVE_TOOL_LIMIT) -> int:
        """How many active tools are folded into the ``+N more`` line."""

        ordered = [
            tool
            for tool in self.tools.values()
            if tool.tool_name
            and tool.status in (ToolStatus.QUEUED, ToolStatus.RUNNING)
        ]
        if len(ordered) <= limit:
            return 0
        return len(ordered) - limit

    def next_sequence(self) -> int:
        """Allocate the next ``StaticCommit.sequence`` value."""

        seq = self.next_commit_sequence
        self.next_commit_sequence += 1
        return seq


__all__ = [
    "CliStreamUiState",
    "CommitKind",
    "CompletedToolCommit",
    "StaticCommit",
    "StreamMode",
    "StreamingToolUseState",
    "ToolStatus",
    "VISIBLE_ACTIVE_TOOL_LIMIT",
]
