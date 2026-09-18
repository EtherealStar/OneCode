from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.context.run_facts import RunFactsAccumulator
from services.context.snapshot import ContextSnapshot
from services.context.transcript import JsonlTranscriptStore
from services.model.stream import ModelStreamEvent
from services.tools.executor import RegistryToolExecutor, ToolExecutionUpdate
from services.tools.registry import ToolRegistry
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
)


def make_store(tmp_path: Path, session_id: str = "session-interrupt") -> MessageStore:
    return MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )


def read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def restore(tmp_path: Path, session_id: str):
    state = RuntimeState()
    store = MessageStore.from_transcript(
        JsonlTranscriptStore(
            tmp_path / ".onecode",
            session_id,
            cwd=tmp_path,
            flush_interval_seconds=60,
        ),
        state,
    )
    return store.current_messages()


def test_interrupt_keeps_half_text_exactly_once(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("please explain")
    user_uuid = store.last_record_uuid
    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    accumulator.add_text("This is the half")
    facts = accumulator.freeze()

    result = store.finalize_interrupted_run(facts)

    assert result.success is True
    messages = store.current_messages()
    assert messages[0] == {"role": "user", "content": "please explain"}
    assert messages[1] == {"role": "assistant", "content": "This is the half"}

    disk = read_jsonl(store.transcript_store.messages_path)
    assert [record["message"]["role"] for record in disk] == ["user", "assistant"]
    assert [record["message"].get("content") for record in disk] == [
        "please explain",
        "This is the half",
    ]
    assert restore(tmp_path, store.session_id) == messages

    second = store.finalize_interrupted_run(facts)
    assert second.success is True
    disk_again = read_jsonl(store.transcript_store.messages_path)
    assert len(disk_again) == 2


def test_interrupt_keeps_real_pair_and_removes_unpaired_declaration(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    store.append_user("run tools")
    user_uuid = store.last_record_uuid
    store.append_assistant(
        {
            "role": "assistant",
            "content": "working",
            "tool_calls": [
                {"id": "call-a", "function": {"name": "read_file"}},
                {"id": "call-b", "function": {"name": "grep"}},
            ],
        },
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    accumulator.declare("call-a", "read_file")
    accumulator.declare("call-b", "grep")
    accumulator.add_result(
        ToolExecutionResult(
            tool_call_id="call-a",
            tool_name="read_file",
            content="real result",
        )
    )
    facts = accumulator.freeze()

    result = store.finalize_interrupted_run(facts)

    assert result.success is True
    disk = read_jsonl(store.transcript_store.messages_path)
    assistant = next(r for r in disk if r["message"]["role"] == "assistant")
    assert [call["id"] for call in assistant["message"]["tool_calls"]] == ["call-a"]
    tool_results = [r for r in disk if r["message"]["role"] == "tool_result"]
    assert [r["message"]["tool_call_id"] for r in tool_results] == ["call-a"]
    assert all(r["message"].get("metadata", {}).get("synthetic") is not True for r in disk)
    assert store.current_messages()[1]["tool_calls"] == [
        {"id": "call-a", "function": {"name": "read_file"}}
    ]


def test_interrupt_removes_orphan_tool_result(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("run tools")
    user_uuid = store.last_record_uuid
    store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-a", "function": {"name": "read_file"}}],
        },
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call-a", tool_name="read_file", content="a"
            ),
            ToolExecutionResult(
                tool_call_id="call-orphan", tool_name="grep", content="o"
            ),
        ],
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    accumulator.declare("call-a", "read_file")
    accumulator.add_result(
        ToolExecutionResult(tool_call_id="call-a", tool_name="read_file", content="a")
    )
    facts = accumulator.freeze()

    result = store.finalize_interrupted_run(facts)

    assert result.success is True
    assert result.records_removed == 1
    disk = read_jsonl(store.transcript_store.messages_path)
    ids = [
        r["message"].get("tool_call_id")
        for r in disk
        if r["message"]["role"] == "tool_result"
    ]
    assert ids == ["call-a"]


def test_interrupt_clears_pending_buffer_after_rewrite(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("run tools")
    user_uuid = store.last_record_uuid
    store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-a", "function": {"name": "read_file"}}],
        },
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    accumulator.declare("call-a", "read_file")
    accumulator.add_result(
        ToolExecutionResult(tool_call_id="call-a", tool_name="read_file", content="a")
    )
    facts = accumulator.freeze()

    # Records are still in the pending buffer (never flushed).
    result = store.finalize_interrupted_run(facts)
    assert result.success is True

    # A later timer flush must not write the stale buffered records back.
    store.transcript_store.flush()
    disk = read_jsonl(store.transcript_store.messages_path)
    assert [r["message"]["role"] for r in disk] == [
        "user",
        "assistant",
        "tool_result",
    ]
    assert len(disk) == 3


def test_interrupt_rewrite_failure_keeps_original_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import services.context.transcript as transcript_module

    store = make_store(tmp_path)
    store.append_user("run tools")
    store.flush_transcript()
    user_uuid = store.last_record_uuid
    store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-a", "function": {"name": "read_file"}}],
        },
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    store.flush_transcript()
    before = store.transcript_store.messages_path.read_text(encoding="utf-8")

    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    accumulator.declare("call-a", "read_file")
    accumulator.add_result(
        ToolExecutionResult(tool_call_id="call-a", tool_name="read_file", content="a")
    )
    facts = accumulator.freeze()

    def fail_replace(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr(transcript_module.os, "replace", fail_replace)

    result = store.finalize_interrupted_run(facts)

    assert result.success is False
    assert result.error is not None
    assert result.staging_path is not None
    assert store.transcript_store.messages_path.read_text(encoding="utf-8") == before
    # Memory is not committed on failure.
    roles = [message["role"] for message in store.current_messages()]
    assert roles == ["user", "assistant"]

    monkeypatch.undo()
    retry = store.finalize_interrupted_run(facts)
    assert retry.success is True
    disk = read_jsonl(store.transcript_store.messages_path)
    assert [r["message"]["role"] for r in disk] == ["user", "assistant", "tool_result"]


def test_interrupt_staging_write_failure_logs_and_keeps_original(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from pathlib import Path as PathType

    store = make_store(tmp_path)
    store.append_user("run tools")
    store.flush_transcript()
    user_uuid = store.last_record_uuid
    store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-a", "function": {"name": "read_file"}}],
        },
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    store.flush_transcript()
    before = store.transcript_store.messages_path.read_text(encoding="utf-8")

    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    accumulator.declare("call-a", "read_file")
    accumulator.add_result(
        ToolExecutionResult(tool_call_id="call-a", tool_name="read_file", content="a")
    )
    facts = accumulator.freeze()

    original_write_text = PathType.write_text

    def flaky_write_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if ".staging-" in self.name:
            raise OSError("no space left")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(PathType, "write_text", flaky_write_text)

    logged: list[str] = []

    class _Recorder:
        def record_error(self, error, *, source, attributes=None):  # type: ignore[no-untyped-def]
            _ = attributes
            logged.append(f"{source}:{type(error).__name__}")

    result = store.finalize_interrupted_run(facts, error_log_recorder=_Recorder())

    assert result.success is False
    assert logged == ["context.interrupt_cleanup:OSError"]
    assert store.transcript_store.messages_path.read_text(encoding="utf-8") == before
    assert [message["role"] for message in store.current_messages()] == [
        "user",
        "assistant",
    ]


def test_interrupt_preserves_unaffected_history_identity(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("first")
    store.append_assistant({"content": "first answer"})
    store.append_user("second")
    store.flush_transcript()
    untouched = [
        record["uuid"] for record in read_jsonl(store.transcript_store.messages_path)
    ]
    user_uuid = store.last_record_uuid
    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    accumulator.add_text("partial")
    facts = accumulator.freeze()

    result = store.finalize_interrupted_run(facts)

    assert result.success is True
    disk = read_jsonl(store.transcript_store.messages_path)
    assert [record["uuid"] for record in disk[:3]] == untouched
    assert disk[0]["parent_uuid"] is None
    assert disk[1]["parent_uuid"] == disk[0]["uuid"]
    assert disk[2]["parent_uuid"] == disk[1]["uuid"]
    assert disk[3]["parent_uuid"] == disk[2]["uuid"]


def test_interrupt_drops_empty_assistant(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("q")
    user_uuid = store.last_record_uuid
    store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-x", "function": {"name": "read_file"}}],
        },
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    facts = accumulator.freeze()

    result = store.finalize_interrupted_run(facts)

    assert result.success is True
    assert result.records_removed == 1
    disk = read_jsonl(store.transcript_store.messages_path)
    assert [r["message"]["role"] for r in disk] == ["user"]


def test_interrupt_prunes_content_block_declarations(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("q")
    user_uuid = store.last_record_uuid
    store.append_assistant(
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "keep this text"},
                {"type": "tool_use", "id": "call-a", "name": "read_file"},
            ],
        },
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    facts = accumulator.freeze()

    result = store.finalize_interrupted_run(facts)

    assert result.success is True
    disk = read_jsonl(store.transcript_store.messages_path)
    assistant = next(r for r in disk if r["message"]["role"] == "assistant")
    assert assistant["message"]["content"] == [
        {"type": "text", "text": "keep this text"}
    ]


def test_interrupt_keeps_real_error_result_pair(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("q")
    user_uuid = store.last_record_uuid
    store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-denied", "function": {"name": "bash"}}],
        },
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call-denied",
                tool_name="bash",
                content='{"error": "permission_denied"}',
                is_error=True,
            )
        ],
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    accumulator.declare("call-denied", "bash")
    accumulator.add_result(
        ToolExecutionResult(
            tool_call_id="call-denied",
            tool_name="bash",
            content='{"error": "permission_denied"}',
            is_error=True,
        )
    )
    facts = accumulator.freeze()

    result = store.finalize_interrupted_run(facts)

    assert result.success is True
    store.flush_transcript()
    disk = read_jsonl(store.transcript_store.messages_path)
    results = [r for r in disk if r["message"]["role"] == "tool_result"]
    assert len(results) == 1
    assert results[0]["message"]["tool_call_id"] == "call-denied"
    assert results[0]["message"]["is_error"] is True


