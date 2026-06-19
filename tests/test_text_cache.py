"""Tests for the text→ANSI-lines cache.

The cache memoises the result of rendering assistant markdown text at
a given width. It is keyed by a 16-byte blake2b digest of the text
and the requested width. The tests below pin down the four
invariants the production code depends on:

1. A repeat request with the same text and width is a cache hit.
2. The same text at a different width is a cache miss.
3. The cache evicts entries once ``max_size`` is exceeded.
4. The cache is safe under concurrent use from multiple threads.
"""

from __future__ import annotations

import threading
from typing import Callable

from ui.cli.terminal.text_cache import TextCache


def test_cache_hit_does_not_call_render_again() -> None:
    """A second call with the same text + width must not re-render."""

    calls: list[str] = []

    def render(text: str, width: int) -> list[str]:
        calls.append(text)
        return [text, "x" * width]

    cache = TextCache(max_size=10)
    first = cache.get_or_render("hello", width=80, render_fn=render)
    second = cache.get_or_render("hello", width=80, render_fn=render)
    assert first == second
    assert calls == ["hello"]


def test_cache_miss_on_width_change() -> None:
    """A different width is a fresh cache entry."""

    calls: list[tuple[str, int]] = []

    def render(text: str, width: int) -> list[str]:
        calls.append((text, width))
        return [f"{text}@{width}"]

    cache = TextCache(max_size=10)
    cache.get_or_render("hello", width=80, render_fn=render)
    cache.get_or_render("hello", width=120, render_fn=render)
    assert calls == [("hello", 80), ("hello", 120)]


def test_cache_evicts_oldest_when_full() -> None:
    """Inserting past ``max_size`` drops the oldest insertion (FIFO)."""

    calls: list[str] = []

    def render(text: str, width: int) -> list[str]:
        calls.append(text)
        return [text]

    cache = TextCache(max_size=2)
    cache.get_or_render("a", width=80, render_fn=render)
    cache.get_or_render("b", width=80, render_fn=render)
    cache.get_or_render("c", width=80, render_fn=render)  # evicts "a"
    cache.get_or_render("a", width=80, render_fn=render)  # re-renders "a"
    assert calls == ["a", "b", "c", "a"]


def test_cache_repeated_access_refreshes_lru_order() -> None:
    """A re-access moves the entry to the back of the eviction order."""

    calls: list[str] = []

    def render(text: str, width: int) -> list[str]:
        calls.append(text)
        return [text]

    cache = TextCache(max_size=2)
    cache.get_or_render("a", width=80, render_fn=render)
    cache.get_or_render("b", width=80, render_fn=render)
    # "a" is the oldest; accessing it should move it to the back so
    # "b" is now the eviction candidate.
    cache.get_or_render("a", width=80, render_fn=render)
    cache.get_or_render("c", width=80, render_fn=render)  # evicts "b"
    cache.get_or_render("b", width=80, render_fn=render)  # re-renders "b"
    assert calls == ["a", "b", "c", "b"]


def test_cache_empty_text_returns_empty_without_rendering() -> None:
    """An empty text must short-circuit and never call the render fn."""

    calls: list[str] = []

    def render(text: str, width: int) -> list[str]:
        calls.append(text)
        return [text]

    cache = TextCache(max_size=10)
    assert cache.get_or_render("", width=80, render_fn=render) == []
    assert calls == []


def test_cache_distinguishes_texts_by_content() -> None:
    """Two distinct texts must each get their own cache slot."""

    calls: list[str] = []

    def render(text: str, width: int) -> list[str]:
        calls.append(text)
        return [text]

    cache = TextCache(max_size=10)
    cache.get_or_render("hello", width=80, render_fn=render)
    cache.get_or_render("world", width=80, render_fn=render)
    cache.get_or_render("hello", width=80, render_fn=render)  # hit
    cache.get_or_render("world", width=80, render_fn=render)  # hit
    assert calls == ["hello", "world"]


def test_cache_stats_track_hits_and_misses() -> None:
    """``stats()`` should expose hit/miss counters for diagnostics."""

    def render(text: str, width: int) -> list[str]:
        return [text]

    cache = TextCache(max_size=10)
    cache.get_or_render("a", width=80, render_fn=render)  # miss
    cache.get_or_render("a", width=80, render_fn=render)  # hit
    cache.get_or_render("b", width=80, render_fn=render)  # miss
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["size"] == 2


def test_cache_clear_drops_everything() -> None:
    def render(text: str, width: int) -> list[str]:
        return [text]

    cache = TextCache(max_size=10)
    cache.get_or_render("a", width=80, render_fn=render)
    cache.clear()
    assert cache.stats()["size"] == 0
    assert cache.stats()["hits"] == 0
    assert cache.stats()["misses"] == 0


def test_cache_is_thread_safe() -> None:
    """Concurrent ``get_or_render`` calls must not corrupt the cache.

    A naïve implementation that mutates the dict outside a lock can
    occasionally drop a freshly-inserted entry. We launch many
    threads racing on a small set of distinct keys and assert that
    every distinct (text, width) pair ends up with a stable result.
    """

    results: dict[tuple[str, int], list[str]] = {}
    results_lock = threading.Lock()

    def render(text: str, width: int) -> list[str]:
        # Sleep a tiny amount to encourage interleaving.
        import time
        time.sleep(0.001)
        return [f"{text}@{width}"]

    cache = TextCache(max_size=10)

    def worker(key: str) -> None:
        for width in (80, 120):
            out = cache.get_or_render(key, width=width, render_fn=render)
            with results_lock:
                results[(key, width)] = out

    threads = [
        threading.Thread(target=worker, args=(f"key-{i % 4}",))
        for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # All four distinct keys at both widths must have a result.
    assert set(results) == {(f"key-{i}", w) for i in range(4) for w in (80, 120)}
    # And every result is the deterministic render output.
    for (key, width), out in results.items():
        assert out == [f"{key}@{width}"]
