"""Resume selector and session history views."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from rich.console import Group
from rich.table import Table

from ui.cli.resume import SessionSummary
from ui.cli.views.common import display_path, titled_section


def render_session_summaries(
    summaries: Iterable[SessionSummary],
    workspace: Path,
) -> Group:
    table = Table(box=None, show_header=True, header_style="onecode.subtle")
    table.add_column("session")
    table.add_column("updated")
    table.add_column("messages", justify="right")
    table.add_column("path")
    items = list(summaries)
    if not items:
        table.add_row("none", "", "", "No sessions found.")
    for summary in items:
        table.add_row(
            summary.title,
            _format_updated_at(summary.updated_at),
            str(summary.message_count),
            display_path(summary.messages_path, workspace),
        )
    return titled_section("Resume", table, style="onecode.info")


def _format_updated_at(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")
