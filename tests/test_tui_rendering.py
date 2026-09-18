from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.markdown import Markdown
from rich.theme import Theme as RichTheme

from ui.tui.conversation.render_cache import (
    MarkdownBlockCache,
    OneCodeMarkdown,
    split_closed_blocks,
)
from ui.tui.projection_types import (
    LIFECYCLE_COMPLETED,
    LIFECYCLE_DRAFT,
    PART_ATTACHMENT,
    PART_TEXT,
    PART_TOOL,
    UiMessage,
    UiPart,
)
from ui.tui.renderers.message import render_message
from ui.tui.renderers.status import (
    RunState,
    render_run_state,
    render_status_left,
)
from ui.tui.renderers.tool import (
    build_tool_presentation_registry,
    first_line_excerpt,
)
from ui.tui.theme import MARKDOWN_CODE_THEME, RICH_STYLES


def render_to_text(renderable, width: int = 200) -> str:
    stream = io.StringIO()
    console = Console(
        width=width,
        file=stream,
        force_terminal=False,
        theme=RichTheme(RICH_STYLES),
    )
    console.print(renderable)
    return stream.getvalue()


def test_split_closed_blocks_keeps_fence_internal_blanks() -> None:
    source = "```python\nprint(1)\n\nprint(2)\n```\n\nafter\n"
    closed, tail = split_closed_blocks(source)
    assert "print(1)\n\nprint(2)" in closed
    assert tail == "after\n"


def test_split_closed_blocks_handles_mixed_fences() -> None:
    source = "~~~\n```\nstill fence\n~~~\n\nend\n"
    closed, tail = split_closed_blocks(source)
    assert "still fence" in closed
    assert tail == "end\n"


def test_markdown_cache_rebuilds_on_body_replacement() -> None:
    cache = MarkdownBlockCache()
    first = render_to_text(cache.render("alpha\n\n", finalized=True))
    assert "alpha" in first
    replaced = render_to_text(cache.render("beta\n\n", finalized=True))
    assert "beta" in replaced
    assert "alpha" not in replaced


def test_markdown_streaming_then_final_matches_full_parse() -> None:
    source = (
        "# Title\n\n"
        "A paragraph with 中文和 emoji 😀.\n\n"
        "```python\nx = 1\n\ny = 2\n```\n\n"
        "~~~\n```\nnested\n~~~\n\n"
        "see [docs]\n\n"
        "[docs]: https://example.com\n"
    )
    cache = MarkdownBlockCache()
    for end in range(0, len(source), 7):
        cache.render(source[:end])
    final = render_to_text(OneCodeMarkdown(source, code_theme=MARKDOWN_CODE_THEME))
    cached_final = render_to_text(cache.render(source, finalized=True))
    assert cached_final == final
    assert "中文" in cached_final
    assert "😀" in cached_final
    assert "nested" in cached_final


@pytest.mark.parametrize(
    "source",
    [
        "- a\n- b\n- c\n",
        "1. one\n2. two\n",
        "> quote\n> more\n",
        "para\n\n[1]: https://example.org\n",
    ],
)
def test_markdown_final_matches_full_markdown(source: str) -> None:
    cache = MarkdownBlockCache()
    cached = render_to_text(cache.render(source, finalized=True))
    full = render_to_text(Markdown(source))
    assert cached == full


def test_incremental_streaming_never_loses_tail() -> None:
    cache = MarkdownBlockCache()
    text = ""
    words = [f"word{i}" for i in range(500)]
    for word in words:
        text += word + " "
        output = render_to_text(cache.render(text))
    assert "word0" in output
    assert "word499" in output


def test_render_user_message_shows_label_and_attachments() -> None:
    message = UiMessage(
        message_id="u1",
        role="user",
        parts=(
            UiPart(kind=PART_TEXT, content="hello"),
            UiPart(kind=PART_ATTACHMENT, content="[file] a.py", attachment_type="file"),
        ),
    )
    text = render_to_text(render_message(message))
    assert "You" in text
    assert "hello" in text
    assert "a.py" in text


def test_render_assistant_message_keeps_text_and_tool_order() -> None:
    message = UiMessage(
        message_id="a1",
        role="assistant",
        lifecycle=LIFECYCLE_COMPLETED,
        parts=(
            UiPart(kind=PART_TEXT, content="answer"),
            UiPart(
                kind=PART_TOOL,
                tool_call_id="t1",
                tool_name="read_file",
                tool_input={"path": "a.py"},
                status="completed",
                result_preview="read 3 lines",
            ),
            UiPart(
                kind=PART_TOOL,
                tool_call_id="t2",
                tool_name="read_file",
                tool_input={"path": "b.py"},
                status="declared",
            ),
        ),
    )
    text = render_to_text(render_message(message))
    assert "OneCode" in text
    assert "answer" in text
    assert text.index("a.py") < text.index("b.py")
    expanded = render_to_text(render_message(message, details_expanded=True))
    assert "read 3 lines" in expanded


def test_unknown_tool_fallback_keeps_error_excerpt() -> None:
    registry = build_tool_presentation_registry()
    presentation = registry.present(
        name="mcp_some_tool",
        tool_input={},
        result_preview="Traceback: boom failure",
        is_error=True,
    )
    assert presentation.is_error is True
    assert "boom failure" in presentation.body
    message = UiMessage(
        message_id="a2",
        role="assistant",
        lifecycle=LIFECYCLE_COMPLETED,
        parts=(
            UiPart(
                kind=PART_TOOL,
                tool_call_id="t9",
                tool_name="mcp_some_tool",
                status="error",
                is_error=True,
                result_preview="Traceback: boom failure",
            ),
        ),
    )
    text = render_to_text(render_message(message))
    assert "boom failure" in text
    assert "✗" in text


def test_known_tool_summaries_use_input_facts() -> None:
    registry = build_tool_presentation_registry()
    bash = registry.present(
        name="bash",
        tool_input={"command": "echo hello\nsecond"},
        result_preview=None,
        is_error=False,
    )
    assert "echo hello" in bash.summary
    grep = registry.present(
        name="grep",
        tool_input={"pattern": "needle", "path": "src"},
        result_preview=None,
        is_error=False,
    )
    assert "needle" in grep.summary
    assert "src" in grep.summary


def test_first_line_excerpt_redacts_secrets() -> None:
    excerpt = first_line_excerpt("api_key=abcdef123\nsecond")
    assert "abcdef123" not in excerpt
    assert "已隐藏" in excerpt


def test_status_renderers() -> None:
    left = render_to_text(render_status_left("/workspace", "gpt-x"))
    assert "OneCode" in left
    assert "gpt-x" in left
    assert "运行中" in render_to_text(render_run_state(RunState("running")))
    assert "排队 2" in render_to_text(render_run_state(RunState("queued", 2)))
    assert "出错" in render_to_text(render_run_state(RunState("error")))
    assert render_to_text(render_run_state(None)).strip() == ""
