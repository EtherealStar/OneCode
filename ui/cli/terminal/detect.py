"""Terminal background brightness detection.

We follow three escalating probes:

1. **OSC 11** query — terminals that understand ``\\e]11;?\\a`` reply
   with an RGB background color. We parse the reply and decide dark vs
   light from the perceived luminance. The query is best-effort and
   non-blocking when the terminal does not respond within a short
   timeout.

2. **COLORFGBG** environment variable — most terminals set this to
   ``"<fg>;<bg>"`` where ``bg`` is an ANSI palette index. We map the
   16-color ANSI palette to dark vs light backgrounds.

3. **Hard-coded dark fallback** — when nothing else is available we
   assume dark, which matches the historical OneCode default.

Rich itself never sets a background style, so the terminal host always
wins. The brightness only governs which foreground accent we pick for
the reverse-video user prompt.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from dataclasses import dataclass
from typing import Literal

TerminalBrightness = Literal["light", "dark"]

# ANSI 16-color palette: most terminals resolve COLORFGBG to one of
# these indices. Indices 0–7 are dark (lower) and 8–15 are light
# (bright) variants of the same hues.
_ANSI_DARK_BG = frozenset({0, 1, 2, 3, 4, 5, 6, 8})
_ANSI_LIGHT_BG = frozenset({7, 15})


@dataclass(frozen=True)
class _ProbeResult:
    brightness: TerminalBrightness
    source: Literal["osc11", "colorfgbg", "fallback"]


def detect_terminal_brightness(
    stdout=None,
    *,
    timeout: float = 0.15,
) -> TerminalBrightness:
    """Return whether the host terminal is most likely light or dark.

    The function never raises. When stdout is not a TTY or all probes
    fail it falls back to ``"dark"``.
    """

    if stdout is None:
        stdout = sys.stdout
    if not getattr(stdout, "isatty", lambda: False)():
        return "dark"
    if _should_probe_osc11():
        osc = _probe_osc11_background(stdout, timeout=timeout)
        if osc is not None:
            return osc.brightness
    colorfgbg = _probe_colorfgbg(os.environ.get("COLORFGBG"))
    if colorfgbg is not None:
        return colorfgbg
    return "dark"


# --- OSC 11 ---------------------------------------------------------------


_OSC11_REQUEST = b"\x1b]11;?\x07"
_OSC11_REPLY = re.compile(rb"\x1b]11;rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)")


def _should_probe_osc11(system: str | None = None) -> bool:
    """Return whether OSC 11 probing is safe enough to attempt.

    Windows terminal hosts can echo OSC 11 replies into the next line
    editor when the query is not consumed from the real input stream.
    Until the probe owns the input side of the TTY, skip it there and
    rely on COLORFGBG/fallback.
    """

    current = system if system is not None else platform.system()
    return current.lower() != "windows"


def _probe_osc11_background(stdout, *, timeout: float) -> _ProbeResult | None:
    """Send an OSC 11 query and parse a single reply.

    OSC 11 reads the terminal's current background color. Terminals
    that don't implement the escape will simply not reply, so this
    probe is always safe.
    """

    try:
        # We read the raw fd rather than using a buffered text reader
        # because the reply is a binary escape sequence interleaved
        # with potential local echo. Using os.read with a short timeout
        # keeps the spike responsive when the terminal doesn't reply.
        fd = stdout.fileno()
    except (AttributeError, OSError):
        return None

    try:
        os.write(fd, _OSC11_REQUEST)
    except OSError:
        return None

    import select

    try:
        readable, _, _ = select.select([fd], [], [], timeout)
    except (OSError, ValueError):
        return None
    if not readable:
        return None

    try:
        chunk = os.read(fd, 64)
    except OSError:
        return None
    match = _OSC11_REPLY.search(chunk)
    if match is None:
        return None
    try:
        brightness = _brightness_from_osc11_match(match)
    except ValueError:
        return None
    return _ProbeResult(brightness=brightness, source="osc11")


def _brightness_from_osc11_reply(reply: bytes) -> TerminalBrightness | None:
    match = _OSC11_REPLY.search(reply)
    if match is None:
        return None
    try:
        return _brightness_from_osc11_match(match)
    except ValueError:
        return None


def _brightness_from_osc11_match(match: re.Match[bytes]) -> TerminalBrightness:
    raw_channels = tuple(match.groups())
    max_digits = max(len(value) for value in raw_channels)
    max_value = (16**max_digits) - 1
    if max_value <= 0:
        raise ValueError("invalid OSC 11 channel width")
    r, g, b = tuple(int(value, 16) / max_value * 255 for value in raw_channels)
    luminance = _relative_luminance(r, g, b)
    return "light" if luminance >= 0.5 else "dark"


def _relative_luminance(r: float, g: float, b: float) -> float:
    """Compute relative luminance per WCAG, but on 0..255 channels."""

    def channel(value: int) -> float:
        s = value / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


# --- COLORFGBG ------------------------------------------------------------


def _probe_colorfgbg(value: str | None) -> TerminalBrightness | None:
    if not value:
        return None
    parts = value.split(";")
    if len(parts) < 2:
        return None
    try:
        bg_index = int(parts[-1])
    except ValueError:
        return None
    if bg_index in _ANSI_LIGHT_BG:
        return "light"
    if bg_index in _ANSI_DARK_BG:
        return "dark"
    # Default colors (index -1 or unset) usually render as black on
    # most modern terminals — we treat them as dark.
    if bg_index < 0:
        return "dark"
    return None
