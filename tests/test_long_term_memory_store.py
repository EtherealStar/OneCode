from __future__ import annotations

from services.memory.auto_store import LongTermMemoryStore
from services.memory.paths import is_auto_memory_markdown_path, is_auto_memory_path


def test_long_term_memory_store_scans_topics_and_rebuilds_index(tmp_path):
    workspace = tmp_path / "repo"
    store = LongTermMemoryStore(workspace)
    store.ensure_exists()
    (store.memory_dir / "user-tabs.md").write_text(
        "---\nname: User tabs\ndescription: Prefers tabs\ntype: user\n---\nUse tabs.",
        encoding="utf-8",
    )

    catalog = store.scan()

    assert len(catalog) == 1
    assert catalog[0].relative_path == "user-tabs.md"
    assert catalog[0].type == "user"
    store.rebuild_entrypoint()
    assert "- [User tabs](user-tabs.md) - Prefers tabs" in store.read_entrypoint()
    assert is_auto_memory_path(store.memory_dir / "user-tabs.md", workspace)
    assert is_auto_memory_markdown_path(store.memory_dir / "user-tabs.md", workspace)
    assert not is_auto_memory_path(workspace / ".onecode" / "settings.json", workspace)


def test_entrypoint_truncates_by_line_count(tmp_path):
    workspace = tmp_path / "repo"
    store = LongTermMemoryStore(workspace)
    store.ensure_exists()
    store.entrypoint_path.write_text("\n".join(f"- item {i}" for i in range(220)), encoding="utf-8")

    text, truncated = store.truncated_entrypoint(max_lines=200, max_chars=25_000)

    assert truncated is True
    assert "- item 199" in text
    assert "- item 200" not in text
    assert "truncated" in text.lower()
