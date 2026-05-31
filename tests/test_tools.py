from __future__ import annotations

from onecode.model_client import ToolCall
from onecode.tools import build_builtin_registry, partition_tool_calls
from onecode.tools.builtin import safe_path


def test_registry_exposes_runtime_schemas(tmp_path):
    registry = build_builtin_registry(tmp_path)
    schemas = registry.api_schemas()

    assert {schema["name"] for schema in schemas} >= {"bash", "read_file", "write_file", "edit_file", "glob"}
    assert registry.get("read_file").meta.read_only is True


def test_safe_path_rejects_escape(tmp_path):
    try:
        safe_path(tmp_path, "../outside.txt")
    except ValueError as exc:
        assert "escapes workspace" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_partition_is_serial_for_mvp(tmp_path):
    registry = build_builtin_registry(tmp_path)
    calls = [
        ToolCall(id="1", name="read_file", input={"path": "a"}),
        ToolCall(id="2", name="glob", input={"pattern": "*"}),
    ]

    assert partition_tool_calls(calls, registry) == [[calls[0]], [calls[1]]]
