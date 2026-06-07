from __future__ import annotations

from core.runtime_state import RuntimeState
from services.memory.instruction_loader import InstructionMemoryLoader


def test_instruction_loader_layers_includes_comments_and_conditional_rules(tmp_path):
    workspace = tmp_path / "repo"
    home = tmp_path / "home"
    src = workspace / "src"
    src.mkdir(parents=True)
    (home / ".onecode" / "rules").mkdir(parents=True)
    (workspace / ".onecode" / "rules").mkdir(parents=True)

    (home / ".onecode" / "ONECODE.md").write_text("user base", encoding="utf-8")
    (workspace / "included.md").write_text("included text", encoding="utf-8")
    (workspace / "ONECODE.md").write_text(
        "@./included.md\nproject <!-- hidden --> base",
        encoding="utf-8",
    )
    (workspace / ".onecode" / "rules" / "python.md").write_text(
        "---\npaths: src/*.py\n---\npython rule",
        encoding="utf-8",
    )
    (workspace / ".onecode" / "rules" / "docs.md").write_text(
        "---\npaths: docs/*.md\n---\ndocs rule",
        encoding="utf-8",
    )
    (workspace / "ONECODE.local.md").write_text("local override", encoding="utf-8")

    state = RuntimeState()
    state.metadata["files_read"] = {str(src / "app.py")}
    result = InstructionMemoryLoader(workspace, home=home).load(state, src)

    rendered = result.rendered_text
    assert "user base" in rendered
    assert "included text" in rendered
    assert "project  base" in rendered
    assert "hidden" not in rendered
    assert "python rule" in rendered
    assert "docs rule" not in rendered
    assert rendered.index("user base") < rendered.index("project  base")
    assert rendered.index("project  base") < rendered.index("local override")


def test_instruction_loader_stops_include_cycles(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "ONECODE.md").write_text("@./a.md\nroot", encoding="utf-8")
    (workspace / "a.md").write_text("@./ONECODE.md\na", encoding="utf-8")

    result = InstructionMemoryLoader(workspace, home=tmp_path / "home").load(
        RuntimeState(),
        workspace,
    )

    assert "root" in result.rendered_text
    assert any("repeated" in warning for warning in result.warnings)
