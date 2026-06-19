"""Input queue for the inline REPL.

This module owns the FIFO queue used by :class:`ui.cli.terminal.repl.InlineRepl`.
The execplan in ``docs/exec-plans/active/cli-running-input-queue.md`` upgrades the
queue from a bare ``deque[str]`` to a typed queue of :class:`QueuedInput`
records so the REPL can dispatch different submission kinds (ordinary prompt
vs. slash command) without re-classifying text on the consumer side.

Responsibilities:

- keep insertion order (FIFO) — :class:`collections.deque` is used for O(1)
  ``append`` / ``popleft``;
- accept only non-blank lines (whitespace-stripped);
- classify each entry as ``prompt`` or ``slash`` based on whether the line
  starts with ``/``;
- expose a read-only :meth:`InputQueue.snapshot` view that downstream
  components (status line, queued preview) can render without mutating
  the queue;
- assign a monotonically increasing ``sequence`` so consumers and tests
  can detect stable ordering without depending on deque indices.

The single consumer remains :class:`ui.cli.terminal.repl.InlineRepl`. The
running-turn input box inside :class:`ui.cli.terminal.stream_session.StreamingSession`
also calls :meth:`InputQueue.push`, so the queue is intentionally the only
shared channel between the running dynamic region and the idle-time
dispatch loop.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Deque, Literal


#: Kinds of input the queue can carry. ``prompt`` is a regular user turn;
#: ``slash`` is a line starting with ``/`` and must be routed through the
#: command dispatcher rather than the agent loop.
QueuedInputKind = Literal["prompt", "slash"]


@dataclass(frozen=True)
class QueuedInput:
    """A single queued submission waiting to be dispatched.

    Attributes:
        text: The literal submission text (already stripped of trailing
            whitespace). The original leading whitespace is also dropped
            because the queue only accepts non-blank input.
        kind: Either ``"prompt"`` or ``"slash"``. ``slash`` entries start
            with ``/`` and must skip the agent loop.
        sequence: Monotonic insertion counter so consumers can detect
            ordering without depending on deque indices.
        visible: Whether the running-turn preview should render this
            entry. Reserved for future use (e.g. system-inserted entries
            that should not clutter the preview).
    """

    text: str
    kind: QueuedInputKind
    sequence: int
    visible: bool = True


@dataclass
class InputQueue:
    """FIFO queue of :class:`QueuedInput` records awaiting dispatch."""

    _items: Deque[QueuedInput] = field(default_factory=deque)
    _next_sequence: int = 0

    def push(self, line: str) -> QueuedInput | None:
        """Append a submission; returns the queued object, or ``None`` if blank.

        A blank line (whitespace-only or empty after stripping) is silently
        dropped. Slash commands keep their leading ``/``; the queue does
        not re-format text.
        """

        normalized = line.rstrip()
        if not normalized.strip():
            return None
        kind: QueuedInputKind = "slash" if normalized.lstrip().startswith("/") else "prompt"
        item = QueuedInput(
            text=normalized,
            kind=kind,
            sequence=self._next_sequence,
        )
        self._next_sequence += 1
        self._items.append(item)
        return item

    def pop(self) -> QueuedInput | None:
        """Return and remove the next queued input, or ``None`` if empty."""

        if not self._items:
            return None
        return self._items.popleft()

    def snapshot(self) -> tuple[QueuedInput, ...]:
        """Read-only snapshot used by the queued preview and tests.

        The returned tuple is a copy; later mutations to the queue do not
        affect already-issued snapshots.
        """

        return tuple(self._items)

    def clear(self) -> None:
        """Drop every queued input. Used on shutdown."""

        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __iter__(self) -> Iterator[QueuedInput]:
        return iter(tuple(self._items))


__all__ = ["InputQueue", "QueuedInput", "QueuedInputKind"]