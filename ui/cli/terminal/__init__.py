"""Inline terminal REPL for the TTY CLI path.

This package implements the "Static + dynamic" rendering model used by
Claude Code / Ink:

- **Static region**: committed conversation, tool banners, errors and
  assistant Markdown are printed once with :class:`rich.console.Console`
  bound to ``sys.stdout`` and *without* a background style, so the
  terminal host provides the background and the output remains in the
  terminal scrollback after the application exits.

- **Dynamic region**: a non-full-screen :class:`prompt_toolkit.Application`
  with ``erase_when_done=True`` owns the bottom input prompt, completion
  menu and live streaming preview. The application erases its own
  region when it returns, leaving scrollback untouched.

- **Alternate screen**: full-screen temporary surfaces (``/status``,
  ``/resume`` selector, permission prompts, MCP trust, ``/connect``
  wizard) enter DEC 1049 before the first frame and exit on ``finally``,
  so their contents never leak into the main scrollback.

The package is built up across the milestones in
``docs/exec-plans/active/cli-inline-terminal-ui-refactor-execplan.md``:

- M0 exposes :mod:`ui.cli.terminal.detect` and the spike module.
- M1 adds :class:`InlineRepl` and the rest of the package.
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