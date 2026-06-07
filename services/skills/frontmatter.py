"""Small frontmatter parser for SKILL.md files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services.skills.types import SkillCommand, SkillContext, SkillSource


def parse_skill_markdown(
    text: str,
    *,
    source: SkillSource,
    root: Path | None,
    fallback_name: str,
) -> SkillCommand | None:
    """Parse a SKILL.md document into a normalized command object."""

    frontmatter, body = _split_frontmatter(text)
    name = _clean_string(frontmatter.get("name")) or _normalize_name(fallback_name)
    if not name:
        return None
    description = (
        _clean_string(frontmatter.get("description"))
        or _description_from_body(body)
        or name
    )
    context: SkillContext = (
        "fork" if _clean_string(frontmatter.get("context")).lower() == "fork" else "inline"
    )
    return SkillCommand(
        name=name,
        description=description,
        when_to_use=_clean_string(frontmatter.get("when_to_use")),
        content=body.strip(),
        source=source,
        root=root,
        allowed_tools=_string_list(frontmatter.get("allowed-tools")),
        context=context,
        model=_clean_string(frontmatter.get("model")),
        user_invocable=_bool_value(frontmatter.get("user-invocable"), default=True),
        disable_model_invocation=_bool_value(
            frontmatter.get("disable-model-invocation"),
            default=False,
        ),
        paths=_string_list(frontmatter.get("paths")),
        frontmatter_keys=frozenset(frontmatter.keys()),
    )


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Extract a tiny YAML-like frontmatter block if the file starts with one."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, normalized

    block: list[str] = []
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return _parse_frontmatter_lines(block), "\n".join(lines[index + 1 :])
        block.append(line)
    return {}, normalized


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, Any]:
    """Parse key/value lines and simple indented list items."""

    values: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if current_list_key and stripped.startswith("-"):
            item = stripped[1:].strip()
            if item:
                values.setdefault(current_list_key, []).append(_strip_quotes(item))
            continue
        current_list_key = None
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        value = raw_value.strip()
        if value == "":
            values[key] = []
            current_list_key = key
            continue
        values[key] = _strip_quotes(value)
    return values


def _description_from_body(body: str) -> str:
    """Use the first heading or non-empty paragraph as fallback description."""

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        return stripped[:250]
    return ""


def _string_list(value: Any) -> tuple[str, ...]:
    """Normalize comma-separated strings and simple YAML lists."""

    if value is None:
        return ()
    items: list[str] = []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).split(",")
    for item in raw_items:
        cleaned = str(item).strip()
        if cleaned:
            items.append(cleaned)
    return tuple(dict.fromkeys(items))


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return _strip_quotes(str(value)).strip()


def _strip_quotes(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1]
    return cleaned


def _normalize_name(value: str) -> str:
    return value.strip().lstrip("/")
