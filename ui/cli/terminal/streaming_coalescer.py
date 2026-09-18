"""流式 CLI 的事件合并器。

Provider 在流式传输文本期间会发出大量高频事件，例如单个轮次内产生数百个
assistant_delta 事件。若将每个事件单独交由 reducer 处理并在每个事件后触发屏幕重绘，
将白白消耗 CPU 且无任何肉眼可见的好处：人类阅读字符的速度无法快于每个字形约 16 毫秒。

本模块提供 StreamingCoalescer，将高频突发事件缓冲进单个 16 毫秒窗口内。
在窗口内，增量会被拼接累加，对应的 reducer 仅被调用一次。
低频事件（tool_call_ready、tool_started、tool_result、transition、completed、
error）会立即生效，并强制刷新任何待处理批次，确保可见 UI 与 reducer 保持同步。

窗口大小与参考实现所用的 16 毫秒（约 60 fps）节奏保持一致，
这也大致是人类感知屏幕重绘为“即时”的阈值。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.stream_events import AgentEvent


# 可合并的事件类型。assistant_delta 累积文本，tool_progress 覆盖针对调用的进度字符串
# （同一调用 ID 的多个事件折叠为最终值）。tool_call_delta 携带流式工具调用名称；
# reducer 仅读取首个名称，因此合并是安全的。
_COALESCED_EVENT_TYPES = frozenset({"assistant_delta", "tool_progress", "tool_call_delta"})


class StreamingCoalescer:
    """将高频 agent 事件合并进 16 毫秒窗口。

    参数：
        apply：reducer 入口点。接收聚合了当前窗口内所缓冲事件的单个 AgentEvent（或对于低频/单次事件接收原始事件）。
        window_seconds：挂起事件在缓冲区内滞留的最长时间，超时需执行刷新。默认为 0.016（16 毫秒）。
        clock：用于窗口计时的单调时钟。默认为 time.monotonic。测试可注入虚拟时钟以驱动确定性的刷新行为。
    """

    def __init__(
        self,
        *,
        apply: Callable[["AgentEvent"], None],
        window_seconds: float = 0.016,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._apply = apply
        self._window_seconds = window_seconds
        self._clock = clock
        # 挂起批次状态。我们按可合并类型跟踪合并事件，使单个窗口至多包含
        # 一个合并后的 assistant_delta 和一个合并后的 tool_progress
        # （reducer 无论如何只读取最新的进度消息）。
        self._pending_assistant_text: str = ""
        self._pending_assistant_metadata: dict | None = None
        self._has_pending_assistant = False
        self._pending_progress: dict[str, str] = {}
        self._pending_progress_metadata: dict[str, dict] = {}
        self._has_pending_progress = False
        self._pending_tool_name: str | None = None
        self._pending_tool_metadata: dict | None = None
        self._has_pending_tool_delta = False
        self._last_flush = clock()

    def push(self, event: "AgentEvent") -> bool:
        """缓冲 event；若为低频事件则立即生效。

        当事件立即生效时返回 True（调用方应安排屏幕重绘）；
        当事件被合并进挂起批次时返回 False（调用方应等待下个窗口重绘）。
        """

        event_type = getattr(event, "type", None)
        metadata = getattr(event, "metadata", None) or {}
        if event_type == "assistant_delta":
            text = getattr(event, "text", "") or ""
            if text:
                self._pending_assistant_text += text
                self._has_pending_assistant = True
            # 保留最新事件的归属信息，使刷新时合成的 assistant_delta 依然携带稳定的 assistant_call_id 和 model_turn_index。
            if self._pending_assistant_metadata is None:
                self._pending_assistant_metadata = dict(metadata)
            else:
                self._pending_assistant_metadata.update(metadata)
            return False
        if event_type == "tool_progress":
            call_id = metadata.get("tool_call_id")
            message = str(metadata.get("message") or metadata.get("text") or "")
            if call_id:
                self._pending_progress[str(call_id)] = message
                self._pending_progress_metadata[str(call_id)] = dict(metadata)
                self._has_pending_progress = True
            return False
        if event_type == "tool_call_delta":
            name = metadata.get("name") or ""
            if name and not self._pending_tool_name:
                self._pending_tool_name = name
            if self._pending_tool_metadata is None:
                self._pending_tool_metadata = dict(metadata)
            else:
                self._pending_tool_metadata.update(metadata)
            self._has_pending_tool_delta = True
            return False
        # 低频事件：先刷新所有待处理批次，使可见状态反映完整历史，然后生效。
        if self._has_pending():
            self.flush()
        self._apply(event)
        return True

    def flush(self) -> bool:
        """使所有挂起事件生效并清空批次。

        当至少有一个事件被刷新时返回 True。无挂起内容时为空操作。
        """

        flushed = False
        if self._has_pending_tool_delta:
            from core.stream_events import AgentEvent

            tool_metadata = dict(self._pending_tool_metadata or {})
            tool_metadata["name"] = self._pending_tool_name or ""
            self._apply(
                AgentEvent(
                    type="tool_call_delta",
                    metadata=tool_metadata,
                )
            )
            self._pending_tool_name = None
            self._pending_tool_metadata = None
            self._has_pending_tool_delta = False
            flushed = True
        if self._has_pending_assistant:
            from core.stream_events import AgentEvent

            self._apply(
                AgentEvent(
                    type="assistant_delta",
                    text=self._pending_assistant_text,
                    metadata=dict(self._pending_assistant_metadata or {}),
                )
            )
            self._pending_assistant_text = ""
            self._pending_assistant_metadata = None
            self._has_pending_assistant = False
            flushed = True
        if self._has_pending_progress:
            from core.stream_events import AgentEvent

            for call_id, message in self._pending_progress.items():
                meta = dict(self._pending_progress_metadata.get(call_id) or {})
                meta["tool_call_id"] = call_id
                meta["message"] = message
                self._apply(
                    AgentEvent(
                        type="tool_progress",
                        metadata=meta,
                    )
                )
            self._pending_progress.clear()
            self._pending_progress_metadata.clear()
            self._has_pending_progress = False
            flushed = True
        if flushed:
            self._last_flush = self._clock()
        return flushed

    def should_flush(self, now: float | None = None) -> bool:
        """当窗口已超时且存在挂起事件时返回 True。"""

        if not self._has_pending():
            return False
        current = self._clock() if now is None else now
        return (current - self._last_flush) >= self._window_seconds

    def _has_pending(self) -> bool:
        return (
            self._has_pending_assistant
            or self._has_pending_progress
            or self._has_pending_tool_delta
        )


__all__ = ["StreamingCoalescer"]
