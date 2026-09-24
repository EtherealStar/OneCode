from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from textual.timer import Timer


class UiRefreshScheduler:
    """把高频投影更新合并为一次 Textual 布局刷新。"""

    def __init__(self, schedule: Callable[[float, Callable[[], None]], Timer], flush: Callable[[set[UUID], bool], None], *, delay: float = 0.04) -> None:
        self._schedule = schedule
        self._flush = flush
        self._delay = delay
        self._dirty: set[UUID] = set()
        self._structural = False
        self._timer: Timer | None = None
        self.closed = False
        self.requested_updates = 0
        self.flush_count = 0

    def request(self, dirty_message_ids: set[UUID], *, structural: bool = False, immediate: bool = False) -> None:
        if self.closed:
            return
        self.requested_updates += 1
        self._dirty.update(dirty_message_ids)
        self._structural = self._structural or structural
        if immediate:
            self.flush_now()
        elif self._timer is None:
            self._timer = self._schedule(self._delay, self.flush_now)

    def flush_now(self) -> None:
        if self.closed:
            return
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        dirty, structural = self._dirty, self._structural
        self._dirty, self._structural = set(), False
        if not dirty and not structural:
            return
        self.flush_count += 1
        self._flush(dirty, structural)

    def close(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.closed = True
        self._dirty.clear()
