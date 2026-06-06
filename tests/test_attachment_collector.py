from __future__ import annotations

import asyncio
from pathlib import Path

from core.runtime_state import RuntimeState
from services.attachments.collector import AttachmentCollector, AttachmentFileReader
from services.guard import SandboxBoundary, SandboxGuard
from services.permissions import PermissionPolicy, SessionPermissionStore
from services.tools.file_state import FileStateCache


def _collector(workspace: Path) -> AttachmentCollector:
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    policy = PermissionPolicy(SessionPermissionStore())
    reader = AttachmentFileReader(guard=guard, permission_policy=policy)
    return AttachmentCollector(workspace=workspace, reader=reader)


def _collector_with_cache(
    workspace: Path,
    cache: FileStateCache,
) -> AttachmentCollector:
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    policy = PermissionPolicy(SessionPermissionStore())
    reader = AttachmentFileReader(guard=guard, permission_policy=policy)
    return AttachmentCollector(
        workspace=workspace,
        reader=reader,
        file_state_cache=cache,
    )


def test_collects_file_attachment_with_line_range(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")

    attachments = asyncio.run(
        _collector(tmp_path).collect_for_user_turn(
            "summarize @note.txt#L2-3",
            RuntimeState(),
            (),
        )
    )

    assert len(attachments) == 1
    attachment = attachments[0]["attachment"]
    assert attachment["type"] == "file"
    assert attachment["path"].endswith("note.txt")
    assert attachment["content"] == "2\ttwo\n3\tthree"
    assert attachment["offset"] == 2
    assert attachment["limit"] == 2


def test_collects_directory_attachment_sorted(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "b.py").write_text("", encoding="utf-8")
    (src / "a.py").write_text("", encoding="utf-8")

    attachments = asyncio.run(
        _collector(tmp_path).collect_for_user_turn(
            "list @src",
            RuntimeState(),
            (),
        )
    )

    attachment = attachments[0]["attachment"]
    assert attachment["type"] == "directory"
    assert attachment["entries"] == ["a.py", "b.py"]


def test_ambiguous_name_becomes_resolution_error_attachment(
    tmp_path: Path,
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "same.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b" / "same.txt").write_text("b", encoding="utf-8")

    attachments = asyncio.run(
        _collector(tmp_path).collect_for_user_turn(
            "read @same.txt",
            RuntimeState(),
            (),
        )
    )

    attachment = attachments[0]["attachment"]
    assert attachment["type"] == "attachment_error"
    assert attachment["error"] == "ambiguous"


def test_detects_edited_text_file_on_next_turn(tmp_path: Path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("one\ntwo\n", encoding="utf-8")
    collector = _collector(tmp_path)
    state = RuntimeState()

    asyncio.run(collector.collect_for_user_turn("read @note.txt", state, ()))
    note.write_text("one\nTWO\n", encoding="utf-8")
    attachments = asyncio.run(
        collector.collect_for_user_turn(
            "continue",
            state,
            (),
            is_main_thread=True,
        )
    )

    assert len(attachments) == 1
    attachment = attachments[0]["attachment"]
    assert attachment["type"] == "edited_text_file"
    assert "--- before" in attachment["diff"]
    assert "+TWO" in attachment["diff"]


def test_detects_changes_from_tool_owned_cache(tmp_path: Path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("one\ntwo\n", encoding="utf-8")
    cache = FileStateCache()
    cache.snapshot_path(note)
    collector = _collector_with_cache(tmp_path, cache)

    note.write_text("one\nTWO\n", encoding="utf-8")
    attachments = asyncio.run(
        collector.collect_for_user_turn(
            "continue",
            RuntimeState(),
            (),
            is_main_thread=True,
        )
    )

    assert len(attachments) == 1
    attachment = attachments[0]["attachment"]
    assert attachment["type"] == "edited_text_file"
    assert "+TWO" in attachment["diff"]
