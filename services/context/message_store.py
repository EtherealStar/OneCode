"""In-memory message store backed by JSONL session persistence."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

from services.context.transcript import JsonlTranscriptStore
from services.tools.types import ToolExecutionResult

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState


class MessageStore:
    """内存优先、JSONL 持久化必经的会话消息存储。

    会话进行时模型上下文从内存读取；每次追加消息都会同时进入 transcript
    store 的缓冲区，并由 transcript store 定时写入磁盘。
    """

    def __init__(
        self,
        *,
        transcript_store: JsonlTranscriptStore | None = None,
        transcript_root: Path | str = ".onecode",
        session_id: str | None = None,
        cwd: Path | None = None,
        flush_interval_seconds: float = 1.0,
    ) -> None:
        self._messages: list[dict[str, Any]] = []
        self._last_uuid: str | None = None
        resolved_session_id = session_id or str(uuid.uuid4())
        self._transcript_store = transcript_store or JsonlTranscriptStore(
            Path(transcript_root),
            resolved_session_id,
            cwd=cwd,
            flush_interval_seconds=flush_interval_seconds,
        )

    def append_user(self, content: str | list[dict[str, Any]]) -> dict[str, Any]:
        """追加用户消息。

        参数:
        - content: 用户输入文本，或未来多模态消息块列表。
        """

        return self._append({"role": "user", "content": content})

    def append_assistant(self, message: dict[str, Any]) -> dict[str, Any]:
        """追加 assistant 消息。

        参数:
        - message: provider adapter 归一化后的 assistant 内部消息。
        """

        assistant_message = deepcopy(message)
        assistant_message.setdefault("role", "assistant")
        return self._append(assistant_message)

    def append_tool_results(
        self,
        results: list[ToolExecutionResult],
    ) -> list[dict[str, Any]]:
        """追加工具执行结果。

        参数:
        - results: executor 返回的工具结果列表。每个结果会转换为内部
          `role="tool_result"` 消息，并按顺序进入内存与 JSONL transcript。
        """

        stored_results: list[dict[str, Any]] = []
        for result in results:
            stored_results.append(
                self._append(
                    {
                        "role": "tool_result",
                        "tool_call_id": result.tool_call_id,
                        "tool_name": result.tool_name,
                        "content": result.content,
                        "is_error": result.is_error,
                        "metadata": result.metadata,
                    }
                )
            )
        return stored_results

    def current_messages(self) -> tuple[dict[str, Any], ...]:
        """返回当前内存中的模型上下文消息副本。"""

        return tuple(deepcopy(self._messages))

    @property
    def transcript_store(self) -> JsonlTranscriptStore:
        return self._transcript_store

    @property
    def session_id(self) -> str:
        return self._transcript_store.session_id

    def bind_session(self, session_id: str) -> None:
        """把消息存储绑定到运行时 session UUID。

        参数:
        - session_id: `RuntimeState.session_id`。已有消息时不能切到另一个
          session，避免把同一内存链写入两个不相关的目录。
        """

        if self.session_id == session_id:
            return
        if self._messages:
            raise ValueError("cannot rebind a non-empty message store")
        self._transcript_store.switch_session(session_id)

    def flush_transcript(self) -> None:
        """立即把待写 JSONL 记录刷入磁盘。"""

        self._transcript_store.flush()

    def clear_for_new_session(self, new_session_id: str) -> None:
        """清空内存消息并切换到新的 session。

        参数:
        - new_session_id: 新会话 UUID，通常来自 `RuntimeState.start_new_session()`。
        """

        self._messages.clear()
        self._last_uuid = None
        self._transcript_store.switch_session(new_session_id)

    @classmethod
    def from_transcript(
        cls,
        transcript_store: JsonlTranscriptStore,
        state: RuntimeState,
    ) -> "MessageStore":
        """从 JSONL transcript 恢复内存消息存储。

        参数:
        - transcript_store: 指向既有 `.onecode/<session_id>/messages.jsonl`
          的 transcript store。
        - state: 当前运行时状态。恢复成功后会把 `state.session_id` 替换为
          transcript 文件中的 session UUID。
        """

        loaded = transcript_store.load_messages()
        if loaded:
            state.session_id = loaded[-1].session_id
            transcript_store.switch_session(state.session_id)
        else:
            state.session_id = transcript_store.session_id

        message_store = cls(transcript_store=transcript_store)
        message_store._messages = [deepcopy(item.message) for item in loaded]
        message_store._last_uuid = loaded[-1].uuid if loaded else None
        return message_store

    def _append(self, message: dict[str, Any]) -> dict[str, Any]:
        stored = deepcopy(message)
        self._messages.append(stored)
        message_uuid = str(uuid.uuid4())
        self._transcript_store.append_message(
            stored,
            message_uuid=message_uuid,
            parent_uuid=self._last_uuid,
        )
        self._last_uuid = message_uuid
        return deepcopy(stored)
