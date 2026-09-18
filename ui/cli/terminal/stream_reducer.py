"""CLI 流式事件 reducer（execplan §M2）。

这是唯一的事件→状态转换入口。它是纯函数:

- 不 import :mod:`ui.cli.terminal.static_output`
- 不创建 Rich ``Console``、不调用 ``print_tool_result`` 或
  ``print_assistant_markdown``
- 不退出 prompt_toolkit app、不 sleep、不读取文件

reducer 的职责是把 ``AgentEvent`` 折叠进 ``CliStreamUiState``:

``assistant_delta``
  追加到 ``streaming_text``,并把当前 assistant 文本归入事件
  metadata 中的 ``assistant_call_id``;reducer 维护
  ``current_assistant_call_id`` 和 ``current_model_turn_index``。
  reducer 用 ``_require_attribution`` 校验事件携带稳定 id;缺失
  则把 state 切到 error 并把诊断信息记到 ``error_text``。
``tool_call_delta``
  工具名只在当前工具还未命名时写入 tools 字典里第一个没有名字的
  工具( reducer 不维护一个单独的 "current tool name" 字段)。
  这一事件也要求携带 ``assistant_call_id`` 以便回链。
``tool_call_ready``
  把工具加入 ``tools`` 字典,状态 ``queued``;记录
  ``tool_call_id → assistant_call_id`` 和
  ``tool_call_id → declared_index``(在同一 ``assistant_call_id``
  内严格递增)。当至少有一个 queued 工具时把 ``stream_mode``
  切到 ``tool_input``。
``tool_started``
  把对应 call_id 的工具状态改为 ``running``,记录 ``tool_name``。
  工具事件同样要求稳定 id。
``tool_progress``
  更新对应 call_id 工具的 progress 字段。当有工具正在 running
  时把 ``stream_mode`` 切到 ``tool_running``。
``tool_result``
  从 ``tools`` 移除对应工具,把 ``ToolExecutionResult`` 暂存到
  ``completed_tool_results_by_assistant[assistant_call_id][declared_index]``,
  然后调用 :func:`release_ready_tool_result_commits` 把
  ``assistant_call_id`` 下从最小未提交 index 开始连续完成的
  结果包装成 :class:`StaticCommit` 加入 ``pending_static_commits``。
  这一逻辑保证“后声明但先完成”的工具不能越过前面的工具。
``assistant_message_completed``
  把当前 ``streaming_text`` 包装成
  :class:`StaticCommit` (``kind="assistant_markdown"``),把
  ``streaming_text`` 清空,把 ``current_assistant_call_id`` 也
  清空,让下一轮 assistant 文本从空动态区继续。设置
  ``assistant_completed = True``。当没有 active tools 时把
  ``stream_mode`` 切到 ``awaiting_model``。
``completed``
  收尾:把可能尚未提交的 assistant markdown 包装成 commit
  并清空 ``streaming_text``,设置 ``turn_completed = True``,
  ``stream_mode = completed``。它不再承担“把所有历史统一写出”
  的职责。
``transition`` / ``interaction_started``
  当前不修改 state。``transition`` 是模型层事实,UI 不消费。
``error``
  把 ``event.text`` 写入 ``error_text`` 并把 ``stream_mode`` 切
  到 ``error``。

reducer 的设计动机是测试友好:可以构造一系列 ``AgentEvent`` 然后
断言 ``CliStreamUiState`` 的字段变化而不启动任何终端。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.stream_events import event_requires_attribution
from ui.cli.terminal.stream_state import (
    CliStreamUiState,
    CommitKind,
    StaticCommit,
    StreamMode,
    StreamingToolUseState,
    ToolStatus,
)

if TYPE_CHECKING:
    from core.stream_events import AgentEvent
    from services.tools.types import ToolExecutionResult


# 计数器，用于为到达时缺少可识别 call_id 的 tool_result 事件生成合成调用 ID。
# 该计数器为进程本地变量，仅作为最终保底手段，确保两个未识别的结果绝不会归并到同一个 ID。
_unknown_call_counter = 0


def _next_unknown_call_id() -> str:
    """为未识别的结果返回唯一的合成调用 ID。"""

    global _unknown_call_counter
    _unknown_call_counter += 1
    return f"unknown_call_{_unknown_call_counter}"


def _resolve_call_id(metadata: dict[str, Any], result: Any) -> str | None:
    """从事件元数据或结果中提取规范的 call_id。

    无可用 ID 时返回 None；reducer 随后会回退使用合成 ID，
    以确保结果仍能进入 pending_static_commits。
    """

    call_id = metadata.get("tool_call_id")
    if call_id:
        return str(call_id)
    if result is not None:
        result_id = getattr(result, "tool_call_id", None)
        if result_id:
            return str(result_id)
    return None


def _preview_tool_input(input_obj: Any, *, limit: int = 120) -> str:
    """为工具调用的输入构建受限的单行预览。

    与 ui.cli.terminal.static_output._summarize_arguments 对应，
    但独立实现以便 reducer 预渲染输入而不依赖静态输出模块。
    """

    if not isinstance(input_obj, dict):
        return ""
    parts: list[str] = []
    for key, value in input_obj.items():
        rendered = _preview_value(value)
        parts.append(f"{key}={rendered}")
        if sum(len(part) for part in parts) > limit:
            break
    text = " ".join(parts)
    if len(text) > limit:
        return text[: max(limit - 1, 0)] + "…"
    return text


def _preview_value(value: Any, *, inner_limit: int = 40) -> str:
    if isinstance(value, str):
        compact = " ".join(value.split())
        if len(compact) > inner_limit:
            return f'"{compact[: inner_limit - 1]}…"'
        return f'"{compact}"'
    if isinstance(value, (list, tuple)):
        return f"<{len(value)} items>"
    if isinstance(value, dict):
        return f"<{len(value)} keys>"
    return str(value)


def _set_mode(state: CliStreamUiState, mode: str) -> None:
    """切换 state.stream_mode，除非轮次已经结束。

    completed 和 error 为终态，一旦进入便不会被后续事件覆盖
    （例如 completed 之后滞后到达的 assistant delta）。
    其他状态转换尽力而为，在竞争时倾向于更活跃的模式。
    """

    if state.stream_mode in (StreamMode.COMPLETED, StreamMode.ERROR):
        return
    state.stream_mode = mode


def _mark_tool_error(state: CliStreamUiState, call_id: str) -> None:
    """将 call_id 标记为错误并从活跃集合中移除。

    reducer 当前不会接收到单独的 tool_error 事件，
    错误通道即 error 事件本身（同时会切换 stream_mode）。
    保留此辅助函数是为了逻辑对称性，并为未来工具级错误事件预留统一处理入口。
    """

    tool = state.tools.get(call_id)
    if tool is not None:
        tool.status = ToolStatus.ERROR


def _require_attribution(
    state: CliStreamUiState,
    event: "AgentEvent",
) -> tuple[str, int] | None:
    """返回 (assistant_call_id, model_turn_index)，失败时返回 None。

    Reducer 用它强制要求：归属于某次模型调用的事件必须携带稳定 id。
    失败时不抛异常，而是切到 error 模式，把诊断写入 error_text，
    让 coordinator / 端到端测试能够察觉实现错误。
    """

    metadata = getattr(event, "metadata", None) or {}
    call_id = metadata.get("assistant_call_id")
    turn_index = metadata.get("model_turn_index")
    if not call_id or turn_index is None:
        state.error_text = (
            f"missing stable attribution on {event.type} "
            "(assistant_call_id and model_turn_index are required)"
        )
        _set_mode(state, StreamMode.ERROR)
        return None
    return str(call_id), int(turn_index)


def _set_current_attribution(
    state: CliStreamUiState,
    call_id: str,
    turn_index: int,
) -> None:
    """更新 reducer 对当前 assistant 消息的记录。

    当 assistant_call_id 改变时（下一轮模型调用）同步重置声明
    顺序游标和下一个可释放 index，这样不同 assistant message 下
    的工具不会跨边界串味。

    注意：streaming_text 不会被本函数清空，reducer 显式
    在 assistant_message_completed 时清空它，这样可以保证同
    一 call_id 内的多次 assistant_delta 不会互相冲掉。
    assistant_committed 在 call_id 切换时重置，以允许新的
    assistant message 提交 checkpoint。
    """

    if state.current_assistant_call_id != call_id:
        state.current_assistant_call_id = call_id
        # 不同 assistant_call_id 的声明 index 独立计算,确保 tool
        # 归到正确的 message。
        state.tool_call_declared_index = {}
        state.tool_call_to_assistant_call_id = {}
        state.completed_tool_results_by_assistant.pop(call_id, None)
        state.next_tool_result_index_to_release_by_assistant[call_id] = 0
        # 切换 assistant_call_id 时也清零 ``assistant_committed``
        # 和 ``streaming_text``,让新 message 从空动态区开始。
        # 注意:这意味着新 call_id 的第一次 ``assistant_delta`` 之
        # 前,streaming_text 已经是空,所以 delta 会从空开始累加。
        state.streaming_text = ""
        state.assistant_committed = False
    state.current_model_turn_index = turn_index


def _next_declared_index(state: CliStreamUiState, call_id: str) -> int:
    """为工具调用分配严格单调递增的声明索引。

    同一 assistant_call_id 下，每次 tool_call_ready 都让
    tool_call_declared_index[call_id] 自增 1；不同 call 之间
    互不干扰，后到达的 tool_call_ready 永远得到更大的 index。

    我们以 state.tools 字典的插入顺序作为声明顺序的事实来源
    （Python 3.7+ 保证 dict 有序），所以 call_id 的 declared_index
    等于它在 tools 字典里出现的位置（0-based）。这避免维护
    next index 计数器，确保声明顺序和实际展示顺序一致。
    """

    current = state.tool_call_declared_index.get(call_id)
    if current is not None:
        return current
    keys = list(state.tools.keys())
    if call_id not in keys:
        # tool_started 之后才到达 reducer 的话,tools 里没有它,
        # 分配一个新的末尾 index。
        declared = [
            v for v in state.tool_call_declared_index.values() if isinstance(v, int)
        ]
        next_index = (max(declared) + 1) if declared else 0
    else:
        next_index = keys.index(call_id)
    state.tool_call_declared_index[call_id] = next_index
    return next_index


def release_ready_tool_result_commits(
    state: CliStreamUiState,
    assistant_call_id: str,
) -> list[StaticCommit]:
    """释放同一 assistant_call_id 下从最小未提交 index 开始连续完成的结果。

    防止“后声明但先完成”的工具越过前面的工具。返回本次新释放的
    StaticCommit 列表（已追加到 pending_static_commits）。
    """

    bucket = state.completed_tool_results_by_assistant.setdefault(
        assistant_call_id, {}
    )
    next_index = state.next_tool_result_index_to_release_by_assistant.get(
        assistant_call_id, 0
    )
    released: list[StaticCommit] = []
    while next_index in bucket:
        result = bucket.pop(next_index)
        commit = StaticCommit(
            sequence=state.next_sequence(),
            kind=CommitKind.TOOL_RESULT,
            payload=result,
            model_turn_index=state.current_model_turn_index or 0,
            assistant_call_id=assistant_call_id,
            declared_index=next_index,
        )
        state.pending_static_commits.append(commit)
        released.append(commit)
        next_index += 1
    state.next_tool_result_index_to_release_by_assistant[
        assistant_call_id
    ] = next_index
    return released


def queue_assistant_checkpoint(
    state: CliStreamUiState,
    text: str,
    *,
    assistant_call_id: str,
    model_turn_index: int,
) -> StaticCommit | None:
    """构建 assistant_markdown 检查点并追加至队列。

    空白文本不会产生 commit。reducer 在 assistant_message_completed
    时调用此辅助函数，确保 streaming_text 提交后立即清空。
    """

    if not text:
        return None
    commit = StaticCommit(
        sequence=state.next_sequence(),
        kind=CommitKind.ASSISTANT_MARKDOWN,
        payload=text,
        model_turn_index=model_turn_index,
        assistant_call_id=assistant_call_id,
    )
    state.pending_static_commits.append(commit)
    return commit


def reduce_stream_event(state: CliStreamUiState, event: "AgentEvent") -> None:
    """就地将一个 AgentEvent 折叠进 state 中。

    该函数是纯函数：绝不向 stdout 输出，绝不构造 Rich Console，
    绝不抛出异常。未知事件类型将被忽略，以便未来版本新增
    AgentEventType 时不会导致动态区域崩溃。
    """

    event_type = getattr(event, "type", None)
    metadata = getattr(event, "metadata", None) or {}

    if event_type == "assistant_delta":
        # 强制要求稳定归属,否则进入 error 状态而不是静默依赖上一
        # 条事件,这样端到端测试可以察觉。
        attribution = _require_attribution(state, event)
        if attribution is None:
            return
        call_id, turn_index = attribution
        # ``_set_current_attribution`` 在 call_id 切换时清空
        # ``streaming_text``,让新 message 从空动态区开始。同一
        # call_id 的连续 delta 不影响 streaming_text。
        _set_current_attribution(state, call_id, turn_index)
        delta = getattr(event, "text", "") or ""
        if delta:
            state.streaming_text += delta
        if state.stream_mode not in (StreamMode.COMPLETED, StreamMode.ERROR):
            _set_mode(state, StreamMode.RESPONDING)
        return

    if event_type == "tool_call_delta":
        attribution = _require_attribution(state, event)
        if attribution is None:
            return
        call_id, turn_index = attribution
        _set_current_attribution(state, call_id, turn_index)
        name = str(metadata.get("name") or "")
        if not name:
            return
        for tool in state.tools.values():
            if not tool.tool_name:
                tool.tool_name = name
                break
        return

    if event_type == "tool_call_ready":
        attribution = _require_attribution(state, event)
        if attribution is None:
            return
        call_id, turn_index = attribution
        _set_current_attribution(state, call_id, turn_index)
        tool_call = metadata.get("tool_call")
        tool_name = getattr(tool_call, "name", "") if tool_call else ""
        raw_call_id = getattr(tool_call, "id", None) if tool_call else None
        if not raw_call_id:
            # 没有 id 就不能跨 tool_started/tool_result 跟踪,reducer
            # 静默忽略;未来这里会升级成 error 状态以便测试发现。
            return
        tool_call_id = str(raw_call_id)
        input_obj = getattr(tool_call, "input", None) or {}
        existing = state.tools.get(tool_call_id)
        if existing is None:
            state.tools[tool_call_id] = StreamingToolUseState(
                call_id=tool_call_id,
                tool_name=tool_name or "",
                status=ToolStatus.QUEUED,
                input_preview=_preview_tool_input(input_obj),
            )
        else:
            if tool_name:
                existing.tool_name = tool_name
            if not existing.input_preview:
                existing.input_preview = _preview_tool_input(input_obj)
            existing.status = ToolStatus.QUEUED
        # 记录工具声明顺序和所属 assistant message。
        state.tool_call_to_assistant_call_id[tool_call_id] = call_id
        _next_declared_index(state, tool_call_id)
        _set_mode(state, StreamMode.TOOL_INPUT)
        return

    if event_type == "tool_started":
        attribution = _require_attribution(state, event)
        if attribution is None:
            return
        call_id, turn_index = attribution
        _set_current_attribution(state, call_id, turn_index)
        raw_call_id = metadata.get("tool_call_id")
        if not raw_call_id:
            return
        tool_call_id = str(raw_call_id)
        tool_name = str(metadata.get("tool_name") or "")
        existing = state.tools.get(tool_call_id)
        if existing is None:
            state.tools[tool_call_id] = StreamingToolUseState(
                call_id=tool_call_id,
                tool_name=tool_name,
                status=ToolStatus.RUNNING,
            )
            state.tool_call_to_assistant_call_id[tool_call_id] = call_id
            _next_declared_index(state, tool_call_id)
        else:
            existing.status = ToolStatus.RUNNING
            if tool_name and not existing.tool_name:
                existing.tool_name = tool_name
        _set_mode(state, StreamMode.TOOL_RUNNING)
        return

    if event_type == "tool_progress":
        attribution = _require_attribution(state, event)
        if attribution is None:
            return
        call_id, turn_index = attribution
        _set_current_attribution(state, call_id, turn_index)
        raw_call_id = metadata.get("tool_call_id")
        if not raw_call_id:
            return
        tool_call_id = str(raw_call_id)
        existing = state.tools.get(tool_call_id)
        if existing is not None:
            existing.progress = str(
                metadata.get("message") or metadata.get("text") or ""
            )
        _set_mode(state, StreamMode.TOOL_RUNNING)
        return

    if event_type == "tool_result":
        attribution = _require_attribution(state, event)
        if attribution is None:
            return
        call_id, turn_index = attribution
        _set_current_attribution(state, call_id, turn_index)
        result = getattr(event, "result", None)
        raw_call_id = _resolve_call_id(metadata, result)
        if raw_call_id is None:
            if result is None:
                return
            tool_call_id = _next_unknown_call_id()
            declared_index = _next_declared_index(state, tool_call_id)
        else:
            tool_call_id = str(raw_call_id)
            declared_index = state.tool_call_declared_index.get(tool_call_id)
            if declared_index is None:
                # 没有声明就直接到达 result:把它放到声明顺序末尾,
                # 让它能被后面到达的 result 正常释放。
                declared_index = _next_declared_index(state, tool_call_id)
        state.tools.pop(tool_call_id, None)
        bucket = state.completed_tool_results_by_assistant.setdefault(
            call_id, {}
        )
        bucket[declared_index] = result
        release_ready_tool_result_commits(state, call_id)
        if state.has_active_tools():
            _set_mode(state, StreamMode.TOOL_RUNNING)
        else:
            _set_mode(state, StreamMode.AWAITING_MODEL)
        return

    if event_type == "assistant_message_completed":
        attribution = _require_attribution(state, event)
        if attribution is None:
            return
        call_id, turn_index = attribution
        _set_current_attribution(state, call_id, turn_index)
        # 立刻把当前 assistant 文本提交到 checkpoint 队列;reducer
        # 自己清空 ``streaming_text``,让下一轮 assistant 文本从空
        # 动态区继续。``assistant_committed`` 标志让 ``completed``
        # 收尾时不再重复 emit。
        if state.streaming_text and not state.assistant_committed:
            queue_assistant_checkpoint(
                state,
                state.streaming_text,
                assistant_call_id=call_id,
                model_turn_index=turn_index,
            )
            state.assistant_committed = True
        state.streaming_text = ""
        state.assistant_completed = True
        if not state.has_active_tools():
            _set_mode(state, StreamMode.AWAITING_MODEL)
        return

    if event_type == "completed":
        attribution = _require_attribution(state, event)
        if attribution is None:
            return
        call_id, turn_index = attribution
        _set_current_attribution(state, call_id, turn_index)
        # ``streaming_text`` 通常已经在 ``assistant_message_completed``
        # 时被清空并 emit commit;``completed`` 收尾时不应该再 emit
        # 一次。但有些 provider 不发 ``assistant_message_completed``
        # 直接发 ``completed`` (通常携带完整 text);此时
        # ``streaming_text`` 为空、``assistant_committed`` 也还是
        # False,我们要用 event text 兜底提交一次 commit。
        if state.assistant_committed:
            state.streaming_text = ""
        else:
            text = getattr(event, "text", "") or ""
            if text:
                state.streaming_text = text
            if state.streaming_text:
                queue_assistant_checkpoint(
                    state,
                    state.streaming_text,
                    assistant_call_id=call_id,
                    model_turn_index=turn_index,
                )
                state.assistant_committed = True
            state.streaming_text = ""
        state.turn_completed = True
        _set_mode(state, StreamMode.COMPLETED)
        return

    if event_type == "error":
        message = getattr(event, "text", "") or "error"
        state.error_text = message
        _set_mode(state, StreamMode.ERROR)
        return

    if event_type == "interaction_started":
        # 轮次起始。无需折叠，流式会话在首个事件到达前已重置状态对象。
        return

    if event_type == "transition":
        # 仅限模型的转换标记，UI 予以忽略。
        return

    # 未知事件类型有意忽略，保证前向兼容性：
    # 未来新增的 AgentEventType 字面量不会使动态区域崩溃。


__all__ = [
    "queue_assistant_checkpoint",
    "reduce_stream_event",
    "release_ready_tool_result_commits",
]
