"""把 UiMessage 渲染为 Rich renderable（Text 或 Group）。

排版层级遵循 DESIGN.md：You 灰色、MiniAgent 蓝色加粗、reasoning 灰斜体、
工具行缩进两格并带终态字符、queued 整行灰斜体加橙色标注。样式只引用
`theme.RICH_STYLES` 中的命名样式。
"""

from __future__ import annotations

from uuid import UUID
from pathlib import Path

from rich.console import Group
from rich.padding import Padding
from rich.text import Text

from ..projection import MessageLifecycle, UiMessage, UiPart
from ..render_cache import MarkdownBlockCache
from .reasoning import reasoning_preview
from .tool import ToolPresentationRegistry, build_tool_presentation_registry, first_line_excerpt

# 每个 text part 一个块缓存，键为 (message_id, part 序号)。
MarkdownCaches = dict[tuple[UUID, int], MarkdownBlockCache]


def render_message(
    message: UiMessage,
    *,
    reasoning_expanded: bool = False,
    md_caches: MarkdownCaches | None = None,
    tool_presentations: ToolPresentationRegistry | None = None,
) -> Text | Group:
    if message.role.value == "user":
        return _render_user(message)
    if message.role.value == "tool":
        return _render_tool_message(message)
    return _render_assistant(
        message,
        reasoning_expanded=reasoning_expanded,
        md_caches=md_caches,
        tool_presentations=tool_presentations or build_tool_presentation_registry(Path.cwd()),
    )


def _render_user(message: UiMessage) -> Text:
    queued = message.lifecycle is MessageLifecycle.QUEUED
    output = Text()
    output.append("You\n", style="ui.label.user")
    body_style = "ui.queued" if queued else ""
    for part in message.parts:
        output.append(part.content + "\n", style=body_style)
    if queued:
        output.append("排队中\n", style="ui.queued.tag")
    if message.lifecycle is MessageLifecycle.FAILED:
        output.append("未完成\n", style="ui.error")
    return output


def _render_assistant(
    message: UiMessage,
    *,
    reasoning_expanded: bool,
    md_caches: MarkdownCaches | None,
    tool_presentations: ToolPresentationRegistry,
) -> Group:
    blocks: list = [Text("MiniAgent", style="ui.label.agent")]
    for index, part in enumerate(message.parts):
        if part.kind == "reasoning":
            blocks.append(_render_reasoning(part, expanded=reasoning_expanded))
        elif part.kind == "tool":
            blocks.append(_render_tool_part(part, expanded=reasoning_expanded, registry=tool_presentations))
        elif part.kind == "text":
            blocks.append(_render_text_part(message, index, part, md_caches))
    if message.lifecycle is MessageLifecycle.FAILED:
        blocks.append(Text("未完成", style="ui.error"))
    return Group(*blocks)


def _render_reasoning(part: UiPart, *, expanded: bool) -> Text | Padding:
    if expanded:
        return Padding(Text(part.content, style="ui.reasoning"), pad=(0, 0, 0, 2))
    return Text("▸ " + reasoning_preview(part.content), style="ui.reasoning")


def _render_text_part(
    message: UiMessage,
    index: int,
    part: UiPart,
    md_caches: MarkdownCaches | None,
) -> Group:
    if md_caches is None:
        return MarkdownBlockCache().render(part.content)
    key = (message.message_id, index)
    cache = md_caches.get(key)
    if cache is None:
        cache = MarkdownBlockCache()
        md_caches[key] = cache
    return cache.render(part.content)


def _render_tool_part(
    part: UiPart,
    *,
    expanded: bool,
    registry: ToolPresentationRegistry,
) -> Text | Group:
    presentation = registry.present(
        name=part.name or "工具",
        arguments=part.content,
        result=part.result,
        result_output=part.result_output,
        is_error=part.is_error,
    )
    line = Text("  ")
    if part.result is None:
        line.append("▸ ", style="ui.tool")
    elif part.is_error:
        line.append("✗ ", style="ui.error")
    else:
        line.append("✓ ", style="ui.success")
    line.append(part.name or "工具", style="ui.tool")
    if presentation.summary:
        line.append("  " + presentation.summary, style="ui.tool.summary")
    if part.result is not None and part.is_error:
        excerpt = first_line_excerpt(presentation.body)
        if excerpt:
            line.append("\n    " + excerpt, style="ui.error")
    blocks: list = [line]
    blocks.extend(Padding(preview, pad=(0, 0, 0, 4)) for preview in presentation.preview_lines)
    if expanded and presentation.body:
        blocks.append(Padding(Text(presentation.body, style="ui.meta"), pad=(0, 0, 0, 4)))
    if len(blocks) > 1:
        return Group(*blocks)
    return line


def _render_tool_message(message: UiMessage) -> Text:
    """无法配对到 ToolUse 的孤立工具结果，降级为独立状态行。"""
    output = Text()
    for part in message.parts:
        glyph, style = ("✗", "ui.error") if part.is_error else ("✓", "ui.success")
        output.append("  " + glyph + " ", style=style)
        output.append("工具结果", style="ui.tool")
        if part.is_error:
            excerpt = first_line_excerpt(part.content)
            if excerpt:
                output.append("  " + excerpt, style="ui.error")
        output.append("\n")
    return output
