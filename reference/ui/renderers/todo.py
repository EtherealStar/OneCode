"""TodoList 的紧凑只读渲染（textual-ui.md §7）。

三种状态必须可区分且不只靠色相：pending `○`、in_progress `▸`、completed `✓`；
in_progress 是唯一的视觉焦点（青色 glyph + 正文色内容），其余沉到 text-muted。
UI 不修改、排序或补全列表，按原顺序渲染。
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text

from ...tools.todo_write.tool import TodoItem

_GLYPHS = {"pending": "○", "in_progress": "▸", "completed": "✓"}
_LABELS = {"pending": "待处理", "in_progress": "进行中", "completed": "已完成"}


def _append_item(output: Text, item: TodoItem, *, include_label: bool) -> None:
    glyph = _GLYPHS.get(item.status, "○")
    prefix = f"{glyph} [{_LABELS.get(item.status, '待处理')}] " if include_label else glyph + " "
    if item.status == "in_progress":
        output.append(prefix, style="ui.todo.active")
        output.append(item.content, style="ui.todo.active.text")
    elif item.status == "completed":
        output.append(prefix, style="ui.todo.done.glyph")
        output.append(item.content, style="ui.todo.done")
    else:
        output.append(prefix, style="ui.todo.pending")
        output.append(item.content, style="ui.todo.pending")


def render_todo_list(items: Sequence[TodoItem]) -> Text:
    output = Text()
    for index, item in enumerate(items):
        if index:
            output.append("  ", style="ui.todo")
        _append_item(output, item, include_label=False)
    return output


def render_todo_tool_preview(items: Sequence[TodoItem]) -> tuple[Text, ...]:
    """按调用时顺序渲染历史 todo_write 的完整三态列表。"""
    lines: list[Text] = []
    for item in items:
        line = Text()
        _append_item(line, item, include_label=True)
        lines.append(line)
    return tuple(lines)
