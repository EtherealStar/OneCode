from __future__ import annotations

from pathlib import Path

from services.skills import (
    SkillCommand,
    clear_skill_caches,
    get_commands,
    init_bundled_skills,
)


def write_skill(root: Path, name: str, content: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_loader_returns_empty_when_skill_dirs_do_not_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ONECODE_HOME", str(tmp_path / "home"))
    init_bundled_skills(())
    clear_skill_caches()

    assert get_commands(tmp_path / "workspace") == ()


def test_loader_discovers_project_skill_and_frontmatter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ONECODE_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    write_skill(
        workspace / ".onecode" / "skills",
        "code-review",
        """---
description: Review code changes
when_to_use: asked to review a diff
allowed-tools:
  - grep
  - read_file
context: fork
model: test-model
paths: "*.py, docs/**"
---
# Review

Follow this review checklist.
""",
    )
    clear_skill_caches()

    commands = get_commands(workspace)

    assert [command.name for command in commands] == ["code-review"]
    command = commands[0]
    assert command.source == "project"
    assert command.description == "Review code changes"
    assert command.when_to_use == "asked to review a diff"
    assert command.allowed_tools == ("grep", "read_file")
    assert command.context == "fork"
    assert command.model == "test-model"
    assert command.paths == ("*.py", "docs/**")
    assert "Follow this review checklist." in command.content


def test_loader_precedence_project_over_user_over_bundled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("ONECODE_HOME", str(home))
    init_bundled_skills(
        (
            SkillCommand(
                name="shared",
                description="bundled",
                content="bundled body",
                source="bundled",
            ),
        )
    )
    write_skill(home / "skills", "shared", "---\ndescription: user\n---\nuser body")
    write_skill(
        workspace / ".onecode" / "skills",
        "shared",
        "---\ndescription: project\n---\nproject body",
    )
    clear_skill_caches()

    commands = get_commands(workspace)

    assert len(commands) == 1
    assert commands[0].source == "project"
    assert commands[0].description == "project"
    assert commands[0].content == "project body"
