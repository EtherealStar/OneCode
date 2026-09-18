"""Streaming Markdown block cache.

Rich's ``Markdown`` parses at construction time, so caching instances is
caching parse results. During streaming we only need to re-parse the unclosed
tail; closed blocks are reused. When the body is finalised we re-parse the whole
source once, because blank-line block splitting cannot represent every
cross-block construct (loose lists, link reference definitions, and so on).
Correctness wins over the incremental optimisation.
"""

from __future__ import annotations

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import CodeBlock, Markdown
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text

from ui.tui.theme import MARKDOWN_CODE_THEME, SURFACE

_FENCE_MARKERS = ("```", "~~~")


class _InsetCodeBlock(CodeBlock):
    """Code block: surface background plus a two-cell left inset."""

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
    """Markdown that applies the TUI tokens to fenced and indented code."""

    elements = {
        **Markdown.elements,
        "code_block": _InsetCodeBlock,
        "fence": _InsetCodeBlock,
    }


def _scan(source: str) -> tuple[list[str], str]:
    """Split ``source`` into closed blocks and an unclosed tail.

    Blank lines outside a fence are block boundaries; blank lines inside a
    fence belong to the code content. Mixed fence markers are tracked so a
    ``~~~`` block is not closed by a ```` ``` ```` line and vice versa.
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
    """Return ``(closed_prefix, unclosed_tail)`` source text."""

    blocks, tail = _scan(source)
    return "".join(blocks), tail


class MarkdownBlockCache:
    """Incremental render cache for a single growing text part."""

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
        for block in blocks[len(self._blocks):]:
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
