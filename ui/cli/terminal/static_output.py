"""行内 REPL 的静态区域打印器。

静态区域即终端的回滚历史。此处打印的所有内容均通过
print_static 输出，它使用绑定到 sys.stdout 的 Rich 控制台，
且不设置背景样式，由宿主终端提供背景。内容一旦打印便绝不重绘。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from ui.cli.terminal.markdown_rendering import render_cached_markdown
from ui.cli.tool_renderers import (
    render_fallback_tool_result,
    render_tool_result,
)
from ui.cli.theme import RICH_THEME
from ui.cli.types import CliRuntime


# 所有静态打印器共享的模块级控制台。共享该实例可避免
# 每次打印都构建新 Rich Console 的开销，这在长会话重放数百个工具横幅时尤为重要。
_STATIC_CONSOLE: Console | None = None


def static_console() -> Console:
    """返回进程级的静态区域控制台。

    主题仅定义前景色样式。实际背景色始终由终端宿主决定。
    """

    global _STATIC_CONSOLE
    if _STATIC_CONSOLE is None:
        _STATIC_CONSOLE = Console(theme=RICH_THEME)
    return _STATIC_CONSOLE


def reset_static_console() -> None:
    """丢弃缓存的控制台实例。

    通过重定向 sys.stdout 捕获标准输出的测试需要指向新流的全新控制台，
    因此测试会先调用本函数，然后通过调用 static_console 触发重建。
    """

    global _STATIC_CONSOLE
    _STATIC_CONSOLE = None


def print_static(renderable: Any) -> None:
    """向静态区域（终端回滚历史）打印可渲染对象。"""

    static_console().print(renderable)


# --- 反色用户提示词 ---


def user_reverse_style(brightness: str) -> str:
    """选取用于用户提示词的反色样式。

    在深色宿主环境中（默认）使用 white on black；在浅色宿主环境中
    使用 black on white。我们避免使用 Rich 的 reverse 关键字，
    因为其产生的颜色取决于当前前景色样式，该样式依赖主题，
    在不同深浅宿主环境中渲染同一行时会导致显示不一致。
    """

    if brightness == "light":
        return "black on white"
    return "white on black"


def print_user_submitted(line: str, *, brightness: str) -> None:
    """以反色高亮打印已提交的用户输入行。

    line 为原始用户输入。我们剥离尾随换行符，使反色高亮条带保持在单个终端行上。
    """

    text = Text(f"> {line.rstrip()}", style=user_reverse_style(brightness))
    print_static(text)


# --- assistant 前缀与 Markdown 提交 ---


def assistant_prefix_style() -> str:
    """onecode> 前缀的颜色。

    使用与章节标题相同的强调色，使前缀看起来像是 assistant 身份标识的一部分，而不是工具项标记。
    """

    return "onecode.title"


def print_assistant_start() -> None:
    """在即将输出的回复前打印 onecode> 前缀。

    当 Rich 遵循 end="" 时，随后的 Markdown 正文将从同一行开始输出。
    我们在流式传输前先提交此前缀，这样即使流式中途发生中断，也能留下可见的 assistant 标识。
    """

    static_console().print(
        Text("onecode>", style=assistant_prefix_style())
    )


def print_assistant_markdown(text: str) -> None:
    """将完整的 assistant 回复作为 Markdown 提交。

    流式传输完成时调用一次。该函数在新行打印 onecode> 前缀，
    然后输出 Markdown 正文。我们在本函数内部打印前缀（而非依赖
    单独调用 print_assistant_start），以防止调用方遗漏前缀导致提交的
    assistant 文本缺少身份标识。

    正文通过 render_cached_markdown 进行渲染，因此重放相同 assistant 消息
    （例如 /clear 或会话恢复后）将命中文本缓存而无需重新进行词法解析。
    """

    if not text:
        return
    static_console().print(
        Text("onecode>", style=assistant_prefix_style())
    )
    width = static_console().width or 80
    cached_lines = render_cached_markdown(text, width=width)
    if cached_lines:
        # 逐字打印已渲染的行；保留之前计算好的所有颜色和表格布局。
        # 渲染结果为空时（例如仅包含空白字符的输入）不输出任何内容。
        body = "\n".join(cached_lines)
        print_static(Text(body))


def print_assistant_inline(text: str) -> None:
    """打印简短的行内 assistant 片段（例如错误消息）。

    用于需要具有 assistant 输出外观但无需 Markdown 渲染的命令结果和错误路径。
    """

    static_console().print(
        Text("onecode> ", style=assistant_prefix_style())
        + Text(text, style="onecode.metric")
    )


# --- 工具横幅 ---


def print_tool_banner_start(tool_name: str, call_id: str, arguments: dict[str, Any] | None = None) -> None:
    """打印工具调用的起始行。

    静态区域仅需紧凑的单行摘要，因此我们直接格式化调用名称与受限的参数预览，
    而无需复用重量级的横幅组件。
    """

    label = Text("● ", style="onecode.info") + Text(
        tool_name or "tool", style="onecode.command"
    )
    if call_id:
        label += Text(f" [{call_id}]", style="onecode.subtle")
    print_static(label)
    if arguments:
        preview = _summarize_arguments(arguments)
        if preview:
            print_static(Text(f"  → {preview}", style="onecode.subtle"))


def print_tool_banner_running(call_id: str) -> None:
    """为正在执行的工具打印进度标记。

    每次调用都输出新的旋转动画行会污染回滚历史，因此工具执行完成后
    我们通过结果行静默更新。该函数保留作为未来需要在静态区域输出工具进度的钩子。
    """

    _ = call_id


def print_tool_result(
    result: Any,
    *,
    call_id: str,
    workspace: Path | None = None,
) -> None:
    """为执行完毕的工具调用打印结果行。

    该行包装在静态区域所有工具结果通用的 ⎿ 容器中。
    ui.cli.tool_renderers 中的具体工具渲染器不得自行内嵌容器字符；
    由框架统一持有以保证各个工具之间的嵌套和样式保持一致。
    """

    if hasattr(result, "tool_call_id"):
        line = render_tool_result(result, workspace=workspace) if workspace is not None else render_fallback_tool_result(result)
    else:
        line = render_fallback_tool_result(result)
    print_static(Text(f"  ⎿  {line}", style="onecode.subtle"))


def print_untrusted_mcp_notice(name: str, detail: str) -> None:
    """为跳过的非受信任 MCP 服务器打印单行警告。"""

    suffix = f" ({detail})" if detail else ""
    print_static(
        Text(
            f"! Skipped untrusted MCP server: {name}{suffix}. "
            "It was not run; its tools are unavailable.",
            style="onecode.warning",
        )
    )


def _summarize_arguments(arguments: dict[str, Any], *, limit: int = 120) -> str:
    """将工具调用的输入参数格式化为单行预览。

    工具横幅属于视觉辅助；我们绝不希望将完整的数千字节参数字典转储到回滚历史中。
    """

    parts: list[str] = []
    for key, value in arguments.items():
        rendered = _render_argument_value(value)
        parts.append(f"{key}={rendered}")
        if sum(len(part) for part in parts) > limit:
            break
    text = " ".join(parts)
    if len(text) > limit:
        return text[: limit - 1] + "…"
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


# --- 显式初始化（供调用方重建控制台） ---


def rebind_static_console() -> Console:
    """强制重建静态控制台。

    测试借助此方法使模块级控制台指向捕获的标准输出。
    """

    reset_static_console()
    return static_console()


def runtime_is_attached(runtime: CliRuntime | None) -> bool:
    """供其他终端模块使用的轻量辅助函数，用于在 REPL 未完全装配 runtime
    （如 M0 探索阶段和测试桩）时进行短路判断。
    """

    return runtime is not None
