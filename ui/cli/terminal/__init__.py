"""面向 TTY CLI 路径的内联终端 REPL。

本包实现了类似 Claude Code / Ink 的静态加动态分层渲染模型：

- 静态区域：定稿对话、工具横幅、错误和助手的 Markdown 输出使用绑定到 sys.stdout
  且不带背景样式的 rich.console.Console 打印一次，使终端宿主提供背景色，
  且输出在应用程序退出后保留在终端回滚历史中。

- 动态区域：带有 erase_when_done=True 的非全屏 prompt_toolkit.Application
  负责底部输入提示符、补全菜单和实时流式预览。该应用退出时自行擦除所属区域，
  不污染回滚历史。

- 备用屏幕：全屏临时界面（/status、/resume 选择器、权限提示、MCP 信任确认、
  /connect 向导）在首帧前进入 DEC 1049 并在 finally 块中退出，
  因此其内容绝不会泄漏到主回滚历史中。

本包根据 docs/exec-plans/active/cli-inline-terminal-ui-refactor-execplan.md 中的里程碑构建：

- M0 导出 ui.cli.terminal.detect 与 spike 模块。
- M1 添加 InlineRepl 及本包其余模块。
"""

from __future__ import annotations

from ui.cli.terminal.detect import (
    TerminalBrightness,
    detect_terminal_brightness,
)
from ui.cli.terminal.repl import InlineRepl

__all__ = [
    "InlineRepl",
    "TerminalBrightness",
    "detect_terminal_brightness",
]