"""Tests that confirm ``AgentLoop.stream`` releases events in real time.

These tests exist because the old design collected every event from a
model attempt into a ``model_events`` list and only forwarded them to
the caller once the attempt completed — which meant the CLI was
showing batched output, not streaming output. After the rewrite the
caller must observe the first ``assistant_delta`` while the provider
is still mid-stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.model.stream import ModelStreamEvent


@dataclass
class SlowStreamingFakeModelClient:
    """Fake model client that sleeps between deltas.

    Used to verify that ``AgentLoop.stream`` actually yields
    ``assistant_delta`` while the provider is still mid-stream rather
    than after ``message_completed``.
    """

    streams: list[list[ModelStreamEvent]]
    snapshots: list[ContextSnapshot] = field(default_factory=list)
    first_delta_after_complete: list[bool] = field(default_factory=list)

    async def stream(
        self,
        snapshot: ContextSnapshot,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.snapshots.append(snapshot)
        if not self.streams:
            raise AssertionError("unexpected model stream")
        events = self.streams.pop(0)
        # Yield first delta, sleep, then yield the rest. This proves
        # the consumer receives the delta while the provider is still
        # running.
        first = events[0]
        for event in events[1:]:
            yield first
            await asyncio.sleep(0.02)
            first = event
        yield first


def _make_loop(
    tmp_path: Path,
    streams: list[list[ModelStreamEvent]],
) -> tuple[AgentLoop, MessageStore, SlowStreamingFakeModelClient]:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    model_client = SlowStreamingFakeModelClient(streams)
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model_client,  # type: ignore[arg-type]
        tool_executor=_NoopToolExecutor(),
    )
    return loop, message_store, model_client


@dataclass
class _NoopToolExecutor:
    async def execute(self, tool_calls, state):  # type: ignore[no-untyped-def]
        if False:
            yield None
        return
        yield None  # pragma: no cover


def test_first_delta_arrives_before_message_completed(tmp_path: Path) -> None:
    """The first ``assistant_delta`` must be observable before the attempt ends."""

    async def run() -> list[AgentEvent]:
        loop, _, _ = _make_loop(
            tmp_path,
            [
                [
                    ModelStreamEvent.content_delta("first chunk "),
                    ModelStreamEvent.content_delta("second "),
                    ModelStreamEvent.content_delta("third"),
                    ModelStreamEvent.message_completed(
                        assistant_message={
                            "role": "assistant",
                            "content": "first chunk second third",
                        },
                        final_text="first chunk second third",
                    ),
                ]
            ],
        )

        results: list[AgentEvent] = []
        consumer_started = asyncio.Event()
        first_delta_seen = asyncio.Event()

        async def consume() -> None:
            consumer_started.set()
            async for event in loop.stream("hi"):
                results.append(event)
                if event.type == "assistant_delta" and not first_delta_seen.is_set():
                    first_delta_seen.set()

        consumer = asyncio.create_task(consume())
        await consumer_started.wait()
        # Wait for the first delta or timeout — proves the consumer
        # received it before the producer's `message_completed` event
        # was reached.
        try:
            await asyncio.wait_for(first_delta_seen.wait(), timeout=1.0)
        except asyncio.TimeoutError as exc:  # pragma: no cover
            await consumer
            raise AssertionError("first assistant_delta never arrived") from exc
        await consumer
        return results

    events = asyncio.run(run())
    types = [event.type for event in events]
    assert "interaction_started" in types
    # First three events are deltas, fourth is the message completion.
    assert types[:5] == [
        "interaction_started",
        "assistant_delta",
        "assistant_delta",
        "assistant_delta",
        "assistant_message_completed",
    ]
    deltas = [event.text for event in events if event.type == "assistant_delta"]
    assert deltas == ["first chunk ", "second ", "third"]
    assert events[-1].type == "completed"
