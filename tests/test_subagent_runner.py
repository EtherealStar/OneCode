from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.guard import SandboxBoundary, SandboxGuard
from services.model.stream import ModelStreamEvent
from services.model.types import LLMResponse, ModelUsage
from services.observability import TraceRecorder
from services.permissions import PermissionPolicy, SessionPermissionStore
from services.context.current_model_context import CurrentModelContext
from services.subagents.runner import SubagentRunner
from services.subagents.types import SubagentRequest
from services.tasks import resolve_task_list_id
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
)


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


class FakePrompter:
    def __init__(self) -> None:
        self.requests = []

    async def request_permission(self, request):
        self.requests.append(request)
        from services.permissions import PermissionResponse

        return PermissionResponse(action="allow", scope="session")


def run(coro):
    return asyncio.run(coro)


def assistant(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def make_runner(
    tmp_path: Path,
    responses: list[LLMResponse],
    *,
    parent_store: MessageStore | None = None,
    current_context: CurrentModelContext | None = None,
    base_descriptors: tuple[ToolDescriptor, ...] = (),
    permission_policy: PermissionPolicy | None = None,
    permission_prompter=None,
) -> tuple[SubagentRunner, FakeModelClient, MessageStore, PermissionPolicy]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    parent_state = RuntimeState(session_id="parent-session")
    parent_store = parent_store or MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=parent_state.session_id,
        cwd=workspace,
        flush_interval_seconds=60,
    )
    policy = permission_policy or PermissionPolicy(SessionPermissionStore())
    model = FakeModelClient(responses)
    runner = SubagentRunner(
        workspace=workspace,
        transcript_root=tmp_path / ".onecode",
        parent_message_store=parent_store,
        current_model_context=current_context or CurrentModelContext(),
        model_client=model,
        base_descriptors=base_descriptors,
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
        permission_policy=policy,
        permission_prompter=permission_prompter,
        trace_recorder=TraceRecorder.noop("parent-session"),
    )
    return runner, model, parent_store, policy


def test_general_purpose_subagent_uses_clean_messages(tmp_path: Path) -> None:
    runner, model, parent_store, _policy = make_runner(
        tmp_path,
        [
            LLMResponse(
                assistant_message=assistant("child done"),
                final_text="child done",
                usage=ModelUsage(input_tokens=1, output_tokens=2),
            )
        ],
    )
    parent_store.append_user("parent secret")

    result = run(
        runner.run(
            SubagentRequest(
                prompt="search parser",
                subagent_type="general-purpose",
                parent_session_id="parent-session",
                parent_tool_call_id="call-agent",
            )
        )
    )

    assert result.final_text == "child done"
    assert result.usage is not None
    assert result.usage.output_tokens == 2
    assert model.snapshots[0].messages == (
        {"role": "user", "content": "search parser"},
    )
    assert parent_store.current_messages() == (
        {"role": "user", "content": "parent secret"},
    )


def test_fork_subagent_inherits_parent_prompt_and_messages(tmp_path: Path) -> None:
    parent_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id="parent-session",
        flush_interval_seconds=60,
    )
    parent_store.append_user("parent context")
    parent_store.append_assistant(
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call-agent", "name": "agent"}],
        }
    )
    current_context = CurrentModelContext(
        ContextSnapshot(system_prompt="EXACT_PARENT_PROMPT", messages=())
    )
    runner, model, _parent_store, _policy = make_runner(
        tmp_path,
        [LLMResponse(assistant_message=assistant("fork done"), final_text="fork done")],
        parent_store=parent_store,
        current_context=current_context,
    )

    result = run(
        runner.run(
            SubagentRequest(
                prompt="continue from here",
                subagent_type=None,
                parent_session_id="parent-session",
                parent_tool_call_id="call-agent",
            )
        )
    )

    assert result.metadata["is_fork"] is True
    assert model.snapshots[0].system_prompt == "EXACT_PARENT_PROMPT"
    assert model.snapshots[0].messages[0]["content"] == "parent context"
    assert model.snapshots[0].messages[2]["content"] == (
        "Fork started - processing in child agent"
    )
    assert "continue from here" in model.snapshots[0].messages[-1]["content"]
    assert parent_store.current_messages()[-1]["role"] == "assistant"


def test_child_registry_hides_agent_even_when_base_descriptors_include_it(
    tmp_path: Path,
) -> None:
    call = ToolCall(id="call-nested", name="agent", input={"prompt": "nested"})
    runner, model, _parent_store, _policy = make_runner(
        tmp_path,
        [
            LLMResponse(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(call,),
            ),
            LLMResponse(assistant_message=assistant("done"), final_text="done"),
        ],
        base_descriptors=(dummy_descriptor("agent"),),
    )

    result = run(
        runner.run(
            SubagentRequest(
                prompt="try nested",
                subagent_type="general-purpose",
                parent_session_id="parent-session",
                parent_tool_call_id="call-agent",
            )
        )
    )

    assert result.final_text == "done"
    assert model.snapshots[0].tool_schemas == ()
    tool_result = model.snapshots[1].messages[-1]
    assert tool_result["role"] == "tool_result"
    assert json.loads(tool_result["content"])["error"] == "unknown_tool"


