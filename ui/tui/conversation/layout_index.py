"""估算消息高度与 滚动行 -> 消息 映射索引。

该索引针对每个布局键保存一个估算/实测高度，并在同步或更新时重建前缀和数组。
这是线性时间复杂度的计算，而非常数时间；其核心价值在于保证视口仅针对可见消息及预加载区域创建控件。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Hashable, Iterable


class VirtualLayoutIndex:
    """将滚动行映射到布局键，并维护估算高度。"""

    def __init__(
        self,
        message_ids: Iterable[Hashable] = (),
        *,
        default_height: int = 1,
    ) -> None:
        if default_height < 1:
            raise ValueError("default_height must be positive")
        self._default_height = default_height
        self._ids = list(message_ids)
        self._heights = [default_height] * len(self._ids)
        self._positions = {
            message_id: index for index, message_id in enumerate(self._ids)
        }
        self._prefixes: list[int] = []
        self._rebuild_prefixes()

    @property
    def total_height(self) -> int:
        return self._prefixes[-1] if self._prefixes else 0

    @property
    def ids(self) -> tuple[Hashable, ...]:
        return tuple(self._ids)

    def sync(self, message_ids: Iterable[Hashable]) -> None:
        heights = dict(zip(self._ids, self._heights))
        self._ids = list(message_ids)
        self._heights = [
            heights.get(message_id, self._default_height) for message_id in self._ids
        ]
        self._positions = {
            message_id: index for index, message_id in enumerate(self._ids)
        }
        self._rebuild_prefixes()

    def append(self, message_id: Hashable, height: int = 1) -> None:
        if message_id in self._positions or height < 1:
            raise ValueError("message already exists or invalid height")
        self._positions[message_id] = len(self._ids)
        self._ids.append(message_id)
        self._heights.append(height)
        self._prefixes.append(self.total_height + height)

    def update_height(self, message_id: Hashable, height: int) -> None:
        if height < 1:
            raise ValueError("message height must be positive")
        self._heights[self._positions[message_id]] = height
        self._rebuild_prefixes()

    def prefix_height(self, message_id: Hashable) -> int:
        index = self._positions[message_id]
        return self._prefixes[index - 1] if index else 0

    def locate(self, scroll_y: int) -> tuple[Hashable, int] | None:
        if not self._ids:
            return None
        row = max(0, min(scroll_y, self.total_height - 1))
        index = bisect_right(self._prefixes, row)
        before = self._prefixes[index - 1] if index else 0
        return self._ids[index], row - before

    def visible_range(
        self, scroll_y: int, viewport_height: int, overscan: int = 0
    ) -> tuple[int, int]:
        if viewport_height < 0 or overscan < 0:
            raise ValueError("viewport_height and overscan must be non-negative")
        if not self._ids:
            return (0, 0)
        first = bisect_right(self._prefixes, max(scroll_y, 0))
        last = bisect_right(self._prefixes, max(scroll_y, 0) + viewport_height - 1) + 1
        return max(0, first - overscan), min(len(self._ids), last + overscan)

    def _rebuild_prefixes(self) -> None:
        total = 0
        self._prefixes = []
        for height in self._heights:
            total += height
            self._prefixes.append(total)


__all__ = ["VirtualLayoutIndex"]
