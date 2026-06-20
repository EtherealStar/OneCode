"""Shared attachment path ignore rules."""

from __future__ import annotations

from pathlib import Path


ATTACHMENT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)


def is_ignored_attachment_dir(path: Path) -> bool:
    return path.name.casefold() in ATTACHMENT_IGNORED_DIRS
