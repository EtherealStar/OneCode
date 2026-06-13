"""Alternate-screen (DEC 1049) lifecycle for transient TTY surfaces.

The contract follows the completed
``docs/exec-plans/completed/cli-transient-alternate-screen-plan.md``:

1. ``enter_alternate_screen()`` writes ``\\x1b[?1049h`` to stdout
   *before* the first frame is drawn. The terminal swaps to its
   secondary buffer; the primary buffer (the static scrollback) is
   frozen until exit.

2. The caller renders its full-screen page into the alternate screen
   using a Rich :class:`rich.console.Console` bound to the same
   stdout.

3. ``exit_alternate_screen()`` writes ``\\x1b[?1049l``. The terminal
   restores the primary buffer unchanged, so the user sees their
   scrollback exactly as it was when the page opened.

4. Both operations are no-ops when stdout is not a TTY. The caller
   is expected to detect this case and refuse to launch a transient
   surface (see :func:`can_enter_alternate_screen` and the M5 page /
   selector / connect flow).

The :class:`transient_terminal_scope` context manager guarantees
that ``exit_alternate_screen`` runs in a ``finally`` even when the
page raises mid-render — preventing a stuck alternate screen, which
is a real failure mode on legacy terminals.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TextIO

_ENTER_SEQUENCE = "\x1b[?1049h"
_EXIT_SEQUENCE = "\x1b[?1049l"


@dataclass(frozen=True)
class _AlternateScreenState:
    is_alternate: bool = False


_STATE = _AlternateScreenState()


def is_alternate_screen_active() -> bool:
    """Return ``True`` if we are currently inside an alternate screen.

    Exposed mainly for tests that want to assert the lifecycle ran
    exactly once across nested entries.
    """

    return _STATE.is_alternate


def can_enter_alternate_screen(stdout: TextIO | None = None) -> bool:
    """Return whether the host stdout supports DEC 1049.

    When stdout is redirected to a file or piped into another process,
    alternate-screen entry would corrupt the destination stream, so
    we refuse to enter and callers must degrade gracefully (e.g. by
    rendering the page inline into the static region instead).
    """

    stream = stdout if stdout is not None else sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def enter_alternate_screen(stdout: TextIO | None = None) -> None:
    """Switch the host terminal to its alternate buffer.

    Idempotent: a second call while already inside the alternate
    screen is a silent no-op so nested page launches (for example a
    page opening a selector) don't confuse the terminal.
    """

    global _STATE
    if _STATE.is_alternate:
        return
    if not can_enter_alternate_screen(stdout):
        return
    stream = stdout if stdout is not None else sys.stdout
    stream.write(_ENTER_SEQUENCE)
    stream.flush()
    _STATE = _AlternateScreenState(is_alternate=True)


def exit_alternate_screen(stdout: TextIO | None = None) -> None:
    """Restore the host terminal's primary buffer.

    Always safe to call: it is a no-op when we never entered.
    """

    global _STATE
    if not _STATE.is_alternate:
        return
    stream = stdout if stdout is not None else sys.stdout
    try:
        stream.write(_EXIT_SEQUENCE)
        stream.flush()
    finally:
        _STATE = _AlternateScreenState(is_alternate=False)


@contextlib.contextmanager
def transient_terminal_scope(stdout: TextIO | None = None) -> Iterator[None]:
    """Run a block inside the alternate screen, exiting on exit/exception.

    Usage::

        with transient_terminal_scope():
            page.show(renderable)

    The block runs only when the host supports alternate screen; on
    non-TTY streams the context still runs the body so callers don't
    need a second branch — but they should detect this case earlier
    and decline to launch the page in the first place.
    """

    enter_alternate_screen(stdout)
    try:
        yield
    finally:
        exit_alternate_screen(stdout)


def reset_for_tests() -> None:
    """Reset module state. Tests call this in fixtures so each case
    starts with a known alternate-screen flag."""

    global _STATE
    _STATE = _AlternateScreenState()