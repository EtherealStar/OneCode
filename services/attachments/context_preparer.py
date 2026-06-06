"""Context preparer wrapper that applies attachment projection."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Iterable, Protocol

from core.runtime_state import RuntimeState
from services.attachments.projector import AttachmentProjector
from services.context.snapshot import PreparedContext


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


class AttachmentContextPreparer:
    def __init__(
        self,
        inner: InnerContextPreparer | None = None,
        projector: AttachmentProjector | None = None,
    ) -> None:
        self.inner = inner
        self.projector = projector or AttachmentProjector()

    async def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> PreparedContext:
        """Run optional inner preparation, then hide raw attachment messages."""

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
        return PreparedContext(
            messages=self.projector.project(prepared_messages, state),
            usage_hints=usage_hints,
            transcript_refs=transcript_refs,
        )
