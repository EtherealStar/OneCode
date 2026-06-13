"""Read-only skill catalog view."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ui.cli.theme import SYMBOLS
from ui.cli.types import CliRuntime
from ui.cli.views.common import display_path, preview, titled_section


def render_skills(runtime: CliRuntime) -> Group:
    provider = runtime.skill_provider
    if provider is None:
        return titled_section(
            "Skills",
            Text(f"{SYMBOLS.info} Skills: disabled", style="onecode.subtle"),
            style="onecode.info",
        )
    skills = tuple(provider.visible_skills(runtime.state, runtime.workspace))
    table = Table(box=None, show_header=True, header_style="onecode.subtle")
    table.add_column("name")
    table.add_column("source")
    table.add_column("context")
    table.add_column("description")
    table.add_column("paths")
    table.add_column("allowed tools")
    if not skills:
        table.add_row("none", "", "", "No visible skills.", "", "")
    for skill in sorted(skills, key=lambda item: item.name):
        paths = ", ".join(skill.paths)
        if skill.root is not None and not paths:
            paths = display_path(skill.root, runtime.workspace)
        table.add_row(
            skill.name,
            skill.source,
            skill.context,
            preview(skill.description or skill.when_to_use or ""),
            preview(paths) if paths else "none",
            ", ".join(skill.allowed_tools) if skill.allowed_tools else "none",
        )
    return titled_section("Skills", table, style="onecode.info")
