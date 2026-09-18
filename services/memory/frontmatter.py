"""长期记忆文件的轻量 Markdown frontmatter 辅助函数。"""

from __future__ import annotations

import re
from typing import Any


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析文件开头的微型类似 YAML 的 frontmatter 块。"""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, normalized
    block: list[str] = []
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return parse_frontmatter_lines(block), "\n".join(lines[index + 1 :])
        block.append(line)
    return {}, normalized


def parse_frontmatter_lines(lines: list[str]) -> dict[str, Any]:
    """解析键值对行以及简单的缩进 YAML 列表项。"""

    values: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
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
        else:
            values[key] = _strip_quotes(value)
    return values


def string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_values = value if isinstance(value, list) else str(value).split(",")
    items: list[str] = []
    for item in raw_values:
        cleaned = str(item).strip()
        if cleaned and cleaned not in items:
            items.append(cleaned)
    return tuple(items)


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return _strip_quotes(str(value)).strip()


def strip_html_comments(text: str) -> str:
    """从模型可见的指令文本中移除所有 HTML 注释片段。"""

    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _strip_quotes(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1]
    return cleaned
