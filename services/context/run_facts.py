"""Provider-neutral running facts for the current foreground turn.

The agent loop keeps these facts live while a turn is in flight so an
interrupt can be finalized from what really happened, not from the last
UI buffer or the last yielded event. The store consumes a frozen copy to
rewrite the transcript.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from services.tools.types import ToolExecutionResult


@dataclass(frozen=True)
class ToolCallFact:
    tool_call_id: str
    tool_name: str


@dataclass(frozen=True)
class InterruptedRunFacts:
    """Frozen truth about one interrupted foreground turn."""

    session_id: str
    user_prompt_uuid: str | None = None
    assistant_call_id: str | None = None
    model_turn_index: int | None = None
    #: 当前模型调用已经向用户输出的未定稿文字（可能尚未写入存储）。
    assistant_text: str = ""
    #: 已经完成但可能尚未追加到存储的 assistant message。
    assistant_message: dict[str, Any] | None = None
    #: 当前 assistant message 已写入存储时的记录 UUID（存储确认）。
    assistant_record_uuid: str | None = None
    #: 已声明的工具调用，跨本 turn 的全部模型调用累计。
    tool_calls: tuple[ToolCallFact, ...] = ()
    #: 已收到真实结果（含失败和权限拒绝）的工具结果，可能尚未批量落盘。
    results: tuple[ToolExecutionResult, ...] = ()
    status: str = "running"

    @property
    def result_ids(self) -> frozenset[str]:
        return frozenset(result.tool_call_id for result in self.results)

    @property
    def declared_ids(self) -> frozenset[str]:
        return frozenset(call.tool_call_id for call in self.tool_calls)


@dataclass
class RunFactsAccumulator:
    """Mutable accumulator the loop mutates as events arrive."""

    session_id: str
    user_prompt_uuid: str | None = None
    assistant_call_id: str | None = None
    model_turn_index: int | None = None
    assistant_text: str = ""
    assistant_message: dict[str, Any] | None = None
    assistant_record_uuid: str | None = None
    status: str = "running"
    _calls: dict[str, str] = field(default_factory=dict)
    _results: dict[str, ToolExecutionResult] = field(default_factory=dict)

    def begin_model_call(
        self,
        assistant_call_id: str | None,
        model_turn_index: int | None,
    ) -> None:
        self.assistant_call_id = assistant_call_id
        self.model_turn_index = model_turn_index
        self.assistant_text = ""
        self.assistant_message = None
        self.assistant_record_uuid = None

    def add_text(self, text: str) -> None:
        if text:
            self.assistant_text += text

    def set_assistant_message(self, message: dict[str, Any] | None) -> None:
        self.assistant_message = deepcopy(message) if message is not None else None

    def declare_many(self, tool_calls: Any) -> None:
        for tool_call in tool_calls or ():
            call_id = getattr(tool_call, "id", None)
            name = getattr(tool_call, "name", None)
            if isinstance(call_id, str) and call_id:
                self._calls.setdefault(
                    call_id,
                    name if isinstance(name, str) else "unknown_tool",
                )

    def declare(self, tool_call_id: str, tool_name: str) -> None:
        if tool_call_id:
            self._calls.setdefault(tool_call_id, tool_name or "unknown_tool")

    def add_result(self, result: ToolExecutionResult) -> None:
        self._results[result.tool_call_id] = result

    def note_assistant_record(self, record_uuid: str | None) -> None:
        self.assistant_record_uuid = record_uuid

    def freeze(self, *, status: str | None = None) -> InterruptedRunFacts:
        return InterruptedRunFacts(
            session_id=self.session_id,
            user_prompt_uuid=self.user_prompt_uuid,
            assistant_call_id=self.assistant_call_id,
            model_turn_index=self.model_turn_index,
            assistant_text=self.assistant_text,
            assistant_message=(
                deepcopy(self.assistant_message)
                if self.assistant_message is not None
                else None
            ),
            assistant_record_uuid=self.assistant_record_uuid,
            tool_calls=tuple(
                ToolCallFact(tool_call_id=call_id, tool_name=name)
                for call_id, name in self._calls.items()
            ),
            results=tuple(self._results.values()),
            status=status or self.status,
        )
