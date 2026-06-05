from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.observability import JsonlTraceSink, TraceRecorder
from services.observability.sanitize import sanitize_attributes


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_jsonl_sink_writes_event_and_span_records(tmp_path: Path) -> None:
    sink = JsonlTraceSink(
        tmp_path / ".onecode",
        "session-trace",
        flush_interval_seconds=60,
    )
    recorder = TraceRecorder(
        session_id="session-trace",
        workspace=tmp_path,
        sink=sink,
    )

    recorder.event("transition", {"transition": "completed"})
    with recorder.span("model_call") as parent:
        with recorder.span("child") as child:
            assert child.parent_span_id == parent.span_id
    recorder.flush()

    records = read_jsonl(sink.trace_path)
    assert [record["record_type"] for record in records] == [
        "event",
        "span_start",
        "span_start",
        "span_end",
        "span_end",
    ]
    assert records[0]["attributes"]["transition"] == "completed"
    assert records[2]["parent_span_id"] == records[1]["span_id"]
    assert records[-1]["attributes"]["duration_ms"] >= 0


def test_span_records_error_and_reraises(tmp_path: Path) -> None:
    sink = JsonlTraceSink(tmp_path / ".onecode", "session-error")
    recorder = TraceRecorder(session_id="session-error", sink=sink)

    with pytest.raises(RuntimeError):
        with recorder.span("model_call"):
            raise RuntimeError("provider failed")
    recorder.flush()

    records = read_jsonl(sink.trace_path)
    end_record = records[-1]
    assert end_record["record_type"] == "span_end"
    assert end_record["attributes"]["error_type"] == "RuntimeError"
    assert end_record["attributes"]["error_message"] == "provider failed"


def test_sanitizer_redacts_sensitive_metadata_and_paths(tmp_path: Path) -> None:
    inside = tmp_path / "src" / "app.py"
    inside.parent.mkdir()
    inside.write_text("print('x')", encoding="utf-8")
    outside = tmp_path.parent / "secret.py"

    sanitized = sanitize_attributes(
        {
            "api_key": "sk-secret",
            "Authorization": "Bearer value",
            "prompt": "full prompt",
            "content": "source code",
            "old_string": "old source",
            "new_string": "new source",
            "stdout": "huge output",
            "stdout_chars": 11,
            "input_tokens": 3,
            "file_path": inside,
            "external_path": outside,
            "long": "x" * 260,
            "nested": {"level1": {"level2": {"level3": "hidden"}}},
        },
        workspace=tmp_path,
    )

    assert sanitized["api_key"] == "[redacted]"
    assert sanitized["Authorization"] == "[redacted]"
    assert sanitized["prompt"] == "[redacted]"
    assert sanitized["content"] == "[redacted]"
    assert sanitized["old_string"] == "[redacted]"
    assert sanitized["new_string"] == "[redacted]"
    assert sanitized["stdout"] == "[redacted]"
    assert sanitized["stdout_chars"] == 11
    assert sanitized["input_tokens"] == 3
    assert sanitized["file_path"] == str(Path("src") / "app.py")
    assert sanitized["external_path"] == "[external_path].py"
    assert len(sanitized["long"]) <= 243
    assert "level3" not in json.dumps(sanitized["nested"], ensure_ascii=False)
