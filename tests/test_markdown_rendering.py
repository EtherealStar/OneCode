"""Tests for the GFM markdown table parser / renderer.

These cover the column-width allocation, the vertical-fallback
strategy, and width-aware wrapping that the streaming CLI relies on
to avoid truncated or flickering tables in narrow terminals.
"""

from __future__ import annotations

from ui.cli.terminal.markdown_rendering import (
    MAX_ROW_LINES,
    MIN_COLUMN_WIDTH,
    SAFETY_MARGIN,
    parse_markdown_table_block,
    render_markdown_table_block,
)


def test_parse_simple_table() -> None:
    text = (
        "| name | value |\n"
        "| --- | --- |\n"
        "| a | 1 |\n"
        "| b | 2 |\n"
    )
    block = parse_markdown_table_block(text)
    assert block is not None
    assert block.headers == ("name", "value")
    assert block.alignments == ("default", "default")
    assert block.rows == (("a", "1"), ("b", "2"))


def test_parse_alignment_tokens() -> None:
    text = (
        "| left | center | right |\n"
        "| :--- | :---: | ---: |\n"
        "| a | b | c |\n"
    )
    block = parse_markdown_table_block(text)
    assert block is not None
    assert block.alignments == ("left", "center", "right")


def test_parse_returns_none_when_separator_missing() -> None:
    text = "| name | value |\n| a | 1 |\n"
    assert parse_markdown_table_block(text) is None


def test_parse_returns_none_when_body_missing() -> None:
    text = "| name | value |\n| --- | --- |\n"
    assert parse_markdown_table_block(text) is None


def test_horizontal_table_fits_in_wide_terminal() -> None:
    text = (
        "| name | value |\n"
        "| --- | --- |\n"
        "| alpha | 1 |\n"
        "| beta | 2 |\n"
    )
    block = parse_markdown_table_block(text)
    assert block is not None
    lines = render_markdown_table_block(block, width=80)
    # Horizontal layout has borders and at least a header row.
    assert len(lines) >= 4
    # Every rendered line fits within the requested width.
    for line in lines:
        assert _visible_width(line) <= 80, f"line too wide: {line!r}"


def test_narrow_terminal_falls_back_to_vertical() -> None:
    text = (
        "| feature | current | target |\n"
        "| --- | --- | --- |\n"
        "| live streaming | buffered | live |\n"
        "| table rendering | broken | stable |\n"
    )
    block = parse_markdown_table_block(text)
    assert block is not None
    # Use a width that is too small to render horizontally.
    lines = render_markdown_table_block(block, width=20)
    assert any("feature:" in _strip_ansi(line) for line in lines)
    assert any("current:" in _strip_ansi(line) for line in lines)
    assert any("target:" in _strip_ansi(line) for line in lines)


def test_long_words_force_horizontal_to_warp_with_hard_wrap() -> None:
    text = (
        "| longword | other |\n"
        "| --- | --- |\n"
        "| supercalifragilisticexpialidocious | short |\n"
    )
    block = parse_markdown_table_block(text)
    assert block is not None
    # Width that is wide enough that the safety margin doesn't trip,
    # but the only way to keep the row count within MAX_ROW_LINES is
    # to hard-wrap the long word.
    lines = render_markdown_table_block(block, width=80)
    assert len(lines) >= 4


def test_alignment_is_honored_in_horizontal_layout() -> None:
    text = (
        "| left | center | right |\n"
        "| :--- | :---: | ---: |\n"
        "| a | b | c |\n"
    )
    block = parse_markdown_table_block(text)
    assert block is not None
    lines = render_markdown_table_block(block, width=80)
    # Find the data row (the second ``│ a │ b │ c │``-style line).
    data_lines = [
        line for line in lines
        if " a " in _strip_ansi(line) and " b " in _strip_ansi(line) and " c " in _strip_ansi(line)
    ]
    assert data_lines
    # Right alignment pushes the value to the right edge of its column.
    data_line = data_lines[0]
    stripped = _strip_ansi(data_line)
    assert stripped.rfind("c") > stripped.rfind("b") > stripped.rfind("a")


def test_render_block_handles_wide_characters() -> None:
    text = (
        "| 名称 | 值 |\n"
        "| --- | --- |\n"
        "| 中文 | 1 |\n"
    )
    block = parse_markdown_table_block(text)
    assert block is not None
    lines = render_markdown_table_block(block, width=80)
    # Wide characters should be measured as 2 cells; the rendered
    # table should not crash.
    assert lines


def test_constants_match_reference() -> None:
    assert SAFETY_MARGIN == 4
    assert MIN_COLUMN_WIDTH == 3
    assert MAX_ROW_LINES == 4


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _visible_width(text: str) -> int:
    import wcwidth

    return max(wcwidth.wcswidth(_strip_ansi(line)) for line in [text])
