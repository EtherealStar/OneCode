"""终端背景亮度探测。

我们采用三级梯次探测：

1. OSC 11 查询：支持 \\e]11;?\\a 的终端会回复 RGB 背景颜色。
   我们解析该回复并根据相对亮度判定明暗。该查询为尽力而为，
   在终端未在短超时时间内响应时是非阻塞的。

2. COLORFGBG 环境变量：多数终端将其设置为 <fg>;<bg>，
   其中 bg 为 ANSI 调色板索引。我们将 16 色 ANSI 调色板映射为明暗背景。

3. 硬编码暗色回退：当其他方式均不可用时默认假定为暗色，这与 OneCode 的历史默认值一致。

Rich 本身从不设置背景样式，因此终端宿主始终起决定作用。
亮度探测仅用于决定为反色用户提示行选取何种前景色。
"""

from __future__ import annotations

import os
import platform
import re
import sys
from dataclasses import dataclass
from typing import Literal

TerminalBrightness = Literal["light", "dark"]

# ANSI 16 色调色板：多数终端将 COLORFGBG 解析为这些索引之一。
# 索引 0 至 7 为暗色（常规），8 至 15 为相同色调的亮色（高亮）变体。
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
    """返回宿主终端更可能为亮色还是暗色。

    该函数绝不抛出异常。当 stdout 不是 TTY 或所有探测均失败时，回退到 dark。
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
    """返回执行 OSC 11 探测是否足够安全。

    若未从真实输入流中消费查询，Windows 终端宿主可能会将 OSC 11 回复回显到下一行编辑器中。
    在探测掌控 TTY 输入端之前，在 Windows 上跳过该探测并依赖 COLORFGBG 或回退。
    """

    current = system if system is not None else platform.system()
    return current.lower() != "windows"


def _probe_osc11_background(stdout, *, timeout: float) -> _ProbeResult | None:
    """发送 OSC 11 查询并解析单次回复。

    OSC 11 用于读取终端当前背景色。未实现该转义序列的终端将直接不予回复，
    因此该探测始终是安全的。
    """

    try:
        # 我们读取原始 fd 而非使用带缓冲的文本读取器，
        # 因为回复是交织着潜在本地回显的二进制转义序列。
        # 使用带短超时的 os.read 能够在终端未回复时保持响应迅速。
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
    """按照 WCAG 标准计算相对亮度，通道取值范围为 0 至 255。"""

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
    # 默认颜色（索引 -1 或未设置）在多数现代终端上通常呈现为黑色，我们将其视为暗色。
    if bg_index < 0:
        return "dark"
    return None