def test_interrupt_keeps_attachments(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("q")
    user_uuid = store.last_record_uuid
    store.append_attachments(
        [{"role": "attachment", "content": "", "attachment": {"type": "file"}}]
    )
    accumulator = RunFactsAccumulator(session_id=store.session_id)
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    accumulator.add_text("partial")
    facts = accumulator.freeze()

    result = store.finalize_interrupted_run(facts)

    assert result.success is True
    disk = read_jsonl(store.transcript_store.messages_path)
    assert [r["message"]["role"] for r in disk] == [
        "user",
        "attachment",
        "assistant",
    ]
    assert store.current_messages()[1]["role"] == "attachment"


def test_interrupt_cleanup_works_on_ephemeral_store(tmp_path: Path) -> None:
    store = MessageStore.ephemeral(session_id="child-session")
    store.append_user("q")
    user_uuid = store.last_record_uuid
    store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-x", "function": {"name": "read_file"}}],
        },
        assistant_call_id="ac_1",
        model_turn_index=1,
    )
    accumulator = RunFactsAccumulator(session_id="child-session")
    accumulator.user_prompt_uuid = user_uuid
    accumulator.begin_model_call("ac_1", 1)
    facts = accumulator.freeze()

    result = store.finalize_interrupted_run(facts)

    assert result.success is True
    assert [message["role"] for message in store.current_messages()] == ["user"]
    assert not store.transcript_store.messages_path.exists()


