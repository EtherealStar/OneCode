"""Project-level permission settings stored in .onecode/settings.json."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from services.permissions.rules import (
    PermissionBehavior,
    PermissionRule,
    PermissionUpdate,
    permission_rule_value_from_string,
    permission_rule_value_to_string,
)


BEHAVIORS: tuple[PermissionBehavior, ...] = ("allow", "deny", "ask")


class ProjectPermissionSettingsStore:
    """Loads and updates persistent project permission rules."""

    def __init__(self, settings_path: Path) -> None:
        self.settings_path = settings_path

    def load_rules(self) -> tuple[PermissionRule, ...]:
        settings = self._read_settings()
        permissions = _permissions_object(settings, create=False)
        if permissions is None:
            return ()

        rules: list[PermissionRule] = []
        for behavior in BEHAVIORS:
            for raw_rule in _permission_strings(permissions, behavior):
                rules.append(
                    PermissionRule(
                        source="project_settings",
                        behavior=behavior,
                        value=permission_rule_value_from_string(raw_rule),
                    )
                )
        return tuple(rules)

    def apply_update(self, update: PermissionUpdate) -> None:
        if update.destination != "projectSettings":
            raise ValueError(
                "ProjectPermissionSettingsStore only accepts projectSettings updates."
            )
        settings = self._read_settings()
        permissions = _permissions_object(settings, create=True)
        assert permissions is not None

        for behavior in BEHAVIORS:
            _permission_strings(permissions, behavior)

        behavior = update.behavior
        current = [
            permission_rule_value_to_string(permission_rule_value_from_string(raw_rule))
            for raw_rule in _permission_strings(permissions, behavior)
        ]
        update_values = [
            permission_rule_value_to_string(rule_value) for rule_value in update.rules
        ]

        if update.type == "addRules":
            merged = _dedupe([*current, *update_values])
        elif update.type == "removeRules":
            removals = set(update_values)
            merged = [rule for rule in current if rule not in removals]
        elif update.type == "replaceRules":
            merged = _dedupe(update_values)
        else:
            raise ValueError(f"Unsupported permission update type: {update.type}")

        permissions[behavior] = merged
        self._write_settings(settings)

    def _read_settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            return {}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in project settings: {self.settings_path}: {exc.msg}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"Project settings must contain a JSON object: {self.settings_path}"
            )
        return data

    def _write_settings(self, settings: dict[str, Any]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _permissions_object(
    settings: dict[str, Any],
    *,
    create: bool,
) -> dict[str, Any] | None:
    value = settings.get("permissions")
    if value is None:
        if not create:
            return None
        value = {}
        settings["permissions"] = value
    if not isinstance(value, dict):
        raise ValueError("Project settings field 'permissions' must be an object.")
    return value


def _permission_strings(
    permissions: dict[str, Any],
    behavior: PermissionBehavior,
) -> list[str]:
    value = permissions.get(behavior, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(
            f"Project settings field 'permissions.{behavior}' must be a string array."
        )
    return value


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
