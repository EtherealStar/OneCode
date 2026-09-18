"""将高频投影更新合并为单次布局刷新。

该调度器仅合并批处理刷新“请求”；绝不自行应用投影，也绝不丢弃正文增量。
附属状态变更（队列、状态、用量、运行状态）虽然携带空的脏标记集合，但因其标记了 ``structural`` 仍会被正常刷出。
对于最终提交或超时的情况，可请求立即刷出。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Hashable

from textual.timer import Timer

#: 布局键（消息 ID）的脏标记集合。
DirtyIds = set[Hashable]
Flush = Callable[[DirtyIds, bool], None]


class UiRefreshScheduler:
    def __init__(
        self,
        schedule: Callable[[float, Callable[[], None]], Timer],
        flush: Flush,
        *,
        delay: float = 0.04,
    ) -> None:
        self._schedule = schedule
        self._flush = flush
        self._delay = delay
        self._dirty: DirtyIds = set()
        self._structural = False
        self._timer: Timer | None = None
        self.closed = False
        self.requested_updates = 0
        self.flush_count = 0

    def request(
        self,
        dirty_message_ids: DirtyIds,
        *,
        structural: bool = False,
        immediate: bool = False,
    ) -> None:
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


__all__ = ["DirtyIds", "UiRefreshScheduler"]
