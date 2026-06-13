"""Input queue for the inline REPL.

The execplan §M3 specifies that, while the agent loop is running, the
user must be able to keep typing and queue additional prompts; once
the current turn finishes, the queued prompts are processed in FIFO
order.

The queue is intentionally a small, single-consumer structure — the
:class:`ui.cli.terminal.repl.InlineRepl` is the sole consumer. We use
:class:`collections.deque` for O(1) append/popleft and provide a
typed handle for tests.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class InputQueue:
    """FIFO queue of pending user prompts."""

    _items: Deque[str] = field(default_factory=deque)

    def push(self, line: str) -> None:
        """Append a prompt to the queue."""

        normalized = line.rstrip()
        if not normalized:
            return
        self._items.append(normalized)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __iter__(self) -> Iterator[str]:
        return iter(tuple(self._items))

    def pop(self) -> str | None:
        """Return and remove the next prompt, or ``None`` if empty."""

        if not self._items:
            return None
        return self._items.popleft()

    def snapshot(self) -> tuple[str, ...]:
        """Read-only view used by the live status line."""

        return tuple(self._items)

    def clear(self) -> None:
        """Drop every queued prompt (used on shutdown)."""

        self._items.clear()