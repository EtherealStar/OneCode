from __future__ import annotations

from services.subagents import BUILT_IN_AGENTS


def test_builtin_subagent_definitions_match_first_version_scope() -> None:
    assert set(BUILT_IN_AGENTS) == {"general-purpose", "Explore", "Plan", "fork"}
    assert BUILT_IN_AGENTS["fork"].hidden is True
    assert BUILT_IN_AGENTS["general-purpose"].read_only is False
    assert BUILT_IN_AGENTS["Explore"].read_only is True
    assert BUILT_IN_AGENTS["Plan"].read_only is True
    assert "agent" in BUILT_IN_AGENTS["general-purpose"].disallowed_tools
    assert "edit_file" in BUILT_IN_AGENTS["Explore"].disallowed_tools
    assert "edit_file" in BUILT_IN_AGENTS["Plan"].disallowed_tools
