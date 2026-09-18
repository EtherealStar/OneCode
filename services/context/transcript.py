"""JSONL transcript storage for session messages."""

from __future__ import annotations

import atexit
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock, Timer
from typing import Any, Iterator
import uuid

from utils.toolResultStorage import ToolResultStorage


VALID_MESSAGE_ROLES = {"user", "assistant", "tool_result", "attachment"}
TOOL_RESULT_EXTERNALIZE_THRESHOLD_BYTES = 50 * 1024
DEFAULT_TOOL_RESULT_PREVIEW_CHARS = 4_000


@dataclass(frozen=True)
class LoadedTranscriptMessage:
    uuid: str
    parent_uuid: str | None
    session_id: str
    timestamp: str | None
    sequence: int
    message: dict[str, Any]
    #: 稳定的模型调用归属,由 ``core/loop.py`` 的
    #: ``assistant_call_id`` / ``model_turn_index`` 提供。它和持久化记录
    #: UUID 是两类身份,这里显式保存二者的关联,而不是靠下标或正文推断。
    assistant_call_id: str | None = None
    model_turn_index: int | None = None
    #: 对 compaction 复制出的记录指向原始记录 UUID;内部产物(边界、摘要)
    #: 的 ``record_kind`` 为 ``compaction`` 且 ``source_uuid`` 为空。
    source_uuid: str | None = None
    record_kind: str | None = None

    def with_message(self, message: dict[str, Any]) -> "LoadedTranscriptMessage":
        return replace(self, message=message)


