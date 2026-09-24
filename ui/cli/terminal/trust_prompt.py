"""TTY 启动路径的 MCP 信任提示。

TTY 启动时会针对每个未信任的 stdio 服务器提示确认 MCP 信任
（批处理路径仍默认跳过）。每个提示是在 REPL 启动前直接从 stdin 读取的单行 y/n 问题。
"""

from __future__ import annotations

from collections.abc import Callable

from application.runtime import TrustChoice
from ui.cli.app import McpTrustPromptRequest
from ui.cli.input import ConfirmOption, read_confirm_sync


def default_trust_prompt(
    request: McpTrustPromptRequest,
    *,
    input_func: Callable[[str], str] | None = None,
    output_func: Callable[[str], None] | None = None,
) -> TrustChoice:
    """阻塞等待 stdin，直到用户信任或跳过 MCP 服务器。

    与旧版 build_runtime 辅助函数在委托给 read_confirm_sync 之前打印的 stdout 面板对应。
    """

    out = output_func or print
    out("Project MCP stdio server requires trust before it can run:")
    out(f"  server: {request.server_name}")
    out(f"  command: {request.command}")
    out(f"  args: {request.args}")
    out(f"  cwd: {request.cwd}")
    out(f"  explicit env keys: {request.explicit_env_keys}")
    out(f"  base env keys: {request.base_env_keys}")
    if input_func is not None:
        answer = input_func("Trust this project MCP server? [t] trust / [s] skip: ")
        return (
            "trust" if answer.strip().lower() in {"t", "trust", "y", "yes"} else "skip"
        )
    try:
        result = read_confirm_sync(
            "Trust this project MCP server?",
            (
                ConfirmOption("trust", "t trust", aliases=("t", "y", "yes")),
                ConfirmOption("skip", "s skip", aliases=("s", "n", "no")),
            ),
        )
    except (EOFError, KeyboardInterrupt):
        return "skip"
    return "trust" if result == "trust" else "skip"
