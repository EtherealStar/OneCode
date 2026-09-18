"""Tool presentation registry for the TUI.

The registry is a policy dispatcher over OneCode public facts only: a tool
name, its structured input mapping, its result preview text, and whether the
result is an error. It never imports concrete tool input models or reference
domain types.

Unknown and MCP tools fall through to a generic presenter that keeps a usable
summary and, on failure, a redacted first-line error excerpt. Missing a
specialised presenter must never hide the error summary.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text

_SECRET_KEY = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|credential)", re.I
)
_PATH_KEY = re.compile(r"(^|[_-])(path|cwd|home|file)([_-]|$)", re.I)
_INLINE_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^,\s]+)"
)


def redact_sensitive(value: object, *, key: str = "") -> object:
    """Redact secret-like keys, inline secrets, and absolute paths in text."""

    if _SECRET_KEY.search(key) or _PATH_KEY.search(key):
        return "[已隐藏]"
    if isinstance(value, Mapping):
        return {str(k): redact_sensitive(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        value = _INLINE_SECRET.sub(
            lambda match: f"{match.group(1)}=[已隐藏]", value
        )
        value = re.sub(r"(?i)([A-Z]:\\|/)(?:[^\s,;]+)", "[路径已隐藏]", value)
        return value[:2000] + ("…" if len(value) > 2000 else "")
    return value


def first_line_excerpt(content: str, limit: int = 120) -> str:
    """Return the first non-empty, redacted line, truncated for display."""

    safe = redact_sensitive(content, key="result")
    first = next((line.strip() for line in str(safe).splitlines() if line.strip()), "")
    return _truncate(first, limit)


@dataclass(frozen=True, slots=True)
class ToolPresentation:
    summary: str
    body: str
    is_error: bool = False
    preview_lines: tuple[Text, ...] = ()


#: ``(name, tool_input, result_preview, is_error) -> ToolPresentation``.
ToolPresenter = Callable[
    [str, Mapping[str, object] | None, str | None, bool], ToolPresentation
]


class ToolPresentationRegistry:
    def __init__(self) -> None:
        self._presenters: dict[str, ToolPresenter] = {}

    def register(self, name: str, presenter: ToolPresenter) -> None:
        self._presenters[name] = presenter

    def present(
        self,
        *,
        name: str,
        tool_input: Mapping[str, object] | None,
        result_preview: str | None,
        is_error: bool,
    ) -> ToolPresentation:
        presenter = self._presenters.get(name)
        if presenter is not None:
            try:
                return presenter(name, tool_input, result_preview, is_error)
            except Exception:
                pass
        return _generic_presenter(name, tool_input, result_preview, is_error)


def build_tool_presentation_registry(
    workspace_root: Path | None = None,
) -> ToolPresentationRegistry:
    """Create the registry of built-in input-based presenters."""

    del workspace_root  # kept for call-site compatibility; paths stay lexical
    registry = ToolPresentationRegistry()

    registry.register("read_file", _generic_path_presenter(lambda values: values.get("path")))
    registry.register("read_docs", _generic_path_presenter(lambda values: values.get("path")))
    registry.register("write_file", _write_presenter)
    registry.register("edit_file", _edit_presenter)
    registry.register("glob", _search_presenter(lambda values: values.get("pattern")))
    registry.register("grep", _search_presenter(lambda values: values.get("pattern")))
    registry.register("bash", _bash_presenter)
    return registry


# --- presenters ------------------------------------------------------------


def _generic_presenter(
    name: str,
    tool_input: Mapping[str, object] | None,
    result_preview: str | None,
    is_error: bool,
) -> ToolPresentation:
    summary = _arguments_summary(tool_input)
    body = _body(result_preview, tool_input)
    return ToolPresentation(summary=summary, body=body, is_error=is_error)


def _generic_path_presenter(
    extract: Callable[[Mapping[str, object]], object],
) -> ToolPresenter:
    def presenter(
        name: str,
        tool_input: Mapping[str, object] | None,
        result_preview: str | None,
        is_error: bool,
    ) -> ToolPresentation:
        values = tool_input or {}
        summary = _truncate(str(extract(values) or ""), 100)
        return ToolPresentation(
            summary=summary,
            body=_body(result_preview, tool_input),
            is_error=is_error,
        )

    return presenter


def _write_presenter(
    name: str,
    tool_input: Mapping[str, object] | None,
    result_preview: str | None,
    is_error: bool,
) -> ToolPresentation:
    values = tool_input or {}
    summary = _truncate(str(values.get("path") or ""), 100)
    operation = values.get("operation")
    if operation in {"created", "replaced", "create"}:
        summary = f"{summary} · {operation}"
    return ToolPresentation(summary, _body(result_preview, tool_input), is_error)


def _edit_presenter(
    name: str,
    tool_input: Mapping[str, object] | None,
    result_preview: str | None,
    is_error: bool,
) -> ToolPresentation:
    values = tool_input or {}
    operation = "delete" if values.get("operation") == "delete" else "replace"
    path = _truncate(str(values.get("path") or ""), 90)
    summary = f"{operation} {path}".strip()
    count = values.get("replace_all")
    if operation == "replace" and count is True:
        summary += " · all"
    return ToolPresentation(summary, _body(result_preview, tool_input), is_error)


def _search_presenter(
    extract: Callable[[Mapping[str, object]], object],
) -> ToolPresenter:
    def presenter(
        name: str,
        tool_input: Mapping[str, object] | None,
        result_preview: str | None,
        is_error: bool,
    ) -> ToolPresentation:
        values = tool_input or {}
        pattern = _truncate(str(extract(values) or ""), 50)
        root = str(values.get("path") or "")
        summary = f'"{pattern}"' + (f" · {_truncate(root, 60)}" if root else "")
        return ToolPresentation(summary, _body(result_preview, tool_input), is_error)

    return presenter


def _bash_presenter(
    name: str,
    tool_input: Mapping[str, object] | None,
    result_preview: str | None,
    is_error: bool,
) -> ToolPresentation:
    values = tool_input or {}
    command = str(values.get("command") or "")
    first_lines = "\n".join(command.splitlines()[:2])
    summary = _truncate(" ".join(first_lines.split()), 100)
    return ToolPresentation(summary, _body(result_preview, tool_input), is_error)


# --- helpers ---------------------------------------------------------------


def _body(
    result_preview: str | None, tool_input: Mapping[str, object] | None
) -> str:
    if result_preview is not None:
        return _safe_text(result_preview)
    if not tool_input:
        return "参数不可显示"
    try:
        return json.dumps(
            redact_sensitive(dict(tool_input)), ensure_ascii=False, indent=2
        )[:2000]
    except (TypeError, ValueError):
        return "参数不可显示"


def _safe_text(value: str) -> str:
    return str(redact_sensitive(value, key="result"))


def _arguments_summary(tool_input: Mapping[str, object] | None, *, limit: int = 120) -> str:
    if not tool_input:
        return ""
    parts: list[str] = []
    for key, value in tool_input.items():
        parts.append(f"{key}={_render_value(value)}")
        if sum(len(part) for part in parts) > limit:
            break
    return _truncate(" ".join(parts), limit)


def _render_value(value: object, *, inner_limit: int = 40) -> str:
    if isinstance(value, str):
        compact = " ".join(value.split())
        if len(compact) > inner_limit:
            return f'"{compact[: inner_limit - 1]}…"'
        return f'"{compact}"'
    if isinstance(value, (list, tuple)):
        return f"<{len(value)} items>"
    if isinstance(value, Mapping):
        return f"<{len(value)} keys>"
    return str(value)


def _truncate(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


__all__ = [
    "ToolPresentation",
    "ToolPresentationRegistry",
    "build_tool_presentation_registry",
    "first_line_excerpt",
    "redact_sensitive",
]