class _OneShotModel:
    def __init__(self, tool_calls: tuple[ToolCall, ...]) -> None:
        self._tool_calls = tool_calls
        self._served = False

    async def stream(self, snapshot: ContextSnapshot):
        _ = snapshot
        if self._served:
            raise AssertionError("unexpected second model call")
        self._served = True
        for tool_call in self._tool_calls:
            yield ModelStreamEvent.tool_call_completed(tool_call)
        yield ModelStreamEvent.message_completed(
            assistant_message={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "function": {"name": tool_call.name},
                    }
                    for tool_call in self._tool_calls
                ],
            },
            final_text="",
            tool_calls=self._tool_calls,
            stop_reason="tool_calls",
        )


class _BlockingToolExecutor:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def execute(self, tool_calls: tuple[ToolCall, ...], state: RuntimeState):
        _ = state
        first = tool_calls[0]
        yield ToolExecutionUpdate(
            type="started", tool_call_id=first.id, tool_name=first.name
        )
        yield ToolExecutionUpdate(
            type="result",
            result=ToolExecutionResult(
                tool_call_id=first.id,
                tool_name=first.name,
                content="first result",
            ),
            tool_call_id=first.id,
            tool_name=first.name,
        )
        await self.release.wait()


def test_loop_retains_result_before_batch_append(tmp_path: Path) -> None:
    state = RuntimeState()
    tool_calls = (
        ToolCall(id="call-a", name="read_file", input={}),
        ToolCall(id="call-b", name="grep", input={}),
    )
    store = make_store(tmp_path, state.session_id)
    model = _OneShotModel(tool_calls)
    executor = _BlockingToolExecutor()
    loop = AgentLoop(
        state=state,
        message_store=store,
        context_engine=ContextEngine(store),
        model_client=model,  # type: ignore[arg-type]
        tool_executor=executor,  # type: ignore[arg-type]
    )

    async def consume_until_result():
        generator = loop.stream("run tools")
        async for event in generator:
            if event.type == "tool_result":
                break
        await generator.aclose()

    asyncio.run(consume_until_result())

    facts = loop.snapshot_run_facts()
    assert facts is not None
    assert facts.status == "interrupted"
    assert {call.tool_call_id for call in facts.tool_calls} == {"call-a", "call-b"}
    assert {result.tool_call_id for result in facts.results} == {"call-a"}
    assert facts.assistant_call_id is not None

    result = store.finalize_interrupted_run(facts)
    assert result.success is True
    disk = read_jsonl(store.transcript_store.messages_path)
    assistant = next(r for r in disk if r["message"]["role"] == "assistant")
    assert [call["id"] for call in assistant["message"]["tool_calls"]] == ["call-a"]
    results = [r["message"]["tool_call_id"] for r in disk if r["message"]["role"] == "tool_result"]
    assert results == ["call-a"]
    # The assistant record keeps the stable assistant_call_id association.
    assert assistant.get("assistant_call_id") == facts.assistant_call_id


