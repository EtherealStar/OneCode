from __future__ import annotations

import asyncio
import json
from typing import Any

from core.runtime_state import RuntimeState
from services.permissions import PermissionPolicy, SessionPermissionStore
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
)


def execute_one(registry: ToolRegistry, policy: PermissionPolicy, call: ToolCall):
    async def collect():
        executor = RegistryToolExecutor(registry, permission_policy=policy)
        results = []
        async for update in executor.execute((call,), RuntimeState()):
            if update.result is not None:
                results.append(update.result)
        return results[0]

    return asyncio.run(collect())


def command_descriptor() -> ToolDescriptor:
    def handler(tool_input: dict[str, Any], runtime: ToolRuntime) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id=runtime.tool_call_id,
            tool_name="bash",
            content="ran",
        )

    def classify(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolCallClassification:
        return ToolCallClassification(
            read_only=False,
            modifies_filesystem=False,
            concurrency_safe=False,
            targets=(ToolTarget(kind="command", operation="execute", value="npm test"),),
        )

    return ToolDescriptor(
        name="bash",
        description="Run command",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
        classify_input=classify,
    )


def skill_descriptor_with_allowed_tools() -> ToolDescriptor:
    def handler(tool_input: dict[str, Any], runtime: ToolRuntime) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id=runtime.tool_call_id,
            tool_name="skill",
            content="Launching skill: test",
            metadata={"allowed_tools": ("bash",)},
        )

    return ToolDescriptor(
        name="skill",
        description="Load skill",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
        classify_input=lambda tool_input, runtime: ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=False,
        ),
    )


def test_allowed_tools_session_grant_turns_command_ask_into_allow() -> None:
    store = SessionPermissionStore()
    store.allow_tool("bash")
    policy = PermissionPolicy(store)
    registry = ToolRegistry([command_descriptor()], permission_policy=policy)

    result = execute_one(registry, policy, ToolCall(id="call-1", name="bash", input={}))

    assert result.is_error is False
    assert result.content == "ran"


def test_scoped_tool_grant_turns_command_ask_into_allow_without_session_grant() -> None:
    store = SessionPermissionStore()
    policy = PermissionPolicy(store, scoped_allowed_tools=("bash",))
    registry = ToolRegistry([command_descriptor()], permission_policy=policy)

    result = execute_one(registry, policy, ToolCall(id="call-1", name="bash", input={}))

    assert result.is_error is False
    assert result.content == "ran"
    assert store.is_tool_allowed("bash") is False


def test_skill_allowed_tools_metadata_does_not_create_session_tool_grant() -> None:
    store = SessionPermissionStore()
    policy = PermissionPolicy(store)
    registry = ToolRegistry(
        [skill_descriptor_with_allowed_tools()],
        permission_policy=policy,
    )

    result = execute_one(registry, policy, ToolCall(id="call-1", name="skill", input={}))

    assert result.is_error is False
    assert store.is_tool_allowed("bash") is False


def test_tool_deny_still_overrides_allowed_tool_grant() -> None:
    store = SessionPermissionStore()
    store.allow_tool("bash")
    store.deny_tool("bash")
    policy = PermissionPolicy(store)
    registry = ToolRegistry([command_descriptor()], permission_policy=policy)

    result = execute_one(registry, policy, ToolCall(id="call-1", name="bash", input={}))

    assert result.is_error is True
    assert json.loads(result.content)["error"] == "permission_denied"


def test_tool_deny_still_overrides_scoped_tool_grant() -> None:
    store = SessionPermissionStore()
    store.deny_tool("bash")
    policy = PermissionPolicy(store, scoped_allowed_tools=("bash",))
    registry = ToolRegistry([command_descriptor()], permission_policy=policy)

    result = execute_one(registry, policy, ToolCall(id="call-1", name="bash", input={}))

    assert result.is_error is True
    assert json.loads(result.content)["error"] == "permission_denied"
