"""瞬态 TTY 界面的备用屏幕（DEC 1049）生命周期管理。

生命周期约定：

1. enter_alternate_screen() 在绘制第一帧之前向 stdout 写入 \x1b[?1049h。
   终端切换至次级缓冲区；主缓冲区（静态回滚历史）在退出前被冻结保持不变。

2. 调用方使用绑定到相同 stdout 的 Rich Console 将其全屏页面渲染到备用屏幕中。

3. exit_alternate_screen() 写入 \x1b[?1049l。终端无损恢复主缓冲区，
   用户看到的回滚历史与其打开页面之前完全一致。

4. 当 stdout 不是 TTY 时，两项操作均为空操作。调用方应检测此情况并拒绝启动瞬态界面。

transient_terminal_scope 上下文管理器确保即便页面在渲染中途抛出异常，
exit_alternate_screen 也会在 finally 中执行，防止终端停留在备用屏幕中。
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
    """若当前正处于备用屏幕中则返回 True。

    主要暴露给用于断言生命周期在嵌套进入时仅执行一次的测试。
    """

    return _STATE.is_alternate


def can_enter_alternate_screen(stdout: TextIO | None = None) -> bool:
    """返回宿主 stdout 是否支持 DEC 1049。

    当 stdout 被重定向到文件或通过管道传递给其他进程时，进入备用屏幕会损坏目标数据流，
    因此我们拒绝进入，调用方必须适度降级（例如改为将页面行内打印到静态区域）。
    """

    stream = stdout if stdout is not None else sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def enter_alternate_screen(stdout: TextIO | None = None) -> None:
    """将宿主终端切换至其备用缓冲区。

    幂等：在已处于备用屏幕时二次调用为静默空操作，因此嵌套的页面启动不会引起终端混乱。
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
    """恢复宿主终端的主缓冲区。

    始终可以安全调用：若未曾进入备用屏幕则为空操作。
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
    """在备用屏幕内执行代码块，退出或发生异常时均会自动退出备用屏幕。

    用法：

        with transient_terminal_scope():
            page.show(renderable)

    仅当宿主支持备用屏幕时代码块才在备用屏幕中运行；在非 TTY 数据流中该上下文仍会运行代码块，
    但调用方应在此之前检测该情况并放弃启动全屏页面。
    """

    enter_alternate_screen(stdout)
    try:
        yield
    finally:
        exit_alternate_screen(stdout)


def reset_for_tests() -> None:
    """重置模块状态。测试在夹具中调用此函数，使每个测试用例以已知的备用屏幕标志开始。"""

    global _STATE
    _STATE = _AlternateScreenState()
