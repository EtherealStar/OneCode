from __future__ import annotations

from pathlib import Path


def test_core_loop_does_not_import_subagent_modules() -> None:
    source = Path("core/loop.py").read_text(encoding="utf-8")

    assert "services.subagents" not in source


def test_subagents_package_init_does_not_import_runner() -> None:
    source = Path("services/subagents/__init__.py").read_text(encoding="utf-8")

    assert "services.subagents.runner" not in source
    assert "SubagentRunner" not in source
