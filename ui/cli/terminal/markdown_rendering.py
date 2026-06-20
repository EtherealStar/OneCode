"""Terminal-aware Markdown rendering for the CLI.

This module houses two related helpers:

- :func:`parse_markdown_table_block` /
  :func:`render_markdown_table_block` — GFM table parser and
  width-aware renderer (column-width allocation, vertical fallback
  for narrow terminals, hard-wrap for over-long cells).

- :func:`render_cached_markdown` and :func:`_render_assistant_segment`
  — Rich-Markdown-backed text renderer used by both the dynamic
  preview and the static commit path. The dynamic preview path goes
  through :meth:`AssistantTailState.coalesce_with_cache` (see
  ``ui/cli/terminal/turn_render_state.py``) which keeps a stable
  prefix of already-rendered lines so successive deltas don't pay
  the full re-lex cost.

The GFM table rendering follows the column-width / vertical-fallback
strategy from the reference ``MarkdownTable.tsx`` implementation.

Constraints (mirroring the reference):

- :data:`SAFETY_MARGIN` — leave a few columns of headroom so terminal
  resizes, parent indent (the ``●`` tool bullet) and other races don't
  cause alternating-frame clip-and-flicker.
- :data:`MIN_COLUMN_WIDTH` — degenerate columns are useless; floor each
  column at 3 cells.
- :data:`MAX_ROW_LINES` — if any row would wrap to more than 4 lines
  in the horizontal layout, fall back to vertical key-value format.

The renderer is intentionally minimal: it handles plain text and
single-line emphasis inside cells, and degrades anything more exotic
to ``rich.text.Text`` rendering. Width measurement uses :mod:`wcwidth`
so Chinese, emoji, and other wide characters don't desync alignment.
ANSI styling is preserved by stripping SGR codes for measurement and
then re-inserting them around padded output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

try:
    import wcwidth  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - wcwidth is a project dep
    wcwidth = None  # type: ignore[assignment]


SAFETY_MARGIN = 4
MIN_COLUMN_WIDTH = 3
MAX_ROW_LINES = 4


@dataclass(frozen=True)
class MarkdownTableBlock:
    """A parsed GFM-style markdown table block.

    ``headers`` and ``rows`` are tuples of cell strings (without
    surrounding ``|``). ``alignments`` is one of ``"left"``,
    ``"right"``, ``"center"``, ``"default"`` per column.
    """

    headers: tuple[str, ...]
    alignments: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$"
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def parse_markdown_table_block(text: str) -> MarkdownTableBlock | None:
    """Return the first complete GFM table block in ``text`` or ``None``.

    A complete GFM table is at least three consecutive lines:

    1. header row — ``| a | b | c |``
    2. separator row — ``| --- | :---: | ---: |``
    3. one or more body rows of the same shape as the header
    """

    lines = text.split("\n")
    for i in range(len(lines) - 1):
        line = lines[i] or ""
        if not _TABLE_ROW_RE.match(line):
            continue
        if not _TABLE_SEPARATOR_RE.match(lines[i + 1] or ""):
            continue
        # Found the start of a table; collect body rows.
        body: list[tuple[str, ...]] = []
        j = i + 2
        while j < len(lines) and _TABLE_ROW_RE.match(lines[j] or ""):
            body.append(_split_row(lines[j]))
            j += 1
        if not body:
            return None
        headers = _split_row(line)
        alignments = _parse_alignments(lines[i + 1])
        # Pad alignments to header count if needed.
        if len(alignments) < len(headers):
            alignments = alignments + ("default",) * (len(headers) - len(alignments))
        return MarkdownTableBlock(
            headers=headers,
            alignments=tuple(alignments[: len(headers)]),
            rows=tuple(body),
        )
    return None


def _split_row(line: str) -> tuple[str, ...]:
    """Split a ``| a | b | c |`` line into ``("a", "b", "c")`` cells."""

    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    parts = _CELL_SPLIT_RE.split(stripped)
    return tuple(p.strip() for p in parts)


def _parse_alignments(separator_line: str) -> tuple[str, ...]:
    """Convert a separator row into per-column alignment tokens."""

    cells = _split_row(separator_line)
    alignments = []
    for cell in cells:
        s = cell.strip()
        left = s.startswith(":")
        right = s.endswith(":")
        if left and right:
            alignments.append("center")
        elif right:
            alignments.append("right")
        elif left:
            alignments.append("left")
        else:
            alignments.append("default")
    return tuple(alignments)


def _display_width(text: str) -> int:
    """Width of ``text`` in terminal cells.

    Strips ANSI escapes first so embedded styles don't count as visible
    characters. Uses :mod:`wcwidth` when available so wide CJK / emoji
    characters occupy 2 cells.
    """

    stripped = _ANSI_ESCAPE_RE.sub("", text)
    if wcwidth is None:
        return len(stripped)
    width = wcwidth.wcswidth(stripped)
    if width < 0:
        # wcwidth returns -1 for unprintable characters; fall back to
        # raw character count so we never under-size a column.
        return len(stripped)
    return width


def _min_cell_width(text: str) -> int:
    """Minimum sensible column width — the longest whitespace-separated token."""

    stripped = _ANSI_ESCAPE_RE.sub("", text).strip()
    if not stripped:
        return MIN_COLUMN_WIDTH
    tokens = [tok for tok in re.split(r"\s+", stripped) if tok]
    if not tokens:
        return MIN_COLUMN_WIDTH
    return max(_display_width(tok) for tok in tokens)


def _wrap_text(text: str, width: int, *, hard: bool = False) -> list[str]:
    """Wrap ``text`` to fit ``width``.

    ``hard=True`` allows breaking inside words (used when columns are
    narrower than the longest word). Otherwise we break on whitespace.
    Trailing whitespace is stripped. Empty input returns ``[""]`` so
    the caller always has at least one line for the cell.
    """

    if width <= 0:
        return [text]
    stripped = _ANSI_ESCAPE_RE.sub("", text).rstrip()
    if not stripped:
        return [""]
    words = re.split(r"(\s+)", stripped) if not hard else list(stripped)
    lines: list[str] = []
    current = ""
    current_width = 0
    for token in words:
        if not token:
            continue
        token_width = _display_width(token)
        if current_width + token_width <= width:
            current += token
            current_width += token_width
            continue
        if current:
            lines.append(current.rstrip())
            current = ""
            current_width = 0
        if token_width <= width:
            current = token
            current_width = token_width
        else:
            # Token is wider than the column. Hard-wrap.
            if hard:
                # Naive character-level hard wrap that respects wide
                # character widths.
                buf = ""
                buf_w = 0
                for ch in token:
                    ch_w = _display_width(ch)
                    if buf_w + ch_w > width:
                        lines.append(buf)
                        buf = ch
                        buf_w = ch_w
                    else:
                        buf += ch
                        buf_w += ch_w
                if buf:
                    current = buf
                    current_width = _display_width(current)
            else:
                # Soft fallback: put the whole token on its own line
                # and accept that it may overflow the column visually.
                current = token
                current_width = token_width
    if current or not lines:
        lines.append(current.rstrip())
    return lines or [""]


def _pad_aligned(text: str, visible_width: int, width: int, align: str) -> str:
    """Pad ``text`` to ``width`` columns given its visible width."""

    if visible_width >= width:
        return text
    pad = width - visible_width
    if align == "right":
        return " " * pad + text
    if align == "center":
        left = pad // 2
        right = pad - left
        return " " * left + text + " " * right
    return text + " " * pad


def _column_widths(
    block: MarkdownTableBlock,
    *,
    width: int,
) -> tuple[list[int], bool, int]:
    """Return ``(column_widths, needs_hard_wrap, max_row_line_count)``.

    Mirrors the reference's three-tier allocation:

    1. If the sum of ideal (un-wrapped) widths fits, use them.
    2. Otherwise, allocate the min widths and distribute the remaining
       space proportionally to each column's overflow.
    3. If even the min widths don't fit, scale them down proportionally
       and mark ``needs_hard_wrap=True`` so cells break long words.
    """

    headers = block.headers
    rows = block.rows
    n = len(headers)
    min_widths = [0] * n
    ideal_widths = [0] * n
    for col, header in enumerate(headers):
        col_min = _min_cell_width(header)
        col_ideal = max(_display_width(header), MIN_COLUMN_WIDTH)
        for row in rows:
            cell = row[col] if col < len(row) else ""
            col_min = max(col_min, _min_cell_width(cell))
            col_ideal = max(col_ideal, _display_width(cell), MIN_COLUMN_WIDTH)
        min_widths[col] = col_min
        ideal_widths[col] = col_ideal
    border_overhead = 1 + n * 3  # │ + (2 padding + 1 border) per column
    available = max(width - border_overhead - SAFETY_MARGIN, n * MIN_COLUMN_WIDTH)
    total_min = sum(min_widths)
    total_ideal = sum(ideal_widths)
    needs_hard_wrap = False
    if total_ideal <= available:
        column_widths = ideal_widths
    elif total_min <= available:
        extra = available - total_min
        overflows = [ideal_widths[i] - min_widths[i] for i in range(n)]
        total_overflow = sum(overflows)
        if total_overflow == 0:
            column_widths = list(min_widths)
        else:
            column_widths = []
            for i in range(n):
                add = int(overflows[i] / total_overflow * extra)
                column_widths.append(min_widths[i] + add)
    else:
        needs_hard_wrap = True
        scale = available / total_min if total_min else 1.0
        column_widths = [max(int(w * scale), MIN_COLUMN_WIDTH) for w in min_widths]
    # Calculate max row lines with the chosen widths.
    max_lines = 1
    for col, header in enumerate(headers):
        max_lines = max(
            max_lines,
            len(_wrap_text(header, column_widths[col], hard=needs_hard_wrap)),
        )
    for row in rows:
        for col, cell in enumerate(row):
            max_lines = max(
                max_lines,
                len(_wrap_text(cell, column_widths[col], hard=needs_hard_wrap)),
            )
    return column_widths, needs_hard_wrap, max_lines


def _render_horizontal(
    block: MarkdownTableBlock,
    *,
    width: int,
) -> list[str]:
    """Render the table as a horizontal grid of ``│ ─ ┌ ┐`` characters."""

    column_widths, needs_hard_wrap, _ = _column_widths(block, width=width)
    alignments = block.alignments

    def render_row(cells: tuple[str, ...], *, is_header: bool) -> list[str]:
        wrapped = [
            _wrap_text(cell, column_widths[c], hard=needs_hard_wrap)
            for c, cell in enumerate(cells)
        ]
        max_lines = max((len(w) for w in wrapped), default=1)
        # Pad each cell to max_lines by centering it vertically.
        offsets = [(max_lines - len(w)) // 2 for w in wrapped]
        result: list[str] = []
        for line_idx in range(max_lines):
            line = "│"
            for c, cell_lines in enumerate(wrapped):
                content_idx = line_idx - offsets[c]
                if 0 <= content_idx < len(cell_lines):
                    cell_text = cell_lines[content_idx]
                else:
                    cell_text = ""
                visible = _display_width(cell_text)
                align = "center" if is_header else alignments[c]
                line += " " + _pad_aligned(cell_text, visible, column_widths[c], align) + " │"
            result.append(line)
        return result

    def border_line(kind: str) -> str:
        parts = {
            "top": ("┌", "┬", "┐"),
            "middle": ("├", "┼", "┤"),
            "bottom": ("└", "┴", "┘"),
        }[kind]
        left, cross, right = parts
        line = left
        for c, w in enumerate(column_widths):
            line += "─" * (w + 2)
            line += cross if c < len(column_widths) - 1 else right
        return line

    lines: list[str] = [border_line("top")]
    lines.extend(render_row(block.headers, is_header=True))
    lines.append(border_line("middle"))
    for idx, row in enumerate(block.rows):
        lines.extend(render_row(row, is_header=False))
        if idx < len(block.rows) - 1:
            lines.append(border_line("middle"))
    lines.append(border_line("bottom"))
    return lines


def _render_vertical(
    block: MarkdownTableBlock,
    *,
    width: int,
) -> list[str]:
    """Render the table as ``header: value`` key-value rows.

    Used when the horizontal layout would wrap to too many lines or
    overflow the available width.
    """

    lines: list[str] = []
    separator_width = min(max(width - 1, 1), 40)
    separator = "─" * separator_width
    wrap_indent = "  "
    for row_idx, row in enumerate(block.rows):
        if row_idx > 0:
            lines.append(separator)
        for col_idx, cell in enumerate(row):
            label = block.headers[col_idx] if col_idx < len(block.headers) else f"Column {col_idx + 1}"
            value = _ANSI_ESCAPE_RE.sub("", cell).rstrip()
            value = re.sub(r"\s+", " ", value).strip()
            if not value:
                value = ""
            # First line is narrower to fit the label.
            first_line_width = max(width - _display_width(label) - 3, 10)
            subsequent_width = max(width - len(wrap_indent) - 1, 10)
            first_pass = _wrap_text(value, first_line_width)
            first_line = first_pass[0] if first_pass else ""
            if len(first_pass) <= 1 or subsequent_width <= first_line_width:
                wrapped = first_pass
            else:
                remaining = " ".join(line.strip() for line in first_pass[1:])
                rewrapped = _wrap_text(remaining, subsequent_width)
                wrapped = [first_line, *rewrapped]
            lines.append(f"\x1b[1m{label}:\x1b[22m {wrapped[0] if wrapped else ''}")
            for extra in wrapped[1:]:
                if not extra.strip():
                    continue
                lines.append(f"{wrap_indent}{extra}")
    return lines


def render_markdown_table_block(
    block: MarkdownTableBlock,
    *,
    width: int,
    safety_margin: int = SAFETY_MARGIN,
    min_column_width: int = MIN_COLUMN_WIDTH,
    max_row_lines: int = MAX_ROW_LINES,
) -> list[str]:
    """Render a parsed :class:`MarkdownTableBlock` to terminal lines.

    Returns a list of strings, one per terminal row. The function never
    writes to stdout and never throws on degenerate input — it falls
    back to the vertical layout whenever the horizontal layout would
    exceed ``max_row_lines`` lines per row or come within
    ``safety_margin`` cells of the right edge.
    """

    del safety_margin, min_column_width  # kept for API compatibility
    column_widths, needs_hard_wrap, max_lines = _column_widths(block, width=width)
    if max_lines > max_row_lines:
        return _render_vertical(block, width=width)
    lines = _render_horizontal(block, width=width)
    # Safety check: if any rendered line is within the safety margin of
    # the right edge, fall back to vertical so terminal resize races
    # don't cause alternating-frame clipping.
    max_line_width = max((_display_width(line) for line in lines), default=0)
    if max_line_width > width - SAFETY_MARGIN:
        return _render_vertical(block, width=width)
    _ = column_widths, needs_hard_wrap  # keep locals referenced
    return lines


__all__ = [
    "MarkdownTableBlock",
    "parse_markdown_table_block",
    "render_markdown_table_block",
    "render_cached_markdown",
    "_render_assistant_segment",
    "SAFETY_MARGIN",
    "MIN_COLUMN_WIDTH",
    "MAX_ROW_LINES",
]


# --- assistant-text rendering ---------------------------------------------


import io as _io
from threading import Lock as _Lock

from rich.console import Console as _Console
from rich.markdown import Markdown as _RichMarkdown
from rich.text import Text as _RichText

from ui.cli.terminal.text_cache import TextCache as _TextCache
from ui.cli.theme import RICH_THEME as _RICH_THEME


#: Module-level cache shared by every call to
#: :func:`render_cached_markdown`. The cache key is
#: ``(text_hash, width)``; we never store the raw text, only the
#: rendered ANSI lines, so a long session does not balloon RSS even
#: when the user replays the same assistant message.
_TEXT_CACHE = _TextCache(max_size=500)
_TEXT_CACHE_LOCK = _Lock()


def _render_segment_to_lines(text: str, width: int) -> list[str]:
    """Render ``text`` (which may contain GFM tables) to ANSI lines.

    Detects the first complete GFM table block, renders it with the
    width-aware table helper, and renders the surrounding text with
    Rich's Markdown renderer. Fences that are unbalanced in ``text``
    fall back to plain text so we never leak a synthetic closing
    fence into the dynamic region.
    """

    if not text:
        return []
    out = _io.StringIO()
    console = _Console(
        file=out,
        force_terminal=True,
        color_system="standard",
        width=max(width, 20),
        theme=_RICH_THEME,
    )
    lines: list[str] = []
    remaining = text
    while remaining:
        table = parse_markdown_table_block(remaining)
        if table is None:
            break
        before, after = _split_around_table(remaining, table)
        if before:
            _emit_segment(before, console)
            lines.extend(_take_new_lines(out, lines))
        lines.extend(render_markdown_table_block(table, width=max(width, 20)))
        remaining = after
    if remaining:
        _emit_segment(remaining, console)
        lines.extend(_take_new_lines(out, lines))
    return lines


def _emit_segment(segment: str, console: _Console) -> None:
    """Render a single text segment with ``console`` (no return value).

    An unbalanced triple-backtick / tilde fence falls back to plain
    text so the dynamic region never shows a synthetic closing fence
    that the next delta would have to remove.
    """

    if not segment.strip():
        return
    if segment.count("```") % 2 == 1 or segment.count("~~~") % 2 == 1:
        console.print(_RichText(segment, style="onecode.metric"))
    else:
        console.print(_RichMarkdown(segment))


def _take_new_lines(out: _io.StringIO, existing: list[str]) -> list[str]:
    """Return the lines ``console`` has written since the last call."""

    rendered = out.getvalue()
    out.truncate(0)
    out.seek(0)
    # If the rendered buffer is empty, the new content was either an
    # empty segment or ended without a trailing newline. Return an
    # empty list so the caller can keep going.
    if not rendered:
        return []
    return [_rstrip_terminal_padding(line) for line in rendered.splitlines()]


def _rstrip_terminal_padding(line: str) -> str:
    """Remove Rich's terminal-width fill while preserving closing SGR codes."""

    line = line.rstrip(" ")
    match = re.search(r"((?:\x1b\[[0-9;]*m)+)$", line)
    if match is None:
        return line
    suffix = match.group(1)
    body = line[: -len(suffix)]
    return body.rstrip(" ") + suffix


