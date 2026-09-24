from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError
from rich.text import Text

from ...tools.calculator.tool import CalculatorInput
from ...tools.edit_file.tool import EditFileInput
from ...tools.glob.tool import GlobInput
from ...tools.grep.tool import GrepInput
from ...tools.read_docs.tool import ReadDocsInput
from ...tools.read_file.tool import ReadFileInput
from ...tools.todo_write.tool import TodoWriteInput
from ...tools.web_search.tool import WebSearchInput
from ...tools.write_file.tool import WriteFileInput
from .todo import render_todo_tool_preview


_SECRET_KEY = re.compile(r"(api[_-]?key|token|secret|password|authorization|credential)", re.I)
_PATH_KEY = re.compile(r"(^|[_-])(path|cwd|home|file)([_-]|$)", re.I)
_INLINE_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^,\s]+)")


def redact_sensitive(value: object, *, key: str = "") -> object:
    if _SECRET_KEY.search(key) or _PATH_KEY.search(key):
        return "[已隐藏]"
    if isinstance(value, dict):
        return {str(k): redact_sensitive(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        value = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}=[已隐藏]", value)
        value = re.sub(r"(?i)([A-Z]:\\|/)(?:[^\s,;]+)", "[路径已隐藏]", value)
        return value[:2000] + ("…" if len(value) > 2000 else "")
    return value


def first_line_excerpt(content: str, limit: int = 120) -> str:
    """失败结果只展示经过通用脱敏的第一条非空行。"""
    safe = redact_sensitive(content, key="result")
    first = next((line.strip() for line in str(safe).splitlines() if line.strip()), "")
    return _truncate(first, limit)


@dataclass(frozen=True, slots=True)
class ToolPresentation:
    summary: str
    body: str
    is_error: bool = False
    preview_lines: tuple[Text, ...] = ()


ToolPresenter = Callable[[str, Mapping[str, object] | None, str | None, bool], ToolPresentation]


class ToolPresentationRegistry:
    def __init__(self) -> None:
        self._presenters: dict[str, ToolPresenter] = {}

    def register(self, name: str, presenter: ToolPresenter) -> None:
        self._presenters[name] = presenter

    def present(
        self,
        *,
        name: str,
        arguments: str,
        result: str | None,
        result_output: Mapping[str, object] | None,
        is_error: bool,
    ) -> ToolPresentation:
        presenter = self._presenters.get(name)
        if presenter is not None:
            return presenter(arguments, result_output, result, is_error)
        # 未注册工具永远走保守回退，path-like 字段也不能因其他工具获准展示而放宽。
        try:
            decoded = json.loads(arguments)
            body = json.dumps(redact_sensitive(decoded), ensure_ascii=False, indent=2)[:2000]
        except (TypeError, ValueError):
            body = "参数不可显示"
        if result is not None:
            body = str(redact_sensitive(result, key="result"))
        return ToolPresentation("", body, is_error)


def build_tool_presentation_registry(workspace_root: Path) -> ToolPresentationRegistry:
    registry = ToolPresentationRegistry()

    def register(name: str, model: type[BaseModel], summary_builder) -> None:
        def presenter(
            arguments: str,
            result_output: Mapping[str, object] | None,
            result: str | None,
            is_error: bool,
        ) -> ToolPresentation:
            try:
                values = model.model_validate_json(arguments)
            except (ValidationError, ValueError, TypeError):
                return ToolPresentation("", _safe_body(result), is_error)
            summary = summary_builder(values, result_output)
            return ToolPresentation(_truncate(summary, 100), _safe_body(result), is_error)

        registry.register(name, presenter)

    register("read_file", ReadFileInput, lambda value, output: _read_file_summary(value, output, workspace_root))
    register("write_file", WriteFileInput, lambda value, output: _write_file_summary(value, output, workspace_root))
    register("edit_file", EditFileInput, lambda value, output: _edit_file_summary(value, output, workspace_root))
    register("glob", GlobInput, lambda value, output: _search_summary(value, output, workspace_root, count_key="match_count"))
    register("grep", GrepInput, lambda value, output: _search_summary(value, output, workspace_root, count_key="matched_line_count"))
    register("read_docs", ReadDocsInput, lambda value, output: _path_summary(value.path, output, workspace_root))
    register("calculator", CalculatorInput, _calculator_summary)
    register("web_search", WebSearchInput, _web_search_summary)

    def todo_presenter(arguments, result_output, result, is_error):
        try:
            values = TodoWriteInput.model_validate_json(arguments)
        except (ValidationError, ValueError, TypeError):
            return ToolPresentation("", _safe_body(result), is_error)
        previews = render_todo_tool_preview(values.todos) or (Text("清空任务列表", style="ui.todo.pending"),)
        return ToolPresentation("", _safe_body(result), is_error, previews)

    registry.register("todo_write", todo_presenter)
    return registry


def _metadata(output: Mapping[str, object] | None) -> Mapping[str, object]:
    value = output.get("metadata") if output is not None else None
    return value if isinstance(value, Mapping) else {}


def _data(output: Mapping[str, object] | None) -> Mapping[str, object]:
    value = output.get("data") if output is not None else None
    return value if isinstance(value, Mapping) else {}


def _display_path(value: object, workspace_root: Path) -> str:
    text = str(value).strip()
    if not text:
        return ""
    path = Path(os.path.normpath(text))
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.relative_to(workspace_root).as_posix()
    except ValueError:
        # 越界路径已在 ToolUse 或 ToolOutput 中经过授权；这里只做词法格式化，不访问文件系统。
        return path.as_posix()


def _path_summary(input_path: str, output: Mapping[str, object] | None, root: Path) -> str:
    metadata = _metadata(output)
    return _display_path(metadata.get("path", input_path), root)


def _read_file_summary(value: ReadFileInput, output: Mapping[str, object] | None, root: Path) -> str:
    metadata = _metadata(output)
    summary = _path_summary(value.path, output, root)
    start, end = metadata.get("start_line"), metadata.get("end_line")
    if isinstance(start, int) and isinstance(end, int):
        summary += f" · lines {start}-{end}"
    return summary


def _write_file_summary(value: WriteFileInput, output: Mapping[str, object] | None, root: Path) -> str:
    metadata = _metadata(output)
    summary = _path_summary(value.path, output, root)
    operation = metadata.get("operation")
    return summary + (f" · {operation}" if operation in {"created", "replaced"} else "")


def _edit_file_summary(value: EditFileInput, output: Mapping[str, object] | None, root: Path) -> str:
    metadata = _metadata(output)
    operation = "delete" if value.operation == "delete" else "replace"
    summary = f"{operation} {_path_summary(value.path, output, root)}"
    count = metadata.get("replacement_count")
    if operation == "replace" and isinstance(count, int):
        summary += f" · {count} replacement" + ("s" if count != 1 else "")
    return summary


def _search_summary(value, output: Mapping[str, object] | None, root: Path, *, count_key: str) -> str:
    metadata = _metadata(output)
    search_root = metadata.get("search_root", value.path)
    summary = f'"{_truncate(value.pattern, 50)}" · {_display_path(search_root, root)}'
    count = metadata.get(count_key)
    if isinstance(count, int):
        summary += f" · {count}"
    if metadata.get("truncated") is True:
        summary += " · truncated"
    return summary


def _calculator_summary(value: CalculatorInput, output: Mapping[str, object] | None) -> str:
    result = _data(output).get("value")
    return value.expression + (f" = {result}" if isinstance(result, str) else "")


def _web_search_summary(value: WebSearchInput, output: Mapping[str, object] | None) -> str:
    count = _metadata(output).get("returned_count")
    return value.query + (f" · {count} results" if isinstance(count, int) else "")


def _safe_body(result: str | None) -> str:
    return "" if result is None else str(redact_sensitive(result, key="result"))


def _truncate(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"
