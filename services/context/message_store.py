"""由 JSONL 会话持久化支持的内存消息存储。"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from services.context.message_shapes import (
    assistant_declarations,
    message_has_body,
    prune_assistant_declarations,
)
from services.context.recovery import restore_transcript_active_chain
from services.context.run_facts import InterruptedRunFacts
from services.context.transcript import (
    InMemoryTranscriptStore,
    JsonlTranscriptStore,
    LoadedTranscriptMessage,
)
from services.tools.types import ToolExecutionResult

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState
    from services.observability import ErrorLogRecorder


@dataclass(frozen=True)
class StoredRecordMeta:
    """与中立消息一并保存的记录级身份标识。"""

    uuid: str
    parent_uuid: str | None
    assistant_call_id: str | None = None
    model_turn_index: int | None = None
    source_uuid: str | None = None
    record_kind: str | None = None


@dataclass(frozen=True)
class ActiveMessage:
    """带有稳定身份标识的单条活动链记录。"""

    meta: StoredRecordMeta
    message: dict[str, Any]

    @property
    def uuid(self) -> str:
        return self.meta.uuid

    @property
    def source_uuid(self) -> str | None:
        return self.meta.source_uuid


@dataclass(frozen=True)
class InterruptCleanupResult:
    success: bool
    records_removed: int = 0
    records_added: int = 0
    records_modified: int = 0
    error: str | None = None
    staging_path: str | None = None


class MessageStore:
    """内存优先、JSONL 持久化必经的会话消息存储。

    会话进行时模型上下文从内存读取；每次追加消息都会同时进入 transcript
    store 的缓冲区，并由 transcript store 定时写入磁盘。
    """

    def __init__(
        self,
        *,
        transcript_store: Any | None = None,
        transcript_root: Path | str = ".onecode",
        session_id: str | None = None,
        cwd: Path | None = None,
        flush_interval_seconds: float = 1.0,
    ) -> None:
        self._messages: list[dict[str, Any]] = []
        self._metas: list[StoredRecordMeta] = []
        self._last_uuid: str | None = None
        self._state_lock = RLock()
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

    def append_assistant(
        self,
        message: dict[str, Any],
        *,
        assistant_call_id: str | None = None,
        model_turn_index: int | None = None,
    ) -> dict[str, Any]:
        """追加 assistant 消息。

        参数:
        - message: provider adapter 归一化后的 assistant 内部消息。
        """

        assistant_message = deepcopy(message)
        assistant_message.setdefault("role", "assistant")
        return self._append(
            assistant_message,
            assistant_call_id=assistant_call_id,
            model_turn_index=model_turn_index,
        )

    def append_tool_results(
        self,
        results: list[ToolExecutionResult],
        *,
        assistant_call_id: str | None = None,
        model_turn_index: int | None = None,
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
                    },
                    assistant_call_id=assistant_call_id,
                    model_turn_index=model_turn_index,
                )
            )
        return stored_results

    def append_attachments(
        self,
        attachments: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """追加 durable attachment messages，不写入 synthetic tool pairs。"""

        stored: list[dict[str, Any]] = []
        for attachment in attachments:
            next_message = deepcopy(attachment)
            next_message.setdefault("role", "attachment")
            stored.append(self._append(next_message))
        return stored

    def current_messages(self) -> tuple[dict[str, Any], ...]:
        """返回当前内存中的模型上下文消息副本。"""

        return tuple(deepcopy(self._messages))

    def active_records(self) -> tuple[ActiveMessage, ...]:
        """返回带有稳定身份标识的活动链记录。"""

        return tuple(
            ActiveMessage(meta=meta, message=deepcopy(message))
            for meta, message in zip(self._metas, self._messages)
        )

    @property
    def last_record_uuid(self) -> str | None:
        return self._last_uuid

    def seed_messages(
        self,
        messages: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """预置一条空消息链，并像普通追加一样写入 transcript。"""

        with self._state_lock:
            if self._messages:
                raise ValueError("cannot seed a non-empty message store")
            stored: list[dict[str, Any]] = []
            for message in messages:
                stored.append(self._append(message))
            return stored

    def replace_messages_for_compaction(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
        source_uuids: Iterable[str | None] | None = None,
    ) -> list[dict[str, Any]]:
        """压缩完成后仅替换活动内存链。

        transcript 保持仅追加：先刷盘已有记录，再将压缩后的链作为新消息记录追加。
        每条替换记录保留明确指向原始被复制记录的 source_uuid 链接，
        以便历史记录无需比对文本即可过滤重复项。
        """

        replacement = [deepcopy(message) for message in messages]
        if not replacement:
            raise ValueError("cannot replace active messages with an empty chain")
        sources = (
            list(source_uuids)
            if source_uuids is not None
            else [None] * len(replacement)
        )
        if len(sources) != len(replacement):
            raise ValueError("source_uuids must align with replacement messages")

        with self._state_lock:
            self.flush_transcript()
            self._messages.clear()
            self._metas.clear()
            self._last_uuid = None
            stored: list[dict[str, Any]] = []
            for message, source_uuid in zip(replacement, sources):
                enriched = deepcopy(message)
                message_metadata = dict(enriched.get("metadata") or {})
                message_metadata.setdefault(
                    "compaction",
                    {
                        "reason": reason,
                        **(metadata or {}),
                    },
                )
                enriched["metadata"] = message_metadata
                stored.append(
                    self._append(
                        enriched,
                        source_uuid=source_uuid,
                        record_kind="compaction",
                    )
                )
            return stored

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
        with self._state_lock:
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

        with self._state_lock:
            self._messages.clear()
            self._metas.clear()
            self._last_uuid = None
            self._transcript_store.switch_session(new_session_id)

    def finalize_interrupted_run(
        self,
        facts: InterruptedRunFacts,
        *,
        error_log_recorder: ErrorLogRecorder | None = None,
    ) -> InterruptCleanupResult:
        """将中断清理契约应用到内存和 transcript。

        保留用户输出和真实的工具配对，移除未配对声明和孤立结果，
        并原子重写实际的 transcript。仅在磁盘重写成功后才提交内存和终端状态。
        """

        with self._state_lock, self._transcript_store.write_guard():
            return self._finalize_locked(facts, error_log_recorder)

    @classmethod
    def ephemeral(
        cls,
        *,
        session_id: str,
    ) -> MessageStore:
        """创建其 transcript 绝不写入磁盘的消息存储。"""

        return cls(transcript_store=InMemoryTranscriptStore(session_id))

    @classmethod
    def from_transcript(
        cls,
        transcript_store: JsonlTranscriptStore,
        state: RuntimeState,
    ) -> MessageStore:
        """从 JSONL transcript 恢复内存消息存储。

        参数:
        - transcript_store: 指向既有 `.onecode/sessions/<session_id>/messages.jsonl`
          的 transcript store。
        - state: 当前运行时状态。恢复成功后会把 `state.session_id` 替换为
          transcript 文件中的 session UUID。
        """

        restored = restore_transcript_active_chain(transcript_store)
        state.session_id = restored.session_id
        transcript_store.switch_session(state.session_id)

        message_store = cls(transcript_store=transcript_store)
        message_store._messages = [deepcopy(message) for message in restored.messages]
        message_store._metas = [
            StoredRecordMeta(
                uuid=record.uuid,
                parent_uuid=record.parent_uuid,
                assistant_call_id=record.assistant_call_id,
                model_turn_index=record.model_turn_index,
                source_uuid=record.source_uuid,
                record_kind=record.record_kind,
            )
            for record in restored.records
        ]
        message_store._last_uuid = restored.last_uuid
        return message_store

    def _append(
        self,
        message: dict[str, Any],
        *,
        source_uuid: str | None = None,
        record_kind: str | None = None,
        assistant_call_id: str | None = None,
        model_turn_index: int | None = None,
    ) -> dict[str, Any]:
        with self._state_lock:
            stored = deepcopy(message)
            message_uuid = str(uuid.uuid4())
            self._messages.append(stored)
            self._metas.append(
                StoredRecordMeta(
                    uuid=message_uuid,
                    parent_uuid=self._last_uuid,
                    assistant_call_id=assistant_call_id,
                    model_turn_index=model_turn_index,
                    source_uuid=source_uuid,
                    record_kind=record_kind,
                )
            )
            self._transcript_store.append_message(
                stored,
                message_uuid=message_uuid,
                parent_uuid=self._last_uuid,
                assistant_call_id=assistant_call_id,
                model_turn_index=model_turn_index,
                source_uuid=source_uuid,
                record_kind=record_kind,
            )
            self._last_uuid = message_uuid
            return deepcopy(stored)

    def _finalize_locked(
        self,
        facts: InterruptedRunFacts,
        error_log_recorder: ErrorLogRecorder | None,
    ) -> InterruptCleanupResult:
        full = list(self._transcript_store.read_records())
        original_by_uuid = {record.uuid: record for record in full}
        active = list(zip(self._metas, self._messages))

        run_start = self._run_start_index(active, facts)
        prior = active[:run_start]
        region = active[run_start:]

        paired_ids = _paired_tool_call_ids(region, facts)
        corrected_meta: list[StoredRecordMeta] = []
        corrected_messages: list[dict[str, Any]] = []
        removed = 0
        modified = 0
        added = 0

        persisted_results = {
            message.get("tool_call_id")
            for meta, message in region
            if message.get("role") == "tool_result"
            and message.get("tool_call_id") in paired_ids
        }

        prefix_end = self._prefix_end_index(full, active)
        prefix = full[:prefix_end]
        prior_records = [
            _to_loaded(
                meta,
                _persistable_message(message, original_by_uuid.get(meta.uuid)),
                session_id=facts.session_id,
                original=original_by_uuid.get(meta.uuid),
            )
            for meta, message in prior
        ]
        parent_uuid = (
            prior[-1][0].uuid if prior else (prefix[-1].uuid if prefix else None)
        )
        for meta, message in region:
            role = message.get("role")
            if role == "assistant":
                pruned = prune_assistant_declarations(message, paired_ids)
                if not message_has_body(pruned):
                    removed += 1
                    continue
                if pruned != message:
                    modified += 1
                corrected_meta.append(replace(meta, parent_uuid=parent_uuid))
                corrected_messages.append(pruned)
                parent_uuid = meta.uuid
                for call in assistant_declarations(pruned):
                    if call not in paired_ids or call in persisted_results:
                        continue
                    result = _result_by_id(facts, call)
                    if result is None:
                        continue
                    new_meta = _new_result_meta(
                        parent_uuid,
                        facts,
                    )
                    corrected_meta.append(new_meta)
                    corrected_messages.append(_result_message(result))
                    parent_uuid = new_meta.uuid
                    persisted_results.add(call)
                    added += 1
                continue
            if role == "tool_result":
                call_id = message.get("tool_call_id")
                if call_id in paired_ids:
                    corrected_meta.append(replace(meta, parent_uuid=parent_uuid))
                    corrected_messages.append(
                        _persistable_message(
                            message,
                            original_by_uuid.get(meta.uuid),
                        )
                    )
                    parent_uuid = meta.uuid
                else:
                    removed += 1
                continue
            corrected_meta.append(replace(meta, parent_uuid=parent_uuid))
            corrected_messages.append(
                _persistable_message(message, original_by_uuid.get(meta.uuid))
            )
            parent_uuid = meta.uuid

        if not _has_assistant_record(region, facts):
            appended_text, appended_meta = self._append_unpersisted_assistant(
                facts,
                paired_ids,
                parent_uuid,
            )
            if appended_text is not None and appended_meta is not None:
                corrected_meta.append(appended_meta)
                corrected_messages.append(appended_text)
                parent_uuid = appended_meta.uuid
                added += 1

        if removed == 0 and modified == 0 and added == 0:
            return InterruptCleanupResult(success=True)

        # 构建完整修正后的 transcript：未修改的前缀 + 先前的活动记录 + 修正后的运行区域。
        corrected_records = [
            _to_loaded(
                meta,
                message,
                session_id=facts.session_id,
                original=original_by_uuid.get(meta.uuid),
            )
            for meta, message in zip(corrected_meta, corrected_messages)
        ]
        new_full = prefix + prior_records + corrected_records

        try:
            self._transcript_store.rewrite_records(new_full)
        except OSError as exc:
            staging_path = getattr(self._transcript_store, "last_staging_path", None)
            if error_log_recorder is not None:
                error_log_recorder.record_error(
                    exc,
                    source="context.interrupt_cleanup",
                    attributes={"session_id": facts.session_id},
                )
            return InterruptCleanupResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                staging_path=staging_path,
            )

        self._messages = corrected_messages
        self._metas = corrected_meta
        self._last_uuid = corrected_meta[-1].uuid if corrected_meta else None
        return InterruptCleanupResult(
            success=True,
            records_removed=removed,
            records_added=added,
            records_modified=modified,
        )

    def _run_start_index(
        self,
        active: list[tuple[StoredRecordMeta, dict[str, Any]]],
        facts: InterruptedRunFacts,
    ) -> int:
        if facts.user_prompt_uuid is not None:
            for index, (meta, _message) in enumerate(active):
                if meta.uuid == facts.user_prompt_uuid:
                    return index
        return 0

    def _prefix_end_index(
        self,
        full: list[LoadedTranscriptMessage],
        active: list[tuple[StoredRecordMeta, dict[str, Any]]],
    ) -> int:
        active_uuids = {meta.uuid for meta, _message in active}
        for index, record in enumerate(full):
            if record.uuid in active_uuids:
                return index
        return len(full)

    def _append_unpersisted_assistant(
        self,
        facts: InterruptedRunFacts,
        paired_ids: frozenset[str],
        parent_uuid: str | None,
    ) -> tuple[dict[str, Any] | None, StoredRecordMeta | None]:
        candidate = facts.assistant_message
        if candidate is None:
            if not facts.assistant_text.strip() and not (
                facts.declared_ids & paired_ids
            ):
                return None, None
            candidate = {"role": "assistant", "content": facts.assistant_text}
        pruned = prune_assistant_declarations(candidate, paired_ids)
        if not message_has_body(pruned):
            return None, None
        meta = StoredRecordMeta(
            uuid=str(uuid.uuid4()),
            parent_uuid=parent_uuid,
            assistant_call_id=facts.assistant_call_id,
            model_turn_index=facts.model_turn_index,
        )
        return pruned, meta


def _paired_tool_call_ids(
    region: list[tuple[StoredRecordMeta, dict[str, Any]]],
    facts: InterruptedRunFacts,
) -> frozenset[str]:
    declared: set[str] = set(facts.declared_ids)
    result_ids: set[str] = set(facts.result_ids)
    for _meta, message in region:
        role = message.get("role")
        if role == "assistant":
            declared.update(assistant_declarations(message))
        elif role == "tool_result":
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                result_ids.add(call_id)
    return frozenset(declared & result_ids)


def _has_assistant_record(
    region: list[tuple[StoredRecordMeta, dict[str, Any]]],
    facts: InterruptedRunFacts,
) -> bool:
    if facts.assistant_record_uuid is not None and any(
        meta.uuid == facts.assistant_record_uuid for meta, _message in region
    ):
        return True
    for meta, message in region:
        if message.get("role") != "assistant":
            continue
        if facts.assistant_call_id is not None:
            if meta.assistant_call_id == facts.assistant_call_id:
                return True
            continue
        # 无稳定调用 ID：将任意 assistant 视为已持久化的草稿。
        return True
    return False


def _result_by_id(
    facts: InterruptedRunFacts,
    tool_call_id: str,
) -> ToolExecutionResult | None:
    for result in facts.results:
        if result.tool_call_id == tool_call_id:
            return result
    return None


def _new_result_meta(
    parent_uuid: str | None,
    facts: InterruptedRunFacts,
) -> StoredRecordMeta:
    return StoredRecordMeta(
        uuid=str(uuid.uuid4()),
        parent_uuid=parent_uuid,
        assistant_call_id=facts.assistant_call_id,
        model_turn_index=facts.model_turn_index,
    )


def _result_message(result: ToolExecutionResult) -> dict[str, Any]:
    return {
        "role": "tool_result",
        "tool_call_id": result.tool_call_id,
        "tool_name": result.tool_name,
        "content": result.content,
        "is_error": result.is_error,
        "metadata": deepcopy(result.metadata),
    }


def _persistable_message(
    message: dict[str, Any],
    original: LoadedTranscriptMessage | None,
) -> dict[str, Any]:
    """优先使用原始存储记录，使外置的结果保持外置状态。"""

    if original is not None:
        return deepcopy(original.message)
    return deepcopy(message)


def _to_loaded(
    meta: StoredRecordMeta,
    message: dict[str, Any],
    *,
    session_id: str,
    original: LoadedTranscriptMessage | None,
) -> LoadedTranscriptMessage:
    return LoadedTranscriptMessage(
        uuid=meta.uuid,
        parent_uuid=meta.parent_uuid,
        session_id=session_id,
        timestamp=original.timestamp if original is not None else None,
        sequence=original.sequence if original is not None else 0,
        message=deepcopy(message),
        assistant_call_id=meta.assistant_call_id,
        model_turn_index=meta.model_turn_index,
        source_uuid=meta.source_uuid,
        record_kind=meta.record_kind,
    )
