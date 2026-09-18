"""命令输出结果 → 面向 TUI 命令视图的 Rich 可渲染对象。

应用层返回结构化的 :class:`CommandOutcome` 数据；
本模块是将该数据转换为 TUI 用户可见文本的唯一入口。
其不修改状态、不运行命令，也不读取运行时。
"""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from application.commands import CommandOutcome

_MISSING = "—"


def command_view(
    outcome: CommandOutcome,
) -> tuple[str, RenderableType] | None:
    """针对视图类命令输出结果，返回 ``(title, renderable)``。

    由 App 处理的生命周期结果（exit/clear/resume selector/connect）以及纯文本提交均返回 ``None``。
    """

    if outcome.category == "lifecycle":
        return None
    name = outcome.name
    if outcome.status in {"unknown", "rejected"} or outcome.error:
        message = outcome.error or outcome.text or f"命令 /{name} 执行失败。"
        return (f"/{name}", Text(message, style="ui.error"))
    if name == "status":
        return ("状态", _render_status(outcome.data or {}))
    if name == "usage":
        return ("用量", _render_usage(outcome.data or {}))
    if name == "memory":
        return ("记忆", _render_memory(outcome.data or {}))
    if name == "skills":
        return ("技能", _render_list("可用技能", outcome.data or []))
    if name == "mcp":
        return ("MCP", _render_mcp(outcome.data or []))
    if name == "tasks":
        return ("任务", _render_tasks(outcome.data or {}))
    if name == "permissions":
        return ("权限", _render_permissions(outcome.data or {}))
    if name == "plan":
        return ("计划", _render_plan(outcome.data or {}))
    if name == "compact":
        return ("上下文压缩", _render_compact(outcome.data or {}))
    if outcome.text:
        return (f"/{name}", Text(outcome.text))
    return None


def _render_status(data: dict[str, Any]) -> RenderableType:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="ui.meta")
    table.add_column(style="")
    rows = (
        ("Session", data.get("session_id", _MISSING)),
        ("Workspace", data.get("workspace", _MISSING)),
        ("Provider", data.get("provider_label") or _MISSING),
        ("Model", data.get("model") or _MISSING),
        ("Configured", "是" if data.get("configured", True) else "否"),
        ("Turn count", str(data.get("turn_count", 0))),
        ("Permission mode", str(data.get("permission_mode", _MISSING))),
        ("Plan mode", "是" if data.get("plan_mode") else "否"),
    )
    for label, value in rows:
        table.add_row(label, str(value))
    return table


def _render_usage(data: dict[str, Any]) -> RenderableType:
    output = Text()
    output.append(f"input: {data.get('input_tokens', 0):,}\n")
    output.append(f"output: {data.get('output_tokens', 0):,}\n")
    output.append(f"cache read: {data.get('cache_read_input_tokens', 0):,}\n")
    output.append(f"cache write: {data.get('cache_creation_input_tokens', 0):,}\n")
    return output


def _render_memory(data: dict[str, Any]) -> RenderableType:
    return Text(
        f"session memory: {data.get('session_memory_path') or _MISSING}\n"
        f"long-term memory: {data.get('long_term_memory_dir') or _MISSING}"
    )


def _render_list(title: str, values: Any) -> RenderableType:
    items = [str(value) for value in values] if values else []
    output = Text()
    output.append(f"{title} ({len(items)})\n", style="ui.label.agent")
    for item in items:
        output.append(f"· {item}\n")
    if not items:
        output.append("(无)\n", style="ui.meta")
    return output


def _render_mcp(servers: Any) -> RenderableType:
    items = list(servers) if servers else []
    output = Text()
    output.append(f"MCP 服务器 ({len(items)})\n", style="ui.label.agent")
    for server in items:
        name = server.get("name", _MISSING) if isinstance(server, dict) else str(server)
        status = server.get("status", _MISSING) if isinstance(server, dict) else ""
        output.append(f"· {name}", style="ui.tool")
        output.append(f"  {status}\n", style="ui.meta")
    if not items:
        output.append("(无)\n", style="ui.meta")
    return output


def _render_tasks(data: dict[str, Any]) -> RenderableType:
    output = Text()
    tasks = list(data.get("tasks") or [])
    background = list(data.get("background") or [])
    output.append(f"任务 ({len(tasks)})\n", style="ui.label.agent")
    for task in tasks:
        output.append(f"· [{task.get('status', '')}] {task.get('description', '')}\n")
    if not tasks:
        output.append("(无)\n", style="ui.meta")
    output.append(f"\n后台任务 ({len(background)})\n", style="ui.label.agent")
    for task in background:
        output.append(f"· [{task.get('status', '')}] {task.get('description', '')}\n")
    if not background:
        output.append("(无)\n", style="ui.meta")
    return output


def _render_permissions(data: dict[str, Any]) -> RenderableType:
    if "rules" in data:
        return _render_list("项目权限规则", data.get("rules") or [])
    update = data.get("type")
    behavior = data.get("behavior")
    return Text(f"已更新项目权限规则：{update} / {behavior}", style="ui.success")


def _render_plan(data: dict[str, Any]) -> RenderableType:
    path = data.get("path")
    content = data.get("content")
    if content is None:
        return Text(f"计划文件: {path or '(无活动计划)'}")
    return Group(
        Text(f"计划文件: {path}", style="ui.meta"),
        Text(content or "(空计划文件)"),
    )


def _render_compact(data: dict[str, Any]) -> RenderableType:
    return Text(
        "已压缩上下文\n"
        f"trigger: {data.get('trigger')}\n"
        f"tokens: {data.get('token_before')} → {data.get('token_after')}\n"
        f"messages: {data.get('message_count')}"
    )


__all__ = ["command_view"]
