"""CLI 动态区渲染 view（execplan §M2）。

本模块把 :class:`CliStreamUiState` 翻译成 prompt_toolkit 可显示的
``ANSI`` / ``FormattedText``，不产生 I/O，不修改 state，不调用静态打
印函数。

布局策略（自上而下）：

1. **Assistant preview 段**：用
   :func:`ui.cli.terminal.markdown_rendering.render_cached_markdown`
   渲染 ``streaming_text`` 尾部若干行，与 tool panel 用一个空行隔
   开。空行是视觉边界 — 旧 ``turn_render_state`` 把两段拼在同一个
   ``out_lines`` 里再 ``\\n.join``，结果在窄终端上可能让工具行紧
   贴 assistant 文本尾部。
2. **Tool panel 段**：最多 ``VISIBLE_ACTIVE_TOOL_LIMIT`` 条工具行，
   超过的折叠为 ``…  N more tools running`` 摘要行。
3. **Status line 段**（独立函数 :func:`render_status_fragments`）：
   始终由 ``stream_mode`` + ``active_tool_count`` 推导，绝不会在
   仍有运行工具时显示裸 ``thinking…`` 提示。

view 故意保持简单：它只读 state。Reducer 写、Coordinator 读 → 提交，
view 读 → 渲染，循环单向。``StreamingSession`` 把三个组件串起来。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from prompt_toolkit.formatted_text import ANSI, FormattedText

from ui.cli.terminal.markdown_rendering import render_cached_markdown
from ui.cli.terminal.queue import QueuedInput
from ui.cli.terminal.stream_state import (
    StreamMode,
    ToolStatus,
    VISIBLE_ACTIVE_TOOL_LIMIT,
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


def _format_active_tool_line(tool: "StreamingToolUseState") -> str:
    """Format a single active-tool row for the dynamic region.

    Three visible states match the reference implementation:

    - ``queued`` — clean ``tool: <name> (queued)`` line, no input
      preview. The full preview lives in the static banner printed
      when the tool actually starts.
    - ``running`` with progress — progress text replaces the input
      preview so the user sees the latest status.
    - ``running`` without progress — fall back to the input preview.
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
    """Bound the rendered text length so a long queued command does
    not steal the dynamic region from assistant/tool output.

    Whitespace is collapsed to single spaces so embedded newlines
    cannot break the row layout. Trailing ellipsis is appended when
    truncation actually happens.
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
    """Format a queued preview block for the dynamic region.

    Returns a list of plain lines. The first line is a leading
    ``queued:`` header; subsequent lines are one per visible
    queued input, and a final ``…  +N more queued`` summary line
    is appended when there are more entries than ``visible_limit``.

    An empty / ``None`` input returns an empty list so callers can
    simply ``extend`` the body without conditional branching.

    The function never raises. It is also pure — it only reads
    the iterable's contents and does not mutate any state.
    """

    if queue_items is None:
        return []
    # Materialise the snapshot so the iterable can be re-iterated
    # safely (e.g. tests that pass a generator).
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
    state: "CliStreamUiState",
    *,
    width: int,
    active_tool_limit: int = VISIBLE_ACTIVE_TOOL_LIMIT,
    queued_inputs: Iterable[QueuedInput] | None = None,
) -> ANSI:
    """Render the in-flight turn state to bounded ANSI for the dynamic region.

    Layout (top to bottom):

    1. The last :data:`ASSISTANT_TAIL_MAX_LINES` lines of the
       accumulated assistant text (markdown-rendered when possible).
       The assistant segment is separated from the tool panel by a
       single blank line so the user can tell where the body ends
       and the tool list begins — even on narrow terminals.
    2. Up to ``active_tool_limit`` active tools, each on its own line.
    3. A ``…  N more tools running`` line if there are more.
    4. Queued preview (when ``queued_inputs`` is non-empty): a
       ``queued:`` header, one line per visible entry, and an
       overflow summary if more entries exist than the preview
       limit. The queued preview is purely a dynamic-region
       signal — :class:`TerminalOutputCoordinator` is the only
       component allowed to write to the static scrollback, and
       this function never invokes it.
    5. Errors (``state.error_text``) are rendered as a single
       red-tinted line at the bottom of the body so the user never
       loses the last error message when the dynamic region is
       erased.

    The function never raises. Markdown render failures fall back
    to plain text through the existing
    :func:`render_cached_markdown` helper.
    """

    out_lines: list[str] = []

    # 1) Assistant tail — render the full text through the cache so
    # unchanged prefix lines are not re-lexed.
    if state.streaming_text:
        all_lines = render_cached_markdown(state.streaming_text, width=max(width, 20))
        if len(all_lines) > ASSISTANT_TAIL_MAX_LINES:
            out_lines.append("  …")
            out_lines.extend(all_lines[-ASSISTANT_TAIL_MAX_LINES:])
        else:
            out_lines.extend(all_lines)

    # 2) Active tools. Insert a blank separator so the tool panel
    # never visually fuses with the assistant text tail — that was
    # the layout bug the ExecPlan targets.
    visible_tools = state.visible_active_tools(limit=active_tool_limit)
    if visible_tools:
        if out_lines:
            # The assistant segment is present; insert a blank line
            # to create a stable visual boundary. We only need one
            # blank line; the next section starts cleanly below.
            out_lines.append("")
        for tool in visible_tools:
            out_lines.append(_format_active_tool_line(tool))
        overflow = state.overflow_active_count(limit=active_tool_limit)
        if overflow > 0:
            out_lines.append(f"  …  {overflow} more tools running")

    # 3) Queued preview (running-turn input box). Inserted only
    # when there is at least one queued input, so an idle turn
    # shows no extra noise. The header ``queued:`` plus the
    # bullet lines come from :func:`render_queued_inputs`.
    queued_lines = render_queued_inputs(queued_inputs)
    if queued_lines:
        if out_lines:
            out_lines.append("")
        out_lines.extend(queued_lines)

    # 4) Error tail (if any). The error line is its own short
    # paragraph so it stays visible when the rest of the body is
    # cleared at turn end.
    if state.error_text:
        if out_lines:
            out_lines.append("")
        out_lines.append(f"! {state.error_text}")

    if not out_lines:
        return ANSI("")
    return ANSI("\n".join(out_lines))


def render_status_fragments(state: "CliStreamUiState") -> FormattedText:
    """Render the bottom status line for the dynamic region.

    The status text is derived from ``state.stream_mode`` and the
    active tool bucket. Crucially, the legacy "bare thinking… when
    tools are running" bug is gone — if any tool is queued or
    running, the status line shows ``tool: <name>`` (or
    ``tools: <count>`` when more than one is visible) instead of
    the misleading idle indicator.
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
        # ``REQUESTING`` and any residual modes fall back to the
        # original idle indicator — the user sees a familiar
        # "thinking…" prompt while we wait for the model's first
        # token.
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