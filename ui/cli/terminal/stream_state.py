"""CLI 流式 UI 状态模型（execplan §M1）。

本模块定义一个 turn 内（从用户提交到 completed/error 退出）CLI 动态区
的临时状态模型。状态与 ``core/loop.py``、``MessageStore`` 等模型事实
解耦 — 它是 UI 渲染的事实来源，但不是上下文或 transcript 的事实来源。

设计动机：

- 替换旧 ui.cli.terminal.turn_render_state.TurnRenderState 的混合
  职责。旧类同时承载"动态区状态 + 已完成但未提交到 scrollback 的工具
  结果",让 reducer、view、flush 三者互相耦合,flush 阶段直接调用
  print_tool_result 写 stdout,从而造成动态区擦除和静态写入竞争。
- 借鉴 docs/references/ui/screens/REPL.tsx 的状态分层：把
  streamingText、streamingToolUses、streamMode、助手定稿
  标志、turn 完成标志分到独立字段,让 reducer、view、coordinator 三
  个职责之间通过 state object 通信。
- 纯数据。state 不会 import Rich、prompt_toolkit、static_output,
  不会在自身方法里产生 I/O。任何写入 stdout 的动作都走
  ui.cli.terminal.output_coordinator。

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
    """当前正在进行的轮次的阶段。

    与参考实现的 streamMode 字段对应：视图读取的单个字符串，用于决定在正文与状态行中绘制什么内容。
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
    """活跃池中单个工具调用的生命周期状态。"""

    QUEUED = "queued"  # tool_call_ready 已收到, 等待 tool_started
    RUNNING = "running"  # tool_started 已收到, 等待 tool_result
    COMPLETED = "completed"  # tool_result 已收到
    ERROR = "error"  # 工具失败, 不会再产生 result


#: 动态区视图中同时显示的最大活跃工具数量；超过则折叠为一条
#: ``…  N more tools running`` 摘要行。
VISIBLE_ACTIVE_TOOL_LIMIT = 3


@dataclass
class StreamingToolUseState:
    """reducer 跟踪的运行中工具调用。

    与 StaticCommit 不同：这代表模型已声明（或运行时已启动）但尚未产生结果的工具调用。
    视图读取 status 与 progress 来渲染工具面板。
    """

    call_id: str
    tool_name: str = ""
    status: str = ToolStatus.QUEUED
    input_preview: str = ""
    progress: str = ""


#: ``StaticCommit`` 提交类型。
class CommitKind:
    """StaticCommit 代表的静态区域提交类型。

    当前有两种类型：已完成的 assistant 消息正文，以及工具结果行。
    协调器使用相同的 Rich 流水线打印它们，但它们分开存储，
    以便动态区域即使在交织时也能在回滚历史的不同位置显示它们。
    """

    ASSISTANT_MARKDOWN = "assistant_markdown"
    TOOL_RESULT = "tool_result"


@dataclass
class StaticCommit:
    """等待提交到回滚历史的就绪检查点载荷。

    携带稳定的 assistant_call_id 和 model_turn_index，
    以便协调器（及未来的重渲染）将提交与产生它的 assistant 消息相匹配。
    sequence 为进程本地单调递增的 ID，协调器用它在重新暂存时对提交去重；
    二元组 (assistant_call_id, sequence) 即唯一身份标识。

    declared_index 是同一 assistant_call_id 下工具的声明顺序；
    对于 assistant markdown 提交为 None。reducer 使用它严格按声明顺序释放工具结果提交。
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
    """单次轮次动态区域的所有内存中状态。

    在轮次开始时构建一次，由 ui.cli.terminal.stream_reducer 中的
    reduce_stream_event 变更，由 ui.cli.terminal.stream_view 读取（渲染动态区域），
    并由 TerminalOutputCoordinator 读取（决定何时刷新待处理提交）。
    """

    #: 迄今为止所有 assistant_delta 拼接而成的 assistant 文本。
    #: 当 assistant 消息提交到静态区域时，reducer 清空此字符串，
    #: 以便下一轮 assistant 文本在全新的动态区域开始流式传输。
    streaming_text: str = ""
    #: 当前 assistant message 的稳定归属 id。每次进入新模型调用
    #: 时,reducer 用 ``AgentEvent.metadata["assistant_call_id"]``
    #: 覆盖这个值;reducer 也用它把 tool_result 归到正确的 message。
    current_assistant_call_id: str = ""
    #: 当前 assistant message 对应的 model turn 序号。
    current_model_turn_index: int | None = None
    #: 按 call_id 索引的活跃工具调用（保留插入顺序）。
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
    #: 当前轮次阶段。由 reducer 更新；视图读取以决定正文与状态行展示什么。
    stream_mode: str = StreamMode.REQUESTING
    #: 最近一次 error 事件的错误文本。本轮次未遇到错误时为空。
    error_text: str = ""
    #: 在 assistant_message_completed 到达时置位。没有工具在运行时，视图据此允许提前结束预览。
    assistant_completed: bool = False
    #: 在 completed 到达时置位。协调器将其作为最终 markdown 提交的触发信号；视图据此将状态行锁定为“completed”。
    turn_completed: bool = False
    #: Set when ``assistant_message_completed`` has already emitted a
    #: checkpoint for the current assistant message. ``completed`` 的
    #: 收尾路径用这个标志来避免在同一条 assistant message 上重复
    #: emit checkpoint。
    assistant_committed: bool = False

    # --- reducer 和 view 使用的辅助方法 ---

    def has_active_tools(self) -> bool:
        """当至少有一个工具处于排队或运行中时返回 True。"""

        return any(
            tool.status in (ToolStatus.QUEUED, ToolStatus.RUNNING)
            for tool in self.tools.values()
        )

    def active_tool_count(self) -> int:
        """当前处于排队或运行中的工具数量。"""

        return sum(
            1
            for tool in self.tools.values()
            if tool.status in (ToolStatus.QUEUED, ToolStatus.RUNNING)
        )

    def uncommitted_commits(self) -> list[StaticCommit]:
        """返回尚未刷新至回滚历史的提交项。

        协调器调用此方法以获知接下来需要打印的内容。
        返回列表（而非生成器）以保持调用的确定性与易测性。
        """

        return [c for c in self.pending_static_commits if not c.committed]

    def ready_tool_results_for(
        self,
        assistant_call_id: str,
    ) -> list[StaticCommit]:
        """返回给定 assistant 调用 ID 的就绪工具结果提交项。

        工具结果提交项在被暂存（声明）且轮到其释放时被视为“就绪”：
        其 declared_index 与该 assistant 调用的
        next_tool_result_index_to_release_by_assistant 相匹配。列表按声明顺序返回。
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
        """为动态面板返回最多 limit 个活跃工具。

        tool_name 为空的工具（模型发出无名工具调用的罕见情况）会被过滤掉，
        确保动态区域绝不显示空行。保留插入顺序以使用户在工具运行时看到稳定的列表。
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
        """折叠进 +N more 行中的活跃工具数量。"""

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
        """分配下一个 StaticCommit.sequence 值。"""

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
