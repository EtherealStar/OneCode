from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.background_tasks import BackgroundTaskManager
from services.compaction import (
    SessionMemoryExtractionPolicy,
    SessionMemoryExtractionService,
    SessionMemoryStore,
    count_tool_calls,
    should_extract_memory,
)
from services.compaction.token_estimator import estimate_messages_tokens
from services.context.current_model_context import CurrentModelContext
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.guard import SandboxBoundary, SandboxGuard
from services.model.stream import ModelStreamEvent
from services.model.types import LLMResponse
from services.observability import TraceRecorder
from services.permissions import PermissionPolicy, SessionPermissionStore
from services.subagents.runner import SubagentRunner
from services.subagents.types import SubagentRequest, SubagentResult
from services.tools.types import ToolCall
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor
from ui.cli.session_memory import BackgroundSessionMemoryExtractor


def run(coro):
    return asyncio.run(coro)


def message_chain(text: str, *, tool_calls: int = 0) -> tuple[dict[str, Any], ...]:
    assistant: dict[str, Any] = {"role": "assistant", "content": "response"}
    if tool_calls:
        assistant["tool_calls"] = [
            {"id": f"call-{index}", "name": "read_file"}
            for index in range(tool_calls)
        ]
    return ({"role": "user", "content": text}, assistant)


def test_should_extract_memory_waits_for_initial_token_threshold() -> None:
    state = RuntimeState()
    decision = should_extract_memory(
        message_chain("short"),
        state,
        SessionMemoryExtractionPolicy(
            minimum_message_tokens_to_init=1_000,
            minimum_tokens_between_update=10,
            tool_calls_between_updates=1,
        ),
        last_response_had_tool_calls=False,
    )

    assert decision.should_extract is False
    assert decision.reason == "below_initial_token_threshold"


def test_should_extract_memory_triggers_on_token_and_tool_growth() -> None:
    state = RuntimeState()
    messages = message_chain("x" * 300, tool_calls=3)

    decision = should_extract_memory(
        messages,
        state,
        SessionMemoryExtractionPolicy(
            minimum_message_tokens_to_init=10,
            minimum_tokens_between_update=10,
            tool_calls_between_updates=3,
        ),
        last_response_had_tool_calls=True,
    )

    assert decision.should_extract is True
    assert decision.reason == "token_and_tool_growth"
    assert decision.tool_call_count == 3


def test_should_extract_memory_triggers_after_text_response_without_tools() -> None:
    state = RuntimeState()

    decision = should_extract_memory(
        message_chain("x" * 300),
        state,
        SessionMemoryExtractionPolicy(
            minimum_message_tokens_to_init=10,
            minimum_tokens_between_update=10,
            tool_calls_between_updates=3,
        ),
        last_response_had_tool_calls=False,
    )

    assert decision.should_extract is True
    assert decision.reason == "token_growth_after_text_response"


def test_should_extract_memory_skips_when_token_delta_is_too_small() -> None:
    state = RuntimeState()
    messages = message_chain("x" * 300, tool_calls=3)
    current_tokens = estimate_messages_tokens(messages)
    state.metadata["session_memory_extraction"] = {
        "last_extracted_token_count": current_tokens - 1,
        "last_extracted_tool_call_count": 0,
    }

    decision = should_extract_memory(
        messages,
        state,
        SessionMemoryExtractionPolicy(
            minimum_message_tokens_to_init=10,
            minimum_tokens_between_update=10,
            tool_calls_between_updates=3,
        ),
        last_response_had_tool_calls=True,
    )

    assert decision.should_extract is False
    assert decision.reason == "insufficient_token_delta"


def test_count_tool_calls_reads_assistant_fields_and_tool_use_blocks() -> None:
    messages = (
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "block-call", "name": "grep"}],
            "tool_calls": [{"id": "field-call", "name": "read_file"}],
        },
    )

    assert count_tool_calls(messages) == 2


@dataclass
class FakeMemoryRunner:
    requests: list[SubagentRequest] = field(default_factory=list)

    async def run(self, request: SubagentRequest) -> SubagentResult:
        self.requests.append(request)
        return SubagentResult(
            agent_type="fork",
            session_id="child-memory",
            final_text="updated",
            metadata={"is_fork": True},
        )


def test_extraction_service_prepares_and_runs_background_job(
    tmp_path: Path,
) -> None:
    state = RuntimeState(session_id="session-memory")
    store = SessionMemoryStore(tmp_path / ".onecode" / state.session_id)
    runner = FakeMemoryRunner()
    service = SessionMemoryExtractionService(
        store,
        subagent_runner=runner,
        policy=SessionMemoryExtractionPolicy(
            minimum_message_tokens_to_init=10,
            minimum_tokens_between_update=10,
            tool_calls_between_updates=3,
        ),
    )
    messages = message_chain("x" * 300)

    job = service.prepare_extraction_job(
        messages,
        state,
        assistant_message=messages[-1],
        tool_calls=(),
    )

    assert job is not None
    assert runner.requests == []
    assert state.metadata["session_memory_extraction"]["last_status"] == "scheduled"

    run(service.run_extraction_job(job, state))

    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request.subagent_type is None
    assert request.metadata["purpose"] == "session_memory_extraction"
    assert request.metadata["allowed_memory_path"] == str(store.path.resolve())
    assert state.metadata["session_memory_extraction"]["last_status"] == "success"


