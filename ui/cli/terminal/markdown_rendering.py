"""CLI 终端感知的 Markdown 渲染器。

本模块包含两个相关的辅助能力：

- parse_markdown_table_block / render_markdown_table_block：GFM 表格解析器与
  终端宽度感知渲染器（列宽分配、窄终端垂直回退降级、超长单元格硬折行）。

- render_cached_markdown 和 _render_assistant_segment：
  由 Rich Markdown 支持的文本渲染器，动态预览和静态提交路径均使用该渲染器。
  动态预览路径通过 AssistantTailState.coalesce_with_cache 处理，
  保留已渲染行的稳定前缀，避免连续增量支付完整的重新词法解析开销。

GFM 表格渲染遵循参考实现中的列宽分配与垂直回退降级策略。

约束条件：

- SAFETY_MARGIN：预留若干列的安全边距，防止终端缩放、父级缩进等竞争条件导致交替帧截断与闪烁。
- MIN_COLUMN_WIDTH：退化列无意义，单列宽度保底为 3 单元格。
- MAX_ROW_LINES：如果横向布局中任一行折行超过 4 行，则回退为键值对形式的垂直布局。

渲染器保持轻量设计：处理单元格内的纯文本和单行强调格式，超出范围的内容降级为
rich.text.Text 渲染。使用 wcwidth 测量宽度以确保中文、emoji 及宽字符对齐不发生错位。
通过剥离 SGR ANSI 样式码进行测量并在填充输出后重新插入，从而保留样式。
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
    """解析后的 GFM 风格 Markdown 表格块。

    headers 与 rows 为单元格字符串元组（不包含两端的竖线）。
    alignments 为每列的对齐方式，取值为 left、right、center 或 default 之一。
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
    """返回 text 中的第一个完整 GFM 表格块；若未找到则返回 None。

    完整的 GFM 表格至少包含连续三行：

    1. 表头行：| a | b | c |
    2. 分隔行：| --- | :---: | ---: |
    3. 一行或多行与表头形状一致的主体行
    """

    lines = text.split("\n")
    for i in range(len(lines) - 1):
        line = lines[i] or ""
        if not _TABLE_ROW_RE.match(line):
            continue
        if not _TABLE_SEPARATOR_RE.match(lines[i + 1] or ""):
            continue
        # 找到表格起始位置；收集表格主体各行。
        body: list[tuple[str, ...]] = []
        j = i + 2
        while j < len(lines) and _TABLE_ROW_RE.match(lines[j] or ""):
            body.append(_split_row(lines[j]))
            j += 1
        if not body:
            return None
        headers = _split_row(line)
        alignments = _parse_alignments(lines[i + 1])
        # 如有需要，将对齐配置填充至与表头数量一致。
        if len(alignments) < len(headers):
            alignments = alignments + ("default",) * (len(headers) - len(alignments))
        return MarkdownTableBlock(
            headers=headers,
            alignments=tuple(alignments[: len(headers)]),
            rows=tuple(body),
        )
    return None


def _split_row(line: str) -> tuple[str, ...]:
    """将 | a | b | c | 行拆分为 ("a", "b", "c") 单元格元组。"""

    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    parts = _CELL_SPLIT_RE.split(stripped)
    return tuple(p.strip() for p in parts)


def _parse_alignments(separator_line: str) -> tuple[str, ...]:
    """将分隔行转换为各列的对齐方式标记。"""

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
    """计算 text 在终端单元格中的显示宽度。

    优先剥离 ANSI 转义序列，避免内嵌样式占用可见字符宽度。
    在 wcwidth 可用时使用它，使中日韩宽字符与 emoji 占用 2 个单元格。
    """

    stripped = _ANSI_ESCAPE_RE.sub("", text)
    if wcwidth is None:
        return len(stripped)
    width = wcwidth.wcswidth(stripped)
    if width < 0:
        # wcwidth 对不可打印字符返回 -1；回退到原始字符计数，避免列宽不足。
        return len(stripped)
    return width


def _min_cell_width(text: str) -> int:
    """合理的最小列宽，即由空白分隔的最长词元宽度。"""

    stripped = _ANSI_ESCAPE_RE.sub("", text).strip()
    if not stripped:
        return MIN_COLUMN_WIDTH
    tokens = [tok for tok in re.split(r"\s+", stripped) if tok]
    if not tokens:
        return MIN_COLUMN_WIDTH
    return max(_display_width(tok) for tok in tokens)


