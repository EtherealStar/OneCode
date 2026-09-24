"""流式 Markdown 的块级解析缓存（textual-ui.md §9.1）。

普通文本在流式期间就渲染 Markdown。`Markdown` 实例在构造时完成解析，
因此缓存实例即缓存解析结果：已闭合 block 只解析一次，每次增量只重新
解析末尾未闭合 block。渲染交给 Textual 按实际宽度现场排版，终端宽度
变化不需要缓存失效。
"""

from __future__ import annotations

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import CodeBlock, Markdown
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text

from .theme import MARKDOWN_CODE_THEME, SURFACE

_FENCE_MARKERS = ("```", "~~~")


class _InsetCodeBlock(CodeBlock):
    """代码块：surface 底 + 左缩进 2 格（DESIGN.md Typography 3）。

    Rich 默认让 Syntax 使用代码主题自带背景（如 one-dark 的 #282c34），
    这里固定为 token 的 surface，并把整条色带从正文左缘缩进 2 格。
    """

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
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


class MiniAgentMarkdown(Markdown):
    """应用视觉 token 的 Markdown：仅替换围栏与缩进代码块的渲染。"""

    elements = {**Markdown.elements, "code_block": _InsetCodeBlock, "fence": _InsetCodeBlock}


def _scan(source: str) -> tuple[list[str], str]:
    """把 source 切成（已闭合块列表, 未闭合尾部）。

    围栏（``` 或 ~~~）外的空行是块边界；围栏内的空行属于代码内容。
    尾部是不以空行结尾的剩余原文，即流式期间尚未闭合的块。
    """
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in source.splitlines(keepends=True):
        stripped = line.strip()
        marker = next((m for m in _FENCE_MARKERS if stripped.startswith(m)), None)
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
    """返回（已闭合前缀, 未闭合尾部）原文切分，供需要原文边界的调用方使用。"""
    blocks, tail = _scan(source)
    return "".join(blocks), tail


class MarkdownBlockCache:
    """同一文本 part 的增量渲染缓存。

    流式文本只会向后增长，因此闭合块前缀单调递增时只追加解析新增块；
    前缀失配（内容被替换）时整体重建。
    """

    def __init__(self) -> None:
        self._closed: list[str] = []
        self._blocks: list[Markdown] = []

    def render(self, source: str) -> Group:
        blocks, tail = _scan(source)
        if blocks[: len(self._closed)] != self._closed:
            self._closed = []
            self._blocks = []
        for block in blocks[len(self._blocks):]:
            self._blocks.append(MiniAgentMarkdown(block, code_theme=MARKDOWN_CODE_THEME))
        self._closed = blocks
        renderables: list[Markdown | Text] = [*self._blocks]
        if tail.strip():
            renderables.append(MiniAgentMarkdown(tail, code_theme=MARKDOWN_CODE_THEME))
        if not renderables:
            renderables.append(Text(""))
        return Group(*renderables)
