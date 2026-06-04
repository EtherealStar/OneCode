from __future__ import annotations

import json
from typing import Any

import pytest

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.permissions import (
    PermissionPolicy,
    PermissionRequest,
    PermissionResponse,
    SessionPermissionStore,
)
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ValidationResult,
)
from tools.read_file import descriptor as read_file_descriptor


class FakePrompter:
    def __init__(self, response: PermissionResponse) -> None:
        self.response = response
        self.requests: list[PermissionRequest] = []

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        self.requests.append(request)
        return self.response


def make_descriptor(
    name: str,
    *,
    handler=None,
    validate_input=None,
) -> ToolDescriptor:
    def default_handler(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id=str(tool_input["call_id"]),
            tool_name=name,
            content=f"ok:{name}",
        )

    def classify_input(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolCallClassification:
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            targets=(),
            permission_subject=f"{name}:{tool_input['call_id']}",
        )

    return ToolDescriptor(
        name=name,
        description=f"{name} description",
        input_schema={
            "type": "object",
            "properties": {
                "call_id": {"type": "string"},
                "count": {"type": "integer", "minimum": 1},
            },
            "required": ["call_id"],
            "additionalProperties": False,
        },
        handler=handler or default_handler,
        validate_input=validate_input,
        classify_input=classify_input,
    )


def test_registry_rejects_duplicate_descriptors() -> None:
    registry = ToolRegistry([make_descriptor("read_file")])

    with pytest.raises(ValueError):
        registry.register(make_descriptor("read_file"))


def test_registry_generates_stable_openai_tool_schemas() -> None:
    registry = ToolRegistry(
        [
            make_descriptor("z_tool"),
            make_descriptor("a_tool"),
        ]
    )

    schemas = registry.tool_schemas(RuntimeState())

    assert [schema["function"]["name"] for schema in schemas] == ["a_tool", "z_tool"]
    assert schemas[0] == {
        "type": "function",
        "function": {
            "name": "a_tool",
            "description": "a_tool description",
            "parameters": make_descriptor("a_tool").input_schema,
        },
    }


def test_executor_returns_unknown_tool_error() -> None:
    executor = RegistryToolExecutor(ToolRegistry())

    results = executor.execute(
        (ToolCall(id="call-1", name="missing", input={}),),
        RuntimeState(),
    )

    assert len(results) == 1
    assert results[0].is_error is True
    assert json.loads(results[0].content)["error"] == "unknown_tool"


def test_executor_validates_required_and_unexpected_fields() -> None:
    executor = RegistryToolExecutor(ToolRegistry([make_descriptor("tool")]))

    missing = executor.execute(
        (ToolCall(id="call-1", name="tool", input={}),),
        RuntimeState(),
    )[0]
    unexpected = executor.execute(
        (
            ToolCall(
                id="call-2",
                name="tool",
                input={"call_id": "call-2", "extra": True},
            ),
        ),
        RuntimeState(),
    )[0]

    assert missing.is_error is True
    assert "Missing required input field" in json.loads(missing.content)["message"]
    assert unexpected.is_error is True
    assert "Unexpected input field" in json.loads(unexpected.content)["message"]


