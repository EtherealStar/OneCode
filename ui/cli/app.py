"""OneCode 命令行入口。

运行时装配已移至 application.runtime 模块。本模块保留
TTY/batch 入口路由以及面向终端的 MCP 信任提示。旧的内联 REPL
在 M5 切换默认入口前仍然可用。
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from application.runtime import (
    McpTrustMode,
    McpTrustPromptRequest,
    TrustChoice,
    _collect_untrusted_project_mcp_servers,
    _iter_untrusted_project_mcp_server_requests,
)
from application.runtime import (
    build_runtime as _build_application_runtime,
)
from application.runtime import (
    build_unconfigured_runtime as _build_unconfigured_application_runtime,
)
from services.mcp import McpConfigSet, McpTrustStore
from ui.cli.input import ConfirmOption, read_confirm_sync
from ui.cli.types import CliRuntime

__all__ = [
    "McpTrustPromptRequest",
    "_collect_untrusted_project_mcp_servers",
    "_iter_untrusted_project_mcp_server_requests",
    "_prompt_for_project_mcp_trust",
    "build_runtime",
    "build_unconfigured_runtime",
    "main",
]


def _terminal_trust_prompt(request: McpTrustPromptRequest) -> TrustChoice:
    print("Project MCP stdio server requires trust before it can run:")
    print(f"  server: {request.server_name}")
    print(f"  command: {request.command}")
    print(f"  args: {request.args}")
    print(f"  cwd: {request.cwd}")
    print(f"  explicit env keys: {request.explicit_env_keys}")
    print(f"  base env keys: {request.base_env_keys}")
    try:
        choice = read_confirm_sync(
            "Trust this project MCP server?",
            (
                ConfirmOption("trust", "t trust", aliases=("t", "y", "yes")),
                ConfirmOption("skip", "s skip", aliases=("s", "n", "no")),
            ),
        )
    except (EOFError, KeyboardInterrupt):
        print("Skipping untrusted MCP server.")
        return "skip"
    return choice  # type: ignore[return-value]


def build_runtime(
    workspace: Path,
    *,
    trust_prompt: Callable[[McpTrustPromptRequest], TrustChoice] | None = None,
    permission_prompter: object | None = None,
    mcp_trust_mode: McpTrustMode = "prompt",
) -> CliRuntime:
    if mcp_trust_mode == "prompt" and trust_prompt is None:
        trust_prompt = _terminal_trust_prompt
    return _build_application_runtime(
        workspace,
        trust_prompt=trust_prompt,
        permission_prompter=permission_prompter,  # type: ignore[arg-type]
        mcp_trust_mode=mcp_trust_mode,
    )


def build_unconfigured_runtime(workspace: Path) -> CliRuntime:
    return _build_unconfigured_application_runtime(workspace)


def _prompt_for_project_mcp_trust(
    workspace: Path,
    mcp_config: McpConfigSet,
    trust_store: McpTrustStore,
    *,
    trust_prompt: Callable[[McpTrustPromptRequest], TrustChoice] | None = None,
) -> None:
    """面向终端的信任确认循环，保留用于 CLI 兼容性与测试。"""

    from services.mcp import fingerprint_mcp_server

    for config, request in _iter_untrusted_project_mcp_server_requests(
        workspace,
        mcp_config,
        trust_store,
    ):
        fingerprint = fingerprint_mcp_server(config, workspace)
        if trust_prompt is not None:
            response = trust_prompt(request)
        else:
            response = _terminal_trust_prompt(request)
        if response == "trust":
            trust_store.trust_server(
                config.name,
                fingerprint,
                transport=config.transport,
            )
            print(f"Trusted MCP server: {config.name}")
        else:
            print(f"Skipped MCP server: {config.name}")


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    workspace = Path.cwd()
    if not sys.stdin.isatty():
        from ui.cli.batch import run_batch

        return run_batch(workspace)
    if not sys.stdout.isatty():
        print(
            "Error: OneCode CLI requires an interactive terminal: stdout is not a TTY. "
            "Run `uv run python -m ui.cli.app` from a real terminal window, "
            "or pipe a prompt into the command to use batch mode.",
            file=sys.stderr,
        )
        return 1

    # TTY 路径采用全屏 Textual TUI。App 在挂载后构建运行时
    # （并询问 MCP 信任），因此启动阶段绝不阻塞输入。
    from ui.tui.app import run_tui

    try:
        return run_tui(workspace)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
