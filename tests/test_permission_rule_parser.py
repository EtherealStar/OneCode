from __future__ import annotations

import pytest

from services.permissions import (
    PermissionRuleValue,
    permission_rule_value_from_string,
    permission_rule_value_to_string,
)


def test_permission_rule_parser_parses_tool_and_content_rules() -> None:
    assert permission_rule_value_from_string("bash") == PermissionRuleValue("bash")
    assert permission_rule_value_from_string("bash(npm run:*)") == PermissionRuleValue(
        "bash",
        "npm run:*",
    )


def test_permission_rule_parser_round_trips_escaped_content() -> None:
    value = PermissionRuleValue("bash", r"echo \(ok\) \\ done")
    serialized = permission_rule_value_to_string(value)

    assert serialized == r"bash(echo \\\(ok\\\) \\\\ done)"
    assert permission_rule_value_from_string(serialized) == value


def test_permission_rule_parser_rejects_invalid_rules() -> None:
    with pytest.raises(ValueError):
        permission_rule_value_from_string("")
    with pytest.raises(ValueError):
        permission_rule_value_from_string("bash(rule) trailing")
    with pytest.raises(ValueError):
        permission_rule_value_from_string("bad tool")