def test_executor_validates_integer_minimum_and_custom_validator() -> None:
    def validate(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ValidationResult:
        return ValidationResult.failure("custom failure")

    executor = RegistryToolExecutor(
        ToolRegistry([make_descriptor("tool", validate_input=validate)])
    )

    bad_type = executor.execute(
        (
            ToolCall(
                id="call-1",
                name="tool",
                input={"call_id": "call-1", "count": 0},
            ),
        ),
        RuntimeState(),
    )[0]
    custom_failure = executor.execute(
        (
            ToolCall(
                id="call-2",
                name="tool",
                input={"call_id": "call-2", "count": 1},
            ),
        ),
        RuntimeState(),
    )[0]

    assert bad_type.is_error is True
    assert "greater than or equal to 1" in json.loads(bad_type.content)["message"]
    assert custom_failure.is_error is True
    assert json.loads(custom_failure.content)["message"] == "custom failure"


def test_executor_converts_classification_exceptions_to_tool_errors() -> None:
    def classify_input(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolCallClassification:
        raise RuntimeError("cannot classify")

    descriptor = make_descriptor("tool")
    descriptor = ToolDescriptor(
        name=descriptor.name,
        description=descriptor.description,
        input_schema=descriptor.input_schema,
        handler=descriptor.handler,
        prompt=descriptor.prompt,
        search_hint=descriptor.search_hint,
        validate_input=descriptor.validate_input,
        classify_input=classify_input,
    )
    executor = RegistryToolExecutor(ToolRegistry([descriptor]))

    result = executor.execute(
        (ToolCall(id="call-1", name="tool", input={"call_id": "call-1"}),),
        RuntimeState(),
    )[0]

    assert result.is_error is True
    assert json.loads(result.content) == {
        "error": "tool_classification_error",
        "message": "cannot classify",
    }


def test_executor_applies_result_policy_from_classification() -> None:
    def handler(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="tool",
            content="abcdef",
        )

    def classify_input(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolCallClassification:
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            targets=(),
            result_policy=ToolResultPolicy(
                max_result_size_chars=3,
                persist_when_exceeded=False,
                preview_chars=2,
            ),
        )

    descriptor = make_descriptor("tool", handler=handler)
    descriptor = ToolDescriptor(
        name=descriptor.name,
        description=descriptor.description,
        input_schema=descriptor.input_schema,
        handler=descriptor.handler,
        validate_input=descriptor.validate_input,
        classify_input=classify_input,
    )
    executor = RegistryToolExecutor(ToolRegistry([descriptor]))

    result = executor.execute(
        (ToolCall(id="call-1", name="tool", input={"call_id": "call-1"}),),
        RuntimeState(),
    )[0]

    payload = json.loads(result.content)
    assert result.is_error is False
    assert payload["result_truncated"] is True
    assert payload["preview"] == "ab"
    assert result.metadata["original_size_chars"] == 6


def test_executor_runs_tools_serially_in_provider_order() -> None:
    order: list[str] = []

    def handler(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        call_id = str(tool_input["call_id"])
        order.append(call_id)
        return ToolExecutionResult(
            tool_call_id=call_id,
            tool_name="tool",
            content=call_id,
        )

    executor = RegistryToolExecutor(
        ToolRegistry([make_descriptor("tool", handler=handler)])
    )

    results = executor.execute(
        (
            ToolCall(id="call-1", name="tool", input={"call_id": "call-1"}),
            ToolCall(id="call-2", name="tool", input={"call_id": "call-2"}),
        ),
        RuntimeState(),
    )

    assert order == ["call-1", "call-2"]
    assert [result.content for result in results] == ["call-1", "call-2"]


def test_executor_converts_handler_exceptions_to_tool_errors() -> None:
    def handler(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        raise RuntimeError("boom")

    executor = RegistryToolExecutor(
        ToolRegistry([make_descriptor("tool", handler=handler)])
    )

    result = executor.execute(
        (ToolCall(id="call-1", name="tool", input={"call_id": "call-1"}),),
        RuntimeState(),
    )[0]

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload == {"error": "tool_execution_error", "message": "boom"}


def test_executor_prompts_and_runs_external_read_when_allowed(
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    store = SessionPermissionStore()
    policy = PermissionPolicy(store)
    prompter = FakePrompter(PermissionResponse(action="allow", scope="once"))
    registry = ToolRegistry([read_file_descriptor()], permission_policy=policy)
    executor = RegistryToolExecutor(
        registry,
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
        permission_policy=policy,
        permission_prompter=prompter,
    )
    state = RuntimeState()

    result = executor.execute(
        (
            ToolCall(
                id="call-1",
                name="read_file",
                input={"file_path": str(outside)},
            ),
        ),
        state,
    )[0]

    assert result.is_error is False
    assert result.content == "1\toutside"
    assert len(prompter.requests) == 1
    assert prompter.requests[0].decision.action == "ask"
    assert str(outside.resolve()) in state.metadata["files_read"]


def test_executor_returns_permission_denied_when_user_denies(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    policy = PermissionPolicy(SessionPermissionStore())
    prompter = FakePrompter(PermissionResponse(action="deny"))
    registry = ToolRegistry([read_file_descriptor()], permission_policy=policy)
    executor = RegistryToolExecutor(
        registry,
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
        permission_policy=policy,
        permission_prompter=prompter,
    )
    state = RuntimeState()

    result = executor.execute(
        (
            ToolCall(
                id="call-1",
                name="read_file",
                input={"file_path": str(outside)},
            ),
        ),
        state,
    )[0]

    payload = json.loads(result.content)
    assert result.is_error is True
    assert payload["error"] == "permission_denied"
    assert state.metadata.get("files_read") is None


def test_session_allow_skips_later_prompt_for_same_directory(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    first = outside / "first.txt"
    second = outside / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    store = SessionPermissionStore()
    policy = PermissionPolicy(store)
    prompter = FakePrompter(PermissionResponse(action="allow", scope="session"))
    registry = ToolRegistry([read_file_descriptor()], permission_policy=policy)
    executor = RegistryToolExecutor(
        registry,
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
        permission_policy=policy,
        permission_prompter=prompter,
    )
    state = RuntimeState()

    first_result = executor.execute(
        (
            ToolCall(
                id="call-1",
                name="read_file",
                input={"file_path": str(first)},
            ),
        ),
        state,
    )[0]
    second_result = executor.execute(
        (
            ToolCall(
                id="call-2",
                name="read_file",
                input={"file_path": str(second)},
            ),
        ),
        state,
    )[0]

    assert first_result.is_error is False
    assert second_result.is_error is False
    assert len(prompter.requests) == 1


def test_permission_policy_hides_schema_prompt_and_denies_old_tool_call() -> None:
    state = RuntimeState()
    store = SessionPermissionStore()
    store.deny_tool("tool")
    policy = PermissionPolicy(store)
    descriptor = ToolDescriptor(
        name="tool",
        description="tool description",
        input_schema={
            "type": "object",
            "properties": {"call_id": {"type": "string"}},
            "required": ["call_id"],
            "additionalProperties": False,
        },
        handler=lambda tool_input, runtime: ToolExecutionResult(
            tool_call_id="",
            tool_name="tool",
            content="should not run",
        ),
        prompt="# Tool: tool\nblocked",
        classify_input=lambda tool_input, runtime: ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
        ),
    )
    registry = ToolRegistry([descriptor], permission_policy=policy)
    executor = RegistryToolExecutor(registry, permission_policy=policy)

    result = executor.execute(
        (ToolCall(id="call-1", name="tool", input={"call_id": "call-1"}),),
        state,
    )[0]

    assert registry.tool_schemas(state) == ()
    assert registry.tool_prompt_sections(state) == ()
    assert json.loads(result.content)["error"] == "permission_denied"
