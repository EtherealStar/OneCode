from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.permissions import (
    PermissionRuleValue,
    PermissionUpdate,
    ProjectPermissionSettingsStore,
)


def test_project_permission_settings_loads_rules(tmp_path: Path) -> None:
    settings_path = tmp_path / ".onecode" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text(
        json.dumps(
            {
                "permissions": {
                    "deny": ["edit_file"],
                    "ask": ["bash"],
                    "allow": ["bash(npm run:*)"],
                }
            }
        ),
        encoding="utf-8",
    )

    rules = ProjectPermissionSettingsStore(settings_path).load_rules()

    assert [(rule.behavior, rule.value.tool_name, rule.value.rule_content) for rule in rules] == [
        ("allow", "bash", "npm run:*"),
        ("deny", "edit_file", None),
        ("ask", "bash", None),
    ]
    assert {rule.source for rule in rules} == {"project_settings"}


def test_project_permission_settings_adds_and_removes_idempotently(
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / ".onecode" / "settings.json"
    store = ProjectPermissionSettingsStore(settings_path)
    update = PermissionUpdate(
        type="addRules",
        rules=(PermissionRuleValue("bash", "npm run:*"),),
        behavior="allow",
        destination="projectSettings",
    )

    store.apply_update(update)
    store.apply_update(update)

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == ["bash(npm run:*)"]

    store.apply_update(
        PermissionUpdate(
            type="removeRules",
            rules=(PermissionRuleValue("bash", "npm run:*"),),
            behavior="allow",
            destination="projectSettings",
        )
    )

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == []


def test_project_permission_settings_preserves_other_fields(tmp_path: Path) -> None:
    settings_path = tmp_path / ".onecode" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text('{"model": "test"}', encoding="utf-8")

    ProjectPermissionSettingsStore(settings_path).apply_update(
        PermissionUpdate(
            type="addRules",
            rules=(PermissionRuleValue("edit_file"),),
            behavior="deny",
            destination="projectSettings",
        )
    )

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["model"] == "test"
    assert data["permissions"]["deny"] == ["edit_file"]


def test_project_permission_settings_rejects_bad_json_without_overwrite(
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / ".onecode" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        ProjectPermissionSettingsStore(settings_path).apply_update(
            PermissionUpdate(
                type="addRules",
                rules=(PermissionRuleValue("bash"),),
                behavior="ask",
                destination="projectSettings",
            )
        )

    assert settings_path.read_text(encoding="utf-8") == "{bad json"


def test_project_permission_settings_rejects_non_string_arrays(
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / ".onecode" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text('{"permissions": {"allow": [1]}}', encoding="utf-8")

    with pytest.raises(ValueError, match="permissions.allow"):
        ProjectPermissionSettingsStore(settings_path).load_rules()
