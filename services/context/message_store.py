"""In-memory message store for the first runtime skeleton."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class MessageStore:
    """Append-only session message store.

    This minimal implementation keeps internal messages in memory. Future
    transcript and compaction services can extend this boundary without
    changing the main loop.
    """

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []

    def append_user(self, content: str | list[dict[str, Any]]) -> dict[str, Any]:
        return self._append({"role": "user", "content": content})

    def append_assistant(self, message: dict[str, Any]) -> dict[str, Any]:
        assistant_message = deepcopy(message)
        assistant_message.setdefault("role", "assistant")
        return self._append(assistant_message)

    def append_tool_results(
        self,
        result_blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._append({"role": "user", "content": deepcopy(result_blocks)})

    def current_messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._messages))

    def _append(self, message: dict[str, Any]) -> dict[str, Any]:
        stored = deepcopy(message)
        self._messages.append(stored)
        return deepcopy(stored)
