from __future__ import annotations

from typing import Any

from core.context_engine import ContextEngine
from core.runtime_state import RuntimeState
from core.transitions import TransitionReason
from services.context.message_store import MessageStore
from services.tools.types import ToolExecutionResult


class FakePromptAssembler:
    def __init__(self) -> None:
        self.states: list[RuntimeState] = []

    def assemble(self, state: RuntimeState) -> str:
        self.states.append(state)
        return f"session={state.session_id}"


class FakeToolSchemaProvider:
    def __init__(self) -> None:
        self.states: list[RuntimeState] = []

    def tool_schemas(self, state: RuntimeState) -> list[dict[str, Any]]:
        self.states.append(state)
        return [{"name": "fake_tool", "input_schema": {"type": "object"}}]


class ReplacingPreparer:
    def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> list[dict[str, Any]]:
        return [messages[0], {"role": "user", "content": "prepared"}]


def test_context_engine_rebuilds_snapshot_from_current_messages() -> None:
    message_store = MessageStore()
    message_store.append_user("question")
    message_store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call-1",
                tool_name="fake_tool",
                content="answer",
            )
        ]
    )
    state = RuntimeState(session_id="session-1")
    state.set_transition(TransitionReason.TOOL_USE)
    prompt_assembler = FakePromptAssembler()
    tool_schema_provider = FakeToolSchemaProvider()
    engine = ContextEngine(
        message_store,
        prompt_assembler=prompt_assembler,
        tool_schema_provider=tool_schema_provider,
    )

    snapshot = engine.build_for_model(state)

    assert snapshot.system_prompt == "session=session-1"
    assert snapshot.messages == message_store.current_messages()
    assert snapshot.tool_schemas == (
        {"name": "fake_tool", "input_schema": {"type": "object"}},
    )
    assert snapshot.transition == "tool_use"
    assert prompt_assembler.states == [state]
    assert tool_schema_provider.states == [state]


def test_context_preparer_can_replace_projected_messages() -> None:
    message_store = MessageStore()
    message_store.append_user("original")
    message_store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call-1",
                tool_name="fake_tool",
                content="large",
            )
        ]
    )
    engine = ContextEngine(
        message_store,
        context_preparer=ReplacingPreparer(),
    )

    snapshot = engine.build_for_model(RuntimeState())

    assert snapshot.messages == (
        {"role": "user", "content": "original"},
        {"role": "user", "content": "prepared"},
    )