def test_background_session_memory_extractor_schedules_dream_without_waiting(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        state = RuntimeState(session_id="session-memory")
        store = SessionMemoryStore(tmp_path / ".onecode" / state.session_id)
        runner = FakeMemoryRunner()
        service = SessionMemoryExtractionService(
            store,
            subagent_runner=runner,
            policy=SessionMemoryExtractionPolicy(
                minimum_message_tokens_to_init=10,
                minimum_tokens_between_update=10,
                tool_calls_between_updates=3,
            ),
        )
        manager = BackgroundTaskManager(workspace=tmp_path)
        adapter = BackgroundSessionMemoryExtractor(service, manager)
        messages = message_chain("x" * 300)

        await adapter.maybe_extract_after_model_response(
            messages,
            state,
            assistant_message=messages[-1],
            tool_calls=(),
        )

        tasks = manager.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].type == "dream"
        assert tasks[0].description == "updating session memory"
        metadata = state.metadata["session_memory_extraction"]
        assert metadata["last_status"] == "scheduled"
        assert metadata["background_task_id"] == tasks[0].id

        await adapter.wait_for_current_extraction(state)

        assert len(runner.requests) == 1
        assert state.metadata["session_memory_extraction"]["last_status"] == "success"

    run(scenario())


@dataclass
class FakeModelClient:
    responses: list[LLMResponse]
    snapshots: list[ContextSnapshot] = field(default_factory=list)

    async def stream(self, snapshot: ContextSnapshot):
        self.snapshots.append(snapshot)
        if not self.responses:
            raise AssertionError("unexpected model call")
        response = self.responses.pop(0)
        yield ModelStreamEvent.message_completed(
            assistant_message=response.assistant_message,
            final_text=response.final_text,
            tool_calls=response.tool_calls,
            usage=response.usage,
        )


def make_runner(
    tmp_path: Path,
    responses: list[LLMResponse],
) -> tuple[SubagentRunner, FakeModelClient, MessageStore, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id="parent-session",
        cwd=workspace,
        flush_interval_seconds=60,
    )
    parent_store.append_user("parent context")
    model = FakeModelClient(responses)
    runner = SubagentRunner(
        workspace=workspace,
        transcript_root=tmp_path / ".onecode",
        parent_message_store=parent_store,
        current_model_context=CurrentModelContext(
            ContextSnapshot(system_prompt="PARENT_PROMPT", messages=())
        ),
        model_client=model,
        base_descriptors=(read_file_descriptor(), edit_file_descriptor()),
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
        permission_policy=PermissionPolicy(SessionPermissionStore()),
        permission_prompter=None,
        trace_recorder=TraceRecorder.noop("parent-session"),
    )
    return runner, model, parent_store, workspace


def test_memory_extraction_child_only_exposes_edit_file_schema(
    tmp_path: Path,
) -> None:
    memory_path = tmp_path / ".onecode" / "parent-session" / "session-memory.md"
    runner, model, _parent_store, _workspace = make_runner(
        tmp_path,
        [
            LLMResponse(
                assistant_message={"role": "assistant", "content": "done"},
                final_text="done",
            )
        ],
    )

    result = run(
        runner.run(
            SubagentRequest(
                prompt="update memory",
                subagent_type=None,
                parent_session_id="parent-session",
                parent_tool_call_id="memory",
                metadata={
                    "purpose": "session_memory_extraction",
                    "allowed_memory_path": str(memory_path.resolve()),
                },
            )
        )
    )

    assert result.is_error is False
    rendered_schema = json.dumps(model.snapshots[0].tool_schemas)
    assert "edit_file" in rendered_schema
    assert "read_file" not in rendered_schema


def test_memory_extraction_child_denies_editing_non_memory_path(
    tmp_path: Path,
) -> None:
    memory_path = tmp_path / ".onecode" / "parent-session" / "session-memory.md"
    other_path = tmp_path / "workspace" / "other.md"
    bad_call = ToolCall(
        id="call-edit",
        name="edit_file",
        input={
            "file_path": str(other_path),
            "old_string": "",
            "new_string": "bad",
        },
    )
    runner, model, _parent_store, _workspace = make_runner(
        tmp_path,
        [
            LLMResponse(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(bad_call,),
            ),
            LLMResponse(
                assistant_message={"role": "assistant", "content": "reported"},
                final_text="reported",
            ),
        ],
    )

    result = run(
        runner.run(
            SubagentRequest(
                prompt="update memory",
                subagent_type=None,
                parent_session_id="parent-session",
                parent_tool_call_id="memory",
                metadata={
                    "purpose": "session_memory_extraction",
                    "allowed_memory_path": str(memory_path.resolve()),
                },
            )
        )
    )

    assert result.final_text == "reported"
    payload = json.loads(model.snapshots[1].messages[-1]["content"])
    assert payload["error"] == "permission_denied"
    assert payload["source"] == "memory_extraction_agent"
    assert not other_path.exists()