class JsonlTranscriptStore:
    """按 session 将内部消息缓冲并定时追加写入 JSONL。

    参数:
    - root_dir: 会话根目录，通常是项目根目录下的 `.onecode/sessions`。
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
        self._pending_records: list[dict[str, Any]] = []
        self._flush_timer: Timer | None = None
        self._lock = RLock()
        self.last_staging_path: str | None = None
        atexit.register(self.flush)

    @contextmanager
    def write_guard(self) -> Iterator[None]:
        """Hold the transcript write lock across a read-modify-rewrite cycle."""

        with self._lock:
            yield

    @property
    def session_dir(self) -> Path:
        return self.root_dir / self.session_id

    @property
    def messages_path(self) -> Path:
        return self.session_dir / "messages.jsonl"

    @property
    def tool_results_dir(self) -> Path:
        return self.session_dir / "tool-results"

    @property
    def tool_result_storage(self) -> ToolResultStorage:
        return ToolResultStorage(self.session_dir)

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
        assistant_call_id: str | None = None,
        model_turn_index: int | None = None,
        source_uuid: str | None = None,
        record_kind: str | None = None,
    ) -> None:
        """追加一条内部消息到当前 session 的 `messages.jsonl`。

        参数:
        - message: `MessageStore` 中保存的内部消息对象。
        - message_uuid: 本条 transcript record 的 UUID。
        - parent_uuid: 上一条 transcript record 的 UUID，用于建立线性消息链。
        - assistant_call_id / model_turn_index: 该记录所属模型调用的稳定归属。
        - source_uuid / record_kind: compaction 复制来源及内部产物标记。
        """

        record_message = self._message_for_record(message)
        record: dict[str, Any] = {
            "type": "message",
            "uuid": message_uuid,
            "parent_uuid": parent_uuid,
            "session_id": self.session_id,
            "timestamp": _utc_timestamp(),
            "cwd": str(self.cwd),
            "message": record_message,
        }
        if assistant_call_id is not None:
            record["assistant_call_id"] = assistant_call_id
        if model_turn_index is not None:
            record["model_turn_index"] = model_turn_index
        if source_uuid is not None:
            record["source_uuid"] = source_uuid
        if record_kind is not None:
            record["record_kind"] = record_kind
        self._enqueue_record(record)

    def load_messages(
        self,
        *,
        restore_external_results: bool = True,
    ) -> tuple[LoadedTranscriptMessage, ...]:
        """从当前 session 的 JSONL 文件恢复内部消息列表。

        读取时按文件顺序恢复主链；空行、损坏 JSON、缺少 message 的记录和
        未知角色会被跳过。``restore_external_results`` 为 True 时会把外置
        工具结果的完整 content 读回；历史浏览应传 False，只保留摘要和引用。
        """

        self.flush()
        if not self.messages_path.exists():
            return ()

        with self._lock:
            return tuple(
                loaded
                for loaded in (
                    self._record_to_loaded(
                        record,
                        restore_external_results=restore_external_results,
                    )
                    for record in self._read_file_records()
                )
                if loaded is not None
            )

    def read_records(self) -> tuple[LoadedTranscriptMessage, ...]:
        """返回文件与待写缓冲中的全部记录，不读取外置结果正文。

        该方法不触发 flush，用于受控重写和恢复修复：调用者拿到完整原始
        transcript（含尚未落盘的缓冲）后再决定修正哪些记录。
        """

        with self._lock:
            records: list[dict[str, Any]] = []
            if self.messages_path.exists():
                records.extend(self._read_file_records())
            next_sequence = len(records)
            for offset, pending in enumerate(self._pending_records):
                records.append({**pending, "_sequence": next_sequence + offset})
            return tuple(
                loaded
                for loaded in (
                    self._record_to_loaded(
                        record,
                        restore_external_results=False,
                    )
                    for record in records
                )
                if loaded is not None
            )

    def rewrite_records(
        self,
        records: tuple[LoadedTranscriptMessage, ...] | list[LoadedTranscriptMessage],
    ) -> None:
        """用修正后的完整记录集原子替换正式 transcript。

        整个读取-暂存-替换-清缓冲过程在同一把写锁内完成，避免与定时
        flush 竞争。失败时保留原文件和暂存文件，由调用者决定如何降级。
        """

        with self._lock:
            self._cancel_timer_locked()
            staging_path = self.messages_path.with_name(
                f"{self.messages_path.name}.staging-{uuid.uuid4().hex}"
            )
            self.messages_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                json.dumps(
                    self._loaded_to_record(record),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                for record in records
            ]
            payload = "".join(f"{line}\n" for line in lines)
            self.last_staging_path = str(staging_path)
            try:
                staging_path.write_text(payload, encoding="utf-8", newline="\n")
                os.replace(staging_path, self.messages_path)
            except OSError:
                # 保留暂存文件以便人工恢复，绝不触碰正式文件。
                raise
            self._pending_records = []

    def flush(self) -> None:
        """把当前缓冲中的 JSONL 行立即写入磁盘。

        写入在写锁内完成：旧实现取出 pending 行后释放锁再写磁盘，会和
        受控重写交错并写回已被删除的记录。这里持有锁直到写入结束。
        """

        with self._lock:
            self._cancel_timer_locked()
            pending = self._pending_records
            if not pending:
                return
            self.session_dir.mkdir(parents=True, exist_ok=True)
            payload = "".join(
                f"{json.dumps(record, ensure_ascii=False, separators=(',', ':'))}\n"
                for record in pending
            )
            with self.messages_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
            del self._pending_records[: len(pending)]

    def _read_file_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with self.messages_path.open("r", encoding="utf-8") as handle:
            for sequence, line in enumerate(handle):
                record = _parse_json_line(line)
                if record is None or record.get("type") != "message":
                    continue
                records.append({**record, "_sequence": sequence})
        return records

    def _record_to_loaded(
        self,
        record: dict[str, Any],
        *,
        restore_external_results: bool,
    ) -> LoadedTranscriptMessage | None:
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        if message.get("role") not in VALID_MESSAGE_ROLES:
            return None

        message_uuid = record.get("uuid")
        session_id = record.get("session_id")
        if not isinstance(message_uuid, str) or not isinstance(session_id, str):
            return None

        parent_uuid = record.get("parent_uuid")
        if parent_uuid is not None and not isinstance(parent_uuid, str):
            parent_uuid = None

        restored_message = (
            self._restore_externalized_tool_result(message)
            if restore_external_results
            else deepcopy(message)
        )
        timestamp = record.get("timestamp")
        sequence = record.get("_sequence")
        return LoadedTranscriptMessage(
            uuid=message_uuid,
            parent_uuid=parent_uuid,
            session_id=session_id,
            timestamp=timestamp if isinstance(timestamp, str) else None,
            sequence=sequence if isinstance(sequence, int) else 0,
            message=restored_message,
            assistant_call_id=_optional_str(record.get("assistant_call_id")),
            model_turn_index=(
                record["model_turn_index"]
                if isinstance(record.get("model_turn_index"), int)
                else None
            ),
            source_uuid=_optional_str(record.get("source_uuid")),
            record_kind=_optional_str(record.get("record_kind")),
        )

    def _loaded_to_record(self, loaded: LoadedTranscriptMessage) -> dict[str, Any]:
        record: dict[str, Any] = {
            "type": "message",
            "uuid": loaded.uuid,
            "parent_uuid": loaded.parent_uuid,
            "session_id": loaded.session_id or self.session_id,
            "timestamp": loaded.timestamp or _utc_timestamp(),
            "cwd": str(self.cwd),
            "message": self._message_for_record(loaded.message),
        }
        if loaded.assistant_call_id is not None:
            record["assistant_call_id"] = loaded.assistant_call_id
        if loaded.model_turn_index is not None:
            record["model_turn_index"] = loaded.model_turn_index
        if loaded.source_uuid is not None:
            record["source_uuid"] = loaded.source_uuid
        if loaded.record_kind is not None:
            record["record_kind"] = loaded.record_kind
        return record

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
        tool_name = record_message.get("tool_name")
        storage = self.tool_result_storage
        ref = storage.persist_tool_result(
            tool_call_id=tool_call_id,
            tool_name=tool_name if isinstance(tool_name, str) else "",
            content=content,
        )

        metadata = deepcopy(record_message.get("metadata") or {})
        metadata.update(
            storage.transcript_metadata(
                ref,
                preview_chars=DEFAULT_TOOL_RESULT_PREVIEW_CHARS,
            )
        )
        preview = content[:DEFAULT_TOOL_RESULT_PREVIEW_CHARS]
        record_message["content"] = storage.format_transcript_externalization(
            ref,
            preview=preview,
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

        try:
            restored["content"] = self.tool_result_storage.read_result(relative_path)
        except OSError:
            metadata["missing_external_tool_result"] = True
            restored["metadata"] = metadata
        return restored

    def _enqueue_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._pending_records.append(record)
            if self._flush_timer is None:
                self._flush_timer = Timer(
                    self.flush_interval_seconds,
                    self.flush,
                )
                self._flush_timer.daemon = True
                self._flush_timer.start()

    def _cancel_timer_locked(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None


class InMemoryTranscriptStore:
    """Transcript-store compatible sink that never writes session files.

    This is used for short-lived internal child runtimes. Their live
    conversation still belongs in ``MessageStore`` while the child is running,
    but the transcript is an implementation detail and should not appear as a
    resumable user session.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._records: list[LoadedTranscriptMessage] = []
        self.last_staging_path: str | None = None

    @contextmanager
    def write_guard(self) -> Iterator[None]:
        yield

    @property
    def session_dir(self) -> Path:
        return Path("<memory>") / self.session_id

    @property
    def messages_path(self) -> Path:
        return self.session_dir / "messages.jsonl"

    @property
    def tool_results_dir(self) -> Path:
        return self.session_dir / "tool-results"

    @property
    def tool_result_storage(self) -> ToolResultStorage:
        return ToolResultStorage(self.session_dir)

    def switch_session(self, session_id: str) -> None:
        self.session_id = session_id
        self._records.clear()

    def append_message(
        self,
        message: dict[str, Any],
        *,
        message_uuid: str,
        parent_uuid: str | None,
        assistant_call_id: str | None = None,
        model_turn_index: int | None = None,
        source_uuid: str | None = None,
        record_kind: str | None = None,
    ) -> None:
        self._records.append(
            LoadedTranscriptMessage(
                uuid=message_uuid,
                parent_uuid=parent_uuid,
                session_id=self.session_id,
                timestamp=_utc_timestamp(),
                sequence=len(self._records),
                message=deepcopy(message),
                assistant_call_id=assistant_call_id,
                model_turn_index=model_turn_index,
                source_uuid=source_uuid,
                record_kind=record_kind,
            )
        )

    def load_messages(
        self,
        *,
        restore_external_results: bool = True,
    ) -> tuple[LoadedTranscriptMessage, ...]:
        _ = restore_external_results
        return tuple(
            replace(record, message=deepcopy(record.message))
            for record in self._records
        )

    def read_records(self) -> tuple[LoadedTranscriptMessage, ...]:
        return tuple(
            replace(record, message=deepcopy(record.message))
            for record in self._records
        )

    def rewrite_records(
        self,
        records: tuple[LoadedTranscriptMessage, ...] | list[LoadedTranscriptMessage],
    ) -> None:
        self._records = [
            replace(record, message=deepcopy(record.message)) for record in records
        ]

    def flush(self) -> None:
        return None


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


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