def test_read_only_subagent_denies_state_changing_tool_calls(tmp_path: Path) -> None:
    ran = False

    def handler(tool_input: dict[str, Any], runtime: ToolRuntime) -> ToolExecutionResult:
        nonlocal ran
        ran = True
        return ToolExecutionResult(tool_call_id="", tool_name="mutate", content="ran")

    call = ToolCall(id="call-mutate", name="mutate", input={"call_id": "call-mutate"})
    runner, model, _parent_store, _policy = make_runner(
        tmp_path,
        [
            LLMResponse(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(call,),
            ),
            LLMResponse(assistant_message=assistant("after deny"), final_text="after deny"),
        ],
        base_descriptors=(
            dummy_descriptor(
                "mutate",
                handler=handler,
                classification=ToolCallClassification(
                    read_only=False,
                    modifies_filesystem=True,
                    concurrency_safe=False,
                ),
            ),
        ),
    )

    result = run(
        runner.run(
            SubagentRequest(
                prompt="plan only",
                subagent_type="Plan",
                parent_session_id="parent-session",
                parent_tool_call_id="call-agent",
            )
        )
    )

    assert result.final_text == "after deny"
    assert ran is False
    payload = json.loads(model.snapshots[1].messages[-1]["content"])
    assert payload["error"] == "permission_denied"
    assert payload["source"] == "read_only_agent"


def test_child_permission_ask_bubbles_to_shared_prompter_and_session_store(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("external", encoding="utf-8")
    store = SessionPermissionStore()
    policy = PermissionPolicy(store)
    prompter = FakePrompter()
    read_call = ToolCall(
        id="call-read",
        name="read_external",
        input={"path": str(outside)},
    )

    runner, model, _parent_store, _policy = make_runner(
        tmp_path,
        [
            LLMResponse(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(read_call,),
            ),
            LLMResponse(assistant_message=assistant("reported"), final_text="reported"),
        ],
        base_descriptors=(external_read_descriptor(),),
        permission_policy=policy,
        permission_prompter=prompter,
    )

    result = run(
        runner.run(
            SubagentRequest(
                prompt="read external",
                subagent_type="Explore",
                parent_session_id="parent-session",
                parent_tool_call_id="call-agent",
            )
        )
    )

    assert result.final_text == "reported"
    assert len(prompter.requests) == 1
    assert store.is_allowed(
        tool_name="read_external",
        operation="read",
        target=outside,
    )
    assert model.snapshots[1].messages[-1]["is_error"] is False


def test_child_runtime_inherits_task_list_id_metadata(tmp_path: Path) -> None:
    call = ToolCall(id="call-probe", name="task_list_probe", input={})
    runner, model, _parent_store, _policy = make_runner(
        tmp_path,
        [
            LLMResponse(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(call,),
            ),
            LLMResponse(assistant_message=assistant("done"), final_text="done"),
        ],
        base_descriptors=(task_list_probe_descriptor(),),
    )

    result = run(
        runner.run(
            SubagentRequest(
                prompt="check task list",
                subagent_type="general-purpose",
                parent_session_id="parent-session",
                parent_tool_call_id="call-agent",
                metadata={"task_list_id": "shared-demo"},
            )
        )
    )

    assert result.final_text == "done"
    tool_result = model.snapshots[1].messages[-1]
    assert tool_result["role"] == "tool_result"
    assert tool_result["content"] == "shared-demo"


def dummy_descriptor(
    name: str,
    *,
    handler=None,
    classification: ToolCallClassification | None = None,
) -> ToolDescriptor:
    def default_handler(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(tool_call_id="", tool_name=name, content="ok")

    def classify(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolCallClassification:
        return classification or ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
        )

    return ToolDescriptor(
        name=name,
        description=f"{name} description",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "call_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        handler=handler or default_handler,
        classify_input=classify,
    )


def external_read_descriptor() -> ToolDescriptor:
    def classify(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolCallClassification:
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            targets=(ToolTarget(kind="file", operation="read", value=tool_input["path"]),),
        )

    def handler(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="read_external",
            content=Path(tool_input["path"]).read_text(encoding="utf-8"),
        )

    return ToolDescriptor(
        name="read_external",
        description="read external",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=handler,
        classify_input=classify,
    )


def task_list_probe_descriptor() -> ToolDescriptor:
    def classify(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolCallClassification:
        task_list_id = resolve_task_list_id(runtime.state)
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            targets=(ToolTarget(kind="session_state", operation="task_read", value=task_list_id),),
        )

    def handler(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="task_list_probe",
            content=resolve_task_list_id(runtime.state),
        )

    return ToolDescriptor(
        name="task_list_probe",
        description="probe task list id",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=handler,
        classify_input=classify,
    )
