from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.filesystem.onecode_paths import sessions_dir
from infrastructure.providers.chat_completions import OpenAICompatibleChatCompletionsClient
from infrastructure.providers.catalog import get_provider_definition
from sdk_test_support import (
    SSE_HEADERS,
    async_sdk,
    error_response,
    text_chunk,
    tool_call_chunk,
)
from services.context.message_store import MessageStore
from services.guard import SandboxBoundary, SandboxGuard
from services.model.retry import ModelRetryRunner, RetryPolicy
from services.observability import JsonlTraceSink, TraceRecorder
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor


class SequencedSdkHandler:
    """为每个请求返回一段 SSE 流或一个预设响应，并记录出站请求体。"""

    def __init__(self, turns: list[Any]) -> None:
        self._turns = list(turns)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._turns:
            raise AssertionError("Unexpected provider call")
        turn = self._turns.pop(0)
        if isinstance(turn, httpx.Response):
            return turn
        chunks = turn
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        return httpx.Response(
            200,
            content=body + "data: [DONE]\n\n",
            headers=SSE_HEADERS,
        )

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def tool_call_turn(call_id: str, name: str, arguments: str) -> list[dict[str, Any]]:
    return [
        tool_call_chunk(
            0,
            call_id=call_id,
            name=name,
            arguments=arguments,
            finish_reason="tool_calls",
        )
    ]


def text_turn(content: str) -> list[dict[str, Any]]:
    return [text_chunk(content, finish_reason="stop")]


def make_config() -> ResolvedProviderConfig:
    provider = get_provider_definition("openai")
    return ResolvedProviderConfig(
        provider,
        provider.id,
        provider.display_name,
        "https://api.openai.com/v1",
        "gpt-test",
        "secret",
        models_path=provider.models_path,
        chat_completions_path=provider.chat_completions_path,
    )


def make_loop(
    workspace: Path,
    handler: SequencedSdkHandler,
    *,
    trace_recorder: TraceRecorder | None = None,
    model_retry_runner: ModelRetryRunner | None = None,
) -> tuple[AgentLoop, ToolRegistry, Any]:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=workspace / ".onecode",
        session_id=state.session_id,
        cwd=workspace,
        flush_interval_seconds=60,
    )
    registry = ToolRegistry([read_file_descriptor(), edit_file_descriptor()])
    context_engine = ContextEngine(message_store, tool_schema_provider=registry)
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    config = make_config()
    sdk = async_sdk(config, handler)
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=context_engine,
        model_client=OpenAICompatibleChatCompletionsClient(config, sdk_client=sdk),
        tool_executor=RegistryToolExecutor(registry, guard=guard),
        trace_recorder=trace_recorder,
        model_retry_runner=model_retry_runner,
    )
    return loop, registry, sdk


def run_collect(loop: AgentLoop, prompt: str, sdk: Any | None = None) -> list[Any]:
    async def run() -> list[Any]:
        events: list[Any] = []
        async for event in loop.stream(prompt):
            events.append(event)
        return events

    try:
        return asyncio.run(run())
    finally:
        if sdk is not None:
            asyncio.run(sdk.close())


def run_to_final_text(loop: AgentLoop, prompt: str, sdk: Any | None = None) -> str:
    async def run() -> str:
        final_text = ""
        async for event in loop.stream(prompt):
            if event.type == "completed":
                final_text = event.text
        return final_text

    try:
        return asyncio.run(run())
    finally:
        if sdk is not None:
            asyncio.run(sdk.close())


def test_provider_loop_can_read_file_with_registry_executor(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    handler = SequencedSdkHandler(
        [
            tool_call_turn("call_read", "read_file", '{"file_path":"a.txt"}'),
            text_turn("read complete"),
        ]
    )
    loop, registry, sdk = make_loop(workspace, handler)

    result = run_to_final_text(loop, "inspect a.txt", sdk)

    assert result == "read complete"
    assert len(handler.bodies) == 2
    first_payload = handler.bodies[0]
    assert first_payload["tools"] == list(registry.tool_schemas(loop.state))
    second_messages = handler.bodies[1]["messages"]
    assert second_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call_read",
        "content": "1\tone\n2\ttwo",
    }


