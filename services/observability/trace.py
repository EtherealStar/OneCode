"""Trace recorder and span helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextvars
from contextvars import ContextVar, Token
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
from typing import Any

from services.observability.events import TraceRecord
from services.observability.sanitize import sanitize_attributes
from services.observability.sinks import NoopTraceSink, TraceSink

_CURRENT_SPAN_ID: ContextVar[str | None] = ContextVar(
    "onecode_current_span_id",
    default=None,
)


class TraceRecorder:
    def __init__(
        self,
        *,
        session_id: str,
        workspace: Path | None = None,
        sink: TraceSink | None = None,
        trace_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[], str] | None = None,
    ) -> None:
        self.session_id = session_id
        self.workspace = workspace
        self.sink = sink or NoopTraceSink()
        self.trace_id = trace_id or str(uuid.uuid4())
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_generator = id_generator or (lambda: str(uuid.uuid4()))

    @classmethod
    def noop(cls, session_id: str = "") -> "TraceRecorder":
        return cls(session_id=session_id, sink=NoopTraceSink())

    @property
    def trace_path(self) -> Path | None:
        return getattr(self.sink, "trace_path", None)

    def event(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
        *,
        parent_span_id: str | None = None,
    ) -> None:
        self._emit(
            "event",
            name,
            span_id=None,
            parent_span_id=parent_span_id or _CURRENT_SPAN_ID.get(),
            attributes=attributes,
        )

    def start_span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
        *,
        parent_span_id: str | None = None,
    ) -> "TraceSpan":
        span_id = self._id_generator()
        parent_id = parent_span_id or _CURRENT_SPAN_ID.get()
        self._emit(
            "span_start",
            name,
            span_id=span_id,
            parent_span_id=parent_id,
            attributes=attributes,
        )
        return TraceSpan(self, name=name, span_id=span_id, parent_span_id=parent_id)

    def span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
        *,
        parent_span_id: str | None = None,
    ) -> "TraceSpan":
        return self.start_span(name, attributes, parent_span_id=parent_span_id)

    def flush(self) -> None:
        self.sink.flush()

    def switch_session(self, session_id: str) -> None:
        self.flush()
        self.session_id = session_id
        self.trace_id = str(uuid.uuid4())
        switch = getattr(self.sink, "switch_session", None)
        if callable(switch):
            switch(session_id)

    def recent_records(self, limit: int = 20) -> list[dict[str, Any]]:
        path = self.trace_path
        if path is None:
            return []
        self.flush()
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()[-max(1, limit):]
        except OSError:
            return []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    def _end_span(
        self,
        span: "TraceSpan",
        attributes: Mapping[str, Any] | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        merged: dict[str, Any] = {
            "duration_ms": round((time.perf_counter() - span._started_at) * 1000, 3)
        }
        if attributes:
            merged.update(attributes)
        if error is not None:
            merged.update(
                {
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                }
            )
        self._emit(
            "span_end",
            span.name,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            attributes=merged,
        )

    def _emit(
        self,
        record_type: str,
        name: str,
        *,
        span_id: str | None,
        parent_span_id: str | None,
        attributes: Mapping[str, Any] | None,
    ) -> None:
        try:
            record = TraceRecord(
                record_type=record_type,  # type: ignore[arg-type]
                timestamp=self._timestamp(),
                session_id=self.session_id,
                trace_id=self.trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                name=name,
                attributes=sanitize_attributes(
                    attributes,
                    workspace=self.workspace,
                ),
            )
            self.sink.emit(record)
        except Exception:
            return

    def _timestamp(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class TraceSpan:
    def __init__(
        self,
        recorder: TraceRecorder,
        *,
        name: str,
        span_id: str,
        parent_span_id: str | None,
    ) -> None:
        self._recorder = recorder
        self.name = name
        self._span_id = span_id
        self.parent_span_id = parent_span_id
        self._started_at = time.perf_counter()
        self._ended = False
        self._token: Token[str | None] | None = None

    @property
    def span_id(self) -> str:
        return self._span_id

    def __enter__(self) -> "TraceSpan":
        self._token = _CURRENT_SPAN_ID.set(self.span_id)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        _ = exc_type, traceback
        self.end(error=exc)
        if self._token is not None:
            try:
                _CURRENT_SPAN_ID.reset(self._token)
            except ValueError:
                # Async generators can be closed from a different
                # context than the one that entered the span, for
                # example when the CLI cancels a streaming turn. Trace
                # cleanup must not turn cancellation into a runtime
                # failure.
                contextvars.copy_context().run(_CURRENT_SPAN_ID.set, self.parent_span_id)
            self._token = None
        return False

    def end(
        self,
        attributes: Mapping[str, Any] | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        if self._ended:
            return
        self._ended = True
        self._recorder._end_span(self, attributes, error=error)
