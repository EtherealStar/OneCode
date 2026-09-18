"""已渲染提示词分节的内存缓存。"""

from __future__ import annotations


class PromptSectionCache:
    """以分节 key 和指纹为键的小型进程局部缓存。"""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], str] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str, fingerprint: str) -> str | None:
        value = self._entries.get((key, fingerprint))
        if value is None:
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key: str, fingerprint: str, value: str) -> None:
        self._entries[(key, fingerprint)] = value

    def clear(self) -> None:
        self._entries.clear()
        self.hits = 0
        self.misses = 0