def test_provider_loop_can_read_then_edit_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("hello old world", encoding="utf-8")
    handler = SequencedSdkHandler(
        [
            tool_call_turn("call_read", "read_file", '{"file_path":"a.txt"}'),
            tool_call_turn(
                "call_edit",
                "edit_file",
                (
                    '{"file_path":"a.txt","old_string":"old",'
                    '"new_string":"new"}'
                ),
            ),
            text_turn("edit complete"),
        ]
    )
    loop, _registry, sdk = make_loop(workspace, handler)

    result = run_to_final_text(loop, "change a.txt", sdk)

    assert result == "edit complete"
    assert target.read_text(encoding="utf-8") == "hello new world"
    assert len(handler.bodies) == 3
    edit_payload_messages = handler.bodies[2]["messages"]
    assert edit_payload_messages[-1]["role"] == "tool"
    assert edit_payload_messages[-1]["tool_call_id"] == "call_edit"
    assert "replacement(s)" in edit_payload_messages[-1]["content"]


def test_provider_loop_streams_answer_and_pairs_transcript(tmp_path: Path) -> None:
    """端到端协议演练：工具调用、执行、结果回填与逐段文本流。

    第一次回复流式声明 `read_file` 工具调用，执行器真正读取文件，第二次
    回复读到工具结果后分两段输出答案。验证 UI 收到逐段 delta、transcript
    中 assistant 工具声明与其 tool_result 通过稳定归属配对。
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    handler = SequencedSdkHandler(
        [
            tool_call_turn("call_read", "read_file", '{"file_path":"a.txt"}'),
            [text_chunk("read "), text_chunk("complete", finish_reason="stop")],
        ]
    )
    loop, _registry, sdk = make_loop(workspace, handler)

    events = run_collect(loop, "inspect a.txt", sdk)

    # UI 逐段收到最终答案，而不是只在结束时一次性出现。
    assert [event.text for event in events if event.type == "assistant_delta"] == [
        "read ",
        "complete",
    ]
    assert [event.text for event in events if event.type == "completed"] == [
        "read complete"
    ]
    # 工具确实被执行，其结果作为事件到达 UI。
    tool_results = [event for event in events if event.type == "tool_result"]
    assert [event.result.tool_call_id for event in tool_results] == ["call_read"]
    assert "one" in tool_results[0].result.content

    # transcript 中 assistant 工具声明与 tool_result 通过相同 tool_call_id
    # 和相同稳定归属配对。
    records = loop.message_store.transcript_store.load_messages()
    assistant = next(
        record for record in records if record.message.get("role") == "assistant"
    )
    tool_result = next(
        record for record in records if record.message.get("role") == "tool_result"
    )
    declared_ids = [
        call["id"] for call in assistant.message.get("tool_calls", [])
    ]
    assert declared_ids == ["call_read"]
    assert tool_result.message["tool_call_id"] == "call_read"
    assert tool_result.assistant_call_id == assistant.assistant_call_id
    assert tool_result.model_turn_index == assistant.model_turn_index


def test_provider_loop_retries_rate_limit_once_with_trace(tmp_path: Path) -> None:
    """429 一次后成功：只发两次请求，trace 中恰好一次 `model_retry`。"""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    handler = SequencedSdkHandler(
        [
            error_response(429, "slow down"),
            text_turn("recovered"),
        ]
    )
    sink = JsonlTraceSink(
        sessions_dir(tmp_path),
        "retry-trace",
        flush_interval_seconds=60,
    )
    recorder = TraceRecorder(
        session_id="retry-trace",
        workspace=tmp_path,
        sink=sink,
    )
    runner = ModelRetryRunner(
        policy=RetryPolicy(max_retries=2, jitter_ratio=0),
        sleep=_noop_sleep,
        trace_recorder=recorder,
    )
    loop, _registry, sdk = make_loop(
        workspace,
        handler,
        trace_recorder=recorder,
        model_retry_runner=runner,
    )

    events = run_collect(loop, "hello", sdk)

    assert len(handler.requests) == 2
    retry_transitions = [
        event
        for event in events
        if event.type == "transition" and event.transition == "rate_limit_retry"
    ]
    assert len(retry_transitions) == 1
    assert retry_transitions[0].metadata["attempt"] == 1
    assert [event.text for event in events if event.type == "completed"] == ["recovered"]

    retry_records = [
        record
        for record in recorder.recent_records(limit=100)
        if record.get("name") == "model_retry"
    ]
    assert len(retry_records) == 1
    assert retry_records[0]["attributes"]["status_code"] == 429
    assert retry_records[0]["attributes"]["partial_output_visible"] is False


async def _noop_sleep(_seconds: float) -> None:
    return None
