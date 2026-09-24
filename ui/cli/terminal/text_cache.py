"""已渲染 assistant markdown 的进程级缓存。

CLI 在每轮结束时向静态回滚历史提交一次完整的 assistant 回复。
若不使用缓存，重放相同的回复（例如 /clear 或会话恢复后）将在每次重放时重复执行完整的 Rich Markdown 词法分析。
该缓存记忆化 ANSI 行列表，使内容相同的二次提交开销为零。

设计说明：

- 按哈希而非内容索引：原始 Markdown 文本绝不保存在缓存中，仅保留其 16 字节 blake2b 摘要。这是有意为之的：OneCode 在长会话中可能需要重放数千条 assistant 消息，逐字存储每条消息会导致 RSS 内存膨胀。
- 宽度是缓存键的一部分：200 列表格与 60 列格式差异巨大。我们绝不向宽度发生变化的终端提供过期的行。
- FIFO 淘汰策略：当缓存超出 max_size 时，按插入顺序使用简单的先进先出策略挑选要丢弃的条目。在此规模下完整的 LRU 记账没有必要，只会给每次调用带来固定开销。
- 线程安全：CLI 为单线程异步，但测试和某些后台辅助程序可能会从多个任务中发起调用。单个锁同时保护字典和 FIFO 顺序。
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Callable


class TextCache:
    """记忆化 render_fn(text, width) -> list[str] 的计算结果。

    参数：
        max_size：保留的 (text, width) 条目最大数量。缓存满后丢弃最早插入的条目（FIFO）。默认值 500。
    """

    def __init__(self, max_size: int = 500) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be > 0")
        self._max_size = max_size
        self._entries: OrderedDict[tuple[str, int], list[str]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get_or_render(
        self,
        text: str,
        *,
        width: int,
        render_fn: Callable[[str, int], list[str]],
    ) -> list[str]:
        """返回 text 在 width 下的已缓存行列表，或计算并缓存。

        针对每个 (text, width) 二元组，render_fn 至多调用一次。
        返回的列表始终为全新列表（调用方可在不影响缓存条目的情况下对其进行修改）。
        """

        if not text:
            return []
        key = self._make_key(text, width)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._hits += 1
                # 刷新插入顺序使其成为最近使用的条目，将 FIFO 转换为近似 LRU 而无需每个条目的记账开销。
                self._entries.move_to_end(key)
                return list(cached)
        # 在锁外部执行渲染，防止较慢的渲染器阻塞其他调用方；获取锁后二次检查，避免两个调用方竞争时产生重复工作。
        rendered = render_fn(text, width)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                self._hits += 1
                self._entries.move_to_end(key)
                return list(existing)
            self._misses += 1
            self._entries[key] = list(rendered)
            while len(self._entries) > self._max_size:
                self._entries.popitem(last=False)
            return list(rendered)

    def clear(self) -> None:
        """丢弃所有已缓存的条目。"""

        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        """返回缓存统计信息的快照以供诊断。"""

        with self._lock:
            return {
                "size": len(self._entries),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
            }

    @staticmethod
    def _make_key(text: str, width: int) -> tuple[str, int]:
        # 16 字节 = 128 位 blake2b 摘要。对于长期运行的 CLI，哈希碰撞概率微乎其微；无论何种情况缓存查找均为 O(1)。
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()
        return (digest, width)


__all__ = ["TextCache"]
