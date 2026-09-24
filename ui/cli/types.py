"""共享的 CLI 运行时类型。

具体的运行时数据类与装配逻辑现已移至 application.runtime 模块。
本模块保留历史名称 CliRuntime 作为别名，以便在应用层负责装配的同时，
现有的 CLI 代码与测试能够继续正常工作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from application.runtime import ApplicationRuntime

CliRuntime = ApplicationRuntime

CommandPresentation = Literal["inline", "page"]
CommandInteraction = Literal["resume_selector", "connect"]


@dataclass(frozen=True)
class CommandResult:
    should_exit: bool = False
    runtime: CliRuntime | None = None
    renderable: object | None = None
    presentation: CommandPresentation = "inline"
    interaction: CommandInteraction | None = None
    reset_main_view: bool = False
    # 命令执行成功后请求 REPL 使用常规静态输出渲染器重放至主滚动缓冲区的消息。
    # 这纯粹是界面层的重放请求（例如会话恢复）；它不是模型上下文的事实来源，
    # 模型上下文由运行时的 MessageStore 维护。
    replay_messages: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    # 注入到下一轮模型调用的持久化计划模式附件。
    # REPL 将这些附件传递给 AgentLoop.stream(prompt, attachments=...)，
    # 使计划模式状态转换成为 transcript 的一部分。
    attachments: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    # 命令请求 REPL 在用户完成当前轮次后入队的可选提示词。
    # 由 /plan <description> 使用，使描述成为计划模式下的下一条用户消息。
    queued_prompt: str | None = None


__all__ = [
    "CliRuntime",
    "CommandInteraction",
    "CommandPresentation",
    "CommandResult",
]
