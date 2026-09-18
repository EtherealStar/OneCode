"""OneCode CLI entry point.

Runtime assembly moved to :mod:`application.runtime`. This module keeps the
TTY/batch entrypoint routing and the terminal-facing MCP trust prompt. The old
inline REPL remains available until M5 switches the default entrypoint.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Sequence

from application.runtime import (
    McpTrustMode,
    McpTrustPromptRequest,
    TrustChoice,
    _collect_untrusted_project_mcp_servers,
    _iter_untrusted_project_mcp_server_requests,
    build_runtime as _build_application_runtime,
    build_unconfigured_runtime as _build_unconfigured_application_runtime,
)
from services.mcp import McpConfigSet, McpTrustStore
from services.model.types import ProviderError
from ui.cli import renderer
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
    """Terminal-facing trust loop kept for CLI compatibility and tests."""

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

    # The TTY path is an inline REPL (prompt_toolkit + Rich). We wire
    # MCP trust prompts through ``default_trust_prompt`` and start with
    # ``mcp_trust_mode="prompt"`` so the user is asked to trust project
    # stdio servers on startup rather than having them silently skipped.
    from ui.cli.terminal.interaction_host import TerminalInteractionHost
    from ui.cli.terminal.permission_prompt import TtyPermissionPrompter
    from ui.cli.terminal.repl import InlineRepl
    from ui.cli.terminal.trust_prompt import default_trust_prompt

    try:
        interaction_host = TerminalInteractionHost()
        permission_prompter = TtyPermissionPrompter(interaction_host)
        try:
            runtime = build_runtime(
                workspace,
                trust_prompt=default_trust_prompt,
                permission_prompter=permission_prompter,
                mcp_trust_mode="prompt",
            )
        except ProviderError:
            # .env is missing or incomplete — start in unconfigured mode.
            runtime = build_unconfigured_runtime(workspace)
        repl = InlineRepl(
            runtime,
            permission_prompter=permission_prompter,
            interaction_host=interaction_host,
        )
        return repl.run()
    except Exception as exc:
        renderer.print_renderable(renderer.render_error(str(exc)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
