from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from services.errors import OneCodeError, ErrorCategory
from services.observability.error_log import ErrorLogRecorder, JsonlErrorLogSink


def test_jsonl_error_log_writes_sanitized_records(tmp_path: Path) -> None:
    session_id = "session-x"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sink = JsonlErrorLogSink(
        tmp_path / ".onecode",
        session_id,
        flush_interval_seconds=60,
    )
    recorder = ErrorLogRecorder(
        session_id=session_id,
        workspace=workspace,
        sink=sink,
        clock=lambda: datetime(2026, 6, 7, tzinfo=timezone.utc),
    )
    error = OneCodeError(
        f"failed in {workspace}\\secret.py with Bearer abc.def and sk-testsecret123",
        category=ErrorCategory.INTERNAL,
        safe_message="Internal failure.",
    )

    recorder.record_error(
        error,
        source="unit_test",
        attributes={
            "file_path": workspace / "secret.py",
            "prompt": "must redact",
            "count": 3,
        },
    )
    recorder.flush()

    record = json.loads(sink.error_log_path.read_text(encoding="utf-8"))
    assert record["timestamp"] == "2026-06-07T00:00:00Z"
    assert record["session_id"] == session_id
    assert record["source"] == "unit_test"
    assert record["category"] == "internal"
    assert record["message"] == "failed in .\\secret.py with Bearer [redacted] and [redacted]"
    assert "sk-testsecret123" not in json.dumps(record)
    assert record["attributes"]["file_path"] == "secret.py"
    assert record["attributes"]["prompt"] == "[redacted]"
    assert record["attributes"]["count"] == 3


def test_error_log_switch_session_writes_new_file(tmp_path: Path) -> None:
    sink = JsonlErrorLogSink(
        tmp_path / ".onecode",
        "session-a",
        flush_interval_seconds=60,
    )
    recorder = ErrorLogRecorder(session_id="session-a", sink=sink)

    recorder.record_error(RuntimeError("first"), source="test")
    recorder.switch_session("session-b")
    recorder.record_error(RuntimeError("second"), source="test")
    recorder.flush()

    first = tmp_path / ".onecode" / "session-a" / "errors.jsonl"
    second = tmp_path / ".onecode" / "session-b" / "errors.jsonl"
    assert "first" in first.read_text(encoding="utf-8")
    assert "second" in second.read_text(encoding="utf-8")


def test_error_log_records_mcp_server_attribute(tmp_path: Path) -> None:
    sink = JsonlErrorLogSink(
        tmp_path / ".onecode",
        "session-x",
        flush_interval_seconds=60,
    )
    recorder = ErrorLogRecorder(session_id="session-x", sink=sink)

    recorder.record_mcp_error("server-a", RuntimeError("failed"))
    recorder.flush()

    record = json.loads(sink.error_log_path.read_text(encoding="utf-8"))
    assert record["source"] == "mcp"
    assert record["attributes"]["mcp_server"] == "server-a"