def test_executor_waits_for_handlers_on_cancel() -> None:
    started: list[str] = []
    cancelled: list[str] = []
    gate = asyncio.Event()
    ready = asyncio.Event()

    def classify(tool_input, runtime):  # type: ignore[no-untyped-def]
        _ = tool_input, runtime
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
        )

    async def slow_handler(tool_input, runtime):  # type: ignore[no-untyped-def]
        _ = tool_input, runtime
        name = runtime.tool_call_id
        started.append(name)
        if len(started) == 2:
            ready.set()
        try:
            await gate.wait()
        except asyncio.CancelledError:
            cancelled.append(name)
            raise
        return ToolExecutionResult(tool_call_id=name, tool_name="slow", content="ok")

    registry = ToolRegistry(
        [
            ToolDescriptor(
                name="slow",
                description="slow",
                input_schema={"type": "object", "properties": {}},
                handler=slow_handler,
                classify_input=classify,
            )
        ]
    )
    executor = RegistryToolExecutor(registry, max_tool_concurrency=2)
    state = RuntimeState()
    calls = (
        ToolCall(id="call-a", name="slow", input={}),
        ToolCall(id="call-b", name="slow", input={}),
    )

    async def scenario() -> None:
        generator = executor.execute(calls, state)
        async for _update in generator:
            pass

    async def run() -> None:
        task = asyncio.ensure_future(scenario())
        await asyncio.wait_for(ready.wait(), timeout=2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())

    assert sorted(started) == ["call-a", "call-b"]
    assert sorted(cancelled) == ["call-a", "call-b"]
