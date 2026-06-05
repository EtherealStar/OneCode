"""Provider-neutral trace event records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TraceRecordType = Literal["event", "span_start", "span_end"]


@dataclass(frozen=True)
class TraceRecord:
    record_type: TraceRecordType
    timestamp: str
    session_id: str
    trace_id: str
    name: str
    span_id: str | None = None
    parent_span_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


def record_to_json_dict(record: TraceRecord) -> dict[str, Any]:
    """Return a stable JSON object for a trace record."""

    return {
        "record_type": record.record_type,
        "timestamp": record.timestamp,
        "session_id": record.session_id,
        "trace_id": record.trace_id,
        "span_id": record.span_id,
        "parent_span_id": record.parent_span_id,
        "name": record.name,
        "attributes": dict(record.attributes),
    }
