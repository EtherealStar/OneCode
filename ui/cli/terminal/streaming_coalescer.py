"""Event coalescer for the streaming CLI.

The provider emits a flurry of high-frequency events while streaming
text — for example hundreds of ``assistant_delta`` events in a single
turn. Applying every event to the reducer individually and triggering
a screen redraw after each one would burn CPU for no visible benefit:
the user cannot read characters faster than ~16 ms per glyph.

This module provides :class:`StreamingCoalescer` which buffers bursts
of high-frequency events into a single 16 ms window. Within a window
the deltas are concatenated and the per-event reducers are only
invoked once. Low-frequency events (``tool_call_ready``,
``tool_started``, ``tool_result``, ``transition``, ``completed``,
``error``) are applied immediately and force a flush of any pending
batch so the visible UI stays in sync with the reducer.

The window size matches the 16 ms (~60 fps) cadence used by the
reference implementation. It's also roughly the threshold at which a
human perceives a redraw as "instant".
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.stream_events import AgentEvent


#: Event types that can be batched. ``assistant_delta`` accumulates
#: text and ``tool_progress`` overwrites the per-call progress string,
#: so multiple events for the same call id collapse to the final
#: value. ``tool_call_delta`` carries the streaming tool-call name;
#: the reducer only reads the first one, so coalescing is safe.
_COALESCED_EVENT_TYPES = frozenset({"assistant_delta", "tool_progress", "tool_call_delta"})


class StreamingCoalescer:
    """Fold high-frequency agent events into 16 ms windows.

    Parameters
    ----------
    apply:
        Reducer entry point. Receives a single :class:`AgentEvent`
        that aggregates every event buffered in the current window
        (or the original event for low-frequency / single-shot
        events).
    window_seconds:
        How long pending events may sit in the buffer before a flush
        is required. The default ``0.016`` is 16 ms.
    clock:
        Monotonic clock used for window accounting. Defaults to
        :func:`time.monotonic`. Tests can inject a fake clock to
        drive deterministic flush behaviour.
    """

    def __init__(
        self,
        *,
        apply: Callable[["AgentEvent"], None],
        window_seconds: float = 0.016,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._apply = apply
        self._window_seconds = window_seconds
        self._clock = clock
        # Pending batch state. We track the merged event per
        # coalescable type so that a single window can have at most
        # one merged ``assistant_delta`` and one merged
        # ``tool_progress`` (the reducer reads only the latest
        # progress message anyway).
        self._pending_assistant_text: str = ""
        self._pending_assistant_metadata: dict | None = None
        self._has_pending_assistant = False
        self._pending_progress: dict[str, str] = {}
        self._pending_progress_metadata: dict[str, dict] = {}
        self._has_pending_progress = False
        self._pending_tool_name: str | None = None
        self._pending_tool_metadata: dict | None = None
        self._has_pending_tool_delta = False
        self._last_flush = clock()

    def push(self, event: "AgentEvent") -> bool:
        """Buffer ``event`` and apply it if it's a low-frequency event.

        Returns ``True`` when the event was applied immediately
        (caller should schedule a screen redraw); returns ``False``
        when it was merged into the pending batch and the caller
        should wait for the next window to redraw.
        """

        event_type = getattr(event, "type", None)
        metadata = getattr(event, "metadata", None) or {}
        if event_type == "assistant_delta":
            text = getattr(event, "text", "") or ""
            if text:
                self._pending_assistant_text += text
                self._has_pending_assistant = True
            # Keep the latest event's attribution so the synthesised
            # ``assistant_delta`` at flush time still carries a stable
            # ``assistant_call_id`` and ``model_turn_index``.
            if self._pending_assistant_metadata is None:
                self._pending_assistant_metadata = dict(metadata)
            else:
                self._pending_assistant_metadata.update(metadata)
            return False
        if event_type == "tool_progress":
            call_id = metadata.get("tool_call_id")
            message = str(metadata.get("message") or metadata.get("text") or "")
            if call_id:
                self._pending_progress[str(call_id)] = message
                self._pending_progress_metadata[str(call_id)] = dict(metadata)
                self._has_pending_progress = True
            return False
        if event_type == "tool_call_delta":
            name = metadata.get("name") or ""
            if name and not self._pending_tool_name:
                self._pending_tool_name = name
            if self._pending_tool_metadata is None:
                self._pending_tool_metadata = dict(metadata)
            else:
                self._pending_tool_metadata.update(metadata)
            self._has_pending_tool_delta = True
            return False
        # Low-frequency event: flush any pending batch first so the
        # visible state reflects the full history, then apply.
        if self._has_pending():
            self.flush()
        self._apply(event)
        return True

    def flush(self) -> bool:
        """Apply every pending event and clear the batch.

        Returns ``True`` when at least one event was flushed. The
        function is a no-op when nothing is pending.
        """

        flushed = False
        if self._has_pending_tool_delta:
            from core.stream_events import AgentEvent

            tool_metadata = dict(self._pending_tool_metadata or {})
            tool_metadata["name"] = self._pending_tool_name or ""
            self._apply(
                AgentEvent(
                    type="tool_call_delta",
                    metadata=tool_metadata,
                )
            )
            self._pending_tool_name = None
            self._pending_tool_metadata = None
            self._has_pending_tool_delta = False
            flushed = True
        if self._has_pending_assistant:
            from core.stream_events import AgentEvent

            self._apply(
                AgentEvent(
                    type="assistant_delta",
                    text=self._pending_assistant_text,
                    metadata=dict(self._pending_assistant_metadata or {}),
                )
            )
            self._pending_assistant_text = ""
            self._pending_assistant_metadata = None
            self._has_pending_assistant = False
            flushed = True
        if self._has_pending_progress:
            from core.stream_events import AgentEvent

            for call_id, message in self._pending_progress.items():
                meta = dict(self._pending_progress_metadata.get(call_id) or {})
                meta["tool_call_id"] = call_id
                meta["message"] = message
                self._apply(
                    AgentEvent(
                        type="tool_progress",
                        metadata=meta,
                    )
                )
            self._pending_progress.clear()
            self._pending_progress_metadata.clear()
            self._has_pending_progress = False
            flushed = True
        if flushed:
            self._last_flush = self._clock()
        return flushed

    def should_flush(self, now: float | None = None) -> bool:
        """Return ``True`` when the window has elapsed and pending events exist."""

        if not self._has_pending():
            return False
        current = self._clock() if now is None else now
        return (current - self._last_flush) >= self._window_seconds

    def _has_pending(self) -> bool:
        return (
            self._has_pending_assistant
            or self._has_pending_progress
            or self._has_pending_tool_delta
        )


__all__ = ["StreamingCoalescer"]
