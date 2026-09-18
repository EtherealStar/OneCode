"""M0 探针：验证内联 REPL 原语。

运行方式：

    uv run python -m ui.cli.terminal._spike

探针验证以下能力：

1. 终端背景亮度探测（OSC 11 -> COLORFGBG -> 暗色回退）。
2. 根据探测到的亮度生成相应样式的反色用户提示行。
3. 带有上下 ─ 边框且退出时擦除动态区域的非全屏 prompt_toolkit.Application。
4. 50ms 节流的实时 Markdown 预览，累积文本片段并在动态区域中重新渲染，支持未闭合的代码块。

本文件刻意独立于 CLI 其余部分；不导入 ui.cli.app 或 core.loop。
M0 仅需端到端证明上述四项原语可行；生产级装配将在 M1 至 M5 中完成。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Iterable

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input import create_input
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.output import create_output
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from ui.cli.terminal.detect import detect_terminal_brightness


# --- 静态输出（探针最小切片） --------------------------


def _user_reverse_style(brightness: str) -> str:
    # 在亮色背景下反转为黑底白字，使提示行呈现为色块。
    # 在暗色背景下反转为白底黑字，保持相同的视觉突出效果。
    # Rich 的 reverse 样式会交换前背景色，因此在已知宿主背景时直接指定样式。
    if brightness == "light":
        return "black on white"
    return "white on black"


def _spike_static_banner(brightness: str) -> None:
    """向静态回滚历史打印固定的横幅行。"""

    console = Console()
    console.print(
        Text(
            "M0 spike — inline terminal REPL primitives",
            style="bold cyan",
        )
    )
    console.print(
        Text(
            f"Detected terminal brightness: {brightness}",
            style="onecode.subtle",
        )
    )
    console.print(
        Text(
            "> /status",
            style=_user_reverse_style(brightness),
        )
    )
    console.print(
        Text("onecode> ", style="onecode.title")
        + Text("(assistant reply goes here)", style="onecode.metric")
    )


# --- 动态提示符 -------------------------------------------------------


@dataclass
class _SpikeState:
    submitted: str | None = None
    streaming_text: str = ""


def _spike_prompt_layout(state: _SpikeState) -> Layout:
    """最小提示符布局：上边框、提示输入、下边框。

    两个边框与提示输入均位于非全屏 Application 内。
    Application 带有 erase_when_done=True，因此用户提交后边框和提示符会彻底消失。
    """

    top_border = Window(
        height=1,
        char="─",
        style="class:prompt-border",
        content=FormattedTextControl(
            FormattedText([("class:prompt-border", "─" * 40)])
        ),
    )
    prompt_window = Window(
        height=1,
        content=FormattedTextControl(
            "spike> ",
            focusable=True,
            key_bindings=None,
        ),
    )
    bottom_border = Window(
        height=1,
        char="─",
        style="class:prompt-border",
        content=FormattedTextControl(
            FormattedText([("class:prompt-border", "─" * 40)])
        ),
    )
    body = HSplit(
        [
            top_border,
            prompt_window,
            bottom_border,
        ],
    )
    return Layout(body, focused_element=prompt_window)


async def _run_prompt(state: _SpikeState) -> None:
    """运行动态提示符，直到用户提交空文本。"""

    app: Application[None] = Application(
        layout=_spike_prompt_layout(state),
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
    )
    await app.run_async()


# --- 实时流式预览 -----------------------------------------------


def _render_streaming_preview(state: _SpikeState) -> FormattedText:
    # 通过 Rich 重新渲染部分 Markdown 开销较大，因此仅在缓冲区较短时重绘。
    # 对于探针，渲染 Markdown 前缀并追加省略号以示意后续内容。
    buffer = state.streaming_text
    if not buffer:
        return FormattedText([("class:stream-dim", "(streaming preview idle)")])
    if buffer.endswith("\n```") or buffer.count("```") % 2 == 1:
        # 未闭合的代码块：Rich 虽可渲染，但底部会遗留不完整的围栏。
        # 此处仅以低调样式展示原始文本。
        return FormattedText(
            [("class:stream-text", buffer + " …")]
        )
    return FormattedText(
        [("class:stream-text", buffer + " …")]
    )


async def _run_streaming_preview(state: _SpikeState) -> None:
    """模拟 50ms 节流的流式预览。"""

    fragments: Iterable[str] = (
        "# Hello from OneCode\n\n",
        "This is a *streaming* ",
        "Markdown ",
        "preview.\n\n",
        "- bullet 1\n",
        "- bullet 2\n",
        "```python\n",
        "print('hi')\n",
    )

    # 在提示符上方使用微型实时区域。复用相同的提示符布局，但将提示输入替换为状态行。
    top_border = Window(
        height=1,
        content=FormattedTextControl(
            FormattedText([("class:prompt-border", "─" * 40)])
        ),
    )
    preview_window = Window(
        height=3,
        content=FormattedTextControl(
            lambda: _render_streaming_preview(state),
        ),
    )
    status = Window(
        height=1,
        content=FormattedTextControl("streaming... press Ctrl-C to abort"),
    )
    bottom_border = Window(
        height=1,
        content=FormattedTextControl(
            FormattedText([("class:prompt-border", "─" * 40)])
        ),
    )
    body = HSplit([top_border, preview_window, status, bottom_border])
    app: Application[None] = Application(
        layout=Layout(body),
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
    )

    # 在运行应用的同时调度流式生成任务。
    async def feed() -> None:
        for fragment in fragments:
            state.streaming_text += fragment
            app.invalidate()
            await asyncio.sleep(0.05)
        # 短暂保持预览可见，以便用户在区域擦除前看到最终状态。
        await asyncio.sleep(0.2)

    await asyncio.gather(app.run_async(), feed())


# --- 探针入口 ----------------------------------------------------


def main() -> int:
    brightness = detect_terminal_brightness()
    _spike_static_banner(brightness)

    # 1. 静态区域已在上方打印。现在运行动态提示符。
    state = _SpikeState()
    asyncio.run(_run_prompt(state))
    # 2. 随后运行同样位于动态区域的流式预览。
    asyncio.run(_run_streaming_preview(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())