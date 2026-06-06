"""JSONL transcript storage for session messages."""

from __future__ import annotations

import atexit
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from threading import RLock, Timer
import uuid
from typing import Any


VALID_MESSAGE_ROLES = {"user", "assistant", "tool_result", "attachment"}
TOOL_RESULT_EXTERNALIZE_THRESHOLD_BYTES = 50 * 1024
DEFAULT_TOOL_RESULT_PREVIEW_CHARS = 4_000


@dataclass(frozen=True)
class LoadedTranscriptMessage:
    uuid: str
    parent_uuid: str | None
    session_id: str
    message: dict[str, Any]


class JsonlTranscriptStore:
    """按 session 将内部消息缓冲并定时追加写入 JSONL。

    参数:
    - root_dir: 会话根目录，通常是项目根目录下的 `.onecode`。
    - session_id: 当前运行时会话 UUID，会成为子目录名。
    - cwd: 记录到 JSONL 的当前工作目录；不传时使用当前进程目录。
    - flush_interval_seconds: 自动 flush 的间隔；测试可调用 `flush()` 立即落盘。
    """

    def __init__(
        self,
        root_dir: Path,
        session_id: str,
        cwd: Path | None = None,
        flush_interval_seconds: float = 1.0,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.session_id = session_id
        self.cwd = Path.cwd() if cwd is None else Path(cwd)
        self.flush_interval_seconds = flush_interval_seconds
        self._pending_lines: list[str] = []
        self._flush_timer: Timer | None = None
        self._lock = RLock()
        atexit.register(self.flush)

    @property
    def session_dir(self) -> Path:
        return self.root_dir / self.session_id

    @property
    def messages_path(self) -> Path:
        return self.session_dir / "messages.jsonl"

    @property
    def tool_results_dir(self) -> Path:
        return self.session_dir / "tool-results"

    def switch_session(self, session_id: str) -> None:
        """切换当前写入的 session 目录。

        参数:
        - session_id: 新的会话 UUID。切换只改变后续写入路径，不删除旧文件。
        """

        self.flush()
        with self._lock:
            self.session_id = session_id

    def append_message(
        self,
        message: dict[str, Any],
        *,
        message_uuid: str,
        parent_uuid: str | None,
    ) -> None:
        """追加一条内部消息到当前 session 的 `messages.jsonl`。

        参数:
        - message: `MessageStore` 中保存的内部消息对象。
        - message_uuid: 本条 transcript record 的 UUID。
        - parent_uuid: 上一条 transcript record 的 UUID，用于建立线性消息链。
        """

        record_message = self._message_for_record(message)
        record = {
            "type": "message",
            "uuid": message_uuid,
            "parent_uuid": parent_uuid,
            "session_id": self.session_id,
            "timestamp": _utc_timestamp(),
            "cwd": str(self.cwd),
            "message": record_message,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        self._enqueue_line(line)

    def load_messages(self) -> tuple[LoadedTranscriptMessage, ...]:
        """从当前 session 的 JSONL 文件恢复内部消息列表。

        读取时按文件顺序恢复主链；空行、损坏 JSON、缺少 message 的记录和
        未知角色会被跳过。外置工具结果存在时会重新读回完整 content。
        """

        self.flush()
        if not self.messages_path.exists():
            return ()

        loaded: list[LoadedTranscriptMessage] = []
        with self.messages_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = _parse_json_line(line)
                if record is None or record.get("type") != "message":
                    continue

                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                if message.get("role") not in VALID_MESSAGE_ROLES:
                    continue

                message_uuid = record.get("uuid")
                session_id = record.get("session_id")
                if not isinstance(message_uuid, str) or not isinstance(session_id, str):
                    continue

                parent_uuid = record.get("parent_uuid")
                if parent_uuid is not None and not isinstance(parent_uuid, str):
                    parent_uuid = None

                restored_message = self._restore_externalized_tool_result(message)
                loaded.append(
                    LoadedTranscriptMessage(
                        uuid=message_uuid,
                        parent_uuid=parent_uuid,
                        session_id=session_id,
                        message=restored_message,
                    )
                )
        return tuple(loaded)

    def flush(self) -> None:
        """把当前缓冲中的 JSONL 行立即写入磁盘。

        该方法用于定时器回调、测试验证和 session 切换。它只写入已经缓冲的
        record，不修改内存中的模型消息。
        """

        with self._lock:
            lines = self._pending_lines
            self._pending_lines = []
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None

        if not lines:
            return

        self.session_dir.mkdir(parents=True, exist_ok=True)
        with self.messages_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")

    def _message_for_record(self, message: dict[str, Any]) -> dict[str, Any]:
        record_message = deepcopy(message)
        if record_message.get("role") != "tool_result":
            return record_message

        content = record_message.get("content")
        if not isinstance(content, str):
            return record_message

        content_size = len(content.encode("utf-8"))
        if content_size <= TOOL_RESULT_EXTERNALIZE_THRESHOLD_BYTES:
            return record_message

        tool_call_id = record_message.get("tool_call_id")
        safe_file_name = _safe_tool_result_file_name(tool_call_id)
        relative_path = f"tool-results/{safe_file_name}"
        self.tool_results_dir.mkdir(parents=True, exist_ok=True)
        (self.tool_results_dir / safe_file_name).write_text(content, encoding="utf-8")

        metadata = deepcopy(record_message.get("metadata") or {})
        metadata.update(
            {
                "tool_result_externalized": True,
                "tool_result_path": relative_path,
                "original_tool_call_id": tool_call_id,
                "original_size_bytes": content_size,
                "preview_chars": DEFAULT_TOOL_RESULT_PREVIEW_CHARS,
            }
        )
        preview = content[:DEFAULT_TOOL_RESULT_PREVIEW_CHARS]
        record_message["content"] = (
            f"[tool result externalized: {relative_path}]\n{preview}"
        )
        record_message["metadata"] = metadata
        return record_message

    def _restore_externalized_tool_result(
        self,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        restored = deepcopy(message)
        metadata = deepcopy(restored.get("metadata") or {})
        if metadata.get("tool_result_externalized") is not True:
            return restored

        relative_path = metadata.get("tool_result_path")
        if not isinstance(relative_path, str):
            metadata["missing_external_tool_result"] = True
            restored["metadata"] = metadata
            return restored

        result_path = self.session_dir / relative_path
        try:
            restored["content"] = result_path.read_text(encoding="utf-8")
        except OSError:
            metadata["missing_external_tool_result"] = True
            restored["metadata"] = metadata
        return restored

    def _enqueue_line(self, line: str) -> None:
        with self._lock:
            self._pending_lines.append(line)
            if self._flush_timer is None:
                self._flush_timer = Timer(
                    self.flush_interval_seconds,
                    self.flush,
                )
                self._flush_timer.daemon = True
                self._flush_timer.start()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_json_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _safe_tool_result_file_name(tool_call_id: Any) -> str:
    """把 tool call id 转成可安全用作文件名的字符串。

    参数:
    - tool_call_id: provider 或 runtime 生成的工具调用 ID，可能为空或包含路径字符。
    """

    raw = tool_call_id if isinstance(tool_call_id, str) else ""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._")
    if not safe:
        safe = str(uuid.uuid4())
    return f"{safe}.txt"
