"""CLI 动态区渲染 view。

本模块把 CliStreamUiState 翻译成 prompt_toolkit 可显示的
ANSI / FormattedText，不产生 I/O，不修改 state，不调用静态打印函数。

布局策略（自上而下）：

1. Assistant preview 段：用
   ui.cli.terminal.markdown_rendering.render_cached_markdown
   渲染 streaming_text 尾部若干行，与 tool panel 用一个空行隔开。
2. Tool panel 段：最多 VISIBLE_ACTIVE_TOOL_LIMIT 条工具行，
   超过的折叠为省略摘要行。
3. Status line 段（独立函数 render_status_fragments）：
   始终由 stream_mode + active_tool_count 推导，绝不会在
   仍有运行工具时显示裸 thinking 提示。

view 故意保持简单：它只读 state。Reducer 写、Coordinator 读与提交，
view 读与渲染，循环单向。StreamingSession 把三个组件串联起来。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import ANSI, FormattedText

from ui.cli.terminal.markdown_rendering import render_cached_markdown
from ui.cli.terminal.queue import QueuedInput
from ui.cli.terminal.stream_state import (
    VISIBLE_ACTIVE_TOOL_LIMIT,
    StreamMode,
    ToolStatus,
)

if TYPE_CHECKING:
    from ui.cli.terminal.stream_state import (
        CliStreamUiState,
        StreamingToolUseState,
    )


#: 动态区 assistant preview 段的最大可见行数。完整文本总是会通过
#: :class:`TerminalOutputCoordinator` 提交到静态 scrollback，preview
#: 只用来让用户在 turn 还未结束时看见尾部。
ASSISTANT_TAIL_MAX_LINES = 5
#: 运行中 queued preview 的最大可见条目数。超过会折叠成
#: ``…  +N more queued`` 摘要行。设置小一些保证动态区不抢屏。
QUEUED_PREVIEW_LIMIT = 3
#: queued preview 单条文本的最大字符数。超过会截断并加省略号。
QUEUED_PREVIEW_TEXT_LIMIT = 60


def _format_active_tool_line(tool: StreamingToolUseState) -> str:
    """为动态区域格式化单条活跃工具行。

    三种可见状态与参考实现对应：

    - queued：整洁的 tool: <name> (queued) 行，无输入预览。完整预览位于工具实际启动时打印的静态横幅中。
    - running 且带进度：进度文本替换输入预览，使用户看到最新状态。
    - running 且无进度：回退显示输入预览。
    """

    label = tool.tool_name or "tool"
    if tool.status == ToolStatus.QUEUED:
        return f"tool: {label} (queued)"
    if tool.status == ToolStatus.RUNNING and tool.progress:
        return f"tool: {label} {tool.progress}"
    if tool.input_preview:
        return f"tool: {label} {tool.input_preview}"
    return f"tool: {label}"


def _truncate_for_preview(text: str, *, limit: int = QUEUED_PREVIEW_TEXT_LIMIT) -> str:
    """限制渲染文本长度，避免冗长排队命令占据动态区域中属于 assistant / 工具输出的空间。

    空白字符折叠为单个空格，防止内嵌换行符破坏行布局。实际发生截断时追加末尾省略号。
    """

    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 1)].rstrip() + "…"


def render_queued_inputs(
    queue_items: Iterable[QueuedInput] | None,
    *,
    visible_limit: int = QUEUED_PREVIEW_LIMIT,
) -> list[str]:
    """为动态区域格式化排队预览块。

    返回纯文本行列表。首行为前导 queued: 表头；
    后续行为每个可见排队输入，当条目数超出 visible_limit 时，
    在末尾追加一条省略摘要行。

    输入为空或 None 时返回空列表，以便调用方无需条件分支即可直接 extend 正文。

    本函数绝不抛出异常，也是纯函数：仅读取可迭代对象内容，不修改任何状态。
    """

    if queue_items is None:
        return []
    # 将快照实例化，以便可迭代对象可被安全重复迭代（例如测试传入的生成器）。
    items = tuple(item for item in queue_items if item.visible)
    if not items:
        return []
    lines = ["queued:"]
    visible = items[:visible_limit]
    for item in visible:
        lines.append(f"  - {_truncate_for_preview(item.text)}")
    overflow = len(items) - len(visible)
    if overflow > 0:
        lines.append(f"  …  +{overflow} more queued")
    return lines


def render_stream_body_ansi(
    state: CliStreamUiState,
    *,
    width: int,
    active_tool_limit: int = VISIBLE_ACTIVE_TOOL_LIMIT,
    queued_inputs: Iterable[QueuedInput] | None = None,
) -> ANSI:
    """将正在进行的轮次状态渲染为用于动态区域的受限 ANSI。

    布局（自上而下）：

    1. 累积 assistant 文本的最后 ASSISTANT_TAIL_MAX_LINES 行（尽可能通过 Markdown 渲染）。
       assistant 片段与工具面板用单个空行隔开，以便用户清晰辨识正文结束和工具列表开始的位置（即便在窄终端上也是如此）。
    2. 最多 active_tool_limit 个活跃工具，每个独占一行。
    3. 若有更多工具，追加一条折叠摘要行。
    4. 排队预览（当 queued_inputs 非空时）：包含 queued: 表头、每个可见条目一行，
       以及超出预览上限时的溢出摘要。排队预览纯粹是动态区域信号，
       仅允许 TerminalOutputCoordinator 写入静态回滚历史，本函数绝不调用它。
    5. 错误信息（state.error_text）在正文底部渲染为单行红调文本，
       确保动态区域被擦除时用户绝不会丢失最后一条错误消息。

    本函数绝不抛出异常。Markdown 渲染失败时通过现有的 render_cached_markdown 辅助函数回退为纯文本。
    """

    out_lines: list[str] = []

    # 1) Assistant 尾部：通过缓存全量渲染文本，避免重复解析未改变的前缀行。
    if state.streaming_text:
        all_lines = render_cached_markdown(state.streaming_text, width=max(width, 20))
        if len(all_lines) > ASSISTANT_TAIL_MAX_LINES:
            out_lines.append("  …")
            out_lines.extend(all_lines[-ASSISTANT_TAIL_MAX_LINES:])
        else:
            out_lines.extend(all_lines)

    # 2) 活跃工具。插入空行分隔符，使工具面板在视觉上绝不与 assistant 文本尾部粘连。
    visible_tools = state.visible_active_tools(limit=active_tool_limit)
    if visible_tools:
        if out_lines:
            # assistant 片段存在，插入空行建立稳定的视觉边界。只需一个空行，后续段落干净地从下方开始。
            out_lines.append("")
        for tool in visible_tools:
            out_lines.append(_format_active_tool_line(tool))
        overflow = state.overflow_active_count(limit=active_tool_limit)
        if overflow > 0:
            out_lines.append(f"  …  {overflow} more tools running")

    # 3) 排队预览（运行中输入框）。仅在存在至少一个排队输入时插入，使空闲轮次不显示多余杂音。表头加上条目行来自 render_queued_inputs。
    queued_lines = render_queued_inputs(queued_inputs)
    if queued_lines:
        if out_lines:
            out_lines.append("")
        out_lines.extend(queued_lines)

    # 4) 错误尾部（如有）。错误行独立成简短段落，在轮次结束正文其余部分被清空时依然可见。
    if state.error_text:
        if out_lines:
            out_lines.append("")
        out_lines.append(f"! {state.error_text}")

    if not out_lines:
        return ANSI("")
    return ANSI("\n".join(out_lines))


def render_status_fragments(state: CliStreamUiState) -> FormattedText:
    """为动态区域渲染底部状态行。

    状态文本由 state.stream_mode 与活跃工具池推导。
    关键在于移除了工具运行时的空闲提示问题：若任意工具处于排队或运行中，
    状态行显示工具运行状态，而不是误导性的空闲指示符。
    """

    active_count = state.active_tool_count()
    first_active = next(
        (
            tool
            for tool in state.tools.values()
            if tool.status in (ToolStatus.QUEUED, ToolStatus.RUNNING) and tool.tool_name
        ),
        None,
    )

    if state.stream_mode == StreamMode.ERROR:
        label = "error"
        style = "class:stream-status-error"
    elif state.stream_mode == StreamMode.COMPLETED:
        label = "done"
        style = "class:stream-status-done"
    elif active_count > 1:
        label = f"tools: {active_count} running"
        style = "class:stream-status-tool"
    elif first_active is not None:
        if first_active.status == ToolStatus.QUEUED:
            label = f"tool: {first_active.tool_name} (queued)"
        else:
            label = f"tool: {first_active.tool_name}"
        style = "class:stream-status-tool"
    elif state.stream_mode == StreamMode.AWAITING_MODEL:
        label = "awaiting model…"
        style = "class:stream-status"
    elif state.stream_mode == StreamMode.RESPONDING:
        label = "responding…"
        style = "class:stream-status"
    else:
        # REQUESTING 及其他残留模式回退为原始空闲指示符，在等待模型首个 token 时向用户展示熟悉的 thinking 提示。
        label = "thinking…"
        style = "class:stream-status"

    return FormattedText(
        [
            ("class:stream-prefix", "onecode> "),
            (style, f"{label}  (Esc to cancel)"),
        ]
    )


__all__ = [
    "ASSISTANT_TAIL_MAX_LINES",
    "QUEUED_PREVIEW_LIMIT",
    "QUEUED_PREVIEW_TEXT_LIMIT",
    "render_queued_inputs",
    "render_status_fragments",
    "render_stream_body_ansi",
]
