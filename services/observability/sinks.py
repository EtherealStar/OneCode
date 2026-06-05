"""Trace sinks."""

from __future__ import annotations

import atexit
import json
from pathlib import Path
from threading import RLock, Timer
from typing import Protocol

from services.observability.events import TraceRecord, record_to_json_dict


class TraceSink(Protocol):
    def emit(self, record: TraceRecord) -> None:
        ...

    def flush(self) -> None:
        ...


class NoopTraceSink:
    @property
    def trace_path(self) -> Path | None:
        return None

    def emit(self, record: TraceRecord) -> None:
        _ = record

    def flush(self) -> None:
        return None

    def switch_session(self, session_id: str) -> None:
        _ = session_id


class JsonlTraceSink:
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
    def trace_path(self) -> Path:
        return self.session_dir / "trace.jsonl"

    def switch_session(self, session_id: str) -> None:
        self.flush()
        with self._lock:
            self.session_id = session_id

    def emit(self, record: TraceRecord) -> None:
        try:
            line = json.dumps(
                record_to_json_dict(record),
                ensure_ascii=False,
                separators=(",", ":"),
            )
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
            with self.trace_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError:
            self.dropped_count += len(lines)

    def _enqueue_line(self, line: str) -> None:
        with self._lock:
            self._pending_lines.append(line)
            if self._flush_timer is None:
                self._flush_timer = Timer(
                    self.flush_interval_seconds,
                    self.flush,
                )
                self._flush_timer.daemon = True
                self._flush_timer.start()
