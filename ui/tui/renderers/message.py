"""Message rendering: projection value types to Rich renderables.

Layout follows the reference TUI: user labels are muted, agent labels are
accent-bold, tool lines are indented with a terminal-state glyph, and queued
messages are muted-italic with an accent tag. Only named styles from
:mod:`ui.tui.theme` are used.
"""

from __future__ import annotations

from rich.console import Group
from rich.padding import Padding
from rich.text import Text

from ui.tui.conversation.render_cache import MarkdownBlockCache
from ui.tui.projection_types import (
    LIFECYCLE_COMPLETED,
    LIFECYCLE_DRAFT,
    MESSAGE_ROLE_ASSISTANT,
    MESSAGE_ROLE_SYSTEM,
    MESSAGE_ROLE_USER,
    PART_ATTACHMENT,
    PART_TEXT,
    PART_TOOL,
    UiMessage,
    UiPart,
)
from ui.tui.renderers.tool import (
    ToolPresentationRegistry,
    build_tool_presentation_registry,
    first_line_excerpt,
)

#: One Markdown cache per ``(message_id, text part index)``.
MarkdownCaches = dict[tuple[str, int], MarkdownBlockCache]


def render_message(
    message: UiMessage,
    *,
    details_expanded: bool = False,
    md_caches: MarkdownCaches | None = None,
    tool_presentations: ToolPresentationRegistry | None = None,
) -> Text | Group:
    if message.role == MESSAGE_ROLE_USER:
        return _render_user(message)
    if message.role == MESSAGE_ROLE_SYSTEM:
        return _render_system(message)
    return _render_assistant(
        message,
        details_expanded=details_expanded,
        md_caches=md_caches,
        tool_presentations=tool_presentations
        or build_tool_presentation_registry(),
    )


def _render_user(message: UiMessage) -> Text:
    queued = message.lifecycle == LIFECYCLE_DRAFT
    output = Text()
    output.append("You\n", style="ui.label.user")
    body_style = "ui.queued" if queued else ""
    for part in message.parts:
        if part.kind == PART_ATTACHMENT:
            output.append("附件: ", style="ui.attachment")
            output.append(part.content + "\n", style="ui.attachment")
        else:
            output.append(part.content + "\n", style=body_style)
    if queued:
        output.append("排队中\n", style="ui.queued.tag")
    if message.lifecycle == "failed":
        output.append("未完成\n", style="ui.error")
    return output


def _render_system(message: UiMessage) -> Text:
    output = Text()
    output.append("System\n", style="ui.label.user")
    for part in message.parts:
        output.append(part.content + "\n", style="ui.meta")
    return output


def _render_assistant(
    message: UiMessage,
    *,
    details_expanded: bool,
    md_caches: MarkdownCaches | None,
    tool_presentations: ToolPresentationRegistry,
) -> Group:
    blocks: list = [Text("OneCode", style="ui.label.agent")]
    for index, part in enumerate(message.parts):
        if part.kind == PART_TOOL:
            blocks.append(
                _render_tool_part(
                    part, expanded=details_expanded, registry=tool_presentations
                )
            )
        elif part.kind == PART_ATTACHMENT:
            blocks.append(Text("附件: " + part.content, style="ui.attachment"))
        elif part.kind == PART_TEXT:
            blocks.append(
                _render_text_part(message, index, part, md_caches)
            )
    if message.lifecycle == "failed":
        blocks.append(Text("未完成", style="ui.error"))
    return Group(*blocks)


def _render_text_part(
    message: UiMessage,
    index: int,
    part: UiPart,
    md_caches: MarkdownCaches | None,
) -> Group:
    if md_caches is None:
        return MarkdownBlockCache().render(part.content, finalized=True)
    key = (message.message_id, index)
    cache = md_caches.get(key)
    if cache is None:
        cache = MarkdownBlockCache()
        md_caches[key] = cache
    finalized = message.lifecycle != LIFECYCLE_DRAFT
    return cache.render(part.content, finalized=finalized)


def _render_tool_part(
    part: UiPart,
    *,
    expanded: bool,
    registry: ToolPresentationRegistry,
) -> Text | Group:
    presentation = registry.present(
        name=part.tool_name or "工具",
        tool_input=part.tool_input,
        result_preview=part.result_preview,
        is_error=part.is_error,
    )
    line = Text("  ")
    if part.result_preview is None and not part.is_error:
        line.append("▸ ", style="ui.tool")
    elif part.is_error:
        line.append("✗ ", style="ui.error")
    else:
        line.append("✓ ", style="ui.success")
    line.append(part.tool_name or "工具", style="ui.tool")
    summary = presentation.summary
    if summary:
        line.append("  " + summary, style="ui.tool.summary")
    if part.is_error:
        excerpt = first_line_excerpt(presentation.body)
        if excerpt:
            line.append("\n    " + excerpt, style="ui.error")
    blocks: list = [line]
    blocks.extend(
        Padding(preview, pad=(0, 0, 0, 4))
        for preview in presentation.preview_lines
    )
    if expanded and presentation.body:
        blocks.append(
            Padding(Text(presentation.body, style="ui.meta"), pad=(0, 0, 0, 4))
        )
    if len(blocks) > 1:
        return Group(*blocks)
    return line


__all__ = ["MarkdownCaches", "render_message"]
