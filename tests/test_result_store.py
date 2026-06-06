from __future__ import annotations

import asyncio

from core.runtime_state import RuntimeState
from services.compaction.result_store import ToolResultStore
from services.guard import SandboxBoundary, SandboxGuard
from services.permissions import PermissionPolicy
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
)
from tools.read_file import descriptor as read_file_descriptor


def _execute_results(
    executor: RegistryToolExecutor,
    tool_calls: tuple[ToolCall, ...],
    state: RuntimeState,
) -> list[ToolExecutionResult]:
    async def collect() -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        async for update in executor.execute(tool_calls, state):
            if update.result is not None:
                results.append(update.result)
        return results

    return asyncio.run(collect())


def test_result_store_persists_content_and_formats_reference(tmp_path) -> None:
    store = ToolResultStore(tmp_path / ".onecode" / "session-1")

    ref = store.persist_tool_result(
        tool_call_id="call/1",
        tool_name="grep",
        content="full result",
    )
    rendered = store.format_model_reference(ref, preview="full")

    assert ref.relative_path.startswith("tool-results/")
    assert ref.absolute_path.read_text(encoding="utf-8") == "full result"
    assert "call_1" in ref.result_id
    assert str(ref.absolute_path) in rendered
    assert "full" in rendered


def test_result_store_reuses_same_reference_for_same_content(tmp_path) -> None:
    store = ToolResultStore(tmp_path / ".onecode" / "session-1")

    first = store.persist_tool_result(
        tool_call_id="call-1",
        tool_name="grep",
        content="full result",
    )
    second = store.persist_tool_result(
        tool_call_id="call-1",
        tool_name="grep",
        content="full result",
    )

    assert second == first
    assert sorted(path.name for path in store.results_dir.iterdir()) == ["call-1.txt"]


def test_result_store_uses_stable_hash_suffix_for_changed_content(tmp_path) -> None:
    store = ToolResultStore(tmp_path / ".onecode" / "session-1")

    first = store.persist_tool_result(
        tool_call_id="call-1",
        tool_name="grep",
        content="first result",
    )
    second = store.persist_tool_result(
        tool_call_id="call-1",
        tool_name="grep",
        content="second result",
    )
    third = store.persist_tool_result(
        tool_call_id="call-1",
        tool_name="grep",
        content="second result",
    )

    assert first.relative_path == "tool-results/call-1.txt"
    assert second.relative_path.startswith("tool-results/call-1-")
    assert third == second
    assert len(tuple(store.results_dir.iterdir())) == 2


def test_executor_persists_oversized_result_when_store_is_injected(tmp_path) -> None:
    state = RuntimeState(session_id="session-store")
    result_store = ToolResultStore(tmp_path / ".onecode" / state.session_id)

    def handler(
        tool_input: dict,
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id="call-1",
            tool_name="tool",
            content="abcdef",
        )

    def classify_input(
        tool_input: dict,
        runtime: ToolRuntime,
    ) -> ToolCallClassification:
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            result_policy=ToolResultPolicy(
                max_result_size_chars=3,
                persist_when_exceeded=True,
                preview_chars=2,
            ),
        )

    descriptor = ToolDescriptor(
        name="tool",
        description="tool",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=handler,
        classify_input=classify_input,
    )
    executor = RegistryToolExecutor(
        ToolRegistry([descriptor]),
        result_store=result_store,
    )

    result = _execute_results(
        executor,
        (ToolCall(id="call-1", name="tool", input={}),),
        state,
    )[0]

    assert result.metadata["result_stored"] is True
    assert result.metadata["stored_result_relative_path"] == "tool-results/call-1.txt"
    assert "Preview:\nab" in result.content
    assert (result_store.results_dir / "call-1.txt").read_text(encoding="utf-8") == "abcdef"


def test_permission_policy_exempts_current_session_tool_results_read(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = RuntimeState(session_id="session-1")
    tool_results = workspace / ".onecode" / state.session_id / "tool-results"
    tool_results.mkdir(parents=True)
    target = tool_results / "call-1.txt"
    target.write_text("stored", encoding="utf-8")

    descriptor = read_file_descriptor()
    tool_input = {"file_path": str(target)}
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    runtime = ToolRuntime(state=state, guard=guard)
    classification = descriptor.classify_input(tool_input, runtime)
    guard_policy = guard.check_path(target, operation="read", kind="file")

    decision = PermissionPolicy().evaluate(
        tool_call=ToolCall(id="call-1", name="read_file", input=tool_input),
        descriptor=descriptor,
        classification=classification,
        guard_policies=(guard_policy,),
        state=state,
    )

    assert decision.action == "allow"


def test_permission_policy_still_protects_other_onecode_files(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = RuntimeState(session_id="session-1")
    target = workspace / ".onecode" / state.session_id / "messages.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    descriptor = read_file_descriptor()
    tool_input = {"file_path": str(target)}
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    runtime = ToolRuntime(state=state, guard=guard)
    classification = descriptor.classify_input(tool_input, runtime)
    guard_policy = guard.check_path(target, operation="read", kind="file")

    decision = PermissionPolicy().evaluate(
        tool_call=ToolCall(id="call-1", name="read_file", input=tool_input),
        descriptor=descriptor,
        classification=classification,
        guard_policies=(guard_policy,),
        state=state,
    )

    assert decision.action == "ask"
