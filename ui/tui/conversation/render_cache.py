"""流式 Markdown 块渲染缓存。

Rich 的 ``Markdown`` 在构造时进行语法解析，因此缓存其实例即是缓存解析结果。
在流式输出期间，仅需重新解析未闭合的末尾片段；已闭合的块直接复用。
当正文最终提交完毕时，重新全量解析一次原始文本，
因为基于空行的分块机制无法覆盖所有跨块语法结构（如松散列表、链接引用定义等）。
正确性优先于增量优化。
"""

from __future__ import annotations

from typing import ClassVar

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import CodeBlock, Markdown
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text

from ui.tui.theme import MARKDOWN_CODE_THEME, SURFACE

_FENCE_MARKERS = ("```", "~~~")


class _InsetCodeBlock(CodeBlock):
    """代码块：表面背景色并在左侧缩进两个字符宽度。"""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        code = str(self.text).rstrip()
        syntax = Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            padding=1,
            background_color=SURFACE,
        )
        yield Padding(syntax, (0, 0, 0, 2))


class OneCodeMarkdown(Markdown):
    """将 TUI 设计令牌应用于围栏代码块和缩进代码块的 Markdown 渲染器。"""

    elements: ClassVar[dict[str, type]] = {
        **Markdown.elements,
        "code_block": _InsetCodeBlock,
        "fence": _InsetCodeBlock,
    }


def _scan(source: str) -> tuple[list[str], str]:
    """将 ``source`` 切分为已闭合的代码块列表与未闭合的末尾片段。

    围栏代码块外部的空行视为块边界；围栏内部的空行属于代码内容。
    分别追踪不同的围栏标记，确保 ``~~~`` 代码块不会被 ```` ``` ```` 误闭合，反之亦然。
    """

    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in source.splitlines(keepends=True):
        stripped = line.strip()
        marker = next(
            (item for item in _FENCE_MARKERS if stripped.startswith(item)), None
        )
        if marker is not None:
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif stripped.startswith(fence_marker):
                in_fence = False
            current.append(line)
        elif not stripped and not in_fence:
            if current:
                blocks.append("".join(current))
                current = []
        else:
            current.append(line)
    tail = "".join(current)
    return blocks, tail


def split_closed_blocks(source: str) -> tuple[str, str]:
    """返回 ``(closed_prefix, unclosed_tail)`` 源码文本元组。"""

    blocks, tail = _scan(source)
    return "".join(blocks), tail


class MarkdownBlockCache:
    """针对单个持续增长的文本分片的增量渲染缓存。"""

    def __init__(self) -> None:
        self._closed: list[str] = []
        self._blocks: list[Markdown] = []

    def render(self, source: str, *, finalized: bool = False) -> Group:
        if finalized:
            self._closed = []
            self._blocks = []
            return Group(OneCodeMarkdown(source, code_theme=MARKDOWN_CODE_THEME))

        blocks, tail = _scan(source)
        if blocks[: len(self._closed)] != self._closed:
            self._closed = []
            self._blocks = []
        for block in blocks[len(self._blocks) :]:
            self._blocks.append(OneCodeMarkdown(block, code_theme=MARKDOWN_CODE_THEME))
        self._closed = blocks
        renderables: list[Markdown | Text] = [*self._blocks]
        if tail.strip():
            renderables.append(OneCodeMarkdown(tail, code_theme=MARKDOWN_CODE_THEME))
        if not renderables:
            renderables.append(Text(""))
        return Group(*renderables)

    def clear(self) -> None:
        self._closed = []
        self._blocks = []


__all__ = [
    "MarkdownBlockCache",
    "OneCodeMarkdown",
    "split_closed_blocks",
]
