from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.tasks import TaskStore, TaskStoreError, TaskUpdate


def test_create_get_list_uses_highwatermark_and_camel_case_json(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)

    first = store.create_task(
        "list-a",
        subject="Set up schema",
        description="Create tables.",
        active_form="Setting up schema",
        metadata={"priority": "high"},
    )
    store.delete_task("list-a", first.id)
    second = store.create_task(
        "list-a",
        subject="Build API",
        description="Implement endpoints.",
    )

    assert first.id == "1"
    assert second.id == "2"
    assert (tmp_path / ".onecode" / "tasks" / "list-a" / ".highwatermark").read_text(
        encoding="utf-8"
    ) == "2"
    payload = json.loads(store.task_path("list-a", "2").read_text(encoding="utf-8"))
    assert payload["activeForm"] is None
    assert payload["blockedBy"] == []
    assert store.get_task("list-a", "2") == second
    assert [task.id for task in store.list_tasks("list-a")] == ["2"]


def test_update_merges_metadata_and_deletes_none_values(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task = store.create_task(
        "list-a",
        subject="Write tests",
        description="Initial",
        metadata={"priority": "high", "old": "remove"},
    )

    updated = store.update_task(
        "list-a",
        task.id,
        TaskUpdate(
            description="Updated",
            owner="main",
            status="in_progress",
            metadata={"old": None, "area": "api"},
        ),
    )

    assert updated is not None
    assert updated.description == "Updated"
    assert updated.owner == "main"
    assert updated.status == "in_progress"
    assert updated.metadata == {"priority": "high", "area": "api"}


def test_block_task_maintains_both_dependency_directions(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    first = store.create_task("list-a", subject="Schema", description="A")
    second = store.create_task("list-a", subject="API", description="B")

    blocker, blocked = store.block_task("list-a", first.id, second.id)

    assert blocker.blocks == (second.id,)
    assert blocked.blocked_by == (first.id,)
    assert store.get_task("list-a", first.id).blocks == (second.id,)
    assert store.get_task("list-a", second.id).blocked_by == (first.id,)


def test_delete_task_cleans_other_dependency_references(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    first = store.create_task("list-a", subject="Schema", description="A")
    second = store.create_task("list-a", subject="API", description="B")
    third = store.create_task("list-a", subject="Tests", description="C")
    store.block_task("list-a", first.id, second.id)
    store.block_task("list-a", second.id, third.id)

    assert store.delete_task("list-a", second.id) is True

    assert store.get_task("list-a", first.id).blocks == ()
    assert store.get_task("list-a", third.id).blocked_by == ()
    assert store.get_task("list-a", second.id) is None


def test_block_task_rejects_self_dependency_and_cycles(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    first = store.create_task("list-a", subject="One", description="A")
    second = store.create_task("list-a", subject="Two", description="B")
    third = store.create_task("list-a", subject="Three", description="C")

    with pytest.raises(TaskStoreError, match="cannot block itself"):
        store.block_task("list-a", first.id, first.id)

    store.block_task("list-a", first.id, second.id)
    store.block_task("list-a", second.id, third.id)
    with pytest.raises(TaskStoreError, match="cycle"):
        store.block_task("list-a", third.id, first.id)


def test_claim_task_rejects_blocked_completed_or_claimed_tasks(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    first = store.create_task("list-a", subject="One", description="A")
    second = store.create_task("list-a", subject="Two", description="B")
    store.block_task("list-a", first.id, second.id)

    blocked = store.claim_task("list-a", second.id, "agent-a")
    assert blocked.claimed is False
    assert blocked.reason == "blocked"

    claimed = store.claim_task("list-a", first.id, "agent-a")
    assert claimed.claimed is True
    assert claimed.task is not None
    assert claimed.task.owner == "agent-a"
    other_owner = store.claim_task("list-a", first.id, "agent-b")
    assert other_owner.reason == "claimed"

    store.update_task("list-a", first.id, TaskUpdate(status="completed"))
    completed = store.claim_task("list-a", first.id, "agent-a")
    assert completed.reason == "completed"


def test_corrupt_highwatermark_and_task_json_raise_clear_errors(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    directory = store.tasks_dir("list-a")
    directory.mkdir(parents=True)
    (directory / ".highwatermark").write_text("not-an-int", encoding="utf-8")

    with pytest.raises(TaskStoreError, match="Invalid task highwatermark"):
        store.create_task("list-a", subject="One", description="A")

    (directory / ".highwatermark").write_text("0", encoding="utf-8")
    (directory / "1.json").write_text("{bad json", encoding="utf-8")
    with pytest.raises(TaskStoreError, match="Could not read task file"):
        store.list_tasks("list-a")
