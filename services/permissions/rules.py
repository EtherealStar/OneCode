"""Persistent permission rule values and serialization helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PermissionBehavior = Literal["allow", "deny", "ask"]
PermissionUpdateType = Literal["addRules", "removeRules", "replaceRules"]
PermissionUpdateDestination = Literal["projectSettings", "session"]


@dataclass(frozen=True)
class PermissionRuleValue:
    tool_name: str
    rule_content: str | None = None


@dataclass(frozen=True)
class PermissionRule:
    source: str
    behavior: PermissionBehavior
    value: PermissionRuleValue


@dataclass(frozen=True)
class PermissionUpdate:
    type: PermissionUpdateType
    rules: tuple[PermissionRuleValue, ...]
    behavior: PermissionBehavior
    destination: PermissionUpdateDestination


def permission_rule_value_from_string(raw: str) -> PermissionRuleValue:
    """Parse ``tool`` or ``tool(rule content)`` permission rule strings."""

    text = raw.strip()
    if not text:
        raise ValueError("Permission rule must not be empty.")

    open_index = _find_unescaped(text, "(")
    close_index = _find_unescaped(text, ")")
    if open_index is None:
        if close_index is not None:
            raise ValueError(f"Unexpected ')' in permission rule: {raw}")
        return PermissionRuleValue(tool_name=_validate_tool_name(text))

    tool_name = _validate_tool_name(text[:open_index].strip())
    if close_index != len(text) - 1:
        raise ValueError(
            "Permission rule content must end with an unescaped ')' character."
        )
    content = _unescape_rule_content(text[open_index + 1 : close_index])
    return PermissionRuleValue(tool_name=tool_name, rule_content=content)


def permission_rule_value_to_string(value: PermissionRuleValue) -> str:
    tool_name = _validate_tool_name(value.tool_name.strip())
    if value.rule_content is None:
        return tool_name
    return f"{tool_name}({_escape_rule_content(value.rule_content)})"


def _validate_tool_name(value: str) -> str:
    if not value:
        raise ValueError("Permission rule tool name must not be empty.")
    if any(char.isspace() for char in value):
        raise ValueError(f"Permission rule tool name must not contain whitespace: {value}")
    if any(char in value for char in "()\\"):
        raise ValueError(f"Permission rule tool name contains invalid characters: {value}")
    return value


def _find_unescaped(text: str, needle: str) -> int | None:
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == needle:
            return index
    return None


def _unescape_rule_content(text: str) -> str:
    result: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            if char not in {"\\", "(", ")"}:
                result.append("\\")
            result.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        result.append(char)
    if escaped:
        result.append("\\")
    return "".join(result)


def _escape_rule_content(text: str) -> str:
    result: list[str] = []
    for char in text:
        if char in {"\\", "(", ")"}:
            result.append("\\")
        result.append(char)
    return "".join(result)
