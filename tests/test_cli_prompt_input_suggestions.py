from __future__ import annotations

from pathlib import Path

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.tools.executor import ToolExecutionUpdate
from services.tools.registry import ToolRegistry
from tools.read_file import descriptor as read_file_descriptor
from ui.cli.suggestions import suggestions_for
from ui.cli.types import CliRuntime


class FakeModelClient:
    async def stream(self, snapshot: object):
        raise AssertionError("model should not be called by suggestion tests")
        yield


class FakeToolExecutor:
    async def execute(self, tool_calls: tuple, state: object):
        if False:
            yield ToolExecutionUpdate(type="result")


class FakeLoop:
    async def stream(self, prompt: str):
        raise AssertionError("loop should not be called by suggestion tests")
        yield


def make_runtime(tmp_path: Path) -> CliRuntime:
    state = RuntimeState(session_id="session-cli")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    return CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        registry=ToolRegistry([read_file_descriptor()]),
        loop=FakeLoop(),  # type: ignore[arg-type]
        provider_label="TestProvider",
        model="test-model",
        model_client=FakeModelClient(),
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
    )


def displays(runtime: CliRuntime, text: str) -> list[str]:
    return [item.display for item in suggestions_for(runtime, text, len(text))]


def test_command_suggestions_use_visible_commands(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    items = suggestions_for(runtime, "/sta", len("/sta"))

    assert "/status" in [item.display for item in items]
    status = next(item for item in items if item.display == "/status")
    assert status.kind == "command"
    assert status.replacement == "/status"
    assert "Show runtime status" in status.description


def test_resume_suggestions_list_session_ids(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    messages_path = tmp_path / ".onecode" / "session-old" / "messages.jsonl"
    messages_path.parent.mkdir(parents=True)
    messages_path.write_text("", encoding="utf-8")

    items = suggestions_for(runtime, "/resume session", len("/resume session"))

    assert "session-old" in [item.replacement for item in items]
    assert {item.kind for item in items} == {"session"}


def test_file_suggestions_are_bounded_to_workspace(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
    outside = tmp_path.parent / "outside-onecode-suggestions.txt"
    outside.write_text("no\n", encoding="utf-8")

    try:
        assert "src/" in displays(runtime, "read @s")
        assert "src/main.py" in displays(runtime, "read @src/ma")
        assert not displays(runtime, f"read @{outside}")
    finally:
        outside.unlink(missing_ok=True)


def test_file_suggestions_do_not_scan_recursively(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    nested = tmp_path / "src" / "nested"
    nested.mkdir(parents=True)
    (nested / "deep.py").write_text("", encoding="utf-8")

    assert "src/" in displays(runtime, "read @s")
    assert "src/nested/" in displays(runtime, "read @src/n")
    assert "src/nested/deep.py" not in displays(runtime, "read @s")
