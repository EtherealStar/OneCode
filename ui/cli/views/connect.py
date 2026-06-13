"""Provider connection views."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ui.cli.theme import SYMBOLS
from ui.cli.views.common import titled_section


def render_connect_success(provider_name: str, model: str) -> Group:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="onecode.subtle", no_wrap=True)
    table.add_column()
    table.add_row("provider", provider_name)
    table.add_row("model", model)
    return titled_section(f"{SYMBOLS.success} Connected", table, style="onecode.success")


def render_connect_cancelled() -> Text:
    return Text("Connect cancelled.", style="onecode.subtle")
