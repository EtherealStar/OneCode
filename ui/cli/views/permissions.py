"""Read-only permission policy view."""

from __future__ import annotations

from collections import defaultdict

from rich.console import Group
from rich.table import Table
from rich.text import Text

from services.permissions import permission_rule_value_to_string
from ui.cli.theme import SYMBOLS
from ui.cli.types import CliRuntime
from ui.cli.views.common import display_path, preview, titled_section


def render_permissions(runtime: CliRuntime) -> Group:
    session = _session_table(runtime)
    project = _project_table(runtime)
    return titled_section(
        "Permissions",
        Group(session, Text(), project),
        style="onecode.permission",
    )


def _session_table(runtime: CliRuntime) -> object:
    store = runtime.permission_store
    if store is None:
        return Text(f"{SYMBOLS.info} Session permissions: disabled", style="onecode.subtle")
    snapshot = store.snapshot()
    table = Table(title="Session", box=None, show_header=False)
    table.add_column("field", style="onecode.subtle")
    table.add_column("value")
    table.add_row("allowed directories", str(len(snapshot.allowed_directories)))
    if snapshot.allowed_directories:
        grants = [
            f"{tool}:{operation}:{display_path(path, runtime.workspace)}"
            for tool, operation, path in snapshot.allowed_directories
        ]
        table.add_row("directory grants", preview(", ".join(grants)))
    table.add_row("allowed tools", _joined(snapshot.allowed_tools))
    table.add_row("allowed skills", _joined(snapshot.allowed_skills))
    table.add_row("denied tools", _joined(snapshot.denied_tools))
    table.add_row("denied skills", _joined(snapshot.denied_skills))
    table.add_row("disabled tools", _joined(snapshot.disabled_tools))
    return table


def _project_table(runtime: CliRuntime) -> object:
    policy = runtime.permission_policy
    project_store = policy.project_store if policy is not None else None
    if project_store is None:
        return Text(f"{SYMBOLS.info} Project permissions: disabled", style="onecode.subtle")
    table = Table(title="Project", box=None, show_header=False)
    table.add_column("field", style="onecode.subtle")
    table.add_column("value")
    table.add_row("settings", display_path(project_store.settings_path, runtime.workspace))
    try:
        rules = project_store.load_rules()
    except Exception as exc:
        table.add_row("error", f"{type(exc).__name__}: {exc}")
        return table
    by_behavior: dict[str, list[str]] = defaultdict(list)
    for rule in rules:
        by_behavior[rule.behavior].append(permission_rule_value_to_string(rule.value))
    for behavior in ("allow", "deny", "ask"):
        table.add_row(behavior, _joined(tuple(sorted(by_behavior[behavior]))))
    return table


def _joined(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"
