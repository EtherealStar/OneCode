"""TUI 工具呈现注册表。

该注册表是仅基于 OneCode 公共既定事实的策略分发器：包含工具名称、
结构化输入映射、结果预览文本以及结果是否为错误。
它绝不导入具体的工具输入模型或引用领域类型。

未知工具和 MCP 工具将降级回退到通用呈现器，保留可用的摘要，并在失败时提供经过脱敏的首行错误摘要。
缺少专用呈现器绝不能掩盖错误摘要信息。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text

_SECRET_KEY = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|credential)", re.IGNORECASE
)
_PATH_KEY = re.compile(r"(^|[_-])(path|cwd|home|file)([_-]|$)", re.IGNORECASE)
_INLINE_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^,\s]+)"
)


def redact_sensitive(value: object, *, key: str = "") -> object:
    """对文本中的机密键名、内联机密以及绝对路径进行脱敏处理。"""

    if _SECRET_KEY.search(key) or _PATH_KEY.search(key):
        return "[已隐藏]"
    if isinstance(value, Mapping):
        return {str(k): redact_sensitive(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        value = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}=[已隐藏]", value)
        value = re.sub(r"(?i)([A-Z]:\\|/)(?:[^\s,;]+)", "[路径已隐藏]", value)
        return value[:2000] + ("…" if len(value) > 2000 else "")
    return value


def first_line_excerpt(content: str, limit: int = 120) -> str:
    """返回首个非空且经过脱敏的行，并按限制长度截断以供展示。"""

    safe = redact_sensitive(content, key="result")
    first = next((line.strip() for line in str(safe).splitlines() if line.strip()), "")
    return _truncate(first, limit)


@dataclass(frozen=True, slots=True)
class ToolPresentation:
    summary: str
    body: str
    is_error: bool = False
    preview_lines: tuple[Text, ...] = ()


#: ``(name, tool_input, result_preview, is_error) -> ToolPresentation`` 回调签名。
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
            except Exception:  # noqa: BLE001, S110
                pass
        return _generic_presenter(name, tool_input, result_preview, is_error)


def build_tool_presentation_registry(
    workspace_root: Path | None = None,
) -> ToolPresentationRegistry:
    """创建基于输入的内置工具呈现器注册表。"""

    del workspace_root  # 保留以维持调用兼容性；路径保持字面处理
    registry = ToolPresentationRegistry()

    registry.register(
        "read_file", _generic_path_presenter(lambda values: values.get("path"))
    )
    registry.register(
        "read_docs", _generic_path_presenter(lambda values: values.get("path"))
    )
    registry.register("write_file", _write_presenter)
    registry.register("edit_file", _edit_presenter)
    registry.register("glob", _search_presenter(lambda values: values.get("pattern")))
    registry.register("grep", _search_presenter(lambda values: values.get("pattern")))
    registry.register("bash", _bash_presenter)
    return registry


# --- 各具体呈现器 ----------------------------------------------------------


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


# --- 辅助函数 --------------------------------------------------------------


def _body(result_preview: str | None, tool_input: Mapping[str, object] | None) -> str:
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


def _arguments_summary(
    tool_input: Mapping[str, object] | None, *, limit: int = 120
) -> str:
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
