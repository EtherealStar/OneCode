"""Context preparer that appends selected long-term memory attachments."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Iterable, Protocol

from core.runtime_state import RuntimeState
from services.context.snapshot import PreparedContext
from services.memory.auto_store import LongTermMemoryStore
from services.memory.selector import RelevantMemorySelector


class InnerContextPreparer(Protocol):
    def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> (
        Iterable[dict[str, Any]]
        | PreparedContext
        | Awaitable[Iterable[dict[str, Any]] | PreparedContext]
    ):
        ...


class RelevantMemoryContextPreparer:
    def __init__(
        self,
        store: LongTermMemoryStore,
        selector: RelevantMemorySelector,
        *,
        inner: InnerContextPreparer | None = None,
        max_total_chars: int = 60_000,
    ) -> None:
        self.store = store
        self.selector = selector
        self.inner = inner
        self.max_total_chars = max_total_chars

    async def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> PreparedContext:
        prepared: Iterable[dict[str, Any]] | PreparedContext
        if self.inner is None:
            prepared = messages
        else:
            prepared = self.inner.prepare(messages, state)
            if inspect.isawaitable(prepared):
                prepared = await prepared
        usage_hints: dict[str, Any] = {}
        transcript_refs: tuple[str, ...] = ()
        if isinstance(prepared, PreparedContext):
            prepared_messages = tuple(prepared.messages)
            usage_hints = dict(prepared.usage_hints)
            transcript_refs = tuple(prepared.transcript_refs)
        else:
            prepared_messages = tuple(prepared)
        memory_messages = await self._memory_attachments(prepared_messages, state)
        if memory_messages:
            usage_hints["relevant_memory_count"] = len(memory_messages)
        return PreparedContext(
            messages=(*prepared_messages, *memory_messages),
            usage_hints=usage_hints,
            transcript_refs=transcript_refs,
        )

    async def _memory_attachments(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> tuple[dict[str, Any], ...]:
        catalog = self.store.scan()
        selected = await self.selector.select(messages, state, catalog)
        attachments: list[dict[str, Any]] = []
        total_chars = 0
        surfaced: list[str] = []
        for memory in selected:
            content = self.store.read_topic(memory.relative_path)
            if total_chars + len(content) > self.max_total_chars:
                content = content[: max(0, self.max_total_chars - total_chars)].rstrip()
                content += "\n[Memory attachment budget reached]"
            total_chars += len(content)
            attachments.append(
                {
                    "role": "attachment",
                    "attachment": {
                        "type": "relevant_memories",
                        "path": memory.relative_path,
                        "content": content,
                    },
                    "metadata": {
                        "synthetic": True,
                        "source": "long_term_memory",
                    },
                }
            )
            surfaced.append(memory.relative_path)
            if total_chars >= self.max_total_chars:
                break
        if surfaced:
            state.metadata["long_term_memory_surface_paths"] = surfaced
        return tuple(attachments)
