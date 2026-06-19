"""Runtime event types emitted by the async agent loop.

事件 metadata 约定 (execplan §M1):

所有归属于某次模型调用或其工具执行的事件,在 ``AgentEvent.metadata``
中必须携带 ``model_turn_index`` 和 ``assistant_call_id``。这两个字段是
``ui.cli.terminal`` checkpoint 渲染的事实来源,不是 provider 私有协议。

- ``model_turn_index``: 整个 session 内从 1 开始的递增整数,标识事件
  归属的第几次模型调用。多次模型调用共享同一 ``turn_count`` 周期内
  的 ``model_turn_index`` 严格递增。
- ``assistant_call_id``: 当前 session 内稳定唯一的字符串,由
  ``session_id``、``turn_count`` 和 ``model_turn_index`` 组合生成,
  在 assistant 文本、工具声明、工具结果和工具 progress 之间共享。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from services.tools.types import ToolExecutionResult


AgentEventType = Literal[
    "interaction_started",
    "assistant_delta",
    "assistant_message_completed",
    "tool_call_delta",
    "tool_call_ready",
    "tool_started",
    "tool_progress",
    "tool_result",
    "transition",
    "completed",
    "error",
]


@dataclass(frozen=True)
class AgentEvent:
    type: AgentEventType
    text: str = ""
    result: ToolExecutionResult | None = None
    transition: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


#: 哪些 AgentEvent 类型必须携带 ``model_turn_index`` / ``assistant_call_id``。
#: Reducer 在收到不在这两个集合内的事件时,既不要求稳定 ID 也不会报
#: 错;只有列表中的事件缺失 ID 才会被诊断为实现错误 (recoverable)。
_ATTRIBUTED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "assistant_delta",
        "tool_call_delta",
        "tool_call_ready",
        "assistant_message_completed",
        "tool_started",
        "tool_progress",
        "tool_result",
    }
)


def event_requires_attribution(event: "AgentEvent") -> bool:
    """Return True when the event must carry stable attribution IDs."""

    return event.type in _ATTRIBUTED_EVENT_TYPES


def mint_assistant_call_id(session_id: str, turn_count: int, model_turn_index: int) -> str:
    """Generate a stable, session-unique id for one model invocation.

    The id is a short human-readable string composed of the session id's
    first 8 hex chars, the runtime ``turn_count`` and the
    ``model_turn_index`` within that turn. It is stable across retries
    and re-runs of the same model call, so checkpoint renderers can
    match assistant text, tool declarations, and tool results to the
    same invocation without depending on provider-specific message ids.
    """

    short = (session_id or "0").replace("-", "")[:8] or "0"
    return f"ac_{short}_t{turn_count}_m{model_turn_index}"
