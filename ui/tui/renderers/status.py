"""状态栏渲染：左侧显示产品/工作区/模型，右侧显示运行状态。"""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len, get_character_cell_size
from rich.text import Text

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@dataclass(frozen=True, slots=True)
class RunState:
    """右侧运行状态。``kind`` 取值为 ``running`` / ``queued`` / ``error``。"""

    kind: str
    count: int = 0


def render_status_left(
    workspace_root: str, model: str | None, *, max_width: int | None = None
) -> Text:
    model_text = model or "未配置模型"
    fixed_width = len("OneCode ·  · ") + len(model_text)
    cwd_width = None if max_width is None else max(1, max_width - fixed_width)
    workspace = (
        _middle_ellipsize(workspace_root, cwd_width)
        if cwd_width is not None
        else workspace_root
    )
    output = Text(style="ui.meta")
    output.append("OneCode", style="ui.label.agent")
    output.append(" · ")
    output.append(workspace, style="ui.meta")
    output.append(" · ")
    output.append(model_text, style="ui.status.model" if model else "ui.meta")
    return output


def _middle_ellipsize(value: str, width: int) -> str:
    if cell_len(value) <= width:
        return value
    if width <= 1:
        return "…"
    left_width = (width - 1 + 1) // 2
    right_width = width - 1 - left_width
    return _take_cells(value, left_width) + "…" + _take_cells(
        value, right_width, reverse=True
    )


def _take_cells(value: str, width: int, *, reverse: bool = False) -> str:
    characters = reversed(value) if reverse else iter(value)
    selected: list[str] = []
    used = 0
    for character in characters:
        size = get_character_cell_size(character)
        if used + size > width:
            break
        selected.append(character)
        used += size
    if reverse:
        selected.reverse()
    return "".join(selected)


def render_run_state(state: RunState | None, frame: int = 0) -> Text:
    if state is None:
        return Text("")
    if state.kind == "running":
        spinner = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
        return Text(f"{spinner} 运行中", style="ui.tool")
    if state.kind == "queued":
        return Text(f"排队 {state.count}", style="ui.queued.tag")
    return Text("✗ 出错", style="ui.error")


__all__ = [
    "SPINNER_FRAMES",
    "RunState",
    "render_run_state",
    "render_status_left",
]