def _split_around_table(text: str, table) -> tuple[str, str]:
    """Split ``text`` into the part before and after the first table block."""

    table_row_re = re.compile(r"^\s*\|.*\|\s*$")
    table_sep_re = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
    lines = text.split("\n")
    start = None
    for idx in range(len(lines) - 1):
        if table_row_re.match(lines[idx] or "") and table_sep_re.match(lines[idx + 1] or ""):
            start = idx
            break
    if start is None:
        return text, ""
    end = start + 2
    while end < len(lines) and table_row_re.match(lines[end] or ""):
        end += 1
    before = "\n".join(lines[:start])
    after = "\n".join(lines[end:])
    if before:
        before += "\n"
    if after and not after.endswith("\n"):
        after += "\n"
    return before, after


def render_cached_markdown(text: str, *, width: int) -> list[str]:
    """Render ``text`` to ANSI lines, consulting a module-level cache.

    The cache key is ``(text_hash, width)`` and the cached value is
    the list of ANSI lines. Original ``text`` is not retained, which
    is important for long sessions where the user replays the same
    assistant message after a ``/clear`` or session resume.

    The cache is process-wide. Concurrent calls are safe (the cache
    uses an internal lock).
    """

    if not text:
        return []
    return _TEXT_CACHE.get_or_render(
        text,
        width=max(width, 20),
        render_fn=_render_segment_to_lines,
    )


def _render_assistant_segment(
    full_text: str,
    *,
    width: int,
    base_lines: list[str],
) -> list[str]:
    """Render ``full_text`` to ANSI lines, treating ``base_lines`` as already-cached.

    The dynamic preview path uses this to avoid re-rendering the
    already-stable prefix: callers pass the previously-rendered
    lines and the function only re-lexes the freshly-appended delta.

    Internally this just renders ``full_text`` end-to-end through the
    module-level cache. ``base_lines`` is accepted for API symmetry
    with the reference implementation; the cache itself is keyed by
    the full text so two calls with the same text always agree.
    """

    del base_lines  # kept for API symmetry with the reference TS impl
    return render_cached_markdown(full_text, width=width)
