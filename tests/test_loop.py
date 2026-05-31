from __future__ import annotations

from pathlib import Path

from onecode.compaction import CompactionConfig, Compactor
from onecode.config import AgentConfig, ModelProfile
from onecode.errors import ContextLimitError, RateLimitError
from onecode.hooks import HookRegistry, register_default_hooks
from onecode.loop import AgentLoop
from onecode.model_client import LLMResponse, ToolCall
from onecode.tools import build_builtin_registry


class FakeModelClient:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []
        self.summary_calls = 0

    def send(self, *, system, messages, tools, max_output_tokens):
        self.calls.append(
            {
                "system": system,
                "messages": list(messages),
                "tools": tools,
                "max_output_tokens": max_output_tokens,
            }
        )
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def summarize(self, *, messages, focus=None):
        self.summary_calls += 1
        return f"summary for {focus}"


def make_agent(tmp_path: Path, client: FakeModelClient, *, register_hooks: bool = True) -> AgentLoop:
    profile = ModelProfile(
        name="fake",
        context_window=100,
        default_max_output_tokens=10,
        escalated_max_output_tokens=40,
        reserved_output_tokens=10,
    )
    config = AgentConfig(cwd=tmp_path, model="fake", model_profile=profile)
    hooks = HookRegistry()
    if register_hooks:
        register_default_hooks(hooks, verbose=False)
    return AgentLoop(
        config=config,
        model_client=client,
        tool_registry=build_builtin_registry(tmp_path),
        hooks=hooks,
        compactor=Compactor(
            CompactionConfig(
                state_dir=tmp_path / ".onecode",
                auto_compact_ratio=0.80,
                sliding_window_max_messages=20,
            )
        ),
        sleep=lambda _: None,
    )


def text_response(text: str, usage: dict | None = None) -> LLMResponse:
    return LLMResponse(
        assistant_message={"role": "assistant", "content": [{"type": "text", "text": text}]},
        final_text=text,
        usage=usage or {"input_tokens": 10, "output_tokens": 2},
    )


def tool_response(call: ToolCall) -> LLMResponse:
    return LLMResponse(
        assistant_message={
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
            ],
        },
        tool_calls=[call],
        stop_reason="tool_use",
        usage={"input_tokens": 10, "output_tokens": 2},
    )


def test_no_tool_response_stops(tmp_path):
    client = FakeModelClient([text_response("done")])
    agent = make_agent(tmp_path, client)

    assert agent.run("hello") == "done"
    assert len(client.calls) == 1


def test_tool_call_executes_and_continues(tmp_path):
    (tmp_path / "README.md").write_text("hello from file", encoding="utf-8")
    client = FakeModelClient(
        [
            tool_response(ToolCall(id="t1", name="read_file", input={"path": "README.md"})),
            text_response("read it"),
        ]
    )
    agent = make_agent(tmp_path, client)

    assert agent.run("read README") == "read it"
    tool_result_message = agent.state.messages[-2]
    assert tool_result_message["content"][0]["content"] == "hello from file"


def test_pre_tool_hook_blocks_dangerous_bash(tmp_path):
    client = FakeModelClient(
        [
            tool_response(ToolCall(id="t1", name="bash", input={"command": "rm -rf /"})),
            text_response("blocked"),
        ]
    )
    agent = make_agent(tmp_path, client)

    assert agent.run("delete everything") == "blocked"
    tool_result = agent.state.messages[-2]["content"][0]
    assert tool_result["is_error"] is True
    assert "Permission denied" in tool_result["content"]


def test_rate_limit_retries_same_request(tmp_path):
    client = FakeModelClient([RateLimitError(retry_after=0), text_response("ok")])
    agent = make_agent(tmp_path, client)

    assert agent.run("hello") == "ok"
    assert len(client.calls) == 2
    assert len(agent.state.messages) == 2


def test_context_limit_triggers_reactive_compact(tmp_path):
    client = FakeModelClient([ContextLimitError("too long"), text_response("ok")])
    agent = make_agent(tmp_path, client)

    assert agent.run("large task") == "ok"
    assert client.summary_calls == 1
    assert agent.state.messages[0]["content"].startswith("[Compacted conversation]")


def test_max_tokens_escalates_before_appending_partial(tmp_path):
    interrupted = LLMResponse(
        assistant_message={"role": "assistant", "content": [{"type": "text", "text": "partial"}]},
        final_text="partial",
        stop_reason="max_tokens",
        usage={"input_tokens": 10, "output_tokens": 10},
        output_interrupted=True,
    )
    client = FakeModelClient([interrupted, text_response("complete")])
    agent = make_agent(tmp_path, client)

    assert agent.run("write a lot") == "complete"
    assert client.calls[0]["max_output_tokens"] == 10
    assert client.calls[1]["max_output_tokens"] == 40
    assert all("partial" not in str(message) for message in agent.state.messages)


def test_usage_above_threshold_triggers_auto_compact(tmp_path):
    client = FakeModelClient([text_response("ok")])
    agent = make_agent(tmp_path, client)
    agent.state.messages.append({"role": "user", "content": "old"})
    agent.state.usage.occupied_ratio = 0.9

    assert agent.run("continue") == "ok"
    assert client.summary_calls == 1
    assert agent.state.messages[0]["content"].startswith("[Compacted conversation]")