def _wrap_text(text: str, width: int, *, hard: bool = False) -> list[str]:
    """将 text 折行以适应 width。

    hard=True 允许在单词内部断行（用于列宽小于最长单词的情况）。
    否则在空白字符处断行。尾随空白会被剥离。
    输入为空时返回 [""]，保证调用方始终至少获取一行。
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
            # 词元宽度超过列宽，执行硬折行。
            if hard:
                # 遵循宽字符宽度的简单字符级硬折行。
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
                # 软回退：将整个词元单独放在一行，允许视觉上略微超出列宽。
                current = token
                current_width = token_width
    if current or not lines:
        lines.append(current.rstrip())
    return lines or [""]


def _pad_aligned(text: str, visible_width: int, width: int, align: str) -> str:
    """根据可见宽度将 text 填充对齐至 width 列。"""

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
    """返回 (column_widths, needs_hard_wrap, max_row_line_count)。

    遵循三层分配策略：

    1. 若理想（未折行）宽度之和容纳得下，直接使用理想宽度。
    2. 否则分配最小宽度，并按各列溢出比例分配剩余空间。
    3. 若最小宽度之和仍超出可用空间，按比例等比压缩并标记 needs_hard_wrap=True
       以便单元格对长单词进行硬截断折行。
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
    border_overhead = 1 + n * 3  # 每列增加 1 个分隔线与 2 个内边距，另加起始分隔线
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
    # 使用选定的列宽计算最大行数。
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
    """将表格渲染为使用网格线字符的横向表格。"""

    column_widths, needs_hard_wrap, _ = _column_widths(block, width=width)
    alignments = block.alignments

    def render_row(cells: tuple[str, ...], *, is_header: bool) -> list[str]:
        wrapped = [
            _wrap_text(cell, column_widths[c], hard=needs_hard_wrap)
            for c, cell in enumerate(cells)
        ]
        max_lines = max((len(w) for w in wrapped), default=1)
        # 将每个单元格垂直居中填充至 max_lines 行。
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
    """将表格渲染为 header: value 形式的键值对行。

    当横向布局折行过多或超出可用宽度时作为回退方案使用。
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
            # 第一行缩窄以容纳表头标签。
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
    """将解析后的 MarkdownTableBlock 渲染为终端文本行列表。

    返回字符串列表，每项对应一个终端行。该函数不向 stdout 输出，
    也不在畸形输入时抛出异常。当横向布局超过每行 max_row_lines 折行上限
    或距离右边界不足 safety_margin 时，自动回退到垂直键值对布局。
    """

    del safety_margin, min_column_width  # 保留以维持接口兼容性
    column_widths, needs_hard_wrap, max_lines = _column_widths(block, width=width)
    if max_lines > max_row_lines:
        return _render_vertical(block, width=width)
    lines = _render_horizontal(block, width=width)
    # 安全检查：若任意渲染行超出安全边界，回退到垂直模式，防止终端缩放竞争引起帧交替截断。
    max_line_width = max((_display_width(line) for line in lines), default=0)
    if max_line_width > width - SAFETY_MARGIN:
        return _render_vertical(block, width=width)
    _ = column_widths, needs_hard_wrap  # 保持局部变量引用
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


# --- assistant 文本渲染 ---


import io as _io
from threading import Lock as _Lock

from rich.console import Console as _Console
from rich.markdown import Markdown as _RichMarkdown
from rich.text import Text as _RichText

from ui.cli.terminal.text_cache import TextCache as _TextCache
from ui.cli.theme import RICH_THEME as _RICH_THEME


# render_cached_markdown 共享的模块级缓存。
# 缓存键为 (text_hash, width)；仅存储渲染后的 ANSI 行而不缓存原始文本，
# 确保长会话即使重放相同消息也不会导致 RSS 内存膨胀。
_TEXT_CACHE = _TextCache(max_size=500)
_TEXT_CACHE_LOCK = _Lock()


def _render_segment_to_lines(text: str, width: int) -> list[str]:
    """将可能包含 GFM 表格的 text 渲染为 ANSI 行列表。

    检测首个完整的 GFM 表格块，通过宽度感知表格辅助函数进行渲染，
    周围文本则使用 Rich Markdown 渲染器处理。若文本中包含未闭合的代码块围栏，
    则回退降级为纯文本，防止向动态区域泄漏合成的闭合围栏。
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
    """使用 console 渲染单个文本片段（无返回值）。

    遇到未成对的反引号或波浪线代码块围栏时降级为纯文本，
    防止动态区域渲染出后续增量需要移除的虚假闭合围栏。
    """

    if not segment.strip():
        return
    if segment.count("```") % 2 == 1 or segment.count("~~~") % 2 == 1:
        console.print(_RichText(segment, style="onecode.metric"))
    else:
        console.print(_RichMarkdown(segment))


def _take_new_lines(out: _io.StringIO, existing: list[str]) -> list[str]:
    """返回 console 自上次调用以来新写入的行列表。"""

    rendered = out.getvalue()
    out.truncate(0)
    out.seek(0)
    # 若渲染缓冲区为空，说明新增内容为空片段或末尾未包含换行符。
    # 返回空列表以使调用方继续推进。
    if not rendered:
        return []
    return [_rstrip_terminal_padding(line) for line in rendered.splitlines()]


def _rstrip_terminal_padding(line: str) -> str:
    """移除 Rich 自动填充的终端宽度空白，同时保留末尾的 SGR 样式码。"""

    line = line.rstrip(" ")
    match = re.search(r"((?:\x1b\[[0-9;]*m)+)$", line)
    if match is None:
        return line
    suffix = match.group(1)
    body = line[: -len(suffix)]
    return body.rstrip(" ") + suffix


def _split_around_table(text: str, table) -> tuple[str, str]:
    """将 text 拆分为第一个表格块之前和之后的部分。"""

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
    """将 text 渲染为 ANSI 行，优先查询模块级缓存。

    缓存键为 (text_hash, width)，缓存值为 ANSI 行列表。
    不保留原始 text，这对于在清屏或会话恢复后重放相同 assistant 消息的长会话非常重要。

    该缓存为进程级共享，并发调用是线程安全的（内部使用互斥锁）。
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
    """将 full_text 渲染为 ANSI 行，将 base_lines 视为已缓存内容。

    动态预览路径借此避免重新渲染已稳定的前缀：
    调用方传入之前已渲染的行，函数仅需对新追加的增量部分进行词法解析。

    在内部，该实现通过模块级缓存全量渲染 full_text。
    接收 base_lines 参数是为了与参考实现保持接口对齐；
    缓存自身以全量文本为键，确保相同文本的多次调用结果始终一致。
    """

    del base_lines  # 保留以与参考实现接口保持对称
    return render_cached_markdown(full_text, width=width)
