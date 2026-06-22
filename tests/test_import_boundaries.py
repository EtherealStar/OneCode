from __future__ import annotations

from pathlib import Path


def test_core_loop_does_not_import_subagent_modules() -> None:
    source = Path("core/loop.py").read_text(encoding="utf-8")

    assert "services.subagents" not in source


def test_subagents_package_init_does_not_import_runner() -> None:
    source = Path("services/subagents/__init__.py").read_text(encoding="utf-8")

    assert "services.subagents.runner" not in source
    assert "SubagentRunner" not in source


def test_core_loop_does_not_import_plan_modules() -> None:
    """The plan lifecycle is owned by services.plans, not the core loop."""

    source = Path("core/loop.py").read_text(encoding="utf-8")

    assert "services.plans" not in source
    assert "tools.enter_plan_mode" not in source
    assert "tools.exit_plan_mode" not in source
    assert "tools.ask_user_question" not in source


def test_services_tools_does_not_import_plan_tools_directly() -> None:
    """services/tools stays at the descriptor level; concrete plan tools live
    in tools/ and only get wired into the registry at the CLI/app layer."""

    for path in (
        Path("services/tools/executor.py"),
        Path("services/tools/registry.py"),
        Path("services/tools/schema.py"),
        Path("services/tools/types.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "tools.enter_plan_mode" not in source
        assert "tools.exit_plan_mode" not in source
        assert "tools.ask_user_question" not in source
