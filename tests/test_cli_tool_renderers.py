from __future__ import annotations

from pathlib import Path

from services.tools.types import ToolExecutionResult
from ui.cli.renderer import render_tool_result_summary
from ui.cli.tool_renderers import render_tool_result


def _result(
    tool_name: str,
    metadata: dict,
    *,
    is_error: bool = False,
    call_id: str = "call_1",
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id=call_id,
        tool_name=tool_name,
        content="content",
        is_error=is_error,
        metadata=metadata,
    )


def _slash(text: str) -> str:
    return text.replace("\\", "/")


def test_unknown_tool_fallback_keeps_call_id_for_diagnostics(tmp_path: Path) -> None:
    text = render_tool_result(_result("mcp_tool", {}), workspace=tmp_path)

    assert text == "[mcp_tool] call call_1"


def test_read_file_success_uses_full_relative_path_without_call_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ui" / "cli" / "app.py"
    text = render_tool_result(
        _result(
            "read_file",
            {"path": str(path), "offset": 1, "line_count": 42},
        ),
        workspace=tmp_path,
    )

    normalized = _slash(text)
    assert normalized == "[read_file] Read 42 line(s) from ui/cli/app.py"
    assert "call_1" not in text
    assert " ok" not in text
    assert normalized != "[read_file] Read 42 line(s) from app.py"


def test_read_file_success_mentions_non_default_offset(tmp_path: Path) -> None:
    text = render_tool_result(
        _result(
            "read_file",
            {"path": str(tmp_path / "src" / "main.py"), "offset": 9, "line_count": 3},
        ),
        workspace=tmp_path,
    )

    assert _slash(text) == "[read_file] Read 3 line(s) from src/main.py from line 9"


def test_file_tool_error_uses_error_metadata_and_relative_path(tmp_path: Path) -> None:
    text = render_tool_result(
        _result(
            "read_file",
            {"error": "file_not_found", "path": str(tmp_path / "docs" / "missing.md")},
            is_error=True,
        ),
        workspace=tmp_path,
    )

    assert _slash(text) == "[read_file error] file_not_found docs/missing.md"
    assert "call_1" not in text


def test_grep_modes_render_key_counts(tmp_path: Path) -> None:
    files = render_tool_result(
        _result("grep", {"mode": "files_with_matches", "num_files": 4}),
        workspace=tmp_path,
    )
    count = render_tool_result(
        _result("grep", {"mode": "count", "num_matches": 12, "num_files": 3}),
        workspace=tmp_path,
    )
    content = render_tool_result(
        _result("grep", {"mode": "content", "num_lines": 7, "num_files": 2}),
        workspace=tmp_path,
    )

    assert files == "[grep] Found 4 files"
    assert count == "[grep] Found 12 matches across 3 files"
    assert content == "[grep] Found 7 matches across 2 files"


def test_grep_truncation_mentions_pagination(tmp_path: Path) -> None:
    text = render_tool_result(
        _result(
            "grep",
            {
                "mode": "count",
                "num_matches": 40,
                "num_files": 10,
                "truncated": True,
                "applied_limit": 5,
                "applied_offset": 2,
            },
        ),
        workspace=tmp_path,
    )

    assert text == (
        "[grep] Found 40 matches across 10 files, "
        "showing first 5 after offset 2"
    )


def test_glob_truncated_summary(tmp_path: Path) -> None:
    text = render_tool_result(
        _result(
            "glob",
            {
                "num_files": 10,
                "total_matches_before_pagination": 31,
                "truncated": True,
                "applied_limit": 10,
                "applied_offset": 0,
            },
        ),
        workspace=tmp_path,
    )

    assert text == "[glob] Found 31 files, showing 10"


def test_bash_success_and_timed_out_error(tmp_path: Path) -> None:
    ok = render_tool_result(
        _result(
            "bash",
            {
                "exit_code": 0,
                "duration_ms": 14,
                "stdout_chars": 20,
                "stderr_chars": 0,
            },
        ),
        workspace=tmp_path,
    )
    timed_out = render_tool_result(
        _result(
            "bash",
            {
                "exit_code": 124,
                "duration_ms": 1000,
                "timed_out": True,
                "stdout_chars": 1,
                "stderr_chars": 2,
            },
            is_error=True,
        ),
        workspace=tmp_path,
    )

    assert ok == "[bash] exit 0 in 14 ms, stdout 20 chars, stderr 0 chars"
    assert timed_out == (
        "[bash error] exit 124 in 1000 ms, timed out, stdout 1 chars, stderr 2 chars"
    )


def test_background_bash_mentions_task_and_output_file(tmp_path: Path) -> None:
    text = render_tool_result(
        _result(
            "bash",
            {
                "background": True,
                "task_id": "bg_1",
                "status": "running",
                "output_file": str(tmp_path / ".onecode" / "tasks" / "bg_1.out"),
            },
        ),
        workspace=tmp_path,
    )

    assert _slash(text) == (
        "[bash] Started background task bg_1 (running), "
        "output .onecode/tasks/bg_1.out"
    )


def test_write_file_create_update_paths_and_diff_truncated(tmp_path: Path) -> None:
    created = render_tool_result(
        _result(
            "write_file",
            {
                "path": str(tmp_path / "docs" / "new.md"),
                "operation": "create",
                "line_count": 5,
            },
        ),
        workspace=tmp_path,
    )
    updated = render_tool_result(
        _result(
            "write_file",
            {
                "path": str(tmp_path / "docs" / "old.md"),
                "operation": "update",
                "line_count": 9,
                "diff_truncated": True,
            },
        ),
        workspace=tmp_path,
    )

    assert _slash(created) == "[write_file] Created docs/new.md (5 line(s))"
    assert _slash(updated) == (
        "[write_file] Updated docs/old.md (9 line(s), diff truncated)"
    )
    assert "call_1" not in created + updated


def test_edit_file_success_uses_full_relative_path(tmp_path: Path) -> None:
    text = render_tool_result(
        _result(
            "edit_file",
            {
                "path": str(tmp_path / "ui" / "cli" / "renderer.py"),
                "replacement_count": 1,
            },
        ),
        workspace=tmp_path,
    )

    assert _slash(text) == (
        "[edit_file] Edited ui/cli/renderer.py with 1 replacement(s)"
    )
    assert "call_1" not in text


def test_renderer_entry_uses_fallback_without_workspace(tmp_path: Path) -> None:
    text = render_tool_result_summary(
        _result("read_file", {"path": str(tmp_path / "app.py")})
    )

    assert text == "\n[read_file] call call_1"
