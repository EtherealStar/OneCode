from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

RUNTIME_PACKAGES = {
    "application",
    "core",
    "infrastructure",
    "prompts",
    "services",
    "tools",
    "ui",
    "utils",
}

NON_RUNTIME_PACKAGES = {"reference", "tests", "docs", "lessons", "assets"}


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


def test_console_script_entry_point() -> None:
    """Installing the package must expose the `onecode` terminal command."""

    scripts = _pyproject()["project"]["scripts"]

    assert scripts["onecode"] == "ui.cli.app:main"


def test_build_backend_is_setuptools() -> None:
    build_system = _pyproject()["build-system"]

    assert build_system["build-backend"] == "setuptools.build_meta"


def test_packages_find_includes_all_runtime_packages() -> None:
    """The flat layout must explicitly ship every runtime top-level package."""

    find = _pyproject()["tool"]["setuptools"]["packages"]["find"]
    prefixes = {pattern[:-1] for pattern in find["include"] if pattern.endswith("*")}

    assert prefixes == RUNTIME_PACKAGES


def test_packages_find_excludes_non_runtime_directories() -> None:
    find = _pyproject()["tool"]["setuptools"]["packages"]["find"]
    prefixes = {pattern[:-1] for pattern in find["exclude"] if pattern.endswith("*")}

    assert NON_RUNTIME_PACKAGES <= prefixes


def test_tui_stylesheet_is_package_data() -> None:
    """The Textual stylesheet is a non-Python file and must be declared."""

    package_data = _pyproject()["tool"]["setuptools"]["package-data"]

    assert "*.tcss" in package_data["ui.tui"]


def test_ui_cli_app_main_is_importable_and_callable() -> None:
    from ui.cli.app import main

    assert callable(main)


def test_tui_stylesheet_is_installed_resource() -> None:
    stylesheet = resources.files("ui.tui").joinpath("onecode.tcss")

    assert stylesheet.is_file()


def test_tui_app_resolves_stylesheet_relative_to_package() -> None:
    from ui.tui.app import OneCodeTuiApp

    assert OneCodeTuiApp.CSS_PATH == "onecode.tcss"
