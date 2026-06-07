"""Structured runtime error logging.

Error logs are intentionally separate from trace records.  Trace keeps compact
runtime facts for UI and tests; this module stores sanitized debugging evidence
for unrecovered failures.
"""

from __future__ import annotations

import atexit
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Timer
from typing import Any, Protocol

from services.errors import onecode_error_details, short_error_stack
from services.observability.sanitize import sanitize_attributes

MAX_STACK_CHARS = 4000
MAX_MESSAGE_CHARS = 1000
REDACTED = "[redacted]"

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_SK_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


class ErrorLogSink(Protocol):
    def emit(self, record: Mapping[str, Any]) -> None:
        ...

    def flush(self) -> None:
        ...


class NoopErrorLogSink:
    @property
    def error_log_path(self) -> Path | None:
        return None

    def emit(self, record: Mapping[str, Any]) -> None:
        _ = record

    def flush(self) -> None:
        return None

    def switch_session(self, session_id: str) -> None:
        _ = session_id


class JsonlErrorLogSink:
    def __init__(
        self,
        root_dir: Path,
        session_id: str,
        *,
        flush_interval_seconds: float = 1.0,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.session_id = session_id
        self.flush_interval_seconds = flush_interval_seconds
        self._pending_lines: list[str] = []
        self._flush_timer: Timer | None = None
        self._lock = RLock()
        self.dropped_count = 0
        atexit.register(self.flush)

    @property
    def session_dir(self) -> Path:
        return self.root_dir / self.session_id

    @property
    def error_log_path(self) -> Path:
        return self.session_dir / "errors.jsonl"

    def switch_session(self, session_id: str) -> None:
        self.flush()
        with self._lock:
            self.session_id = session_id

    def emit(self, record: Mapping[str, Any]) -> None:
        try:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            self.dropped_count += 1
            return
        self._enqueue_line(line)

    def flush(self) -> None:
        with self._lock:
            lines = self._pending_lines
            self._pending_lines = []
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None

        if not lines:
            return

        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            with self.error_log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError:
            self.dropped_count += len(lines)

    def _enqueue_line(self, line: str) -> None:
        with self._lock:
            self._pending_lines.append(line)
            if self._flush_timer is None:
                self._flush_timer = Timer(self.flush_interval_seconds, self.flush)
                self._flush_timer.daemon = True
                self._flush_timer.start()


class ErrorLogRecorder:
    def __init__(
        self,
        *,
        session_id: str,
        workspace: Path | None = None,
        sink: ErrorLogSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_id = session_id
        self.workspace = workspace
        self.sink = sink or NoopErrorLogSink()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def noop(cls, session_id: str | None = None) -> "ErrorLogRecorder":
        return cls(session_id=session_id or "", sink=NoopErrorLogSink())

    @property
    def error_log_path(self) -> Path | None:
        return getattr(self.sink, "error_log_path", None)

    def switch_session(self, session_id: str) -> None:
        self.flush()
        self.session_id = session_id
        switch = getattr(self.sink, "switch_session", None)
        if callable(switch):
            switch(session_id)

    def flush(self) -> None:
        self.sink.flush()

    def record_error(
        self,
        error: object,
        *,
        source: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        details = onecode_error_details(error)
        record = {
            "timestamp": self._timestamp(),
            "session_id": self.session_id,
            "source": source,
            "category": details.category.value,
            "error_type": details.error_type,
            "message": _sanitize_text(details.message, self.workspace, MAX_MESSAGE_CHARS),
            "safe_message": _sanitize_text(
                details.safe_message,
                self.workspace,
                MAX_MESSAGE_CHARS,
            ),
            "retryable": details.retryable,
            "stack": _sanitize_text(
                short_error_stack(error),
                self.workspace,
                MAX_STACK_CHARS,
            ),
            "attributes": sanitize_attributes(
                {
                    **details.metadata,
                    **dict(attributes or {}),
                },
                workspace=self.workspace,
            ),
        }
        self.sink.emit(record)

    def record_mcp_error(
        self,
        server_name: str,
        error: object,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        merged = {"mcp_server": server_name, **dict(attributes or {})}
        self.record_error(error, source="mcp", attributes=merged)

    def _timestamp(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_text(value: str, workspace: Path | None, max_chars: int) -> str:
    sanitized = _BEARER_RE.sub(f"Bearer {REDACTED}", value)
    sanitized = _SK_KEY_RE.sub(REDACTED, sanitized)
    sanitized = _sanitize_paths(sanitized, workspace)
    if len(sanitized) <= max_chars:
        return sanitized
    return f"{sanitized[:max_chars]}..."


def _sanitize_paths(value: str, workspace: Path | None) -> str:
    if workspace is None:
        return value
    try:
        workspace_text = str(workspace.expanduser().resolve())
    except OSError:
        workspace_text = str(workspace.expanduser().absolute())
    return value.replace(workspace_text, ".")
