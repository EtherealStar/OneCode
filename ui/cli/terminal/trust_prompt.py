"""MCP trust prompt for the TTY startup path.

The TTY startup prompts for MCP trust per untrusted stdio server
(the batch path still defaults to skipping). Each prompt is a one-line
y/n question read directly from stdin before the REPL starts.
"""

from __future__ import annotations

import sys
from typing import Callable, TextIO

from ui.cli.app import McpTrustPromptRequest
from ui.cli.input import ConfirmOption, read_confirm_sync


def default_trust_prompt(
    request: McpTrustPromptRequest,
    *,
    input_func: Callable[[str], str] | None = None,
    output_func: Callable[[str], None] | None = None,
) -> str:
    """Block on stdin until the user trusts or skips an MCP server.

    Mirrors the stdout panel that the legacy ``build_runtime`` helper
    printed before delegating to :func:`read_confirm_sync`.
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
        return input_func("Trust this project MCP server? [t] trust / [s] skip: ")
    try:
        return read_confirm_sync(
            "Trust this project MCP server?",
            (
                ConfirmOption("t", "t trust", aliases=("y", "yes")),
                ConfirmOption("s", "s skip", aliases=("n", "no")),
            ),
        )
    except (EOFError, KeyboardInterrupt):
        return "skip"