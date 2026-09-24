from __future__ import annotations

import ast
from pathlib import Path


def _imported_modules(path: Path) -> set[str]:
    """Return top-level module names imported by a Python source file."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def _source_files(package: str) -> list[Path]:
    return sorted(Path(package).rglob("*.py"))


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


def test_core_services_infrastructure_do_not_import_application_or_ui() -> None:
    """M5.2 boundary: lower layers never depend on application/UI/Textual."""

    for package in ("core", "services", "infrastructure"):
        for path in _source_files(package):
            modules = _imported_modules(path)
            assert "application" not in modules, path
            assert "ui" not in modules, path
            assert "textual" not in modules, path


def test_application_does_not_import_ui_or_textual() -> None:
    for path in _source_files("application"):
        modules = _imported_modules(path)
        assert "ui" not in modules, path
        assert "textual" not in modules, path


def test_projection_does_not_import_textual_or_old_reducer() -> None:
    for path in (
        Path("ui/tui/projection.py"),
        Path("ui/tui/projection_types.py"),
    ):
        modules = _imported_modules(path)
        assert "textual" not in modules, path
        source = path.read_text(encoding="utf-8")
        assert "cli.terminal" not in source, path
        assert "stream_reducer" not in source, path


def test_production_code_does_not_import_reference_or_miniagent() -> None:
    for package in ("core", "services", "infrastructure", "application", "ui"):
        for path in _source_files(package):
            source = path.read_text(encoding="utf-8")
            assert "from reference" not in source, path
            assert "import reference" not in source, path
            assert "miniagent" not in source, path
