"""Process-wide cache of rendered assistant markdown.

The CLI commits a complete assistant reply to the static scrollback
once at the end of each turn. Re-rendering the same reply — for
example after ``/clear`` or session resume — would otherwise redo
the full Rich Markdown lex on every replay. This cache memoises the
ANSI lines so a second commit with identical content is free.

Design notes (mirroring ``docs/references/ui/components/Markdown.tsx``):

- **Keyed by hash, not by content.** The original Markdown text is
  never retained in the cache; only its 16-byte blake2b digest.
  This is intentional: OneCode can be asked to replay thousands of
  assistant messages across a long session, and storing every
  message verbatim would balloon RSS. The reference implementation
  does the same.
- **Width is part of the key.** A 200-column table wraps very
  differently from a 60-column one. We never serve stale lines for
  a terminal whose width has changed.
- **FIFO eviction.** A simple FIFO list of insertion order is used
  to pick the entry to drop when the cache exceeds ``max_size``.
  Full LRU bookkeeping is unnecessary at this scale and would just
  add constant overhead to every call.
- **Thread-safe.** The CLI is single-threaded async, but tests and
  some background helpers may call from multiple tasks. A single
  lock protects both the dict and the FIFO order.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Callable


class TextCache:
    """Memoise ``render_fn(text, width) -> list[str]`` results.

    Parameters
    ----------
    max_size:
        Maximum number of (text, width) entries to retain. Once the
        cache is full, the oldest insertion is dropped (FIFO). The
        default ``500`` matches ``TOKEN_CACHE_MAX`` in the reference
        ``Markdown.tsx``.
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
        """Return cached lines for ``text`` at ``width``, or compute + cache.

        ``render_fn`` is invoked at most once per ``(text, width)``
        pair. The returned list is always a fresh list (the caller
        may mutate it without affecting the cached entry).
        """

        if not text:
            return []
        key = self._make_key(text, width)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._hits += 1
                # Refresh insertion order so the entry is the most
                # recently used — this turns the FIFO list into an
                # approximate LRU without per-entry bookkeeping.
                self._entries.move_to_end(key)
                return list(cached)
        # Render outside the lock so a slow renderer doesn't block
        # other callers; double-check after acquiring the lock to
        # avoid duplicate work when two callers race.
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
        """Drop every cached entry."""

        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        """Return a snapshot of cache statistics for diagnostics."""

        with self._lock:
            return {
                "size": len(self._entries),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
            }

    @staticmethod
    def _make_key(text: str, width: int) -> tuple[str, int]:
        # 16 bytes = 128 bits of blake2b digest. Collision odds for a
        # long-running CLI are negligible; the cache lookup is O(1)
        # either way.
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()
        return (digest, width)


__all__ = ["TextCache"]