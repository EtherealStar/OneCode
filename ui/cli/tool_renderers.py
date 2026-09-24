"""CLI 的工具结果渲染策略。

本模块是 CLI 渲染工具输出的统一入口。
它是一个轻量级的策略分发器：每个内置工具都有一个小型渲染器，
消费 ToolExecutionResult 并返回单行摘要。动态区域使用 render_use_preview
和 render_running 展示当前的运行状态；静态区域使用 render_tool_result /
render_fallback_tool_result 提交最终摘要。

该架构对应了参考实现中 UserToolResultMessage 容器（由框架处理）
与每个工具的 renderToolResultMessage 函数（由策略处理）之间的职责划分。
在 OneCode 中，框架容器位于 ui.cli.terminal.static_output（带有 ⎿ 前缀），
而策略位于本模块。工具本身不允许注入自定义的容器前缀。

渲染器规则：

- 渲染器是结果与工作区路径的纯函数。严禁调用工具、读取文件或执行子进程。
- 渲染器绝不能抛出异常；若渲染器抛出异常，分发器会回退到 render_fallback_tool_result，确保用户依然能看到有效信息。
- 渲染器返回纯字符串；颜色由框架层添加（静态区域使用样式包裹整行）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from services.tools.types import ToolExecutionResult
from ui.cli.views.common import display_path

# --- 策略类型 ----------------------------------------------------------


class ToolCliRenderer(Protocol):
    """内置工具可实现的最小接口。

    工具无需实现所有方法；缺失的方法会导致分发器针对该生命周期回退到默认渲染器。
    动态区域仅需要预览方法；静态区域仅需要结果方法。
    """

    def render_use_preview(self, tool_name: str, tool_input: Any) -> str:
        """正在进行的工具调用的有界单行预览。"""

        ...

    def render_running(self, tool_name: str, tool_input: Any) -> str:
        """工具运行期间展示的有界状态行。"""

        ...

    def render_success(
        self, result: ToolExecutionResult, *, workspace: Path | None
    ) -> str:
        """成功工具结果的单行摘要。"""

        ...

    def render_error(
        self, result: ToolExecutionResult, *, workspace: Path | None
    ) -> str:
        """失败工具结果的单行摘要。"""

        ...


@dataclass(frozen=True)
class BuiltinToolRenderer:
    """单个工具对应的 ToolCliRenderer 具体实现。

    任意字段均可为 None；当渲染器未选择实现某项能力时，分发器将针对该生命周期使用回退逻辑。
    """

    name: str
    render_use_preview: Callable[[str, Any], str] | None = None
    render_running: Callable[[str, Any], str] | None = None
    render_success: Callable[[ToolExecutionResult, Path | None], str] | None = None
    render_error: Callable[[ToolExecutionResult, Path | None], str] | None = None


# 供现有调用方（静态输出）使用的向后兼容别名。
# 保留该函数签名以便从旧版 ToolResultRenderer 可调用对象填充调度表。
ToolResultRenderer = Callable[[ToolExecutionResult, Path], str]


# --- 公共入口 ---------------------------------------------------


def render_tool_result(result: ToolExecutionResult, *, workspace: Path) -> str:
    """返回已完成工具结果在静态区域展示的摘要行。

    分发器优先使用策略的 render_success / render_error 方法；
    若缺失或抛出异常，则回退到 render_fallback_tool_result。
    """

    policy = _POLICIES.get(result.tool_name)
    if policy is not None:
        method = policy.render_error if result.is_error else policy.render_success
        if method is not None:
            try:
                return method(result, workspace)
            except Exception:  # noqa: BLE001, S110
                pass
    return render_fallback_tool_result(result)


def render_fallback_tool_result(result: Any) -> str:
    """当策略不存在或抛出异常时使用的安全通用摘要。

    该回退逻辑绝不能抛出异常，且必须始终返回字符串，以保证命令行界面绝不会因畸形结果而崩溃。
    """

    tool_name = getattr(result, "tool_name", "unknown_tool") or "unknown_tool"
    call_id = getattr(result, "tool_call_id", "unknown_call") or "unknown_call"
    if getattr(result, "is_error", False):
        return f"[{tool_name} error] call {call_id}"
    return f"[{tool_name}] call {call_id}"


def render_use_preview(tool_name: str, tool_input: Any) -> str:
    """首次宣布工具调用时在动态区域展示的预览。

    输出被刻意限制长度（不展示完整 JSON，不展开无界路径），
    因为动态区域必须保持简短与稳定。
    """

    policy = _POLICIES.get(tool_name)
    if policy is not None and policy.render_use_preview is not None:
        try:
            text = policy.render_use_preview(tool_name, tool_input)
            if text:
                return text
        except Exception:  # noqa: BLE001, S110
            pass
    # 通用回退：单行 key=value 预览。
    return _default_use_preview(tool_name, tool_input)


def render_running(tool_name: str, tool_input: Any) -> str:
    """工具仍在执行期间在动态区域展示的状态。

    用作活跃工具列表中的逐行条目。对应 Bash 工具的 MAX_COMMAND_DISPLAY_LINES = 2 与 160 字符预算。
    """

    policy = _POLICIES.get(tool_name)
    if policy is not None and policy.render_running is not None:
        try:
            text = policy.render_running(tool_name, tool_input)
            if text:
                return text
        except Exception:  # noqa: BLE001, S110
            pass
    return _default_use_preview(tool_name, tool_input)


# --- 策略注册 ---------------------------------------------------


_POLICIES: dict[str, BuiltinToolRenderer] = {}


def register_renderer(renderer: BuiltinToolRenderer) -> None:
    """注册（或替换）指定工具名称的 CLI 策略。

    用于测试及未来希望覆盖单工具摘要的插件。内置工具在下方导入时完成注册。
    """

    _POLICIES[renderer.name] = renderer


def registered_renderers() -> dict[str, BuiltinToolRenderer]:
    """返回已注册策略的副本（用于测试）。"""

    return dict(_POLICIES)


# --- 默认实现 --------------------------------------------------------------


def _default_use_preview(tool_name: str, tool_input: Any) -> str:
    """当不存在工具专用渲染器时使用的通用预览。"""

    preview = _summarize_arguments(_as_dict(tool_input), limit=120)
    if preview:
        return f"tool: {tool_name} {preview}"
    return f"tool: {tool_name}"


def _summarize_arguments(arguments: dict[str, Any], *, limit: int = 120) -> str:
    """将工具调用的输入格式化为有界的单行预览。"""

    parts: list[str] = []
    for key, value in arguments.items():
        rendered = _render_argument_value(value)
        parts.append(f"{key}={rendered}")
        if sum(len(part) for part in parts) > limit:
            break
    text = " ".join(parts)
    if len(text) > limit:
        return text[: max(limit - 1, 0)] + "…"
    return text


def _render_argument_value(value: Any, *, inner_limit: int = 40) -> str:
    if isinstance(value, str):
        compact = " ".join(value.split())
        if len(compact) > inner_limit:
            return f'"{compact[: inner_limit - 1]}…"'
        return f'"{compact}"'
    if isinstance(value, (list, tuple)):
        return f"<{len(value)} items>"
    if isinstance(value, dict):
        return f"<{len(value)} keys>"
    return str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


# --- 共享辅助函数 --------------------------------------------------------


def _metadata_path(metadata: dict[str, Any], workspace: Path) -> str:
    path = metadata.get("path")
    if not path:
        return ""
    return display_path(str(path), workspace)


def _number(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _text(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _with_pagination(prefix: str, summary: str, metadata: dict[str, Any]) -> str:
    if metadata.get("truncated") is not True:
        return f"{prefix} {summary}"
    limit = metadata.get("applied_limit")
    offset = _number(metadata.get("applied_offset"), default=0)
    if limit is None:
        return f"{prefix} {summary}, truncated"
    return f"{prefix} {summary}, showing first {limit} after offset {offset}"


def _error_summary(
    tool_name: str,
    metadata: dict[str, Any],
    workspace: Path | None,
) -> str:
    error = _text(metadata.get("error"), "error")
    if workspace is None:
        return f"[{tool_name} error] {error}"
    path = _metadata_path(metadata, workspace)
    suffix = f" {path}" if path else ""
    return f"[{tool_name} error] {error}{suffix}"


# --- 单工具策略 -----------------------------------------------------


def _bash_use_preview(tool_name: str, tool_input: Any) -> str:
    """按照参考设计展示命令的前两行或前 160 字符。

    对应 BashTool/UI.tsx 中的 MAX_COMMAND_DISPLAY_LINES = 2 与 MAX_COMMAND_DISPLAY_CHARS = 160 预算。
    """

    cmd = ""
    if isinstance(tool_input, dict):
        cmd = str(tool_input.get("command") or "")
    first_lines = "\n".join(cmd.splitlines()[:2])
    if len(first_lines) > 160:
        first_lines = first_lines[:159] + "…"
    if first_lines:
        return f"tool: {tool_name} {first_lines}"
    return f"tool: {tool_name}"


def _bash_running(tool_name: str, tool_input: Any) -> str:
    return _bash_use_preview(tool_name, tool_input)


def _bash_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    if metadata.get("background") is True:
        task_id = _text(metadata.get("task_id"), "unknown_task")
        status = _text(metadata.get("status"), "unknown")
        output_file = metadata.get("output_file")
        suffix = ""
        if output_file and workspace is not None:
            suffix = f", output {display_path(str(output_file), workspace)}"
        return f"[bash] Started background task {task_id} ({status}){suffix}"

    if metadata.get("error") is not None and metadata.get("exit_code") is None:
        return f"[bash error] {_text(metadata.get('error'), 'error')}"

    exit_code = _number(metadata.get("exit_code"), default=0)
    duration = _number(metadata.get("duration_ms"), default=0)
    stdout_chars = _number(metadata.get("stdout_chars"), default=0)
    stderr_chars = _number(metadata.get("stderr_chars"), default=0)
    timed_out = ", timed out" if metadata.get("timed_out") is True else ""
    return (
        f"[bash] exit {exit_code} in {duration} ms{timed_out}, "
        f"stdout {stdout_chars} chars, stderr {stderr_chars} chars"
    )


def _bash_error(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    if metadata.get("background") is True:
        task_id = _text(metadata.get("task_id"), "unknown_task")
        return f"[bash error] Background task {task_id} failed"
    if metadata.get("error") is not None and metadata.get("exit_code") is None:
        return f"[bash error] {_text(metadata.get('error'), 'error')}"
    exit_code = _number(metadata.get("exit_code"), default=0)
    duration = _number(metadata.get("duration_ms"), default=0)
    stdout_chars = _number(metadata.get("stdout_chars"), default=0)
    stderr_chars = _number(metadata.get("stderr_chars"), default=0)
    timed_out = ", timed out" if metadata.get("timed_out") is True else ""
    return (
        f"[bash error] exit {exit_code} in {duration} ms{timed_out}, "
        f"stdout {stdout_chars} chars, stderr {stderr_chars} chars"
    )


def _read_file_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    ws = workspace or Path(".")
    line_count = _number(metadata.get("line_count"), default=0)
    path = _metadata_path(metadata, ws)
    suffix = f" from {path}" if path else ""
    offset = _number(metadata.get("offset"), default=1)
    if offset > 1:
        suffix = f"{suffix} from line {offset}"
    return f"[read_file] Read {line_count} line(s){suffix}"


def _read_file_use_preview(tool_name: str, tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        path = tool_input.get("path")
        if path:
            return f'tool: {tool_name} path="{path}"'
    return _default_use_preview(tool_name, tool_input)


def _grep_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    mode = metadata.get("mode")
    num_files = _number(metadata.get("num_files"), default=0)
    if mode == "count":
        num_matches = _number(metadata.get("num_matches"), default=0)
        summary = f"Found {num_matches} matches across {num_files} files"
    elif mode == "content":
        num_matches = _number(
            metadata.get("num_matches"),
            default=_number(metadata.get("num_lines"), default=0),
        )
        summary = f"Found {num_matches} matches across {num_files} files"
    else:
        summary = f"Found {num_files} files"
    return _with_pagination("[grep]", summary, metadata)


def _grep_use_preview(tool_name: str, tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        pattern = tool_input.get("pattern")
        if pattern:
            compact = " ".join(str(pattern).split())
            if len(compact) > 60:
                compact = compact[:59] + "…"
            return f'tool: {tool_name} pattern="{compact}"'
    return _default_use_preview(tool_name, tool_input)


def _glob_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    total = _number(
        metadata.get("total_matches_before_pagination"),
        default=_number(metadata.get("num_files"), default=0),
    )
    shown = _number(metadata.get("num_files"), default=total)
    summary = f"Found {total} files"
    if metadata.get("truncated") is True or shown != total:
        summary = f"{summary}, showing {shown}"
        offset = _number(metadata.get("applied_offset"), default=0)
        if offset:
            summary = f"{summary} after offset {offset}"
    return f"[glob] {summary}"


def _glob_use_preview(tool_name: str, tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        pattern = tool_input.get("pattern")
        if pattern:
            return f'tool: {tool_name} pattern="{pattern}"'
    return _default_use_preview(tool_name, tool_input)


def _write_file_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    ws = workspace or Path(".")
    operation = str(metadata.get("operation") or "update").lower()
    verb = "Created" if operation == "create" else "Updated"
    path = _metadata_path(metadata, ws)
    line_count = _number(metadata.get("line_count"), default=0)
    suffix = ", diff truncated" if metadata.get("diff_truncated") is True else ""
    return f"[write_file] {verb} {path} ({line_count} line(s){suffix})"


def _edit_file_success(result: ToolExecutionResult, workspace: Path | None) -> str:
    metadata = result.metadata
    ws = workspace or Path(".")
    path = _metadata_path(metadata, ws)
    replacement_count = _number(metadata.get("replacement_count"), default=0)
    return f"[edit_file] Edited {path} with {replacement_count} replacement(s)"


# 注册内置策略。未知工具或 MCP 工具将回退到 render_tool_result 中的通用分发器。
register_renderer(
    BuiltinToolRenderer(
        name="bash",
        render_use_preview=_bash_use_preview,
        render_running=_bash_running,
        render_success=_bash_success,
        render_error=_bash_error,
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="read_file",
        render_use_preview=_read_file_use_preview,
        render_success=_read_file_success,
        render_error=lambda r, workspace: _error_summary(
            "read_file", r.metadata, workspace
        ),
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="grep",
        render_use_preview=_grep_use_preview,
        render_success=_grep_success,
        render_error=lambda r, workspace: _error_summary("grep", r.metadata, workspace),
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="glob",
        render_use_preview=_glob_use_preview,
        render_success=_glob_success,
        render_error=lambda r, workspace: _error_summary("glob", r.metadata, workspace),
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="write_file",
        render_success=_write_file_success,
        render_error=lambda r, workspace: _error_summary(
            "write_file", r.metadata, workspace
        ),
    )
)
register_renderer(
    BuiltinToolRenderer(
        name="edit_file",
        render_success=_edit_file_success,
        render_error=lambda r, workspace: _error_summary(
            "edit_file", r.metadata, workspace
        ),
    )
)


__all__ = [
    "RENDERERS",  # 保留用于测试的旧版别名
    "BuiltinToolRenderer",
    "ToolCliRenderer",
    "ToolResultRenderer",
    "register_renderer",
    "registered_renderers",
    "render_fallback_tool_result",
    "render_running",
    "render_tool_result",
    "render_use_preview",
]


# --- 遗留兼容 -------------------------------------------------


def _legacy_dispatch_for(
    name: str, result: ToolExecutionResult, workspace: Path
) -> str:
    """将新策略 API 适配回旧版 ToolResultRenderer 形式。

    部分重构前的测试仍导入 RENDERERS 映射并通过工具名查找可调用对象。
    此适配垫片在不分叉策略接口的前提下桥接两个 API。
    """

    policy = _POLICIES.get(name)
    if policy is None:
        return render_fallback_tool_result(result)
    method = policy.render_error if result.is_error else policy.render_success
    if method is None:
        return render_fallback_tool_result(result)
    try:
        return method(result, workspace)
    except Exception:  # noqa: BLE001
        return render_fallback_tool_result(result)


# 部分测试与下游代码仍导入 ToolResultRenderer 可调用对象的 RENDERERS 映射。
# 该映射从已注册的策略中一次性构建。
RENDERERS: dict[str, ToolResultRenderer] = {
    name: (
        lambda result, workspace, _name=name: _legacy_dispatch_for(
            _name, result, workspace
        )
    )
    for name in _POLICIES
}
